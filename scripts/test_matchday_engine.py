"""
Regresión del motor de jornada: racha con huecos, bloque gameweek del feed,
xPts en pretemporada, FDR sin muestra y elección de capitán.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from competitive_actions import pick_captain  # noqa: E402
from expected_points import expected_points, probability_of_playing  # noqa: E402
from external_data import _derive_chollo  # noqa: E402
from fixture_difficulty import build_team_strength, fdr_for  # noqa: E402
from league_rules import resolve_captain_rule  # noqa: E402
from mister_client import (  # noqa: E402
    normalize_sw_player,
    parse_streak_values,
    played_gw_points,
    points_trend_from_gw,
)
from mister_gameweek import (  # noqa: E402
    build_matchday,
    extract_preview,
    parse_feed_fixtures,
    parse_feed_gameweek_id,
)

# HTML reducido del bloque `feed-top-gameweek` (ver cache/probe/feed.html):
# los partidos jugados traen marcador y los pendientes `data-ts` con el kickoff.
FEED_HTML = """
<div data-sw="gameweek/3968" class="feed-top-gameweek">
  <div class="gameweek-matches-inline">
    <button class="btn btn-match match-played" data-sw="gameweek/3968/37155" data-status="played">
      <div class="home"><img src='https://cdn/teams/48.png?version='></div>
      <div class="mid"><div class="score"><span>3</span><span>0</span></div></div>
      <div class="away"><img src='https://cdn/teams/9.png?version='></div>
    </button>
    <button class="btn btn-match match-fixture" data-sw="gameweek/3968/37160" data-status="fixture">
      <div class="home"><img src='https://cdn/teams/1490.png?version='></div>
      <div class="mid"><div class="date tz" data-ts="1786892400">Hoy 17:00</div></div>
      <div class="away"><img src='https://cdn/teams/20.png?version='></div>
    </button>
    <button class="btn btn-match match-fixture" data-sw="gameweek/3968/37157" data-status="fixture">
      <div class="home"><img src='https://cdn/teams/8.png?version='></div>
      <div class="mid"><div class="date tz" data-ts="1786899600">Hoy 19:00</div></div>
      <div class="away"><img src='https://cdn/teams/12.png?version='></div>
    </button>
  </div>
</div>
"""


def _gw_panel() -> dict:
    return {
        "gameweekStatus": {
            "id": 3968,
            "gameweek": 2,
            "status": "live",
            "isLive": True,
            "season": 2026,
            "firstMatchDate": "2026-08-15 19:30:00",
            "lastMatchDate": "2026-08-17 21:00:00",
            "secondsRemainingToStart": 0,
        },
        "games": [
            {
                "id": 37157,
                "id_home": 8,
                "id_away": 12,
                "date": {"ts": 1786899600},
                "status": "fixture",
            },
            {
                "id": 37155,
                "id_home": 48,
                "id_away": 9,
                "date": {"ts": 1786806000},
                "status": "played",
                "goals_home": 3,
                "goals_away": 0,
            },
        ],
        "preview": {
            "37155": {
                "confirmed": 1,
                "players": {
                    "48": [{"id": 111, "name": "Titular local"}],
                    "9": [{"id": 222, "name": "Titular visitante"}],
                },
            }
        },
        "gameweeks": [
            {"id": 3967, "gameweek": 1, "status": "finished", "firstMatchDate": "2026-08-08 19:30:00"},
            {"id": 3968, "gameweek": 2, "status": "live", "firstMatchDate": "2026-08-15 19:30:00"},
        ],
    }


# ---------------------------------------------------------------------------
# Fase 1 — racha con huecos
# ---------------------------------------------------------------------------

def test_streak_with_gaps() -> None:
    raw = [8, "-", 7, None, "0", "2"]
    values = parse_streak_values(raw)
    assert values == [8, None, 7, None, 0, 2], values
    # El hueco es un dato ("no jugó"), no un cero: no debe entrar en la media
    assert played_gw_points(values) == [8, 7, 0, 2], played_gw_points(values)


def test_streak_all_gaps_is_unknown() -> None:
    values = parse_streak_values(["-", "-", "—"])
    assert values == [None, None, None], values
    assert played_gw_points(values) == []
    assert points_trend_from_gw(values) == "unknown"


def test_streak_trend_uses_only_played() -> None:
    assert points_trend_from_gw(parse_streak_values([2, "-", 9])) == "up"
    assert points_trend_from_gw(parse_streak_values([9, "-", 2])) == "down"
    assert points_trend_from_gw(parse_streak_values([5])) == "unknown"


def test_normalize_sw_player_maps_pool_fields() -> None:
    p = normalize_sw_player(
        {
            "id": 4242,
            "name": "Jugador Pool",
            "position": 3,
            "id_team": 7,
            "value": 11_000_000,
            "prev_value": 10_000_000,
            "streak": [8, "-", 7],
            "streak_sum": 15,
            "clausesRank": 4,
            "match_info": {"rival_team_id": 12, "is_home": True},
        }
    )
    assert p is not None
    assert p["recent_gw_points"] == [8, None, 7], p["recent_gw_points"]
    assert p["gw_points_sum"] == 15
    assert p["prev_market_value"] == 10_000_000
    assert p["price_delta_1d"] == 0.1, p["price_delta_1d"]
    assert p["next_opponent_team_id"] == "12"
    assert p["next_is_home"] is True
    assert p["clause_rank"] == 4


# ---------------------------------------------------------------------------
# Fase 2 — bloque gameweek
# ---------------------------------------------------------------------------

def test_parse_feed_gameweek_id() -> None:
    assert parse_feed_gameweek_id(FEED_HTML) == "3968"
    assert parse_feed_gameweek_id("") is None


def test_parse_feed_fixtures_keeps_kickoff_and_teams() -> None:
    fixtures = parse_feed_fixtures(FEED_HTML)
    # El jugado no lleva data-ts: no aporta kickoff y se descarta
    assert len(fixtures) == 2, fixtures
    first = fixtures[0]
    assert first["id"] == "37160"
    assert first["home_id"] == "1490"
    assert first["away_id"] == "20"
    assert first["kickoff_ts"] == 1786892400
    assert first["status"] == "fixture"


def test_build_matchday_sorts_by_kickoff() -> None:
    md = build_matchday(_gw_panel(), team_label=lambda tid: f"T{tid}", competition="laliga")
    assert md["status"] == "ok"
    assert md["jornada"] == 2
    assert md["gameweek_status"] == "live"
    assert md["is_live"] is True
    assert md["fixtures_count"] == 2
    assert [f["id"] for f in md["fixtures"]] == ["37155", "37157"], md["fixtures"]
    assert md["fixtures"][0]["home"] == "T48"
    assert md["first_match"] == "2026-08-15T19:30:00"


def test_build_matchday_without_data_is_unavailable() -> None:
    md = build_matchday(None)
    assert md["status"] == "unavailable"
    assert md["fixtures"] == []


def test_extract_preview_marks_probable_xi() -> None:
    preview = extract_preview(_gw_panel())
    assert set(preview) == {"111", "222"}, preview
    home = preview["111"]
    assert home["gw_probable_xi"] is True
    assert home["gw_confirmed"] is True
    assert home["gw_is_home"] is True
    assert home["gw_opponent_id"] == "9"
    assert preview["222"]["gw_is_home"] is False
    assert preview["222"]["gw_opponent_id"] == "48"


# ---------------------------------------------------------------------------
# Fase 4/5 — FDR y xPts en pretemporada
# ---------------------------------------------------------------------------

def test_fdr_neutral_without_sample() -> None:
    strength = build_team_strength({})
    assert strength["confidence"] == "none"
    info = fdr_for("12", position="FW", is_home=True, strength=strength)
    assert info["fdr"] == 3.0
    assert info["fdr_multiplier"] == 1.0
    assert "Sin muestra" in info["fdr_why"]


def test_fdr_uses_table_when_there_is_sample() -> None:
    table = {
        "1": {"team_id": "1", "played": 6, "goals_for": 12, "goals_against": 3},
        "2": {"team_id": "2", "played": 6, "goals_for": 3, "goals_against": 12},
    }
    strength = build_team_strength(table)
    assert strength["confidence"] == "high"
    easy = fdr_for("2", position="FW", is_home=True, strength=strength)
    hard = fdr_for("1", position="FW", is_home=False, strength=strength)
    assert easy["fdr"] < hard["fdr"], (easy, hard)
    assert easy["fdr_multiplier"] > hard["fdr_multiplier"]


def test_xpts_preseason_falls_back_to_history() -> None:
    # Pretemporada: sin racha ni media de la temporada, solo histórico FF
    player = {
        "id": "1",
        "position": "MF",
        "price": 10_000_000,
        "gw_lineup_prob": 85,
        "external": {"availability": "available", "ff_prior_avg": 6.0},
    }
    out = expected_points(player)
    assert out["xpts_base"] == 6.0, out
    assert out["xpts_p_play"] == 0.85, out
    assert out["xpts"] == 5.1, out
    assert out["xpts_floor"] < out["xpts"]
    assert "histórico FF" in out["xpts_why"]


def test_xpts_without_any_signal_is_conservative() -> None:
    out = expected_points({"id": "2", "position": "DF", "price": 5_000_000})
    # Escala Mixto (8) x 0.55 de producción, x 0.45 de probabilidad desconocida
    assert out["xpts_base"] == 4.4, out
    assert out["xpts_p_play"] == 0.45, out
    assert "sin histórico" in out["xpts_why"]


def test_xpts_injured_is_near_zero() -> None:
    player = {
        "id": "3",
        "position": "FW",
        "mister_avg": 9.0,
        "recent_gw_points": [10, 12, 9],
        "external": {"availability": "injured"},
    }
    p_play, why = probability_of_playing(player)
    assert p_play <= 0.05, (p_play, why)
    out = expected_points(player)
    assert out["xpts"] < 1.0, out


def test_xpts_scales_with_provider() -> None:
    player = {"id": "4", "position": "MF", "external": {}}
    mixto = expected_points(player, league_rules={"avg_scale": 8.0})
    rpg = expected_points(player, league_rules={"avg_scale": 16.0})
    assert rpg["xpts"] > mixto["xpts"] * 1.9, (mixto, rpg)


# ---------------------------------------------------------------------------
# Fase 6 — capitán
# ---------------------------------------------------------------------------

def _xi() -> list[dict]:
    return [
        {"player_id": "1", "name": "Estrella", "position": "FW", "xpts": 9.0, "p_play": 0.95},
        {"player_id": "2", "name": "Titular fijo", "position": "MF", "xpts": 7.0, "p_play": 0.96},
        {"player_id": "3", "name": "Portero", "position": "GK", "xpts": 5.0, "p_play": 0.9},
    ]


def test_captain_picks_highest_expected_gain() -> None:
    cap = pick_captain(_xi(), multiplier=2.0)
    assert cap is not None
    assert cap["player_id"] == "1", cap
    assert cap["expected_gain"] == 9.0, cap
    assert cap["alternative"]["player_id"] == "2", cap
    assert "x2" in cap["why"]


def test_captain_avoids_risky_starter() -> None:
    xi = _xi()
    xi[0]["p_play"] = 0.5  # duda seria: el x2 se penaliza a la mitad
    cap = pick_captain(xi, multiplier=2.0)
    assert cap is not None
    assert cap["player_id"] == "2", cap


def test_captain_scales_with_multiplier() -> None:
    x2 = pick_captain(_xi(), multiplier=2.0)
    x3 = pick_captain(_xi(), multiplier=3.0)
    assert x2 is not None and x3 is not None
    assert x3["expected_gain"] == 18.0, x3
    assert x3["expected_gain"] > x2["expected_gain"]


def test_captain_disabled_returns_none() -> None:
    assert pick_captain(_xi(), multiplier=1.0) is None
    assert pick_captain([], multiplier=2.0) is None
    # Sin xPts calculados no se capitanea a ciegas
    assert pick_captain([{"player_id": "9", "name": "Sin xpts"}], multiplier=2.0) is None


def test_captain_rule_from_fg_cfg() -> None:
    rule = resolve_captain_rule(fg_cfg={"LEAGUE_CAPTAIN_ENABLED": 1})
    assert rule["enabled"] is True
    assert rule["known"] is True
    assert rule["multiplier"] == 2.0
    assert rule["source"] == "fg_cfg"
    assert rule["multiplier_source"] == "default"


def test_captain_rule_admin_and_override() -> None:
    admin = resolve_captain_rule(admin_settings={"is_captain_enabled": 0})
    assert admin["enabled"] is False
    assert admin["multiplier"] == 1.0, admin

    override = resolve_captain_rule(
        fg_cfg={"LEAGUE_CAPTAIN_ENABLED": 1},
        league_cfg={"captain_enabled": True, "captain_multiplier": 3},
    )
    assert override["enabled"] is True
    assert override["multiplier"] == 3.0
    assert override["source"] == "override"


def test_captain_rule_unknown_defaults_to_off() -> None:
    rule = resolve_captain_rule()
    assert rule["enabled"] is False
    assert rule["known"] is False


# ---------------------------------------------------------------------------
# Fase 8 — señales que antes venían de Comuniate
# ---------------------------------------------------------------------------

def test_chollo_derived_from_price_drop_and_production() -> None:
    assert _derive_chollo({"price_delta_1d": -0.03, "recent_gw_points": [6, 5, None, 7]}) is True
    # Baja de precio pero no produce
    assert _derive_chollo({"price_delta_1d": -0.03, "recent_gw_points": [1, 0, 2]}) is False
    # Produce pero está subiendo: ya no es chollo
    assert _derive_chollo({"price_delta_1d": 0.04, "recent_gw_points": [8, 9]}) is False
    assert _derive_chollo({}) is False


def main() -> None:
    tests = [
        test_streak_with_gaps,
        test_streak_all_gaps_is_unknown,
        test_streak_trend_uses_only_played,
        test_normalize_sw_player_maps_pool_fields,
        test_parse_feed_gameweek_id,
        test_parse_feed_fixtures_keeps_kickoff_and_teams,
        test_build_matchday_sorts_by_kickoff,
        test_build_matchday_without_data_is_unavailable,
        test_extract_preview_marks_probable_xi,
        test_fdr_neutral_without_sample,
        test_fdr_uses_table_when_there_is_sample,
        test_xpts_preseason_falls_back_to_history,
        test_xpts_without_any_signal_is_conservative,
        test_xpts_injured_is_near_zero,
        test_xpts_scales_with_provider,
        test_captain_picks_highest_expected_gain,
        test_captain_avoids_risky_starter,
        test_captain_scales_with_multiplier,
        test_captain_disabled_returns_none,
        test_captain_rule_from_fg_cfg,
        test_captain_rule_admin_and_override,
        test_captain_rule_unknown_defaults_to_off,
        test_chollo_derived_from_price_drop_and_production,
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
