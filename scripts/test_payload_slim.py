"""El JSON público no arrastra el universo completo ni plantillas rivales."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from payload_slim import (  # noqa: E402
    FREE_AGENTS_PUBLIC_CAP,
    slim_player,
    slim_public_payload,
    slim_rival,
)


def _fat_player(pid: str, *, pos: str = "MF") -> dict:
    return {
        "id": pid,
        "name": f"Jugador {pid}",
        "position": pos,
        "team": "Test",
        "team_id": "1",
        "price": 2_000_000,
        "form": 5.0,
        "lineup_prob": 0.8,
        "xpts": 4.2,
        "xpts_why": "texto largo " * 40,
        "fdr_next": [{"jornada": 1, "opponent_name": "X"} for _ in range(3)],
        "seasons": [{"year": 2024, "ppg": 5} for _ in range(5)],
        "data_quality": {"price": "mister", "form": "mister"},
        "external": {
            "availability": "available",
            "lineup_prob_ext": 80,
            "profile_url": "https://example.test/p",
            "ff_mister_avg": 4.1,
            "matched_name": "Jugador",
            "match_score": 99,
            "gw_role": None,
            "top_reason": None,
        },
        "fotmob_stats": {
            "rating_promedio": 6.8,
            "minutos_ultimos_5": 400,
            "goles_ultimos_5": 1,
            "xg_promedio": 0.2,
            "fotmob_id": 99,
        },
    }


def test_slim_player_drops_bulk() -> None:
    slim = slim_player(_fat_player("1"))
    assert slim["id"] == "1"
    assert slim["xpts"] == 4.2
    assert "fdr_next" not in slim
    assert "seasons" not in slim
    assert "data_quality" not in slim
    assert "xpts_why" not in slim
    ext = slim["external"]
    assert ext["availability"] == "available"
    assert "matched_name" not in ext
    assert slim["fotmob_stats"]["rating_promedio"] == 6.8
    assert slim["fotmob_stats"]["goles_ultimos_5"] == 1
    assert "xg_promedio" not in slim["fotmob_stats"]


def test_slim_payload_caps_free_and_drops_rival_squads() -> None:
    payload = {
        "league_slug": "test",
        "me": {"balance": 1, "squad": [_fat_player("s1", pos="FW")]},
        "market_opportunities": [_fat_player(str(i)) for i in range(5)],
        "free_agents_top": [_fat_player(f"f{i}") for i in range(80)],
        "rivals": [
            {
                "team_name": "Rival",
                "manager": "X",
                "rank": 2,
                "points": 10,
                "position_gaps": ["FW"],
                "squad": [_fat_player("r1"), _fat_player("r2")],
                "key_players": [{"id": "r1", "name": "Crack", "position": "FW", "price": 9}],
                "data_quality": {"squad": "html"},
            }
        ],
        "diagnostico_plantilla": {
            "salud_score": 50,
            "matchday": {"jornada": 1},
            "lineas": {"FW": {"status": "ok", "players": [_fat_player("s1", pos="FW")]}},
        },
        "squad_diagnosis": {
            "alerts": [{"level": "warning", "message": "hueco"}],
            "by_position": {"FW": {"count": 2, "players": [_fat_player("s1", pos="FW")]}},
        },
        "action_plan": [{"player_id": "1", "action": "buy_now", "name": "A"}],
        "recommended_xi": {"xi": []},
        "meta": {},
    }
    slim = slim_public_payload(payload)
    assert len(slim["free_agents_top"]) == FREE_AGENTS_PUBLIC_CAP
    rival = slim["rivals"][0]
    assert "squad" not in rival
    assert rival["key_players"][0]["name"] == "Crack"
    assert "data_quality" not in rival
    assert "matchday" not in slim["diagnostico_plantilla"]
    assert slim["me"]["squad"][0]["id"] == "s1"
    assert slim["me"]["squad"][0]["xpts_why"].startswith("texto largo")
    assert "fdr_next" not in slim["squad_diagnosis"]["by_position"]["FW"]["players"][0]
    assert slim["meta"]["payload"]["slim"] is True
    # El original no se muta
    assert "squad" in payload["rivals"][0]
    assert len(payload["free_agents_top"]) == 80


def test_slim_is_idempotent() -> None:
    payload = {
        "me": {"squad": [_fat_player("s1")]},
        "market_opportunities": [_fat_player("m1")],
        "free_agents_top": [],
        "rivals": [slim_rival({"team_name": "R", "rank": 1, "key_players": []})],
        "meta": {},
    }
    once = slim_public_payload(payload)
    twice = slim_public_payload(once)
    assert twice["me"]["squad"][0]["name"] == once["me"]["squad"][0]["name"]
    assert twice["market_opportunities"][0]["id"] == "m1"


def test_committed_league_files_keep_ui_contract() -> None:
    """Los latest_data.json del repo ya van slim: la PWA no se queda sin campos."""
    files = [
        ROOT / "public/data/leagues/premier-league-d0g38-4036/latest_data.json",
        ROOT / "public/data/leagues/laliga-patio/latest_data.json",
        ROOT / "public/data/latest_data.json",
    ]
    for path in files:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["meta"]["payload"]["slim"] is True
        assert "squad" not in (data["rivals"][0] if data.get("rivals") else {})
        market = data.get("market_opportunities") or []
        assert market, path
        row = next((p for p in market if p.get("puja_recomendada")), market[0])
        for key in ("id", "name", "position", "price", "photo_url"):
            assert row.get(key) not in (None, ""), (path.name, key)
        assert not any("fdr_next" in p or "seasons" in p for p in market)
        squad = (data.get("me") or {}).get("squad") or []
        assert squad
        assert any(p.get("xpts") is not None for p in squad)
        assert len(data.get("free_agents_top") or []) <= FREE_AGENTS_PUBLIC_CAP
        assert data.get("recommended_xi", {}).get("xi")
        assert data.get("action_plan")
        assert data.get("diagnostico_plantilla", {}).get("lineas")
        # Compacto: una sola línea (+ newline)
        assert path.read_text(encoding="utf-8").count("\n") <= 1
        assert path.stat().st_size < 1_200_000, (path, path.stat().st_size)


def test_compact_json_is_much_smaller_than_pretty() -> None:
    payload = {
        "me": {"squad": [_fat_player(str(i)) for i in range(15)]},
        "market_opportunities": [_fat_player(str(i)) for i in range(80)],
        "free_agents_top": [_fat_player(f"f{i}") for i in range(80)],
        "rivals": [
            {
                "team_name": f"R{i}",
                "rank": i,
                "squad": [_fat_player(f"r{i}-{j}") for j in range(18)],
                "key_players": [{"id": f"r{i}-0", "name": "K", "position": "MF"}],
            }
            for i in range(12)
        ],
        "meta": {},
    }
    fat = json.dumps(payload, ensure_ascii=False, indent=2)
    slim = json.dumps(slim_public_payload(payload), ensure_ascii=False, separators=(",", ":"))
    assert len(slim) < len(fat) * 0.35, (len(slim), len(fat))


def main() -> None:
    tests = [
        test_slim_player_drops_bulk,
        test_slim_payload_caps_free_and_drops_rival_squads,
        test_slim_is_idempotent,
        test_committed_league_files_keep_ui_contract,
        test_compact_json_is_much_smaller_than_pretty,
    ]
    for fn in tests:
        fn()
        print(f"ok {fn.__name__}")
    print(f"{len(tests)} tests ok")


if __name__ == "__main__":
    main()
