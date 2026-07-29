"""
Acciones competitivas: ventas situacionales, presupuesto/riesgo,
y objetivos en plantillas rivales (cláusulas).
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any

import config


def _money(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def budget_fit(cost: float | None, balance: float, *, min_cost: float | None = None) -> str:
    """comfortable|tight|stretch|blocked según saldo real."""
    bal = max(0.0, float(balance or 0))
    if cost is None:
        return "blocked"
    c = float(cost)
    if c <= 0:
        return "comfortable"
    if c > bal:
        if min_cost is not None and float(min_cost) <= bal:
            return "stretch"
        return "blocked"
    if c <= bal * 0.40:
        return "comfortable"
    return "tight"


def target_tier_from_budget_fit(bf: str | None) -> str:
    """realistic | stretch | aspirational."""
    if bf in ("comfortable", "tight", "funding"):
        # funding = venta para liberar caja del día (no es objetivo aspiracional)
        return "realistic"
    if bf == "stretch":
        return "stretch"
    return "aspirational"


def _ext_avail(p: dict[str, Any]) -> str:
    ext = p.get("external") or {}
    return ext.get("availability") or ("injured" if p.get("injury") else "unknown")


def _lineup_pct(p: dict[str, Any]) -> float | None:
    ext = p.get("external") or {}
    if ext.get("lineup_prob_ext") is not None:
        try:
            return float(ext["lineup_prob_ext"])
        except (TypeError, ValueError):
            return None
    if p.get("lineup_prob") is not None:
        try:
            return float(p["lineup_prob"]) * 100.0
        except (TypeError, ValueError):
            return None
    return None


def _fotmob_rating(p: dict[str, Any]) -> float | None:
    fm = p.get("fotmob_stats") or {}
    if fm.get("rating_promedio") is not None:
        try:
            return float(fm["rating_promedio"])
        except (TypeError, ValueError):
            return None
    ext = p.get("external") or {}
    if ext.get("sofascore_avg_5") is not None:
        try:
            return float(ext["sofascore_avg_5"])
        except (TypeError, ValueError):
            return None
    return None


def _mister_avg(p: dict[str, Any] | None) -> float | None:
    if not p:
        return None
    for key in ("mister_avg", "form"):
        if p.get(key) is not None:
            try:
                return float(p[key])
            except (TypeError, ValueError):
                pass
    return None


def _mister_points(p: dict[str, Any] | None) -> float | None:
    if not p:
        return None
    if p.get("points") is None:
        return None
    try:
        return float(p["points"])
    except (TypeError, ValueError):
        return None


def _prior_avg(p: dict[str, Any] | None) -> float | None:
    if not p:
        return None
    if p.get("prior_avg") is not None:
        try:
            return float(p["prior_avg"])
        except (TypeError, ValueError):
            return None
    return None


def _ff_avg(p: dict[str, Any] | None) -> float | None:
    if not p:
        return None
    for key in ("ff_mister_avg", "ff_prior_avg"):
        if p.get(key) is not None:
            try:
                return float(p[key])
            except (TypeError, ValueError):
                pass
    ext = p.get("external") or {}
    for key in ("ff_mister_avg", "ff_prior_avg"):
        if ext.get(key) is not None:
            try:
                return float(ext[key])
            except (TypeError, ValueError):
                pass
    return None


def _production_score(p: dict[str, Any] | None) -> float | None:
    if not p:
        return None
    v = p.get("production_score")
    if v is None:
        v = (p.get("external") or {}).get("production_score")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _points_trend(p: dict[str, Any] | None) -> str:
    if not p:
        return "unknown"
    t = p.get("points_trend") or "unknown"
    return str(t)


def detect_points_phase(players: list[dict[str, Any]]) -> str:
    """active si ≥30% tiene points>0 o mister_avg>0; si no preseason."""
    if not players:
        return "preseason"
    n = 0
    hit = 0
    for p in players:
        n += 1
        pts = _mister_points(p) or 0
        avg = _mister_avg(p) or 0
        if pts > 0 or avg > 0:
            hit += 1
    if n > 0 and (hit / n) >= 0.30:
        return "active"
    return "preseason"


def detect_competition_phase(
    *,
    now: datetime | date | None = None,
    season_start: str | None = None,
    points_phase: str | None = None,
) -> dict[str, Any]:
    """
    Fase de campeonato por calendario + heurística de puntos.
    preseason (>RAMP días) | ramp (≤RAMP hasta J1) | active (desde J1 o puntos).
    """
    start_raw = (season_start or getattr(config, "SEASON_START_DATE", "2026-08-15")).strip()
    try:
        y, m, d = (int(x) for x in start_raw.split("-")[:3])
        kickoff = date(y, m, d)
    except (TypeError, ValueError):
        kickoff = date(2026, 8, 15)

    if now is None:
        today = datetime.now(timezone.utc).date()
    elif isinstance(now, datetime):
        today = now.date()
    else:
        today = now

    days = (kickoff - today).days
    ramp_n = int(getattr(config, "RAMP_DAYS_BEFORE_KICKOFF", 7))
    if points_phase == "active" or days <= 0:
        phase = "active"
    elif days <= ramp_n:
        phase = "ramp"
    else:
        phase = "preseason"

    return {
        "competition_phase": phase,
        "season_start": kickoff.isoformat(),
        "days_to_kickoff": days,
        "points_phase_heuristic": points_phase or "preseason",
    }


def _is_top_ff(p: dict[str, Any]) -> bool:
    if p.get("is_top_ff"):
        return True
    return bool((p.get("external") or {}).get("is_top_ff"))


def _is_star(p: dict[str, Any]) -> bool:
    """Estrella / pieza de once: TOP FF, o titular con buen rating/media."""
    if _is_top_ff(p):
        return True
    lineup = _lineup_pct(p)
    rating = _fotmob_rating(p)
    avg = _mister_avg(p)
    prod = _production_score(p)
    ff = _ff_avg(p)
    if lineup is not None and lineup >= 80:
        if rating is not None and rating >= 7.0:
            return True
        if avg is not None and avg >= 5.0:
            return True
        if ff is not None and ff >= 5.0:
            return True
        if prod is not None and prod >= 58:
            return True
    return False


def _is_reliable_starter(p: dict[str, Any]) -> bool:
    """Titular real (alineación ≥70%). Ignora el once fantasy de Mister."""
    if _ext_avail(p) in ("injured", "suspended") or p.get("injury"):
        return False
    lineup = _lineup_pct(p)
    return lineup is not None and lineup >= 70


def _recent_minutes(p: dict[str, Any]) -> float | None:
    fm = p.get("fotmob_stats") or {}
    if fm.get("minutos_ultimos_5") is None:
        return None
    try:
        return float(fm["minutos_ultimos_5"])
    except (TypeError, ValueError):
        return None


def _plays_little(p: dict[str, Any]) -> bool:
    """Poca titularidad real o minutos recientes bajos."""
    if _ext_avail(p) in ("injured", "suspended") or p.get("injury"):
        return True
    ext = p.get("external") or {}
    if p.get("gw_out") or ext.get("gw_out"):
        return True
    gw_prob = p.get("gw_lineup_prob")
    if gw_prob is None:
        gw_prob = ext.get("gw_lineup_prob")
    try:
        if gw_prob is not None and float(gw_prob) < 40:
            return True
    except (TypeError, ValueError):
        pass
    lineup = _lineup_pct(p)
    low_lp = getattr(config, "LINEUP_PROB_LOW", 0.40) * 100.0
    mins_low = getattr(config, "MINUTES_RECENT_LOW", 90)
    mins = _recent_minutes(p)
    if mins is not None and mins < mins_low:
        return True
    if lineup is not None and lineup < low_lp:
        # LP muy baja (<25%): juega poco aunque FotMob tenga minutos residuales
        if lineup < 25:
            return True
        if mins is not None and mins >= mins_low * 2:
            return False
        return True
    return False


def build_gw_xi_advice(
    squad: list[dict[str, Any]] | None,
    *,
    matchday: dict[str, Any] | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """
    Consejos de once para la jornada FF: start / sit / doubt.
    Solo jugadores de la plantilla con señal gw_*.
    """
    out: list[dict[str, Any]] = []
    jornada = (matchday or {}).get("jornada")
    for p in squad or []:
        ext = p.get("external") or {}
        prob = p.get("gw_lineup_prob")
        if prob is None:
            prob = ext.get("gw_lineup_prob")
        # Solo señal de jornada FF (no ficha/JP genérico)
        if prob is None and not (
            p.get("gw_starter")
            or p.get("gw_doubt")
            or p.get("gw_out")
            or ext.get("gw_starter")
            or ext.get("gw_doubt")
            or ext.get("gw_out")
        ):
            continue
        if prob is None:
            continue
        try:
            prob_f = float(prob)
        except (TypeError, ValueError):
            continue

        role = p.get("gw_role") or ext.get("gw_role")
        gw_starter = bool(p.get("gw_starter") or ext.get("gw_starter"))
        gw_doubt = bool(p.get("gw_doubt") or ext.get("gw_doubt"))
        gw_out = bool(p.get("gw_out") or ext.get("gw_out"))
        opponent = p.get("gw_opponent") or ext.get("gw_opponent")

        if gw_out or prob_f < 40:
            advice = "sit"
            why = f"FF jornada: {prob_f:.0f}% — mejor no alinear"
        elif gw_doubt or (40 <= prob_f < 70):
            advice = "doubt"
            why = f"FF jornada: {prob_f:.0f}% — duda de titularidad"
        elif gw_starter or prob_f >= 70:
            advice = "start"
            why = f"FF jornada: {prob_f:.0f}% titular probable"
        else:
            continue

        if opponent:
            why = f"{why} (vs {opponent})"
        if role == "bench" and advice == "start":
            why = f"{why}; aparece en banquillo FF pero con % alto"

        out.append(
            {
                "player_id": p.get("id"),
                "name": p.get("name"),
                "position": p.get("position"),
                "team": p.get("team"),
                "advice": advice,
                "prob": round(prob_f, 0),
                "role": role,
                "opponent": opponent,
                "jornada": jornada,
                "why": why,
                "in_lineup": bool(p.get("in_lineup")),
            }
        )

    order = {"sit": 0, "doubt": 1, "start": 2}
    out.sort(
        key=lambda x: (
            order.get(str(x.get("advice")), 9),
            -float(x.get("prob") or 0) if x.get("advice") == "start" else float(x.get("prob") or 0),
        )
    )
    return out[: max(1, limit)]


def _parse_formation(formation: str | None) -> dict[str, int]:
    """'1-4-4-2' / '4-3-3' → cupos por posición. Fallback IDEAL_XI."""
    default = dict(getattr(config, "IDEAL_XI", None) or {"GK": 1, "DF": 4, "MF": 3, "FW": 3})
    if not formation:
        return default
    parts = [p.strip() for p in str(formation).replace("–", "-").split("-") if p.strip()]
    nums: list[int] = []
    for p in parts:
        try:
            nums.append(int(p))
        except ValueError:
            return default
    if len(nums) == 4 and sum(nums) == 11:
        return {"GK": nums[0], "DF": nums[1], "MF": nums[2], "FW": nums[3]}
    if len(nums) == 3 and sum(nums) == 10:
        return {"GK": 1, "DF": nums[0], "MF": nums[1], "FW": nums[2]}
    return default


def _gw_prob_pct(p: dict[str, Any]) -> float | None:
    """Probabilidad de jugar esta jornada (0–100), si hay señal FF."""
    ext = p.get("external") or {}
    raw = p.get("gw_lineup_prob")
    if raw is None:
        raw = ext.get("gw_lineup_prob")
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    # Algunos payloads traen 0–1
    if 0.0 <= v <= 1.0:
        v *= 100.0
    return max(0.0, min(100.0, v))


def _xi_play_score(p: dict[str, Any]) -> tuple:
    """
    Ranking para el once de jornada desde plantilla propia.
    Prioriza señal GW; si no hay, titularidad habitual + producción.
    """
    gw = _gw_prob_pct(p)
    lp = _lineup_pct(p)
    avail = _ext_avail(p)
    injured = avail in ("injured", "suspended") or bool(p.get("injury"))
    gw_out = bool(p.get("gw_out") or (p.get("external") or {}).get("gw_out"))

    if injured or gw_out:
        play = -50.0
        signal = "out"
    elif gw is not None:
        play = float(gw)
        if gw >= 70:
            signal = "start"
        elif gw >= 40:
            signal = "doubt"
        else:
            signal = "sit"
            play = gw - 25.0  # castigo fuerte si FF dice fuera
    elif lp is not None:
        play = float(lp) * 0.85  # sin señal jornada: un poco menos peso
        signal = "start" if lp >= 70 else ("doubt" if lp >= 40 else "sit")
    else:
        play = 25.0  # desconocido: relleno solo si hace falta
        signal = "unknown"

    avg = _mister_avg(p) or 0.0
    pts = 0.0
    try:
        pts = float(p.get("ff_mister_points") or p.get("points") or 0)
    except (TypeError, ValueError):
        pts = 0.0
    rating = _fotmob_rating(p) or 0.0
    # Score: probabilidad efectiva + desempates de calidad
    score = play + min(12.0, avg * 1.2) + min(8.0, pts / 40.0) + min(5.0, rating)
    return (score, play, signal, gw, lp, injured)


def build_recommended_gw_xi(
    squad: list[dict[str, Any]] | None,
    *,
    formation: str | None = None,
    matchday: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Once recomendado (11) para la siguiente jornada usando SOLO la plantilla actual.
    Formación Mister (p.ej. 1-4-4-2) o IDEAL_XI. Ranking por % jornada FF + fallback titularidad.
    """
    shape = _parse_formation(formation)
    jornada = (matchday or {}).get("jornada")
    fixtures = (matchday or {}).get("fixtures_count")

    scored: list[dict[str, Any]] = []
    for p in squad or []:
        pos = str(p.get("position") or "").upper()
        if pos not in ("GK", "DF", "MF", "FW"):
            continue
        score, play, signal, gw, lp, injured = _xi_play_score(p)
        scored.append(
            {
                "player": p,
                "position": pos,
                "score": score,
                "play": play,
                "signal": signal,
                "gw": gw,
                "lp": lp,
                "injured": injured,
            }
        )

    by_pos: dict[str, list[dict[str, Any]]] = {"GK": [], "DF": [], "MF": [], "FW": []}
    for row in scored:
        by_pos[row["position"]].append(row)
    for pos in by_pos:
        by_pos[pos].sort(key=lambda x: (-x["score"], -float(x["gw"] or x["lp"] or 0)))

    picked_ids: set[str] = set()
    xi: list[dict[str, Any]] = []

    def _row_out(item: dict[str, Any], *, slot: str, role: str = "xi") -> dict[str, Any]:
        p = item["player"]
        gw = item["gw"]
        lp = item["lp"]
        signal = item["signal"]
        opponent = p.get("gw_opponent") or (p.get("external") or {}).get("gw_opponent")
        if item["injured"]:
            why = "Lesionado/sancionado — solo si no hay alternativa"
        elif gw is not None:
            why = f"FF jornada {gw:.0f}%"
            if opponent:
                why = f"{why} vs {opponent}"
        elif lp is not None:
            why = f"Titularidad habitual {lp:.0f}% (sin previa FF)"
        else:
            why = "Sin % — mejor disponible en plantilla"
        if signal == "doubt" and role == "xi":
            why = f"Duda · {why}"
        elif signal == "sit" and role == "xi":
            why = f"Riesgo bajo % · {why}"
        return {
            "slot": slot,
            "player_id": p.get("id"),
            "name": p.get("name"),
            "position": item["position"],
            "team": p.get("team"),
            "role": role,
            "signal": signal,
            "prob": round(gw, 0) if gw is not None else (round(lp, 0) if lp is not None else None),
            "prob_source": "gw" if gw is not None else ("season" if lp is not None else None),
            "opponent": opponent,
            "in_lineup": bool(p.get("in_lineup")),
            "injured": bool(item["injured"]),
            "score": round(item["score"], 1),
            "why": why,
            "jornada": jornada,
        }

    # 1) Cubrir cupos con mejores por posición (evitar out/lesión si hay alternativa)
    for pos in ("GK", "DF", "MF", "FW"):
        need = int(shape.get(pos, 0))
        pool = by_pos.get(pos) or []
        preferred = [x for x in pool if not x["injured"] and x["signal"] != "out"]
        fallback = [x for x in pool if x not in preferred]
        ordered = preferred + fallback
        n = 0
        for item in ordered:
            if n >= need:
                break
            pid = str(item["player"].get("id") or "")
            if not pid or pid in picked_ids:
                continue
            n += 1
            picked_ids.add(pid)
            xi.append(_row_out(item, slot=f"{pos}{n}", role="xi"))

    # 2) Si faltan plazas (plantilla corta en una línea), rellenar con cualquier campo sobrante
    total_need = sum(int(shape.get(p, 0)) for p in ("GK", "DF", "MF", "FW"))
    if len(xi) < total_need:
        leftovers = [
            x
            for x in scored
            if str(x["player"].get("id") or "") not in picked_ids and not x["injured"]
        ]
        leftovers.sort(key=lambda x: (-x["score"], 0 if x["position"] != "GK" else 1))
        while len(xi) < total_need and leftovers:
            item = leftovers.pop(0)
            pid = str(item["player"].get("id") or "")
            if pid in picked_ids:
                continue
            picked_ids.add(pid)
            pos = item["position"]
            n = sum(1 for r in xi if r["position"] == pos) + 1
            xi.append(_row_out(item, slot=f"{pos}{n}", role="xi"))

    # Orden visual GK→DF→MF→FW
    order_pos = {"GK": 0, "DF": 1, "MF": 2, "FW": 3}
    xi.sort(key=lambda r: (order_pos.get(str(r.get("position")), 9), str(r.get("slot") or "")))

    bench: list[dict[str, Any]] = []
    for item in scored:
        pid = str(item["player"].get("id") or "")
        if pid in picked_ids:
            continue
        if item["injured"] and item["signal"] == "out":
            continue
        bench.append(_row_out(item, slot=item["position"], role="bench"))
    bench.sort(key=lambda r: -float(r.get("score") or 0))
    bench = bench[:6]

    signals = {"start": 0, "doubt": 0, "sit": 0, "unknown": 0, "out": 0}
    gw_n = 0
    for r in xi:
        sig = str(r.get("signal") or "unknown")
        if sig in signals:
            signals[sig] += 1
        else:
            signals["unknown"] += 1
        if r.get("prob_source") == "gw":
            gw_n += 1

    form_label = formation or "-".join(
        str(shape[p]) for p in ("GK", "DF", "MF", "FW") if shape.get(p) is not None
    )
    # Etiqueta tipo fútbol: sin el 1 de GK si formation era 4-4-2
    if formation and str(formation).count("-") == 2:
        form_label = str(formation)
    elif formation:
        form_label = str(formation)

    return {
        "jornada": jornada,
        "fixtures_count": fixtures,
        "formation": form_label,
        "shape": shape,
        "xi": xi,
        "bench": bench,
        "summary": {
            "xi_count": len(xi),
            "xi_target": total_need,
            "complete": len(xi) >= total_need,
            "with_gw_signal": gw_n,
            "signals": signals,
        },
    }


def _is_useful_patch(p: dict[str, Any]) -> bool:
    """Parche barato que juega de verdad: no vender salvo emergencia."""
    if _ext_avail(p) in ("injured", "suspended") or p.get("injury"):
        return False
    price = _money(p.get("price") or p.get("market_value"))
    if price <= 0 or price > 2_000_000:
        return False
    lineup = _lineup_pct(p)
    return lineup is not None and lineup >= 45


def _starter_floor(position: str) -> int:
    return {"GK": 1, "DF": 3, "MF": 3, "FW": 2}.get(position or "MF", 2)


def _healthy_count(squad: list[dict[str, Any]], position: str) -> int:
    n = 0
    for p in squad:
        if (p.get("position") or "") != position:
            continue
        avail = _ext_avail(p)
        if avail in ("injured", "suspended"):
            continue
        if p.get("injury"):
            continue
        n += 1
    return n


def _starter_count(squad: list[dict[str, Any]], position: str) -> int:
    return sum(
        1
        for p in squad
        if (p.get("position") or "") == position and _is_reliable_starter(p)
    )


def starters_after_sale(squad: list[dict[str, Any]], player: dict[str, Any]) -> int:
    pos = player.get("position") or "MF"
    others = [p for p in squad if str(p.get("id")) != str(player.get("id"))]
    return _starter_count(others, pos)


def protect_depth_if_sold(squad: list[dict[str, Any]], player: dict[str, Any]) -> bool:
    """
    True si vender deja la línea fina: cupo sano bajo MIN_* o titulares reales
    por debajo del suelo del once.

    Vender poco usados (no starter/regular) no cuenta como riesgo de once.
    """
    lineup = _lineup_pct(player)
    regular_lp = getattr(config, "LINEUP_PROB_REGULAR", 0.45) * 100.0
    is_regular = _is_reliable_starter(player) or (
        lineup is not None and lineup >= regular_lp
    )
    if not is_regular:
        return False
    pos = player.get("position") or "MF"
    others = [p for p in squad if str(p.get("id")) != str(player.get("id"))]
    healthy = _healthy_count(others, pos)
    starters = _starter_count(others, pos)
    min_need = _mins().get(pos, 2)
    if healthy < min_need:
        return True
    if starters < _starter_floor(pos):
        return True
    return False


def _xi_impact_if_sold(squad: list[dict[str, Any]], player: dict[str, Any]) -> str:
    """
    safe  → el once/línea aguanta sin él
    risk  → vendería un titular dejando la línea justa
    soft  → cobertura sana pero pierdes un regular
    """
    pos = player.get("position") or "MF"
    mins = _mins()
    min_need = mins.get(pos, 2)
    others = [p for p in squad if str(p.get("id")) != str(player.get("id"))]
    healthy = _healthy_count(others, pos)
    starters = _starter_count(others, pos)
    was_starter = _is_reliable_starter(player)
    starter_floor = _starter_floor(pos)

    if healthy < min_need:
        return "risk"
    if was_starter and starters < starter_floor:
        return "risk"
    if protect_depth_if_sold(squad, player):
        return "risk"
    if was_starter and starters < starter_floor + 1:
        return "soft"
    return "safe"


def _mins() -> dict[str, int]:
    return {
        "GK": config.MIN_GK,
        "DF": config.MIN_DF,
        "MF": config.MIN_MF,
        "FW": config.MIN_FW,
    }


_GAP_FALLBACK_COST = {
    "GK": 1_500_000.0,
    "DF": 3_000_000.0,
    "MF": 3_000_000.0,
    "FW": 4_000_000.0,
}


def estimate_gap_funding(
    structural_needs: list[dict[str, Any]] | None,
    market_opportunities: list[dict[str, Any]] | None,
    balance: float,
    *,
    top_n: int = 3,
) -> dict[str, Any]:
    """
    Estima coste mínimo para cubrir needs Alta (multi-carencia).
    Prefiere candidatos asequibles (realistic) del pool; si no hay, marca shortfall.
    funding_target = suma de hasta top_n gaps; shortfall vs saldo.
    """
    needs = [n for n in (structural_needs or []) if n.get("priority") == "Alta"]
    market = list(market_opportunities or [])
    bal = max(0.0, float(balance or 0))

    gap_rows: list[dict[str, Any]] = []
    seen_pos: set[str] = set()

    for need in needs:
        pos = need.get("position")
        key = str(pos or need.get("need") or "")
        if not key or key in seen_pos:
            continue
        seen_pos.add(key)

        min_need_price = need.get("min_price")
        max_need_price = need.get("max_price")
        try:
            floor = float(min_need_price) if min_need_price is not None else None
        except (TypeError, ValueError):
            floor = None
        try:
            ceil = float(max_need_price) if max_need_price is not None else None
        except (TypeError, ValueError):
            ceil = None

        affordable_costs: list[float] = []
        any_costs: list[float] = []
        for o in market:
            opos = o.get("position")
            if pos and opos != pos:
                continue
            if not pos and need.get("need") == "patch_cheap":
                price = _money(o.get("puja_recomendada") or o.get("price"))
                if price <= 0 or price > float(ceil or 2_000_000):
                    continue
            elif pos:
                # Preferir quien cubre estructuralmente o tiene titularidad usable
                lp = None
                ext = o.get("external") or {}
                if ext.get("lineup_prob_ext") is not None:
                    try:
                        lp = float(ext["lineup_prob_ext"])
                    except (TypeError, ValueError):
                        lp = None
                elif o.get("lineup_prob") is not None:
                    try:
                        lp = float(o["lineup_prob"]) * 100.0
                    except (TypeError, ValueError):
                        lp = None
                fills = bool(o.get("fills_structural") or o.get("fills_need"))
                if not fills and lp is not None and lp < 45:
                    continue
                # Muestra corta: no anclar funding a medias poco fiables
                if o.get("sample_thin"):
                    continue
                price = _money(o.get("puja_recomendada") or o.get("price"))
                if price <= 0:
                    continue
                if floor is not None and price < floor * 0.5:
                    continue
                if ceil is not None and price > ceil:
                    continue
            else:
                continue
            price = _money(o.get("puja_recomendada") or o.get("price"))
            any_costs.append(price)
            tier = o.get("target_tier") or target_tier_from_budget_fit(
                o.get("budget_fit") or budget_fit(price, bal, min_cost=price)
            )
            if tier == "realistic" or price <= bal:
                affordable_costs.append(price)

        no_affordable = False
        if affordable_costs:
            cost = min(affordable_costs)
        elif any_costs:
            # Hay candidatos pero todos fuera de caja → shortfall explícito
            cost = min(any_costs)
            no_affordable = True
        elif floor is not None:
            cost = floor
            no_affordable = floor > bal
        elif ceil is not None:
            cost = min(ceil, _GAP_FALLBACK_COST.get(str(pos or ""), 2_000_000.0))
            no_affordable = cost > bal
        else:
            cost = _GAP_FALLBACK_COST.get(str(pos or ""), 2_000_000.0)
            no_affordable = cost > bal

        gap_rows.append(
            {
                "position": pos,
                "need": need.get("need"),
                "cost": cost,
                "label": need.get("reason") or need.get("need") or (pos or "gap"),
                "no_affordable_candidate": no_affordable,
            }
        )

    # Priorizar carencias más caras / críticas primero en el target (tope top_n)
    gap_rows.sort(key=lambda g: -float(g.get("cost") or 0))
    selected = gap_rows[: max(1, top_n)] if gap_rows else []
    funding_target = sum(float(g["cost"]) for g in selected)
    funding_shortfall = max(0.0, funding_target - bal)
    cheapest = min((float(g["cost"]) for g in gap_rows), default=None)
    cash_tight = funding_shortfall > 0 or (cheapest is not None and bal < cheapest)
    if any(g.get("no_affordable_candidate") for g in selected):
        cash_tight = True

    return {
        "funding_target": funding_target,
        "funding_shortfall": funding_shortfall,
        "cash_tight": cash_tight,
        "gap_costs": selected,
        "all_gap_costs": gap_rows,
        "positions": [g.get("position") for g in selected if g.get("position")],
        "cheapest_need": cheapest,
    }


def other_gaps_min_cost(
    funding_info: dict[str, Any] | None,
    *,
    exclude_position: str | None = None,
) -> float:
    """Suma de costes mínimos de hasta 3 gaps Alta distintos a la posición del fichaje."""
    info = funding_info or {}
    others = [
        float(g.get("cost") or 0)
        for g in (info.get("all_gap_costs") or info.get("gap_costs") or [])
        if not (exclude_position and g.get("position") == exclude_position)
    ]
    others.sort(reverse=True)
    return sum(others[:3])


def rival_demand_for_position(
    rivals: list[dict[str, Any]],
    position: str,
    *,
    market_mode: str = "auction",
) -> list[dict[str, Any]]:
    if (market_mode or "auction") == "fixed":
        return []
    demand = []
    for r in rivals:
        gaps = r.get("position_gaps") or []
        if position in gaps:
            demand.append({
                "team_id": r.get("team_id"),
                "team_name": r.get("team_name"),
                "rank": r.get("rank"),
            })
    return demand


def wait_risk(
    o: dict[str, Any],
    rivals: list[dict[str, Any]],
    *,
    fills_need: bool,
    market_mode: str = "auction",
) -> str:
    if (market_mode or "auction") == "fixed":
        return "low"
    demand = rival_demand_for_position(rivals, o.get("position") or "", market_mode=market_mode)
    top_demand = sum(1 for d in demand if int(d.get("rank") or 99) <= 3)
    score = 0
    score += min(3, len(demand))
    score += min(2, top_demand)
    if o.get("trend") == "up" or (o.get("delta_5d") is not None and float(o["delta_5d"]) > 0.05):
        score += 1
    if o.get("external", {}).get("is_chollo_ext") or o.get("category") == "chollo_economico":
        score += 1
    if fills_need:
        score += 1
    if score >= 5:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


def priority_score_buy(item: dict[str, Any]) -> int:
    score = 0
    # Jugador clave en mercado del día: por encima de cualquier parche
    if item.get("is_key_market") and _is_daily_market_item(item):
        score += 140
    elif item.get("is_primary_target") and _is_daily_market_item(item):
        score += 100
    elif item.get("is_board_objective") and _is_daily_market_item(item):
        score += 55
    if item.get("fills_need"):
        score += 35
    if item.get("fills_structural"):
        score += 20
    if item.get("fills_coverage_gap"):
        score += 22
    if item.get("on_daily_market") and item.get("fills_coverage_gap"):
        score += 10
    if item.get("line_already_covered") and not item.get("is_upgrade"):
        score -= 40
    elif item.get("is_upgrade"):
        score += 15
    risk = item.get("wait_risk") or item.get("risk") or "low"
    score += {"high": 25, "medium": 15, "low": 5}.get(str(risk), 5)
    bf = item.get("budget_fit") or "blocked"
    score += {"comfortable": 20, "tight": 10, "stretch": 0, "blocked": -40, "funding": 8}.get(str(bf), 0)
    tier = item.get("target_tier") or target_tier_from_budget_fit(str(bf))
    if tier == "aspirational":
        score -= 15
    elif tier == "stretch":
        score -= 5
    if item.get("crowds_out_gaps"):
        score -= 55
        cost = _money(item.get("cost") or item.get("bid") or item.get("price"))
        if cost >= 8_000_000:
            score -= 20  # caro que aprieta gaps: no apilar en el paquete
    elif item.get("leaves_gap_budget"):
        score += 18
    score += min(15, int(item.get("rival_demand") or 0) * 5)
    if item.get("improves_owned"):
        score += 20
    # Producción FF / Mister (castiga muestra corta)
    sample_thin = bool(item.get("sample_thin"))
    if sample_thin:
        score -= 8
    prod = _production_score(item)
    if prod is not None:
        score += int(min(25, prod / 4))
        if prod < 35:
            score -= 10
    ff = _ff_avg(item)
    if ff is not None and not sample_thin:
        score += int(min(12, ff * 1.5))
        if ff < 3.5 and _money(item.get("price") or item.get("market_value")) >= 5_000_000:
            score -= 12
    if (item.get("is_top_ff") or (item.get("external") or {}).get("is_top_ff")) and not sample_thin:
        score += 8
    # Capa puntos adicional cuando discrimina
    avg = item.get("mister_avg")
    try:
        if avg is not None and float(avg) > 0 and not sample_thin:
            score += min(15, int(float(avg) * 2))
    except (TypeError, ValueError):
        pass
    trend = item.get("points_trend") or item.get("trend")
    if trend == "up":
        score += 8
    elif trend == "down":
        score -= 6
    # Capacidad de trueque / activo revendible
    score += int(min(20, trade_asset_score(item) / 2.5))
    return score


def trade_asset_score(item: dict[str, Any]) -> float:
    """
    Valor como activo de trueque/mejora futura:
    producción por millón (acotada) + flecha de precio + categorías chollo/trading.
    """
    cost = _money(item.get("cost") or item.get("bid") or item.get("price") or item.get("market_value"))
    # Suelo 0.8M para no inflar chollos de 200k por encima de cracks
    price_m = max(cost / 1_000_000.0, 0.8)
    prod = _production_score(item) or 0.0
    ff = _ff_avg(item) or 0.0
    score = min(36.0, float(prod) / price_m) + (float(ff) * 2.0)
    delta = item.get("delta_5d")
    try:
        if delta is not None:
            d = float(delta)
            if d >= 0.05:
                score += 10.0
            elif d >= 0.02:
                score += 5.0
            elif d <= -0.06:
                score -= 8.0
            elif d < 0:
                score -= 3.0
    except (TypeError, ValueError):
        pass
    cats = item.get("categories") or []
    if isinstance(cats, list):
        if "chollo_economico" in cats:
            score += 6.0
        if "especulacion_trading" in cats:
            score += 5.0
        if "titular_garantizado" in cats:
            score += 3.0
    if item.get("is_upgrade"):
        score += 4.0
    if item.get("sample_thin"):
        score -= 6.0
    return round(score, 1)


def is_key_market_candidate(
    o: dict[str, Any],
    *,
    is_primary_obj: bool,
    is_objective: bool,
    on_daily: bool,
    gw_out: bool,
    real_starter: bool,
    fills_gap: bool,
) -> bool:
    """
    Jugador clave del mercado de hoy: objetivo del ideal, o crack/top que cubre hueco.
    """
    if not on_daily or gw_out:
        return False
    if is_primary_obj:
        return True
    if is_objective and (real_starter or bool(o.get("is_top_ff"))):
        return True
    if not fills_gap:
        return False
    if not real_starter and not o.get("is_top_ff"):
        return False
    prod = _production_score(o)
    ff = _ff_avg(o)
    try:
        pts = float(o.get("ff_mister_points") or 0)
    except (TypeError, ValueError):
        pts = 0.0
    if o.get("is_top_ff") and not o.get("sample_thin"):
        return True
    if prod is not None and float(prod) >= 65 and not o.get("sample_thin"):
        return True
    if ff is not None and float(ff) >= 6.0 and not o.get("sample_thin"):
        return True
    if pts >= 120 and real_starter:
        return True
    return False


def priority_score_sell(item: dict[str, Any]) -> int:
    """
    Prioriza ventas que mejoran el once: banquillo caro, baja producción,
    liberar caja; protege titulares TOP / once fiable.
    """
    score = 0
    reason = item.get("sell_reason") or ""
    score += {
        "expensive_bench": 42,
        "low_minutes": 40,
        "low_production": 36,
        "fund_buy": 32,
        "fund_target": 44,
        "injured_covered": 28,
        "surplus_to_demand": 22,
        "form_drop": 16,
    }.get(reason, 10)

    if item.get("budget_fit") == "funding":
        score += 12
    demand = int(item.get("rival_demand") or 0)
    score += min(12, demand * 4)

    sell_risk = item.get("sell_risk") or item.get("wait_risk") or "medium"
    if sell_risk == "high":
        score -= 18
    elif sell_risk == "low":
        score += 5

    xi = item.get("xi_impact") or "soft"
    if xi == "safe":
        score += 10
    elif xi == "risk":
        score -= 30

    lineup = item.get("lineup_pct")
    try:
        lp = float(lineup) if lineup is not None else None
    except (TypeError, ValueError):
        lp = None
    if lp is not None:
        if lp < 40:
            score += 18
        elif lp < 60:
            score += 10
        elif lp >= 80:
            score -= 22

    prod = item.get("production_score")
    try:
        pr = float(prod) if prod is not None else None
    except (TypeError, ValueError):
        pr = None
    if pr is not None:
        if pr < 35:
            score += 16
        elif pr < 45:
            score += 8
        elif pr >= 60:
            score -= 16

    ff = item.get("ff_mister_avg")
    try:
        ffv = float(ff) if ff is not None else None
    except (TypeError, ValueError):
        ffv = None
    if ffv is not None:
        if ffv < 3.5:
            score += 10
        elif ffv >= 5.2:
            score -= 12

    price = _money(item.get("price"))
    if price >= 5_000_000 and (lp is None or lp < 65):
        score += 12  # dinero estancado fuera del once
    if price >= 8_000_000 and (pr is not None and pr < 45):
        score += 8
    if item.get("plays_little"):
        score += 14
        if price >= 4_000_000:
            score += 8

    if item.get("is_top_ff") or item.get("keep_if_rank_top"):
        score -= 40
    if item.get("is_useful_patch"):
        score -= 20  # conservar versatilidad / fondo de armario

    return int(score)


def priority_score_clause(item: dict[str, Any]) -> int:
    score = priority_score_buy(item)
    if item.get("clause_known"):
        score += 10
    owner_rank = int(item.get("owner_rank") or 99)
    if owner_rank <= 3:
        score += 10
    return score


def build_sell_opportunities(
    me: dict[str, Any],
    diagnosis: dict[str, Any],
    rivals: list[dict[str, Any]],
    *,
    price_series: dict[str, list[float]] | None = None,
    delta_fn=None,
    market_opportunities: list[dict[str, Any]] | None = None,
    points_phase: str = "preseason",
    diagnostico_plantilla: dict[str, Any] | None = None,
    target_board: dict[str, Any] | None = None,
    funding_info: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Ventas orientadas a estrategia Fantasy:
    máxima puntuación, titularidades, once fiable y no estancar valor en banquillo.
    """
    price_series = price_series or {}
    squad = list(me.get("squad") or [])
    balance = _money(me.get("balance"))
    rank = int(me.get("rank") or 99)
    mins = _mins()
    diag = diagnostico_plantilla or {}
    finance = diag.get("financiero") or {}
    bench_info = finance.get("bench_inflated") or {}
    bench_inflated = bool(
        bench_info.get("status") == "alert" or bench_info.get("ok") is False
    )
    # Solo usar la lista del diagnóstico si el banquillo está realmente inflado
    expensive_bench_ids = (
        {
            str(p.get("id"))
            for p in (bench_info.get("players") or [])
            if p.get("id") is not None
        }
        if bench_inflated
        else set()
    )

    critical_pos = {
        a["position"] for a in diagnosis.get("alerts", []) if a.get("level") == "critical"
    }
    needy = {
        pos for pos, info in diagnosis.get("by_position", {}).items()
        if info.get("status") in ("critical", "warning")
    }
    funding = funding_info or estimate_gap_funding(
        diag.get("structural_needs") or [],
        market_opportunities,
        balance,
        top_n=3,
    )
    cash_tight = bool(funding.get("cash_tight"))
    funding_pressure = float(funding.get("funding_shortfall") or 0) > 0
    gap_labels = ", ".join(
        str(p) for p in (funding.get("positions") or []) if p
    ) or "carencias"

    sells: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: dict[str, Any]) -> None:
        pid = str(item["player_id"])
        if pid in seen:
            prev = next(x for x in sells if str(x["player_id"]) == pid)
            urg = {"high": 0, "medium": 1, "low": 2}
            # Preferir el motivo de mayor score estratégico
            if urg.get(item.get("urgency"), 9) < urg.get(prev.get("urgency"), 9):
                sells.remove(prev)
                seen.discard(pid)
            elif (item.get("_pref") or 0) > (prev.get("_pref") or 0):
                sells.remove(prev)
                seen.discard(pid)
            else:
                return
        seen.add(pid)
        sells.append(item)

    def base_item(p: dict[str, Any], *, reason: str, why: str, urgency: str, sell_risk: str) -> dict[str, Any]:
        pid = str(p.get("id") or "")
        pos = p.get("position") or "MF"
        price = _money(p.get("price") or p.get("market_value"))
        lineup = _lineup_pct(p)
        ff = _ff_avg(p)
        prod = _production_score(p)
        demand = rival_demand_for_position(rivals, pos)
        xi = _xi_impact_if_sold(squad, p)
        keep_top = rank <= 2 and _is_star(p) and _ext_avail(p) not in ("injured", "suspended")
        return {
            "player_id": pid,
            "name": p.get("name"),
            "position": pos,
            "action": "sell",
            "sell_reason": reason,
            "bid": None,
            "wait_risk": "low",
            "sell_risk": sell_risk,
            "urgency": urgency,
            "why": why,
            "rival_demand": len(demand),
            "rival_targets": demand[:3],
            "budget_fit": "funding" if cash_tight else "comfortable",
            "keep_if_rank_top": keep_top,
            "price": price,
            "lineup_pct": lineup,
            "ff_mister_avg": ff,
            "production_score": prod,
            "is_top_ff": _is_top_ff(p),
            "xi_impact": xi,
            "is_useful_patch": _is_useful_patch(p),
            "in_lineup": bool(p.get("in_lineup")),
            "plays_little": _plays_little(p),
            "recent_minutes": _recent_minutes(p),
            "funding_shortfall": funding.get("funding_shortfall"),
            "funding_target": funding.get("funding_target"),
        }

    squad_value = sum(_money(p.get("price") or p.get("market_value")) for p in squad) or 1.0

    for p in squad:
        pid = str(p.get("id") or "")
        if not pid:
            continue
        pos = p.get("position") or "MF"
        price = _money(p.get("price") or p.get("market_value"))
        avail = _ext_avail(p)
        lineup = _lineup_pct(p)
        rating = _fotmob_rating(p)
        healthy = _healthy_count(squad, pos)
        starters = _starter_count(squad, pos)
        min_need = mins.get(pos, 2)
        if avail in ("injured", "suspended"):
            covered_if_sold = healthy >= min_need
        else:
            covered_if_sold = healthy - 1 >= min_need

        demand = rival_demand_for_position(rivals, pos)
        top_demand = [d for d in demand if int(d.get("rank") or 99) <= 3]
        delta = None
        if delta_fn:
            delta = delta_fn(pid, price, price_series)

        is_star = _is_star(p)
        is_starter = _is_reliable_starter(p)
        plays_little = _plays_little(p)
        useful_patch = _is_useful_patch(p)
        keep_top = rank <= 2 and is_star and avail not in ("injured", "suspended")
        xi = _xi_impact_if_sold(squad, p)
        ff = _ff_avg(p)
        prod = _production_score(p)
        avg = _mister_avg(p)
        ptrend = _points_trend(p)
        recent_mins = _recent_minutes(p)
        protect_depth = protect_depth_if_sold(squad, p)
        starters_left = starters_after_sale(squad, p)

        # Solo proteger si es titular real y la línea quedaría bajo el suelo
        protect_xi = xi == "risk" and is_starter and avail not in ("injured", "suspended")
        # Conservar profundidad (titulares reales / cupo) salvo presión de caja multi-gap
        protect_depth_soft = protect_depth and not funding_pressure
        # Conservar parches útiles (versatilidad) salvo financiar crítico
        protect_patch = useful_patch and not (cash_tight and bool(critical_pos))

        # 1) Banquillo caro / valor estancado fuera del once real (≥3M o ≥7% plantilla)
        min_bench_price = max(3_000_000.0, squad_value * 0.07)
        bench_flag = (
            pid in expensive_bench_ids
            or (
                not is_starter
                and price >= min_bench_price
                and (prod is None or prod < 52)
                and not useful_patch
            )
        )
        if (
            bench_flag
            and covered_if_sold
            and not keep_top
            and not protect_xi
            and not protect_patch
            and not protect_depth_soft
            and price > 0
        ):
            urg = "high" if bench_inflated or price >= 6_000_000 or funding_pressure else "medium"
            bits = [
                f"fuera del once real (titularidad {int(lineup) if lineup is not None else '—'}%)",
                f"libera {price:,.0f} € para titulares/producción",
            ]
            if p.get("in_lineup"):
                bits.insert(0, "en tu once Mister pero sin titularidad real")
            if funding_pressure:
                bits.append(f"libera caja para {gap_labels}")
            if protect_depth and funding_pressure:
                bits.append(f"profundidad justa ({starters_left} titulares) pero falta caja")
            if ff is not None:
                bits.append(f"FF {ff:.1f}")
            item = base_item(
                p,
                reason="expensive_bench",
                why="; ".join(bits),
                urgency=urg,
                sell_risk="high" if protect_depth else ("low" if xi == "safe" else "medium"),
            )
            item["_pref"] = 50
            add(item)

        # 1b) Pocos minutos / titularidad baja a precio relevante
        if (
            plays_little
            and price >= 3_000_000
            and covered_if_sold
            and not keep_top
            and not protect_xi
            and not protect_patch
            and not protect_depth_soft
            and not is_star
        ):
            bits = []
            if lineup is not None:
                bits.append(f"titularidad {int(lineup)}%")
            if recent_mins is not None:
                bits.append(f"{int(recent_mins)}' en últimos partidos")
            if p.get("in_lineup"):
                bits.append("está en tu once fantasy")
            bits.append(f"libera {price:,.0f} €")
            if funding_pressure:
                bits.append(f"caja justa vs {gap_labels}")
            item = base_item(
                p,
                reason="low_minutes",
                why="; ".join(bits),
                urgency="high" if price >= 5_000_000 or funding_pressure else "medium",
                sell_risk="high" if protect_depth else ("low" if not is_starter else "medium"),
            )
            item["_pref"] = 48
            add(item)

        # 1c) No juega esta jornada (FF posibles alineaciones) — no vender TOP por duda media
        ext = p.get("external") or {}
        gw_out = bool(p.get("gw_out") or ext.get("gw_out"))
        gw_prob = p.get("gw_lineup_prob")
        if gw_prob is None:
            gw_prob = ext.get("gw_lineup_prob")
        try:
            gw_prob_f = float(gw_prob) if gw_prob is not None else None
        except (TypeError, ValueError):
            gw_prob_f = None
        if (
            gw_out
            and price >= 2_500_000
            and covered_if_sold
            and not keep_top
            and not protect_xi
            and not protect_patch
        ):
            opp = p.get("gw_opponent") or ext.get("gw_opponent")
            bits = [
                f"FF jornada: no juega / {int(gw_prob_f) if gw_prob_f is not None else 'bajo'}%"
            ]
            if opp:
                bits.append(f"vs {opp}")
            bits.append(f"libera {price:,.0f} € si hay mejor opción")
            item = base_item(
                p,
                reason="low_minutes",
                why="; ".join(bits),
                urgency="medium",
                sell_risk="medium" if is_starter else "low",
            )
            item["_pref"] = 46
            add(item)

        # 2) Baja producción a precio alto → reinvertir en puntos
        low_prod = False
        if price >= 4_000_000 and not is_star:
            if ff is not None and ff < 4.0:
                low_prod = True
            if prod is not None and prod < 42:
                low_prod = True
            if points_phase == "active" and avg is not None and avg < 4.2:
                low_prod = True
        if (
            low_prod
            and covered_if_sold
            and not keep_top
            and not protect_xi
            and not protect_patch
            and not protect_depth_soft
        ):
            bits = [f"producción floja para {price / 1e6:.1f} M€"]
            if ff is not None:
                bits.append(f"FF {ff:.1f}")
            if prod is not None:
                bits.append(f"score {prod:.0f}")
            if not is_starter:
                bits.append("no asegura titularidad real")
            else:
                bits.append("mejorable vs objetivo de máxima puntuación")
            item = base_item(
                p,
                reason="low_production",
                why="; ".join(bits),
                urgency="medium" if is_starter else "high",
                sell_risk="medium" if is_starter or protect_depth else "low",
            )
            item["_pref"] = 45
            add(item)

        # 3) Lesionado/sancionado con cobertura
        if avail in ("injured", "suspended") and covered_if_sold and price > 0:
            item = base_item(
                p,
                reason="injured_covered",
                why=f"{avail} con cobertura en {pos}; libera valor ({price:,.0f} €) sin romper el once.",
                urgency="medium",
                sell_risk="low",
            )
            item["_pref"] = 40
            add(item)

        # 4) Excedente → vender el de menor impacto (no titulares estrella)
        if (
            healthy > min_need
            and demand
            and not keep_top
            and not protect_xi
            and not protect_patch
            and not protect_depth_soft
        ):
            weak_surplus = (
                not is_starter
                or (lineup is not None and lineup < 75)
                or (prod is not None and prod < 50)
                or not is_star
            )
            starter_floor = _starter_floor(pos)
            starters_after = starters - (1 if is_starter else 0)
            if weak_surplus and starters_after >= starter_floor:
                urg = "high" if top_demand and not is_starter else "medium"
                names = ", ".join(
                    d["team_name"] for d in (top_demand or demand)[:2] if d.get("team_name")
                )
                item = base_item(
                    p,
                    reason="surplus_to_demand",
                    why=(
                        f"Excedente {pos} (sanos {healthy}/{min_need}, titulares {starters}); "
                        f"rivales con gap: {names or 'varios'}."
                    ),
                    urgency=urg,
                    sell_risk="low" if healthy > min_need + 1 and not is_starter else "medium",
                )
                item["_pref"] = 30
                add(item)

        # 5) Financiar carencias multi-gap (prioriza no titulares)
        if cash_tight and needy and covered_if_sold and not keep_top and not protect_xi:
            # Con presión de caja se puede vender aunque profundidad sea justa
            if protect_patch and not critical_pos and not funding_pressure:
                pass
            elif protect_depth_soft:
                pass
            else:
                essential = is_star or is_starter
                weak = (
                    (rating is not None and rating < 6.5)
                    or (lineup is not None and lineup < 75)
                    or not is_starter
                    or plays_little
                )
                if not essential or weak:
                    shortfall = float(funding.get("funding_shortfall") or 0)
                    item = base_item(
                        p,
                        reason="fund_buy",
                        why=(
                            f"Caja justa ({balance:,.0f} €) vs ~{funding.get('funding_target', 0):,.0f} € "
                            f"para {gap_labels}"
                            + (f" (faltan {shortfall:,.0f} €)" if shortfall else "")
                            + f"; vender libera ~{price:,.0f} €."
                        ),
                        urgency="high" if critical_pos or funding_pressure else "medium",
                        sell_risk=(
                            "high"
                            if protect_depth
                            else ("medium" if is_starter or healthy <= min_need + 1 else "low")
                        ),
                    )
                    item["_pref"] = 35
                    add(item)

        # 5b) Financiar primary del target board con mínima pérdida de valor
        primary_targets = list(funding.get("primary_targets") or [])
        if not primary_targets and target_board:
            primary_targets = list(target_board.get("primary_targets") or [])
        for pt in primary_targets:
            need_price = _money(pt.get("price"))
            if need_price <= 0:
                continue
            shortfall_pt = max(0.0, need_price - balance)
            if shortfall_pt <= 0:
                continue
            if not covered_if_sold or keep_top or protect_xi or protect_patch:
                continue
            if is_star or (is_starter and (prod is not None and prod >= 55)):
                continue
            # Preferir bajo EP / banquillo / Δ negativo (menor pérdida)
            low_ep = (prod is not None and prod < 45) or (ff is not None and ff < 3.8)
            delta_ok = delta is None or float(delta) <= 0.02  # no vender si está subiendo fuerte
            if not (low_ep or plays_little or not is_starter):
                continue
            if not delta_ok and price < 3_000_000:
                continue
            loss_note = (
                f"Δ {float(delta)*100:.0f}%"
                if delta is not None
                else "sin serie Δ"
            )
            item = base_item(
                p,
                reason="fund_target",
                why=(
                    f"Financia objetivo {pt.get('name')} (~{need_price:,.0f} €); "
                    f"faltan ~{shortfall_pt:,.0f} €; libera {price:,.0f} € ({loss_note})"
                ),
                urgency="high" if shortfall_pt >= need_price * 0.35 else "medium",
                sell_risk="low" if not is_starter else "medium",
            )
            item["_pref"] = 42
            item["funds_for"] = pt.get("player_id")
            item["funds_for_name"] = pt.get("name")
            add(item)
            break  # una venta fund_target por jugador owned basta vía add()

        # 6) Forma / tendencia a la baja
        form_bad = (lineup is not None and lineup < 50) or (rating is not None and rating < 6.0)
        if points_phase == "active":
            if avg is not None and avg < 4.0:
                form_bad = True
            if ptrend == "down":
                form_bad = True
        if price >= 4_000_000 and (
            (ff is not None and ff < 3.8) or (prod is not None and prod < 38)
        ):
            form_bad = True
        delta_bad = delta is not None and float(delta) <= -0.08
        if (
            covered_if_sold
            and not keep_top
            and not protect_xi
            and not protect_patch
            and not protect_depth_soft
            and price >= 2_000_000
            and (form_bad or delta_bad)
        ):
            bits = []
            if lineup is not None and lineup < 50:
                bits.append(f"titularidad {int(lineup)}%")
            if rating is not None and rating < 6.0:
                bits.append(f"nota {rating}")
            if points_phase == "active" and avg is not None and avg < 4.0:
                bits.append(f"media Mister {avg}")
            if ff is not None and ff < 3.8 and price >= 4_000_000:
                bits.append(f"FF Mister Mixto {ff:.1f} baja para {price/1e6:.1f}M€")
            if ptrend == "down":
                bits.append("tendencia pts ↓")
            if delta_bad:
                bits.append(f"Δprecio {float(delta)*100:.1f}%")
            item = base_item(
                p,
                reason="form_drop",
                why="; ".join(bits) or "Forma/valor a la baja.",
                urgency="low",
                sell_risk="medium",
            )
            item["_pref"] = 20
            add(item)

    for s in sells:
        s.pop("_pref", None)
        s["priority_score"] = priority_score_sell(s)
    sells.sort(
        key=lambda x: (
            -int(x.get("priority_score") or 0),
            0 if x.get("xi_impact") == "safe" else 1,
            -_money(x.get("price")),
        )
    )
    return sells[:8]


def _best_owned_reference(
    squad: list[dict[str, Any]],
    position: str,
    *,
    points_phase: str = "preseason",
) -> dict[str, Any] | None:
    candidates = [p for p in squad if (p.get("position") or "") == position]
    if not candidates:
        return None

    def key(p: dict[str, Any]) -> tuple:
        avail = _ext_avail(p)
        healthy = 0 if avail in ("injured", "suspended") or p.get("injury") else 1
        avg = _mister_avg(p) or 0
        pts = _mister_points(p) or 0
        lineup = _lineup_pct(p) or 0
        rating = _fotmob_rating(p) or 0
        prior = _prior_avg(p) or 0
        price = _money(p.get("price") or p.get("market_value"))
        if points_phase == "active":
            return (healthy, avg, pts, lineup, rating, price)
        return (healthy, lineup, rating, prior, avg, price)

    return max(candidates, key=key)


def compute_upgrade_score(
    cand: dict[str, Any],
    ref: dict[str, Any] | None,
    *,
    points_phase: str,
    fills: bool,
    critical_elsewhere: bool,
    balance: float,
    acquisition: float | None,
) -> tuple[float, bool, list[str]]:
    """
    Score adaptativo + improves_owned.
    Puntos son capa adicional (peso ~0 si ambos a 0).
    """
    score = 0.0
    why_extra: list[str] = []
    cand_avg = _mister_avg(cand)
    ref_avg = _mister_avg(ref) if ref else None
    cand_pts = _mister_points(cand)
    ref_pts = _mister_points(ref) if ref else None
    cand_lineup = _lineup_pct(cand)
    ref_lineup = _lineup_pct(ref) if ref else None
    cand_rating = _fotmob_rating(cand)
    ref_rating = _fotmob_rating(ref) if ref else None
    cand_prior = _prior_avg(cand)
    ref_prior = _prior_avg(ref) if ref else None
    ref_avail = _ext_avail(ref) if ref else "unknown"
    market_value = _money(cand.get("market_value") or cand.get("price"))
    ref_price = _money((ref or {}).get("price") or (ref or {}).get("market_value")) if ref else 0
    ptrend = _points_trend(cand)

    if fills:
        score += 28
        why_extra.append("carencia")
    if critical_elsewhere and not fills:
        score -= 22

    # Titularidad
    if cand_lineup is not None and ref_lineup is not None:
        score += (cand_lineup - ref_lineup) / (8.0 if points_phase == "preseason" else 12.0)
    elif cand_lineup is not None and points_phase == "preseason":
        score += cand_lineup / 20.0

    # FotMob: más peso en preseason
    fotmob_w = 10.0 if points_phase == "preseason" else 4.0
    if cand_rating is not None and ref_rating is not None:
        score += (cand_rating - ref_rating) * fotmob_w
    elif cand_rating is not None and points_phase == "preseason":
        score += 6

    # Temporada anterior (valioso en preseason)
    if points_phase == "preseason" and cand_prior is not None:
        if ref_prior is not None:
            score += (cand_prior - ref_prior) * 8
        else:
            score += min(10, cand_prior)
        why_extra.append("prior season")

    # Producción Fútbol Fantasy Mister Mixto
    cand_ff = _ff_avg(cand)
    ref_ff = _ff_avg(ref) if ref else None
    cand_prod = _production_score(cand)
    ref_prod = _production_score(ref) if ref else None
    ff_w = 14.0 if points_phase == "preseason" else 6.0
    if cand_ff is not None and ref_ff is not None:
        score += (cand_ff - ref_ff) * ff_w
        why_extra.append(f"FF {cand_ff:.1f} vs {ref_ff:.1f}")
    elif cand_ff is not None:
        score += min(16, cand_ff * 2.2)
        why_extra.append(f"FF media {cand_ff:.1f}")
    if cand_prod is not None and ref_prod is not None:
        score += (cand_prod - ref_prod) / 8.0
    elif cand_prod is not None and points_phase == "preseason":
        score += cand_prod / 12.0

    # Capa puntos actual — solo si discrimina
    points_signal = False
    if (cand_avg or 0) > 0 or (ref_avg or 0) > 0:
        points_signal = True
        ca = cand_avg or 0.0
        ra = ref_avg or 0.0
        w = 20.0 if points_phase == "active" else 8.0
        score += (ca - ra) * w
        why_extra.append(f"media {ca:.1f} vs {ra:.1f}")
    if (cand_pts or 0) > 0 or (ref_pts or 0) > 0:
        points_signal = True
        score += ((cand_pts or 0) - (ref_pts or 0)) / (8.0 if points_phase == "active" else 20.0)

    if ptrend == "up":
        score += 10 if points_phase == "active" else 4
    elif ptrend == "down":
        score -= 10 if points_phase == "active" else 3

    if ref_avail in ("injured", "suspended"):
        score += 14

    # Efficiency coste
    cost = acquisition if acquisition and acquisition > 0 else market_value
    if cost > 0 and score > 0:
        score += min(12, score / (1.0 + math.log10(max(cost, 10) / 1_000_000)))

    bf = budget_fit(acquisition, balance) if acquisition is not None else None
    if bf == "blocked":
        score -= 8
    elif bf == "stretch":
        score -= 4

    if acquisition and market_value > 0 and acquisition > market_value * 2.5 and not fills:
        score -= 12

    price_trend = cand.get("trend")
    if price_trend == "down" and not fills and points_phase == "preseason":
        score -= 6
    elif price_trend == "up":
        score += 2

    # improves_owned adaptativo
    improves = False
    if fills and score >= 5:
        improves = True
    elif points_phase == "active" and points_signal:
        avg_ok = (cand_avg or 0) >= (ref_avg or 0) - 0.15
        trend_ok = ptrend in ("up", "flat", "unknown")
        improves = score >= 12 and (avg_ok or trend_ok == "up" or ptrend == "up")
    else:
        # preseason: titularidad / fotmob / prior / carencia
        lineup_up = (
            cand_lineup is not None
            and ref_lineup is not None
            and cand_lineup >= ref_lineup + 5
        ) or (cand_lineup is not None and cand_lineup >= 75 and ref is None)
        fotmob_up = (
            cand_rating is not None
            and ref_rating is not None
            and cand_rating >= ref_rating + 0.25
        ) or (cand_rating is not None and cand_rating >= 7.0 and ref_rating is None)
        prior_up = (
            cand_prior is not None
            and ref_prior is not None
            and cand_prior > ref_prior
        )
        improves = score >= 10 and (lineup_up or fotmob_up or prior_up or fills or ref is None)

    return score, improves, why_extra


def build_rival_upgrade_targets(
    me: dict[str, Any],
    diagnosis: dict[str, Any],
    rivals: list[dict[str, Any]],
    *,
    balance: float | None = None,
    points_phase: str | None = None,
) -> list[dict[str, Any]]:
    """
    Objetivos en plantillas rivales.
    clause_bid solo si clause_known + asequible + improves_owned.
    Score adaptativo: puntos como capa adicional según fase.
    """
    squad = list(me.get("squad") or [])
    bal = _money(balance if balance is not None else me.get("balance"))
    needy = {
        pos for pos, info in diagnosis.get("by_position", {}).items()
        if info.get("status") in ("critical", "warning")
    }
    critical_pos = {
        a["position"] for a in diagnosis.get("alerts", []) if a.get("level") == "critical"
    }
    has_critical = bool(critical_pos)

    # Universo para fase
    universe: list[dict[str, Any]] = list(squad)
    for r in rivals:
        universe.extend(r.get("squad") or [])
    phase = points_phase or detect_points_phase(universe)

    candidates: list[dict[str, Any]] = []
    for r in rivals:
        owner_name = r.get("team_name") or r.get("manager")
        owner_rank = r.get("rank")
        pool = list(r.get("key_players") or [])
        squad_idx = {str(p.get("id")): p for p in (r.get("squad") or [])}
        for kp in pool:
            pid = str(kp.get("id") or "")
            base = dict(squad_idx.get(pid) or kp)
            base["owner_team"] = owner_name
            base["owner_rank"] = owner_rank
            base["owner_id"] = r.get("team_id")
            candidates.append(base)
        for p in sorted(r.get("squad") or [], key=lambda x: -_money(x.get("price")))[:3]:
            if str(p.get("id")) not in {str(c.get("id")) for c in candidates}:
                row = dict(p)
                row["owner_team"] = owner_name
                row["owner_rank"] = owner_rank
                row["owner_id"] = r.get("team_id")
                candidates.append(row)

    by_id: dict[str, dict[str, Any]] = {}
    for c in candidates:
        pid = str(c.get("id") or "")
        if not pid:
            continue
        prev = by_id.get(pid)
        if not prev or (c.get("clause_known") and not prev.get("clause_known")):
            by_id[pid] = c
        elif prev and _money(c.get("price")) > _money(prev.get("price")):
            by_id[pid] = {**prev, **c}

    results: list[dict[str, Any]] = []
    my_ids = {str(p.get("id")) for p in squad}

    for c in by_id.values():
        pid = str(c.get("id"))
        if pid in my_ids:
            continue
        pos = c.get("position") or "MF"
        ref = _best_owned_reference(squad, pos, points_phase=phase)
        market_value = _money(c.get("market_value") or c.get("price"))
        clause_known = bool(c.get("clause_known"))
        clause = _money(c.get("clause")) if clause_known else None
        acquisition = clause if clause_known else None

        avail = _ext_avail(c)
        if avail in ("injured", "suspended"):
            continue

        fills = pos in needy or pos in critical_pos
        critical_elsewhere = has_critical and not fills

        upgrade_score, improves, why_extra = compute_upgrade_score(
            c,
            ref,
            points_phase=phase,
            fills=fills,
            critical_elsewhere=critical_elsewhere,
            balance=bal,
            acquisition=acquisition,
        )
        if not improves:
            continue

        # Gate plantilla: lujo con carencia crítica otra pos → solo scout
        luxury_block = critical_elsewhere and clause_known

        compared_to = (ref or {}).get("name") if ref else None
        bf = budget_fit(acquisition, bal) if clause_known else None
        why_bits = []
        if fills:
            why_bits.append(f"cubre carencia {pos}")
        if compared_to:
            why_bits.append(f"mejora a {compared_to}")
        else:
            why_bits.append(f"refuerzo {pos}")
        why_bits.append(f"en {c.get('owner_team') or 'rival'} (#{c.get('owner_rank') or '?'})")
        if why_extra:
            why_bits.append(", ".join(why_extra[:2]))
        why_bits.append(f"señal:{phase}")

        avg_s = _mister_avg(c)
        pts_s = _mister_points(c)
        if avg_s is not None or pts_s is not None:
            why_bits.append(
                f"media {avg_s if avg_s is not None else '—'} · pts {int(pts_s) if pts_s is not None else '—'}"
            )

        if luxury_block:
            action = "scout"
            urgency = "low"
            why_bits.append("prioridad: cubre antes tu carencia crítica")
            risk = "low"
            bf = bf
        elif clause_known and bf in ("comfortable", "tight"):
            action = "clause_bid"
            urgency = "high" if fills or bf == "comfortable" else "medium"
            why_bits.append(f"cláusula {clause:,.0f} €")
            risk = "medium" if bf == "tight" else "low"
        elif clause_known and bf in ("stretch", "blocked"):
            action = "scout"
            urgency = "low"
            why_bits.append(
                f"mejora clara pero caja corta (cláusula {clause:,.0f} € / saldo {bal:,.0f} €)"
                if clause
                else "mejora clara pero caja corta"
            )
            risk = "low"
        else:
            action = "scout"
            urgency = "low"
            why_bits.append("ver cláusula en Mister")
            risk = "low"
            bf = None

        item = {
            "player_id": pid,
            "name": c.get("name"),
            "position": pos,
            "action": action,
            "bid": acquisition if action == "clause_bid" else None,
            "acquisition_cost": acquisition,
            "clause": clause,
            "clause_known": clause_known,
            "market_value": market_value,
            "owner_team": c.get("owner_team"),
            "owner_rank": c.get("owner_rank"),
            "improves_owned": True,
            "compared_to": compared_to,
            "upgrade_score": round(upgrade_score, 1),
            "fills_need": fills,
            "budget_fit": bf,
            "wait_risk": risk,
            "urgency": urgency,
            "trend": c.get("trend"),
            "mister_avg": avg_s,
            "points": pts_s,
            "points_trend": _points_trend(c),
            "points_phase": phase,
            "prior_avg": _prior_avg(c),
            "rival_demand": 0,
            "why": "; ".join(why_bits),
            "affordable": action == "clause_bid",
        }
        if action == "clause_bid":
            item["priority_score"] = priority_score_clause(item) + int(upgrade_score // 2)
        else:
            item["priority_score"] = max(5, priority_score_clause(item) // 2 + int(upgrade_score // 3))
        results.append(item)

    results.sort(key=lambda x: (-int(x.get("priority_score") or 0), -float(x.get("upgrade_score") or 0)))
    capped: list[dict[str, Any]] = []
    n_clause = 0
    n_scout = 0
    for r in results:
        if r["action"] == "clause_bid":
            if n_clause >= 4:
                continue
            n_clause += 1
        else:
            if n_scout >= 4:
                continue
            n_scout += 1
        capped.append(r)
    return capped


def annotate_market_budget_risk(
    opportunities: list[dict[str, Any]],
    rivals: list[dict[str, Any]],
    balance: float,
    *,
    points_phase: str = "preseason",
    market_mode: str = "auction",
) -> list[dict[str, Any]]:
    """Añade budget_fit, target_tier, wait_risk, priority_score y reordena mercado."""
    out: list[dict[str, Any]] = []
    bal = _money(balance)
    mode = market_mode or "auction"
    for o in opportunities:
        row = dict(o)
        fills = bool(row.get("fills_need"))
        risk = wait_risk(row, rivals, fills_need=fills, market_mode=mode)
        cost = _money(row.get("puja_recomendada") or row.get("price"))
        min_c = _money(row.get("puja_minima") or row.get("price"))
        bf = budget_fit(cost, bal, min_cost=min_c)
        tier = target_tier_from_budget_fit(bf)
        # Muestra corta desde ff_apps si no vino ya
        if row.get("sample_thin") is None:
            apps = row.get("ff_apps")
            if apps is None:
                apps = (row.get("external") or {}).get("ff_apps")
            try:
                row["sample_thin"] = apps is not None and int(apps) < 8
                if row.get("ff_apps") is None and apps is not None:
                    row["ff_apps"] = int(apps)
            except (TypeError, ValueError):
                row["sample_thin"] = False
        row["wait_risk"] = risk
        row["budget_fit"] = bf
        row["target_tier"] = tier
        row["rival_demand"] = len(
            rival_demand_for_position(rivals, row.get("position") or "", market_mode=mode)
        )
        row["mister_avg"] = _mister_avg(row)
        row["points_trend"] = _points_trend(row)
        row["points_phase"] = points_phase
        row["market_mode"] = mode
        row["priority_score"] = priority_score_buy({
            **row,
            "risk": risk,
            "budget_fit": bf,
            "target_tier": tier,
        })
        row["affordable"] = bf in ("comfortable", "tight")
        # Nunca Alta si aspiracional (refuerzo tras annotate)
        if tier == "aspirational" and row.get("priority") == "Alta":
            row["priority"] = "Media"
        out.append(row)
    out.sort(key=lambda x: (-int(x.get("priority_score") or 0), -float(x.get("score") or 0)))
    return out


def _is_daily_market_item(item: dict[str, Any]) -> bool:
    return bool(item.get("on_daily_market") or item.get("seller") == "market")


def _item_buy_cost(item: dict[str, Any]) -> float:
    for key in ("cost", "bid", "puja_recomendada", "price"):
        if item.get(key) is not None:
            try:
                return float(item[key])
            except (TypeError, ValueError):
                pass
    return 0.0


def finalize_action_plan(
    plan: list[dict[str, Any]],
    *,
    balance: float | None = None,
    funding_info: dict[str, Any] | None = None,
    market_mode: str = "auction",
    target_board: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Cola operativa = paquete del día (máx. 2 buy_now compatibles) + plan B / also_good.
    No reserva 1 compra por posición: evita la sensación de «compra los 3».
    Devuelve (action_plan, daily_package).
    """
    cash_reserve = float(getattr(config, "PACKAGE_CASH_RESERVE", 8_000_000))
    # Reserva del paquete = objetivos operables de HOY (no reconstruir toda la plantilla)
    bal = float(balance) if balance is not None else 0.0
    if funding_info and funding_info.get("cash_reserved") is not None:
        daily_reserve = float(funding_info.get("cash_reserved") or 0)
        if daily_reserve > 0:
            # Nunca hinchar por encima del saldo: si falta caja, se vende; la reserva del
            # paquete es lo que hay que proteger hoy, acotada al balance.
            cash_reserve = min(daily_reserve, bal) if bal > 0 else daily_reserve
        else:
            cash_reserve = min(cash_reserve, bal * 0.5) if bal > 0 else cash_reserve
    else:
        cash_reserve = min(cash_reserve, bal * 0.5) if bal > 0 else cash_reserve
    secondary_max = float(getattr(config, "PACKAGE_SECONDARY_MAX", 2_500_000))
    package_id = datetime.now(timezone.utc).date().isoformat()
    fixed = (market_mode or "auction") == "fixed"
    primary_ids = {
        str(t.get("player_id"))
        for t in (target_board or {}).get("primary_targets") or []
        if t.get("player_id")
    }
    # Ideal aspiracional: solo scout / watching — no reserva caja ni fund_target
    plan_ids = {str(i.get("player_id")) for i in plan if i.get("player_id")}
    for at in (target_board or {}).get("aspirational_targets") or []:
        pid = str(at.get("player_id") or "")
        if not pid or pid in plan_ids or pid in primary_ids:
            continue
        plan.append(
            {
                "player_id": pid,
                "name": at.get("name"),
                "position": at.get("position"),
                "action": "scout",
                "bid": None,
                "acquisition_cost": _money(at.get("price")),
                "price": _money(at.get("price")),
                "ep_score": at.get("ep_score"),
                "why": (
                    f"Ideal aspiracional (máx EP) · no reserva caja operable · "
                    f"EP {at.get('ep_score') or '—'} · ~{_money(at.get('price')):,.0f} €"
                ),
                "urgency": "low",
                "affordable": False,
                "budget_fit": "blocked",
                "target_tier": "aspirational",
                "is_primary_target": False,
                "is_board_objective": False,
                "queue_role": "aspirational_watch",
                "priority_score": 20,
            }
        )
        plan_ids.add(pid)

    action_base = {
        "buy_now": 1000,
        "clause_bid": 920,
        "sell": 840,
        "avoid": 760,
        "wait": 200,
        "scout": 160,
    }
    urg_bonus = {"high": 40, "medium": 15, "low": 0}
    for item in plan:
        if item.get("priority_score") is None:
            act = item.get("action")
            if act == "sell":
                item["priority_score"] = priority_score_sell(item)
            elif act in ("clause_bid", "scout"):
                item["priority_score"] = priority_score_clause(item)
            else:
                item["priority_score"] = priority_score_buy(item)
        base = action_base.get(item.get("action"), 0)
        need_boost = 0
        if item.get("fills_structural"):
            need_boost = 90
        elif item.get("fills_need"):
            need_boost = 45
        if item.get("leaves_gap_budget"):
            need_boost += 25
        if item.get("crowds_out_gaps"):
            need_boost -= 70
        daily_boost = 0
        if item.get("action") in ("buy_now", "wait", "avoid"):
            if _is_daily_market_item(item):
                daily_boost = 120
                if item.get("is_key_market"):
                    daily_boost += 180  # clave del día por encima de parches
                elif item.get("is_primary_target"):
                    daily_boost += 100
            elif item.get("action") == "wait":
                daily_boost = -80
        rival_boost = 0 if fixed else min(20, int(item.get("rival_demand") or 0) * 4)
        # Sin clave: carencia + puntaje/trueque
        asset_boost = 0
        if not item.get("is_key_market") and item.get("action") == "buy_now":
            try:
                asset_boost = min(40, int(float(item.get("trade_asset_score") or 0)))
            except (TypeError, ValueError):
                asset_boost = 0
        item["_queue_rank"] = (
            base
            + int(item.get("priority_score") or 0)
            + urg_bonus.get(item.get("urgency"), 0)
            + rival_boost
            + need_boost
            + daily_boost
            + asset_boost
        )

    plan.sort(
        key=lambda x: (
            -int(x.get("_queue_rank") or 0),
            -int(x.get("priority_score") or 0),
        )
    )

    daily_buys = [
        i
        for i in plan
        if i.get("action") == "buy_now"
        and _is_daily_market_item(i)
        and (i.get("target_tier") or target_tier_from_budget_fit(i.get("budget_fit"))) == "realistic"
        and (i.get("budget_fit") in ("comfortable", "tight", None))
    ]

    def _primary_sort_key(item: dict[str, Any]) -> tuple:
        cost = _item_buy_cost(item)
        residual = bal - cost
        leaves = bool(item.get("leaves_gap_budget")) or residual >= cash_reserve
        crowds = bool(item.get("crowds_out_gaps"))
        fills = bool(
            item.get("fills_coverage_gap")
            or item.get("fills_structural")
            or item.get("fills_need")
        )
        pid = str(item.get("player_id") or "")
        is_prim = bool(item.get("is_primary_target")) or pid in primary_ids
        is_key = bool(item.get("is_key_market")) or is_prim
        is_obj = bool(item.get("is_board_objective")) or is_prim
        try:
            asset = float(item.get("trade_asset_score") or 0)
        except (TypeError, ValueError):
            asset = 0.0
        prod = 0.0
        try:
            prod = float(item.get("production_score") or 0)
        except (TypeError, ValueError):
            prod = 0.0
        return (
            1 if is_key else 0,
            1 if is_prim else 0,
            1 if is_obj else 0,
            1 if fills else 0,
            # Sin clave: maximizar puntaje, luego trueque (caja es filtro, no empate principal)
            int(prod),
            int(asset * 10),
            0 if crowds else 1,
            1 if leaves else 0,
            int(item.get("priority_score") or 0),
            int(item.get("_queue_rank") or 0),
            -cost,
        )

    primary: dict[str, Any] | None = None
    if daily_buys:
        # 1) Clave / primary del board en mercado de hoy
        key_hit = [
            i
            for i in daily_buys
            if i.get("is_key_market")
            or str(i.get("player_id") or "") in primary_ids
            or i.get("is_primary_target")
        ]
        # 2) Carencias del mercado de hoy (todas; no filtrar antes por “deja reserva”
        #    — eso excluía cracks asequibles frente a chollos que “dejan caja”)
        gap_pool = [
            i
            for i in daily_buys
            if i.get("fills_coverage_gap")
            or i.get("fills_structural")
            or i.get("fills_need")
            or i.get("is_board_objective")
        ]
        preferred = [
            i
            for i in daily_buys
            if (bal - _item_buy_cost(i)) >= cash_reserve or i.get("leaves_gap_budget")
        ]
        pool = key_hit or gap_pool or preferred or daily_buys
        primary = max(pool, key=_primary_sort_key)

    secondary: dict[str, Any] | None = None
    residual_after_primary = bal
    if primary is not None:
        residual_after_primary = max(0.0, bal - _item_buy_cost(primary))
        primary_pos = primary.get("position")
        for cand in sorted(daily_buys, key=_primary_sort_key, reverse=True):
            if str(cand.get("player_id") or "") == str(primary.get("player_id") or ""):
                continue
            if cand.get("position") == primary_pos:
                continue
            # Tras un clave, el secundario es parche de carencia con buen trueque
            if primary.get("is_key_market") and cand.get("is_key_market"):
                # dos claves solo si el 2º cabe barato
                if _item_buy_cost(cand) > secondary_max:
                    continue
            cost = _item_buy_cost(cand)
            if cost <= 0 or cost > secondary_max:
                continue
            if cost > residual_after_primary:
                continue
            if not (
                cand.get("fills_coverage_gap")
                or cand.get("fills_structural")
                or cand.get("fills_need")
                or cand.get("is_key_market")
            ):
                continue
            # No vaciar el colchón con el secundario
            if residual_after_primary - cost < cash_reserve * 0.45 and cost > 1_200_000:
                continue
            secondary = cand
            break

    primary_id = str(primary.get("player_id") or "") if primary else ""
    secondary_id = str(secondary.get("player_id") or "") if secondary else ""
    primary_name = str(primary.get("name") or "") if primary else ""
    demoted: list[dict[str, Any]] = []

    for item in plan:
        if item.get("action") != "buy_now":
            continue
        pid = str(item.get("player_id") or "")
        item["package_id"] = package_id
        if primary and pid == primary_id:
            if item.get("is_key_market") or item.get("is_primary_target") or pid in primary_ids:
                item["queue_role"] = "primary_target"
                item["package_note"] = (
                    "Clave del mercado — fichar al precio"
                    if fixed
                    else "Clave del mercado — pujar ya"
                )
            else:
                item["queue_role"] = "primary"
                item["package_note"] = (
                    "Carencia prioritaria — fichar al precio"
                    if fixed
                    else "Carencia prioritaria — pujar (máx. puntaje/trueque)"
                )
            item["alt_for"] = None
            continue
        if secondary and pid == secondary_id:
            item["queue_role"] = "secondary"
            item["package_note"] = "También si cabe — carencia con buen trueque"
            item["alt_for"] = None
            continue

        # Resto: demote a wait (plan B / also_good / no acumular)
        why_prev = (item.get("why") or "").strip()
        if primary and item.get("position") == primary.get("position"):
            item["action"] = "wait"
            if fixed:
                item["queue_role"] = "also_good"
                item["alt_for"] = primary.get("player_id")
                item["package_note"] = "También válido — sin prisa (plantillas compartidas)"
                item["urgency"] = "low"
                prefix = "También válido — sin prisa"
            else:
                item["queue_role"] = "alt_if_lost"
                item["alt_for"] = primary.get("player_id")
                item["package_note"] = f"Plan B si se va {primary_name}"
                item["urgency"] = "medium"
                prefix = f"Plan B si se va {primary_name}"
            item["why"] = f"{prefix}; {why_prev}" if why_prev else prefix
        else:
            item["action"] = "wait"
            item["queue_role"] = "do_not_stack"
            item["alt_for"] = primary.get("player_id") if primary else None
            item["package_note"] = "No acumular con el paquete de hoy"
            item["urgency"] = "low"
            prefix = "No acumular con el paquete de hoy"
            item["why"] = f"{prefix}; {why_prev}" if why_prev else prefix
        demoted.append(item)

    # Aspiracionales / fuera de caja → no mezclar con plan de hoy
    for item in plan:
        tier = item.get("target_tier") or target_tier_from_budget_fit(item.get("budget_fit"))
        if tier != "aspirational" and item.get("budget_fit") != "blocked":
            continue
        if item.get("queue_role") in ("primary", "primary_target", "secondary"):
            continue
        if item.get("action") in ("buy_now", "clause_bid"):
            # No debería llegar: buy_now exige caja; por seguridad
            item["action"] = "wait"
        if not item.get("queue_role") or item.get("queue_role") in (
            "alt_if_lost",
            "also_good",
            "do_not_stack",
        ):
            item["queue_role"] = "out_of_budget"
            note = "Fuera de caja — vigilar / vender antes"
            item["package_note"] = note
            item["urgency"] = "low"
            why_prev = (item.get("why") or "").strip()
            if "Fuera de caja" not in why_prev:
                item["why"] = f"{note}; {why_prev}" if why_prev else note

    # Re-rank tras demote
    for item in plan:
        role = item.get("queue_role")
        if role in ("primary", "primary_target"):
            item["_queue_rank"] = 10_000 + int(item.get("priority_score") or 0)
        elif role == "secondary":
            item["_queue_rank"] = 9_000 + int(item.get("priority_score") or 0)
        elif role in ("alt_if_lost", "also_good"):
            item["_queue_rank"] = 700 + int(item.get("priority_score") or 0)
        elif role == "do_not_stack":
            item["_queue_rank"] = 550 + int(item.get("priority_score") or 0) // 2
        elif role == "out_of_budget":
            item["_queue_rank"] = 300 + int(item.get("priority_score") or 0) // 3

    plan.sort(
        key=lambda x: (
            -int(x.get("_queue_rank") or 0),
            -int(x.get("priority_score") or 0),
        )
    )

    capped: list[dict[str, Any]] = []
    per_action: dict[str, int] = {}
    limits = {
        "buy_now": 2,  # paquete: primary + secondary
        "clause_bid": 0 if fixed else 4,
        "wait": 8,  # alts + waits del día
        "avoid": 3,
        "sell": 5,
        "scout": 0 if fixed else 3,
    }
    max_pipeline_waits = 2
    max_alt_waits = 3
    max_stack_waits = 2
    max_oob_waits = 2
    pipeline_waits = 0
    alt_waits = 0
    stack_waits = 0
    oob_waits = 0
    max_total = sum(limits.values())
    used_ids: set[str] = set()
    sim_balance = float(balance) if balance is not None else None

    def _append(item: dict[str, Any]) -> bool:
        nonlocal sim_balance, pipeline_waits, alt_waits, stack_waits, oob_waits
        a = item.get("action") or ""
        pid = str(item.get("player_id") or "")
        role = item.get("queue_role")
        if pid and pid in used_ids:
            return False
        if per_action.get(a, 0) >= limits.get(a, 3):
            return False
        if a == "buy_now" and role not in ("primary", "primary_target", "secondary"):
            return False
        if a == "buy_now" and sim_balance is not None:
            cost = _item_buy_cost(item)
            if cost > sim_balance:
                return False
        if a == "wait":
            if role in ("alt_if_lost", "also_good"):
                if alt_waits >= max_alt_waits:
                    return False
                alt_waits += 1
            elif role == "do_not_stack":
                if stack_waits >= max_stack_waits:
                    return False
                stack_waits += 1
            elif role == "out_of_budget":
                if oob_waits >= max_oob_waits:
                    return False
                oob_waits += 1
            elif not _is_daily_market_item(item):
                if pipeline_waits >= max_pipeline_waits:
                    return False
                pipeline_waits += 1
        if len(capped) >= max_total:
            return False
        per_action[a] = per_action.get(a, 0) + 1
        if pid:
            used_ids.add(pid)
        if a == "buy_now" and sim_balance is not None:
            sim_balance = max(0.0, sim_balance - _item_buy_cost(item))
        clean = {k: v for k, v in item.items() if k != "_queue_rank"}
        capped.append(clean)
        return True

    # Paquete → also_good / plan B / no acumular → fuera de caja → resto
    for item in plan:
        if item.get("queue_role") in ("primary", "primary_target", "secondary"):
            _append(item)
    for item in plan:
        if item.get("queue_role") in ("alt_if_lost", "also_good", "do_not_stack"):
            _append(item)
    for item in plan:
        if item.get("queue_role") == "out_of_budget":
            _append(item)
    for item in plan:
        if item.get("queue_role") in (
            "primary",
            "primary_target",
            "secondary",
            "alt_if_lost",
            "also_good",
            "do_not_stack",
            "out_of_budget",
        ):
            continue
        if len(capped) >= max_total:
            break
        _append(item)

    spend = 0.0
    if primary:
        spend += _item_buy_cost(primary)
    if secondary:
        spend += _item_buy_cost(secondary)
    residual_after = max(0.0, bal - spend)

    if primary:
        if primary.get("is_key_market") or primary.get("is_primary_target"):
            note = (
                "Prioridad: jugador clave en mercado. Luego carencias con buen puntaje/trueque."
                if fixed
                else "Prioridad: clave en mercado (pujar). Si no hay, carencias con máx. puntaje/trueque."
            )
        else:
            note = (
                "Sin clave hoy: prioriza carencias con más puntaje y valor de trueque."
                if fixed
                else "Sin clave hoy: pujas a carencias con más puntaje y valor de trueque."
            )
    else:
        note = "Sin compra clara en el mercado de hoy — vigila claves y carencias."

    daily_package: dict[str, Any] = {
        "package_id": package_id,
        "market_mode": "fixed" if fixed else "auction",
        "primary": (
            {
                "player_id": primary.get("player_id"),
                "name": primary.get("name"),
                "position": primary.get("position"),
                "bid": primary.get("bid") or primary.get("cost"),
                "is_key_market": bool(primary.get("is_key_market")),
                "trade_asset_score": primary.get("trade_asset_score"),
            }
            if primary
            else None
        ),
        "secondary": (
            {
                "player_id": secondary.get("player_id"),
                "name": secondary.get("name"),
                "position": secondary.get("position"),
                "bid": secondary.get("bid") or secondary.get("cost"),
                "trade_asset_score": secondary.get("trade_asset_score"),
            }
            if secondary
            else None
        ),
        "alts": [
            {
                "player_id": a.get("player_id"),
                "name": a.get("name"),
                "position": a.get("position"),
                "queue_role": a.get("queue_role"),
                "alt_for": a.get("alt_for"),
            }
            for a in demoted[:6]
        ],
        "spend_cap": spend,
        "cash_reserve": cash_reserve,
        "residual_after": residual_after,
        "note": note,
        "cash_reserved_targets": float((funding_info or {}).get("cash_reserved") or 0),
        "primary_is_target": bool(
            primary
            and (
                primary.get("is_primary_target")
                or primary.get("is_key_market")
                or str(primary.get("player_id") or "") in primary_ids
            )
        ),
        "policy": "key_market_first_then_gap_score_trade",
    }

    return capped, daily_package
