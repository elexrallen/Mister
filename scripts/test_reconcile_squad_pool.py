"""Once fantasma: un vendido que Mister deja en el XI no debe seguir en plantilla."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mister_client import reconcile_squad_with_pool  # noqa: E402


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_drops_sold_to_rival() -> None:
    squad = [
        {"id": "49424", "name": "K. Salas", "from_lineup_only": True},
        {"id": "340", "name": "Isco", "from_lineup_only": False},
    ]
    pool = [
        {"id": "49424", "name": "K. Salas", "owner_id": "999", "is_mine": False},
        {"id": "340", "name": "Isco", "owner_id": "me", "is_mine": True},
    ]
    out = reconcile_squad_with_pool(squad, pool, "me")
    ids = [p["id"] for p in out]
    _assert(ids == ["340"], ids)


def test_keeps_listed_but_still_mine() -> None:
    squad = [{"id": "1", "name": "A", "from_lineup_only": False}]
    pool = [{"id": "1", "name": "A", "owner_id": "me", "is_mine": True}]
    out = reconcile_squad_with_pool(squad, pool, "me")
    _assert([p["id"] for p in out] == ["1"], out)


def test_drops_ghost_xi_now_free() -> None:
    squad = [{"id": "49424", "name": "K. Salas", "from_lineup_only": True}]
    pool = [{"id": "49424", "name": "K. Salas", "owner_id": None, "is_mine": False}]
    out = reconcile_squad_with_pool(squad, pool, "me")
    _assert(out == [], out)


def test_keeps_when_pool_misses_player() -> None:
    squad = [{"id": "9", "name": "X", "from_lineup_only": True}]
    out = reconcile_squad_with_pool(squad, [{"id": "1", "owner_id": "me"}], "me")
    _assert([p["id"] for p in out] == ["9"], out)


if __name__ == "__main__":
    test_drops_sold_to_rival()
    test_keeps_listed_but_still_mine()
    test_drops_ghost_xi_now_free()
    test_keeps_when_pool_misses_player()
    print("ok")
