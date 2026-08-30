"""
Once objetivo de la jornada: 11 del pool, sin filtro de propiedad ni caja.

Ranking = xPts. El cruce explica. La cobertura compara con tu once real.
"""

from __future__ import annotations

from typing import Any

import config
from competitive_actions import build_recommended_gw_xi, mister_bid_cap, _money

NEAR_XPTS_RATIO = 0.85

# Señales que el mercado / plantilla pisan sobre el catálogo crudo
_OVERLAY_KEYS = (
    "gw_lineup_prob",
    "gw_starter",
    "gw_doubt",
    "gw_out",
    "gw_blank",
    "gw_probable_xi",
    "gw_confirmed",
    "gw_played",
    "gw_points",
    "gw_opponent",
    "next_jornada",
    "fdr_applies_to_current_gw",
    "on_daily_market",
    "seller",
    "owner_id",
    "owner_name",
    "clause",
    "clause_known",
    "external",
    "ff_mister_avg",
    "ff_prior_avg",
    "xpts",
    "xpts_why",
    "xpts_p_play",
    "xpts_floor",
    "xpts_base",
    "fdr",
    "fdr_why",
    "fdr_multiplier",
    "fdr_label",
    "matchup",
    "is_home",
    "opponent_name",
    "next_is_home",
    "fotmob_stats",
    "price",
    "market_value",
)


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
    market_ids: set[str] | None = None,
    max_debt: float | None = None,
) -> tuple[str, str]:
    """
    (`ownership`, `reachable`).

    reachable: daily_market | free | clause | no
    Para `owned` reachable es vacío (ya lo tienes).
    Mercado del día gana a un owner rival.
    Cláusula reachable si cabe en el techo de deuda, no en caja ≥ 0.
    """
    pid = _pid(player)
    oid = str(player.get("owner_id") or "").strip()
    mine = bool(pid and pid in squad_ids) or (bool(my_id) and oid == str(my_id))
    if mine:
        return "owned", ""
    on_market = bool(
        player.get("on_daily_market")
        or player.get("seller") == "market"
        or (pid and pid in (market_ids or set()))
    )
    if on_market:
        return "daily_market", "daily_market"
    if not oid or oid in ("0", "None"):
        return "free", "free"
    clause = _f(player.get("clause"))
    cap = mister_bid_cap(balance, max_debt)
    if (
        clauses_enabled
        and player.get("clause_known")
        and clause is not None
        and clause > 0
        and clause <= cap + 1
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


def merge_target_universe(
    pool: list[dict[str, Any]] | None,
    *overlays: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """
    Universo del once objetivo: catálogo + previa FF y flags de mercado.

    El pool crudo no trae `gw_lineup_prob` ni `on_daily_market`. Las copias
    de plantilla/mercado sí: se fusionan por id y el overlay gana.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for src in (pool, *overlays):
        for raw in src or []:
            if not isinstance(raw, dict):
                continue
            pid = _pid(raw)
            if not pid:
                continue
            if pid not in by_id:
                by_id[pid] = dict(raw)
                continue
            base = by_id[pid]
            for key in _OVERLAY_KEYS:
                if raw.get(key) is not None:
                    base[key] = raw[key]
            if raw.get("on_daily_market"):
                base["on_daily_market"] = True
                if raw.get("seller"):
                    base["seller"] = raw["seller"]
            ext = raw.get("external")
            if isinstance(ext, dict):
                merged_ext = dict(base.get("external") or {})
                merged_ext.update({k: v for k, v in ext.items() if v is not None})
                base["external"] = merged_ext
    return list(by_id.values())


def _slot_why(row: dict[str, Any], src: dict[str, Any]) -> str:
    """Una frase: titularidad · rival/casa · FDR. H2H de temporada al final."""
    bits: list[str] = []
    p_play = _f(row.get("p_play"))
    if p_play is None:
        p_play = _f(src.get("xpts_p_play"))
    if p_play is not None:
        bits.append(f"Titular {p_play * 100:.0f}%")
    elif row.get("prob") is not None:
        bits.append(f"Titular {float(row['prob']):.0f}%")
    opp = row.get("opponent_name") or src.get("opponent_name") or row.get("opponent")
    is_home = row.get("is_home")
    if is_home is None:
        is_home = src.get("is_home")
    where = "en casa" if is_home is True else ("fuera" if is_home is False else "")
    if opp:
        bits.append(f"vs {opp}" + (f" {where}" if where else ""))
    label = row.get("fdr_label") or src.get("fdr_label")
    if label:
        bits.append(str(label))
    mu = src.get("matchup") if isinstance(src.get("matchup"), dict) else None
    vs = (mu or {}).get("vs_opponent") if isinstance(mu, dict) else None
    last = (vs or {}).get("last") if isinstance(vs, dict) else None
    if last and last.get("points") is not None:
        last_where = (
            "en casa"
            if last.get("is_home") is True
            else ("fuera" if last.get("is_home") is False else "")
        )
        bits.append(
            f"vs este rival: {last.get('points')} pts"
            + (f" ({last_where})" if last_where else "")
        )
    return " · ".join(bits)


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
    try:
        max_debt = float(me["max_debt"]) if me.get("max_debt") is not None else None
    except (TypeError, ValueError):
        max_debt = None
    clauses_enabled = bool(rules.get("clauses"))

    by_id: dict[str, dict[str, Any]] = {}
    market_ids: set[str] = set()
    for src in list(pool or []) + list(squad or []):
        pid = _pid(src)
        if not pid:
            continue
        by_id[pid] = src
        if src.get("on_daily_market") or src.get("seller") == "market":
            market_ids.add(pid)

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
            market_ids=market_ids,
            max_debt=max_debt,
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
        out["why"] = _slot_why(out, src)
        out["near"] = False
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
            out["near"] = almost
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
