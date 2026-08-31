"""15 al KO: pool alcanzable, packing con caja/lag, rivales sin cláusula fuera."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from destination_15 import (  # noqa: E402
    appear_probability,
    assemble_destination,
    build_path,
    classify_reach,
    rotation_finance,
    swap_wealth_ok,
    typical_week_xpts,
)
from target_board import _normalize_player, build_target_board  # noqa: E402


def _assert(cond: bool, msg: object) -> None:
    if not cond:
        raise AssertionError(msg)


def _raw(
    pid: str,
    pos: str,
    *,
    owned: bool = False,
    seller: str = "free",
    xpts: float = 6.0,
    **extra: object,
) -> dict:
    price = extra.pop("price", 2_000_000)
    row = {
        "id": pid,
        "name": pid,
        "position": pos,
        "price": price,
        "market_value": extra.pop("market_value", price),
        "lineup_prob": 0.9,
        "gw_starter": True,
        "gw_lineup_prob": 90,
        "xpts": xpts,
        "xpts_p_play": 0.9,
        "ff_mister_avg": extra.pop("avg", 7.0),
        "ff_mister_points": extra.pop("ff_mister_points", 220),
        "ff_apps": 28,
        "seller": seller,
        "team": extra.pop("team", "Club A"),
        "team_id": extra.pop("team_id", "1"),
    }
    if owned:
        row["seller"] = "owned"
    row.update(extra)
    return row


def _universe(rows: list[tuple[dict, bool]]) -> list[dict]:
    out: list[dict] = []
    for raw, owned in rows:
        n = _normalize_player(raw, owned=owned, price_series=None)
        _assert(n is not None, raw)
        out.append(n)
    return out


def _owned_15(*, weak_fw_xpts: float = 5.2, weak_fw_price: float = 8_000_000) -> list[tuple[dict, bool]]:
    specs: list[tuple[str, str, float, dict]] = []
    specs.append(("gk1", "GK", 6.2, {"team_id": "10", "price": 1_000_000}))
    specs.append(("gk2", "GK", 4.1, {"team_id": "10", "price": 800_000}))
    for i in range(1, 6):
        specs.append((f"d{i}", "DF", 6.0 - i * 0.08, {"price": 2_000_000}))
    for i in range(1, 6):
        specs.append((f"m{i}", "MF", 6.1 - i * 0.08, {"price": 2_000_000}))
    specs.append(("f1", "FW", weak_fw_xpts, {"price": weak_fw_price}))
    specs.append(("f2", "FW", 6.5, {"price": 3_000_000}))
    specs.append(("f3", "FW", 6.0, {"price": 2_500_000}))
    out: list[tuple[dict, bool]] = []
    for pid, pos, xp, extra in specs:
        out.append((_raw(pid, pos, owned=True, xpts=xp, **extra), True))
    return out


def _owned_ids() -> set[str]:
    return {r[0]["id"] for r in _owned_15()}


def _dest_ids(dest: dict) -> set[str]:
    named = list(dest.get("xi") or []) + list(dest.get("bench") or [])
    return {str(r.get("player_id")) for r in named if r.get("player_id")}


def test_appear_probability_lottery_not_queue() -> None:
    _assert(appear_probability(n_free=20, s_on_board=4, k_future=0, on_board_now=False) == 0.0, "k=0")
    _assert(appear_probability(n_free=20, s_on_board=4, k_future=1, on_board_now=True) == 0.0, "hoy en tablero")
    p_off = appear_probability(n_free=20, s_on_board=4, k_future=2, on_board_now=False)
    # p = 4/16 = 0.25 → 1 - 0.75^2 = 0.4375
    _assert(abs(p_off - 0.4375) < 1e-6, p_off)
    p_on = appear_probability(n_free=20, s_on_board=4, k_future=3, on_board_now=True)
    # resto = 2, misma p
    _assert(abs(p_on - 0.4375) < 1e-6, p_on)
    _assert(appear_probability(n_free=4, s_on_board=4, k_future=3, on_board_now=False) == 1.0, "denom 0")


def test_classify_reach_ghost_vs_clause_vs_watch() -> None:
    ghost = {
        "player_id": "ghost",
        "seller": "rival",
        "owner_id": "99",
        "clause_known": False,
    }
    clause = {
        "player_id": "star",
        "seller": "rival",
        "owner_id": "99",
        "clause": 4_000_000,
        "clause_known": True,
    }
    watch = {"player_id": "raph", "seller": "free", "owner_id": ""}
    market = {"player_id": "mkt", "seller": "market", "on_daily_market": True}
    _assert(classify_reach(ghost, owned=False, clauses_on=True) == "ghost", ghost)
    _assert(classify_reach(clause, owned=False, clauses_on=True) == "clause", clause)
    _assert(classify_reach(clause, owned=False, clauses_on=False) == "ghost", "clauses off")
    _assert(classify_reach(watch, owned=False, clauses_on=True) == "watch_free", watch)
    _assert(classify_reach(market, owned=False, clauses_on=True) == "market", market)
    _assert(classify_reach(ghost, owned=True, clauses_on=True) == "keep", "owned")


def test_rotation_finance_sales_cover_peak_is_debt() -> None:
    owned = {
        "weak": {
            "player_id": "weak",
            "name": "weak",
            "position": "FW",
            "market_value": 8_000_000,
            "price": 8_000_000,
        }
    }
    dest = [
        {
            "player_id": "star",
            "reach": "market",
            "buy_price": 10_000_000,
            "price": 10_000_000,
        }
    ]
    ok = rotation_finance(
        dest,
        owned_ids={"weak"},
        owned_by_id=owned,
        balance=2_000_000,
        max_debt=40_000_000,
        settle_ok=True,
        sale_remaining=5,
    )
    _assert(ok["ok"] is True, ok)
    _assert(ok["proceeds"] == 8_000_000, ok)
    lag = rotation_finance(
        dest,
        owned_ids={"weak"},
        owned_by_id=owned,
        balance=2_000_000,
        max_debt=40_000_000,
        settle_ok=False,
        sale_remaining=5,
    )
    _assert(lag["ok"] is False, lag)
    _assert(lag["reason"] == "no_recover", lag)
    famine = rotation_finance(
        dest,
        owned_ids={"weak"},
        owned_by_id=owned,
        balance=2_000_000,
        max_debt=5_000_000,
        settle_ok=True,
        sale_remaining=5,
    )
    _assert(famine["ok"] is False, famine)
    _assert(famine["reason"] == "max_debt", famine)


def test_ghost_without_clause_does_not_enter() -> None:
    cands = [
        _raw(
            "ghost",
            "FW",
            seller="rival",
            owner_id="88",
            owner_name="Otro",
            xpts=19.0,
            price=2_200_000,
        )
    ]
    universe = _universe(_owned_15() + [(c, False) for c in cands])
    dest = assemble_destination(
        universe,
        owned_ids=_owned_ids(),
        balance=20_000_000,
        max_debt=40_000_000,
        settle_ok=True,
        sale_remaining=5,
        listed_ids=set(),
        k_future=4,
        clauses_on=True,
        n_free=0,
        s_on_board=0,
    )
    ids = _dest_ids(dest)
    _assert("ghost" not in ids, ids)
    watch_ids = {str(w.get("player_id")) for w in dest.get("watch_frees") or []}
    _assert("ghost" not in watch_ids, watch_ids)


def test_market_free_with_better_xpts_enters() -> None:
    star = _raw(
        "raphinha",
        "FW",
        seller="market",
        on_daily_market=True,
        xpts=12.9,
        price=3_000_000,
    )
    universe = _universe(_owned_15() + [(star, False)])
    dest = assemble_destination(
        universe,
        owned_ids=_owned_ids(),
        balance=5_000_000,
        max_debt=40_000_000,
        settle_ok=True,
        sale_remaining=5,
        listed_ids=set(),
        k_future=2,
        clauses_on=True,
        n_free=1,
        s_on_board=1,
    )
    ids = _dest_ids(dest)
    _assert("raphinha" in ids, ids)
    buys = [r for r in list(dest.get("xi") or []) + list(dest.get("bench") or []) if r.get("player_id") == "raphinha"]
    _assert(buys and buys[0].get("status") == "buy", buys)
    _assert(buys[0].get("acquisition") in ("market", "free"), buys[0])


def test_unlisted_free_is_watch_not_slot() -> None:
    raph = _raw(
        "raphinha",
        "FW",
        seller="free",
        owner_id="",
        xpts=12.9,
        price=3_000_000,
    )
    listed = _raw(
        "mediocre",
        "MF",
        seller="market",
        on_daily_market=True,
        xpts=4.0,
        price=1_500_000,
    )
    universe = _universe(_owned_15() + [(raph, False), (listed, False)])
    dest = assemble_destination(
        universe,
        owned_ids=_owned_ids(),
        balance=8_000_000,
        max_debt=40_000_000,
        settle_ok=True,
        sale_remaining=5,
        listed_ids=set(),
        k_future=3,
        clauses_on=True,
        n_free=2,
        s_on_board=1,
    )
    ids = _dest_ids(dest)
    _assert("raphinha" not in ids, ids)
    watch_ids = {str(w.get("player_id")) for w in dest.get("watch_frees") or []}
    _assert("raphinha" in watch_ids, dest.get("watch_frees"))
    p = (dest.get("watch_frees") or [{}])[0].get("p_appear")
    _assert(p is not None and 0 < float(p) <= 1, p)


def test_sale_that_does_not_settle_cannot_fund_buy() -> None:
    # Destino 16→15: quien salga vale 10 M. El crack cuesta 8 M y el saldo es 2 M.
    # Con cobro a tiempo el neto cuadra; si la venta no llega al KO, no.
    owned = _owned_15(weak_fw_xpts=1.0, weak_fw_price=10_000_000)
    owned = [
        (dict(raw, price=10_000_000, market_value=10_000_000), True)
        for raw, _ in owned
    ]
    star = _raw(
        "star",
        "FW",
        seller="market",
        on_daily_market=True,
        xpts=14.0,
        price=8_000_000,
    )
    kwargs = dict(
        owned_ids=_owned_ids(),
        balance=2_000_000,
        max_debt=40_000_000,
        sale_remaining=5,
        listed_ids=set(),
        k_future=1,
        clauses_on=True,
        n_free=1,
        s_on_board=1,
    )
    blocked = assemble_destination(
        _universe(owned + [(star, False)]),
        settle_ok=False,
        **kwargs,
    )
    _assert("star" not in _dest_ids(blocked), _dest_ids(blocked))
    funded = assemble_destination(
        _universe(owned + [(star, False)]),
        settle_ok=True,
        **kwargs,
    )
    _assert("star" in _dest_ids(funded), _dest_ids(funded))
    _assert((funded.get("finance") or {}).get("ok") is True, funded.get("finance"))


def test_board_exposes_destination_not_three_modes() -> None:
    squad = [r for r, _ in _owned_15()]
    cands = [
        _raw(
            "raphinha",
            "FW",
            seller="market",
            on_daily_market=True,
            xpts=12.9,
            price=3_000_000,
        ),
        _raw(
            "ghost",
            "FW",
            seller="rival",
            owner_id="88",
            owner_name="Otro",
            xpts=18.0,
            price=2_200_000,
        ),
        _raw(
            "waitfree",
            "MF",
            seller="free",
            owner_id="",
            xpts=11.0,
            price=2_000_000,
        ),
    ]
    board = build_target_board(
        slug="test-ko",
        structural_needs=[],
        candidates=cands,
        balance=6_000_000,
        squad=squad,
        squad_value=sum(float(p["price"]) for p in squad),
        previous={},
        me={"max_debt": 40_000_000},
        league_rules={"clauses": True, "economy": {"sale_limit": 5}},
        market_cycle={
            "cycles_left_before_gw": 3,
            "cycle_hours": 24,
            "cash_lag_hours": 24,
            "hours_to_jornada": 96,
        },
        hours_to_jornada=96,
    )
    ids = {str(r.get("player_id")) for r in (board.get("destination_15") or []) if r.get("player_id")}
    _assert("ghost" not in ids, ids)
    _assert("raphinha" in ids, ids)
    _assert("waitfree" not in ids, ids)
    watch_ids = {str(w.get("player_id")) for w in (board.get("watch_frees") or [])}
    _assert("waitfree" in watch_ids, board.get("watch_frees"))
    _assert(board.get("perfect_squad_aspirational") == [], board.get("perfect_squad_aspirational"))
    _assert("path" in board, board.keys())
    _assert("constraints" in board, board.keys())
    _assert((board.get("constraints") or {}).get("cycles_left") == 3, board.get("constraints"))
    path = board.get("path") or []
    _assert(any(m.get("kind") == "bid" and m.get("player_id") == "raphinha" for m in path), path)


def test_build_path_lists_then_bids() -> None:
    dest = [
        {
            "player_id": "star",
            "name": "star",
            "position": "FW",
            "acquisition": "market",
            "reach": "market",
            "buy_price": 4_000_000,
            "price": 4_000_000,
        }
    ]
    finance = {
        "ok": True,
        "sells": [
            {
                "player_id": "weak",
                "name": "weak",
                "position": "FW",
                "market_value": 3_000_000,
            }
        ],
    }
    path = build_path(
        dest,
        owned_ids={"weak"},
        finance=finance,
        k_future=2,
        settle_ok=True,
    )
    kinds = [m.get("kind") for m in path]
    _assert(kinds[0] == "list", kinds)
    _assert("bid" in kinds, kinds)
    _assert(all(m.get("cycle") == 0 for m in path if m.get("kind") in ("list", "bid")), path)


def test_typical_week_strips_fdr() -> None:
    _assert(typical_week_xpts({"xpts_base": 10.0, "xpts_p_play": 0.8}) == 8.0, "base")
    _assert(abs(typical_week_xpts({"xpts": 9.0, "fdr_multiplier": 1.5}) - 6.0) < 1e-9, "fdr")
    _assert(typical_week_xpts({"xpts": 7.0}) == 7.0, "plain")


def _assemble(universe: list[dict], **kw):
    defaults = dict(
        owned_ids=_owned_ids(),
        balance=20_000_000,
        max_debt=40_000_000,
        settle_ok=True,
        sale_remaining=5,
        listed_ids=set(),
        k_future=3,
        clauses_on=True,
        n_free=0,
        s_on_board=0,
    )
    defaults.update(kw)
    return assemble_destination(universe, **defaults)


def test_clause_rental_does_not_enter() -> None:
    owned = []
    for raw, flag in _owned_15(weak_fw_xpts=6.0):
        row = dict(raw)
        if row["id"] == "f1":
            row["xpts_base"] = 9.0
            row["xpts_p_play"] = 0.9
        owned.append((row, flag))
    rental = _raw(
        "rental",
        "FW",
        seller="rival",
        owner_id="99",
        owner_name="Rival",
        clause=4_000_000,
        clause_known=True,
        xpts=10.0,
        xpts_base=4.0,
        xpts_p_play=0.9,
        price=2_000_000,
        market_value=2_000_000,
    )
    dest = _assemble(_universe(owned + [(rental, False)]))
    _assert("rental" not in _dest_ids(dest), _dest_ids(dest))
    inc = {
        "player_id": "f1",
        "position": "FW",
        "xpts": 6.0,
        "xpts_base": 9.0,
        "xpts_p_play": 0.9,
    }
    cand = {
        "player_id": "rental",
        "position": "FW",
        "reach": "clause",
        "xpts": 10.0,
        "xpts_base": 4.0,
        "xpts_p_play": 0.9,
        "clause": 4_000_000,
        "buy_price": 4_000_000,
        "market_value": 2_000_000,
    }
    _assert(
        swap_wealth_ok(cand, xi_now=[inc], t_xi=[cand], owned_ids={"f1"}) is False,
        "alquiler debe cortar",
    )


def test_rising_starter_not_sold_for_spike() -> None:
    owned = []
    for raw, flag in _owned_15(weak_fw_xpts=5.8):
        row = dict(raw)
        if row["id"] == "f1":
            row["delta_5d"] = 0.12
        owned.append((row, flag))
    spike = _raw(
        "spike",
        "FW",
        seller="market",
        on_daily_market=True,
        xpts=6.2,
        price=3_000_000,
    )
    dest = _assemble(_universe(owned + [(spike, False)]), n_free=1, s_on_board=1)
    _assert("spike" not in _dest_ids(dest), _dest_ids(dest))
    _assert("f1" in _dest_ids(dest), _dest_ids(dest))


if __name__ == "__main__":
    test_appear_probability_lottery_not_queue()
    test_classify_reach_ghost_vs_clause_vs_watch()
    test_rotation_finance_sales_cover_peak_is_debt()
    test_ghost_without_clause_does_not_enter()
    test_market_free_with_better_xpts_enters()
    test_unlisted_free_is_watch_not_slot()
    test_sale_that_does_not_settle_cannot_fund_buy()
    test_board_exposes_destination_not_three_modes()
    test_build_path_lists_then_bids()
    test_typical_week_strips_fdr()
    test_clause_rental_does_not_enter()
    test_rising_starter_not_sold_for_spike()
    print("test_target_board: OK")
