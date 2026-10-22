"""
Pruebas del módulo src/limpieza.py (Fases 1, 2 y 3).

Ejecutar desde la raíz del proyecto:
    python -m unittest discover -s tests -v
"""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src.limpieza import (  # noqa: E402
    ARCHIVO_ATM, ARCHIVO_CATALOGO, ARCHIVO_SPEI, CAJERO_DESCONOCIDO, FUENTE_ATM, FUENTE_SPEI,
    SIN_CORREO, aplicar_capping, capping_iqr, cargar_datos, clasificar_email,
    corregir_emails, generar_reporte_perfil, graficar_boxplots, imputar,
    imputar_cajeros, imputar_montos, limpiar, perfilar_columnas,
    separar_duplicados, separar_imposibles, separar_imposibles_atm,
    separar_imposibles_spei, tabla_comparativa,
)

try:
    import matplotlib  # noqa: F401
    HAY_MATPLOTLIB = True
except ImportError:
    HAY_MATPLOTLIB = False


def _hash(ruta):
    return hashlib.sha256(Path(ruta).read_bytes()).hexdigest()


class TestSepararDuplicados(unittest.TestCase):
    """Pruebas con datos sintéticos pequeños."""

    def setUp(self):
        self.df = pd.DataFrame({
            "id": ["A", "B", "A", "C", "A"],
            "monto": [10.0, None, 10.0, 5.0, 10.0],
        })

    def test_conserva_primera_aparicion(self):
        limpio, duplicados = separar_duplicados(self.df)
        self.assertEqual(limpio["id"].tolist(), ["A", "B", "C"])
        self.assertEqual(len(duplicados), 2)

    def test_trazabilidad(self):
        _, duplicados = separar_duplicados(self.df)
        self.assertEqual(duplicados["indice_original"].tolist(), [2, 4])
        self.assertEqual(duplicados["duplicado_de"].tolist(), [0, 0])

    def test_no_pierde_registros(self):
        limpio, duplicados = separar_duplicados(self.df)
        self.assertEqual(len(limpio) + len(duplicados), len(self.df))

    def test_nulos_iguales_cuentan_como_duplicado(self):
        df = pd.DataFrame({"id": ["X", "X"], "monto": [None, None]})
        limpio, duplicados = separar_duplicados(df)
        self.assertEqual((len(limpio), len(duplicados)), (1, 1))

    def test_no_modifica_entrada(self):
        copia = self.df.copy()
        separar_duplicados(self.df)
        pd.testing.assert_frame_equal(self.df, copia)

    def test_indice_no_consecutivo_distingue_posicion_y_etiqueta(self):
        df = self.df.copy()
        df.index = [10, 3, 7, 99, 5]
        limpio, duplicados = separar_duplicados(df)
        self.assertEqual(duplicados["indice_original"].tolist(), [2, 4])
        self.assertEqual(duplicados["etiqueta_indice"].tolist(), [7, 5])
        self.assertEqual(duplicados["duplicado_de"].tolist(), [0, 0])
        self.assertEqual(limpio.index.tolist(), [10, 3, 99])

    def test_indice_desplazado(self):
        df = self.df.copy()
        df.index = df.index + 1000
        _, duplicados = separar_duplicados(df)
        self.assertEqual(duplicados["indice_original"].tolist(), [2, 4])
        self.assertEqual(duplicados["etiqueta_indice"].tolist(), [1002, 1004])


class TestPerfilarColumnas(unittest.TestCase):

    def test_cuenta_nulos_vacios_y_tipos_mezclados(self):
        df = pd.DataFrame({"ts": ["2026-01-01", 1788816180, None],
                           "email": ["a@b.com", "", None]})
        perfil = perfilar_columnas(df, "prueba").set_index("columna")
        self.assertTrue((perfil["fuente"] == "prueba").all())
        self.assertEqual(perfil.loc["ts", "nulos"], 1)
        self.assertIn("int(1)", perfil.loc["ts", "tipos_python"])
        self.assertIn("str(1)", perfil.loc["ts", "tipos_python"])
        self.assertEqual(perfil.loc["email", "vacios"], 1)


def _atm_sintetico():
    return pd.DataFrame({
        "tipo_operacion": ["RETIRO", "RETIRO", "RETIRO", "RETIRO",
                           "CONSULTA_SALDO", "CONSULTA_SALDO",
                           "DEPOSITO", "DEPOSITO"],
        "monto_mxn": [100.0, 300.0, None, 200.0, 0.0, 0.0, None, None],
        "id_cajero": ["ATM-1", None, "ATM-2", None, "ATM-1", "ATM-1", None, "ATM-2"],
        "comision_aplicada": [15.0, 0.0, 25.0, 0.0, 0.0, 34.8, 0.0, 15.0],
        "resultado": ["EXITOSA", "FALLO_FONDOS", "EXITOSA", "EXITOSA",
                      "EXITOSA", "FALLO_CONEXION", "EXITOSA", "EXITOSA"],
    })


class TestImputarMontos(unittest.TestCase):

    def setUp(self):
        self.atm = _atm_sintetico()
        self.resultado, self.medianas = imputar_montos(self.atm)

    def test_imputa_con_mediana_del_grupo(self):
        # Mediana de RETIRO con 100, 300 y 200 = 200.
        self.assertEqual(self.resultado.loc[2, "monto_mxn"], 200.0)
        self.assertTrue(self.resultado.loc[2, "monto_imputado"])

    def test_marca_solo_los_imputados(self):
        self.assertEqual(self.resultado["monto_imputado"].tolist(),
                         [False, False, True, False, False, False, False, False])

    def test_conserva_original(self):
        pd.testing.assert_series_equal(self.resultado["monto_mxn_original"],
                                       self.atm["monto_mxn"], check_names=False)

    def test_conserva_ceros_de_consulta(self):
        consultas = self.resultado[self.resultado["tipo_operacion"] == "CONSULTA_SALDO"]
        self.assertEqual(consultas["monto_mxn"].tolist(), [0.0, 0.0])
        self.assertFalse(consultas["monto_imputado"].any())

    def test_grupo_completamente_nulo_no_se_inventa(self):
        depositos = self.resultado[self.resultado["tipo_operacion"] == "DEPOSITO"]
        self.assertTrue(depositos["monto_mxn"].isna().all())
        self.assertFalse(depositos["monto_imputado"].any())
        fila = self.medianas.set_index("tipo_operacion").loc["DEPOSITO"]
        self.assertTrue(pd.isna(fila["mediana_usada"]))
        self.assertEqual(fila["imputados"], 0)

    def test_tipo_operacion_nulo_no_se_imputa(self):
        atm = pd.DataFrame({"tipo_operacion": ["RETIRO", None],
                            "monto_mxn": [100.0, None]})
        resultado, _ = imputar_montos(atm)
        self.assertTrue(pd.isna(resultado.loc[1, "monto_mxn"]))
        self.assertFalse(resultado.loc[1, "monto_imputado"])

    def test_no_modifica_entrada(self):
        pd.testing.assert_frame_equal(self.atm, _atm_sintetico())


class TestImputarCajeros(unittest.TestCase):

    def test_sustituye_nulos_y_marca(self):
        atm = _atm_sintetico()
        resultado = imputar_cajeros(atm)
        self.assertFalse(resultado["id_cajero"].isna().any())
        self.assertEqual((resultado["id_cajero"] == CAJERO_DESCONOCIDO).sum(), 3)
        self.assertEqual(resultado["cajero_imputado"].tolist(),
                         atm["id_cajero"].isna().tolist())
        pd.testing.assert_frame_equal(atm, _atm_sintetico())


class TestCorregirEmails(unittest.TestCase):

    def setUp(self):
        self.spei = pd.DataFrame({"notification_email": [
            "ana@banco.com.mx", None, "", "   ", "correo@@sinformato", "invalido.com",
        ]})

    def test_clasificacion(self):
        self.assertEqual(clasificar_email(self.spei["notification_email"]).tolist(),
                         ["VALIDO", "NULO", "VACIO", "VACIO", "INVALIDO", "INVALIDO"])

    def test_sustituye_por_sin_correo(self):
        resultado = corregir_emails(self.spei)
        self.assertEqual(resultado["notification_email"].tolist(),
                         ["ana@banco.com.mx"] + [SIN_CORREO] * 5)
        self.assertEqual(resultado["email_corregido"].tolist(),
                         [False, True, True, True, True, True])

    def test_correo_con_espacios_se_normaliza(self):
        spei = pd.DataFrame({"notification_email": [
            " ana@banco.com ", "\tluis@banco.com\n", "ana@banco.com"]})
        self.assertEqual(clasificar_email(spei["notification_email"]).tolist(), ["VALIDO"] * 3)
        resultado = corregir_emails(spei)
        self.assertEqual(resultado["notification_email"].tolist(),
                         ["ana@banco.com", "luis@banco.com", "ana@banco.com"])
        self.assertEqual(resultado["email_normalizado"].tolist(), [True, True, False])
        self.assertFalse(resultado["email_corregido"].any())
        self.assertEqual(resultado["notification_email_original"].tolist(),
                         [" ana@banco.com ", "\tluis@banco.com\n", "ana@banco.com"])

    def test_invalido_con_espacios_y_vacios(self):
        spei = pd.DataFrame({"notification_email": [
            "  correo@@sinformato  ", " invalido.com", "", " \t ", "ana @banco.com"]})
        self.assertEqual(clasificar_email(spei["notification_email"]).tolist(),
                         ["INVALIDO", "INVALIDO", "VACIO", "VACIO", "INVALIDO"])
        resultado = corregir_emails(spei)
        self.assertEqual(resultado["notification_email"].tolist(), [SIN_CORREO] * 5)
        self.assertTrue(resultado["email_corregido"].all())
        self.assertFalse(resultado["email_normalizado"].any())

    def test_no_cambia_mayusculas(self):
        resultado = corregir_emails(pd.DataFrame({"notification_email": [" Ana@Banco.COM "]}))
        self.assertEqual(resultado["notification_email"].tolist(), ["Ana@Banco.COM"])

    def test_conserva_original_y_entrada(self):
        copia = self.spei.copy()
        resultado = corregir_emails(self.spei)
        pd.testing.assert_series_equal(resultado["notification_email_original"],
                                       copia["notification_email"], check_names=False)
        pd.testing.assert_frame_equal(self.spei, copia)


class TestImputar(unittest.TestCase):

    def test_conserva_comisiones_y_no_modifica_entradas(self):
        atm = _atm_sintetico()
        spei = pd.DataFrame({"notification_email": ["a@b.com", None],
                             "platform": ["Web", "Web"], "currency": ["MXN", "USD"],
                             "status": ["LIQUIDADA", "PENDIENTE"]})
        atm_copia, spei_copia = atm.copy(), spei.copy()
        resultado = imputar(atm, spei)
        pd.testing.assert_series_equal(resultado["atm"]["comision_aplicada"],
                                       atm["comision_aplicada"])
        pd.testing.assert_frame_equal(atm, atm_copia)
        pd.testing.assert_frame_equal(spei, spei_copia)


def _atm_montos(tipos, montos):
    return pd.DataFrame({
        "id_transaccion": [f"T{i}" for i in range(len(montos))],
        "tipo_operacion": tipos,
        "monto_mxn": pd.Series(montos, dtype="object"),
    })


class TestImposiblesATM(unittest.TestCase):

    def test_monto_negativo(self):
        atm = _atm_montos(["RETIRO", "DEPOSITO", "CONSULTA_SALDO"], [-100.0, 500.0, -1.0])
        validos, rechazados = separar_imposibles_atm(atm)
        self.assertEqual(rechazados["motivo_rechazo"].tolist(), ["NEGATIVO", "NEGATIVO"])
        self.assertEqual(validos["id_transaccion"].tolist(), ["T1"])

    def test_cero_invalido_y_consulta_valida(self):
        atm = _atm_montos(["RETIRO", "DEPOSITO", "PAGO_SERVICIOS", "CONSULTA_SALDO"],
                          [0.0, 0.0, 0.0, 0.0])
        validos, rechazados = separar_imposibles_atm(atm)
        self.assertEqual(rechazados["motivo_rechazo"].tolist(), ["CERO_NO_PERMITIDO"] * 3)
        self.assertEqual(validos["tipo_operacion"].tolist(), ["CONSULTA_SALDO"])

    def test_no_numerico(self):
        atm = _atm_montos(["RETIRO", "RETIRO", "RETIRO"], ["abc", "1500", float("inf")])
        validos, rechazados = separar_imposibles_atm(atm)
        self.assertEqual(rechazados["motivo_rechazo"].tolist(), ["NO_NUMERICO", "NO_NUMERICO"])
        self.assertEqual(rechazados["valor_original"].tolist()[0], "abc")
        self.assertEqual(validos["id_transaccion"].tolist(), ["T1"])

    def test_imputacion_no_oculta_invalidos(self):
        atm = _atm_montos(["RETIRO"] * 4, [100.0, -50.0, "abc", None])
        imputado, medianas = imputar_montos(atm)
        # La mediana solo usa el monto válido (100); -50 y "abc" no participan.
        self.assertEqual(medianas.loc[0, "mediana_usada"], 100.0)
        self.assertEqual(imputado["monto_imputado"].tolist(), [False, False, False, True])
        validos, rechazados = separar_imposibles_atm(imputado)
        self.assertEqual(rechazados["motivo_rechazo"].tolist(), ["NEGATIVO", "NO_NUMERICO"])
        self.assertEqual(validos["id_transaccion"].tolist(), ["T0", "T3"])

    def test_faltante_no_imputable_se_rechaza(self):
        atm = _atm_montos(["RETIRO", "DEPOSITO"], [100.0, None])
        imputado, _ = imputar_montos(atm)
        _, rechazados = separar_imposibles_atm(imputado)
        self.assertEqual(rechazados["id_registro"].tolist(), ["T1"])
        self.assertEqual(rechazados["motivo_rechazo"].tolist(), ["FALTANTE"])

    def test_rechazados_conservan_informacion(self):
        atm = _atm_montos(["RETIRO", "RETIRO"], [100.0, -5.0])
        atm["id_cajero"] = ["ATM-1", "ATM-2"]
        validos, rechazados = separar_imposibles_atm(atm)
        fila = rechazados.iloc[0]
        self.assertEqual(fila["fuente"], FUENTE_ATM)
        self.assertEqual(fila["archivo_origen"], ARCHIVO_ATM)
        self.assertEqual(fila["id_registro"], "T1")
        self.assertEqual(fila["indice_original"], 1)
        self.assertEqual(fila["etiqueta_indice"], 1)
        self.assertEqual(fila["columna_validada"], "monto_mxn")
        self.assertEqual(fila["valor_original"], -5.0)
        self.assertEqual(fila["id_cajero"], "ATM-2")
        self.assertEqual(len(validos) + len(rechazados), len(atm))

    def test_indice_no_consecutivo(self):
        atm = _atm_montos(["RETIRO", "RETIRO", "DEPOSITO"], [100.0, -5.0, 0.0])
        atm.index = [50, 20, 35]
        _, rechazados = separar_imposibles_atm(atm)
        self.assertEqual(rechazados["indice_original"].tolist(), [1, 2])
        self.assertEqual(rechazados["etiqueta_indice"].tolist(), [20, 35])
        self.assertEqual(rechazados["id_registro"].tolist(), ["T1", "T2"])

    def test_no_modifica_entrada(self):
        atm = _atm_montos(["RETIRO", "RETIRO"], [100.0, -5.0])
        copia = atm.copy()
        separar_imposibles_atm(atm)
        pd.testing.assert_frame_equal(atm, copia)


class TestImposiblesSPEI(unittest.TestCase):

    def setUp(self):
        self.spei = pd.DataFrame({
            "transfer_id": ["S0", "S1", "S2", "S3", "S4"],
            "amount": pd.Series([100.0, 0.0, -5.0, "x", None], dtype="object"),
            "currency": ["MXN", "MXN", "USD", "USD", "MXN"],
        })

    def test_motivos(self):
        validos, rechazados = separar_imposibles_spei(self.spei)
        self.assertEqual(rechazados.set_index("id_registro")["motivo_rechazo"].to_dict(),
                         {"S1": "CERO_NO_PERMITIDO", "S2": "NEGATIVO",
                          "S3": "NO_NUMERICO", "S4": "FALTANTE"})
        self.assertEqual(validos["transfer_id"].tolist(), ["S0"])
        self.assertTrue((rechazados["fuente"] == FUENTE_SPEI).all())
        self.assertTrue((rechazados["archivo_origen"] == ARCHIVO_SPEI).all())

    def test_resumen_por_motivo(self):
        atm = _atm_montos(["RETIRO"], [-1.0])
        resumen = separar_imposibles(atm, self.spei)["resumen"]
        conteo = resumen.set_index(["fuente", "motivo_rechazo"])["registros"]
        self.assertEqual(conteo[(FUENTE_ATM, "NEGATIVO")], 1)
        self.assertEqual(conteo[(FUENTE_SPEI, "CERO_NO_PERMITIDO")], 1)
        self.assertEqual(conteo.sum(), 5)


class TestCappingIQR(unittest.TestCase):

    def test_calculo_de_cuartiles_y_limites(self):
        df = pd.DataFrame({"v": [10.0, 20.0, 30.0, 40.0, 1000.0]})
        resultado, limites = capping_iqr(df, "v")
        fila = limites.iloc[0]
        self.assertEqual((fila["q1"], fila["q3"], fila["iqr"]), (20.0, 40.0, 20.0))
        # Q1 - 1.5 * IQR = -10 -> se acota a 0.
        self.assertEqual(fila["limite_inferior"], 0.0)
        self.assertEqual(fila["limite_superior"], 70.0)
        self.assertEqual(resultado["v"].tolist(), [10.0, 20.0, 30.0, 40.0, 70.0])
        self.assertEqual(resultado["v_recortado"].tolist(), [False] * 4 + [True])
        self.assertEqual(resultado["v_original"].tolist(), df["v"].tolist())

    def test_recorte_inferior(self):
        df = pd.DataFrame({"v": [100.0, 101.0, 102.0, 103.0, 1.0]})
        resultado, limites = capping_iqr(df, "v")
        self.assertEqual(limites.loc[0, "limite_inferior"], 97.0)
        self.assertEqual(limites.loc[0, "outliers_inferiores"], 1)
        self.assertEqual(resultado["v"].iloc[-1], 97.0)

    def test_iqr_cero(self):
        df = pd.DataFrame({"v": [5.0, 5.0, 5.0, 5.0, 100.0]})
        resultado, limites = capping_iqr(df, "v")
        self.assertEqual(limites.loc[0, "iqr"], 0.0)
        self.assertEqual(resultado["v"].tolist(), [5.0] * 5)
        self.assertEqual(int(resultado["v_recortado"].sum()), 1)

    def test_grupos_independientes(self):
        df = pd.DataFrame({
            "v": [10.0, 20.0, 30.0, 40.0, 1000.0, 1.0, 2.0, 3.0, 4.0, 5.0],
            "moneda": ["MXN"] * 5 + ["USD"] * 5,
        })
        resultado, limites = capping_iqr(df, "v", grupo="moneda")
        lim = limites.set_index("grupo")
        self.assertEqual(lim.loc["MXN", "limite_superior"], 70.0)
        self.assertEqual(lim.loc["USD", "limite_superior"], 7.0)
        self.assertEqual(resultado.loc[resultado["moneda"] == "USD", "v"].tolist(),
                         [1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual(resultado["v"].iloc[4], 70.0)

    def test_excluir_y_excluir_calculo(self):
        df = pd.DataFrame({"v": [10.0, 20.0, 30.0, 40.0, 1000.0, 5000.0, 30.0]})
        excluir = pd.Series([False] * 5 + [True, False])
        excluir_calculo = pd.Series([False] * 6 + [True])
        resultado, limites = capping_iqr(df, "v", excluir=excluir,
                                         excluir_calculo=excluir_calculo)
        self.assertEqual(limites.loc[0, "registros_calculo"], 5)
        self.assertEqual(limites.loc[0, "registros_aplicados"], 6)
        self.assertEqual(resultado["v"].iloc[5], 5000.0)   # excluido: intacto
        self.assertFalse(resultado["v_recortado"].iloc[5])
        self.assertEqual(resultado["v"].iloc[4], 70.0)

    def test_grupo_vacio(self):
        vacio = pd.DataFrame({"v": pd.Series([], dtype=float)})
        resultado, limites = capping_iqr(vacio, "v")
        self.assertTrue(resultado.empty)
        self.assertTrue(limites.empty)
        # Grupo sin valores para el cálculo: no se recorta y los límites quedan nulos.
        df = pd.DataFrame({"v": [1.0, 1000.0]})
        resultado, limites = capping_iqr(df, "v", excluir_calculo=pd.Series([True, True]))
        self.assertTrue(pd.isna(limites.loc[0, "limite_superior"]))
        self.assertEqual(resultado["v"].tolist(), [1.0, 1000.0])
        self.assertFalse(resultado["v_recortado"].any())

    def test_no_sobrescribe_original_existente(self):
        df = pd.DataFrame({"v": [10.0, 20.0, 30.0, 40.0, 1000.0],
                           "v_original": [10.0, 20.0, None, 40.0, 1000.0]})
        resultado, _ = capping_iqr(df, "v")
        pd.testing.assert_series_equal(resultado["v_original"], df["v_original"])

    def test_no_modifica_entrada(self):
        df = pd.DataFrame({"v": [10.0, 20.0, 30.0, 40.0, 1000.0]})
        copia = df.copy()
        capping_iqr(df, "v")
        pd.testing.assert_frame_equal(df, copia)


class TestAplicarCapping(unittest.TestCase):

    def test_atm_excluye_consultas_e_imputados_del_calculo(self):
        atm = pd.DataFrame({
            "id_transaccion": [f"T{i}" for i in range(7)],
            "tipo_operacion": ["RETIRO"] * 5 + ["CONSULTA_SALDO", "RETIRO"],
            "monto_mxn": [10.0, 20.0, 30.0, 40.0, 1000.0, 0.0, 20.0],
            "monto_mxn_original": [10.0, 20.0, 30.0, 40.0, 1000.0, 0.0, None],
            "monto_imputado": [False] * 6 + [True],
        })
        resultado = aplicar_capping(atm, pd.DataFrame({
            "transfer_id": ["S0"], "amount": [100.0], "currency": ["MXN"]}))
        atm_cap = resultado["atm"]
        self.assertEqual(atm_cap["monto_mxn"].iloc[4], 70.0)
        self.assertEqual(atm_cap["monto_mxn"].iloc[5], 0.0)
        self.assertTrue(pd.isna(atm_cap["monto_mxn_original"].iloc[6]))
        self.assertTrue(atm_cap["monto_imputado"].iloc[6])
        self.assertEqual(resultado["limites"].iloc[0]["registros_calculo"], 5)
        detalle = resultado["detalle"]
        self.assertEqual(detalle["id_registro"].tolist(), ["T4"])
        self.assertEqual(detalle["valor_original"].tolist(), [1000.0])
        self.assertEqual(detalle["valor_recortado"].tolist(), [70.0])


class TestDatosReales(unittest.TestCase):
    """Pruebas de integración con los archivos originales del proyecto."""

    @classmethod
    def setUpClass(cls):
        archivos = [ARCHIVO_CATALOGO, ARCHIVO_ATM, ARCHIVO_SPEI]
        cls.hashes_antes = {a: _hash(RAIZ / a) for a in archivos}
        cls.datos = cargar_datos()
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        cls.perfil = generar_reporte_perfil(cls.datos, dir_reportes=tmp / "reportes")
        cls.resultado = limpiar(cls.datos["atm"], cls.datos["spei"],
                                dir_procesados=tmp / "processed",
                                dir_reportes=tmp / "reportes")
        cls.dir_tmp = tmp
        cls.atm_sin_dup, _ = separar_duplicados(cls.datos["atm"])
        cls.spei_sin_dup, _ = separar_duplicados(cls.datos["spei"])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_tamanos_originales(self):
        self.assertEqual(self.datos["catalogo"].shape, (50, 6))
        self.assertEqual(self.datos["atm"].shape, (620, 8))
        self.assertEqual(self.datos["spei"].shape, (600, 10))

    def test_clabe_conserva_ceros_a_la_izquierda(self):
        self.assertTrue(self.datos["spei"]["beneficiary_clabe"].str.startswith("0").any())

    def test_duplicados_atm(self):
        self.assertEqual(len(self.resultado["atm"]), 600)
        self.assertEqual(len(self.resultado["duplicados_atm"]), 20)
        self.assertTrue(self.resultado["atm"]["id_transaccion"].is_unique)

    def test_spei_sin_duplicados(self):
        self.assertEqual(len(self.resultado["spei"]), 600)
        self.assertEqual(len(self.resultado["duplicados_spei"]), 0)

    def test_archivos_generados(self):
        esperados = [
            "reportes/resumen_calidad.csv", "reportes/perfil_columnas.csv",
            "reportes/perfil_montos.csv", "reportes/duplicados_atm.csv",
            "reportes/duplicados_spei.csv", "reportes/resumen_duplicados.csv",
            "processed/atm_sin_duplicados.csv", "processed/spei_sin_duplicados.csv",
            "reportes/medianas_imputacion.csv", "reportes/resumen_imputacion.csv",
            "reportes/analisis_faltantes.csv",
            "processed/atm_imputado.csv", "processed/spei_imputado.csv",
            "reportes/rechazados_atm.csv", "reportes/rechazados_spei.csv",
            "reportes/resumen_imposibles.csv", "reportes/limites_iqr.csv",
            "reportes/outliers_recortados.csv",
            "processed/atm_limpio.csv", "processed/spei_limpio.csv",
            "reportes/comparativo_calidad.csv", "reportes/comparativo_calidad_tabla.csv",
        ]
        for ruta in esperados:
            self.assertTrue((self.dir_tmp / ruta).exists(), ruta)

    def test_consultas_saldo_conservadas(self):
        es_consulta = self.datos["atm"]["tipo_operacion"] == "CONSULTA_SALDO"
        self.assertEqual(es_consulta.sum(), 101)
        consultas = self.resultado["atm"][self.resultado["atm"]["tipo_operacion"] == "CONSULTA_SALDO"]
        self.assertEqual(len(consultas), 97)
        self.assertTrue((consultas["monto_mxn"] == 0).all())
        self.assertFalse(consultas["monto_imputado"].any())

    def test_imputacion_montos_reales(self):
        atm = self.resultado["atm"]
        self.assertEqual(atm["monto_imputado"].sum(), 27)
        self.assertFalse(atm["monto_mxn"].isna().any())
        medianas = self.resultado["medianas_imputacion"].set_index("tipo_operacion")["mediana_usada"]
        self.assertEqual(medianas.to_dict(), {"CONSULTA_SALDO": 0.0, "DEPOSITO": 4000.0,
                                              "PAGO_SERVICIOS": 4000.0, "RETIRO": 2000.0})
        imputados = atm[atm["monto_imputado"]]
        self.assertTrue((imputados["monto_mxn"] == imputados["tipo_operacion"].map(medianas)).all())

    def test_imputacion_cajeros_reales(self):
        atm = self.resultado["atm"]
        self.assertEqual(atm["cajero_imputado"].sum(), 42)
        self.assertEqual((atm["id_cajero"] == CAJERO_DESCONOCIDO).sum(), 42)

    def test_correccion_emails_reales(self):
        spei = self.resultado["spei"]
        self.assertEqual(spei["email_motivo"].value_counts().to_dict(),
                         {"VALIDO": 554, "INVALIDO": 30, "NULO": 8, "VACIO": 8})
        self.assertEqual((spei["notification_email"] == SIN_CORREO).sum(), 46)

    def test_columnas_no_tratadas_sin_cambios(self):
        atm_cols = ["id_transaccion", "fecha_hora", "id_cuenta", "tipo_operacion",
                    "resultado", "comision_aplicada"]
        spei_cols = ["timestamp", "currency", "beneficiary_clabe", "status"]
        pd.testing.assert_frame_equal(self.resultado["atm"][atm_cols], self.atm_sin_dup[atm_cols])
        pd.testing.assert_frame_equal(self.resultado["spei"][spei_cols], self.spei_sin_dup[spei_cols])
        # Desde la Fase 3, amount se recorta; su valor de fuente queda en amount_original.
        pd.testing.assert_series_equal(self.resultado["spei"]["amount_original"],
                                       self.spei_sin_dup["amount"], check_names=False)

    def test_sin_imposibles_en_datos_reales(self):
        self.assertEqual(len(self.resultado["rechazados_atm"]), 0)
        self.assertEqual(len(self.resultado["rechazados_spei"]), 0)
        self.assertEqual(self.resultado["resumen_imposibles"]["registros"].sum(), 0)
        self.assertEqual(len(self.resultado["atm"]), 600)
        self.assertEqual(len(self.resultado["spei"]), 600)

    def test_capping_atm_real(self):
        limites = self.resultado["limites_iqr"].set_index(["fuente", "grupo"])
        fila = limites.loc[(FUENTE_ATM, "TOTAL")]
        self.assertEqual((fila["q1"], fila["q3"], fila["iqr"]), (500.0, 7000.0, 6500.0))
        self.assertEqual(fila["limite_superior"], 16750.0)  # valor de la guía
        self.assertEqual(fila["outliers"], 1)
        atm = self.resultado["atm"]
        retiro = atm[atm["id_transaccion"] == "ATM-TX-00416"].iloc[0]
        self.assertEqual(retiro["monto_mxn_original"], 3500000.0)
        self.assertEqual(retiro["monto_mxn"], 16750.0)
        self.assertTrue(retiro["monto_recortado"])
        self.assertEqual(atm["monto_recortado"].sum(), 1)
        consultas = atm[atm["tipo_operacion"] == "CONSULTA_SALDO"]
        self.assertTrue((consultas["monto_mxn"] == 0).all())
        self.assertFalse(consultas["monto_recortado"].any())

    def test_capping_spei_real_por_moneda(self):
        limites = self.resultado["limites_iqr"].set_index(["fuente", "grupo"])
        self.assertEqual(limites.loc[(FUENTE_SPEI, "MXN"), "outliers"], 15)
        self.assertEqual(limites.loc[(FUENTE_SPEI, "USD"), "outliers"], 3)
        self.assertAlmostEqual(limites.loc[(FUENTE_SPEI, "MXN"), "limite_superior"], 50667.5675)
        self.assertAlmostEqual(limites.loc[(FUENTE_SPEI, "USD"), "limite_superior"], 49975.75625)
        spei = self.resultado["spei"]
        self.assertEqual(spei["amount_recortado"].sum(), 18)
        recortados = spei[spei["amount_recortado"]]
        self.assertTrue((recortados["amount_original"] > recortados["amount"]).all())
        self.assertTrue((spei.loc[~spei["amount_recortado"], "amount"]
                         == spei.loc[~spei["amount_recortado"], "amount_original"]).all())
        self.assertEqual(len(self.resultado["outliers_recortados"]), 19)

    def test_banderas_fases_anteriores_conservadas(self):
        atm, spei = self.resultado["atm"], self.resultado["spei"]
        for columna in ["monto_imputado", "cajero_imputado", "monto_mxn_original", "monto_recortado"]:
            self.assertIn(columna, atm.columns)
        for columna in ["email_corregido", "email_normalizado", "notification_email_original",
                        "amount_original", "amount_recortado"]:
            self.assertIn(columna, spei.columns)
        self.assertFalse((atm["monto_imputado"] & atm["monto_recortado"]).any())
        self.assertEqual(spei["email_normalizado"].sum(), 0)

    def test_claves_de_limpiar_compatibles(self):
        self.assertEqual(list(self.resultado), [
            "atm", "spei", "duplicados_atm", "duplicados_spei", "resumen_duplicados",
            "medianas_imputacion", "resumen_imputacion", "analisis_faltantes",
            "rechazados_atm", "rechazados_spei", "resumen_imposibles", "limites_iqr",
            "outliers_recortados", "comparativo_calidad",
        ])
        for valor in self.resultado.values():
            self.assertIsInstance(valor, pd.DataFrame)

    def test_identificadores_de_fuente_unificados(self):
        for clave, valor in self.resultado.items():
            if clave in ("atm", "spei"):
                continue
            self.assertNotIn("dataset", valor.columns, clave)
            self.assertIn("fuente", valor.columns, clave)
            self.assertTrue(set(valor["fuente"]) <= {FUENTE_ATM, FUENTE_SPEI}, clave)
        for clave in ["duplicados_atm", "duplicados_spei", "rechazados_atm",
                      "rechazados_spei", "outliers_recortados"]:
            self.assertIn("archivo_origen", self.resultado[clave].columns, clave)
        detalle = self.resultado["outliers_recortados"]
        self.assertTrue((detalle.loc[detalle["fuente"] == FUENTE_ATM, "archivo_origen"]
                         == ARCHIVO_ATM).all())
        self.assertEqual(set(self.perfil["resumen"]["fuente"]), {"catalogo", FUENTE_ATM, FUENTE_SPEI})

    def test_duplicados_reales_con_trazabilidad(self):
        dup = self.resultado["duplicados_atm"]
        self.assertEqual(dup["indice_original"].tolist(), list(range(600, 620)))
        self.assertEqual(dup["etiqueta_indice"].tolist(), list(range(600, 620)))
        self.assertTrue((dup["fuente"] == FUENTE_ATM).all())
        self.assertEqual(dup.loc[0, "duplicado_de"], 454)

    def test_limpiar_con_indice_no_consecutivo(self):
        atm = self.datos["atm"].copy()
        spei = self.datos["spei"].copy()
        dup_de = set(self.resultado["duplicados_atm"]["duplicado_de"])
        p_atm = next(p for p in range(600) if p not in dup_de
                     and atm.iloc[p]["tipo_operacion"] == "RETIRO")
        atm.iloc[p_atm, atm.columns.get_loc("monto_mxn")] = -100.0
        spei.iloc[10, spei.columns.get_loc("amount")] = 0.0
        atm.index = atm.index * 3 + 7
        spei.index = spei.index + 1000

        resultado = limpiar(atm, spei, guardar=False)
        dup = resultado["duplicados_atm"]
        self.assertEqual(dup["indice_original"].tolist(), list(range(600, 620)))
        self.assertEqual(dup["etiqueta_indice"].tolist(), [p * 3 + 7 for p in range(600, 620)])
        self.assertEqual(dup["id_transaccion"].tolist(),
                         self.resultado["duplicados_atm"]["id_transaccion"].tolist())

        rech_atm = resultado["rechazados_atm"]
        self.assertEqual(rech_atm["indice_original"].tolist(), [p_atm])
        self.assertEqual(rech_atm["etiqueta_indice"].tolist(), [p_atm * 3 + 7])
        self.assertEqual(rech_atm["id_registro"].tolist(), [atm.iloc[p_atm]["id_transaccion"]])
        self.assertEqual(rech_atm["motivo_rechazo"].tolist(), ["NEGATIVO"])

        rech_spei = resultado["rechazados_spei"]
        self.assertEqual(rech_spei["indice_original"].tolist(), [10])
        self.assertEqual(rech_spei["etiqueta_indice"].tolist(), [1010])
        self.assertEqual(rech_spei["id_registro"].tolist(), [spei.iloc[10]["transfer_id"]])
        self.assertEqual(len(resultado["atm"]) + len(rech_atm), 600)
        self.assertEqual(len(resultado["comparativo_calidad"]),
                         len(self.resultado["comparativo_calidad"]))

    def test_analisis_faltantes_reproducible(self):
        a = self.resultado["analisis_faltantes"].set_index(
            ["columna_faltante", "variable", "categoria"]).sort_index()
        cajero = a.loc[("id_cajero", "formato_fecha_hora")]
        self.assertEqual(cajero.loc["99/99/9999 99:99", "faltantes"], 22)
        self.assertEqual(cajero.loc["99/99/9999 99:99", "registros"], 193)
        self.assertEqual(cajero.loc["9999/99/99 99:99:99", "faltantes"], 6)
        email = a.loc[("notification_email", "tipo_timestamp")]
        self.assertEqual((email.loc["int", "faltantes"], email.loc["int", "registros"]), (25, 195))
        self.assertEqual((email.loc["str", "faltantes"], email.loc["str", "registros"]), (21, 405))
        self.assertNotIn("formato_fecha_hora", self.resultado["atm"].columns)
        self.assertNotIn("tipo_timestamp", self.resultado["spei"].columns)

    def _valor(self, fuente, indicador, etapa):
        c = self.resultado["comparativo_calidad"]
        fila = c[(c["fuente"] == fuente) & (c["indicador"] == indicador) & (c["etapa"] == etapa)]
        self.assertEqual(len(fila), 1, (fuente, indicador, etapa))
        return fila.iloc[0]

    def test_comparativo_distingue_etapas(self):
        self.assertEqual(self._valor(FUENTE_ATM, "registros", "original")["valor"], 620)
        self.assertEqual(self._valor(FUENTE_ATM, "registros", "sin_duplicados")["valor"], 600)
        self.assertEqual(self._valor(FUENTE_ATM, "id_cajero_nulos", "original")["valor"], 44)
        self.assertEqual(self._valor(FUENTE_ATM, "id_cajero_nulos", "sin_duplicados")["valor"], 42)
        self.assertEqual(self._valor(FUENTE_ATM, "consultas_saldo", "original")["valor"], 101)
        self.assertEqual(self._valor(FUENTE_ATM, "consultas_saldo", "final")["valor"], 97)
        self.assertEqual(self._valor(FUENTE_ATM, "duplicados_exactos", "original")["porcentaje"], 3.23)

    def test_comparativo_separa_imputados_de_observados(self):
        observados = self._valor(FUENTE_ATM, "monto_mxn_observados", "final")["valor"]
        imputados = self._valor(FUENTE_ATM, "monto_mxn_imputados", "final")["valor"]
        self.assertEqual((observados, imputados), (573, 27))
        self.assertEqual(observados + imputados, len(self.resultado["atm"]))

    def test_comparativo_coincide_con_resultados(self):
        limites = self.resultado["limites_iqr"].set_index(["fuente", "grupo"])["outliers"]
        self.assertEqual(self._valor(FUENTE_ATM, "outliers_recortados", "final")["valor"],
                         limites[(FUENTE_ATM, "TOTAL")])
        self.assertEqual(self._valor(FUENTE_SPEI, "outliers_recortados_MXN", "final")["valor"], 15)
        self.assertEqual(self._valor(FUENTE_SPEI, "outliers_recortados_USD", "final")["valor"], 3)
        self.assertEqual(self._valor(FUENTE_SPEI, "email_corregidos", "final")["valor"],
                         self.resultado["spei"]["email_corregido"].sum())
        self.assertEqual(self._valor(FUENTE_ATM, "registros_sin_modificacion", "final")["valor"], 531)
        self.assertEqual(self._valor(FUENTE_SPEI, "registros_sin_modificacion", "final")["valor"], 536)
        guardado = pd.read_csv(self.dir_tmp / "reportes/comparativo_calidad.csv")
        self.assertEqual(guardado["valor"].tolist(),
                         self.resultado["comparativo_calidad"]["valor"].tolist())

    def test_tabla_comparativa(self):
        tabla = tabla_comparativa(self.resultado["comparativo_calidad"]).set_index(["fuente", "indicador"])
        self.assertEqual(tabla.loc[(FUENTE_ATM, "monto_mxn_nulos")].tolist(), [27, 27, 0])
        self.assertTrue(pd.isna(tabla.loc[(FUENTE_ATM, "monto_mxn_imputados"), "original"]))

    @unittest.skipUnless(HAY_MATPLOTLIB, "matplotlib no instalado")
    def test_graficas_generadas(self):
        rutas = graficar_boxplots(self.resultado, dir_reportes=self.dir_tmp / "graficas")
        self.assertEqual([r.name for r in rutas], ["boxplot_atm_capping.png",
                                                   "boxplot_spei_mxn_capping.png",
                                                   "boxplot_spei_usd_capping.png"])
        for ruta in rutas:
            self.assertGreater(ruta.stat().st_size, 10_000, ruta.name)

    def test_entradas_de_limpiar_sin_cambios(self):
        self.assertEqual(self.datos["atm"]["monto_mxn"].isna().sum(), 27)
        self.assertEqual(self.datos["atm"]["id_cajero"].isna().sum(), 44)
        self.assertNotIn("monto_imputado", self.datos["atm"].columns)

    def test_originales_sin_cambios(self):
        for archivo, hash_antes in self.hashes_antes.items():
            self.assertEqual(_hash(RAIZ / archivo), hash_antes, archivo)


if __name__ == "__main__":
    unittest.main()
