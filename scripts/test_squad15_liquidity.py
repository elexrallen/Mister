"""Plantilla 15: reserva 0, liquidez por ventas, economía MD vs Patio."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
from competitive_actions import (  # noqa: E402
    build_sell_opportunities,
    pick_funding_slot,
    promote_funded_swaps,
    resolve_liquidity_slots,
    swap_covers,
)
from daily_playbook import build_daily_playbook  # noqa: E402
from league_rules import normalize_rules, resolve_economy  # noqa: E402
from target_board import _select_daily_primary_targets, funding_plan_from_board  # noqa: E402


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _p(
    pid: str,
    pos: str,
    price: float,
    *,
    name: str | None = None,
    ep: float = 40.0,
    lineup: float = 40.0,
) -> dict:
    return {
        "id": pid,
        "name": name or pid,
        "position": pos,
        "price": price,
        "market_value": price,
        "ep_score": ep,
        "production_score": ep,
        "lineup_prob": lineup / 100.0,
        "external": {"lineup_prob_ext": lineup},
    }


def test_package_reserve_is_zero() -> None:
    _assert(int(getattr(config, "PACKAGE_CASH_RESERVE", 0) or 0) == 0, "PACKAGE_CASH_RESERVE debe ser 0")


def test_funding_plan_does_not_reserve_cash() -> None:
    board = {
        "primary_targets": [
            {
                "player_id": "u1",
                "name": "Crack",
                "position": "FW",
                "price": 20_000_000,
                "ep_score": 90,
                "on_daily_market": False,
            },
        ],
        "cash_reserved": 0,
        "moves": {"buy": []},
        "balance": 12_000_000,
    }
    funding = funding_plan_from_board(board, balance=12_000_000)
    _assert(float(funding["cash_reserved"]) == 0.0, "cash_reserved debe ser 0")
    _assert(float(funding["funding_target"]) == 0.0, "off-market no entra en funding_target")
    _assert("listados" in str(funding.get("liquidity_note") or "").lower() or "15" in str(funding.get("liquidity_note") or ""), "nota de liquidez")


def test_funding_only_on_daily_market() -> None:
    board = {
        "primary_targets": [
            {
                "player_id": "u1",
                "name": "Crack",
                "position": "FW",
                "price": 20_000_000,
                "ep_score": 90,
                "on_daily_market": False,
            },
            {
                "player_id": "d1",
                "name": "Hoy",
                "position": "MF",
                "price": 500_000,
                "ep_score": 50,
                "on_daily_market": True,
            },
        ],
        "moves": {"buy": []},
        "balance": 12_000_000,
    }
    funding = funding_plan_from_board(board, balance=12_000_000)
    names = {t.get("name") for t in funding.get("primary_targets") or []}
    _assert(names == {"Hoy"}, names)
    _assert(float(funding["funding_target"]) == 500_000, funding)


def test_three_daily_primaries_no_cap_of_two() -> None:
    rows = [
        {
            "player_id": f"p{i}",
            "name": f"N{i}",
            "position": pos,
            "price": 100_000,
            "ep_score": 40 + i,
            "on_daily_market": True,
            "role": "starter",
        }
        for i, pos in enumerate(["GK", "DF", "MF"])
    ]
    selected = _select_daily_primary_targets(rows, balance=1_000_000)
    _assert(len(selected) == 3, selected)


def test_swap_covers() -> None:
    _assert(swap_covers(3_000_000, 8_000_000, 10_000_000), "3+8 cubre 10")
    _assert(not swap_covers(3_000_000, 2_000_000, 10_000_000), "3+2 no cubre 10")


def test_pick_slot_upgrades_from_weakest_to_cheapest_cover() -> None:
    weak = _p("w", "DF", 2_000_000, name="Flojo", ep=20)
    cover = _p("c", "DF", 8_000_000, name="Barato-cubre", ep=28)
    extra = _p("x", "MF", 12_000_000, name="Caro", ep=30)
    slot = pick_funding_slot([weak, cover, extra], need_cash=7_000_000, prefer_pos="DF")
    _assert(slot is not None and slot["id"] == "c", f"debe listar el más barato que cubre, got {slot}")
    slot_weak = pick_funding_slot([weak, cover], need_cash=1_500_000, prefer_pos="DF")
    _assert(slot_weak is not None and slot_weak["id"] == "w", "si el débil cubre, se lista el débil")


def test_liquidity_lists_slot_and_scout_off_market() -> None:
    xi = [_p(f"xi{i}", "DF", 6_000_000, name=f"Tit{i}", ep=50, lineup=80) for i in range(4)]
    weak = _p("bench1", "DF", 8_000_000, name="Banquillo", ep=22, lineup=30)
    squad = xi + [weak]
    rec_xi = {"xi": [{"player_id": p["id"]} for p in xi]}
    board = {
        "primary_targets": [
            {
                "player_id": "tgt",
                "name": "UpgradeDF",
                "position": "DF",
                "price": 7_000_000,
                "ep_score": 80,
                "production_score": 80,
                "ff_apps": 20,
                "ff_mister_avg": 7.5,
                "on_daily_market": False,
            }
        ],
        "aspirational_targets": [],
        "moves": {"buy": []},
    }
    liq = resolve_liquidity_slots(
        squad=squad,
        recommended_xi=rec_xi,
        market_opportunities=[],
        target_board=board,
        balance=1_000_000,
        sale_limit=5,
    )
    _assert("bench1" in liq["slot_ids"], f"debe listar el slot que cubre, got {liq['slot_ids']}")
    funded = [p for p in liq["profiles"] if p.get("player_id") == "tgt"]
    _assert(funded, "perfil off-market que renta debe perseguirse")
    _assert(funded[0].get("slot_id") == "bench1", "el listado financia la banda")


def test_unaffordable_without_breaking_xi_is_dropped() -> None:
    xi = [_p("xi1", "DF", 6_000_000, name="Tit", ep=50, lineup=80)]
    cheap = _p("b1", "DF", 1_000_000, name="Churro", ep=15, lineup=20)
    liq = resolve_liquidity_slots(
        squad=xi + [cheap],
        recommended_xi={"xi": [{"player_id": "xi1"}]},
        market_opportunities=[],
        target_board={
            "primary_targets": [
                {
                    "player_id": "mega",
                    "name": "Crack",
                    "position": "DF",
                    "price": 40_000_000,
                    "ep_score": 99,
                    "production_score": 90,
                    "ff_apps": 25,
                    "ff_mister_avg": 8.0,
                    "on_daily_market": False,
                }
            ],
            "moves": {"buy": []},
        },
        balance=500_000,
        sale_limit=5,
    )
    avoided = [p for p in liq["avoided"] if p.get("player_id") == "mega"]
    _assert(avoided, "inabordable sin romper el once → no perseguir")
    _assert(avoided[0].get("avoid_reason") == "inabordable_sin_romper_xi", avoided[0])


def test_low_delta_not_pursued() -> None:
    xi = [_p("xi1", "DF", 6_000_000, name="Tit", ep=70, lineup=80)]
    bench = _p("b1", "DF", 9_000_000, name="Banco", ep=20, lineup=20)
    liq = resolve_liquidity_slots(
        squad=xi + [bench],
        recommended_xi={"xi": [{"player_id": "xi1"}]},
        market_opportunities=[],
        target_board={
            "primary_targets": [
                {
                    "player_id": "meh",
                    "name": "CasiIgual",
                    "position": "DF",
                    "price": 7_000_000,
                    "ep_score": 72,
                    "on_daily_market": False,
                }
            ],
            "moves": {"buy": []},
        },
        balance=2_000_000,
        sale_limit=5,
    )
    avoided = [p for p in liq["avoided"] if p.get("player_id") == "meh"]
    _assert(avoided, "ΔEP flojo → no perseguir")
    _assert(avoided[0].get("avoid_reason") == "delta_ep_or_roi", avoided[0])


def test_sell_opportunities_respect_sale_limit_and_liquidity_reason() -> None:
    xi = [_p(f"xi{i}", "MF", 5_000_000, name=f"Tit{i}", ep=55, lineup=80) for i in range(3)]
    benches = [_p(f"b{i}", "MF", 3_000_000 + i * 500_000, name=f"Banco{i}", ep=18, lineup=25) for i in range(6)]
    me = {"squad": xi + benches, "balance": 4_000_000, "rank": 8}
    sells = build_sell_opportunities(
        me,
        {"alerts": [], "by_position": {}},
        [],
        recommended_xi={"xi": [{"player_id": p["id"]} for p in xi]},
        league_economy={"sale_limit": 3},
        target_board={"primary_targets": [], "moves": {"buy": []}},
    )
    listed = [s for s in sells if s.get("action") == "sell"]
    _assert(len(listed) <= 3, f"sale_limit 3, got {len(listed)}")
    _assert(any(s.get("sell_reason") == "liquidity_slot" for s in listed), "debe haber liquidity_slot")
    listed_ids = {s["player_id"] for s in listed}
    _assert(not listed_ids & {p["id"] for p in xi}, "no listar el once recomendado")


def test_promote_swap_buy_now_when_settlement_timely() -> None:
    plan = [
        {
            "player_id": "upg",
            "name": "Upgrade",
            "action": "wait",
            "on_daily_market": True,
            "bid": 10_000_000,
            "price": 10_000_000,
            "why": "upgrade DF",
        },
        {
            "player_id": "slot",
            "name": "Listado",
            "action": "sell",
            "sell_reason": "liquidity_slot",
            "price": 8_000_000,
            "expected_proceeds": 8_000_000,
            "funds_for": "upg",
        },
    ]
    promote_funded_swaps(plan, balance=3_000_000, hours_to_jornada=120.0, cash_lag_hours=16.0)
    _assert(plan[0]["action"] == "buy_now", "swap a tiempo → buy_now")
    _assert(plan[0].get("swap_funded"), "flag swap_funded")


def test_md_no_gw_cash_bonus() -> None:
    rules = normalize_rules(
        {
            "custom_rules": (
                "No hay bonificaciones al cierre de la jornada. "
                "1º Clasificado: 2.000 Créditos. Premios finales PayPal."
            ),
            "salaries": 0,
            "rewards": 1,
            "prizes": {"points": 0, "goals": 0, "best_xi": 0, "fixed": 0},
            "sale_limit": 5,
        }
    )
    eco = rules["economy"]
    _assert(eco["gw_cash_bonus"] is False, f"MD no debe inflar caja: {eco}")
    _assert(eco["credit_prizes"] is True, "créditos ≠ balance")
    _assert(float(eco["expected_gw_cash"] or 0) == 0, "premios custom no suman a balance")
    _assert(eco["usable_for_bids_today"] is False, "nunca pujar con el bonus")
    _assert(int(eco["sale_limit"]) == 5, "sale_limit")


def test_patio_rewards_post_gw_not_today_cash() -> None:
    rules = normalize_rules(
        {
            "custom_rules": None,
            "salaries": 0,
            "rewards": 1,
            "prizes": {"points": 0, "goals": 0, "best_xi": 0, "fixed": 0},
            "sale_limit": 5,
        }
    )
    eco = rules["economy"]
    _assert(eco["gw_cash_bonus"] is True, f"Patio con rewards: {eco}")
    _assert(eco["when"] == "after_gameweek", "solo post-jornada")
    _assert(eco["usable_for_bids_today"] is False, "no es saldo de hoy")


def test_prizes_euros_and_salaries_subtract() -> None:
    eco = resolve_economy(
        {
            "rewards": 1,
            "custom_rules": None,
            "salaries": 1,
            "prizes": {"points": 2_000_000, "goals": 0, "best_xi": 0, "fixed": 0},
            "sale_limit": 4,
        }
    )
    _assert(eco["salaries"] is True, "salarios on")
    _assert(int(eco["sale_limit"]) == 4, "sale_limit custom")
    _assert(float(eco["expected_gw_cash"] or 0) == 0, "salarios restan / anulan estimación")
    eco2 = resolve_economy(
        {
            "rewards": 1,
            "custom_rules": None,
            "salaries": 0,
            "prizes": {"points": 2_000_000, "goals": 0, "best_xi": 0, "fixed": 0},
            "sale_limit": 4,
        }
    )
    _assert(eco2["gw_cash_bonus"] is True, "euros de prizes")
    _assert(float(eco2["expected_gw_cash"]) == 2_000_000, eco2)


def test_playbook_spend_15_copy() -> None:
    pb = build_daily_playbook(
        hours_to_jornada=80.0,
        competition_phase="active",
        action_plan=[
            {"action": "buy_now", "name": "Fichaje", "player_id": "1"},
            {"action": "sell", "name": "Listado", "player_id": "2", "sell_reason": "liquidity_slot"},
        ],
        recommended_xi={"summary": {"complete": True, "xi_count": 11, "xi_target": 11}},
        diagnostico={"bootstrap_xi": {"active": False}},
        me={"balance": 10_000_000},
        league_rules={
            "economy": {
                "gw_cash_bonus": True,
                "expected_gw_cash": 0,
                "source": "rewards_default",
                "usable_for_bids_today": False,
            }
        },
    )
    ids = {c["id"] for c in pb["checklist"]}
    _assert("gastar_15" in ids, f"playbook debe pedir gastar en el 15: {ids}")
    gastar = next(c for c in pb["checklist"] if c["id"] == "gastar_15")
    _assert("listados" in gastar["detail"].lower() or "reserva" in gastar["detail"].lower(), gastar)
    bonus = next((c for c in pb["checklist"] if c["id"] == "bonus_jornada"), None)
    _assert(bonus, "bonus post-jornada visible")
    _assert("hoy" in bonus["detail"].lower() or "pujar" in bonus["detail"].lower(), bonus)
    sells = next(c for c in pb["checklist"] if c["id"] == "listar_ventas")
    _assert("listados" in sells["detail"].lower(), sells)


def test_playbook_visperas_follows_buy_now() -> None:
    pb = build_daily_playbook(
        hours_to_jornada=12.0,
        competition_phase="active",
        action_plan=[
            {"action": "buy_now", "name": "Perez", "player_id": "1"},
        ],
        recommended_xi={"summary": {"complete": True, "xi_count": 11, "xi_target": 11}},
        diagnostico={"bootstrap_xi": {"active": False}},
        me={"balance": 5_000_000},
        league_rules={},
    )
    ids = {c["id"] for c in pb["checklist"]}
    _assert(pb.get("phase") == "visperas", pb.get("phase"))
    _assert("no_fichar" not in ids, ids)
    _assert("fichar_vispera" in ids, ids)
    hit = next(c for c in pb["checklist"] if c["id"] == "fichar_vispera")
    _assert("Perez" in hit["detail"], hit)


def test_playbook_visperas_without_buys() -> None:
    pb = build_daily_playbook(
        hours_to_jornada=12.0,
        competition_phase="active",
        action_plan=[],
        recommended_xi={"summary": {"complete": True, "xi_count": 11, "xi_target": 11}},
        diagnostico={"bootstrap_xi": {"active": False}},
        me={"balance": 5_000_000},
        league_rules={},
    )
    ids = {c["id"] for c in pb["checklist"]}
    _assert("no_fichar" in ids, ids)
    _assert("fichar_vispera" not in ids, ids)


def test_no_fund_target_for_off_market_primary() -> None:
    squad = [_p(f"s{i}", "DF", 2_000_000, name=f"S{i}", ep=30, lineup=40) for i in range(4)]
    squad.append(_p("bench", "MF", 3_000_000, name="Banco", ep=18, lineup=20))
    sells = build_sell_opportunities(
        {"balance": 500_000, "rank": 8, "squad": squad},
        {"by_position": {}, "alerts": []},
        [],
        diagnostico_plantilla={"financiero": {}},
        funding_info={
            "primary_targets": [
                {
                    "player_id": "off",
                    "name": "Fuera",
                    "price": 8_000_000,
                    "on_daily_market": False,
                }
            ],
            "funding_target": 8_000_000,
            "funding_shortfall": 7_500_000,
            "cash_tight": True,
            "positions": ["FW"],
        },
        recommended_xi={"xi": [{"player_id": p["id"]} for p in squad[:4]]},
    )
    fund = [s for s in sells if s.get("sell_reason") == "fund_target"]
    _assert(not fund, fund)


if __name__ == "__main__":
    test_package_reserve_is_zero()
    test_funding_plan_does_not_reserve_cash()
    test_funding_only_on_daily_market()
    test_three_daily_primaries_no_cap_of_two()
    test_swap_covers()
    test_pick_slot_upgrades_from_weakest_to_cheapest_cover()
    test_liquidity_lists_slot_and_scout_off_market()
    test_unaffordable_without_breaking_xi_is_dropped()
    test_low_delta_not_pursued()
    test_sell_opportunities_respect_sale_limit_and_liquidity_reason()
    test_promote_swap_buy_now_when_settlement_timely()
    test_md_no_gw_cash_bonus()
    test_patio_rewards_post_gw_not_today_cash()
    test_prizes_euros_and_salaries_subtract()
    test_playbook_spend_15_copy()
    test_playbook_visperas_follows_buy_now()
    test_playbook_visperas_without_buys()
    test_no_fund_target_for_off_market_primary()
    print("test_squad15_liquidity: OK")
