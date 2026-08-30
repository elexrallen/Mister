"""Plantilla perfecta: operable vs cláusulas pagables vs aspiracional."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from target_board import (  # noqa: E402
    _assemble_perfect_squad,
    _is_clauses_eligible,
    _normalize_player,
    build_target_board,
)


def _assert(cond: bool, msg: object) -> None:
    if not cond:
        raise AssertionError(msg)


def _raw(
    pid: str,
    pos: str,
    *,
    owned: bool = False,
    seller: str = "free",
    avg: float = 7.0,
    **extra: object,
) -> dict:
    price = extra.pop("price", 2_000_000)
    row = {
        "id": pid,
        "name": pid,
        "position": pos,
        "price": price,
        "market_value": extra.pop("market_value", price),
        "lineup_prob": 0.9,
        "gw_starter": True,
        "ff_mister_avg": avg,
        "ff_mister_points": extra.pop("ff_mister_points", 220),
        "ff_apps": 28,
        "seller": seller,
    }
    if owned:
        row["seller"] = "owned"
    row.update(extra)
    return row


def _universe(rows: list[tuple[dict, bool]]) -> list[dict]:
    out: list[dict] = []
    for raw, owned in rows:
        n = _normalize_player(raw, owned=owned, price_series=None)
        _assert(n is not None, raw)
        out.append(n)
    return out


def _tiny_shape() -> tuple[dict[str, int], dict[str, int]]:
    shape = {"GK": 1, "DF": 1, "MF": 1, "FW": 1}
    return shape, shape


def test_normalize_clause_vs_unknown_rival() -> None:
    clause = _normalize_player(
        _raw(
            "star",
            "FW",
            seller="rival",
            owner_id="99",
            owner_name="Otro",
            clause=4_000_000,
            clause_known=True,
            avg=8.5,
        ),
        owned=False,
        price_series=None,
    )
    ghost = _normalize_player(
        _raw(
            "ghost",
            "FW",
            seller="rival",
            owner_id="99",
            owner_name="Otro",
            avg=9.5,
        ),
        owned=False,
        price_series=None,
    )
    _assert(clause is not None and ghost is not None, (clause, ghost))
    _assert(clause.get("acquisition") == "clause", clause)
    _assert(clause.get("buy_price") == 4_000_000, clause)
    _assert(clause.get("owner_name") == "Otro", clause)
    _assert(_is_clauses_eligible(clause) is True, clause)
    _assert(ghost.get("acquisition") == "rival", ghost)
    _assert(_is_clauses_eligible(ghost) is False, ghost)


def _core_owned() -> list[tuple[dict, bool]]:
    return [
        (_raw("gk1", "GK", owned=True, price=1_000_000, avg=6.5), True),
        (_raw("d1", "DF", owned=True, price=2_000_000, avg=6.5), True),
        (_raw("m1", "MF", owned=True, price=2_000_000, avg=6.5), True),
    ]


def _assemble_kwargs() -> dict:
    starters, ideal = _tiny_shape()
    return {
        "budget_cap": 25_000_000.0,
        "owned_ids": {"gk1", "d1", "m1"},
        "prev_idx": {},
        "starters_n": starters,
        "ideal": ideal,
    }


def test_operable_skips_expensive_clause_if_free_exists() -> None:
    free = _raw("fw_free", "FW", seller="free", price=2_000_000, avg=7.8)
    clause = _raw(
        "fw_clause",
        "FW",
        seller="rival",
        owner_id="99",
        owner_name="Rival",
        clause=4_000_000,
        clause_known=True,
        price=2_000_000,
        market_value=2_000_000,
        avg=8.1,
    )
    universe = _universe(_core_owned() + [(free, False), (clause, False)])
    kwargs = _assemble_kwargs()
    op = _assemble_perfect_squad(universe, mode="operable", **kwargs)
    cl = _assemble_perfect_squad(universe, mode="clauses", **kwargs)
    op_fw = {r.get("player_id") for r in op if r.get("position") == "FW"}
    cl_fw = {r.get("player_id") for r in cl if r.get("position") == "FW"}
    _assert("fw_free" in op_fw, op)
    _assert("fw_clause" not in op_fw, op)
    _assert("fw_clause" in cl_fw, cl)
    row = next(r for r in cl if r.get("player_id") == "fw_clause")
    _assert(row.get("acquisition") == "clause", row)
    _assert(row.get("clause") == 4_000_000, row)


def test_clauses_excludes_unknown_rival_aspirational_can_keep() -> None:
    free = _raw("fw_free", "FW", seller="free", price=2_000_000, avg=6.8)
    ghost = _raw(
        "fw_ghost",
        "FW",
        seller="rival",
        owner_id="88",
        owner_name="Otro",
        price=2_200_000,
        market_value=2_200_000,
        avg=9.4,
    )
    universe = _universe(_core_owned() + [(free, False), (ghost, False)])
    kwargs = _assemble_kwargs()
    cl = _assemble_perfect_squad(universe, mode="clauses", **kwargs)
    asp = _assemble_perfect_squad(universe, mode="aspirational", **kwargs)
    cl_fw = {r.get("player_id") for r in cl if r.get("position") == "FW"}
    asp_fw = {r.get("player_id") for r in asp if r.get("position") == "FW"}
    _assert("fw_ghost" not in cl_fw, cl)
    _assert("fw_free" in cl_fw, cl)
    _assert("fw_ghost" in asp_fw, asp)


def test_board_exposes_clauses_mode() -> None:
    squad = [
        _raw("gk1", "GK", owned=True, price=1_000_000),
        _raw("d1", "DF", owned=True, price=2_000_000),
        _raw("m1", "MF", owned=True, price=2_000_000),
        _raw("f1", "FW", owned=True, price=2_000_000),
    ]
    cands = [
        _raw("fw_free", "FW", seller="free", price=1_500_000, avg=6.6),
        _raw(
            "fw_clause",
            "FW",
            seller="rival",
            owner_id="99",
            owner_name="Rival",
            clause=4_000_000,
            clause_known=True,
            price=2_000_000,
            avg=8.0,
        ),
        _raw(
            "fw_ghost",
            "FW",
            seller="rival",
            owner_id="88",
            avg=9.2,
        ),
    ]
    board = build_target_board(
        slug="test-clauses",
        structural_needs=[],
        candidates=cands,
        balance=12_000_000,
        squad=squad,
        squad_value=7_000_000,
        previous={},
    )
    _assert(isinstance(board.get("perfect_squad_clauses"), list), board.keys())
    _assert(board.get("summary_clauses", {}).get("mode") == "clauses", board.get("summary_clauses"))
    cl_ids = {str(r.get("player_id")) for r in (board.get("perfect_squad_clauses") or [])}
    _assert("fw_ghost" not in cl_ids, cl_ids)
    asp_ids = {str(r.get("player_id")) for r in (board.get("perfect_squad_aspirational") or [])}
    _assert("fw_ghost" in asp_ids or "fw_clause" in cl_ids, (asp_ids, cl_ids))


if __name__ == "__main__":
    test_normalize_clause_vs_unknown_rival()
    test_operable_skips_expensive_clause_if_free_exists()
    test_clauses_excludes_unknown_rival_aspirational_can_keep()
    test_board_exposes_clauses_mode()
    print("test_target_board: OK")
