"""Tests del plan de ciclo: narrativa, cupos, liquidez al siguiente ciclo y revalorización."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cycle_plan import (  # noqa: E402
    KIND_ACCEPT,
    KIND_BID,
    KIND_CLAUSE,
    KIND_DECLINE,
    KIND_HOLD,
    KIND_LIST,
    attach_value_trends,
    build_cycle_plan,
    compute_value_trend,
    squad_value_summary,
)
from competitive_actions import appreciation_play_score  # noqa: E402


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
    _assert("consecutive_up" in t, t)


def test_consecutive_up_counts_live_legs() -> None:
    t = compute_value_trend("1", 210_000, {"1": [160_000, 185_000, 210_000]})
    _assert(t["consecutive_up"] == 2, t)
    _assert(t["rising"] is True, t)
    _assert(t["abs_gain"] >= 49_000, t)
    down = compute_value_trend("1", 180_000, {"1": [160_000, 185_000, 210_000, 180_000]})
    _assert(down["consecutive_up"] == 0, down)
    _assert(down["rising"] is False, down)


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
    _assert("siguiente ciclo" in plan["narrative"], plan["narrative"])
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
    decline = next(m for m in plan["moves"] if m["kind"] == KIND_DECLINE)
    why = (decline["why"] or "").lower()
    _assert("vuelve a listar" in why, decline)
    _assert("colchón" in why, decline)


def test_fair_offer_on_xi_is_hold() -> None:
    squad = [
        {
            "id": "star",
            "name": "Estrella",
            "position": "FW",
            "price": 10_000_000,
            "lineup_prob": 0.9,
            "xpts": 8,
            "delta_5d": 0.12,
            "rising": True,
            "price_delta_1d": 0.02,
        }
    ]
    offers = [
        {
            "player_id": "star",
            "name": "Estrella",
            "amount": 10_000_000,
            "market_value": 10_000_000,
            "pct_of_vm": 1.0,
            "status": "pending",
            "from_machine": True,
        }
    ]
    plan = build_cycle_plan(
        me={"balance": 5_000_000, "squad": squad},
        squad=squad,
        opportunities=[],
        sales_state={"listed_ids": ["star"], "pending_offers": offers},
        recommended_xi=_xi("star"),
        league_rules={"max_squad": 25, "sale_limit": 5},
        max_squad=25,
    )
    kinds = [m["kind"] for m in plan["moves"]]
    _assert(KIND_HOLD in kinds, kinds)
    _assert(KIND_ACCEPT not in kinds, kinds)
    _assert(plan["constraints"]["holds_count"] == 1, plan["constraints"])
    _assert("cartera" in (plan["narrative"] or "").lower(), plan["narrative"])


def test_keep_riding_fair_offer_is_hold() -> None:
    squad = [
        {
            "id": "rise",
            "name": "Sube",
            "position": "MF",
            "price": 3_000_000,
            "lineup_prob": 0.4,
            "xpts": 4.0,
            "delta_5d": 0.12,
            "rising": True,
            "price_delta_1d": 0.02,
        }
    ]
    offers = [
        {
            "player_id": "rise",
            "name": "Sube",
            "amount": 3_000_000,
            "market_value": 3_000_000,
            "pct_of_vm": 1.0,
            "status": "pending",
            "from_machine": True,
        }
    ]
    plan = build_cycle_plan(
        me={"balance": 2_000_000, "squad": squad},
        squad=squad,
        opportunities=[],
        sales_state={"listed_ids": ["rise"], "pending_offers": offers},
        recommended_xi=_xi(),
        league_rules={"max_squad": 25, "sale_limit": 5},
        max_squad=25,
    )
    kinds = [m["kind"] for m in plan["moves"]]
    _assert(KIND_HOLD in kinds, kinds)
    _assert(KIND_ACCEPT not in kinds, kinds)


def test_premium_non_xi_offer_accepts() -> None:
    squad = [
        {
            "id": "bench",
            "name": "Fade",
            "position": "MF",
            "price": 4_000_000,
            "lineup_prob": 0.2,
            "xpts": 1.5,
            "decelerating": True,
            "delta_5d": 0.01,
        }
    ]
    offers = [
        {
            "player_id": "bench",
            "name": "Fade",
            "amount": 4_200_000,
            "market_value": 4_000_000,
            "pct_of_vm": 1.05,
            "status": "pending",
            "from_machine": True,
        }
    ]
    plan = build_cycle_plan(
        me={"balance": 1_000_000, "squad": squad},
        squad=squad,
        opportunities=[],
        sales_state={"listed_ids": ["bench"], "pending_offers": offers},
        recommended_xi=_xi(),
        league_rules={"max_squad": 25, "sale_limit": 5},
        max_squad=25,
    )
    accept = next(m for m in plan["moves"] if m["kind"] == KIND_ACCEPT)
    _assert(accept["name"] == "Fade", accept)
    _assert(accept.get("accept_reason") in ("premium", "fade"), accept)


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


def test_cycle_plan_does_not_list_sold_players() -> None:
    """Mukiele / Nmecha vendidos no deben salir en 'Pon en venta'."""
    from mister_client import reconcile_squad_with_pool

    html_squad = [
        {
            "id": "65186",
            "name": "N. Mukiele",
            "position": "DF",
            "price": 5_350_000,
            "from_lineup_only": False,
            "lineup_prob": 0.2,
            "in_lineup": True,
        },
        {
            "id": "11145",
            "name": "L. Nmecha",
            "position": "FW",
            "price": 1_893_000,
            "from_lineup_only": False,
            "lineup_prob": 0.2,
            "in_lineup": True,
        },
        {
            "id": "1859",
            "name": "D. Solanke",
            "position": "FW",
            "price": 2_300_000,
            "from_lineup_only": False,
            "lineup_prob": 0.9,
            "in_lineup": True,
        },
    ]
    pool = [
        {"id": "65186", "name": "N. Mukiele", "owner_id": None, "is_mine": 0},
        {"id": "11145", "name": "L. Nmecha", "owner_id": "999", "is_mine": 0},
        {"id": "1859", "name": "D. Solanke", "owner_id": "me", "is_mine": 1},
    ]
    squad = reconcile_squad_with_pool(html_squad, pool, "me")
    plan = build_cycle_plan(
        me={"balance": 1_000_000, "squad": squad},
        squad=squad,
        recommended_xi={"xi": [{"player_id": "1859"}]},
        sales_state={"listed_ids": [], "pending_offers": []},
        market_mode="auction",
        max_squad=25,
        league_rules={"sale_limit": 5, "max_squad": 25},
    )
    listed = [m.get("name") for m in plan["moves"] if m.get("kind") == KIND_LIST]
    _assert("N. Mukiele" not in listed, listed)
    _assert("L. Nmecha" not in listed, listed)


def _spike_row(**extra: object) -> dict:
    row = {
        "id": "spike",
        "name": "Pico",
        "position": "MF",
        "price": 210_000,
        "market_value": 210_000,
        "bid": 210_000,
        "puja_recomendada": 210_000,
        "on_daily_market": True,
        "seller": "market",
        "lineup_prob": 0.0,
        "fills_need": True,
        "budget_fit": "comfortable",
    }
    row.update(extra)
    return row


def test_spike_without_minutes_is_not_a_bid() -> None:
    """160k→185k→210k y 0% de once: pico de VM, no puja."""
    row = _spike_row()
    attach_value_trends([row], {"spike": [160_000, 185_000, 210_000]})
    _assert(row["consecutive_up"] == 2, row)
    _assert(row["rising"] is True, row)
    score, why = appreciation_play_score(row)
    _assert(score == 0, (score, why))
    squad = [
        {"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9, "xpts": 8},
    ]
    plan = build_cycle_plan(
        me={"balance": 10_000_000, "squad": squad},
        squad=squad,
        opportunities=[row],
        sales_state={"listed_ids": [], "pending_offers": []},
        recommended_xi=_xi("xi1"),
        league_rules={"max_squad": 25, "sale_limit": 5},
        max_squad=25,
    )
    bid_names = [m["name"] for m in plan["moves"] if m["kind"] == KIND_BID]
    _assert("Pico" not in bid_names, bid_names)


def test_spike_already_falling_is_not_a_bid() -> None:
    """Último ciclo 210→180: aunque el neto 5d siga verde, no pujar."""
    row = _spike_row(price=180_000, market_value=180_000, bid=180_000, puja_recomendada=180_000)
    attach_value_trends([row], {"spike": [160_000, 185_000, 210_000, 180_000]})
    _assert(row["rising"] is False, row)
    score, why = appreciation_play_score(row)
    _assert(score == 0, (score, why))
    squad = [
        {"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9, "xpts": 8},
    ]
    plan = build_cycle_plan(
        me={"balance": 10_000_000, "squad": squad},
        squad=squad,
        opportunities=[row],
        sales_state={"listed_ids": [], "pending_offers": []},
        recommended_xi=_xi("xi1"),
        league_rules={"max_squad": 25, "sale_limit": 5},
        max_squad=25,
    )
    bid_names = [m["name"] for m in plan["moves"] if m["kind"] == KIND_BID]
    _assert("Pico" not in bid_names, bid_names)


def test_starter_live_rise_is_a_bid() -> None:
    """Titular usable, 2 ciclos al alza, VM 1.5M: sí puntúa y entra como puja."""
    row = {
        "id": "live",
        "name": "Vivo",
        "position": "MF",
        "price": 1_500_000,
        "market_value": 1_500_000,
        "bid": 1_500_000,
        "puja_recomendada": 1_500_000,
        "on_daily_market": True,
        "seller": "market",
        "lineup_prob": 0.8,
        "budget_fit": "comfortable",
    }
    attach_value_trends([row], {"live": [1_350_000, 1_420_000, 1_500_000]})
    _assert(row["consecutive_up"] >= 2, row)
    _assert(row["rising"] is True, row)
    _assert(row.get("decelerating") is False, row)
    score, why = appreciation_play_score(row)
    _assert(score > 0, (score, why))
    squad = [
        {"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9, "xpts": 8},
    ]
    plan = build_cycle_plan(
        me={"balance": 10_000_000, "squad": squad},
        squad=squad,
        opportunities=[row],
        sales_state={"listed_ids": [], "pending_offers": []},
        recommended_xi=_xi("xi1"),
        league_rules={"max_squad": 25, "sale_limit": 5},
        max_squad=25,
    )
    bid_names = [m["name"] for m in plan["moves"] if m["kind"] == KIND_BID]
    _assert("Vivo" in bid_names, (bid_names, score, why, row))


def test_rival_listed_on_market_is_not_appreciation() -> None:
    """Listado de rival: el sistema se lo lleva al VM, no hay flip."""
    row = {
        "id": "listed",
        "name": "Listado",
        "position": "MF",
        "price": 1_500_000,
        "market_value": 1_500_000,
        "bid": 1_500_000,
        "puja_recomendada": 1_500_000,
        "seller": "market",
        "on_daily_market": True,
        "listed_by_rival": True,
        "listed_by_name": "Otro",
        "lineup_prob": 0.85,
        "budget_fit": "comfortable",
        "fills_need": True,
        "fills_structural": True,
        "delta_5d": 0.16,
        "rising": True,
        "decelerating": False,
    }
    attach_value_trends([row], {"listed": [1_350_000, 1_420_000, 1_500_000]})
    score, why = appreciation_play_score(row)
    _assert(score == 0, (score, why))
    _assert(any("rival" in str(b).lower() or "sistema" in str(b).lower() for b in why), why)


def test_cycle_plan_bids_only_free_agents_for_appreciation() -> None:
    """Hoy: Durán libre sí; Álvarez/Tárrega en venta de rivales no."""
    squad = [
        {"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9, "xpts": 8},
    ]
    free = {
        "id": "duran",
        "name": "P. Durán",
        "position": "FW",
        "price": 313_000,
        "market_value": 313_000,
        "bid": 344_300,
        "puja_recomendada": 344_300,
        "on_daily_market": True,
        "seller": "market",
        "listed_by_rival": False,
        "lineup_prob": 0.5,
        "fills_need": True,
        "fills_coverage_gap": True,
        "budget_fit": "comfortable",
        "gw_starter": True,
    }
    alvarez = {
        "id": "alvarez",
        "name": "C. Álvarez",
        "position": "MF",
        "price": 4_090_000,
        "market_value": 4_090_000,
        "bid": 4_212_700,
        "puja_recomendada": 4_212_700,
        "on_daily_market": True,
        "seller": "market",
        "listed_by_rival": True,
        "listed_by_name": "Manuel",
        "owner_id": "15399848",
        "lineup_prob": 0.5,
        "fills_need": True,
        "fills_structural": True,
        "budget_fit": "comfortable",
        "gw_starter": True,
    }
    tarrega = {
        "id": "tarrega",
        "name": "C. Tárrega",
        "position": "DF",
        "price": 2_414_000,
        "market_value": 2_414_000,
        "bid": 2_510_560,
        "puja_recomendada": 2_510_560,
        "on_daily_market": True,
        "seller": "market",
        "listed_by_rival": True,
        "listed_by_name": "Abel",
        "owner_id": "15399131",
        "lineup_prob": 0.9,
        "is_upgrade": True,
        "overstocked": True,
        "budget_fit": "comfortable",
    }
    attach_value_trends(
        [free, alvarez, tarrega],
        {
            "duran": [160_000, 220_000, 313_000],
            "alvarez": [3_520_000, 3_800_000, 4_090_000],
            "tarrega": [1_970_000, 2_180_000, 2_414_000],
        },
    )
    plan = build_cycle_plan(
        me={"balance": 20_000_000, "squad": squad},
        squad=squad,
        opportunities=[free, alvarez, tarrega],
        sales_state={"listed_ids": [], "pending_offers": []},
        recommended_xi=_xi("xi1"),
        league_rules={"max_squad": 25, "sale_limit": 5},
        max_squad=25,
    )
    bid_names = [m["name"] for m in plan["moves"] if m["kind"] == KIND_BID]
    next_names = [t["name"] for t in plan.get("next_cycle_targets") or []]
    _assert("P. Durán" in bid_names, bid_names)
    _assert("C. Álvarez" not in bid_names, bid_names)
    _assert("C. Tárrega" not in bid_names, bid_names)
    _assert("C. Álvarez" not in next_names, next_names)
    _assert("C. Tárrega" not in next_names, next_names)


def test_rival_owned_is_not_appreciation() -> None:
    """Subida viva en plantilla rival no es revalorización: no vende al VM."""
    row = {
        "id": "riv",
        "name": "RivalUp",
        "position": "MF",
        "price": 1_500_000,
        "market_value": 1_500_000,
        "clause": 4_000_000,
        "seller": "rival",
        "owner_id": "99",
        "owner_name": "Otro",
        "on_daily_market": False,
        "lineup_prob": 0.85,
        "budget_fit": "comfortable",
    }
    attach_value_trends([row], {"riv": [1_350_000, 1_420_000, 1_500_000]})
    score, why = appreciation_play_score(row)
    _assert(score == 0, (score, why))
    _assert(any("rival" in str(b).lower() for b in why), why)


def test_reachable_target_gets_bid_priority() -> None:
    squad = [{"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9}]
    market = [
        {
            "id": "quiet",
            "name": "Quiet",
            "position": "MF",
            "price": 2_000_000,
            "bid": 2_000_000,
            "puja_recomendada": 2_000_000,
            "on_daily_market": True,
            "seller": "market",
            "delta_5d": 0.11,
            "rising": True,
            "lineup_prob": 0.8,
        },
        {
            "id": "target",
            "name": "Objetivo",
            "position": "FW",
            "price": 3_000_000,
            "bid": 3_000_000,
            "puja_recomendada": 3_000_000,
            "on_daily_market": True,
            "seller": "market",
            "delta_5d": 0.02,
            "rising": True,
            "lineup_prob": 0.85,
        },
    ]
    plan = build_cycle_plan(
        me={"balance": 20_000_000, "squad": squad},
        squad=squad,
        opportunities=market,
        sales_state={"listed_ids": [], "pending_offers": [], "listed_count": 0},
        recommended_xi=_xi("xi1"),
        gw_target_xi={
            "xi": [{"player_id": "target", "ownership": "daily_market", "reachable": "daily_market"}],
            "coverage": {
                "missing_slots": [{"player_id": "target", "reachable": "daily_market"}],
            },
        },
        league_rules={"max_squad": 15, "sale_limit": 5},
        max_squad=15,
    )
    bids = [m for m in plan["moves"] if m["kind"] == KIND_BID]
    _assert(bids and bids[0]["name"] == "Objetivo", plan["moves"])
    _assert(bids[0].get("closes_gw_target") is True, bids[0])
    _assert("once objetivo" in (bids[0].get("why") or ""), bids[0])


def test_near_slot_is_not_bid_priority() -> None:
    squad = [{"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9}]
    market = [
        {
            "id": "quiet",
            "name": "Quiet",
            "position": "MF",
            "price": 2_000_000,
            "bid": 2_000_000,
            "puja_recomendada": 2_000_000,
            "on_daily_market": True,
            "seller": "market",
            "delta_5d": 0.11,
            "rising": True,
            "lineup_prob": 0.8,
        },
        {
            "id": "near-star",
            "name": "CasiCubierto",
            "position": "FW",
            "price": 3_000_000,
            "bid": 3_000_000,
            "puja_recomendada": 3_000_000,
            "on_daily_market": True,
            "seller": "market",
            "delta_5d": 0.02,
            "rising": True,
            "lineup_prob": 0.85,
        },
    ]
    plan = build_cycle_plan(
        me={"balance": 20_000_000, "squad": squad},
        squad=squad,
        opportunities=market,
        sales_state={"listed_ids": [], "pending_offers": [], "listed_count": 0},
        recommended_xi=_xi("xi1"),
        gw_target_xi={
            "xi": [
                {
                    "player_id": "near-star",
                    "ownership": "daily_market",
                    "reachable": "daily_market",
                    "near": True,
                }
            ],
            "coverage": {
                "missing_slots": [
                    {"player_id": "near-star", "reachable": "daily_market", "near": True}
                ],
                "near_slots": [{"player_id": "near-star"}],
            },
        },
        league_rules={"max_squad": 15, "sale_limit": 5},
        max_squad=15,
    )
    bids = [m for m in plan["moves"] if m["kind"] == KIND_BID]
    _assert(bids, plan["moves"])
    _assert(bids[0]["name"] != "CasiCubierto", bids[0])
    _assert(not any(m.get("closes_gw_target") for m in bids), bids)


def _fade_bench(pid: str = "bench", name: str = "Banquillo", price: float = 7_000_000) -> dict:
    return {
        "id": pid,
        "name": name,
        "position": "MF",
        "price": price,
        "lineup_prob": 0.2,
        "decelerating": True,
        "delta_5d": 0.01,
        "accel": -0.03,
    }


def test_debt_bid_allowed_when_closes_target() -> None:
    squad = [
        {"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9},
        _fade_bench(),
    ]
    market = [
        {
            "id": "target",
            "name": "Objetivo",
            "position": "FW",
            "price": 8_000_000,
            "bid": 8_000_000,
            "puja_recomendada": 8_000_000,
            "on_daily_market": True,
            "seller": "market",
            "delta_5d": 0.04,
            "rising": True,
            "lineup_prob": 0.85,
            "debt_risk": True,
            "budget_fit": "stretch",
        }
    ]
    plan = build_cycle_plan(
        me={"balance": 2_000_000, "max_debt": 20_000_000, "squad": squad},
        squad=squad,
        opportunities=market,
        sales_state={"listed_ids": [], "pending_offers": [], "listed_count": 0},
        recommended_xi=_xi("xi1"),
        gw_target_xi={
            "xi": [{"player_id": "target", "ownership": "daily_market", "reachable": "daily_market"}],
            "coverage": {
                "missing_slots": [{"player_id": "target", "reachable": "daily_market"}],
            },
        },
        league_rules={"max_squad": 15, "sale_limit": 5},
        max_squad=15,
        hours_to_jornada=120,
        market_cycle={"cash_lag_hours": 24, "hours_to_end": 10, "cycle_hours": 24},
    )
    bids = [m for m in plan["moves"] if m["kind"] == KIND_BID]
    _assert(bids and bids[0]["name"] == "Objetivo", plan["moves"])
    _assert(plan["constraints"]["spendable"] == 20_000_000, plan["constraints"])
    lists = [m for m in plan["moves"] if m["kind"] == KIND_LIST]
    _assert(any(m.get("list_reason") == "recover_debt" for m in lists), lists)
    _assert("recupera" in (bids[0].get("why") or "").lower(), bids[0])


def test_flip_does_not_use_debt() -> None:
    squad = [{"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9}]
    market = [
        {
            "id": "flip",
            "name": "Flip",
            "position": "MF",
            "price": 6_000_000,
            "bid": 6_000_000,
            "puja_recomendada": 6_000_000,
            "on_daily_market": True,
            "seller": "market",
            "delta_5d": 0.12,
            "rising": True,
            "lineup_prob": 0.75,
            "debt_risk": True,
            "budget_fit": "stretch",
        }
    ]
    plan = build_cycle_plan(
        me={"balance": 1_000_000, "max_debt": 20_000_000, "squad": squad},
        squad=squad,
        opportunities=market,
        sales_state={"listed_ids": [], "pending_offers": [], "listed_count": 0},
        recommended_xi=_xi("xi1"),
        league_rules={"max_squad": 15, "sale_limit": 5},
        max_squad=15,
    )
    bids = [m for m in plan["moves"] if m["kind"] == KIND_BID]
    _assert(not any(m["name"] == "Flip" for m in bids), bids)


def test_hoy_one_clause_after_market_bids() -> None:
    squad = [
        {"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9},
        _fade_bench("bench2", "Reserva", 10_000_000),
    ]
    market = [
        {
            "id": "mkt",
            "name": "Mercado",
            "position": "MF",
            "price": 3_000_000,
            "bid": 3_000_000,
            "puja_recomendada": 3_000_000,
            "on_daily_market": True,
            "seller": "market",
            "delta_5d": 0.05,
            "rising": True,
            "lineup_prob": 0.85,
            "budget_fit": "comfortable",
        }
    ]
    rivals = [
        {
            "player_id": "clause-star",
            "name": "Clausulable",
            "position": "FW",
            "clause": 8_000_000,
            "bid": 8_000_000,
            "market_value": 7_000_000,
            "upgrade_score": 60,
            "clause_roi": 7.5,
            "action": "clause_bid",
            "budget_fit": "stretch",
        },
        {
            "player_id": "clause-two",
            "name": "Otra",
            "position": "MF",
            "clause": 5_000_000,
            "bid": 5_000_000,
            "market_value": 4_500_000,
            "upgrade_score": 40,
            "clause_roi": 8.0,
            "action": "clause_bid",
            "budget_fit": "stretch",
        },
    ]
    plan = build_cycle_plan(
        me={"balance": 2_000_000, "max_debt": 20_000_000, "squad": squad},
        squad=squad,
        opportunities=market,
        sales_state={"listed_ids": [], "pending_offers": [], "listed_count": 0},
        recommended_xi=_xi("xi1"),
        gw_target_xi={
            "xi": [
                {"player_id": "mkt", "ownership": "daily_market", "reachable": "daily_market"},
                {"player_id": "clause-star", "ownership": "rival", "reachable": "clause", "xpts": 9.0},
                {"player_id": "clause-two", "ownership": "rival", "reachable": "clause", "xpts": 7.0},
            ],
            "coverage": {
                "missing_slots": [
                    {"player_id": "mkt", "reachable": "daily_market", "name": "Mercado"},
                    {
                        "player_id": "clause-star",
                        "reachable": "clause",
                        "name": "Clausulable",
                        "clause": 8_000_000,
                        "xpts": 9.0,
                        "your_xpts": 4.0,
                    },
                    {
                        "player_id": "clause-two",
                        "reachable": "clause",
                        "name": "Otra",
                        "clause": 5_000_000,
                        "xpts": 7.0,
                        "your_xpts": 5.0,
                    },
                ],
            },
        },
        league_rules={"max_squad": 15, "sale_limit": 5},
        max_squad=15,
        rival_upgrades=rivals,
        hours_to_jornada=120,
        market_cycle={"cash_lag_hours": 24, "hours_to_end": 10, "cycle_hours": 24},
    )
    clauses = [m for m in plan["moves"] if m["kind"] == KIND_CLAUSE]
    _assert(len(clauses) == 1, clauses)
    _assert(clauses[0]["name"] == "Clausulable", clauses[0])
    _assert(clauses[0].get("closes_gw_target") is True, clauses[0])
    bids = [m for m in plan["moves"] if m["kind"] == KIND_BID]
    _assert(any(m["name"] == "Mercado" for m in bids), bids)
    lists = [m for m in plan["moves"] if m["kind"] == KIND_LIST]
    _assert(any(m.get("list_reason") == "recover_debt" for m in lists), lists)


def test_debt_bid_skipped_without_recover_sale() -> None:
    squad = [{"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9}]
    market = [
        {
            "id": "target",
            "name": "Objetivo",
            "position": "FW",
            "price": 8_000_000,
            "bid": 8_000_000,
            "puja_recomendada": 8_000_000,
            "on_daily_market": True,
            "seller": "market",
            "delta_5d": 0.04,
            "rising": True,
            "lineup_prob": 0.85,
            "debt_risk": True,
            "budget_fit": "stretch",
        }
    ]
    plan = build_cycle_plan(
        me={"balance": 2_000_000, "max_debt": 20_000_000, "squad": squad},
        squad=squad,
        opportunities=market,
        sales_state={"listed_ids": [], "pending_offers": [], "listed_count": 0},
        recommended_xi=_xi("xi1"),
        gw_target_xi={
            "xi": [{"player_id": "target", "ownership": "daily_market", "reachable": "daily_market"}],
            "coverage": {
                "missing_slots": [{"player_id": "target", "reachable": "daily_market"}],
            },
        },
        league_rules={"max_squad": 15, "sale_limit": 5},
        max_squad=15,
        hours_to_jornada=120,
        market_cycle={"cash_lag_hours": 24, "hours_to_end": 10, "cycle_hours": 24},
    )
    bids = [m for m in plan["moves"] if m["kind"] == KIND_BID]
    _assert(not any(m["name"] == "Objetivo" for m in bids), plan["moves"])


def test_debt_bid_skipped_if_sale_misses_jornada() -> None:
    squad = [
        {"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9},
        _fade_bench(),
    ]
    market = [
        {
            "id": "target",
            "name": "Objetivo",
            "position": "FW",
            "price": 8_000_000,
            "bid": 8_000_000,
            "puja_recomendada": 8_000_000,
            "on_daily_market": True,
            "seller": "market",
            "delta_5d": 0.04,
            "rising": True,
            "lineup_prob": 0.85,
            "debt_risk": True,
            "budget_fit": "stretch",
        }
    ]
    plan = build_cycle_plan(
        me={"balance": 2_000_000, "max_debt": 20_000_000, "squad": squad},
        squad=squad,
        opportunities=market,
        sales_state={"listed_ids": [], "pending_offers": [], "listed_count": 0},
        recommended_xi=_xi("xi1"),
        gw_target_xi={
            "xi": [{"player_id": "target", "ownership": "daily_market", "reachable": "daily_market"}],
            "coverage": {
                "missing_slots": [{"player_id": "target", "reachable": "daily_market"}],
            },
        },
        league_rules={"max_squad": 15, "sale_limit": 5},
        max_squad=15,
        hours_to_jornada=8,
        market_cycle={"cash_lag_hours": 24, "hours_to_end": 20, "cycle_hours": 24},
    )
    bids = [m for m in plan["moves"] if m["kind"] == KIND_BID]
    _assert(not any(m["name"] == "Objetivo" for m in bids), plan["moves"])


def _clause_rival(
    pid: str,
    name: str,
    *,
    position: str = "DF",
    clause: float = 8_000_000,
    vm: float = 5_800_000,
    xpts: float = 10.8,
) -> dict:
    return {
        "player_id": pid,
        "name": name,
        "position": position,
        "clause": clause,
        "bid": clause,
        "market_value": vm,
        "upgrade_score": 60,
        "clause_roi": 7.5,
        "action": "clause_bid",
        "budget_fit": "stretch",
        "xpts": xpts,
    }


def _clause_slot(
    pid: str,
    name: str,
    *,
    position: str = "DF",
    clause: float = 8_000_000,
    xpts: float = 10.8,
    your_xpts: float = 9.0,
    your_name: str = "Gvardiol",
) -> dict:
    return {
        "player_id": pid,
        "reachable": "clause",
        "name": name,
        "position": position,
        "clause": clause,
        "xpts": xpts,
        "your_xpts": your_xpts,
        "your_name": your_name,
    }


def test_hoy_clause_skipped_for_tiny_upgrade_of_performing_starter() -> None:
    """+1.8 vs el titular que saldría (9 xPts) no es upgrade. Da igual el puesto."""
    squad = [
        {"id": "df1", "name": "Gvardiol", "position": "DF", "price": 12_000_000, "lineup_prob": 0.95, "xpts": 9.0},
        {"id": "df2", "name": "OtroDF", "position": "DF", "price": 8_000_000, "lineup_prob": 0.9, "xpts": 9.0},
        {"id": "fw1", "name": "Delantero", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9, "xpts": 8.0},
        _fade_bench("bench", "Reserva", 12_000_000),
    ]
    plan = build_cycle_plan(
        me={"balance": 2_000_000, "max_debt": 20_000_000, "squad": squad},
        squad=squad,
        opportunities=[],
        sales_state={"listed_ids": ["bench"], "pending_offers": [], "listed_count": 1},
        recommended_xi={
            "xi": [
                {"player_id": "df1", "position": "DF", "xpts": 9.0, "name": "Gvardiol"},
                {"player_id": "df2", "position": "DF", "xpts": 9.0, "name": "OtroDF"},
                {"player_id": "fw1", "position": "FW", "xpts": 8.0, "name": "Delantero"},
            ]
        },
        gw_target_xi={
            "xi": [
                {
                    "player_id": "kayode",
                    "ownership": "rival",
                    "reachable": "clause",
                    "xpts": 10.8,
                    "position": "DF",
                }
            ],
            "coverage": {
                "missing_slots": [
                    _clause_slot("kayode", "Kayode", your_xpts=9.0, your_name="Gvardiol"),
                ],
            },
        },
        league_rules={"max_squad": 15, "sale_limit": 5},
        max_squad=15,
        rival_upgrades=[_clause_rival("kayode", "Kayode")],
        hours_to_jornada=120,
        market_cycle={"cash_lag_hours": 24, "hours_to_end": 10, "cycle_hours": 24},
    )
    clauses = [m for m in plan["moves"] if m["kind"] == KIND_CLAUSE]
    _assert(not clauses, clauses)


def test_hoy_clause_when_upgrade_beats_weakest_starter() -> None:
    """Contra el DF que saldría (6 xPts) el salto es claro; cobra el banquillo listado."""
    squad = [
        {"id": "df1", "name": "Gvardiol", "position": "DF", "price": 12_000_000, "lineup_prob": 0.95, "xpts": 9.0},
        {"id": "df2", "name": "Castagne", "position": "DF", "price": 2_200_000, "lineup_prob": 0.8, "xpts": 6.0},
        {"id": "fw1", "name": "Delantero", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9, "xpts": 8.0},
        _fade_bench("bench", "Reserva", 12_000_000),
    ]
    plan = build_cycle_plan(
        me={"balance": 2_000_000, "max_debt": 20_000_000, "squad": squad},
        squad=squad,
        opportunities=[],
        sales_state={"listed_ids": ["bench"], "pending_offers": [], "listed_count": 1},
        recommended_xi={
            "xi": [
                {"player_id": "df1", "position": "DF", "xpts": 9.0, "name": "Gvardiol"},
                {"player_id": "df2", "position": "DF", "xpts": 6.0, "name": "Castagne"},
                {"player_id": "fw1", "position": "FW", "xpts": 8.0, "name": "Delantero"},
            ]
        },
        gw_target_xi={
            "xi": [
                {
                    "player_id": "kayode",
                    "ownership": "rival",
                    "reachable": "clause",
                    "xpts": 10.8,
                    "position": "DF",
                }
            ],
            "coverage": {
                "missing_slots": [
                    _clause_slot("kayode", "Kayode", your_xpts=9.0, your_name="Gvardiol"),
                ],
            },
        },
        league_rules={"max_squad": 15, "sale_limit": 5},
        max_squad=15,
        rival_upgrades=[_clause_rival("kayode", "Kayode")],
        hours_to_jornada=120,
        market_cycle={"cash_lag_hours": 24, "hours_to_end": 10, "cycle_hours": 24},
    )
    clauses = [m for m in plan["moves"] if m["kind"] == KIND_CLAUSE]
    _assert(len(clauses) == 1, clauses)
    _assert(clauses[0]["name"] == "Kayode", clauses[0])
    why = (clauses[0].get("why") or "").lower()
    _assert("castagne" in why, clauses[0])
    _assert("gvardiol" not in why, clauses[0])


def test_listed_starter_does_not_fund_clause_debt() -> None:
    """Titular listado (cualquier puesto) no financia el corto de una cláusula."""
    squad = [
        {"id": "df1", "name": "Gvardiol", "position": "DF", "price": 12_000_000, "lineup_prob": 0.95, "xpts": 9.0},
        {"id": "df2", "name": "Castagne", "position": "DF", "price": 2_200_000, "lineup_prob": 0.8, "xpts": 6.0},
        {
            "id": "gk1",
            "name": "Pickford",
            "position": "GK",
            "price": 11_000_000,
            "lineup_prob": 1.0,
            "xpts": 12.2,
        },
    ]
    plan = build_cycle_plan(
        me={"balance": 2_000_000, "max_debt": 20_000_000, "squad": squad},
        squad=squad,
        opportunities=[],
        sales_state={"listed_ids": ["gk1"], "pending_offers": [], "listed_count": 1},
        recommended_xi={
            "xi": [
                {"player_id": "df1", "position": "DF", "xpts": 9.0, "name": "Gvardiol"},
                {"player_id": "df2", "position": "DF", "xpts": 6.0, "name": "Castagne"},
                {"player_id": "gk1", "position": "GK", "xpts": 12.2, "name": "Pickford"},
            ]
        },
        gw_target_xi={
            "xi": [
                {
                    "player_id": "kayode",
                    "ownership": "rival",
                    "reachable": "clause",
                    "xpts": 10.8,
                    "position": "DF",
                }
            ],
            "coverage": {
                "missing_slots": [
                    _clause_slot("kayode", "Kayode", your_xpts=9.0, your_name="Gvardiol"),
                ],
            },
        },
        league_rules={"max_squad": 15, "sale_limit": 5},
        max_squad=15,
        rival_upgrades=[_clause_rival("kayode", "Kayode")],
        hours_to_jornada=120,
        market_cycle={"cash_lag_hours": 24, "hours_to_end": 10, "cycle_hours": 24},
    )
    clauses = [m for m in plan["moves"] if m["kind"] == KIND_CLAUSE]
    _assert(not clauses, plan["moves"])


def test_ongoing_gw_deadline_is_next_jornada() -> None:
    """Domingo en curso: el pitido de hoy no tumba un gasto que cobra antes de la siguiente."""
    squad = [
        {"id": "xi1", "name": "Titular", "position": "FW", "price": 8_000_000, "lineup_prob": 0.9},
        _fade_bench(),
    ]
    market = [
        {
            "id": "target",
            "name": "Objetivo",
            "position": "FW",
            "price": 8_000_000,
            "bid": 8_000_000,
            "puja_recomendada": 8_000_000,
            "on_daily_market": True,
            "seller": "market",
            "delta_5d": 0.04,
            "rising": True,
            "lineup_prob": 0.85,
            "debt_risk": True,
            "budget_fit": "stretch",
        }
    ]
    plan = build_cycle_plan(
        me={"balance": 2_000_000, "max_debt": 20_000_000, "squad": squad},
        squad=squad,
        opportunities=market,
        sales_state={"listed_ids": [], "pending_offers": [], "listed_count": 0},
        recommended_xi=_xi("xi1"),
        gw_target_xi={
            "xi": [{"player_id": "target", "ownership": "daily_market", "reachable": "daily_market"}],
            "coverage": {
                "missing_slots": [{"player_id": "target", "reachable": "daily_market"}],
            },
        },
        league_rules={"max_squad": 15, "sale_limit": 5},
        max_squad=15,
        hours_to_jornada=4.5,
        hours_to_solvency_deadline=130,
        solvency_target="siguiente",
        market_cycle={"cash_lag_hours": 24, "hours_to_end": 16, "cycle_hours": 24},
    )
    bids = [m for m in plan["moves"] if m["kind"] == KIND_BID]
    _assert(any(m["name"] == "Objetivo" for m in bids), plan["moves"])
    _assert(plan["constraints"]["sells_settle_before_gw"] is True, plan["constraints"])
    _assert(plan["constraints"]["solvency_target"] == "siguiente", plan["constraints"])


if __name__ == "__main__":
    test_value_trend_deceleration()
    test_consecutive_up_counts_live_legs()
    test_value_trend_follows_mister_down_arrow()
    test_trend_uses_vm_not_ask_and_skips_zeros()
    test_attach_trends_on_squad()
    test_full_squad_lists_does_not_bid()
    test_free_slot_bids_same_cycle()
    test_does_not_bid_falling_gap_filler()
    test_accept_offer_frees_slot_to_bid()
    test_outlier_offer_is_declined()
    test_fair_offer_on_xi_is_hold()
    test_keep_riding_fair_offer_is_hold()
    test_premium_non_xi_offer_accepts()
    test_sale_limit_caps_listings()
    test_does_not_list_xi()
    test_empty_plan_has_no_wait_copy()
    test_does_not_list_strong_risers_with_free_slots()
    test_lists_riser_only_when_full_and_market_hotter()
    test_does_not_list_riser_when_full_but_market_not_hotter()
    test_lists_fading_bench_with_free_slots()
    test_history_snapshot_stems()
    test_cycle_plan_does_not_list_sold_players()
    test_spike_without_minutes_is_not_a_bid()
    test_spike_already_falling_is_not_a_bid()
    test_starter_live_rise_is_a_bid()
    test_rival_owned_is_not_appreciation()
    test_rival_listed_on_market_is_not_appreciation()
    test_cycle_plan_bids_only_free_agents_for_appreciation()
    test_reachable_target_gets_bid_priority()
    test_near_slot_is_not_bid_priority()
    test_debt_bid_allowed_when_closes_target()
    test_flip_does_not_use_debt()
    test_hoy_one_clause_after_market_bids()
    test_debt_bid_skipped_without_recover_sale()
    test_debt_bid_skipped_if_sale_misses_jornada()
    test_hoy_clause_skipped_for_tiny_upgrade_of_performing_starter()
    test_hoy_clause_when_upgrade_beats_weakest_starter()
    test_listed_starter_does_not_fund_clause_debt()
    test_ongoing_gw_deadline_is_next_jornada()
    print("test_cycle_plan: OK")
