"""
Huecos del once (no titulares / blank) deben generar carencia de mercado.

El diagnóstico estructural usa STARTERS_TARGET (3 DF / 2 FW). Un 4-3-3 pide
4 DF y 3 FW: si el once se rellena con no titulares, eso no es "línea cubierta".
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from daily_playbook import build_daily_playbook  # noqa: E402
from squad_analyzer import (  # noqa: E402
    analyze_squad,
    apply_xi_slot_needs,
    assess_market_coverage,
    compute_xi_slot_gaps,
    has_xi_starter_need,
    is_line_overstocked,
    parse_xi_shape,
    structural_market_boost,
)


def _p(
    pid: str,
    pos: str,
    *,
    lp: float | None = 0.8,
    blank: bool = False,
    in_lineup: bool = False,
    name: str | None = None,
    price: int = 1_000_000,
) -> dict:
    row = {
        "id": pid,
        "name": name or pid,
        "position": pos,
        "lineup_prob": lp,
        "in_lineup": in_lineup,
        "price": price,
        "injury": False,
    }
    if blank:
        row["gw_blank"] = True
        row["gw_out"] = True
    return row


def test_parse_433() -> None:
    assert parse_xi_shape("1-4-3-3") == {"GK": 1, "DF": 4, "MF": 3, "FW": 3}
    assert parse_xi_shape("4-3-3") == {"GK": 1, "DF": 4, "MF": 3, "FW": 3}
    assert parse_xi_shape("3-3-4") == {"GK": 1, "DF": 3, "MF": 3, "FW": 4}
    assert parse_xi_shape("1-3-3-4") == {"GK": 1, "DF": 3, "MF": 3, "FW": 4}


def test_433_missing_fourth_df_is_xi_starter() -> None:
    squad = [
        _p("gk1", "GK", lp=0.95),
        _p("df1", "DF", lp=1.0, in_lineup=True),
        _p("df2", "DF", lp=0.9, in_lineup=True),
        _p("df3", "DF", lp=0.85, in_lineup=True),
        _p("df4", "DF", lp=0.0, in_lineup=True, name="Mukiele"),
        _p("mf1", "MF", lp=0.9),
        _p("mf2", "MF", lp=0.85),
        _p("mf3", "MF", lp=0.8),
        _p("fw1", "FW", lp=0.95, in_lineup=True),
        _p("fw2", "FW", lp=0.0, in_lineup=True, name="Gittens"),
        _p("fw3", "FW", lp=0.1, in_lineup=True, name="Solanke"),
    ]
    needs, tips, summary = compute_xi_slot_gaps(squad, formation="1-4-3-3")
    pos = {n["position"]: n for n in needs}
    assert "DF" in pos, needs
    assert pos["DF"]["slots_short"] == 1, pos["DF"]
    assert "Mukiele" in (pos["DF"].get("reason") or "")
    assert "FW" in pos, needs
    assert pos["FW"]["slots_short"] == 2, pos["FW"]
    assert "GK" not in pos
    assert summary["slots_short"] == 3
    assert any(t.get("code") == "xi_starter_df" for t in tips)


def test_blank_gk_is_xi_starter_even_if_seasonal_starter() -> None:
    squad = [
        _p("gk1", "GK", lp=1.0, blank=True, in_lineup=True, name="Hermansen"),
        _p("gk2", "GK", lp=1.0, blank=True, name="Sa"),
        _p("df1", "DF", lp=0.9),
        _p("df2", "DF", lp=0.9),
        _p("df3", "DF", lp=0.9),
        _p("df4", "DF", lp=0.9),
        _p("mf1", "MF", lp=0.9),
        _p("mf2", "MF", lp=0.9),
        _p("mf3", "MF", lp=0.9),
        _p("fw1", "FW", lp=0.9),
        _p("fw2", "FW", lp=0.9),
        _p("fw3", "FW", lp=0.9),
    ]
    needs, _, summary = compute_xi_slot_gaps(squad, formation="4-3-3")
    gk = next(n for n in needs if n["position"] == "GK")
    assert gk["slots_short"] == 1
    assert "Hermansen" in (gk.get("reason") or "")
    assert summary["slots_short"] == 1
    assert has_xi_starter_need({"structural_needs": needs}, "GK")


def test_enough_starters_no_xi_need() -> None:
    squad = [_p("gk1", "GK", lp=0.95)]
    squad += [_p(f"df{i}", "DF", lp=0.9) for i in range(1, 5)]
    squad += [_p(f"mf{i}", "MF", lp=0.9) for i in range(1, 4)]
    squad += [_p(f"fw{i}", "FW", lp=0.9) for i in range(1, 4)]
    needs, _, summary = compute_xi_slot_gaps(squad, formation="4-3-3")
    assert needs == []
    assert summary["slots_short"] == 0


def test_market_df_starter_fills_xi_slot_not_already_covered() -> None:
    diag = {
        "lineas": {
            "DF": {
                "status": "ok",
                "coverage": "ok",
                "starters_real": 3,
                "count": 7,
            }
        },
        "structural_needs": [
            {
                "need": "xi_starter",
                "position": "DF",
                "priority": "Alta",
                "slots_short": 1,
            }
        ],
    }
    squad = [
        _p("df1", "DF", lp=0.9),
        _p("df2", "DF", lp=0.85),
        _p("df3", "DF", lp=0.8),
        _p("df4", "DF", lp=0.0, in_lineup=True),
    ]
    cand = {
        "id": "mkt",
        "name": "DF titular mercado",
        "position": "DF",
        "lineup_prob": 0.88,
        "price": 2_500_000,
        "ff_mister_avg": 5.2,
        "ff_apps": 30,
        "production_score": 55,
    }
    cov = assess_market_coverage(cand, diag, squad=squad)
    assert cov["fills_coverage_gap"] is True, cov
    assert cov["line_already_covered"] is False, cov
    assert cov["coverage_label"] == "Cubre hueco del once", cov
    assert is_line_overstocked("DF", diag, squad) is False
    bonus, fills, label = structural_market_boost(cand, diag["structural_needs"])
    assert fills is True
    assert label == "Cubre hueco del once"
    assert bonus >= 32


def test_expensive_gk_fills_blank_xi_slot() -> None:
    diag = {
        "lineas": {
            "GK": {
                "status": "warning",
                "coverage": "ok",
                "starters_real": 2,
            }
        },
        "structural_needs": [
            {"need": "xi_starter", "position": "GK", "priority": "Alta", "slots_short": 1}
        ],
    }
    squad = [
        _p("gk1", "GK", lp=1.0, blank=True, in_lineup=True),
        _p("gk2", "GK", lp=1.0, blank=True),
    ]
    roman = {
        "id": "30444",
        "name": "L. Román",
        "position": "GK",
        "team": "Deportivo",
        "price": 9_289_000,
        "lineup_prob": 0.8,
        "production_score": 66.2,
        "ff_mister_avg": 4.48,
        "ff_apps": 33,
    }
    cov = assess_market_coverage(roman, diag, squad=squad)
    assert cov["fills_coverage_gap"] is True, cov
    assert cov["line_already_covered"] is False, cov
    assert cov["coverage_label"] == "Cubre hueco del once", cov


def test_analyze_squad_wires_formation() -> None:
    squad = [
        _p("gk1", "GK", lp=1.0, blank=True, in_lineup=True, name="Hermansen"),
        _p("gk2", "GK", lp=1.0, blank=True, name="Sa"),
        _p("df1", "DF", lp=0.9, in_lineup=True),
        _p("df2", "DF", lp=0.9, in_lineup=True),
        _p("df3", "DF", lp=0.9, in_lineup=True),
        _p("df4", "DF", lp=0.0, in_lineup=True, name="Mukiele"),
        _p("mf1", "MF", lp=0.9, in_lineup=True),
        _p("mf2", "MF", lp=0.9, in_lineup=True),
        _p("mf3", "MF", lp=0.2, in_lineup=True),
        _p("fw1", "FW", lp=0.95, in_lineup=True),
        _p("fw2", "FW", lp=0.0, in_lineup=True, name="Gittens"),
        _p("fw3", "FW", lp=0.1, in_lineup=True, name="Solanke"),
    ]
    diag = analyze_squad(squad, formation="1-4-3-3", points_phase="active")
    xi_needs = [n for n in diag["structural_needs"] if n.get("need") == "xi_starter"]
    pos = {n["position"] for n in xi_needs}
    assert {"GK", "DF", "FW"} <= pos, xi_needs
    assert (diag.get("xi_slot_gaps") or {}).get("slots_short", 0) >= 3
    assert (diag.get("lineas") or {}).get("DF", {}).get("coverage") == "thin"


def test_playbook_lists_all_risky_and_asks_upgrade() -> None:
    xi = {
        "captain_enabled": False,
        "summary": {"complete": True, "xi_count": 11, "xi_target": 11},
        "xi": [],
        "risky_slots": [
            {"player_id": "1", "name": "Hermansen", "position": "GK", "reason": "blank"},
            {"player_id": "2", "name": "Mukiele", "position": "DF", "reason": "18%"},
            {"player_id": "3", "name": "Gittens", "position": "FW", "reason": "18%"},
            {"player_id": "4", "name": "Solanke", "position": "FW", "reason": "18%"},
        ],
    }
    diag = {
        "structural_needs": [
            {
                "need": "xi_starter",
                "position": "GK",
                "reason": "El 4-3-3 pide 1 GK; Hermansen está en blank.",
                "occupant_ids": ["1"],
            },
            {
                "need": "xi_starter",
                "position": "DF",
                "reason": "El 4-3-3 pide 4 DF; Mukiele no es titular.",
                "occupant_ids": ["2"],
            },
        ]
    }
    book = build_daily_playbook(
        hours_to_jornada=5.0,
        matchday={"gameweek_status": "ongoing"},
        recommended_xi=xi,
        diagnostico=diag,
    )
    ceros = next(c for c in book["checklist"] if c["id"] == "xi_ceros")
    assert "Hermansen" in ceros["detail"]
    assert "Mukiele" in ceros["detail"]
    assert "Gittens" in ceros["detail"]
    assert "Solanke" in ceros["detail"]
    mejoras = next(c for c in book["checklist"] if c["id"] == "xi_mejoras")
    assert mejoras["priority"] == "Alta"
    assert "Mukiele" in mejoras["detail"] or "4 DF" in mejoras["detail"]


def test_apply_refreshes_occupant_names_from_recommended_xi() -> None:
    squad = [
        _p("gk1", "GK", lp=1.0, blank=True, in_lineup=True, name="Hermansen"),
        _p("df1", "DF", lp=0.9),
        _p("df2", "DF", lp=0.9),
        _p("df3", "DF", lp=0.9),
        _p("df4", "DF", lp=0.0, in_lineup=True, name="Mukiele"),
        _p("mf1", "MF", lp=0.9),
        _p("mf2", "MF", lp=0.9),
        _p("mf3", "MF", lp=0.9),
        _p("fw1", "FW", lp=0.9),
        _p("fw2", "FW", lp=0.9),
        _p("fw3", "FW", lp=0.9),
    ]
    diag = analyze_squad(squad, formation="4-3-3")
    recommended = {
        "formation": "4-3-3",
        "shape": {"GK": 1, "DF": 4, "MF": 3, "FW": 3},
        "risky_slots": [
            {
                "player_id": "gk1",
                "name": "Hermansen",
                "position": "GK",
                "reason": "Sin partido esta jornada (blank): no puntúa",
            }
        ],
    }
    apply_xi_slot_needs(diag, squad, formation="4-3-3", recommended_xi=recommended)
    gk = next(n for n in diag["structural_needs"] if n.get("need") == "xi_starter" and n.get("position") == "GK")
    assert "Hermansen" in (gk.get("reason") or "")


def main() -> None:
    tests = [
        test_parse_433,
        test_433_missing_fourth_df_is_xi_starter,
        test_blank_gk_is_xi_starter_even_if_seasonal_starter,
        test_enough_starters_no_xi_need,
        test_market_df_starter_fills_xi_slot_not_already_covered,
        test_expensive_gk_fills_blank_xi_slot,
        test_analyze_squad_wires_formation,
        test_playbook_lists_all_risky_and_asks_upgrade,
        test_apply_refreshes_occupant_names_from_recommended_xi,
    ]
    for fn in tests:
        fn()
        print(f"ok {fn.__name__}")
    print(f"{len(tests)} tests ok")


if __name__ == "__main__":
    main()
