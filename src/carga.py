"""Ingeniero 3 - Etapa 2: carga transaccional a SQLite.

Persiste la salida de ``segregar()`` en tres tablas:

* ``transacciones_limpias``: registros que pasaron la validación Pydantic.
* ``cuarentena_errores``: registros apartados en cualquier etapa, con su
  ``motivo_rechazo_cuarentena`` y la fila original en JSON.
* ``bitacora_cargas``: una fila por intento de carga (EXITOSA o REVERTIDA).

Cada carga es un **lote atómico**: se reemplaza el contenido de las dos tablas
de datos dentro de una sola transacción. Si algo falla a la mitad,
``conn.rollback()`` deja la base exactamente como estaba (la carga anterior
sigue completa) y el fallo queda registrado en la bitácora. Correr el
pipeline dos veces no duplica operaciones.

Las restricciones ``CHECK`` de las tablas repiten las reglas principales de
Pydantic: son una segunda barrera por si alguien inserta datos sin validar.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.limpieza import RAIZ
from src.validacion import COLUMNAS_CUARENTENA, _a_python

RUTA_BD = RAIZ / "data" / "etl_fintech.db"

ESQUEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS bitacora_cargas (
    lote_id           TEXT PRIMARY KEY,
    inicio            TEXT NOT NULL,
    fin               TEXT,
    estado            TEXT NOT NULL CHECK (estado IN ('EN_PROCESO', 'EXITOSA', 'REVERTIDA')),
    filas_limpias     INTEGER NOT NULL DEFAULT 0,
    filas_cuarentena  INTEGER NOT NULL DEFAULT 0,
    mensaje           TEXT
);

CREATE TABLE IF NOT EXISTS transacciones_limpias (
    id_operacion            TEXT PRIMARY KEY,
    fuente                  TEXT NOT NULL CHECK (fuente IN ('ATM', 'SPEI')),
    fecha                   TEXT NOT NULL CHECK (fecha GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    fecha_hora              TEXT NOT NULL,
    id_cuenta               TEXT NOT NULL,
    tipo_operacion          TEXT NOT NULL,
    monto_mxn               REAL NOT NULL CHECK (monto_mxn >= 0),
    monto_original_mxn      REAL CHECK (monto_original_mxn IS NULL OR monto_original_mxn >= 0),
    moneda_origen           TEXT NOT NULL CHECK (moneda_origen IN ('MXN', 'USD')),
    resultado               TEXT NOT NULL,
    canal                   TEXT NOT NULL,
    comision_mxn            REAL CHECK (comision_mxn IS NULL OR comision_mxn >= 0),
    tipo_cuenta             TEXT NOT NULL,
    estado_cuenta_catalogo  TEXT NOT NULL,
    monto_imputado          INTEGER NOT NULL CHECK (monto_imputado IN (0, 1)),
    monto_recortado         INTEGER NOT NULL CHECK (monto_recortado IN (0, 1)),
    id_cajero               TEXT,
    cajero_imputado         INTEGER CHECK (cajero_imputado IS NULL OR cajero_imputado IN (0, 1)),
    beneficiary_clabe       TEXT CHECK (beneficiary_clabe IS NULL OR length(beneficiary_clabe) = 18),
    notification_email      TEXT,
    email_corregido         INTEGER CHECK (email_corregido IS NULL OR email_corregido IN (0, 1)),
    concept                 TEXT,
    monto_mxn_minmax        REAL NOT NULL CHECK (monto_mxn_minmax BETWEEN 0 AND 1),
    monto_mxn_categoria     TEXT NOT NULL,
    franja_horaria          TEXT NOT NULL,
    lote_id                 TEXT NOT NULL REFERENCES bitacora_cargas (lote_id)
);

CREATE TABLE IF NOT EXISTS cuarentena_errores (
    id_cuarentena              INTEGER PRIMARY KEY AUTOINCREMENT,
    id_registro                TEXT,
    fuente                     TEXT CHECK (fuente IS NULL OR fuente IN ('ATM', 'SPEI')),
    etapa_origen               TEXT NOT NULL,
    motivo_rechazo_cuarentena  TEXT NOT NULL CHECK (length(trim(motivo_rechazo_cuarentena)) > 0),
    campos_con_error           TEXT,
    indice_original            INTEGER,
    registro_original          TEXT NOT NULL,
    lote_id                    TEXT NOT NULL REFERENCES bitacora_cargas (lote_id)
);

CREATE INDEX IF NOT EXISTS ix_limpias_cuenta ON transacciones_limpias (id_cuenta);
CREATE INDEX IF NOT EXISTS ix_limpias_fecha ON transacciones_limpias (fecha);
CREATE INDEX IF NOT EXISTS ix_cuarentena_etapa ON cuarentena_errores (etapa_origen);
CREATE INDEX IF NOT EXISTS ix_cuarentena_registro ON cuarentena_errores (id_registro);

CREATE VIEW IF NOT EXISTS resumen_cuarentena AS
SELECT fuente,
       etapa_origen,
       substr(motivo_rechazo_cuarentena, 1,
              instr(motivo_rechazo_cuarentena || ':', ':') - 1) AS motivo,
       count(*) AS registros
FROM cuarentena_errores
GROUP BY fuente, etapa_origen, motivo
ORDER BY registros DESC;
"""

COLUMNAS_LIMPIAS = [
    "id_operacion", "fuente", "fecha", "fecha_hora", "id_cuenta", "tipo_operacion", "monto_mxn",
    "monto_original_mxn", "moneda_origen", "resultado", "canal", "comision_mxn", "tipo_cuenta",
    "estado_cuenta_catalogo", "monto_imputado", "monto_recortado", "id_cajero", "cajero_imputado",
    "beneficiary_clabe", "notification_email", "email_corregido", "concept", "monto_mxn_minmax",
    "monto_mxn_categoria", "franja_horaria",
]
_BOOLEANAS = {"monto_imputado", "monto_recortado", "cajero_imputado", "email_corregido"}


def _ahora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def nuevo_lote_id() -> str:
    return f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"


def conectar(ruta_bd=RUTA_BD) -> sqlite3.Connection:
    Path(ruta_bd).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ruta_bd)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def crear_esquema(conn: sqlite3.Connection) -> None:
    """Crea las tablas, índices y la vista si no existen (no borra datos)."""
    conn.executescript(ESQUEMA_SQL)
    conn.commit()


# ---------------------------------------------------------------------------
# Preparación de filas
# ---------------------------------------------------------------------------
def _valor_sql(columna: str, v):
    v = _a_python(v)
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    if columna in _BOOLEANAS:
        return int(bool(v))
    return v


def preparar_limpias(limpias: pd.DataFrame) -> list[tuple]:
    """Convierte las filas aptas a tuplas para SQLite.

    ``fecha_hora`` se guarda en ISO 8601 con zona (``2026-09-15T08:45:00-06:00``)
    y se agrega ``fecha`` en ``YYYY-MM-DD`` (formato que pide la guía).
    """
    filas = []
    for fila in limpias.to_dict("records"):
        fecha_hora = _a_python(fila["fecha_hora"])
        fila = {**fila, "fecha": fecha_hora.date().isoformat() if isinstance(fecha_hora, datetime) else None}
        filas.append(tuple(_valor_sql(c, fila.get(c)) for c in COLUMNAS_LIMPIAS))
    return filas


def preparar_cuarentena(cuarentena: pd.DataFrame) -> list[tuple]:
    return [tuple(_valor_sql(c, fila.get(c)) for c in COLUMNAS_CUARENTENA)
            for fila in cuarentena.to_dict("records")]


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------
def cargar(limpias: pd.DataFrame, cuarentena: pd.DataFrame, ruta_bd=RUTA_BD, lote_id: str | None = None) -> dict:
    """Carga un lote completo en una sola transacción.

    1. Registra el lote en ``bitacora_cargas`` como EN_PROCESO.
    2. Vacía ``transacciones_limpias`` y ``cuarentena_errores``.
    3. Inserta las filas nuevas.
    4. Marca el lote como EXITOSA y hace ``conn.commit()``.

    Si cualquier paso falla se hace ``conn.rollback()``: la base queda como
    antes de la carga y el lote se registra como REVERTIDA con el error.
    No lanza la excepción: devuelve el estado para que ``main.py`` decida.

    Returns: dict con ``lote_id``, ``estado``, ``filas_limpias``,
    ``filas_cuarentena`` y ``error`` (None si fue exitosa).
    """
    lote_id = lote_id or nuevo_lote_id()
    inicio = _ahora()
    conn = conectar(ruta_bd)
    try:
        crear_esquema(conn)
        try:
            filas_l = preparar_limpias(limpias)
            filas_c = preparar_cuarentena(cuarentena)
            conn.execute("INSERT INTO bitacora_cargas (lote_id, inicio, estado) VALUES (?, ?, 'EN_PROCESO')",
                         (lote_id, inicio))
            conn.execute("DELETE FROM transacciones_limpias")
            conn.execute("DELETE FROM cuarentena_errores")
            conn.executemany(
                f"INSERT INTO transacciones_limpias ({', '.join(COLUMNAS_LIMPIAS)}, lote_id) "
                f"VALUES ({', '.join('?' * len(COLUMNAS_LIMPIAS))}, ?)",
                [f + (lote_id,) for f in filas_l])
            conn.executemany(
                f"INSERT INTO cuarentena_errores ({', '.join(COLUMNAS_CUARENTENA)}, lote_id) "
                f"VALUES ({', '.join('?' * len(COLUMNAS_CUARENTENA))}, ?)",
                [f + (lote_id,) for f in filas_c])
            conn.execute(
                "UPDATE bitacora_cargas SET estado = 'EXITOSA', fin = ?, filas_limpias = ?, filas_cuarentena = ? "
                "WHERE lote_id = ?", (_ahora(), len(filas_l), len(filas_c), lote_id))
            conn.commit()
            return {"lote_id": lote_id, "estado": "EXITOSA", "filas_limpias": len(filas_l),
                    "filas_cuarentena": len(filas_c), "error": None}
        except Exception as e:  # noqa: BLE001 - cualquier fallo revierte el lote completo
            conn.rollback()
            error = f"{type(e).__name__}: {e}"
            _registrar_revertida(conn, lote_id, inicio, error)
            return {"lote_id": lote_id, "estado": "REVERTIDA", "filas_limpias": 0,
                    "filas_cuarentena": 0, "error": error}
    finally:
        conn.close()


def _registrar_revertida(conn: sqlite3.Connection, lote_id: str, inicio: str, error: str) -> None:
    """Deja constancia del lote revertido en una transacción aparte."""
    try:
        conn.execute(
            "INSERT OR REPLACE INTO bitacora_cargas (lote_id, inicio, fin, estado, mensaje) "
            "VALUES (?, ?, ?, 'REVERTIDA', ?)", (lote_id, inicio, _ahora(), error[:2000]))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()


# ---------------------------------------------------------------------------
# Consultas de apoyo (pruebas, reporte y defensa)
# ---------------------------------------------------------------------------
def leer(sql: str, ruta_bd=RUTA_BD, params=()) -> pd.DataFrame:
    """Ejecuta una consulta de solo lectura y la devuelve como DataFrame."""
    # closing(): "with sqlite3.connect()" no cierra la conexión y en Windows bloquearía el archivo
    with closing(sqlite3.connect(f"file:{Path(ruta_bd).as_posix()}?mode=ro", uri=True)) as conn:
        return pd.read_sql_query(sql, conn, params=params)


def contar(ruta_bd=RUTA_BD) -> dict:
    """Número de filas en cada tabla."""
    tablas = ("transacciones_limpias", "cuarentena_errores", "bitacora_cargas")
    return {t: int(leer(f"SELECT count(*) AS n FROM {t}", ruta_bd)["n"].iloc[0]) for t in tablas}
