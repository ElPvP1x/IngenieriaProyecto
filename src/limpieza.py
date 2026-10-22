"""
Módulo del Ingeniero/a 1: Calidad, Nulos y Outliers.

Fase 1 (implementada):
    - Carga de los datasets originales (solo lectura).
    - Perfilado inicial de calidad.
    - Detección y separación de duplicados exactos, con trazabilidad.
    - Generación de los primeros reportes de calidad.

Fase 2 (implementada):
    - Análisis de faltantes respecto a otras variables observadas.
    - Imputación de monto_mxn (mediana por tipo_operacion), id_cajero
      (CAJERO_DESCONOCIDO) y notification_email (SIN_CORREO), con banderas.
    - comision_aplicada no se imputa: no tiene nulos reales y sus ceros son
      valores válidos.

Fase 3 (implementada):
    - Separación de registros con montos imposibles (negativos, cero en
      operaciones que mueven dinero, no numéricos o faltantes no imputables),
      preservados para la cuarentena del Ingeniero 3.
    - Capping de outliers con IQR (factor 1.5): ATM sin CONSULTA_SALDO y
      SPEI por moneda, conservando el monto original y una bandera.

Reportes:
    - Comparativo de calidad por etapa (original, sin duplicados, final).
    - Boxplots antes y después del capping (ATM, SPEI MXN y SPEI USD),
      generados por graficar_boxplots() con matplotlib.

Fuera del alcance de este módulo:
    - Homologación de fechas, monedas, categorías e integración con el
      catálogo (Ingeniero 2).
    - Validación, cuarentena y persistencia (Ingeniero 3).

Uso desde main.py (sin escrituras en disco):
    from src.limpieza import cargar_datos, limpiar
    datos = cargar_datos()
    resultado = limpiar(datos["atm"], datos["spei"], guardar=False)

Uso directo (genera perfilado, datasets procesados, reportes y gráficas):
    python -m src.limpieza
"""

import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
DIR_PROCESADOS = RAIZ / "data" / "processed"
DIR_REPORTES = RAIZ / "reportes"

ARCHIVO_CATALOGO = "catalogo_cuentas.csv"
ARCHIVO_ATM = "transacciones_atm.csv"
ARCHIVO_SPEI = "transferencias_digitales.json"

# Identificadores internos de fuente usados en todos los reportes. El nombre
# del archivo se conserva aparte, en la columna archivo_origen.
FUENTE_ATM = "atm"
FUENTE_SPEI = "spei"
ARCHIVOS_ORIGEN = {
    "catalogo": ARCHIVO_CATALOGO,
    FUENTE_ATM: ARCHIVO_ATM,
    FUENTE_SPEI: ARCHIVO_SPEI,
}

# Columnas de monto y de agrupación usadas en el perfil de montos.
COLUMNAS_MONTO = {
    "atm": ("monto_mxn", "tipo_operacion"),
    "spei": ("amount", "currency"),
}

# Valores de sustitución para faltantes categóricos.
CAJERO_DESCONOCIDO = "CAJERO_DESCONOCIDO"
SIN_CORREO = "SIN_CORREO"

# Formato mínimo de correo: texto@dominio.ext, sin espacios ni "@" repetida.
PATRON_EMAIL = r"[^@\s]+@[^@\s]+\.[^@\s]+"

# Única operación ATM que puede tener monto cero.
TIPO_CONSULTA = "CONSULTA_SALDO"

# Motivos de rechazo por monto imposible, en orden de prioridad.
MOTIVOS_RECHAZO = ["FALTANTE", "NO_NUMERICO", "NEGATIVO", "CERO_NO_PERMITIDO"]

# Factor del método IQR.
FACTOR_IQR = 1.5

COLUMNAS_LIMITES_IQR = [
    "columna", "agrupado_por", "grupo", "registros_calculo",
    "registros_aplicados", "q1", "q3", "iqr", "limite_inferior",
    "limite_superior", "outliers_inferiores", "outliers_superiores", "outliers",
]

# Variables observadas con las que se compara la tasa de faltantes.
# formato_fecha_hora y tipo_timestamp se derivan solo para el análisis
# (ver _variables_derivadas); no se agregan a los datos.
VARIABLES_ANALISIS_FALTANTES = {
    "atm": ["tipo_operacion", "resultado", "formato_fecha_hora"],
    "spei": ["platform", "currency", "status", "tipo_timestamp"],
}


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------

def cargar_datos(raiz=RAIZ):
    """Lee los tres datasets originales sin modificarlos.

    Los identificadores y la CLABE se leen como texto para no perder ceros a
    la izquierda. Las fechas se dejan tal como vienen (texto o entero epoch),
    porque su homologación corresponde al Ingeniero 2.

    Args:
        raiz: carpeta donde están los archivos originales.

    Returns:
        dict con los DataFrames "catalogo", "atm" y "spei".
    """
    raiz = Path(raiz)
    catalogo = pd.read_csv(
        raiz / ARCHIVO_CATALOGO, dtype={"id_cuenta": str}, encoding="utf-8"
    )
    atm = pd.read_csv(
        raiz / ARCHIVO_ATM,
        dtype={"id_transaccion": str, "id_cuenta": str, "id_cajero": str,
               "fecha_hora": str},
        encoding="utf-8",
    )
    # json.load conserva el tipo original de cada valor (p. ej. timestamp
    # mezclado str/int); pd.read_json intentaría convertirlos.
    with open(raiz / ARCHIVO_SPEI, encoding="utf-8") as f:
        spei = pd.DataFrame(json.load(f))
    return {"catalogo": catalogo, "atm": atm, "spei": spei}


# ---------------------------------------------------------------------------
# Perfilado
# ---------------------------------------------------------------------------

def _tipos_python(serie):
    """Cuenta los tipos de Python de los valores no nulos de una columna.

    Sirve para detectar columnas con tipos mezclados, como un timestamp que
    combina texto ISO y enteros epoch.
    """
    conteo = Counter(type(v).__name__ for v in serie.dropna())
    return ", ".join(f"{tipo}({n})" for tipo, n in conteo.most_common())


def perfilar_columnas(df, nombre):
    """Genera el perfil de calidad de cada columna de un DataFrame.

    Args:
        df: DataFrame a perfilar.
        nombre: identificador de la fuente (p. ej. "atm").

    Returns:
        DataFrame con una fila por columna: tipo, nulos, cadenas vacías,
        valores únicos y un ejemplo.
    """
    filas = []
    total = len(df)
    for columna in df.columns:
        serie = df[columna]
        nulos = int(serie.isna().sum())
        vacios = int(
            serie.dropna().map(lambda v: isinstance(v, str) and v.strip() == "").sum()
        )
        no_nulos = serie.dropna()
        filas.append({
            "fuente": nombre,
            "columna": columna,
            "tipo_pandas": str(serie.dtype),
            "tipos_python": _tipos_python(serie),
            "registros": total,
            "nulos": nulos,
            "pct_nulos": round(100 * nulos / total, 2) if total else 0.0,
            "vacios": vacios,
            "unicos": int(serie.astype(str).nunique()),
            "ejemplo": no_nulos.iloc[0] if len(no_nulos) else None,
        })
    return pd.DataFrame(filas)


def resumir_dataset(df, nombre):
    """Resume tamaño y duplicados de un dataset.

    Args:
        df: DataFrame a resumir.
        nombre: identificador de la fuente (p. ej. "atm").

    Returns:
        dict con registros, columnas, duplicados exactos y duplicados del
        identificador (primera columna).
    """
    columna_id = df.columns[0]
    return {
        "fuente": nombre,
        "registros": len(df),
        "columnas": df.shape[1],
        "columna_id": columna_id,
        "duplicados_exactos": int(df.duplicated().sum()),
        "ids_duplicados": int(df[columna_id].duplicated().sum()),
        "celdas_nulas": int(df.isna().sum().sum()),
    }


def perfilar_montos(df, columna, agrupar_por):
    """Calcula estadísticas descriptivas de una columna de monto.

    Se calcula para el total y por grupo (por ejemplo, por tipo de operación
    o por moneda), porque mezclar grupos con escalas distintas distorsiona
    las estadísticas.

    Args:
        df: DataFrame con la columna de monto.
        columna: nombre de la columna de monto.
        agrupar_por: columna para agrupar.

    Returns:
        DataFrame con una fila por grupo más una fila "TOTAL".
    """
    def _estadisticas(serie, grupo):
        valores = serie.dropna()
        return {
            "columna": columna,
            "agrupado_por": agrupar_por,
            "grupo": grupo,
            "registros": len(serie),
            "nulos": int(serie.isna().sum()),
            "ceros": int((valores == 0).sum()),
            "negativos": int((valores < 0).sum()),
            "minimo": valores.min(),
            "q1": valores.quantile(0.25),
            "mediana": valores.median(),
            "q3": valores.quantile(0.75),
            "maximo": valores.max(),
            "media": round(valores.mean(), 2),
            "desv_std": round(valores.std(), 2),
        }

    filas = [_estadisticas(g[columna], grupo)
             for grupo, g in df.groupby(agrupar_por, dropna=False)]
    filas.append(_estadisticas(df[columna], "TOTAL"))
    return pd.DataFrame(filas)


# ---------------------------------------------------------------------------
# Duplicados
# ---------------------------------------------------------------------------

def separar_duplicados(df):
    """Separa los duplicados exactos conservando la primera aparición.

    No se pierde información: las filas retiradas se devuelven aparte con
    tres columnas de trazabilidad:
        - indice_original: posición (0 = primera fila) del registro en el
          DataFrame recibido, sin importar su índice de pandas.
        - etiqueta_indice: etiqueta del índice de pandas de esa fila.
        - duplicado_de: posición de la fila que se conservó.

    Args:
        df: DataFrame original.

    Returns:
        tupla (df_sin_duplicados, df_duplicados).
    """
    es_duplicado = df.duplicated(keep="first").to_numpy()
    grupo = df.groupby(list(df.columns), dropna=False, sort=False).ngroup().to_numpy()
    posicion = pd.Series(np.arange(len(df)))
    primera_aparicion = posicion.groupby(grupo).transform("first").to_numpy()

    duplicados = df[es_duplicado].copy()
    duplicados.insert(0, "indice_original", posicion[es_duplicado].to_numpy())
    duplicados.insert(1, "etiqueta_indice", duplicados.index)
    duplicados.insert(2, "duplicado_de", primera_aparicion[es_duplicado])
    return df[~es_duplicado].copy(), duplicados.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Valores faltantes
# ---------------------------------------------------------------------------

def clasificar_email(serie):
    """Clasifica cada correo como VALIDO, NULO, VACIO o INVALIDO.

    Los espacios al principio y al final se eliminan (strip) antes de
    clasificar, así que " ana@banco.com " es VALIDO.

    Args:
        serie: columna notification_email.

    Returns:
        Series de texto con la clasificación de cada registro.
    """
    texto = serie.astype("string").str.strip()
    es_nulo = texto.isna()
    es_vacio = ~es_nulo & (texto == "")
    es_valido = ~es_nulo & texto.str.fullmatch(PATRON_EMAIL).fillna(False)

    clase = pd.Series("INVALIDO", index=serie.index)
    clase[es_valido] = "VALIDO"
    clase[es_vacio] = "VACIO"
    clase[es_nulo] = "NULO"
    return clase


def tasas_faltantes(df, es_faltante, variables, nombre, columna):
    """Compara la tasa de faltantes entre las categorías de otras variables.

    Es un análisis descriptivo para orientar sobre el mecanismo (MCAR/MAR);
    no es una prueba estadística.

    Args:
        df: DataFrame analizado.
        es_faltante: Series booleana que marca los registros faltantes.
        variables: columnas observadas con las que se compara.
        nombre: identificador de la fuente ("atm" o "spei").
        columna: columna cuyos faltantes se analizan.

    Returns:
        DataFrame con faltantes, registros y porcentaje por categoría.
    """
    filas = []
    for variable in variables:
        grupos = es_faltante.groupby(df[variable], dropna=False)
        for categoria, g in grupos:
            filas.append({
                "fuente": nombre,
                "columna_faltante": columna,
                "variable": variable,
                "categoria": categoria,
                "faltantes": int(g.sum()),
                "registros": len(g),
                "pct_faltantes": round(100 * g.mean(), 2),
            })
    return pd.DataFrame(filas)


def _variables_derivadas(atm, spei):
    """Agrega, en copias, variables derivadas solo para analizar faltantes.

    - formato_fecha_hora: patrón de fecha_hora con cada dígito como "9"
      (p. ej. "9999/99/99 99:99:99"); aproxima el sistema de origen.
    - tipo_timestamp: tipo de Python del timestamp SPEI (str o int).
    No homologa ni modifica fechas: las copias no salen de imputar().
    """
    if "fecha_hora" in atm.columns:
        atm = atm.assign(formato_fecha_hora=atm["fecha_hora"].astype("string")
                         .str.replace(r"\d", "9", regex=True))
    if "timestamp" in spei.columns:
        spei = spei.assign(tipo_timestamp=spei["timestamp"].map(lambda v: type(v).__name__))
    return atm, spei


def imputar_montos(atm):
    """Imputa monto_mxn con la mediana de su tipo_operacion.

    - monto_mxn_original conserva el valor tal como venía en la fuente
      (nulo en los imputados). Servirá también como referencia en el capping.
    - monto_imputado marca los registros cuyo monto se rellenó.
    - Si un tipo_operacion no tiene ningún monto válido (o el tipo es nulo),
      no hay mediana: el monto queda nulo y monto_imputado es False, para no
      inventar un valor.
    - Los ceros (p. ej. CONSULTA_SALDO) se conservan: no son faltantes.
    - Solo se imputan nulos reales. Los valores no numéricos o imposibles
      no se imputan ni entran en la mediana, para que no queden ocultos y
      separar_imposibles_atm() pueda rechazarlos.

    Args:
        atm: DataFrame de transacciones ATM.

    Returns:
        tupla (atm_imputado, medianas), donde medianas tiene una fila por
        tipo_operacion con la mediana usada y los registros imputados.
    """
    atm = atm.copy()
    original = atm["monto_mxn"]
    numerico = pd.to_numeric(original, errors="coerce")
    era_nulo = original.isna()
    validos = numerico.where(
        motivo_monto_imposible(original, permite_cero=_permite_cero_atm(atm)).isna()
    )
    mediana_grupo = validos.groupby(atm["tipo_operacion"]).transform("median")

    atm["monto_mxn_original"] = original
    atm["monto_mxn"] = numerico.mask(era_nulo, mediana_grupo)
    atm["monto_imputado"] = era_nulo & atm["monto_mxn"].notna()

    medianas = (
        atm.assign(era_nulo=era_nulo, monto_valido=validos)
        .groupby("tipo_operacion", dropna=False)
        .agg(mediana_usada=("monto_valido", "median"),
             montos_validos=("monto_valido", "count"),
             nulos_antes=("era_nulo", "sum"),
             imputados=("monto_imputado", "sum"))
        .reset_index()
    )
    medianas.insert(0, "fuente", FUENTE_ATM)
    return atm, medianas


def imputar_cajeros(atm):
    """Sustituye los id_cajero nulos por CAJERO_DESCONOCIDO.

    Args:
        atm: DataFrame de transacciones ATM.

    Returns:
        DataFrame con id_cajero rellenado y la bandera cajero_imputado.
    """
    atm = atm.copy()
    atm["cajero_imputado"] = atm["id_cajero"].isna()
    atm["id_cajero"] = atm["id_cajero"].fillna(CAJERO_DESCONOCIDO)
    return atm


def corregir_emails(spei):
    """Sustituye los correos nulos, vacíos o inválidos por SIN_CORREO.

    - notification_email_original conserva el valor original.
    - email_motivo guarda la clasificación (VALIDO, NULO, VACIO, INVALIDO).
    - email_corregido marca los registros sustituidos.
    - Los correos válidos se conservan sin espacios al principio ni al
      final; email_normalizado marca los que cambiaron por ese strip().
      No se cambian mayúsculas ni ningún otro carácter.

    Args:
        spei: DataFrame de transferencias digitales.

    Returns:
        DataFrame con los correos corregidos y sus columnas de trazabilidad.
    """
    spei = spei.copy()
    original = spei["notification_email"]
    motivo = clasificar_email(original)
    es_valido = motivo == "VALIDO"
    normalizado = original.astype("string").str.strip()

    spei["notification_email_original"] = original
    spei["email_motivo"] = motivo
    spei["email_corregido"] = ~es_valido
    cambio = (normalizado != original.astype("string")).fillna(False).astype(bool)
    spei["email_normalizado"] = es_valido & cambio
    spei["notification_email"] = normalizado.where(es_valido, SIN_CORREO).astype(original.dtype)
    return spei


def imputar(atm, spei):
    """Aplica el tratamiento de faltantes del Ingeniero 1.

    Debe ejecutarse sobre datos ya sin duplicados. No modifica los
    DataFrames de entrada ni cambia fechas, monedas, categorías ni
    comisiones (comision_aplicada no tiene nulos reales y sus ceros son
    válidos).

    Args:
        atm: DataFrame de transacciones ATM.
        spei: DataFrame de transferencias digitales.

    Returns:
        dict con:
            - "atm", "spei": datasets imputados con sus banderas.
            - "medianas": medianas usadas por tipo_operacion.
            - "resumen": faltantes antes/después por columna.
            - "analisis": tasas de faltantes por variable observada.
    """
    nulos_monto = int(atm["monto_mxn"].isna().sum())
    nulos_cajero = int(atm["id_cajero"].isna().sum())
    clase_email = clasificar_email(spei["notification_email"])

    atm_imp, medianas = imputar_montos(atm)
    atm_imp = imputar_cajeros(atm_imp)
    spei_imp = corregir_emails(spei)

    # Correos que siguen sin ser válidos ni SIN_CORREO (debería ser 0).
    email_pendiente = (
        (clasificar_email(spei_imp["notification_email"]) != "VALIDO")
        & (spei_imp["notification_email"] != SIN_CORREO)
    )

    resumen = pd.DataFrame([
        {"fuente": FUENTE_ATM, "columna": "monto_mxn", "faltantes_antes": nulos_monto,
         "imputados": int(atm_imp["monto_imputado"].sum()),
         "faltantes_despues": int(atm_imp["monto_mxn"].isna().sum()),
         "metodo": "mediana por tipo_operacion"},
        {"fuente": FUENTE_ATM, "columna": "id_cajero", "faltantes_antes": nulos_cajero,
         "imputados": int(atm_imp["cajero_imputado"].sum()),
         "faltantes_despues": int(atm_imp["id_cajero"].isna().sum()),
         "metodo": CAJERO_DESCONOCIDO},
        {"fuente": FUENTE_SPEI, "columna": "notification_email",
         "faltantes_antes": int((clase_email != "VALIDO").sum()),
         "imputados": int(spei_imp["email_corregido"].sum()),
         "faltantes_despues": int(email_pendiente.sum()),
         "metodo": SIN_CORREO},
        {"fuente": FUENTE_ATM, "columna": "comision_aplicada",
         "faltantes_antes": int(atm["comision_aplicada"].isna().sum()),
         "imputados": 0,
         "faltantes_despues": int(atm_imp["comision_aplicada"].isna().sum()),
         "metodo": "sin imputación (ceros válidos)"},
    ])

    atm_an, spei_an = _variables_derivadas(atm, spei)
    vars_atm = [v for v in VARIABLES_ANALISIS_FALTANTES[FUENTE_ATM] if v in atm_an.columns]
    vars_spei = [v for v in VARIABLES_ANALISIS_FALTANTES[FUENTE_SPEI] if v in spei_an.columns]
    analisis = pd.concat([
        tasas_faltantes(atm_an, atm["monto_mxn"].isna(), vars_atm, FUENTE_ATM, "monto_mxn"),
        tasas_faltantes(atm_an, atm["id_cajero"].isna(), vars_atm, FUENTE_ATM, "id_cajero"),
        tasas_faltantes(spei_an, clase_email != "VALIDO", vars_spei, FUENTE_SPEI,
                        "notification_email"),
    ], ignore_index=True)

    return {"atm": atm_imp, "spei": spei_imp, "medianas": medianas,
            "resumen": resumen, "analisis": analisis}


# ---------------------------------------------------------------------------
# Valores imposibles
# ---------------------------------------------------------------------------

def _permite_cero_atm(atm):
    """Marca las filas ATM donde un monto cero es válido (CONSULTA_SALDO)."""
    return atm["tipo_operacion"].eq(TIPO_CONSULTA)


def motivo_monto_imposible(valores, permite_cero=False):
    """Determina el motivo de rechazo de cada monto, si lo hay.

    Reglas, en orden de prioridad:
        - FALTANTE: el valor es nulo.
        - NO_NUMERICO: no puede interpretarse como número finito.
        - NEGATIVO: el monto es menor que cero.
        - CERO_NO_PERMITIDO: el monto es cero donde no se permite.

    Args:
        valores: Series con los montos tal como vienen de la fuente.
        permite_cero: bool o Series booleana que indica dónde el cero es
            válido.

    Returns:
        Series con el motivo de rechazo o <NA> si el monto es válido.
    """
    numerico = pd.to_numeric(valores, errors="coerce")
    numerico = numerico.where(np.isfinite(numerico))
    if np.isscalar(permite_cero):
        permite_cero = pd.Series(permite_cero, index=valores.index)

    motivo = pd.Series(pd.NA, index=valores.index, dtype="object")
    motivo[(numerico == 0) & ~permite_cero] = "CERO_NO_PERMITIDO"
    motivo[numerico < 0] = "NEGATIVO"
    motivo[valores.notna() & numerico.isna()] = "NO_NUMERICO"
    motivo[valores.isna()] = "FALTANTE"
    return motivo


def _separar_por_motivo(df, motivo, fuente, columna_id, columna, valor_original):
    """Separa las filas con motivo de rechazo, preservando la fila completa.

    Los rechazados conservan todas sus columnas y agregan al inicio:
    fuente, archivo_origen, indice_original (posición en el DataFrame
    recibido), etiqueta_indice (etiqueta de pandas), id_registro,
    columna_validada, valor_original y motivo_rechazo.
    """
    rechazado = motivo.notna().to_numpy()
    rechazados = df[rechazado].copy()
    rechazados.insert(0, "fuente", fuente)
    rechazados.insert(1, "archivo_origen", ARCHIVOS_ORIGEN[fuente])
    rechazados.insert(2, "indice_original", np.flatnonzero(rechazado))
    rechazados.insert(3, "etiqueta_indice", rechazados.index)
    rechazados.insert(4, "id_registro", rechazados[columna_id])
    rechazados.insert(5, "columna_validada", columna)
    rechazados.insert(6, "valor_original", valor_original.to_numpy()[rechazado])
    rechazados.insert(7, "motivo_rechazo", motivo.to_numpy()[rechazado])
    return df[~rechazado].copy(), rechazados.reset_index(drop=True)


def separar_imposibles_atm(atm):
    """Separa las transacciones ATM con monto imposible.

    - CONSULTA_SALDO admite monto cero; las demás operaciones requieren
      monto mayor que cero.
    - Se valida monto_mxn_original (el valor de la fuente) cuando existe,
      para que la imputación no oculte un valor inválido.
    - Los montos imputados son válidos. Un nulo que no pudo imputarse se
      rechaza como FALTANTE.

    Args:
        atm: DataFrame ATM (idealmente ya imputado).

    Returns:
        tupla (atm_validos, atm_rechazados).
    """
    columna_valor = "monto_mxn_original" if "monto_mxn_original" in atm.columns else "monto_mxn"
    motivo = motivo_monto_imposible(atm[columna_valor], permite_cero=_permite_cero_atm(atm))
    if "monto_imputado" in atm.columns:
        motivo = motivo.mask(atm["monto_imputado"])
    return _separar_por_motivo(atm, motivo, FUENTE_ATM, "id_transaccion",
                               "monto_mxn", atm[columna_valor])


def separar_imposibles_spei(spei):
    """Separa las transferencias SPEI con amount imposible (<= 0, nulo o
    no numérico).

    Args:
        spei: DataFrame SPEI.

    Returns:
        tupla (spei_validos, spei_rechazados).
    """
    motivo = motivo_monto_imposible(spei["amount"])
    return _separar_por_motivo(spei, motivo, FUENTE_SPEI, "transfer_id",
                               "amount", spei["amount"])


def separar_imposibles(atm, spei):
    """Separa los registros con montos imposibles de ATM y SPEI.

    No elimina información: los rechazados se devuelven completos para que
    el Ingeniero 3 los envíe a cuarentena. Los DataFrames de entrada no se
    modifican.

    Args:
        atm: DataFrame ATM.
        spei: DataFrame SPEI.

    Returns:
        dict con "atm", "spei" (válidos), "rechazados_atm",
        "rechazados_spei" y "resumen" (rechazos por fuente y motivo).
    """
    atm_validos, rechazados_atm = separar_imposibles_atm(atm)
    spei_validos, rechazados_spei = separar_imposibles_spei(spei)

    resumen = pd.DataFrame([
        {"fuente": fuente, "motivo_rechazo": m,
         "registros": int((rechazados["motivo_rechazo"] == m).sum())}
        for fuente, rechazados in [(FUENTE_ATM, rechazados_atm),
                                   (FUENTE_SPEI, rechazados_spei)]
        for m in MOTIVOS_RECHAZO
    ])
    return {"atm": atm_validos, "spei": spei_validos,
            "rechazados_atm": rechazados_atm, "rechazados_spei": rechazados_spei,
            "resumen": resumen}


# ---------------------------------------------------------------------------
# Outliers
# ---------------------------------------------------------------------------

def capping_iqr(df, columna, columna_original=None, bandera=None, grupo=None,
                excluir=None, excluir_calculo=None, factor=FACTOR_IQR):
    """Recorta outliers con el método IQR.

    Por cada grupo:
        IQR = Q3 - Q1
        límite inferior = max(Q1 - factor * IQR, 0)
        límite superior = Q3 + factor * IQR
    Los valores fuera de los límites se recortan con clip.

    - columna_original conserva el valor previo. Si ya existe (p. ej.
      monto_mxn_original de la Fase 2) no se sobrescribe.
    - bandera marca los registros recortados.
    - Si un grupo no tiene valores para calcular, no se recorta y sus
      límites quedan nulos. Con IQR = 0 ambos límites coinciden con Q1.

    Args:
        df: DataFrame con montos ya validados (numéricos).
        columna: columna a recortar.
        columna_original: columna donde conservar el valor original
            (por defecto "<columna>_original").
        bandera: nombre de la bandera (por defecto "<columna>_recortado").
        grupo: columna para calcular límites por separado (p. ej. currency).
        excluir: máscara de filas que no participan en el cálculo ni se
            recortan (p. ej. CONSULTA_SALDO).
        excluir_calculo: máscara de filas que no participan en el cálculo
            de cuartiles pero sí se recortan (p. ej. montos imputados).
        factor: multiplicador del IQR.

    Returns:
        tupla (df_recortado, limites), donde limites tiene una fila por
        grupo con Q1, Q3, IQR, límites y cantidad de outliers.
    """
    df = df.copy()
    columna_original = columna_original or f"{columna}_original"
    bandera = bandera or f"{columna}_recortado"
    if columna_original not in df.columns:
        df[columna_original] = df[columna]
    df[columna] = pd.to_numeric(df[columna]).astype(float)
    df[bandera] = False

    ninguno = pd.Series(False, index=df.index)
    aplicar = ~(ninguno if excluir is None else excluir)
    calcular = aplicar & ~(ninguno if excluir_calculo is None else excluir_calculo)
    claves = df[grupo].fillna("SIN_GRUPO") if grupo else pd.Series("TOTAL", index=df.index)

    filas = []
    for clave in pd.unique(claves[aplicar]):
        destino = aplicar & (claves == clave)
        base = df.loc[calcular & (claves == clave), columna].dropna()
        valores = df.loc[destino, columna]
        if base.empty:
            q1 = q3 = iqr = lim_inf = lim_sup = np.nan
            inferiores = superiores = pd.Series(False, index=valores.index)
        else:
            q1, q3 = base.quantile([0.25, 0.75])
            iqr = q3 - q1
            lim_inf = max(q1 - factor * iqr, 0.0)
            lim_sup = q3 + factor * iqr
            inferiores = valores < lim_inf
            superiores = valores > lim_sup
            df.loc[destino, columna] = valores.clip(lim_inf, lim_sup)
        df.loc[destino, bandera] = inferiores | superiores
        filas.append({
            "columna": columna, "agrupado_por": grupo or "", "grupo": clave,
            "registros_calculo": len(base), "registros_aplicados": len(valores),
            "q1": q1, "q3": q3, "iqr": iqr,
            "limite_inferior": lim_inf, "limite_superior": lim_sup,
            "outliers_inferiores": int(inferiores.sum()),
            "outliers_superiores": int(superiores.sum()),
            "outliers": int((inferiores | superiores).sum()),
        })
    return df, pd.DataFrame(filas, columns=COLUMNAS_LIMITES_IQR)


def _detalle_recortes(df, fuente, columna_id, columna, columna_original,
                      bandera, grupo=None):
    """Lista los registros recortados con su valor original y recortado."""
    recortados = df[df[bandera]]
    return pd.DataFrame({
        "fuente": fuente,
        "archivo_origen": ARCHIVOS_ORIGEN[fuente],
        "id_registro": recortados[columna_id],
        "grupo": recortados[grupo] if grupo else "TOTAL",
        "valor_original": recortados[columna_original],
        "valor_recortado": recortados[columna],
    }).reset_index(drop=True)


def aplicar_capping(atm, spei):
    """Aplica el capping IQR según las reglas de cada fuente.

    ATM: sobre monto_mxn, excluyendo CONSULTA_SALDO (sus ceros se
    conservan). Los montos imputados no entran en el cálculo de cuartiles,
    porque la mediana repetida comprime el IQR, pero sí se recortan si
    quedaran fuera. monto_mxn_original (Fase 2) no se sobrescribe.

    SPEI: sobre amount, con límites independientes para MXN y USD y sin
    convertir monedas. Se crea amount_original antes de recortar.

    Args:
        atm: DataFrame ATM con montos válidos.
        spei: DataFrame SPEI con montos válidos.

    Returns:
        dict con "atm", "spei" (recortados), "limites" (límites IQR por
        fuente y grupo) y "detalle" (valores originales y recortados).
    """
    excluir_calculo = atm["monto_imputado"] if "monto_imputado" in atm.columns else None
    atm_cap, limites_atm = capping_iqr(
        atm, "monto_mxn", columna_original="monto_mxn_original",
        bandera="monto_recortado", excluir=_permite_cero_atm(atm),
        excluir_calculo=excluir_calculo,
    )
    spei_cap, limites_spei = capping_iqr(
        spei, "amount", columna_original="amount_original",
        bandera="amount_recortado", grupo="currency",
    )

    limites = pd.concat([limites_atm.assign(fuente=FUENTE_ATM),
                         limites_spei.assign(fuente=FUENTE_SPEI)], ignore_index=True)
    limites = limites[["fuente"] + COLUMNAS_LIMITES_IQR]

    detalle = pd.concat([
        _detalle_recortes(atm_cap, FUENTE_ATM, "id_transaccion", "monto_mxn",
                          "monto_mxn_original", "monto_recortado"),
        _detalle_recortes(spei_cap, FUENTE_SPEI, "transfer_id", "amount",
                          "amount_original", "amount_recortado", grupo="currency"),
    ], ignore_index=True)
    detalle = detalle.merge(
        limites[["fuente", "grupo", "limite_inferior", "limite_superior"]],
        on=["fuente", "grupo"], how="left",
    )
    return {"atm": atm_cap, "spei": spei_cap, "limites": limites, "detalle": detalle}


# ---------------------------------------------------------------------------
# Reportes
# ---------------------------------------------------------------------------

def generar_reporte_perfil(datasets, dir_reportes=DIR_REPORTES):
    """Genera los reportes de perfilado inicial de los datasets dados.

    Archivos generados en dir_reportes:
        - resumen_calidad.csv: tamaño y duplicados por dataset.
        - perfil_columnas.csv: perfil de cada columna.
        - perfil_montos.csv: estadísticas de montos por grupo.

    Args:
        datasets: dict {fuente: DataFrame}, p. ej. el que devuelve
            cargar_datos() ("catalogo", "atm", "spei").
        dir_reportes: carpeta de salida.

    Returns:
        dict con los tres DataFrames de reporte.
    """
    dir_reportes = Path(dir_reportes)
    dir_reportes.mkdir(parents=True, exist_ok=True)

    resumen = pd.DataFrame([resumir_dataset(df, n) for n, df in datasets.items()])
    columnas = pd.concat(
        [perfilar_columnas(df, n) for n, df in datasets.items()], ignore_index=True
    )
    montos = pd.concat(
        [perfilar_montos(df, *COLUMNAS_MONTO[n]).assign(fuente=n)
         for n, df in datasets.items() if n in COLUMNAS_MONTO],
        ignore_index=True,
    )
    montos = montos[["fuente"] + [c for c in montos.columns if c != "fuente"]]

    resumen.to_csv(dir_reportes / "resumen_calidad.csv", index=False, encoding="utf-8")
    columnas.to_csv(dir_reportes / "perfil_columnas.csv", index=False, encoding="utf-8")
    montos.to_csv(dir_reportes / "perfil_montos.csv", index=False, encoding="utf-8")
    return {"resumen": resumen, "columnas": columnas, "montos": montos}


ETAPAS = ["original", "sin_duplicados", "final"]


def _fila_comparativo(fuente, indicador, etapa, valor, total=None, nota=""):
    """Crea una fila del comparativo con su porcentaje sobre total."""
    return {
        "fuente": fuente, "indicador": indicador, "etapa": etapa,
        "valor": int(valor), "total_referencia": total,
        "porcentaje": round(100 * valor / total, 2) if total else None,
        "nota": nota,
    }


def comparar_calidad(atm, spei, resultado):
    """Construye el reporte comparativo de calidad antes y después.

    Etapas:
        - original: datos tal como vienen de la fuente (con duplicados).
        - sin_duplicados: después de separar duplicados exactos, antes de
          cualquier imputación o capping.
        - final: registros válidos entregados por limpiar().
    Los imputados se reportan por separado de los valores observados.

    Args:
        atm: DataFrame ATM original (el mismo que recibió limpiar()).
        spei: DataFrame SPEI original.
        resultado: dict devuelto por limpiar().

    Returns:
        DataFrame en formato largo: fuente, indicador, etapa, valor,
        total_referencia, porcentaje y nota.
    """
    def _sin_posiciones(df, posiciones):
        return df[~np.isin(np.arange(len(df)), posiciones)]

    atm_sd = _sin_posiciones(atm, resultado["duplicados_atm"]["indice_original"])
    spei_sd = _sin_posiciones(spei, resultado["duplicados_spei"]["indice_original"])
    atm_fin, spei_fin = resultado["atm"], resultado["spei"]
    filas = []

    # --- ATM ---
    f = FUENTE_ATM
    etapas_atm = {"original": atm, "sin_duplicados": atm_sd, "final": atm_fin}
    for etapa, df in etapas_atm.items():
        filas.append(_fila_comparativo(f, "registros", etapa, len(df)))
    filas.append(_fila_comparativo(f, "duplicados_exactos", "original",
                                   len(resultado["duplicados_atm"]), len(atm),
                                   "separados con trazabilidad"))
    for etapa, df in etapas_atm.items():
        filas.append(_fila_comparativo(f, "consultas_saldo", etapa,
                                       df["tipo_operacion"].eq(TIPO_CONSULTA).sum(), len(df),
                                       "monto cero válido, se conservan"))
    for etapa, df in etapas_atm.items():
        filas.append(_fila_comparativo(f, "monto_mxn_nulos", etapa,
                                       df["monto_mxn"].isna().sum(), len(df)))
    filas.append(_fila_comparativo(f, "monto_mxn_observados", "final",
                                   (~atm_fin["monto_imputado"]).sum(), len(atm_fin),
                                   "valores de la fuente, no imputados"))
    filas.append(_fila_comparativo(f, "monto_mxn_imputados", "final",
                                   atm_fin["monto_imputado"].sum(), len(atm_fin),
                                   "mediana por tipo_operacion; no son observaciones"))
    for etapa, df in etapas_atm.items():
        filas.append(_fila_comparativo(f, "id_cajero_nulos", etapa,
                                       df["id_cajero"].isna().sum(), len(df)))
    filas.append(_fila_comparativo(f, "id_cajero_imputados", "final",
                                   atm_fin["cajero_imputado"].sum(), len(atm_fin),
                                   CAJERO_DESCONOCIDO))
    for etapa, df in etapas_atm.items():
        filas.append(_fila_comparativo(f, "comision_aplicada_nulos", etapa,
                                       df["comision_aplicada"].isna().sum(), len(df),
                                       "sin imputación"))
    filas.append(_fila_comparativo(f, "montos_imposibles", "sin_duplicados",
                                   len(resultado["rechazados_atm"]), len(atm_sd),
                                   "separados para cuarentena"))
    monetarias = ~atm_fin["tipo_operacion"].eq(TIPO_CONSULTA)
    filas.append(_fila_comparativo(f, "outliers_recortados", "final",
                                   atm_fin["monto_recortado"].sum(), monetarias.sum(),
                                   "IQR sobre operaciones monetarias"))
    sin_cambios_atm = ~(atm_fin["monto_imputado"] | atm_fin["cajero_imputado"]
                        | atm_fin["monto_recortado"])
    filas.append(_fila_comparativo(f, "registros_sin_modificacion", "final",
                                   sin_cambios_atm.sum(), len(atm_fin),
                                   "sin imputación ni capping"))

    # --- SPEI ---
    f = FUENTE_SPEI
    etapas_spei = {"original": spei, "sin_duplicados": spei_sd, "final": spei_fin}
    for etapa, df in etapas_spei.items():
        filas.append(_fila_comparativo(f, "registros", etapa, len(df)))
    filas.append(_fila_comparativo(f, "duplicados_exactos", "original",
                                   len(resultado["duplicados_spei"]), len(spei),
                                   "separados con trazabilidad"))
    for etapa, df in [("original", spei), ("sin_duplicados", spei_sd)]:
        clase = clasificar_email(df["notification_email"])
        for motivo in ["NULO", "VACIO", "INVALIDO"]:
            filas.append(_fila_comparativo(f, f"email_{motivo.lower()}", etapa,
                                           (clase == motivo).sum(), len(df)))
    filas.append(_fila_comparativo(f, "email_corregidos", "final",
                                   spei_fin["email_corregido"].sum(), len(spei_fin),
                                   SIN_CORREO))
    filas.append(_fila_comparativo(f, "email_validos", "final",
                                   (~spei_fin["email_corregido"]).sum(), len(spei_fin),
                                   "correo original conservado"))
    filas.append(_fila_comparativo(f, "montos_imposibles", "sin_duplicados",
                                   len(resultado["rechazados_spei"]), len(spei_sd),
                                   "separados para cuarentena"))
    for moneda, g in spei_fin.groupby("currency"):
        filas.append(_fila_comparativo(f, f"outliers_recortados_{moneda}", "final",
                                       g["amount_recortado"].sum(), len(g),
                                       f"IQR calculado solo con {moneda}"))
    filas.append(_fila_comparativo(f, "email_normalizados", "final",
                                   spei_fin["email_normalizado"].sum(), len(spei_fin),
                                   "válidos tras quitar espacios"))
    sin_cambios_spei = ~(spei_fin["email_corregido"] | spei_fin["email_normalizado"]
                         | spei_fin["amount_recortado"])
    filas.append(_fila_comparativo(f, "registros_sin_modificacion", "final",
                                   sin_cambios_spei.sum(), len(spei_fin),
                                   "sin corrección de correo ni capping"))

    return pd.DataFrame(filas)


def tabla_comparativa(comparativo):
    """Pivotea el comparativo largo a una tabla indicador x etapa.

    Args:
        comparativo: DataFrame devuelto por comparar_calidad().

    Returns:
        DataFrame con una columna por etapa (vacía si no aplica).
    """
    orden = comparativo[["fuente", "indicador"]].drop_duplicates()
    tabla = comparativo.pivot(index=["fuente", "indicador"], columns="etapa", values="valor")
    tabla = tabla.reindex(pd.MultiIndex.from_frame(orden)).reindex(columns=ETAPAS)
    return tabla.astype("Int64").reset_index()


# Colores de las gráficas: "antes" neutro, "después" acento (paleta de referencia).
COLOR_ANTES = "#8a8984"
COLOR_DESPUES = "#2a78d6"
COLOR_TEXTO = "#0b0b0b"
COLOR_TEXTO_SECUNDARIO = "#52514e"
COLOR_REJILLA = "#e4e3df"


def _boxplot_antes_despues(ruta, antes, despues, limite, moneda, titulo, subtitulo):
    """Dibuja un boxplot antes/después del capping en escala logarítmica."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    fig, ax = plt.subplots(figsize=(8, 5.5), dpi=150)
    cajas = ax.boxplot(
        [antes, despues], patch_artist=True, widths=0.45,
        tick_labels=[f"Antes del capping\n(máx. {antes.max():,.0f})",
                     f"Después del capping\n(máx. {despues.max():,.0f})"],
        medianprops={"color": COLOR_TEXTO, "linewidth": 2},
        whiskerprops={"color": COLOR_TEXTO_SECUNDARIO, "linewidth": 1.2},
        capprops={"color": COLOR_TEXTO_SECUNDARIO, "linewidth": 1.2},
        flierprops={"marker": "o", "markersize": 5, "markerfacecolor": "none"},
    )
    for caja, flier, color in zip(cajas["boxes"], cajas["fliers"], [COLOR_ANTES, COLOR_DESPUES]):
        caja.set(facecolor=color + "40", edgecolor=color, linewidth=1.5)
        flier.set(markeredgecolor=color)

    ax.axhline(limite, color=COLOR_DESPUES, linestyle="--", linewidth=1.2)
    ax.text(2.45, limite * 1.1, f"Límite superior IQR\n{limite:,.2f} {moneda}",
            va="bottom", ha="left", fontsize=9, color=COLOR_TEXTO_SECUNDARIO)

    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_ylabel(f"Monto ({moneda}, escala logarítmica)", color=COLOR_TEXTO)
    ax.set_xlim(0.5, 3.3)
    ax.set_title(titulo, loc="left", fontsize=13, fontweight="bold", color=COLOR_TEXTO, pad=26)
    ax.text(0, 1.025, subtitulo, transform=ax.transAxes, fontsize=9,
            color=COLOR_TEXTO_SECUNDARIO)
    ax.grid(axis="y", color=COLOR_REJILLA, linewidth=0.8)
    ax.set_axisbelow(True)
    for lado in ["top", "right"]:
        ax.spines[lado].set_visible(False)
    ax.tick_params(colors=COLOR_TEXTO_SECUNDARIO)
    fig.subplots_adjust(top=0.85, left=0.15, right=0.97, bottom=0.14)
    fig.savefig(ruta, facecolor="white")
    plt.close(fig)


def graficar_boxplots(resultado, dir_reportes=DIR_REPORTES):
    """Genera boxplots antes y después del capping (requiere matplotlib).

    Para comparar los mismos registros, se grafican solo montos observados:
    ATM excluye CONSULTA_SALDO y los montos imputados; SPEI se grafica por
    moneda (MXN y USD por separado).

    Args:
        resultado: dict devuelto por limpiar().
        dir_reportes: carpeta de salida.

    Returns:
        lista de rutas de los PNG generados.
    """
    dir_reportes = Path(dir_reportes)
    dir_reportes.mkdir(parents=True, exist_ok=True)
    limites = resultado["limites_iqr"].set_index(["fuente", "grupo"])["limite_superior"]
    rutas = []

    atm = resultado["atm"]
    es_consulta = atm["tipo_operacion"].eq(TIPO_CONSULTA)
    observados = atm[~es_consulta & ~atm["monto_imputado"]]
    ruta = dir_reportes / "boxplot_atm_capping.png"
    _boxplot_antes_despues(
        ruta, observados["monto_mxn_original"], observados["monto_mxn"],
        limites[(FUENTE_ATM, "TOTAL")], "MXN",
        "ATM: monto_mxn antes y después del capping IQR",
        f"{len(observados)} montos observados · excluye {es_consulta.sum()} consultas de saldo "
        f"y {(~es_consulta & atm['monto_imputado']).sum()} montos imputados · "
        f"{atm['monto_recortado'].sum()} recortado(s)",
    )
    rutas.append(ruta)

    spei = resultado["spei"]
    for moneda in ["MXN", "USD"]:
        g = spei[spei["currency"] == moneda]
        ruta = dir_reportes / f"boxplot_spei_{moneda.lower()}_capping.png"
        _boxplot_antes_despues(
            ruta, g["amount_original"], g["amount"], limites[(FUENTE_SPEI, moneda)], moneda,
            f"SPEI {moneda}: amount antes y después del capping IQR",
            f"{len(g)} transferencias en {moneda} · límites calculados solo con {moneda}, "
            f"sin conversión · {g['amount_recortado'].sum()} recortadas",
        )
        rutas.append(ruta)
    return rutas


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def _trazar_entrada(registros, posiciones, etiquetas, fuente):
    """Refiere duplicados o rechazados a la entrada de limpiar().

    - indice_original: posición (0 = primera fila) en el DataFrame que
      recibió limpiar().
    - etiqueta_indice: etiqueta del índice de pandas de esa fila.
    Agrega fuente y archivo_origen al inicio si no existen.
    """
    registros = registros.copy()
    posiciones = np.asarray(posiciones, dtype=int)
    registros["indice_original"] = posiciones
    registros["etiqueta_indice"] = etiquetas.take(posiciones).to_numpy()
    if "fuente" not in registros.columns:
        registros.insert(0, "fuente", fuente)
        registros.insert(1, "archivo_origen", ARCHIVOS_ORIGEN[fuente])
    return registros


def limpiar(atm, spei, guardar=True, dir_procesados=DIR_PROCESADOS,
            dir_reportes=DIR_REPORTES):
    """Aplica la limpieza del Ingeniero 1 a los datasets de ATM y SPEI.

    Fase 1: separa los duplicados exactos y los registra para trazabilidad.
    Fase 2: imputa los faltantes sobre los datos ya sin duplicados.
    Fase 3: separa los montos imposibles y aplica capping IQR a los válidos.
    Los DataFrames de entrada no se modifican.

    Trazabilidad: en duplicados y rechazados, indice_original es la
    posición de la fila en el DataFrame recibido (0 = primera fila) y
    etiqueta_indice su etiqueta de pandas, aunque el índice esté desplazado
    o no sea consecutivo. Los DataFrames "atm" y "spei" devueltos usan esa
    posición de entrada como índice.

    Args:
        atm: DataFrame de transacciones ATM.
        spei: DataFrame de transferencias digitales.
        guardar: si es True (ejecución individual), escribe 19 CSV en
            dir_procesados y dir_reportes. main.py debe usar guardar=False:
            la persistencia corresponde al Ingeniero 3.
        dir_procesados: carpeta para los datasets procesados.
        dir_reportes: carpeta para los reportes.

    Returns:
        dict con:
            - "atm", "spei": registros válidos, sin duplicados, imputados y
              con capping (resultado final del módulo).
            - "duplicados_atm", "duplicados_spei": filas retiradas.
            - "resumen_duplicados": conteos antes y después.
            - "medianas_imputacion": medianas usadas por tipo_operacion.
            - "resumen_imputacion": faltantes antes/después por columna.
            - "analisis_faltantes": tasas de faltantes por variable.
            - "rechazados_atm", "rechazados_spei": registros con monto
              imposible, completos y con motivo, para cuarentena.
            - "resumen_imposibles": rechazos por fuente y motivo.
            - "limites_iqr": Q1, Q3, IQR, límites y outliers por grupo.
            - "outliers_recortados": valores originales y recortados.
            - "comparativo_calidad": indicadores por etapa (original,
              sin_duplicados, final).
    """
    # Con el índice reiniciado, cada etiqueta es la posición de entrada en
    # todas las etapas; las etiquetas originales se restauran en la traza.
    etiquetas_atm, etiquetas_spei = atm.index, spei.index
    atm_limpio, duplicados_atm = separar_duplicados(atm.reset_index(drop=True))
    spei_limpio, duplicados_spei = separar_duplicados(spei.reset_index(drop=True))
    imputacion = imputar(atm_limpio, spei_limpio)
    imposibles = separar_imposibles(imputacion["atm"], imputacion["spei"])
    capping = aplicar_capping(imposibles["atm"], imposibles["spei"])

    duplicados_atm = _trazar_entrada(duplicados_atm, duplicados_atm["indice_original"],
                                     etiquetas_atm, FUENTE_ATM)
    duplicados_spei = _trazar_entrada(duplicados_spei, duplicados_spei["indice_original"],
                                      etiquetas_spei, FUENTE_SPEI)
    # En los rechazados, la etiqueta de la etapa ya es la posición de entrada.
    for clave, etiquetas, fuente in [("rechazados_atm", etiquetas_atm, FUENTE_ATM),
                                     ("rechazados_spei", etiquetas_spei, FUENTE_SPEI)]:
        rechazados = imposibles[clave]
        imposibles[clave] = _trazar_entrada(rechazados, rechazados["etiqueta_indice"],
                                            etiquetas, fuente)

    resumen = pd.DataFrame([
        {"fuente": nombre, "registros_antes": len(antes),
         "duplicados_retirados": len(dup), "registros_despues": len(despues)}
        for nombre, antes, despues, dup in [
            (FUENTE_ATM, atm, atm_limpio, duplicados_atm),
            (FUENTE_SPEI, spei, spei_limpio, duplicados_spei),
        ]
    ])

    if guardar:
        dir_procesados = Path(dir_procesados)
        dir_reportes = Path(dir_reportes)
        dir_procesados.mkdir(parents=True, exist_ok=True)
        dir_reportes.mkdir(parents=True, exist_ok=True)

        atm_limpio.to_csv(dir_procesados / "atm_sin_duplicados.csv", index=False, encoding="utf-8")
        spei_limpio.to_csv(dir_procesados / "spei_sin_duplicados.csv", index=False, encoding="utf-8")
        duplicados_atm.to_csv(dir_reportes / "duplicados_atm.csv", index=False, encoding="utf-8")
        duplicados_spei.to_csv(dir_reportes / "duplicados_spei.csv", index=False, encoding="utf-8")
        resumen.to_csv(dir_reportes / "resumen_duplicados.csv", index=False, encoding="utf-8")

        imputacion["atm"].to_csv(dir_procesados / "atm_imputado.csv", index=False, encoding="utf-8")
        imputacion["spei"].to_csv(dir_procesados / "spei_imputado.csv", index=False, encoding="utf-8")
        imputacion["medianas"].to_csv(dir_reportes / "medianas_imputacion.csv", index=False, encoding="utf-8")
        imputacion["resumen"].to_csv(dir_reportes / "resumen_imputacion.csv", index=False, encoding="utf-8")
        imputacion["analisis"].to_csv(dir_reportes / "analisis_faltantes.csv", index=False, encoding="utf-8")

        capping["atm"].to_csv(dir_procesados / "atm_limpio.csv", index=False, encoding="utf-8")
        capping["spei"].to_csv(dir_procesados / "spei_limpio.csv", index=False, encoding="utf-8")
        imposibles["rechazados_atm"].to_csv(dir_reportes / "rechazados_atm.csv", index=False, encoding="utf-8")
        imposibles["rechazados_spei"].to_csv(dir_reportes / "rechazados_spei.csv", index=False, encoding="utf-8")
        imposibles["resumen"].to_csv(dir_reportes / "resumen_imposibles.csv", index=False, encoding="utf-8")
        capping["limites"].to_csv(dir_reportes / "limites_iqr.csv", index=False, encoding="utf-8")
        capping["detalle"].to_csv(dir_reportes / "outliers_recortados.csv", index=False, encoding="utf-8")

    resultado = {
        "atm": capping["atm"],
        "spei": capping["spei"],
        "duplicados_atm": duplicados_atm,
        "duplicados_spei": duplicados_spei,
        "resumen_duplicados": resumen,
        "medianas_imputacion": imputacion["medianas"],
        "resumen_imputacion": imputacion["resumen"],
        "analisis_faltantes": imputacion["analisis"],
        "rechazados_atm": imposibles["rechazados_atm"],
        "rechazados_spei": imposibles["rechazados_spei"],
        "resumen_imposibles": imposibles["resumen"],
        "limites_iqr": capping["limites"],
        "outliers_recortados": capping["detalle"],
    }
    resultado["comparativo_calidad"] = comparar_calidad(atm, spei, resultado)

    if guardar:
        comparativo = resultado["comparativo_calidad"]
        comparativo.to_csv(dir_reportes / "comparativo_calidad.csv", index=False, encoding="utf-8")
        tabla_comparativa(comparativo).to_csv(dir_reportes / "comparativo_calidad_tabla.csv",
                                              index=False, encoding="utf-8")
    return resultado


if __name__ == "__main__":
    datos = cargar_datos()
    perfil = generar_reporte_perfil(datos)
    resultado = limpiar(datos["atm"], datos["spei"])

    print("Resumen de calidad:")
    print(perfil["resumen"].to_string(index=False))
    print("\nDuplicados exactos:")
    print(resultado["resumen_duplicados"].to_string(index=False))
    print("\nImputación de faltantes:")
    print(resultado["resumen_imputacion"].to_string(index=False))
    print("\nMedianas usadas:")
    print(resultado["medianas_imputacion"].to_string(index=False))
    print("\nMontos imposibles:")
    print(resultado["resumen_imposibles"].to_string(index=False))
    print("\nLímites IQR:")
    print(resultado["limites_iqr"].to_string(index=False))
    print("\nOutliers recortados:")
    print(resultado["outliers_recortados"].to_string(index=False))
    print("\nComparativo de calidad:")
    print(tabla_comparativa(resultado["comparativo_calidad"]).to_string(index=False))
    print("\nGráficas:")
    for ruta in graficar_boxplots(resultado):
        print(f"  {ruta.name}")
    print(f"\nReportes en: {DIR_REPORTES}")
    print(f"Datasets procesados en: {DIR_PROCESADOS}")
