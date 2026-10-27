"""Pruebas de estrés del pipeline completo con inyección de datos corruptos.

Se corrompen copias EN MEMORIA de los datos crudos (los archivos originales no
se tocan) y se corre todo: Ingeniero 1 -> Ingeniero 2 -> Ingeniero 3 -> SQLite.

Criterios que se verifican en cada escenario:
* El pipeline no lanza excepciones no controladas (no crash).
* Cuadre: transacciones_limpias + cuarentena_errores = filas de entrada.
* Cada registro corrompido termina en cuarentena con un motivo.
* La carga a la base de datos termina en commit.
"""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

import main
from src.carga import cargar, contar, leer
from src.limpieza import cargar_datos, limpiar
from src.transformacion import ejecutar_transformacion
from src.validacion import segregar

DATOS = cargar_datos()
CUENTAS_ACTIVAS = set(DATOS["catalogo"].loc[DATOS["catalogo"]["estado_cuenta"] == "ACTIVA", "id_cuenta"])


def filas_activas(df, col_cuenta, n, condicion=None):
    """Posiciones de filas con cuenta ACTIVA (para que no las aparte antes el cruce de catálogo)."""
    ok = df[col_cuenta].isin(CUENTAS_ACTIVAS)
    if condicion is not None:
        ok &= condicion
    return list(np.flatnonzero(ok.to_numpy()))[:n]


def corromper(df, posicion, columna, valor):
    if columna not in df.columns:
        raise KeyError(columna)
    df[columna] = df[columna].astype(object)
    df.iloc[posicion, df.columns.get_loc(columna)] = valor


def correr(atm, spei, ruta_bd):
    limpio = limpiar(atm, spei, guardar=False)
    trans = ejecutar_transformacion(limpio, DATOS["catalogo"])
    seg = segregar(limpio, trans)
    carga = cargar(seg["transacciones_limpias"], seg["cuarentena_errores"], ruta_bd)
    return seg, carga


# Cada caso: (columna, valor corrupto). Todos deben acabar en cuarentena.
CORRUPCIONES_ATM = {
    "monto negativo": ("monto_mxn", -500.0),
    "monto como texto": ("monto_mxn", "quinientos"),
    "monto infinito": ("monto_mxn", np.inf),
    "comisión nula": ("comision_aplicada", np.nan),
    "comisión negativa": ("comision_aplicada", -15.0),
    "fecha basura": ("fecha_hora", "ayer en la tarde"),
    "fecha imposible": ("fecha_hora", "2026-02-31 10:00:00"),
    "fecha nula": ("fecha_hora", None),
    "resultado desconocido": ("resultado", "PROCESANDO"),
    "tipo de operación desconocido": ("tipo_operacion", "PRESTAMO"),
    "cuenta huérfana": ("id_cuenta", "CTA-9999"),
    "cuenta nula": ("id_cuenta", None),
    "cuenta con basura": ("id_cuenta", "'; DROP TABLE x; --"),
}
CORRUPCIONES_SPEI = {
    "CLABE de 17 dígitos": ("beneficiary_clabe", "0" * 17),
    "CLABE con letras": ("beneficiary_clabe", "ABCDEFGHIJKLMNOPQR"),
    "CLABE nula": ("beneficiary_clabe", None),
    "monto negativo": ("amount", -10.0),
    "monto como texto": ("amount", "diez mil"),
    "monto cero": ("amount", 0.0),
    "moneda desconocida": ("currency", "EUR"),
    "moneda nula": ("currency", None),
    "timestamp basura": ("timestamp", "nunca"),
    "timestamp nulo": ("timestamp", None),
    "status desconocido": ("status", "EN_PROCESO"),
    "cuenta huérfana": ("account_id", "CTA-0000"),
}


class BaseEstres(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.bd = Path(self._dir.name) / "estres.db"

    def tearDown(self):
        self._dir.cleanup()

    def assertCuadra(self, seg, entrada):
        total = len(seg["transacciones_limpias"]) + len(seg["cuarentena_errores"])
        self.assertEqual(total, entrada, "se perdieron o duplicaron registros")


class TestInyeccionPorCaso(BaseEstres):
    """Una corrupción a la vez: el registro dañado debe acabar en cuarentena."""

    def _probar(self, fuente, nombre, columna, valor):
        atm, spei = DATOS["atm"].copy(), DATOS["spei"].copy()
        if fuente == "ATM":
            # una operación que mueve dinero, para que el monto 0 o la mediana no oculten el caso
            pos = filas_activas(atm, "id_cuenta", 1, atm["tipo_operacion"] == "RETIRO")[0]
            id_ = atm["id_transaccion"].iloc[pos]
            corromper(atm, pos, columna, valor)
        else:
            pos = filas_activas(spei, "account_id", 1, spei["beneficiary_clabe"].str.len() == 18)[0]
            id_ = spei["transfer_id"].iloc[pos]
            corromper(spei, pos, columna, valor)
        seg, carga = correr(atm, spei, self.bd)
        self.assertCuadra(seg, len(atm) + len(spei))
        self.assertEqual(carga["estado"], "EXITOSA")
        c = seg["cuarentena_errores"]
        self.assertIn(id_, set(c["id_registro"]), f"{fuente} / {nombre}: no llegó a cuarentena")
        self.assertNotIn(id_, set(seg["transacciones_limpias"]["id_operacion"]))
        self.assertTrue(c.loc[c["id_registro"] == id_, "motivo_rechazo_cuarentena"].str.len().gt(0).all())

    def test_corrupciones_atm(self):
        for nombre, (columna, valor) in CORRUPCIONES_ATM.items():
            with self.subTest(fuente="ATM", caso=nombre):
                self._probar("ATM", nombre, columna, valor)

    def test_corrupciones_spei(self):
        for nombre, (columna, valor) in CORRUPCIONES_SPEI.items():
            with self.subTest(fuente="SPEI", caso=nombre):
                self._probar("SPEI", nombre, columna, valor)


class TestLotesMasivos(BaseEstres):
    def test_todas_las_corrupciones_juntas(self):
        """Un solo lote con todas las corrupciones en filas distintas."""
        atm, spei = DATOS["atm"].copy(), DATOS["spei"].copy()
        pos_a = filas_activas(atm, "id_cuenta", len(CORRUPCIONES_ATM), atm["tipo_operacion"] == "RETIRO")
        pos_s = filas_activas(spei, "account_id", len(CORRUPCIONES_SPEI), spei["beneficiary_clabe"].str.len() == 18)
        ids = list(atm["id_transaccion"].iloc[pos_a]) + list(spei["transfer_id"].iloc[pos_s])
        for p, (col, val) in zip(pos_a, CORRUPCIONES_ATM.values()):
            corromper(atm, p, col, val)
        for p, (col, val) in zip(pos_s, CORRUPCIONES_SPEI.values()):
            corromper(spei, p, col, val)
        seg, carga = correr(atm, spei, self.bd)
        self.assertCuadra(seg, 1220)
        self.assertEqual(carga["estado"], "EXITOSA")
        self.assertTrue(set(ids) <= set(seg["cuarentena_errores"]["id_registro"]))
        self.assertEqual(contar(self.bd)["transacciones_limpias"], len(seg["transacciones_limpias"]))

    def test_todo_el_lote_atm_corrupto(self):
        atm = DATOS["atm"].copy()
        atm["monto_mxn"] = "x"
        seg, carga = correr(atm, DATOS["spei"], self.bd)
        self.assertCuadra(seg, 1220)
        self.assertEqual((seg["transacciones_limpias"]["fuente"] == "ATM").sum(), 0)
        self.assertEqual(carga["estado"], "EXITOSA")

    def test_todas_las_clabe_invalidas(self):
        spei = DATOS["spei"].copy()
        spei["beneficiary_clabe"] = "12345"
        seg, _ = correr(DATOS["atm"], spei, self.bd)
        self.assertCuadra(seg, 1220)
        self.assertEqual((seg["transacciones_limpias"]["fuente"] == "SPEI").sum(), 0)

    def test_lote_vacio(self):
        seg, carga = correr(DATOS["atm"].iloc[0:0], DATOS["spei"].iloc[0:0], self.bd)
        self.assertCuadra(seg, 0)
        self.assertEqual(carga["estado"], "EXITOSA")

    def test_volumen_10x(self):
        """12,200 registros (los datos reales repetidos 10 veces con IDs únicos)."""
        atm = pd.concat([DATOS["atm"].assign(id_transaccion=DATOS["atm"]["id_transaccion"] + f"-{k}")
                         for k in range(10)], ignore_index=True)
        spei = pd.concat([DATOS["spei"].assign(transfer_id=DATOS["spei"]["transfer_id"] + f"-{k}")
                          for k in range(10)], ignore_index=True)
        inicio = time.perf_counter()
        seg, carga = correr(atm, spei, self.bd)
        duracion = time.perf_counter() - inicio
        self.assertCuadra(seg, 12200)
        self.assertEqual(carga["estado"], "EXITOSA")
        self.assertEqual(contar(self.bd)["transacciones_limpias"], 6830)
        self.assertLess(duracion, 60, f"tardó {duracion:.1f} s")


class TestOrquestador(BaseEstres):
    """main.py debe terminar de forma controlada aunque falle una etapa."""

    def test_main_exitoso(self):
        with mock.patch("sys.stdout"):
            codigo = main.main(["--bd", str(self.bd), "--sin-reportes"])
        self.assertEqual(codigo, 0)
        self.assertEqual(contar(self.bd)["transacciones_limpias"], 683)

    def test_columna_faltante_en_crudos_no_rompe_ni_toca_la_bd(self):
        with mock.patch("sys.stdout"):
            main.main(["--bd", str(self.bd), "--sin-reportes"])          # carga buena previa
            sin_col = {**DATOS, "atm": DATOS["atm"].drop(columns=["comision_aplicada"])}
            with mock.patch("main.cargar_datos", return_value=sin_col):
                codigo = main.main(["--bd", str(self.bd), "--sin-reportes"])
        self.assertEqual(codigo, 1)
        self.assertEqual(contar(self.bd)["transacciones_limpias"], 683)  # la base no cambió

    def test_falla_en_la_carga_hace_rollback(self):
        with mock.patch("sys.stdout"):
            main.main(["--bd", str(self.bd), "--sin-reportes"])
            original = segregar

            def segregar_con_id_repetido(*args, **kwargs):
                r = original(*args, **kwargs)
                t = r["transacciones_limpias"]
                r["transacciones_limpias"] = pd.concat([t, t.iloc[[0]]], ignore_index=True)
                return r

            with mock.patch("main.segregar", side_effect=segregar_con_id_repetido):
                codigo = main.main(["--bd", str(self.bd), "--sin-reportes"])
        self.assertEqual(codigo, 1)
        self.assertEqual(contar(self.bd)["transacciones_limpias"], 683)
        estados = list(leer("SELECT estado FROM bitacora_cargas ORDER BY rowid", self.bd)["estado"])
        self.assertEqual(estados, ["EXITOSA", "REVERTIDA"])


if __name__ == "__main__":
    unittest.main()
