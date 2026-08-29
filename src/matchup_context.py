"""
Contexto de cruce para la ficha del once objetivo.

Alinea la racha Mister con el calendario (rival + casa/fuera). No toca xPts
ni fdr_multiplier: un H2H de n=1 no manda el ranking.
"""

from __future__ import annotations

from typing import Any

HOME_AWAY_MIN = 3


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(rows: list[float]) -> float | None:
    if not rows:
        return None
    return sum(rows) / len(rows)


def tag_streak_with_fixtures(
    player: dict[str, Any],
    fixtures: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """
    Cada punto reciente con rival y localía.

    La racha se alinea por la cola, igual que `build_fantasy_conceded`:
    si el jugador trae 4 jornadas y su equipo lleva 6, esas 4 son las últimas.
    """
    streak = player.get("recent_gw_points")
    if not isinstance(streak, list) or not fixtures:
        return []
    window = streak[-len(fixtures) :] if len(streak) > len(fixtures) else streak
    offset = len(fixtures) - len(window)
    out: list[dict[str, Any]] = []
    for i, pts in enumerate(window):
        fx = fixtures[offset + i] if 0 <= offset + i < len(fixtures) else {}
        value = _num(pts)
        out.append(
            {
                "jornada": fx.get("jornada"),
                "opponent_id": str(fx.get("opponent_id") or "") or None,
                "is_home": fx.get("is_home"),
                "points": int(value) if value is not None else None,
            }
        )
    return out


def vs_opponent_rows(
    tagged: list[dict[str, Any]],
    opponent_id: str | None,
) -> list[dict[str, Any]]:
    oid = str(opponent_id or "").strip()
    if not oid:
        return []
    return [
        row
        for row in tagged
        if str(row.get("opponent_id") or "") == oid and row.get("points") is not None
    ]


def home_away_split(tagged: list[dict[str, Any]]) -> dict[str, Any] | None:
    home = [float(r["points"]) for r in tagged if r.get("is_home") is True and r.get("points") is not None]
    away = [float(r["points"]) for r in tagged if r.get("is_home") is False and r.get("points") is not None]
    if len(home) < HOME_AWAY_MIN or len(away) < HOME_AWAY_MIN:
        return None
    return {
        "home_avg": round(_mean(home) or 0.0, 2),
        "away_avg": round(_mean(away) or 0.0, 2),
        "home_n": len(home),
        "away_n": len(away),
    }


def build_matchup(
    player: dict[str, Any],
    *,
    played_fixtures: dict[str, list[dict[str, Any]]] | None = None,
    team_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Bloque informativo del cruce. Nunca altera xPts."""
    names = team_names or {}
    tid = str(player.get("team_id") or "")
    fixtures = (played_fixtures or {}).get(tid) if played_fixtures else None
    tagged = tag_streak_with_fixtures(player, fixtures)
    opp_id = player.get("next_opponent_team_id") or player.get("gw_opponent_id")
    vs_rows = vs_opponent_rows(tagged, str(opp_id) if opp_id else None)
    split = home_away_split(tagged)
    opp_name = (
        player.get("opponent_name")
        or player.get("gw_opponent")
        or names.get(str(opp_id or ""))
    )
    is_home = player.get("is_home")
    if is_home is None:
        is_home = player.get("next_is_home")
        if is_home is None:
            is_home = player.get("gw_is_home")

    vs_payload = None
    if vs_rows:
        vs_payload = {
            "n": len(vs_rows),
            "points": [int(r["points"]) for r in vs_rows if r.get("points") is not None],
            "avg": round(_mean([float(r["points"]) for r in vs_rows]) or 0.0, 2),
            "last": {
                "jornada": vs_rows[-1].get("jornada"),
                "points": vs_rows[-1].get("points"),
                "is_home": vs_rows[-1].get("is_home"),
            },
        }

    where = "en casa" if is_home is True else ("fuera" if is_home is False else "")
    why_bits: list[str] = []
    if opp_name:
        why_bits.append(f"vs {opp_name}" + (f" {where}" if where else ""))
    elif where:
        why_bits.append(where)
    if player.get("fdr_why"):
        why_bits.append(str(player.get("fdr_why")))
    if vs_payload:
        last = vs_payload["last"]
        last_where = "en casa" if last.get("is_home") is True else ("fuera" if last.get("is_home") is False else "")
        why_bits.append(
            f"vs este rival esta temporada: {last.get('points')} pts"
            + (f" ({last_where})" if last_where else "")
        )
    if split:
        why_bits.append(f"casa {split['home_avg']:.1f} / fuera {split['away_avg']:.1f}")

    return {
        "opponent_id": str(opp_id) if opp_id else None,
        "opponent": opp_name,
        "is_home": is_home,
        "fdr": player.get("fdr"),
        "fdr_label": player.get("fdr_label"),
        "fdr_why": player.get("fdr_why"),
        "vs_opponent": vs_payload,
        "home_away_split": split,
        "why": " · ".join(why_bits) if why_bits else None,
    }


def annotate_players_with_matchup(
    players: list[dict[str, Any]],
    *,
    played_fixtures: dict[str, list[dict[str, Any]]] | None = None,
    team_names: dict[str, str] | None = None,
) -> int:
    """Escribe `matchup` in-place. No muta xPts ni FDR."""
    if not players:
        return 0
    touched = 0
    seen: set[int] = set()
    for p in players:
        if id(p) in seen:
            continue
        seen.add(id(p))
        p["matchup"] = build_matchup(
            p, played_fixtures=played_fixtures, team_names=team_names
        )
        touched += 1
    return touched
