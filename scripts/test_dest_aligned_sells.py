"""Ventas diarias alineadas al 15 al KO: keep destino ≠ parking/liquidity sell."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from competitive_actions import (  # noqa: E402
    build_sell_opportunities,
    destination_board_ids,
    resolve_liquidity_slots,
)


def _assert(cond: bool, msg: object = "") -> None:
    if not cond:
        raise AssertionError(msg or "assertion failed")


def _p(
    pid: str,
    name: str,
    pos: str,
    *,
    price: float,
    lp: float = 0.9,
    in_lineup: bool = False,
    prod: float = 40,
    **extra: object,
) -> dict:
    row = {
        "id": pid,
        "name": name,
        "position": pos,
        "price": price,
        "market_value": price,
        "lineup_prob": lp,
        "gw_starter": lp >= 0.7,
        "in_lineup": in_lineup,
        "production_score": prod,
        "ff_mister_avg": 6.0 if lp >= 0.7 else 4.0,
        "ff_mister_points": 80,
    }
    row.update(extra)
    return row


def test_destination_board_ids() -> None:
    board = {
        "destination_15": [
            {"player_id": "keep1", "status": "keep", "name": "K"},
            {"player_id": "buy1", "status": "buy", "name": "B"},
            {"player_id": "flex", "status": "flex", "name": ""},
        ],
        "moves": {
            "sell": [{"player_id": "sell1", "name": "S"}],
            "keep": [{"player_id": "keep1"}],
        },
        "path": [{"kind": "list", "player_id": "sell2"}],
    }
    keep, sell = destination_board_ids(board)
    _assert("keep1" in keep, keep)
    _assert("buy1" not in keep, keep)
    _assert("sell1" in sell and "sell2" in sell, sell)
    _assert("sell1" not in keep, keep)


def test_dest_keep_not_expensive_bench_sell() -> None:
    """Politano-like: keep en el 15 + lineup swap → no sell parking."""
    politano = _p(
        "8887",
        "Politano",
        "MF",
        price=10_800_000,
        lp=0.5,
        in_lineup=True,
        prod=35,
    )
    basic = _p("b1", "Basic", "MF", price=300_000, lp=1.0, in_lineup=False, prod=60)
    hainaut = _p("m1", "Hainaut", "MF", price=2_000_000, lp=1.0, in_lineup=True, prod=70)
    romano = _p("m2", "Romano", "MF", price=1_500_000, lp=1.0, in_lineup=True, prod=65)
    me = {
        "balance": 2_000_000,
        "rank": 1,
        "squad": [politano, basic, hainaut, romano],
    }
    diagnosis = {
        "alerts": [],
        "by_position": {
            "GK": {"status": "ok"},
            "DF": {"status": "ok"},
            "MF": {"status": "ok"},
            "FW": {"status": "ok"},
        },
    }
    diag_plantilla = {
        "financiero": {
            "bench_inflated": {
                "ok": False,
                "status": "alert",
                "players": [{"id": "8887", "name": "Politano"}],
            }
        },
        "structural_needs": [],
    }
    board = {
        "destination_15": [
            {"player_id": "8887", "status": "keep", "role": "bench", "name": "Politano"},
            {"player_id": "m1", "status": "keep", "role": "starter"},
            {"player_id": "m2", "status": "keep", "role": "starter"},
            {"player_id": "b1", "status": "keep", "role": "starter"},
        ],
        "moves": {"sell": [], "keep": [{"player_id": "8887"}], "buy": []},
        "path": [],
    }
    sells = build_sell_opportunities(
        me,
        diagnosis,
        rivals=[],
        diagnostico_plantilla=diag_plantilla,
        target_board=board,
        recommended_xi={
            "xi": [
                {"player_id": "8887"},
                {"player_id": "m1"},
                {"player_id": "m2"},
            ]
        },
        market_mode="auction",
        league_economy={"sale_limit": 5},
    )
    pol_sells = [
        s
        for s in sells
        if s.get("action") == "sell" and str(s.get("player_id")) == "8887"
    ]
    _assert(pol_sells == [], pol_sells)
    swaps = [s for s in sells if s.get("action") == "lineup"]
    _assert(any(s.get("player_id") == "b1" for s in swaps), swaps)


def test_dest_keep_not_liquidity_slot_for_star() -> None:
    """Lautaro keep titular en el 15 no financia un chase de mercado."""
    star = _p(
        "14028",
        "Martinez",
        "FW",
        price=22_000_000,
        lp=0.9,
        in_lineup=False,
        prod=55,
    )
    other = _p("f2", "Osmajic", "FW", price=3_000_000, lp=1.0, in_lineup=True, prod=60)
    weak = _p("f3", "BenchFW", "FW", price=2_500_000, lp=0.2, in_lineup=False, prod=15)
    me = {"balance": 2_000_000, "rank": 1, "squad": [star, other, weak]}
    board = {
        "destination_15": [
            {"player_id": "14028", "status": "keep", "role": "starter"},
            {"player_id": "f2", "status": "keep", "role": "starter"},
        ],
        "moves": {
            "sell": [{"player_id": "f3", "name": "BenchFW", "price": 2_500_000}],
            "buy": [],
        },
        "primary_targets": [
            {
                "id": "mancini",
                "name": "Mancini",
                "position": "DF",
                "price": 12_000_000,
                "on_daily_market": True,
                "ep_score": 8,
                "is_upgrade": True,
            }
        ],
    }
    market = [
        {
            "id": "mancini",
            "name": "Mancini",
            "position": "DF",
            "price": 12_000_000,
            "on_daily_market": True,
            "is_upgrade": True,
            "ep_score": 8,
            "puja_recomendada": 12_000_000,
        }
    ]
    liq = resolve_liquidity_slots(
        squad=me["squad"],
        recommended_xi={"xi": [{"player_id": "f2"}]},
        market_opportunities=market,
        target_board=board,
        balance=2_000_000,
        sale_limit=5,
        market_mode="auction",
    )
    slot_ids = {str(p.get("id")) for p in liq.get("slots") or []}
    _assert("14028" not in slot_ids, slot_ids)

    sells = build_sell_opportunities(
        me,
        {
            "alerts": [],
            "by_position": {
                "GK": {"status": "ok"},
                "DF": {"status": "warning"},
                "MF": {"status": "ok"},
                "FW": {"status": "ok"},
            },
        },
        rivals=[],
        target_board=board,
        market_opportunities=market,
        recommended_xi={"xi": [{"player_id": "f2"}]},
        market_mode="auction",
        league_economy={"sale_limit": 5},
        funding_info={
            "cash_tight": True,
            "funding_shortfall": 10_000_000,
            "funding_target": 12_000_000,
            "positions": ["DF"],
            "primary_targets": board["primary_targets"],
        },
    )
    star_sells = [
        s
        for s in sells
        if s.get("action") == "sell" and str(s.get("player_id")) == "14028"
    ]
    _assert(star_sells == [], star_sells)


def main() -> int:
    test_destination_board_ids()
    test_dest_keep_not_expensive_bench_sell()
    test_dest_keep_not_liquidity_slot_for_star()
    print("OK dest aligned sells")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
