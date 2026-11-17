"""Regresión: no listar jugadores <1M salvo plaza, muerto o funding material."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
from competitive_actions import (  # noqa: E402
    build_sell_opportunities,
    micro_sell_allowed,
    resolve_liquidity_slots,
)
from destination_15 import build_path  # noqa: E402


def _assert(cond: bool, msg: str = "") -> None:
    if not cond:
        raise AssertionError(msg or "assertion failed")


def test_micro_sell_gate() -> None:
    _assert(micro_sell_allowed(1_500_000) is True)
    _assert(micro_sell_allowed(160_000) is False)
    _assert(micro_sell_allowed(160_000, need_slot=True) is True)
    _assert(micro_sell_allowed(160_000, reason="free_slot") is True)
    # Por debajo de 500k el VM no baja y la caja no es margen:
    # ni la baja ni un hueco pequeño justifican listarlos.
    _assert(micro_sell_allowed(200_000, dead=True) is False)
    _assert(micro_sell_allowed(200_000, reason="injured_covered") is False)
    _assert(micro_sell_allowed(200_000, funding_shortfall=5_000_000) is False)
    _assert(micro_sell_allowed(200_000, funding_shortfall=700_000) is False)
    # 600k sí cubre 25% de un hueco de 700k (por encima del suelo de 500k)
    _assert(micro_sell_allowed(600_000, funding_shortfall=700_000) is True)
    _assert(micro_sell_allowed(600_000, dead=True) is True)


def test_liquidity_padding_skips_micro() -> None:
    squad = [
        {
            "id": "xi1",
            "name": "Starter",
            "position": "MF",
            "price": 8_000_000,
            "lineup_prob": 0.9,
            "in_lineup": True,
        },
        {
            "id": "micro1",
            "name": "Lucchesi",
            "position": "DF",
            "price": 160_000,
            "lineup_prob": 0.3,
            "in_lineup": False,
            "production_score": 10,
        },
        {
            "id": "fat1",
            "name": "BenchFat",
            "position": "MF",
            "price": 3_500_000,
            "lineup_prob": 0.2,
            "in_lineup": False,
            "production_score": 20,
        },
    ]
    xi = {"xi": [{"player_id": "xi1"}]}
    liq = resolve_liquidity_slots(
        squad=squad,
        recommended_xi=xi,
        market_opportunities=[],
        target_board=None,
        balance=5_000_000,
        sale_limit=5,
        market_mode="auction",
    )
    ids = {str(p.get("id")) for p in liq.get("slots") or []}
    _assert("micro1" not in ids, ids)
    _assert("fat1" in ids, ids)


def test_build_sells_drops_surplus_micro() -> None:
    me = {
        "balance": 5_000_000,
        "rank": 5,
        "squad": [
            {
                "id": "d1",
                "name": "DF Good",
                "position": "DF",
                "price": 2_000_000,
                "lineup_prob": 0.85,
                "in_lineup": True,
                "ff_mister_avg": 6.0,
                "production_score": 55,
            },
            {
                "id": "d2",
                "name": "DF2",
                "position": "DF",
                "price": 1_800_000,
                "lineup_prob": 0.8,
                "in_lineup": True,
                "ff_mister_avg": 5.5,
                "production_score": 50,
            },
            {
                "id": "d3",
                "name": "DF3",
                "position": "DF",
                "price": 1_500_000,
                "lineup_prob": 0.75,
                "in_lineup": False,
                "ff_mister_avg": 5.0,
                "production_score": 48,
            },
            {
                "id": "d4",
                "name": "DF4",
                "position": "DF",
                "price": 1_200_000,
                "lineup_prob": 0.7,
                "in_lineup": False,
                "ff_mister_avg": 4.5,
                "production_score": 45,
            },
            {
                "id": "micro",
                "name": "Fortini",
                "position": "DF",
                "price": 270_000,
                "lineup_prob": 0.4,
                "in_lineup": False,
                "ff_mister_avg": 3.0,
                "production_score": 25,
            },
        ],
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
    rivals = [
        {
            "rank": 2,
            "team_name": "Rival",
            "gaps": [{"position": "DF", "severity": "thin"}],
        }
    ]
    sells = build_sell_opportunities(
        me,
        diagnosis,
        rivals,
        market_mode="auction",
        recommended_xi={"xi": [{"player_id": "d1"}, {"player_id": "d2"}]},
        league_economy={"sale_limit": 5},
    )
    micro_sells = [
        s
        for s in sells
        if s.get("action") == "sell" and str(s.get("player_id")) == "micro"
    ]
    _assert(micro_sells == [], micro_sells)


def test_dest_path_skips_micro_unless_needed() -> None:
    dest = [
        {
            "player_id": "keep1",
            "name": "Keep",
            "position": "MF",
            "owned": True,
            "reach": "keep",
            "xpts": 5,
        }
    ]
    finance = {
        "ok": True,
        "cost": 0,
        "proceeds": 430_000,
        "peak": 0,
        "sells": [
            {
                "player_id": "micro1",
                "name": "MicroA",
                "position": "DF",
                "price": 160_000,
                "value_note": "falling",
            },
            {
                "player_id": "micro2",
                "name": "MicroB",
                "position": "MF",
                "price": 270_000,
                "value_note": "falling",
            },
        ],
    }
    path = build_path(
        dest,
        owned_ids={"keep1", "micro1", "micro2"},
        finance=finance,
        k_future=3,
        settle_ok=True,
        balance=3_000_000,
    )
    lists = [p for p in path if p.get("kind") == "list"]
    _assert(lists == [], lists)

    # Con coste que exige micros
    finance2 = {
        **finance,
        "cost": 3_200_000,
        "peak": 200_000,
        "sells": finance["sells"]
        + [
            {
                "player_id": "fat",
                "name": "Fat",
                "position": "FW",
                "price": 2_000_000,
                "value_note": "falling",
            }
        ],
    }
    path2 = build_path(
        dest,
        owned_ids={"keep1", "micro1", "micro2", "fat"},
        finance=finance2,
        k_future=3,
        settle_ok=True,
        balance=1_000_000,
    )
    list_ids = [p.get("player_id") for p in path2 if p.get("kind") == "list"]
    _assert("fat" in list_ids, list_ids)
    # Faltan 0.2M, pero 160k/270k no bajan y no son margen: no se listan.
    _assert("micro1" not in list_ids and "micro2" not in list_ids, list_ids)

    finance3 = {
        **finance2,
        "cost": 3_700_000,
        "sells": finance2["sells"]
        + [
            {
                "player_id": "mid",
                "name": "Mid",
                "position": "MF",
                "price": 700_000,
                "value_note": "falling",
            }
        ],
    }
    path3 = build_path(
        dest,
        owned_ids={"keep1", "micro1", "micro2", "fat", "mid"},
        finance=finance3,
        k_future=3,
        settle_ok=True,
        balance=1_000_000,
    )
    list_ids3 = [p.get("player_id") for p in path3 if p.get("kind") == "list"]
    _assert("fat" in list_ids3 and "mid" in list_ids3, list_ids3)
    _assert("micro1" not in list_ids3 and "micro2" not in list_ids3, list_ids3)


def main() -> int:
    _assert(float(config.SELL_MICRO_VM_MAX) == 1_000_000)
    _assert(float(config.SELL_NO_DROP_VM) == 500_000)
    test_micro_sell_gate()
    test_liquidity_padding_skips_micro()
    test_build_sells_drops_surplus_micro()
    test_dest_path_skips_micro_unless_needed()
    print("OK micro sell policy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
