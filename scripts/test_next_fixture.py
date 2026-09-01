"""Kickoff pasado vs pendiente: next_* no es el rival ya pitado."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from expected_points import expected_points  # noqa: E402
from fixture_difficulty import annotate_players_with_fdr  # noqa: E402
from competitive_actions import build_recommended_gw_xi  # noqa: E402
from mister_client import apply_gameweek_to_players  # noqa: E402
from mister_gameweek import (  # noqa: E402
    apply_blank_gameweek,
    build_played_opponents,
    build_team_schedule,
    fixture_for_jornada,
    fixture_is_unplayed,
    next_unplayed_fixture,
    resolve_scoring_jornada,
)

NOW = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
PAST_TS = int(datetime(2026, 8, 28, 17, 0, tzinfo=timezone.utc).timestamp())
FUTURE_TS = int(datetime(2026, 9, 4, 19, 0, tzinfo=timezone.utc).timestamp())
SAME_GW_FUTURE_TS = int(datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc).timestamp())
J6_THU_TS = int(datetime(2026, 9, 3, 19, 0, tzinfo=timezone.utc).timestamp())
J4_FRI_TS = int(datetime(2026, 9, 4, 19, 0, tzinfo=timezone.utc).timestamp())
J6_SAT_TS = int(datetime(2026, 9, 5, 15, 0, tzinfo=timezone.utc).timestamp())
BEFORE_THU = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)
AFTER_THU = datetime(2026, 9, 3, 22, 0, tzinfo=timezone.utc)


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


def _laliga_split_comp(*, thursday_played: bool = False) -> dict:
    """J6 jueves (10 vs 20) + J4 viernes + resto de J6 el sábado."""
    thu_status = "played" if thursday_played else "fixture"
    thu_extra = {"goals_home": 1, "goals_away": 0} if thursday_played else {}
    return {
        "games": {
            "3": [
                {
                    "id_home": "1",
                    "id_away": "2",
                    "status": "played",
                    "date": {"ts": PAST_TS},
                    "goals_home": 2,
                    "goals_away": 0,
                }
            ],
            "4": [
                {
                    "id_home": "3",
                    "id_away": "4",
                    "status": "fixture",
                    "date": {"ts": J4_FRI_TS},
                },
                {
                    "id_home": "10",
                    "id_away": "30",
                    "status": "fixture",
                    "date": {"ts": J4_FRI_TS},
                },
                {
                    "id_home": "99",
                    "id_away": "5",
                    "status": "fixture",
                    "date": {"ts": J4_FRI_TS},
                },
            ],
            "6": [
                {
                    "id_home": "10",
                    "id_away": "20",
                    "status": thu_status,
                    "date": {"ts": J6_THU_TS},
                    **thu_extra,
                },
                {
                    "id_home": "3",
                    "id_away": "5",
                    "status": "fixture",
                    "date": {"ts": J6_SAT_TS},
                },
            ],
        }
    }


def test_schedule_keeps_pending_lower_jornada() -> None:
    sched = build_team_schedule(
        _laliga_split_comp(), from_jornada=6, now=BEFORE_THU
    )
    jornadas = {
        r["jornada"]
        for rows in sched.values()
        for r in rows
    }
    assert 4 in jornadas and 6 in jornadas, jornadas
    assert fixture_for_jornada(sched["3"], 6, now=BEFORE_THU)["opponent_id"] == "5"


def test_played_opponents_skips_unplayed_lower_jornada() -> None:
    played = build_played_opponents(
        _laliga_split_comp(), before_jornada=6, now=BEFORE_THU
    )
    assert played.get("1") == ["2"]
    assert "4" not in (played.get("3") or [])
    assert "20" not in (played.get("10") or [])

    after = build_played_opponents(
        _laliga_split_comp(thursday_played=True), before_jornada=6, now=AFTER_THU
    )
    assert "20" in after.get("10", [])
    assert "4" not in (after.get("3") or [])


def test_laliga_split_gw_aligns_to_j6_before_thursday() -> None:
    sched = build_team_schedule(_laliga_split_comp(), from_jornada=6, now=BEFORE_THU)
    assert resolve_scoring_jornada(sched, now=BEFORE_THU) == 6
    players = [
        {"id": "a", "name": "A", "team_id": "10", "position": "FW"},
        {"id": "b", "name": "B", "team_id": "20", "position": "FW"},
        {"id": "c", "name": "C", "team_id": "3", "position": "MF"},
    ]
    bundle = {
        "matchday": {"jornada": 6, "fixtures_count": 1, "fixtures": []},
        "team_schedule": sched,
        "preview": {
            "a": {
                "gw_opponent_id": "20",
                "gw_is_home": True,
                "gw_kickoff_ts": J6_THU_TS,
            }
        },
    }
    apply_gameweek_to_players(players, bundle, now=BEFORE_THU)
    by_id = {p["id"]: p for p in players}
    assert bundle["matchday"]["scoring_jornada"] == 6
    assert by_id["a"]["next_jornada"] == 6
    assert by_id["a"]["next_opponent_team_id"] == "20"
    assert by_id["b"]["next_jornada"] == 6
    assert by_id["b"]["next_opponent_team_id"] == "10"
    assert by_id["c"]["next_jornada"] == 6
    assert by_id["c"]["next_opponent_team_id"] == "5"
    xi = build_recommended_gw_xi(players, matchday=bundle["matchday"])
    assert xi["jornada"] == 6


def test_laliga_split_gw_aligns_to_j4_after_thursday() -> None:
    sched = build_team_schedule(
        _laliga_split_comp(thursday_played=True), from_jornada=6, now=AFTER_THU
    )
    assert resolve_scoring_jornada(sched, now=AFTER_THU) == 4
    players = [
        {
            "id": "a",
            "name": "A",
            "team_id": "10",
            "position": "FW",
        },
        {"id": "c", "name": "C", "team_id": "3", "position": "MF"},
        {"id": "z", "name": "Z", "team_id": "99", "position": "DF"},
    ]
    apply_gameweek_to_players(
        players,
        {
            "matchday": {"jornada": 6},
            "team_schedule": sched,
            "preview": {
                "a": {
                    "gw_opponent_id": "20",
                    "gw_is_home": True,
                    "gw_kickoff_ts": J6_THU_TS,
                }
            },
            "points": {"a": {"points": 6, "played": True, "status": "played"}},
        },
        now=AFTER_THU,
    )
    by_id = {p["id"]: p for p in players}
    assert by_id["a"]["gw_played"] is True
    assert by_id["a"]["next_jornada"] == 4
    assert by_id["a"]["next_opponent_team_id"] == "30"
    assert by_id["c"]["next_jornada"] == 4
    assert by_id["c"]["next_opponent_team_id"] == "4"
    assert by_id["z"]["next_jornada"] == 4
    assert by_id["z"]["next_opponent_team_id"] == "5"
    xi = build_recommended_gw_xi(
        players, matchday={"jornada": 6, "scoring_jornada": 4}
    )
    assert xi["jornada"] == 4


def test_laliga_split_gw_blanks_team_without_scoring_fixture() -> None:
    sched = build_team_schedule(_laliga_split_comp(), from_jornada=6, now=BEFORE_THU)
    players = [
        {"id": "z", "name": "Z", "team_id": "99", "position": "DF"},
        {"id": "c", "name": "C", "team_id": "3", "position": "MF"},
    ]
    bundle = {
        "matchday": {"jornada": 6, "fixtures": [{"home_id": "10", "away_id": "20"}]},
        "team_schedule": sched,
    }
    apply_gameweek_to_players(players, bundle, now=BEFORE_THU)
    n = apply_blank_gameweek(
        players, bundle["matchday"], team_schedule=sched, now=BEFORE_THU
    )
    by_id = {p["id"]: p for p in players}
    assert by_id["z"]["next_jornada"] is None
    assert by_id["z"]["gw_blank"] is True
    assert by_id["c"].get("gw_blank") in (False, None)
    assert by_id["c"]["next_opponent_team_id"] == "5"
    assert n >= 1


if __name__ == "__main__":
    test_fixture_is_unplayed_by_kickoff_and_status()
    test_next_unplayed_skips_played()
    test_next_unplayed_keeps_later_same_gw()
    test_team_schedule_drops_played()
    test_apply_gw_played_advances_next()
    test_apply_gw_unplayed_keeps_this_gw()
    test_fdr_horizon_skips_played_and_flags_next_gw()
    test_xpts_neutral_when_fdr_is_next_gw()
    test_schedule_keeps_pending_lower_jornada()
    test_played_opponents_skips_unplayed_lower_jornada()
    test_laliga_split_gw_aligns_to_j6_before_thursday()
    test_laliga_split_gw_aligns_to_j4_after_thursday()
    test_laliga_split_gw_blanks_team_without_scoring_fixture()
    print("test_next_fixture: OK")
