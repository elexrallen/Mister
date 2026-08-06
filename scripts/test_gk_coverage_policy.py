"""
Regresión: política GK (no buy caro si hay titular) + hours_to_jornada sin amistosos.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from competitive_actions import is_key_market_candidate, resolve_hours_to_jornada  # noqa: E402
from scrapers.ff_matchday import (  # noqa: E402
    _both_clubs_in_league,
    _is_amistoso_context,
)
from squad_analyzer import assess_market_coverage  # noqa: E402


def _diag_with_starter() -> dict:
    return {
        "lineas": {
            "GK": {
                "status": "warning",
                "coverage": "thin",
                "starters_real": 1,
                "tandem": False,
            }
        },
        "structural_needs": [
            {
                "need": "gk_tandem",
                "position": "GK",
                "priority": "Alta",
                "same_team_as": "Villarreal",
                "same_team_id": "20",
                "max_price": 4_000_000,
            }
        ],
    }


def _squad_junior_padilla() -> list[dict]:
    return [
        {
            "id": "1",
            "name": "L. Júnior",
            "position": "GK",
            "team": "Villarreal",
            "team_id": "20",
            "lineup_prob": 0.85,
            "price": 2_350_000,
            "production_score": 6.8,
        },
        {
            "id": "2",
            "name": "A. Padilla",
            "position": "GK",
            "team": "Athletic",
            "team_id": "1",
            "lineup_prob": 0.1,
            "price": 283_000,
            "production_score": 38.5,
        },
    ]


def test_roman_not_gap() -> None:
    diag = _diag_with_starter()
    squad = _squad_junior_padilla()
    roman = {
        "id": "30444",
        "name": "L. Román",
        "position": "GK",
        "team": "Deportivo",
        "team_id": "6",
        "price": 9_289_000,
        "lineup_prob": 0.8,
        "production_score": 66.2,
        "ff_mister_avg": 4.48,
        "ff_mister_points": 148.0,
    }
    cov = assess_market_coverage(roman, diag, squad=squad)
    assert cov["fills_coverage_gap"] is False, cov
    assert cov["is_upgrade"] is False, cov
    assert cov["line_already_covered"] is True, cov
    assert cov["coverage_label"] == "Ya cubierto", cov

    roman_flags = {
        **roman,
        "fills_structural": False,
        "fills_coverage_gap": False,
        "line_already_covered": True,
        "is_upgrade": False,
        "sample_thin": False,
    }
    assert (
        is_key_market_candidate(
            roman_flags,
            is_primary_obj=False,
            is_objective=False,
            on_daily=True,
            gw_out=False,
            real_starter=True,
            fills_gap=True,  # incluso si el plan pasa gap por posición Alta
        )
        is False
    )


def test_villarreal_backup_is_tandem() -> None:
    diag = _diag_with_starter()
    squad = _squad_junior_padilla()
    backup = {
        "id": "99",
        "name": "Suplente VIL",
        "position": "GK",
        "team": "Villarreal",
        "team_id": "20",
        "price": 1_200_000,
        "lineup_prob": 0.15,
    }
    cov = assess_market_coverage(backup, diag, squad=squad)
    assert cov["fills_coverage_gap"] is True, cov
    assert cov["coverage_label"] == "Tándem portero", cov
    assert cov["is_upgrade"] is False, cov


def test_cheap_depth_fills_gap() -> None:
    diag = _diag_with_starter()
    squad = _squad_junior_padilla()
    cheap = {
        "id": "88",
        "name": "GK barato otro club",
        "position": "GK",
        "team": "Getafe",
        "team_id": "9",
        "price": 3_500_000,
        "lineup_prob": 0.5,
    }
    cov = assess_market_coverage(cheap, diag, squad=squad)
    assert cov["fills_coverage_gap"] is True, cov
    assert cov["coverage_label"] == "Cubre hueco", cov


def test_no_starter_any_gk_fills() -> None:
    diag = {
        "lineas": {
            "GK": {
                "status": "critical",
                "coverage": "critical",
                "starters_real": 0,
            }
        },
        "structural_needs": [
            {"need": "gk_backup", "position": "GK", "priority": "Alta", "max_price": None}
        ],
    }
    roman = {
        "id": "30444",
        "name": "L. Román",
        "position": "GK",
        "team": "Deportivo",
        "team_id": "6",
        "price": 9_289_000,
        "lineup_prob": 0.8,
    }
    cov = assess_market_coverage(roman, diag, squad=[])
    assert cov["fills_coverage_gap"] is True, cov
    assert cov["coverage_label"] == "Cubre hueco", cov


def test_hours_ignores_friendly_before_season_start() -> None:
    now = datetime(2026, 8, 6, 8, 0, tzinfo=timezone.utc)
    matchday = {
        "fixtures": [
            {"kickoff": "2026-08-06T20:00", "home": "Fiorentina", "away": "Deportivo"},
            {"kickoff": "2026-08-15T19:30", "home": "Alaves", "away": "Getafe"},
        ]
    }
    hours = resolve_hours_to_jornada(
        days_to_kickoff=9,
        matchday=matchday,
        now=now,
        season_start="2026-08-15",
    )
    assert hours is not None
    # ~9.5 días hasta Alavés–Getafe, no ~12h del amistoso
    assert hours > 200, hours
    assert hours < 250, hours


def test_league_club_filter() -> None:
    assert _both_clubs_in_league("alaves", "getafe", "laliga")
    assert not _both_clubs_in_league("fiorentina", "deportivo", "laliga")
    assert _is_amistoso_context("Fiorentina 20:00", "Amistoso Ago Fiorentina Deportivo")
    assert not _is_amistoso_context("Alavés Sab 15/08 Previa", "LaLiga J1 Alavés Getafe Previa")
    # Premier 26/27 hub: https://www.futbolfantasy.com/premier-league/posibles-alineaciones
    assert _both_clubs_in_league("arsenal", "coventry-city", "premier")
    assert _both_clubs_in_league("hull-city", "manchester-united", "premier-league")
    assert _both_clubs_in_league("leeds-united", "nottingham-forest", "premier")
    assert not _both_clubs_in_league("arsenal", "fiorentina", "premier")


def main() -> None:
    tests = [
        test_roman_not_gap,
        test_villarreal_backup_is_tandem,
        test_cheap_depth_fills_gap,
        test_no_starter_any_gk_fills,
        test_hours_ignores_friendly_before_season_start,
        test_league_club_filter,
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
