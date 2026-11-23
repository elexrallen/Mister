"""
Reventa por liga.

Con el modo encendido el ciclo solo compra libres de más de 4M que revalorizan
en positivo. La puja es el valor de mercado de Mister más el 30% de la subida
del día, no la puja recomendada de subasta. Solo entra si la máquina al +5%
del valor sigue dejando más de un 3% sobre ese precio. La plantilla del
momento de activarlo queda congelada. Un alta posterior solo es reventa si
la máquina ya la tenía vigilada: la cláusula y la puja hecha a mano se
congelan igual y, si están en el mercado, se retiran. Cada compra abierta
y cada puja vigilada se pueden sacar a mano: pasan a la plantilla congelada
y el modo deja de venderlas. Apagar el modo borra el libro: las compras de
esa tanda dejan de ser reventa y, al volver a activarlo, entran en la
plantilla que se congela.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from competitive_actions import is_rival_market_listing, mister_bid_cap, sells_settle_before_deadline

KIND_ACCEPT = "accept_offer"
KIND_DECLINE = "decline_offer"
KIND_RENEW = "renew_offer"
KIND_LIST = "list_for_sale"
KIND_UNLIST = "unlist"
KIND_BID = "bid"


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _money(v: Any) -> float:
    n = _f(v)
    if n is None or n <= 0:
        return 0.0
    return n


def _pid(row: dict[str, Any] | None) -> str:
    if not isinstance(row, dict):
        return ""
    return str(row.get("player_id") or row.get("id") or "").strip()


def _now_iso(now: datetime | None) -> str:
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return ref.isoformat()


def fresh_book() -> dict[str, Any]:
    return {"active": False, "frozen_ids": [], "positions": {}, "watches": {}, "manual_ids": []}


def leagues_root(root: Path | None = None) -> Path:
    return root or config.LEAGUES_DIR


def flip_mode_path(slug: str, root: Path | None = None) -> Path:
    return leagues_root(root) / slug / "flip_mode.json"


def flip_book_path(slug: str, root: Path | None = None) -> Path:
    return leagues_root(root) / slug / "flip_book.json"


def flip_enabled(slug: str, root: Path | None = None) -> bool:
    path = flip_mode_path(slug, root)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(isinstance(data, dict) and data.get("enabled"))


def _flip_flag(raw: Any) -> bool | None:
    """true/false, 1/0, o el toJSON de Actions (`\"true\"` con comillas)."""
    if isinstance(raw, bool):
        return raw
    text = str(raw if raw is not None else "").strip().lower()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    return None


def apply_flip_toggle(league: str, raw: Any, *, root: Path | None = None) -> bool:
    """Escribe flip_mode.json si el disparo trae true o false. Si no, no toca nada."""
    slug = str(league or "").strip()
    if not slug or slug == "all":
        return False
    flag = _flip_flag(raw)
    if flag is None:
        return False
    path = flip_mode_path(slug, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "enabled": flag,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not flag:
        save_book(slug, fresh_book(), root=root)
    return True


def load_book(slug: str, root: Path | None = None) -> dict[str, Any]:
    path = flip_book_path(slug, root)
    if not path.is_file():
        return fresh_book()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fresh_book()
    if not isinstance(data, dict):
        return fresh_book()
    data.setdefault("active", False)
    data.setdefault("frozen_ids", [])
    data.setdefault("positions", {})
    data.setdefault("watches", {})
    data.setdefault("manual_ids", [])
    return data


def save_book(slug: str, book: dict[str, Any], root: Path | None = None) -> None:
    path = flip_book_path(slug, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(book, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _release_ids(raw: Any) -> list[str]:
    """Ids que el disparo pide sacar de la reventa."""
    if isinstance(raw, str):
        parts = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in parts:
        pid = str(item if item is not None else "").strip()
        if not pid or pid in seen or len(pid) > 32:
            continue
        if not pid.replace("_", "").replace("-", "").isalnum():
            continue
        seen.add(pid)
        out.append(pid)
        if len(out) >= 30:
            break
    return out


def release_from_book(book: dict[str, Any] | None, ids: list[str]) -> dict[str, Any]:
    """Quita esas compras y pujas del libro y las deja congeladas."""
    book = deepcopy(book) if isinstance(book, dict) else fresh_book()
    book.setdefault("frozen_ids", [])
    book.setdefault("positions", {})
    book.setdefault("watches", {})
    book.setdefault("manual_ids", [])
    positions = book.get("positions")
    if not isinstance(positions, dict):
        positions = {}
        book["positions"] = positions
    watches = book.get("watches")
    if not isinstance(watches, dict):
        watches = {}
        book["watches"] = watches
    frozen = {str(pid) for pid in book.get("frozen_ids") or []}
    for pid in ids:
        pos = positions.get(pid)
        if isinstance(pos, dict) and pos.get("status") != "sold":
            positions.pop(pid, None)
        elif pid in positions and not isinstance(pos, dict):
            positions.pop(pid, None)
        watches.pop(pid, None)
        _freeze(book, frozen, pid)
        _mark_manual(book, pid)
    return book


def apply_flip_release(league: str, raw: Any, *, root: Path | None = None) -> bool:
    """Saca del libro los ids del disparo. Si no hay ids válidos, no toca nada."""
    slug = str(league or "").strip()
    ids = _release_ids(raw)
    if not slug or slug == "all" or not ids:
        return False
    save_book(slug, release_from_book(load_book(slug, root), ids), root=root)
    return True


def daily_change_euros(player: dict[str, Any]) -> float | None:
    """Diferencia de hoy contra el valor de ayer que manda Mister, en euros."""
    vm = _money(player.get("market_value") or player.get("price"))
    if vm <= 0:
        return None
    prev = player.get("prev_market_value")
    if prev in (None, ""):
        prev = player.get("prev_value")
    prev_f = _f(prev)
    if prev_f is not None:
        return vm - prev_f
    delta = _f(player.get("delta_1d"))
    if delta is None:
        delta = _f(player.get("price_delta_1d"))
    if delta is None or delta <= -0.99:
        return None
    # delta = (hoy − ayer) / ayer
    return vm * delta / (1.0 + delta)


def _ask(player: dict[str, Any]) -> float:
    for key in ("puja_recomendada", "ask_price", "bid", "price"):
        amount = _money(player.get(key))
        if amount > 0:
            return amount
    return 0.0


def _vm(player: dict[str, Any]) -> float:
    return _money(player.get("market_value") or player.get("price"))


def _premium(change: float | None) -> float:
    if change is None or change == 0:
        return 0.0
    share = config.FLIP_UP_SHARE if change > 0 else config.FLIP_DOWN_SHARE
    return abs(change) * float(share)


def _margin_ok(vm: float, bid: float) -> bool:
    ceiling = vm * (1.0 + float(config.FLIP_MACHINE_PREMIUM))
    need = bid * (1.0 + float(config.FLIP_MIN_PROFIT))
    return ceiling - need > 1.0


def _free_daily_listing(player: dict[str, Any] | None) -> bool:
    """Libre del mercado de hoy. Estar en el universo no basta."""
    if not isinstance(player, dict):
        return False
    on_market = bool(player.get("on_daily_market") or str(player.get("seller") or "") == "market")
    return on_market and not is_rival_market_listing(player)


def quote_bid(player: dict[str, Any]) -> dict[str, Any]:
    ask = _ask(player)
    vm = _vm(player)
    change = daily_change_euros(player)
    premium = _premium(change)
    bid = vm + premium
    on_market = _free_daily_listing(player)
    rival = is_rival_market_listing(player)
    rising_today = change is not None and change > 0
    eligible = bool(
        on_market
        and rising_today
        and vm > float(config.FLIP_MIN_ASK)
        and bid > 0
        and _margin_ok(vm, bid)
    )
    return {
        "ask": ask,
        "bid": round(bid),
        "premium": round(premium),
        "daily_change": None if change is None else round(change),
        "vm": vm,
        "eligible": eligible,
        "rival": rival,
    }


def _sale_limit(rules: dict[str, Any] | None) -> int:
    rules = rules or {}
    eco = rules.get("economy") if isinstance(rules.get("economy"), dict) else {}
    raw = eco.get("sale_limit") if isinstance(eco, dict) and eco.get("sale_limit") is not None else rules.get("sale_limit")
    try:
        limit = int(raw if raw is not None else 5)
    except (TypeError, ValueError):
        limit = 5
    return max(1, limit)


def _max_squad(rules: dict[str, Any] | None, fallback: int | None) -> int:
    rules = rules or {}
    raw = rules.get("max_squad") if rules.get("max_squad") is not None else fallback
    try:
        return max(11, int(raw if raw is not None else 25))
    except (TypeError, ValueError):
        return 25


def _listed(player: dict[str, Any]) -> bool:
    return bool(player.get("on_sale") or player.get("listed_for_sale"))


def _paid(row: dict[str, Any] | None) -> float | None:
    """Precio de una puja de la reventa. Sin él, el alta no es suya."""
    if not isinstance(row, dict):
        return None
    buy = _f(row.get("buy_price"))
    if buy is None or buy <= 0:
        return None
    return buy


def _freeze(book: dict[str, Any], frozen: set[str], pid: str) -> None:
    if not pid or pid in frozen:
        return
    ids = book.get("frozen_ids")
    if not isinstance(ids, list):
        ids = []
        book["frozen_ids"] = ids
    ids.append(pid)
    frozen.add(pid)


def _mark_manual(book: dict[str, Any], pid: str) -> None:
    """El alta no la pujó la reventa. Sirve para repetir el retiro si sigue listado."""
    if not pid:
        return
    ids = book.get("manual_ids")
    if not isinstance(ids, list):
        ids = []
        book["manual_ids"] = ids
    if pid not in {str(item) for item in ids}:
        ids.append(pid)


def _sync_book(
    book: dict[str, Any] | None,
    *,
    enabled: bool,
    squad: list[dict[str, Any]],
    now_iso: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    book = deepcopy(book) if isinstance(book, dict) else fresh_book()
    book.setdefault("frozen_ids", [])
    book.setdefault("positions", {})
    book.setdefault("watches", {})
    book.setdefault("manual_ids", [])
    if not enabled:
        return fresh_book(), []
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for player in squad:
        if not isinstance(player, dict):
            continue
        pid = _pid(player)
        if not pid or pid in by_id:
            continue
        by_id[pid] = player
        order.append(pid)
    squad_ids = set(order)
    open_ids = {
        pid
        for pid, pos in (book.get("positions") or {}).items()
        if isinstance(pos, dict) and pos.get("status") != "sold"
    }
    if enabled and not book.get("active"):
        book["frozen_ids"] = [pid for pid in order if pid not in open_ids]
        book["activated_at"] = now_iso
    book["active"] = bool(enabled)
    frozen = {str(pid) for pid in book.get("frozen_ids") or []}
    # Fichajes que hay que retirar: entraron sin puja de la reventa y siguen listados.
    pull_off: list[dict[str, Any]] = []

    for pid, player in by_id.items():
        if pid in open_ids:
            pos = book["positions"][pid]
            listed = _listed(player)
            pos["listed"] = listed
            if pos.get("status") != "sold":
                pos["status"] = "listed" if listed else "owned"
            if player.get("name"):
                pos["name"] = player.get("name")
            continue
        if pid in frozen or not enabled:
            continue
        watch = (book.get("watches") or {}).pop(pid, None)
        paid = _paid(watch if isinstance(watch, dict) else None)
        if paid is None:
            _freeze(book, frozen, pid)
            _mark_manual(book, pid)
            if _listed(player):
                pull_off.append(player)
            continue
        listed = _listed(player)
        book["positions"][pid] = {
            "player_id": pid,
            "name": player.get("name"),
            "buy_price": watch.get("buy_price") if isinstance(watch, dict) else paid,
            "opened_at": now_iso,
            "status": "listed" if listed else "owned",
            "listed": listed,
        }

    for pid, pos in list((book.get("positions") or {}).items()):
        if not isinstance(pos, dict) or pos.get("status") == "sold":
            continue
        if pid not in squad_ids:
            pos["status"] = "sold"
            pos["sold_at"] = now_iso
            continue
        if _paid(pos) is not None:
            continue
        player = by_id.get(pid) or {"player_id": pid, "id": pid, "name": pos.get("name")}
        _freeze(book, frozen, pid)
        _mark_manual(book, pid)
        if pos.get("listed") or _listed(player):
            pull_off.append(player)
        del book["positions"][pid]

    # El libro se guarda antes del POST. Si el retiro falló, el siguiente ciclo lo repite.
    queued = {_pid(player) for player in pull_off}
    for raw in book.get("manual_ids") or []:
        pid = str(raw or "")
        player = by_id.get(pid)
        if player is None or pid in queued or not _listed(player):
            continue
        pull_off.append(player)
    return book, pull_off


def _move(player: dict[str, Any], *, kind: str, amount: float, why: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    row = {
        "kind": kind,
        "player_id": _pid(player),
        "id": _pid(player),
        "name": player.get("name"),
        "position": player.get("position"),
        "team": player.get("team"),
        "price": _vm(player) or amount,
        "amount": round(amount),
        "market_value": _vm(player) or None,
        "why": why,
        "flip_play": True,
        "on_daily_market": bool(player.get("on_daily_market")),
        "id_market": player.get("id_market"),
        "owner_id": player.get("owner_id"),
        "id_bid": player.get("id_bid") or player.get("offer_id"),
    }
    if extra:
        row.update(extra)
    return row


def _fmt_money(amount: float) -> str:
    n = float(amount or 0)
    if n >= 1_000_000:
        txt = f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".")
        return f"{txt}M €"
    return f"{n:,.0f} €"


def _profit_label(rate: float) -> str:
    points = float(rate) * 100.0
    if abs(points - round(points)) < 1e-6:
        return f"{int(round(points))}%"
    txt = f"{points:.1f}".rstrip("0").rstrip(".")
    return f"{txt}%"


def _accept_min_profit(
    *,
    balance: float,
    hours_to_deadline: float | None,
    cycle_hours: float | None,
) -> float:
    """Suelo de la oferta sobre el valor de mercado: +3%, o +1% cerca del pitido.

    El +1% solo entra con saldo negativo y 4 ciclos enteros o menos hasta el
    pitido. Sin reloj se queda el +3%.
    """
    base = float(config.FLIP_MIN_PROFIT)
    if balance >= 0 or hours_to_deadline is None:
        return base
    step = cycle_hours if cycle_hours is not None and cycle_hours > 0 else None
    if step is None:
        step = float(getattr(config, "MARKET_CYCLE_HOURS", 24) or 24)
    if step <= 0:
        return base
    cycles_left = math.floor(float(hours_to_deadline) / step)
    if cycles_left <= int(config.FLIP_LATE_CYCLES):
        return float(config.FLIP_LATE_MIN_PROFIT)
    return base


def _cash_arrival_hours(wait_remaining: float, lag: float | None, cycle: float | None) -> float | None:
    """Horas hasta el cierre de ciclo en que cobra alguien que aún no puede listarse."""
    if lag is None:
        return None
    wait = max(0.0, float(wait_remaining or 0))
    lag_h = max(0.0, float(lag))
    if wait <= 0:
        return lag_h
    step = float(cycle or 0)
    if step <= 0:
        return None
    if lag_h + 1e-9 >= wait:
        return lag_h
    steps = math.ceil((wait - lag_h) / step - 1e-9)
    return lag_h + steps * step


def _sale_lands_before_kickoff(hours: float | None, lag: float | None) -> bool:
    if hours is None or lag is None:
        return False
    return sells_settle_before_deadline(hours_to_deadline=hours, cash_lag_hours=lag)


def _bid_resolves_after_kickoff(hours: float | None, lag: float | None) -> bool:
    """La puja a un libre se cobra al cierre del ciclo, no al enviarla.

    Si ese cierre es posterior al pitido, el importe no sale antes y no puede
    dejar el saldo de esta jornada en negativo.
    """
    if hours is None or lag is None:
        return False
    return float(lag) > float(hours)


def _flip_roster(book: dict[str, Any], squad: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compras abiertas y pujas vigiladas: lo que el modo todavía maneja."""
    by_id = {_pid(player): player for player in squad if _pid(player)}
    rows: list[dict[str, Any]] = []
    for pid, pos in (book.get("positions") or {}).items():
        if not isinstance(pos, dict) or pos.get("status") == "sold":
            continue
        key = str(pid)
        player = by_id.get(key) or {}
        listed = bool(pos.get("listed") or pos.get("status") == "listed" or _listed(player))
        buy = _f(pos.get("buy_price"))
        vm = _vm(player)
        rows.append(
            {
                "player_id": key,
                "name": pos.get("name") or player.get("name") or "",
                "position": player.get("position"),
                "team": player.get("team"),
                "team_id": player.get("team_id"),
                "photo_url": player.get("photo_url"),
                "team_logo_url": player.get("team_logo_url"),
                "buy_price": None if buy is None else round(buy),
                "market_value": round(vm) if vm > 0 else None,
                "status": "listed" if listed else "owned",
            }
        )
    owned = {row["player_id"] for row in rows}
    for pid, watch in (book.get("watches") or {}).items():
        key = str(pid or "")
        if not key or key in owned or key in by_id or not isinstance(watch, dict):
            continue
        buy = _f(watch.get("buy_price"))
        rows.append(
            {
                "player_id": key,
                "name": watch.get("name") or "",
                "position": None,
                "team": None,
                "team_id": None,
                "photo_url": None,
                "team_logo_url": None,
                "buy_price": None if buy is None else round(buy),
                "market_value": None,
                "status": "bid",
            }
        )
    order = {"listed": 0, "owned": 1, "bid": 2}
    rows.sort(key=lambda row: (order.get(str(row.get("status")), 9), str(row.get("name") or "")))
    return rows


def build_flip_cycle(
    *,
    book: dict[str, Any] | None,
    enabled: bool,
    squad: list[dict[str, Any]] | None,
    market: list[dict[str, Any]] | None,
    sales_state: dict[str, Any] | None,
    me: dict[str, Any] | None,
    league_rules: dict[str, Any] | None,
    max_squad: int | None = None,
    locked_ids: set[str] | None = None,
    lock_hours: dict[str, float] | None = None,
    hours_to_solvency_deadline: float | None = None,
    cash_lag_hours: float | None = None,
    cycle_hours: float | None = None,
    transfer_wait_hours: float | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Plan de reventa y libro ya actualizado. Con el modo apagado el libro queda vacío."""
    now_iso = _now_iso(now)
    squad = [p for p in (squad or []) if isinstance(p, dict)]
    book, manual_listed = _sync_book(book, enabled=enabled, squad=squad, now_iso=now_iso)
    rules = league_rules or {}
    state = sales_state if isinstance(sales_state, dict) else {}
    me = me if isinstance(me, dict) else {}
    cap = _max_squad(rules, max_squad)
    sale_limit = _sale_limit(rules)
    by_id = {_pid(p): p for p in squad if _pid(p)}
    listed_ids = {pid for pid, p in by_id.items() if _listed(p)}
    for raw in state.get("listed_ids") or []:
        if raw:
            listed_ids.add(str(raw))
    sale_remaining = max(0, sale_limit - len(listed_ids))
    frozen = {str(pid) for pid in book.get("frozen_ids") or []}
    positions = book.get("positions") or {}
    locked = {str(pid) for pid in (locked_ids or set())}
    active = bool(book.get("active"))
    raw_balance = _f(me.get("balance"))
    balance = 0.0 if raw_balance is None else raw_balance
    min_profit = _accept_min_profit(
        balance=balance,
        hours_to_deadline=_f(hours_to_solvency_deadline),
        cycle_hours=_f(cycle_hours),
    )
    profit_label = _profit_label(min_profit)

    moves: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    accept_ids: set[str] = set()
    cash_in = 0.0

    if active:
        for player in manual_listed:
            row = _move(
                player,
                kind=KIND_UNLIST,
                amount=0,
                why="Fichaje a mano: se retira del mercado. La reventa no lo vende.",
            )
            row["amount"] = None
            row["price"] = None
            moves.append(row)

    seen_offers: set[str] = set()
    inbox: list[dict[str, Any]] = []
    for offer in list(state.get("pending_offers") or []) + list(state.get("offers_received") or []):
        if not isinstance(offer, dict):
            continue
        pid = _pid(offer)
        if not pid or pid in seen_offers or pid in frozen:
            continue
        status = str(offer.get("status") or "pending").strip().lower()
        # La fila sigue en el buzón aunque el último POST la pintara como
        # rechazada. 'accept' es una venta ya hecha y no se toca.
        if status == "accept":
            continue
        if status not in ("pending", "decline"):
            continue
        seen_offers.add(pid)
        inbox.append(offer)

    for offer in inbox:
        pid = _pid(offer)
        status = str(offer.get("status") or "pending").strip().lower()
        pos = positions.get(pid)
        if not isinstance(pos, dict) or pos.get("status") == "sold":
            continue
        squad_player = by_id.get(pid)
        player = squad_player or offer
        amount = _money(offer.get("amount") or offer.get("bid"))
        buy = _f(pos.get("buy_price"))
        if buy is None or buy <= 0:
            skipped.append(
                {
                    "kind": KIND_ACCEPT,
                    "player_id": pid,
                    "name": player.get("name"),
                    "why": "sin precio de compra: no se acepta",
                    "status": "descartada",
                }
            )
            continue
        # El suelo se mide sobre el valor de mercado de este ciclo. Si la ficha
        # no trae valor, vale el de la oferta.
        vm = _vm(squad_player) if isinstance(squad_player, dict) else 0.0
        if vm <= 0:
            vm = _vm(offer)
        extra = {
            "buy_price": round(buy),
            "market_value": round(vm) if vm > 0 else None,
            "from_machine": offer.get("from_machine", True),
            # El check y el botón circular identifican la oferta y el listado,
            # no al dueño. id_uc es quien puja (0 = máquina).
            "id_bid": offer.get("id_bid") or offer.get("offer_id"),
            "id_market": offer.get("id_market"),
            "id_uc": offer.get("id_uc"),
        }
        if vm <= 0:
            if status != "pending":
                continue
            moves.append(
                _move(
                    player,
                    kind=KIND_RENEW,
                    amount=amount,
                    why="Sin valor de mercado: no se acepta. Se renueva el listado.",
                    extra=extra,
                )
            )
            continue
        threshold = round(vm * (1.0 + min_profit))
        extra["threshold"] = threshold
        if amount >= threshold:
            moves.append(
                _move(
                    player,
                    kind=KIND_ACCEPT,
                    amount=amount,
                    why=(
                        f"Oferta {_fmt_money(amount)} llega al +{profit_label} del valor de mercado "
                        f"({_fmt_money(vm)})."
                    ),
                    extra=extra,
                )
            )
            accept_ids.add(pid)
            cash_in += amount
        elif status != "pending":
            continue
        else:
            moves.append(
                _move(
                    player,
                    kind=KIND_RENEW,
                    amount=amount,
                    why=(
                        f"Oferta {_fmt_money(amount)} no llega a {_fmt_money(threshold)} "
                        f"(valor de mercado {_fmt_money(vm)} +{profit_label}). "
                        "Se renueva el listado."
                    ),
                    extra=extra,
                )
            )

    list_room = sale_remaining
    for pid, pos in positions.items():
        if not isinstance(pos, dict) or pos.get("status") in ("sold", "listed"):
            continue
        if pid in frozen or pid in accept_ids or pos.get("listed"):
            continue
        player = by_id.get(pid)
        if player is None:
            continue
        if _listed(player):
            continue
        if pid in locked:
            skipped.append(
                {
                    "kind": KIND_LIST,
                    "player_id": pid,
                    "name": player.get("name"),
                    "why": "espera compra→venta",
                    "status": "descartada",
                }
            )
            continue
        if list_room <= 0:
            skipped.append(
                {
                    "kind": KIND_LIST,
                    "player_id": pid,
                    "name": player.get("name"),
                    "why": "sale_limit agotado",
                    "status": "descartada",
                }
            )
            continue
        price = _vm(player)
        buy = _f(pos.get("buy_price"))
        moves.append(
            _move(
                player,
                kind=KIND_LIST,
                amount=price,
                why=f"Listar a {_fmt_money(price)} para que la máquina oferte en el siguiente ciclo.",
                extra={"buy_price": None if buy is None else round(buy), "list_reason": "flip"},
            )
        )
        list_room -= 1

    max_debt = _f(me.get("max_debt"))
    hours_deadline = _f(hours_to_solvency_deadline)
    lag = _f(cash_lag_hours)
    cycle_h = _f(cycle_hours)
    wait_new = max(0.0, float(transfer_wait_hours or 0))
    lock_left = {str(pid): float(hours) for pid, hours in (lock_hours or {}).items()}

    listed_now = {
        pid
        for pid, pos in positions.items()
        if isinstance(pos, dict)
        and (
            pos.get("status") == "listed"
            or pos.get("listed")
            or _listed(by_id.get(pid) or {})
        )
    }
    list_ids = {str(m.get("player_id") or "") for m in moves if m.get("kind") == KIND_LIST}
    timely: dict[str, float] = {}
    for pid, pos in positions.items():
        if not isinstance(pos, dict) or pos.get("status") == "sold":
            continue
        if pid in frozen or pid in accept_ids:
            continue
        player = by_id.get(pid)
        if player is None:
            continue
        vm = _vm(player)
        if vm <= 0:
            continue
        if pid in locked:
            rem = lock_left.get(pid, wait_new)
            arrival = _cash_arrival_hours(rem, lag, cycle_h)
        elif pid in list_ids or pid in listed_now:
            arrival = lag
        else:
            continue
        if _sale_lands_before_kickoff(hours_deadline, arrival):
            timely[pid] = vm

    ko_cash = balance + cash_in + sum(timely.values())
    # Sin pitido no hay llegada que cubrir: el suelo corto solo baja dentro de la ventana.
    if hours_deadline is not None and ko_cash < -1:
        renews = [m for m in moves if m.get("kind") == KIND_RENEW]
        renews.sort(key=lambda m: -_money(m.get("amount")))
        for offer_move in renews:
            if ko_cash >= -1:
                break
            pid = str(offer_move.get("player_id") or "")
            amount = _money(offer_move.get("amount"))
            if amount <= 0 or not pid:
                continue
            counted = timely.get(pid, 0.0)
            if amount - counted <= 0:
                continue
            if pid in timely:
                ko_cash -= timely.pop(pid)
                moves[:] = [
                    row
                    for row in moves
                    if not (row.get("kind") == KIND_LIST and str(row.get("player_id") or "") == pid)
                ]
            ko_cash += amount
            cash_in += amount
            accept_ids.add(pid)
            offer_move["kind"] = KIND_ACCEPT
            offer_move["why"] = (
                f"Oferta {_fmt_money(amount)} se acepta para no llegar en negativo al pitido."
            )

    max_open = int(config.FLIP_MAX_OPEN)
    by_market: dict[str, dict[str, Any]] = {}
    for row in market or []:
        if not isinstance(row, dict):
            continue
        pid = _pid(row)
        if not pid:
            continue
        # El universo y los libres llegan juntos. Si hay dos fichas, manda la de hoy.
        prev = by_market.get(pid)
        if prev is None or (_free_daily_listing(row) and not _free_daily_listing(prev)):
            by_market[pid] = row
    pending_sale = {
        pid
        for pid, pos in positions.items()
        if isinstance(pos, dict) and pos.get("status") != "sold" and pid not in accept_ids
    }
    watches = book.get("watches")
    if not isinstance(watches, dict):
        watches = {}
        book["watches"] = watches
    for pid in list(watches):
        key = str(pid or "")
        # Estar en el universo no es estar en la subasta. La plaza se suelta.
        if (
            key
            and key not in by_id
            and key not in pending_sale
            and _free_daily_listing(by_market.get(key))
        ):
            continue
        watches.pop(pid, None)
    live_watches = {
        str(pid)
        for pid in watches
        if pid and str(pid) not in by_id and str(pid) not in pending_sale
    }
    bid_room = max(0, max_open - len(pending_sale) - len(live_watches))
    cupo_why = f"cupo de reventa lleno ({max_open}): no se puja hasta vender uno"

    # La vigilancia no prueba que Mister siga enseñando la puja. Si el libre
    # sigue saliendo a cuenta, se vuelve a mandar; la plaza ya está contada.
    renewals: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    quotes: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    if active:
        seen: set[str] = set()
        for row in market or []:
            if not isinstance(row, dict):
                continue
            pid = _pid(row)
            if not pid or pid in seen or pid in by_id or pid in frozen:
                continue
            seen.add(pid)
            renewing = pid in live_watches
            if not renewing and (list_room <= 0 or bid_room <= 0):
                continue
            quote = quote_bid(row)
            if not quote["eligible"]:
                continue
            edge = quote["vm"] * (1.0 + float(config.FLIP_MACHINE_PREMIUM)) - quote["bid"]
            (renewals if renewing else quotes).append((edge, row, quote))
        renewals.sort(key=lambda item: -item[0])
        quotes.sort(key=lambda item: -item[0])

    debt_cap = mister_bid_cap(balance + cash_in, max_debt)
    free_slots = max(0, cap - len(by_id) + len(accept_ids))
    room = ko_cash
    spent = 0.0
    taken = 0
    ko_why = "dejaría el saldo en negativo al inicio de la jornada"
    if active and list_room <= 0:
        skipped.append(
            {
                "kind": KIND_BID,
                "player_id": "",
                "name": "",
                "why": "sale_limit agotado: no se abren pujas nuevas",
                "status": "descartada",
            }
        )
    elif active and bid_room <= 0:
        skipped.append(
            {
                "kind": KIND_BID,
                "player_id": "",
                "name": "",
                "why": cupo_why,
                "status": "descartada",
            }
        )
    for renewing, _edge, row, quote in (
        [(True, *item) for item in renewals] + [(False, *item) for item in quotes]
    ):
        pid = _pid(row)
        if not renewing and taken >= bid_room:
            skipped.append(
                {
                    "kind": KIND_BID,
                    "player_id": pid,
                    "name": row.get("name"),
                    "amount": quote["bid"],
                    "why": cupo_why,
                    "status": "descartada",
                }
            )
            continue
        if not renewing and taken >= free_slots:
            skipped.append(
                {
                    "kind": KIND_BID,
                    "player_id": pid,
                    "name": row.get("name"),
                    "amount": quote["bid"],
                    "why": "sin plaza",
                    "status": "descartada",
                }
            )
            continue
        # El cierre posterior al pitido no descuenta la puja de esta jornada.
        settles_later = _bid_resolves_after_kickoff(hours_deadline, lag)
        vm_back = 0.0
        if not settles_later:
            if wait_new <= 0:
                if list_room > 0 and _sale_lands_before_kickoff(hours_deadline, lag):
                    vm_back = quote["vm"]
            else:
                arrival = _cash_arrival_hours(wait_new, lag, cycle_h)
                if _sale_lands_before_kickoff(hours_deadline, arrival):
                    vm_back = quote["vm"]
            if room - quote["bid"] + vm_back < -1:
                skipped.append(
                    {
                        "kind": KIND_BID,
                        "player_id": pid,
                        "name": row.get("name"),
                        "amount": quote["bid"],
                        "why": ko_why,
                        "status": "descartada",
                    }
                )
                continue
        if spent + quote["bid"] > debt_cap + 1:
            skipped.append(
                {
                    "kind": KIND_BID,
                    "player_id": pid,
                    "name": row.get("name"),
                    "amount": quote["bid"],
                    "why": "sin caja",
                    "status": "descartada",
                }
            )
            continue
        change = quote["daily_change"]
        if change is None:
            premium_txt = "sin dato del día, a la salida"
        elif change > 0:
            premium_txt = f"sube {_fmt_money(change)} hoy, +30% ({_fmt_money(quote['premium'])})"
        elif change < 0:
            premium_txt = f"baja {_fmt_money(abs(change))} hoy, +5% ({_fmt_money(quote['premium'])})"
        else:
            premium_txt = "revalorización plana, a la salida"
        moves.append(
            _move(
                row,
                kind=KIND_BID,
                amount=quote["bid"],
                why=(
                    f"Puja {_fmt_money(quote['bid'])} ({premium_txt}). "
                    f"La máquina puede llegar a {_fmt_money(quote['vm'] * (1 + config.FLIP_MACHINE_PREMIUM))}."
                    + (
                        " Se cobra al cierre de este ciclo, después del pitido."
                        if settles_later
                        else ""
                    )
                ),
                extra={
                    "bid": quote["bid"],
                    "buy_price": quote["bid"],
                    "premium": quote["premium"],
                    "daily_change": change,
                    "rising": True,
                    "positive_trend": True,
                    "threshold": round(quote["bid"] * (1.0 + float(config.FLIP_MIN_PROFIT))),
                },
            )
        )
        book.setdefault("watches", {})[pid] = {
            "player_id": pid,
            "name": row.get("name"),
            "buy_price": quote["bid"],
            "bid_at": now_iso,
        }
        if vm_back > 0 and wait_new <= 0:
            list_room -= 1
        if not settles_later:
            room = room - quote["bid"] + vm_back
        spent += quote["bid"]
        if not renewing:
            taken += 1

    open_n = sum(
        1
        for pos in positions.values()
        if isinstance(pos, dict) and pos.get("status") != "sold"
    )
    headline = "Reventa" if active else "Salida de reventas"
    if active:
        bid_n = sum(1 for row in moves if row.get("kind") == KIND_BID)
        narrative = (
            f"Plantilla congelada ({len(frozen)}). "
            f"{bid_n} puja{'s' if bid_n != 1 else ''} por libres de más de 4M con margen para el +3%."
        )
    else:
        narrative = "Reventa apagada."

    plan = {
        "headline": headline,
        "narrative": narrative,
        "cycle": {},
        "moves": moves,
        "constraints": {
            "squad_size": len(by_id),
            "max_squad": cap,
            "free_slots": max(0, cap - len(by_id)),
            "free_slots_after_accepts": free_slots,
            "sale_limit": sale_limit,
            "listed_count": len(listed_ids),
            "sale_remaining": sale_remaining,
            "balance": balance,
            "max_debt": None if max_debt is None else round(max_debt),
            "spendable": round(max(spent, min(debt_cap, max(0.0, ko_cash)))),
            "hours_to_solvency_deadline": hours_deadline,
            "holds_count": 0,
        },
        "squad_value": {},
        "next_cycle_targets": [],
        "counts": {
            "accept": sum(1 for m in moves if m["kind"] == KIND_ACCEPT),
            "list": sum(1 for m in moves if m["kind"] == KIND_LIST),
            "bid": sum(1 for m in moves if m["kind"] == KIND_BID),
            "clause": 0,
            "decline": sum(1 for m in moves if m["kind"] == KIND_DECLINE),
            "renew": sum(1 for m in moves if m["kind"] == KIND_RENEW),
            "hold": 0,
        },
        "flip": {
            "enabled": active,
            "frozen_count": len(frozen),
            "open_count": open_n,
            "max_open": max_open,
            "eligible": len(quotes),
            "players": _flip_roster(book, squad),
        },
        "flip_skipped": skipped,
    }
    return plan, book


def merge_flip_exit(normal: dict[str, Any] | None, flip_plan: dict[str, Any], book: dict[str, Any]) -> dict[str, Any]:
    """Con la reventa apagada, el ciclo normal vuelve y solo se conservan las salidas abiertas."""
    base = dict(normal or {})
    open_ids = {
        pid
        for pid, pos in (book.get("positions") or {}).items()
        if isinstance(pos, dict) and pos.get("status") != "sold"
    }
    moves = [
        m
        for m in (base.get("moves") or [])
        if str(m.get("player_id") or "") not in open_ids
    ]
    moves.extend(flip_plan.get("moves") or [])
    base["moves"] = moves
    base["flip"] = {
        "enabled": False,
        "frozen_count": len(book.get("frozen_ids") or []),
        "open_count": len(open_ids),
        "max_open": int(config.FLIP_MAX_OPEN),
        "eligible": 0,
        "players": list((flip_plan.get("flip") or {}).get("players") or []),
    }
    return base
