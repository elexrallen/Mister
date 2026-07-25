"""
Acciones competitivas: ventas situacionales, presupuesto/riesgo,
y objetivos en plantillas rivales (cláusulas).
"""

from __future__ import annotations

import math
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
    lineup = _lineup_pct(p)
    low_lp = getattr(config, "LINEUP_PROB_LOW", 0.40) * 100.0
    mins_low = getattr(config, "MINUTES_RECENT_LOW", 90)
    mins = _recent_minutes(p)
    if mins is not None and mins < mins_low:
        return True
    if lineup is not None and lineup < low_lp:
        if mins is not None and mins >= mins_low * 2:
            return False
        return True
    return False


def _is_useful_patch(p: dict[str, Any]) -> bool:
    """Parche barato que juega de verdad: no vender salvo emergencia."""
    if _ext_avail(p) in ("injured", "suspended") or p.get("injury"):
        return False
    price = _money(p.get("price") or p.get("market_value"))
    if price <= 0 or price > 2_000_000:
        return False
    lineup = _lineup_pct(p)
    return lineup is not None and lineup >= 45


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


def _xi_impact_if_sold(squad: list[dict[str, Any]], player: dict[str, Any]) -> str:
    """
    safe  → el once/línea aguanta sin él
    risk  → vendería un titular dejando la línea justa
    soft  → cobertura sana pero pierdes un regular
    """
    pos = player.get("position") or "MF"
    mins = _mins()
    min_need = mins.get(pos, 2)
    # Conteos excluyendo al jugador
    others = [p for p in squad if str(p.get("id")) != str(player.get("id"))]
    healthy = _healthy_count(others, pos)
    starters = _starter_count(others, pos)
    was_starter = _is_reliable_starter(player)

    # Mínimos de titulares por línea (estrategia once fiable)
    starter_floor = {"GK": 1, "DF": 3, "MF": 3, "FW": 2}.get(pos, 2)

    if healthy < min_need:
        return "risk"
    if was_starter and starters < starter_floor:
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


def rival_demand_for_position(rivals: list[dict[str, Any]], position: str) -> list[dict[str, Any]]:
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
) -> str:
    demand = rival_demand_for_position(rivals, o.get("position") or "")
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
    if item.get("fills_need"):
        score += 35
    if item.get("fills_structural"):
        score += 20
    risk = item.get("wait_risk") or item.get("risk") or "low"
    score += {"high": 25, "medium": 15, "low": 5}.get(str(risk), 5)
    bf = item.get("budget_fit") or "blocked"
    score += {"comfortable": 20, "tight": 10, "stretch": 0, "blocked": -25}.get(str(bf), 0)
    score += min(15, int(item.get("rival_demand") or 0) * 5)
    if item.get("improves_owned"):
        score += 20
    # Producción FF / Mister
    prod = _production_score(item)
    if prod is not None:
        score += int(min(25, prod / 4))
        if prod < 35:
            score -= 10
    ff = _ff_avg(item)
    if ff is not None:
        score += int(min(12, ff * 1.5))
        if ff < 3.5 and _money(item.get("price") or item.get("market_value")) >= 5_000_000:
            score -= 12
    if item.get("is_top_ff") or (item.get("external") or {}).get("is_top_ff"):
        score += 8
    # Capa puntos adicional cuando discrimina
    avg = item.get("mister_avg")
    try:
        if avg is not None and float(avg) > 0:
            score += min(15, int(float(avg) * 2))
    except (TypeError, ValueError):
        pass
    trend = item.get("points_trend") or item.get("trend")
    if trend == "up":
        score += 8
    elif trend == "down":
        score -= 6
    return score


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
    need_costs: list[float] = []
    for o in market_opportunities or []:
        if o.get("fills_need") and o.get("position") in (critical_pos | needy):
            need_costs.append(_money(o.get("puja_recomendada") or o.get("price")))
    cheapest_need = min(need_costs) if need_costs else None
    cash_tight = cheapest_need is not None and balance < cheapest_need

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

        # Solo proteger si es titular real y la línea quedaría bajo el suelo
        protect_xi = xi == "risk" and is_starter and avail not in ("injured", "suspended")
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
            and price > 0
        ):
            urg = "high" if bench_inflated or price >= 6_000_000 else "medium"
            bits = [
                f"fuera del once real (titularidad {int(lineup) if lineup is not None else '—'}%)",
                f"libera {price:,.0f} € para titulares/producción",
            ]
            if p.get("in_lineup"):
                bits.insert(0, "en tu once Mister pero sin titularidad real")
            if ff is not None:
                bits.append(f"FF {ff:.1f}")
            item = base_item(
                p,
                reason="expensive_bench",
                why="; ".join(bits),
                urgency=urg,
                sell_risk="low" if xi == "safe" else "medium",
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
            item = base_item(
                p,
                reason="low_minutes",
                why="; ".join(bits),
                urgency="high" if price >= 5_000_000 else "medium",
                sell_risk="low" if not is_starter else "medium",
            )
            item["_pref"] = 48
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
                sell_risk="medium" if is_starter else "low",
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
        if healthy > min_need and demand and not keep_top and not protect_xi and not protect_patch:
            # Solo si no es de los mejores titulares de la línea
            weak_surplus = (
                not is_starter
                or (lineup is not None and lineup < 75)
                or (prod is not None and prod < 50)
                or not is_star
            )
            starter_floor = {"GK": 1, "DF": 3, "MF": 3, "FW": 2}.get(pos, 2)
            starters_after = starters - (1 if is_starter else 0)
            # Evitar vender si la línea de titulares quedaría bajo el suelo del once
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

        # 5) Financiar carencia del once (prioriza no titulares)
        if cash_tight and needy and covered_if_sold and not keep_top and not protect_xi:
            if protect_patch and not critical_pos:
                pass
            else:
                essential = is_star or is_starter
                weak = (rating is not None and rating < 6.5) or (lineup is not None and lineup < 75) or not is_starter
                if not essential or weak:
                    item = base_item(
                        p,
                        reason="fund_buy",
                        why=(
                            f"Caja justa ({balance:,.0f} €) vs carencia del once; "
                            f"vender libera ~{price:,.0f} € con cobertura en {pos}."
                        ),
                        urgency="high" if critical_pos else "medium",
                        sell_risk="medium" if is_starter or healthy <= min_need + 1 else "low",
                    )
                    item["_pref"] = 35
                    add(item)

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
) -> list[dict[str, Any]]:
    """Añade budget_fit, wait_risk, priority_score y reordena mercado."""
    out: list[dict[str, Any]] = []
    bal = _money(balance)
    for o in opportunities:
        row = dict(o)
        fills = bool(row.get("fills_need"))
        risk = wait_risk(row, rivals, fills_need=fills)
        cost = _money(row.get("puja_recomendada") or row.get("price"))
        min_c = _money(row.get("puja_minima") or row.get("price"))
        bf = budget_fit(cost, bal, min_cost=min_c)
        row["wait_risk"] = risk
        row["budget_fit"] = bf
        row["rival_demand"] = len(rival_demand_for_position(rivals, row.get("position") or ""))
        row["mister_avg"] = _mister_avg(row)
        row["points_trend"] = _points_trend(row)
        row["points_phase"] = points_phase
        row["priority_score"] = priority_score_buy({
            **row,
            "risk": risk,
            "budget_fit": bf,
        })
        row["affordable"] = bf in ("comfortable", "tight")
        out.append(row)
    out.sort(key=lambda x: (-int(x.get("priority_score") or 0), -float(x.get("score") or 0)))
    return out


def finalize_action_plan(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Una sola cola 'a tiro hecho': lo más accionable y urgente primero.
    Orden: score unificado (tipo de acción + priority_score + urgencia), luego caps.
    Reserva diversidad: al menos un buy_now por posición con carencia.
    """
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
        item["_queue_rank"] = (
            base
            + int(item.get("priority_score") or 0)
            + urg_bonus.get(item.get("urgency"), 0)
            + min(20, int(item.get("rival_demand") or 0) * 4)
            + need_boost
        )
    plan.sort(
        key=lambda x: (
            -int(x.get("_queue_rank") or 0),
            -int(x.get("priority_score") or 0),
        )
    )

    # Reserva: mejor buy_now por posición que cubra carencia (titularidad real / estructural)
    reserved: list[dict[str, Any]] = []
    seen_pos: set[str] = set()
    for item in plan:
        if item.get("action") != "buy_now":
            continue
        pos = item.get("position") or ""
        if not pos or pos in seen_pos:
            continue
        if item.get("fills_need") or item.get("fills_structural"):
            reserved.append(item)
            seen_pos.add(pos)

    capped: list[dict[str, Any]] = []
    per_action: dict[str, int] = {}
    limits = {
        "buy_now": 6,
        "clause_bid": 4,
        "wait": 5,
        "avoid": 3,
        "sell": 5,
        "scout": 3,
    }
    used_ids: set[str] = set()

    def _append(item: dict[str, Any]) -> bool:
        a = item.get("action") or ""
        pid = str(item.get("player_id") or "")
        if pid and pid in used_ids:
            return False
        if per_action.get(a, 0) >= limits.get(a, 3):
            return False
        per_action[a] = per_action.get(a, 0) + 1
        if pid:
            used_ids.add(pid)
        clean = {k: v for k, v in item.items() if k != "_queue_rank"}
        capped.append(clean)
        return True

    for item in reserved:
        _append(item)
        if len(capped) >= 12:
            break

    for item in plan:
        if len(capped) >= 12:
            break
        _append(item)

    return capped
