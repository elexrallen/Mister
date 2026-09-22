"""
Tests de oferta real del mercado: libre vs. listado de rival.

Pujar a un libre resuelve al cierre del ciclo y es nuestro. Pujar a un listado
de rival es una oferta que el dueño puede rechazar, y el sistema ya le paga el
valor de mercado. Por eso un listado de rival no puede contar como cobertura de
una carencia del once ni rebajar la reserva.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from competitive_actions import (  # noqa: E402
    gap_reserve_cost,
    is_key_market_candidate,
    is_rival_market_listing,
)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _row(pid: str, pos: str, price: float, *, owner: str | None = None,
         daily: bool = True, lineup: float = 85.0) -> dict:
    return {
        "id": pid,
        "player_id": pid,
        "name": pid,
        "position": pos,
        "price": price,
        "market_value": price,
        "puja_recomendada": price,
        "on_daily_market": daily,
        "seller": "market" if daily else ("rival" if owner else "free"),
        "owner_id": owner,
        "external": {"lineup_prob_ext": lineup},
    }


def test_classifies_free_vs_rival() -> None:
    _assert(not is_rival_market_listing(_row("libre", "DF", 1_000_000)), "libre")
    _assert(is_rival_market_listing(_row("rival", "DF", 1_000_000, owner="77")), "rival")
    _assert(
        not is_rival_market_listing(_row("mine", "DF", 1_000_000, owner="77") | {"is_mine": True}),
        "mío listado no es de rival",
    )
    _assert(
        not is_rival_market_listing(_row("owner_zero", "DF", 1_000_000, owner="0")),
        "owner_id 0 es libre",
    )


def test_rival_listing_does_not_lower_the_reserve() -> None:
    needs = [
        {"need": "xi_starter", "position": "DF", "priority": "Alta", "slots_short": 1},
        {"need": "xi_starter", "position": "FW", "priority": "Alta", "slots_short": 1},
    ]
    only_rival_fw = [
        _row("df", "DF", 1_000_000),
        _row("fw_rival", "FW", 500_000, owner="77"),
        _row("fw_free", "FW", 4_000_000),
    ]
    reserve = gap_reserve_cost(
        exclude_position="DF",
        structural_needs=needs,
        opportunities=only_rival_fw,
        balance=100_000_000,
    )
    _assert(
        reserve == 4_000_000,
        f"la oferta del rival a 500k no puede rebajar la reserva: {reserve}",
    )


def test_rival_held_player_is_not_supply() -> None:
    """Un jugador en plantilla rival fuera de mercado solo se saca con cláusula."""
    needs = [
        {"need": "xi_starter", "position": "DF", "priority": "Alta", "slots_short": 1},
        {"need": "xi_starter", "position": "FW", "priority": "Alta", "slots_short": 1},
    ]
    ops = [
        _row("df", "DF", 1_000_000),
        _row("fw_held", "FW", 300_000, owner="77", daily=False),
    ]
    reserve = gap_reserve_cost(
        exclude_position="DF",
        structural_needs=needs,
        opportunities=ops,
        balance=100_000_000,
    )
    _assert(reserve > 300_000, f"no puede tomarse el jugador del rival como oferta: {reserve}")


def test_rival_listing_is_never_a_key_candidate() -> None:
    base = dict(
        is_primary_obj=False,
        is_objective=True,
        on_daily=True,
        gw_out=False,
        real_starter=True,
        fills_gap=True,
    )
    free_row = _row("libre", "DF", 1_000_000)
    free_row["fills_coverage_gap"] = True
    rival_row = _row("rival", "DF", 1_000_000, owner="77")
    rival_row["fills_coverage_gap"] = True
    _assert(is_key_market_candidate(free_row, **base), "el libre sí es clave")
    _assert(
        not is_key_market_candidate(rival_row, **base),
        "la oferta condicional a un rival no es fichaje asegurado",
    )


def test_conditional_offer_flag_is_respected() -> None:
    row = _row("x", "DF", 1_000_000)
    row["conditional_offer"] = True
    row["fills_coverage_gap"] = True
    _assert(
        not is_key_market_candidate(
            row,
            is_primary_obj=False,
            is_objective=True,
            on_daily=True,
            gw_out=False,
            real_starter=True,
            fills_gap=True,
        ),
        "conditional_offer debe bastar para degradarlo",
    )


def test_multi_gap_intents_skip_rival_listings() -> None:
    """Con varios huecos, un listado de rival no entra como cobertura."""
    from competitive_actions import select_intent_lines

    daily = [
        {
            "player_id": "meunier",
            "position": "DF",
            "cost": 1_546_710,
            "fills_coverage_gap": True,
            "listed_by_rival": True,
            "owner_id": "15539306",
            "production_score": 60,
            "priority_score": 200,
        },
        {
            "player_id": "oshea",
            "position": "DF",
            "cost": 2_947_360,
            "fills_coverage_gap": True,
            "production_score": 55,
            "priority_score": 90,
        },
    ]
    intents = select_intent_lines(
        daily,
        bal=21_688_160,
        cash_reserve=0.0,
        primary_ids=set(),
        secondary_max=3_000_000,
        max_intents=8,
        xi_gap_count=9,
    )
    _assert(intents, "debe quedar el libre")
    _assert(intents[0]["player_id"] == "oshea", intents[0])


TESTS = [
    test_classifies_free_vs_rival,
    test_rival_listing_does_not_lower_the_reserve,
    test_rival_held_player_is_not_supply,
    test_rival_listing_is_never_a_key_candidate,
    test_conditional_offer_flag_is_respected,
    test_multi_gap_intents_skip_rival_listings,
]


if __name__ == "__main__":
    for fn in TESTS:
        fn()
        print(f"OK  {fn.__name__}")
    print(f"test_free_vs_rival_supply: {len(TESTS)} tests OK")
