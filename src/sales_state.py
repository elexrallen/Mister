"""Estado de ventas propias: listados en mercado + ofertas recibidas (máquina/rivales)."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("sales_state")

MISTER_OFFERS_RECEIVED_URL = (
    "https://mister.mundodeportivo.com/market#market/offers-received"
)


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _pos(code: Any) -> str | None:
    try:
        n = int(code)
    except (TypeError, ValueError):
        return None
    return {1: "GK", 2: "DF", 3: "MF", 4: "FW"}.get(n)


def tag_own_market_listings(
    market: list[dict[str, Any]],
    my_uc: str | None,
) -> list[dict[str, Any]]:
    """Marca listados propios (owner_id == id_uc) frente a rivales."""
    uid = str(my_uc or "").strip()
    if not uid:
        for row in market:
            row.setdefault("listed_by_me", False)
            if row.get("owner_id") and str(row.get("owner_id")) not in ("", "0"):
                row["listed_by_rival"] = True
        return market
    for row in market:
        oid = str(row.get("owner_id") or "").strip()
        if oid and oid not in ("", "0") and oid == uid:
            row["listed_by_me"] = True
            row["listed_by_rival"] = False
            row["on_sale"] = True
            row["seller"] = "owned"
            # No es oportunidad de compra
            row["on_daily_market"] = False
        else:
            row.setdefault("listed_by_me", False)
            if oid and oid not in ("", "0"):
                row["listed_by_rival"] = True
    return market


def parse_offers_received(raw: Any) -> dict[str, Any]:
    """
    Normaliza POST /ajax/sw/offers-received.

    Vacío: {"count": {"total": 0, "pending": 0}}
    Con datos: offers = {player_id: {... bid_status, bid, uname Mister ...}}
    """
    empty = {
        "count": {"total": 0, "pending": 0},
        "offers": [],
        "pending_offers": [],
    }
    if not isinstance(raw, dict):
        return empty
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    if not isinstance(data, dict):
        return empty

    count_raw = data.get("count") if isinstance(data.get("count"), dict) else {}
    try:
        total = int(count_raw.get("total") or 0)
    except (TypeError, ValueError):
        total = 0
    try:
        pending_n = int(count_raw.get("pending") or 0)
    except (TypeError, ValueError):
        pending_n = 0

    offers_raw = data.get("offers")
    items: list[dict[str, Any]] = []
    if isinstance(offers_raw, dict):
        for key, row in offers_raw.items():
            if not isinstance(row, dict):
                continue
            items.append(_normalize_offer(row, fallback_id=str(key)))
    elif isinstance(offers_raw, list):
        for row in offers_raw:
            if isinstance(row, dict):
                items.append(_normalize_offer(row))

    pending = [o for o in items if o.get("status") == "pending"]
    if not total:
        total = len(items)
    if not pending_n:
        pending_n = len(pending)

    return {
        "count": {"total": total, "pending": pending_n},
        "offers": items,
        "pending_offers": pending,
    }


def _normalize_offer(row: dict[str, Any], *, fallback_id: str | None = None) -> dict[str, Any]:
    pid = str(row.get("id") or fallback_id or "")
    bid = _f(row.get("bid")) or 0.0
    value = _f(row.get("value")) or _f(row.get("price")) or 0.0
    list_price = _f(row.get("price")) or value
    status = str(row.get("bid_status") or row.get("status") or "pending").strip().lower()
    if status not in ("pending", "accept", "decline"):
        status = "pending"
    id_user = row.get("id_user")
    try:
        id_user_i = int(id_user) if id_user is not None else None
    except (TypeError, ValueError):
        id_user_i = None
    uname = str(row.get("uname") or "").strip()
    from_machine = id_user_i in (0, None) or uname.lower() in ("mister", "máquina", "maquina", "cpu")
    pct = (bid / value) if value > 0 else None
    return {
        "offer_id": str(row.get("id_bid") or row.get("offer_id") or ""),
        "player_id": pid,
        "name": str(row.get("name") or "").strip() or f"Jugador {pid}",
        "position": _pos(row.get("position")),
        "amount": bid,
        "market_value": value,
        "list_price": list_price,
        "status": status,
        "from_machine": from_machine,
        "from_name": uname or ("Mister" if from_machine else "Rival"),
        "id_market": row.get("id_market"),
        "id_bid": row.get("id_bid"),
        "pct_of_vm": round(pct, 4) if pct is not None else None,
        "date": row.get("date"),
        "owner_id": str(row.get("owner") or "") or None,
        "team_id": str(row.get("id_team") or "") or None,
        "photo_url": row.get("photoUrl") or row.get("photo_url"),
    }


def listed_from_market(market: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in market:
        if not row.get("listed_by_me") and not row.get("on_sale"):
            continue
        pid = str(row.get("id") or row.get("player_id") or "")
        if not pid:
            continue
        out.append(
            {
                "player_id": pid,
                "name": row.get("name"),
                "position": row.get("position"),
                "price": _f(row.get("price") or row.get("market_value")) or 0.0,
                "market_value": _f(row.get("market_value") or row.get("price")) or 0.0,
                "team": row.get("team"),
                "team_id": row.get("team_id"),
                "source": "market",
            }
        )
    return out


def build_sales_state(
    *,
    market: list[dict[str, Any]] | None = None,
    offers_payload: dict[str, Any] | None = None,
    squad: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Combina listados propios + ofertas recibidas para el plan diario."""
    parsed = offers_payload if isinstance(offers_payload, dict) else parse_offers_received({})
    listed = listed_from_market(market or [])
    listed_by_id = {str(x["player_id"]): x for x in listed}

    # Jugadores con oferta pendiente están sí o sí en venta
    for offer in parsed.get("offers") or []:
        pid = str(offer.get("player_id") or "")
        if not pid:
            continue
        if pid not in listed_by_id:
            listed_by_id[pid] = {
                "player_id": pid,
                "name": offer.get("name"),
                "position": offer.get("position"),
                "price": float(offer.get("list_price") or offer.get("market_value") or 0),
                "market_value": float(offer.get("market_value") or 0),
                "team": None,
                "team_id": offer.get("team_id"),
                "source": "offers_received",
                "has_pending_offer": offer.get("status") == "pending",
            }
        else:
            listed_by_id[pid]["has_pending_offer"] = offer.get("status") == "pending"
            listed_by_id[pid]["pending_amount"] = offer.get("amount")

    # Marcar on_sale en plantilla
    listed_ids = set(listed_by_id.keys())
    for p in squad or []:
        pid = str(p.get("id") or p.get("player_id") or "")
        if pid in listed_ids:
            p["on_sale"] = True
            p["listed_for_sale"] = True

    listed_list = list(listed_by_id.values())
    pending = list(parsed.get("pending_offers") or [])
    return {
        "listed": listed_list,
        "listed_ids": sorted(listed_ids),
        "listed_count": len(listed_list),
        "offers_received": list(parsed.get("offers") or []),
        "pending_offers": pending,
        "pending_count": len(pending),
        "count": parsed.get("count") or {"total": 0, "pending": 0},
        "mister_offers_url": MISTER_OFFERS_RECEIVED_URL,
    }
