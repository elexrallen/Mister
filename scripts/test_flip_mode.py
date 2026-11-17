"""Modo reventa: snapshot, recargo diario, margen de la máquina y diario."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import auto_executor as ax  # noqa: E402
from behavior_log import record_cycle, reconcile, summarize  # noqa: E402
from flip_mode import (  # noqa: E402
    build_flip_cycle,
    daily_change_euros,
    fresh_book,
    quote_bid,
    load_book,
    save_book,
)

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
M = 1_000_000


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _player(
    pid: str,
    *,
    name: str,
    vm: float,
    ask: float,
    prev: float | None = None,
    rival: bool = False,
    on_sale: bool = False,
) -> dict:
    row = {
        "id": pid,
        "player_id": pid,
        "name": name,
        "position": "MF",
        "market_value": vm,
        "price": vm,
        "puja_recomendada": ask,
        "prev_market_value": prev,
        "on_daily_market": not rival,
        "seller": "market",
        "id_market": 100 + int(pid) if pid.isdigit() else 100,
    }
    if rival:
        row["listed_by_rival"] = True
        row["owner_id"] = "9"
        row["on_daily_market"] = True
    if on_sale:
        row["on_sale"] = True
    return row


def _rules(**over) -> dict:
    rules = {"max_squad": 25, "sale_limit": 5, "transfer_wait": 0}
    rules.update(over)
    return rules


def test_daily_change_is_euros() -> None:
    up = _player("1", name="Soler", vm=6 * M, ask=6 * M, prev=6 * M - 50_000)
    down = _player("2", name="Baja", vm=6.5 * M, ask=6.4 * M, prev=6.5 * M + 80_000)
    _assert(daily_change_euros(up) == 50_000, daily_change_euros(up))
    _assert(daily_change_euros(down) == -80_000, daily_change_euros(down))
    plain = _player("3", name="Plano", vm=5 * M, ask=5 * M, prev=None)
    _assert(daily_change_euros(plain) is None, daily_change_euros(plain))
    konate = _player("4", name="Konate", vm=11_126_000, ask=12_238_600, prev=None)
    konate["delta_1d"] = 0.0057
    _assert(abs(daily_change_euros(konate) - 63_058.76) < 1, daily_change_euros(konate))
    quoted = quote_bid(konate)
    _assert(quoted["bid"] == 11_144_918 and quoted["eligible"], quoted)


def test_bid_premium_and_margin() -> None:
    squad = [_player(str(i), name=f"P{i}", vm=1 * M, ask=1 * M) for i in range(18)]
    market = [
        _player("21", name="Soler", vm=6 * M, ask=6 * M, prev=6 * M - 50_000),
        _player("22", name="Caliente", vm=6 * M, ask=6 * M, prev=6 * M - 400_000),
        _player("23", name="Frio", vm=6.5 * M, ask=6.4 * M, prev=6.5 * M + 80_000),
        _player("24", name="Caro", vm=7 * M, ask=7.2 * M, prev=7 * M),
        _player("25", name="Justo", vm=5 * M, ask=5.1 * M, prev=5 * M),
        _player("26", name="Hueco", vm=5 * M, ask=5_090_000, prev=5 * M),
        _player("27", name="Barato", vm=3.9 * M, ask=3.9 * M, prev=3.9 * M),
        _player("28", name="Tope", vm=4 * M, ask=4 * M, prev=4 * M),
        _player("29", name="Rival", vm=6 * M, ask=6 * M, prev=6 * M, rival=True),
    ]
    plan, book = build_flip_cycle(
        book=fresh_book(),
        enabled=True,
        squad=squad,
        market=market,
        sales_state={},
        me={"balance": 30 * M},
        league_rules=_rules(max_squad=25),
        now=NOW,
    )
    bids = {m["player_id"]: m for m in plan["moves"] if m["kind"] == "bid"}
    _assert("21" in bids, bids.keys())
    _assert(bids["21"]["amount"] == 6_015_000, bids["21"]["amount"])
    _assert("22" not in bids, "la subida de 400k se come el margen")
    _assert("23" not in bids, "baja de VM: no se puja en reventa")
    _assert("24" not in bids and "25" not in bids and "26" not in bids, "VM plano")
    _assert("27" not in bids and "28" not in bids, "4M justo o por debajo no entra")
    _assert("29" not in bids, "rival")
    frozen = set(book["frozen_ids"])
    _assert(frozen == {str(i) for i in range(18)}, frozen)
    _assert(all(m.get("flip_play") for m in bids.values()), "falta flip_play")


def test_cash_keeps_the_wider_margin() -> None:
    squad = [_player(str(i), name=f"P{i}", vm=1 * M, ask=1 * M) for i in range(18)]
    market = [
        _player("21", name="Soler", vm=6 * M, ask=6 * M, prev=6 * M - 50_000),
        _player("23", name="Frio", vm=6.5 * M, ask=6.4 * M, prev=6.5 * M + 80_000),
    ]
    plan, _book = build_flip_cycle(
        book=fresh_book(),
        enabled=True,
        squad=squad,
        market=market,
        sales_state={},
        me={"balance": 12 * M},
        league_rules=_rules(max_squad=20),
        now=NOW,
    )
    bids = [m for m in plan["moves"] if m["kind"] == "bid"]
    _assert(len(bids) == 1 and bids[0]["player_id"] == "21", bids)


def test_snapshot_is_not_retaken() -> None:
    squad = [_player("1", name="A", vm=1 * M, ask=1 * M), _player("2", name="B", vm=1 * M, ask=1 * M)]
    _plan, book = build_flip_cycle(
        book=fresh_book(),
        enabled=True,
        squad=squad,
        market=[],
        sales_state={},
        me={"balance": 10 * M},
        league_rules=_rules(),
        now=NOW,
    )
    later = squad + [_player("3", name="Nuevo", vm=2 * M, ask=2 * M)]
    plan, book = build_flip_cycle(
        book=book,
        enabled=True,
        squad=later,
        market=[],
        sales_state={},
        me={"balance": 10 * M},
        league_rules=_rules(),
        now=NOW + timedelta(hours=8),
    )
    _assert(set(book["frozen_ids"]) == {"1", "2"}, book["frozen_ids"])
    _assert("3" in book["positions"], book["positions"])
    kinds = {m["kind"] for m in plan["moves"] if m["player_id"] in {"1", "2"}}
    _assert(not kinds, kinds)


def test_offer_threshold_and_frozen_untouched() -> None:
    book = fresh_book()
    book["active"] = True
    book["frozen_ids"] = ["1"]
    book["positions"] = {
        "9": {
            "player_id": "9",
            "name": "Flip",
            "buy_price": 6_404_000,
            "status": "listed",
            "listed": True,
        }
    }
    squad = [
        _player("1", name="Titular", vm=9 * M, ask=9 * M),
        _player("9", name="Flip", vm=6.5 * M, ask=6.5 * M, on_sale=True),
    ]
    offers = [
        {"player_id": "1", "name": "Titular", "amount": 9.5 * M, "market_value": 9 * M},
        {"player_id": "9", "name": "Flip", "amount": 6_596_120, "market_value": 6.5 * M},
    ]
    plan, _book = build_flip_cycle(
        book=book,
        enabled=True,
        squad=squad,
        market=[],
        sales_state={"pending_offers": offers},
        me={"balance": 1 * M},
        league_rules=_rules(),
        now=NOW,
    )
    kinds = {(m["player_id"], m["kind"]) for m in plan["moves"]}
    _assert(("1", "accept_offer") not in kinds and ("1", "decline_offer") not in kinds, kinds)
    _assert(("9", "decline_offer") in kinds, kinds)
    _assert(("9", "accept_offer") not in kinds, kinds)

    offers[1]["amount"] = 6_650_000
    plan, _book = build_flip_cycle(
        book=book,
        enabled=True,
        squad=squad,
        market=[],
        sales_state={"pending_offers": offers},
        me={"balance": 1 * M},
        league_rules=_rules(),
        now=NOW,
    )
    accept = next(m for m in plan["moves"] if m["kind"] == "accept_offer")
    _assert(accept["player_id"] == "9" and accept["amount"] == 6_650_000, accept)


def test_disable_stops_bids_and_keeps_exit() -> None:
    book = fresh_book()
    book["active"] = True
    book["frozen_ids"] = ["1"]
    book["positions"] = {
        "9": {
            "player_id": "9",
            "name": "Flip",
            "buy_price": 6_404_000,
            "status": "owned",
            "listed": False,
        }
    }
    squad = [
        _player("1", name="Titular", vm=9 * M, ask=9 * M),
        _player("9", name="Flip", vm=6.5 * M, ask=6.5 * M),
    ]
    market = [_player("21", name="Soler", vm=6 * M, ask=6 * M, prev=6 * M - 50_000)]
    plan, book = build_flip_cycle(
        book=book,
        enabled=False,
        squad=squad,
        market=market,
        sales_state={},
        me={"balance": 20 * M},
        league_rules=_rules(),
        now=NOW,
    )
    kinds = [m["kind"] for m in plan["moves"]]
    _assert("bid" not in kinds, kinds)
    _assert(any(m["kind"] == "list_for_sale" and m["player_id"] == "9" for m in plan["moves"]), plan["moves"])
    _assert(book["active"] is False, book["active"])


def test_full_sale_limit_blocks_new_bids() -> None:
    squad = [_player("1", name="L", vm=5 * M, ask=5 * M, on_sale=True)]
    market = [_player("21", name="Soler", vm=6 * M, ask=6 * M, prev=6 * M - 50_000)]
    plan, _book = build_flip_cycle(
        book=fresh_book(),
        enabled=True,
        squad=squad,
        market=market,
        sales_state={},
        me={"balance": 20 * M},
        league_rules=_rules(sale_limit=1, max_squad=5),
        now=NOW,
    )
    _assert(not any(m["kind"] == "bid" for m in plan["moves"]), plan["moves"])


def test_book_roundtrip(tmp: Path | None = None) -> None:
    root = tmp or Path(__file__).resolve().parent / "_flip_tmp"
    root.mkdir(parents=True, exist_ok=True)
    book = fresh_book()
    book["frozen_ids"] = ["7"]
    save_book("liga-prueba", book, root=root)
    loaded = load_book("liga-prueba", root=root)
    _assert(loaded["frozen_ids"] == ["7"], loaded)
    # no dejar basura
    target = root / "liga-prueba" / "flip_book.json"
    target.unlink(missing_ok=True)
    target.parent.rmdir()


def test_behavior_win_and_profit() -> None:
    at = NOW.isoformat()
    log = {"league": "liga", "cycles": []}
    log = record_cycle(
        log,
        {
            "at": at,
            "mode": "reventa",
            "decisions": [
                {
                    "kind": "bid",
                    "player_id": "9",
                    "name": "Flip",
                    "amount": 6_404_000,
                    "buy_price": 6_404_000,
                    "status": "recomendada",
                    "why": "puja",
                }
            ],
        },
    )
    log = reconcile(log, squad_ids={"9"}, market_ids=set(), now=NOW + timedelta(hours=8))
    bid = log["cycles"][0]["decisions"][0]
    _assert(bid["result"] == "ganada", bid)

    later = (NOW + timedelta(days=1)).isoformat()
    log = record_cycle(
        log,
        {
            "at": later,
            "mode": "reventa",
            "decisions": [
                {
                    "kind": "accept_offer",
                    "player_id": "9",
                    "name": "Flip",
                    "amount": 6_650_000,
                    "buy_price": 6_404_000,
                    "status": "recomendada",
                    "why": "cierra",
                },
                {
                    "kind": "accept_offer",
                    "player_id": "8",
                    "name": "Justo",
                    "amount": 6_404_000 * 1.03,
                    "buy_price": 6_404_000,
                    "status": "recomendada",
                    "why": "en el borde",
                },
            ],
        },
    )
    summary = summarize(log, now=NOW + timedelta(days=1))
    _assert(summary["d7"]["profit"] == 246_000, summary["d7"])
    _assert(summary["d7"]["accepts"] == 1, summary["d7"])


def test_flip_play_ignores_xi_and_percent_caps() -> None:
    move = {
        "kind": "bid",
        "player_id": "21",
        "name": "Soler",
        "amount": 6_015_000,
        "bid": 6_015_000,
        "flip_play": True,
        "is_xi_starter": False,
        "rising": True,
        "delta_5d": 0.04,
        "id_market": 121,
        "why": "reventa",
    }
    decision = ax.plan_operations(
        cycle_plan={
            "constraints": {
                "balance": 8 * M,
                "spendable": 8 * M,
                "max_debt": 8 * M,
                "free_slots_after_accepts": 2,
                "sale_remaining": 3,
            },
            "moves": [move],
        },
        settings={
            "slug": "test",
            "enabled": True,
            "dry_run": True,
            "kill_switch_off": False,
            "league_registered": True,
            "allowed_actions": ["bid", "list_for_sale", "accept_offer", "decline_offer"],
            "max_spend_per_cycle_pct": 0.60,
            "max_single_buy_pct": 0.40,
            "max_clause_pct": 0.30,
            "max_clauses_per_cycle": 1,
            "max_ops_per_cycle": 6,
            "max_pending_offers": 2,
            "offer_stale_cycles": 2,
            "min_cash_floor": 0,
            "never_sell_xi_starters": True,
            "require_xi_starters": True,
            "require_positive_trend": True,
            "allow_rescind": False,
        },
        state={"xi_ids": ["21"]},
        league_rules={"transfer_wait": 0},
        now=NOW,
    )
    ops = decision["operations"]
    _assert(len(ops) == 1 and ops[0]["player_id"] == "21", decision["skipped"])


def test_flip_skips_without_positive_revaluation() -> None:
    falling = _player("23", name="Frio", vm=6.5 * M, ask=6.4 * M, prev=6.5 * M + 80_000)
    flat = _player("24", name="Caro", vm=7 * M, ask=7.2 * M, prev=7 * M)
    quoted_down = quote_bid(falling)
    quoted_flat = quote_bid(flat)
    _assert(quoted_down["eligible"] is False, quoted_down)
    _assert(quoted_flat["eligible"] is False, quoted_flat)

    decision = ax.plan_operations(
        cycle_plan={
            "flip": {"enabled": True},
            "constraints": {
                "balance": 20 * M,
                "spendable": 20 * M,
                "max_debt": 20 * M,
                "free_slots_after_accepts": 2,
                "sale_remaining": 3,
            },
            "moves": [
                {
                    "kind": "bid",
                    "player_id": "23",
                    "name": "Frio",
                    "amount": 6_504_000,
                    "flip_play": True,
                    "rising": False,
                    "delta_5d": -0.012,
                    "id_market": 123,
                }
            ],
        },
        settings={
            "slug": "test",
            "enabled": True,
            "dry_run": True,
            "kill_switch_off": False,
            "league_registered": True,
            "allowed_actions": ["bid"],
            "max_spend_per_cycle_pct": 0.60,
            "max_single_buy_pct": 0.40,
            "max_clause_pct": 0.30,
            "max_clauses_per_cycle": 1,
            "max_ops_per_cycle": 6,
            "max_pending_offers": 2,
            "offer_stale_cycles": 2,
            "min_cash_floor": 0,
            "never_sell_xi_starters": True,
            "require_xi_starters": True,
            "require_positive_trend": True,
            "allow_rescind": False,
        },
        state={},
        league_rules={"transfer_wait": 0},
        now=NOW,
    )
    _assert(not decision["operations"], decision["operations"])
    reasons = " ".join(s.get("reason") or "" for s in decision["skipped"])
    _assert("reventa" in reasons, decision["skipped"])


def test_apply_flip_toggle_accepts_quoted_json() -> None:
    import tempfile

    from flip_mode import apply_flip_toggle, flip_enabled

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for raw in ("true", '"true"', "'true'", True, "1"):
            _assert(apply_flip_toggle("laliga", raw, root=root) is True, raw)
            _assert(flip_enabled("laliga", root) is True, raw)
        _assert(apply_flip_toggle("laliga", '"false"', root=root) is True, "false quoted")
        _assert(flip_enabled("laliga", root) is False, "off")
        _assert(apply_flip_toggle("all", "true", root=root) is False, "all")
        _assert(apply_flip_toggle("laliga", "maybe", root=root) is False, "garbage")


def main() -> None:
    test_daily_change_is_euros()
    test_bid_premium_and_margin()
    test_cash_keeps_the_wider_margin()
    test_snapshot_is_not_retaken()
    test_offer_threshold_and_frozen_untouched()
    test_disable_stops_bids_and_keeps_exit()
    test_full_sale_limit_blocks_new_bids()
    test_book_roundtrip()
    test_behavior_win_and_profit()
    test_flip_play_ignores_xi_and_percent_caps()
    test_flip_skips_without_positive_revaluation()
    test_apply_flip_toggle_accepts_quoted_json()
    print("flip mode ok")


if __name__ == "__main__":
    main()
