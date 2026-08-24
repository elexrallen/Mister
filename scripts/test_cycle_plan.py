"""Tests del plan de ciclo: narrativa, cupos, liquidez a 2 ciclos y revalorización."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cycle_plan import (  # noqa: E402
    KIND_ACCEPT,
    KIND_BID,
    KIND_DECLINE,
    KIND_LIST,
    attach_value_trends,
    build_cycle_plan,
    compute_value_trend,
    squad_value_summary,
)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _xi(*ids: str) -> dict:
    return {"xi": [{"player_id": i} for i in ids]}


def test_value_trend_deceleration() -> None:
    series = {"1": [10.0, 11.0, 11.6, 11.8, 11.9]}
    t = compute_value_trend("1", 11.9, series)
    _assert(t["delta_5d"] is not None and t["delta_5d"] > 0, t)
    _assert(t["decelerating"] is True, t)
    _assert(t["accel"] is not None and t["accel"] < 0, t)


def test_value_trend_follows_mister_down_arrow() -> None:
    """Neto a 5d positivo no cuenta como 'subiendo' si Mister marca bajada."""
    series = {"1": [1.757e6, 1.809e6, 1.865e6, 1.911e6, 1.932e6, 1.893e6, 1.893e6]}
    t = compute_value_trend("1", 1.893e6, series, price_delta_1d=-0.020186, trend="down")
    _assert(t["delta_5d"] is not None and t["delta_5d"] > 0.05, t)
    _assert(t["delta_cycle"] is not None and t["delta_cycle"] < 0, t)
    _assert(t["rising"] is False, t)
    _assert(t["decelerating"] is True, t)


def test_trend_uses_vm_not_ask_and_skips_zeros() -> None:
    """Listado a 2.71M con VM 1.71M: el Δ es sobre el VM, ignorando ceros del history."""
    row = {
        "id": "berg",
        "name": "Bergvall",
        "price": 2_710_000,
        "market_value": 1_710_000,
        "price_delta_1d": 0.0,
        "trend": None,
    }
    series = {"berg": [1_610_000, 1_635_000, 1_669_000, 0, 0, 1_710_000]}
    attach_value_trends([row], series)
    _assert(row["delta_5d"] is not None and 0.04 < row["delta_5d"] < 0.12, row)
    _assert(row["delta_cycle"] is not None and abs(row["delta_cycle"]) < 0.08, row)


def test_attach_trends_on_squad() -> None:
    squad = [{"id": "a", "name": "A", "price": 5_000_000}]
    attach_value_trends(squad, {"a": [4_000_000, 4_400_000, 5_000_000]})
    _assert(squad[0]["delta_5d"] is not None and squad[0]["delta_5d"] > 0.2, squad[0])
    summary = squad_value_summary(squad)
    _assert(summary["delta_5d"] is not None and summary["delta_5d"] > 0, summary)


def test_full_squad_lists_does_not_bid() -> None:
    squad = [
        {"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9, "xpts": 8},
        {"id": "xi2", "name": "Titular2", "position": "MF", "price": 7_000_000, "lineup_prob": 0.85, "xpts": 7},
        {
            "id": "bench",
            "name": "Nmecha",
            "position": "FW",
            "price": 4_000_000,
            "lineup_prob": 0.2,
            "xpts": 2.0,
            "delta_5d": 0.06,
            "accel": -0.04,
            "decelerating": True,
        },
    ]
    market = [
        {
            "id": "hot",
            "name": "Hot",
            "position": "MF",
            "price": 3_000_000,
            "bid": 3_000_000,
            "puja_recomendada": 3_000_000,
            "on_daily_market": True,
            "seller": "market",
            "delta_5d": 0.12,
            "rising": True,
            "lineup_prob": 0.7,
            "budget_fit": "comfortable",
        }
    ]
    plan = build_cycle_plan(
        me={"balance": 20_000_000, "squad": squad},
        squad=squad,
        opportunities=market,
        sales_state={"listed_ids": [], "pending_offers": [], "listed_count": 0},
        recommended_xi=_xi("xi1", "xi2"),
        league_rules={"max_squad": 3, "sale_limit": 5},
        max_squad=3,
    )
    kinds = [m["kind"] for m in plan["moves"]]
    _assert(KIND_LIST in kinds, plan["moves"])
    _assert(KIND_BID not in kinds, f"no pujar con plantilla llena: {kinds}")
    _assert("wait" not in kinds and "scout" not in kinds and "avoid" not in kinds, kinds)
    listed = [m for m in plan["moves"] if m["kind"] == KIND_LIST]
    _assert(listed[0]["name"] == "Nmecha", listed)
    _assert(plan["constraints"]["free_slots_after_accepts"] == 0, plan["constraints"])
    _assert(any(t["name"] == "Hot" for t in plan["next_cycle_targets"]), plan["next_cycle_targets"])
    _assert("próximo ciclo" in plan["narrative"] or "proximo ciclo" in plan["narrative"], plan["narrative"])
    _assert("esperar" not in plan["narrative"].lower(), plan["narrative"])
    _assert("vigilar" not in plan["narrative"].lower(), plan["narrative"])


def test_free_slot_bids_same_cycle() -> None:
    squad = [
        {"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9, "xpts": 8},
    ]
    market = [
        {
            "id": "hot",
            "name": "Hot",
            "position": "MF",
            "price": 3_000_000,
            "bid": 3_000_000,
            "puja_recomendada": 3_000_000,
            "on_daily_market": True,
            "seller": "market",
            "delta_5d": 0.12,
            "rising": True,
            "lineup_prob": 0.75,
            "fills_need": True,
            "budget_fit": "comfortable",
        }
    ]
    plan = build_cycle_plan(
        me={"balance": 10_000_000, "squad": squad},
        squad=squad,
        opportunities=market,
        sales_state={"listed_ids": [], "pending_offers": []},
        recommended_xi=_xi("xi1"),
        league_rules={"max_squad": 3, "sale_limit": 5, "market_mode": "auction"},
        max_squad=3,
    )
    _assert(any(m["kind"] == KIND_BID and m["name"] == "Hot" for m in plan["moves"]), plan["moves"])
    _assert(plan["constraints"]["free_slots"] == 2, plan["constraints"])


def test_does_not_bid_falling_gap_filler() -> None:
    """Cubre hueco + VM −19% no es puja: es un parche que se deprecia."""
    squad = [
        {"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9, "xpts": 8},
    ]
    market = [
        {
            "id": "pdiaz",
            "name": "P. Díaz",
            "position": "MF",
            "price": 1_000_000,
            "bid": 1_050_000,
            "puja_recomendada": 1_050_000,
            "on_daily_market": True,
            "seller": "market",
            "delta_5d": -0.19,
            "rising": False,
            "lineup_prob": 0.4,
            "fills_need": True,
            "fills_coverage_gap": True,
            "xpts": 0.4,
        },
        {
            "id": "hot",
            "name": "Rueda",
            "position": "DF",
            "price": 2_000_000,
            "bid": 2_000_000,
            "puja_recomendada": 2_000_000,
            "on_daily_market": True,
            "seller": "market",
            "delta_5d": 0.44,
            "rising": True,
            "lineup_prob": 0.7,
        },
    ]
    plan = build_cycle_plan(
        me={"balance": 10_000_000, "squad": squad},
        squad=squad,
        opportunities=market,
        sales_state={"listed_ids": [], "pending_offers": []},
        recommended_xi=_xi("xi1"),
        league_rules={"max_squad": 25, "sale_limit": 5},
        max_squad=25,
    )
    bid_names = [m["name"] for m in plan["moves"] if m["kind"] == KIND_BID]
    _assert("P. Díaz" not in bid_names, bid_names)
    _assert("Rueda" in bid_names, bid_names)


def test_accept_offer_frees_slot_to_bid() -> None:
    squad = [
        {"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9, "xpts": 8},
        {"id": "listed", "name": "Mukiele", "position": "DF", "price": 4_000_000, "lineup_prob": 0.2, "on_sale": True},
    ]
    market = [
        {
            "id": "hot",
            "name": "Hot",
            "position": "MF",
            "price": 3_500_000,
            "bid": 3_500_000,
            "puja_recomendada": 3_500_000,
            "on_daily_market": True,
            "seller": "market",
            "delta_5d": 0.10,
            "rising": True,
            "lineup_prob": 0.7,
            "budget_fit": "comfortable",
        }
    ]
    offers = [
        {
            "player_id": "listed",
            "name": "Mukiele",
            "amount": 4_100_000,
            "market_value": 4_000_000,
            "pct_of_vm": 1.025,
            "status": "pending",
            "from_machine": True,
        }
    ]
    plan = build_cycle_plan(
        me={"balance": 500_000, "squad": squad},
        squad=squad,
        opportunities=market,
        sales_state={
            "listed_ids": ["listed"],
            "pending_offers": offers,
            "listed_count": 1,
        },
        recommended_xi=_xi("xi1"),
        league_rules={"max_squad": 2, "sale_limit": 5},
        max_squad=2,
    )
    kinds = [m["kind"] for m in plan["moves"]]
    _assert(KIND_ACCEPT in kinds, kinds)
    _assert(KIND_BID in kinds, kinds)
    accept = next(m for m in plan["moves"] if m["kind"] == KIND_ACCEPT)
    _assert(accept["name"] == "Mukiele", accept)
    _assert(plan["constraints"]["cash_from_accepts"] >= 4_000_000, plan["constraints"])


def test_outlier_offer_is_declined() -> None:
    squad = [{"id": "x", "name": "Caro", "position": "MF", "price": 5_000_000}]
    offers = [
        {
            "player_id": "x",
            "name": "Caro",
            "amount": 3_500_000,
            "market_value": 5_000_000,
            "pct_of_vm": 0.70,
            "status": "pending",
            "from_machine": True,
        }
    ]
    plan = build_cycle_plan(
        me={"balance": 1_000_000, "squad": squad},
        squad=squad,
        opportunities=[],
        sales_state={"listed_ids": ["x"], "pending_offers": offers},
        recommended_xi=_xi(),
        league_rules={"max_squad": 25, "sale_limit": 5},
        max_squad=25,
    )
    _assert(any(m["kind"] == KIND_DECLINE for m in plan["moves"]), plan["moves"])
    _assert(not any(m["kind"] == KIND_ACCEPT for m in plan["moves"]), plan["moves"])


def test_sale_limit_caps_listings() -> None:
    squad = [
        {
            "id": f"b{i}",
            "name": f"Banquillo{i}",
            "position": "MF",
            "price": 2_000_000,
            "lineup_prob": 0.15,
            "xpts": 1.5,
            "decelerating": True,
            "delta_5d": 0.05,
            "accel": -0.03,
        }
        for i in range(4)
    ]
    plan = build_cycle_plan(
        me={"balance": 0, "squad": squad},
        squad=squad,
        opportunities=[],
        sales_state={"listed_ids": ["already"], "listed_count": 1, "pending_offers": []},
        recommended_xi=_xi(),
        league_rules={"max_squad": 25, "sale_limit": 2},
        max_squad=25,
    )
    lists = [m for m in plan["moves"] if m["kind"] == KIND_LIST]
    _assert(len(lists) <= 1, lists)
    _assert(plan["constraints"]["sale_remaining"] == 1, plan["constraints"])


def test_does_not_list_xi() -> None:
    squad = [
        {
            "id": "star",
            "name": "Estrella",
            "position": "FW",
            "price": 20_000_000,
            "lineup_prob": 0.95,
            "xpts": 9,
            "decelerating": True,
            "delta_5d": 0.03,
        }
    ]
    plan = build_cycle_plan(
        me={"balance": 0, "squad": squad},
        squad=squad,
        opportunities=[],
        sales_state={"listed_ids": [], "pending_offers": []},
        recommended_xi=_xi("star"),
        league_rules={"max_squad": 25, "sale_limit": 5},
        max_squad=25,
    )
    _assert(not any(m["kind"] == KIND_LIST for m in plan["moves"]), plan["moves"])


def test_empty_plan_has_no_wait_copy() -> None:
    squad = [
        {"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9, "xpts": 8}
    ]
    plan = build_cycle_plan(
        me={"balance": 0, "squad": squad},
        squad=squad,
        opportunities=[],
        sales_state={"listed_ids": [], "pending_offers": []},
        recommended_xi=_xi("xi1"),
        league_rules={"max_squad": 1, "sale_limit": 5},
        max_squad=1,
    )
    _assert(plan["moves"] == [], plan)
    _assert("esperar" not in plan["narrative"].lower(), plan["narrative"])
    _assert("vigilar" not in plan["narrative"].lower(), plan["narrative"])
    _assert("evitar" not in plan["narrative"].lower(), plan["narrative"])
    _assert(plan["headline"] == "Sin movimientos de mercado", plan["headline"])


def _hot_market(delta: float, name: str = "Hot") -> dict:
    return {
        "id": "hot",
        "name": name,
        "position": "MF",
        "price": 3_000_000,
        "bid": 3_000_000,
        "puja_recomendada": 3_000_000,
        "on_daily_market": True,
        "seller": "market",
        "delta_5d": delta,
        "rising": True,
        "lineup_prob": 0.7,
        "budget_fit": "comfortable",
    }


def _bench_riser(delta: float, name: str = "Cepeda") -> dict:
    return {
        "id": "bench",
        "name": name,
        "position": "FW",
        "price": 1_700_000,
        "lineup_prob": 0.2,
        "xpts": 2.0,
        "delta_5d": delta,
        "accel": -0.04,
        "decelerating": True,
    }


def test_does_not_list_strong_risers_with_free_slots() -> None:
    """18/25 y +40% en banquillo: se conserva; se puede pujar sin venderlo."""
    squad = [
        {"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9, "xpts": 8},
        _bench_riser(0.40, "Cepeda"),
        {
            "id": "perez",
            "name": "Pérez",
            "position": "DF",
            "price": 800_000,
            "lineup_prob": 0.15,
            "xpts": 1.2,
            "delta_5d": 2.97,
            "decelerating": True,
            "accel": -0.05,
        },
    ]
    plan = build_cycle_plan(
        me={"balance": 8_000_000, "squad": squad},
        squad=squad,
        opportunities=[_hot_market(0.44, "Rueda")],
        sales_state={"listed_ids": [], "pending_offers": []},
        recommended_xi=_xi("xi1"),
        league_rules={"max_squad": 25, "sale_limit": 5},
        max_squad=25,
    )
    listed_names = [m["name"] for m in plan["moves"] if m["kind"] == KIND_LIST]
    _assert("Cepeda" not in listed_names, listed_names)
    _assert("Pérez" not in listed_names, listed_names)
    _assert(any(m["kind"] == KIND_BID for m in plan["moves"]), plan["moves"])
    _assert("frenando" not in plan["narrative"].lower(), plan["narrative"])


def test_lists_riser_only_when_full_and_market_hotter() -> None:
    """Plantilla llena + mercado +25% vs banquillo +10% → listar para rotar VM."""
    squad = [
        {"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9, "xpts": 8},
        {"id": "xi2", "name": "Titular2", "position": "MF", "price": 7_000_000, "lineup_prob": 0.85, "xpts": 7},
        _bench_riser(0.10, "Ciss"),
    ]
    plan = build_cycle_plan(
        me={"balance": 20_000_000, "squad": squad},
        squad=squad,
        opportunities=[_hot_market(0.25, "Hot")],
        sales_state={"listed_ids": [], "pending_offers": []},
        recommended_xi=_xi("xi1", "xi2"),
        league_rules={"max_squad": 3, "sale_limit": 5},
        max_squad=3,
    )
    listed = [m for m in plan["moves"] if m["kind"] == KIND_LIST]
    _assert(len(listed) == 1 and listed[0]["name"] == "Ciss", listed)
    _assert(listed[0].get("list_reason") == "swap", listed[0])
    _assert("llena" in listed[0]["why"].lower() or "tope" in plan["narrative"].lower(), listed[0]["why"])
    _assert(KIND_BID not in [m["kind"] for m in plan["moves"]], plan["moves"])


def test_does_not_list_riser_when_full_but_market_not_hotter() -> None:
    """Lleno pero el mercado solo está +4pp por encima: se conserva el +40%."""
    squad = [
        {"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9, "xpts": 8},
        {"id": "xi2", "name": "Titular2", "position": "MF", "price": 7_000_000, "lineup_prob": 0.85, "xpts": 7},
        _bench_riser(0.40, "Cepeda"),
    ]
    plan = build_cycle_plan(
        me={"balance": 20_000_000, "squad": squad},
        squad=squad,
        opportunities=[_hot_market(0.44, "Rueda")],
        sales_state={"listed_ids": [], "pending_offers": []},
        recommended_xi=_xi("xi1", "xi2"),
        league_rules={"max_squad": 3, "sale_limit": 5},
        max_squad=3,
    )
    listed_names = [m["name"] for m in plan["moves"] if m["kind"] == KIND_LIST]
    _assert("Cepeda" not in listed_names, listed_names)


def test_lists_fading_bench_with_free_slots() -> None:
    """Banquillo que ya no sube sí se lista aunque haya plazas: el VM no tira."""
    squad = [
        {"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9, "xpts": 8},
        {
            "id": "dead",
            "name": "Parche",
            "position": "MF",
            "price": 2_000_000,
            "lineup_prob": 0.15,
            "xpts": 1.5,
            "delta_5d": -0.04,
            "decelerating": False,
        },
    ]
    plan = build_cycle_plan(
        me={"balance": 2_000_000, "squad": squad},
        squad=squad,
        opportunities=[],
        sales_state={"listed_ids": [], "pending_offers": []},
        recommended_xi=_xi("xi1"),
        league_rules={"max_squad": 25, "sale_limit": 5},
        max_squad=25,
    )
    listed = [m for m in plan["moves"] if m["kind"] == KIND_LIST]
    _assert(len(listed) == 1 and listed[0]["name"] == "Parche", listed)
    _assert(listed[0].get("list_reason") == "fade", listed[0])


def test_history_snapshot_stems() -> None:
    from datetime import datetime, timezone

    from data_engine import _snapshot_date_from_stem, history_snapshot_stem

    stem = history_snapshot_stem(datetime(2026, 8, 24, 11, 40, tzinfo=timezone.utc))
    _assert(stem == "2026-08-24T11", stem)
    _assert(str(_snapshot_date_from_stem("2026-08-24T11")) == "2026-08-24", "cycle stem")
    _assert(str(_snapshot_date_from_stem("2026-08-24")) == "2026-08-24", "legacy daily stem")


if __name__ == "__main__":
    test_value_trend_deceleration()
    test_value_trend_follows_mister_down_arrow()
    test_trend_uses_vm_not_ask_and_skips_zeros()
    test_attach_trends_on_squad()
    test_full_squad_lists_does_not_bid()
    test_free_slot_bids_same_cycle()
    test_does_not_bid_falling_gap_filler()
    test_accept_offer_frees_slot_to_bid()
    test_outlier_offer_is_declined()
    test_sale_limit_caps_listings()
    test_does_not_list_xi()
    test_empty_plan_has_no_wait_copy()
    test_does_not_list_strong_risers_with_free_slots()
    test_lists_riser_only_when_full_and_market_hotter()
    test_does_not_list_riser_when_full_but_market_not_hotter()
    test_lists_fading_bench_with_free_slots()
    test_history_snapshot_stems()
    print("test_cycle_plan: OK")
