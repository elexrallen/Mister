"""Regresión: ranking Mister de más robados por cláusula."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_engine import find_clauses_ranking  # noqa: E402
from payload_slim import CLAUSES_RANKING_PUBLIC_CAP, slim_player, slim_public_payload  # noqa: E402


def _p(
    pid: str,
    name: str,
    rank: int | None,
    *,
    owner_id: str | None = None,
    is_mine: bool = False,
    clause: int | None = 1_000_000,
) -> dict:
    return {
        "id": pid,
        "name": name,
        "position": "FW",
        "team": "Test",
        "team_id": "7",
        "price": 8_000_000,
        "clause": clause,
        "clause_known": clause is not None,
        "clause_rank": rank,
        "owner_id": owner_id,
        "is_mine": is_mine,
        "owner_name": None,
    }


def test_orders_by_rank_and_skips_nulls() -> None:
    pool = [
        _p("b", "Beta", 2, owner_id="r1"),
        _p("a", "Alfa", 1, owner_id="r1"),
        _p("z", "Sin rank", None),
        _p("zero", "Cero", 0),
    ]
    rows = find_clauses_ranking(
        pool,
        clauses_enabled=True,
        me={"team_id": "me", "team_name": "Mío"},
        rivals=[{"team_id": "r1", "team_name": "Rival Uno", "manager": "X"}],
    )
    assert [r["id"] for r in rows] == ["a", "b"]
    assert [r["clause_rank"] for r in rows] == [1, 2]
    assert rows[0]["owner_kind"] == "rival"
    assert rows[0]["owner_name"] == "Rival Uno"


def test_disabled_clauses_empty() -> None:
    pool = [_p("a", "Alfa", 1)]
    assert find_clauses_ranking(pool, clauses_enabled=False) == []
    assert find_clauses_ranking([], clauses_enabled=True) == []


def test_owner_kinds_mine_rival_free() -> None:
    pool = [
        _p("mine", "Mío", 3, is_mine=True, owner_id="me"),
        _p("riv", "Rival", 1, owner_id="r2"),
        _p("free", "Libre", 2),
    ]
    rows = find_clauses_ranking(
        pool,
        clauses_enabled=True,
        me={"team_id": "me", "manager": "Emilio"},
        squad=[{"id": "mine", "clause_multiplier": 2.0}],
        rivals=[{"team_id": "r2", "manager": "Otro", "squad": []}],
    )
    by_id = {r["id"]: r for r in rows}
    assert by_id["riv"]["owner_kind"] == "rival"
    assert by_id["free"]["owner_kind"] == "free"
    assert by_id["mine"]["owner_kind"] == "mine"
    assert by_id["mine"]["owner_name"] == "Emilio"
    assert by_id["mine"]["clause_multiplier"] == 2.0


def test_cap_is_25() -> None:
    pool = [_p(str(i), f"J{i:03d}", i + 1) for i in range(40)]
    rows = find_clauses_ranking(pool, clauses_enabled=True)
    assert len(rows) == CLAUSES_RANKING_PUBLIC_CAP
    assert rows[0]["clause_rank"] == 1
    assert rows[-1]["clause_rank"] == 25


def test_slim_keeps_clause_rank_fields() -> None:
    slim = slim_player(
        {
            "id": "1",
            "name": "Crack",
            "clause_rank": 2,
            "clause_multiplier": 1.5,
            "owner_kind": "mine",
            "seasons": [1, 2, 3],
        }
    )
    assert slim["clause_rank"] == 2
    assert slim["clause_multiplier"] == 1.5
    assert slim["owner_kind"] == "mine"
    assert "seasons" not in slim

    payload = {
        "clauses_ranking": [
            {"id": str(i), "name": f"P{i}", "clause_rank": i, "owner_kind": "free"}
            for i in range(1, 40)
        ],
        "free_agents_top": [],
        "market_opportunities": [],
        "rivals": [],
        "meta": {},
    }
    public = slim_public_payload(payload)
    assert len(public["clauses_ranking"]) == CLAUSES_RANKING_PUBLIC_CAP
    assert public["clauses_ranking"][0]["clause_rank"] == 1
    assert public["meta"]["payload"]["clauses_ranking_cap"] == CLAUSES_RANKING_PUBLIC_CAP
    assert len(payload["clauses_ranking"]) == 39


def main() -> None:
    tests = [
        test_orders_by_rank_and_skips_nulls,
        test_disabled_clauses_empty,
        test_owner_kinds_mine_rival_free,
        test_cap_is_25,
        test_slim_keeps_clause_rank_fields,
    ]
    for fn in tests:
        fn()
        print(f"ok {fn.__name__}")
    print(f"{len(tests)} tests ok")


if __name__ == "__main__":
    main()
