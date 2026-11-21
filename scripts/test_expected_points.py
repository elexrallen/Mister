"""
Regresión de xPts: hist FF inflado (Serie A Fantasy✨) vs Patio Mixto.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from expected_points import (  # noqa: E402
    expected_points,
    probability_of_playing,
    production_base,
)


def test_diouf_like_hist_does_not_dominate_short_sample() -> None:
    """Hist FF 17.3 con forma Mister 6.3 no puede salir a ~15.7 pts/partido."""
    player = {
        "id": "65400",
        "position": "MF",
        "gw_probable_xi": True,
        "gw_lineup_prob": 90,
        "ff_mister_avg": 17.33,
        "mister_avg": 6.3,
        "form": 6.3,
        "recent_gw_points": [8, 6, 5],
        "ff_avg_scale": 16.0,
        "external": {"ff_mister_avg": 17.33, "ff_avg_scale": 16.0},
    }
    base, why = production_base(player, 16.0)
    # Viejo: 0.85×17.33 + 0.15×6.33 ≈ 15.7
    assert base < 12.0, (base, why)
    assert base > 5.0, (base, why)
    assert "desinflado" in why or "manda" in why, why
    assert "aún no manda" not in why, why
    out = expected_points(player, league_rules={"avg_scale": 16.0})
    assert out["xpts_base"] < 12.0, out
    assert "histórico FF 17.3" in out["xpts_why"]
    assert "aún no manda" not in out["xpts_why"]


def test_keeper_hist_cannot_print_twenty_xpts() -> None:
    player = {
        "id": "28163",
        "position": "GK",
        "gw_probable_xi": True,
        "gw_lineup_prob": 90,
        "ff_mister_avg": 17.25,
        "mister_avg": 7.5,
        "form": 7.5,
        "recent_gw_points": [11, 1, 7],
        "fdr_multiplier": 1.0,
        "external": {"ff_mister_avg": 17.25, "ff_avg_scale": 16.0},
        "ff_avg_scale": 16.0,
    }
    out = expected_points(player, league_rules={"avg_scale": 16.0})
    assert out["xpts"] < 14.0, out
    assert out["xpts_base"] < 14.0, out


def test_mixto_short_sample_still_trusts_history() -> None:
    player = {
        "id": "5",
        "position": "FW",
        "gw_lineup_prob": 90,
        "recent_gw_points": [2, 0],
        "mister_avg": 1.0,
        "external": {"ff_mister_avg": 7.0},
    }
    base, why = production_base(player, 8.0)
    assert abs(base - 6.10) < 0.02, (base, why)
    assert "histórico FF 7.0" in why
    assert "aún no manda" in why


def test_probable_xi_not_descartado_by_pred11_bench() -> None:
    """Pred11 al 18% no puede tumbar un once probable de Mister a 3%."""
    player = {
        "id": "1",
        "position": "FW",
        "gw_probable_xi": True,
        "gw_out": True,
        "gw_lineup_prob": 18,
        "lineup_prob_source": "predicted11",
        "external": {
            "gw_out": True,
            "gw_lineup_prob": 18,
            "lineup_prob_source": "predicted11",
        },
    }
    p_play, why = probability_of_playing(player)
    assert p_play >= 0.55, (p_play, why)
    assert "Descartado" not in why, why
    assert "Mister" in why, why


def test_blank_gw_out_still_unavailable() -> None:
    player = {
        "id": "2",
        "gw_blank": True,
        "gw_out": True,
        "gw_lineup_prob": 100,
        "external": {"lineup_prob_source": "predicted11", "gw_lineup_prob": 100},
    }
    p_play, why = probability_of_playing(player)
    assert p_play <= 0.05, (p_play, why)
    assert "blank" in why.lower() or "partido" in why.lower(), why


def test_preseason_hist_only_unchanged() -> None:
    player = {
        "id": "1",
        "position": "MF",
        "gw_lineup_prob": 85,
        "external": {"availability": "available", "ff_prior_avg": 6.0},
    }
    out = expected_points(player)
    assert out["xpts_base"] == 6.0, out
    assert "histórico FF" in out["xpts_why"]


def main() -> None:
    tests = [
        test_diouf_like_hist_does_not_dominate_short_sample,
        test_keeper_hist_cannot_print_twenty_xpts,
        test_mixto_short_sample_still_trusts_history,
        test_preseason_hist_only_unchanged,
        test_probable_xi_not_descartado_by_pred11_bench,
        test_blank_gw_out_still_unavailable,
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
