"""
Once objetivo de la jornada: 11 del pool, sin filtro de propiedad ni caja.

Ranking = xPts. El cruce explica. La cobertura compara con tu once real.
"""

from __future__ import annotations

from typing import Any

import config
from competitive_actions import build_recommended_gw_xi, _money

NEAR_XPTS_RATIO = 0.85


def _pid(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    return str(row.get("player_id") or row.get("id") or "").strip()


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _squad_ids(squad: list[dict[str, Any]] | None) -> set[str]:
    return {_pid(p) for p in (squad or []) if _pid(p)}


def resolve_ownership(
    player: dict[str, Any],
    *,
    my_id: str,
    squad_ids: set[str],
    balance: float,
    clauses_enabled: bool,
) -> tuple[str, str]:
    """
    (`ownership`, `reachable`).

    reachable: daily_market | free | clause | no
    Para `owned` reachable es vacío (ya lo tienes).
    """
    pid = _pid(player)
    oid = str(player.get("owner_id") or "").strip()
    mine = bool(pid and pid in squad_ids) or (bool(my_id) and oid == str(my_id))
    if mine:
        return "owned", ""
    if player.get("on_daily_market") or player.get("seller") == "market":
        return "daily_market", "daily_market"
    if not oid or oid in ("0", "None"):
        return "free", "free"
    clause = _f(player.get("clause"))
    if (
        clauses_enabled
        and player.get("clause_known")
        and clause is not None
        and clause > 0
        and clause <= balance + 1
    ):
        return "rival", "clause"
    return "rival", "no"


def _xpts_total(block: dict[str, Any] | None) -> float:
    summary = (block or {}).get("summary") or {}
    raw = summary.get("xpts_total")
    val = _f(raw)
    if val is not None:
        return val
    total = 0.0
    for row in (block or {}).get("xi") or []:
        total += _f(row.get("xpts")) or 0.0
    cap = (block or {}).get("captain") or {}
    total += _f(cap.get("expected_gain")) or 0.0
    return total


def _pick_best_formation(
    pool: list[dict[str, Any]],
    *,
    matchday: dict[str, Any] | None,
    captain_rule: dict[str, Any] | None,
) -> dict[str, Any]:
    best: dict[str, Any] | None = None
    best_x = -1.0
    seen: set[tuple] = set()
    names = list(getattr(config, "IDEAL_FORMATIONS", ()) or ())
    if not names:
        names = ["4-3-3"]
    for name in names:
        cand = build_recommended_gw_xi(
            pool,
            formation=name,
            matchday=matchday,
            captain_rule=captain_rule,
        )
        shape = cand.get("shape") or {}
        key = tuple(sorted((str(k), int(v)) for k, v in shape.items()))
        if key in seen:
            continue
        seen.add(key)
        xpts = _xpts_total(cand)
        complete = bool((cand.get("summary") or {}).get("complete"))
        score = (1 if complete else 0, xpts)
        best_score = (1 if best and (best.get("summary") or {}).get("complete") else 0, best_x)
        if best is None or score > best_score:
            best = cand
            best_x = xpts
    return best or build_recommended_gw_xi(
        pool, matchday=matchday, captain_rule=captain_rule
    )


def _best_owned_at_pos(
    recommended_xi: dict[str, Any] | None,
    position: str,
) -> dict[str, Any] | None:
    rows = [
        r
        for r in (recommended_xi or {}).get("xi") or []
        if str(r.get("position") or "").upper() == position
    ]
    if not rows:
        return None
    return max(rows, key=lambda r: _f(r.get("xpts")) or 0.0)


def build_gw_target_xi(
    pool: list[dict[str, Any]] | None,
    *,
    squad: list[dict[str, Any]] | None = None,
    recommended_xi: dict[str, Any] | None = None,
    matchday: dict[str, Any] | None = None,
    captain_rule: dict[str, Any] | None = None,
    me: dict[str, Any] | None = None,
    league_rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Once de máximo xPts del universo. Sin filtro de dueño ni presupuesto.
    """
    me = me or {}
    rules = league_rules or {}
    my_id = str(me.get("team_id") or me.get("id_uc") or "")
    squad_ids = _squad_ids(squad)
    balance = _money(me.get("balance"))
    clauses_enabled = bool(rules.get("clauses"))

    by_id: dict[str, dict[str, Any]] = {}
    for src in list(pool or []) + list(squad or []):
        pid = _pid(src)
        if pid:
            by_id[pid] = src

    assembled = _pick_best_formation(
        list(pool or []),
        matchday=matchday,
        captain_rule=captain_rule,
    )
    xi: list[dict[str, Any]] = []
    owned_ids: list[str] = []
    missing: list[dict[str, Any]] = []
    near: list[dict[str, Any]] = []

    for row in assembled.get("xi") or []:
        out = dict(row)
        pid = _pid(out)
        src = by_id.get(pid) or {}
        ownership, reachable = resolve_ownership(
            {**src, **out},
            my_id=my_id,
            squad_ids=squad_ids,
            balance=balance,
            clauses_enabled=clauses_enabled,
        )
        out["ownership"] = ownership
        out["reachable"] = reachable or None
        out["owner_name"] = src.get("owner_name") or out.get("owner_name")
        out["owner_id"] = src.get("owner_id") or out.get("owner_id")
        if src.get("clause") is not None:
            out["clause"] = src.get("clause")
        if src.get("on_daily_market") is not None:
            out["on_daily_market"] = src.get("on_daily_market")
        matchup = src.get("matchup") if isinstance(src.get("matchup"), dict) else None
        if matchup:
            out["matchup"] = matchup
            extra = matchup.get("why")
            if extra and extra not in str(out.get("why") or ""):
                out["why"] = f"{out.get('why') or ''} · {extra}".strip(" ·")
        if ownership == "owned":
            owned_ids.append(pid)
        else:
            pos = str(out.get("position") or "").upper()
            yours = _best_owned_at_pos(recommended_xi, pos)
            yours_x = _f((yours or {}).get("xpts")) or 0.0
            target_x = _f(out.get("xpts")) or 0.0
            almost = bool(
                yours
                and target_x > 0
                and yours_x >= target_x * NEAR_XPTS_RATIO
            )
            slot = {
                "slot": out.get("slot"),
                "player_id": pid,
                "name": out.get("name"),
                "position": out.get("position"),
                "ownership": ownership,
                "reachable": reachable or "no",
                "owner_name": out.get("owner_name"),
                "price": out.get("price"),
                "clause": out.get("clause"),
                "xpts": out.get("xpts"),
                "your_player_id": (yours or {}).get("player_id"),
                "your_name": (yours or {}).get("name"),
                "your_xpts": (yours or {}).get("xpts"),
                "near": almost,
            }
            missing.append(slot)
            if almost:
                near.append(slot)
        xi.append(out)

    assembled["xi"] = xi
    assembled["source"] = "pool"
    your_xpts = _xpts_total(recommended_xi)
    target_xpts = _xpts_total(assembled)
    assembled["coverage"] = {
        "owned_count": len(owned_ids),
        "owned_ids": owned_ids,
        "xi_count": len(xi),
        "your_xi_xpts": round(your_xpts, 2) if recommended_xi else None,
        "target_xpts": round(target_xpts, 2) if target_xpts else None,
        "xpts_gap": round(target_xpts - your_xpts, 2) if recommended_xi else None,
        "missing_slots": missing,
        "near_slots": near,
    }
    return assembled
