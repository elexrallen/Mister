"""Regresión: Serie A cableada como competición de primera clase."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
from league_rules import ff_hint_for_provider  # noqa: E402
from scrapers.ff_matchday import SERIEA_CLUB_SLUGS, _competition_club_slugs  # noqa: E402
from scrapers.futbolfantasy import FF_PATH, _ff_path  # noqa: E402
from scrapers.teams import team_slug  # noqa: E402
from scrapers import JP_SUPPORTED, SUPPORTED_EXTERNAL  # noqa: E402


def _assert(cond: bool, msg: str = "") -> None:
    if not cond:
        raise AssertionError(msg or "assertion failed")


def test_competition_map_seriea() -> None:
    meta = config.competition_meta(10)
    _assert(meta.get("external") == "seriea", meta)
    _assert(meta.get("competition") == "Serie A", meta)
    _assert(config.external_competition_key(id_competition=10) == "seriea")
    _assert(config.external_competition_key(league_cfg={"external": "seriea"}) == "seriea")
    _assert(config.league_max_squad({"id_competition": 10}) == 25)
    _assert(config.league_max_squad({"external": "seriea"}) == 25)


def test_resolve_leagues_seriea() -> None:
    discovered = [
        {
            "id_community": "2525236",
            "name": "Serie A © - D0G8",
            "id_competition": 10,
        }
    ]
    resolved = config.resolve_leagues(discovered)
    _assert(len(resolved) == 1, resolved)
    row = resolved[0]
    _assert(row["external"] == "seriea", row)
    _assert(row["competition"] == "Serie A", row)
    _assert(row["id_competition"] == 10, row)
    _assert("serie" in str(row["slug"]).lower() or "d0g8" in str(row["slug"]).lower(), row)


def test_ff_paths_seriea() -> None:
    _assert(FF_PATH.get("seriea") == "serie-a")
    _assert(_ff_path("seriea") == "serie-a")
    _assert("seriea" in SUPPORTED_EXTERNAL)
    _assert("seriea" not in JP_SUPPORTED)
    try:
        _ff_path("unknown-comp")
        raise AssertionError("expected ValueError for unknown FF_PATH")
    except ValueError:
        pass


def test_seriea_club_slugs() -> None:
    clubs = _competition_club_slugs("seriea")
    _assert(clubs is SERIEA_CLUB_SLUGS)
    _assert("inter" in clubs and "milan" in clubs and "juventus" in clubs)
    # no-Premier no debe caer a LaLiga
    _assert("barcelona" not in clubs)
    _assert(team_slug("Inter de Milan") == "inter")
    _assert(team_slug("AC Milan") == "milan")
    _assert(team_slug("Juventus") == "juventus")
    _assert(team_slug("Napoli") == "napoles")
    _assert(team_slug("SSC Napoli") == "napoles")


def test_ff_hint_mr_seriea_not_premier() -> None:
    hint = ff_hint_for_provider("mr", competition="seriea")
    _assert(hint.get("prefer_competition") != "premier", hint)
    _assert(hint.get("prefer_competition") == "seriea", hint)
    _assert(float(hint.get("avg_scale") or 0) == 16.0, hint)

    premier = ff_hint_for_provider("mr", competition="premier")
    _assert(premier.get("prefer_competition") == "premier", premier)


def main() -> int:
    test_competition_map_seriea()
    test_resolve_leagues_seriea()
    test_ff_paths_seriea()
    test_seriea_club_slugs()
    test_ff_hint_mr_seriea_not_premier()
    print("OK seriea support")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
