# Reglas de negocio: Calidad, Nulos y Outliers (Ingeniero/a 1)

Este documento describe las reglas que aplica el módulo `src/limpieza.py`. Cada decisión se justifica con los resultados obtenidos sobre los datos reales del proyecto.

**Alcance.** Solo se incluyen las reglas del rol de Calidad, Nulos y Outliers:

- La homologación de fechas, monedas y categorías y la integración con el catálogo corresponden al Ingeniero 2.
- La validación, la cuarentena y la persistencia corresponden al Ingeniero 3.

**Fuentes.**

| Archivo | Registros originales |
|---|---|
| `transacciones_atm.csv` | 620 |
| `transferencias_digitales.json` | 600 |
| `catalogo_cuentas.csv` | 50 (solo se perfila) |

Los archivos originales nunca se modifican.

**Orden de aplicación.** Las reglas se aplican en este orden dentro de `limpiar(atm, spei)`:

1. Separación de duplicados exactos.
2. Imputación de faltantes.
3. Separación de montos imposibles.
4. Capping mediante IQR.

---

## 1. Duplicados

**Regla.** Un registro es duplicado cuando coincide exactamente en **todas** sus columnas con otro registro. Se conserva la primera aparición y las repeticiones se separan, sin borrarlas.

**Resultados.**

| Fuente | Registros originales | Duplicados exactos | Registros sin duplicados |
|---|---|---|---|
| ATM | 620 | 20 (3.23 %) | 600 |
| SPEI | 600 | 0 | 600 |

- Los 20 duplicados de ATM ocupan las posiciones 600 a 619 del archivo. Cada uno repite una transacción anterior; por ejemplo, la posición 600 repite la 454 (`ATM-TX-00455`).
- Al quitarlos, `id_transaccion` queda único. No había IDs repetidos con contenido distinto.

**Trazabilidad.** El archivo `reportes/duplicados_atm.csv` conserva cada fila retirada con estas columnas:

- `fuente` (`atm`) y `archivo_origen` (`transacciones_atm.csv`).
- `indice_original`: la **posición** de la fila en la entrada de `limpiar()` (0 = primera fila). Con `cargar_datos()` coincide con la posición en el archivo.
- `etiqueta_indice`: la etiqueta del índice de pandas de esa fila. Es distinta de la posición si el DataFrame recibido tenía un índice desplazado o no consecutivo.
- `duplicado_de`: la posición de la fila que se conservó.

Los identificadores de transacción no se modifican.

**Consecuencia.** Todos los conteos posteriores se calculan sobre los datos **sin duplicados**. Por eso `id_cajero` muestra 44 nulos en los datos originales y 42 después de deduplicar.

## 2. Valores faltantes

**Regla.** Se distinguen tres tipos de faltante:

- **Nulo real:** celda vacía o `null`.
- **Vacío:** cadena vacía o con solo espacios.
- **Inválido:** el valor existe, pero no cumple el formato esperado.

| Columna | Faltantes (sin duplicados) | Tipo |
|---|---|---|
| `monto_mxn` (ATM) | 27 (4.5 %) | nulos reales |
| `id_cajero` (ATM) | 42 (7.0 %) | nulos reales |
| `notification_email` (SPEI) | 46 (7.67 %) | 8 nulos, 8 vacíos, 30 inválidos |
| `comision_aplicada` (ATM) | 0 | sin faltantes |

## 3. Clasificación tentativa MCAR / MAR

Comparamos la proporción de faltantes entre las categorías de otras variables observadas. Es un análisis **descriptivo**: no se aplica ninguna prueba estadística. Todas las cifras se reproducen con `imputar()` y están en `reportes/analisis_faltantes.csv`.

Para el análisis se derivan dos variables, que solo existen en ese reporte y no se agregan a los datos:

- `formato_fecha_hora`: el patrón de `fecha_hora` con cada dígito como `9`.
- `tipo_timestamp`: el tipo de Python del `timestamp`.

**Advertencia.** Las clasificaciones de abajo son **hipótesis tentativas, no demostradas**. Una diferencia de proporciones no prueba un mecanismo, y algunos grupos son pequeños. Además, un mecanismo **MNAR** (que el valor falte por causa del propio valor no observado) **no puede descartarse** usando solo los datos observados.

| Columna | Evidencia descriptiva (datos sin duplicados) | Hipótesis tentativa |
|---|---|---|
| `monto_mxn` | 0 nulos en CONSULTA_SALDO (0/97), frente a DEPOSITO 5.51 % (7/127), PAGO_SERVICIOS 3.51 % (4/114) y RETIRO 6.11 % (16/262). Por formato de fecha varía poco: 3.79 %, 4.66 % y 5.10 %. | **MAR** respecto a `tipo_operacion` (solo faltan montos en operaciones que mueven dinero) y **compatible con MCAR** dentro de esas operaciones |
| `id_cajero` | Por tipo de operación, entre 5.34 % y 10.31 %. Por formato de fecha la diferencia es mayor: `DD/MM/YYYY HH:MM` 11.40 % (22/193), `YYYY-MM-DD HH:MM:SS` 7.14 % (14/196) y `YYYY/MM/DD HH:MM:SS` 2.84 % (6/211). | **MAR**, posiblemente ligado al sistema de origen que representa cada formato |
| `notification_email` | Con `timestamp` entero (epoch) 12.82 % (25/195), frente a 5.19 % (21/405) con texto ISO. Por moneda: USD 11.19 % y MXN 6.65 %. Por plataforma, entre 6.00 % y 10.79 %. | **MAR**, posiblemente ligado al canal o sistema de origen |

**Implicación.** Si el mecanismo es MAR, eliminar los registros con faltantes sesgaría la muestra hacia ciertos sistemas de origen. Por eso se imputa y se marca con una bandera, en lugar de eliminar. Esta decisión no depende de que el mecanismo esté demostrado.

## 4. Imputación de `monto_mxn` por mediana

**Regla.** Cada monto nulo se rellena con la **mediana de su `tipo_operacion`**. La mediana se calcula solo con montos válidos: sin nulos, sin valores no numéricos y sin valores imposibles.

| tipo_operacion | Mediana usada | Montos válidos | Imputados |
|---|---|---|---|
| CONSULTA_SALDO | 0 | 97 | 0 |
| DEPOSITO | 4,000 | 120 | 7 |
| PAGO_SERVICIOS | 4,000 | 110 | 4 |
| RETIRO | 2,000 | 246 | 16 |

**Por qué la mediana y no la media.** El retiro de $3,500,000 eleva la media de RETIRO a 17,075, mientras que la mediana es 2,000 y no cambia aunque se excluya ese valor.

**Por qué agrupar por tipo.** Es la variable con la que se asocian los faltantes, según la sección 3. Además, cada tipo de operación tiene una escala de montos distinta.

**Reglas complementarias:**

- La bandera `monto_imputado` marca los 27 registros imputados. **Un valor imputado no es una observación**: en los reportes se cuentan por separado los 573 montos observados y los 27 imputados.
- `monto_mxn_original` conserva el valor de la fuente, que es nulo en los imputados.
- Si un grupo no tiene montos válidos, o si `tipo_operacion` es nulo, no se inventa una mediana. El monto queda nulo y se rechaza después como `FALTANTE` (sección 8). En los datos reales esto no ocurre.

**Otras columnas:**

- `id_cajero`: los 42 nulos se sustituyen por `CAJERO_DESCONOCIDO` y se marcan con `cajero_imputado`. No se intenta adivinar el cajero.
- `comision_aplicada`: no se imputa. No tiene nulos reales, y sus 164 ceros se consideran valores válidos.

## 5. Corrección de correos

**Regla.**

1. Primero se eliminan los espacios al principio y al final con `strip()`.
2. Después se valida el patrón `texto@dominio.ext`: sin espacios internos y con una sola `@`.
3. Los correos nulos, vacíos (incluidos los que solo tienen espacios) o inválidos se sustituyen por `SIN_CORREO`.
4. Un correo que es válido después del `strip()` se conserva **normalizado**, es decir, sin esos espacios.

**Resultados.**

| Clasificación | Registros |
|---|---|
| Válido | 554 (92.33 %) |
| Nulo | 8 |
| Vacío | 8 |
| Inválido: `correo@@sinformato` | 19 |
| Inválido: `invalido.com` | 11 |

En total se corrigieron **46** correos.

**Trazabilidad.**

- `notification_email_original` conserva el valor recibido, con sus espacios.
- `email_motivo` guarda la clasificación.
- `email_corregido` marca los registros sustituidos por `SIN_CORREO`.
- `email_normalizado` marca los correos válidos que cambiaron solo por el `strip()`.

Solo se eliminan los espacios al principio y al final; no se cambian mayúsculas ni ningún otro carácter. En los datos reales ningún correo tiene esos espacios: `email_normalizado` vale 0 y las cifras de esta sección no cambian.

## 6. Conservación de las consultas de saldo

**Regla.** CONSULTA_SALDO es la única operación ATM que puede tener `monto_mxn = 0`. Ese cero es un valor válido, no un faltante.

**Resultados:**

- Hay 101 consultas en el archivo original y 97 después de quitar duplicados (4 eran duplicadas).
- Las 97 conservan su monto 0.
- Ninguna se imputa, se rechaza ni se recorta.
- Las consultas se excluyen del cálculo IQR. Si se incluyeran, sus ceros bajarían artificialmente los cuartiles.

## 7. Comisiones: sin cambios

**Regla.** `comision_aplicada` se conserva tal como viene, porque el proyecto no define una regla de negocio para las comisiones. Solo se documentan las combinaciones que parecen incoherentes. Datos sin duplicados:

- 70 de las 97 consultas de saldo tienen una comisión mayor que 0.
- Tienen comisión mayor que 0 92 de las 117 operaciones con FALLO_CONEXION y 67 de las 104 con FALLO_FONDOS.
- El valor 34.8 aparece en 152 registros. Equivale a 30 más 16 % de IVA, pero no se puede confirmar.

## 8. Montos imposibles

**Regla.** Un monto es imposible cuando cumple alguno de estos motivos. Se asigna el primero que aplique, en este orden de prioridad:

| Motivo | ATM (`monto_mxn`) | SPEI (`amount`) |
|---|---|---|
| `FALTANTE` | nulo que no pudo imputarse | nulo |
| `NO_NUMERICO` | no se puede interpretar como número finito (incluye `inf`) | igual que en ATM |
| `NEGATIVO` | menor que 0 | menor que 0 |
| `CERO_NO_PERMITIDO` | igual a 0 en RETIRO, DEPOSITO o PAGO_SERVICIOS | igual a 0 |

**Para que la imputación no oculte inválidos:**

- En ATM se valida `monto_mxn_original`, es decir, el valor de la fuente.
- La imputación solo rellena nulos reales. Un texto como `"abc"` no se convierte en nulo para luego imputarse: se rechaza como `NO_NUMERICO`.
- El capping se aplica únicamente a los registros válidos.

**Resultados.** En los datos reales hay **0 montos imposibles**, tanto en ATM como en SPEI. Las reglas están verificadas con pruebas automatizadas sobre datos sintéticos.

## 9. Capping mediante IQR

**Regla.**

- Q1 y Q3 son los percentiles 25 y 75, e IQR = Q3 − Q1.
- Límite inferior = max(Q1 − 1.5 × IQR, 0).
- Límite superior = Q3 + 1.5 × IQR.
- Los valores fuera de los límites se recortan al límite más cercano (clip). No se eliminan.

**Cómo se aplica en cada fuente:**

- **ATM:** se usa `monto_mxn` y se excluye CONSULTA_SALDO. Los 27 montos imputados no entran en el cálculo de los cuartiles, aunque sí se recortarían si quedaran fuera. Si entraran, el límite superior sería 16,000 en lugar de 16,750, porque la mediana repetida comprime el IQR.
- **SPEI:** se usa `amount`, con límites calculados **por separado para MXN y USD** y **sin convertir monedas**.

**Resultados.**

| Fuente | Grupo | n cálculo | Q1 | Q3 | IQR | Límite inf. | Límite sup. | Outliers |
|---|---|---|---|---|---|---|---|---|
| ATM | sin consultas | 476 | 500.00 | 7,000.00 | 6,500.00 | 0 | **16,750.00** | 1 (0.20 % de 503) |
| SPEI | MXN | 466 | 10,000.96 | 26,267.60 | 16,266.65 | 0 | 50,667.57 | 15 (3.22 %) |
| SPEI | USD | 134 | 11,413.89 | 26,838.64 | 15,424.75 | 0 | 49,975.76 | 3 (2.24 %) |

- El límite de ATM, 16,750, coincide con el de la guía del proyecto.
- El único outlier de ATM es `ATM-TX-00416`, un retiro de $3,500,000 que se recorta a 16,750.
- En SPEI se recortan las **18** transferencias detectadas en la exploración inicial:
  - 15 en MXN, entre 199,902 y 5,240,100.
  - 3 en USD: 707,211, 2,893,386 y 4,141,401.
- Todos los outliers son superiores. El detalle está en `reportes/outliers_recortados.csv` y las gráficas en `reportes/boxplot_*_capping.png`.

**Limitaciones que el equipo debe considerar:**

1. **El capping cambia mucho los totales de SPEI.** Por ejemplo, 5,240,100 pasa a 50,667.57. Si alguna de esas transferencias fuera legítima, como un pago empresarial, el monto recortado la subestima. Para analizarla, se debe usar `amount_original`.
2. **El valor 16,750 no es una denominación real** de cajero automático. Es una consecuencia esperada del método.
3. **Con IQR = 0** (todos los valores del grupo iguales), ambos límites coinciden y cualquier valor distinto se recorta. Esto no ocurre en los datos reales.

## 10. Conservación de valores originales

Ninguna regla sobrescribe el valor de la fuente sin dejar una copia:

| Columna tratada | Copia del valor original | Bandera |
|---|---|---|
| `monto_mxn` | `monto_mxn_original` (creada al imputar; el capping no la sobrescribe) | `monto_imputado`, `monto_recortado` |
| `id_cajero` | el valor original siempre es nulo cuando se imputa | `cajero_imputado` |
| `notification_email` | `notification_email_original` | `email_corregido`, `email_normalizado`, `email_motivo` |
| `amount` | `amount_original` (creada antes del capping) | `amount_recortado` |

Las columnas que no corresponden a este rol no se modifican: fechas, monedas, categorías, CLABE y comisiones. Hay una prueba automatizada que lo verifica.

## 11. Trazabilidad y separación de registros rechazados

Cada registro de salida se puede ubicar en uno de estos estados:

| Estado | Cómo se identifica |
|---|---|
| Valor observado | Todas las banderas en `False` (531 de 600 en ATM y 536 de 600 en SPEI) |
| Valor imputado | `monto_imputado`, `cajero_imputado` o `email_corregido` en `True` |
| Correo normalizado | `email_normalizado` en `True` (válido tras quitar espacios) |
| Valor recortado | `monto_recortado` o `amount_recortado` en `True`, con el valor previo en `*_original` |
| Duplicado separado | `reportes/duplicados_*.csv`, con `indice_original`, `etiqueta_indice` y `duplicado_de` |
| Rechazado por imposible | `reportes/rechazados_*.csv`, con `motivo_rechazo` |

**Los registros rechazados no se eliminan.** Se entregan completos, es decir, con todas sus columnas y banderas, junto con estos campos:

- `fuente` (`atm` o `spei`) y `archivo_origen` (el nombre del archivo).
- `indice_original`: la posición en la entrada de `limpiar()`.
- `etiqueta_indice`: la etiqueta del índice de pandas.
- `id_registro`
- `columna_validada`
- `valor_original`
- `motivo_rechazo`

Así el Ingeniero 3 puede enviarlos a cuarentena. Este módulo no persiste la cuarentena.

**Identificadores de fuente.** Todos los reportes estructurados identifican la fuente con `fuente` = `atm`, `spei` o `catalogo`. Las tablas por registro (duplicados, rechazados y outliers recortados) agregan además `archivo_origen`.

## 12. Decisiones pendientes del equipo

Este módulo no implementa las decisiones siguientes; solo las deja documentadas:

1. Si se usa `amount` (recortado) o `amount_original` para los totales y la conversión de moneda.
2. Si los duplicados van a cuarentena o solo a una bitácora.
3. El tratamiento de las 46 CLABE inválidas (`12345`).
4. El tratamiento de las comisiones incoherentes (sección 7).

El detalle está en [DOCUMENTACION_LIMPIEZA.md](DOCUMENTACION_LIMPIEZA.md#decisiones-pendientes-del-equipo).
