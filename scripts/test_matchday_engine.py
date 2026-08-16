"""
Regresión del motor de jornada: racha con huecos, bloque gameweek del feed,
xPts en pretemporada, FDR sin muestra y elección de capitán.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from competitive_actions import (  # noqa: E402
    build_recommended_gw_xi,
    pick_captain,
    priority_score_buy,
    set_matchday_phase,
)
from daily_playbook import build_daily_playbook  # noqa: E402
from league_rules import captain_multiplier_for_price, resolve_captain_rule  # noqa: E402
from model_calibration import build_calibration  # noqa: E402
from expected_points import expected_points, probability_of_playing  # noqa: E402
from external_data import _derive_chollo  # noqa: E402
from fixture_difficulty import (  # noqa: E402
    build_fantasy_conceded,
    build_team_prior,
    build_team_strength,
    fdr_for,
)
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

def _prior_pool() -> list[dict]:
    """Pool de 5 equipos: '1' es el Barça de la muestra y '5' el colista."""
    tiers = {"1": 40_000_000, "2": 20_000_000, "3": 10_000_000, "4": 6_000_000, "5": 3_000_000}
    pool = []
    for tid, value in tiers.items():
        for i in range(14):
            pool.append(
                {
                    "id": f"{tid}-{i}",
                    "team_id": tid,
                    "position": "FW" if i < 4 else "DF",
                    "market_value": value,
                    "ff_prior_avg": value / 4_000_000.0,
                }
            )
    return pool


def test_fdr_without_table_separates_top_from_bottom() -> None:
    # Desde J1: sin clasificación, el prior de plantilla ya distingue rivales
    strength = build_team_strength({}, prior=build_team_prior(_prior_pool()))
    assert strength["source"] == "prior"
    hard = fdr_for("1", position="FW", is_home=True, strength=strength)
    easy = fdr_for("5", position="FW", is_home=True, strength=strength)
    assert hard["fdr"] > easy["fdr"], (hard, easy)
    assert hard["fdr_confidence"] == "prior"
    # El rival mueve del orden de ±20-25%, no un 2%
    assert easy["fdr_multiplier"] / hard["fdr_multiplier"] > 1.25, (easy, hard)


def test_fdr_home_advantage_is_about_five_percent() -> None:
    strength = build_team_strength({}, prior=build_team_prior(_prior_pool()))
    home = fdr_for("3", position="FW", is_home=True, strength=strength)
    away = fdr_for("3", position="FW", is_home=False, strength=strength)
    edge = home["fdr_multiplier"] / away["fdr_multiplier"] - 1.0
    assert 0.08 <= edge <= 0.12, (home, away, edge)


def test_fdr_without_opponent_still_counts_locality() -> None:
    strength = build_team_strength({}, prior=build_team_prior(_prior_pool()))
    info = fdr_for(None, position="FW", is_home=True, strength=strength)
    assert info["fdr_confidence"] == "home_only"
    assert info["fdr_multiplier"] > 1.0, info
    blind = fdr_for(None, position="FW", is_home=None, strength=strength)
    assert blind["fdr_multiplier"] == 1.0


def test_fdr_uses_table_when_there_is_sample() -> None:
    table = {
        "1": {"team_id": "1", "played": 6, "goals_for": 12, "goals_against": 3},
        "2": {"team_id": "2", "played": 6, "goals_for": 3, "goals_against": 12},
    }
    strength = build_team_strength(table)
    assert strength["confidence"] == "high"
    assert strength["source"] == "table"
    easy = fdr_for("2", position="FW", is_home=True, strength=strength)
    hard = fdr_for("1", position="FW", is_home=False, strength=strength)
    assert easy["fdr"] < hard["fdr"], (easy, hard)
    assert easy["fdr_multiplier"] > hard["fdr_multiplier"]


def test_fdr_blends_prior_while_the_table_is_short() -> None:
    # Con 2 jornadas la tabla pesa un tercio: el prior todavía manda
    table = {
        tid: {"team_id": tid, "played": 2, "goals_for": 2, "goals_against": 2}
        for tid in ("1", "2", "3", "4", "5")
    }
    strength = build_team_strength(table, prior=build_team_prior(_prior_pool()))
    assert strength["source"] == "mixed"
    assert strength["table_weight"] == 0.33
    hard = fdr_for("1", position="FW", is_home=True, strength=strength)
    easy = fdr_for("5", position="FW", is_home=True, strength=strength)
    assert hard["fdr"] > easy["fdr"], (hard, easy)


def test_fdr_uses_fantasy_points_conceded() -> None:
    pool = _prior_pool()
    # El equipo 3 encaja goles normales pero regala puntos Mister a los delanteros
    played = {"1": ["3"] * 8, "2": ["3"] * 8, "3": ["1"] * 8}
    for p in pool:
        if p["team_id"] in ("1", "2") and p["position"] == "FW":
            p["recent_gw_points"] = [12] * 8
        elif p["position"] == "FW":
            p["recent_gw_points"] = [2] * 8
    conceded = build_fantasy_conceded(pool, played_opponents=played)
    assert conceded["teams"]["3"]["FW"] > 1.2, conceded
    strength = build_team_strength(
        {}, prior=build_team_prior(pool), conceded=conceded
    )
    info = fdr_for("3", position="FW", is_home=True, strength=strength)
    assert info["fdr_confidence"] == "fantasy"
    assert info["fdr"] < 3.0, info


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
        {
            "player_id": "1",
            "name": "Estrella",
            "position": "FW",
            "xpts": 9.0,
            "p_play": 0.95,
            "price": 6_000_000,
        },
        {
            "player_id": "2",
            "name": "Titular fijo",
            "position": "MF",
            "xpts": 7.0,
            "p_play": 0.96,
            "price": 6_000_000,
        },
        {
            "player_id": "3",
            "name": "Portero",
            "position": "GK",
            "xpts": 5.0,
            "p_play": 0.9,
            "price": 6_000_000,
        },
    ]


def test_captain_multiplier_for_price_tiers() -> None:
    assert captain_multiplier_for_price(2_300_000) == 3.0
    assert captain_multiplier_for_price(4_999_999) == 3.0
    assert captain_multiplier_for_price(5_000_000) == 2.0
    assert captain_multiplier_for_price(9_999_999) == 2.0
    assert captain_multiplier_for_price(10_000_000) == 1.5
    assert captain_multiplier_for_price(19_500_000) == 1.5
    assert captain_multiplier_for_price(None) == 2.0
    assert captain_multiplier_for_price(0) == 2.0


def test_captain_picks_highest_expected_gain() -> None:
    cap = pick_captain(_xi(), multiplier=2.0, mode="fixed")
    assert cap is not None
    assert cap["player_id"] == "1", cap
    assert cap["expected_gain"] == 9.0, cap
    assert cap["alternative"]["player_id"] == "2", cap
    assert "x2" in cap["why"]


def test_captain_avoids_risky_starter() -> None:
    xi = _xi()
    xi[0]["p_play"] = 0.5  # duda seria: el x2 se penaliza a la mitad
    cap = pick_captain(xi, multiplier=2.0, mode="fixed")
    assert cap is not None
    assert cap["player_id"] == "2", cap


def test_captain_scales_with_multiplier() -> None:
    x2 = pick_captain(_xi(), multiplier=2.0, mode="fixed")
    x3 = pick_captain(_xi(), multiplier=3.0, mode="fixed")
    assert x2 is not None and x3 is not None
    assert x3["expected_gain"] == 18.0, x3
    assert x3["expected_gain"] > x2["expected_gain"]


def test_captain_by_market_value_prefers_cheap_x3() -> None:
    """Saka-like x1.5 no debe ganar a un barato con mejor gain a x3."""
    xi = [
        {
            "player_id": "saka",
            "name": "Saka",
            "position": "MF",
            "xpts": 8.86,
            "p_play": 0.95,
            "price": 19_500_000,
        },
        {
            "player_id": "cheap",
            "name": "Mitchell",
            "position": "DF",
            "xpts": 5.0,
            "p_play": 0.95,
            "price": 2_300_000,
        },
    ]
    cap = pick_captain(xi, mode="by_market_value")
    assert cap is not None
    assert cap["player_id"] == "cheap", cap
    assert cap["multiplier"] == 3.0, cap
    assert cap["expected_gain"] == 10.0, cap  # 5 * (3-1)
    # Saka: 8.86 * 0.5 = 4.43


def test_captain_expensive_gets_x15() -> None:
    xi = [
        {
            "player_id": "saka",
            "name": "Saka",
            "position": "MF",
            "xpts": 8.86,
            "p_play": 0.95,
            "price": 19_500_000,
        },
    ]
    cap = pick_captain(xi, mode="by_market_value")
    assert cap is not None
    assert cap["multiplier"] == 1.5, cap
    assert abs(cap["expected_gain"] - 4.43) < 0.01, cap
    assert "x1.5" in cap["why"]


def test_captain_disabled_returns_none() -> None:
    assert pick_captain(_xi(), multiplier=1.0, mode="fixed") is None
    assert pick_captain([], multiplier=2.0, mode="fixed") is None
    # Sin xPts calculados no se capitanea a ciegas
    assert pick_captain([{"player_id": "9", "name": "Sin xpts"}], mode="by_market_value") is None


def test_captain_rule_from_fg_cfg() -> None:
    rule = resolve_captain_rule(fg_cfg={"LEAGUE_CAPTAIN_ENABLED": 1})
    assert rule["enabled"] is True
    assert rule["known"] is True
    assert rule["mode"] == "by_market_value"
    assert rule["multiplier"] is None
    assert rule["source"] == "fg_cfg"
    assert rule["multiplier_source"] == "by_market_value"


def test_captain_rule_admin_and_override() -> None:
    admin = resolve_captain_rule(admin_settings={"is_captain_enabled": 0})
    assert admin["enabled"] is False
    assert admin["multiplier"] == 1.0, admin
    assert admin["mode"] == "off"

    override = resolve_captain_rule(
        fg_cfg={"LEAGUE_CAPTAIN_ENABLED": 1},
        league_cfg={"captain_enabled": True, "captain_multiplier": 3},
    )
    assert override["enabled"] is True
    assert override["mode"] == "fixed"
    assert override["multiplier"] == 3.0
    assert override["source"] == "override"


def test_captain_rule_unknown_defaults_to_off() -> None:
    rule = resolve_captain_rule()
    assert rule["enabled"] is False
    assert rule["known"] is False
    assert rule["mode"] == "off"


# ---------------------------------------------------------------------------
# Fase 8 — señales que antes venían de Comuniate
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Once sin ceros programados
# ---------------------------------------------------------------------------

def _sq_player(pid: str, pos: str, *, xpts: float, p_play: float, **extra) -> dict:
    p = {
        "id": pid,
        "name": f"{pos}{pid}",
        "position": pos,
        "team": "T",
        "xpts": xpts,
        "xpts_p_play": p_play,
        "gw_lineup_prob": p_play * 100.0,
    }
    p.update(extra)
    return p


def test_xi_declares_risk_instead_of_selling_a_starter() -> None:
    # Un solo portero y con 18% de jugar: el hueco existe pero se avisa
    squad = [_sq_player("gk1", "GK", xpts=1.0, p_play=0.18)]
    squad += [_sq_player(f"df{i}", "DF", xpts=5.0, p_play=0.9) for i in range(1, 5)]
    squad += [_sq_player(f"mf{i}", "MF", xpts=6.0, p_play=0.9) for i in range(1, 4)]
    squad += [_sq_player(f"fw{i}", "FW", xpts=7.0, p_play=0.9) for i in range(1, 4)]

    out = build_recommended_gw_xi(squad, formation="4-3-3")
    risky = out["risky_slots"]
    assert [r["player_id"] for r in risky] == ["gk1"], risky
    assert "18%" in (risky[0]["reason"] or ""), risky
    assert out["summary"]["risk_slots"] == 1
    assert out["summary"]["safe_starters"] == 10
    gk_row = next(r for r in out["xi"] if r["position"] == "GK")
    assert gk_row["slot_risk"] is True
    assert gk_row["why"].startswith("Solo 18%"), gk_row["why"]


def test_xi_suggests_formation_that_avoids_the_zero() -> None:
    # Solo 3 defensas fiables: con 4-3-3 entra un cuarto que no juega
    squad = [_sq_player("gk1", "GK", xpts=4.0, p_play=0.95)]
    squad += [_sq_player(f"df{i}", "DF", xpts=5.0, p_play=0.9) for i in range(1, 4)]
    squad.append(_sq_player("df4", "DF", xpts=0.5, p_play=0.1))
    squad += [_sq_player(f"mf{i}", "MF", xpts=6.0, p_play=0.9) for i in range(1, 6)]
    squad += [_sq_player(f"fw{i}", "FW", xpts=7.0, p_play=0.9) for i in range(1, 4)]

    out = build_recommended_gw_xi(squad, formation="4-3-3")
    assert out["summary"]["risk_slots"] == 1
    switch = out["formation_switch"]
    assert switch is not None, out["summary"]
    # A igualdad de riesgo cero gana la de más xPts: 3-4-3 (64) sobre 3-5-2 (63)
    assert switch["formation"] == "3-4-3", switch
    assert switch["risk_slots_after"] == 0, switch
    assert switch["xpts_after"] == 64.0, switch
    assert [d["player_id"] for d in switch["drops"]] == ["df4"], switch


def test_xi_without_risk_does_not_suggest_a_switch() -> None:
    squad = [_sq_player("gk1", "GK", xpts=4.0, p_play=0.95)]
    squad += [_sq_player(f"df{i}", "DF", xpts=5.0, p_play=0.9) for i in range(1, 5)]
    squad += [_sq_player(f"mf{i}", "MF", xpts=6.0, p_play=0.9) for i in range(1, 4)]
    squad += [_sq_player(f"fw{i}", "FW", xpts=7.0, p_play=0.9) for i in range(1, 4)]

    out = build_recommended_gw_xi(squad, formation="4-3-3")
    assert out["risky_slots"] == []
    assert out["formation_switch"] is None


def test_playbook_names_the_risky_starters() -> None:
    xi = {
        "captain_enabled": False,
        "summary": {"complete": True, "xi_count": 11, "xi_target": 11},
        "xi": [],
        "risky_slots": [
            {"player_id": "gk1", "name": "Portero", "position": "GK", "reason": "Solo 18% de jugar"}
        ],
    }
    book = build_daily_playbook(hours_to_jornada=20.0, recommended_xi=xi)
    item = next(c for c in book["checklist"] if c["id"] == "xi_ceros")
    assert item["priority"] == "Alta"
    assert "Portero" in item["detail"]
    assert item["related_player_ids"] == ["gk1"]


# ---------------------------------------------------------------------------
# Calibración: xPts predicho vs puntos reales
# ---------------------------------------------------------------------------

def _snapshots() -> list[dict]:
    # J1 predicha el día 1, cerrada el día 2; J2 abierta el día 3
    return [
        {
            "date": "2026-08-10",
            "jornada": 1,
            "gameweek_status": "pending",
            "xpts": {"1": [8.0, 0.9], "2": [6.0, 0.5], "3": [1.0, 0.2]},
        },
        {
            "date": "2026-08-11",
            "jornada": 1,
            "gameweek_status": "ongoing",
            "xpts": {"1": [99.0, 0.9]},  # con la jornada rodando ya no es predicción
            "gw_points": {"1": 9, "2": 0, "3": 7},
        },
        {
            "date": "2026-08-12",
            "jornada": 2,
            "gameweek_status": "pending",
            "xpts": {"1": [7.0, 0.9]},
        },
    ]


def test_calibration_ignores_the_open_gameweek() -> None:
    rep = build_calibration(_snapshots(), current_jornada=2)
    assert rep["jornadas_measured"] == [1], rep
    assert rep["sample"] == 3, rep


def test_calibration_measures_bias_and_error() -> None:
    rep = build_calibration(_snapshots(), current_jornada=2)
    # errores: 8-9=-1, 6-0=+6, 1-7=-6 → sesgo -0.33, mae 4.33
    assert rep["bias"] == -0.33, rep
    assert rep["mae"] == 4.33, rep
    assert rep["by_p_play"]["titular"]["bias"] == -1.0, rep["by_p_play"]
    assert rep["by_p_play"]["duda"]["bias"] == 6.0, rep["by_p_play"]


def test_calibration_names_the_biggest_misses() -> None:
    rep = build_calibration(
        _snapshots(),
        names={"1": "Acierto", "2": "Humo", "3": "Sorpresa"},
        current_jornada=2,
    )
    last = rep["last_closed"]
    assert last["jornada"] == 1
    assert last["overestimated"][0]["name"] == "Humo", last
    assert last["underestimated"][0]["name"] == "Sorpresa", last
    assert last["hits"][0]["name"] == "Acierto", last


def test_calibration_falls_back_to_a_late_prediction() -> None:
    # Si nunca hubo predicción antes del kickoff, se usa la que haya
    snaps = [
        {
            "date": "2026-08-11",
            "jornada": 1,
            "gameweek_status": "ongoing",
            "xpts": {"1": [5.0, 0.8]},
            "gw_points": {"1": 4},
        },
        {"date": "2026-08-12", "jornada": 2, "gameweek_status": "pending"},
    ]
    rep = build_calibration(snaps, current_jornada=2)
    assert rep["sample"] == 1, rep
    assert rep["bias"] == 1.0, rep


def test_calibration_without_history_says_so() -> None:
    rep = build_calibration([])
    assert rep["status"] == "empty"
    assert rep["last_closed"] is None
    assert "Aún no hay" in rep["reading"]


# ---------------------------------------------------------------------------
# Mercado según fase de la jornada
# ---------------------------------------------------------------------------

def _market_items() -> tuple[dict, dict]:
    board_pick = {
        "player_id": "b",
        "is_board_objective": True,
        "on_daily_market": True,
        "xpts": 2.0,
        "xpts_p_play": 0.9,
        "budget_fit": "comfortable",
    }
    gw_pick = {
        "player_id": "g",
        "on_daily_market": True,
        "xpts": 9.0,
        "xpts_p_play": 0.95,
        "budget_fit": "comfortable",
    }
    return board_pick, gw_pick


def test_market_prefers_the_board_far_from_kickoff() -> None:
    set_matchday_phase("ventana_compra")
    board_pick, gw_pick = _market_items()
    assert priority_score_buy(board_pick) > priority_score_buy(gw_pick)


def test_market_prefers_this_gameweek_near_kickoff() -> None:
    set_matchday_phase("visperas")
    board_pick, gw_pick = _market_items()
    assert priority_score_buy(gw_pick) > priority_score_buy(board_pick)
    set_matchday_phase("ventana_compra")


def test_market_punishes_buying_someone_who_will_not_play() -> None:
    set_matchday_phase("dia_partido")
    playing = {"player_id": "a", "xpts": 6.0, "xpts_p_play": 0.9}
    benched = {"player_id": "b", "xpts": 6.0, "xpts_p_play": 0.2}
    gap = priority_score_buy(playing) - priority_score_buy(benched)
    set_matchday_phase("ventana_compra")
    assert gap >= 50, gap


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
        test_fdr_without_table_separates_top_from_bottom,
        test_fdr_home_advantage_is_about_five_percent,
        test_fdr_without_opponent_still_counts_locality,
        test_fdr_uses_table_when_there_is_sample,
        test_fdr_blends_prior_while_the_table_is_short,
        test_fdr_uses_fantasy_points_conceded,
        test_xpts_preseason_falls_back_to_history,
        test_xpts_without_any_signal_is_conservative,
        test_xpts_injured_is_near_zero,
        test_xpts_scales_with_provider,
        test_captain_multiplier_for_price_tiers,
        test_captain_picks_highest_expected_gain,
        test_captain_avoids_risky_starter,
        test_captain_scales_with_multiplier,
        test_captain_by_market_value_prefers_cheap_x3,
        test_captain_expensive_gets_x15,
        test_captain_disabled_returns_none,
        test_captain_rule_from_fg_cfg,
        test_captain_rule_admin_and_override,
        test_captain_rule_unknown_defaults_to_off,
        test_xi_declares_risk_instead_of_selling_a_starter,
        test_xi_suggests_formation_that_avoids_the_zero,
        test_xi_without_risk_does_not_suggest_a_switch,
        test_playbook_names_the_risky_starters,
        test_calibration_ignores_the_open_gameweek,
        test_calibration_measures_bias_and_error,
        test_calibration_names_the_biggest_misses,
        test_calibration_falls_back_to_a_late_prediction,
        test_calibration_without_history_says_so,
        test_market_prefers_the_board_far_from_kickoff,
        test_market_prefers_this_gameweek_near_kickoff,
        test_market_punishes_buying_someone_who_will_not_play,
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
