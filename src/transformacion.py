"""Ingeniero 2 - Etapa 1: homologación de fechas, timestamps y estados.

Recibe la salida de ``limpiar()`` (Ingeniero 1) y no modifica sus columnas.
Todo queda en hora de México (UTC-6). Lo que no se puede interpretar queda
nulo y marcado con una bandera; nunca se descarta una fila.
"""
import numpy as np
import pandas as pd

ZONA = "America/Mexico_City"  # UTC-6 todo el periodo (sin horario de verano)
FORMATOS_ATM = {
    "YYYY-MM-DD HH:MM:SS": "%Y-%m-%d %H:%M:%S",
    "YYYY/MM/DD HH:MM:SS": "%Y/%m/%d %H:%M:%S",
    "DD/MM/YYYY HH:MM": "%d/%m/%Y %H:%M",  # día primero
}
ESTADOS_ATM = {"EXITOSA", "FALLO_CONEXION", "FALLO_FONDOS"}
ESTADOS_SPEI = {"LIQUIDADA", "DEVUELTA", "RECHAZADA", "PENDIENTE"}


def _parsear_atm(texto):
    """Devuelve (fecha con zona o NaT, nombre del formato o 'INVALIDO')."""
    if isinstance(texto, str):
        for nombre, fmt in FORMATOS_ATM.items():
            try:
                f = pd.to_datetime(texto.strip(), format=fmt)
            except (ValueError, TypeError):
                continue
            return f.tz_localize(ZONA), nombre
    return pd.NaT, "INVALIDO"


def _parsear_spei(valor):
    """Devuelve (fecha con zona o NaT, 'ISO' | 'EPOCH' | 'INVALIDO')."""
    try:
        if isinstance(valor, bool) or pd.isna(valor):
            return pd.NaT, "INVALIDO"
        if isinstance(valor, (int, float)) or (isinstance(valor, str) and valor.strip().lstrip("-").isdigit()):
            f = pd.to_datetime(int(valor), unit="s", utc=True)
            return f.tz_convert(ZONA), "EPOCH"
        f = pd.to_datetime(str(valor).strip(), format="ISO8601", utc=True)
        return f.tz_convert(ZONA), "ISO"
    except (ValueError, TypeError, OverflowError):
        return pd.NaT, "INVALIDO"


def _normalizar_estado(serie, esperados):
    original = serie.copy()
    norm = serie.astype("string").str.strip().str.upper()
    cambio = (norm != original.astype("string")).fillna(False).astype(bool)
    fuera = sorted(set(norm.dropna()) - esperados)
    return original, norm.astype(object), cambio, fuera


def transformar_fechas_atm(atm):
    """Homologa ``fecha_hora`` de ATM a fecha con zona UTC-6."""
    df = atm.copy()
    res = df["fecha_hora"].map(_parsear_atm)
    df["fecha_hora_original"] = df["fecha_hora"]
    df["formato_fecha_atm"] = [r[1] for r in res]
    df["fecha_invalida"] = df["formato_fecha_atm"] == "INVALIDO"
    df["fecha_hora"] = pd.Series([r[0] for r in res], index=df.index, dtype=f"datetime64[ns, {ZONA}]")
    return df


def transformar_timestamps_spei(spei):
    """Homologa ``timestamp`` de SPEI (ISO o epoch) a fecha con zona UTC-6."""
    df = spei.copy()
    res = df["timestamp"].map(_parsear_spei)
    df["timestamp_original"] = df["timestamp"]
    df["formato_timestamp"] = [r[1] for r in res]
    df["timestamp_invalido"] = df["formato_timestamp"] == "INVALIDO"
    df["timestamp"] = pd.Series([r[0] for r in res], index=df.index, dtype=f"datetime64[ns, {ZONA}]")
    return df


def normalizar_estados(df, columna, esperados):
    """Pasa ``columna`` a mayúsculas; guarda original y si cambió.

    Returns: (df nuevo, lista de estados fuera de la lista esperada).
    """
    df = df.copy()
    orig, norm, cambio, fuera = _normalizar_estado(df[columna], esperados)
    df[f"{columna}_original"] = orig
    df[columna] = norm
    df[f"{columna}_normalizado"] = cambio
    return df, fuera


def etapa1_fechas_y_categorias(atm, spei):
    """Aplica la etapa 1 a las tablas ``atm`` y ``spei`` de ``limpiar()``.

    Returns: dict con ``atm``, ``spei``, ``resumen_etapa1`` y
    ``estados_fuera_catalogo``.
    """
    a = transformar_fechas_atm(atm)
    a, fuera_a = normalizar_estados(a, "resultado", ESTADOS_ATM)
    s = transformar_timestamps_spei(spei)
    s, fuera_s = normalizar_estados(s, "status", ESTADOS_SPEI)
    resumen = pd.DataFrame([
        ("ATM", "filas", len(a)),
        *[("ATM", f"formato {k}", int((a["formato_fecha_atm"] == k).sum())) for k in FORMATOS_ATM],
        ("ATM", "fechas inválidas", int(a["fecha_invalida"].sum())),
        ("ATM", "resultado normalizados", int(a["resultado_normalizado"].sum())),
        ("SPEI", "filas", len(s)),
        *[("SPEI", f"formato {k}", int((s["formato_timestamp"] == k).sum())) for k in ("ISO", "EPOCH")],
        ("SPEI", "timestamps inválidos", int(s["timestamp_invalido"].sum())),
        ("SPEI", "status normalizados", int(s["status_normalizado"].sum())),
    ], columns=["fuente", "indicador", "valor"])
    return {"atm": a, "spei": s, "resumen_etapa1": resumen,
            "estados_fuera_catalogo": {"atm": fuera_a, "spei": fuera_s}}


# ---------------------------------------------------------------------------
# Etapa 2: moneda (SPEI en USD -> MXN)
# ---------------------------------------------------------------------------
TASA_USD_MXN = 19.50  # confirmada por el equipo; cambiar aquí si se actualiza


def etapa2_moneda(spei, tasa=TASA_USD_MXN):
    """Convierte los montos de SPEI a pesos, sin quitar nada de lo anterior.

    Se convierten los DOS montos del Ingeniero 1 (decisión del equipo):
    ``amount`` (recortado) y ``amount_original`` (el que llegó). El capping
    ya se hizo por moneda antes; aquí NO se recalcula.

    Columnas nuevas: ``tasa_aplicada``, ``amount_mxn``, ``amount_original_mxn``
    y ``moneda_desconocida`` (True si la moneda no es MXN ni USD; su monto
    convertido queda nulo, la fila no se descarta).

    Returns: dict con ``spei`` y ``resumen_etapa2``.
    """
    if tasa is None or tasa <= 0:
        raise ValueError("La tasa debe ser un número mayor que cero.")
    df = spei.copy()
    moneda = df["currency"].astype("string").str.strip().str.upper()
    factor = moneda.map({"MXN": 1.0, "USD": float(tasa)}).astype(float)
    df["moneda_desconocida"] = factor.isna().to_numpy()
    df["tasa_aplicada"] = factor
    df["amount_mxn"] = (df["amount"] * factor).round(2)
    df["amount_original_mxn"] = (df["amount_original"] * factor).round(2)
    resumen = pd.DataFrame([
        ("filas", len(df)),
        ("en MXN (sin cambio)", int((moneda == "MXN").sum())),
        ("en USD (convertidas)", int((moneda == "USD").sum())),
        ("moneda desconocida", int(df["moneda_desconocida"].sum())),
        ("tasa USD/MXN", float(tasa)),
    ], columns=["indicador", "valor"])
    return {"spei": df, "resumen_etapa2": resumen}


# ---------------------------------------------------------------------------
# Etapa 3: cruce con el catálogo de cuentas
# ---------------------------------------------------------------------------
ESTADOS_RECHAZO = ("BLOQUEADA", "SUSPENDIDA")


def _cruzar(df, col_cuenta, catalogo):
    cat = catalogo[["id_cuenta", "tipo_cuenta", "estado_cuenta"]].rename(
        columns={"id_cuenta": "_id_cat", "estado_cuenta": "estado_cuenta_catalogo"})
    out = df.copy()
    llave = out[col_cuenta].astype("string").str.strip().str.upper()
    out = out.assign(_llave=llave).merge(cat, left_on="_llave", right_on="_id_cat", how="left")
    out["cuenta_en_catalogo"] = out["_id_cat"].notna()
    out["estado_cuenta_catalogo"] = out["estado_cuenta_catalogo"].astype("string").str.strip().str.upper()
    out = out.drop(columns=["_llave", "_id_cat"])
    out.index = df.index
    return out


def _separar(df):
    huerfanas = df[~df["cuenta_en_catalogo"]].copy()
    huerfanas["motivo_separacion"] = "CUENTA_HUERFANA"
    malas = df[df["cuenta_en_catalogo"] & df["estado_cuenta_catalogo"].isin(ESTADOS_RECHAZO)].copy()
    malas["motivo_separacion"] = "CUENTA_" + malas["estado_cuenta_catalogo"]
    aceptadas = df.drop(index=huerfanas.index.union(malas.index))
    return aceptadas, huerfanas, malas


def etapa3_catalogo(atm, spei, catalogo):
    """Cruza ATM y SPEI con el catálogo y separa lo que no se puede aceptar.

    Reglas del equipo:
      * Cuenta que no existe en el catálogo (huérfana) -> se SEPARA.
      * Cuenta BLOQUEADA o SUSPENDIDA -> se RECHAZA (por ahora).
    Nada se borra: lo separado y lo rechazado van a tablas aparte, con la
    columna ``motivo_separacion``, para que el Ingeniero 3 decida su
    cuarentena. Las aceptadas llevan ``tipo_cuenta`` y
    ``estado_cuenta_catalogo``.

    Returns: dict con ``atm``, ``spei`` (aceptadas), ``huerfanas_atm``,
    ``huerfanas_spei``, ``rechazadas_cuenta_atm``, ``rechazadas_cuenta_spei``
    y ``resumen_etapa3``.
    """
    a = _cruzar(atm, "id_cuenta", catalogo)
    s = _cruzar(spei, "account_id", catalogo)
    a_ok, a_h, a_r = _separar(a)
    s_ok, s_h, s_r = _separar(s)
    filas = []
    for nombre, total, ok, h, r in (("ATM", a, a_ok, a_h, a_r), ("SPEI", s, s_ok, s_h, s_r)):
        filas += [(nombre, "filas que entran", len(total)),
                  (nombre, "huérfanas (separadas)", len(h)),
                  (nombre, "cuenta BLOQUEADA (rechazadas)", int((r["motivo_separacion"] == "CUENTA_BLOQUEADA").sum())),
                  (nombre, "cuenta SUSPENDIDA (rechazadas)", int((r["motivo_separacion"] == "CUENTA_SUSPENDIDA").sum())),
                  (nombre, "aceptadas", len(ok))]
    return {"atm": a_ok, "spei": s_ok,
            "huerfanas_atm": a_h, "huerfanas_spei": s_h,
            "rechazadas_cuenta_atm": a_r, "rechazadas_cuenta_spei": s_r,
            "resumen_etapa3": pd.DataFrame(filas, columns=["fuente", "indicador", "valor"])}


# ---------------------------------------------------------------------------
# Etapa 5: integración de ATM y SPEI en una sola tabla
# ---------------------------------------------------------------------------
COLUMNAS_INTEGRADA = [
    "id_operacion", "fuente", "fecha_hora", "id_cuenta", "tipo_operacion",
    "monto_mxn", "monto_original_mxn", "moneda_origen", "resultado", "canal",
    "comision_mxn", "tipo_cuenta", "estado_cuenta_catalogo",
    "monto_imputado", "monto_recortado",
    # datos propios de cada fuente (vacíos en la otra)
    "id_cajero", "cajero_imputado", "beneficiary_clabe", "notification_email",
    "email_corregido", "concept",
]


def etapa5_integrar(atm, spei):
    """Junta ATM y SPEI (ya aceptadas) en una tabla, una fila por operación.

    ``fuente`` indica el origen (``ATM`` o ``SPEI``). Los nombres se unifican:
    en ATM, ``monto_mxn``; en SPEI, ``amount_mxn`` (ya convertido a pesos).
    ``canal`` es ``id_cajero`` en ATM y ``platform`` en SPEI. A las
    transferencias se les pone ``tipo_operacion = TRANSFERENCIA_SPEI``.
    Lo que una fuente no tiene queda vacío; no se inventa nada.
    """
    a = pd.DataFrame({
        "id_operacion": atm["id_transaccion"], "fuente": "ATM",
        "fecha_hora": atm["fecha_hora"], "id_cuenta": atm["id_cuenta"],
        "tipo_operacion": atm["tipo_operacion"],
        "monto_mxn": atm["monto_mxn"], "monto_original_mxn": atm["monto_mxn_original"],
        "moneda_origen": "MXN", "resultado": atm["resultado"], "canal": atm["id_cajero"],
        "comision_mxn": atm["comision_aplicada"],
        "tipo_cuenta": atm["tipo_cuenta"], "estado_cuenta_catalogo": atm["estado_cuenta_catalogo"],
        "monto_imputado": atm["monto_imputado"], "monto_recortado": atm["monto_recortado"],
        "id_cajero": atm["id_cajero"], "cajero_imputado": atm["cajero_imputado"],
    })
    s = pd.DataFrame({
        "id_operacion": spei["transfer_id"], "fuente": "SPEI",
        "fecha_hora": spei["timestamp"], "id_cuenta": spei["account_id"],
        "tipo_operacion": "TRANSFERENCIA_SPEI",
        "monto_mxn": spei["amount_mxn"], "monto_original_mxn": spei["amount_original_mxn"],
        "moneda_origen": spei["currency"], "resultado": spei["status"], "canal": spei["platform"],
        "tipo_cuenta": spei["tipo_cuenta"], "estado_cuenta_catalogo": spei["estado_cuenta_catalogo"],
        "monto_imputado": False, "monto_recortado": spei["amount_recortado"],
        "beneficiary_clabe": spei["beneficiary_clabe"], "notification_email": spei["notification_email"],
        "email_corregido": spei["email_corregido"], "concept": spei["concept"],
    })
    out = pd.concat([a, s], ignore_index=True)
    for c in COLUMNAS_INTEGRADA:
        if c not in out.columns:
            out[c] = np.nan
    out = out[COLUMNAS_INTEGRADA]
    return out.sort_values(["fecha_hora", "id_operacion"], kind="stable", ignore_index=True)


# ---------------------------------------------------------------------------
# Etapa 4: Min-Max y discretización (sobre la tabla integrada)
# ---------------------------------------------------------------------------
ETIQUETAS_MONTO = ["BAJO", "MEDIO", "ALTO", "MUY_ALTO"]


def minmax(df, columna="monto_mxn", por="fuente", salida=None):
    """Escala ``columna`` entre 0 y 1: (x - min) / (max - min).

    Por defecto se escala por separado en cada ``fuente``, porque un retiro
    de cajero (máx. unos miles) y una transferencia (cientos de miles) no
    tienen la misma escala. Con ``por=None`` se escala todo junto. Si todos
    los valores son iguales, el resultado es 0.0. Los nulos siguen nulos.
    El valor sin escalar se conserva.

    Returns: (df nuevo, dict con el mínimo y máximo usados por grupo).
    """
    salida = salida or f"{columna}_minmax"
    df = df.copy()
    grupos = df.groupby(por) if por else [("TODOS", df)]
    params, resultado = {}, pd.Series(np.nan, index=df.index, dtype=float)
    for nombre, g in grupos:
        x = g[columna].astype(float)
        lo, hi = x.min(), x.max()
        params[nombre] = {"min": None if pd.isna(lo) else float(lo), "max": None if pd.isna(hi) else float(hi)}
        resultado.loc[g.index] = 0.0 if hi == lo else (x - lo) / (hi - lo)
        if pd.isna(lo):
            resultado.loc[g.index] = np.nan
    df[salida] = resultado.where(df[columna].notna())
    return df, params


def discretizar_monto(df, columna="monto_mxn", por="fuente", etiquetas=ETIQUETAS_MONTO,
                      cortes=None, salida=None):
    """Convierte el monto en categorías (BAJO, MEDIO, ALTO, MUY_ALTO).

    Sin ``cortes``, los límites son los cuartiles de cada ``fuente``
    (así cada fuente se compara contra sí misma). Con ``cortes`` (lista de
    límites internos, por ejemplo ``[1000, 10000, 50000]``) se usan los mismos
    límites para todos. Si algunos cuartiles se repiten, quedan menos
    categorías en vez de inventar cortes.

    Returns: (df nuevo, dict con los límites usados por grupo).
    """
    salida = salida or f"{columna}_categoria"
    df = df.copy()
    grupos = df.groupby(por) if por else [("TODOS", df)]
    limites, cat = {}, pd.Series(pd.NA, index=df.index, dtype="object")
    for nombre, g in grupos:
        x = g[columna].astype(float)
        if x.notna().sum() == 0:
            limites[nombre] = []
            continue
        if cortes is not None:
            internos = sorted(set(float(c) for c in cortes))
        else:
            q = np.linspace(0, 1, len(etiquetas) + 1)[1:-1]
            internos = sorted(set(float(v) for v in x.quantile(q)))
            internos = [v for v in internos if x.min() < v < x.max()] if x.min() < x.max() else []
        bordes = [-np.inf] + internos + [np.inf]
        n = len(bordes) - 1
        # con límites propios, cada categoría es (a, b]; el mínimo cae en la primera
        cat.loc[g.index] = pd.cut(x, bins=bordes, labels=list(etiquetas)[:n], right=True).astype(object)
        limites[nombre] = internos
    df[salida] = cat.where(df[columna].notna())
    return df, limites


def franja_horaria(df, columna="fecha_hora", salida="franja_horaria"):
    """Agrupa la hora local en MADRUGADA (0-5), MANANA (6-11), TARDE (12-17) y NOCHE (18-23)."""
    df = df.copy()
    h = df[columna].dt.hour
    df[salida] = pd.cut(h, bins=[-1, 5, 11, 17, 23], labels=["MADRUGADA", "MANANA", "TARDE", "NOCHE"]).astype(object)
    return df


def etapa4_minmax_discretizacion(integrada, cortes_monto=None):
    """Aplica Min-Max y discretización a la tabla integrada.

    Agrega ``monto_mxn_minmax``, ``monto_mxn_categoria`` y ``franja_horaria``.
    Devuelve ``tabla``, ``parametros_minmax`` y ``limites_discretizacion``.
    """
    t, p = minmax(integrada, "monto_mxn")
    t, lim = discretizar_monto(t, "monto_mxn", cortes=cortes_monto)
    t = franja_horaria(t)
    return {"tabla": t, "parametros_minmax": p, "limites_discretizacion": lim}


# ---------------------------------------------------------------------------
# Etapa 6: flujo completo del Ingeniero 2
# ---------------------------------------------------------------------------
def ejecutar_transformacion(limpio, catalogo, tasa=TASA_USD_MXN, cortes_monto=None):
    """Corre las etapas 1 a 5 sobre la salida de ``limpiar()``.

    Args:
        limpio: dict que devuelve ``limpiar(..., guardar=False)``.
        catalogo: DataFrame del catálogo de cuentas.

    Returns: dict con ``integrada`` (lista para el Ingeniero 3),
    ``huerfanas``, ``rechazadas_cuenta`` (ambas con ``fuente``),
    ``parametros_minmax``, ``limites_discretizacion``, ``estados_fuera_catalogo``
    y ``resumen`` (conteos de cada etapa). No escribe archivos.
    """
    e1 = etapa1_fechas_y_categorias(limpio["atm"], limpio["spei"])
    e2 = etapa2_moneda(e1["spei"], tasa=tasa)
    e3 = etapa3_catalogo(e1["atm"], e2["spei"], catalogo)
    integrada = etapa5_integrar(e3["atm"], e3["spei"])
    e4 = etapa4_minmax_discretizacion(integrada, cortes_monto=cortes_monto)
    huerf = pd.concat([e3["huerfanas_atm"].assign(fuente="ATM"), e3["huerfanas_spei"].assign(fuente="SPEI")], ignore_index=True)
    rech = pd.concat([e3["rechazadas_cuenta_atm"].assign(fuente="ATM"), e3["rechazadas_cuenta_spei"].assign(fuente="SPEI")], ignore_index=True)
    resumen = pd.concat([e1["resumen_etapa1"].assign(etapa=1), e2["resumen_etapa2"].assign(etapa=2, fuente="SPEI"),
                         e3["resumen_etapa3"].assign(etapa=3)], ignore_index=True)
    return {"integrada": e4["tabla"], "huerfanas": huerf, "rechazadas_cuenta": rech,
            "parametros_minmax": e4["parametros_minmax"], "limites_discretizacion": e4["limites_discretizacion"],
            "estados_fuera_catalogo": e1["estados_fuera_catalogo"], "resumen": resumen}
