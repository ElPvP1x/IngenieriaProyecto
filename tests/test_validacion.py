"""Pruebas del módulo del Ingeniero 3, Etapa 1 (src/validacion.py).

Dos bloques:
* Reglas del esquema con filas sintéticas (válidas y corruptas a propósito).
* Integración real: limpiar() -> ejecutar_transformacion() -> segregar().
"""
import json
import unittest

import numpy as np
import pandas as pd
from pydantic import ValidationError

from src.limpieza import cargar_datos, limpiar
from src.transformacion import COLUMNAS_INTEGRADA, ZONA, ejecutar_transformacion
from src.validacion import (COLUMNAS_CUARENTENA, ETAPA_CATALOGO, ETAPA_DUPLICADO, ETAPA_PYDANTIC,
                            TransaccionValidada, consolidar_cuarentena, segregar, validar_integrada)

FECHA = pd.Timestamp("2026-09-15 08:45:00", tz=ZONA)


def fila_atm(**cambios):
    base = {
        "id_operacion": "ATM-TX-00001", "fuente": "ATM", "fecha_hora": FECHA, "id_cuenta": "CTA-0001",
        "tipo_operacion": "RETIRO", "monto_mxn": 2000.0, "monto_original_mxn": 2000.0,
        "moneda_origen": "MXN", "resultado": "EXITOSA", "canal": "ATM-COL-01", "comision_mxn": 15.0,
        "tipo_cuenta": "Ahorro", "estado_cuenta_catalogo": "ACTIVA", "monto_imputado": False,
        "monto_recortado": False, "id_cajero": "ATM-COL-01", "cajero_imputado": False,
        "beneficiary_clabe": np.nan, "notification_email": np.nan, "email_corregido": np.nan,
        "concept": np.nan, "monto_mxn_minmax": 0.5, "monto_mxn_categoria": "MEDIO",
        "franja_horaria": "MANANA",
    }
    base.update(cambios)
    return base


def fila_spei(**cambios):
    base = {
        "id_operacion": "SPEI-2026-00001", "fuente": "SPEI", "fecha_hora": FECHA, "id_cuenta": "CTA-0002",
        "tipo_operacion": "TRANSFERENCIA_SPEI", "monto_mxn": 19844.18, "monto_original_mxn": 19844.18,
        "moneda_origen": "MXN", "resultado": "LIQUIDADA", "canal": "Web Banking", "comision_mxn": np.nan,
        "tipo_cuenta": "Empresarial", "estado_cuenta_catalogo": "ACTIVA", "monto_imputado": False,
        "monto_recortado": False, "id_cajero": np.nan, "cajero_imputado": np.nan,
        "beneficiary_clabe": "012210100614023846", "notification_email": "a@banco.com.mx",
        "email_corregido": False, "concept": "Nómina", "monto_mxn_minmax": 0.02,
        "monto_mxn_categoria": "MEDIO", "franja_horaria": "MANANA",
    }
    base.update(cambios)
    return base


def tabla(*filas):
    return pd.DataFrame(list(filas), columns=COLUMNAS_INTEGRADA + ["monto_mxn_minmax", "monto_mxn_categoria",
                                                                    "franja_horaria"])


def motivo_de(fila):
    """Valida una sola fila y devuelve su motivo (o None si es apta)."""
    r = validar_integrada(tabla(fila))
    return None if len(r["aptos"]) else r["cuarentena"]["motivo_rechazo_cuarentena"].iloc[0]


class TestFilasValidas(unittest.TestCase):
    def test_atm_valida(self):
        self.assertIsNone(motivo_de(fila_atm()))

    def test_spei_valida(self):
        self.assertIsNone(motivo_de(fila_spei()))

    def test_consulta_saldo_con_monto_cero_es_valida(self):
        self.assertIsNone(motivo_de(fila_atm(tipo_operacion="CONSULTA_SALDO", monto_mxn=0.0,
                                             monto_original_mxn=0.0, monto_mxn_minmax=0.0)))

    def test_monto_imputado_sin_original_es_valido(self):
        self.assertIsNone(motivo_de(fila_atm(monto_original_mxn=np.nan, monto_imputado=True)))

    def test_spei_en_usd_es_valida(self):
        self.assertIsNone(motivo_de(fila_spei(moneda_origen="USD")))

    def test_comision_cero_es_valida(self):
        self.assertIsNone(motivo_de(fila_atm(comision_mxn=0.0)))

    def test_tipos_numpy_se_aceptan(self):
        self.assertIsNone(motivo_de(fila_atm(monto_mxn=np.float64(2000), monto_imputado=np.bool_(False))))


class TestDatosCorruptos(unittest.TestCase):
    """Cada caso debe ir a cuarentena con un motivo que nombre el campo."""

    def assertRechaza(self, fila, campo):
        motivo = motivo_de(fila)
        self.assertIsNotNone(motivo, f"debía rechazarse por {campo}")
        self.assertIn(campo, motivo)

    def test_clabe_corta(self):
        self.assertRechaza(fila_spei(beneficiary_clabe="12345"), "beneficiary_clabe")

    def test_clabe_17_digitos(self):
        self.assertRechaza(fila_spei(beneficiary_clabe="0" * 17), "beneficiary_clabe")

    def test_clabe_con_letras(self):
        self.assertRechaza(fila_spei(beneficiary_clabe="01221010061402384X"), "beneficiary_clabe")

    def test_clabe_nula_en_spei(self):
        self.assertRechaza(fila_spei(beneficiary_clabe=None), "beneficiary_clabe")

    def test_monto_negativo(self):
        self.assertRechaza(fila_atm(monto_mxn=-500.0), "monto_mxn")

    def test_monto_como_texto(self):
        self.assertRechaza(fila_atm(monto_mxn="2000"), "monto_mxn")

    def test_monto_infinito(self):
        self.assertRechaza(fila_atm(monto_mxn=float("inf")), "monto_mxn")

    def test_monto_nulo(self):
        self.assertRechaza(fila_atm(monto_mxn=np.nan), "monto_mxn")

    def test_retiro_con_monto_cero(self):
        self.assertRechaza(fila_atm(monto_mxn=0.0), "monto 0 solo es válido")

    def test_spei_con_monto_cero(self):
        self.assertRechaza(fila_spei(monto_mxn=0.0), "monto 0")

    def test_comision_nula_en_atm(self):
        self.assertRechaza(fila_atm(comision_mxn=np.nan), "comision_mxn")

    def test_comision_negativa(self):
        self.assertRechaza(fila_atm(comision_mxn=-15.0), "comision_mxn")

    def test_cajero_vacio_en_atm(self):
        self.assertRechaza(fila_atm(id_cajero=None), "id_cajero")

    def test_cuenta_formato_invalido(self):
        self.assertRechaza(fila_atm(id_cuenta="XYZ-1"), "id_cuenta")

    def test_cuenta_bloqueada(self):
        self.assertRechaza(fila_atm(estado_cuenta_catalogo="BLOQUEADA"), "estado_cuenta_catalogo")

    def test_fecha_nula(self):
        self.assertRechaza(fila_atm(fecha_hora=pd.NaT), "fecha_hora")

    def test_fecha_como_texto(self):
        self.assertRechaza(fila_atm(fecha_hora="2026-09-15"), "fecha_hora")

    def test_fecha_sin_zona(self):
        self.assertRechaza(fila_atm(fecha_hora=pd.Timestamp("2026-09-15 08:45")), "fecha_hora")

    def test_minmax_fuera_de_rango(self):
        self.assertRechaza(fila_atm(monto_mxn_minmax=1.5), "monto_mxn_minmax")

    def test_categoria_desconocida(self):
        self.assertRechaza(fila_atm(monto_mxn_categoria="ENORME"), "monto_mxn_categoria")

    def test_fuente_desconocida(self):
        self.assertRechaza(fila_atm(fuente="POS"), "fuente")

    def test_estado_atm_sin_normalizar(self):
        self.assertRechaza(fila_atm(resultado="exitosa"), "resultado ATM desconocido")

    def test_moneda_desconocida(self):
        self.assertRechaza(fila_spei(moneda_origen="EUR"), "moneda_origen")

    def test_atm_en_usd(self):
        self.assertRechaza(fila_atm(moneda_origen="USD"), "MXN")

    def test_bandera_como_texto(self):
        self.assertRechaza(fila_atm(monto_imputado="False"), "monto_imputado")

    def test_varios_errores_se_reportan_juntos(self):
        r = validar_integrada(tabla(fila_spei(beneficiary_clabe="12345", monto_mxn=-1.0)))
        campos = r["cuarentena"]["campos_con_error"].iloc[0].split(",")
        self.assertIn("beneficiary_clabe", campos)
        self.assertIn("monto_mxn", campos)


class TestResiliencia(unittest.TestCase):
    def test_lote_mixto_no_se_detiene(self):
        filas = [fila_atm(), fila_spei(beneficiary_clabe="12345"), fila_atm(monto_mxn="abc"),
                 fila_spei(), fila_atm(fecha_hora=None)]
        r = validar_integrada(tabla(*filas))
        self.assertEqual(len(r["aptos"]), 2)
        self.assertEqual(len(r["cuarentena"]), 3)

    def test_tabla_vacia(self):
        r = validar_integrada(tabla())
        self.assertEqual(len(r["aptos"]), 0)
        self.assertEqual(list(r["cuarentena"].columns), COLUMNAS_CUARENTENA)

    def test_columna_faltante_manda_todo_a_cuarentena(self):
        df = tabla(fila_atm(), fila_spei()).drop(columns=["monto_mxn"])
        r = validar_integrada(df)
        self.assertEqual(len(r["aptos"]), 0)
        self.assertTrue(r["cuarentena"]["motivo_rechazo_cuarentena"].str.contains("monto_mxn").all())

    def test_columnas_extra_se_ignoran(self):
        df = tabla(fila_atm()).assign(columna_nueva=1)
        self.assertEqual(len(validar_integrada(df)["aptos"]), 1)

    def test_no_modifica_la_entrada(self):
        df = tabla(fila_atm(), fila_spei(beneficiary_clabe="12345"))
        copia = df.copy()
        validar_integrada(df)
        pd.testing.assert_frame_equal(df, copia)

    def test_aptos_conservan_valores(self):
        df = tabla(fila_atm(), fila_spei())
        pd.testing.assert_frame_equal(validar_integrada(df)["aptos"], df)

    def test_registro_original_es_json_valido(self):
        r = validar_integrada(tabla(fila_atm(monto_mxn=float("inf"))))
        datos = json.loads(r["cuarentena"]["registro_original"].iloc[0])
        self.assertEqual(datos["id_operacion"], "ATM-TX-00001")

    def test_volumen_5000_filas(self):
        filas = [fila_atm(id_operacion=f"ATM-TX-{i:05d}") if i % 10 else fila_spei(beneficiary_clabe="1")
                 for i in range(5000)]
        r = validar_integrada(tabla(*filas))
        self.assertEqual(len(r["aptos"]) + len(r["cuarentena"]), 5000)
        self.assertEqual(len(r["cuarentena"]), 500)

    def test_entrada_que_no_es_dataframe(self):
        with self.assertRaises(TypeError):
            validar_integrada([fila_atm()])

    def test_modelo_directo_lanza_validation_error(self):
        with self.assertRaises(ValidationError):
            TransaccionValidada.model_validate({"fuente": "ATM"})


class TestIntegracionReal(unittest.TestCase):
    """Corre el pipeline real de los Ingenieros 1 y 2 y luego la segregación."""

    @classmethod
    def setUpClass(cls):
        d = cargar_datos()
        cls.limpio = limpiar(d["atm"], d["spei"], guardar=False)
        cls.trans = ejecutar_transformacion(cls.limpio, d["catalogo"])
        cls.r = segregar(cls.limpio, cls.trans)
        cls.c = cls.r["cuarentena_errores"]

    def test_no_se_pierde_ninguna_fila(self):
        # 620 ATM originales + 600 SPEI = 1,220
        self.assertEqual(len(self.r["transacciones_limpias"]) + len(self.c), 1220)

    def test_conteos_actuales(self):
        limpias = self.r["transacciones_limpias"]["fuente"].value_counts().to_dict()
        self.assertEqual(limpias, {"ATM": 354, "SPEI": 329})
        self.assertEqual(len(self.c), 537)

    def test_pydantic_atrapa_las_29_clabe(self):
        p = self.c[self.c["etapa_origen"] == ETAPA_PYDANTIC]
        self.assertEqual(len(p), 29)
        self.assertTrue((p["campos_con_error"] == "beneficiary_clabe").all())

    def test_las_46_clabe_quedan_en_cuarentena(self):
        # 29 por Pydantic + 17 que ya habían salido por su cuenta (huérfana o bloqueada/suspendida)
        clabe = self.c["registro_original"].str.contains('"beneficiary_clabe": "12345"')
        self.assertEqual(int(clabe.sum()), 46)

    def test_origenes(self):
        etapas = self.c["etapa_origen"].value_counts().to_dict()
        self.assertEqual(etapas, {ETAPA_CATALOGO: 488, ETAPA_PYDANTIC: 29, ETAPA_DUPLICADO: 20})

    def test_ids_unicos_en_limpias(self):
        self.assertTrue(self.r["transacciones_limpias"]["id_operacion"].is_unique)

    def test_limpias_y_cuarentena_no_se_cruzan(self):
        # los duplicados repiten un ID ya aceptado o apartado, por eso se excluyen de esta comparación
        ids_c = set(self.c.loc[self.c["etapa_origen"] != ETAPA_DUPLICADO, "id_registro"])
        self.assertFalse(ids_c & set(self.r["transacciones_limpias"]["id_operacion"]))

    def test_cuarentena_sin_motivo_vacio(self):
        self.assertFalse(self.c["motivo_rechazo_cuarentena"].isna().any())
        self.assertFalse((self.c["motivo_rechazo_cuarentena"].str.strip() == "").any())
        self.assertFalse(self.c["id_registro"].isna().any())
        self.assertTrue(self.c["fuente"].isin(["ATM", "SPEI"]).all())

    def test_sin_duplicados_si_el_equipo_decide_bitacora(self):
        r = segregar(self.limpio, self.trans, incluir_duplicados=False)
        self.assertEqual(len(r["cuarentena_errores"]), 517)

    def test_consolidar_acepta_tablas_vacias(self):
        vacio = {k: v.iloc[0:0] for k, v in self.limpio.items()}
        trans = {"huerfanas": pd.DataFrame(), "rechazadas_cuenta": pd.DataFrame()}
        c = consolidar_cuarentena(vacio, trans, None)
        self.assertEqual(len(c), 0)
        self.assertEqual(list(c.columns), COLUMNAS_CUARENTENA)


if __name__ == "__main__":
    unittest.main()
