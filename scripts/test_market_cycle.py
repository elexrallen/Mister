"""Tests de cadencia de mercado y modo bootstrap de once."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from competitive_actions import sell_cash_phrase, sell_settlement_fields  # noqa: E402
from market_cycle import (  # noqa: E402
    adjust_funding_for_bootstrap,
    bootstrap_buy_cap,
    derive_cycle_hours,
    resolve_bootstrap_xi,
    resolve_market_cycle,
    xi_gap_positions,
)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_derive_cycle_hours() -> None:
    assert derive_cycle_hours({"market_speed": 1}) == 24.0
    assert derive_cycle_hours({"market_speed": 2}) == 12.0
    assert derive_cycle_hours({"market_speed": 3}, market_mode="fixed") == 8.0


def test_bootstrap_active_short_squad() -> None:
    squad = [
        {"id": "1", "position": "GK"},
        {"id": "2", "position": "DF"},
        {"id": "3", "position": "MF"},
    ]
    mc = resolve_market_cycle(
        {"market_speed": 3, "market_mode": "fixed"},
        market_mode="fixed",
        hours_to_jornada=72.0,
    )
    b = resolve_bootstrap_xi(
        squad=squad,
        xi_summary={"complete": False, "xi_count": 3, "xi_target": 11},
        hours_to_jornada=72.0,
        market_cycle=mc,
        competition_phase="active",
    )
    _assert(b["active"], "bootstrap debe activarse con plantilla corta")
    _assert(b["position_gaps"]["FW"] >= 1, "debe faltar delantero")
    _assert(b["slots_short"] >= 7, "faltan muchos huecos")
    _assert(b["posture"] == "buy_now", "sin once legal no se espera al siguiente ciclo")


def test_bootstrap_inactive_full_xi() -> None:
    squad = [{"id": str(i), "position": p} for i, p in enumerate(
        ["GK"] + ["DF"] * 4 + ["MF"] * 4 + ["FW"] * 2
    )]
    b = resolve_bootstrap_xi(
        squad=squad,
        xi_summary={"complete": True, "xi_count": 11, "xi_target": 11},
        hours_to_jornada=48.0,
        market_cycle={"cycles_left_before_gw": 6, "hours_to_end": 4.0},
        competition_phase="active",
    )
    _assert(not b["active"], "once completo no debe activar bootstrap")


def test_adjust_funding_lowers_reserve() -> None:
    funding = {
        "cash_reserved": 25_000_000,
        "funding_target": 25_000_000,
        "funding_shortfall": 0,
    }
    bootstrap = {
        "active": True,
        "position_gaps": {"FW": 1, "DF": 2},
    }
    opps = [
        {
            "id": "99",
            "name": "Parche",
            "position": "FW",
            "price": 1_500_000,
            "on_daily_market": True,
            "external": {"lineup_prob_ext": 70},
        }
    ]
    out = adjust_funding_for_bootstrap(
        funding, bootstrap=bootstrap, balance=20_000_000, opportunities=opps
    )
    _assert(out["cash_reserved"] == 0, "reserva debe ser 0 en bootstrap")
    _assert(out.get("bootstrap_xi"), "flag bootstrap en funding")


def test_bootstrap_buy_cap() -> None:
    cap = bootstrap_buy_cap(
        free_slots=16,
        bootstrap={"active": True, "slots_short": 7},
        fixed=True,
    )
    _assert(cap >= 5, "bootstrap amplía buy_cap en fixed")
    cap_norm = bootstrap_buy_cap(free_slots=16, bootstrap=None, fixed=True)
    _assert(cap_norm == 8, f"sin bootstrap tope=min(slots,8) got {cap_norm}")
    cap_tight = bootstrap_buy_cap(free_slots=3, bootstrap=None, fixed=False)
    _assert(cap_tight == 3, cap_tight)


def test_sell_copy_uses_league_cycle() -> None:
    phrase = sell_cash_phrase(1_000_000, cycle_hours=8)
    _assert("16h" in phrase, phrase)
    _assert("48h" not in phrase, phrase)
    fields = sell_settlement_fields(1_000_000, cycle_hours=8)
    _assert(float(fields["cash_lag_hours"]) == 16.0, fields)
    deferred = sell_cash_phrase(1_000_000, deferred=True, cycle_hours=8)
    _assert("16h" in deferred, deferred)


if __name__ == "__main__":
    test_derive_cycle_hours()
    test_bootstrap_active_short_squad()
    test_bootstrap_inactive_full_xi()
    test_adjust_funding_lowers_reserve()
    test_bootstrap_buy_cap()
    test_sell_copy_uses_league_cycle()
    print("test_market_cycle: OK")
