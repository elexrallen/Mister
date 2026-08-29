"""
Puntos esperados por jornada (xPts).

    xpts = p_juega * produccion_base * ajuste_fdr

Donde:
  - `p_juega`  combina la previa de Mister (once probable, confirmado o no),
    el % de FutbolFantasy y los minutos reales derivados de la racha Mister.
  - `produccion_base` mezcla histórico FF, racha y media Mister. Con menos
    de 5 partidos el histórico manda; media y racha corta no se doblecuentan.
  - `ajuste_fdr` viene de `fixture_difficulty` (rival y localía).

Regla de oro: una jornada se pierde por ceros, no por falta de estrellas. Por eso
`p_juega` multiplica y no suma, y un jugador sin minutos cae por mucho que puntúe
cuando juega.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("expected_points")

MIXTO_AVG_SCALE = 8.0

# Probabilidad de jugar según la señal más fuerte disponible
P_PLAY_UNAVAILABLE = 0.02
P_PLAY_GW_OUT = 0.03
P_PLAY_CONFIRMED_XI = 0.96
P_PLAY_PROBABLE_XI = 0.82
P_PLAY_OUT_OF_PREVIEW = 0.18
P_PLAY_UNKNOWN = 0.45

# Jornadas a partir de las cuales la racha deja de ser ruido
RECENT_MIN_SAMPLE = 5
# A partir de aquí manda la temporada en curso sobre el histórico
SEASON_TRUST_SAMPLE = 10


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f


def _ext(player: dict[str, Any]) -> dict[str, Any]:
    ext = player.get("external")
    return ext if isinstance(ext, dict) else {}


def resolve_scale(player: dict[str, Any], league_rules: dict[str, Any] | None) -> float:
    """Escala de media del provider (Mixto ~8, SofaScore/RPG ~16)."""
    for source in (player, _ext(player)):
        v = _num(source.get("ff_avg_scale"))
        if v and v > 0:
            return v
    v = _num((league_rules or {}).get("avg_scale"))
    if v and v > 0:
        return v
    return MIXTO_AVG_SCALE


def _played_points(player: dict[str, Any]) -> list[int]:
    streak = player.get("recent_gw_points")
    if not isinstance(streak, list):
        return []
    out: list[int] = []
    for v in streak:
        if v is None:
            continue
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


def _minutes_share(player: dict[str, Any]) -> float | None:
    """Fracción de jornadas disputadas según la racha Mister (None si no hay racha)."""
    streak = player.get("recent_gw_points")
    if not isinstance(streak, list) or not streak:
        return None
    played = sum(1 for v in streak if v is not None)
    return played / float(len(streak))


def probability_of_playing(player: dict[str, Any]) -> tuple[float, str]:
    """
    Probabilidad de que el jugador dispute la jornada, con el motivo.
    Mister manda cuando publica el once probable; FF aporta el matiz del %.
    """
    ext = _ext(player)
    if player.get("gw_blank") or ext.get("gw_blank"):
        return P_PLAY_UNAVAILABLE, "Sin partido esta jornada (blank)"
    avail = str(ext.get("availability") or ("injured" if player.get("injury") else "")).lower()
    if avail in ("injured", "suspended"):
        return P_PLAY_UNAVAILABLE, f"Baja ({avail})"
    if player.get("gw_out") or ext.get("gw_out"):
        return P_PLAY_GW_OUT, "Descartado en la previa"

    ff_prob = _num(player.get("gw_lineup_prob")) or _num(ext.get("gw_lineup_prob"))
    if ff_prob is not None:
        ff_prob = max(0.0, min(1.0, ff_prob / 100.0))

    probable = player.get("gw_probable_xi")
    confirmed = bool(player.get("gw_confirmed"))

    if probable is True:
        base = P_PLAY_CONFIRMED_XI if confirmed else P_PLAY_PROBABLE_XI
        why = "Once confirmado en Mister" if confirmed else "Once probable en Mister"
        if ff_prob is not None and not confirmed:
            # FF puede rebajar un once probable todavía sin confirmar
            base = max(0.55, (base + ff_prob) / 2.0)
            why = f"{why} + FF {ff_prob * 100:.0f}%"
        return base, why

    if probable is False:
        base = P_PLAY_OUT_OF_PREVIEW
        why = "Fuera del once probable de Mister"
        if ff_prob is not None:
            base = min(0.45, max(base, ff_prob * 0.6))
            why = f"{why} (FF {ff_prob * 100:.0f}%)"
        return base, why

    if ff_prob is not None:
        return ff_prob, f"FF {ff_prob * 100:.0f}% titular"

    season_prob = _num(player.get("lineup_prob"))
    if season_prob is not None:
        season_prob = season_prob if season_prob <= 1 else season_prob / 100.0
        return max(0.0, min(1.0, season_prob)), "Titularidad habitual (sin previa)"

    share = _minutes_share(player)
    if share is not None:
        return max(0.10, min(0.90, share)), "Minutos recientes en Mister"

    if avail == "doubt":
        return 0.35, "Duda física"
    return P_PLAY_UNKNOWN, "Sin señal de titularidad"


def _historic_avg(player: dict[str, Any]) -> float | None:
    ext = _ext(player)
    hist = _num(player.get("ff_mister_avg")) or _num(ext.get("ff_mister_avg"))
    if hist is None:
        hist = _num(ext.get("ff_prior_avg"))
    if hist is None or hist <= 0:
        return None
    return hist


def production_base(player: dict[str, Any], scale: float) -> tuple[float, str]:
    """
    Puntos esperados por partido jugado, en la escala del provider.

    Con muestra corta el histórico FF manda y la racha solo matiza. Media
    Mister y racha de <5 jornadas son el mismo n: no se cuentan dos veces.
    """
    played = _played_points(player)
    n = len(played)
    recent_avg = (sum(played) / n) if n else None
    season_avg = _num(player.get("mister_avg")) or _num(player.get("form"))
    if season_avg is not None and season_avg <= 0:
        season_avg = None
    hist = _historic_avg(player)

    current: float | None = None
    current_label = ""
    if n >= RECENT_MIN_SAMPLE and recent_avg is not None:
        if season_avg is not None and n >= 8:
            current = 0.55 * recent_avg + 0.45 * season_avg
            current_label = f"racha {n}j + media Mister"
        else:
            current = recent_avg
            current_label = f"racha {n}j"
    elif n and recent_avg is not None:
        current = recent_avg
        current_label = f"racha {n}j"
    elif season_avg is not None:
        current = season_avg
        current_label = "media Mister"

    if n < RECENT_MIN_SAMPLE:
        if hist is not None and current is not None:
            base = 0.85 * hist + 0.15 * current
            why = f"histórico FF {hist:.1f} · {current_label} (aún no manda)"
        elif hist is not None:
            base = hist
            why = f"histórico FF {hist:.1f}"
        elif current is not None:
            base = current
            why = f"{current_label} · sin histórico"
        else:
            return scale * 0.55, "sin histórico"
    elif n < SEASON_TRUST_SAMPLE:
        if hist is not None and current is not None:
            base = 0.5 * hist + 0.5 * current
            why = f"histórico FF {hist:.1f} + {current_label}"
        elif current is not None:
            base = current
            why = current_label
        elif hist is not None:
            base = hist
            why = f"histórico FF {hist:.1f}"
        else:
            return scale * 0.55, "sin histórico"
    else:
        if current is not None and hist is not None:
            base = 0.70 * current + 0.30 * hist
            why = f"{current_label} + histórico FF"
        elif current is not None:
            base = current
            why = current_label
        elif hist is not None:
            base = hist
            why = f"histórico FF {hist:.1f}"
        else:
            return scale * 0.55, "sin histórico"

    return max(0.0, min(base, scale * 2.0)), why


def expected_points(
    player: dict[str, Any],
    *,
    league_rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """xPts de un jugador para la jornada en curso."""
    scale = resolve_scale(player, league_rules)
    p_play, play_why = probability_of_playing(player)
    base, base_why = production_base(player, scale)

    fdr_mult = _num(player.get("fdr_multiplier"))
    if fdr_mult is None or fdr_mult <= 0:
        fdr_mult = 1.0

    xpts = p_play * base * fdr_mult
    # Suelo: escenario en el que juega menos o cunde poco
    conservative_play = p_play if player.get("gw_confirmed") else p_play * 0.85
    xpts_floor = conservative_play * base * 0.8 * fdr_mult

    why = f"{p_play * 100:.0f}% jugar ({play_why}) x {base:.1f} pts/partido ({base_why})"
    if abs(fdr_mult - 1.0) >= 0.01:
        rival = player.get("opponent_name") or "rival"
        is_home = player.get("is_home")
        where = " en casa" if is_home is True else (" fuera" if is_home is False else "")
        why = f"{why} x {fdr_mult:.2f} vs {rival}{where}"

    return {
        "xpts": round(xpts, 2),
        "xpts_floor": round(xpts_floor, 2),
        "xpts_p_play": round(p_play, 3),
        "xpts_base": round(base, 2),
        "xpts_scale": scale,
        "xpts_why": why,
    }


def annotate_players_with_xpts(
    players: list[dict[str, Any]],
    *,
    league_rules: dict[str, Any] | None = None,
) -> int:
    """Escribe xPts in-place sobre plantilla / mercado / pool."""
    if not players:
        return 0
    touched = 0
    seen: set[int] = set()
    for p in players:
        if id(p) in seen:
            continue
        seen.add(id(p))
        p.update(expected_points(p, league_rules=league_rules))
        per_m = xpts_per_million(p)
        if per_m is not None:
            p["xpts_per_m"] = per_m
        touched += 1
    return touched


def xpts_of(player: dict[str, Any] | None) -> float:
    """Lectura tolerante para ordenaciones (0.0 si aún no se ha calculado)."""
    return _num((player or {}).get("xpts")) or 0.0


def xpts_per_million(player: dict[str, Any] | None) -> float | None:
    """Eficiencia: puntos esperados por millón de valor."""
    p = player or {}
    price = _num(p.get("price")) or _num(p.get("market_value"))
    x = _num(p.get("xpts"))
    if not price or price <= 0 or x is None:
        return None
    return round(x / (price / 1_000_000.0), 3)
