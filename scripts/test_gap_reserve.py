"""
Tests de la reserva por carencias del once.

El caso que motiva todo esto: plantilla recién reiniciada, nueve huecos de
titularidad y 21,7 M€. El sistema proponía un central de 20 M€ y dejaba el
resto del once sin tapar.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
from competitive_actions import (  # noqa: E402
    gap_reserve_cost,
    is_closing_phase,
    other_gaps_min_cost,
    select_intent_lines,
    set_matchday_phase,
    spend_share_cap,
    unique_critical_over_cap,
    xi_gap_slots,
)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _cand(pid: str, pos: str, price: float, *, lineup: float = 85.0, daily: bool = True,
          owner: str | None = None) -> dict:
    return {
        "id": pid,
        "player_id": pid,
        "name": pid,
        "position": pos,
        "price": price,
        "market_value": price,
        "puja_recomendada": price,
        "on_daily_market": daily,
        "seller": "market" if daily else "free",
        "owner_id": owner,
        "external": {"lineup_prob_ext": lineup},
    }


def _needs(**slots: int) -> list[dict]:
    return [
        {"need": "xi_starter", "position": pos, "priority": "Alta", "slots_short": n}
        for pos, n in slots.items()
    ]


# ---------------------------------------------------------------------------
# Conteo de huecos
# ---------------------------------------------------------------------------

def test_slots_from_structural_needs() -> None:
    slots = xi_gap_slots(None, _needs(DF=3, MF=4, FW=1, GK=1))
    _assert(slots == {"DF": 3, "MF": 4, "FW": 1, "GK": 1}, slots)


def test_slots_from_diagnosis_summary() -> None:
    diag = {
        "xi_slot_gaps": {
            "gaps": [
                {"position": "DF", "slots_short": 2},
                {"position": "FW", "slots_short": 1},
            ]
        }
    }
    slots = xi_gap_slots(diag, None)
    _assert(slots == {"DF": 2, "FW": 1}, slots)


def test_no_gaps_means_no_reserve() -> None:
    reserve = gap_reserve_cost(
        exclude_position="DF",
        diagnosis={"xi_slot_gaps": {"gaps": [], "slots_short": 0}},
        structural_needs=[],
        opportunities=[_cand("a", "DF", 1_000_000)],
        balance=20_000_000,
    )
    _assert(reserve == 0.0, reserve)


# ---------------------------------------------------------------------------
# La reserva suma todos los huecos, no solo el chollo más barato
# ---------------------------------------------------------------------------

def test_reserve_sums_every_gap() -> None:
    """La antigua regla miraba un único gap; la nueva suma los que quedan."""
    needs = _needs(DF=1, MF=1, FW=1)
    ops = [
        _cand("df", "DF", 1_000_000),
        _cand("mf", "MF", 2_000_000),
        _cand("fw", "FW", 3_000_000),
    ]
    old = other_gaps_min_cost(
        {"all_gap_costs": [
            {"position": "DF", "cost": 1_000_000},
            {"position": "MF", "cost": 2_000_000},
            {"position": "FW", "cost": 3_000_000},
        ]},
        exclude_position="DF",
    )
    new = gap_reserve_cost(
        exclude_position="DF",
        structural_needs=needs,
        opportunities=ops,
        balance=100_000_000,
    )
    _assert(old == 2_000_000, old)
    _assert(new == 5_000_000, f"debe reservar MF+FW, no solo el más barato: {new}")


def test_buy_discounts_only_one_slot_of_its_line() -> None:
    """Fichar un DF tapa un hueco de DF, no la línea entera."""
    needs = _needs(DF=3)
    ops = [
        _cand("d1", "DF", 1_000_000),
        _cand("d2", "DF", 1_500_000),
        _cand("d3", "DF", 2_000_000),
    ]
    reserve = gap_reserve_cost(
        exclude_position="DF",
        structural_needs=needs,
        opportunities=ops,
        balance=100_000_000,
    )
    _assert(reserve == 2_500_000, f"quedan 2 DF por tapar (1.0M + 1.5M): {reserve}")


def test_van_hecke_case_is_blocked() -> None:
    """El caso real: 21,7 M€, nueve huecos y un central de 20 M€."""
    balance = 21_688_160
    needs = _needs(DF=3, MF=4, FW=1, GK=1)
    ops = [
        _cand("vanhecke", "DF", 20_090_400),
        _cand("williams", "DF", 8_442_500),
        _cand("oshea", "DF", 2_947_360),
        _cand("mandava", "DF", 1_886_960),
        _cand("mf1", "MF", 3_000_000),
        _cand("mf2", "MF", 2_500_000),
        _cand("mf3", "MF", 2_000_000),
        _cand("mf4", "MF", 1_500_000),
        _cand("fw1", "FW", 4_000_000),
        _cand("gk1", "GK", 1_200_000),
    ]
    reserve = gap_reserve_cost(
        exclude_position="DF",
        structural_needs=needs,
        opportunities=ops,
        balance=balance,
    )
    residual_expensive = balance - 20_090_400
    residual_cheap = balance - 2_947_360
    _assert(residual_expensive < reserve, "el central de 20M debe hipotecar el once")
    _assert(residual_cheap >= reserve, "un DF de 3M debe dejar caja para el resto")


def test_reserve_is_capped_by_balance() -> None:
    """Con la plantilla muy rota el coste ideal supera la caja: sin tope, parálisis."""
    needs = _needs(DF=4, MF=4, FW=2, GK=1)
    ops = [_cand(f"p{i}", pos, 9_000_000) for i, pos in enumerate("DF DF MF MF FW GK".split())]
    balance = 10_000_000
    reserve = gap_reserve_cost(
        exclude_position="DF",
        structural_needs=needs,
        opportunities=ops,
        balance=balance,
    )
    share = float(getattr(config, "GAP_RESERVE_MAX_SHARE", 0.60))
    _assert(reserve <= balance * share + 1, f"reserva sin acotar: {reserve}")
    _assert(reserve > 0, "debe seguir reservando algo")


# ---------------------------------------------------------------------------
# Mercado seco: la reserva no puede anularse
# ---------------------------------------------------------------------------

def test_dry_line_uses_pool_replacement_cost() -> None:
    """Sin candidato en el mercado de hoy, vale el libre más barato del pool."""
    needs = _needs(DF=1, FW=1)
    ops = [
        _cand("df", "DF", 1_000_000),
        _cand("fw_pool", "FW", 4_000_000, daily=False),
    ]
    reserve = gap_reserve_cost(
        exclude_position="DF",
        structural_needs=needs,
        opportunities=ops,
        balance=100_000_000,
    )
    _assert(reserve == 4_000_000, f"debe usar el coste de reposición del pool: {reserve}")


def test_dry_line_falls_back_to_floor() -> None:
    """Ni en mercado ni en pool: se usa el suelo por línea, nunca cero."""
    needs = _needs(DF=1, FW=1)
    ops = [_cand("df", "DF", 1_000_000)]
    reserve = gap_reserve_cost(
        exclude_position="DF",
        structural_needs=needs,
        opportunities=ops,
        balance=100_000_000,
    )
    floor = float((getattr(config, "GAP_REPLACEMENT_FLOOR", {}) or {}).get("FW") or 0)
    _assert(floor > 0, "config debe definir un suelo de reposición para FW")
    _assert(reserve == floor, f"esperaba el suelo {floor}, obtuve {reserve}")


def test_bench_candidates_do_not_cover_a_gap() -> None:
    """Un suplente barato no tapa un hueco del once: no rebaja la reserva."""
    needs = _needs(DF=1, FW=1)
    ops = [
        _cand("df", "DF", 1_000_000),
        _cand("fw_bench", "FW", 200_000, lineup=25.0),
        _cand("fw_real", "FW", 3_000_000, lineup=90.0),
    ]
    reserve = gap_reserve_cost(
        exclude_position="DF",
        structural_needs=needs,
        opportunities=ops,
        balance=100_000_000,
    )
    _assert(reserve == 3_000_000, f"debe contar el titular, no el suplente: {reserve}")


# ---------------------------------------------------------------------------
# Tope de concentración de gasto
# ---------------------------------------------------------------------------

def test_spend_cap_only_with_several_gaps() -> None:
    min_gaps = int(getattr(config, "SPEND_SHARE_CAP_MIN_GAPS", 3))
    _assert(spend_share_cap(20_000_000, min_gaps - 1) is None, "con pocos huecos no aplica")
    cap = spend_share_cap(20_000_000, min_gaps)
    share = float(getattr(config, "SPEND_SHARE_CAP_MULTI_GAP", 0.40))
    _assert(cap == 20_000_000 * share, cap)


def test_spend_cap_filters_the_expensive_intent() -> None:
    daily = [
        {
            "player_id": "vanhecke",
            "name": "van Hecke",
            "position": "DF",
            "cost": 20_090_400,
            "is_key_market": True,
            "fills_coverage_gap": True,
            "production_score": 70,
            "priority_score": 200,
        },
        {
            "player_id": "oshea",
            "name": "O'Shea",
            "position": "DF",
            "cost": 2_947_360,
            "fills_coverage_gap": True,
            "production_score": 55,
            "priority_score": 90,
        },
    ]
    with_gaps = select_intent_lines(
        daily,
        bal=21_688_160,
        cash_reserve=0.0,
        primary_ids=set(),
        secondary_max=3_000_000,
        max_intents=8,
        xi_gap_count=9,
    )
    _assert(with_gaps, "debe elegir algo barato en vez de bloquearse")
    _assert(
        with_gaps[0]["player_id"] == "oshea",
        f"con nueve huecos no puede llevarse el 93% del saldo: {with_gaps[0]}",
    )

    no_gaps = select_intent_lines(
        daily,
        bal=21_688_160,
        cash_reserve=0.0,
        primary_ids=set(),
        secondary_max=3_000_000,
        max_intents=8,
        xi_gap_count=0,
    )
    _assert(
        no_gaps[0]["player_id"] == "vanhecke",
        f"sin carencias el jugador clave sigue mandando: {no_gaps[0]}",
    )


def test_spend_cap_prefers_nothing_over_mortgaging() -> None:
    """Si el mercado solo ofrece caro, mejor guardar caja que romper el once."""
    daily = [
        {
            "player_id": "caro",
            "position": "DF",
            "cost": 19_000_000,
            "fills_coverage_gap": True,
            "is_key_market": True,
        }
    ]
    intents = select_intent_lines(
        daily,
        bal=21_000_000,
        cash_reserve=0.0,
        primary_ids=set(),
        secondary_max=3_000_000,
        max_intents=8,
        xi_gap_count=9,
    )
    _assert(intents == [], f"no debe fichar nada: {intents}")


def test_unique_critical_over_cap_waits_unless_closing() -> None:
    """Excepción A3: un solo libre para un hueco crítico entra como wait, no buy."""
    daily = [
        {
            "player_id": "unico",
            "position": "GK",
            "cost": 19_000_000,
            "fills_structural": True,
            "fills_coverage_gap": True,
            "urgency": "high",
        }
    ]
    cap = spend_share_cap(21_000_000, 9)
    _assert(cap is not None and 19_000_000 > cap, cap)
    _assert(unique_critical_over_cap(daily[0], daily, cap), "debe detectar la excepción")

    prev = "ventana_compra"
    try:
        set_matchday_phase("ventana_compra")
        waiting = select_intent_lines(
            daily,
            bal=21_000_000,
            cash_reserve=0.0,
            primary_ids=set(),
            secondary_max=3_000_000,
            max_intents=8,
            xi_gap_count=9,
        )
        _assert(waiting, "el único crítico no se descarta")
        _assert(waiting[0]["player_id"] == "unico", waiting[0])
        _assert(waiting[0].get("action") == "wait", waiting[0].get("action"))
        _assert(
            waiting[0].get("spend_cap_exception") == "unique_critical",
            waiting[0].get("spend_cap_exception"),
        )

        set_matchday_phase("visperas")
        _assert(is_closing_phase(), "vísperas es fase de cierre")
        closing = select_intent_lines(
            daily,
            bal=21_000_000,
            cash_reserve=0.0,
            primary_ids=set(),
            secondary_max=3_000_000,
            max_intents=8,
            xi_gap_count=9,
        )
        _assert(closing, "en vísperas sí se permite")
        _assert(closing[0]["player_id"] == "unico", closing[0])
        _assert(closing[0].get("action") != "wait", closing[0].get("action"))
    finally:
        set_matchday_phase(prev)


def test_multi_gap_spreads_across_positions() -> None:
    """Con multi-carencia, dos fichajes baratos de líneas distintas."""
    daily = [
        {
            "player_id": "df",
            "position": "DF",
            "cost": 2_000_000,
            "fills_coverage_gap": True,
            "production_score": 60,
            "priority_score": 100,
        },
        {
            "player_id": "mf",
            "position": "MF",
            "cost": 1_800_000,
            "fills_coverage_gap": True,
            "production_score": 58,
            "priority_score": 95,
        },
    ]
    intents = select_intent_lines(
        daily,
        bal=21_000_000,
        cash_reserve=0.0,
        primary_ids=set(),
        secondary_max=2_500_000,
        max_intents=8,
        xi_gap_count=9,
    )
    positions = {i["position"] for i in intents}
    _assert(len(intents) == 2, f"debe encadenar dos intents: {intents}")
    _assert(positions == {"DF", "MF"}, positions)


TESTS = [
    test_slots_from_structural_needs,
    test_slots_from_diagnosis_summary,
    test_no_gaps_means_no_reserve,
    test_reserve_sums_every_gap,
    test_buy_discounts_only_one_slot_of_its_line,
    test_van_hecke_case_is_blocked,
    test_reserve_is_capped_by_balance,
    test_dry_line_uses_pool_replacement_cost,
    test_dry_line_falls_back_to_floor,
    test_bench_candidates_do_not_cover_a_gap,
    test_spend_cap_only_with_several_gaps,
    test_spend_cap_filters_the_expensive_intent,
    test_spend_cap_prefers_nothing_over_mortgaging,
    test_unique_critical_over_cap_waits_unless_closing,
    test_multi_gap_spreads_across_positions,
]


if __name__ == "__main__":
    for fn in TESTS:
        fn()
        print(f"OK  {fn.__name__}")
    print(f"test_gap_reserve: {len(TESTS)} tests OK")
