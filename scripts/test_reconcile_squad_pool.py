"""Once fantasma: un vendido que Mister deja en el XI no debe seguir en plantilla."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mister_client import (  # noqa: E402
    flag_is_true,
    player_is_mine,
    reconcile_squad_with_pool,
)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_flag_is_true_rejects_string_zero() -> None:
    _assert(flag_is_true(1) is True, "1")
    _assert(flag_is_true(0) is False, "0")
    _assert(flag_is_true("0") is False, "str 0")
    _assert(flag_is_true("1") is True, "str 1")
    _assert(player_is_mine({"is_mine": "0", "owner_id": None}, "me") is False, "is_mine str 0")


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


def test_drops_sold_even_if_still_in_html_sidebar() -> None:
    """Premier: /team deja al vendido en once + lista; el pool ya no lo marca mío."""
    squad = [
        {"id": "65186", "name": "N. Mukiele", "from_lineup_only": False, "in_lineup": True},
        {"id": "11145", "name": "L. Nmecha", "from_lineup_only": False, "in_lineup": True},
        {"id": "1859", "name": "D. Solanke", "from_lineup_only": False, "in_lineup": True},
    ]
    pool = [
        {"id": "65186", "name": "N. Mukiele", "owner_id": None, "is_mine": 0},
        {"id": "11145", "name": "L. Nmecha", "owner_id": "999", "is_mine": 0},
        {"id": "1859", "name": "D. Solanke", "owner_id": "me", "is_mine": 1},
    ]
    out = reconcile_squad_with_pool(squad, pool, "me")
    _assert([p["id"] for p in out] == ["1859"], [p["name"] for p in out])


def test_drops_when_rival_html_already_has_them() -> None:
    """Pool aún dice is_mine (retraso); el perfil rival ya los tiene."""
    squad = [
        {"id": "65186", "name": "N. Mukiele", "from_lineup_only": True},
        {"id": "1859", "name": "D. Solanke", "from_lineup_only": False},
    ]
    pool = [
        {"id": "65186", "name": "N. Mukiele", "owner_id": "me", "is_mine": True},
        {"id": "1859", "name": "D. Solanke", "owner_id": "me", "is_mine": True},
    ]
    out = reconcile_squad_with_pool(
        squad, pool, "me", foreign_ids={"65186"}
    )
    _assert([p["id"] for p in out] == ["1859"], [p["id"] for p in out])


def test_own_profile_roster_drops_sold() -> None:
    squad = [
        {"id": "65186", "name": "N. Mukiele", "from_lineup_only": True},
        {"id": "11145", "name": "L. Nmecha", "from_lineup_only": False},
        {"id": "1859", "name": "D. Solanke"},
    ]
    roster = {f"p{i}" for i in range(10)}
    roster.add("1859")
    out = reconcile_squad_with_pool(squad, [], "me", roster_ids=roster)
    _assert([p["id"] for p in out] == ["1859"], [p["id"] for p in out])


def test_adds_pool_mine_missing_from_html() -> None:
    squad = [{"id": "1859", "name": "D. Solanke", "from_lineup_only": False}]
    pool = [
        {"id": "1859", "name": "D. Solanke", "owner_id": "me", "is_mine": True},
        {"id": "99", "name": "Nuevo", "owner_id": "me", "is_mine": True, "position": "MF"},
    ]
    out = reconcile_squad_with_pool(squad, pool, "me")
    _assert({p["id"] for p in out} == {"1859", "99"}, [p["id"] for p in out])
    nuevo = next(p for p in out if p["id"] == "99")
    _assert(nuevo.get("in_lineup") is False, nuevo)


def test_keeps_when_pool_misses_player() -> None:
    squad = [{"id": "9", "name": "X", "from_lineup_only": True}]
    out = reconcile_squad_with_pool(squad, [{"id": "1", "owner_id": "me"}], "me")
    ids = [p["id"] for p in out]
    _assert("9" in ids, ids)


def test_drops_uncatalogued_ghost_when_pool_mine_is_complete() -> None:
    squad = [{"id": "ghost", "name": "Fantasma", "from_lineup_only": True}]
    pool = [
        {"id": str(i), "name": f"P{i}", "owner_id": "me", "is_mine": True}
        for i in range(11)
    ]
    out = reconcile_squad_with_pool(squad, pool, "me")
    ids = {p["id"] for p in out}
    _assert("ghost" not in ids, ids)
    _assert(len(ids) == 11, len(ids))


if __name__ == "__main__":
    test_flag_is_true_rejects_string_zero()
    test_drops_sold_to_rival()
    test_keeps_listed_but_still_mine()
    test_drops_ghost_xi_now_free()
    test_drops_sold_even_if_still_in_html_sidebar()
    test_drops_when_rival_html_already_has_them()
    test_own_profile_roster_drops_sold()
    test_adds_pool_mine_missing_from_html()
    test_keeps_when_pool_misses_player()
    test_drops_uncatalogued_ghost_when_pool_mine_is_complete()
    print("ok")
