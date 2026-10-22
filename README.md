# Ingeniería de Datos — Pipeline ETL Financiero

Proyecto académico de Ingeniería de Datos enfocado en el desarrollo de un **pipeline ETL robusto con zona de cuarentena**, utilizando información de operaciones bancarias realizadas mediante cajeros automáticos (ATM) y transferencias digitales (SPEI).

El proyecto tiene como objetivo implementar un proceso de extracción, transformación, validación y almacenamiento de datos, aplicando técnicas de calidad de datos, tratamiento de valores faltantes, detección de anomalías e integración de distintas fuentes.

## Objetivo del proyecto

Desarrollar un pipeline ETL que permita:

- Integrar información procedente de distintas fuentes financieras.
- Identificar y tratar registros duplicados.
- Analizar e imputar valores faltantes.
- Detectar valores imposibles y valores atípicos.
- Estandarizar formatos, monedas y categorías.
- Validar registros mediante reglas de negocio.
- Separar información inválida en una zona de cuarentena.
- Almacenar los datos procesados en una base de datos.
- Generar reportes de calidad y evidencias del procesamiento.

## Tecnologías utilizadas

| Tecnología | Propósito |
|---|---|
| Python | Desarrollo del pipeline |
| Pandas | Procesamiento y limpieza de datos |
| NumPy | Operaciones numéricas |
| Matplotlib | Visualización y análisis de outliers |
| Unittest | Pruebas automatizadas |
| Git y GitHub | Control de versiones y colaboración |

Las tecnologías adicionales para validación y persistencia se incorporarán durante las siguientes etapas del proyecto.

## Fuentes de datos

El proyecto utiliza tres archivos originales:

| Archivo | Descripción | Registros |
|---|---|---:|
| `catalogo_cuentas.csv` | Catálogo de cuentas bancarias | 50 |
| `transacciones_atm.csv` | Operaciones realizadas en cajeros automáticos | 620 |
| `transferencias_digitales.json` | Transferencias digitales SPEI | 600 |

Los archivos originales se conservan sin modificaciones para garantizar la trazabilidad del procesamiento.

## Estructura del proyecto

```text
IngenieriaProyecto/
├── catalogo_cuentas.csv
├── transacciones_atm.csv
├── transferencias_digitales.json
│
├── src/
│   ├── __init__.py
│   └── limpieza.py
│
├── tests/
│   └── test_limpieza.py
│
├── data/
│   └── processed/
│       ├── atm_sin_duplicados.csv
│       ├── atm_imputado.csv
│       ├── atm_limpio.csv
│       ├── spei_sin_duplicados.csv
│       ├── spei_imputado.csv
│       └── spei_limpio.csv
│
├── reportes/
│   ├── comparativo_calidad.csv
│   ├── limites_iqr.csv
│   ├── outliers_recortados.csv
│   ├── boxplot_atm_capping.png
│   ├── boxplot_spei_mxn_capping.png
│   └── boxplot_spei_usd_capping.png
│
├── DOCUMENTACION_LIMPIEZA.md
├── REGLAS_DE_NEGOCIO.md
├── requirements.txt
├── .gitignore
└── README.md
```

La carpeta `reportes/` también contiene los perfiles de calidad, análisis de faltantes, registros duplicados y rechazados, y resúmenes de imputación.

La estructura se ampliará conforme se integren los módulos de los demás integrantes.

## Organización del equipo

El proyecto está dividido en tres responsabilidades principales.

### Ingeniero 1 — Calidad, Nulos y Outliers

Responsable del análisis inicial y la limpieza de los datasets.

**Actividades desarrolladas:**

- Perfilado de calidad de las tres fuentes.
- Identificación y separación de duplicados.
- Análisis de valores faltantes.
- Imputación mediante medianas y valores categóricos.
- Identificación de correos electrónicos inválidos.
- Detección de montos imposibles.
- Tratamiento de outliers mediante el método IQR.
- Generación de reportes y gráficas comparativas.
- Desarrollo de pruebas automatizadas.
- Documentación de las reglas de negocio.

**Estado:** Implementado y probado de manera independiente.

### Ingeniero 2 — Transformación e Integración

Responsable de homologar e integrar las fuentes de información.

**Actividades previstas:**

- Normalización de fechas y timestamps.
- Homologación de categorías y estados.
- Conversión de monedas USD a MXN.
- Integración de operaciones ATM y SPEI.
- Cruce con el catálogo de cuentas.
- Identificación de cuentas huérfanas.
- Normalización Min-Max y discretización de variables.

**Estado:** Pendiente de integración.

### Ingeniero 3 — Validación, Cuarentena y Persistencia

Responsable de validar los registros y almacenar los resultados del pipeline.

**Actividades previstas:**

- Validación de datos mediante Pydantic.
- Aplicación de reglas de negocio.
- Gestión de registros rechazados.
- Implementación de la zona de cuarentena.
- Persistencia de datos en una base de datos.
- Integración del flujo completo mediante `main.py`.
- Pruebas de integración y estrés.

**Estado:** Pendiente de integración.

## Resultados del módulo de calidad

El módulo del Ingeniero 1 fue ejecutado sobre los datasets originales.

| Indicador | Resultado |
|---|---:|
| Transacciones ATM originales | 620 |
| Duplicados ATM separados | 20 |
| Transacciones ATM sin duplicados | 600 |
| Transferencias SPEI | 600 |
| Montos ATM imputados | 27 |
| Identificadores de cajero imputados | 42 |
| Correos SPEI corregidos | 46 |
| Outliers ATM recortados | 1 |
| Outliers SPEI MXN recortados | 15 |
| Outliers SPEI USD recortados | 3 |
| Registros con montos imposibles detectados | 0 |
| Pruebas automatizadas aprobadas | 68 |

### Tratamiento de valores faltantes

- `monto_mxn`: imputación mediante la mediana agrupada por tipo de operación.
- `id_cajero`: sustitución por `CAJERO_DESCONOCIDO`.
- `notification_email`: sustitución de valores nulos, vacíos o inválidos por `SIN_CORREO`.
- `comision_aplicada`: conservación de los valores originales, incluidos los ceros válidos.

### Tratamiento de outliers

Se utilizó el rango intercuartílico (IQR) con un factor de 1.5.

Los límites superiores obtenidos fueron:

| Fuente | Límite superior |
|---|---:|
| ATM | $16,750 MXN |
| SPEI MXN | $50,667.57 MXN |
| SPEI USD | $49,975.76 USD |

Los valores originales se conservan en columnas adicionales para garantizar la trazabilidad. Los montos recortados se utilizan como valores procesados para análisis estadístico y no deben interpretarse automáticamente como importes bancarios corregidos.

## Instalación y ejecución

### 1. Clonar el repositorio

```bash
git clone https://github.com/ElPvP1x/IngenieriaProyecto.git
cd IngenieriaProyecto
```

### 2. Crear un entorno virtual

```powershell
py -3.14 -m venv .venv
```

Activarlo en PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

### 3. Instalar dependencias

```powershell
python -m pip install -r requirements.txt
```

### 4. Ejecutar el módulo de limpieza

```powershell
python -m src.limpieza
```

Este comando ejecuta el procesamiento y genera los archivos de salida, reportes y gráficas correspondientes.

### 5. Ejecutar las pruebas

```powershell
python -m unittest discover -s tests -v
```

La última validación reportó **68 pruebas aprobadas**.

## Uso del módulo desde Python

El módulo puede importarse directamente para integrarse con las siguientes etapas del pipeline.

```python
from src.limpieza import cargar_datos, limpiar

datos = cargar_datos()

resultado = limpiar(
    datos["atm"],
    datos["spei"],
    guardar=False
)

atm_limpio = resultado["atm"]
spei_limpio = resultado["spei"]

duplicados_atm = resultado["duplicados_atm"]
duplicados_spei = resultado["duplicados_spei"]

rechazados_atm = resultado["rechazados_atm"]
rechazados_spei = resultado["rechazados_spei"]
```

El parámetro `guardar=False` permite obtener los resultados en memoria sin escribir archivos automáticamente.

La función `limpiar()` devuelve 14 DataFrames que incluyen los datasets procesados, duplicados, rechazados, estadísticas de imputación, límites IQR y reportes comparativos.

Para consultar el contrato completo de integración, revisar `DOCUMENTACION_LIMPIEZA.md`.

## Reglas de negocio y trazabilidad

Durante el procesamiento se conservan los valores originales y se generan indicadores para identificar las transformaciones realizadas.

Entre las reglas principales se encuentran:

- Conservar las consultas de saldo con monto cero.
- Separar los duplicados sin perder su información.
- Identificar montos negativos, imposibles o no numéricos.
- Imputar valores faltantes mediante criterios documentados.
- Calcular outliers de SPEI por separado para MXN y USD.
- Conservar montos originales antes de aplicar capping.
- Mantener disponibles los registros rechazados para su posterior tratamiento en cuarentena.

Las reglas detalladas se encuentran en `REGLAS_DE_NEGOCIO.md`.

## Estado actual del proyecto

| Etapa | Estado |
|---|---|
| Perfilado y calidad inicial | Completada |
| Limpieza de duplicados | Completada |
| Análisis e imputación de faltantes | Completada |
| Tratamiento de outliers | Completada |
| Reportes y pruebas del Ingeniero 1 | Completada |
| Homologación e integración de fuentes | Pendiente |
| Validación y cuarentena | Pendiente |
| Persistencia en base de datos | Pendiente |
| Pruebas del pipeline completo | Pendiente |

El módulo del Ingeniero 1 está preparado para integrarse con los módulos restantes. La validación completa del pipeline se realizará cuando las tres partes estén conectadas.

## Documentación adicional

- **`DOCUMENTACION_LIMPIEZA.md`**: funciones, parámetros, resultados y contrato de integración.
- **`REGLAS_DE_NEGOCIO.md`**: reglas de limpieza, imputación, tratamiento de outliers y decisiones pendientes.
- **`reportes/`**: evidencias del análisis y resultados comparativos.
- **`tests/test_limpieza.py`**: pruebas automatizadas del módulo.

---

**Proyecto académico — Ingeniería de Datos**

Pipeline ETL Robusto con Zona de Cuarentena — Grupo 2: Fintech (ATM + SPEI).