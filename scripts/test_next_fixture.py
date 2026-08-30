"""Kickoff pasado vs pendiente: next_* no es el rival ya pitado."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from expected_points import expected_points  # noqa: E402
from fixture_difficulty import annotate_players_with_fdr  # noqa: E402
from mister_client import apply_gameweek_to_players  # noqa: E402
from mister_gameweek import (  # noqa: E402
    build_team_schedule,
    fixture_is_unplayed,
    next_unplayed_fixture,
)

NOW = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
PAST_TS = int(datetime(2026, 8, 28, 17, 0, tzinfo=timezone.utc).timestamp())
FUTURE_TS = int(datetime(2026, 9, 4, 19, 0, tzinfo=timezone.utc).timestamp())
SAME_GW_FUTURE_TS = int(datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc).timestamp())


def test_fixture_is_unplayed_by_kickoff_and_status() -> None:
    assert fixture_is_unplayed(
        {"kickoff_ts": FUTURE_TS, "status": "fixture"}, now=NOW
    )
    assert not fixture_is_unplayed(
        {"kickoff_ts": PAST_TS, "status": "played"}, now=NOW
    )
    assert not fixture_is_unplayed({"status": "played"}, now=NOW)
    assert fixture_is_unplayed({"status": "live", "kickoff_ts": PAST_TS}, now=NOW)


def test_next_unplayed_skips_played() -> None:
    rows = [
        {"jornada": 3, "opponent_id": "20", "kickoff_ts": PAST_TS, "status": "played"},
        {"jornada": 4, "opponent_id": "30", "kickoff_ts": FUTURE_TS, "status": "fixture"},
    ]
    nxt = next_unplayed_fixture(rows, now=NOW)
    assert nxt and nxt["opponent_id"] == "30" and nxt["jornada"] == 4


def test_next_unplayed_keeps_later_same_gw() -> None:
    rows = [
        {"jornada": 3, "opponent_id": "20", "kickoff_ts": PAST_TS, "status": "played"},
        {
            "jornada": 3,
            "opponent_id": "40",
            "kickoff_ts": SAME_GW_FUTURE_TS,
            "status": "fixture",
        },
    ]
    nxt = next_unplayed_fixture(rows, now=NOW)
    assert nxt and nxt["opponent_id"] == "40" and nxt["jornada"] == 3


def test_team_schedule_drops_played() -> None:
    comp = {
        "games": {
            "3": [
                {
                    "id_home": "10",
                    "id_away": "20",
                    "status": "played",
                    "date": {"ts": PAST_TS},
                }
            ],
            "4": [
                {
                    "id_home": "10",
                    "id_away": "30",
                    "status": "fixture",
                    "date": {"ts": FUTURE_TS},
                }
            ],
        }
    }
    sched = build_team_schedule(comp, from_jornada=3, now=NOW)
    assert all(r["jornada"] != 3 or r.get("status") != "played" for r in sched.get("10") or [])
    assert sched["10"][0]["opponent_id"] == "30"


def test_apply_gw_played_advances_next() -> None:
    players = [{"id": "1", "name": "A", "team_id": "10"}]
    apply_gameweek_to_players(
        players,
        {
            "preview": {
                "1": {
                    "gw_opponent_id": "20",
                    "gw_is_home": True,
                    "gw_kickoff_ts": PAST_TS,
                    "gw_confirmed": True,
                }
            },
            "points": {"1": {"points": 6, "played": True, "status": "played"}},
            "matchday": {"jornada": 3},
            "team_schedule": {
                "10": [
                    {
                        "jornada": 4,
                        "opponent_id": "30",
                        "is_home": False,
                        "kickoff_ts": FUTURE_TS,
                        "status": "fixture",
                    }
                ]
            },
        },
        now=NOW,
    )
    p = players[0]
    assert p["gw_opponent_id"] == "20"
    assert p["gw_played"] is True
    assert p["gw_points"] == 6
    assert p["next_opponent_team_id"] == "30"
    assert p["next_jornada"] == 4


def test_apply_gw_unplayed_keeps_this_gw() -> None:
    players = [{"id": "1", "name": "A", "team_id": "10"}]
    apply_gameweek_to_players(
        players,
        {
            "preview": {
                "1": {
                    "gw_opponent_id": "20",
                    "gw_is_home": True,
                    "gw_kickoff_ts": SAME_GW_FUTURE_TS,
                }
            },
            "matchday": {"jornada": 3},
            "team_schedule": {
                "10": [
                    {
                        "jornada": 3,
                        "opponent_id": "20",
                        "is_home": True,
                        "kickoff_ts": SAME_GW_FUTURE_TS,
                        "status": "fixture",
                    }
                ]
            },
        },
        now=NOW,
    )
    p = players[0]
    assert p.get("gw_played") in (None, False)
    assert p["next_opponent_team_id"] == "20"
    assert p["next_jornada"] == 3


def test_fdr_horizon_skips_played_and_flags_next_gw() -> None:
    players = [
        {
            "id": "1",
            "team_id": "10",
            "position": "DF",
            "gw_played": True,
            "next_opponent_team_id": "30",
            "next_is_home": False,
            "next_jornada": 4,
        }
    ]
    n = annotate_players_with_fdr(
        players,
        strength={"teams": {}, "source": "none"},
        team_schedule={
            "10": [
                {
                    "jornada": 3,
                    "opponent_id": "20",
                    "kickoff_ts": PAST_TS,
                    "status": "played",
                },
                {
                    "jornada": 4,
                    "opponent_id": "30",
                    "kickoff_ts": FUTURE_TS,
                    "status": "fixture",
                    "is_home": False,
                },
            ]
        },
        current_jornada=3,
        now=NOW,
    )
    assert n == 1
    assert players[0]["fdr_applies_to_current_gw"] is False
    assert players[0]["fdr_next"][0]["jornada"] == 4


def test_xpts_neutral_when_fdr_is_next_gw() -> None:
    base = {
        "id": "1",
        "position": "MF",
        "price": 10_000_000,
        "gw_lineup_prob": 85,
        "external": {"availability": "available", "ff_prior_avg": 6.0},
        "fdr_multiplier": 1.2,
    }
    live = expected_points(base)
    done = expected_points({**base, "gw_played": True, "fdr_applies_to_current_gw": False})
    assert live["xpts"] == 6.12, live
    assert done["xpts"] == 5.1, done
    assert "vs" not in done["xpts_why"]


if __name__ == "__main__":
    test_fixture_is_unplayed_by_kickoff_and_status()
    test_next_unplayed_skips_played()
    test_next_unplayed_keeps_later_same_gw()
    test_team_schedule_drops_played()
    test_apply_gw_played_advances_next()
    test_apply_gw_unplayed_keeps_this_gw()
    test_fdr_horizon_skips_played_and_flags_next_gw()
    test_xpts_neutral_when_fdr_is_next_gw()
    print("test_next_fixture: OK")
