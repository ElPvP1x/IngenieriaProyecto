# Validación, cuarentena y carga a base de datos (Ingeniero/a 3)

Este documento explica los módulos `src/validacion.py` y `src/carga.py`, el orquestador `main.py` y las pruebas de estrés. Cubre las fases 6 y 7 de la guía y el criterio de resiliencia.

## Ejecución

Todos los comandos se ejecutan desde la raíz del proyecto.

| Tarea | Comando |
|---|---|
| Instalar dependencias (incluye `pydantic`) | `py -3.14 -m pip install -r requirements.txt` |
| Correr el pipeline completo | `py -3.14 main.py` |
| Correr todas las pruebas (198) | `py -3.14 -m unittest discover -s tests -v` |
| Solo las pruebas del Ingeniero 3 | `py -3.14 -m unittest tests.test_validacion tests.test_carga tests.test_estres -v` |

`main.py` acepta tres opciones:

- `--bd RUTA`: cambia el archivo SQLite. Por defecto es `data/etl_fintech.db`.
- `--sin-duplicados`: no manda los duplicados a la cuarentena (ver el acuerdo B más abajo).
- `--sin-reportes`: no escribe los CSV de evidencia.

## Flujo del pipeline

```
cargar_datos()                                   originales (solo lectura)
  -> limpiar(..., guardar=False)                 Ingeniero 1: duplicados, imputación, imposibles, IQR
  -> ejecutar_transformacion(limpio, catalogo)   Ingeniero 2: fechas, USD->MXN, catálogo, Min-Max
  -> segregar(limpio, transformado)              Ingeniero 3: Pydantic + cuarentena consolidada
  -> cargar(limpias, cuarentena)                 Ingeniero 3: SQLite con commit / rollback
```

Resultado con los datos reales:

| Tabla | Registros |
|---|---:|
| `transacciones_limpias` | 683 (354 ATM + 329 SPEI) |
| `cuarentena_errores` | 537 |
| **Total** | **1,220 = 620 ATM + 600 SPEI de entrada** |

Ningún registro se pierde: cada fila de entrada termina en exactamente una de las dos tablas. Al final de cada ejecución, `main.py` imprime este cuadre.

## Fase 6: validación con Pydantic

### Esquema `TransaccionValidada`

Hay un solo `BaseModel` para la tabla integrada del Ingeniero 2, que contiene ATM y SPEI. Los campos que solo existen en una fuente son opcionales en el modelo, y se exigen según el valor de `fuente`.

Los campos numéricos, de fecha y booleanos son **estrictos**. Por ejemplo, un monto que llega como texto (`"2000"`) se rechaza en lugar de convertirse sin avisar.

| Regla | Mecanismo | Justificación |
|---|---|---|
| CLABE de exactamente 18 dígitos | `@field_validator("beneficiary_clabe")` | Reto explícito del Grupo 2 ("CLABEs con menos de 18 dígitos") |
| La cuenta debe estar ACTIVA | `@field_validator("estado_cuenta_catalogo")` | Regla del equipo aplicada en la etapa 3 del Ingeniero 2; aquí se verifica otra vez |
| La fecha debe tener zona horaria | `@field_validator("fecha_hora")` | Contrato del Ingeniero 2 (UTC-6); una fecha nula o con texto basura se rechaza |
| Categoría de monto y franja horaria conocidas | `@field_validator` | Las etiquetas se toman de `src/transformacion.py` |
| `id_cuenta` con formato `CTA-9999` | `Field(pattern=...)` | Formato del catálogo |
| Montos y comisión ≥ 0 y finitos | `Field(ge=0, allow_inf_nan=False)` | Reto "montos negativos absurdos" |
| `monto_mxn_minmax` en [0, 1] | `Field(ge=0, le=1)` | Fase 5 de la guía |
| `fuente` ATM/SPEI; moneda MXN/USD | `Literal[...]` | Contrato del Ingeniero 2 |
| ATM: comisión y cajero obligatorios, moneda MXN, tipo y resultado conocidos | `@model_validator` | Reto "comisiones nulas"; catálogos de estados del Ingeniero 2 |
| ATM: monto 0 solo en `CONSULTA_SALDO` | `@model_validator` | REGLAS_DE_NEGOCIO.md §6 |
| SPEI: CLABE obligatoria, monto > 0, status conocido | `@model_validator` | Reto del Grupo 2 |

**Qué no se valida, a propósito.** No se rechazan las comisiones incoherentes, como una consulta de saldo con comisión, porque el proyecto no define una regla de negocio para ellas (REGLAS_DE_NEGOCIO.md §7). Tampoco se verifica el dígito de control de la CLABE, porque la guía solo menciona la longitud.

### Segregación: `try ... except ValidationError`

`validar_integrada()` recorre la tabla fila por fila:

```python
try:
    TransaccionValidada.model_validate(fila)
    aptos.append(fila)
except ValidationError as e:
    cuarentena.append(fila + motivo_rechazo_cuarentena)
except Exception as e:          # cualquier otra falla tampoco detiene el lote
    cuarentena.append(fila + "ERROR_INESPERADO: ...")
```

- Si un registro tiene varios errores, se guardan **todos** en el motivo, separados por ` | `, y los campos involucrados quedan en `campos_con_error`.
- Los mensajes de Pydantic se traducen al español.
- La validación es una compuerta: **no cambia ningún valor**, ni vuelve a imputar ni a recortar.

### Cuarentena consolidada

`consolidar_cuarentena()` junta en una sola tabla todo lo que el pipeline aparta, sin importar la etapa:

| `etapa_origen` | Origen | Registros reales |
|---|---|---:|
| `LIMPIEZA_DUPLICADO` | Ingeniero 1, `duplicados_atm/spei` | 20 |
| `LIMPIEZA_MONTO_IMPOSIBLE` | Ingeniero 1, `rechazados_atm/spei` | 0 (la lógica sí está probada con datos inyectados) |
| `TRANSFORMACION_CATALOGO` | Ingeniero 2: 61 cuentas huérfanas + 427 cuentas bloqueadas o suspendidas | 488 |
| `VALIDACION_PYDANTIC` | Ingeniero 3: CLABE `"12345"` | 29 |

Las 46 CLABE `"12345"` terminan **todas** en cuarentena: 29 por Pydantic y 17 que ya habían salido antes por su cuenta.

## Fase 7: carga transaccional a SQLite

### Tablas (`data/etl_fintech.db`)

| Tabla | Contenido |
|---|---|
| `transacciones_limpias` | Una fila por operación válida. Llave primaria `id_operacion`. Incluye `fecha` (`YYYY-MM-DD`, formato de la guía), `fecha_hora` (ISO 8601 con `-06:00`), `monto_mxn` y `monto_original_mxn` (antes y después del capping), Min-Max, segmento, todas las banderas de trazabilidad y `lote_id`. |
| `cuarentena_errores` | `id_registro`, `fuente`, `etapa_origen`, `motivo_rechazo_cuarentena`, `campos_con_error`, `indice_original`, `registro_original` (fila completa en JSON) y `lote_id`. |
| `bitacora_cargas` | Una fila por cada intento de carga: `inicio`, `fin`, `estado` (EXITOSA o REVERTIDA), conteos y mensaje de error. |
| Vista `resumen_cuarentena` | Conteo de la cuarentena por fuente, etapa y motivo. |

Las restricciones `CHECK`, `NOT NULL`, `PRIMARY KEY` y `FOREIGN KEY` repiten las reglas principales de Pydantic: CLABE de 18 caracteres, montos ≥ 0, Min-Max en [0, 1], fecha `YYYY-MM-DD`, etc. Funcionan como una **segunda barrera**: aunque alguien inserte datos sin pasar por la validación, la base los rechaza.

### Transacción: `commit()` / `rollback()`

`cargar()` procesa cada ejecución como **un lote atómico**:

1. Registra el lote en `bitacora_cargas` como `EN_PROCESO`.
2. Vacía las dos tablas de datos e inserta las filas nuevas.
3. Marca el lote como `EXITOSA` y ejecuta `conn.commit()`.

Si **cualquier** paso falla (una llave duplicada, una `CHECK`, una columna faltante, un error de tipo), se ejecuta `conn.rollback()`. La base queda exactamente como estaba, con la carga anterior completa, y el intento se registra como `REVERTIDA` en una transacción aparte, con el mensaje de error.

Correr el pipeline varias veces **no duplica operaciones**: cada carga exitosa reemplaza la anterior completa, y la bitácora conserva el historial de todas las ejecuciones.

### Consultas útiles para la defensa

```sql
SELECT * FROM resumen_cuarentena;
SELECT * FROM bitacora_cargas ORDER BY inicio;
SELECT fuente, count(*), round(sum(monto_mxn), 2) FROM transacciones_limpias GROUP BY fuente;
SELECT id_registro, motivo_rechazo_cuarentena FROM cuarentena_errores
 WHERE etapa_origen = 'VALIDACION_PYDANTIC';
```

## Resiliencia: pruebas de estrés

`tests/test_estres.py` corrompe **en memoria** copias de los datos crudos y corre el pipeline completo, de los tres ingenieros hasta SQLite, con una base temporal. En cada escenario se verifica que no haya excepciones, que el cuadre de filas sea exacto, que el registro dañado llegue a cuarentena con un motivo y que la carga termine en `commit`.

| Escenario | Qué se inyecta |
|---|---|
| 13 corrupciones ATM, una por una | monto negativo, en texto o infinito; comisión nula o negativa; fecha basura, imposible (31 de febrero) o nula; resultado y tipo desconocidos; cuenta huérfana, nula o con texto de inyección SQL |
| 12 corrupciones SPEI, una por una | CLABE de 17 dígitos, con letras o nula; monto negativo, en texto o cero; moneda EUR o nula; timestamp basura o nulo; status desconocido; cuenta huérfana |
| Todas juntas | Las 25 corrupciones en un solo lote |
| Lotes completos | Todo ATM con montos en texto; todas las CLABE inválidas; lote vacío |
| Volumen | 12,200 registros (los datos ×10), en unos 3 s |
| Fallas del orquestador | Columna faltante en los crudos → `main.py` termina con código 1 y la base no cambia. ID repetido en la carga → `rollback`; la bitácora registra EXITOSA y luego REVERTIDA |

`tests/test_carga.py` prueba la carga, la idempotencia, el `rollback` ante 4 tipos de falla y las restricciones de la base. `tests/test_validacion.py` prueba cada regla del esquema con filas sintéticas y con los datos reales.

## Archivos que agrega este rol

| Archivo | Contenido |
|---|---|
| `src/validacion.py` | Esquema Pydantic, segregación y cuarentena consolidada |
| `src/carga.py` | Esquema SQLite y carga con commit/rollback |
| `main.py` | Orquestador del pipeline completo |
| `tests/test_validacion.py`, `tests/test_carga.py`, `tests/test_estres.py` | 81 pruebas |
| `data/etl_fintech.db` | Base de datos generada por `main.py` (entregable) |
| `reportes/resumen_pipeline.csv`, `reportes/cuarentena_errores.csv` | Evidencia generada por `main.py` |

También se agregó `pydantic` a `requirements.txt`. No se modificó ningún archivo de los Ingenieros 1 y 2 ni los datasets originales.

## Acuerdos del equipo que afectan a este módulo

| # | Decisión | Cómo quedó implementada |
|---|---|---|
| A | ¿Importe original o recortado? | Se guardan ambos: `monto_mxn` (procesado) y `monto_original_mxn` |
| B | ¿Duplicados a cuarentena o a bitácora? | A cuarentena por defecto, con el motivo `DUPLICADO_EXACTO`. Con `--sin-duplicados` no se envían |
| C | CLABE `"12345"` | A cuarentena (reto explícito de la guía) |
| C | Comisiones incoherentes | Se conservan y no se rechazan (no hay regla de negocio) |
| D | ¿Quién escribe la base? | El Ingeniero 3, de forma centralizada; los otros módulos se llaman sin escribir archivos |
| E | Esquema entre los Ingenieros 2 y 3 | La tabla `integrada` de `ejecutar_transformacion()` |
| — | Cuentas BLOQUEADA/SUSPENDIDA | A cuarentena (regla "por ahora" del Ingeniero 2, verificada también en Pydantic). Mandan 427 de 1,220 registros fuera |

## Observaciones para el Ingeniero 2

1. **Discretización.** La guía pide **3 segmentos** (Bajo, Medio, Alto) y la etapa 4 genera 4 (agrega `MUY_ALTO`). Este módulo toma las etiquetas de `ETIQUETAS_MONTO`, así que si se cambian allá, la validación se ajusta sola.
2. **Columna faltante.** Si a un archivo crudo le falta una columna completa (por ejemplo `comision_aplicada`), `etapa5_integrar` lanza `KeyError`. `main.py` lo atrapa y termina de forma controlada sin tocar la base, pero el pipeline no procesa ese lote.
