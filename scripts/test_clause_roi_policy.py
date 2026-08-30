"""
Regresión: valoración ROI de cláusulas (prima, mutual exclusivity, finance).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from competitive_actions import (  # noqa: E402
    allocate_clause_bids,
    build_rival_upgrade_targets,
    clause_premium_ratio,
    clause_roi,
    clause_roi_gate,
)


def _me(balance: float = 19_000_000) -> dict:
    return {
        "balance": balance,
        "max_debt": balance,
        "balance_future": balance,
        "squad": [
            {
                "id": "gk1",
                "name": "GK Propio",
                "position": "GK",
                "price": 2_000_000,
                "lineup_prob": 0.5,
                "ff_mister_avg": 3.0,
                "production_score": 30,
            },
            {
                "id": "fw1",
                "name": "FW Propio",
                "position": "FW",
                "price": 3_000_000,
                "lineup_prob": 0.55,
                "ff_mister_avg": 4.0,
                "production_score": 35,
            },
        ],
    }


def _diag_gk_fw_thin() -> dict:
    return {
        "by_position": {
            "GK": {"status": "warning", "coverage": "thin"},
            "DF": {"status": "ok", "coverage": "ok"},
            "MF": {"status": "ok", "coverage": "ok"},
            "FW": {"status": "warning", "coverage": "thin"},
        },
        "alerts": [
            {"level": "warning", "position": "GK"},
            {"level": "warning", "position": "FW"},
        ],
    }


def _diag_ok() -> dict:
    return {
        "by_position": {
            "GK": {"status": "ok", "coverage": "ok"},
            "DF": {"status": "ok", "coverage": "ok"},
            "MF": {"status": "ok", "coverage": "ok"},
            "FW": {"status": "ok", "coverage": "ok"},
        },
        "alerts": [],
    }


def _rival_player(
    pid: str,
    name: str,
    pos: str,
    *,
    clause: float,
    vm: float,
    lineup: float = 0.9,
    ff: float = 7.0,
    prod: float = 70,
) -> dict:
    return {
        "id": pid,
        "name": name,
        "position": pos,
        "price": vm,
        "market_value": vm,
        "clause": clause,
        "clause_known": True,
        "lineup_prob": lineup,
        "ff_mister_avg": ff,
        "production_score": prod,
        "is_top_ff": True,
        "fotmob_stats": {"rating_promedio": 7.4},
    }


def test_clause_roi_helpers() -> None:
    assert abs(clause_premium_ratio(12_500_000, 10_000_000) - 1.25) < 1e-6
    assert abs(clause_roi(50, 10_000_000) - 5.0) < 1e-6
    ok, why = clause_roi_gate(
        upgrade_score=60,
        clause=10_000_000,
        market_value=9_000_000,
        fills=True,
    )
    assert ok is True, why
    bad, why2 = clause_roi_gate(
        upgrade_score=40,
        clause=12_000_000,
        market_value=6_000_000,
        fills=False,
    )
    assert bad is False, why2
    assert "prima" in (why2 or "").lower() or "roi" in (why2 or "").lower()


def test_allocate_keeps_best_roi_only() -> None:
    """3 cláusulas tight que suman > saldo → solo la de mejor ROI queda clause_bid."""
    items = [
        {
            "player_id": "a",
            "name": "A",
            "action": "clause_bid",
            "clause": 10_000_000,
            "bid": 10_000_000,
            "market_value": 7_000_000,
            "upgrade_score": 50,
            "clause_roi": 5.0,
            "why": "mejora A",
            "budget_fit": "tight",
            "fills_need": True,
        },
        {
            "player_id": "b",
            "name": "B Best",
            "action": "clause_bid",
            "clause": 9_000_000,
            "bid": 9_000_000,
            "market_value": 7_000_000,
            "upgrade_score": 72,
            "clause_roi": 8.0,
            "why": "mejora B",
            "budget_fit": "tight",
            "fills_need": True,
        },
        {
            "player_id": "c",
            "name": "C",
            "action": "clause_bid",
            "clause": 11_000_000,
            "bid": 11_000_000,
            "market_value": 7_500_000,
            "upgrade_score": 55,
            "clause_roi": 5.0,
            "why": "mejora C",
            "budget_fit": "tight",
            "fills_need": True,
        },
    ]
    out = allocate_clause_bids(items, 19_000_000, market_reserved=0)
    clause = [x for x in out if x["action"] == "clause_bid"]
    scout = [x for x in out if x["action"] == "scout"]
    assert len(clause) == 1, [(x["name"], x["action"]) for x in out]
    assert clause[0]["name"] == "B Best", clause[0]
    assert len(scout) == 2
    assert any("mejor cláusula" in (s.get("why") or "") or "comprometida" in (s.get("why") or "") for s in scout)


def test_premium_high_without_need_is_scout() -> None:
    me = _me(25_000_000)
    rivals = [
        {
            "team_name": "Rival",
            "rank": 2,
            "team_id": "r1",
            "key_players": [],
            "squad": [
                _rival_player(
                    "lux",
                    "Lujo MF",
                    "MF",
                    clause=15_000_000,
                    vm=8_000_000,
                    lineup=0.85,
                    ff=6.5,
                    prod=60,
                )
            ],
        }
    ]
    # Sin carencia MF → prima 1.875× debe ir a scout
    out = build_rival_upgrade_targets(
        me,
        _diag_ok(),
        rivals,
        balance=25_000_000,
        points_phase="preseason",
        market_reserved=0,
    )
    lux = next((x for x in out if x.get("player_id") == "lux"), None)
    assert lux is not None, out
    assert lux["action"] == "scout", lux
    assert "cara" in (lux.get("why") or "").lower() or "prima" in (lux.get("why") or "").lower()


def test_clause_with_need_and_residual_ok() -> None:
    me = _me(20_000_000)
    rivals = [
        {
            "team_name": "Rival",
            "rank": 1,
            "team_id": "r1",
            "key_players": [],
            "squad": [
                _rival_player(
                    "gk_up",
                    "GK Upgrade",
                    "GK",
                    clause=6_000_000,
                    vm=5_500_000,
                    lineup=0.9,
                    ff=6.0,
                    prod=65,
                )
            ],
        }
    ]
    out = build_rival_upgrade_targets(
        me,
        _diag_gk_fw_thin(),
        rivals,
        balance=20_000_000,
        points_phase="preseason",
        market_reserved=0,
        hours_to_jornada=200.0,
    )
    hit = next((x for x in out if x.get("player_id") == "gk_up"), None)
    assert hit is not None, out
    assert hit["action"] == "clause_bid", hit
    assert hit.get("clause_roi") is not None
    assert "ROI" in (hit.get("why") or "")


def test_clause_crowds_out_market_gaps() -> None:
    """Con reserva de mercado, cláusula cara que deja poco residual → scout o no clause."""
    items = [
        {
            "player_id": "exp",
            "name": "Cara",
            "action": "clause_bid",
            "clause": 12_000_000,
            "bid": 12_000_000,
            "market_value": 10_000_000,
            "upgrade_score": 70,
            "clause_roi": 5.8,
            "why": "mejora cara",
            "budget_fit": "tight",
            "fills_need": True,
        },
        {
            "player_id": "ch",
            "name": "Barata",
            "action": "clause_bid",
            "clause": 4_000_000,
            "bid": 4_000_000,
            "market_value": 3_500_000,
            "upgrade_score": 40,
            "clause_roi": 10.0,
            "why": "mejora barata",
            "budget_fit": "comfortable",
            "fills_need": False,
        },
    ]
    # Saldo 19M, reserva 8M → sim 11M. Cara 12M no cabe en sim → scout.
    out = allocate_clause_bids(items, 19_000_000, market_reserved=8_000_000)
    by_id = {x["player_id"]: x for x in out}
    assert by_id["exp"]["action"] == "scout", by_id["exp"]
    # Barata: tras cara scout, sim sigue 11M; barata 4M cabe pero residual 7M < min(8M,8M) → scout
    assert by_id["ch"]["action"] == "scout", by_id["ch"]
    assert "reserva" in (by_id["ch"].get("why") or "").lower() or "comprometida" in (
        by_id["ch"].get("why") or ""
    ).lower() or "vigilante" in (by_id["ch"].get("why") or "").lower()


def test_mutual_one_expensive_one_cheap() -> None:
    items = [
        {
            "player_id": "e",
            "name": "Exp",
            "action": "clause_bid",
            "clause": 9_000_000,
            "bid": 9_000_000,
            "market_value": 8_000_000,
            "upgrade_score": 80,
            "clause_roi": 8.9,
            "why": "exp",
            "budget_fit": "tight",
            "fills_need": True,
        },
        {
            "player_id": "c1",
            "name": "Cheap1",
            "action": "clause_bid",
            "clause": 3_000_000,
            "bid": 3_000_000,
            "market_value": 2_800_000,
            "upgrade_score": 45,
            "clause_roi": 15.0,
            "why": "c1",
            "budget_fit": "comfortable",
            "fills_need": True,
        },
        {
            "player_id": "c2",
            "name": "Cheap2",
            "action": "clause_bid",
            "clause": 2_500_000,
            "bid": 2_500_000,
            "market_value": 2_200_000,
            "upgrade_score": 40,
            "clause_roi": 16.0,
            "why": "c2",
            "budget_fit": "comfortable",
            "fills_need": True,
        },
    ]
    # Sin reserva: 1 cara + 1 barata (mejor ROI barata = Cheap2)
    out = allocate_clause_bids(items, 20_000_000, market_reserved=0)
    clause = [x for x in out if x["action"] == "clause_bid"]
    names = {x["name"] for x in clause}
    assert "Exp" in names, names
    assert len(clause) == 2, names
    assert "Cheap2" in names or "Cheap1" in names
    # Solo una barata
    cheap = [x for x in clause if x["name"].startswith("Cheap")]
    assert len(cheap) == 1, cheap


def test_allocate_uses_debt_cap_not_cash() -> None:
    """Cláusula 8 M con 2 M en caja y maxDebt 20 M sigue clause_bid."""
    items = [
        {
            "player_id": "debt",
            "name": "DeudaOK",
            "action": "clause_bid",
            "clause": 8_000_000,
            "bid": 8_000_000,
            "market_value": 7_000_000,
            "upgrade_score": 60,
            "clause_roi": 7.5,
            "why": "cierra hueco",
            "budget_fit": "stretch",
            "fills_need": True,
        },
    ]
    out = allocate_clause_bids(items, 2_000_000, market_reserved=0, max_debt=20_000_000)
    clause = [x for x in out if x["action"] == "clause_bid"]
    assert len(clause) == 1, out
    assert clause[0]["name"] == "DeudaOK"


def test_no_hidden_market_reserve_keeps_fitting_clause() -> None:
    """Sin reserva 40%, una cláusula que cabe en el saldo sigue clause_bid."""
    items = [
        {
            "player_id": "exp",
            "name": "Cara",
            "action": "clause_bid",
            "clause": 12_000_000,
            "bid": 12_000_000,
            "market_value": 10_000_000,
            "upgrade_score": 70,
            "clause_roi": 5.8,
            "why": "mejora cara",
            "budget_fit": "tight",
            "fills_need": True,
        },
    ]
    out = allocate_clause_bids(items, 19_000_000, market_reserved=0)
    clause = [x for x in out if x["action"] == "clause_bid"]
    assert len(clause) == 1, out
    assert clause[0]["name"] == "Cara"


def main() -> None:
    tests = [
        test_clause_roi_helpers,
        test_allocate_keeps_best_roi_only,
        test_premium_high_without_need_is_scout,
        test_clause_with_need_and_residual_ok,
        test_clause_crowds_out_market_gaps,
        test_mutual_one_expensive_one_cheap,
        test_allocate_uses_debt_cap_not_cash,
        test_no_hidden_market_reserve_keeps_fitting_clause,
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


if __name__ == "__main__":
    main()
