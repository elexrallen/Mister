# Normas Mister Fantasy → factores del advisor

El advisor **descubre** las ligas de tu cuenta y **lee las normas** de cada comunidad para adaptar scoring, cupo, mercado y recomendaciones.

## De dónde salen las normas

| Fuente | Cuándo | Qué aporta |
|--------|--------|------------|
| `_FG_user` (HTML tras `switch_community`) | Siempre (miembro) | `provider`, `team_limit`, `type`, `clauses`, `loans`, `market_speed`, `custom_rules`, … |
| `_FG_cfg` (HTML de `/feed`) | Siempre | Ventana de mercado y señal de capitán de la comunidad activa |
| `POST /ajax/sw/admin` (`/feed#admin`) | Solo si eres admin | Settings extra del panel; fail-soft si `admin=0` |
| `LEAGUE_OVERRIDES` en [`src/config.py`](../src/config.py) | Opcional | `slug`, `season_start`, `default`; puede forzar `market_mode` / `max_squad` |

Las normas son **solo de Mister**: ninguna fuente externa las puede contradecir.
Ver la jerarquía completa de fuentes en el [README](../README.md#jerarquía-de-fuentes).

Documentación oficial Mister: [Reglas del juego](https://help.playmister.com/article/8-reglas-del-juego) y providers ([AS](https://help.playmister.com/article/63-points-as), [MARCA](https://help.playmister.com/article/64-points-marca), [SofaScore](https://help.playmister.com/article/96-puntos-sofascore), [MD](https://help.playmister.com/article/100-puntos-diario-md), [Clásico](https://help.playmister.com/article/122-sistema-de-puntuacion-clasico)).

## Providers de puntuación (`provider`)

| Código | Etiqueta | Escala FF del advisor | Factor |
|--------|----------|----------------------|--------|
| `mix` / `mix2` | Mixto / Mixto 2 | ~8 (Mister Mixto) | `scoring_mixto` |
| `mr` | SofaScore | ~16 (RPG-like) | `scoring_sofascore` |
| `as` / `marca` / `md` / `cls` | Cronistas / Clásico | ~8 | `scoring_*` |

La URL de scrape (LaLiga vs Premier) sigue la competición; el provider ajusta umbrales y etiqueta.

## Modo de mercado

| Señal Mister | `market_mode` | Efecto |
|--------------|---------------|--------|
| `type=comunio` / liga privada | `auction` | Pujas, `wait_risk`, hedges |
| `type=lfm` / contest | `fixed` | Precio listado, sin sobrepuja típica |

## Otras normas → factores

| Norma | Campo | Factor / comportamiento |
|-------|-------|-------------------------|
| Tope plantilla | `team_limit` → `max_squad` | Cupo en action plan / hedges |
| Cláusulas | `clauses` | Si off → no `clause_bid` |
| Cesiones | `loans` | Si off → no asumir liquidez por loan |
| Ritmo mercado | `market_speed` / `market_stay` | `market_urgency`; mercado rápido promueve `wait`→`buy_now` |
| Capitán | `captain` (`is_captain_enabled` + multiplicador) | Factor `captain_x{n}`; `recommended_xi.captain` elegido por `xpts` |
| Texto admin | `custom_rules` | Factor `custom_rules_text` (visible en JSON) |

### Capitán

Mister no expone el multiplicador como número en ningún endpoint; los textos de
la interfaz lo describen como doble puntuación, así que el advisor asume **x2**
salvo que `admin_settings` o un override de `LEAGUE_OVERRIDES` digan otra cosa.
La detección de si la liga tiene capitán activo se lee, por este orden, de
`league_cfg`, `admin_settings` y `_FG_cfg`.

El capitán se elige maximizando `xpts × (multiplicador − 1)` y se desempata por
probabilidad de jugar: un capitán que no salta al campo es el error más caro del
juego, así que ante xPts parecidos gana siempre el titular más seguro.

## Dónde se ve en el JSON

```json
"league": {
  "rules": {
    "provider": "mix",
    "provider_label": "Mixto",
    "max_squad": 25,
    "market_mode": "auction",
    "clauses": true,
    "loans": true,
    "market_speed": 1,
    "captain": {
      "enabled": true,
      "known": true,
      "multiplier": 2.0,
      "source": "fg_cfg",
      "multiplier_source": "default"
    },
    "source": "fg_user",
    "factors": ["scoring_mixto", "auction_urgency", "clause_bids", "max_squad_25", "normal_market_cycle"]
  }
}
```

El índice `public/data/leagues.json` resume las mismas normas por liga para el selector de la PWA.
