"""Pruebas de la carga transaccional a SQLite (src/carga.py).

Cada prueba usa una base temporal: nunca toca data/etl_fintech.db.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.carga import cargar, conectar, contar, crear_esquema, leer
from src.limpieza import cargar_datos, limpiar
from src.transformacion import ejecutar_transformacion
from src.validacion import COLUMNAS_CUARENTENA, segregar


def segregado_real():
    d = cargar_datos()
    limpio = limpiar(d["atm"], d["spei"], guardar=False)
    return segregar(limpio, ejecutar_transformacion(limpio, d["catalogo"]))


class BaseTemporal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seg = segregado_real()
        cls.limpias = cls.seg["transacciones_limpias"]
        cls.cuarentena = cls.seg["cuarentena_errores"]

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.bd = Path(self._dir.name) / "prueba.db"

    def tearDown(self):
        self._dir.cleanup()

    def estados_bitacora(self):
        return list(leer("SELECT estado FROM bitacora_cargas ORDER BY inicio, rowid", self.bd)["estado"])


class TestCargaExitosa(BaseTemporal):
    def test_carga_real(self):
        r = cargar(self.limpias, self.cuarentena, self.bd)
        self.assertEqual(r["estado"], "EXITOSA")
        self.assertIsNone(r["error"])
        self.assertEqual(contar(self.bd), {"transacciones_limpias": 683, "cuarentena_errores": 537,
                                           "bitacora_cargas": 1})

    def test_idempotente_no_duplica(self):
        cargar(self.limpias, self.cuarentena, self.bd)
        cargar(self.limpias, self.cuarentena, self.bd)
        c = contar(self.bd)
        self.assertEqual((c["transacciones_limpias"], c["cuarentena_errores"]), (683, 537))
        self.assertEqual(self.estados_bitacora(), ["EXITOSA", "EXITOSA"])

    def test_fecha_en_formato_iso(self):
        cargar(self.limpias, self.cuarentena, self.bd)
        f = leer("SELECT fecha, fecha_hora FROM transacciones_limpias", self.bd)
        self.assertTrue(f["fecha"].str.fullmatch(r"\d{4}-\d{2}-\d{2}").all())
        self.assertTrue(f["fecha_hora"].str.endswith("-06:00").all())

    def test_booleanos_como_0_y_1(self):
        cargar(self.limpias, self.cuarentena, self.bd)
        f = leer("SELECT DISTINCT monto_imputado FROM transacciones_limpias", self.bd)
        self.assertTrue(set(f["monto_imputado"]) <= {0, 1})

    def test_montos_original_y_procesado_se_conservan(self):
        cargar(self.limpias, self.cuarentena, self.bd)
        f = leer("SELECT count(*) AS n FROM transacciones_limpias WHERE monto_recortado = 1 "
                 "AND monto_original_mxn > monto_mxn", self.bd)
        self.assertGreater(f["n"].iloc[0], 0)

    def test_cuarentena_trazable(self):
        cargar(self.limpias, self.cuarentena, self.bd)
        f = leer("SELECT * FROM cuarentena_errores WHERE id_registro = 'SPEI-2026-00250'", self.bd)
        self.assertEqual(f["etapa_origen"].iloc[0], "VALIDACION_PYDANTIC")
        self.assertIn("CLABE", f["motivo_rechazo_cuarentena"].iloc[0])
        self.assertIn('"beneficiary_clabe": "12345"', f["registro_original"].iloc[0])

    def test_vista_resumen(self):
        cargar(self.limpias, self.cuarentena, self.bd)
        v = leer("SELECT sum(registros) AS n FROM resumen_cuarentena", self.bd)
        self.assertEqual(v["n"].iloc[0], 537)

    def test_lote_vacio(self):
        r = cargar(self.limpias.iloc[0:0], pd.DataFrame(columns=COLUMNAS_CUARENTENA), self.bd)
        self.assertEqual(r["estado"], "EXITOSA")
        self.assertEqual(contar(self.bd)["transacciones_limpias"], 0)


class TestRollback(BaseTemporal):
    """Una falla a la mitad debe dejar la base exactamente como estaba."""

    def assertSinCambios(self, r):
        self.assertEqual(r["estado"], "REVERTIDA")
        c = contar(self.bd)
        self.assertEqual((c["transacciones_limpias"], c["cuarentena_errores"]), (683, 537))
        self.assertEqual(self.estados_bitacora(), ["EXITOSA", "REVERTIDA"])
        msg = leer("SELECT mensaje FROM bitacora_cargas WHERE estado = 'REVERTIDA'", self.bd)["mensaje"].iloc[0]
        self.assertTrue(msg)

    def setUp(self):
        super().setUp()
        cargar(self.limpias, self.cuarentena, self.bd)   # carga buena previa

    def test_id_duplicado_viola_llave_primaria(self):
        malo = pd.concat([self.limpias, self.limpias.iloc[[0]]], ignore_index=True)
        r = cargar(malo, self.cuarentena, self.bd)
        self.assertIn("IntegrityError", r["error"])
        self.assertSinCambios(r)

    def test_ultima_fila_viola_check_y_revierte_las_anteriores(self):
        # simula un registro que se saltó la validación: monto negativo al final del lote
        malo = self.limpias.copy()
        malo.loc[len(malo) - 1, "monto_mxn"] = -1.0
        r = cargar(malo, self.cuarentena, self.bd)
        self.assertIn("CHECK", r["error"])
        self.assertSinCambios(r)

    def test_falla_en_cuarentena_revierte_tambien_las_limpias(self):
        mala = self.cuarentena.copy()
        mala.loc[len(mala) - 1, "motivo_rechazo_cuarentena"] = None
        r = cargar(self.limpias.iloc[:10], mala, self.bd)
        self.assertSinCambios(r)

    def test_columna_faltante(self):
        r = cargar(self.limpias.drop(columns=["fecha_hora"]), self.cuarentena, self.bd)
        self.assertSinCambios(r)

    def test_recupera_despues_de_un_fallo(self):
        cargar(self.limpias.drop(columns=["monto_mxn"]), self.cuarentena, self.bd)
        r = cargar(self.limpias, self.cuarentena, self.bd)
        self.assertEqual(r["estado"], "EXITOSA")
        self.assertEqual(self.estados_bitacora(), ["EXITOSA", "REVERTIDA", "EXITOSA"])


class TestRestriccionesBD(BaseTemporal):
    """Las CHECK de SQLite son una segunda barrera aunque se salte Pydantic."""

    def setUp(self):
        super().setUp()
        self.conn = conectar(self.bd)
        crear_esquema(self.conn)
        self.conn.execute("INSERT INTO bitacora_cargas (lote_id, inicio, estado) VALUES ('L', 'x', 'EXITOSA')")

    def tearDown(self):
        self.conn.close()
        super().tearDown()

    def insertar(self, **cambios):
        fila = {"id_operacion": "X", "fuente": "SPEI", "fecha": "2026-09-01", "fecha_hora": "x",
                "id_cuenta": "CTA-0001", "tipo_operacion": "TRANSFERENCIA_SPEI", "monto_mxn": 10.0,
                "moneda_origen": "MXN", "resultado": "LIQUIDADA", "canal": "Web", "tipo_cuenta": "Ahorro",
                "estado_cuenta_catalogo": "ACTIVA", "monto_imputado": 0, "monto_recortado": 0,
                "beneficiary_clabe": "0" * 18, "monto_mxn_minmax": 0.5, "monto_mxn_categoria": "BAJO",
                "franja_horaria": "TARDE", "lote_id": "L"}
        fila.update(cambios)
        self.conn.execute(f"INSERT INTO transacciones_limpias ({', '.join(fila)}) "
                          f"VALUES ({', '.join('?' * len(fila))})", tuple(fila.values()))

    def test_fila_correcta_entra(self):
        self.insertar()

    def test_clabe_corta_rechazada(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.insertar(beneficiary_clabe="12345")

    def test_fecha_mal_formada_rechazada(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.insertar(fecha="01/09/2026")

    def test_minmax_fuera_de_rango_rechazado(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.insertar(monto_mxn_minmax=2.0)

    def test_lote_inexistente_rechazado(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.insertar(lote_id="NO_EXISTE")


if __name__ == "__main__":
    unittest.main()
