"""Regresión: Predicted11 cableado para Premier/Serie A y merge de titularidad."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from external_data import _apply_predicted11_overlay  # noqa: E402
from scrapers import PRED11_SUPPORTED, SUPPORTED_EXTERNAL, fetch_all_external  # noqa: E402
from scrapers.predicted11 import (  # noqa: E402
    PRED11_SLUG,
    _pick_expert_urls,
    _records_from_counts,
)


def _assert(cond: bool, msg: str = "") -> None:
    if not cond:
        raise AssertionError(msg or "assertion failed")


def test_pred11_supported_keys() -> None:
    _assert(PRED11_SUPPORTED == frozenset({"premier", "seriea"}))
    _assert(PRED11_SLUG["premier"] == "premier-league")
    _assert(PRED11_SLUG["seriea"] == "serie-a")
    _assert("seriea" in SUPPORTED_EXTERNAL)


def test_pick_expert_urls_prefers_rank() -> None:
    ranked = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta"]
    news = [
        ("Zeta", "https://example/zeta"),
        ("Beta", "https://example/beta"),
        ("Alpha", "https://example/alpha"),
        ("Other", "https://example/other"),
    ]
    picked = _pick_expert_urls(ranked, news, limit=3)
    _assert(picked == [
        "https://example/alpha",
        "https://example/beta",
        "https://example/zeta",
    ], picked)


def test_records_from_counts_pct() -> None:
    counts = {
        ("Moreno", "Venezia"): 4,
        ("Bench", "Venezia"): 1,
    }
    rows = _records_from_counts(counts, n_experts=5)
    by_name = {r["name"]: r for r in rows}
    _assert(by_name["Moreno"]["lineup_prob"] == 80, by_name["Moreno"])
    _assert(by_name["Moreno"]["source"] == "predicted11")
    _assert(by_name["Moreno"]["is_recommendation"] is True)
    _assert(by_name["Bench"]["lineup_prob"] == 20)


def test_overlay_authority_premier() -> None:
    candidates = [
        {
            "name": "M. Moreno",
            "team": "Venezia",
            "availability": "available",
            "lineup_prob": 40,
            "sources": ["futbolfantasy"],
            "gw_lineup_prob": 40,
        }
    ]
    pred11 = [
        {
            "name": "Moreno",
            "team": "Venezia",
            "lineup_prob": 80,
            "source": "predicted11",
        }
    ]
    out = _apply_predicted11_overlay(candidates, pred11, authority=True)
    row = out[0]
    _assert(row["gw_lineup_prob"] == 80, row)
    _assert(row["lineup_prob"] == 80, row)
    _assert(row["lineup_prob_source"] == "predicted11", row)
    _assert(row["gw_starter"] is True, row)


def test_fetch_all_external_wires_pred11() -> None:
    fake_pred = [
        {
            "name": "Test Player",
            "team": "Test",
            "lineup_prob": 100,
            "source": "predicted11",
            "availability": "available",
            "is_recommendation": True,
            "is_chollo": False,
            "profile_url": None,
        }
    ]
    with (
        patch("scrapers.fetch_futbolfantasy", return_value=[{"name": "X", "source": "futbolfantasy"}]),
        patch(
            "scrapers.fetch_ff_matchday",
            return_value={"status": "ok", "fixtures": [], "players": []},
        ),
        patch("scrapers.fetch_jornadaperfecta", return_value=[]),
        patch("scrapers.fetch_predicted11", return_value=fake_pred) as m_pred,
    ):
        bundle = fetch_all_external(competition="seriea")
        _assert(m_pred.called)
        _assert(bundle["status"]["predicted11"] == "ok", bundle["status"])
        _assert(bundle["predicted11"] == fake_pred)
        bundle_ll = fetch_all_external(competition="laliga")
        _assert(bundle_ll["status"]["predicted11"] == "skip", bundle_ll["status"])
        _assert(bundle_ll["predicted11"] == [])


def main() -> int:
    test_pred11_supported_keys()
    test_pick_expert_urls_prefers_rank()
    test_records_from_counts_pct()
    test_overlay_authority_premier()
    test_fetch_all_external_wires_pred11()
    print("OK predicted11")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
