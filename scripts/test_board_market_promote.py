"""Mercado diario: rival en venta ≠ cláusula; tag listed_by_rival."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from competitive_actions import tag_rival_market_listings  # noqa: E402
from target_board import (  # noqa: E402
    _accept_operable_candidate,
    _is_opportunity_buy,
    _normalize_player,
)


def _simulate_append():
    """Réplica mínima de la promoción mercado-sobre-rival."""
    board_candidates: list[dict] = []
    board_seen: set[str] = set()
    owned: set[str] = set()

    def append(raw, *, overwrite_as_market=False):
        pid = str(raw.get("id") or "")
        if not pid:
            return
        if pid in board_seen:
            if not overwrite_as_market:
                return
            for i, c in enumerate(board_candidates):
                if str(c.get("id") or "") != pid:
                    continue
                if pid in owned or c.get("seller") == "owned":
                    return
                base = dict(c)
                prev_rival = str(c.get("seller") or "") == "rival"
                for k, v in raw.items():
                    if v is not None:
                        base[k] = v
                base["seller"] = "market"
                base["on_daily_market"] = True
                if not raw.get("owner_id"):
                    base["owner_id"] = None
                    base["owner_name"] = None
                    base["clause"] = None
                    base["clause_known"] = False
                if prev_rival:
                    base["listed_by_rival"] = True
                    base["listed_by_name"] = c.get("owner_name") or "Rival"
                    base["clause_reference"] = c.get("clause")
                board_candidates[i] = base
            return
        board_seen.add(pid)
        board_candidates.append(dict(raw))

    append(
        {
            "id": "58125",
            "name": "Mario Martín",
            "position": "MF",
            "price": 878000,
            "owner_id": "15399697",
            "owner_name": "Jesus Rodriguez Crespo",
            "clause": 1317000,
            "clause_known": True,
            "seller": "rival",
            "on_daily_market": False,
            "ff_mister_avg": 3.06,
            "ff_mister_points": 107.0,
            "lineup_prob": 0.8,
            "gw_starter": True,
        }
    )
    append(
        {
            "id": "58125",
            "name": "M. Martín",
            "position": "MF",
            "price": 878000,
            "seller": "market",
            "on_daily_market": True,
            "owner_id": None,
            "puja_recomendada": 974580,
            "ff_mister_avg": 3.06,
            "ff_mister_points": 107.0,
            "lineup_prob": 0.8,
            "gw_starter": True,
        },
        overwrite_as_market=True,
    )
    return board_candidates[0]


def main() -> None:
    row = _simulate_append()
    assert row["seller"] == "market", row
    assert row["on_daily_market"] is True, row
    assert not row.get("clause"), row
    assert not row.get("owner_id"), row
    assert row.get("listed_by_rival") is True, row
    assert "Jesus" in str(row.get("listed_by_name") or ""), row
    assert row.get("clause_reference") == 1317000, row

    n = _normalize_player(row, owned=False, price_series=None)
    assert n is not None
    assert _is_opportunity_buy(n), n
    assert _accept_operable_candidate(
        n, universe=[n], exclude=set(), eligible=lambda _u: True
    ), n
    assert float(n["price"]) == 878000.0, n["price"]

    tagged = tag_rival_market_listings(
        [
            {
                "id": "58125",
                "name": "M. Martín",
                "seller": "market",
                "on_daily_market": True,
                "price": 878000,
            },
            {
                "id": "1",
                "name": "Libre",
                "seller": "market",
                "on_daily_market": True,
                "price": 100000,
            },
        ],
        [
            {
                "manager": "Jesus Rodriguez Crespo",
                "team_id": "15399697",
                "squad": [
                    {
                        "id": "58125",
                        "name": "Mario Martín",
                        "clause": 1317000,
                        "owner_id": "15399697",
                    }
                ],
            }
        ],
    )
    assert tagged[0]["listed_by_rival"] is True
    assert tagged[0]["listed_by_name"] == "Jesus Rodriguez Crespo"
    assert tagged[0]["seller"] == "market"
    assert tagged[0]["clause_reference"] == 1317000
    assert tagged[1].get("listed_by_rival") is False

    print("ok: rival en venta = mercado + badge, no cláusula")


if __name__ == "__main__":
    main()
