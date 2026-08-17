"""
Cadencia de mercado por liga y modo bootstrap de once.

Dos relojes:
  - hours_to_jornada / cycles_left_before_gw
  - hours_to_cycle_end (cuánto queda del ciclo actual)
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any

import config

# LFM rápido (p. ej. MD con CAPITÁN): ciclos diarios anclados (hora local España ≈ UTC+2 en verano;
# Mister publica 5h / 13h / 21h en reglas de concurso).
LFM_FAST_CYCLE_ANCHORS_UTC = (3, 11, 19)  # 5h/13h/21h CEST ≈ 3/11/19 UTC


def derive_cycle_hours(
    rules: dict[str, Any] | None = None,
    *,
    market_mode: str | None = None,
) -> float:
    """
    Duración de un ciclo de mercado según normas Mister.

    market_speed: 1=24h, 2=12h, 3=8h (3 ciclos/día). Valores mayores → más rápido.
    """
    rules = rules or {}
    try:
        speed = int(rules.get("market_speed") or 1)
    except (TypeError, ValueError):
        speed = 1
    speed = max(1, min(speed, 6))
    base = float(getattr(config, "MARKET_CYCLE_HOURS", 24) or 24)
    # speed 1 → 24h, 2 → 12h, 3 → 8h
    cycle = base / float(speed)
    mode = (market_mode or rules.get("market_mode") or "auction").strip().lower()
    if mode == "fixed" and speed >= 3:
        cycle = min(cycle, 8.0)
    return max(1.0, cycle)


def derive_cash_lag_hours(cycle_hours: float, rules: dict[str, Any] | None = None) -> float:
    """Liquidez tras venta: en fixed/LFM suele ser inmediata; en subasta ~2 ciclos."""
    rules = rules or {}
    mode = (rules.get("market_mode") or "auction").strip().lower()
    if mode == "fixed" or rules.get("direct_transfer"):
        return max(0.0, cycle_hours * 0.25)
    return max(cycle_hours * 2.0, 1.0)


def parse_auction_cycle_ends(html: str | None) -> list[float]:
    """Timestamps unix futuros desde data-ends en listados de subasta."""
    if not html:
        return []
    now = datetime.now(timezone.utc).timestamp()
    out: list[float] = []
    for m in re.finditer(r'data-ends=["\'](\d+)["\']', html, re.I):
        try:
            ts = float(m.group(1))
        except (TypeError, ValueError):
            continue
        if ts > now:
            out.append(ts)
    return sorted(out)


def _next_anchor_cycle_end(
    now_ts: float,
    *,
    anchors_utc: tuple[int, ...] = LFM_FAST_CYCLE_ANCHORS_UTC,
) -> float:
    """Próximo cierre de ciclo en horas ancla UTC (mismo día o siguiente)."""
    dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    day_start = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc).timestamp()
    best: float | None = None
    for hour in anchors_utc:
        candidate = day_start + hour * 3600
        if candidate > now_ts + 30:
            best = candidate if best is None else min(best, candidate)
    if best is not None:
        return best
    # mañana primer ancla
    return day_start + 86400 + anchors_utc[0] * 3600


def _hours_until(ts: float, *, now_ts: float | None = None) -> float:
    now = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
    return max(0.0, (ts - now) / 3600.0)


def resolve_market_cycle(
    rules: dict[str, Any] | None = None,
    *,
    market_mode: str | None = None,
    hours_to_jornada: float | None = None,
    auction_ends_ts: list[float] | None = None,
    market_lock: bool | int | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    """
    Contexto operativo del mercado para una liga.

    Returns:
        cycle_hours, cash_lag_hours, current_ends_at, hours_to_end,
        cycles_left_before_gw, source, market_locked
    """
    rules = rules or {}
    mode = (market_mode or rules.get("market_mode") or "auction").strip().lower()
    cycle_hours = derive_cycle_hours(rules, market_mode=mode)
    cash_lag = derive_cash_lag_hours(cycle_hours, rules)

    now = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
    locked = bool(market_lock) if market_lock is not None else False

    ends_at: float | None = None
    source = "derived"

    ends = list(auction_ends_ts or [])
    if ends:
        ends_at = float(ends[0])
        source = "market_html"
    elif mode == "fixed":
        try:
            speed = int(rules.get("market_speed") or 1)
        except (TypeError, ValueError):
            speed = 1
        if speed >= 3:
            ends_at = _next_anchor_cycle_end(now, anchors_utc=LFM_FAST_CYCLE_ANCHORS_UTC)
            source = "lfm_anchors"
        else:
            # Próximo múltiplo de cycle_hours desde medianoche UTC
            day_sec = now % 86400
            cycle_sec = cycle_hours * 3600
            rem = cycle_sec - (day_sec % cycle_sec) if cycle_sec > 0 else cycle_sec
            if rem < 60:
                rem = cycle_sec
            ends_at = now + rem
            source = "derived"
    else:
        rem = cycle_hours * 3600
        ends_at = now + rem
        source = "derived"

    hours_to_end = _hours_until(ends_at, now_ts=now) if ends_at else cycle_hours

    cycles_left = None
    if hours_to_jornada is not None and cycle_hours > 0:
        cycles_left = max(0, int(math.floor(float(hours_to_jornada) / cycle_hours)))

    return {
        "cycle_hours": round(cycle_hours, 2),
        "cash_lag_hours": round(cash_lag, 2),
        "current_ends_at": int(ends_at) if ends_at else None,
        "hours_to_end": round(hours_to_end, 3),
        "minutes_to_end": round(hours_to_end * 60.0, 1),
        "cycles_left_before_gw": cycles_left,
        "source": source,
        "market_locked": locked,
        "market_mode": mode,
    }


def squad_position_counts(squad: list[dict[str, Any]] | None) -> dict[str, int]:
    counts = {"GK": 0, "DF": 0, "MF": 0, "FW": 0}
    for p in squad or []:
        pos = str(p.get("position") or "").upper()
        if pos in counts:
            counts[pos] += 1
    return counts


def xi_gap_positions(
    squad: list[dict[str, Any]] | None,
    *,
    xi_summary: dict[str, Any] | None = None,
) -> dict[str, int]:
    """
    Huecos mínimos por línea para poder alinear un once legal (heurística 4-4-2).
    """
    summary = xi_summary or {}
    if summary.get("complete"):
        return {"GK": 0, "DF": 0, "MF": 0, "FW": 0}
    counts = squad_position_counts(squad)
    # Mínimos para 1-4-4-2
    need = {
        "GK": max(0, 1 - counts["GK"]),
        "DF": max(0, 4 - counts["DF"]),
        "MF": max(0, 4 - counts["MF"]),
        "FW": max(0, 2 - counts["FW"]),
    }
    xi_count = int(summary.get("xi_count") or 0)
    xi_target = int(summary.get("xi_target") or 11)
    if xi_count < xi_target and sum(need.values()) == 0:
        # Plantilla corta en total: rellenar la línea más vacía
        if counts["FW"] == 0:
            need["FW"] = 1
        elif counts["DF"] < 3:
            need["DF"] = max(need["DF"], 1)
        elif counts["MF"] < 3:
            need["MF"] = max(need["MF"], 1)
    return need


def resolve_bootstrap_xi(
    *,
    squad: list[dict[str, Any]] | None,
    xi_summary: dict[str, Any] | None,
    hours_to_jornada: float | None,
    market_cycle: dict[str, Any] | None,
    competition_phase: str | None = None,
) -> dict[str, Any]:
    """
    Modo urgente: completar once antes que plantilla ideal.
    """
    summary = xi_summary or {}
    complete = bool(summary.get("complete"))
    xi_count = int(summary.get("xi_count") or 0)
    xi_target = int(summary.get("xi_target") or 11)
    squad_n = len(squad or [])
    gaps = xi_gap_positions(squad, xi_summary=summary)
    slots_short = max(0, xi_target - xi_count) if xi_target else max(0, 11 - squad_n)

    max_hours = float(getattr(config, "BOOTSTRAP_XI_MAX_HOURS", 240) or 240)
    urgent_cycle_h = float(getattr(config, "BOOTSTRAP_CYCLE_END_URGENT_HOURS", 3) or 3)

    hours = float(hours_to_jornada) if hours_to_jornada is not None else None
    mc = market_cycle or {}
    cycles_left = mc.get("cycles_left_before_gw")
    hours_to_cycle_end = mc.get("hours_to_end")

    within_window = hours is not None and hours <= max_hours
    phase = (competition_phase or "").strip().lower()
    needs_xi = not complete or squad_n < 11 or slots_short > 0

    active = bool(
        needs_xi
        and (
            within_window
            or (phase in ("active", "ramp") and squad_n < 11)
        )
    )

    cycle_urgent = False
    if hours_to_cycle_end is not None:
        try:
            cycle_urgent = float(hours_to_cycle_end) <= urgent_cycle_h
        except (TypeError, ValueError):
            cycle_urgent = False

    low_cycles = False
    if cycles_left is not None and slots_short > 0:
        try:
            low_cycles = int(cycles_left) <= max(slots_short, 2)
        except (TypeError, ValueError):
            low_cycles = False

    posture = "normal"
    if active:
        if cycle_urgent or low_cycles:
            posture = "buy_now"
        elif hours_to_cycle_end is not None and float(hours_to_cycle_end) > urgent_cycle_h * 2:
            posture = "can_wait_cycle"
        else:
            posture = "buy_now"

    return {
        "active": active,
        "complete": complete,
        "xi_count": xi_count,
        "xi_target": xi_target,
        "slots_short": slots_short,
        "squad_size": squad_n,
        "position_gaps": gaps,
        "posture": posture,
        "cycle_urgent": cycle_urgent,
        "low_cycles": low_cycles,
        "hours_to_jornada": hours,
        "max_hours_window": max_hours,
    }


def adjust_funding_for_bootstrap(
    funding: dict[str, Any],
    *,
    bootstrap: dict[str, Any],
    balance: float,
    opportunities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    En bootstrap no reservar caja para objetivos del board fuera del mercado de hoy.
    Reserva = suma de titulares operables hoy que cubren huecos del once.
    """
    if not bootstrap.get("active"):
        return funding
    gaps = bootstrap.get("position_gaps") or {}
    gap_pos = {p for p, n in gaps.items() if int(n or 0) > 0}
    if not gap_pos:
        return funding

    daily_cost = 0.0
    selected: list[dict[str, Any]] = []
    for o in opportunities or []:
        if not o.get("on_daily_market"):
            continue
        pos = o.get("position")
        if pos not in gap_pos:
            continue
        ext = o.get("external") or {}
        lp = ext.get("lineup_prob_ext") or o.get("lineup_prob")
        try:
            lp_f = float(lp) * 100 if lp is not None and float(lp) <= 1 else float(lp or 0)
        except (TypeError, ValueError):
            lp_f = 0.0
        if lp_f < 40 and not o.get("in_lineup"):
            continue
        cost = float(o.get("price") or o.get("puja_recomendada") or 0)
        if cost <= 0 or cost > balance:
            continue
        selected.append(
            {
                "position": pos,
                "need": "bootstrap_xi",
                "cost": cost,
                "label": f"Once: {o.get('name')}",
                "on_daily_market": True,
                "primary_player_id": o.get("id"),
                "primary_name": o.get("name"),
            }
        )
        daily_cost += cost
        if len(selected) >= max(3, len(gap_pos)):
            break

    if daily_cost <= 0:
        # Al menos no bloquear toda la caja en ideal fuera de mercado
        daily_cost = min(float(funding.get("cash_reserved") or 0), balance * 0.35)
        if daily_cost <= 0:
            daily_cost = min(balance * 0.5, 5_000_000)

    out = dict(funding)
    out["cash_reserved"] = round(min(daily_cost, balance), 0)
    out["funding_target"] = out["cash_reserved"]
    out["funding_shortfall"] = max(0.0, float(out["funding_target"]) - balance)
    out["bootstrap_xi"] = True
    if selected:
        out["gap_costs"] = selected
        out["positions"] = [g.get("position") for g in selected if g.get("position")]
    return out


def bootstrap_buy_cap(
    *,
    free_slots: int,
    bootstrap: dict[str, Any] | None,
    fixed: bool,
) -> int:
    """Tope de buy_now: más holgado en bootstrap con muchas plazas libres."""
    b = bootstrap or {}
    if not b.get("active"):
        return min(2 if fixed else 4, free_slots if free_slots > 0 else 0)
    slots_short = int(b.get("slots_short") or 0)
    cap = max(slots_short, 2 if fixed else 3)
    cap = min(cap, 6 if fixed else 8)
    return min(cap, free_slots if free_slots > 0 else 0)
