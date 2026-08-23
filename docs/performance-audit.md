# Auditoría de rendimiento

Cada mañana el advisor decide once, capitán y cola de mercado. Esta automatización
comprueba **a posteriori** si esas decisiones (y el xPts que las alimenta) siguen
siendo mejores que no hacer nada, y si el pipeline no se ha degradado.

No sustituye el criterio humano: mide sesgo, ranking y puntos dejados en la mesa.

## Qué mide

| Capa | Pregunta | Datos |
|------|----------|--------|
| Calibración | ¿El xPts se acerca a los puntos reales? Sesgo y MAE, por tramo de titularidad | Snapshots `xpts` + `gw_points` (ya existían) |
| Ranking | ¿Ordenar por xPts ordena de verdad? Spearman y lift del cuartil alto vs el bajo | Los mismos pares |
| Once | ¿El XI recomendado puntúa más que el alineado y que “los 11 más caros”? | `decisions.xi_ids` (nuevo en el snapshot diario) |
| Mercado | ¿`buy_now` rinde más puntos/precio que `avoid`? | `decisions.actions` |
| Pipeline | ¿Mock, 429, pocos emparejados FF, ciclo > 15 min? | `sources` + `pipeline_seconds` |

Los umbrales viven en `DEFAULT_GATES` de [`src/performance_audit.py`](../src/performance_audit.py):

- Spearman ≥ 0.15 y lift ≥ 1.15× (n ≥ 30)
- MAE de titulares ≤ 3.5 y sesgo optimista ≤ +2.5
- Once recomendado no peor que el alineado en más de un 15 %
- `buy_now` no rinde claramente menos que `avoid`
- Mister no puede caer a mock ni a rate-limit duro

Si la muestra es fina (pretemporada, jornada 1 sin cerrar), los umbrales se **omiten**
en vez de fallar en falso.

## Cómo se ejecuta

```bash
# Informe en Markdown (todas las ligas del índice)
python src/performance_audit.py --markdown

# Una liga, JSON, y exit 1 si un umbral se rompe
python src/performance_audit.py --league laliga-patio --fail-on-gates --json-out /tmp/audit.json

# Tests
python scripts/test_performance_audit.py
```

En GitHub:

1. **Daily data update** — tras generar el JSON, corre el auditor y escribe el
   job summary. No bloquea el deploy (`continue-on-error`).
2. **Performance audit** — en PRs (tests) y los martes 08:00 UTC / a mano, con
   `--fail-on-gates`. Si el modelo o el once empeoran de verdad, el check se pone rojo.

El snapshot diario (`public/data/leagues/<slug>/history/YYYY-MM-DD.json`) guarda
además un recorte `decisions` (ids del once, plantilla, cola) y `pipeline`. El
payload vivo lleva el informe slim en `meta.performance_audit`.

## Cómo leer un fallo

- **spearman / lift** — el xPts ha dejado de ordenar; revisar titularidad FF o FDR.
- **titular_mae / titular_bias** — promete de más (o falla) en quien debería jugar.
- **xi_vs_current** — el once recomendado está dejando puntos vs el que ya alineas.
- **market_buy_vs_avoid** — la cola de fichajes rinde peor que lo que marcamos avoid.
- **pipeline** — auth caducada (mock) o FutbolFantasy cortando a 429.

El playbook diario ya enseña el balance de la última jornada; esta auditoría es
la misma idea, pero **con umbral y CI**, para no enterarnos a ojo.
