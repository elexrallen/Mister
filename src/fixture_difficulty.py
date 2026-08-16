"""
Dificultad de rival (FDR) a partir de datos propios de Mister.

Fuentes (todas de `/ajax/sw/competition` + `/ajax/sw/players`):
  - Clasificación real: goles a favor / en contra por equipo
  - Calendario completo: rival y localía de cada jornada
  - Puntos fantasy por jornada agregados por equipo (señal endógena)

Escala FDR 1..5 (1 = rival muy asequible, 5 = rival muy duro). Sin muestra
suficiente devuelve 3 (neutro) y lo marca como baja confianza: en pretemporada
no hay nada que medir y es preferible no inventar.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("fixture_difficulty")

NEUTRAL_FDR = 3.0
# Partidos jugados por equipo a partir de los cuales la señal deja de ser ruido
MIN_PLAYED_FOR_SIGNAL = 3
# Peso del FDR sobre los puntos esperados: FDR 1 → x1.10, FDR 5 → x0.90
FDR_MULTIPLIER_STEP = 0.05
HOME_ADVANTAGE_FDR = 0.25

FDR_LABELS = {
    1: "muy favorable",
    2: "favorable",
    3: "neutro",
    4: "exigente",
    5: "muy exigente",
}


def _safe_div(num: Any, den: Any) -> float | None:
    try:
        n = float(num)
        d = float(den)
    except (TypeError, ValueError):
        return None
    if d <= 0:
        return None
    return n / d


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def build_team_strength(table: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    """
    Fuerza ofensiva y defensiva por equipo, normalizada contra la media de la liga.
    Devuelve también la confianza según partidos jugados.
    """
    rows = [r for r in (table or {}).values() if isinstance(r, dict)]
    played_total = sum(int(r.get("played") or 0) for r in rows)
    max_played = max((int(r.get("played") or 0) for r in rows), default=0)
    if not rows or max_played < 1:
        return {"teams": {}, "confidence": "none", "max_played": max_played}

    gf_rates: dict[str, float] = {}
    ga_rates: dict[str, float] = {}
    for r in rows:
        tid = str(r.get("team_id") or "")
        if not tid:
            continue
        gf = _safe_div(r.get("goals_for"), r.get("played"))
        ga = _safe_div(r.get("goals_against"), r.get("played"))
        if gf is not None:
            gf_rates[tid] = gf
        if ga is not None:
            ga_rates[tid] = ga

    if not gf_rates or not ga_rates:
        return {"teams": {}, "confidence": "none", "max_played": max_played}

    avg_gf = sum(gf_rates.values()) / len(gf_rates)
    avg_ga = sum(ga_rates.values()) / len(ga_rates)

    teams: dict[str, dict[str, float]] = {}
    for tid in set(gf_rates) | set(ga_rates):
        teams[tid] = {
            # >1 = ataca más que la media; >1 en concede = encaja más que la media
            "attack": (gf_rates.get(tid, avg_gf) / avg_gf) if avg_gf > 0 else 1.0,
            "concede": (ga_rates.get(tid, avg_ga) / avg_ga) if avg_ga > 0 else 1.0,
            "goals_for_pg": gf_rates.get(tid, avg_gf),
            "goals_against_pg": ga_rates.get(tid, avg_ga),
        }

    confidence = "high" if max_played >= MIN_PLAYED_FOR_SIGNAL * 2 else (
        "medium" if max_played >= MIN_PLAYED_FOR_SIGNAL else "low"
    )
    return {
        "teams": teams,
        "confidence": confidence,
        "max_played": max_played,
        "matches_played": played_total,
        "avg_goals_for": avg_gf,
        "avg_goals_against": avg_ga,
    }


def fdr_for(
    opponent_id: str | None,
    *,
    position: str,
    is_home: bool | None,
    strength: dict[str, Any],
) -> dict[str, Any]:
    """
    FDR de un jugador frente a un rival concreto.

    A un delantero le importa lo que encaja el rival; a un defensa o portero,
    lo que el rival marca. Sin muestra suficiente → neutro declarado.
    """
    teams = (strength or {}).get("teams") or {}
    confidence = str((strength or {}).get("confidence") or "none")
    row = teams.get(str(opponent_id or ""))
    if not row or confidence in ("none", "low"):
        return {
            "fdr": NEUTRAL_FDR,
            "fdr_label": FDR_LABELS[3],
            "fdr_multiplier": 1.0,
            "fdr_confidence": confidence if confidence != "none" else "none",
            "fdr_why": "Sin muestra suficiente para medir al rival",
        }

    pos = (position or "").upper()
    if pos in ("GK", "DF"):
        # Rival que marca mucho → difícil mantener portería a cero
        ratio = float(row.get("attack") or 1.0)
        basis = "ataque del rival"
    else:
        # Rival que encaja mucho → más fácil producir
        ratio = 2.0 - float(row.get("concede") or 1.0)
        basis = "solidez defensiva del rival"

    # ratio 1.0 = media de la liga → FDR 3; cada 25% de desviación ≈ 1 punto de FDR
    fdr = NEUTRAL_FDR + (ratio - 1.0) * 4.0
    if is_home is True:
        fdr -= HOME_ADVANTAGE_FDR
    elif is_home is False:
        fdr += HOME_ADVANTAGE_FDR
    fdr = _clamp(fdr, 1.0, 5.0)

    multiplier = _clamp(1.0 + (NEUTRAL_FDR - fdr) * FDR_MULTIPLIER_STEP, 0.85, 1.15)
    where = "en casa" if is_home else ("fuera" if is_home is False else "")
    return {
        "fdr": round(fdr, 2),
        "fdr_label": FDR_LABELS[int(round(fdr))],
        "fdr_multiplier": round(multiplier, 3),
        "fdr_confidence": confidence,
        "fdr_why": f"{FDR_LABELS[int(round(fdr))].capitalize()} por {basis}{(' ' + where) if where else ''}",
    }


def annotate_players_with_fdr(
    players: list[dict[str, Any]],
    *,
    strength: dict[str, Any],
    team_schedule: dict[str, list[dict[str, Any]]] | None = None,
    horizon: int = 3,
) -> int:
    """Escribe `fdr`, `fdr_multiplier` y el resumen de las próximas jornadas."""
    if not players:
        return 0
    touched = 0
    seen: set[int] = set()
    for p in players:
        if id(p) in seen:
            continue
        seen.add(id(p))
        info = fdr_for(
            p.get("next_opponent_team_id") or p.get("gw_opponent_id"),
            position=str(p.get("position") or ""),
            is_home=p.get("next_is_home") if p.get("next_is_home") is not None else p.get("gw_is_home"),
            strength=strength,
        )
        p.update(info)

        upcoming = (team_schedule or {}).get(str(p.get("team_id") or "")) or []
        if upcoming:
            rows = []
            for fx in upcoming[:horizon]:
                sub = fdr_for(
                    fx.get("opponent_id"),
                    position=str(p.get("position") or ""),
                    is_home=fx.get("is_home"),
                    strength=strength,
                )
                rows.append(
                    {
                        "jornada": fx.get("jornada"),
                        "opponent_id": fx.get("opponent_id"),
                        "is_home": fx.get("is_home"),
                        "fdr": sub["fdr"],
                    }
                )
            if rows:
                p["fdr_next"] = rows
                p["fdr_next_avg"] = round(sum(r["fdr"] for r in rows) / len(rows), 2)
        touched += 1
    return touched
