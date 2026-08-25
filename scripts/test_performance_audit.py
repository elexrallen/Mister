"""
Regresión del auditor de rendimiento: ranking, umbrales, once y mercado.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_engine import build_price_history_snapshot  # noqa: E402
from performance_audit import (  # noqa: E402
    apply_gates,
    audit_league,
    evaluate_market,
    evaluate_pipeline,
    evaluate_xi,
    format_markdown,
    ranking_quality,
    slim_decisions,
    slim_report,
    spearman_rho,
)


def test_spearman_perfect_and_inverse() -> None:
    assert spearman_rho([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]) == 1.0
    assert spearman_rho([1, 2, 3, 4, 5], [10, 8, 6, 4, 2]) == -1.0


def test_ranking_lift_prefers_high_xpts() -> None:
    rows = (
        [{"xpts": 8.0, "real": 9.0} for _ in range(6)]
        + [{"xpts": 2.0, "real": 1.0} for _ in range(6)]
    )
    rep = ranking_quality(rows)
    assert rep["status"] == "ok", rep
    assert rep["spearman"] == 1.0, rep
    assert rep["lift"] is not None and rep["lift"] > 2, rep


def test_ranking_empty_when_too_few() -> None:
    rep = ranking_quality([{"xpts": 1, "real": 1}, {"xpts": 2, "real": 2}])
    assert rep["status"] in ("empty", "thin")
    assert rep["spearman"] is None


def _closed_gw_snaps() -> list[dict]:
    """J1 predicha, luego cerrada; J2 abierta (se ignora)."""
    decisions = {
        "xi_ids": ["a", "b", "c"],
        "current_starter_ids": ["a", "b", "d"],
        "squad_ids": ["a", "b", "c", "d", "e"],
        "captain_id": "a",
        "actions": [
            {"player_id": "a", "action": "buy_now", "price": 100},
            {"player_id": "c", "action": "buy_now", "price": 50},
            {"player_id": "f", "action": "buy_now", "price": 40},
            {"player_id": "e", "action": "avoid", "price": 80},
            {"player_id": "d", "action": "avoid", "price": 90},
            {"player_id": "g", "action": "avoid", "price": 70},
        ],
    }
    return [
        {
            "date": "2026-08-10",
            "jornada": 1,
            "gameweek_status": "pending",
            "prices": {"a": 100, "b": 90, "c": 50, "d": 200, "e": 80, "f": 40, "g": 70},
            "xpts": {
                "a": [8.0, 0.9],
                "b": [6.0, 0.85],
                "c": [5.0, 0.8],
                "d": [4.0, 0.7],
                "e": [1.0, 0.2],
                "f": [4.5, 0.75],
                "g": [1.5, 0.25],
            },
            "decisions": decisions,
        },
        {
            "date": "2026-08-12",
            "jornada": 1,
            "gameweek_status": "ongoing",
            "prices": {"a": 110, "b": 90, "c": 60, "d": 180, "e": 70, "f": 48, "g": 60},
            "gw_points": {"a": 10, "b": 7, "c": 6, "d": 1, "e": 0, "f": 5, "g": 0},
            "xpts": {"a": [99.0, 0.9]},
        },
        {
            "date": "2026-08-13",
            "jornada": 2,
            "gameweek_status": "pending",
            "xpts": {"a": [7.0, 0.9]},
            "decisions": {"xi_ids": ["a"]},
        },
    ]


def test_xi_recommended_beats_current_and_price_naive() -> None:
    rep = evaluate_xi(_closed_gw_snaps(), current_jornada=2)
    assert rep["status"] == "ok", rep
    assert rep["sample_gws"] == 1, rep
    # rec a+b+c = 23; current a+b+d = 18; naive price d,a,b = 1+10+7 = 18
    assert rep["recommended_pts"] == 23, rep
    assert rep["current_pts"] == 18, rep
    assert rep["naive_price_pts"] == 18, rep
    assert rep["gap_vs_current_pct"] > 0, rep


def test_xi_fails_when_worse_than_aligned() -> None:
    snaps = _closed_gw_snaps()
    snaps[0]["decisions"]["xi_ids"] = ["d", "e"]  # 1+0
    snaps[0]["decisions"]["current_starter_ids"] = ["a", "b", "c"]  # 23
    rep = evaluate_xi(snaps, current_jornada=2)
    assert rep["status"] == "fail", rep
    assert rep["gap_vs_current_pct"] is not None and rep["gap_vs_current_pct"] < -15, rep


def test_xi_empty_without_decisions() -> None:
    snaps = [
        {"date": "2026-08-10", "jornada": 1, "gameweek_status": "pending", "xpts": {"a": [5, 0.9]}},
        {"date": "2026-08-11", "jornada": 1, "gameweek_status": "ongoing", "gw_points": {"a": 4}},
    ]
    assert evaluate_xi(snaps)["status"] == "empty"


def test_market_buy_now_beats_avoid() -> None:
    rep = evaluate_market(_closed_gw_snaps(), current_jornada=2)
    assert rep["status"] == "ok", rep
    assert rep["buy_now_pts"] is not None and rep["avoid_pts"] is not None, rep
    assert rep["buy_now_pts"] > rep["avoid_pts"], rep


def test_market_fails_when_avoids_score_more() -> None:
    snaps = _closed_gw_snaps()
    snaps[1]["gw_points"] = {"a": 0, "c": 1, "f": 0, "e": 9, "d": 8, "g": 9, "b": 0}
    rep = evaluate_market(snaps, current_jornada=2)
    assert rep["status"] == "fail", rep


def test_market_ignores_mid_gw_null_status() -> None:
    """Un snapshot mid-GW sin gameweek_status no es predicción pre-partido."""
    snaps = [
        {
            "date": "2026-08-24",
            "jornada": 1,
            "gameweek_status": None,
            "prices": {"x": 100, "y": 100, "z": 100, "u": 100, "v": 100, "w": 100},
            "decisions": {
                "actions": [
                    {"player_id": "x", "action": "buy_now", "price": 100},
                    {"player_id": "y", "action": "buy_now", "price": 100},
                    {"player_id": "z", "action": "buy_now", "price": 100},
                    {"player_id": "u", "action": "avoid", "price": 100},
                    {"player_id": "v", "action": "avoid", "price": 100},
                    {"player_id": "w", "action": "avoid", "price": 100},
                ]
            },
        },
        {
            "date": "2026-08-25",
            "jornada": 1,
            "gameweek_status": "finished",
            "gw_points": {"x": 0, "y": 0, "z": 0, "u": 9, "v": 8, "w": 7},
            "prices": {"x": 90, "y": 90, "z": 90, "u": 110, "v": 110, "w": 110},
        },
    ]
    rep = evaluate_market(snaps, current_jornada=2)
    assert rep["status"] in ("empty", "thin"), rep
    assert rep["status"] != "fail", rep


def test_pipeline_flags_mock_and_rate_limit() -> None:
    ok = evaluate_pipeline(
        {
            "sources": {
                "mister": "api",
                "honest_live": True,
                "external": {"matched": 200, "rate_limited": None},
            },
            "meta": {"pipeline_seconds": 120},
        }
    )
    assert ok["status"] == "ok", ok
    bad = evaluate_pipeline(
        {
            "sources": {
                "mister": "mock",
                "external": {"matched": 10, "rate_limited": "futbolfantasy"},
            }
        }
    )
    assert bad["status"] == "fail", bad
    assert any("mock" in i for i in bad["issues"])


def test_audit_league_passes_healthy_history() -> None:
    # 30+ pares para no saltar umbrales por muestra fina
    pending = {
        "date": "2026-08-10",
        "jornada": 1,
        "gameweek_status": "pending",
        "xpts": {str(i): [6.0 if i < 20 else 2.0, 0.85 if i < 20 else 0.3] for i in range(40)},
        "decisions": {
            "xi_ids": [str(i) for i in range(11)],
            "current_starter_ids": [str(i) for i in range(11)],
            "squad_ids": [str(i) for i in range(20)],
            "actions": [
                {"player_id": "0", "action": "buy_now", "price": 10},
                {"player_id": "1", "action": "buy_now", "price": 10},
                {"player_id": "2", "action": "buy_now", "price": 10},
                {"player_id": "30", "action": "avoid", "price": 10},
                {"player_id": "31", "action": "avoid", "price": 10},
                {"player_id": "32", "action": "avoid", "price": 10},
            ],
        },
        "prices": {str(i): float(100 - i) for i in range(40)},
    }
    closed = {
        "date": "2026-08-12",
        "jornada": 1,
        "gameweek_status": "ongoing",
        "gw_points": {str(i): (8 if i < 20 else 1) for i in range(40)},
        "prices": {str(i): float(110 - i) for i in range(40)},
    }
    latest = {
        "matchday": {"jornada": 2},
        "sources": {"mister": "api", "external": {"matched": 80, "rate_limited": None}},
        "meta": {"pipeline_seconds": 90},
    }
    rep = audit_league([pending, closed], latest=latest, current_jornada=2, slug="demo")
    assert rep["status"] in ("ok", "warn"), rep
    failed = [g["id"] for g in rep["gates"] if not g["ok"] and not g["skip"]]
    assert failed == [], (failed, rep)
    slim = slim_report(rep)
    assert "per_jornada" not in slim["xi"]
    md = format_markdown(rep)
    assert "demo" in md
    assert "Spearman" in md


def test_gates_fail_on_anti_predictive_model() -> None:
    pending = {
        "date": "2026-08-10",
        "jornada": 1,
        "gameweek_status": "pending",
        "xpts": {str(i): [10.0 - i * 0.2, 0.9] for i in range(40)},
    }
    closed = {
        "date": "2026-08-12",
        "jornada": 1,
        "gameweek_status": "ongoing",
        "gw_points": {str(i): i * 0.5 for i in range(40)},
    }
    rep = audit_league([pending, closed], current_jornada=2)
    assert rep["ranking"]["spearman"] is not None and rep["ranking"]["spearman"] < 0, rep["ranking"]
    failed = [g["id"] for g in apply_gates(rep) if not g["ok"] and not g["skip"]]
    assert "spearman" in failed, failed


def test_slim_decisions_and_snapshot_shape() -> None:
    payload = {
        "league_slug": "demo",
        "matchday": {"jornada": 3, "gameweek_status": "pending"},
        "me": {
            "squad": [
                {"id": "1", "xpts": 5.1, "xpts_p_play": 0.9, "gw_points": 4, "price": 1_000_000}
            ]
        },
        "recommended_xi": {
            "formation": "1-4-4-2",
            "xi": [{"player_id": "1", "name": "Uno"}],
            "captain": {"player_id": "1"},
            "summary": {"xpts_total": 5.1},
            "current": {"starter_ids": ["1", "2"], "points": 8, "rank": 3},
        },
        "action_plan": [
            {"player_id": "9", "action": "buy_now", "price": 500, "name": "Nueve"}
        ],
        "sources": {
            "mister": "api",
            "honest_live": True,
            "external": {"matched": 12, "futbolfantasy": "ok", "rate_limited": None},
        },
        "meta": {"pipeline_seconds": 42},
        "market_opportunities": [],
        "free_agents_top": [],
        "rivals": [],
    }
    decisions = slim_decisions(payload)
    assert decisions["xi_ids"] == ["1"]
    assert decisions["captain_id"] == "1"
    assert decisions["current_starter_ids"] == ["1", "2"]
    assert decisions["actions"][0]["action"] == "buy_now"
    snap = build_price_history_snapshot(payload, day="2026-08-23")
    assert snap["decisions"]["xi_ids"] == ["1"]
    assert snap["pipeline"]["pipeline_seconds"] == 42
    assert snap["xpts"]["1"][0] == 5.1


def test_live_history_smoke_if_present() -> None:
    """No es un umbral: solo comprueba que el histórico real se puede auditar."""
    from performance_audit import load_history_snapshots  # noqa: PLC0415

    snaps = load_history_snapshots("laliga-patio")
    if len(snaps) < 3:
        return
    rep = audit_league(snaps, current_jornada=2, slug="laliga-patio")
    assert "calibration" in rep
    assert rep["calibration"]["sample"] >= 0


def main() -> None:
    tests = [
        test_spearman_perfect_and_inverse,
        test_ranking_lift_prefers_high_xpts,
        test_ranking_empty_when_too_few,
        test_xi_recommended_beats_current_and_price_naive,
        test_xi_fails_when_worse_than_aligned,
        test_xi_empty_without_decisions,
        test_market_buy_now_beats_avoid,
        test_market_fails_when_avoids_score_more,
        test_market_ignores_mid_gw_null_status,
        test_pipeline_flags_mock_and_rate_limit,
        test_audit_league_passes_healthy_history,
        test_gates_fail_on_anti_predictive_model,
        test_slim_decisions_and_snapshot_shape,
        test_live_history_smoke_if_present,
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
