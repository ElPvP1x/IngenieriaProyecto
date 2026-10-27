# Etapa 1 – Transformación (Ingeniero 2): fechas, timestamps y estados

Uso:

```python
from src.limpieza import cargar_datos, limpiar
from src.transformacion import etapa1_fechas_y_categorias
d = cargar_datos()
limpio = limpiar(d["atm"], d["spei"], guardar=False)
e = etapa1_fechas_y_categorias(limpio["atm"], limpio["spei"])
```

Devuelve `atm`, `spei`, `resumen_etapa1`, `estados_fuera_catalogo`.

| Tabla | Columna | Cambio | Columnas nuevas |
|---|---|---|---|
| ATM | `fecha_hora` | texto -> fecha UTC-6 | `fecha_hora_original`, `formato_fecha_atm`, `fecha_invalida` |
| ATM | `resultado` | mayúsculas | `resultado_original`, `resultado_normalizado` |
| SPEI | `timestamp` | ISO/epoch -> fecha UTC-6 | `timestamp_original`, `formato_timestamp`, `timestamp_invalido` |
| SPEI | `status` | mayúsculas | `status_original`, `status_normalizado` |

Decisiones por confirmar: zona UTC-6 (ATM sin zona se asume hora local), mismo nombre de columna con tipo nuevo (acuerdo E), inválidos quedan nulos con bandera, estados nuevos solo se reportan.

---

## Etapa 2 – Moneda (`etapa2_moneda`)

Tasa USD/MXN = 19.50 (constante `TASA_USD_MXN`, cambiable). Se convierten los dos montos: `amount_mxn` (desde `amount`) y `amount_original_mxn` (desde `amount_original`). Columnas nuevas: `tasa_aplicada`, `moneda_desconocida`. No se recalcula el capping. Resultado: 466 MXN sin cambio, 134 USD convertidas, 0 monedas desconocidas.

## Etapa 3 – Catálogo (`etapa3_catalogo`)

Reglas del equipo: cuenta huérfana -> se separa; cuenta BLOQUEADA o SUSPENDIDA -> se rechaza (por ahora). Nada se borra: van a tablas aparte con `motivo_separacion`.

| Fuente | Entran | Huérfanas | Bloqueadas | Suspendidas | Aceptadas |
|---|---|---|---|---|---|
| ATM | 600 | 32 | 117 | 97 | 354 |
| SPEI | 600 | 29 | 98 | 115 | 358 |

Las aceptadas traen `cuenta_en_catalogo`, `tipo_cuenta` y `estado_cuenta_catalogo`.

---

## Etapa 5 – Integración (`etapa5_integrar`)

Junta las aceptadas de ATM (354) y SPEI (358) en **una tabla de 712 filas**, ordenada por fecha, con `fuente` (`ATM` o `SPEI`).

Columnas comunes: `id_operacion`, `fuente`, `fecha_hora`, `id_cuenta`, `tipo_operacion` (en SPEI: `TRANSFERENCIA_SPEI`), `monto_mxn` (en SPEI viene de `amount_mxn`), `monto_original_mxn`, `moneda_origen`, `resultado` (en SPEI viene de `status`), `canal` (`id_cajero` o `platform`), `comision_mxn` (solo ATM), `tipo_cuenta`, `estado_cuenta_catalogo`, `monto_imputado`, `monto_recortado`.
Columnas propias (vacías en la otra fuente): `id_cajero`, `cajero_imputado`, `beneficiary_clabe`, `notification_email`, `email_corregido`, `concept`.

## Etapa 4 – Min-Max y discretización (`etapa4_minmax_discretizacion`)

Se aplica sobre la tabla integrada. Las columnas y cortes son **propuesta mía** (la guía del profesor no se tenía) y se pueden cambiar.

- `monto_mxn_minmax`: (x − mín) / (máx − mín), entre 0 y 1, **por fuente** (ATM y SPEI tienen escalas muy distintas). Los mínimos y máximos usados salen en `parametros_minmax`. El monto sin escalar se conserva.
- `monto_mxn_categoria`: BAJO / MEDIO / ALTO / MUY_ALTO con los cuartiles de cada fuente (límites en `limites_discretizacion`). También acepta cortes fijos con `cortes_monto=[...]`.
- `franja_horaria`: MADRUGADA (0-5), MANANA (6-11), TARDE (12-17), NOCHE (18-23), con la hora local.

## Etapa 6 – Flujo completo (`ejecutar_transformacion`)

```python
from src.limpieza import cargar_datos, limpiar
from src.transformacion import ejecutar_transformacion
d = cargar_datos()
limpio = limpiar(d["atm"], d["spei"], guardar=False)
r = ejecutar_transformacion(limpio, d["catalogo"])
```

Devuelve: `integrada` (para el Ingeniero 3), `huerfanas`, `rechazadas_cuenta`, `parametros_minmax`, `limites_discretizacion`, `estados_fuera_catalogo`, `resumen`. No escribe archivos.

Cuentas: 1,200 filas entran = 712 integradas + 61 huérfanas + 427 rechazadas. No se pierde ninguna.

## Pendiente con el Ingeniero 3

- Acuerdo de columnas y tipos de la tabla integrada (acuerdo E): `fecha_hora` es fecha con zona, no texto.
- Qué hace con `huerfanas` y `rechazadas_cuenta` en su cuarentena.
- Aún no existe una prueba de integración con su módulo.
