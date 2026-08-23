"""Tests unitarios: sales_state, solvencia siguiente jornada, ofertas."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from competitive_actions import (  # noqa: E402
    build_offer_actions,
    evaluate_bid_finance,
    resolve_solvency_deadline,
    sells_settle_before_deadline,
)
from sales_state import (  # noqa: E402
    build_sales_state,
    parse_offers_received,
    tag_own_market_listings,
)


def test_parse_offers_empty():
    out = parse_offers_received({"status": "ok", "data": {"count": {"total": 0, "pending": 0}}})
    assert out["pending_offers"] == []
    assert out["count"]["pending"] == 0


def test_parse_offers_machine():
    raw = {
        "status": "ok",
        "data": {
            "count": {"total": 1, "pending": 1},
            "offers": {
                "18004": {
                    "id": 18004,
                    "name": "Ante Budimir",
                    "position": 4,
                    "value": 16_000_000,
                    "price": 16_000_000,
                    "bid": 16_500_000,
                    "id_bid": 99,
                    "bid_status": "pending",
                    "uname": "Mister",
                    "id_user": 0,
                    "owner": 1,
                }
            },
        },
    }
    out = parse_offers_received(raw)
    assert len(out["pending_offers"]) == 1
    o = out["pending_offers"][0]
    assert o["from_machine"] is True
    assert o["player_id"] == "18004"
    assert o["amount"] == 16_500_000


def test_tag_own_listings():
    market = [
        {"id": "1", "owner_id": "100", "name": "A"},
        {"id": "2", "owner_id": "999", "name": "B"},
        {"id": "3", "owner_id": None, "name": "C"},
    ]
    tagged = tag_own_market_listings(market, "100")
    assert tagged[0]["listed_by_me"] is True
    assert tagged[0]["listed_by_rival"] is False
    assert tagged[1]["listed_by_rival"] is True
    assert tagged[2].get("listed_by_me") is False


def test_sales_state_merges_offers_as_listed():
    state = build_sales_state(
        market=[],
        offers_payload=parse_offers_received(
            {
                "data": {
                    "count": {"total": 1, "pending": 1},
                    "offers": {
                        "5": {
                            "id": 5,
                            "name": "X",
                            "value": 1_000_000,
                            "bid": 1_000_000,
                            "bid_status": "pending",
                            "uname": "Mister",
                            "id_user": 0,
                        }
                    },
                }
            }
        ),
        squad=[{"id": "5", "name": "X"}],
    )
    assert "5" in state["listed_ids"]
    assert state["pending_count"] == 1
    assert state["listed"][0].get("has_pending_offer") is True


def test_solvency_deadline_next_gw_when_ongoing():
    md = {
        "gameweek_status": "ongoing",
        "jornada": 2,
        "seconds_to_start": -1000,
        "first_match": "2026-08-20T21:00:00",
        "season_schedule": [
            {"jornada": 2, "status": "ongoing", "first_match": "2026-08-20T21:00:00"},
            {"jornada": 3, "status": "unstarted", "first_match": "2026-08-28T19:00:00"},
        ],
    }
    d = resolve_solvency_deadline(hours_to_jornada=6.0, matchday=md)
    assert d["current_jornada_started"] is True
    assert d["solvency_target"] == "siguiente"
    assert d["hours_to_solvency_deadline"] is not None
    assert d["hours_to_solvency_deadline"] > 48


def test_debt_allowed_with_coverage_in_strict_window():
    # Sin cobertura → bloqueado cerca del deadline
    fin_block = evaluate_bid_finance(
        5_000_000,
        1_000_000,
        max_debt=10_000_000,
        hours_to_jornada=24,
        matchday={"gameweek_status": "unstarted", "first_match": None},
        liquidity_coverage=0,
    )
    assert fin_block["debt_risk"] is True
    assert fin_block["solvency_blocked"] is True

    # Con cobertura de ofertas/listados → stretch, no blocked
    fin_ok = evaluate_bid_finance(
        5_000_000,
        1_000_000,
        max_debt=10_000_000,
        hours_to_jornada=24,
        matchday={"gameweek_status": "unstarted", "first_match": None},
        liquidity_coverage=5_000_000,
    )
    assert fin_ok["debt_risk"] is True
    assert fin_ok["solvency_blocked"] is False
    assert fin_ok["budget_fit"] in ("stretch", "tight", "comfortable")


def test_settle_before_deadline():
    assert sells_settle_before_deadline(hours_to_deadline=100, cash_lag_hours=48) is True
    assert sells_settle_before_deadline(hours_to_deadline=40, cash_lag_hours=48) is False


def test_offer_actions_accept_when_need_liquidity():
    actions = build_offer_actions(
        {
            "pending_offers": [
                {
                    "player_id": "1",
                    "name": "A",
                    "amount": 1_000_000,
                    "market_value": 1_200_000,
                    "pct_of_vm": 0.83,
                    "from_machine": True,
                    "from_name": "Mister",
                    "status": "pending",
                }
            ],
            "mister_offers_url": "https://example.com",
        },
        need_liquidity=True,
        cash_lag_hours=48,
        hours_to_solvency_deadline=120,
        solvency_target="siguiente",
    )
    assert actions and actions[0]["action"] == "accept_offer"


def main():
    tests = [
        test_parse_offers_empty,
        test_parse_offers_machine,
        test_tag_own_listings,
        test_sales_state_merges_offers_as_listed,
        test_solvency_deadline_next_gw_when_ongoing,
        test_debt_allowed_with_coverage_in_strict_window,
        test_settle_before_deadline,
        test_offer_actions_accept_when_need_liquidity,
    ]
    for t in tests:
        t()
        print("OK", t.__name__)
    print(f"\n{len(tests)} tests OK")


if __name__ == "__main__":
    main()
