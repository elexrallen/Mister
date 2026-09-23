"""
Ejecutor automático de un ciclo de mercado.

Está partido en dos a propósito:

  `plan_operations`  puro. Recibe el `cycle_plan` ya generado, la configuración
                     de la liga y el estado, y devuelve la lista de operaciones
                     que haría más el detalle de lo que descartó y por qué.
                     No toca red, así que toda la casuística se prueba sin
                     tocar la cuenta real.

  `execute`          coge esa lista y la manda con `MisterWriteClient`.

El orden dentro del ciclo no es estético: las ventas y los listados liberan
caja y plazas, las cláusulas se cobran al instante y son irreversibles, las
pujas a libres se resuelven al cierre, y las ofertas a rivales pueden quedarse
vivas indefinidamente, así que van al final con cupo propio y sin consumir el
presupuesto de carencias, porque no garantizan nada.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable

import config
from competitive_actions import (
    CLAUSE_DAILY_WINDOW_HOURS,
    _has_starter_signal,
    clause_executable,
    has_negative_trend,
    hours_since_acquired,
    is_xi_quality_starter,
    mister_bid_cap,
    owner_signed_hours_from_profile,
    resolve_clauses_daily_limit,
    resolve_transfer_wait_hours,
)
from mister_actions import (
    BID_PLACE,
    BID_UPDATE,
    MisterWriteClient,
    UnverifiedAction,
)
from mister_client import (
    fetch_player_sw_profile,
    listing_context_for_player,
    switch_community,
)

log = logging.getLogger("auto_executor")

# Tipos de move que produce cycle_plan
KIND_ACCEPT = "accept_offer"
KIND_DECLINE = "decline_offer"
KIND_LIST = "list_for_sale"
KIND_BID = "bid"
KIND_CLAUSE = "clause_bid"
KIND_HOLD = "hold_offer"

# Tipos que el ejecutor deriva, no vienen del plan
KIND_OFFER = "offer"
KIND_WITHDRAW = "withdraw_offer"
KIND_RESCIND = "sell_to_system"

# Operaciones de /ajax/bid: sin id_market el formulario no se puede armar.
LISTING_KINDS = frozenset({KIND_BID, KIND_OFFER, KIND_WITHDRAW})

# Orden de ejecución. Primero lo que ingresa o libera, al final lo que
# solo compromete.
PHASES = [
    KIND_ACCEPT,
    KIND_DECLINE,
    KIND_WITHDRAW,
    KIND_LIST,
    KIND_RESCIND,
    KIND_CLAUSE,
    KIND_BID,
    KIND_OFFER,
]

# Un POST que no se llegó a mandar no bloquea fichajes ni cuenta como fallo.
NON_SUCCESS_STATUSES = frozenset(
    {"error", "blocked", "blocked_unverified", "skipped", "deferred"}
)

# Método de MisterWriteClient que ejecuta cada tipo
DISPATCH = {
    KIND_ACCEPT: "accept_offer",
    KIND_DECLINE: "decline_offer",
    KIND_LIST: "list_for_sale",
    KIND_RESCIND: "sell_to_system",
    KIND_CLAUSE: "pay_clause",
    KIND_BID: "place_bid",
    KIND_OFFER: "place_bid",
    KIND_WITHDRAW: "withdraw_bid",
}


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _money(v: Any) -> float:
    return max(0.0, _f(v) or 0.0)


def _pid(row: dict[str, Any]) -> str:
    return str(row.get("player_id") or row.get("id") or "")


def _pct(settings: dict[str, Any], key: str, default: float) -> float:
    v = _f(settings.get(key))
    if v is None:
        return default
    return max(0.0, min(1.0, v))


def _int(settings: dict[str, Any], key: str, default: int) -> int:
    try:
        return max(0, int(settings.get(key, default)))
    except (TypeError, ValueError):
        return default


def _is_offer_to_rival(move: dict[str, Any]) -> bool:
    """Puja a un libre u oferta a un rival: son operaciones distintas."""
    if move.get("listed_by_rival"):
        return True
    owner = move.get("owner_id") or move.get("listed_by_owner_id")
    return bool(owner) and str(owner) not in ("", "0")


def _xi_starter_sale_blocked(move: dict[str, Any], pid: str, xi_ids: set[str]) -> bool:
    """
    never_sell_xi_starters solo cubre un titular real.

    El once recomendado a veces mete banquillo: si el plan marca
    is_xi_starter=False, se puede listar. Sin el flag (plan viejo / test)
    se sigue protegiendo a quien está en xi_ids.
    """
    if move.get("xi_impact") == "risk":
        return True
    if "is_xi_starter" in move:
        return bool(move.get("is_xi_starter") or move.get("is_xi_quality_starter"))
    return bool(pid and pid in xi_ids)


class _Budget:
    """
    Contabilidad del ciclo.

    Las compras a libres y las cláusulas consumen caja de verdad. Las ofertas a
    rivales no: el rival puede no aceptar nunca, así que van aparte y solo
    gastan cupo de ofertas pendientes.
    """

    def __init__(
        self,
        *,
        balance: float,
        usable: float,
        settings: dict[str, Any],
        free_slots: int,
    ) -> None:
        self.balance = float(balance)
        self.usable = max(0.0, float(usable))
        self.cash_in = 0.0
        self.spent = 0.0
        self.free_slots = max(0, int(free_slots))
        # Una plaza de salida permite sobresuscribir pujas del once objetivo:
        # los rivales pujan y no ganaremos todos los tickets.
        self.slot_available_this_cycle = self.free_slots > 0
        self.floor = _money(settings.get("min_cash_floor"))
        self.cycle_cap = self.usable * _pct(settings, "max_spend_per_cycle_pct", 0.60)
        self.single_cap = self.usable * _pct(settings, "max_single_buy_pct", 0.40)
        self.clause_cap = self.usable * _pct(settings, "max_clause_pct", 0.30)

    @property
    def cash(self) -> float:
        return self.balance + self.cash_in - self.spent

    def check_buy(
        self,
        cost: float,
        *,
        is_clause: bool,
        oversubscribe_slot: bool = False,
    ) -> str | None:
        cost = _money(cost)
        if self.free_slots <= 0 and not (
            oversubscribe_slot and self.slot_available_this_cycle
        ):
            return "plantilla a cupo, no queda plaza"
        if cost > self.single_cap + 1:
            return (
                f"supera max_single_buy_pct: {cost / 1e6:.1f}M vs "
                f"{self.single_cap / 1e6:.1f}M"
            )
        if is_clause and cost > self.clause_cap + 1:
            return (
                f"supera max_clause_pct: {cost / 1e6:.1f}M vs "
                f"{self.clause_cap / 1e6:.1f}M"
            )
        if self.spent + cost > self.cycle_cap + 1:
            return (
                f"supera max_spend_per_cycle_pct: {(self.spent + cost) / 1e6:.1f}M vs "
                f"{self.cycle_cap / 1e6:.1f}M"
            )
        if self.cash - cost < self.floor - 1:
            return (
                f"dejaría la caja por debajo del suelo "
                f"({(self.cash - cost) / 1e6:.1f}M < {self.floor / 1e6:.1f}M)"
            )
        return None

    def commit_buy(self, cost: float) -> None:
        self.spent += _money(cost)
        if self.free_slots > 0:
            self.free_slots -= 1

    def commit_sale(self, amount: float, *, frees_slot: bool = True) -> None:
        self.cash_in += _money(amount)
        if frees_slot:
            self.free_slots += 1
            self.slot_available_this_cycle = True

    def snapshot(self) -> dict[str, Any]:
        return {
            "balance": round(self.balance, 0),
            "usable": round(self.usable, 0),
            "cash_in": round(self.cash_in, 0),
            "spent": round(self.spent, 0),
            "projected_cash": round(self.cash, 0),
            "cycle_cap": round(self.cycle_cap, 0),
            "single_cap": round(self.single_cap, 0),
            "clause_cap": round(self.clause_cap, 0),
            "min_cash_floor": round(self.floor, 0),
            "free_slots_left": self.free_slots,
        }


def transfer_locked_ids(
    *,
    automation_log: dict[str, Any] | None,
    transfer_wait_hours: float,
    now: datetime | None = None,
) -> dict[str, float]:
    """
    Fichajes propios que aún no se pueden listar, con las horas que les faltan.

    La fuente es el log de ciclos anteriores: las compras las hace este mismo
    ejecutor, así que sabe cuándo entró cada jugador. El payload de Mister no
    trae la fecha de adquisición, y sin este registro el ejecutor listaría un
    fichaje recién hecho y Mister rechazaría el POST.
    """
    wait = max(0.0, float(transfer_wait_hours or 0))
    if wait <= 0 or not isinstance(automation_log, dict):
        return {}
    ref = now or datetime.now(timezone.utc)
    locked: dict[str, float] = {}
    for entry in automation_log.get("cycles") or []:
        ts = _parse_ts(entry.get("at"))
        if ts is None:
            continue
        elapsed = (ref - ts).total_seconds() / 3600.0
        if elapsed >= wait:
            continue
        for op in entry.get("operations") or []:
            if op.get("kind") not in (KIND_BID, KIND_CLAUSE, KIND_OFFER):
                continue
            if str(op.get("status") or "") in NON_SUCCESS_STATUSES:
                continue
            pid = _pid(op)
            if pid:
                locked[pid] = max(locked.get(pid, 0.0), wait - elapsed)
    return locked


def clauses_paid_in_window(
    automation_log: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    hours: float | None = None,
) -> int:
    """Cláusulas propias cobradas en la ventana (por defecto 24 h)."""
    window = float(hours if hours is not None else CLAUSE_DAILY_WINDOW_HOURS)
    if window <= 0 or not isinstance(automation_log, dict):
        return 0
    ref = now or datetime.now(timezone.utc)
    n = 0
    for entry in automation_log.get("cycles") or []:
        ts = _parse_ts(entry.get("at"))
        if ts is None:
            continue
        elapsed = (ref - ts).total_seconds() / 3600.0
        if elapsed < 0 or elapsed >= window:
            continue
        for op in entry.get("operations") or []:
            if op.get("kind") != KIND_CLAUSE:
                continue
            if str(op.get("status") or "") in NON_SUCCESS_STATUSES:
                continue
            n += 1
    return n


def stale_offer_ids(
    *,
    automation_log: dict[str, Any] | None,
    pending_sent: list[dict[str, Any]] | None,
    max_cycles: int,
) -> set[str]:
    """
    Ofertas propias a rivales que llevan `max_cycles` sin respuesta.

    Una oferta viva consume margen de deuda sin aportar nada, y el rival que no
    la ha aceptado en dos ciclos no la va a aceptar.
    """
    if max_cycles <= 0 or not isinstance(automation_log, dict):
        return set()
    alive = {str(o.get("player_id") or o.get("id") or "") for o in pending_sent or []}
    alive.discard("")
    if not alive:
        return set()
    seen: dict[str, int] = {}
    cycles = list(automation_log.get("cycles") or [])[-max(1, max_cycles) * 3 :]
    for idx, entry in enumerate(reversed(cycles)):
        for op in entry.get("operations") or []:
            if op.get("kind") != KIND_OFFER:
                continue
            pid = _pid(op)
            if pid in alive and pid not in seen:
                seen[pid] = idx
    return {pid for pid, age in seen.items() if age >= max_cycles - 1}


def _parse_ts(raw: Any) -> datetime | None:
    if not raw:
        return None
    txt = str(raw).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def plan_operations(
    *,
    cycle_plan: dict[str, Any] | None,
    settings: dict[str, Any],
    state: dict[str, Any] | None = None,
    league_rules: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Decide qué operaciones mandaría este ciclo. Función pura.

    `state` aporta lo que el move no lleva encima: el once titular, las ofertas
    propias vivas y el log de ciclos anteriores, del que sale la espera de 24h
    para listar un fichaje reciente.
    """
    plan = cycle_plan if isinstance(cycle_plan, dict) else {}
    st = state if isinstance(state, dict) else {}
    rules = league_rules if isinstance(league_rules, dict) else {}
    ref_now = now or datetime.now(timezone.utc)

    slug = str(settings.get("slug") or st.get("slug") or "")
    operations: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    def skip(move: dict[str, Any], kind: str, reason: str) -> None:
        skipped.append(
            {
                "kind": kind,
                "player_id": _pid(move),
                "name": move.get("name"),
                "amount": _money(move.get("amount") or move.get("bid")),
                "reason": reason,
            }
        )

    if not settings.get("enabled"):
        why = (
            "kill switch global apagado"
            if settings.get("kill_switch_off")
            else (
                "liga no registrada en config/automation.json"
                if not settings.get("league_registered")
                else "liga registrada pero desactivada"
            )
        )
        return {
            "league": slug,
            "enabled": False,
            "reason": why,
            "operations": [],
            "skipped": [],
            "budget": {},
            "at": ref_now.isoformat(),
        }

    constraints = plan.get("constraints") if isinstance(plan.get("constraints"), dict) else {}
    balance = _f(constraints.get("balance"))
    if balance is None:
        balance = _f(st.get("balance")) or 0.0
    max_debt = constraints.get("max_debt")
    usable = _f(constraints.get("spendable"))
    if usable is None:
        usable = mister_bid_cap(balance, max_debt)

    free_slots = constraints.get("free_slots_after_accepts")
    if free_slots is None:
        free_slots = constraints.get("free_slots")
    # Los topes se fijan al abrir el ciclo y no crecen con lo que se venda
    # dentro: si no, una venta serviría de excusa para el fichaje caro que la
    # reserva por carencias acaba de bloquear.
    budget = _Budget(
        balance=balance,
        usable=usable,
        settings=settings,
        free_slots=int(free_slots or 0),
    )

    allowed = settings.get("allowed_actions")
    allowed_set = {str(a) for a in allowed} if isinstance(allowed, (list, tuple, set)) else set()
    max_ops = _int(settings, "max_ops_per_cycle", 6)
    max_clauses = _int(settings, "max_clauses_per_cycle", 1)
    max_offers = _int(settings, "max_pending_offers", 2)
    protect_xi = bool(settings.get("never_sell_xi_starters", True))
    allow_rescind = bool(settings.get("allow_rescind"))
    require_starters = bool(settings.get("require_xi_starters", True))
    require_trend = bool(settings.get("require_positive_trend", True))

    sale_remaining = int(_f(constraints.get("sale_remaining")) or 0)
    debt_shortfall = _money(constraints.get("debt_shortfall"))
    hours_to_jornada = _f(constraints.get("hours_to_jornada"))
    gameweek_live = bool(st.get("gameweek_live"))

    xi_ids = {str(x) for x in (st.get("xi_ids") or [])}
    automation_log = st.get("automation_log")
    wait_h = resolve_transfer_wait_hours(rules.get("transfer_wait"))
    locked = transfer_locked_ids(
        automation_log=automation_log, transfer_wait_hours=wait_h, now=ref_now
    )
    clause_cfg = rules.get("clause_rules") if isinstance(rules.get("clause_rules"), dict) else {}
    clause_daily_cap = resolve_clauses_daily_limit(
        clause_cfg.get("daily_limit", rules.get("clauses_daily")),
        default=1,
    )
    paid_raw = _f(st.get("clauses_paid_today"))
    clauses_paid_window = (
        int(paid_raw)
        if paid_raw is not None
        else clauses_paid_in_window(
            automation_log, now=ref_now, hours=CLAUSE_DAILY_WINDOW_HOURS
        )
    )
    pending_sent = st.get("offers_sent") or []
    live_offers = len(pending_sent)
    stale = stale_offer_ids(
        automation_log=automation_log,
        pending_sent=pending_sent,
        max_cycles=_int(settings, "offer_stale_cycles", 2),
    )

    clauses_done = 0
    by_phase: dict[str, list[dict[str, Any]]] = {k: [] for k in PHASES}
    for move in plan.get("moves") or []:
        kind = str(move.get("kind") or "")
        if kind == KIND_HOLD:
            continue
        if kind == KIND_BID and _is_offer_to_rival(move):
            kind = KIND_OFFER
        if kind in by_phase:
            by_phase[kind].append(move)

    # Retirar ofertas obsoletas no viene del plan: lo decide el ejecutor
    for pid in sorted(stale):
        row = next((o for o in pending_sent if _pid(o) == pid), {"player_id": pid})
        by_phase[KIND_WITHDRAW].append({**row, "kind": KIND_WITHDRAW})

    # Las pujas caras primero solo si el plan las ordenó así; dentro de cada
    # fase se respeta el orden del motor, que ya aplicó los pesos del ciclo.
    for kind in PHASES:
        for move in by_phase.get(kind) or []:
            pid = _pid(move)
            amount = _money(move.get("amount") or move.get("bid") or move.get("price"))

            if kind not in allowed_set:
                if kind == KIND_CLAUSE:
                    skip(
                        move,
                        kind,
                        "contrato /ajax/clause-pay sin confirmar; no se manda hasta sondearlo",
                    )
                elif kind == KIND_ACCEPT:
                    skip(
                        move,
                        kind,
                        "contrato /ajax/offer sin confirmar; no se manda hasta sondearlo",
                    )
                else:
                    skip(move, kind, f"acción no permitida en allowed_actions ({kind})")
                continue
            if len(operations) >= max_ops:
                skip(move, kind, f"tope de operaciones por ciclo ({max_ops})")
                continue
            if not pid:
                skip(move, kind, "move sin id de jugador")
                continue

            if kind == KIND_ACCEPT:
                bid_id = move.get("id_bid")
                operations.append(
                    _op(
                        move,
                        kind,
                        amount=amount,
                        params={
                            "player_id": pid,
                            "owner_id": move.get("owner_id"),
                            "bid_id": bid_id,
                        },
                    )
                )
                budget.commit_sale(amount)
                continue

            if kind == KIND_DECLINE:
                operations.append(
                    _op(
                        move,
                        kind,
                        amount=amount,
                        params={
                            "player_id": pid,
                            "owner_id": move.get("owner_id"),
                            "bid_id": move.get("id_bid"),
                        },
                    )
                )
                continue

            if kind == KIND_WITHDRAW:
                if live_offers <= 0:
                    skip(move, kind, "no hay oferta viva que retirar")
                    continue
                operations.append(
                    _op(
                        move,
                        kind,
                        amount=0.0,
                        params={
                            "player_id": pid,
                            "id_market": move.get("id_market"),
                            "offeree_id": move.get("owner_id") or 0,
                        },
                    )
                )
                live_offers -= 1
                continue

            if kind == KIND_LIST:
                if sale_remaining <= 0:
                    skip(move, kind, "sale_limit agotado: no queda hueco de listado")
                    continue
                if move.get("on_sale"):
                    skip(move, kind, "ya está listado")
                    continue
                if pid in locked:
                    skip(
                        move,
                        kind,
                        f"espera compra→venta: faltan {locked[pid]:.1f}h de {wait_h:.0f}h",
                    )
                    continue
                if protect_xi and _xi_starter_sale_blocked(move, pid, xi_ids):
                    skip(move, kind, "titular del once y never_sell_xi_starters activo")
                    continue
                price = _money(move.get("price") or move.get("amount"))
                if price <= 0:
                    skip(move, kind, "sin precio de listado")
                    continue
                operations.append(
                    _op(move, kind, amount=price, params={"player_id": pid, "price": price})
                )
                sale_remaining -= 1
                continue

            if kind == KIND_RESCIND:
                if not allow_rescind:
                    skip(move, kind, "allow_rescind desactivado: rescindir regala el 20%")
                    continue
                if debt_shortfall <= 1:
                    skip(move, kind, "sin deuda que cubrir: no se rescinde por gusto")
                    continue
                if protect_xi and _xi_starter_sale_blocked(move, pid, xi_ids):
                    skip(move, kind, "titular del once y never_sell_xi_starters activo")
                    continue
                operations.append(
                    _op(move, kind, amount=amount, params={"player_id": pid})
                )
                budget.commit_sale(amount)
                debt_shortfall -= amount
                continue

            if kind in (KIND_CLAUSE, KIND_BID, KIND_OFFER) and not move.get(
                "cpu_spread_play"
            ):
                if require_trend and has_negative_trend(move):
                    skip(
                        move,
                        kind,
                        "tendencia negativa: no entra en el once objetivo",
                    )
                    continue
                if require_starters:
                    flagged = move.get("is_xi_starter")
                    known_bench = _has_starter_signal(move) and not is_xi_quality_starter(
                        move
                    )
                    if flagged is False or (flagged is None and known_bench):
                        skip(move, kind, "solo se ficha un titular real para el once")
                        continue

            if kind == KIND_CLAUSE:
                if clauses_done >= max_clauses:
                    skip(move, kind, f"tope de cláusulas por ciclo ({max_clauses})")
                    continue
                if (
                    clause_daily_cap
                    and clause_daily_cap > 0
                    and (clauses_paid_window + clauses_done) >= clause_daily_cap
                ):
                    skip(
                        move,
                        kind,
                        f"tope de {clause_daily_cap} cláusula(s) cada "
                        f"{int(CLAUSE_DAILY_WINDOW_HOURS)} h",
                    )
                    continue
                ok, why = clause_executable(
                    move,
                    league_rules=rules,
                    gameweek_live=gameweek_live,
                    hours_to_jornada=hours_to_jornada,
                    inbound_clauses=_f(st.get("inbound_clauses")),
                    clauses_paid_today=clauses_paid_window + clauses_done,
                )
                if not ok:
                    skip(move, kind, f"cláusula no ejercitable: {why}")
                    continue
                if not move.get("owner_id"):
                    skip(move, kind, "sin id del dueño: no se puede pagar la cláusula")
                    continue
                blocked = budget.check_buy(amount, is_clause=True)
                if blocked:
                    skip(move, kind, blocked)
                    continue
                operations.append(
                    _op(
                        move,
                        kind,
                        amount=amount,
                        params={
                            "player_id": pid,
                            "owner_id": move.get("owner_id"),
                            "amount": amount,
                        },
                    )
                )
                budget.commit_buy(amount)
                clauses_done += 1
                continue

            if kind == KIND_BID:
                blocked = budget.check_buy(
                    amount,
                    is_clause=False,
                    oversubscribe_slot=bool(move.get("closes_gw_target")),
                )
                if blocked:
                    skip(move, kind, blocked)
                    continue
                operations.append(
                    _op(
                        move,
                        kind,
                        amount=amount,
                        params={
                            "player_id": pid,
                            "amount": amount,
                            "id_market": move.get("id_market"),
                            "offeree_id": 0,
                        },
                    )
                )
                budget.commit_buy(amount)
                continue

            if kind == KIND_OFFER:
                # No descuenta caja: el rival puede no aceptar nunca. Solo cupo.
                if live_offers >= max_offers:
                    skip(move, kind, f"tope de ofertas vivas ({max_offers})")
                    continue
                if budget.free_slots <= 0:
                    skip(move, kind, "plantilla a cupo, no queda plaza")
                    continue
                if amount > budget.single_cap + 1:
                    skip(
                        move,
                        kind,
                        f"supera max_single_buy_pct: {amount / 1e6:.1f}M vs "
                        f"{budget.single_cap / 1e6:.1f}M",
                    )
                    continue
                operations.append(
                    _op(
                        move,
                        kind,
                        amount=amount,
                        params={
                            "player_id": pid,
                            "amount": amount,
                            "id_market": move.get("id_market"),
                            "offeree_id": move.get("owner_id"),
                        },
                    )
                )
                live_offers += 1
                continue

    for idx, op in enumerate(operations, start=1):
        op["seq"] = idx

    return {
        "league": slug,
        "enabled": True,
        "at": ref_now.isoformat(),
        "dry_run": bool(settings.get("dry_run")),
        "operations": operations,
        "skipped": skipped,
        "budget": budget.snapshot(),
        "context": {
            "transfer_wait_hours": wait_h,
            "transfer_locked": sorted(locked),
            "stale_offers": sorted(stale),
            "sale_remaining_after": sale_remaining,
            "hours_to_jornada": hours_to_jornada,
            "pending_offers": live_offers,
            "gameweek_live": gameweek_live,
            "league_rules": rules,
        },
    }


def _op(
    move: dict[str, Any],
    kind: str,
    *,
    amount: float,
    params: dict[str, Any],
) -> dict[str, Any]:
    return {
        "kind": kind,
        "action": DISPATCH[kind],
        "player_id": _pid(move),
        "name": move.get("name"),
        "position": move.get("position"),
        "amount": round(_money(amount), 0),
        "why": move.get("why"),
        "params": {k: v for k, v in params.items() if v is not None},
    }


def _positive_id(raw: Any) -> int | None:
    if raw in (None, "", 0, "0", False):
        return None
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def community_id_from_payload(payload: dict[str, Any] | None) -> str:
    """id_community de la liga del payload publicado."""
    if not isinstance(payload, dict):
        return ""
    sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
    league = payload.get("league") if isinstance(payload.get("league"), dict) else {}
    for raw in (
        sources.get("id_community"),
        league.get("id_community"),
        league.get("id"),
        payload.get("id_community"),
    ):
        cid = str(raw or "").strip()
        if cid and cid != "0":
            return cid
    return ""


def ensure_league_session(id_community: str) -> bool:
    """Activa la comunidad y renueva x-auth. Sin esto el POST cae en otra liga."""
    cid = str(id_community or "").strip()
    if not cid:
        return False
    fg = switch_community(cid)
    got = str((fg or {}).get("id_community") or "")
    if got != cid:
        log.error("sesión no está en comunidad %s (quedó %s)", cid, got or "vacía")
        return False
    return True


def live_listing_lookup(*, enrich_clause: bool = False) -> Callable[[str], dict[str, Any]]:
    """
    Resuelve id_market como el popup de puja: player-community-info.

    sw/market es un atajo por lote (data_engine); aquí hay 1-6 pujas y el
    preload del formulario es la fuente que Mister usa de verdad.
    Con `enrich_clause` añade horas desde el fichaje (sw/players).
    """
    cache: dict[str, dict[str, Any]] = {}

    def lookup(player_id: str) -> dict[str, Any]:
        pid = str(player_id or "").strip()
        if pid in cache:
            return cache[pid]
        extra = dict(listing_context_for_player(pid) or {})
        if enrich_clause and extra.get("owner_signed_hours") is None:
            extra["owner_signed_hours"] = _signed_hours_from_sw_profile(
                pid, owner_id=str(extra.get("owner_id") or "") or None
            )
        cache[pid] = extra
        return extra

    return lookup


def _hydrate_clause_op(
    op: dict[str, Any],
    lookup: Callable[[str], dict[str, Any]] | None,
) -> str | None:
    """Rellena id_uc del dueño y horas desde el fichaje."""
    params = dict(op.get("params") or {})
    pid = str(params.get("player_id") or op.get("player_id") or "")
    extra: dict[str, Any] = {}
    if lookup and pid:
        try:
            extra = lookup(pid) or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("clause lookup %s falló: %s", pid, exc)
    owner = (
        extra.get("owner_id")
        or extra.get("offeree_id")
        or params.get("owner_id")
        or op.get("owner_id")
    )
    if owner in (None, "", 0, "0"):
        return "sin id_uc del dueño: no se puede pagar la cláusula"
    params["owner_id"] = str(owner)
    if extra.get("shield") is not None:
        op["shield"] = extra.get("shield")
        op["shielded"] = bool(extra.get("shielded") or extra.get("shield"))
    hours = extra.get("owner_signed_hours")
    if hours is None:
        hours = hours_since_acquired(extra.get("transfer_date"))
    if hours is not None:
        op["owner_signed_hours"] = hours
    if extra.get("owner_signed_recently"):
        op["owner_signed_recently"] = True
    op["params"] = params
    log.info(
        "cláusula lista %s id_uc=%s signed_h=%s",
        pid,
        params["owner_id"],
        op.get("owner_signed_hours"),
    )
    return None


def _signed_hours_from_sw_profile(
    player_id: str,
    *,
    owner_id: str | None = None,
) -> float | None:
    """Una ficha sw/players por cláusula: owners[0].date / transfer.date."""
    try:
        raw = fetch_player_sw_profile(player_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("ficha cláusula %s falló: %s", player_id, exc)
        return None
    if not raw:
        return None
    try:
        from rival_finances import parse_player_profile
    except ImportError:
        return hours_since_acquired((raw.get("player") or {}).get("transfer", {}).get("date") if isinstance(raw.get("player"), dict) else None)
    prof = parse_player_profile(raw)
    return owner_signed_hours_from_profile(prof, owner_id=owner_id)


def _clause_blocked_after_hydrate(op: dict[str, Any], decision: dict[str, Any]) -> str | None:
    ctx = decision.get("context") if isinstance(decision.get("context"), dict) else {}
    item = {
        "clause": op.get("amount"),
        "clause_known": True,
        "shield": op.get("shield"),
        "shielded": op.get("shielded"),
        "owner_id": (op.get("params") or {}).get("owner_id") or op.get("owner_id"),
        "owner_signed_hours": op.get("owner_signed_hours"),
        "owner_signed_recently": op.get("owner_signed_recently"),
    }
    ok, why = clause_executable(
        item,
        league_rules=ctx.get("league_rules"),
        gameweek_live=ctx.get("gameweek_live"),
        hours_to_jornada=ctx.get("hours_to_jornada"),
    )
    return None if ok else why


def is_recent_signing_clause_error(exc: BaseException | str) -> bool:
    txt = str(exc).lower()
    return (
        "recién fichad" in txt
        or "recien fichad" in txt
        or "primeras 24 hora" in txt
    )


def _hydrate_listing_op(
    op: dict[str, Any],
    lookup: Callable[[str], dict[str, Any]] | None,
) -> str | None:
    """Rellena id_market/action. Devuelve motivo de error o None si se puede mandar."""
    params = dict(op.get("params") or {})
    pid = str(params.get("player_id") or op.get("player_id") or "")
    extra: dict[str, Any] = {}
    if lookup and pid:
        try:
            extra = lookup(pid) or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("listing lookup %s falló: %s", pid, exc)
    mid = _positive_id(extra.get("id_market")) or _positive_id(params.get("id_market"))
    if not mid:
        return "sin id_market: no se puede armar la puja"
    params["id_market"] = mid
    action = extra.get("action") or params.get("action")
    if action in (BID_PLACE, BID_UPDATE, "remove"):
        params["action"] = action
    if op.get("kind") == KIND_OFFER:
        offeree = extra.get("offeree_id") or extra.get("owner_id") or params.get("offeree_id")
        if offeree not in (None, "", 0, "0"):
            params["offeree_id"] = offeree
    elif op.get("kind") == KIND_BID:
        params["offeree_id"] = 0
    op["params"] = params
    log.info(
        "puja lista %s id_market=%s action=%s offeree=%s",
        pid,
        mid,
        params.get("action") or BID_PLACE,
        params.get("offeree_id", 0),
    )
    return None


def execute(
    decision: dict[str, Any],
    *,
    client: MisterWriteClient | None = None,
    dry_run: bool | None = None,
    listing_lookup: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Manda las operaciones que decidió el núcleo puro.

    Un fallo no corta el ciclo: se registra y se sigue con la siguiente. Media
    tanda ejecutada es peor que ninguna, pero peor aún es que un rechazo de
    Mister deje sin ejecutar la venta que financia el resto.

    `listing_lookup` inyecta el id_market en tests. En producción, con
    transporte real, se resuelve contra Mister después de `switch_community`.
    """
    ops = list(decision.get("operations") or [])
    if not decision.get("enabled") or not ops:
        return decision

    run_dry = decision.get("dry_run") if dry_run is None else dry_run
    cl = client or MisterWriteClient(dry_run=bool(run_dry))

    lookup = listing_lookup
    if lookup is None and not getattr(cl, "_injected", False):
        if any(op.get("kind") in LISTING_KINDS or op.get("kind") == KIND_CLAUSE for op in ops):
            lookup = live_listing_lookup(
                enrich_clause=any(op.get("kind") == KIND_CLAUSE for op in ops)
            )

    for op in ops:
        method = getattr(cl, str(op.get("action") or ""), None)
        if method is None:
            op["status"] = "error"
            op["error"] = f"acción desconocida: {op.get('action')}"
            continue
        if op.get("kind") == KIND_CLAUSE:
            missing = _hydrate_clause_op(op, lookup)
            if missing:
                op["status"] = "deferred"
                op["error"] = missing
                log.info("%s %s aplazada: %s", op.get("kind"), op.get("name"), missing)
                continue
            blocked = _clause_blocked_after_hydrate(op, decision)
            if blocked:
                op["status"] = "deferred"
                op["error"] = blocked
                log.info("%s %s aplazada: %s", op.get("kind"), op.get("name"), blocked)
                continue
        if op.get("kind") in LISTING_KINDS:
            missing = _hydrate_listing_op(op, lookup)
            if missing:
                op["status"] = "deferred"
                op["error"] = missing
                log.info("%s %s aplazada: %s", op.get("kind"), op.get("name"), missing)
                continue
        try:
            result = method(**op["params"])
        except UnverifiedAction as exc:
            op["status"] = "blocked"
            op["error"] = str(exc)
            op["unverified"] = True
            log.info("%s %s bloqueada: %s", op.get("kind"), op.get("name"), exc)
            continue
        except Exception as exc:  # noqa: BLE001
            if op.get("kind") == KIND_CLAUSE and is_recent_signing_clause_error(exc):
                op["status"] = "deferred"
                op["error"] = str(exc)
                log.info("%s %s aplazada: %s", op.get("kind"), op.get("name"), exc)
                continue
            op["status"] = "error"
            op["error"] = str(exc)
            log.warning("%s %s falló: %s", op.get("kind"), op.get("name"), exc)
            continue
        op["status"] = str(result.get("status") or "unknown")
        if result.get("unverified"):
            op["unverified"] = True

    decision["executed"] = sum(1 for o in ops if o.get("status") in ("ok", "dry_run"))
    decision["failed"] = sum(1 for o in ops if o.get("status") == "error")
    decision["deferred"] = sum(1 for o in ops if o.get("status") == "deferred")
    decision["blocked"] = sum(1 for o in ops if o.get("status") == "blocked")
    return decision


# Ciclos que se conservan en el log. Tres al día: dos semanas de rastro, que es
# de sobra para la espera de 24h y para revisar qué pasó sin inflar el repo.
LOG_MAX_CYCLES = 45


def load_log(slug: str) -> dict[str, Any]:
    """Log de automatización de la liga. Ausente o roto = sin historial."""
    path = config.league_automation_log_path(slug)
    if not path.is_file():
        return {"league": slug, "cycles": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("automation_log de %s ilegible, se empieza de cero", slug)
        return {"league": slug, "cycles": []}
    if not isinstance(data, dict) or not isinstance(data.get("cycles"), list):
        return {"league": slug, "cycles": []}
    return data


def log_entry(decision: dict[str, Any]) -> dict[str, Any]:
    """
    Entrada de log de un ciclo.

    Guarda lo ejecutado y lo descartado con su motivo: sin el motivo el log solo
    dice que no pasó nada, que es justo lo que no sirve para depurar.
    """
    return {
        "at": decision.get("at"),
        "dry_run": bool(decision.get("dry_run")),
        "enabled": bool(decision.get("enabled")),
        "reason": decision.get("reason"),
        "budget": decision.get("budget") or {},
        "context": decision.get("context") or {},
        "operations": [
            {
                "kind": op.get("kind"),
                "player_id": op.get("player_id"),
                "name": op.get("name"),
                "position": op.get("position"),
                "amount": op.get("amount"),
                "status": op.get("status") or "planned",
                "error": op.get("error"),
                "unverified": op.get("unverified"),
                "why": op.get("why"),
            }
            for op in decision.get("operations") or []
        ],
        "skipped": list(decision.get("skipped") or []),
        "counts": {
            "planned": len(decision.get("operations") or []),
            "executed": int(decision.get("executed") or 0),
            "failed": int(decision.get("failed") or 0),
            "skipped": len(decision.get("skipped") or []),
        },
    }


def write_log(slug: str, decision: dict[str, Any]) -> dict[str, Any]:
    """Añade el ciclo al log de la liga y lo guarda. El workflow lo commitea."""
    data = load_log(slug)
    cycles = list(data.get("cycles") or [])
    cycles.append(log_entry(decision))
    data["league"] = slug
    data["cycles"] = cycles[-LOG_MAX_CYCLES:]
    data["updated_at"] = decision.get("at") or datetime.now(timezone.utc).isoformat()
    data["last_cycle"] = data["cycles"][-1]

    path = config.league_automation_log_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return data


def offers_outstanding(
    automation_log: dict[str, Any] | None,
    *,
    squad_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Ofertas propias que siguen vivas, reconstruidas desde el log.

    Mister expone las ofertas enviadas en `ajax/sw/market-offers-sent`, pero el
    cliente de lectura no lo consulta, así que la única fuente fiable es lo que
    apuntó el ejecutor. Una oferta deja de estar viva si la retiró o si el
    jugador ya está en plantilla, que es como se ve que el rival aceptó.
    """
    if not isinstance(automation_log, dict):
        return []
    owned = squad_ids or set()
    alive: dict[str, dict[str, Any]] = {}
    for entry in automation_log.get("cycles") or []:
        for op in entry.get("operations") or []:
            pid = _pid(op)
            if not pid or str(op.get("status") or "") in NON_SUCCESS_STATUSES:
                continue
            kind = op.get("kind")
            if kind == KIND_OFFER:
                alive[pid] = {
                    "player_id": pid,
                    "name": op.get("name"),
                    "amount": op.get("amount"),
                    "sent_at": entry.get("at"),
                }
            elif kind == KIND_WITHDRAW:
                alive.pop(pid, None)
    return [row for pid, row in alive.items() if pid not in owned]


def state_from_payload(payload: dict[str, Any], *, slug: str) -> dict[str, Any]:
    """Estado que el `cycle_plan` no lleva encima, sacado del payload publicado."""
    me = payload.get("me") if isinstance(payload.get("me"), dict) else {}
    xi_block = (
        payload.get("recommended_xi")
        if isinstance(payload.get("recommended_xi"), dict)
        else {}
    )
    matchday = payload.get("matchday") if isinstance(payload.get("matchday"), dict) else {}
    squad_ids = {
        str(p.get("id"))
        for p in me.get("squad") or []
        if isinstance(p, dict) and p.get("id") is not None
    }
    automation_log = load_log(slug)
    return {
        "slug": slug,
        "balance": me.get("balance"),
        "xi_ids": [
            str(row.get("player_id"))
            for row in xi_block.get("xi") or []
            if isinstance(row, dict) and row.get("player_id")
        ],
        "squad_ids": sorted(squad_ids),
        "gameweek_live": bool(matchday.get("is_live")),
        "automation_log": automation_log,
        "offers_sent": offers_outstanding(automation_log, squad_ids=squad_ids),
    }


def rules_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    league = payload.get("league") if isinstance(payload.get("league"), dict) else {}
    rules = league.get("rules")
    return rules if isinstance(rules, dict) else {}


def run_cycle(
    *,
    slug: str,
    cycle_plan: dict[str, Any] | None,
    state: dict[str, Any] | None = None,
    league_rules: dict[str, Any] | None = None,
    client: MisterWriteClient | None = None,
    write_log_file: bool = True,
) -> dict[str, Any]:
    """
    Atajo de producción: lee la configuración de la liga, decide, ejecuta y
    registra.

    El log se escribe también cuando la liga no está automatizada: dejar
    constancia de que el ciclo pasó sin actuar es lo que permite distinguir
    «no había nada que hacer» de «el ejecutor no llegó a correr».
    """
    settings = config.automation_for_league(slug)
    state = dict(state or {})
    state.setdefault("slug", slug)
    state.setdefault("automation_log", load_log(slug))

    decision = plan_operations(
        cycle_plan=cycle_plan,
        settings=settings,
        state=state,
        league_rules=league_rules,
    )
    if decision.get("enabled"):
        decision = execute(decision, client=client)
    else:
        log.info("[%s] automatización inactiva: %s", slug, decision.get("reason"))

    if write_log_file:
        try:
            write_log(slug, decision)
        except OSError as exc:
            log.warning("[%s] no se pudo escribir automation_log: %s", slug, exc)
    return decision


def run_league(
    slug: str,
    *,
    dry_run: bool | None = None,
    verified_only: bool = True,
) -> dict[str, Any]:
    """Lee el payload publicado de la liga y ejecuta su ciclo."""
    path = config.league_data_path(slug)
    if not path.is_file():
        return {
            "league": slug,
            "enabled": False,
            "reason": f"sin payload publicado en {path}",
            "operations": [],
            "skipped": [],
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    settings = config.automation_for_league(slug)
    dry = settings.get("dry_run") if dry_run is None else dry_run
    cid = community_id_from_payload(payload)
    session_ok = True
    if cid:
        session_ok = ensure_league_session(cid)
    else:
        session_ok = False
        log.error("[%s] payload sin id_community: no se puede seleccionar la liga", slug)

    if not session_ok and not dry:
        decision = plan_operations(
            cycle_plan=payload.get("cycle_plan"),
            settings=settings,
            state=state_from_payload(payload, slug=slug),
            league_rules=rules_from_payload(payload),
        )
        for op in decision.get("operations") or []:
            op["status"] = "error"
            op["error"] = f"comunidad {cid or '?'} no activa; no se ha enviado nada"
        decision["executed"] = 0
        decision["failed"] = len(decision.get("operations") or [])
        try:
            write_log(slug, decision)
        except OSError as exc:
            log.warning("[%s] no se pudo escribir automation_log: %s", slug, exc)
        return decision

    return run_cycle(
        slug=slug,
        cycle_plan=payload.get("cycle_plan"),
        state=state_from_payload(payload, slug=slug),
        league_rules=rules_from_payload(payload),
        client=MisterWriteClient(dry_run=bool(dry), verified_only=verified_only),
    )


def _summary_line(decision: dict[str, Any]) -> str:
    if not decision.get("enabled"):
        return f"[{decision.get('league')}] inactiva: {decision.get('reason')}"
    ops = decision.get("operations") or []
    kinds: dict[str, int] = {}
    for op in ops:
        kinds[str(op.get("kind"))] = kinds.get(str(op.get("kind")), 0) + 1
    detail = ", ".join(f"{k}×{n}" for k, n in sorted(kinds.items())) or "nada"
    mode = "simulado" if decision.get("dry_run") else "en vivo"
    return (
        f"[{decision.get('league')}] {mode}: {detail}; "
        f"ejecutadas={decision.get('executed', 0)} "
        f"fallidas={decision.get('failed', 0)} "
        f"aplazadas={decision.get('deferred', 0)} "
        f"bloqueadas={decision.get('blocked', 0)} "
        f"descartadas={len(decision.get('skipped') or [])}"
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Ejecutor automático de un ciclo de mercado")
    ap.add_argument("--league", default="all", help="slug de la liga o 'all'")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="fuerza simulación aunque la liga esté en modo real",
    )
    ap.add_argument(
        "--allow-unverified",
        action="store_true",
        help="permite endpoints sin contrato confirmado (offer, resale)",
    )
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    if args.league and args.league != "all":
        slugs = [args.league]
    else:
        slugs = config.automated_league_slugs()
        if not slugs:
            print("Ninguna liga automatizada activa (kill switch o config/automation.json).")
            return 0

    acted: list[str] = []
    for slug in slugs:
        decision = run_league(
            slug,
            dry_run=True if args.dry_run else None,
            verified_only=not args.allow_unverified,
        )
        print(_summary_line(decision))
        for op in decision.get("operations") or []:
            print(f"  {op.get('status'):>10}  {op.get('kind'):14} {op.get('name')} "
                  f"{op.get('amount'):,.0f}")
        for sk in decision.get("skipped") or []:
            print(f"  {'descartada':>10}  {sk.get('kind'):14} {sk.get('name')} "
                  f"— {sk.get('reason')}")
        if decision.get("executed") and not decision.get("dry_run"):
            acted.append(slug)

    # La caja y la plantilla cambian al actuar: el payload publicado ya no vale.
    # El workflow lee esto para regenerar solo las ligas que movieron algo.
    if acted:
        print(f"::notice::Regenerar payload de: {', '.join(acted)}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        try:
            with open(out, "a", encoding="utf-8") as fh:
                fh.write(f"acted={','.join(acted)}\n")
        except OSError as exc:
            log.warning("no se pudo escribir GITHUB_OUTPUT: %s", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
