# Documentación técnica: `src/limpieza.py` (Ingeniero/a 1)

Este documento explica el módulo de calidad, duplicados, faltantes, montos imposibles y outliers, y su contrato de integración con `main.py` y con los Ingenieros 2 y 3. Las reglas de negocio y su justificación están en [REGLAS_DE_NEGOCIO.md](REGLAS_DE_NEGOCIO.md).

## Entorno y comandos

**Entorno probado:** Python **3.14.3**, con pandas 3.0.5, numpy 2.3.4 y matplotlib 3.10.7. Las dependencias están en [requirements.txt](requirements.txt). matplotlib solo se necesita para `graficar_boxplots()`.

Todos los comandos se ejecutan desde la raíz del proyecto. En Windows se usa `py -3.14`; en macOS o Linux, `python3.14`.

| Tarea | Comando |
|---|---|
| Instalar dependencias | `py -3.14 -m pip install -r requirements.txt` |
| Ejecutar las pruebas (68) | `py -3.14 -m unittest discover -s tests -v` |
| Generar perfilado, datasets, reportes y gráficas | `py -3.14 -m src.limpieza` |

En el equipo donde se desarrolló, el `python` por defecto (3.12) no tiene pandas. Por eso los comandos indican la versión explícitamente.

## Estructura

```
proyecto/
├── catalogo_cuentas.csv            # original (solo lectura)
├── transacciones_atm.csv           # original (solo lectura)
├── transferencias_digitales.json   # original (solo lectura)
├── requirements.txt
├── src/
│   ├── __init__.py
│   └── limpieza.py
├── data/processed/                 # datasets generados
├── reportes/                       # reportes CSV y gráficas PNG
├── tests/test_limpieza.py          # 68 pruebas
├── REGLAS_DE_NEGOCIO.md
└── DOCUMENTACION_LIMPIEZA.md
```

## Uso desde `main.py`

**Regla de integración:** `main.py` debe llamar `limpiar(atm, spei, guardar=False)`. La persistencia le corresponde al Ingeniero 3, así que este módulo no debe escribir archivos durante la ejecución integrada.

```python
from src.limpieza import cargar_datos, limpiar

datos = cargar_datos()                                     # solo lectura
resultado = limpiar(datos["atm"], datos["spei"], guardar=False)

atm_limpio = resultado["atm"]                              # para el Ingeniero 2
spei_limpio = resultado["spei"]
duplicados = [resultado["duplicados_atm"], resultado["duplicados_spei"]]   # para el Ingeniero 3
rechazados = [resultado["rechazados_atm"], resultado["rechazados_spei"]]   # para el Ingeniero 3
```

- `main.py` debe estar en la raíz del proyecto y ejecutarse desde ahí, para que funcione `from src.limpieza import ...`.
- **Importar el módulo no ejecuta nada:** no lee datos, no escribe archivos y no carga matplotlib. Todo lo que genera archivos está bajo `if __name__ == "__main__":` o en funciones que hay que llamar de forma explícita.

## Qué escribe cada función

| Función | ¿Escribe en disco? | Archivos |
|---|---|---|
| `cargar_datos()` | Nunca (solo lee) | — |
| `limpiar(..., guardar=True)` **(valor por defecto)** | Sí: **19 CSV**, sobrescribiendo los existentes | 6 en `data/processed/` y 13 en `reportes/` (ver la tabla siguiente) |
| `limpiar(..., guardar=False)` | **No escribe nada** | Devuelve los mismos 14 DataFrames solo en memoria |
| `generar_reporte_perfil(datasets)` | Siempre | 3 CSV de perfilado en `reportes/` |
| `graficar_boxplots(resultado)` | Siempre | 3 PNG en `reportes/` |
| `py -3.14 -m src.limpieza` | Sí | Las tres funciones anteriores: 25 archivos |

`guardar=True` se mantiene como valor por defecto para la ejecución individual del módulo. Los resultados en memoria son idénticos con `guardar=True` y con `guardar=False`.

### Archivos generados

| Archivo | Genera | Contenido |
|---|---|---|
| `data/processed/atm_limpio.csv`, `spei_limpio.csv` | `limpiar` | **Archivos finales del Ingeniero 1**, iguales a `resultado["atm"]` y `resultado["spei"]` |
| `data/processed/atm_sin_duplicados.csv`, `spei_sin_duplicados.csv` | `limpiar` | Punto de control intermedio (Fase 1). No son entrega. |
| `data/processed/atm_imputado.csv`, `spei_imputado.csv` | `limpiar` | Punto de control intermedio (Fase 2). No son entrega. |
| `reportes/duplicados_atm.csv`, `duplicados_spei.csv`, `resumen_duplicados.csv` | `limpiar` | Duplicados |
| `reportes/medianas_imputacion.csv`, `resumen_imputacion.csv`, `analisis_faltantes.csv` | `limpiar` | Faltantes |
| `reportes/rechazados_atm.csv`, `rechazados_spei.csv`, `resumen_imposibles.csv` | `limpiar` | Montos imposibles |
| `reportes/limites_iqr.csv`, `outliers_recortados.csv` | `limpiar` | Outliers |
| `reportes/comparativo_calidad.csv`, `comparativo_calidad_tabla.csv` | `limpiar` | Comparativo por etapa |
| `reportes/resumen_calidad.csv`, `perfil_columnas.csv`, `perfil_montos.csv` | `generar_reporte_perfil` | Perfilado de los datos originales |
| `reportes/boxplot_atm_capping.png`, `boxplot_spei_mxn_capping.png`, `boxplot_spei_usd_capping.png` | `graficar_boxplots` | Gráficas antes y después del capping |

Si `main.py` solo llama `limpiar(..., guardar=False)`, ninguno de estos archivos se actualiza. Para regenerarlos se usa `py -3.14 -m src.limpieza`.

**Cuidado al leer los CSV:**

- Lean `beneficiary_clabe` con `dtype=str`; de lo contrario se pierde el cero inicial.
- En CSV, `timestamp` pierde la distinción entre texto y entero. En memoria se conservan los tipos originales.

## Firma de las funciones principales

```python
cargar_datos(raiz=RAIZ)
limpiar(atm, spei, guardar=True, dir_procesados=DIR_PROCESADOS, dir_reportes=DIR_REPORTES)
```

`RAIZ` es la carpeta del proyecto, `DIR_PROCESADOS` es `RAIZ/data/processed` y `DIR_REPORTES` es `RAIZ/reportes`.

| Parámetro de `limpiar` | Descripción |
|---|---|
| `atm` | DataFrame de `transacciones_atm.csv`, tal como lo devuelve `cargar_datos()` |
| `spei` | DataFrame de `transferencias_digitales.json`, tal como lo devuelve `cargar_datos()` |
| `guardar` | `True` (por defecto) escribe 19 CSV; `False` no escribe nada |
| `dir_procesados`, `dir_reportes` | Carpetas de salida cuando `guardar=True` |

**Comportamiento de `limpiar()`:**

- Los DataFrames de entrada no se modifican.
- El orden interno es: duplicados → imputación → montos imposibles → capping → comparativo.
- **Índice:** `limpiar()` acepta DataFrames con índice desplazado o no consecutivo. Los DataFrames `atm` y `spei` devueltos usan como índice la **posición de entrada** de cada fila (0 = primera). Los identificadores de transacción no cambian.

### Claves del diccionario devuelto

Son 14 claves y todas son `pandas.DataFrame`:

| Clave | Contenido |
|---|---|
| `atm` | **Salida final ATM:** registros válidos, sin duplicados, imputados y recortados |
| `spei` | **Salida final SPEI:** el mismo tratamiento |
| `duplicados_atm`, `duplicados_spei` | Filas retiradas por duplicado, con trazabilidad |
| `resumen_duplicados` | Registros antes y después de quitar duplicados |
| `medianas_imputacion` | Mediana usada, montos válidos e imputados por `tipo_operacion` |
| `resumen_imputacion` | Faltantes antes y después por columna, y método usado |
| `analisis_faltantes` | Proporción de faltantes por categoría de otras variables (análisis MCAR/MAR) |
| `rechazados_atm`, `rechazados_spei` | Registros con monto imposible, completos y con su motivo |
| `resumen_imposibles` | Rechazos por fuente y motivo (incluye los motivos con 0) |
| `limites_iqr` | Q1, Q3, IQR, límites y outliers por fuente y grupo |
| `outliers_recortados` | Valor original y recortado de cada outlier, con sus límites |
| `comparativo_calidad` | Indicadores por etapa: `original`, `sin_duplicados` y `final` |

### Identificadores de fuente

- Todos los reportes estructurados usan la columna `fuente`, con los valores `atm`, `spei` o `catalogo` (este último solo en el perfilado).
- Las tablas por registro agregan `archivo_origen` con el nombre del archivo: `duplicados_*`, `rechazados_*` y `outliers_recortados`.
- Las constantes son `FUENTE_ATM`, `FUENTE_SPEI` y `ARCHIVOS_ORIGEN`.

### Columnas adicionales generadas

**ATM** (`resultado["atm"]`). Las columnas originales se conservan con el mismo nombre y orden. Se agregan al final:

| Columna | Tipo | Significado |
|---|---|---|
| `monto_mxn_original` | float | Valor de la fuente antes de imputar y recortar (nulo en los imputados) |
| `monto_imputado` | bool | `monto_mxn` se rellenó con la mediana de su tipo |
| `cajero_imputado` | bool | `id_cajero` era nulo y ahora vale `CAJERO_DESCONOCIDO` |
| `monto_recortado` | bool | `monto_mxn` se recortó con IQR |

**SPEI** (`resultado["spei"]`):

| Columna | Tipo | Significado |
|---|---|---|
| `notification_email_original` | texto | Correo tal como se recibió, con sus espacios |
| `email_motivo` | texto | VALIDO, NULO, VACIO o INVALIDO, evaluado después de `strip()` |
| `email_corregido` | bool | El correo se sustituyó por `SIN_CORREO` |
| `email_normalizado` | bool | El correo es válido y se le quitaron espacios al principio o al final |
| `amount_original` | float | Monto antes del capping |
| `amount_recortado` | bool | `amount` se recortó con IQR |

**Columnas de trazabilidad de duplicados** (`duplicados_atm` y `duplicados_spei`), al inicio de cada fila:

| Columna | Significado |
|---|---|
| `fuente`, `archivo_origen` | `atm` / `transacciones_atm.csv`, o `spei` / `transferencias_digitales.json` |
| `indice_original` | Posición (0 = primera fila) en el DataFrame que recibió `limpiar()` |
| `etiqueta_indice` | Etiqueta del índice de pandas de esa fila |
| `duplicado_de` | Posición de la fila que se conservó |

**Columnas de trazabilidad de rechazados** (`rechazados_atm` y `rechazados_spei`): `fuente`, `archivo_origen`, `indice_original`, `etiqueta_indice`, `id_registro`, `columna_validada`, `valor_original` y `motivo_rechazo`, seguidas de todas las columnas del registro.

Con `cargar_datos()`, `indice_original` y `etiqueta_indice` coinciden. Difieren si alguien pasa un DataFrame filtrado, concatenado o con otro índice: `indice_original` sigue siendo la posición y `etiqueta_indice` conserva la etiqueta.

## Funciones disponibles

| Sección | Función | Descripción |
|---|---|---|
| Carga | `cargar_datos(raiz=RAIZ)` | Lee los 3 originales. Los IDs y la CLABE se leen como texto. El `timestamp` de SPEI conserva sus tipos mezclados. |
| Perfilado | `perfilar_columnas(df, nombre)` | Por columna: tipo, nulos, vacíos, únicos y tipos de Python mezclados |
| | `resumir_dataset(df, nombre)` | Registros, columnas, duplicados exactos y de ID, celdas nulas |
| | `perfilar_montos(df, columna, agrupar_por)` | Estadísticas de montos por grupo y en total |
| | `generar_reporte_perfil(datasets, dir_reportes)` | Escribe los 3 CSV de perfilado |
| Duplicados | `separar_duplicados(df)` | Devuelve `(sin_duplicados, duplicados)`, con `indice_original`, `etiqueta_indice` y `duplicado_de` |
| Faltantes | `clasificar_email(serie)` | VALIDO, NULO, VACIO o INVALIDO, después de `strip()` |
| | `tasas_faltantes(df, es_faltante, variables, nombre, columna)` | Proporción de faltantes por categoría |
| | `imputar_montos(atm)` | Mediana por `tipo_operacion`. Devuelve `(atm, medianas)` |
| | `imputar_cajeros(atm)` | Sustituye nulos por `CAJERO_DESCONOCIDO` |
| | `corregir_emails(spei)` | Normaliza los válidos, sustituye el resto por `SIN_CORREO` y conserva el original |
| | `imputar(atm, spei)` | Orquesta las tres funciones anteriores y el análisis de faltantes |
| Imposibles | `motivo_monto_imposible(valores, permite_cero)` | Motivo de rechazo por valor, o `<NA>` si es válido |
| | `separar_imposibles_atm(atm)` / `separar_imposibles_spei(spei)` | Devuelven `(validos, rechazados)` |
| | `separar_imposibles(atm, spei)` | Ambas fuentes más el resumen por motivo |
| Outliers | `capping_iqr(df, columna, columna_original, bandera, grupo, excluir, excluir_calculo, factor)` | Capping IQR reutilizable. Devuelve `(df, limites)` |
| | `aplicar_capping(atm, spei)` | Reglas de cada fuente: ATM sin consultas, SPEI por moneda |
| Reportes | `comparar_calidad(atm, spei, resultado)` | Comparativo de calidad por etapa |
| | `tabla_comparativa(comparativo)` | Tabla de indicadores por etapa |
| | `graficar_boxplots(resultado, dir_reportes)` | 3 boxplots PNG, antes y después del capping |
| Principal | `limpiar(atm, spei, ...)` | Pipeline completo del Ingeniero 1 |

Constantes públicas: `CAJERO_DESCONOCIDO`, `SIN_CORREO`, `PATRON_EMAIL`, `TIPO_CONSULTA`, `MOTIVOS_RECHAZO`, `FACTOR_IQR`, `FUENTE_ATM`, `FUENTE_SPEI` y `ARCHIVOS_ORIGEN`.

## Contrato con el Ingeniero 2 (homologación e integración)

**Qué recibe:** `resultado["atm"]` y `resultado["spei"]` (600 registros cada uno) en memoria, o los archivos finales `data/processed/atm_limpio.csv` y `spei_limpio.csv`.

**Qué entrega este módulo:**

- No hay duplicados exactos y `id_transaccion` es único.
- `monto_mxn`, `id_cajero` y `notification_email` no tienen faltantes.
- Todos los montos son válidos y ya tienen capping.
- Los valores imputados, normalizados y recortados están marcados con banderas.

**Pendientes del Ingeniero 2, detectados pero no tratados aquí** (cifras sobre la salida final):

| Pendiente | Detalle |
|---|---|
| Fechas ATM | 3 formatos: `YYYY/MM/DD HH:MM:SS` (211), `YYYY-MM-DD HH:MM:SS` (196) y `DD/MM/YYYY HH:MM` (193). El tercero es día/mes: comprobado porque el primer campo llega a ser mayor que 12 y el segundo nunca. |
| Timestamp SPEI | 405 cadenas ISO con `-06:00` y 195 enteros epoch (UTC) |
| Categorías | En `resultado`, `EXITOSA` (298) convive con `exitosa` (81). En `status`, `LIQUIDADA` (115) convive con `liquidada` (121). |
| Moneda | 134 transferencias en USD. El capping se hizo **antes** de la conversión, por moneda. No se debe recalcular después de convertir. |
| Cuentas fuera del catálogo | 32 registros en ATM y 29 en SPEI |

## Contrato con el Ingeniero 3 (validación, cuarentena y persistencia)

**Registros que recibe:**

| Qué | Dónde | Registros actuales |
|---|---|---|
| Rechazados por monto imposible | `resultado["rechazados_atm"]`, `resultado["rechazados_spei"]` | 0 y 0 |
| Duplicados exactos | `resultado["duplicados_atm"]`, `resultado["duplicados_spei"]` | 20 y 0 |

- Las columnas de trazabilidad están descritas en [Columnas adicionales generadas](#columnas-adicionales-generadas).
- Las tablas vacías conservan todas sus columnas, así que la cuarentena debe aceptar una tabla con 0 filas.
- **Persistencia:** con `guardar=False`, este módulo no escribe nada; el Ingeniero 3 decide qué se persiste y dónde.
- **Hallazgos para validación**, no tratados por este módulo: ver las decisiones 3 y 4 de la sección siguiente.

## Decisiones pendientes del equipo

El módulo **no implementa** ninguna de estas decisiones. Solo las documenta para acordarlas:

| # | Decisión | Situación actual | Quién la toma |
|---|---|---|---|
| 1 | ¿Usar `amount` o `amount_original` para los totales y la conversión de moneda? | `amount` está recortado en 18 transferencias (por ejemplo, 5,240,100 queda en 50,667.57). `amount_original` conserva el valor de la fuente. | Equipo (Ingenieros 2 y 3) |
| 2 | ¿Los duplicados van a cuarentena o solo a una bitácora? | Los 20 duplicados de ATM se entregan completos y con trazabilidad. | Ingeniero 3 |
| 3 | ¿Cómo se tratan las CLABE inválidas? | 46 transferencias tienen `beneficiary_clabe = "12345"` (5 dígitos en lugar de 18). No se modifican. | Ingeniero 3 (validación) |
| 4 | ¿Cómo se tratan las comisiones incoherentes? | Los valores se conservan. Hay comisión en 70 de 97 consultas, en 92 de 117 operaciones con FALLO_CONEXION y en 67 de 104 con FALLO_FONDOS (ver [REGLAS_DE_NEGOCIO.md](REGLAS_DE_NEGOCIO.md#7-comisiones-sin-cambios)). | Equipo |

## Cambios del contrato en esta versión

Estos cambios afectan a quien ya hubiera leído reportes de una versión anterior:

1. En todos los reportes, la columna **`fuente`** usa `atm`, `spei` o `catalogo`, en lugar de nombres de archivo. Las columnas `dataset` se renombraron a `fuente`. El nombre del archivo pasó a la nueva columna `archivo_origen` en las tablas por registro.
2. **`indice_original`** es siempre una posición de entrada. Se agregó **`etiqueta_indice`** en duplicados y rechazados.
3. Los duplicados ahora incluyen `fuente` y `archivo_origen`.
4. `medianas_imputacion` incluye la columna `fuente`.
5. SPEI tiene una nueva columna, **`email_normalizado`**, y los correos válidos con espacios al principio o al final se conservan sin ellos.
6. `analisis_faltantes` agrega las variables `formato_fecha_hora` (ATM) y `tipo_timestamp` (SPEI).

Las 14 claves de `limpiar()`, su firma y todas las cifras de perfilado, imputación y capping son las mismas.
