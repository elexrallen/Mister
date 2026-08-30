"""
Plan de un ciclo de mercado: texto + movimientos ejecutables.

Listar hoy → en el siguiente ciclo aceptas la oferta (≈ VM) y el dinero llega al instante.
No hay wait / scout / avoid.
"""

from __future__ import annotations

from typing import Any

import config
from competitive_actions import (
    _has_play_minutes,
    _is_floor_vm,
    _lineup_pct,
    _money,
    appreciation_play_score,
    clause_roi_gate,
    is_rival_market_listing,
    mister_bid_cap,
    sells_settle_before_deadline,
    xi_owned_ids,
)

KIND_ACCEPT = "accept_offer"
KIND_LIST = "list_for_sale"
KIND_BID = "bid"
KIND_CLAUSE = "clause_bid"
KIND_DECLINE = "decline_offer"

FORBIDDEN_ACTIONS = {"wait", "scout", "avoid"}


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _pid(row: dict[str, Any]) -> str:
    return str(row.get("player_id") or row.get("id") or "").strip()


def _price(row: dict[str, Any]) -> float:
    return _money(row.get("price") or row.get("market_value") or row.get("cost"))


def _vm(row: dict[str, Any]) -> float:
    """VM de Mister, no el precio de salida/ask de un listado."""
    return _money(row.get("market_value") or row.get("price") or row.get("cost"))


def compute_value_trend(
    player_id: str,
    current_price: float,
    series: dict[str, list[float]] | None = None,
    *,
    price_delta_1d: float | None = None,
    trend: str | None = None,
) -> dict[str, Any]:
    """
    delta_cycle / delta_1d / delta_5d / accel a partir de la serie de snapshots.
    Decelera si sigue en positivo pero la segunda mitad de la ventana sube menos.
    consecutive_up: tramos finales de VM que suben de verdad (≥1%).
    """
    prices = list((series or {}).get(str(player_id)) or [])
    try:
        current = float(current_price or 0)
    except (TypeError, ValueError):
        current = 0.0
    if current > 0 and (not prices or abs(prices[-1] - current) > 1):
        prices = prices + [current]

    delta_cycle = None
    delta_1d = _f(price_delta_1d)
    delta_5d = None
    accel = None

    distinct: list[float] = []
    for px in prices:
        if px is None or px <= 0:
            continue
        if not distinct or abs(px - distinct[-1]) > 0.01:
            distinct.append(float(px))

    if len(distinct) >= 2:
        delta_cycle = (distinct[-1] - distinct[-2]) / distinct[-2]
        if delta_1d is None:
            delta_1d = delta_cycle
        if distinct[0] > 0:
            delta_5d = (distinct[-1] - distinct[0]) / distinct[0]
    elif len(prices) >= 2 and prices[0] > 0:
        delta_5d = (prices[-1] - prices[0]) / prices[0]
    if len(distinct) >= 4 and distinct[0] > 0:
        mid_i = len(distinct) // 2
        mid_p = distinct[mid_i]
        if mid_p > 0:
            first_leg = (mid_p - distinct[0]) / distinct[0]
            second_leg = (distinct[-1] - mid_p) / mid_p
            accel = second_leg - first_leg

    if delta_5d is None and delta_1d is not None:
        delta_5d = delta_1d
    if delta_5d is None and trend == "up":
        delta_5d = 0.02
    elif delta_5d is None and trend == "down":
        delta_5d = -0.02

    last_down = bool(
        trend == "down"
        or (delta_1d is not None and delta_1d < -0.003)
        or (delta_cycle is not None and delta_cycle < -0.003)
    )
    last_up = bool(
        trend == "up"
        or (delta_1d is not None and delta_1d >= 0.01)
        or (delta_cycle is not None and delta_cycle >= 0.01)
    )
    decelerating = bool(
        delta_5d is not None
        and delta_5d > 0
        and (
            last_down
            or (accel is not None and accel < -0.005)
        )
    )
    rising = bool(last_up and not last_down)
    consecutive_up = 0
    up_step = 0.01
    for i in range(len(distinct) - 1, 0, -1):
        prev, cur = distinct[i - 1], distinct[i]
        if prev > 0 and (cur - prev) / prev >= up_step:
            consecutive_up += 1
        else:
            break
    abs_gain = 0.0
    if consecutive_up and len(distinct) > consecutive_up:
        abs_gain = float(distinct[-1] - distinct[-1 - consecutive_up])
    elif len(distinct) >= 2:
        abs_gain = float(distinct[-1] - distinct[0])
    return {
        "delta_cycle": round(delta_cycle, 4) if delta_cycle is not None else None,
        "delta_1d": round(delta_1d, 4) if delta_1d is not None else None,
        "delta_5d": round(delta_5d, 4) if delta_5d is not None else None,
        "accel": round(accel, 4) if accel is not None else None,
        "decelerating": decelerating,
        "rising": rising,
        "consecutive_up": consecutive_up,
        "abs_gain": round(abs_gain, 2),
    }


def attach_value_trends(
    rows: list[dict[str, Any]] | None,
    series: dict[str, list[float]] | None = None,
) -> list[dict[str, Any]]:
    """Anota plantilla y mercado con la misma métrica de revalorización."""
    out = list(rows or [])
    for row in out:
        if not isinstance(row, dict):
            continue
        pid = _pid(row)
        if not pid:
            continue
        trend = compute_value_trend(
            pid,
            _vm(row),
            series,
            price_delta_1d=_f(row.get("price_delta_1d") or row.get("delta_1d")),
            trend=row.get("trend"),
        )
        for key, val in trend.items():
            if key == "delta_5d" and row.get("delta_5d") is not None and val is None:
                continue
            row[key] = val
    return out


def squad_value_summary(squad: list[dict[str, Any]] | None) -> dict[str, Any]:
    """VM actual de plantilla y Δ agregado (reconstruido desde delta_5d)."""
    now = 0.0
    prev = 0.0
    n_up = 0
    n_down = 0
    n_decel = 0
    for p in squad or []:
        price = _price(p)
        now += price
        d = _f(p.get("delta_5d"))
        if d is not None and d > -0.99:
            prev += price / (1.0 + d)
            if d >= 0.005:
                n_up += 1
            elif d <= -0.005:
                n_down += 1
        else:
            prev += price
        if p.get("decelerating"):
            n_decel += 1
    delta = ((now - prev) / prev) if prev > 0 else None
    return {
        "current": round(now, 0),
        "previous": round(prev, 0),
        "delta_5d": round(delta, 4) if delta is not None else None,
        "n_rising": n_up,
        "n_falling": n_down,
        "n_decelerating": n_decel,
    }


def _fmt_money(v: Any) -> str:
    n = _money(v)
    if n >= 1_000_000:
        txt = f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{txt}M €"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k €"
    return f"{n:.0f} €"


def _fmt_pct(v: Any) -> str:
    d = _f(v)
    if d is None:
        return ""
    sign = "+" if d >= 0 else ""
    return f"{sign}{d * 100:.0f}%"


def _join_names(names: list[str]) -> str:
    clean = [n for n in names if n]
    if not clean:
        return ""
    if len(clean) == 1:
        return clean[0]
    if len(clean) == 2:
        return f"{clean[0]} y {clean[1]}"
    return f"{', '.join(clean[:-1])} y {clean[-1]}"


def _player_ref(row: dict[str, Any], *, kind: str, why: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    ref = {
        "kind": kind,
        "player_id": _pid(row),
        "id": _pid(row),
        "name": row.get("name"),
        "position": row.get("position"),
        "team": row.get("team"),
        "team_id": row.get("team_id"),
        "photo_url": row.get("photo_url"),
        "team_logo_url": row.get("team_logo_url"),
        "price": _price(row),
        "delta_5d": row.get("delta_5d"),
        "delta_cycle": row.get("delta_cycle"),
        "accel": row.get("accel"),
        "decelerating": bool(row.get("decelerating")),
        "why": why,
        "on_daily_market": bool(row.get("on_daily_market")),
    }
    if extra:
        ref.update(extra)
    return ref


def _production(p: dict[str, Any]) -> float:
    for key in ("xpts", "ff_mister_avg", "avg_ppg", "form"):
        v = _f(p.get(key))
        if v is not None:
            return v
    return 0.0


def _keep_riding(p: dict[str, Any]) -> bool:
    """Sigue subiendo lo bastante como para no venderlo solo porque 'frena'."""
    d5 = _f(p.get("delta_5d"))
    floor = float(getattr(config, "CYCLE_STRONG_RISE", 0.08) or 0.08)
    if d5 is None or d5 < floor:
        return False
    if p.get("trend") == "down":
        return False
    now = _f(p.get("price_delta_1d")) or _f(p.get("delta_cycle")) or _f(p.get("delta_1d"))
    if now is not None and now < 0:
        return False
    return True


def _market_much_hotter(owned_delta: float | None, market_row: dict[str, Any] | None) -> bool:
    """El listado del mercado revaloriza claramente más que la pieza propia."""
    if market_row is None or owned_delta is None:
        return False
    md = _f(market_row.get("delta_5d"))
    if md is None:
        return False
    margin = float(getattr(config, "CYCLE_LIST_SWAP_MARGIN", 0.08) or 0.08)
    floor = float(getattr(config, "CYCLE_STRONG_RISE", 0.08) or 0.08)
    return md >= floor and md >= owned_delta + margin


def _is_recover_sale(p: dict[str, Any], xi_ids: set[str]) -> bool:
    """Banquillo vendible para tapar deuda: no XI, no titular, no pieza que aún sube."""
    pid = _pid(p)
    if not pid or pid in xi_ids:
        return False
    if _keep_riding(p):
        return False
    lp = _lineup_pct(p)
    if lp is not None and lp >= 70:
        return False
    return True


def _recover_pool(
    squad: list[dict[str, Any]],
    *,
    xi_ids: set[str],
    skip_ids: set[str],
) -> list[dict[str, Any]]:
    rows = [
        p
        for p in squad
        if _pid(p) and _pid(p) not in skip_ids and _is_recover_sale(p, xi_ids)
    ]
    rows.sort(key=lambda p: (-_list_score(p), -_price(p)))
    return rows


def _recovery_capacity(
    pool: list[dict[str, Any]],
    *,
    sale_remaining: int,
    listed_timely: float,
) -> float:
    extra = sum(_price(p) for p in pool[: max(0, sale_remaining)])
    return listed_timely + extra


def _list_score(p: dict[str, Any]) -> float:
    """Más alto = mejor candidato a listar (banquillo cuyo VM ya no tira)."""
    score = 0.0
    if p.get("decelerating"):
        score += 40.0
    d5 = _f(p.get("delta_5d"))
    if d5 is not None:
        if d5 <= 0:
            score += 22.0
        elif d5 < 0.04:
            score += 16.0
        elif d5 < 0.08:
            score += 6.0
        else:
            score -= 28.0
    acc = _f(p.get("accel"))
    if acc is not None and acc < 0:
        score += 12.0
    lp = _lineup_pct(p)
    if lp is not None and lp < 45:
        score += 24.0
    elif lp is not None and lp < 60:
        score += 8.0
    prod = _production(p)
    if prod < 3.5:
        score += 16.0
    elif prod < 5.0:
        score += 6.0
    if p.get("injury") or (p.get("external") or {}).get("availability") in ("injured", "suspended"):
        score += 10.0
    return score


def _starter_coverage_hole(p: dict[str, Any]) -> bool:
    """Hueco real del once: titular usable (≥70%) que cubre una carencia."""
    if not (p.get("fills_coverage_gap") or p.get("fills_structural") or p.get("fills_need")):
        return False
    lp = _lineup_pct(p)
    return lp is not None and lp >= 70.0


def _bid_score(p: dict[str, Any]) -> float:
    appr, _why = appreciation_play_score(p)
    score = float(appr)
    d5 = _f(p.get("delta_5d")) or 0.0
    strong = float(getattr(config, "CYCLE_STRONG_RISE", 0.08) or 0.08)
    fills_gap = bool(
        p.get("fills_coverage_gap") or p.get("fills_structural") or p.get("fills_need")
    )
    if p.get("decelerating"):
        score -= 24.0
    elif appr > 0:
        if d5 >= 0.10:
            score += 20.0
        elif d5 >= strong:
            score += 12.0
    elif d5 <= -strong:
        score -= 36.0
    elif d5 < 0:
        score -= 14.0
    # Hueco real: titular usable con VM de verdad. Un parche 200k que no
    # juega no se cuela como puja de revalorización.
    live_gap = fills_gap and d5 >= 0 and not p.get("decelerating")
    if live_gap and _has_play_minutes(p) and not _is_floor_vm(p):
        score += 28.0
    elif _starter_coverage_hole(p) and not _is_floor_vm(p) and not p.get("decelerating"):
        score += 12.0
    if p.get("is_upgrade") or p.get("upgrade_worth_buy"):
        score += 14.0
    if not p.get("on_daily_market") and p.get("seller") != "market":
        score -= 40.0
    return score


def _sale_limit(league_rules: dict[str, Any] | None) -> int:
    rules = league_rules or {}
    eco = rules.get("economy") if isinstance(rules.get("economy"), dict) else {}
    try:
        limit = int(eco.get("sale_limit") or rules.get("sale_limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    return max(1, limit)


def _max_squad(league_rules: dict[str, Any] | None, fallback: int = 25) -> int:
    rules = league_rules or {}
    try:
        n = int(rules.get("max_squad") or fallback or 25)
    except (TypeError, ValueError):
        n = fallback
    return max(11, n)


def _offer_pct(offer: dict[str, Any], player: dict[str, Any] | None = None) -> float | None:
    pct = _f(offer.get("pct_of_vm"))
    if pct is not None:
        return pct
    amount = _money(offer.get("amount") or offer.get("bid"))
    vm = _money(offer.get("market_value") or (player or {}).get("price"))
    if vm <= 0:
        return None
    return amount / vm


def _clause_target_rows(gw_target_xi: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Huecos del once objetivo alcanzables solo por cláusula. Casi cubiertos, no."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    coverage = (gw_target_xi or {}).get("coverage") or {}
    near_ids = {
        _pid(s)
        for s in (coverage.get("near_slots") or [])
        if isinstance(s, dict) and _pid(s)
    }
    sources: list[Any] = []
    sources.extend(coverage.get("missing_slots") or [])
    sources.extend((gw_target_xi or {}).get("xi") or [])
    for slot in sources:
        if not isinstance(slot, dict):
            continue
        if slot.get("near"):
            continue
        if str(slot.get("reachable") or "") != "clause":
            continue
        pid = _pid(slot)
        if not pid or pid in near_ids or pid in seen:
            continue
        seen.add(pid)
        rows.append(slot)
    return rows


def _pick_hoy_clause(
    *,
    gw_target_xi: dict[str, Any] | None,
    rival_upgrades: list[dict[str, Any]] | None,
    remaining: float,
    accept_ids: set[str],
) -> dict[str, Any] | None:
    """Como mucho 1 cláusula de Hoy: cierra hueco no-near, ROI OK, cabe en margen."""
    by_upgrade = {
        _pid(r): r
        for r in (rival_upgrades or [])
        if isinstance(r, dict) and _pid(r)
    }
    best: tuple[float, dict[str, Any]] | None = None
    for slot in _clause_target_rows(gw_target_xi):
        pid = _pid(slot)
        if not pid or pid in accept_ids:
            continue
        rival = by_upgrade.get(pid) or {}
        cost = _money(
            rival.get("clause")
            or rival.get("bid")
            or slot.get("clause")
            or slot.get("acquisition_cost")
        )
        if cost <= 0 or cost > remaining + 1:
            continue
        if rival.get("solvency_blocked") or rival.get("budget_fit") == "blocked":
            continue
        vm = _money(
            rival.get("market_value")
            or rival.get("price")
            or slot.get("price")
            or slot.get("market_value")
        )
        upgrade = _f(rival.get("upgrade_score"))
        if upgrade is None:
            gap = (_f(slot.get("xpts")) or 0.0) - (_f(slot.get("your_xpts")) or 0.0)
            upgrade = max(30.0, gap * 10.0)
        roi_ok, roi_why = clause_roi_gate(
            upgrade_score=upgrade,
            clause=cost,
            market_value=vm or None,
            fills=True,
        )
        if not roi_ok:
            continue
        score = float(rival.get("clause_roi") or 0) * 10.0 + upgrade
        row = {**slot, **rival}
        row["clause"] = cost
        row["market_value"] = vm or row.get("market_value")
        row["upgrade_score"] = upgrade
        row["roi_why"] = roi_why
        row["closes_gw_target"] = True
        if best is None or score > best[0]:
            best = (score, row)
    if best is None:
        return None
    return best[1]


def _reachable_target_ids(gw_target_xi: dict[str, Any] | None) -> set[str]:
    """Huecos del once objetivo que este ciclo puede cerrar. Casi cubiertos, no."""
    ids: set[str] = set()
    coverage = (gw_target_xi or {}).get("coverage") or {}
    near_ids = {
        _pid(s)
        for s in (coverage.get("near_slots") or [])
        if isinstance(s, dict) and _pid(s)
    }
    for slot in coverage.get("missing_slots") or []:
        if not isinstance(slot, dict):
            continue
        if slot.get("near"):
            continue
        reach = str(slot.get("reachable") or "")
        if reach in ("daily_market", "free", "clause"):
            pid = _pid(slot)
            if pid and pid not in near_ids:
                ids.add(pid)
    for row in (gw_target_xi or {}).get("xi") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("ownership") or "") not in ("daily_market", "free"):
            continue
        if str(row.get("reachable") or "") == "no":
            continue
        pid = _pid(row)
        if pid and pid not in near_ids and not row.get("near"):
            ids.add(pid)
    return ids


def build_cycle_plan(
    *,
    me: dict[str, Any] | None = None,
    squad: list[dict[str, Any]] | None = None,
    opportunities: list[dict[str, Any]] | None = None,
    sales_state: dict[str, Any] | None = None,
    recommended_xi: dict[str, Any] | None = None,
    gw_target_xi: dict[str, Any] | None = None,
    league_rules: dict[str, Any] | None = None,
    market_cycle: dict[str, Any] | None = None,
    market_mode: str = "auction",
    max_squad: int | None = None,
    rival_upgrades: list[dict[str, Any]] | None = None,
    hours_to_jornada: float | None = None,
) -> dict[str, Any]:
    """
    Fuente de verdad de la pestaña Hoy.

    1) Aceptar ofertas del sistema (salvo outlier a la baja).
    2) Pujar/fichar si hay plazas libres (tras aceptar). El techo es maxDebt,
       no caja ≥ 0. Revalorización: solo libres del mercado, no listados de rivales.
    3) Como mucho 1 cláusula si cierra un hueco no-near del XI, ROI OK y cabe
       en el margen residual.
    4) Si el gasto deja negativo: solo si hay ventas rentables (banquillo, no XI)
       cuyo cobro llega antes de la jornada. Esas ventas salen en el plan.
    5) Listar banquillo cuyo VM ya no tira. Si aún revaloriza fuerte, solo
       con plantilla llena y un recambio de mercado que suba claramente más.
    """
    me = me or {}
    squad = list(squad or me.get("squad") or [])
    market = [
        o
        for o in (opportunities or [])
        if isinstance(o, dict) and (o.get("on_daily_market") or o.get("seller") == "market")
    ]
    state = sales_state or me.get("sales_state") or {}
    rules = league_rules or {}
    xi_ids = xi_owned_ids(recommended_xi)
    target_ids = _reachable_target_ids(gw_target_xi)
    listed_ids = {str(x) for x in (state.get("listed_ids") or []) if x}
    for p in squad:
        pid = _pid(p)
        if pid and (p.get("on_sale") or p.get("listed_for_sale")):
            listed_ids.add(pid)

    sale_limit = _sale_limit(rules)
    listed_count = len(listed_ids)
    sale_remaining = max(0, sale_limit - listed_count)
    cap = int(max_squad or _max_squad(rules))
    squad_n = len(squad)
    balance = _money(me.get("balance"))
    try:
        max_debt = float(me["max_debt"]) if me.get("max_debt") is not None else None
    except (TypeError, ValueError):
        max_debt = None
    pending = [o for o in (state.get("pending_offers") or []) if isinstance(o, dict)]
    by_id = {_pid(p): p for p in squad if _pid(p)}
    outlier_pct = float(getattr(config, "CYCLE_OFFER_OUTLIER_PCT", 0.82) or 0.82)
    max_bids = int(getattr(config, "CYCLE_MAX_BIDS", 3) or 3)
    max_lists = min(int(getattr(config, "CYCLE_MAX_LISTS", 5) or 5), sale_remaining)
    fixed = (market_mode or "auction") == "fixed"
    verb_bid = "ficha" if fixed else "puja"
    verb_bid_inf = "Fichar" if fixed else "Pujar"

    moves: list[dict[str, Any]] = []
    accept_ids: set[str] = set()
    cash_from_accepts = 0.0
    slots_from_accepts = 0

    for offer in pending:
        pid = _pid(offer)
        player = by_id.get(pid) or offer
        pct = _offer_pct(offer, player)
        amount = _money(offer.get("amount") or offer.get("bid"))
        vm = _money(offer.get("market_value") or player.get("price"))
        extra = {
            "amount": amount,
            "market_value": vm,
            "pct_of_vm": round(pct, 4) if pct is not None else None,
            "from_machine": bool(offer.get("from_machine", True)),
            "mister_url": state.get("mister_offers_url"),
        }
        if pct is not None and pct < outlier_pct:
            why = (
                f"Oferta a {pct * 100:.0f}% del VM ({_fmt_money(amount)} vs {_fmt_money(vm)}): "
                f"demasiado baja; no cierres esta venta."
            )
            moves.append(_player_ref(player, kind=KIND_DECLINE, why=why, extra=extra))
            continue
        why = (
            f"Oferta del sistema {_fmt_money(amount)}"
            + (f" vs {_fmt_money(vm)} de VM" if vm else "")
            + ". Cierra la venta: libera plaza y caja este ciclo."
        )
        moves.append(_player_ref(player, kind=KIND_ACCEPT, why=why, extra=extra))
        accept_ids.add(pid)
        cash_from_accepts += amount
        slots_from_accepts += 1

    free_slots = max(0, cap - squad_n + slots_from_accepts)
    spendable = mister_bid_cap(balance + cash_from_accepts, max_debt)
    cash_after_accepts = balance + cash_from_accepts

    mc = dict(market_cycle or {})
    hours_deadline = _f(hours_to_jornada)
    if hours_deadline is None:
        hours_deadline = _f(mc.get("hours_to_jornada")) or _f(me.get("hours_to_jornada"))
    hours_to_end = _f(mc.get("hours_to_end"))
    cycle_h = _f(mc.get("cycle_hours")) or float(getattr(config, "MARKET_CYCLE_HOURS", 24) or 24)
    # Listas este ciclo; al empezar el siguiente aceptas y cobra al instante.
    lag = hours_to_end if hours_to_end is not None else _f(mc.get("cash_lag_hours"))
    if lag is None:
        lag = 0.0 if fixed else max(cycle_h, 1.0)
    settle_new = bool(fixed) or sells_settle_before_deadline(
        hours_to_deadline=hours_deadline,
        cash_lag_hours=lag,
    )
    listed_lag = lag
    listed_timely = 0.0
    if settle_new or sells_settle_before_deadline(
        hours_to_deadline=hours_deadline,
        cash_lag_hours=listed_lag,
    ):
        for p in squad:
            pid = _pid(p)
            if pid and pid in listed_ids and pid not in accept_ids:
                listed_timely += _price(p)
    recover_base = _recover_pool(
        squad, xi_ids=xi_ids, skip_ids=accept_ids | listed_ids
    )
    recover_cap = _recovery_capacity(
        recover_base if settle_new else [],
        sale_remaining=sale_remaining,
        listed_timely=listed_timely,
    )

    def _covers_shortfall(new_spent: float) -> bool:
        short = new_spent - cash_after_accepts
        if short <= 1:
            return True
        return recover_cap + 1 >= short

    bid_cands: list[tuple[float, dict[str, Any]]] = []
    for o in market:
        pid = _pid(o)
        if not pid or pid in accept_ids:
            continue
        if o.get("solvency_blocked") or o.get("budget_fit") == "blocked":
            continue
        if o.get("gw_out") or (o.get("external") or {}).get("availability") in ("injured", "suspended"):
            continue
        if is_rival_market_listing(o):
            # El rival acepta la oferta del sistema al VM; no hay flip.
            continue
        cost = _money(o.get("bid") or o.get("puja_recomendada") or o.get("price"))
        if cost <= 0:
            continue
        d5 = _f(o.get("delta_5d"))
        strong = float(getattr(config, "CYCLE_STRONG_RISE", 0.08) or 0.08)
        if d5 is not None and d5 <= -strong and not _starter_coverage_hole(o):
            continue
        score = _bid_score(o)
        closes_target = pid in target_ids
        fills_hole = bool(
            closes_target
            or _starter_coverage_hole(o)
            or o.get("fills_coverage_gap")
            or o.get("fills_structural")
            or o.get("fills_need")
        )
        if not fills_hole:
            if o.get("debt_risk"):
                continue
            if o.get("budget_fit") not in ("comfortable", "tight", None):
                continue
        if closes_target:
            score += 36.0
            o = dict(o)
            o["closes_gw_target"] = True
        if score < 12 and not closes_target:
            continue
        bid_cands.append((score, o))
    bid_cands.sort(key=lambda x: -x[0])

    bids: list[dict[str, Any]] = []
    spent = 0.0
    used_pos: set[str] = set()
    if free_slots > 0:
        for score, o in bid_cands:
            if len(bids) >= min(max_bids, free_slots):
                break
            cost = _money(o.get("bid") or o.get("puja_recomendada") or o.get("price"))
            if spent + cost > spendable + 1:
                continue
            if not _covers_shortfall(spent + cost):
                continue
            pos = str(o.get("position") or "")
            if pos and pos in used_pos and len(bids) >= 1:
                continue
            d5 = _f(o.get("delta_5d"))
            why_bits = []
            if o.get("closes_gw_target"):
                why_bits.append("entra en el once objetivo de la jornada")
            if o.get("fills_coverage_gap") or o.get("fills_structural") or o.get("fills_need"):
                why_bits.append("cubre un hueco de plantilla")
            if d5 is not None:
                why_bits.append(f"revaloriza {_fmt_pct(d5)}")
            if o.get("decelerating") is False and o.get("rising"):
                why_bits.append("sigue al alza")
            why = (
                f"{'Ficha' if fixed else 'Puja por'} {o.get('name')} "
                f"({_fmt_money(cost)}"
                + (f", {'; '.join(why_bits)}" if why_bits else "")
                + ")."
            )
            extra = {
                "bid": cost,
                "amount": cost,
                "closes_gw_target": bool(o.get("closes_gw_target")),
                "appreciation_play": bool(
                    o.get("appreciation_play")
                    or (
                        (d5 or 0) >= 0.04
                        and o.get("rising")
                        and not o.get("decelerating")
                        and appreciation_play_score(o)[0] > 0
                    )
                ),
            }
            bids.append(_player_ref(o, kind=KIND_BID, why=why, extra=extra))
            spent += cost
            if pos:
                used_pos.add(pos)
        moves.extend(bids)

    clauses: list[dict[str, Any]] = []
    remaining = max(0.0, spendable - spent)
    slots_left = max(0, free_slots - len(bids))
    if slots_left > 0 and remaining > 0:
        picked = _pick_hoy_clause(
            gw_target_xi=gw_target_xi,
            rival_upgrades=rival_upgrades,
            remaining=remaining,
            accept_ids=accept_ids,
        )
        if picked:
            cost = _money(picked.get("clause") or picked.get("bid"))
            if not _covers_shortfall(spent + cost):
                picked = None
        if picked:
            cost = _money(picked.get("clause") or picked.get("bid"))
            why = (
                f"Cláusula de {picked.get('name')} ({_fmt_money(cost)}): "
                f"cierra un hueco del once objetivo"
                + (
                    f" frente a {picked.get('your_name')}"
                    if picked.get("your_name")
                    else ""
                )
                + "."
            )
            extra = {
                "bid": cost,
                "amount": cost,
                "clause": cost,
                "closes_gw_target": True,
                "owner_name": picked.get("owner_name") or picked.get("owner_team"),
            }
            clause_move = _player_ref(picked, kind=KIND_CLAUSE, why=why, extra=extra)
            clauses.append(clause_move)
            moves.append(clause_move)
            spent += cost

    next_targets = [
        o
        for _score, o in bid_cands
        if _pid(o) not in {_pid(b) for b in bids}
    ][:3]

    shortfall = max(0.0, spent - cash_after_accepts)
    recover_lists: list[dict[str, Any]] = []
    recover_ids: set[str] = set()
    recover_need = max(0.0, shortfall - listed_timely)
    if recover_need > 1 and settle_new and sale_remaining > 0:
        covered = 0.0
        for p in recover_base:
            if len(recover_lists) >= sale_remaining:
                break
            if covered >= recover_need - 1:
                break
            pid = _pid(p)
            proceeds = _price(p)
            if proceeds <= 0:
                continue
            recover_ids.add(pid)
            recover_lists.append(
                _player_ref(
                    p,
                    kind=KIND_LIST,
                    why=(
                        f"Pon en venta a {p.get('name')} (≈ {_fmt_money(proceeds)}): "
                        f"el siguiente ciclo aceptas y el dinero llega al instante, "
                        f"antes de la jornada."
                    ),
                    extra={
                        "expected_proceeds": proceeds,
                        "amount": proceeds,
                        "list_reason": "recover_debt",
                    },
                )
            )
            covered += proceeds
    if recover_lists:
        names = _join_names([m.get("name") or "" for m in recover_lists])
        note = (
            f"lista a {names}: el siguiente ciclo aceptas y recuperas el negativo "
            f"antes de la jornada"
        )
        for row in bids:
            why = (row.get("why") or "").rstrip(".")
            row["why"] = f"{why}; {note}."
            row["debt_recovery"] = True
        for row in clauses:
            why = (row.get("why") or "").rstrip(".")
            row["why"] = f"{why}; {note}."
            row["debt_recovery"] = True

    fade_cands: list[tuple[float, dict[str, Any]]] = []
    swap_cands: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for p in squad:
        pid = _pid(p)
        if not pid or pid in listed_ids or pid in accept_ids or pid in recover_ids:
            continue
        if pid in xi_ids:
            continue
        d5 = _f(p.get("delta_5d"))
        if _keep_riding(p):
            # Con plazas libres se puede fichar lo que más sube y conservar esta pieza.
            if free_slots > 0:
                continue
            beaters = [o for _s, o in bid_cands if _market_much_hotter(d5, o)]
            if not beaters:
                continue
            best_beater = max(beaters, key=lambda o: _f(o.get("delta_5d")) or 0.0)
            swap_cands.append((-(d5 or 0.0), p, best_beater))
            continue
        score = _list_score(p)
        if score < 28:
            continue
        fade_cands.append((score, p))
    swap_cands.sort(key=lambda x: x[0])
    fade_cands.sort(key=lambda x: -x[0])

    lists: list[dict[str, Any]] = list(recover_lists)
    swap_cap = min(max_lists, min(max_bids, len(bid_cands)))
    for _rank, p, beater in swap_cands:
        if len(lists) >= swap_cap:
            break
        d5 = _f(p.get("delta_5d"))
        md = _f(beater.get("delta_5d"))
        why = (
            f"Plantilla llena ({squad_n}/{cap}). "
            f"{beater.get('name')} revaloriza {_fmt_pct(md)} frente a su {_fmt_pct(d5)}. "
            f"Listarlo: el siguiente ciclo aceptas (≈ {_fmt_money(_price(p))}) y cobra al instante."
        )
        lists.append(
            _player_ref(
                p,
                kind=KIND_LIST,
                why=why,
                extra={
                    "expected_proceeds": _price(p),
                    "amount": _price(p),
                    "list_reason": "swap",
                    "swap_for_name": beater.get("name"),
                    "swap_for_delta_5d": md,
                },
            )
        )
    for score, p in fade_cands:
        if len(lists) >= max_lists:
            break
        d5 = _f(p.get("delta_5d"))
        bits = []
        if d5 is not None and d5 <= 0:
            bits.append("el VM ya no sube")
        elif p.get("decelerating"):
            bits.append("el VM se está quedando plano")
        else:
            bits.append("no entra en el once")
        if _lineup_pct(p) is not None and (_lineup_pct(p) or 0) < 45:
            bits.append("juega poco")
        why = (
            f"Pon en venta a {p.get('name')}: {', '.join(bits)}. "
            f"El siguiente ciclo aceptas (≈ {_fmt_money(_price(p))}) y el dinero llega al instante."
        )
        lists.append(
            _player_ref(
                p,
                kind=KIND_LIST,
                why=why,
                extra={
                    "expected_proceeds": _price(p),
                    "amount": _price(p),
                    "list_reason": "fade",
                },
            )
        )
    moves.extend(lists)

    value_sum = squad_value_summary(squad)
    constraints = {
        "squad_size": squad_n,
        "max_squad": cap,
        "free_slots": max(0, cap - squad_n),
        "free_slots_after_accepts": free_slots,
        "sale_limit": sale_limit,
        "listed_count": listed_count,
        "sale_remaining": sale_remaining,
        "balance": balance,
        "cash_from_accepts": round(cash_from_accepts, 0),
        "spendable": round(spendable, 0),
        "bid_cap": round(spendable, 0),
        "max_debt": max_debt,
        "market_mode": "fixed" if fixed else "auction",
        "projected_cash": round(cash_after_accepts - spent, 0),
        "debt_shortfall": round(shortfall, 0),
        "debt_recovery": bool(recover_lists or (shortfall > 1 and listed_timely >= shortfall - 1)),
        "sells_settle_before_gw": bool(settle_new),
    }

    headline, narrative = _compose_narrative(
        accepts=[m for m in moves if m["kind"] == KIND_ACCEPT],
        declines=[m for m in moves if m["kind"] == KIND_DECLINE],
        bids=bids,
        clauses=clauses,
        lists=lists,
        next_targets=next_targets,
        constraints=constraints,
        fixed=fixed,
        verb_bid=verb_bid,
        verb_bid_inf=verb_bid_inf,
    )

    mc = dict(market_cycle or {})
    cycle_block = {
        "hours_to_end": mc.get("hours_to_end"),
        "minutes_to_end": mc.get("minutes_to_end"),
        "current_ends_at": mc.get("current_ends_at"),
        "cycle_hours": mc.get("cycle_hours"),
        "source": mc.get("source"),
        "market_locked": mc.get("market_locked"),
    }

    kinds = {m["kind"] for m in moves}
    for k in FORBIDDEN_ACTIONS:
        if k in kinds:
            moves = [m for m in moves if m.get("kind") not in FORBIDDEN_ACTIONS]
            break

    return {
        "headline": headline,
        "narrative": narrative,
        "cycle": cycle_block,
        "moves": moves,
        "constraints": constraints,
        "squad_value": value_sum,
        "next_cycle_targets": [
            {
                "player_id": _pid(o),
                "name": o.get("name"),
                "position": o.get("position"),
                "delta_5d": o.get("delta_5d"),
                "price": _price(o),
            }
            for o in next_targets[:3]
        ],
        "counts": {
            "accept": sum(1 for m in moves if m["kind"] == KIND_ACCEPT),
            "list": sum(1 for m in moves if m["kind"] == KIND_LIST),
            "bid": sum(1 for m in moves if m["kind"] == KIND_BID),
            "clause": sum(1 for m in moves if m["kind"] == KIND_CLAUSE),
            "decline": sum(1 for m in moves if m["kind"] == KIND_DECLINE),
        },
    }


def _compose_narrative(
    *,
    accepts: list[dict[str, Any]],
    declines: list[dict[str, Any]],
    bids: list[dict[str, Any]],
    lists: list[dict[str, Any]],
    clauses: list[dict[str, Any]] | None = None,
    next_targets: list[dict[str, Any]],
    constraints: dict[str, Any],
    fixed: bool,
    verb_bid: str,
    verb_bid_inf: str,
) -> tuple[str, str]:
    parts: list[str] = []
    if accepts:
        names = _join_names([m.get("name") or "" for m in accepts])
        parts.append(
            f"Vende a {names} (oferta del sistema"
            + (
                f", {_fmt_money(accepts[0].get('amount'))} vs {_fmt_money(accepts[0].get('market_value'))} de VM"
                if len(accepts) == 1
                else ""
            )
            + "). Libera plaza y caja este ciclo."
        )
    if declines:
        names = _join_names([m.get("name") or "" for m in declines])
        parts.append(
            f"No cierres la venta de {names}: la oferta está claramente por debajo del valor de mercado."
        )
    if bids:
        bits = []
        for m in bids:
            label = m.get("name") or ""
            d = _fmt_pct(m.get("delta_5d"))
            if d:
                label = f"{label} ({d})"
            bits.append(label)
        parts.append(
            f"{'Ficha' if fixed else 'Puja por'} {_join_names(bits)}"
            + (
                f" con las plazas y el margen de deuda disponibles."
                if constraints.get("free_slots_after_accepts")
                else "."
            )
        )
    if clauses:
        names = _join_names([m.get("name") or "" for m in clauses])
        parts.append(
            f"Cláusula de {names} "
            f"({_fmt_money(clauses[0].get('clause') or clauses[0].get('amount'))}): "
            f"cierra hueco del once objetivo y cabe en el techo de deuda."
        )
    if lists:
        recover = [m for m in lists if m.get("list_reason") == "recover_debt"]
        names = _join_names([m.get("name") or "" for m in lists])
        swaps = [m for m in lists if m.get("list_reason") == "swap"]
        follow = ""
        if next_targets and constraints.get("free_slots_after_accepts", 0) <= 0:
            nxt = _join_names(
                [
                    (t.get("name") or "")
                    + (f" ({_fmt_pct(t.get('delta_5d'))})" if t.get("delta_5d") is not None else "")
                    for t in next_targets[:2]
                ]
            )
            if nxt:
                follow = (
                    f" El siguiente ciclo aceptas (≈ VM) y el dinero llega al instante; "
                    f"entonces podremos ir a por {nxt}."
                )
            else:
                follow = (
                    " El siguiente ciclo aceptas (≈ VM) y el dinero llega al instante; "
                    "entonces podremos ir a por los que más suban."
                )
        elif not bids:
            follow = " El siguiente ciclo aceptas (≈ VM) y el dinero llega al instante."
        if recover and len(recover) == len(lists):
            fade = (
                "su cobro tapa el negativo antes de la jornada"
                if len(recover) == 1
                else "su cobro tapa el negativo antes de la jornada"
            )
        elif recover:
            fade = "parte recupera el negativo antes de la jornada"
        elif swaps and len(swaps) == len(lists):
            fade = "plantilla al tope y el mercado revaloriza más"
        elif any((_f(m.get("delta_5d")) or 0) <= 0 for m in lists):
            fade = (
                "no entra en el once y su VM ya no tira"
                if len(lists) == 1
                else "no entran en el once y su VM ya no tira"
            )
        else:
            fade = (
                "no entra en el once y conviene sacarle el valor de mercado"
                if len(lists) == 1
                else "no entran en el once y conviene sacarles el valor de mercado"
            )
        parts.append(f"Pon en venta a {names}: {fade}.{follow}")

    if not parts:
        headline = "Sin movimientos de mercado"
        narrative = (
            "Este ciclo no hay movimientos de mercado. "
            "Plantilla y listados no ofrecen un intercambio rentable ahora."
        )
        return headline, narrative

    if accepts and (bids or clauses):
        headline = "Vende y " + ("ficha" if fixed else "puja")
    elif accepts:
        headline = "Cierra ventas"
    elif clauses and not bids:
        headline = "Cláusula este ciclo"
    elif bids and clauses:
        headline = f"{verb_bid_inf} y cláusula"
    elif bids and lists:
        headline = f"{verb_bid_inf} y pon en venta"
    elif bids:
        headline = verb_bid_inf + " este ciclo"
    elif lists:
        headline = "Pon en venta"
    else:
        headline = "Plan de este ciclo"

    narrative = " ".join(parts)
    return headline, narrative
