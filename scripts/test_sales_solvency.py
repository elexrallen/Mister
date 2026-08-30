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
from daily_playbook import build_daily_playbook  # noqa: E402
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
    # 5 M de coste / 1 M de caja / 10 M de maxDebt: legal aunque quede negativo
    fin_block = evaluate_bid_finance(
        5_000_000,
        1_000_000,
        max_debt=10_000_000,
        hours_to_jornada=24,
        matchday={"gameweek_status": "unstarted", "first_match": None},
        liquidity_coverage=0,
    )
    assert fin_block["debt_risk"] is True
    assert fin_block["solvency_blocked"] is False
    assert fin_block["solvency_ok"] is True
    assert fin_block["budget_fit"] == "stretch"

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
    assert fin_ok["budget_fit"] == "stretch"


def test_cost_over_max_debt_is_blocked():
    fin = evaluate_bid_finance(
        12_000_000,
        1_000_000,
        max_debt=10_000_000,
        hours_to_jornada=24,
    )
    assert fin["budget_fit"] == "blocked"
    assert fin["solvency_blocked"] is True
    assert fin["solvency_ok"] is False


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
        balance=-800_000,
        cash_needed=800_000,
        cash_lag_hours=48,
        hours_to_solvency_deadline=120,
        solvency_target="siguiente",
    )
    assert actions and actions[0]["action"] == "accept_offer"
    assert actions[0]["offer_needed"] is True
    assert "opcional" not in (actions[0].get("why") or "").lower()
    assert "necesitas esta venta" in (actions[0].get("why") or "").lower()


def test_offer_actions_only_needed_cover_debt():
    actions = build_offer_actions(
        {
            "pending_offers": [
                {
                    "player_id": "18004",
                    "name": "Ante Budimir",
                    "amount": 16_582_180,
                    "market_value": 16_356_000,
                    "pct_of_vm": 1.0138,
                    "from_machine": True,
                    "from_name": "Mister",
                    "status": "pending",
                },
                {
                    "player_id": "56924",
                    "name": "Etta Eyong",
                    "amount": 3_963_750,
                    "market_value": 3_887_000,
                    "pct_of_vm": 1.0197,
                    "from_machine": True,
                    "from_name": "Mister",
                    "status": "pending",
                },
                {
                    "player_id": "29403",
                    "name": "Pathé Ciss",
                    "amount": 4_481_530,
                    "market_value": 4_456_000,
                    "pct_of_vm": 1.0057,
                    "from_machine": True,
                    "from_name": "Mister",
                    "status": "pending",
                },
            ]
        },
        balance=-15_864_844,
        cash_needed=15_864_844,
        cash_lag_hours=48,
        hours_to_solvency_deadline=200,
        solvency_target="siguiente",
        squad=[
            {"id": "18004", "name": "Ante Budimir", "in_lineup": False, "xpts": 5.7},
            {"id": "56924", "name": "Etta Eyong", "in_lineup": False, "xpts": 2.0},
            {"id": "29403", "name": "Pathé Ciss", "in_lineup": True, "xpts": 3.5},
        ],
    )
    by_id = {a["player_id"]: a for a in actions}
    assert by_id["18004"]["action"] == "accept_offer"
    assert by_id["18004"]["offer_needed"] is True
    assert "opcional" not in (by_id["18004"]["why"] or "").lower()
    assert by_id["56924"]["action"] == "decline_offer"
    assert by_id["56924"]["offer_needed"] is False
    assert "no hace falta" in (by_id["56924"]["why"] or "").lower()
    assert by_id["29403"]["action"] == "decline_offer"
    assert by_id["29403"]["offer_needed"] is False
    assert "once" in (by_id["29403"]["why"] or "").lower()


def test_offer_actions_positive_balance_accepts_fair_listed_sale():
    """Listar y luego rechazar una oferta justa de la máquina es un bucle: Mister te saca del mercado."""
    actions = build_offer_actions(
        {
            "pending_offers": [
                {
                    "player_id": "2",
                    "name": "B",
                    "amount": 5_000_000,
                    "market_value": 4_900_000,
                    "pct_of_vm": 1.02,
                    "from_machine": True,
                    "from_name": "Mister",
                    "status": "pending",
                }
            ]
        },
        balance=1_000_000,
        cash_needed=0,
    )
    assert actions and actions[0]["action"] == "accept_offer"
    assert actions[0]["offer_needed"] is True
    assert actions[0]["offer_need"] == "complete_listing"
    assert "opcional" not in (actions[0].get("why") or "").lower()
    assert "cierra la venta" in (actions[0].get("why") or "").lower()
    assert "saca del mercado" in (actions[0].get("why") or "").lower()


def test_offer_actions_declines_keeper_when_cash_ok():
    actions = build_offer_actions(
        {
            "pending_offers": [
                {
                    "player_id": "star",
                    "name": "Titular",
                    "amount": 8_000_000,
                    "market_value": 8_000_000,
                    "pct_of_vm": 1.0,
                    "from_machine": True,
                    "from_name": "Mister",
                    "status": "pending",
                }
            ]
        },
        balance=1_000_000,
        cash_needed=0,
        squad=[
            {
                "id": "star",
                "name": "Titular",
                "in_lineup": True,
                "gw_starter": True,
                "lineup_prob": 0.9,
                "xpts": 8,
            }
        ],
    )
    assert actions and actions[0]["action"] == "decline_offer"
    assert actions[0]["offer_needed"] is False
    why = (actions[0].get("why") or "").lower()
    assert "sigue en venta" not in why
    assert "titular" in why


def test_offer_actions_declines_poor_offer_when_cash_ok():
    actions = build_offer_actions(
        {
            "pending_offers": [
                {
                    "player_id": "2",
                    "name": "B",
                    "amount": 3_000_000,
                    "market_value": 5_000_000,
                    "pct_of_vm": 0.6,
                    "from_machine": True,
                    "from_name": "Mister",
                    "status": "pending",
                }
            ]
        },
        balance=1_000_000,
        cash_needed=0,
    )
    assert actions and actions[0]["action"] == "decline_offer"
    assert "oferta floja" in (actions[0].get("why") or "").lower()


def test_premier_listed_offers_are_not_a_reject_relist_loop():
    """Premier: 5 listados, saldo positivo, ofertas ~VM → aceptar, no rechazar."""
    pending = [
        ("1859", "Solanke", 2_239_730, 2_309_000, 0.97),
        ("11145", "Nmecha", 1_951_320, 1_932_000, 1.01),
        ("65186", "Mukiele", 5_630_560, 5_414_000, 1.04),
        ("22739", "Struijk", 1_480_320, 1_542_000, 0.96),
        ("23381", "Ait Nouri", 2_706_660, 2_734_000, 0.99),
    ]
    actions = build_offer_actions(
        {
            "pending_offers": [
                {
                    "player_id": pid,
                    "name": name,
                    "amount": amount,
                    "market_value": vm,
                    "pct_of_vm": pct,
                    "from_machine": True,
                    "from_name": "Mister",
                    "status": "pending",
                }
                for pid, name, amount, vm, pct in pending
            ]
        },
        balance=307_413,
        cash_needed=0,
        squad=[
            {"id": pid, "name": name, "in_lineup": pid in ("1859", "11145", "65186", "23381"), "lineup_prob": 0.18, "xpts": 1.0}
            for pid, name, *_ in pending
        ],
    )
    assert len(actions) == 5
    assert all(a["action"] == "accept_offer" for a in actions)
    assert all(a["offer_need"] == "complete_listing" for a in actions)


def test_already_listed_skips_players_with_pending_offer():
    from competitive_actions import build_sell_opportunities

    squad = [
        {"id": "1", "name": "Listado", "position": "MF", "price": 3_000_000, "on_sale": True},
        {"id": "2", "name": "Esperando", "position": "MF", "price": 3_000_000, "on_sale": True},
        {"id": "xi", "name": "Titular", "position": "MF", "price": 5_000_000, "lineup_prob": 0.9},
    ]
    sells = build_sell_opportunities(
        {"squad": squad, "balance": 1_000_000, "rank": 8},
        {"alerts": [], "by_position": {}},
        [],
        recommended_xi={"xi": [{"player_id": "xi"}]},
        league_economy={"sale_limit": 5},
        sales_state={
            "listed_ids": ["1", "2"],
            "pending_offers": [{"player_id": "1", "status": "pending"}],
        },
    )
    listed_wait = [
        s for s in sells if s.get("action") == "sell" and s.get("queue_role") == "already_listed"
    ]
    ids = {s["player_id"] for s in listed_wait}
    assert "1" not in ids
    assert "2" in ids


def test_offer_actions_prefers_bench_to_cover_slot():
    actions = build_offer_actions(
        {
            "pending_offers": [
                {
                    "player_id": "xi",
                    "name": "Titular",
                    "amount": 8_000_000,
                    "market_value": 8_000_000,
                    "pct_of_vm": 1.0,
                    "from_machine": True,
                    "from_name": "Mister",
                    "status": "pending",
                },
                {
                    "player_id": "bn",
                    "name": "Banquillo",
                    "amount": 3_000_000,
                    "market_value": 3_000_000,
                    "pct_of_vm": 1.0,
                    "from_machine": True,
                    "from_name": "Mister",
                    "status": "pending",
                },
            ]
        },
        balance=2_000_000,
        cash_needed=0,
        slots_needed=1,
        squad=[
            {"id": "xi", "in_lineup": True, "xpts": 8},
            {"id": "bn", "in_lineup": False, "xpts": 1},
        ],
    )
    by_id = {a["player_id"]: a for a in actions}
    assert by_id["bn"]["action"] == "accept_offer"
    assert by_id["bn"]["offer_needed"] is True
    assert by_id["xi"]["action"] == "decline_offer"
    assert by_id["xi"]["offer_needed"] is False


def test_offer_actions_keeps_prior_scorer_when_listed_cover():
    """0 pts esta temporada no manda: conserva al titular de 5.4 Mixto y cubre con peores listados."""
    actions = build_offer_actions(
        {
            "pending_offers": [
                {
                    "player_id": "18004",
                    "name": "Ante Budimir",
                    "amount": 16_582_180,
                    "market_value": 16_356_000,
                    "pct_of_vm": 1.0138,
                    "from_machine": True,
                    "from_name": "Mister",
                    "status": "pending",
                },
                {
                    "player_id": "56924",
                    "name": "Etta Eyong",
                    "amount": 3_963_750,
                    "market_value": 3_887_000,
                    "pct_of_vm": 1.0197,
                    "from_machine": True,
                    "from_name": "Mister",
                    "status": "pending",
                },
                {
                    "player_id": "29403",
                    "name": "Pathé Ciss",
                    "amount": 4_481_530,
                    "market_value": 4_456_000,
                    "pct_of_vm": 1.0057,
                    "from_machine": True,
                    "from_name": "Mister",
                    "status": "pending",
                },
            ],
            "listed": [
                {"player_id": "340", "name": "Isco", "price": 11_740_000},
                {"player_id": "70917", "name": "A. Sangante", "price": 2_059_000},
            ],
        },
        balance=-15_864_844,
        cash_needed=15_864_844,
        cash_lag_hours=48,
        hours_to_solvency_deadline=126,
        solvency_target="siguiente",
        squad=[
            {
                "id": "18004",
                "name": "Ante Budimir",
                "in_lineup": False,
                "xpts": 5.68,
                "gw_starter": True,
                "lineup_prob": 0.9,
                "points": 0,
                "ff_apps": 0,
                "ff_prior_avg": 5.41,
                "ff_prior_apps": 37,
            },
            {
                "id": "56924",
                "name": "Etta Eyong",
                "in_lineup": False,
                "xpts": 0.0,
                "gw_starter": False,
                "lineup_prob": 0.4,
                "points": 0,
                "ff_apps": 1,
                "ff_mister_avg": 0.0,
                "ff_prior_avg": 3.58,
                "ff_prior_apps": 33,
            },
            {
                "id": "29403",
                "name": "Pathé Ciss",
                "in_lineup": True,
                "xpts": 2.4,
                "gw_starter": True,
                "lineup_prob": 0.8,
                "ff_apps": 2,
                "ff_prior_avg": 3.69,
                "ff_prior_apps": 29,
            },
            {
                "id": "340",
                "name": "Isco",
                "in_lineup": True,
                "xpts": 0.48,
                "lineup_prob": 0.5,
                "ff_apps": 1,
                "ff_prior_avg": 4.13,
                "ff_prior_apps": 8,
            },
            {
                "id": "70917",
                "name": "A. Sangante",
                "in_lineup": True,
                "xpts": 1.2,
                "lineup_prob": 0.6,
                "injury": True,
            },
        ],
    )
    by_id = {a["player_id"]: a for a in actions}
    assert by_id["18004"]["action"] == "decline_offer"
    assert by_id["18004"]["offer_needed"] is False
    why_b = (by_id["18004"]["why"] or "").lower()
    assert "opcional" not in why_b
    assert "temp. pasada" in why_b
    assert by_id["56924"]["action"] == "accept_offer"
    assert by_id["56924"]["offer_needed"] is True
    assert by_id["29403"]["offer_needed"] is False


def test_playbook_splits_needed_and_surplus_offers():
    pb = build_daily_playbook(
        hours_to_jornada=3,
        matchday={"gameweek_status": "ongoing", "jornada": 2},
        action_plan=[
            {
                "action": "accept_offer",
                "name": "Ante Budimir",
                "player_id": "18004",
                "offer_needed": True,
                "why": "Necesitas esta venta: el saldo es -15,864,844 € y esta oferta lo cubre.",
            },
            {
                "action": "decline_offer",
                "name": "Etta Eyong",
                "player_id": "56924",
                "offer_needed": False,
                "why": "No hace falta: con Ante Budimir ya cubres el negativo. Rechaza para no vender de más; el jugador sigue en venta.",
            },
        ],
        me={"balance": -15_864_844},
        diagnostico={"sales_state": {"pending_count": 2, "listed_count": 2}},
    )
    ids = {c["id"]: c for c in pb["checklist"]}
    assert "ofertas_necesarias" in ids
    assert "ofertas_no_necesarias" in ids
    assert "opcional" not in (ids["ofertas_necesarias"]["detail"] or "").lower()
    assert "opcional" not in (ids["ofertas_no_necesarias"]["detail"] or "").lower()
    assert "necesitas esta venta" in ids["ofertas_necesarias"]["detail"].lower()


def main():
    tests = [
        test_parse_offers_empty,
        test_parse_offers_machine,
        test_tag_own_listings,
        test_sales_state_merges_offers_as_listed,
        test_solvency_deadline_next_gw_when_ongoing,
        test_debt_allowed_with_coverage_in_strict_window,
        test_cost_over_max_debt_is_blocked,
        test_settle_before_deadline,
        test_offer_actions_accept_when_need_liquidity,
        test_offer_actions_only_needed_cover_debt,
        test_offer_actions_positive_balance_accepts_fair_listed_sale,
        test_offer_actions_declines_keeper_when_cash_ok,
        test_offer_actions_declines_poor_offer_when_cash_ok,
        test_premier_listed_offers_are_not_a_reject_relist_loop,
        test_already_listed_skips_players_with_pending_offer,
        test_offer_actions_prefers_bench_to_cover_slot,
        test_offer_actions_keeps_prior_scorer_when_listed_cover,
        test_playbook_splits_needed_and_surplus_offers,
    ]
    for t in tests:
        t()
        print("OK", t.__name__)
    print(f"\n{len(tests)} tests OK")


if __name__ == "__main__":
    main()
