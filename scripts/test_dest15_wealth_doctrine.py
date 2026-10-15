"""Regresión: destino 15 respeta ROI/prima, wealth burn y rank (conservar arriba)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from destination_15 import (  # noqa: E402
    _clause_dest_ok,
    _sell_priority,
    rotation_finance,
)


def _assert(cond: bool, msg: str = "") -> None:
    if not cond:
        raise AssertionError(msg or "assertion failed")


def test_sell_priority_falling_first() -> None:
    falling = {"name": "A", "value_note": "falling", "delta_5d": -0.1, "price": 100}
    rising = {"name": "B", "value_note": "rising", "delta_5d": 0.1, "price": 5000}
    stable = {"name": "C", "value_note": "stable", "delta_5d": 0.0, "price": 1000}
    ordered = sorted([rising, stable, falling], key=_sell_priority)
    _assert([p["name"] for p in ordered] == ["A", "C", "B"], ordered)


def test_clause_dest_rejects_luxury_overpay() -> None:
    # Mangas-like: 2x VM, no tapa hueco
    cand = {"reach": "clause", "clause": 5_742_000, "price": 2_871_000, "xpts": 5.0}
    _assert(
        _clause_dest_ok(
            cand, fills_gap=False, week_c=5.0, week_i=4.5, rank=1
        )
        is False
    )


def test_clause_dest_allows_gap_with_modest_premium() -> None:
    cand = {"reach": "clause", "clause": 1_100_000, "price": 1_000_000, "xpts": 8.0}
    _assert(
        _clause_dest_ok(
            cand, fills_gap=True, week_c=8.0, week_i=1.0, rank=8
        )
        is True
    )


def test_clause_dest_leader_blocks_luxury_even_soft_premium() -> None:
    # Prima suave (1.1×) pero rank 1 sin tapa-hueco → conservar
    cand = {"reach": "clause", "clause": 1_100_000, "price": 1_000_000, "xpts": 7.0}
    _assert(
        _clause_dest_ok(
            cand, fills_gap=False, week_c=7.0, week_i=5.0, rank=1
        )
        is False
    )
    # Misma ficha remontando (rank 8) sí puede si ROI/prima OK
    _assert(
        _clause_dest_ok(
            cand, fills_gap=False, week_c=7.0, week_i=5.0, rank=8
        )
        is True
    )


def test_clause_dest_wealth_burn_cap() -> None:
    # burn 3M > 2.5M sin fills
    cand = {"reach": "clause", "clause": 4_000_000, "price": 1_000_000, "xpts": 9.0}
    _assert(
        _clause_dest_ok(
            cand, fills_gap=False, week_c=9.0, week_i=6.0, rank=None
        )
        is False
    )


def test_rotation_finance_orders_sells() -> None:
    dest = [{"player_id": "keep1", "owned": True, "reach": "keep", "price": 1}]
    owned = {
        "keep1": {"player_id": "keep1", "price": 1, "value_note": "stable"},
        "rise": {
            "player_id": "rise",
            "price": 2_000_000,
            "value_note": "rising",
            "delta_5d": 0.1,
            "xpts": 1,
        },
        "fall": {
            "player_id": "fall",
            "price": 200_000,
            "value_note": "falling",
            "delta_5d": -0.2,
            "xpts": 0.5,
        },
    }
    fin = rotation_finance(
        dest,
        owned_ids={"keep1", "rise", "fall"},
        owned_by_id=owned,
        balance=5_000_000,
        max_debt=20_000_000,
        settle_ok=True,
        sale_remaining=5,
    )
    _assert(fin.get("ok") is True, fin)
    sells = fin.get("sells") or []
    _assert([s["player_id"] for s in sells] == ["fall", "rise"], sells)


def main() -> int:
    test_sell_priority_falling_first()
    test_clause_dest_rejects_luxury_overpay()
    test_clause_dest_allows_gap_with_modest_premium()
    test_clause_dest_leader_blocks_luxury_even_soft_premium()
    test_clause_dest_wealth_burn_cap()
    test_rotation_finance_orders_sells()
    print("OK dest15 wealth doctrine")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
