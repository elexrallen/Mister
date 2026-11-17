"""
Reventa por liga.

Con el modo encendido el ciclo solo compra libres de más de 4M que revalorizan
en positivo. La puja es el valor de mercado de Mister más el 30% de la subida
del día, no la puja recomendada de subasta. Solo entra si la máquina al +5%
del valor sigue dejando más de un 3% sobre ese precio. La plantilla del
momento de activarlo queda congelada.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from competitive_actions import is_rival_market_listing, mister_bid_cap

KIND_ACCEPT = "accept_offer"
KIND_DECLINE = "decline_offer"
KIND_LIST = "list_for_sale"
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
    return {"active": False, "frozen_ids": [], "positions": {}, "watches": {}}


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
    return data


def save_book(slug: str, book: dict[str, Any], root: Path | None = None) -> None:
    path = flip_book_path(slug, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(book, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def quote_bid(player: dict[str, Any]) -> dict[str, Any]:
    ask = _ask(player)
    vm = _vm(player)
    change = daily_change_euros(player)
    premium = _premium(change)
    bid = vm + premium
    on_market = bool(player.get("on_daily_market") or str(player.get("seller") or "") == "market")
    rival = is_rival_market_listing(player)
    rising_today = change is not None and change > 0
    eligible = bool(
        on_market
        and not rival
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


def _sync_book(
    book: dict[str, Any] | None,
    *,
    enabled: bool,
    squad: list[dict[str, Any]],
    now_iso: str,
) -> dict[str, Any]:
    book = deepcopy(book) if isinstance(book, dict) else fresh_book()
    book.setdefault("frozen_ids", [])
    book.setdefault("positions", {})
    book.setdefault("watches", {})
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
        watch = (book.get("watches") or {}).pop(pid, None) or {}
        listed = _listed(player)
        book["positions"][pid] = {
            "player_id": pid,
            "name": player.get("name"),
            "buy_price": watch.get("buy_price"),
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
    return book


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
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Plan de reventa y libro ya actualizado. Con el modo apagado solo salen las ventas abiertas."""
    now_iso = _now_iso(now)
    squad = [p for p in (squad or []) if isinstance(p, dict)]
    book = _sync_book(book, enabled=enabled, squad=squad, now_iso=now_iso)
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

    moves: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    accept_ids: set[str] = set()
    cash_in = 0.0

    for offer in state.get("pending_offers") or []:
        if not isinstance(offer, dict):
            continue
        pid = _pid(offer)
        if not pid or pid in frozen:
            continue
        pos = positions.get(pid)
        if not isinstance(pos, dict) or pos.get("status") == "sold":
            continue
        player = by_id.get(pid) or offer
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
        threshold = buy * (1.0 + float(config.FLIP_MIN_PROFIT))
        extra = {
            "buy_price": round(buy),
            "threshold": round(threshold),
            "from_machine": offer.get("from_machine", True),
            # El POST de aceptar/rechazar va contra la oferta, no contra la
            # ficha del jugador: sin id_bid el ejecutor manda un body incompleto.
            "id_bid": offer.get("id_bid") or offer.get("offer_id"),
            "owner_id": offer.get("owner_id") or offer.get("id_owner"),
        }
        if amount > threshold:
            moves.append(
                _move(
                    player,
                    kind=KIND_ACCEPT,
                    amount=amount,
                    why=(
                        f"Oferta {_fmt_money(amount)} supera el +3% de la compra "
                        f"({_fmt_money(buy)})."
                    ),
                    extra=extra,
                )
            )
            accept_ids.add(pid)
            cash_in += amount
        else:
            moves.append(
                _move(
                    player,
                    kind=KIND_DECLINE,
                    amount=amount,
                    why=(
                        f"Oferta {_fmt_money(amount)} no supera {_fmt_money(threshold)} "
                        f"(compra {_fmt_money(buy)} + 3%). "
                        "Se rechaza y se vuelve a listar."
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

    quotes: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    if active and list_room > 0:
        seen: set[str] = set()
        for row in market or []:
            if not isinstance(row, dict):
                continue
            pid = _pid(row)
            if not pid or pid in seen or pid in by_id or pid in frozen:
                continue
            seen.add(pid)
            quote = quote_bid(row)
            if not quote["eligible"]:
                continue
            edge = quote["vm"] * (1.0 + float(config.FLIP_MACHINE_PREMIUM)) - quote["bid"]
            quotes.append((edge, row, quote))
        quotes.sort(key=lambda item: -item[0])

    balance = _money(me.get("balance"))
    max_debt = _f(me.get("max_debt"))
    spendable = mister_bid_cap(balance + cash_in, max_debt)
    free_slots = max(0, cap - len(by_id) + len(accept_ids))
    spent = 0.0
    taken = 0
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
    for edge, row, quote in quotes:
        pid = _pid(row)
        if taken >= free_slots:
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
        if spent + quote["bid"] > spendable + 1:
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
        spent += quote["bid"]
        taken += 1

    open_n = sum(
        1
        for pos in positions.values()
        if isinstance(pos, dict) and pos.get("status") != "sold"
    )
    headline = "Reventa" if active else "Salida de reventas"
    if active:
        narrative = (
            f"Plantilla congelada ({len(frozen)}). "
            f"{taken} puja{'s' if taken != 1 else ''} por libres de más de 4M con margen para el +3%."
        )
    elif moves:
        narrative = "La reventa está apagada. Se siguen vendiendo los jugadores ya comprados para eso, solo por encima del +3%."
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
            "spendable": round(spendable),
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
            "hold": 0,
        },
        "flip": {
            "enabled": active,
            "frozen_count": len(frozen),
            "open_count": open_n,
            "eligible": len(quotes),
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
        "eligible": 0,
    }
    return base
