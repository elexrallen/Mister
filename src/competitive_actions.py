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


def _is_star(p: dict[str, Any]) -> bool:
    lineup = _lineup_pct(p)
    rating = _fotmob_rating(p)
    avg = _mister_avg(p)
    if lineup is not None and lineup >= 80:
        if rating is not None and rating >= 7.0:
            return True
        if avg is not None and avg >= 5.0:
            return True
    return False


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
    score = 0
    reason = item.get("sell_reason") or ""
    score += {
        "fund_buy": 30,
        "surplus_to_demand": 25,
        "injured_covered": 20,
        "form_drop": 15,
    }.get(reason, 10)
    if item.get("budget_fit") == "funding":
        score += 15
    demand = int(item.get("rival_demand") or 0)
    score += min(15, demand * 5)
    sell_risk = item.get("sell_risk") or item.get("wait_risk") or "medium"
    if sell_risk == "high":
        score -= 15
    elif sell_risk == "low":
        score += 5
    if item.get("keep_if_rank_top"):
        score -= 30
    return score


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
) -> list[dict[str, Any]]:
    """Ventas situacionales con motivos tipados."""
    price_series = price_series or {}
    squad = list(me.get("squad") or [])
    balance = _money(me.get("balance"))
    rank = int(me.get("rank") or 99)
    mins = _mins()
    critical_pos = {
        a["position"] for a in diagnosis.get("alerts", []) if a.get("level") == "critical"
    }
    needy = {
        pos for pos, info in diagnosis.get("by_position", {}).items()
        if info.get("status") in ("critical", "warning")
    }
    # Precio mínimo de mercado para carencias (financiar)
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
            # quedarse el de mayor urgency/score
            prev = next(x for x in sells if str(x["player_id"]) == pid)
            urg = {"high": 0, "medium": 1, "low": 2}
            if urg.get(item.get("urgency"), 9) < urg.get(prev.get("urgency"), 9):
                sells.remove(prev)
                seen.discard(pid)
            else:
                return
        seen.add(pid)
        sells.append(item)

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
        min_need = mins.get(pos, 2)
        covered_if_sold = healthy > min_need or (
            avail in ("injured", "suspended") and healthy >= min_need
        )
        # healthy_count already excludes injured; for injured player, covered if healthy >= min
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
        keep_top = rank <= 2 and is_star and avail not in ("injured", "suspended")

        # 1) injured_covered
        if avail in ("injured", "suspended") and covered_if_sold and price > 0:
            add({
                "player_id": pid,
                "name": p.get("name"),
                "position": pos,
                "action": "sell",
                "sell_reason": "injured_covered",
                "bid": None,
                "wait_risk": "low",
                "sell_risk": "low",
                "urgency": "medium",
                "why": f"{avail} con cobertura en {pos}; libera valor ({price:,.0f} €).",
                "rival_demand": len(demand),
                "rival_targets": demand[:3],
                "budget_fit": "funding" if cash_tight else "comfortable",
                "keep_if_rank_top": False,
                "price": price,
            })

        # 2) surplus_to_demand
        if healthy > min_need and demand and not keep_top:
            # no esencial: baja titularidad o no star
            if lineup is None or lineup < 80 or not is_star:
                urg = "high" if top_demand else "medium"
                names = ", ".join(d["team_name"] for d in (top_demand or demand)[:2] if d.get("team_name"))
                add({
                    "player_id": pid,
                    "name": p.get("name"),
                    "position": pos,
                    "action": "sell",
                    "sell_reason": "surplus_to_demand",
                    "bid": None,
                    "wait_risk": "low",
                    "sell_risk": "low" if healthy > min_need + 1 else "medium",
                    "urgency": urg,
                    "why": f"Excedente {pos} · rivales con gap: {names or 'varios'}.",
                    "rival_demand": len(demand),
                    "rival_targets": demand[:3],
                    "budget_fit": "funding" if cash_tight else "comfortable",
                    "keep_if_rank_top": False,
                    "price": price,
                })

        # 3) fund_buy
        if cash_tight and needy and covered_if_sold and not keep_top:
            essential = is_star or (lineup is not None and lineup >= 80)
            weak = (rating is not None and rating < 6.5) or (lineup is not None and lineup < 80)
            if not essential or weak:
                add({
                    "player_id": pid,
                    "name": p.get("name"),
                    "position": pos,
                    "action": "sell",
                    "sell_reason": "fund_buy",
                    "bid": None,
                    "wait_risk": "low",
                    "sell_risk": "medium" if healthy <= min_need + 1 else "low",
                    "urgency": "high" if critical_pos else "medium",
                    "why": (
                        f"Caja justa ({balance:,.0f} €) vs carencia; "
                        f"vender libera ~{price:,.0f} € con cobertura en {pos}."
                    ),
                    "rival_demand": len(demand),
                    "rival_targets": demand[:3],
                    "budget_fit": "funding",
                    "keep_if_rank_top": False,
                    "price": price,
                })

        # 4) form_drop — media/tendencia Mister + producción FF floja a precio alto
        form_bad = (lineup is not None and lineup < 50) or (rating is not None and rating < 6.0)
        avg = _mister_avg(p)
        ptrend = _points_trend(p)
        ff = _ff_avg(p)
        prod = _production_score(p)
        if points_phase == "active":
            if avg is not None and avg < 4.0:
                form_bad = True
            if ptrend == "down":
                form_bad = True
        # Caro con baja producción Fantasy histórica
        if price >= 4_000_000 and (
            (ff is not None and ff < 3.8) or (prod is not None and prod < 38)
        ):
            form_bad = True
        delta_bad = delta is not None and float(delta) <= -0.08
        if covered_if_sold and not keep_top and price >= 2_000_000 and (form_bad or delta_bad):
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
            add({
                "player_id": pid,
                "name": p.get("name"),
                "position": pos,
                "action": "sell",
                "sell_reason": "form_drop",
                "bid": None,
                "wait_risk": "low",
                "sell_risk": "medium",
                "urgency": "low",
                "why": "; ".join(bits) or "Forma/valor a la baja.",
                "rival_demand": len(demand),
                "rival_targets": demand[:3],
                "budget_fit": "funding" if cash_tight else "comfortable",
                "keep_if_rank_top": False,
                "price": price,
                "ff_mister_avg": ff,
                "production_score": prod,
            })

    for s in sells:
        s["priority_score"] = priority_score_sell(s)
    sells.sort(key=lambda x: (-int(x.get("priority_score") or 0),))
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
    """Orden y caps por acción."""
    order = {
        "buy_now": 0,
        "clause_bid": 1,
        "sell": 2,
        "avoid": 3,
        "wait": 4,
        "scout": 5,
    }
    urg = {"high": 0, "medium": 1, "low": 2}
    for item in plan:
        if item.get("priority_score") is None:
            act = item.get("action")
            if act == "sell":
                item["priority_score"] = priority_score_sell(item)
            elif act in ("clause_bid", "scout"):
                item["priority_score"] = priority_score_clause(item)
            else:
                item["priority_score"] = priority_score_buy(item)
    plan.sort(
        key=lambda x: (
            order.get(x.get("action"), 9),
            -int(x.get("priority_score") or 0),
            urg.get(x.get("urgency"), 9),
            -(x.get("rival_demand") or 0),
        )
    )
    capped: list[dict[str, Any]] = []
    per_action = {k: 0 for k in order}
    limits = {
        "buy_now": 5,
        "clause_bid": 4,
        "wait": 6,
        "avoid": 4,
        "sell": 5,
        "scout": 4,
    }
    for item in plan:
        a = item.get("action") or ""
        if per_action.get(a, 0) >= limits.get(a, 3):
            continue
        per_action[a] = per_action.get(a, 0) + 1
        capped.append(item)
    return capped
