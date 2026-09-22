"""
Tests de elegibilidad de cláusula.

Pagar una cláusula es instantáneo e irreversible: no hay ciclo que la deshaga.
Cualquier regla verificable que la impida tiene que bloquearla antes del POST.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from competitive_actions import clause_executable  # noqa: E402
from league_rules import normalize_rules  # noqa: E402
from mister_client import clause_fields_from_community, normalize_sw_player  # noqa: E402


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _target(**over) -> dict:
    row = {
        "id": "123",
        "name": "Objetivo",
        "position": "DF",
        "clause": 5_000_000,
        "clause_known": True,
        "market_value": 4_000_000,
        "shield": 0,
    }
    row.update(over)
    return row


ALL_ON = {
    "clauses": True,
    "clause_rules": {
        "enabled": True,
        "block": False,
        "signs": 1,
        "gameweek": 1,
        "daily_limit": 0,
        "max_inbound": 2,
    },
}


# ---------------------------------------------------------------------------
# Caso base
# ---------------------------------------------------------------------------

def test_clear_target_is_executable() -> None:
    ok, why = clause_executable(_target(), league_rules=ALL_ON)
    _assert(ok, f"debía ser ejecutable: {why}")
    _assert(why is None, why)


# ---------------------------------------------------------------------------
# Blindaje temporal
# ---------------------------------------------------------------------------

def test_shielded_player_is_blocked() -> None:
    ok, why = clause_executable(_target(shield=48), league_rules=ALL_ON)
    _assert(not ok, "un jugador blindado no se puede clausular")
    _assert("blindado" in (why or ""), why)


def test_shielded_flag_alone_blocks() -> None:
    row = _target()
    row.pop("shield")
    row["shielded"] = True
    ok, why = clause_executable(row, league_rules=ALL_ON)
    _assert(not ok, "el flag shielded debe bastar")


def test_shield_zero_is_not_shielded() -> None:
    ok, _ = clause_executable(_target(shield=0), league_rules=ALL_ON)
    _assert(ok, "shield 0 significa sin blindaje")


def test_shield_survives_normalization() -> None:
    """El campo shield venía en el payload de Mister y se estaba descartando."""
    raw = {
        "id": 15653,
        "name": "Kang-In Lee",
        "position": 3,
        "id_team": 2,
        "value": 5_753_000,
        "clause": 5_753_000,
        "shield": 36,
        "id_uc": 4242,
    }
    norm = normalize_sw_player(raw)
    _assert(norm is not None, "debe normalizar")
    _assert(norm["shield"] == 36, norm.get("shield"))
    _assert(norm["shielded"] is True, norm.get("shielded"))

    free = normalize_sw_player({**raw, "shield": 0})
    _assert(free["shield"] == 0 and free["shielded"] is False, free.get("shield"))

    info = clause_fields_from_community({"clause": {"value": 100}, "shield": 12})
    _assert(info["shield"] == 12 and info["shielded"] is True, info)

    listed = clause_fields_from_community(
        {"clause": {"value": 100}, "market": {"id": 998877}, "id_market": 0}
    )
    _assert(listed["id_market"] == 998877, listed.get("id_market"))


# ---------------------------------------------------------------------------
# Reglas de liga
# ---------------------------------------------------------------------------

def test_clauses_disabled_blocks() -> None:
    ok, why = clause_executable(_target(), league_rules={"clauses": False})
    _assert(not ok, "sin cláusulas en la liga no hay nada que pagar")
    _assert("desactivada" in (why or ""), why)


def test_clauses_block_flag() -> None:
    rules = {**ALL_ON, "clause_rules": {**ALL_ON["clause_rules"], "block": True}}
    ok, why = clause_executable(_target(), league_rules=rules)
    _assert(not ok, "clauses_block debe bloquear")
    _assert("bloquead" in (why or ""), why)


def test_gameweek_rule_closes_in_the_pre_kickoff_window() -> None:
    rules = {**ALL_ON, "clause_rules": {**ALL_ON["clause_rules"], "gameweek": 1}}
    ok_far, _ = clause_executable(_target(), league_rules=rules, hours_to_jornada=12)
    _assert(ok_far, "12 h antes del pitido la cláusula sigue abierta")
    ok_close, why = clause_executable(
        _target(), league_rules=rules, hours_to_jornada=0.5
    )
    _assert(not ok_close, "media hora antes está cerrada")
    _assert("previas" in (why or "") or "jornada" in (why or ""), why)


def test_gameweek_zero_stays_open() -> None:
    rules = {**ALL_ON, "clause_rules": {**ALL_ON["clause_rules"], "gameweek": 0}}
    ok, why = clause_executable(
        _target(), league_rules=rules, hours_to_jornada=0.2, gameweek_live=True
    )
    _assert(ok, f"gameweek 0 = desactivado: {why}")


def test_gameweek_live_without_clock_is_conservative() -> None:
    rules = {**ALL_ON, "clause_rules": {**ALL_ON["clause_rules"], "gameweek": 1}}
    ok, why = clause_executable(_target(), league_rules=rules, gameweek_live=True)
    _assert(not ok, "sin reloj y jornada en juego no se dispara")
    _assert("previas" in (why or "") or "jornada" in (why or ""), why)


def test_recent_signing_protection() -> None:
    rules = {**ALL_ON, "clause_rules": {**ALL_ON["clause_rules"], "signs": 1}}
    ok_old, _ = clause_executable(_target(), league_rules=rules)
    _assert(ok_old, "un jugador de siempre no está protegido")
    ok_new, why = clause_executable(
        _target(owner_signed_recently=True), league_rules=rules
    )
    _assert(not ok_new, "fichaje reciente protegido")
    _assert("reciente" in (why or ""), why)


def test_signs_hours_codes() -> None:
    """1=24h, 2=72h, 3=7d. Un fichaje de 30 h está fuera de 24 y dentro de 72."""
    at_30 = _target(owner_signed_hours=30)
    ok_24, _ = clause_executable(
        at_30,
        league_rules={**ALL_ON, "clause_rules": {**ALL_ON["clause_rules"], "signs": 1}},
    )
    _assert(ok_24, "30 h > 24 h de signs=1")
    ok_72, why = clause_executable(
        at_30,
        league_rules={**ALL_ON, "clause_rules": {**ALL_ON["clause_rules"], "signs": 2}},
    )
    _assert(not ok_72, "30 h < 72 h de signs=2")
    _assert("reciente" in (why or "") or "protección" in (why or ""), why)
    ok_off, _ = clause_executable(
        _target(owner_signed_recently=True),
        league_rules={**ALL_ON, "clause_rules": {**ALL_ON["clause_rules"], "signs": 0}},
    )
    _assert(ok_off, "signs=0 no protege al recién fichado")


def test_max_inbound_clauses_cap() -> None:
    ok_under, _ = clause_executable(_target(), league_rules=ALL_ON, inbound_clauses=1)
    _assert(ok_under, "1 de 2 recibidas: cabe")
    ok_at, why = clause_executable(_target(), league_rules=ALL_ON, inbound_clauses=2)
    _assert(not ok_at, "al tope no cabe")
    _assert("recibidas" in (why or ""), why)


def test_daily_clause_limit() -> None:
    rules = {**ALL_ON, "clause_rules": {**ALL_ON["clause_rules"], "daily_limit": 1}}
    ok_first, _ = clause_executable(_target(), league_rules=rules, clauses_paid_today=0)
    _assert(ok_first, "la primera del día cabe")
    ok_second, why = clause_executable(_target(), league_rules=rules, clauses_paid_today=1)
    _assert(not ok_second, "la segunda no")
    _assert("24" in (why or "") or "tope" in (why or ""), why)


def test_daily_limit_zero_means_unlimited() -> None:
    ok, _ = clause_executable(_target(), league_rules=ALL_ON, clauses_paid_today=9)
    _assert(ok, "daily_limit 0 = sin límite")


# ---------------------------------------------------------------------------
# Datos insuficientes
# ---------------------------------------------------------------------------

def test_unknown_clause_is_blocked() -> None:
    ok, why = clause_executable(
        _target(clause=None, clause_known=False), league_rules=ALL_ON
    )
    _assert(not ok, "sin cláusula visible no se paga a ciegas")
    _assert("visible" in (why or ""), why)


def test_zero_clause_is_blocked() -> None:
    ok, why = clause_executable(_target(clause=0), league_rules=ALL_ON)
    _assert(not ok, "importe 0 es dato roto, no un chollo")


def test_unpublished_rules_default_permissive() -> None:
    """Sin ser admin no vemos las reglas finas; el ejecutor las reverifica en vivo."""
    ok, why = clause_executable(_target(), league_rules={"clauses": True})
    _assert(ok, f"no debe bloquear por reglas desconocidas: {why}")


# ---------------------------------------------------------------------------
# Lectura de reglas desde el payload real de Mister
# ---------------------------------------------------------------------------

def test_build_rules_reads_clause_admin_settings() -> None:
    admin = {
        "community": {
            "provider": "mix",
            "team_limit": 25,
            "type": "lfm",
            "mode": "contest",
            "clauses": 0,
            "clauses_block": 0,
            "clauses_signs": 1,
            "clauses_gameweek": 1,
            "clauses_daily": 1,
            "max_inbound_clauses": 2,
            "sale_limit": 5,
            "transfer_wait": 0,
        }
    }
    rules = normalize_rules({}, admin_data=admin["community"], league_cfg={})
    _assert(rules["clauses"] is False, rules.get("clauses"))
    _assert(rules["clauses_signs"] == 1, rules.get("clauses_signs"))
    _assert(rules["clauses_gameweek"] == 1, rules.get("clauses_gameweek"))
    _assert(rules["clause_rules"]["signs"] == 1, rules["clause_rules"])
    _assert(rules["clauses_block"] is False, rules.get("clauses_block"))
    _assert(rules["clauses_daily"] == 1, rules.get("clauses_daily"))
    _assert(rules["max_inbound_clauses"] == 2, rules.get("max_inbound_clauses"))
    ok, why = clause_executable(_target(), league_rules=rules)
    _assert(not ok, "esa liga tiene las cláusulas apagadas")


def test_build_rules_leaves_unknown_as_none() -> None:
    """Liga donde no somos admin: las reglas finas no llegan y quedan en None."""
    fg = {"provider": "mix", "clauses": 1, "type": "comunio", "mode": "private", "team_limit": 25}
    rules = normalize_rules(fg, admin_data=None, league_cfg={})
    _assert(rules["clauses"] is True, rules.get("clauses"))
    _assert(rules["clauses_signs"] is None, rules.get("clauses_signs"))
    _assert(rules["max_inbound_clauses"] is None, rules.get("max_inbound_clauses"))


TESTS = [
    test_clear_target_is_executable,
    test_shielded_player_is_blocked,
    test_shielded_flag_alone_blocks,
    test_shield_zero_is_not_shielded,
    test_shield_survives_normalization,
    test_clauses_disabled_blocks,
    test_clauses_block_flag,
    test_gameweek_rule_closes_in_the_pre_kickoff_window,
    test_gameweek_zero_stays_open,
    test_gameweek_live_without_clock_is_conservative,
    test_recent_signing_protection,
    test_signs_hours_codes,
    test_max_inbound_clauses_cap,
    test_daily_clause_limit,
    test_daily_limit_zero_means_unlimited,
    test_unknown_clause_is_blocked,
    test_zero_clause_is_blocked,
    test_unpublished_rules_default_permissive,
    test_build_rules_reads_clause_admin_settings,
    test_build_rules_leaves_unknown_as_none,
]


if __name__ == "__main__":
    for fn in TESTS:
        fn()
        print(f"OK  {fn.__name__}")
    print(f"test_clause_eligibility: {len(TESTS)} tests OK")
