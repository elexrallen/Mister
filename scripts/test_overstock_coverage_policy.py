"""
Regresión: política adaptativa de sobrecupo / caja (DF y líneas en general).
No hardcodea la plantilla patio; usa fixtures sintéticos.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from competitive_actions import (  # noqa: E402
    evaluate_bid_finance,
    is_key_market_candidate,
    select_intent_lines,
)
from external_data import _merge_source_records  # noqa: E402
from squad_analyzer import (  # noqa: E402
    assess_market_coverage,
    ff_display_fields,
    is_clear_overstock_upgrade,
    is_line_overstocked,
    upgrade_worth_buy,
)


def _diag_df_overstock() -> dict:
    """10 DF, 8 titulares, coverage ok — espejo del caso patio sin nombres reales."""
    return {
        "lineas": {
            "GK": {
                "count": 2,
                "starters_real": 1,
                "coverage": "thin",
                "status": "warning",
            },
            "DF": {
                "count": 10,
                "starters_real": 8,
                "coverage": "ok",
                "status": "ok",
                "usable_count": 10,
                "depth_ok": True,
            },
            "MF": {
                "count": 5,
                "starters_real": 3,
                "coverage": "thin",
                "status": "warning",
            },
            "FW": {
                "count": 3,
                "starters_real": 2,
                "coverage": "thin",
                "status": "warning",
            },
        },
        "structural_needs": [
            {"need": "fw_top", "position": "FW", "priority": "Alta"},
            {"need": "gk_tandem", "position": "GK", "priority": "Alta"},
            {"need": "depth_mf", "position": "MF", "priority": "Media"},
        ],
    }


def _squad_df_overstock() -> list[dict]:
    dfs = []
    for i in range(8):
        dfs.append(
            {
                "id": f"df{i}",
                "name": f"DF Starter {i}",
                "position": "DF",
                "lineup_prob": 0.8,
                "price": 1_500_000,
                "production_score": 40,
            }
        )
    dfs.append(
        {
            "id": "df8",
            "name": "DF Alt 1",
            "position": "DF",
            "lineup_prob": 0.5,
            "price": 1_200_000,
        }
    )
    dfs.append(
        {
            "id": "df9",
            "name": "DF Alt 2",
            "position": "DF",
            "lineup_prob": 0.5,
            "price": 1_100_000,
        }
    )
    return [
        {
            "id": "gk1",
            "name": "GK Titular",
            "position": "GK",
            "team": "Villarreal",
            "team_id": "20",
            "lineup_prob": 0.85,
            "price": 2_000_000,
        },
        {
            "id": "gk2",
            "name": "GK Muerto",
            "position": "GK",
            "lineup_prob": 0.05,
            "price": 300_000,
        },
        *dfs,
        {
            "id": "mf1",
            "name": "MF1",
            "position": "MF",
            "lineup_prob": 0.8,
            "price": 1_400_000,
        },
        {
            "id": "mf2",
            "name": "MF2",
            "position": "MF",
            "lineup_prob": 0.7,
            "price": 1_300_000,
        },
        {
            "id": "mf3",
            "name": "MF3",
            "position": "MF",
            "lineup_prob": 0.7,
            "price": 1_200_000,
        },
        {
            "id": "fw1",
            "name": "FW1",
            "position": "FW",
            "lineup_prob": 0.9,
            "price": 2_000_000,
            "production_score": 45,
        },
        {
            "id": "fw2",
            "name": "FW2",
            "position": "FW",
            "lineup_prob": 0.7,
            "price": 1_600_000,
        },
    ]


def test_overstock_df_expensive_not_gap() -> None:
    diag = _diag_df_overstock()
    squad = _squad_df_overstock()
    assert is_line_overstocked("DF", diag, squad) is True

    cand = {
        "id": "x1",
        "name": "DF Upgrade Caro",
        "position": "DF",
        "price": 8_000_000,
        "lineup_prob": 0.95,
        "production_score": 70,
        "is_top_ff": True,
        "ff_apps": 20,
        "ff_mister_avg": 6.8,
    }
    cov = assess_market_coverage(cand, diag, squad=squad)
    assert cov["overstocked"] is True, cov
    assert cov["fills_coverage_gap"] is False, cov
    assert cov["position_coverage"] == "ok", cov
    # Puede ser upgrade, pero no gap
    assert cov["is_upgrade"] is True or cov["line_already_covered"] is True, cov

    flags = {
        **cand,
        **cov,
        "fills_structural": False,
        "sample_thin": False,
    }
    assert (
        is_key_market_candidate(
            flags,
            is_primary_obj=True,
            is_objective=True,
            on_daily=True,
            gw_out=False,
            real_starter=True,
            fills_gap=True,
        )
        is False
    )


def test_thin_df_few_starters_fills_gap() -> None:
    diag = {
        "lineas": {
            "DF": {
                "count": 2,
                "starters_real": 1,
                "coverage": "thin",
                "status": "warning",
            }
        },
        "structural_needs": [
            {"need": "df_starter", "position": "DF", "priority": "Alta"}
        ],
    }
    squad = [
        {
            "id": "a",
            "name": "Only DF",
            "position": "DF",
            "lineup_prob": 0.75,
            "price": 1_000_000,
        },
        {
            "id": "b",
            "name": "Bench DF",
            "position": "DF",
            "lineup_prob": 0.2,
            "price": 500_000,
        },
    ]
    assert is_line_overstocked("DF", diag, squad) is False
    cand = {
        "id": "mkt",
        "name": "DF Mercado",
        "position": "DF",
        "price": 5_000_000,
        "lineup_prob": 0.85,
    }
    cov = assess_market_coverage(cand, diag, squad=squad)
    assert cov["fills_coverage_gap"] is True, cov
    assert cov["overstocked"] is False, cov
    assert cov["coverage_label"] == "Cubre hueco", cov


def test_overstock_cheap_depth_when_thin() -> None:
    """Titulares OK + count≥ideal + depth thin → solo parche barato cubre gap."""
    diag = {
        "lineas": {
            "DF": {
                "count": 5,
                "starters_real": 3,
                "coverage": "thin",
                "status": "warning",
                "depth_ok": False,
            }
        },
        "structural_needs": [
            {"need": "depth_df", "position": "DF", "priority": "Media", "max_price": 4_000_000}
        ],
    }
    squad = [
        {"id": f"s{i}", "name": f"S{i}", "position": "DF", "lineup_prob": 0.8, "price": 2_000_000}
        for i in range(3)
    ] + [
        {"id": "x", "name": "Dead", "position": "DF", "lineup_prob": 0.1, "price": 400_000},
        {"id": "y", "name": "Dead2", "position": "DF", "lineup_prob": 0.1, "price": 400_000},
    ]
    assert is_line_overstocked("DF", diag, squad) is True

    cheap = {
        "id": "c",
        "name": "DF barato",
        "position": "DF",
        "price": 3_500_000,
        "lineup_prob": 0.5,
    }
    expensive = {
        "id": "e",
        "name": "DF caro",
        "position": "DF",
        "price": 9_000_000,
        "lineup_prob": 0.9,
    }
    cov_c = assess_market_coverage(cheap, diag, squad=squad)
    cov_e = assess_market_coverage(expensive, diag, squad=squad)
    assert cov_c["fills_coverage_gap"] is True, cov_c
    assert cov_e["fills_coverage_gap"] is False, cov_e


def test_select_intent_skips_overstock_df() -> None:
    daily = [
        {
            "player_id": "df_up",
            "name": "DF Upgrade",
            "position": "DF",
            "action": "buy_now",
            "bid": 2_000_000,
            "cost": 2_000_000,
            "overstocked": True,
            "fills_coverage_gap": False,
            "fills_structural": False,
            "is_key_market": True,
            "is_primary_target": True,
            "is_board_objective": True,
            "on_daily_market": True,
            "seller": "market",
            "priority_score": 200,
            "leaves_gap_budget": True,
            "budget_fit": "comfortable",
        },
        {
            "player_id": "fw_need",
            "name": "FW Need",
            "position": "FW",
            "action": "buy_now",
            "bid": 4_500_000,
            "cost": 4_500_000,
            "overstocked": False,
            "fills_coverage_gap": True,
            "fills_structural": True,
            "fills_need": True,
            "is_key_market": False,
            "on_daily_market": True,
            "seller": "market",
            "priority_score": 80,
            "leaves_gap_budget": True,
            "budget_fit": "comfortable",
            "production_score": 55,
        },
    ]
    intents = select_intent_lines(
        daily,
        bal=19_000_000,
        cash_reserve=8_000_000,
        primary_ids={"df_up"},
        secondary_max=2_500_000,
        max_intents=8,
    )
    assert intents, "debe haber al menos un intent"
    assert intents[0]["player_id"] == "fw_need", intents[0]


def test_solvency_strict_blocks_negative() -> None:
    """≤48h: puja que deja liquidez < 0 → solvency_blocked."""
    fin = evaluate_bid_finance(
        20_000_000,
        balance=5_000_000,
        min_cost=5_000_000,
        max_debt=30_000_000,
        balance_future=5_000_000,
        hours_to_jornada=24.0,
    )
    assert fin["solvency_strict"] is True, fin
    assert fin["solvency_blocked"] is True or fin["budget_fit"] == "blocked", fin
    assert float(fin.get("liquidity") or 0) - 20_000_000 < 0 or fin["solvency_ok"] is False


def test_mf_overstock_fw_thin_prefers_fw() -> None:
    diag = {
        "lineas": {
            "MF": {
                "count": 5,
                "starters_real": 3,
                "coverage": "ok",
                "status": "ok",
            },
            "FW": {
                "count": 2,
                "starters_real": 1,
                "coverage": "thin",
                "status": "warning",
            },
        }
    }
    squad = [
        {"id": f"m{i}", "name": f"M{i}", "position": "MF", "lineup_prob": 0.75, "price": 1_000_000}
        for i in range(5)
    ] + [
        {"id": "f1", "name": "F1", "position": "FW", "lineup_prob": 0.8, "price": 2_000_000},
        {"id": "f2", "name": "F2", "position": "FW", "lineup_prob": 0.2, "price": 400_000},
    ]
    assert is_line_overstocked("MF", diag, squad) is True
    assert is_line_overstocked("FW", diag, squad) is False

    mf_cand = {
        "id": "mm",
        "name": "MF mercado",
        "position": "MF",
        "price": 6_000_000,
        "lineup_prob": 0.9,
    }
    fw_cand = {
        "id": "ff",
        "name": "FW mercado",
        "position": "FW",
        "price": 5_000_000,
        "lineup_prob": 0.85,
    }
    cov_m = assess_market_coverage(mf_cand, diag, squad=squad)
    cov_f = assess_market_coverage(fw_cand, diag, squad=squad)
    assert cov_m["fills_coverage_gap"] is False, cov_m
    assert cov_f["fills_coverage_gap"] is True, cov_f


def test_jp_does_not_set_habitual_lineup_prob() -> None:
    """JP STARTER_PROB solo alimenta gw_*; FF conserva titularidad habitual."""
    ff = [
        {
            "name": "Filip Ugrinic",
            "team": "Valencia",
            "availability": "available",
            "lineup_prob": 40,
            "is_chollo": False,
            "is_recommendation": False,
            "source": "futbolfantasy",
        }
    ]
    jp = [
        {
            "name": "Filip Ugrinic",
            "team": "Valencia",
            "availability": "available",
            "lineup_prob": 85,
            "is_chollo": False,
            "is_recommendation": True,
            "source": "jornadaperfecta",
        }
    ]
    merged = _merge_source_records(ff, jp)
    assert len(merged) == 1, merged
    rec = merged[0]
    assert rec.get("lineup_prob") == 40, rec
    assert rec.get("gw_lineup_prob") == 85, rec
    assert rec.get("gw_starter") is True, rec

    # Solo JP: sin % habitual (ficha/apps después)
    only_jp = _merge_source_records([], jp)
    assert len(only_jp) == 1
    assert only_jp[0].get("lineup_prob") is None, only_jp[0]
    assert only_jp[0].get("gw_lineup_prob") == 85, only_jp[0]


def test_upgrade_mediocre_not_worth_buy() -> None:
    diag = _diag_df_overstock()
    squad = _squad_df_overstock()
    # Titular flojo vs alts (is_upgrade) pero sin superar +0.20 al peor titular ni TOP
    cand = {
        "id": "x",
        "name": "DF mediocre",
        "position": "DF",
        "price": 4_000_000,
        "lineup_prob": 0.75,
        "production_score": 40,
        "is_top_ff": False,
        "ff_apps": 20,
        "ff_mister_avg": 4.5,
    }
    cov = assess_market_coverage(cand, diag, squad=squad)
    assert cov.get("is_upgrade") is True, cov
    clear = is_clear_overstock_upgrade(cand, squad, is_upgrade=True)
    worth = upgrade_worth_buy(
        cand,
        is_upgrade=True,
        overstocked=True,
        squad=squad,
        budget_fit="comfortable",
        leaves_gap_budget=True,
        crowds_out_gaps=False,
        residual=15_000_000,
        other_gaps_min=5_000_000,
        cash_reserve=8_000_000,
    )
    assert clear is False, clear
    assert worth is False, worth
def test_upgrade_elite_worth_buy_when_residual_ok() -> None:
    diag = _diag_df_overstock()
    squad = _squad_df_overstock()
    cand = {
        "id": "elite",
        "name": "DF Elite",
        "position": "DF",
        "price": 3_000_000,
        "lineup_prob": 0.95,
        "production_score": 70,
        "is_top_ff": True,
        "ff_apps": 20,
        "ff_mister_avg": 7.2,
    }
    cov = assess_market_coverage(cand, diag, squad=squad)
    assert cov["is_upgrade"] is True, cov
    assert is_clear_overstock_upgrade(cand, squad, is_upgrade=True) is True
    worth = upgrade_worth_buy(
        cand,
        is_upgrade=True,
        overstocked=True,
        squad=squad,
        budget_fit="comfortable",
        debt_risk=False,
        solvency_blocked=False,
        leaves_gap_budget=True,
        crowds_out_gaps=False,
        residual=16_000_000,
        other_gaps_min=5_000_000,
        cash_reserve=8_000_000,
    )
    assert worth is True, worth
    assert (
        is_key_market_candidate(
            {**cand, **cov, "upgrade_worth_buy": True, "fills_structural": False},
            is_primary_obj=False,
            is_objective=False,
            on_daily=True,
            gw_out=False,
            real_starter=True,
            fills_gap=False,
        )
        is True
    )


def test_upgrade_elite_crowds_out_not_worth() -> None:
    squad = _squad_df_overstock()
    cand = {
        "id": "elite2",
        "name": "DF Elite caro",
        "position": "DF",
        "price": 12_000_000,
        "lineup_prob": 0.95,
        "production_score": 75,
        "is_top_ff": True,
    }
    worth = upgrade_worth_buy(
        cand,
        is_upgrade=True,
        overstocked=True,
        squad=squad,
        budget_fit="tight",
        leaves_gap_budget=False,
        crowds_out_gaps=True,
        residual=3_000_000,
        other_gaps_min=10_000_000,
        cash_reserve=8_000_000,
    )
    assert worth is False

    # Intent: upgrade overstock sin worth → sigue saltado; con worth → elegible
    daily_skip = [
        {
            "player_id": "df_up",
            "name": "DF Upgrade",
            "position": "DF",
            "action": "buy_now",
            "bid": 2_000_000,
            "cost": 2_000_000,
            "overstocked": True,
            "fills_coverage_gap": False,
            "fills_structural": False,
            "upgrade_worth_buy": False,
            "is_key_market": True,
            "on_daily_market": True,
            "seller": "market",
            "priority_score": 200,
            "leaves_gap_budget": True,
            "budget_fit": "comfortable",
        },
        {
            "player_id": "fw_need",
            "name": "FW Need",
            "position": "FW",
            "action": "buy_now",
            "bid": 4_500_000,
            "cost": 4_500_000,
            "overstocked": False,
            "fills_coverage_gap": True,
            "fills_structural": True,
            "fills_need": True,
            "on_daily_market": True,
            "seller": "market",
            "priority_score": 80,
            "leaves_gap_budget": True,
            "budget_fit": "comfortable",
            "production_score": 55,
        },
    ]
    intents = select_intent_lines(
        daily_skip,
        bal=19_000_000,
        cash_reserve=8_000_000,
        primary_ids=set(),
        secondary_max=2_500_000,
        max_intents=8,
    )
    assert intents[0]["player_id"] == "fw_need", intents[0]

    daily_ok = [
        {
            **daily_skip[0],
            "upgrade_worth_buy": True,
            "is_upgrade": True,
            "priority_score": 220,
        },
        daily_skip[1],
    ]
    intents_ok = select_intent_lines(
        daily_ok,
        bal=19_000_000,
        cash_reserve=8_000_000,
        primary_ids={"df_up"},
        secondary_max=2_500_000,
        max_intents=8,
    )
    ids = [i["player_id"] for i in intents_ok]
    assert "df_up" in ids, ids


def test_ff_display_prior_when_current_thin() -> None:
    p = {
        "ff_mister_avg": 2.0,
        "ff_apps": 1,
        "ff_prior_avg": 2.25,
        "ff_prior_apps": 12,
    }
    d = ff_display_fields(p)
    assert d["ff_display_source"] == "prior", d
    assert d["ff_display_avg"] == 2.25, d
    assert "aún no comparable" in (d.get("ff_note") or ""), d
    assert "2.2" in (d.get("ff_note") or ""), d
    assert d["ff_no_history"] is False, d


def test_ff_display_no_history() -> None:
    d = ff_display_fields({"ff_apps": 0, "ff_mister_avg": None})
    assert d["ff_no_history"] is True, d
    assert d["ff_display_source"] == "none", d
    assert "titularidad" in (d.get("ff_note") or "").lower(), d


def test_no_upgrade_without_ff_history() -> None:
    foreign = {"position": "MF", "lineup_prob": 0.9, "ff_apps": 0}
    squad = [
        {
            "position": "MF",
            "lineup_prob": 0.70,
            "production_score": 40,
            "ff_apps": 20,
            "ff_mister_avg": 5.0,
        }
    ]
    cov = assess_market_coverage(foreign, {"by_position": {}}, squad=squad)
    assert cov["is_upgrade"] is False, cov


def main() -> None:
    tests = [
        test_overstock_df_expensive_not_gap,
        test_thin_df_few_starters_fills_gap,
        test_overstock_cheap_depth_when_thin,
        test_select_intent_skips_overstock_df,
        test_solvency_strict_blocks_negative,
        test_mf_overstock_fw_thin_prefers_fw,
        test_jp_does_not_set_habitual_lineup_prob,
        test_upgrade_mediocre_not_worth_buy,
        test_upgrade_elite_worth_buy_when_residual_ok,
        test_upgrade_elite_crowds_out_not_worth,
        test_ff_display_prior_when_current_thin,
        test_ff_display_no_history,
        test_no_upgrade_without_ff_history,
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