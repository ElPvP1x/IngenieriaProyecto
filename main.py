"""Orquestador del pipeline ETL - Grupo 2 Fintech (ATM + SPEI).

Ejecuta los tres módulos en orden:

  1. Ingeniero 1 - src/limpieza.py        cargar_datos() y limpiar(..., guardar=False)
  2. Ingeniero 2 - src/transformacion.py  ejecutar_transformacion()
  3. Ingeniero 3 - src/validacion.py      segregar()  (Pydantic + cuarentena)
                   src/carga.py           cargar()    (SQLite con commit/rollback)

Uso (desde la raíz del proyecto):
    py -3.14 main.py
    py -3.14 main.py --bd data/otra.db --sin-duplicados --sin-reportes

Los archivos originales nunca se modifican. Si una etapa falla, el error se
informa de forma controlada y la base de datos no se toca.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from src.carga import RUTA_BD, cargar, contar, leer
from src.limpieza import DIR_REPORTES, cargar_datos, limpiar
from src.transformacion import ejecutar_transformacion
from src.validacion import segregar


class ErrorDeEtapa(Exception):
    """Falla controlada de una etapa del pipeline."""


def _etapa(nombre: str, funcion, *args, **kwargs):
    inicio = time.perf_counter()
    try:
        resultado = funcion(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 - se informa y se detiene sin traza cruda
        raise ErrorDeEtapa(f"{nombre}: {type(e).__name__}: {e}") from e
    print(f"  [ok] {nombre} ({time.perf_counter() - inicio:.2f} s)")
    return resultado


def ejecutar_pipeline(ruta_bd=RUTA_BD, incluir_duplicados=True, dir_reportes=DIR_REPORTES,
                      exportar_reportes=True) -> dict:
    """Corre el pipeline completo y devuelve los resultados de cada etapa."""
    datos = _etapa("Extracción (cargar_datos)", cargar_datos)
    limpio = _etapa("Ingeniero 1: limpieza", limpiar, datos["atm"], datos["spei"], guardar=False)
    transformado = _etapa("Ingeniero 2: transformación", ejecutar_transformacion, limpio, datos["catalogo"])
    segregado = _etapa("Ingeniero 3: validación Pydantic", segregar, limpio, transformado,
                       incluir_duplicados=incluir_duplicados)
    carga = _etapa("Ingeniero 3: carga a SQLite", cargar, segregado["transacciones_limpias"],
                   segregado["cuarentena_errores"], ruta_bd)
    if exportar_reportes and carga["estado"] == "EXITOSA":
        Path(dir_reportes).mkdir(parents=True, exist_ok=True)
        segregado["resumen"].to_csv(Path(dir_reportes) / "resumen_pipeline.csv", index=False, encoding="utf-8")
        segregado["cuarentena_errores"].to_csv(Path(dir_reportes) / "cuarentena_errores.csv",
                                               index=False, encoding="utf-8")
    return {"datos": datos, "limpio": limpio, "transformado": transformado,
            "segregado": segregado, "carga": carga}


def _imprimir_resumen(r: dict, ruta_bd) -> None:
    entrada = len(r["datos"]["atm"]) + len(r["datos"]["spei"])
    limpias = len(r["segregado"]["transacciones_limpias"])
    cuarentena = len(r["segregado"]["cuarentena_errores"])
    print("\nResumen del lote")
    print(f"  Registros de entrada (ATM + SPEI): {entrada}")
    print(f"  transacciones_limpias:             {limpias}")
    print(f"  cuarentena_errores:                {cuarentena}")
    print(f"  Cuadre (limpias + cuarentena):     {limpias + cuarentena}"
          f"  {'OK' if limpias + cuarentena == entrada else '<-- NO CUADRA'}")
    print("\nCuarentena por origen y motivo")
    print(leer("SELECT * FROM resumen_cuarentena", ruta_bd).to_string(index=False))
    print(f"\nBase de datos: {ruta_bd}")
    print(f"  Filas en tablas: {contar(ruta_bd)}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Pipeline ETL robusto con zona de cuarentena (ATM + SPEI).")
    p.add_argument("--bd", default=str(RUTA_BD), help="ruta del archivo SQLite (por defecto data/etl_fintech.db)")
    p.add_argument("--sin-duplicados", action="store_true",
                   help="no enviar los duplicados del Ingeniero 1 a la cuarentena")
    p.add_argument("--sin-reportes", action="store_true",
                   help="no escribir reportes/resumen_pipeline.csv ni reportes/cuarentena_errores.csv")
    args = p.parse_args(argv)

    print("Pipeline ETL - Grupo 2 Fintech (ATM + SPEI)")
    try:
        r = ejecutar_pipeline(args.bd, incluir_duplicados=not args.sin_duplicados,
                              exportar_reportes=not args.sin_reportes)
    except ErrorDeEtapa as e:
        print(f"\n[ERROR] El pipeline se detuvo en la etapa {e}")
        print("        La base de datos no se modificó.")
        return 1

    carga = r["carga"]
    if carga["estado"] != "EXITOSA":
        print(f"\n[ERROR] Carga REVERTIDA (lote {carga['lote_id']}): {carga['error']}")
        print("        Se hizo rollback; la base conserva la carga anterior.")
        return 1
    print(f"\nLote {carga['lote_id']}: carga EXITOSA (commit)")
    _imprimir_resumen(r, args.bd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
