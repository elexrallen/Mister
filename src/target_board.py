"""
Tablero diario de jugadores objetivo por hueco estructural.

Proxy de puntaje esperado: production_score + ff_mister_avg × titularidad.
Persiste entre runs en public/data/leagues/<slug>/target_board.json.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from competitive_actions import budget_fit, target_tier_from_budget_fit

log = logging.getLogger("target_board")

TARGETS_PER_SLOT = 4
MAX_DROPPED_DAYS = 5
PATCH_MAX_FRACTION_OF_RESERVE = 0.15  # parche ≤ 15% de la reserva del primary Alta


def _money(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def target_board_path(slug: str) -> Path:
    return config.LEAGUES_DIR / slug / "target_board.json"


def load_previous_board(slug: str) -> dict[str, Any] | None:
    path = target_board_path(slug)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        log.warning("target_board ilegible (%s): %s", slug, exc)
        return None


def save_target_board(slug: str, board: dict[str, Any]) -> Path:
    path = target_board_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(board, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _lineup_pct(p: dict[str, Any]) -> float | None:
    ext = p.get("external") or {}
    if ext.get("lineup_prob_ext") is not None:
        try:
            return float(ext["lineup_prob_ext"])
        except (TypeError, ValueError):
            return None
    gw = p.get("gw_lineup_prob")
    if gw is None:
        gw = ext.get("gw_lineup_prob")
    if gw is not None:
        try:
            return float(gw)
        except (TypeError, ValueError):
            return None
    if p.get("lineup_prob") is not None:
        try:
            return float(p["lineup_prob"]) * 100.0
        except (TypeError, ValueError):
            return None
    return None


def _production(p: dict[str, Any]) -> float | None:
    for key in ("production_score",):
        if p.get(key) is not None:
            try:
                return float(p[key])
            except (TypeError, ValueError):
                pass
    ext = p.get("external") or {}
    if ext.get("production_score") is not None:
        try:
            return float(ext["production_score"])
        except (TypeError, ValueError):
            return None
    return None


def _ff_avg(p: dict[str, Any]) -> float | None:
    for key in ("ff_mister_avg", "mister_avg", "form"):
        if p.get(key) is not None:
            try:
                return float(p[key])
            except (TypeError, ValueError):
                pass
    ext = p.get("external") or {}
    for key in ("ff_mister_avg",):
        if ext.get(key) is not None:
            try:
                return float(ext[key])
            except (TypeError, ValueError):
                return None
    return None


def ep_score(p: dict[str, Any]) -> float:
    """
    Puntaje esperado 0–100 approx:
    0.55*production + 0.25*(ff_avg/8*100) + 0.20*lineup_pct
    """
    prod = _production(p)
    ff = _ff_avg(p)
    lp = _lineup_pct(p)
    parts: list[tuple[float, float]] = []
    if prod is not None:
        parts.append((0.55, max(0.0, min(100.0, prod))))
    if ff is not None:
        parts.append((0.25, max(0.0, min(100.0, (ff / 8.0) * 100.0))))
    if lp is not None:
        parts.append((0.20, max(0.0, min(100.0, lp))))
    if not parts:
        return 0.0
    wsum = sum(w for w, _ in parts)
    return round(sum(w * v for w, v in parts) / wsum, 1)


def _buy_price(p: dict[str, Any]) -> float:
    return _money(p.get("puja_recomendada") or p.get("clause") or p.get("price") or p.get("market_value"))


def _delta_5d(p: dict[str, Any], price_series: dict[str, list[float]] | None) -> float | None:
    if p.get("delta_5d") is not None:
        try:
            return float(p["delta_5d"])
        except (TypeError, ValueError):
            pass
    pid = str(p.get("id") or p.get("player_id") or "")
    series = (price_series or {}).get(pid) or []
    if len(series) < 2:
        return None
    try:
        base = float(series[0])
        cur = float(series[-1])
        if base <= 0:
            return None
        return (cur - base) / base
    except (TypeError, ValueError):
        return None


def _value_note(delta: float | None) -> str:
    if delta is None:
        return "stable"
    if delta >= 0.05:
        return "rising"
    if delta <= -0.05:
        return "falling"
    return "stable"


def _needs_for_board(structural_needs: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """
    Huecos Alta siempre; Media de profundidad solo si no hay Alta en esa posición.
    """
    needs = list(structural_needs or [])
    alta_pos = {n.get("position") for n in needs if n.get("priority") == "Alta" and n.get("position")}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for n in needs:
        key = str(n.get("need") or n.get("position") or "")
        if not key or key in seen:
            continue
        prio = str(n.get("priority") or "Media")
        pos = n.get("position")
        ntype = str(n.get("need") or "")
        if prio == "Alta":
            seen.add(key)
            out.append(n)
            continue
        if prio == "Media" and (ntype.startswith("depth_") or ntype in ("gk_tandem",)):
            if pos and pos in alta_pos:
                continue
            seen.add(key)
            out.append(n)
    return out


def _candidate_ok(p: dict[str, Any], need: dict[str, Any]) -> bool:
    pos = need.get("position")
    if pos and p.get("position") != pos:
        return False
    if p.get("sample_thin") and (_production(p) or 0) < 50:
        return False
    avail = (p.get("external") or {}).get("availability") or (
        "injured" if p.get("injury") else "unknown"
    )
    if avail in ("injured", "suspended"):
        return False
    if p.get("gw_out") or (p.get("external") or {}).get("gw_out"):
        return False
    price = _buy_price(p)
    if price <= 0:
        return False
    floor = need.get("min_price")
    ceil = need.get("max_price")
    try:
        floor_f = float(floor) if floor is not None else None
    except (TypeError, ValueError):
        floor_f = None
    try:
        ceil_f = float(ceil) if ceil is not None else None
    except (TypeError, ValueError):
        ceil_f = None
    # Banda: [min*0.6, max] — fw_top exige suelo; depth más flexible
    ntype = str(need.get("need") or "")
    if floor_f is not None and ntype in ("fw_top", "mf_starter", "df_starter", "gk_starter"):
        if price < floor_f * 0.6:
            return False
    if ceil_f is not None and price > ceil_f:
        return False
    lp = _lineup_pct(p)
    fills = bool(p.get("fills_structural") or p.get("fills_need") or p.get("fills_coverage_gap"))
    # Titularidad usable o sin señal pero con producción decente
    if lp is not None and lp < 45 and not fills:
        return False
    if lp is None and (_production(p) or 0) < 35 and (_ff_avg(p) or 0) < 3.5:
        return False
    return True


def _status_for(
    p: dict[str, Any],
    *,
    owned_ids: set[str],
) -> str:
    pid = str(p.get("id") or "")
    if pid and pid in owned_ids:
        return "acquired"
    if p.get("clause_known") and p.get("clause") is not None and not p.get("on_daily_market"):
        # rival clause candidate
        if p.get("owner_team") or p.get("owner_name") or p.get("seller") not in (None, "market", "free"):
            return "clause"
    if p.get("on_daily_market") or p.get("seller") == "market":
        return "on_daily"
    if p.get("clause_known") and _money(p.get("clause")) > 0:
        return "clause"
    return "watching"


def _prev_targets_index(prev: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not prev:
        return out
    for slot in prev.get("slots") or []:
        for t in slot.get("targets") or []:
            pid = str(t.get("player_id") or "")
            if pid:
                out[pid] = t
        prim = slot.get("primary_target") or {}
        pid = str(prim.get("player_id") or "")
        if pid and pid not in out:
            out[pid] = prim
    return out


def build_target_board(
    *,
    slug: str,
    structural_needs: list[dict[str, Any]] | None,
    candidates: list[dict[str, Any]],
    balance: float,
    squad: list[dict[str, Any]] | None = None,
    squad_value: float | None = None,
    price_series: dict[str, list[float]] | None = None,
    previous: dict[str, Any] | None = None,
    market_mode: str = "auction",
) -> dict[str, Any]:
    """
    Construye el tablero de objetivos del día y lo fusiona con el board previo.
    """
    bal = max(0.0, float(balance or 0))
    sval = float(squad_value or 0) or sum(_money(p.get("price") or p.get("market_value")) for p in (squad or []))
    owned_ids = {str(p.get("id")) for p in (squad or []) if p.get("id") is not None}
    prev = previous if previous is not None else load_previous_board(slug)
    prev_idx = _prev_targets_index(prev)
    needs = _needs_for_board(structural_needs)
    slots: list[dict[str, Any]] = []
    cash_reserved = 0.0
    primary_targets: list[dict[str, Any]] = []

    for need in needs:
        ntype = str(need.get("need") or "")
        pos = need.get("position")
        prio = str(need.get("priority") or "Media")
        try:
            min_p = float(need["min_price"]) if need.get("min_price") is not None else None
        except (TypeError, ValueError):
            min_p = None
        try:
            max_p = float(need["max_price"]) if need.get("max_price") is not None else None
        except (TypeError, ValueError):
            max_p = None

        ranked: list[dict[str, Any]] = []
        for p in candidates:
            if not _candidate_ok(p, need):
                continue
            price = _buy_price(p)
            ep = ep_score(p)
            if ep < 20 and (_lineup_pct(p) or 0) < 70:
                continue
            price_m = max(price / 1_000_000.0, 0.4)
            value_ratio = ep / price_m
            delta = _delta_5d(p, price_series)
            bf = budget_fit(price, bal, min_cost=_money(p.get("puja_minima") or p.get("price")))
            tier = p.get("target_tier") or target_tier_from_budget_fit(bf)
            status = _status_for(p, owned_ids=owned_ids)
            pid = str(p.get("id") or "")
            prev_t = prev_idx.get(pid) or {}
            ranked.append(
                {
                    "player_id": pid,
                    "name": p.get("name"),
                    "position": p.get("position") or pos,
                    "team": p.get("team"),
                    "tier": tier,
                    "ep_score": ep,
                    "price": round(price, 0),
                    "afford_now": bf in ("comfortable", "tight"),
                    "budget_fit": bf,
                    "delta_5d": round(delta, 4) if delta is not None else None,
                    "value_note": _value_note(delta),
                    "value_ratio": round(value_ratio, 2),
                    "lineup_prob": _lineup_pct(p),
                    "production_score": _production(p),
                    "ff_mister_avg": _ff_avg(p),
                    "on_daily_market": bool(p.get("on_daily_market") or p.get("seller") == "market"),
                    "status": status,
                    "why": (
                        f"EP {ep:.0f} · {price:,.0f} € · "
                        f"{'titular ' + str(int(_lineup_pct(p) or 0)) + '%' if _lineup_pct(p) is not None else 'sin %'} · "
                        f"tier {tier}"
                    ),
                    "added_at": prev_t.get("added_at") or _now_iso(),
                    "miss_days": 0 if status != "dropped" else int(prev_t.get("miss_days") or 0),
                    "clause": p.get("clause") if p.get("clause_known") else None,
                    "owner_name": p.get("owner_name") or p.get("owner_team"),
                }
            )

        # Preferir realistic, luego EP/€, luego EP
        ranked.sort(
            key=lambda t: (
                0 if t.get("tier") == "realistic" else (1 if t.get("tier") == "stretch" else 2),
                0 if t.get("status") == "on_daily" else (1 if t.get("status") == "clause" else 2),
                -float(t.get("value_ratio") or 0),
                -float(t.get("ep_score") or 0),
            )
        )
        targets = ranked[:TARGETS_PER_SLOT]

        # Primary = mejor realistic asequible; si no, mejor stretch; si no, primero
        primary = next((t for t in targets if t.get("tier") == "realistic" and t.get("afford_now")), None)
        if primary is None:
            primary = next((t for t in targets if t.get("tier") == "realistic"), None)
        if primary is None and targets:
            primary = targets[0]

        reserve = float(primary["price"]) if primary and primary.get("tier") in ("realistic", "stretch") else 0.0
        if primary and not primary.get("afford_now") and primary.get("tier") == "aspirational":
            # No reservar aspirational completo; reservar min_price del need o 0
            reserve = float(min_p or 0)

        if prio == "Alta" and primary and primary.get("afford_now"):
            cash_reserved += reserve
            primary_targets.append(
                {
                    "player_id": primary.get("player_id"),
                    "name": primary.get("name"),
                    "need": ntype,
                    "position": pos,
                    "price": primary.get("price"),
                    "ep_score": primary.get("ep_score"),
                    "status": primary.get("status"),
                }
            )

        # Patch policy: no gastar más del 15% de la reserva Alta de este slot (o 500k)
        if prio == "Alta" and reserve > 0:
            max_patch = max(500_000.0, reserve * PATCH_MAX_FRACTION_OF_RESERVE)
            # También limitar para no bajar balance por debajo de cash_reserved acumulado
            allow_patch = True
        elif prio == "Media":
            max_patch = min(2_000_000.0, bal * 0.1)
            allow_patch = True
        else:
            max_patch = 0.0
            allow_patch = False

        slots.append(
            {
                "need": ntype,
                "position": pos,
                "priority": prio,
                "reason": need.get("reason"),
                "budget_envelope": {
                    "cash_reserve_for_slot": round(reserve, 0),
                    "min_price": min_p,
                    "max_price": max_p,
                    "squad_value_share": round(reserve / sval, 4) if sval > 0 else None,
                },
                "targets": targets,
                "primary_target": primary,
                "patch_policy": {
                    "allow": allow_patch,
                    "max_spend": round(max_patch, 0),
                    "note": (
                        "Parche solo si no baja la reserva del primary"
                        if prio == "Alta"
                        else "Profundidad: parche barato OK"
                    ),
                },
            }
        )

    # Marcar dropped del board previo que ya no están
    current_ids = {
        str(t.get("player_id"))
        for s in slots
        for t in (s.get("targets") or [])
        if t.get("player_id")
    }
    dropped: list[dict[str, Any]] = []
    for pid, old in prev_idx.items():
        if pid in current_ids or pid in owned_ids:
            continue
        miss = int(old.get("miss_days") or 0) + 1
        if miss > MAX_DROPPED_DAYS:
            continue
        row = dict(old)
        row["status"] = "dropped"
        row["miss_days"] = miss
        dropped.append(row)

    # Reserva total: no superar balance (si supera, shortfall)
    cash_reserved = min(cash_reserved, bal) if cash_reserved else 0.0
    # Recalcular max_spend global de parches: balance - cash_reserved
    residual_for_patches = max(0.0, bal - cash_reserved)
    for s in slots:
        pp = s.get("patch_policy") or {}
        if pp.get("allow"):
            pp["max_spend"] = round(min(float(pp.get("max_spend") or 0), residual_for_patches), 0)
            # Si no queda margen, no permitir parche en needs Alta
            if s.get("priority") == "Alta" and residual_for_patches < 200_000:
                pp["allow"] = False
                pp["max_spend"] = 0
            s["patch_policy"] = pp

    board = {
        "generated_at": _now_iso(),
        "league_slug": slug,
        "market_mode": market_mode,
        "balance": bal,
        "squad_value": sval,
        "cash_reserved": round(cash_reserved, 0),
        "residual_after_reserve": round(max(0.0, bal - cash_reserved), 0),
        "slots": slots,
        "primary_targets": primary_targets,
        "dropped": dropped[:12],
        "summary": {
            "slots": len(slots),
            "targets": sum(len(s.get("targets") or []) for s in slots),
            "on_daily": sum(
                1
                for s in slots
                for t in (s.get("targets") or [])
                if t.get("status") == "on_daily"
            ),
            "cash_reserved": round(cash_reserved, 0),
        },
    }
    return board


def funding_plan_from_board(
    board: dict[str, Any] | None,
    *,
    balance: float | None = None,
) -> dict[str, Any]:
    """Funding anclado a primaries realistic del board (no mínimo genérico del pool)."""
    bal = max(0.0, float(balance if balance is not None else (board or {}).get("balance") or 0))
    gaps: list[dict[str, Any]] = []
    for slot in (board or {}).get("slots") or []:
        if slot.get("priority") != "Alta":
            continue
        primary = slot.get("primary_target") or {}
        cost = _money(primary.get("price") or (slot.get("budget_envelope") or {}).get("cash_reserve_for_slot"))
        if cost <= 0:
            continue
        gaps.append(
            {
                "position": slot.get("position"),
                "need": slot.get("need"),
                "cost": cost,
                "label": slot.get("reason") or slot.get("need") or slot.get("position"),
                "no_affordable_candidate": not bool(primary.get("afford_now")),
                "primary_player_id": primary.get("player_id"),
                "primary_name": primary.get("name"),
                "ep_score": primary.get("ep_score"),
                "status": primary.get("status"),
            }
        )
    gaps.sort(key=lambda g: -float(g.get("cost") or 0))
    selected = gaps[:3]
    funding_target = sum(float(g["cost"]) for g in selected)
    funding_shortfall = max(0.0, funding_target - bal)
    cheapest = min((float(g["cost"]) for g in gaps), default=None)
    cash_tight = funding_shortfall > 0 or (cheapest is not None and bal < cheapest)
    if any(g.get("no_affordable_candidate") for g in selected):
        cash_tight = True
    return {
        "funding_target": funding_target,
        "funding_shortfall": funding_shortfall,
        "cash_tight": cash_tight,
        "gap_costs": selected,
        "all_gap_costs": gaps,
        "positions": [g.get("position") for g in selected if g.get("position")],
        "cheapest_need": cheapest,
        "primary_targets": list((board or {}).get("primary_targets") or []),
        "cash_reserved": float((board or {}).get("cash_reserved") or funding_target),
        "from_target_board": True,
    }


def board_objective_ids(board: dict[str, Any] | None) -> set[str]:
    ids: set[str] = set()
    for s in (board or {}).get("slots") or []:
        for t in s.get("targets") or []:
            if t.get("player_id"):
                ids.add(str(t["player_id"]))
        prim = s.get("primary_target") or {}
        if prim.get("player_id"):
            ids.add(str(prim["player_id"]))
    return ids


def board_primary_ids(board: dict[str, Any] | None) -> set[str]:
    return {
        str(t["player_id"])
        for t in (board or {}).get("primary_targets") or []
        if t.get("player_id")
    }


def max_patch_spend(board: dict[str, Any] | None) -> float:
    """Techo global de parche = residual tras reservas."""
    if not board:
        return float(getattr(config, "PACKAGE_SECONDARY_MAX", 2_500_000))
    residual = float(board.get("residual_after_reserve") or 0)
    slot_caps = [
        float((s.get("patch_policy") or {}).get("max_spend") or 0)
        for s in (board.get("slots") or [])
        if (s.get("patch_policy") or {}).get("allow")
    ]
    if not slot_caps:
        return min(residual, 500_000.0)
    return min(residual, max(slot_caps))


def patches_allowed(board: dict[str, Any] | None) -> bool:
    if not board:
        return True
    return any((s.get("patch_policy") or {}).get("allow") for s in (board.get("slots") or []))


__all__ = [
    "build_target_board",
    "funding_plan_from_board",
    "load_previous_board",
    "save_target_board",
    "target_board_path",
    "board_objective_ids",
    "board_primary_ids",
    "max_patch_spend",
    "patches_allowed",
    "ep_score",
]
