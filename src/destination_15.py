"""
15 alcanzable antes del KO: once por xPts, banca de cobertura, caja por rotación.

Libres no listados no ocupan plaza: van a vigilancia con P(sale en K ciclos).
"""

from __future__ import annotations

from typing import Any

import config
from competitive_actions import (
    clause_premium_ratio,
    mister_bid_cap,
    sells_settle_before_deadline,
)
from cycle_plan import CLAUSE_MIN_XPTS_GAP, _clause_upgrade_is_material
from gw_target_xi import pick_best_gw_xi

WATCH_MAX = 3
WATCH_P_MIN = 0.12
EV_WAIT_EPS = 0.35
FLEX_BAR_RATIO = 0.85
MARKET_STARTER_MIN_GAP = 1.0


def _money(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pid(p: dict[str, Any] | None) -> str:
    if not p:
        return ""
    return str(p.get("player_id") or p.get("id") or "").strip()


def _xpts(p: dict[str, Any] | None) -> float:
    if not p:
        return 0.0
    return _f(p.get("xpts")) or 0.0


def _pos(p: dict[str, Any] | None) -> str:
    return str((p or {}).get("position") or "MF").upper()


def _vm(p: dict[str, Any]) -> float:
    return _money(p.get("market_value") or p.get("price") or p.get("cost"))


def buy_cost(p: dict[str, Any]) -> float:
    if p.get("owned") or p.get("reach") == "keep":
        return 0.0
    if p.get("reach") == "clause" or str(p.get("acquisition") or "") == "clause":
        c = _f(p.get("clause"))
        if c is not None and c > 0:
            return c
    return _money(p.get("buy_price") or p.get("price") or p.get("cost"))


def classify_reach(
    p: dict[str, Any],
    *,
    owned: bool,
    clauses_on: bool,
) -> str:
    """keep | market | clause | watch_free | ghost."""
    if owned or p.get("owned"):
        return "keep"
    on_market = bool(
        p.get("on_daily_market") or str(p.get("seller") or "").lower() == "market"
    )
    if on_market:
        return "market"
    seller = str(p.get("seller") or "").lower()
    oid = str(p.get("owner_id") or "").strip()
    is_free = seller == "free" or not oid or oid in ("0", "None")
    if is_free:
        return "watch_free"
    clause = _f(p.get("clause"))
    if clauses_on and p.get("clause_known") and clause is not None and clause > 0:
        return "clause"
    return "ghost"


def appear_probability(
    *,
    n_free: int,
    s_on_board: int,
    k_future: int,
    on_board_now: bool,
) -> float:
    """
    Sorteo aleatorio, no cola. Quien está hoy no sale el ciclo siguiente;
    luego p ≈ S/(N-S) cada ciclo futuro.
    """
    k = max(0, int(k_future or 0))
    n = max(0, int(n_free or 0))
    s = max(0, int(s_on_board or 0))
    if k <= 0 or n <= 0 or s <= 0:
        return 0.0
    denom = n - s
    if denom <= 0:
        p = 1.0 if s >= n else 0.0
    else:
        p = min(1.0, s / float(denom))
    if on_board_now:
        rest = k - 1
        if rest <= 0:
            return 0.0
        return round(1.0 - (1.0 - p) ** rest, 4)
    return round(1.0 - (1.0 - p) ** k, 4)


def sale_limit(league_rules: dict[str, Any] | None) -> int:
    rules = league_rules or {}
    eco = rules.get("economy") if isinstance(rules.get("economy"), dict) else {}
    try:
        limit = int(eco.get("sale_limit") or rules.get("sale_limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    return max(1, limit)


def rotation_finance(
    dest: list[dict[str, Any]],
    *,
    owned_ids: set[str],
    owned_by_id: dict[str, dict[str, Any]],
    balance: float,
    max_debt: float | None,
    settle_ok: bool,
    sale_remaining: int,
    listed_ids: set[str] | None = None,
) -> dict[str, Any]:
    """
    Equilibrio: compras − ventas de quien sale. Deuda = pico, no presupuesto.
    Si las ventas no cobran antes del KO, no cuentan como caja.
    """
    listed_ids = listed_ids or set()
    dest_ids = {_pid(p) for p in dest if _pid(p)}
    buys = [p for p in dest if _pid(p) and _pid(p) not in owned_ids]
    sells = [
        owned_by_id[i]
        for i in owned_by_id
        if i not in dest_ids
    ]
    cost = sum(buy_cost(p) for p in buys)
    proceeds = sum(_vm(p) for p in sells)
    new_lists = [p for p in sells if _pid(p) not in listed_ids]
    if len(new_lists) > max(0, int(sale_remaining)):
        return {
            "ok": False,
            "reason": "sale_limit",
            "cost": cost,
            "proceeds": proceeds,
            "peak": cost - float(balance or 0),
        }
    bid_cap = mister_bid_cap(float(balance or 0), max_debt)
    if cost > bid_cap + 1:
        return {
            "ok": False,
            "reason": "max_debt",
            "cost": cost,
            "proceeds": proceeds,
            "peak": cost - float(balance or 0),
        }
    timely = proceeds if settle_ok else 0.0
    covers = float(balance or 0) + timely + 1 >= cost
    if not covers:
        return {
            "ok": False,
            "reason": "no_recover",
            "cost": cost,
            "proceeds": proceeds,
            "peak": cost - float(balance or 0),
        }
    return {
        "ok": True,
        "reason": "",
        "cost": round(cost, 0),
        "proceeds": round(proceeds, 0),
        "peak": round(cost - float(balance or 0), 0),
        "sells": sells,
        "buys": buys,
        "new_lists": new_lists,
    }


def typical_week_xpts(p: dict[str, Any] | None) -> float:
    """xPts de una jornada normal: producción × titularidad, sin FDR de esta J."""
    if not p:
        return 0.0
    base = _f(p.get("xpts_base"))
    if base is not None:
        p_play = _f(p.get("xpts_p_play"))
        if p_play is None:
            p_play = 1.0
        return base * p_play
    xpts = _xpts(p)
    fdr = _f(p.get("fdr_multiplier"))
    if fdr is not None and fdr > 0:
        return xpts / fdr
    return xpts


def _is_rising_keep(p: dict[str, Any] | None) -> bool:
    if not p:
        return False
    if str(p.get("value_note") or "") == "falling":
        return False
    d = _f(p.get("delta_5d"))
    if d is not None and d > 0:
        return True
    return str(p.get("value_note") or "") == "rising"


def _ask_over_vm(cand: dict[str, Any]) -> bool:
    soft = float(getattr(config, "IDEAL_CLAUSE_PREMIUM_SOFT", 1.25))
    return clause_premium_ratio(buy_cost(cand), _vm(cand)) > soft


def _incumbent_dropped(
    xi_now: list[dict[str, Any]],
    t_xi: list[dict[str, Any]],
    *,
    cand: dict[str, Any],
    owned_ids: set[str],
) -> dict[str, Any] | None:
    """Titular del once destino que sale si entra el candidato."""
    new_ids = {_pid(p) for p in t_xi if _pid(p)}
    dropped = [p for p in xi_now if _pid(p) and _pid(p) not in new_ids]
    if not dropped:
        return None
    pos = _pos(cand)
    same = [p for p in dropped if _pos(p) == pos]
    pool = same or dropped
    owned = [p for p in pool if _pid(p) in owned_ids]
    ranked = owned or pool
    return min(ranked, key=_xpts)


def swap_wealth_ok(
    cand: dict[str, Any],
    *,
    xi_now: list[dict[str, Any]],
    t_xi: list[dict[str, Any]],
    owned_ids: set[str],
) -> bool:
    """False si el swap es alquiler de una J o vende un titular que se revaloriza por un spike."""
    if _pid(cand) not in {_pid(p) for p in t_xi}:
        return True
    inc = _incumbent_dropped(xi_now, t_xi, cand=cand, owned_ids=owned_ids)
    week_c = _xpts(cand)
    week_i = _xpts(inc) if inc is not None else None
    gap = week_c - (week_i or 0.0)
    is_clause = cand.get("reach") == "clause" or str(cand.get("acquisition") or "") == "clause"
    displaces_owned = bool(inc) and _pid(inc) in owned_ids

    if is_clause:
        if not _clause_upgrade_is_material(week_c, week_i):
            return False
    elif displaces_owned and gap + 1e-9 < MARKET_STARTER_MIN_GAP:
        return False

    if inc is not None and (is_clause or _ask_over_vm(cand)):
        if week_c > _xpts(inc) and typical_week_xpts(cand) <= typical_week_xpts(inc) + 1e-9:
            return False

    if displaces_owned and _is_rising_keep(inc) and gap + 1e-9 < CLAUSE_MIN_XPTS_GAP:
        return False
    return True


def _to_xi_player(p: dict[str, Any]) -> dict[str, Any]:
    raw = dict(p.get("raw") or {})
    pid = _pid(p)
    raw["id"] = pid
    raw["player_id"] = pid
    raw["name"] = p.get("name") or raw.get("name")
    raw["position"] = _pos(p)
    xp = p.get("xpts")
    if xp is None:
        xp = raw.get("xpts")
    raw["xpts"] = _f(xp) or 0.0
    if p.get("xpts_p_play") is not None:
        raw["xpts_p_play"] = p.get("xpts_p_play")
    if p.get("xpts_base") is not None:
        raw["xpts_base"] = p.get("xpts_base")
    if p.get("xpts_why"):
        raw["xpts_why"] = p.get("xpts_why")
    if p.get("fdr_multiplier") is not None:
        raw["fdr_multiplier"] = p.get("fdr_multiplier")
    if p.get("delta_5d") is not None:
        raw["delta_5d"] = p.get("delta_5d")
    if p.get("value_note"):
        raw["value_note"] = p.get("value_note")
    return raw


def _pick_xi(
    players: list[dict[str, Any]],
    *,
    formation: str,
    matchday: dict[str, Any] | None,
    captain_rule: dict[str, Any] | None,
) -> dict[str, Any]:
    pool = [_to_xi_player(p) for p in players]
    return pick_best_gw_xi(
        pool,
        matchday=matchday,
        captain_rule=captain_rule,
    )


def _shape_from_label(formation: str | None) -> dict[str, int]:
    default = {"GK": 1, "DF": 4, "MF": 3, "FW": 3}
    if not formation:
        return default
    parts = [p.strip() for p in str(formation).replace("–", "-").split("-") if p.strip()]
    nums: list[int] = []
    for p in parts:
        try:
            nums.append(int(p))
        except ValueError:
            return default
    if len(nums) == 3 and sum(nums) == 10:
        return {"GK": 1, "DF": nums[0], "MF": nums[1], "FW": nums[2]}
    if len(nums) == 4 and sum(nums) == 11:
        return {"GK": nums[0], "DF": nums[1], "MF": nums[2], "FW": nums[3]}
    if len(nums) == 4 and sum(nums) == 10:
        return {"GK": 1, "DF": nums[0], "MF": nums[1] + nums[2], "FW": nums[3]}
    return default


def _gk_team_key(u: dict[str, Any]) -> str | None:
    tid = str(u.get("team_id") or "").strip()
    if tid and tid not in ("0", "None"):
        return f"id:{tid}"
    team = str(u.get("team") or "").strip()
    if team and team not in ("—", "-", "?"):
        return f"name:{team.lower()}"
    return None


def _bench_ok(u: dict[str, Any]) -> bool:
    if _pos(u) == "GK":
        return True
    pts = u.get("ff_mister_points")
    try:
        min_pts = float(getattr(config, "IDEAL_BENCH_MIN_POINTS", 100))
        return pts is not None and float(pts) >= min_pts
    except (TypeError, ValueError):
        return False


def _pick_bench(
    pool: list[dict[str, Any]],
    *,
    xi_ids: set[str],
    needs: dict[str, int],
) -> list[dict[str, Any]]:
    rest = [p for p in pool if _pid(p) not in xi_ids]
    picked: list[dict[str, Any]] = []
    used: set[str] = set()

    gk_need = int(needs.get("GK", 0))
    if gk_need > 0:
        gks = [p for p in rest if _pos(p) == "GK"]
        xi_gk = None
        # tándem: mismo club que el titular si se puede
        prefer = None
        for p in pool:
            if _pid(p) in xi_ids and _pos(p) == "GK":
                prefer = _gk_team_key(p)
                xi_gk = p
                break
        tandem = []
        if prefer:
            tandem = [p for p in gks if _gk_team_key(p) == prefer]
        ordered = tandem + [p for p in gks if p not in tandem]
        for p in ordered[:gk_need]:
            picked.append(p)
            used.add(_pid(p))
            if xi_gk is not None and _gk_team_key(p) == prefer:
                p["gk_tandem"] = True
                xi_gk["gk_tandem"] = True

    for pos in ("DF", "MF", "FW"):
        need = int(needs.get(pos, 0))
        cands = [
            p
            for p in rest
            if _pos(p) == pos and _pid(p) not in used and _bench_ok(p)
        ]
        cands.sort(key=lambda x: (-_xpts(x), _money(x.get("price"))))
        if len(cands) < need:
            extra = [
                p
                for p in rest
                if _pos(p) == pos and _pid(p) not in used and p not in cands
            ]
            extra.sort(key=lambda x: (-_xpts(x), _money(x.get("price"))))
            cands = cands + extra
        for p in cands[:need]:
            picked.append(p)
            used.add(_pid(p))
    return picked


def _ideal_for_xi(xi: dict[str, int]) -> dict[str, int]:
    ideal = {p: int(xi.get(p, 0)) for p in ("GK", "DF", "MF", "FW")}
    ideal["GK"] = max(int(ideal.get("GK", 1)), 1) + 1
    remaining = max(0, 15 - sum(ideal.values()))
    order = ("DF", "MF", "FW", "DF", "MF", "FW", "GK")
    i = 0
    while remaining > 0 and i < 40:
        pos = order[i % len(order)]
        ideal[pos] = int(ideal.get(pos, 0)) + 1
        remaining -= 1
        i += 1
    return ideal


def _xi_complete(assembled: dict[str, Any], shape: dict[str, int]) -> bool:
    summary = assembled.get("summary") or {}
    if summary.get("complete") is True:
        return True
    n = len(assembled.get("xi") or [])
    return n >= sum(int(v) for v in shape.values())


def _xi_xpts_total(assembled: dict[str, Any]) -> float:
    summary = assembled.get("summary") or {}
    raw = _f(summary.get("xpts_total"))
    if raw is not None:
        return raw
    return sum(_xpts(r) for r in assembled.get("xi") or [])


def _ev_wait(
    pos: str,
    watch: list[dict[str, Any]],
    p_appear: float,
    k_future: int,
) -> float:
    if k_future <= 1 or p_appear <= 0:
        return 0.0
    best = 0.0
    for p in watch:
        if _pos(p) != pos:
            continue
        best = max(best, _xpts(p))
    return p_appear * best


def _xpts_bar(pos: str, gw_target_xi: dict[str, Any] | None) -> float:
    best = 0.0
    for row in (gw_target_xi or {}).get("xi") or []:
        if _pos(row) == pos:
            best = max(best, _xpts(row))
    return best


def _should_skip_market(
    cand: dict[str, Any],
    *,
    xi_complete: bool,
    k_future: int,
    ev_wait: float,
    bar: float,
) -> bool:
    """Mercado mediocre: no cerrar el hueco si el once ya está cubierto y esperar gana."""
    if cand.get("reach") != "market":
        if cand.get("reach") == "clause" and k_future > 1 and bar > 0:
            # cláusula floja vs listón: esperar
            return _xpts(cand) < bar * FLEX_BAR_RATIO and ev_wait > _xpts(cand) + EV_WAIT_EPS
        return False
    if not xi_complete:
        return False
    if k_future <= 1:
        return False
    return ev_wait > _xpts(cand) + EV_WAIT_EPS


def assemble_named(
    available: list[dict[str, Any]],
    *,
    formation: str,
    matchday: dict[str, Any] | None,
    captain_rule: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, int]]:
    by_id = {_pid(p): p for p in available if _pid(p)}
    assembled = _pick_xi(
        available,
        formation=formation,
        matchday=matchday,
        captain_rule=captain_rule,
    )
    shape = dict(assembled.get("shape") or {})
    if not shape:
        shape = _shape_from_label(formation)
    xi_rows: list[dict[str, Any]] = []
    xi_ids: set[str] = set()
    for row in assembled.get("xi") or []:
        pid = _pid(row)
        src = by_id.get(pid)
        if not src:
            continue
        item = dict(src)
        item["role"] = "starter"
        item["slot"] = row.get("slot")
        item["xpts"] = row.get("xpts") if row.get("xpts") is not None else src.get("xpts")
        item["why_xi"] = row.get("why")
        xi_rows.append(item)
        xi_ids.add(pid)
    ideal = _ideal_for_xi(shape)
    needs = {
        p: max(0, int(ideal.get(p, 0)) - sum(1 for r in xi_rows if _pos(r) == p))
        for p in ("GK", "DF", "MF", "FW")
    }
    bench = _pick_bench(available, xi_ids=xi_ids, needs=needs)
    bench_rows: list[dict[str, Any]] = []
    for i, p in enumerate(bench, start=1):
        item = dict(p)
        item["role"] = "bench"
        item["slot"] = f"{_pos(p)}B{i}"
        bench_rows.append(item)
    return xi_rows, bench_rows, assembled, shape


def _pack_key(
    xi_rows: list[dict[str, Any]],
    bench_rows: list[dict[str, Any]],
    flex: list[dict[str, Any]],
    shape: dict[str, int],
    finance: dict[str, Any],
) -> tuple:
    target = sum(int(v) for v in shape.values()) or 11
    complete = 1 if len(xi_rows) >= target else 0
    xpts = sum(_xpts(r) for r in xi_rows)
    return (
        complete,
        round(xpts, 3),
        -len(flex),
        len(bench_rows),
        -float(finance.get("cost") or 0),
    )


def _annotate_status(rows: list[dict[str, Any]], owned_ids: set[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        item = dict(r)
        pid = _pid(item)
        keep = pid in owned_ids
        item["status"] = "keep" if keep else "buy"
        item["owned"] = keep
        reach = item.get("reach") or ("keep" if keep else "market")
        if keep:
            item["acquisition"] = "keep"
        elif reach == "clause":
            item["acquisition"] = "clause"
        elif reach == "market":
            seller = str(item.get("seller") or "").lower()
            item["acquisition"] = "free" if seller == "free" else "market"
        else:
            item["acquisition"] = reach
        xp = _xpts(item)
        price = buy_cost(item) if not keep else _vm(item)
        item["why"] = (
            f"{xp:.1f} xPts · {price:,.0f} € · {item['status']} · {item.get('role')}"
        )
        out.append(item)
    return out


def max_clauses_in_window(k_future: int) -> int:
    return max(1, int(k_future or 0) + 1)


def assemble_destination(
    universe: list[dict[str, Any]],
    *,
    owned_ids: set[str],
    balance: float,
    max_debt: float | None,
    settle_ok: bool,
    sale_remaining: int,
    listed_ids: set[str],
    k_future: int,
    clauses_on: bool,
    matchday: dict[str, Any] | None = None,
    captain_rule: dict[str, Any] | None = None,
    gw_target_xi: dict[str, Any] | None = None,
    n_free: int = 0,
    s_on_board: int = 0,
) -> dict[str, Any]:
    """Elige formación y 15 alcanzable (named + flex)."""
    for p in universe:
        p["reach"] = classify_reach(p, owned=p.get("owned") or _pid(p) in owned_ids, clauses_on=clauses_on)

    owned_pool = [p for p in universe if _pid(p) in owned_ids]
    owned_by_id = {_pid(p): p for p in owned_pool}
    watch = [p for p in universe if p.get("reach") == "watch_free"]
    reachable = [
        p
        for p in universe
        if p.get("reach") in ("keep", "market", "clause")
    ]
    p_appear = appear_probability(
        n_free=n_free or len([p for p in universe if p.get("reach") in ("watch_free", "market") and not p.get("owned")]),
        s_on_board=s_on_board,
        k_future=k_future,
        on_board_now=False,
    )
    clause_cap = max_clauses_in_window(k_future)

    labels = [str(x).strip() for x in (getattr(config, "IDEAL_FORMATIONS", None) or ("4-3-3",)) if str(x).strip()]
    if not labels:
        labels = ["4-3-3"]

    best: dict[str, Any] | None = None
    best_key: tuple | None = None
    trials: list[dict[str, Any]] = []

    def _finance(named: list[dict[str, Any]]) -> dict[str, Any]:
        clauses_n = sum(
            1
            for p in named
            if _pid(p) not in owned_ids and p.get("reach") == "clause"
        )
        if clauses_n > clause_cap:
            return {"ok": False, "reason": "clause_cap", "cost": 0, "proceeds": 0, "peak": 0}
        return rotation_finance(
            named,
            owned_ids=owned_ids,
            owned_by_id=owned_by_id,
            balance=balance,
            max_debt=max_debt,
            settle_ok=settle_ok,
            sale_remaining=sale_remaining,
            listed_ids=listed_ids,
        )

    for label in labels:
        display = label if label.count("-") >= 2 else label
        xi0, bench0, assembled0, shape = assemble_named(
            owned_pool,
            formation=label,
            matchday=matchday,
            captain_rule=captain_rule,
        )
        pool_ids = {_pid(p) for p in xi0 + bench0 if _pid(p)}
        pool: list[dict[str, Any]] = [owned_by_id[i] for i in pool_ids if i in owned_by_id]
        complete0 = _xi_complete(assembled0, shape)
        skipped_pos: set[str] = set()

        cands = [p for p in reachable if _pid(p) not in owned_ids]
        cands.sort(key=lambda x: (-_xpts(x), buy_cost(x)))
        for cand in cands:
            pos = _pos(cand)
            ev = _ev_wait(pos, watch, p_appear, k_future)
            bar = _xpts_bar(pos, gw_target_xi)
            xi_now, _b, ass_now, _sh = assemble_named(
                pool,
                formation=label,
                matchday=matchday,
                captain_rule=captain_rule,
            )
            complete_now = _xi_complete(ass_now, shape)
            if _should_skip_market(
                cand,
                xi_complete=complete_now,
                k_future=k_future,
                ev_wait=ev,
                bar=bar,
            ):
                if complete_now:
                    skipped_pos.add(pos)
                continue
            trial_pool = list(pool) + [cand]
            t_xi, t_bench, t_ass, t_shape = assemble_named(
                trial_pool,
                formation=label,
                matchday=matchday,
                captain_rule=captain_rule,
            )
            named = t_xi + t_bench
            if _pid(cand) not in {_pid(p) for p in named}:
                continue
            fin = _finance(named)
            if not fin.get("ok"):
                continue
            old_x = sum(_xpts(r) for r in xi_now)
            new_x = sum(_xpts(r) for r in t_xi)
            old_c = 1 if complete_now else 0
            new_c = 1 if _xi_complete(t_ass, t_shape) else 0
            if (new_c, new_x) <= (old_c, old_x + 0.05):
                continue
            if not swap_wealth_ok(
                cand,
                xi_now=xi_now,
                t_xi=t_xi,
                owned_ids=owned_ids,
            ):
                continue
            pool = named
            skipped_pos.discard(pos)

        xi_rows, bench_rows, assembled, shape = assemble_named(
            pool,
            formation=label,
            matchday=matchday,
            captain_rule=captain_rule,
        )
        named = xi_rows + bench_rows
        fin = _finance(named)
        if not fin.get("ok"):
            # revert to owned-only if upgrades broke finance
            xi_rows, bench_rows, assembled, shape = assemble_named(
                owned_pool,
                formation=label,
                matchday=matchday,
                captain_rule=captain_rule,
            )
            named = xi_rows + bench_rows
            fin = _finance(named)

        flex: list[dict[str, Any]] = []
        ideal = _ideal_for_xi(shape)
        complete = _xi_complete(assembled, shape)
        counts = {p: 0 for p in ("GK", "DF", "MF", "FW")}
        for r in named:
            counts[_pos(r)] = counts.get(_pos(r), 0) + 1
        if complete and k_future > 1:
            for pos in ("GK", "DF", "MF", "FW"):
                if pos not in skipped_pos:
                    continue
                if counts.get(pos, 0) >= int(ideal.get(pos, 0)):
                    continue
                bar = _xpts_bar(pos, gw_target_xi) or 0.0
                ev = _ev_wait(pos, watch, p_appear, k_future)
                if ev <= 0 and bar <= 0:
                    continue
                flex.append(
                    {
                        "kind": "flex",
                        "status": "flex",
                        "position": pos,
                        "role": "starter" if counts.get(pos, 0) < int(shape.get(pos, 0)) else "bench",
                        "xpts_bar": round(bar, 2) if bar else None,
                        "p_appear": p_appear,
                        "why": (
                            f"{pos} · listón {bar:.1f} xPts · esperar sorteo"
                            if bar
                            else f"{pos} · esperar sorteo"
                        ),
                    }
                )

        xi_ann = _annotate_status(xi_rows, owned_ids)
        bench_ann = _annotate_status(bench_rows, owned_ids)
        key = _pack_key(xi_ann, bench_ann, flex, shape, fin)
        trial = {
            "formation": display,
            "shape": dict(shape),
            "xpts_starters": round(sum(_xpts(r) for r in xi_ann), 2),
            "complete": bool(complete),
            "flex": len(flex),
            "cost": fin.get("cost"),
        }
        trials.append(trial)
        if best_key is None or key > best_key:
            best_key = key
            best = {
                "formation": display,
                "shape": dict(shape),
                "xi": xi_ann,
                "bench": bench_ann,
                "flex_slots": flex,
                "finance": fin,
                "assembled": assembled,
                "complete": bool(complete),
            }

    if not best:
        best = {
            "formation": labels[0],
            "shape": {"GK": 1, "DF": 4, "MF": 3, "FW": 3},
            "xi": [],
            "bench": [],
            "flex_slots": [],
            "finance": {"ok": True, "cost": 0, "proceeds": 0, "peak": 0, "sells": []},
            "assembled": {},
            "complete": False,
        }

    named = list(best["xi"]) + list(best["bench"])
    watch_rows = _watch_frees(
        watch,
        named_ids={_pid(p) for p in named},
        p_appear=p_appear,
        k_future=k_future,
    )
    dest = named + [
        {**f, "player_id": "", "name": "", "status": "flex"} for f in best["flex_slots"]
    ]
    return {
        "formation": best["formation"],
        "shape": best["shape"],
        "xi": best["xi"],
        "bench": best["bench"],
        "destination_15": dest,
        "flex_slots": best["flex_slots"],
        "finance": best["finance"],
        "watch_frees": watch_rows,
        "p_appear": p_appear,
        "trials": trials[:8],
        "complete": best["complete"],
        "xpts_starters": round(sum(_xpts(r) for r in best["xi"]), 2),
        "xpts_total": round(sum(_xpts(r) for r in named), 2),
    }


def _watch_frees(
    watch: list[dict[str, Any]],
    *,
    named_ids: set[str],
    p_appear: float,
    k_future: int,
) -> list[dict[str, Any]]:
    if k_future <= 0 or p_appear < WATCH_P_MIN:
        return []
    rows: list[dict[str, Any]] = []
    for p in watch:
        pid = _pid(p)
        if not pid or pid in named_ids:
            continue
        ev = p_appear * _xpts(p)
        rows.append(
            {
                "player_id": pid,
                "name": p.get("name"),
                "position": _pos(p),
                "xpts": round(_xpts(p), 2),
                "p_appear": p_appear,
                "ev": round(ev, 2),
                "price": _money(p.get("price")),
                "status": "watch",
                "why": f"P≈{p_appear * 100:.0f}% en {k_future} ciclos · {_xpts(p):.1f} xPts",
            }
        )
    rows.sort(key=lambda r: (-float(r.get("ev") or 0), -float(r.get("xpts") or 0)))
    return rows[:WATCH_MAX]


def build_path(
    dest_named: list[dict[str, Any]],
    *,
    owned_ids: set[str],
    finance: dict[str, Any],
    k_future: int,
    settle_ok: bool,
) -> list[dict[str, Any]]:
    """Lista este ciclo; pujas de mercado ahora; 1 cláusula ahora; el resto después."""
    path: list[dict[str, Any]] = []
    sells = list(finance.get("sells") or finance.get("new_lists") or [])
    for s in sells:
        path.append(
            {
                "kind": "list",
                "cycle": 0,
                "player_id": _pid(s),
                "name": s.get("name"),
                "position": _pos(s),
                "amount": _vm(s),
                "why": (
                    "Lista para equilibrar el 15; el siguiente ciclo aceptas y cobra."
                    if settle_ok
                    else "Lista, pero el cobro no llega al KO: no financia este destino."
                ),
            }
        )
    buys = [p for p in dest_named if _pid(p) and _pid(p) not in owned_ids]
    market_buys = [p for p in buys if p.get("acquisition") in ("market", "free") or p.get("reach") == "market"]
    clause_buys = [p for p in buys if p.get("acquisition") == "clause" or p.get("reach") == "clause"]
    for p in market_buys:
        path.append(
            {
                "kind": "bid",
                "cycle": 0,
                "player_id": _pid(p),
                "name": p.get("name"),
                "position": _pos(p),
                "amount": buy_cost(p),
                "why": "En el mercado de hoy · ficha ahora.",
            }
        )
    for i, p in enumerate(clause_buys):
        cycle = 0 if i == 0 else min(i, max(0, int(k_future or 0)))
        path.append(
            {
                "kind": "clause",
                "cycle": cycle,
                "player_id": _pid(p),
                "name": p.get("name"),
                "position": _pos(p),
                "amount": buy_cost(p),
                "why": (
                    "Cláusula este ciclo."
                    if cycle == 0
                    else f"Cláusula en el ciclo {cycle + 1} (1 por ciclo)."
                ),
            }
        )
    return path
