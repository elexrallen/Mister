"""
Dificultad de rival (FDR) a partir de datos propios de Mister.

Jugar contra el colista no es lo mismo que jugar contra el Barcelona, y eso
tiene que notarse desde la jornada 1. La fuerza de cada equipo se construye
encadenando tres señales, de más fiable a más disponible:

  1. **Clasificación real** (`/ajax/sw/competition`): goles a favor y en contra
     por partido. Es la mejor señal, pero tarda unas jornadas en existir.
  2. **Prior de calidad de plantilla**: valor de mercado agregado del pool de
     Mister mezclado con la media FF de la temporada anterior. Disponible el
     primer día, y suficiente para que Barça y colista nunca empaten.
  3. **Puntos fantasy concedidos**: cuántos puntos Mister regala cada equipo
     por posición. Un equipo que encaja poco no siempre concede pocos puntos.

Escala FDR 1..5 (1 = rival muy asequible, 5 = rival muy duro). La localía se
aplica siempre: vale ~5% por lado, y el rival hasta ±22%.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("fixture_difficulty")

NEUTRAL_FDR = 3.0
# Partidos jugados por equipo a partir de los cuales la tabla deja de ser ruido
MIN_PLAYED_FOR_SIGNAL = 3
# Partidos a partir de los cuales la tabla manda del todo sobre el prior
FULL_TABLE_TRUST_PLAYED = 6

# Peso del FDR sobre los puntos esperados: FDR 1 → x1.22, FDR 5 → x0.78
FDR_MULTIPLIER_STEP = 0.11
FDR_MULTIPLIER_CLAMP = (0.72, 1.28)
# Localía en unidades de FDR: 0.45 * 0.11 ≈ 5% por lado
HOME_ADVANTAGE_FDR = 0.45

# Calidad del prior por ranking: el mejor equipo 1.35, el peor 0.65
PRIOR_INDEX_MAX = 1.35
PRIOR_INDEX_MIN = 0.65
# Jugadores por equipo que definen la calidad de la plantilla
PRIOR_SQUAD_DEPTH = 14
# Peso del valor de mercado frente a la media FF previa
PRIOR_VALUE_WEIGHT = 0.6
# Peso de los puntos fantasy concedidos sobre la dificultad final
FANTASY_CONCEDED_WEIGHT = 0.4
# Registros mínimos por equipo y posición para fiarse de los puntos concedidos
FANTASY_CONCEDED_MIN_SAMPLE = 6

FDR_LABELS = {
    1: "muy favorable",
    2: "favorable",
    3: "neutro",
    4: "exigente",
    5: "muy exigente",
}


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_div(num: Any, den: Any) -> float | None:
    n = _num(num)
    d = _num(den)
    if n is None or d is None or d <= 0:
        return None
    return n / d


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


# ---------------------------------------------------------------------------
# 2) Prior de calidad de plantilla (disponible desde J1)
# ---------------------------------------------------------------------------

def build_team_prior(
    pool: list[dict[str, Any]] | None,
    *,
    depth: int = PRIOR_SQUAD_DEPTH,
) -> dict[str, Any]:
    """
    Calidad relativa de cada equipo antes de que haya clasificación.

    Se ordenan los equipos por una mezcla de valor de plantilla y media FF de la
    temporada anterior, y el ranking se reparte linealmente entre
    `PRIOR_INDEX_MIN` y `PRIOR_INDEX_MAX`. Usar el ranking en vez del ratio
    bruto evita que un Madrid con plantilla tres veces más cara se salga de la
    escala.
    """
    values: dict[str, list[float]] = {}
    priors: dict[str, list[float]] = {}
    for p in pool or []:
        tid = str(p.get("team_id") or "").strip()
        if not tid:
            continue
        val = _num(p.get("market_value")) or _num(p.get("price"))
        if val and val > 0:
            values.setdefault(tid, []).append(val)
        ext = p.get("external") if isinstance(p.get("external"), dict) else {}
        avg = _num(p.get("ff_prior_avg")) or _num(ext.get("ff_prior_avg"))
        if avg is not None:
            priors.setdefault(tid, []).append(avg)

    team_ids = sorted(set(values) | set(priors))
    if len(team_ids) < 4:
        return {"teams": {}, "source": "none", "teams_ranked": 0}

    def _top_mean(rows: list[float]) -> float | None:
        return _mean(sorted(rows, reverse=True)[:depth])

    value_score = {tid: _top_mean(values.get(tid) or []) for tid in team_ids}
    prior_score = {tid: _top_mean(priors.get(tid) or []) for tid in team_ids}
    avg_value = _mean([v for v in value_score.values() if v])
    avg_prior = _mean([v for v in prior_score.values() if v])

    blended: dict[str, float] = {}
    for tid in team_ids:
        parts: list[tuple[float, float]] = []
        if avg_value and value_score.get(tid):
            parts.append((value_score[tid] / avg_value, PRIOR_VALUE_WEIGHT))
        if avg_prior and prior_score.get(tid):
            parts.append((prior_score[tid] / avg_prior, 1.0 - PRIOR_VALUE_WEIGHT))
        if not parts:
            continue
        weight = sum(w for _, w in parts)
        blended[tid] = sum(v * w for v, w in parts) / weight

    if len(blended) < 4:
        return {"teams": {}, "source": "none", "teams_ranked": 0}

    ranked = sorted(blended.items(), key=lambda kv: -kv[1])
    span = PRIOR_INDEX_MAX - PRIOR_INDEX_MIN
    last = len(ranked) - 1
    teams: dict[str, dict[str, Any]] = {}
    for i, (tid, raw) in enumerate(ranked):
        quality = PRIOR_INDEX_MAX - span * (i / last)
        teams[tid] = {
            "quality": round(quality, 3),
            "rank": i + 1,
            "raw_index": round(raw, 3),
            "squad_value_avg": round(value_score.get(tid) or 0.0, 0) or None,
            "ff_prior_avg": round(prior_score.get(tid) or 0.0, 2) or None,
        }
    source = "value+ff" if (avg_value and avg_prior) else ("value" if avg_value else "ff")
    return {"teams": teams, "source": source, "teams_ranked": len(teams)}


# ---------------------------------------------------------------------------
# 3) Puntos fantasy que concede cada equipo, por posición
# ---------------------------------------------------------------------------

def build_fantasy_conceded(
    pool: list[dict[str, Any]] | None,
    *,
    played_opponents: dict[str, list[str]] | None,
) -> dict[str, Any]:
    """
    Puntos Mister que cada equipo regala por posición, relativos a la media.

    `played_opponents` es `{team_id: [rival de cada jornada ya disputada]}`. La
    racha (`recent_gw_points`) se alinea por la cola: si un jugador trae 4
    jornadas y su equipo lleva 6, esas 4 son las 4 últimas.
    """
    schedule = played_opponents or {}
    if not schedule:
        return {"teams": {}, "source": "none"}

    totals: dict[str, dict[str, list[float]]] = {}
    for p in pool or []:
        tid = str(p.get("team_id") or "").strip()
        pos = str(p.get("position") or "").upper()
        streak = p.get("recent_gw_points")
        if not tid or pos not in ("GK", "DF", "MF", "FW") or not isinstance(streak, list):
            continue
        rivals = schedule.get(tid) or []
        if not rivals:
            continue
        window = streak[-len(rivals):] if len(streak) > len(rivals) else streak
        offset = len(rivals) - len(window)
        for i, pts in enumerate(window):
            value = _num(pts)
            if value is None:
                continue
            rival = rivals[offset + i]
            if not rival:
                continue
            totals.setdefault(str(rival), {}).setdefault(pos, []).append(value)

    if not totals:
        return {"teams": {}, "source": "none"}

    league: dict[str, list[float]] = {}
    for by_pos in totals.values():
        for pos, rows in by_pos.items():
            league.setdefault(pos, []).extend(rows)
    league_avg = {pos: _mean(rows) for pos, rows in league.items()}

    teams: dict[str, dict[str, float]] = {}
    for tid, by_pos in totals.items():
        row: dict[str, float] = {}
        for pos, rows in by_pos.items():
            avg = league_avg.get(pos)
            if not avg or avg <= 0 or len(rows) < FANTASY_CONCEDED_MIN_SAMPLE:
                continue
            row[pos] = round((sum(rows) / len(rows)) / avg, 3)
        if row:
            teams[tid] = row
    return {"teams": teams, "source": "fantasy" if teams else "none"}


# ---------------------------------------------------------------------------
# 1) Clasificación real, mezclada con el prior
# ---------------------------------------------------------------------------

def build_team_strength(
    table: dict[str, dict[str, Any]] | None,
    *,
    prior: dict[str, Any] | None = None,
    conceded: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Fuerza ofensiva y defensiva por equipo, normalizada contra la media.

    Con pocos partidos manda el prior de plantilla; según se acumulan jornadas
    la clasificación va tomando el mando. Nunca se devuelve un neutro plano si
    hay prior: preferimos una estimación honesta a fingir que todos los rivales
    son iguales.
    """
    rows = [r for r in (table or {}).values() if isinstance(r, dict)]
    played_total = sum(int(r.get("played") or 0) for r in rows)
    max_played = max((int(r.get("played") or 0) for r in rows), default=0)

    prior_teams = ((prior or {}).get("teams") or {}) if isinstance(prior, dict) else {}
    conceded_teams = ((conceded or {}).get("teams") or {}) if isinstance(conceded, dict) else {}

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

    avg_gf = _mean(list(gf_rates.values()))
    avg_ga = _mean(list(ga_rates.values()))
    has_table = bool(gf_rates and ga_rates and avg_gf and avg_ga and max_played >= 1)
    table_weight = _clamp(max_played / FULL_TABLE_TRUST_PLAYED, 0.0, 1.0) if has_table else 0.0

    if not has_table and not prior_teams:
        return {
            "teams": {},
            "source": "none",
            "confidence": "none",
            "max_played": max_played,
            "table_weight": 0.0,
        }

    teams: dict[str, dict[str, Any]] = {}
    for tid in set(gf_rates) | set(ga_rates) | set(prior_teams) | set(conceded_teams):
        parts: list[tuple[float, float, float]] = []  # (attack, concede, peso)
        if has_table and (tid in gf_rates or tid in ga_rates):
            parts.append(
                (
                    (gf_rates.get(tid, avg_gf) / avg_gf),
                    (ga_rates.get(tid, avg_ga) / avg_ga),
                    table_weight,
                )
            )
        quality = _num((prior_teams.get(tid) or {}).get("quality"))
        if quality is not None:
            parts.append((quality, 2.0 - quality, 1.0 - table_weight))
        usable = [(a, c, w) for a, c, w in parts if w > 0]
        if not usable:
            usable = parts[:1]
        if not usable:
            continue
        total_w = sum(w for _, _, w in usable) or 1.0
        row: dict[str, Any] = {
            "attack": sum(a * w for a, _, w in usable) / total_w,
            "concede": sum(c * w for _, c, w in usable) / total_w,
        }
        if tid in gf_rates:
            row["goals_for_pg"] = gf_rates[tid]
        if tid in ga_rates:
            row["goals_against_pg"] = ga_rates[tid]
        if quality is not None:
            row["prior_quality"] = quality
            row["prior_rank"] = (prior_teams.get(tid) or {}).get("rank")
        if conceded_teams.get(tid):
            row["fantasy_conceded"] = conceded_teams[tid]
        teams[tid] = row

    if table_weight >= 0.99:
        source = "table"
    elif table_weight > 0:
        source = "mixed"
    else:
        source = "prior"
    confidence = (
        "high"
        if max_played >= MIN_PLAYED_FOR_SIGNAL * 2
        else ("medium" if max_played >= MIN_PLAYED_FOR_SIGNAL else ("low" if max_played else "none"))
    )
    return {
        "teams": teams,
        "source": source,
        "confidence": confidence,
        "max_played": max_played,
        "matches_played": played_total,
        "table_weight": round(table_weight, 2),
        "prior_source": (prior or {}).get("source") if isinstance(prior, dict) else None,
        "avg_goals_for": avg_gf,
        "avg_goals_against": avg_ga,
    }


def fdr_for(
    opponent_id: str | None,
    *,
    position: str,
    is_home: bool | None,
    strength: dict[str, Any],
    opponent_name: str | None = None,
) -> dict[str, Any]:
    """
    FDR de un jugador frente a un rival concreto.

    A un delantero le importa lo que encaja el rival; a un defensa o portero,
    lo que el rival marca. Sin rival identificado no hay nada que ajustar, pero
    la localía sí se aplica siempre que se conozca.
    """
    teams = (strength or {}).get("teams") or {}
    source = str((strength or {}).get("source") or "none")
    row = teams.get(str(opponent_id or ""))
    where = "en casa" if is_home else ("fuera" if is_home is False else "")

    if not row:
        if is_home is None:
            return {
                "fdr": NEUTRAL_FDR,
                "fdr_label": FDR_LABELS[3],
                "fdr_multiplier": 1.0,
                "fdr_confidence": "none",
                "fdr_why": "Sin rival identificado para la próxima jornada",
                "opponent_name": opponent_name,
                "is_home": is_home,
            }
        fdr = _clamp(NEUTRAL_FDR - (HOME_ADVANTAGE_FDR if is_home else -HOME_ADVANTAGE_FDR), 1.0, 5.0)
        multiplier = _clamp(
            1.0 + (NEUTRAL_FDR - fdr) * FDR_MULTIPLIER_STEP, *FDR_MULTIPLIER_CLAMP
        )
        return {
            "fdr": round(fdr, 2),
            "fdr_label": FDR_LABELS[int(round(fdr))],
            "fdr_multiplier": round(multiplier, 3),
            "fdr_confidence": "home_only",
            "fdr_why": f"Rival sin medir; solo cuenta jugar {where}",
            "opponent_name": opponent_name,
            "is_home": is_home,
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

    signal = source
    fantasy = (row.get("fantasy_conceded") or {}).get(pos)
    fc = _num(fantasy)
    if fc is not None:
        # Concede muchos puntos fantasy → rival más fácil de lo que dicen los goles
        ratio = ratio * (1.0 - FANTASY_CONCEDED_WEIGHT) + (2.0 - fc) * FANTASY_CONCEDED_WEIGHT
        basis = "puntos que concede el rival"
        signal = "fantasy"

    # ratio 1.0 = media de la liga → FDR 3; cada 25% de desviación ≈ 1 punto de FDR
    fdr = NEUTRAL_FDR + (ratio - 1.0) * 4.0
    if is_home is True:
        fdr -= HOME_ADVANTAGE_FDR
    elif is_home is False:
        fdr += HOME_ADVANTAGE_FDR
    fdr = _clamp(fdr, 1.0, 5.0)

    multiplier = _clamp(1.0 + (NEUTRAL_FDR - fdr) * FDR_MULTIPLIER_STEP, *FDR_MULTIPLIER_CLAMP)
    label = FDR_LABELS[int(round(fdr))]
    rival = opponent_name or "el rival"
    return {
        "fdr": round(fdr, 2),
        "fdr_label": label,
        "fdr_multiplier": round(multiplier, 3),
        "fdr_confidence": signal,
        "fdr_why": f"{label.capitalize()} vs {rival}{(' ' + where) if where else ''} por {basis}",
        "opponent_name": opponent_name,
        "is_home": is_home,
    }


def annotate_players_with_fdr(
    players: list[dict[str, Any]],
    *,
    strength: dict[str, Any],
    team_schedule: dict[str, list[dict[str, Any]]] | None = None,
    team_names: dict[str, str] | None = None,
    horizon: int = 3,
    current_jornada: int | None = None,
    now: Any = None,
) -> int:
    """Escribe `fdr`, `fdr_multiplier`, rival y localía, y el resumen a 3 jornadas."""
    if not players:
        return 0
    names = team_names or {}
    try:
        from mister_gameweek import fixture_is_unplayed
    except Exception:  # noqa: BLE001
        fixture_is_unplayed = None
    touched = 0
    seen: set[int] = set()
    for p in players:
        if id(p) in seen:
            continue
        seen.add(id(p))
        opponent_id = p.get("next_opponent_team_id")
        is_home = p.get("next_is_home")
        opp_name = names.get(str(opponent_id or "")) if opponent_id else None
        if not opponent_id:
            opponent_id = p.get("gw_opponent_id")
            if is_home is None:
                is_home = p.get("gw_is_home")
            opp_name = names.get(str(opponent_id or "")) or p.get("gw_opponent")
        info = fdr_for(
            opponent_id,
            position=str(p.get("position") or ""),
            is_home=is_home,
            strength=strength,
            opponent_name=opp_name or p.get("gw_opponent"),
        )
        p.update(info)

        next_j = p.get("next_jornada")
        applies = True
        if p.get("gw_played") and current_jornada is not None and next_j is not None:
            try:
                applies = int(next_j) <= int(current_jornada)
            except (TypeError, ValueError):
                applies = True
        p["fdr_applies_to_current_gw"] = applies

        upcoming = (team_schedule or {}).get(str(p.get("team_id") or "")) or []
        if upcoming:
            rows = []
            for fx in upcoming:
                if fixture_is_unplayed is not None and not fixture_is_unplayed(fx, now=now):
                    continue
                sub = fdr_for(
                    fx.get("opponent_id"),
                    position=str(p.get("position") or ""),
                    is_home=fx.get("is_home"),
                    strength=strength,
                    opponent_name=names.get(str(fx.get("opponent_id") or "")),
                )
                rows.append(
                    {
                        "jornada": fx.get("jornada"),
                        "opponent_id": fx.get("opponent_id"),
                        "opponent_name": sub.get("opponent_name"),
                        "is_home": fx.get("is_home"),
                        "fdr": sub["fdr"],
                    }
                )
                if len(rows) >= horizon:
                    break
            if rows:
                p["fdr_next"] = rows
                p["fdr_next_avg"] = round(sum(r["fdr"] for r in rows) / len(rows), 2)
        touched += 1
    return touched
