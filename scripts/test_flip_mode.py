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


def _open_window(**over) -> dict:
    """80 h al pitido, cierre en 4 h y ciclos de 8 h: al poder vender quedan 9."""
    clock = {
        "hours_to_solvency_deadline": 80,
        "cash_lag_hours": 4,
        "cycle_hours": 8,
    }
    clock.update(over)
    return clock


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
        **_open_window(),
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
        **_open_window(),
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
    _assert(set(book["frozen_ids"]) == {"1", "2", "3"}, book["frozen_ids"])
    _assert("3" not in book["positions"], book["positions"])
    kinds = {m["kind"] for m in plan["moves"] if m["player_id"] in {"1", "2", "3"}}
    _assert(not kinds, kinds)


def test_watched_arrival_is_listed() -> None:
    """La puja de la reventa sí abre posición y sale a la venta."""
    book = fresh_book()
    book["active"] = True
    book["frozen_ids"] = ["1"]
    book["watches"] = {
        "3": {"player_id": "3", "name": "Vigilado", "buy_price": 2_060_000, "bid_at": NOW.isoformat()}
    }
    squad = [
        _player("1", name="A", vm=1 * M, ask=1 * M),
        _player("3", name="Vigilado", vm=2 * M, ask=2 * M),
    ]
    plan, book = build_flip_cycle(
        book=book,
        enabled=True,
        squad=squad,
        market=[],
        sales_state={},
        me={"balance": 10 * M},
        league_rules=_rules(),
        now=NOW,
    )
    pos = book["positions"]["3"]
    _assert(pos["buy_price"] == 2_060_000, pos)
    _assert("3" not in book["watches"], book["watches"])
    _assert("3" not in book["frozen_ids"], book["frozen_ids"])
    listed = [m for m in plan["moves"] if m["player_id"] == "3"]
    _assert(len(listed) == 1 and listed[0]["kind"] == "list_for_sale", listed)


def test_manual_signing_already_listed_is_withdrawn() -> None:
    """Sin precio de compra no es reventa: se congela y se retira del mercado."""
    book = fresh_book()
    book["active"] = True
    book["frozen_ids"] = ["1"]
    book["positions"] = {
        "9": {
            "player_id": "9",
            "name": "Monteiro",
            "buy_price": None,
            "status": "listed",
            "listed": True,
        },
        "8": {
            "player_id": "8",
            "name": "Ya vendido",
            "buy_price": None,
            "status": "sold",
            "listed": True,
        },
    }
    squad = [
        _player("1", name="A", vm=1 * M, ask=1 * M),
        _player("9", name="Monteiro", vm=160_000, ask=160_000, on_sale=True),
    ]
    plan, book = build_flip_cycle(
        book=book,
        enabled=True,
        squad=squad,
        market=[],
        sales_state={
            "pending_offers": [
                {"player_id": "9", "name": "Monteiro", "amount": 500_000, "status": "pending"}
            ]
        },
        me={"balance": 10 * M},
        league_rules=_rules(),
        now=NOW,
    )
    _assert("9" not in book["positions"], book["positions"])
    _assert("9" in book["frozen_ids"], book["frozen_ids"])
    _assert("9" in book["manual_ids"], book["manual_ids"])
    _assert(book["positions"]["8"]["status"] == "sold", book["positions"])
    kinds = {(m["player_id"], m["kind"]) for m in plan["moves"]}
    _assert(("9", "unlist") in kinds, kinds)
    _assert(("9", "list_for_sale") not in kinds, kinds)
    _assert(("9", "accept_offer") not in kinds, kinds)
    withdrawn = next(m for m in plan["moves"] if m["player_id"] == "9")
    _assert(withdrawn.get("flip_play") is True, withdrawn)
    _assert(withdrawn.get("amount") is None, withdrawn)
    _assert(plan["flip"]["open_count"] == 0, plan["flip"])

    still_listed, book = build_flip_cycle(
        book=book,
        enabled=True,
        squad=[
            _player("1", name="A", vm=1 * M, ask=1 * M),
            _player("9", name="Monteiro", vm=160_000, ask=160_000, on_sale=True),
        ],
        market=[],
        sales_state={},
        me={"balance": 10 * M},
        league_rules=_rules(),
        now=NOW + timedelta(hours=4),
    )
    _assert(
        [(m["player_id"], m["kind"]) for m in still_listed["moves"]] == [("9", "unlist")],
        still_listed["moves"],
    )

    quiet, book = build_flip_cycle(
        book=book,
        enabled=True,
        squad=[
            _player("1", name="A", vm=1 * M, ask=1 * M),
            _player("9", name="Monteiro", vm=160_000, ask=160_000),
        ],
        market=[],
        sales_state={},
        me={"balance": 10 * M},
        league_rules=_rules(),
        now=NOW + timedelta(hours=8),
    )
    _assert(not any(m["player_id"] == "9" for m in quiet["moves"]), quiet["moves"])
    _assert("9" in book["frozen_ids"] and "9" not in book["positions"], book)


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
        {
            "player_id": "9",
            "name": "Flip",
            "amount": 6_596_120,
            "market_value": 6.5 * M,
            "id_bid": 847981312,
            "offer_id": "847981312",
            "id_market": 19813428020,
            "id_uc": 0,
            "owner_id": "15399168",
        },
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
    _assert(("9", "renew_offer") in kinds, kinds)
    _assert(("9", "decline_offer") not in kinds, kinds)
    _assert(("9", "accept_offer") not in kinds, kinds)
    renew = next(m for m in plan["moves"] if m["kind"] == "renew_offer")
    _assert(renew.get("id_bid") in (847981312, "847981312"), renew)
    _assert(renew.get("id_market") == 19813428020, renew)
    _assert(renew.get("id_uc") == 0, renew)
    _assert("renueva" in (renew.get("why") or "").lower(), renew)

    # 6,65 M pasa el +3% de la compra (6,60 M) y se queda bajo el +3% del VM (6,70 M).
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
    kinds = {(m["player_id"], m["kind"]) for m in plan["moves"]}
    _assert(("9", "renew_offer") in kinds, kinds)
    _assert(("9", "accept_offer") not in kinds, kinds)
    renew = next(m for m in plan["moves"] if m["kind"] == "renew_offer")
    _assert(renew.get("threshold") == round(6.5 * M * 1.03), renew)

    # Justo el +3% del VM se acepta.
    exact = round(6.5 * M * 1.03)
    offers[1]["amount"] = exact
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
    _assert(accept["player_id"] == "9" and accept["amount"] == exact, accept)
    _assert(accept.get("threshold") == exact, accept)

    offers[1]["amount"] = 6_710_000
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
    _assert(accept["player_id"] == "9" and accept["amount"] == 6_710_000, accept)
    _assert(accept.get("id_bid") in (847981312, "847981312"), accept)
    _assert(accept.get("threshold") == round(6.5 * M * 1.03), accept)
    _assert("valor de mercado" in (accept.get("why") or "").lower(), accept)


def test_fallen_market_accepts_below_buy_price() -> None:
    """Si el valor cae, el +3% se mide sobre el VM actual, no sobre la compra."""
    book = fresh_book()
    book["active"] = True
    book["frozen_ids"] = []
    book["positions"] = {
        "9": {
            "player_id": "9",
            "name": "Caido",
            "buy_price": 8_000_000,
            "status": "listed",
            "listed": True,
        }
    }
    squad = [_player("9", name="Caido", vm=6.5 * M, ask=6.5 * M, on_sale=True)]
    offer = {
        "player_id": "9",
        "name": "Caido",
        "amount": 6_710_000,
        "market_value": 8_000_000,
        "id_bid": "91",
        "id_market": 91,
        "id_uc": 0,
    }
    plan, _book = build_flip_cycle(
        book=book,
        enabled=True,
        squad=squad,
        market=[],
        sales_state={"pending_offers": [offer]},
        me={"balance": 1 * M},
        league_rules=_rules(),
        now=NOW,
    )
    accept = next(m for m in plan["moves"] if m["kind"] == "accept_offer")
    _assert(accept["player_id"] == "9" and accept["amount"] == 6_710_000, accept)
    _assert(accept["amount"] < 8_000_000 * 1.03, accept)
    _assert(accept.get("threshold") == round(6.5 * M * 1.03), accept)


def test_konate_accepts_and_the_other_two_renew() -> None:
    """Konaté supera el +3%. Ünal y Veiga se renuevan, no se rechazan."""
    book = fresh_book()
    book["active"] = True
    book["frozen_ids"] = []
    book["positions"] = {
        "70922": {"player_id": "70922", "name": "I. Konaté", "buy_price": 11_144_900, "status": "listed", "listed": True},
        "6044": {"player_id": "6044", "name": "E. Ünal", "buy_price": 5_369_100, "status": "listed", "listed": True},
        "65347": {"player_id": "65347", "name": "R. Veiga", "buy_price": 9_937_200, "status": "listed", "listed": True},
    }
    squad = [
        _player("70922", name="I. Konaté", vm=11_126_000, ask=11_126_000, on_sale=True),
        _player("6044", name="E. Ünal", vm=5_368_000, ask=5_368_000, on_sale=True),
        _player("65347", name="R. Veiga", vm=9_933_000, ask=9_933_000, on_sale=True),
    ]
    offers = [
        {"player_id": "70922", "name": "I. Konaté", "amount": 11_571_040, "id_bid": "847981378", "id_market": 19813428020, "id_uc": 0},
        {"player_id": "6044", "name": "E. Ünal", "amount": 5_471_280, "id_bid": "847981312", "id_market": 19813428021, "id_uc": 0},
        {"player_id": "65347", "name": "R. Veiga", "amount": 9_436_350, "id_bid": "847981399", "id_market": 19813428022, "id_uc": 0},
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
    by_id = {m["player_id"]: m for m in plan["moves"]}
    _assert(by_id["70922"]["kind"] == "accept_offer", by_id["70922"])
    _assert(by_id["70922"]["id_bid"] == "847981378", by_id["70922"])
    _assert(by_id["6044"]["kind"] == "renew_offer" and by_id["6044"]["id_market"] == 19813428021, by_id["6044"])
    _assert(by_id["65347"]["kind"] == "renew_offer" and by_id["65347"]["id_market"] == 19813428022, by_id["65347"])
    _assert(all(m["kind"] != "decline_offer" for m in plan["moves"]), plan["moves"])

    offers[0]["status"] = "decline"
    plan, _book = build_flip_cycle(
        book=book,
        enabled=True,
        squad=squad,
        market=[],
        sales_state={"pending_offers": offers[1:], "offers_received": offers},
        me={"balance": 1 * M},
        league_rules=_rules(),
        now=NOW,
    )
    by_id = {m["player_id"]: m for m in plan["moves"]}
    _assert(by_id["70922"]["kind"] == "accept_offer", by_id.get("70922"))
    _assert(by_id["6044"]["kind"] == "renew_offer", by_id.get("6044"))
    _assert(by_id["65347"]["kind"] == "renew_offer", by_id.get("65347"))


def test_disable_clears_the_book_and_refreeze_includes_old_signings() -> None:
    """Apagar olvida la tanda. Al volver a activar, esos fichajes se congelan con la plantilla."""
    book = fresh_book()
    book["active"] = True
    book["frozen_ids"] = ["1"]
    book["watches"] = {"21": {"player_id": "21", "name": "Soler", "buy_price": 6_015_000}}
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
    _assert(plan["moves"] == [], plan["moves"])
    _assert(book == fresh_book(), book)
    _assert(plan["flip"]["open_count"] == 0, plan["flip"])

    again, book = build_flip_cycle(
        book=book,
        enabled=True,
        squad=squad,
        market=market,
        sales_state={},
        me={"balance": 20 * M},
        league_rules=_rules(),
        now=NOW + timedelta(hours=1),
    )
    _assert(set(book["frozen_ids"]) == {"1", "9"}, book["frozen_ids"])
    _assert("9" not in book["positions"], book["positions"])
    _assert(not any(m["player_id"] == "9" for m in again["moves"]), again["moves"])


def _rising(pid: str, name: str) -> dict:
    return _player(pid, name=name, vm=6 * M, ask=6 * M, prev=6 * M - 50_000)


def _owned_book(ids: list[str]) -> dict:
    book = fresh_book()
    book["active"] = True
    book["frozen_ids"] = []
    book["positions"] = {
        pid: {
            "player_id": pid,
            "name": f"R{pid}",
            "buy_price": 5_000_000,
            "status": "owned",
            "listed": False,
        }
        for pid in ids
    }
    return book


def test_bid_needs_four_cycles_before_the_first_whistle() -> None:
    """Solo se puja si, al poder vender, quedan 4 ciclos enteros antes del pitido."""
    common = dict(
        book=fresh_book(),
        enabled=True,
        squad=[],
        market=[_rising("21", "Soler")],
        sales_state={},
        me={"balance": 20 * M, "max_debt": 40 * M},
        league_rules=_rules(sale_limit=10, max_squad=25),
        now=NOW,
    )

    def _bids(**clock):
        plan, book = build_flip_cycle(**common, **clock)
        bids = [m for m in plan["moves"] if m["kind"] == "bid"]
        why = " ".join(s.get("why") or "" for s in plan["flip_skipped"])
        return bids, why, book

    # Cierre a las 4 h, ciclos de 8 h: 4 + 4×8 = 36 h justas son 4 ciclos.
    exact, why, _book = _bids(hours_to_solvency_deadline=36, cash_lag_hours=4, cycle_hours=8)
    _assert(len(exact) == 1 and exact[0]["player_id"] == "21", exact)
    _assert("primer partido" not in why, why)

    short, why, _book = _bids(hours_to_solvency_deadline=35.9, cash_lag_hours=4, cycle_hours=8)
    _assert(not short, short)
    _assert("quedan 3 ciclos" in why and "hacen falta 4" in why, why)

    # La espera se suma al cierre: se puede vender a las 4+24 h.
    waited, why, _book = _bids(
        hours_to_solvency_deadline=60,
        cash_lag_hours=4,
        cycle_hours=8,
        transfer_wait_hours=24,
    )
    _assert(len(waited) == 1, waited)
    waited_short, why, _book = _bids(
        hours_to_solvency_deadline=59.9,
        cash_lag_hours=4,
        cycle_hours=8,
        transfer_wait_hours=24,
    )
    _assert(not waited_short, waited_short)
    _assert("quedan 3 ciclos" in why, why)

    for missing in (
        {"cash_lag_hours": 4, "cycle_hours": 8},
        {"hours_to_solvency_deadline": 80, "cycle_hours": 8},
        {"hours_to_solvency_deadline": 80, "cash_lag_hours": 4},
    ):
        blocked, why, _book = _bids(**missing)
        _assert(not blocked, (missing, blocked))
        _assert("sin reloj" in why, (missing, why))

    # Una vigilancia viva tampoco se reenvía si el margen no está asegurado.
    watched = fresh_book()
    watched["watches"] = {"21": {"player_id": "21", "name": "Soler", "buy_price": 6_015_000}}
    plan, book = build_flip_cycle(
        **{**common, "book": watched},
        hours_to_solvency_deadline=20,
        cash_lag_hours=4,
        cycle_hours=8,
    )
    _assert(not any(m["kind"] == "bid" for m in plan["moves"]), plan["moves"])
    _assert("21" in book["watches"], book["watches"])
    why = " ".join(s.get("why") or "" for s in plan["flip_skipped"])
    _assert("quedan" in why and "primer partido" in why, why)


def test_five_open_positions_block_new_bids() -> None:
    ids = [str(i) for i in range(1, 6)]
    squad = [_player(pid, name=f"R{pid}", vm=5 * M, ask=5 * M) for pid in ids]
    plan, _book = build_flip_cycle(
        book=_owned_book(ids),
        enabled=True,
        squad=squad,
        market=[_rising("21", "Soler")],
        sales_state={},
        me={"balance": 40 * M},
        league_rules=_rules(sale_limit=10, max_squad=25),
        now=NOW,
    )
    _assert(not any(m["kind"] == "bid" for m in plan["moves"]), plan["moves"])
    reasons = " ".join(s.get("why") or "" for s in plan["flip_skipped"])
    _assert("cupo de reventa lleno (5)" in reasons, plan["flip_skipped"])
    _assert(plan["flip"]["max_open"] == 5, plan["flip"])


def test_four_open_positions_allow_one_bid() -> None:
    ids = [str(i) for i in range(1, 5)]
    squad = [_player(pid, name=f"R{pid}", vm=5 * M, ask=5 * M) for pid in ids]
    plan, _book = build_flip_cycle(
        book=_owned_book(ids),
        enabled=True,
        squad=squad,
        market=[_rising("21", "Soler"), _rising("22", "Otro")],
        sales_state={},
        me={"balance": 40 * M},
        league_rules=_rules(sale_limit=10, max_squad=25),
        now=NOW,
        **_open_window(),
    )
    bids = [m for m in plan["moves"] if m["kind"] == "bid"]
    _assert(len(bids) == 1, bids)
    skipped = [s for s in plan["flip_skipped"] if s.get("kind") == "bid"]
    _assert(any("cupo de reventa lleno (5)" in (s.get("why") or "") for s in skipped), skipped)


def test_accept_frees_one_bid_slot() -> None:
    ids = [str(i) for i in range(1, 6)]
    book = _owned_book(ids)
    squad = [_player(pid, name=f"R{pid}", vm=5 * M, ask=5 * M) for pid in ids]
    squad[0]["market_value"] = 6.5 * M
    squad[0]["price"] = 6.5 * M
    offer = {
        "player_id": "1",
        "name": "R1",
        "amount": 6_710_000,
        "id_bid": "91",
        "id_market": 91,
        "id_uc": 0,
    }
    plan, _book = build_flip_cycle(
        book=book,
        enabled=True,
        squad=squad,
        market=[_rising("21", "Soler")],
        sales_state={"pending_offers": [offer]},
        me={"balance": 40 * M},
        league_rules=_rules(sale_limit=10, max_squad=25),
        now=NOW,
        **_open_window(),
    )
    kinds = {(m["player_id"], m["kind"]) for m in plan["moves"]}
    _assert(("1", "accept_offer") in kinds, kinds)
    bids = [m for m in plan["moves"] if m["kind"] == "bid"]
    _assert(len(bids) == 1 and bids[0]["player_id"] == "21", bids)


def test_kickoff_blocks_a_bid_the_cycle_cannot_cover() -> None:
    """El saldo negativo al pitido no frena la puja. La frena el techo de deuda.

    El reloj deja margen de sobra al poder vender, así el descarte que queda es la deuda.
    """
    book = _owned_book(["9"])
    squad = [_player("9", name="R9", vm=8 * M, ask=8 * M)]
    market = [_rising("21", "Soler")]
    me = {"balance": 1 * M, "max_debt": 20 * M}
    rules = _rules(sale_limit=10, max_squad=25)
    clock = _open_window()
    allowed, _book = build_flip_cycle(
        book=book,
        enabled=True,
        squad=squad,
        market=market,
        sales_state={},
        me=me,
        league_rules=rules,
        now=NOW,
        **clock,
    )
    bids = [m for m in allowed["moves"] if m["kind"] == "bid"]
    _assert(len(bids) == 1 and bids[0]["player_id"] == "21", bids)
    reasons = " ".join(s.get("why") or "" for s in allowed["flip_skipped"])
    _assert("negativo al inicio de la jornada" not in reasons, allowed["flip_skipped"])

    tight, _book = build_flip_cycle(
        book=book,
        enabled=True,
        squad=squad,
        market=market,
        sales_state={},
        me={"balance": 1 * M, "max_debt": 2 * M},
        league_rules=rules,
        now=NOW,
        **clock,
    )
    _assert(not any(m["kind"] == "bid" for m in tight["moves"]), tight["moves"])
    tight_reasons = " ".join(s.get("why") or "" for s in tight["flip_skipped"])
    _assert("sin caja" in tight_reasons, tight["flip_skipped"])

    # Cierre posterior al pitido: no da tiempo a vender con 4 ciclos de margen.
    later_close, _book = build_flip_cycle(
        book=book,
        enabled=True,
        squad=squad,
        market=market,
        sales_state={},
        me=me,
        league_rules=rules,
        hours_to_solvency_deadline=3,
        cash_lag_hours=4,
        cycle_hours=12,
        now=NOW,
    )
    later_bids = [m for m in later_close["moves"] if m["kind"] == "bid"]
    _assert(not later_bids, later_bids)
    later_why = " ".join(s.get("why") or "" for s in later_close["flip_skipped"])
    _assert("primer partido" in later_why, later_close["flip_skipped"])

    opened, _book = build_flip_cycle(
        book=book,
        enabled=True,
        squad=squad,
        market=market,
        sales_state={},
        me=me,
        league_rules=rules,
        hours_to_solvency_deadline=7 + 4 * 12,
        cash_lag_hours=7,
        cycle_hours=12,
        now=NOW,
    )
    bids = [m for m in opened["moves"] if m["kind"] == "bid"]
    _assert(len(bids) == 1 and bids[0]["player_id"] == "21", bids)


def test_kickoff_uses_cash_already_in_hand() -> None:
    """Con caja y margen al poder vender, la puja sale. Más cerca, no."""
    plan, _book = build_flip_cycle(
        book=fresh_book(),
        enabled=True,
        squad=[],
        market=[_rising("21", "Soler")],
        sales_state={},
        me={"balance": 10 * M, "max_debt": 20 * M},
        league_rules=_rules(sale_limit=10, max_squad=25),
        now=NOW,
        **_open_window(),
    )
    bids = [m for m in plan["moves"] if m["kind"] == "bid"]
    _assert(len(bids) == 1 and bids[0]["player_id"] == "21", bids)
    tight, _book = build_flip_cycle(
        book=fresh_book(),
        enabled=True,
        squad=[],
        market=[_rising("21", "Soler")],
        sales_state={},
        me={"balance": 10 * M, "max_debt": 20 * M},
        league_rules=_rules(sale_limit=10, max_squad=25),
        hours_to_solvency_deadline=3,
        cash_lag_hours=4,
        cycle_hours=8,
        now=NOW,
    )
    _assert(not any(m["kind"] == "bid" for m in tight["moves"]), tight["moves"])


def test_kickoff_accepts_a_short_offer_to_cover_the_whistle() -> None:
    book = _owned_book(["9"])
    squad = [_player("9", name="R9", vm=6.5 * M, ask=6.5 * M, on_sale=True)]
    offer = {
        "player_id": "9",
        "name": "R9",
        "amount": 4_000_000,
        "id_bid": "91",
        "id_market": 91,
        "id_uc": 0,
    }
    plan, _book = build_flip_cycle(
        book=book,
        enabled=True,
        squad=squad,
        market=[_rising("21", "Soler")],
        sales_state={"pending_offers": [offer]},
        me={"balance": -1 * M, "max_debt": 20 * M},
        league_rules=_rules(sale_limit=10, max_squad=25),
        hours_to_solvency_deadline=3,
        cash_lag_hours=4,
        cycle_hours=8,
        now=NOW,
    )
    kinds = {(m["player_id"], m["kind"]) for m in plan["moves"]}
    _assert(("9", "accept_offer") in kinds, kinds)
    _assert(("9", "renew_offer") not in kinds, kinds)
    # El pitido está a 3 h y el cierre a 4 h: no hay margen para vender.
    bids = [m for m in plan["moves"] if m["kind"] == "bid"]
    _assert(not bids, bids)
    accept = next(m for m in plan["moves"] if m["kind"] == "accept_offer")
    _assert("pitido" in (accept.get("why") or ""), accept)


def test_wait_24h_does_not_fund_a_bid_before_it_can_be_sold() -> None:
    """La espera de 24 h no aporta caja y, si se come el margen de 4 ciclos, no se puja."""
    common = dict(
        book=fresh_book(),
        enabled=True,
        squad=[],
        market=[_rising("21", "Soler")],
        sales_state={},
        me={"balance": 1 * M, "max_debt": 20 * M},
        league_rules=_rules(sale_limit=10, max_squad=25),
        cash_lag_hours=3,
        cycle_hours=8,
        transfer_wait_hours=24,
        now=NOW,
    )
    early, _book = build_flip_cycle(hours_to_solvency_deadline=10, **common)
    early_bids = [m for m in early["moves"] if m["kind"] == "bid"]
    _assert(not early_bids, early_bids)
    early_why = " ".join(s.get("why") or "" for s in early["flip_skipped"])
    _assert("primer partido" in early_why, early["flip_skipped"])
    later, _book = build_flip_cycle(hours_to_solvency_deadline=80, **common)
    bids = [m for m in later["moves"] if m["kind"] == "bid"]
    _assert(len(bids) == 1 and bids[0]["player_id"] == "21", bids)


def test_live_watch_occupies_a_slot_until_it_leaves_the_market() -> None:
    ids = [str(i) for i in range(1, 4)]
    book = _owned_book(ids)
    book["watches"] = {
        "21": {"player_id": "21", "name": "Soler", "buy_price": 6_015_000},
        "98": {"player_id": "98", "name": "Hernández", "buy_price": 6_500_000},
        "99": {"player_id": "99", "name": "Perdida", "buy_price": 6_015_000},
    }
    gone = _player("98", name="Hernández", vm=6 * M, ask=6 * M, prev=6 * M - 50_000)
    gone["on_daily_market"] = False
    gone["seller"] = "club"
    # 23 sube más, así que el margen es peor y se queda fuera de la plaza libre.
    crowded = _player("23", name="Caliente", vm=6 * M, ask=6 * M, prev=6 * M - 400_000)
    squad = [_player(pid, name=f"R{pid}", vm=5 * M, ask=5 * M) for pid in ids]
    plan, book = build_flip_cycle(
        book=book,
        enabled=True,
        squad=squad,
        market=[_rising("21", "Soler"), _rising("22", "Otro"), crowded, gone],
        sales_state={},
        me={"balance": 40 * M},
        league_rules=_rules(sale_limit=10, max_squad=25),
        now=NOW,
        **_open_window(),
    )
    bids = {m["player_id"] for m in plan["moves"] if m["kind"] == "bid"}
    # Soler ya estaba vigilado: se vuelve a pujar. La plaza libre es para Otro.
    _assert(bids == {"21", "22"}, bids)
    _assert("21" in book["watches"], book["watches"])
    _assert("98" not in book["watches"] and "99" not in book["watches"], book["watches"])


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
        stale = fresh_book()
        stale["active"] = True
        stale["frozen_ids"] = ["1"]
        stale["positions"] = {
            "9": {"player_id": "9", "name": "Flip", "buy_price": 6_404_000, "status": "owned"}
        }
        stale["watches"] = {"21": {"player_id": "21", "buy_price": 6_015_000}}
        save_book("laliga", stale, root=root)
        _assert(apply_flip_toggle("laliga", '"false"', root=root) is True, "false quoted")
        _assert(flip_enabled("laliga", root) is False, "off")
        _assert(load_book("laliga", root) == fresh_book(), load_book("laliga", root))
        _assert(apply_flip_toggle("all", "true", root=root) is False, "all")
        _assert(apply_flip_toggle("laliga", "maybe", root=root) is False, "garbage")


VM_WILLIAMS = 6_881_000


def _listed_offer(
    *,
    amount: float,
    balance: float,
    hours: float | None,
    cycle: float,
    enabled: bool = True,
    buy: float | None = 6_861_400,
):
    """Jugador de reventa ya listado. El valor cuenta para el pitido (lag 1 h)."""
    book = fresh_book()
    book["active"] = True
    book["positions"] = {
        "9": {
            "player_id": "9",
            "name": "Williams",
            "buy_price": buy,
            "status": "listed",
            "listed": True,
        }
    }
    squad = [_player("9", name="Williams", vm=VM_WILLIAMS, ask=VM_WILLIAMS, on_sale=True)]
    offer = {
        "player_id": "9",
        "name": "Williams",
        "amount": amount,
        "market_value": VM_WILLIAMS,
        "id_bid": "91",
        "id_market": 91,
        "id_uc": 0,
    }
    kwargs = dict(
        book=book,
        enabled=enabled,
        squad=squad,
        market=[],
        sales_state={"pending_offers": [offer]},
        me={"balance": balance},
        league_rules=_rules(sale_limit=10, max_squad=25),
        cash_lag_hours=1,
        cycle_hours=cycle,
        now=NOW,
    )
    if hours is not None:
        kwargs["hours_to_solvency_deadline"] = hours
    return build_flip_cycle(**kwargs)


def _move_kinds(plan: dict) -> set[tuple[str, str]]:
    return {(m["player_id"], m["kind"]) for m in plan["moves"]}


def test_late_window_accepts_from_one_percent() -> None:
    """Patio a 59 h (4 ciclos de 12 h) y en negativo: el +1,7% se acepta."""
    offer = 7_000_000
    plan, _book = _listed_offer(amount=offer, balance=-4_417_626, hours=59, cycle=12)
    _assert(("9", "accept_offer") in _move_kinds(plan), plan["moves"])
    _assert(("9", "renew_offer") not in _move_kinds(plan), plan["moves"])
    accept = next(m for m in plan["moves"] if m["kind"] == "accept_offer")
    _assert(accept["threshold"] == round(VM_WILLIAMS * 1.01), accept)
    _assert("+1%" in (accept.get("why") or ""), accept)


def test_late_window_stays_at_three_percent_outside() -> None:
    """60 h son 5 ciclos de 12 h: aunque el saldo sea negativo, sigue el +3%."""
    plan, _book = _listed_offer(amount=7_000_000, balance=-4_417_626, hours=60, cycle=12)
    _assert(("9", "renew_offer") in _move_kinds(plan), plan["moves"])
    _assert(("9", "accept_offer") not in _move_kinds(plan), plan["moves"])
    renew = next(m for m in plan["moves"] if m["kind"] == "renew_offer")
    _assert(renew["threshold"] == round(VM_WILLIAMS * 1.03), renew)
    _assert("+3%" in (renew.get("why") or ""), renew)


def test_late_window_exact_one_percent_on_both_cycle_lengths() -> None:
    exact = round(VM_WILLIAMS * 1.01)
    for hours, cycle in ((48, 12), (32, 8)):
        plan, _book = _listed_offer(amount=exact, balance=-1_000_000, hours=hours, cycle=cycle)
        _assert(("9", "accept_offer") in _move_kinds(plan), (hours, cycle, plan["moves"]))
        under, _book = _listed_offer(
            amount=exact - 1, balance=-1_000_000, hours=hours, cycle=cycle
        )
        _assert(("9", "renew_offer") in _move_kinds(under), (hours, cycle, under["moves"]))
        _assert(("9", "accept_offer") not in _move_kinds(under), under["moves"])


def test_open_window_keeps_three_percent_unless_balance_is_negative() -> None:
    """Serie A y Premier: 4,5 h y ciclo de 8 h son 0 ciclos."""
    between = 7_000_000
    positive, _book = _listed_offer(amount=between, balance=3_423_517, hours=4.5, cycle=8)
    _assert(("9", "renew_offer") in _move_kinds(positive), positive["moves"])
    zero, _book = _listed_offer(amount=between, balance=0, hours=4.5, cycle=8)
    _assert(("9", "renew_offer") in _move_kinds(zero), zero["moves"])
    negative, _book = _listed_offer(amount=between, balance=-1_000_000, hours=4.5, cycle=8)
    _assert(("9", "accept_offer") in _move_kinds(negative), negative["moves"])
    accept = next(m for m in negative["moves"] if m["kind"] == "accept_offer")
    _assert("+1%" in (accept.get("why") or ""), accept)


def test_missing_kickoff_clock_keeps_three_percent() -> None:
    plan, _book = _listed_offer(amount=7_000_000, balance=-4_417_626, hours=None, cycle=12)
    _assert(("9", "renew_offer") in _move_kinds(plan), plan["moves"])
    renew = next(m for m in plan["moves"] if m["kind"] == "renew_offer")
    _assert(renew["threshold"] == round(VM_WILLIAMS * 1.03), renew)


def test_missing_buy_price_is_a_manual_signing_inside_the_window() -> None:
    """Sin precio de compra no es reventa: ni el +1% del pitido la vende."""
    plan, book = _listed_offer(
        amount=7_000_000,
        balance=-1_000_000,
        hours=4.5,
        cycle=8,
        buy=None,
    )
    _assert(("9", "accept_offer") not in _move_kinds(plan), plan["moves"])
    _assert(("9", "renew_offer") not in _move_kinds(plan), plan["moves"])
    _assert(("9", "unlist") in _move_kinds(plan), plan["moves"])
    _assert("9" in book["frozen_ids"] and "9" not in book["positions"], book)


def test_roster_and_manual_release() -> None:
    """La tanda visible se puede sacar: queda congelada y deja de venderse o pujarse."""
    from flip_mode import apply_flip_release, release_from_book

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
        },
        "8": {
            "player_id": "8",
            "name": "Vendido",
            "buy_price": 1_000_000,
            "status": "sold",
        },
    }
    book["watches"] = {"21": {"player_id": "21", "name": "Soler", "buy_price": 6_015_000}}
    squad = [
        _player("1", name="Titular", vm=9 * M, ask=9 * M),
        _player("9", name="Flip", vm=6.5 * M, ask=6.5 * M, on_sale=True),
    ]
    market = [_player("21", name="Soler", vm=6 * M, ask=6 * M, prev=6 * M - 50_000)]
    plan, book = build_flip_cycle(
        book=book,
        enabled=True,
        squad=squad,
        market=market,
        sales_state={},
        me={"balance": 20 * M},
        league_rules=_rules(),
        now=NOW,
    )
    roster = {row["player_id"]: row for row in plan["flip"]["players"]}
    _assert(set(roster) == {"9", "21"}, roster)
    _assert(roster["9"]["status"] == "listed" and roster["9"]["buy_price"] == 6_404_000, roster["9"])
    _assert(roster["21"]["status"] == "bid", roster["21"])

    released = release_from_book(book, ["9", "21"])
    _assert("9" not in released["positions"] and "8" in released["positions"], released["positions"])
    _assert("21" not in released["watches"], released["watches"])
    _assert("9" in released["frozen_ids"] and "21" in released["frozen_ids"], released["frozen_ids"])
    _assert("9" in released["manual_ids"], released["manual_ids"])

    plan, book = build_flip_cycle(
        book=released,
        enabled=True,
        squad=squad,
        market=market,
        sales_state={},
        me={"balance": 20 * M},
        league_rules=_rules(),
        now=NOW + timedelta(hours=1),
    )
    kinds = {(m["player_id"], m["kind"]) for m in plan["moves"]}
    _assert(("9", "unlist") in kinds, plan["moves"])
    _assert(("9", "list_for_sale") not in kinds and ("9", "accept_offer") not in kinds, kinds)
    _assert(not any(m["kind"] == "bid" and m["player_id"] == "21" for m in plan["moves"]), plan["moves"])
    left = {row["player_id"] for row in plan["flip"]["players"]}
    _assert("9" not in left and "21" not in left, plan["flip"]["players"])

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        save_book("laliga", released, root=root)
        _assert(apply_flip_release("laliga", ["no id"], root=root) is False, "id raro")
        _assert(apply_flip_release("laliga", ["21"], root=root) is True, "release")
        loaded = load_book("laliga", root)
        _assert("21" in loaded["frozen_ids"] and "21" not in loaded["watches"], loaded)


def test_late_floor_does_not_sell_after_flip_is_off() -> None:
    """Apagada, la oferta del pitido ya no es una salida de reventa."""
    plan, book = _listed_offer(
        amount=7_000_000,
        balance=-1_000_000,
        hours=59,
        cycle=12,
        enabled=False,
    )
    _assert(plan["moves"] == [], plan["moves"])
    _assert(book == fresh_book(), book)
    _assert("Reventa apagada." in (plan.get("narrative") or ""), plan.get("narrative"))


def main() -> None:
    test_daily_change_is_euros()
    test_bid_premium_and_margin()
    test_cash_keeps_the_wider_margin()
    test_snapshot_is_not_retaken()
    test_watched_arrival_is_listed()
    test_manual_signing_already_listed_is_withdrawn()
    test_offer_threshold_and_frozen_untouched()
    test_fallen_market_accepts_below_buy_price()
    test_konate_accepts_and_the_other_two_renew()
    test_disable_clears_the_book_and_refreeze_includes_old_signings()
    test_bid_needs_four_cycles_before_the_first_whistle()
    test_five_open_positions_block_new_bids()
    test_four_open_positions_allow_one_bid()
    test_accept_frees_one_bid_slot()
    test_live_watch_occupies_a_slot_until_it_leaves_the_market()
    test_kickoff_blocks_a_bid_the_cycle_cannot_cover()
    test_kickoff_uses_cash_already_in_hand()
    test_kickoff_accepts_a_short_offer_to_cover_the_whistle()
    test_late_window_accepts_from_one_percent()
    test_late_window_stays_at_three_percent_outside()
    test_late_window_exact_one_percent_on_both_cycle_lengths()
    test_open_window_keeps_three_percent_unless_balance_is_negative()
    test_missing_kickoff_clock_keeps_three_percent()
    test_missing_buy_price_is_a_manual_signing_inside_the_window()
    test_roster_and_manual_release()
    test_late_floor_does_not_sell_after_flip_is_off()
    test_wait_24h_does_not_fund_a_bid_before_it_can_be_sold()
    test_full_sale_limit_blocks_new_bids()
    test_book_roundtrip()
    test_behavior_win_and_profit()
    test_flip_play_ignores_xi_and_percent_caps()
    test_flip_skips_without_positive_revaluation()
    test_apply_flip_toggle_accepts_quoted_json()
    print("flip mode ok")


if __name__ == "__main__":
    main()
