"""Validación del paquete multi-línea con hedges (auction)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from competitive_actions import finalize_action_plan  # noqa: E402


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
) -> dict:
    return {
        "player_id": pid,
        "name": name,
        "position": pos,
        "action": "buy_now",
        "bid": cost,
        "cost": cost,
        "price": cost,
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
    capped, pkg = finalize_action_plan(plan, balance=20_000_000, market_mode="auction")
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
    capped, pkg = finalize_action_plan(plan, balance=6_500_000, market_mode="auction")
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
    capped, pkg = finalize_action_plan(plan, balance=12_000_000, market_mode="auction")
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
    capped, pkg = finalize_action_plan(plan, balance=20_000_000, market_mode="auction")
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
    capped, pkg = finalize_action_plan(plan, balance=20_000_000, market_mode="fixed")
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
    # 7+3+2.4 = 12.4 > 11 → no cabe 2+1; sí 1+1 (10) y sí 2+0 (9.4)
    # Excepción: preferir 1+1 sobre 2º débil
    capped, pkg = finalize_action_plan(plan, balance=11_000_000, market_mode="auction")
    names = {a["name"] for a in capped if a.get("action") == "buy_now"}
    _assert(pkg.get("combo") == "1+1", f"expected 1+1 got {pkg.get('combo')} buys={names}")
    _assert("DF_KEY" in names and "DF_H" in names, names)
    _assert("FW_WEAK" not in names, names)


def main() -> None:
    tests = [
        test_2_plus_2_wide_cash,
        test_2_plus_0_no_cash_for_hedges,
        test_1_plus_1_high_risk,
        test_risk_low_no_hedge,
        test_fixed_no_hedge,
        test_prefer_1_plus_1_over_weak_second,
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
