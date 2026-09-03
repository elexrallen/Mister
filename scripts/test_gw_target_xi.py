"""Once objetivo de jornada: pool, cobertura, cruce y prior encogido."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
from expected_points import expected_points, production_base  # noqa: E402
from gw_target_xi import (  # noqa: E402
    build_gw_target_xi,
    merge_target_universe,
    resolve_ownership,
)
from matchup_context import annotate_players_with_matchup, build_matchup  # noqa: E402
from mister_gameweek import build_played_fixtures  # noqa: E402


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _player(
    pid: str,
    pos: str,
    *,
    xpts: float,
    team: str = "1",
    owner: str | None = None,
    name: str | None = None,
    **extra: object,
) -> dict:
    row = {
        "id": pid,
        "name": name or f"P{pid}",
        "position": pos,
        "team": f"Club {team}",
        "team_id": str(team),
        "xpts": xpts,
        "xpts_p_play": 0.85,
        "xpts_why": f"{xpts:.1f} pts esperados",
        "gw_lineup_prob": 85,
        "price": 8_000_000,
        "fdr": 2.2,
        "fdr_multiplier": 1.1,
        "fdr_label": "favorable",
        "fdr_why": "Favorable vs el rival por puntos que concede el rival",
        "opponent_name": "Girona",
        "is_home": True,
        "next_opponent_team_id": "9",
    }
    if owner is not None:
        row["owner_id"] = owner
    row.update(extra)
    return row


def _pool_of_eleven() -> list[dict]:
    shape = [("g1", "GK"), ("d1", "DF"), ("d2", "DF"), ("d3", "DF"), ("d4", "DF"),
             ("m1", "MF"), ("m2", "MF"), ("m3", "MF"), ("f1", "FW"), ("f2", "FW"), ("f3", "FW")]
    return [
        _player(pid, pos, xpts=9.0 - i * 0.1, team=str(i + 1))
        for i, (pid, pos) in enumerate(shape)
    ]


def test_assembles_eleven_from_pool_without_ownership() -> None:
    pool = _pool_of_eleven() + [_player("bench", "FW", xpts=1.0)]
    out = build_gw_target_xi(pool, squad=[], me={"team_id": "me", "balance": 0})
    xi = out.get("xi") or []
    _assert(len(xi) == 11, xi)
    _assert((out.get("summary") or {}).get("complete") is True, out.get("summary"))
    _assert(out.get("formation"), out)
    ids = {str(r.get("player_id")) for r in xi}
    _assert("bench" not in ids, ids)
    _assert(all(r.get("ownership") != "owned" for r in xi), xi)
    cov = out.get("coverage") or {}
    _assert(cov.get("owned_count") == 0, cov)
    _assert(len(cov.get("missing_slots") or []) == 11, cov)


def test_coverage_eleven_of_eleven() -> None:
    pool = _pool_of_eleven()
    squad = [dict(p, owner_id="me") for p in pool]
    rec = {
        "xi": [{"player_id": p["id"], "position": p["position"], "xpts": p["xpts"]} for p in pool],
        "summary": {"xpts_total": 90.0},
    }
    out = build_gw_target_xi(
        squad,
        squad=squad,
        recommended_xi=rec,
        me={"team_id": "me", "balance": 1_000_000},
    )
    cov = out.get("coverage") or {}
    _assert(cov.get("owned_count") == 11, cov)
    _assert(cov.get("missing_slots") == [], cov)


def test_blank_and_injured_stay_out() -> None:
    pool = _pool_of_eleven()
    pool.append(
        _player(
            "star",
            "FW",
            xpts=20.0,
            gw_blank=True,
            gw_out=True,
            xpts_p_play=0.02,
        )
    )
    pool.append(
        _player(
            "hurt",
            "FW",
            xpts=19.0,
            injury=True,
            external={"availability": "injured"},
            xpts_p_play=0.02,
        )
    )
    out = build_gw_target_xi(pool, squad=[], me={"team_id": "me"})
    ids = {str(r.get("player_id")) for r in (out.get("xi") or [])}
    _assert("star" not in ids, ids)
    _assert("hurt" not in ids, ids)


def test_matchup_does_not_change_xpts() -> None:
    player = _player(
        "1",
        "FW",
        xpts=7.5,
        recent_gw_points=[9, 4],
        team="1",
        next_opponent_team_id="2",
    )
    before = float(player["xpts"])
    annotate_players_with_matchup(
        [player],
        played_fixtures={
            "1": [
                {"jornada": 1, "opponent_id": "2", "is_home": False},
                {"jornada": 2, "opponent_id": "3", "is_home": True},
            ]
        },
    )
    _assert(player["xpts"] == before, player)
    mu = player.get("matchup") or {}
    _assert(mu.get("vs_opponent") and mu["vs_opponent"]["n"] == 1, mu)
    _assert(mu["vs_opponent"]["last"]["points"] == 9, mu)
    _assert("vs este rival" in (mu.get("why") or ""), mu)


def test_home_away_split_needs_three_each() -> None:
    tagged_home = [
        {"is_home": True, "points": 8},
        {"is_home": True, "points": 7},
        {"is_home": False, "points": 3},
        {"is_home": False, "points": 2},
    ]
    player = _player("1", "FW", xpts=6, recent_gw_points=[8, 7, 3, 2], team="1")
    mu = build_matchup(
        player,
        played_fixtures={
            "1": [
                {"jornada": 1, "opponent_id": "2", "is_home": True},
                {"jornada": 2, "opponent_id": "3", "is_home": True},
                {"jornada": 3, "opponent_id": "4", "is_home": False},
                {"jornada": 4, "opponent_id": "5", "is_home": False},
            ]
        },
    )
    _assert(mu.get("home_away_split") is None, mu)
    _assert(len(tagged_home) == 4, tagged_home)


def test_played_fixtures_tags_home_away() -> None:
    comp = {
        "games": {
            "1": [{"id_home": "10", "id_away": "20", "goals_home": 2, "goals_away": 0}],
            "2": [{"id_home": "20", "id_away": "10", "goals_home": 1, "goals_away": 1}],
        }
    }
    fx = build_played_fixtures(comp, before_jornada=3)
    _assert(len(fx["10"]) == 2, fx)
    _assert(fx["10"][0]["is_home"] is True, fx["10"][0])
    _assert(fx["10"][1]["is_home"] is False, fx["10"][1])


def test_prior_shrinks_short_sample() -> None:
    slump = {
        "id": "1",
        "position": "FW",
        "gw_lineup_prob": 90,
        "recent_gw_points": [2, 1],
        "mister_avg": 1.5,
        "external": {"ff_mister_avg": 7.2},
    }
    hot = {
        "id": "2",
        "position": "FW",
        "gw_lineup_prob": 90,
        "recent_gw_points": [12, 11],
        "mister_avg": 11.5,
        "external": {"ff_mister_avg": 5.0},
    }
    base_s, why_s = production_base(slump, 8.0)
    base_h, why_h = production_base(hot, 8.0)
    _assert(base_s > 5.5, (base_s, why_s))
    _assert("histórico FF" in why_s and "aún no manda" in why_s, why_s)
    _assert(base_h < 7.5, (base_h, why_h))
    _assert(base_s > base_h, (base_s, base_h))
    out = expected_points(slump)
    _assert("histórico FF" in out["xpts_why"], out)


def test_ownership_reachable() -> None:
    own, reach = resolve_ownership(
        {"id": "1", "owner_id": "me"},
        my_id="me",
        squad_ids={"1"},
        balance=0,
        clauses_enabled=True,
    )
    _assert(own == "owned" and reach == "", (own, reach))
    own, reach = resolve_ownership(
        {"id": "2", "on_daily_market": True, "seller": "market", "owner_id": "0"},
        my_id="me",
        squad_ids=set(),
        balance=0,
        clauses_enabled=True,
    )
    _assert(own == "daily_market" and reach == "daily_market", (own, reach))
    own, reach = resolve_ownership(
        {"id": "3", "owner_id": "99", "clause": 4_000_000, "clause_known": True},
        my_id="me",
        squad_ids=set(),
        balance=5_000_000,
        clauses_enabled=True,
    )
    _assert(own == "rival" and reach == "clause", (own, reach))
    own, reach = resolve_ownership(
        {"id": "4", "owner_id": "99", "clause": 40_000_000, "clause_known": True},
        my_id="me",
        squad_ids=set(),
        balance=5_000_000,
        clauses_enabled=True,
    )
    _assert(own == "rival" and reach == "no", (own, reach))
    own, reach = resolve_ownership(
        {"id": "4b", "owner_id": "99", "clause": 8_000_000, "clause_known": True},
        my_id="me",
        squad_ids=set(),
        balance=2_000_000,
        clauses_enabled=True,
        max_debt=20_000_000,
    )
    _assert(own == "rival" and reach == "clause", (own, reach))
    own, reach = resolve_ownership(
        {"id": "5", "owner_id": "99", "on_daily_market": True, "seller": "market"},
        my_id="me",
        squad_ids=set(),
        balance=0,
        clauses_enabled=True,
    )
    _assert(own == "daily_market" and reach == "daily_market", (own, reach))
    own, reach = resolve_ownership(
        {"id": "6", "owner_id": "99"},
        my_id="me",
        squad_ids=set(),
        balance=0,
        clauses_enabled=False,
        market_ids={"6"},
    )
    _assert(own == "daily_market" and reach == "daily_market", (own, reach))


def test_near_slot_does_not_count_as_owned() -> None:
    target = _player("star", "FW", xpts=10.0, owner="99")
    mine = _player("mine", "FW", xpts=9.0, owner="me")
    pool = _pool_of_eleven()
    pool = [target if p["id"] == "f1" else p for p in pool]
    rec = {"xi": [{"player_id": "mine", "position": "FW", "xpts": 9.0}], "summary": {"xpts_total": 40}}
    out = build_gw_target_xi(
        pool + [mine],
        squad=[mine],
        recommended_xi=rec,
        me={"team_id": "me", "balance": 0},
        league_rules={"clauses": False},
    )
    cov = out.get("coverage") or {}
    missing = [s for s in (cov.get("missing_slots") or []) if s.get("player_id") == "star"]
    _assert(missing, cov)
    _assert(missing[0].get("near") is True, missing[0])
    _assert(cov.get("owned_count") == 0 or "star" not in (cov.get("owned_ids") or []), cov)


def test_merge_daily_market_wins_over_rival_owner() -> None:
    pool = _pool_of_eleven()
    raw = next(p for p in pool if p["id"] == "f1")
    raw["owner_id"] = "99"
    raw["owner_name"] = "Rival"
    raw["on_daily_market"] = False
    overlay = {
        "id": "f1",
        "on_daily_market": True,
        "seller": "market",
        "owner_id": "99",
        "owner_name": "Rival",
        "gw_lineup_prob": 85,
        "xpts": 8.5,
        "xpts_p_play": 0.85,
    }
    merged = merge_target_universe(pool, [overlay])
    star = next(p for p in merged if p["id"] == "f1")
    _assert(star.get("on_daily_market") is True, star)
    _assert(star.get("gw_lineup_prob") == 85, star)
    out = build_gw_target_xi(merged, squad=[], me={"team_id": "me", "balance": 0})
    row = next(r for r in (out.get("xi") or []) if r.get("player_id") == "f1")
    _assert(row.get("ownership") == "daily_market", row)
    _assert(row.get("reachable") == "daily_market", row)
    why = row.get("why") or ""
    _assert("Titular" in why, why)
    _assert("Girona" in why, why)
    _assert(why.count("Girona") == 1, why)
    _assert("Favorable vs el rival por puntos" not in why, why)
    _assert("pts esperados" not in why, why)


def test_merged_ff_previa_beats_unknown_p_play() -> None:
    template = {
        "position": "FW",
        "external": {"ff_mister_avg": 7.0},
        "fdr_multiplier": 1.0,
        "mister_avg": 7.0,
    }
    pool_star = {**template, "id": "star"}
    overlay = {**template, "id": "star", "gw_lineup_prob": 85}
    unknown = {**template, "id": "unk"}
    merged = merge_target_universe([pool_star, unknown], [overlay])
    star = next(p for p in merged if p["id"] == "star")
    unk = next(p for p in merged if p["id"] == "unk")
    _assert(star.get("gw_lineup_prob") == 85, star)
    xs = expected_points(star)
    xu = expected_points(unk)
    _assert(xs["xpts"] > xu["xpts"], (xs, xu))
    _assert(abs((xs.get("xpts_p_play") or 0) - 0.85) < 0.02, xs)
    _assert(abs((xu.get("xpts_p_play") or 0) - 0.45) < 0.02, xu)

    pool = _pool_of_eleven()
    raw = next(p for p in pool if p["id"] == "f1")
    raw.pop("gw_lineup_prob", None)
    raw["xpts"] = 3.6
    raw["xpts_p_play"] = 0.45
    unknown_fw = _player("unk", "FW", xpts=5.0, team="21")
    unknown_fw.pop("gw_lineup_prob", None)
    unknown_fw["xpts_p_play"] = 0.45
    ff_overlay = {
        "id": "f1",
        "gw_lineup_prob": 85,
        "xpts": 8.5,
        "xpts_p_play": 0.85,
    }
    merged_xi = merge_target_universe(pool + [unknown_fw], [ff_overlay])
    out = build_gw_target_xi(merged_xi, squad=[], me={"team_id": "me"})
    ids = {str(r.get("player_id")) for r in (out.get("xi") or [])}
    _assert("f1" in ids, ids)
    _assert("unk" not in ids, ids)


def test_gw_target_ignores_me_formation() -> None:
    """El pool 4-3-3 gana; me.formation 5-4-1 no ancla el once objetivo."""
    pool = _pool_of_eleven()
    out = build_gw_target_xi(
        pool,
        squad=[],
        me={"team_id": "me", "formation": "5-4-1"},
    )
    shape = out.get("shape") or {}
    _assert(int(shape.get("DF") or 0) == 4, shape)
    _assert(int(shape.get("MF") or 0) == 3, shape)
    _assert(int(shape.get("FW") or 0) == 3, shape)
    form = str(out.get("formation") or "")
    _assert("5-4-1" not in form, form)
    _assert(len(out.get("xi") or []) == 11, out.get("xi"))


def test_ideal_formations_skips_paid_and_noise() -> None:
    names = [str(x) for x in (getattr(config, "IDEAL_FORMATIONS", ()) or ())]
    _assert("4-2-4" not in names, names)
    _assert("4-2-3-1" not in names, names)
    _assert("3-4-3" in names, names)
    _assert("4-3-3" in names, names)
    _assert("3-3-4" in names, names)


if __name__ == "__main__":
    test_assembles_eleven_from_pool_without_ownership()
    test_coverage_eleven_of_eleven()
    test_blank_and_injured_stay_out()
    test_matchup_does_not_change_xpts()
    test_home_away_split_needs_three_each()
    test_played_fixtures_tags_home_away()
    test_prior_shrinks_short_sample()
    test_ownership_reachable()
    test_near_slot_does_not_count_as_owned()
    test_merge_daily_market_wins_over_rival_owner()
    test_merged_ff_previa_beats_unknown_p_play()
    test_gw_target_ignores_me_formation()
    test_ideal_formations_skips_paid_and_noise()
    print("test_gw_target_xi: OK")
