"""Restos: parche no veta, crowds_out solo huecos reales, copy de ciclo."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from competitive_actions import (  # noqa: E402
    estimate_gap_funding,
    other_gaps_min_cost,
)
from data_engine import build_action_plan  # noqa: E402
from target_board import funding_plan_from_board  # noqa: E402


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _opp(
    pid: str,
    name: str,
    pos: str,
    price: float,
    *,
    fills: bool = True,
) -> dict:
    return {
        "id": pid,
        "name": name,
        "position": pos,
        "on_daily_market": True,
        "seller": "market",
        "price": price,
        "puja_recomendada": price,
        "puja_minima": price * 0.7,
        "fills_need": fills,
        "fills_structural": fills,
        "fills_coverage_gap": fills,
        "priority": "Alta",
        "priority_score": 70,
        "lineup_prob": 0.80,
        "budget_fit": "comfortable",
        "categories": ["chollo_economico"],
    }


def test_other_min_ignores_primary_shopping_list() -> None:
    funding = {
        "all_gap_costs": [
            {"position": "FW", "cost": 8_000_000, "need": "perfect_buy_daily"},
            {"position": "MF", "cost": 5_000_000, "need": "perfect_buy_daily"},
        ]
    }
    _assert(other_gaps_min_cost(funding, exclude_position="DF") == 0.0, "primaries no cuentan")
    _assert(
        other_gaps_min_cost(
            funding,
            exclude_position="DF",
            diagnosis={"by_position": {"DF": {"coverage": "ok"}, "FW": {"coverage": "ok"}}},
            opportunities=[],
        )
        == 0.0,
        "cobertura ok → 0",
    )


def test_other_min_uses_cheapest_on_market_for_thin_fw() -> None:
    cost = other_gaps_min_cost(
        {
            "all_gap_costs": [
                {"position": "FW", "cost": 8_000_000, "need": "perfect_buy_daily"},
            ]
        },
        exclude_position="DF",
        diagnosis={"by_position": {"FW": {"coverage": "thin"}}},
        structural_needs=[{"position": "FW", "priority": "Alta"}],
        opportunities=[
            {"position": "FW", "on_daily_market": True, "price": 1_000_000},
            {"position": "FW", "on_daily_market": True, "price": 8_000_000},
        ],
    )
    _assert(cost == 1_000_000, cost)


def test_other_min_zero_when_thin_has_no_listing() -> None:
    cost = other_gaps_min_cost(
        {"all_gap_costs": []},
        exclude_position="DF",
        diagnosis={"by_position": {"FW": {"coverage": "thin"}}},
        structural_needs=[{"position": "FW", "priority": "Alta"}],
        opportunities=[],
    )
    _assert(cost == 0.0, cost)
    cost_df_only = other_gaps_min_cost(
        {},
        exclude_position="DF",
        diagnosis={"by_position": {"FW": {"coverage": "thin"}, "DF": {"coverage": "thin"}}},
        opportunities=[{"position": "DF", "on_daily_market": True, "price": 2_000_000}],
    )
    _assert(cost_df_only == 0.0, cost_df_only)


def test_thin_fw_without_listing_does_not_block_df() -> None:
    me = {"balance": 3_000_000, "squad": [], "rank": 8}
    diagnosis = {
        "alerts": [],
        "by_position": {
            "DF": {"coverage": "thin"},
            "FW": {"coverage": "thin"},
        },
    }
    opps = [_opp("d1", "DF_A", "DF", 2_000_000)]
    plan, _pkg = build_action_plan(
        me,
        diagnosis,
        opps,
        [],
        target_board={
            "primary_targets": [],
            "patch_policy": {"allow": False, "max_spend": 2_500_000},
            "moves": {"buy": []},
            "cash_reserved": 0,
        },
        funding_info={
            "cash_reserved": 0,
            "primary_targets": [],
            "funding_target": 0,
            "gap_costs": [],
        },
        market_mode="fixed",
        diagnostico_plantilla={
            "structural_needs": [],
            "lineas": diagnosis["by_position"],
        },
    )
    buys = {a.get("name") for a in plan if a.get("action") == "buy_now"}
    _assert("DF_A" in buys, [(a.get("name"), a.get("action"), a.get("why")) for a in plan])
    _assert(
        not any("poca caja" in (a.get("why") or "") for a in plan if a.get("name") == "DF_A"),
        [a.get("why") for a in plan],
    )


def test_patch_policy_does_not_block_daily_buys() -> None:
    me = {"balance": 5_000_000, "squad": [], "rank": 8}
    diagnosis = {
        "alerts": [],
        "by_position": {
            "GK": {"coverage": "ok"},
            "DF": {"coverage": "thin"},
            "MF": {"coverage": "thin"},
            "FW": {"coverage": "thin"},
        },
    }
    opps = [
        _opp("1", "GK_A", "GK", 400_000),
        _opp("2", "DF_A", "DF", 500_000),
        _opp("3", "MF_A", "MF", 300_000),
    ]
    board = {
        "primary_targets": [],
        "patch_policy": {"allow": False, "max_spend": 2_500_000},
        "residual_after_reserve": 5_000_000,
        "moves": {"buy": []},
        "cash_reserved": 0,
    }
    plan, _pkg = build_action_plan(
        me,
        diagnosis,
        opps,
        [],
        target_board=board,
        funding_info={
            "cash_reserved": 0,
            "primary_targets": [],
            "funding_target": 0,
            "gap_costs": [],
        },
        market_mode="fixed",
        diagnostico_plantilla={
            "structural_needs": [],
            "lineas": diagnosis["by_position"],
        },
    )
    buys = {a.get("name") for a in plan if a.get("action") == "buy_now"}
    _assert(buys >= {"GK_A", "DF_A", "MF_A"}, buys)
    _assert(
        not any("parche bloqueado" in (a.get("why") or "") for a in plan),
        [a.get("why") for a in plan],
    )


def test_expensive_primary_does_not_kill_chollo_that_fits() -> None:
    me = {"balance": 6_500_000, "squad": [], "rank": 8}
    diagnosis = {
        "alerts": [],
        "by_position": {
            "DF": {"coverage": "thin"},
            "FW": {"coverage": "ok"},
        },
    }
    opps = [_opp("c1", "CholloDF", "DF", 1_000_000)]
    plan, _pkg = build_action_plan(
        me,
        diagnosis,
        opps,
        [],
        target_board={
            "primary_targets": [
                {
                    "player_id": "fw8",
                    "name": "Crack",
                    "price": 8_000_000,
                    "on_daily_market": True,
                    "position": "FW",
                }
            ],
            "patch_policy": {"allow": False, "max_spend": 2_500_000},
            "moves": {"buy": []},
            "cash_reserved": 0,
        },
        funding_info={
            "cash_reserved": 0,
            "primary_targets": [
                {
                    "player_id": "fw8",
                    "name": "Crack",
                    "price": 8_000_000,
                    "on_daily_market": True,
                    "position": "FW",
                }
            ],
            "funding_target": 8_000_000,
            "funding_shortfall": 1_500_000,
            "all_gap_costs": [
                {"position": "FW", "cost": 8_000_000, "need": "perfect_buy_daily"}
            ],
            "gap_costs": [
                {"position": "FW", "cost": 8_000_000, "need": "perfect_buy_daily"}
            ],
        },
        market_mode="fixed",
        diagnostico_plantilla={
            "structural_needs": [],
            "lineas": diagnosis["by_position"],
        },
    )
    buys = {a.get("name") for a in plan if a.get("action") == "buy_now"}
    _assert("CholloDF" in buys, [(a.get("name"), a.get("action"), a.get("why")) for a in plan])
    _assert(
        not any("poca caja" in (a.get("why") or "") for a in plan if a.get("name") == "CholloDF"),
        [a.get("why") for a in plan],
    )


def test_funding_plan_skips_unaffordable_crack() -> None:
    board = {
        "primary_targets": [
            {
                "player_id": "fw8",
                "name": "Crack",
                "position": "FW",
                "price": 8_000_000,
                "ep_score": 90,
                "on_daily_market": True,
            },
            {
                "player_id": "mf1",
                "name": "Chollo",
                "position": "MF",
                "price": 1_000_000,
                "ep_score": 40,
                "on_daily_market": True,
            },
        ],
        "moves": {"buy": []},
        "balance": 6_500_000,
    }
    funding = funding_plan_from_board(board, balance=6_500_000)
    _assert(float(funding["funding_target"]) == 1_000_000, funding)
    _assert(float(funding["funding_shortfall"]) == 0.0, funding)
    _assert(funding["cash_tight"] is False, funding)
    _assert(len(funding.get("all_gap_costs") or []) == 2, funding)


def test_estimate_gap_funding_uses_league_cycle() -> None:
    out = estimate_gap_funding([], [], 1_000_000, cycle_hours=8)
    note = str(out.get("liquidity_note") or "")
    _assert("8h" in note, note)
    _assert("1–2 días" not in note and "1-2 días" not in note, note)
    _assert(float(out["cash_lag_hours"]) == 8.0, out)
    _assert("16h" not in note, note)


if __name__ == "__main__":
    tests = [
        test_other_min_ignores_primary_shopping_list,
        test_other_min_uses_cheapest_on_market_for_thin_fw,
        test_other_min_zero_when_thin_has_no_listing,
        test_thin_fw_without_listing_does_not_block_df,
        test_patch_policy_does_not_block_daily_buys,
        test_expensive_primary_does_not_kill_chollo_that_fits,
        test_funding_plan_skips_unaffordable_crack,
        test_estimate_gap_funding_uses_league_cycle,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"OK  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERR  {fn.__name__}: {exc}")
    if failed:
        raise SystemExit(f"{failed} test(s) failed")
    print(f"All {len(tests)} tests passed")
