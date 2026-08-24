"""
Plan de un ciclo de mercado: texto + movimientos ejecutables.

Listar hoy → el sistema suele comprar el ciclo siguiente (oferta ≈ VM).
No hay wait / scout / avoid. Rotación de valor a 2 ciclos si la plantilla está llena.
"""

from __future__ import annotations

from typing import Any

import config
from competitive_actions import (
    _lineup_pct,
    _money,
    appreciation_play_score,
    xi_owned_ids,
)

KIND_ACCEPT = "accept_offer"
KIND_LIST = "list_for_sale"
KIND_BID = "bid"
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

    if len(prices) >= 2 and prices[-2] > 0:
        delta_cycle = (prices[-1] - prices[-2]) / prices[-2]
    lookback = min(3, max(1, len(prices) - 1))
    if len(prices) >= 2 and prices[-1 - lookback] > 0:
        delta_1d = (prices[-1] - prices[-1 - lookback]) / prices[-1 - lookback]
    if len(prices) >= 2 and prices[0] > 0:
        delta_5d = (prices[-1] - prices[0]) / prices[0]
    if len(prices) >= 4 and prices[0] > 0:
        mid_i = len(prices) // 2
        mid_p = prices[mid_i]
        if mid_p > 0:
            first_leg = (mid_p - prices[0]) / prices[0]
            second_leg = (prices[-1] - mid_p) / mid_p
            accel = second_leg - first_leg

    if delta_5d is None and delta_1d is not None:
        delta_5d = delta_1d
    if delta_5d is None and trend == "up":
        delta_5d = 0.02
    elif delta_5d is None and trend == "down":
        delta_5d = -0.02

    decelerating = bool(
        delta_5d is not None
        and delta_5d > 0
        and accel is not None
        and accel < -0.005
    )
    rising = bool(
        (delta_5d is not None and delta_5d >= float(getattr(config, "APPRECIATION_DELTA_MIN", 0.04)))
        or trend == "up"
        or (delta_cycle is not None and delta_cycle >= 0.02)
    )
    return {
        "delta_cycle": round(delta_cycle, 4) if delta_cycle is not None else None,
        "delta_1d": round(delta_1d, 4) if delta_1d is not None else None,
        "delta_5d": round(delta_5d, 4) if delta_5d is not None else None,
        "accel": round(accel, 4) if accel is not None else None,
        "decelerating": decelerating,
        "rising": rising,
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
            _price(row),
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


def _list_score(p: dict[str, Any]) -> float:
    """Más alto = mejor candidato a listar (banquillo que se frena)."""
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
            score -= 12.0
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


def _bid_score(p: dict[str, Any]) -> float:
    appr, _why = appreciation_play_score(p)
    score = float(appr)
    d5 = _f(p.get("delta_5d")) or 0.0
    strong = float(getattr(config, "CYCLE_STRONG_RISE", 0.08) or 0.08)
    if d5 >= 0.10:
        score += 20.0
    elif d5 >= strong:
        score += 12.0
    if p.get("fills_coverage_gap") or p.get("fills_structural") or p.get("fills_need"):
        score += 28.0
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


def build_cycle_plan(
    *,
    me: dict[str, Any] | None = None,
    squad: list[dict[str, Any]] | None = None,
    opportunities: list[dict[str, Any]] | None = None,
    sales_state: dict[str, Any] | None = None,
    recommended_xi: dict[str, Any] | None = None,
    league_rules: dict[str, Any] | None = None,
    market_cycle: dict[str, Any] | None = None,
    market_mode: str = "auction",
    max_squad: int | None = None,
) -> dict[str, Any]:
    """
    Fuente de verdad de la pestaña Hoy.

    1) Aceptar ofertas del sistema (salvo outlier a la baja).
    2) Pujar/fichar si hay plazas libres (tras aceptar) y caja real.
    3) Listar banquillo que se frena (el sistema compra el ciclo siguiente).
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
    spendable = max(0.0, balance + cash_from_accepts)

    bid_cands: list[tuple[float, dict[str, Any]]] = []
    for o in market:
        pid = _pid(o)
        if not pid or pid in accept_ids:
            continue
        if o.get("solvency_blocked") or o.get("debt_risk"):
            continue
        if o.get("gw_out") or (o.get("external") or {}).get("availability") in ("injured", "suspended"):
            continue
        cost = _money(o.get("bid") or o.get("puja_recomendada") or o.get("price"))
        if cost <= 0:
            continue
        score = _bid_score(o)
        if score < 12:
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
            pos = str(o.get("position") or "")
            if pos and pos in used_pos and len(bids) >= 1:
                continue
            d5 = _f(o.get("delta_5d"))
            why_bits = []
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
                "appreciation_play": bool(o.get("appreciation_play") or (d5 or 0) >= 0.04),
            }
            bids.append(_player_ref(o, kind=KIND_BID, why=why, extra=extra))
            spent += cost
            if pos:
                used_pos.add(pos)
        moves.extend(bids)

    next_targets = [
        o
        for _score, o in bid_cands
        if _pid(o) not in {_pid(b) for b in bids}
    ][:3]

    list_cands: list[tuple[float, dict[str, Any]]] = []
    for p in squad:
        pid = _pid(p)
        if not pid or pid in listed_ids or pid in accept_ids:
            continue
        if pid in xi_ids:
            continue
        score = _list_score(p)
        if score < 28:
            continue
        list_cands.append((score, p))
    list_cands.sort(key=lambda x: -x[0])

    lists: list[dict[str, Any]] = []
    for score, p in list_cands:
        if len(lists) >= max_lists:
            break
        d5 = _f(p.get("delta_5d"))
        bits = []
        if p.get("decelerating"):
            bits.append("la revalorización se está frenando")
        elif d5 is not None and d5 <= 0:
            bits.append("el VM ya no sube")
        else:
            bits.append("no entra en el once")
        if _lineup_pct(p) is not None and (_lineup_pct(p) or 0) < 45:
            bits.append("juega poco")
        why = (
            f"Pon en venta a {p.get('name')}: {', '.join(bits)}. "
            f"El sistema lo comprará el próximo ciclo (cobro ≈ {_fmt_money(_price(p))})."
        )
        lists.append(
            _player_ref(
                p,
                kind=KIND_LIST,
                why=why,
                extra={"expected_proceeds": _price(p), "amount": _price(p)},
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
        "market_mode": "fixed" if fixed else "auction",
    }

    headline, narrative = _compose_narrative(
        accepts=[m for m in moves if m["kind"] == KIND_ACCEPT],
        declines=[m for m in moves if m["kind"] == KIND_DECLINE],
        bids=bids,
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
            "decline": sum(1 for m in moves if m["kind"] == KIND_DECLINE),
        },
    }


def _compose_narrative(
    *,
    accepts: list[dict[str, Any]],
    declines: list[dict[str, Any]],
    bids: list[dict[str, Any]],
    lists: list[dict[str, Any]],
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
                f" con las plazas y el saldo disponibles."
                if constraints.get("free_slots_after_accepts")
                else "."
            )
        )
    if lists:
        names = _join_names([m.get("name") or "" for m in lists])
        fade = "no entran en el once y su revalorización se está frenando"
        if any(m.get("decelerating") for m in lists):
            fade = "no entran en el once y su revalorización se está frenando"
        else:
            fade = "no entran en el once y conviene sacarles el valor de mercado"
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
                    f" El sistema los comprará el próximo ciclo (cobro ≈ valor de mercado) "
                    f"y entonces podremos ir a por {nxt}."
                )
            else:
                follow = (
                    " El sistema los comprará el próximo ciclo (cobro ≈ valor de mercado) "
                    "y entonces podremos ir a por los que más suban."
                )
        elif not bids:
            follow = " El sistema los comprará el próximo ciclo (cobro ≈ valor de mercado)."
        parts.append(f"Pon en venta a {names}: {fade}.{follow}")

    if not parts:
        headline = "Sin movimientos de mercado"
        narrative = (
            "Este ciclo no hay movimientos de mercado. "
            "Plantilla y listados no ofrecen un intercambio rentable ahora."
        )
        return headline, narrative

    if accepts and bids:
        headline = "Vende y " + ("ficha" if fixed else "puja")
    elif accepts:
        headline = "Cierra ventas"
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
