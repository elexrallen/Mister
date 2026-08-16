# Doctrina diaria

El ciclo de una jornada en Mister siempre es el mismo:

```
jornada cerrada → mercado abierto → salen las previas → víspera → partidos
```

Cada tramo premia acciones distintas. Comprar el día antes del cierre paga la
prima del pánico; ajustar el once cuando el partido ya ha empezado no puntúa. El
módulo [`src/daily_playbook.py`](../src/daily_playbook.py) traduce el reloj real
de Mister (`hours_to_jornada`, calculado con los kickoffs unix del `/feed`), el
estado del mercado y el diagnóstico de plantilla en una lista corta de cosas que
hacer hoy.

## Fases

| Fase | Cuándo | En qué se centra |
|------|--------|------------------|
| `post_jornada` | Más de 96 h para el primer partido, o jornada recién cerrada | Balance de la jornada, ventas de los que ya no cuentan y objetivos del siguiente ciclo |
| `ventana_compra` | Entre 48 h y 96 h | Fichar: los precios aún no llevan la prima de la víspera |
| `confirmacion` | Entre 24 h y 48 h | Salen las previas; confirmar titularidades y corregir el once antes de que suban los precios |
| `visperas` | Entre 6 h y 24 h | Cerrar el once y asegurar saldo positivo |
| `dia_partido` | Menos de 6 h | El once casi no se toca: cerrar capitán y suplencias antes de cada kickoff |
| `jornada_en_curso` | Jornada en juego | Solo cambios en vivo si la liga los permite |
| `pretemporada` | Sin jornadas disputadas | Construir plantilla: titularidad y producción histórica mandan sobre el precio |

Los cortes están en las constantes `HOURS_MATCHDAY`, `HOURS_EVE`,
`HOURS_CONFIRMATION` y `HOURS_BUY_WINDOW` del módulo.

## Qué genera

`build_daily_playbook()` devuelve la fase, el countdown, el próximo kickoff y un
`checklist` ordenado por prioridad. Cada punto lleva el porqué, no solo la orden:

- **Once y capitán** (siempre lo primero, es lo único que puntúa): huecos con cero
  probable (`xi_ceros`), dudas pendientes de confirmar, titulares con partido muy
  exigente y cambio de capitán con su motivo y su alternativa.
- **Dinero**: saldo negativo con la jornada encima, que en muchas ligas anula los
  puntos.
- **Mercado según fase**: fichar y listar ventas en la ventana de compra, última
  llamada en confirmación, y freno explícito en víspera y día de partido. En las
  fases pegadas al cierre el ranking de compra dobla el peso de los puntos
  esperados y reduce a la mitad el de objetivo de plantilla a medio plazo.
- **Balance del ciclo**: error medio del xPts en la última jornada cerrada y los
  mayores desvíos con nombre, para saber si el modelo va sobrado o corto.
- **Estructura**: carencias estructurales de plantilla, que salen más baratas de
  resolver lejos de la jornada.

Cada punto lleva `related_player_ids`, así que la PWA enlaza directamente a la
ficha de los jugadores implicados en vez de dejar el aviso en abstracto.

El checklist se vuelca a `recommendations[]` del payload vía
`playbook_to_recommendations()`, y el bloque completo va en `playbook`. La PWA lo
pinta encima de la **Cola del día**, de forma que la fase y el foco se ven aunque
no haya ninguna acción de mercado pendiente.

## Cómo leerlo cada día

1. Mira la fase y el countdown: te dice si hoy toca mover mercado o cerrar once.
2. Resuelve primero lo marcado como **Alta**; suele ser once, capitán o saldo.
3. En `ventana_compra` es cuando fichar sale barato; en `visperas` lo normal es
   no tocar el mercado salvo que tapes un hueco del once.
4. Después de la jornada, mira el punto de **balance**: compara los puntos
   esperados que guardó el snapshot con los reales y señala en qué te pasaste y
   en qué te quedaste corto (`meta.model_calibration` tiene el desglose por tramo
   de probabilidad de jugar).
