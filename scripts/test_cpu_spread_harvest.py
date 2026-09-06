"""Tests del carril cpu_spread_harvest + transfer_wait en normas."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from competitive_actions import (  # noqa: E402
    cpu_spread_min_solvency_hours,
    finalize_action_plan,
    harvest_blocks_on_critical_need,
    is_cpu_spread_candidate,
    promote_appreciation_plays,
    promote_cpu_spread_harvest,
    resolve_transfer_wait_hours,
)
from cycle_plan import KIND_ACCEPT, KIND_BID, build_cycle_plan  # noqa: E402
from league_rules import normalize_rules  # noqa: E402


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_transfer_wait_normalize() -> None:
    rules = normalize_rules(
        {"provider": "mix", "team_limit": 25, "type": "comunio"},
        admin_data={"community": {"transfer_wait": 0, "sale_limit": 5}},
    )
    _assert(rules.get("transfer_wait") == 0, rules)
    _assert("transfer_wait_off" in (rules.get("factors") or []), rules["factors"])

    rules2 = normalize_rules(
        {"provider": "mix", "team_limit": 25, "type": "comunio"},
        admin_data={"community": {"transfer_wait": 1, "sale_limit": 5}},
    )
    _assert(rules2.get("transfer_wait") == 1, rules2)
    _assert("transfer_wait_1" in (rules2.get("factors") or []), rules2["factors"])
    _assert(resolve_transfer_wait_hours(1) == 24.0, resolve_transfer_wait_hours(1))
    _assert(resolve_transfer_wait_hours(24) == 24.0, resolve_transfer_wait_hours(24))
    _assert(resolve_transfer_wait_hours(0) == 0.0, "off")


def test_solvency_hours_profiles() -> None:
    # Patio wait=0, ciclo 12h, 1 hold + aire → 36h
    h0 = cpu_spread_min_solvency_hours(transfer_wait_hours=0, cycle_hours=12)
    _assert(abs(h0 - 36.0) < 0.01, h0)
    # Wait 24 + ciclo 8h → 24+24=48h
    h24 = cpu_spread_min_solvency_hours(transfer_wait_hours=24, cycle_hours=8)
    _assert(abs(h24 - 48.0) < 0.01, h24)


def _free_agent(
    *,
    pid: str = "h1",
    name: str = "Harvest",
    vm: float = 5_000_000,
    buy: float | None = None,
    action: str = "wait",
) -> dict:
    price = buy if buy is not None else vm
    return {
        "player_id": pid,
        "name": name,
        "position": "MF",
        "action": action,
        "on_daily_market": True,
        "seller": "market",
        "market_value": vm,
        "price": price,
        "bid": price,
        "puja_recomendada": price,
        "budget_fit": "stretch",
        "target_tier": "stretch",
        "debt_risk": True,
        "bid_cap": 20_000_000,
        "priority_score": 10,
        "rising": False,
        "decelerating": False,
    }


def test_candidate_gates_vm_and_ratio() -> None:
    ok = _free_agent(vm=5_000_000, buy=5_000_000)
    _assert(is_cpu_spread_candidate(ok, bid_cap=20_000_000, balance=200_000), "5M ok")
    cheap = _free_agent(vm=1_500_000, buy=1_500_000)
    _assert(
        not is_cpu_spread_candidate(cheap, bid_cap=20_000_000, balance=200_000),
        "1.5M too cheap",
    )
    expensive_ask = _free_agent(vm=5_000_000, buy=5_200_000)
    _assert(
        not is_cpu_spread_candidate(expensive_ask, bid_cap=20_000_000, balance=200_000),
        "ask > VM+1%",
    )
    rival = _free_agent()
    rival["listed_by_rival"] = True
    rival["owner_id"] = "99"
    _assert(not is_cpu_spread_candidate(rival, bid_cap=20_000_000), "rival blocked")


def test_promote_only_when_idle() -> None:
    harvest = _free_agent()
    structural = _free_agent(pid="s1", name="Need", vm=2_000_000, buy=2_000_000, action="buy_now")
    structural["fills_need"] = True
    structural["budget_fit"] = "comfortable"
    structural["target_tier"] = "realistic"
    structural["debt_risk"] = False

    blocked = promote_cpu_spread_harvest(
        [structural, harvest],
        league_rules={"transfer_wait": 0, "max_squad": 25, "sale_limit": 5},
        sales_state={"listed_count": 3, "listed": [{}, {}, {}]},
        me={"balance": 200_000, "max_debt": 25_000_000, "squad": [{}] * 16},
        hours_to_solvency=120.0,
        cycle_hours=12.0,
        has_critical_need=False,
    )
    _assert(
        not any(i.get("cpu_spread_play") for i in blocked),
        "no harvest when structural buy exists",
    )

    idle = promote_cpu_spread_harvest(
        [harvest],
        league_rules={"transfer_wait": 0, "max_squad": 25, "sale_limit": 5},
        sales_state={"listed_count": 5, "listed": [{}, {}, {}, {}, {}]},
        me={"balance": 200_000, "max_debt": 25_000_000, "squad": [{}] * 16},
        hours_to_solvency=120.0,
        cycle_hours=12.0,
        has_critical_need=False,
    )
    hit = [i for i in idle if i.get("cpu_spread_play")]
    _assert(len(hit) == 1, hit)
    _assert(hit[0]["action"] == "buy_now", hit[0])
    _assert(hit[0].get("cpu_spread_list_now") is True, hit[0])


def test_promote_skips_critical_and_strict() -> None:
    harvest = _free_agent()
    out = promote_cpu_spread_harvest(
        [harvest],
        league_rules={"transfer_wait": 0, "max_squad": 25, "sale_limit": 5},
        sales_state={"listed_count": 2},
        me={"balance": 200_000, "max_debt": 25_000_000, "squad": [{}] * 16},
        hours_to_solvency=120.0,
        cycle_hours=12.0,
        has_critical_need=True,
    )
    _assert(not any(i.get("cpu_spread_play") for i in out), "critical blocks")

    out2 = promote_cpu_spread_harvest(
        [harvest],
        league_rules={"transfer_wait": 0, "max_squad": 25, "sale_limit": 5},
        sales_state={"listed_count": 2},
        me={"balance": 200_000, "max_debt": 25_000_000, "squad": [{}] * 16},
        hours_to_solvency=20.0,
        cycle_hours=12.0,
        solvency_strict=True,
    )
    _assert(not any(i.get("cpu_spread_play") for i in out2), "strict blocks")


def test_gk_tandem_does_not_block_harvest() -> None:
    # Titular usable + alerta tándem → no tumba harvest
    _assert(
        not harvest_blocks_on_critical_need(
            critical_pos={"GK"},
            need_pos_alta={"GK"},
            structural_needs=[
                {"need": "gk_tandem", "position": "GK", "priority": "Alta"}
            ],
            diagnostico_plantilla={"lineas": {"GK": {"starters_real": 1}}},
        ),
        "gk tandem soft",
    )
    # Sin titular GK → sí bloquea
    _assert(
        harvest_blocks_on_critical_need(
            critical_pos={"GK"},
            need_pos_alta={"GK"},
            structural_needs=[
                {"need": "gk_backup", "position": "GK", "priority": "Alta"}
            ],
            diagnostico_plantilla={"lineas": {"GK": {"starters_real": 0}}},
        ),
        "no starter blocks",
    )
    # FW Alta real sigue bloqueando aunque GK sea soft
    _assert(
        harvest_blocks_on_critical_need(
            critical_pos=set(),
            need_pos_alta={"GK", "FW"},
            structural_needs=[
                {"need": "gk_tandem", "position": "GK", "priority": "Alta"},
                {"need": "fw_starters", "position": "FW", "priority": "Alta"},
            ],
            diagnostico_plantilla={"lineas": {"GK": {"starters_real": 1}}},
        ),
        "fw still blocks",
    )


def test_harvest_uses_ceiling_size_but_residual_for_new() -> None:
    # Ticket 5M cabe en techo 28M (fracción), pero residual 1.4M no permite abrir
    harvest = _free_agent(vm=5_000_000, buy=5_000_000)
    out = promote_cpu_spread_harvest(
        [harvest],
        league_rules={"transfer_wait": 0, "max_squad": 25, "sale_limit": 5},
        sales_state={"listed_count": 2},
        me={
            "balance": 250_000,
            "max_debt": 1_400_000,
            "max_debt_remaining": 1_400_000,
            "bid_cap_ceiling": 28_000_000,
            "squad": [{}] * 16,
        },
        hours_to_solvency=120.0,
        cycle_hours=12.0,
        has_critical_need=False,
    )
    _assert(not any(i.get("cpu_spread_play") for i in out), "residual blocks new")

    # Con holgura residual suficiente sí promueve (tamaño vs techo)
    out2 = promote_cpu_spread_harvest(
        [harvest],
        league_rules={"transfer_wait": 0, "max_squad": 25, "sale_limit": 5},
        sales_state={"listed_count": 2},
        me={
            "balance": 250_000,
            "max_debt": 10_000_000,
            "max_debt_remaining": 10_000_000,
            "bid_cap_ceiling": 28_000_000,
            "squad": [{}] * 16,
        },
        hours_to_solvency=120.0,
        cycle_hours=12.0,
        has_critical_need=False,
    )
    _assert(any(i.get("cpu_spread_play") for i in out2), "ceiling allows size")


def test_promote_below_appreciation() -> None:
    """Appreciation gana: si ya hay appreciation_play buy, no harvest."""
    harvest = _free_agent()
    appr = _free_agent(pid="a1", name="Rising", vm=3_000_000, buy=3_000_000, action="buy_now")
    appr["appreciation_play"] = True
    appr["budget_fit"] = "comfortable"
    appr["target_tier"] = "realistic"
    appr["debt_risk"] = False
    out = promote_cpu_spread_harvest(
        [appr, harvest],
        league_rules={"transfer_wait": 0, "max_squad": 25, "sale_limit": 5},
        sales_state={"listed_count": 1},
        me={"balance": 5_000_000, "max_debt": 25_000_000, "squad": [{}] * 16},
        hours_to_solvency=120.0,
        cycle_hours=12.0,
    )
    _assert(not any(i.get("cpu_spread_play") for i in out), out)


def test_finalize_queue_role_cpu_spread() -> None:
    item = _free_agent(action="buy_now")
    item["cpu_spread_play"] = True
    item["cpu_spread_list_now"] = True
    item["on_daily_market"] = True
    plan, _pkg = finalize_action_plan(
        [item],
        balance=200_000,
        funding_info={"cash_lag_hours": 12, "cycle_hours": 12},
        market_mode="auction",
        squad_size=16,
        max_squad=25,
    )
    buys = [i for i in plan if i.get("cpu_spread_play")]
    _assert(buys, plan)
    _assert(buys[0].get("queue_role") == "cpu_spread", buys[0].get("queue_role"))
    _assert("Harvest CPU" in str(buys[0].get("package_note") or ""), buys[0].get("package_note"))


def test_cycle_impatient_accept_when_wait() -> None:
    squad = [
        {
            "player_id": "p1",
            "name": "Bench",
            "position": "MF",
            "price": 5_000_000,
            "lineup_pct": 10,
        }
    ]
    sales = {
        "listed_ids": ["p1"],
        "pending_offers": [
            {
                "player_id": "p1",
                "name": "Bench",
                "amount": 5_000_000,
                "market_value": 5_000_000,
                "pct_of_vm": 1.0,
                "from_machine": True,
            }
        ],
        "mister_offers_url": "https://mister.example/offers",
    }
    plan = build_cycle_plan(
        me={"balance": 100_000, "max_debt": 20_000_000, "squad": squad},
        squad=squad,
        opportunities=[],
        sales_state=sales,
        league_rules={"max_squad": 25, "sale_limit": 5, "transfer_wait": 1},
        recommended_xi={"xi": []},
        hours_to_solvency_deadline=80.0,
        market_cycle={"cycle_hours": 8, "hours_to_end": 4, "cash_lag_hours": 8},
        solvency_target="siguiente",
    )
    accepts = [m for m in plan["moves"] if m["kind"] == KIND_ACCEPT]
    _assert(accepts, plan["moves"])
    _assert(
        any(m.get("accept_reason") == "cpu_spread_impatient" for m in accepts),
        accepts,
    )


def test_cycle_bid_cpu_spread_with_debt() -> None:
    market = [
        {
            "player_id": "h1",
            "name": "Harvest",
            "position": "MF",
            "on_daily_market": True,
            "seller": "market",
            "market_value": 5_000_000,
            "price": 5_000_000,
            "bid": 5_000_000,
            "puja_recomendada": 5_000_000,
            "cpu_spread_play": True,
            "debt_risk": True,
            "budget_fit": "stretch",
            "delta_5d": 0.0,
        }
    ]
    plan = build_cycle_plan(
        me={"balance": 100_000, "max_debt": 20_000_000, "squad": [{"player_id": "x"}] * 16},
        squad=[{"player_id": f"x{i}", "name": f"P{i}", "position": "MF", "price": 1_000_000} for i in range(16)],
        opportunities=market,
        sales_state={"listed_ids": [], "pending_offers": []},
        league_rules={"max_squad": 25, "sale_limit": 5, "transfer_wait": 0},
        recommended_xi={"xi": []},
        hours_to_solvency_deadline=100.0,
        market_cycle={"cycle_hours": 12, "hours_to_end": 6, "cash_lag_hours": 12},
        solvency_target="siguiente",
    )
    bids = [m for m in plan["moves"] if m["kind"] == KIND_BID]
    _assert(any(m.get("cpu_spread_play") for m in bids), plan["moves"])
    _assert(any(m.get("cpu_spread_list_now") for m in bids), bids)


def test_appreciation_still_first() -> None:
    """Smoke: promote_appreciation no rompe con harvest en el plan."""
    plan = [
        {
            "player_id": "r1",
            "name": "Riser",
            "position": "FW",
            "action": "wait",
            "on_daily_market": True,
            "seller": "market",
            "price": 2_000_000,
            "market_value": 2_000_000,
            "bid": 2_000_000,
            "budget_fit": "comfortable",
            "rising": True,
            "decelerating": False,
            "delta_5d": 0.08,
            "delta_cycle": 0.03,
            "consecutive_up": 3,
            "lineup_pct": 80,
            "abs_gain": 150_000,
            "priority_score": 20,
        }
    ]
    out = promote_appreciation_plays(plan)
    _assert(isinstance(out, list), out)


if __name__ == "__main__":
    tests = [
        test_transfer_wait_normalize,
        test_solvency_hours_profiles,
        test_candidate_gates_vm_and_ratio,
        test_promote_only_when_idle,
        test_promote_skips_critical_and_strict,
        test_gk_tandem_does_not_block_harvest,
        test_harvest_uses_ceiling_size_but_residual_for_new,
        test_promote_below_appreciation,
        test_finalize_queue_role_cpu_spread,
        test_cycle_impatient_accept_when_wait,
        test_cycle_bid_cpu_spread_with_debt,
        test_appreciation_still_first,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"OK {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    if failed:
        raise SystemExit(1)
    print(f"all {len(tests)} passed")
