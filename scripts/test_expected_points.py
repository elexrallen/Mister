"""
Regresión de xPts: hist FF inflado (Serie A Fantasy✨) vs Patio Mixto.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from expected_points import expected_points, production_base  # noqa: E402


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
