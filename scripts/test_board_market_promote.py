"""Mercado diario libre debe ganar sobre ownership rival obsoleto en el tablero."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

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
                board_candidates[i] = base
            return
        board_seen.add(pid)
        board_candidates.append(dict(raw))

    # Catálogo / rival obsoleto (como en latest_data)
    append(
        {
            "id": "58125",
            "name": "Mario Martín",
            "position": "MF",
            "price": 878000,
            "owner_id": "15399697",
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
    # Mercado diario libre
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

    n = _normalize_player(row, owned=False, price_series=None)
    assert n is not None
    assert _is_opportunity_buy(n), n
    assert _accept_operable_candidate(
        n, universe=[n], exclude=set(), eligible=lambda _u: True
    ), n
    # Coste de mercado, no cláusula
    assert float(n["price"]) == 878000.0, n["price"]
    print("ok: mercado diario libera a Martín frente a rival obsoleto")


if __name__ == "__main__":
    main()
