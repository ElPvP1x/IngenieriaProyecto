"""Ingeniero 3 - Etapa 1: validación con Pydantic y segregación a cuarentena.

Recibe la tabla ``integrada`` del Ingeniero 2 (``ejecutar_transformacion``) y la
separa en dos grupos, registro por registro:

* **aptos**: cumplen el esquema ``TransaccionValidada``.
* **cuarentena**: violan alguna regla; se capturan con ``try ... except
  ValidationError`` y conservan la fila original y el ``motivo_rechazo_cuarentena``.

Además, ``consolidar_cuarentena`` junta en una sola tabla todo lo que los otros
módulos ya separaron (duplicados y montos imposibles del Ingeniero 1, cuentas
huérfanas y cuentas bloqueadas/suspendidas del Ingeniero 2), para que la carga a
la base de datos tenga una sola tabla ``cuarentena_errores``.

Reglas generales:
* No se modifica ningún valor: la validación es una compuerta, no una limpieza.
* Un registro malo nunca detiene el lote (no crash).
* No se escribe nada en disco; la persistencia es la Etapa 2.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Literal, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from src.transformacion import ESTADOS_ATM, ESTADOS_SPEI, ETIQUETAS_MONTO

# ---------------------------------------------------------------------------
# Catálogos de valores permitidos (fuente de cada regla en el comentario)
# ---------------------------------------------------------------------------
TIPOS_ATM = {"RETIRO", "DEPOSITO", "PAGO_SERVICIOS", "CONSULTA_SALDO"}   # datos ATM
TIPO_SPEI = "TRANSFERENCIA_SPEI"                                          # Ingeniero 2, etapa 5
TIPO_CONSULTA = "CONSULTA_SALDO"           # único tipo ATM con monto 0 válido (REGLAS_DE_NEGOCIO §6)
ESTADOS_CUENTA_ACEPTADOS = {"ACTIVA"}      # regla del equipo en etapa 3 del Ingeniero 2 ("por ahora")
CATEGORIAS_MONTO = set(ETIQUETAS_MONTO)    # se toma del Ingeniero 2 para no duplicar la lista
FRANJAS_HORARIAS = {"MADRUGADA", "MANANA", "TARDE", "NOCHE"}
LONGITUD_CLABE = 18                        # CLABE interbancaria: 18 dígitos (reto del Grupo 2)
PATRON_CUENTA = r"^CTA-\d{4}$"             # formato de id_cuenta en catalogo_cuentas.csv

# Etapas de origen que se registran en la cuarentena
ETAPA_DUPLICADO = "LIMPIEZA_DUPLICADO"
ETAPA_IMPOSIBLE = "LIMPIEZA_MONTO_IMPOSIBLE"
ETAPA_CATALOGO = "TRANSFORMACION_CATALOGO"
ETAPA_PYDANTIC = "VALIDACION_PYDANTIC"

COLUMNAS_CUARENTENA = [
    "id_registro", "fuente", "etapa_origen", "motivo_rechazo_cuarentena",
    "campos_con_error", "indice_original", "registro_original",
]


# ---------------------------------------------------------------------------
# Esquema Pydantic
# ---------------------------------------------------------------------------
class TransaccionValidada(BaseModel):
    """Una fila de la tabla integrada (ATM o SPEI) lista para la base de datos.

    Los campos numéricos y booleanos son estrictos: un monto como texto
    (``"1500"``) se rechaza en lugar de convertirse en silencio.
    Los campos que solo existen en una fuente son opcionales aquí y se exigen
    en ``reglas_por_fuente`` según ``fuente``.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    # --- comunes ---
    id_operacion: str = Field(min_length=1)
    fuente: Literal["ATM", "SPEI"]
    fecha_hora: datetime = Field(strict=True)
    id_cuenta: str = Field(pattern=PATRON_CUENTA)
    tipo_operacion: str
    monto_mxn: float = Field(strict=True, ge=0, allow_inf_nan=False)
    monto_original_mxn: Optional[float] = Field(default=None, strict=True, ge=0, allow_inf_nan=False)
    moneda_origen: Literal["MXN", "USD"]
    resultado: str
    canal: str = Field(min_length=1)
    comision_mxn: Optional[float] = Field(default=None, strict=True, ge=0, allow_inf_nan=False)
    tipo_cuenta: str = Field(min_length=1)
    estado_cuenta_catalogo: str
    monto_imputado: bool = Field(strict=True)
    monto_recortado: bool = Field(strict=True)
    # --- solo ATM ---
    id_cajero: Optional[str] = None
    cajero_imputado: Optional[bool] = Field(default=None, strict=True)
    # --- solo SPEI ---
    beneficiary_clabe: Optional[str] = None
    notification_email: Optional[str] = None
    email_corregido: Optional[bool] = Field(default=None, strict=True)
    concept: Optional[str] = None
    # --- Ingeniero 2, etapa 4 ---
    monto_mxn_minmax: float = Field(strict=True, ge=0, le=1, allow_inf_nan=False)
    monto_mxn_categoria: str
    franja_horaria: str

    # ---- validadores de un solo campo ----
    @field_validator("fecha_hora")
    @classmethod
    def fecha_con_zona(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("la fecha debe tener zona horaria (contrato del Ingeniero 2: UTC-6)")
        return v

    @field_validator("beneficiary_clabe")
    @classmethod
    def clabe_18_digitos(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not (len(v) == LONGITUD_CLABE and v.isdigit()):
            raise ValueError(f"CLABE inválida: debe tener exactamente {LONGITUD_CLABE} dígitos "
                             f"(recibido {v!r}, {len(v)} caracteres)")
        return v

    @field_validator("estado_cuenta_catalogo")
    @classmethod
    def cuenta_activa(cls, v: str) -> str:
        if v.upper() not in ESTADOS_CUENTA_ACEPTADOS:
            raise ValueError(f"la cuenta está {v!r}; solo se aceptan {sorted(ESTADOS_CUENTA_ACEPTADOS)}")
        return v

    @field_validator("monto_mxn_categoria")
    @classmethod
    def categoria_conocida(cls, v: str) -> str:
        if v not in CATEGORIAS_MONTO:
            raise ValueError(f"categoría de monto desconocida {v!r}; permitidas: {sorted(CATEGORIAS_MONTO)}")
        return v

    @field_validator("franja_horaria")
    @classmethod
    def franja_conocida(cls, v: str) -> str:
        if v not in FRANJAS_HORARIAS:
            raise ValueError(f"franja horaria desconocida {v!r}")
        return v

    # ---- reglas que dependen de la fuente (varios campos a la vez) ----
    @model_validator(mode="after")
    def reglas_por_fuente(self) -> "TransaccionValidada":
        errores = []
        if self.fuente == "ATM":
            if self.tipo_operacion not in TIPOS_ATM:
                errores.append(f"tipo_operacion ATM desconocido {self.tipo_operacion!r}")
            if self.resultado not in ESTADOS_ATM:
                errores.append(f"resultado ATM desconocido {self.resultado!r}")
            if self.comision_mxn is None:
                errores.append("comision_mxn nula en una operación ATM")
            if not self.id_cajero:
                errores.append("id_cajero vacío en una operación ATM")
            if self.moneda_origen != "MXN":
                errores.append("una operación ATM debe estar en MXN")
            if self.monto_mxn == 0 and self.tipo_operacion != TIPO_CONSULTA:
                errores.append(f"monto 0 solo es válido en {TIPO_CONSULTA} (tipo {self.tipo_operacion!r})")
        else:  # SPEI
            if self.tipo_operacion != TIPO_SPEI:
                errores.append(f"tipo_operacion SPEI debe ser {TIPO_SPEI!r}")
            if self.resultado not in ESTADOS_SPEI:
                errores.append(f"status SPEI desconocido {self.resultado!r}")
            if self.beneficiary_clabe is None:
                errores.append("beneficiary_clabe nula en una transferencia SPEI")
            if self.monto_mxn == 0:
                errores.append("una transferencia SPEI no puede tener monto 0")
        if errores:
            raise ValueError("; ".join(errores))
        return self


# ---------------------------------------------------------------------------
# Conversión pandas -> Python
# ---------------------------------------------------------------------------
def _a_python(v):
    """Convierte un valor de pandas/numpy a un tipo nativo; los nulos a None."""
    if v is None:
        return None
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.to_pydatetime()
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and math.isnan(v):
        return None
    if v is pd.NA or v is pd.NaT:
        return None
    return v


def _fila_a_dict(fila: dict) -> dict:
    return {k: _a_python(v) for k, v in fila.items()}


def _a_json(fila: dict) -> str:
    """Serializa la fila completa (sin nulos) para guardarla en la cuarentena."""
    limpio = {}
    for k, v in fila.items():
        v = _a_python(v)
        if v is None:
            continue
        if isinstance(v, datetime):
            v = v.isoformat()
        elif isinstance(v, float) and not math.isfinite(v):
            v = str(v)
        limpio[str(k)] = v
    return json.dumps(limpio, ensure_ascii=False, default=str)


MENSAJES_ES = {
    "missing": "campo obligatorio ausente",
    "float_type": "debe ser un número (se recibió {tipo})",
    "float_parsing": "debe ser un número (se recibió {tipo})",
    "finite_number": "debe ser un número finito",
    "greater_than_equal": "debe ser mayor o igual que {ge}",
    "less_than_equal": "debe ser menor o igual que {le}",
    "datetime_type": "debe ser una fecha válida (se recibió {tipo})",
    "string_type": "debe ser texto (se recibió {tipo})",
    "string_too_short": "no puede estar vacío",
    "string_pattern_mismatch": "formato inválido (patrón {pattern})",
    "literal_error": "valor no permitido; se esperaba {expected}",
    "bool_type": "debe ser verdadero/falso (se recibió {tipo})",
}


def _mensaje(e: dict) -> str:
    """Mensaje de un error de Pydantic en español, o el original si no está traducido."""
    if e["type"] == "value_error":
        return e["msg"].removeprefix("Value error, ")
    plantilla = MENSAJES_ES.get(e["type"])
    if plantilla is None:
        return e["msg"]
    entrada = e.get("input")
    datos = {"tipo": "nulo" if entrada is None else f"{type(entrada).__name__} {entrada!r}",
             **{k: v for k, v in (e.get("ctx") or {}).items()}}
    try:
        return plantilla.format(**datos)
    except (KeyError, IndexError):
        return e["msg"]


def _describir_errores(error: ValidationError) -> tuple[str, str]:
    """Devuelve (motivo legible, campos con error separados por coma)."""
    motivos, campos = [], []
    for e in error.errors():
        campo = ".".join(str(p) for p in e["loc"]) or "registro"
        msg = _mensaje(e)
        motivos.append(f"{campo}: {msg}")
        if campo not in campos:
            campos.append(campo)
    return " | ".join(motivos), ",".join(campos)


# ---------------------------------------------------------------------------
# Segregación de la tabla integrada
# ---------------------------------------------------------------------------
def validar_integrada(integrada: pd.DataFrame) -> dict:
    """Valida cada fila de ``integrada`` con ``TransaccionValidada``.

    Returns: dict con
      * ``aptos``: las filas válidas, sin cambios y con las mismas columnas.
      * ``cuarentena``: una fila por registro rechazado, con ``COLUMNAS_CUARENTENA``.
    Un DataFrame vacío devuelve dos tablas vacías con sus columnas.
    """
    if not isinstance(integrada, pd.DataFrame):
        raise TypeError("validar_integrada espera un pandas.DataFrame")
    posiciones_ok, rechazos = [], []
    for pos, fila in enumerate(integrada.to_dict("records")):
        datos = _fila_a_dict(fila)
        try:
            TransaccionValidada.model_validate(datos)
            posiciones_ok.append(pos)
        except ValidationError as e:
            motivo, campos = _describir_errores(e)
            rechazos.append(_registro_cuarentena(datos, ETAPA_PYDANTIC, motivo, campos))
        except Exception as e:  # cualquier otra falla: a cuarentena, nunca romper el lote
            rechazos.append(_registro_cuarentena(datos, ETAPA_PYDANTIC,
                                                 f"ERROR_INESPERADO: {type(e).__name__}: {e}", ""))
    aptos = integrada.iloc[posiciones_ok].reset_index(drop=True)
    return {"aptos": aptos, "cuarentena": pd.DataFrame(rechazos, columns=COLUMNAS_CUARENTENA)}


def _primero(fila: dict, *claves):
    """Primer valor no nulo entre ``claves`` (en tablas mezcladas ATM/SPEI el otro viene como NaN)."""
    return next((v for v in (_a_python(fila.get(k)) for k in claves) if v is not None), None)


def _registro_cuarentena(fila: dict, etapa: str, motivo: str, campos: str, indice=None) -> dict:
    ident = _primero(fila, "id_operacion", "id_transaccion", "transfer_id")
    fuente = fila.get("fuente")
    return {
        "id_registro": None if _a_python(ident) is None else str(ident),
        "fuente": None if _a_python(fuente) is None else str(fuente).upper(),
        "etapa_origen": etapa,
        "motivo_rechazo_cuarentena": motivo,
        "campos_con_error": campos,
        "indice_original": None if _a_python(indice) is None else int(indice),
        "registro_original": _a_json(fila),
    }


# ---------------------------------------------------------------------------
# Consolidación de la cuarentena de todos los módulos
# ---------------------------------------------------------------------------
def _desde_tabla(df: pd.DataFrame, etapa: str, motivo_fn, campos_fn, fuente=None) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=COLUMNAS_CUARENTENA)
    filas = []
    for fila in df.to_dict("records"):
        if fuente is not None:
            fila = {**fila, "fuente": fuente}
        filas.append(_registro_cuarentena(fila, etapa, motivo_fn(fila), campos_fn(fila),
                                          fila.get("indice_original")))
    return pd.DataFrame(filas, columns=COLUMNAS_CUARENTENA)


def consolidar_cuarentena(limpio: dict, transformado: dict, cuarentena_pydantic: pd.DataFrame,
                          incluir_duplicados: bool = True) -> pd.DataFrame:
    """Une en una tabla todos los registros apartados en el pipeline.

    Orígenes:
      1. Ingeniero 1: ``duplicados_atm``/``duplicados_spei`` (si ``incluir_duplicados``).
      2. Ingeniero 1: ``rechazados_atm``/``rechazados_spei`` (montos imposibles).
      3. Ingeniero 2: ``huerfanas`` y ``rechazadas_cuenta`` (cruce con catálogo).
      4. Ingeniero 3: rechazos de Pydantic.
    """
    partes = []
    if incluir_duplicados:
        for clave in ("duplicados_atm", "duplicados_spei"):
            partes.append(_desde_tabla(
                limpio.get(clave), ETAPA_DUPLICADO,
                lambda f: f"DUPLICADO_EXACTO: repite la posición {_a_python(f.get('duplicado_de'))}",
                lambda f: ""))
    for clave in ("rechazados_atm", "rechazados_spei"):
        partes.append(_desde_tabla(
            limpio.get(clave), ETAPA_IMPOSIBLE,
            lambda f: f"MONTO_IMPOSIBLE_{f.get('motivo_rechazo')}: valor {f.get('valor_original')!r}",
            lambda f: str(f.get("columna_validada") or "")))
    partes.append(_desde_tabla(
        transformado.get("huerfanas"), ETAPA_CATALOGO,
        lambda f: f"{f.get('motivo_separacion')}: la cuenta {_primero(f, 'id_cuenta', 'account_id')} "
                  "no existe en catalogo_cuentas.csv",
        lambda f: "id_cuenta"))
    partes.append(_desde_tabla(
        transformado.get("rechazadas_cuenta"), ETAPA_CATALOGO,
        lambda f: f"{f.get('motivo_separacion')}: la cuenta {_primero(f, 'id_cuenta', 'account_id')} "
                  f"está {f.get('estado_cuenta_catalogo')}",
        lambda f: "estado_cuenta_catalogo"))
    partes.append(cuarentena_pydantic if cuarentena_pydantic is not None
                  else pd.DataFrame(columns=COLUMNAS_CUARENTENA))
    partes = [p for p in partes if len(p)]
    if not partes:
        return pd.DataFrame(columns=COLUMNAS_CUARENTENA)
    out = pd.concat(partes, ignore_index=True)[COLUMNAS_CUARENTENA]
    out["indice_original"] = out["indice_original"].astype("Int64")
    return out


def segregar(limpio: dict, transformado: dict, incluir_duplicados: bool = True) -> dict:
    """Flujo completo de la Etapa 1 del Ingeniero 3.

    Args:
        limpio: dict de ``limpiar(..., guardar=False)``.
        transformado: dict de ``ejecutar_transformacion(...)``.
        incluir_duplicados: si los duplicados del Ingeniero 1 van a cuarentena
            (acuerdo pendiente B del equipo; por defecto sí).

    Returns: dict con ``transacciones_limpias``, ``cuarentena_errores`` y
    ``resumen`` (conteos por etapa y motivo, más el cuadre de filas).
    """
    validacion = validar_integrada(transformado["integrada"])
    cuarentena = consolidar_cuarentena(limpio, transformado, validacion["cuarentena"], incluir_duplicados)
    return {
        "transacciones_limpias": validacion["aptos"],
        "cuarentena_errores": cuarentena,
        "resumen": resumir(validacion["aptos"], cuarentena),
    }


def resumir(aptos: pd.DataFrame, cuarentena: pd.DataFrame) -> pd.DataFrame:
    """Conteos de aptos y cuarentena por fuente y etapa de origen."""
    filas = [("transacciones_limpias", f, "", int(n))
             for f, n in aptos["fuente"].value_counts().sort_index().items()] if len(aptos) else []
    if len(cuarentena):
        motivo_corto = cuarentena["motivo_rechazo_cuarentena"].str.split(":").str[0]
        g = cuarentena.assign(motivo=motivo_corto).groupby(["fuente", "etapa_origen", "motivo"], dropna=False).size()
        filas += [("cuarentena_errores", f, f"{e} / {m}", int(n)) for (f, e, m), n in g.items()]
    filas.append(("total", "", "", len(aptos) + len(cuarentena)))
    return pd.DataFrame(filas, columns=["tabla", "fuente", "detalle", "registros"])
