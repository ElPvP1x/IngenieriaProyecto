import unittest
import pandas as pd
from src.limpieza import cargar_datos, limpiar
from src.transformacion import (etapa5_integrar, minmax, discretizar_monto, franja_horaria,
                                etapa4_minmax_discretizacion, ejecutar_transformacion, COLUMNAS_INTEGRADA,
                                etapa2_moneda, etapa3_catalogo, TASA_USD_MXN, ZONA, etapa1_fechas_y_categorias, normalizar_estados,
                                transformar_fechas_atm, transformar_timestamps_spei)


def _atm(fechas, res=None):
    return pd.DataFrame({"fecha_hora": fechas, "resultado": res or ["EXITOSA"] * len(fechas)})


def _spei(ts, st=None):
    return pd.DataFrame({"timestamp": pd.Series(ts, dtype=object), "status": st or ["LIQUIDADA"] * len(ts)})


class TestFechasATM(unittest.TestCase):
    def test_tres_formatos(self):
        r = transformar_fechas_atm(_atm(["2026-10-03 12:51:00", "2026/09/11 11:42:00", "08/09/2026 07:14"]))
        self.assertEqual(list(r["formato_fecha_atm"]),
                         ["YYYY-MM-DD HH:MM:SS", "YYYY/MM/DD HH:MM:SS", "DD/MM/YYYY HH:MM"])
        self.assertFalse(r["fecha_invalida"].any())

    def test_dia_primero(self):
        r = transformar_fechas_atm(_atm(["08/09/2026 07:14"]))
        f = r["fecha_hora"].iloc[0]
        self.assertEqual((f.month, f.day), (9, 8))

    def test_zona_utc_menos_6(self):
        f = transformar_fechas_atm(_atm(["2026-10-03 12:51:00"]))["fecha_hora"].iloc[0]
        self.assertEqual(str(f.utcoffset()), "-1 day, 18:00:00")

    def test_invalida_queda_nula_y_marcada(self):
        r = transformar_fechas_atm(_atm(["basura", None, "31/02/2026 10:00"]))
        self.assertTrue(r["fecha_invalida"].all())
        self.assertTrue(r["fecha_hora"].isna().all())
        self.assertEqual(len(r), 3)

    def test_conserva_original(self):
        r = transformar_fechas_atm(_atm(["08/09/2026 07:14"]))
        self.assertEqual(r["fecha_hora_original"].iloc[0], "08/09/2026 07:14")

    def test_no_modifica_entrada(self):
        d = _atm(["08/09/2026 07:14"]); transformar_fechas_atm(d)
        self.assertEqual(d["fecha_hora"].iloc[0], "08/09/2026 07:14")


class TestTimestampsSPEI(unittest.TestCase):
    def test_iso_y_epoch(self):
        r = transformar_timestamps_spei(_spei(["2026-09-12T19:49:00-06:00", 1791605700]))
        self.assertEqual(list(r["formato_timestamp"]), ["ISO", "EPOCH"])

    def test_epoch_se_convierte_de_utc(self):
        f = transformar_timestamps_spei(_spei([0]))["timestamp"].iloc[0]
        self.assertEqual((f.year, f.month, f.day, f.hour), (1969, 12, 31, 18))

    def test_iso_con_otra_zona_se_convierte(self):
        f = transformar_timestamps_spei(_spei(["2026-09-12T12:00:00Z"]))["timestamp"].iloc[0]
        self.assertEqual(f.hour, 6)

    def test_iso_y_epoch_equivalentes(self):
        r = transformar_timestamps_spei(_spei(["2026-09-12T00:00:00+00:00", 1789171200]))
        self.assertEqual(r["timestamp"].iloc[0], r["timestamp"].iloc[1])

    def test_invalido(self):
        r = transformar_timestamps_spei(_spei(["nada", None]))
        self.assertTrue(r["timestamp_invalido"].all())
        self.assertTrue((r["formato_timestamp"] == "INVALIDO").all())

    def test_conserva_original(self):
        r = transformar_timestamps_spei(_spei([1791605700]))
        self.assertEqual(r["timestamp_original"].iloc[0], 1791605700)


class TestEstados(unittest.TestCase):
    def test_mayusculas_y_bandera(self):
        d, fuera = normalizar_estados(_atm(["x", "x"], ["exitosa", "EXITOSA"]), "resultado", {"EXITOSA"})
        self.assertEqual(list(d["resultado"]), ["EXITOSA", "EXITOSA"])
        self.assertEqual(list(d["resultado_normalizado"]), [True, False])
        self.assertEqual(list(d["resultado_original"]), ["exitosa", "EXITOSA"])
        self.assertEqual(fuera, [])

    def test_estado_nuevo_se_reporta_no_se_rechaza(self):
        d, fuera = normalizar_estados(_spei([0, 0], ["rara", "LIQUIDADA"]), "status", {"LIQUIDADA"})
        self.assertEqual(fuera, ["RARA"]); self.assertEqual(len(d), 2)

    def test_espacios(self):
        d, _ = normalizar_estados(_spei([0], [" liquidada "]), "status", {"LIQUIDADA"})
        self.assertEqual(d["status"].iloc[0], "LIQUIDADA")


class TestDatosReales(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        d = cargar_datos()
        cls.limpio = limpiar(d["atm"], d["spei"], guardar=False)
        cls.e = etapa1_fechas_y_categorias(cls.limpio["atm"], cls.limpio["spei"])

    def test_filas_no_cambian(self):
        self.assertEqual((len(self.e["atm"]), len(self.e["spei"])), (600, 600))

    def test_conteos(self):
        a, s = self.e["atm"], self.e["spei"]
        self.assertEqual(a["formato_fecha_atm"].value_counts().to_dict(),
                         {"YYYY/MM/DD HH:MM:SS": 211, "YYYY-MM-DD HH:MM:SS": 196, "DD/MM/YYYY HH:MM": 193})
        self.assertEqual(s["formato_timestamp"].value_counts().to_dict(), {"ISO": 405, "EPOCH": 195})
        self.assertEqual(int(a["resultado_normalizado"].sum()), 81)
        self.assertEqual(int(s["status_normalizado"].sum()), 121)

    def test_sin_invalidos_ni_estados_raros(self):
        self.assertEqual(int(self.e["atm"]["fecha_invalida"].sum()), 0)
        self.assertEqual(int(self.e["spei"]["timestamp_invalido"].sum()), 0)
        self.assertEqual(self.e["estados_fuera_catalogo"], {"atm": [], "spei": []})

    def test_periodo_coherente(self):
        for df, c in ((self.e["atm"], "fecha_hora"), (self.e["spei"], "timestamp")):
            self.assertGreaterEqual(df[c].min(), pd.Timestamp("2026-09-01", tz=ZONA))
            self.assertLess(df[c].max(), pd.Timestamp("2026-10-17", tz=ZONA))

    def test_columnas_del_ingeniero1_intactas(self):
        for k in ("atm", "spei"):
            for c in self.limpio[k].columns:
                if c not in ("fecha_hora", "timestamp", "resultado", "status"):
                    pd.testing.assert_series_equal(self.limpio[k][c], self.e[k][c])

    def test_claves(self):
        self.assertEqual(set(self.e), {"atm", "spei", "resumen_etapa1", "estados_fuera_catalogo"})


class TestMoneda(unittest.TestCase):
    def _s(self, cur, amt, orig=None):
        return pd.DataFrame({"currency": cur, "amount": amt, "amount_original": orig or amt})

    def test_usd_se_convierte_ambos_montos(self):
        r = etapa2_moneda(self._s(["USD"], [100.0], [5000.0]))["spei"]
        self.assertEqual(r["amount_mxn"].iloc[0], 1950.0)
        self.assertEqual(r["amount_original_mxn"].iloc[0], 97500.0)
        self.assertEqual(r["tasa_aplicada"].iloc[0], 19.5)

    def test_mxn_no_cambia(self):
        r = etapa2_moneda(self._s(["MXN"], [123.45]))["spei"]
        self.assertEqual(r["amount_mxn"].iloc[0], 123.45)

    def test_conserva_amount_y_original(self):
        r = etapa2_moneda(self._s(["USD"], [10.0], [20.0]))["spei"]
        self.assertEqual((r["amount"].iloc[0], r["amount_original"].iloc[0]), (10.0, 20.0))

    def test_moneda_desconocida_nula_y_marcada(self):
        r = etapa2_moneda(self._s(["EUR", "usd"], [1.0, 1.0]))["spei"]
        self.assertTrue(r["moneda_desconocida"].iloc[0]); self.assertTrue(pd.isna(r["amount_mxn"].iloc[0]))
        self.assertEqual(r["amount_mxn"].iloc[1], 19.5)

    def test_tasa_configurable_y_valida(self):
        r = etapa2_moneda(self._s(["USD"], [10.0]), tasa=20)["spei"]
        self.assertEqual(r["amount_mxn"].iloc[0], 200.0)
        with self.assertRaises(ValueError):
            etapa2_moneda(self._s(["USD"], [1.0]), tasa=0)

    def test_datos_reales(self):
        d = cargar_datos(); l = limpiar(d["atm"], d["spei"], guardar=False)
        r = etapa2_moneda(l["spei"])
        s = r["spei"]
        self.assertEqual(int((s["tasa_aplicada"] == TASA_USD_MXN).sum()), 134)
        self.assertEqual(len(s), 600); self.assertEqual(int(s["moneda_desconocida"].sum()), 0)
        self.assertFalse(s["amount_mxn"].isna().any())


class TestCatalogo(unittest.TestCase):
    CAT = pd.DataFrame({"id_cuenta": ["CTA-1", "CTA-2", "CTA-3"], "tipo_cuenta": ["A", "B", "C"],
                        "estado_cuenta": ["ACTIVA", "BLOQUEADA", "SUSPENDIDA"]})

    def _run(self):
        atm = pd.DataFrame({"id_cuenta": ["CTA-1", "CTA-2", "CTA-3", "CTA-9"], "x": range(4)})
        spei = pd.DataFrame({"account_id": ["cta-1 ", "CTA-9"], "x": range(2)})
        return etapa3_catalogo(atm, spei, self.CAT)

    def test_separa_y_rechaza(self):
        r = self._run()
        self.assertEqual(list(r["atm"]["id_cuenta"]), ["CTA-1"])
        self.assertEqual(list(r["huerfanas_atm"]["id_cuenta"]), ["CTA-9"])
        self.assertEqual(list(r["rechazadas_cuenta_atm"]["motivo_separacion"]), ["CUENTA_BLOQUEADA", "CUENTA_SUSPENDIDA"])

    def test_no_se_pierde_ninguna_fila(self):
        r = self._run()
        self.assertEqual(len(r["atm"]) + len(r["huerfanas_atm"]) + len(r["rechazadas_cuenta_atm"]), 4)
        self.assertEqual(len(r["spei"]) + len(r["huerfanas_spei"]) + len(r["rechazadas_cuenta_spei"]), 2)

    def test_llave_con_mayusculas_y_espacios(self):
        self.assertEqual(len(self._run()["spei"]), 1)

    def test_aceptadas_traen_tipo_y_estado(self):
        a = self._run()["atm"]
        self.assertEqual((a["tipo_cuenta"].iloc[0], a["estado_cuenta_catalogo"].iloc[0]), ("A", "ACTIVA"))

    def test_datos_reales(self):
        d = cargar_datos(); l = limpiar(d["atm"], d["spei"], guardar=False)
        r = etapa3_catalogo(l["atm"], etapa2_moneda(l["spei"])["spei"], d["catalogo"])
        self.assertEqual((len(r["huerfanas_atm"]), len(r["huerfanas_spei"])), (32, 29))
        self.assertEqual((len(r["rechazadas_cuenta_atm"]), len(r["rechazadas_cuenta_spei"])), (214, 213))
        self.assertEqual((len(r["atm"]), len(r["spei"])), (354, 358))
        self.assertEqual(len(r["atm"]["id_transaccion"].unique()), 354)


class TestMinMax(unittest.TestCase):
    def test_valores_basicos(self):
        df = pd.DataFrame({"fuente": ["A"] * 3, "monto_mxn": [0.0, 50.0, 100.0]})
        r, p = minmax(df)
        self.assertEqual(list(r["monto_mxn_minmax"]), [0.0, 0.5, 1.0])
        self.assertEqual(p["A"], {"min": 0.0, "max": 100.0})

    def test_por_fuente_es_independiente(self):
        df = pd.DataFrame({"fuente": ["A", "A", "B", "B"], "monto_mxn": [0.0, 10.0, 0.0, 1000.0]})
        r, _ = minmax(df)
        self.assertEqual(list(r["monto_mxn_minmax"]), [0.0, 1.0, 0.0, 1.0])
        r2, _ = minmax(df, por=None)
        self.assertEqual(r2["monto_mxn_minmax"].iloc[1], 0.01)

    def test_constante_y_nulos(self):
        df = pd.DataFrame({"fuente": ["A"] * 3, "monto_mxn": [5.0, 5.0, None]})
        r, _ = minmax(df)
        self.assertEqual(r["monto_mxn_minmax"].iloc[0], 0.0)
        self.assertTrue(pd.isna(r["monto_mxn_minmax"].iloc[2]))

    def test_conserva_valor_original(self):
        df = pd.DataFrame({"fuente": ["A", "A"], "monto_mxn": [1.0, 3.0]})
        self.assertEqual(list(minmax(df)[0]["monto_mxn"]), [1.0, 3.0])


class TestDiscretizar(unittest.TestCase):
    def test_cortes_fijos(self):
        df = pd.DataFrame({"fuente": "A", "monto_mxn": [10.0, 1000.0, 20000.0, 90000.0]})
        r, lim = discretizar_monto(df, cortes=[1000, 10000, 50000])
        self.assertEqual(list(r["monto_mxn_categoria"]), ["BAJO", "BAJO", "ALTO", "MUY_ALTO"])
        self.assertEqual(lim["A"], [1000.0, 10000.0, 50000.0])

    def test_cuartiles(self):
        df = pd.DataFrame({"fuente": "A", "monto_mxn": list(range(1, 101))})
        r, _ = discretizar_monto(df)
        self.assertEqual(r["monto_mxn_categoria"].value_counts().to_dict(), {"BAJO": 25, "MEDIO": 25, "ALTO": 25, "MUY_ALTO": 25})

    def test_cuartiles_repetidos_dan_menos_categorias(self):
        df = pd.DataFrame({"fuente": "A", "monto_mxn": [0.0] * 10 + [100.0]})
        r, _ = discretizar_monto(df)
        self.assertLessEqual(r["monto_mxn_categoria"].nunique(), 2)
        self.assertFalse(r["monto_mxn_categoria"].isna().any())

    def test_nulo_queda_nulo(self):
        df = pd.DataFrame({"fuente": "A", "monto_mxn": [1.0, None, 3.0]})
        self.assertTrue(pd.isna(discretizar_monto(df)[0]["monto_mxn_categoria"].iloc[1]))

    def test_franja_horaria(self):
        f = pd.to_datetime(["2026-09-01 02:00", "2026-09-01 08:00", "2026-09-01 15:00", "2026-09-01 23:00"]).tz_localize(ZONA)
        r = franja_horaria(pd.DataFrame({"fecha_hora": f}))
        self.assertEqual(list(r["franja_horaria"]), ["MADRUGADA", "MANANA", "TARDE", "NOCHE"])


class TestFlujoCompleto(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = cargar_datos()
        cls.limpio = limpiar(cls.d["atm"], cls.d["spei"], guardar=False)
        cls.r = ejecutar_transformacion(cls.limpio, cls.d["catalogo"])

    def test_no_se_pierde_ninguna_fila(self):
        t = self.r["integrada"]
        self.assertEqual(len(t) + len(self.r["huerfanas"]) + len(self.r["rechazadas_cuenta"]), 1200)
        self.assertEqual(len(t), 712)
        self.assertEqual(len(self.r["huerfanas"]), 61)
        self.assertEqual(len(self.r["rechazadas_cuenta"]), 427)

    def test_ids_unicos_y_fuentes(self):
        t = self.r["integrada"]
        self.assertTrue(t["id_operacion"].is_unique)
        self.assertEqual(t["fuente"].value_counts().to_dict(), {"SPEI": 358, "ATM": 354})

    def test_columnas_del_contrato(self):
        t = self.r["integrada"]
        for c in COLUMNAS_INTEGRADA + ["monto_mxn_minmax", "monto_mxn_categoria", "franja_horaria"]:
            self.assertIn(c, t.columns)

    def test_montos_correctos(self):
        t = self.r["integrada"].set_index("id_operacion")
        s = self.limpio["spei"].set_index("transfer_id")
        usd = s[s["currency"] == "USD"].index.intersection(t.index)[0]
        self.assertAlmostEqual(t.loc[usd, "monto_mxn"], round(s.loc[usd, "amount"] * 19.5, 2), places=2)

    def test_sin_nulos_en_campos_clave(self):
        t = self.r["integrada"]
        for c in ("fecha_hora", "monto_mxn", "id_cuenta", "resultado", "monto_mxn_minmax", "monto_mxn_categoria", "franja_horaria"):
            self.assertFalse(t[c].isna().any(), c)

    def test_minmax_entre_0_y_1(self):
        m = self.r["integrada"]["monto_mxn_minmax"]
        self.assertTrue(((m >= 0) & (m <= 1)).all())

    def test_solo_cuentas_activas(self):
        self.assertEqual(set(self.r["integrada"]["estado_cuenta_catalogo"]), {"ACTIVA"})

    def test_no_modifica_entrada(self):
        d2 = cargar_datos(); l2 = limpiar(d2["atm"], d2["spei"], guardar=False)
        antes = l2["atm"].copy()
        ejecutar_transformacion(l2, d2["catalogo"])
        pd.testing.assert_frame_equal(antes, l2["atm"])


if __name__ == "__main__":
    unittest.main()
