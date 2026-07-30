"""Validación del paquete multi-línea con hedges (auction)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from competitive_actions import finalize_action_plan  # noqa: E402


def _finalize(plan, *, balance, market_mode="auction", squad_size=20, max_squad=25):
    """Default: plazas de sobra para no interferir con tests de caja/hedge."""
    return finalize_action_plan(
        plan,
        balance=balance,
        market_mode=market_mode,
        squad_size=squad_size,
        max_squad=max_squad,
    )


def _buy(
    pid: str,
    name: str,
    pos: str,
    cost: float,
    *,
    wait_risk: str = "low",
    is_key: bool = False,
    fills_need: bool = True,
    fills_structural: bool = False,
    priority: int = 50,
    puja_minima: float | None = None,
) -> dict:
    floor = puja_minima if puja_minima is not None else cost * 0.7
    return {
        "player_id": pid,
        "name": name,
        "position": pos,
        "action": "buy_now",
        "bid": cost,
        "cost": cost,
        "price": floor,
        "puja_minima": floor,
        "min_bid": floor,
        "on_daily_market": True,
        "seller": "market",
        "wait_risk": wait_risk,
        "budget_fit": "comfortable",
        "target_tier": "realistic",
        "is_key_market": is_key,
        "is_primary_target": is_key,
        "fills_need": fills_need,
        "fills_structural": fills_structural,
        "fills_coverage_gap": fills_need,
        "priority_score": priority,
        "production_score": priority,
        "trade_asset_score": 10,
        "why": f"test {name}",
    }


def _roles(plan: list[dict]) -> dict[str, str]:
    return {a["name"]: a.get("queue_role") for a in plan if a.get("name")}


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_2_plus_2_wide_cash() -> None:
    plan = [
        _buy("1", "DF_A", "DF", 5_000_000, wait_risk="high", fills_structural=True, priority=90),
        _buy("2", "DF_B", "DF", 3_000_000, wait_risk="medium", priority=70),
        _buy("3", "FW_A", "FW", 4_000_000, wait_risk="high", fills_structural=True, priority=85),
        _buy("4", "FW_B", "FW", 2_000_000, wait_risk="medium", priority=60),
    ]
    capped, pkg = _finalize(plan, balance=20_000_000, market_mode="auction")
    buys = [a for a in capped if a.get("action") == "buy_now"]
    roles = {a["queue_role"] for a in buys}
    _assert(pkg.get("combo") == "2+2", f"expected 2+2 got {pkg.get('combo')}")
    _assert(len(buys) == 4, f"expected 4 buy_now got {len(buys)}")
    _assert("hedge" in roles, f"missing hedge in {roles}")
    _assert(pkg.get("policy") == "multi_line_risk_hedge", "bad policy")
    _assert(len(pkg.get("lines") or []) == 2, "expected 2 lines")
    _assert(all(L.get("hedge_status") == "bid" for L in pkg["lines"]), "hedges should be bid")


def test_2_plus_0_no_cash_for_hedges() -> None:
    plan = [
        _buy("1", "DF_A", "DF", 5_000_000, wait_risk="high", fills_structural=True, priority=90),
        _buy("2", "DF_B", "DF", 4_000_000, wait_risk="high", priority=70),
        _buy("3", "FW_A", "FW", 1_000_000, wait_risk="high", fills_need=True, priority=80),
        _buy("4", "FW_B", "FW", 900_000, wait_risk="high", priority=60),
    ]
    # Intents 5+1 = 6; hedges ~4+0.9 no caben en residual 0.5
    capped, pkg = _finalize(plan, balance=6_500_000, market_mode="auction")
    buys = [a for a in capped if a.get("action") == "buy_now"]
    hedges = [a for a in buys if a.get("queue_role") == "hedge"]
    unfunded = [a for a in capped if a.get("queue_role") == "alt_unfunded"]
    _assert(pkg.get("combo") == "2+0", f"expected 2+0 got {pkg.get('combo')}")
    _assert(len(hedges) == 0, "no hedge bids")
    _assert(len(unfunded) >= 1, "expected alt_unfunded copy")
    _assert(
        any("probablemente ya no esté" in (a.get("package_note") or "") for a in unfunded),
        "honest unfunded copy missing",
    )


def test_1_plus_1_high_risk() -> None:
    plan = [
        _buy("1", "DF_A", "DF", 6_000_000, wait_risk="high", is_key=True, fills_structural=True, priority=95),
        _buy("2", "DF_B", "DF", 2_000_000, wait_risk="medium", priority=70),
    ]
    capped, pkg = _finalize(plan, balance=12_000_000, market_mode="auction")
    buys = [a for a in capped if a.get("action") == "buy_now"]
    roles = _roles(buys)
    _assert(pkg.get("combo") == "1+1", f"expected 1+1 got {pkg.get('combo')}")
    _assert(roles.get("DF_A") in ("primary", "primary_target"), roles)
    _assert(roles.get("DF_B") == "hedge", roles)


def test_risk_low_no_hedge() -> None:
    plan = [
        _buy("1", "DF_A", "DF", 5_000_000, wait_risk="low", fills_structural=True, priority=90),
        _buy("2", "DF_B", "DF", 3_000_000, wait_risk="low", priority=70),
        _buy("3", "FW_A", "FW", 2_000_000, wait_risk="low", fills_need=True, priority=80),
    ]
    capped, pkg = _finalize(plan, balance=20_000_000, market_mode="auction")
    buys = [a for a in capped if a.get("action") == "buy_now"]
    hedges = [a for a in buys if a.get("queue_role") == "hedge"]
    _assert(pkg.get("combo") == "2+0", f"expected 2+0 got {pkg.get('combo')}")
    _assert(len(hedges) == 0, "low risk should not hedge")
    alts = [a for a in capped if a.get("queue_role") == "alt_if_lost"]
    _assert(len(alts) >= 1, "same-pos low risk stays as alt_if_lost")


def test_fixed_no_hedge() -> None:
    plan = [
        _buy("1", "DF_A", "DF", 5_000_000, wait_risk="high", fills_structural=True, priority=90),
        _buy("2", "DF_B", "DF", 3_000_000, wait_risk="high", priority=70),
        _buy("3", "FW_A", "FW", 2_000_000, wait_risk="high", fills_need=True, priority=80),
    ]
    capped, pkg = _finalize(plan, balance=20_000_000, market_mode="fixed")
    buys = [a for a in capped if a.get("action") == "buy_now"]
    hedges = [a for a in buys if a.get("queue_role") == "hedge"]
    _assert(len(hedges) == 0, "fixed must not hedge")
    _assert(len(buys) <= 2, f"fixed max 2 buy_now got {len(buys)}")
    also = [a for a in capped if a.get("queue_role") == "also_good"]
    _assert(len(also) >= 1, "fixed same-pos should be also_good")
    _assert(pkg.get("combo", "").startswith("2+") or pkg.get("combo", "").startswith("1+"), pkg)


def test_prefer_1_plus_1_over_weak_second() -> None:
    """Clave high-risk: 1+1 gana a un 2º intent débil si no cabe 2+1."""
    plan = [
        _buy(
            "1",
            "DF_KEY",
            "DF",
            7_000_000,
            wait_risk="high",
            is_key=True,
            fills_structural=True,
            priority=99,
        ),
        _buy("2", "DF_H", "DF", 3_000_000, wait_risk="medium", priority=70),
        # 2º intent débil (sin carencia real) — no debería entrar si impide el hedge
        _buy(
            "3",
            "FW_WEAK",
            "FW",
            2_400_000,
            wait_risk="low",
            fills_need=False,
            fills_structural=False,
            priority=40,
        ),
    ]
    # 7+3+2.4 = 12.4 > 11 → no cabe 2+1 a puja llena; con hedge 85% (2.55M):
    # 7+2.55=9.55 cabe 1+1; 7+2.4+2.55=11.95 > 11 → no 2+1
    capped, pkg = _finalize(plan, balance=11_000_000, market_mode="auction")
    names = {a["name"] for a in capped if a.get("action") == "buy_now"}
    _assert(pkg.get("combo") == "1+1", f"expected 1+1 got {pkg.get('combo')} buys={names}")
    _assert("DF_KEY" in names and "DF_H" in names, names)
    _assert("FW_WEAK" not in names, names)


def test_hedge_reduced_bid_and_exit_note() -> None:
    plan = [
        _buy("1", "DF_A", "DF", 6_000_000, wait_risk="high", is_key=True, fills_structural=True, priority=95),
        _buy(
            "2",
            "DF_B",
            "DF",
            2_000_000,
            wait_risk="medium",
            priority=70,
            puja_minima=1_500_000,
        ),
    ]
    capped, pkg = _finalize(plan, balance=12_000_000, market_mode="auction")
    hedge = next(a for a in capped if a.get("queue_role") == "hedge")
    _assert(hedge.get("hedge_bid_discount") is True, "expected discount flag")
    _assert(float(hedge["bid"]) < 2_000_000, f"hedge bid should be reduced: {hedge['bid']}")
    _assert(float(hedge["bid"]) >= 1_500_000, f"hedge bid >= min: {hedge['bid']}")
    _assert("vende el peor" in (hedge.get("package_note") or ""), hedge.get("package_note"))
    hedges = pkg.get("hedges") or []
    _assert(hedges and hedges[0].get("exit_if_both") == "sell_worse_next_cycle", hedges)
    _assert("vende el peor" in (pkg.get("note") or ""), pkg.get("note"))


def test_hedge_never_below_min_bid() -> None:
    """Sin puja_minima explícita, usar price/listado como suelo."""
    plan = [
        _buy("1", "DF_A", "DF", 5_000_000, wait_risk="high", is_key=True, fills_structural=True, priority=95),
        {
            "player_id": "2",
            "name": "DF_B",
            "position": "DF",
            "action": "buy_now",
            "bid": 2_050_000,
            "cost": 2_050_000,
            "price": 2_000_000,
            # sin puja_minima: el suelo debe ser price (2M), no 85% de 2.05M
            "on_daily_market": True,
            "seller": "market",
            "wait_risk": "medium",
            "budget_fit": "comfortable",
            "target_tier": "realistic",
            "fills_need": True,
            "fills_coverage_gap": True,
            "priority_score": 70,
            "production_score": 70,
            "trade_asset_score": 10,
            "why": "test DF_B",
        },
    ]
    capped, _pkg = _finalize(plan, balance=12_000_000, market_mode="auction")
    hedge = next(a for a in capped if a.get("queue_role") == "hedge")
    _assert(float(hedge["bid"]) >= 2_000_000, f"bid {hedge['bid']} < min price 2M")


def test_hedge_missing_floor_no_illegal_discount() -> None:
    """Sin ningún suelo (ni min ni price): no descontar (evita puja ilegal)."""
    from competitive_actions import hedge_bid_amount

    item = {"bid": 2_050_000, "cost": 2_050_000}
    amt = hedge_bid_amount(item)
    _assert(amt == 2_050_000, f"expected no discount without floor, got {amt}")


def test_hedge_floor_round_keeps_min() -> None:
    """Redondeo a 10k no puede dejar la puja bajo el mínimo."""
    from competitive_actions import apply_hedge_pricing, hedge_bid_amount

    item = {
        "bid": 2_007_000,
        "cost": 2_007_000,
        "puja_minima": 2_003_000,
        "price": 2_003_000,
    }
    amt = hedge_bid_amount(item)
    _assert(amt >= 2_003_000, f"hedge_bid_amount {amt} < min")
    apply_hedge_pricing(item)
    _assert(float(item["bid"]) >= 2_003_000, f"apply {item['bid']} < min")


def test_squad_cap_blocks_hedge() -> None:
    """Con 1 plaza libre: intent sí, hedge no (alt_no_slot)."""
    plan = [
        _buy("1", "DF_A", "DF", 5_000_000, wait_risk="high", is_key=True, fills_structural=True, priority=95),
        _buy("2", "DF_B", "DF", 2_000_000, wait_risk="medium", priority=70),
    ]
    capped, pkg = _finalize(
        plan, balance=20_000_000, market_mode="auction", squad_size=24, max_squad=25
    )
    buys = [a for a in capped if a.get("action") == "buy_now"]
    no_slot = [a for a in capped if a.get("queue_role") == "alt_no_slot"]
    _assert(pkg.get("combo") == "1+0", f"expected 1+0 got {pkg.get('combo')}")
    _assert(pkg.get("free_slots") == 1, pkg)
    _assert(len(buys) == 1, buys)
    _assert(len(no_slot) >= 1, "expected alt_no_slot for hedge")
    _assert(any("Sin plaza" in (a.get("package_note") or "") for a in no_slot), no_slot)


def test_squad_full_no_buys() -> None:
    """Plantilla llena: cero buy_now."""
    plan = [
        _buy("1", "DF_A", "DF", 5_000_000, wait_risk="high", is_key=True, fills_structural=True, priority=95),
        _buy("2", "DF_B", "DF", 2_000_000, wait_risk="medium", priority=70),
    ]
    capped, pkg = _finalize(
        plan, balance=20_000_000, market_mode="auction", squad_size=25, max_squad=25
    )
    buys = [a for a in capped if a.get("action") == "buy_now"]
    _assert(pkg.get("combo") == "0+0", f"expected 0+0 got {pkg.get('combo')}")
    _assert(len(buys) == 0, buys)
    _assert(pkg.get("free_slots") == 0, pkg)
    _assert("llena" in (pkg.get("note") or "").lower() or "Cupo" in (pkg.get("note") or ""), pkg.get("note"))


def test_premier_max_22() -> None:
    from config import league_max_squad

    _assert(league_max_squad({"external": "premier", "max_squad": 22}) == 22, "premier")
    _assert(league_max_squad({"external": "laliga", "max_squad": 25}) == 25, "laliga")
    _assert(league_max_squad({"id_competition": 3}) == 22, "premier by id")


def test_squad_full_prioritizes_sells() -> None:
    """Plantilla llena: ventas free_slot en el plan de hoy."""
    plan = [
        _buy("1", "DF_A", "DF", 5_000_000, wait_risk="high", is_key=True, fills_structural=True, priority=95),
        _buy("2", "DF_B", "DF", 2_000_000, wait_risk="medium", priority=70),
        {
            "player_id": "s1",
            "name": "BENCH",
            "position": "MF",
            "action": "sell",
            "sell_reason": "expensive_bench",
            "price": 4_000_000,
            "priority_score": 40,
            "xi_impact": "safe",
            "urgency": "medium",
            "why": "banquillo caro",
        },
    ]
    capped, pkg = _finalize(
        plan, balance=20_000_000, market_mode="auction", squad_size=25, max_squad=25
    )
    free = [a for a in capped if a.get("queue_role") == "free_slot"]
    _assert(len(free) >= 1, free)
    _assert(free[0].get("name") == "BENCH", free)
    _assert((pkg.get("slot_sells") or []), pkg)
    _assert(any("plaza" in (a.get("package_note") or "").lower() for a in free), free)


def main() -> None:
    tests = [
        test_2_plus_2_wide_cash,
        test_2_plus_0_no_cash_for_hedges,
        test_1_plus_1_high_risk,
        test_risk_low_no_hedge,
        test_fixed_no_hedge,
        test_prefer_1_plus_1_over_weak_second,
        test_hedge_reduced_bid_and_exit_note,
        test_hedge_never_below_min_bid,
        test_hedge_missing_floor_no_illegal_discount,
        test_hedge_floor_round_keeps_min,
        test_squad_cap_blocks_hedge,
        test_squad_full_no_buys,
        test_premier_max_22,
        test_squad_full_prioritizes_sells,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"OK  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    if failed:
        raise SystemExit(f"{failed} test(s) failed")
    print(f"All {len(tests)} tests passed.")


if __name__ == "__main__":
    main()
