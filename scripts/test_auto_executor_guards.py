"""
Tests de los guardarraíles del ejecutor automático.

Cada tope tiene dos casos: uno que lo dispara y otro que lo respeta por poco.
El margen estrecho es lo que importa: un tope que solo se prueba con valores
absurdos no detecta un error de signo ni un off-by-one en el redondeo.

Todo corre sobre `plan_operations`, que es pura, más una comprobación de que la
capa de transporte despacha lo que el núcleo decidió.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import auto_executor as ax  # noqa: E402
from mister_actions import MisterWriteClient  # noqa: E402

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
M = 1_000_000


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _settings(**over) -> dict:
    s = {
        "slug": "test-league",
        "enabled": True,
        "dry_run": True,
        "kill_switch_off": False,
        "league_registered": True,
        "allowed_actions": [
            "bid",
            "offer",
            "clause_bid",
            "list_for_sale",
            "decline_offer",
            "accept_offer",
            "withdraw_offer",
        ],
        "max_spend_per_cycle_pct": 0.60,
        "max_single_buy_pct": 0.40,
        "max_clause_pct": 0.30,
        "max_clauses_per_cycle": 1,
        "max_ops_per_cycle": 6,
        "max_pending_offers": 2,
        "offer_stale_cycles": 2,
        "min_cash_floor": 0,
        "never_sell_xi_starters": True,
        "allow_rescind": False,
    }
    s.update(over)
    return s


def _plan(moves: list[dict], **constraints) -> dict:
    base = {
        "balance": 20 * M,
        "spendable": 20 * M,
        "max_debt": 20 * M,
        "free_slots_after_accepts": 6,
        "sale_remaining": 3,
        "debt_shortfall": 0,
        "hours_to_jornada": 40,
    }
    base.update(constraints)
    return {"constraints": base, "moves": moves}


def _bid(pid: str, amount: float, **over) -> dict:
    row = {
        "kind": "bid",
        "player_id": pid,
        "name": f"Libre {pid}",
        "position": "DF",
        "amount": amount,
        "id_market": int(pid) + 100,
    }
    row.update(over)
    return row


def _clause(pid: str, amount: float, **over) -> dict:
    row = {
        "kind": "clause_bid",
        "player_id": pid,
        "name": f"Clausulable {pid}",
        "position": "MF",
        "amount": amount,
        "clause": amount,
        "clause_known": True,
        "shield": 0,
        "owner_id": "777",
    }
    row.update(over)
    return row


def _listing(pid: str, price: float, **over) -> dict:
    row = {
        "kind": "list_for_sale",
        "player_id": pid,
        "name": f"Propio {pid}",
        "position": "FW",
        "price": price,
        "amount": price,
    }
    row.update(over)
    return row


def _run(moves, *, settings=None, state=None, rules=None, **constraints) -> dict:
    return ax.plan_operations(
        cycle_plan=_plan(moves, **constraints),
        settings=settings or _settings(),
        state=state or {},
        league_rules=rules or {"clauses": True, "transfer_wait": 1},
        now=NOW,
    )


def _kinds(decision: dict) -> list[str]:
    return [o["kind"] for o in decision["operations"]]


def _ids(decision: dict) -> list[str]:
    return [o["player_id"] for o in decision["operations"]]


def _skip_reason(decision: dict, pid: str) -> str:
    for s in decision["skipped"]:
        if s["player_id"] == pid:
            return s["reason"]
    raise AssertionError(f"{pid} no está en skipped: {decision['skipped']}")


# ---------------------------------------------------------------------------
# Interruptores de activación
# ---------------------------------------------------------------------------

def test_kill_switch_blocks_everything() -> None:
    d = _run([_bid("1", M)], settings=_settings(enabled=False, kill_switch_off=True))
    _assert(not d["enabled"], "con el kill switch apagado no se ejecuta nada")
    _assert(d["operations"] == [], d["operations"])
    _assert("kill switch" in d["reason"], d["reason"])


def test_unregistered_league_never_runs() -> None:
    d = _run(
        [_bid("1", M)],
        settings=_settings(enabled=False, kill_switch_off=False, league_registered=False),
    )
    _assert(not d["enabled"], "una liga ausente del fichero no se automatiza")
    _assert("no registrada" in d["reason"], d["reason"])


def test_action_not_in_allowed_actions_is_skipped() -> None:
    d = _run([_bid("1", M)], settings=_settings(allowed_actions=["list_for_sale"]))
    _assert(not d["operations"], "bid fuera de allowed_actions no se manda")
    _assert("allowed_actions" in _skip_reason(d, "1"), _skip_reason(d, "1"))


# ---------------------------------------------------------------------------
# max_single_buy_pct  (40% de 20M = 8M)
# ---------------------------------------------------------------------------

def test_single_buy_cap_triggers() -> None:
    d = _run([_bid("1", 8 * M + 100_000)])
    _assert(not d["operations"], "8,1M supera el 40% de 20M")
    _assert("max_single_buy_pct" in _skip_reason(d, "1"), _skip_reason(d, "1"))


def test_single_buy_cap_respected_by_a_hair() -> None:
    d = _run([_bid("1", 8 * M)])
    _assert(_ids(d) == ["1"], f"8M justo es el tope, debe pasar: {d['skipped']}")


def test_van_hecke_case_is_blocked() -> None:
    """El caso que originó todo: 20,1M de 21,7M con el once roto."""
    d = _run([_bid("1", 20_100_000, name="Van Hecke")], balance=21_700_000, spendable=21_700_000)
    _assert(not d["operations"], "no se puede llevar casi toda la caja en un defensa")


# ---------------------------------------------------------------------------
# max_spend_per_cycle_pct  (60% de 20M = 12M)
# ---------------------------------------------------------------------------

def test_cycle_spend_cap_triggers_on_the_third_buy() -> None:
    d = _run([_bid("1", 5 * M), _bid("2", 5 * M), _bid("3", 5 * M)])
    _assert(_ids(d) == ["1", "2"], f"la tercera compra rompe el 60%: {_ids(d)}")
    _assert("max_spend_per_cycle_pct" in _skip_reason(d, "3"), _skip_reason(d, "3"))


def test_cycle_spend_cap_respected_at_the_limit() -> None:
    d = _run([_bid("1", 6 * M), _bid("2", 6 * M)])
    _assert(len(d["operations"]) == 2, f"12M exactos entran: {d['skipped']}")
    _assert(d["budget"]["spent"] == 12 * M, d["budget"])


# ---------------------------------------------------------------------------
# min_cash_floor
# ---------------------------------------------------------------------------

def test_cash_floor_triggers() -> None:
    d = _run(
        [_bid("1", 4 * M)],
        settings=_settings(min_cash_floor=17 * M),
        balance=20 * M,
    )
    _assert(not d["operations"], "4M dejaría 16M, por debajo del suelo de 17M")
    _assert("suelo" in _skip_reason(d, "1"), _skip_reason(d, "1"))


def test_cash_floor_respected_at_the_limit() -> None:
    d = _run([_bid("1", 3 * M)], settings=_settings(min_cash_floor=17 * M))
    _assert(_ids(d) == ["1"], f"3M deja exactamente el suelo: {d['skipped']}")


# ---------------------------------------------------------------------------
# max_clause_pct (30% de 20M = 6M) y una cláusula por ciclo
# ---------------------------------------------------------------------------

def test_clause_cap_is_stricter_than_buy_cap() -> None:
    """7M pasa el tope de compra (8M) pero no el de cláusula (6M)."""
    d = _run([_clause("1", 7 * M)])
    _assert(not d["operations"], "la cláusula tiene su propio techo, más bajo")
    _assert("max_clause_pct" in _skip_reason(d, "1"), _skip_reason(d, "1"))


def test_clause_cap_respected_at_the_limit() -> None:
    d = _run([_clause("1", 6 * M)])
    _assert(_ids(d) == ["1"], f"6M exactos entran: {d['skipped']}")


def test_only_one_clause_per_cycle() -> None:
    d = _run([_clause("1", 2 * M), _clause("2", 2 * M)])
    _assert(_ids(d) == ["1"], f"una cláusula por ciclo: {_ids(d)}")
    _assert("tope de cláusulas" in _skip_reason(d, "2"), _skip_reason(d, "2"))


def test_two_clauses_when_config_allows() -> None:
    d = _run(
        [_clause("1", 2 * M), _clause("2", 2 * M)],
        settings=_settings(max_clauses_per_cycle=2),
        rules={"clauses": True, "transfer_wait": 1, "clause_rules": {"daily_limit": 0}},
    )
    _assert(len(d["operations"]) == 2, f"con el tope a 2 pasan las dos: {d['skipped']}")


def test_shielded_clause_never_reaches_the_post() -> None:
    d = _run([_clause("1", 2 * M, shield=3600)])
    _assert(not d["operations"], "un blindado no se puede clausular")
    _assert("blindado" in _skip_reason(d, "1"), _skip_reason(d, "1"))


def test_clause_without_owner_is_skipped() -> None:
    d = _run([_clause("1", 2 * M, owner_id=None)])
    _assert(not d["operations"], "sin dueño no se puede armar el POST")
    _assert("dueño" in _skip_reason(d, "1"), _skip_reason(d, "1"))


def test_clause_blocked_in_gameweek_window() -> None:
    rules = {"clauses": True, "clause_rules": {"enabled": True, "gameweek": 1}}
    d = _run([_clause("1", 2 * M)], rules=rules, hours_to_jornada=0.5)
    _assert(not d["operations"], "en las horas previas al pitido no se clausula")
    _assert("previas" in _skip_reason(d, "1") or "cláusula no ejercitable" in _skip_reason(d, "1"), _skip_reason(d, "1"))


def test_clause_open_far_from_kickoff() -> None:
    rules = {"clauses": True, "clause_rules": {"enabled": True, "gameweek": 1}}
    d = _run([_clause("1", 2 * M)], rules=rules, hours_to_jornada=40)
    _assert(_kinds(d) == ["clause_bid"], f"40 h antes sí se puede: {_kinds(d)}")


def test_second_clause_inside_24h_is_blocked() -> None:
    """Lo habitual en Mister: 1 cláusula cada 24 h, no cada ciclo de 8 h."""
    log = {
        "cycles": [
            {
                "at": (NOW - timedelta(hours=3)).isoformat(),
                "operations": [
                    {
                        "kind": "clause_bid",
                        "player_id": "9",
                        "status": "ok",
                    }
                ],
            }
        ]
    }
    d = _run(
        [_clause("1", 2 * M)],
        state={"automation_log": log},
        rules={"clauses": True, "clause_rules": {"enabled": True, "daily_limit": 1}},
    )
    _assert(not d["operations"], "a las 3 h aún no ha pasado la ventana")
    _assert("24" in _skip_reason(d, "1"), _skip_reason(d, "1"))


def test_clause_allowed_after_24h_window() -> None:
    log = {
        "cycles": [
            {
                "at": (NOW - timedelta(hours=25)).isoformat(),
                "operations": [
                    {"kind": "clause_bid", "player_id": "9", "status": "ok"}
                ],
            }
        ]
    }
    d = _run(
        [_clause("1", 2 * M)],
        state={"automation_log": log},
        rules={"clauses": True, "clause_rules": {"enabled": True, "daily_limit": 1}},
    )
    _assert(_kinds(d) == ["clause_bid"], f"a las 25 h ya cabe: {_kinds(d)}")


def test_unpublished_daily_limit_defaults_to_one_per_24h() -> None:
    log = {
        "cycles": [
            {
                "at": (NOW - timedelta(hours=1)).isoformat(),
                "operations": [
                    {"kind": "clause_bid", "player_id": "9", "status": "ok"}
                ],
            }
        ]
    }
    d = _run([_clause("1", 2 * M)], state={"automation_log": log})
    _assert(not d["operations"], "sin dato de admin se asume 1/24h")
    _assert("24" in _skip_reason(d, "1"), _skip_reason(d, "1"))


# ---------------------------------------------------------------------------
# Pujas a libres frente a ofertas a rivales
# ---------------------------------------------------------------------------

def test_rival_listing_becomes_an_offer_not_a_bid() -> None:
    d = _run([_bid("1", 3 * M, owner_id="555", listed_by_rival=True)])
    _assert(_kinds(d) == ["offer"], f"un listado de rival es una oferta: {_kinds(d)}")
    _assert(d["operations"][0]["params"]["offeree_id"] == "555", d["operations"][0])


def test_free_agent_bid_has_no_offeree() -> None:
    d = _run([_bid("1", 3 * M)])
    _assert(_kinds(d) == ["bid"], _kinds(d))
    _assert(d["operations"][0]["params"]["offeree_id"] == 0, d["operations"][0])


def test_offers_do_not_consume_cycle_spend() -> None:
    """Una oferta pendiente no es dinero gastado: no puede bloquear las pujas."""
    d = _run(
        [
            _bid("1", 7 * M, owner_id="555", listed_by_rival=True),
            _bid("2", 7 * M, owner_id="556", listed_by_rival=True),
            _bid("3", 6 * M),
        ]
    )
    _assert(_ids(d) == ["3", "1", "2"], f"las pujas van antes que las ofertas: {_ids(d)}")
    _assert(d["budget"]["spent"] == 6 * M, f"solo la puja gasta: {d['budget']}")


def test_max_pending_offers_triggers() -> None:
    moves = [
        _bid(str(i), M, owner_id=f"55{i}", listed_by_rival=True) for i in (1, 2, 3)
    ]
    d = _run(moves)
    _assert(len(d["operations"]) == 2, f"tope de 2 ofertas vivas: {_ids(d)}")
    _assert("ofertas vivas" in _skip_reason(d, "3"), _skip_reason(d, "3"))


def test_existing_offers_count_against_the_quota() -> None:
    d = _run(
        [_bid("1", M, owner_id="555", listed_by_rival=True)],
        state={"offers_sent": [{"player_id": "9"}, {"player_id": "8"}]},
    )
    _assert(not d["operations"], "ya hay 2 ofertas vivas, no cabe otra")


def test_stale_offers_are_withdrawn() -> None:
    log = {
        "cycles": [
            {
                "at": (NOW - timedelta(hours=30)).isoformat(),
                "operations": [{"kind": "offer", "player_id": "9", "status": "ok"}],
            },
            {"at": (NOW - timedelta(hours=20)).isoformat(), "operations": []},
        ]
    }
    d = _run(
        [],
        state={"offers_sent": [{"player_id": "9", "id_market": 5, "owner_id": "555"}],
               "automation_log": log},
    )
    _assert(_kinds(d) == ["withdraw_offer"], f"la oferta lleva 2 ciclos muerta: {_kinds(d)}")
    _assert(d["operations"][0]["player_id"] == "9", d["operations"][0])


def test_fresh_offer_is_not_withdrawn() -> None:
    log = {
        "cycles": [
            {
                "at": (NOW - timedelta(hours=2)).isoformat(),
                "operations": [{"kind": "offer", "player_id": "9", "status": "ok"}],
            }
        ]
    }
    d = _run(
        [],
        state={"offers_sent": [{"player_id": "9"}], "automation_log": log},
    )
    _assert(not d["operations"], "una oferta de este ciclo se deja vivir")


# ---------------------------------------------------------------------------
# Ventas
# ---------------------------------------------------------------------------

def test_sale_limit_exhausted_blocks_listing() -> None:
    d = _run([_listing("1", M)], sale_remaining=0)
    _assert(not d["operations"], "con 5 de 5 listados no se puede listar más")
    _assert("sale_limit" in _skip_reason(d, "1"), _skip_reason(d, "1"))


def test_sale_limit_allows_exactly_the_remaining_slots() -> None:
    d = _run([_listing("1", M), _listing("2", M), _listing("3", M)], sale_remaining=2)
    _assert(_ids(d) == ["1", "2"], f"solo caben 2: {_ids(d)}")


def test_transfer_wait_blocks_listing_a_fresh_signing() -> None:
    log = {
        "cycles": [
            {
                "at": (NOW - timedelta(hours=8)).isoformat(),
                "operations": [{"kind": "bid", "player_id": "1", "status": "ok"}],
            }
        ]
    }
    d = _run([_listing("1", M)], state={"automation_log": log})
    _assert(not d["operations"], "fichado hace 8h, faltan 16h para poder listarlo")
    _assert("espera compra" in _skip_reason(d, "1"), _skip_reason(d, "1"))


def test_transfer_wait_expired_allows_listing() -> None:
    log = {
        "cycles": [
            {
                "at": (NOW - timedelta(hours=25)).isoformat(),
                "operations": [{"kind": "bid", "player_id": "1", "status": "ok"}],
            }
        ]
    }
    d = _run([_listing("1", M)], state={"automation_log": log})
    _assert(_ids(d) == ["1"], f"25h > 24h de espera: {d['skipped']}")


def test_transfer_wait_off_ignores_the_log() -> None:
    log = {
        "cycles": [
            {
                "at": (NOW - timedelta(hours=1)).isoformat(),
                "operations": [{"kind": "bid", "player_id": "1", "status": "ok"}],
            }
        ]
    }
    d = _run(
        [_listing("1", M)],
        state={"automation_log": log},
        rules={"clauses": True, "transfer_wait": 0},
    )
    _assert(_ids(d) == ["1"], "con transfer_wait a 0 se puede listar al instante")


def test_never_sell_xi_starters_triggers() -> None:
    d = _run([_listing("1", M)], state={"xi_ids": ["1"]})
    _assert(not d["operations"], "no se lista un titular del once")
    _assert("titular" in _skip_reason(d, "1"), _skip_reason(d, "1"))


def test_starter_can_be_listed_when_protection_is_off() -> None:
    d = _run(
        [_listing("1", M)],
        settings=_settings(never_sell_xi_starters=False),
        state={"xi_ids": ["1"]},
    )
    _assert(_ids(d) == ["1"], "con la protección apagada se puede listar")


def test_rescind_is_off_by_default() -> None:
    move = {"kind": "sell_to_system", "player_id": "1", "name": "Lastre", "amount": 2 * M}
    d = _run([move], settings=_settings(allowed_actions=["sell_to_system"]), debt_shortfall=5 * M)
    _assert(not d["operations"], "rescindir regala el 20%: apagado de inicio")
    _assert("allow_rescind" in _skip_reason(d, "1"), _skip_reason(d, "1"))


def test_rescind_needs_real_debt_even_when_allowed() -> None:
    move = {"kind": "sell_to_system", "player_id": "1", "name": "Lastre", "amount": 2 * M}
    d = _run(
        [move],
        settings=_settings(allowed_actions=["sell_to_system"], allow_rescind=True),
        debt_shortfall=0,
    )
    _assert(not d["operations"], "sin deuda no se rescinde")
    _assert("sin deuda" in _skip_reason(d, "1"), _skip_reason(d, "1"))


def test_rescind_fires_with_debt_and_permission() -> None:
    move = {"kind": "sell_to_system", "player_id": "1", "name": "Lastre", "amount": 2 * M}
    d = _run(
        [move],
        settings=_settings(allowed_actions=["sell_to_system"], allow_rescind=True),
        debt_shortfall=5 * M,
    )
    _assert(_kinds(d) == ["sell_to_system"], f"con deuda sí: {d['skipped']}")


def test_accept_offer_stays_off_unless_allowed() -> None:
    move = {
        "kind": "accept_offer",
        "player_id": "1",
        "name": "Vendible",
        "amount": 3 * M,
        "id_bid": "42",
    }
    d = _run([move], settings=_settings(allowed_actions=["bid"]))
    _assert(not d["operations"], "el endpoint de aceptar no está sondeado")


def test_accepted_cash_does_not_widen_the_caps() -> None:
    """
    Una venta no puede ser la excusa del fichaje caro.

    Los topes se calculan sobre el saldo usable al abrir el ciclo, así que
    aceptar una oferta de 10M no habilita una compra de 12M.
    """
    accept = {
        "kind": "accept_offer",
        "player_id": "9",
        "name": "Vendible",
        "amount": 10 * M,
        "id_bid": "42",
    }
    d = _run([accept, _bid("1", 12 * M)])
    _assert(_ids(d) == ["9"], f"la compra sigue fuera de tope: {_ids(d)}")
    _assert("max_single_buy_pct" in _skip_reason(d, "1"), _skip_reason(d, "1"))
    _assert(d["budget"]["cash_in"] == 10 * M, d["budget"])


# ---------------------------------------------------------------------------
# Cupo de plantilla y tope de operaciones
# ---------------------------------------------------------------------------

def test_squad_cap_blocks_buys() -> None:
    d = _run([_bid("1", M), _bid("2", M)], free_slots_after_accepts=1)
    _assert(_ids(d) == ["1"], f"solo queda una plaza: {_ids(d)}")
    _assert("cupo" in _skip_reason(d, "2"), _skip_reason(d, "2"))


def test_accept_frees_a_slot_for_a_buy() -> None:
    accept = {
        "kind": "accept_offer",
        "player_id": "9",
        "name": "Vendible",
        "amount": 2 * M,
        "id_bid": "42",
    }
    d = _run([accept, _bid("1", M), _bid("2", M)], free_slots_after_accepts=1)
    _assert(_ids(d) == ["9", "1", "2"], f"la venta libera plaza antes de comprar: {_ids(d)}")


def test_max_ops_per_cycle_triggers() -> None:
    moves = [_bid(str(i), M) for i in range(1, 6)]
    d = _run(moves, settings=_settings(max_ops_per_cycle=3))
    _assert(len(d["operations"]) == 3, f"tope de 3 operaciones: {_ids(d)}")
    _assert("tope de operaciones" in _skip_reason(d, "4"), _skip_reason(d, "4"))


def test_hold_offer_moves_are_ignored() -> None:
    d = _run([{"kind": "hold_offer", "player_id": "1", "name": "En cartera"}])
    _assert(not d["operations"] and not d["skipped"], "hold no es una acción")


# ---------------------------------------------------------------------------
# Orden de ejecución
# ---------------------------------------------------------------------------

def test_execution_order_is_sells_then_clauses_then_bids_then_offers() -> None:
    moves = [
        _bid("5", M, owner_id="555", listed_by_rival=True),
        _bid("4", M),
        _clause("3", M),
        _listing("2", M),
        {
            "kind": "accept_offer",
            "player_id": "1",
            "name": "Vendible",
            "amount": M,
            "id_bid": "42",
        },
    ]
    d = _run(moves)
    _assert(
        _kinds(d) == ["accept_offer", "list_for_sale", "clause_bid", "bid", "offer"],
        f"orden incorrecto: {_kinds(d)}",
    )
    _assert([o["seq"] for o in d["operations"]] == [1, 2, 3, 4, 5], d["operations"])


# ---------------------------------------------------------------------------
# Capa de transporte
# ---------------------------------------------------------------------------

def test_execute_dispatches_each_operation_once() -> None:
    d = _run([_listing("1", M), _bid("2", 2 * M)])
    calls: list[tuple[str, dict]] = []
    client = MisterWriteClient(
        dry_run=False,
        verified_only=False,
        transport=lambda path, data: calls.append((path, data)) or {"status": "ok"},
    )
    ax.execute(d, client=client)
    _assert([c[0] for c in calls] == ["/ajax/sale", "/ajax/bid"], calls)
    _assert(d["executed"] == 2 and d["failed"] == 0, d)
    _assert(all(o["status"] == "ok" for o in d["operations"]), d["operations"])


def test_execute_in_dry_run_sends_nothing() -> None:
    d = _run([_bid("1", M)])
    calls: list = []
    client = MisterWriteClient(dry_run=True, transport=lambda p, data: calls.append(p))
    ax.execute(d, client=client)
    _assert(not calls, "en simulación no sale ninguna petición")
    _assert(d["operations"][0]["status"] == "dry_run", d["operations"][0])


def test_execute_survives_a_rejected_operation() -> None:
    d = _run([_listing("1", M), _listing("2", M)])

    def transport(path, data):
        if data.get("id_player") == "1":
            raise RuntimeError("market_blocked")
        return {"status": "ok"}

    client = MisterWriteClient(dry_run=False, verified_only=False, transport=transport)
    ax.execute(d, client=client)
    _assert(d["failed"] == 1 and d["executed"] == 1, d)
    _assert("market_blocked" in d["operations"][0]["error"], d["operations"][0])
    _assert(d["operations"][1]["status"] == "ok", d["operations"][1])


def test_unverified_endpoint_is_blocked_in_live_mode() -> None:
    move = {
        "kind": "accept_offer",
        "player_id": "1",
        "name": "Vendible",
        "amount": M,
        "id_bid": "42",
    }
    d = _run([move])
    client = MisterWriteClient(dry_run=False, transport=lambda p, data: {"status": "ok"})
    ax.execute(d, client=client)
    _assert(d["failed"] == 1, d)
    _assert("sin confirmar" in d["operations"][0]["error"], d["operations"][0])


# ---------------------------------------------------------------------------
# El log que escribe es el que luego lee
# ---------------------------------------------------------------------------

def test_log_entry_feeds_back_the_transfer_lock() -> None:
    """
    Ida y vuelta del log.

    El ejecutor deduce la espera de 24h de lo que él mismo apuntó en ciclos
    anteriores. Si el formato que escribe deja de ser el que lee, listaría un
    fichaje recién hecho sin enterarse.
    """
    bought = _run([_bid("1", 2 * M)])
    ax.execute(
        bought,
        client=MisterWriteClient(
            dry_run=True, transport=lambda *a, **k: {"status": "ok"}
        ),
    )
    entry = ax.log_entry(bought)
    _assert(entry["operations"][0]["status"] == "dry_run", entry)
    _assert(entry["counts"]["planned"] == 1, entry["counts"])

    log = {"cycles": [entry]}
    locked = ax.transfer_locked_ids(
        automation_log=log, transfer_wait_hours=24, now=NOW + timedelta(hours=3)
    )
    _assert("1" in locked, f"la compra de hace 3h debe bloquear el listado: {locked}")
    _assert(21 - 0.1 < locked["1"] < 21 + 0.1, locked)


def test_failed_buy_does_not_lock_the_player() -> None:
    """Si Mister rechazó la puja, el jugador no es nuestro y no hay espera."""
    entry = {
        "at": (NOW - timedelta(hours=1)).isoformat(),
        "operations": [{"kind": "bid", "player_id": "1", "status": "error"}],
    }
    locked = ax.transfer_locked_ids(
        automation_log={"cycles": [entry]}, transfer_wait_hours=24, now=NOW
    )
    _assert(not locked, f"una puja fallida no bloquea nada: {locked}")


def test_bid_without_id_market_is_not_posted() -> None:
    """id_market=0 es lo que mandó el ejecutor y Mister respondió 400."""
    d = _run([_bid("1", M, id_market=None)])
    calls: list = []
    client = MisterWriteClient(
        dry_run=False,
        transport=lambda p, data: calls.append((p, data)) or {"status": "ok"},
    )
    ax.execute(d, client=client)
    _assert(not calls, f"sin id_market no se POST: {calls}")
    _assert(d["operations"][0]["status"] == "error", d["operations"][0])
    _assert("id_market" in str(d["operations"][0].get("error")), d["operations"][0])


def test_execute_hydrates_id_market_from_lookup() -> None:
    d = _run([_bid("1", M, id_market=None)])
    calls: list = []
    client = MisterWriteClient(
        dry_run=False,
        transport=lambda p, data: calls.append((p, data)) or {"status": "ok"},
    )
    ax.execute(
        d,
        client=client,
        listing_lookup=lambda pid: {"id_market": 4242, "action": "bid"},
    )
    _assert(len(calls) == 1, calls)
    _assert(calls[0][1]["id_market"] == 4242, calls[0])
    _assert(calls[0][1]["id_player"] == "1", calls[0])
    _assert(d["operations"][0]["status"] == "ok", d["operations"][0])


def test_already_active_bid_uses_update_action() -> None:
    d = _run([_bid("1", M, id_market=None)])
    calls: list = []
    client = MisterWriteClient(
        dry_run=False,
        transport=lambda p, data: calls.append((p, data)) or {"status": "ok"},
    )
    ax.execute(
        d,
        client=client,
        listing_lookup=lambda pid: {"id_market": 7, "action": "update"},
    )
    _assert(calls[0][1]["action"] == "update", calls[0])


def test_community_id_from_payload() -> None:
    _assert(
        ax.community_id_from_payload({"sources": {"id_community": "2550556"}})
        == "2550556",
        "sources",
    )
    _assert(
        ax.community_id_from_payload({"league": {"id": "2550628"}}) == "2550628",
        "league.id",
    )
    _assert(ax.community_id_from_payload({}) == "", "vacío")


TESTS = [
    test_kill_switch_blocks_everything,
    test_unregistered_league_never_runs,
    test_action_not_in_allowed_actions_is_skipped,
    test_single_buy_cap_triggers,
    test_single_buy_cap_respected_by_a_hair,
    test_van_hecke_case_is_blocked,
    test_cycle_spend_cap_triggers_on_the_third_buy,
    test_cycle_spend_cap_respected_at_the_limit,
    test_cash_floor_triggers,
    test_cash_floor_respected_at_the_limit,
    test_clause_cap_is_stricter_than_buy_cap,
    test_clause_cap_respected_at_the_limit,
    test_only_one_clause_per_cycle,
    test_two_clauses_when_config_allows,
    test_shielded_clause_never_reaches_the_post,
    test_clause_without_owner_is_skipped,
    test_clause_blocked_in_gameweek_window,
    test_clause_open_far_from_kickoff,
    test_second_clause_inside_24h_is_blocked,
    test_clause_allowed_after_24h_window,
    test_unpublished_daily_limit_defaults_to_one_per_24h,
    test_rival_listing_becomes_an_offer_not_a_bid,
    test_free_agent_bid_has_no_offeree,
    test_offers_do_not_consume_cycle_spend,
    test_max_pending_offers_triggers,
    test_existing_offers_count_against_the_quota,
    test_stale_offers_are_withdrawn,
    test_fresh_offer_is_not_withdrawn,
    test_sale_limit_exhausted_blocks_listing,
    test_sale_limit_allows_exactly_the_remaining_slots,
    test_transfer_wait_blocks_listing_a_fresh_signing,
    test_transfer_wait_expired_allows_listing,
    test_transfer_wait_off_ignores_the_log,
    test_never_sell_xi_starters_triggers,
    test_starter_can_be_listed_when_protection_is_off,
    test_rescind_is_off_by_default,
    test_rescind_needs_real_debt_even_when_allowed,
    test_rescind_fires_with_debt_and_permission,
    test_accept_offer_stays_off_unless_allowed,
    test_accepted_cash_does_not_widen_the_caps,
    test_squad_cap_blocks_buys,
    test_accept_frees_a_slot_for_a_buy,
    test_max_ops_per_cycle_triggers,
    test_hold_offer_moves_are_ignored,
    test_execution_order_is_sells_then_clauses_then_bids_then_offers,
    test_execute_dispatches_each_operation_once,
    test_execute_in_dry_run_sends_nothing,
    test_execute_survives_a_rejected_operation,
    test_unverified_endpoint_is_blocked_in_live_mode,
    test_log_entry_feeds_back_the_transfer_lock,
    test_failed_buy_does_not_lock_the_player,
    test_bid_without_id_market_is_not_posted,
    test_execute_hydrates_id_market_from_lookup,
    test_already_active_bid_uses_update_action,
    test_community_id_from_payload,
]


if __name__ == "__main__":
    for fn in TESTS:
        fn()
        print(f"OK  {fn.__name__}")
    print(f"test_auto_executor_guards: {len(TESTS)} tests OK")
