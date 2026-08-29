"""
Estimación de caja y puja máxima de rivales.

Mister no publica saldos ajenos (`show_balances=0`).

Flujo sostenible:
1. Bootstrap: barrer fichas de TODOS los jugadores (plantilla + libres).
2. Snapshot en cache/rival_finances/{id_community}.json.
3. Refresh diario: solo el feed (~30 días) y aplicar deltas.

Puja máxima confirmada en Liga del patio:
    bid_cap = saldo + squad_value / max_debt_level
con max_debt_level=4 → crédito del 25% del VM de plantilla.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("rival_finances")

MISTER_UC = "0"
DEFAULT_STARTING_BUDGET = 50_000_000
DEFAULT_DEBT_LEVEL = 4.0
DEFAULT_SORTEO = date(2026, 7, 24)
SNAPSHOT_VERSION = 1
# El feed de Mister cubre ~30 días; con margen, si el snapshot es más viejo hay que re-barrer fichas.
FEED_MAX_AGE_DAYS = 25
RECENT_MOVES_CAP = 40

_MONTHS_ES = {
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}


def parse_es_date(raw: Any) -> date | None:
    """'24 jul 2026' / '29 ago 2026' → date. Fail-soft."""
    if raw in (None, False, True, "", 0):
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    text = str(raw).strip().lower()
    m = re.match(r"(\d{1,2})\s+([a-záéíóú]{3,5})\s+(\d{4})", text)
    if not m:
        m2 = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
        if m2:
            try:
                return date(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
            except ValueError:
                return None
        return None
    day_s, mon_s, year_s = m.group(1), m.group(2), m.group(3)
    mon = _MONTHS_ES.get(mon_s) or _MONTHS_ES.get(mon_s[:3])
    if not mon:
        return None
    try:
        return date(int(year_s), mon, int(day_s))
    except ValueError:
        return None


def parse_mister_money(raw: Any) -> int:
    """'1.650.000', '+2.500.000', '6,17M', '176.750' → euros int."""
    if raw is None or raw is False:
        return 0
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(round(float(raw)))
    text = re.sub(r"<[^>]+>", " ", str(raw))
    text = text.replace("\xa0", " ").strip()
    sign = -1 if re.search(r"[-−↓]", text) else 1
    compact = re.sub(r"\s+", "", text)
    m_mil = re.search(r"([\d]+(?:[.,]\d+)?)\s*[mM]\b", compact)
    if m_mil:
        num = float(m_mil.group(1).replace(",", "."))
        return int(round(sign * num * 1_000_000))
    digits = re.sub(r"[^\d]", "", compact)
    return sign * int(digits) if digits else 0


def is_machine_uc(uc: Any) -> bool:
    return str(uc or "").strip() in ("", "0", "None", "false", "False")


def debt_credit_fraction(max_debt_level: Any = None) -> float:
    """max_debt de normas (4) → 0.25. No confundir con maxDebt en euros."""
    try:
        lvl = float(max_debt_level)
    except (TypeError, ValueError):
        return 1.0 / DEFAULT_DEBT_LEVEL
    if 1.0 <= lvl <= 20.0:
        return 1.0 / lvl
    return 1.0 / DEFAULT_DEBT_LEVEL


def rival_bid_cap(
    balance: float,
    squad_value: float,
    max_debt_level: Any = None,
) -> float:
    """Techo de puja Mister: saldo + fracción del VM de plantilla."""
    frac = debt_credit_fraction(max_debt_level)
    return float(balance or 0) + float(squad_value or 0) * frac


def vm_at_date(points: list[dict[str, Any]], target: date | None) -> int | None:
    """VM en `target` o el punto más cercano (empate → el anterior o igual)."""
    if not points or target is None:
        return None
    dated: list[tuple[date, int]] = []
    for p in points:
        d = p.get("date")
        if not isinstance(d, date):
            d = parse_es_date(d)
        if d is None:
            continue
        try:
            val = int(p.get("value") or 0)
        except (TypeError, ValueError):
            continue
        dated.append((d, val))
    if not dated:
        return None
    dated.sort(key=lambda x: x[0])
    best = dated[0]
    for d, val in dated:
        if d <= target:
            best = (d, val)
        elif d > target:
            # si el primer punto es posterior, úsalo
            if best[0] > target:
                best = (d, val)
            break
    return best[1]


def parse_player_profile(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normaliza POST /ajax/sw/players (post=players&id=…)."""
    if not isinstance(raw, dict):
        return None
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    player = data.get("player") if isinstance(data.get("player"), dict) else {}
    pid = str(player.get("id") or data.get("id") or "").strip()
    if not pid:
        return None
    owner = player.get("owner") if isinstance(player.get("owner"), dict) else {}
    transfer = player.get("transfer") if isinstance(player.get("transfer"), dict) else {}
    origin = transfer.get("origin")
    price_raw = transfer.get("price")
    try:
        t_price = int(price_raw) if price_raw not in (None, False, "") else 0
    except (TypeError, ValueError):
        t_price = 0
    owners_out: list[dict[str, Any]] = []
    for row in data.get("owners") or []:
        if not isinstance(row, dict):
            continue
        owners_out.append(
            {
                "id": str(row.get("id") or ""),
                "player_id": str(row.get("id_player") or pid),
                "from_name": str(row.get("from") or "").strip(),
                "to_name": str(row.get("to") or "").strip(),
                "from_uc": str(row.get("id_uc_from") if row.get("id_uc_from") is not None else ""),
                "to_uc": str(row.get("id_uc_to") if row.get("id_uc_to") is not None else ""),
                "price": parse_mister_money(row.get("price")),
                "date": parse_es_date(row.get("date")),
                "type": str(row.get("type") or "").strip().lower() or "normal",
                "transfer_type": str(row.get("transferType") or "").strip(),
            }
        )
    chart_pts: list[dict[str, Any]] = []
    chart = data.get("values_chart") if isinstance(data.get("values_chart"), dict) else {}
    for p in chart.get("points") or []:
        if not isinstance(p, dict):
            continue
        d = parse_es_date(p.get("date"))
        if d is None:
            continue
        chart_pts.append({"date": d, "value": parse_mister_money(p.get("value"))})
    owner_id = None
    if owner.get("id") not in (None, "", 0, "0"):
        owner_id = str(owner.get("id"))
    return {
        "player_id": pid,
        "name": str(player.get("name") or "").strip(),
        "owner_id": owner_id,
        "owner_name": str(owner.get("name") or "").strip() or None,
        "transfer_origin": str(origin).strip().lower() if origin else None,
        "transfer_price": t_price,
        "transfer_date": parse_es_date(transfer.get("date")),
        "owners": owners_out,
        "values_chart": chart_pts,
    }


def sorteo_owner_id(profile: dict[str, Any]) -> str | None:
    """
    Manager que recibió al jugador en el sorteo (price=0 / sin owners).
    Si el más antiguo sale de Mister, no fue inicial.
    """
    owners = list(profile.get("owners") or [])
    current = profile.get("owner_id")
    if not owners:
        if current and not is_machine_uc(current):
            origin = profile.get("transfer_origin")
            price = int(profile.get("transfer_price") or 0)
            if origin in (None, "", "none") and price <= 0:
                return str(current)
        return None
    oldest = owners[-1]  # API: más reciente primero
    from_uc = oldest.get("from_uc")
    if is_machine_uc(from_uc):
        return None
    return str(from_uc)


def profile_is_initial_held(profile: dict[str, Any]) -> bool:
    """Sigue en el manager del sorteo (Huijsen: owners=[], transfer sin precio)."""
    if profile.get("owners"):
        return False
    origin = profile.get("transfer_origin")
    price = int(profile.get("transfer_price") or 0)
    return (not origin or origin in ("none",)) and price <= 0 and bool(profile.get("owner_id"))


def _uc_key(uc: Any) -> str | None:
    s = str(uc or "").strip()
    if is_machine_uc(s):
        return None
    return s


def ledger_from_profiles(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Eventos de caja desde `owners` (dedup por id de transferencia)."""
    seen: set[str] = set()
    events: list[dict[str, Any]] = []
    for prof in profiles:
        pid = str(prof.get("player_id") or "")
        for row in prof.get("owners") or []:
            eid = str(row.get("id") or "")
            key = eid or f"{pid}:{row.get('from_uc')}:{row.get('to_uc')}:{row.get('price')}:{row.get('date')}"
            if key in seen:
                continue
            seen.add(key)
            price = int(row.get("price") or 0)
            kind = str(row.get("type") or "normal")
            events.append(
                {
                    "id": eid or key,
                    "player_id": str(row.get("player_id") or pid),
                    "from_uc": _uc_key(row.get("from_uc")),
                    "to_uc": _uc_key(row.get("to_uc")),
                    "price": price,
                    "date": row.get("date"),
                    "type": kind,
                    "source": "profile",
                }
            )
    return events


def parse_feed_transfers(html: str) -> list[dict[str, Any]]:
    """card-transfer del HTML /feed → movimientos con precio e IDs."""
    if not html:
        return []
    events: list[dict[str, Any]] = []
    for m in re.finditer(
        r'<div id="feed-(\d+)" class="card card-transfer"[\s\S]*?(?=<div id="feed-|\Z)',
        html,
        re.I,
    ):
        feed_id, block = m.group(1), m.group(0)
        items = re.split(r"<li\b", block)
        for i, item in enumerate(items[1:], start=1):
            pid_m = re.search(r'data-id_player=["\'](\d+)["\']', item)
            price_m = re.search(r'<div class="price">\s*([^<]+)', item)
            users = re.findall(r'href=["\']users/(\d+)/', item)
            title_m = re.search(
                r"<strong>([^<]+)</strong>\s*cambia de\s*<em>([\s\S]*?)</em>\s*a\s*<em>([\s\S]*?)</em>",
                item,
                re.I,
            )
            from_name = to_name = ""
            if title_m:
                from_name = re.sub(r"\s+", " ", title_m.group(2)).strip()
                to_name = re.sub(r"\s+", " ", title_m.group(3)).strip()
            from_uc = users[0] if users else None
            to_uc = users[1] if len(users) >= 2 else None
            if from_name.lower() == "mister":
                from_uc = None
            if to_name.lower() == "mister":
                to_uc = None
            if len(users) == 1 and from_name.lower() == "mister":
                to_uc = users[0]
                from_uc = None
            elif len(users) == 1 and to_name.lower() == "mister":
                from_uc = users[0]
                to_uc = None
            kind = "clause" if re.search(r"cl[aá]usula", item, re.I) else "normal"
            events.append(
                {
                    "id": f"feed-{feed_id}-{i}",
                    "player_id": pid_m.group(1) if pid_m else "",
                    "from_uc": _uc_key(from_uc),
                    "to_uc": _uc_key(to_uc),
                    "price": parse_mister_money(price_m.group(1) if price_m else 0),
                    "date": None,
                    "type": kind,
                    "source": "feed",
                    "from_name": from_name,
                    "to_name": to_name,
                }
            )
    return events


def parse_feed_prizes(html: str) -> list[dict[str, Any]]:
    """card-gameweek_end: `+1.650.000` por manager (se suman si hay varias jornadas)."""
    if not html:
        return []
    prizes: list[dict[str, Any]] = []
    for m in re.finditer(
        r'<div id="feed-(\d+)" class="card card-gameweek_end"[\s\S]*?(?=<div id="feed-|\Z)',
        html,
        re.I,
    ):
        feed_id, block = m.group(1), m.group(0)
        for row in re.finditer(
            r'href=["\']users/(\d+)/[^"\']*["\'][\s\S]{0,600}?'
            r'<div class="played[^"]*">\s*([^<]+)',
            block,
            re.I,
        ):
            uc, raw_amt = row.group(1), row.group(2)
            amount = parse_mister_money(raw_amt)
            if amount == 0:
                continue
            prizes.append(
                {
                    "id": f"prize-{feed_id}-{uc}",
                    "uc": str(uc),
                    "amount": amount,
                    "source": "feed_gameweek_end",
                }
            )
    return prizes


def normalize_gameweek_id(raw: Any) -> str | None:
    """id_gameweek puede venir int, str o dict `{id: …}`."""
    if isinstance(raw, dict):
        raw = raw.get("id") or raw.get("id_gameweek")
    if raw in (None, "", False, True):
        return None
    text = str(raw).strip()
    if not text or text in ("0", "None"):
        return None
    return text


def prize_event_id(uc: str, gameweek_id: str | None, card_id: str = "") -> str:
    """Una jornada + manager = un premio. El feed a veces publica la misma GW dos veces."""
    if gameweek_id:
        return f"prize-gw{gameweek_id}-{uc}"
    return f"prize-{card_id}-{uc}"


def _gameweek_prize_positions(card_data: dict[str, Any]) -> list[dict[str, Any]]:
    ranking = card_data.get("ranking")
    if isinstance(ranking, list):
        return [r for r in ranking if isinstance(r, dict)]
    if not isinstance(ranking, dict):
        return []
    inner = ranking.get("ranking")
    if isinstance(inner, dict) and isinstance(inner.get("positions"), list):
        return [r for r in inner["positions"] if isinstance(r, dict)]
    if isinstance(ranking.get("positions"), list):
        return [r for r in ranking["positions"] if isinstance(r, dict)]
    return []


def parse_feed_ajax_cards(
    cards: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Tarjetas JSON de POST /ajax/feed.

    Economía:
      - transfer: P2P, compra/venta Mister, clausulazo (`type=clause`)
      - gameweek_end: premios (`payment` por manager)
    Se ignora: player_transfer (fichajes reales), gameweek_end_pools,
    clauses_drops (baja de cláusula, no caja), market, posts, porra, etc.
    """
    transfers: list[dict[str, Any]] = []
    prizes: list[dict[str, Any]] = []
    seen_prize_gw: set[tuple[str, str]] = set()
    if not cards:
        return transfers, prizes
    for card in cards:
        if not isinstance(card, dict):
            continue
        cat = str(card.get("category") or "").strip().lower()
        card_id = str(card.get("id") or "")
        created = parse_es_date(card.get("created") or card.get("date"))
        if cat == "transfer":
            rows = card.get("data") if isinstance(card.get("data"), list) else []
            for i, item in enumerate(rows, start=1):
                if not isinstance(item, dict):
                    continue
                tid = item.get("id_transfer")
                eid = (
                    str(tid)
                    if tid not in (None, "", 0, "0")
                    else f"ajax-{card_id}-{i}"
                )
                kind = str(item.get("type") or "normal").strip().lower() or "normal"
                transfers.append(
                    {
                        "id": eid,
                        "player_id": str(item.get("id") or ""),
                        "from_uc": _uc_key(item.get("id_uc_from")),
                        "to_uc": _uc_key(item.get("id_uc_to")),
                        "price": parse_mister_money(item.get("price")),
                        "date": created,
                        "type": kind,
                        "source": "feed_ajax",
                        "from_name": str(item.get("from") or "").strip(),
                        "to_name": str(item.get("to") or "").strip(),
                    }
                )
        elif cat == "gameweek_end":
            data = card.get("data") if isinstance(card.get("data"), dict) else {}
            gw = normalize_gameweek_id(data.get("id_gameweek") or data.get("gameweek"))
            for pos in _gameweek_prize_positions(data):
                uc = pos.get("idUc") or pos.get("id_uc")
                amount = parse_mister_money(pos.get("payment") or pos.get("amount"))
                if not uc or amount == 0:
                    continue
                uc_s = str(uc)
                gw_key = (gw or f"card:{card_id}", uc_s)
                if gw_key in seen_prize_gw:
                    continue
                seen_prize_gw.add(gw_key)
                prizes.append(
                    {
                        "id": prize_event_id(uc_s, gw, card_id),
                        "uc": uc_s,
                        "amount": amount,
                        "source": "feed_gameweek_end",
                        "gameweek_id": gw,
                    }
                )
    return transfers, prizes


def merge_ledger(
    profile_events: list[dict[str, Any]],
    feed_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Prefiere ficha de jugador; el feed cubre ventas a Mister de jugadores ya libres."""
    out: list[dict[str, Any]] = list(profile_events)
    seen_player_price: set[tuple[str, int, str | None, str | None]] = set()
    for ev in profile_events:
        seen_player_price.add(
            (
                str(ev.get("player_id") or ""),
                int(ev.get("price") or 0),
                ev.get("from_uc"),
                ev.get("to_uc"),
            )
        )
    for ev in feed_events:
        key = (
            str(ev.get("player_id") or ""),
            int(ev.get("price") or 0),
            ev.get("from_uc"),
            ev.get("to_uc"),
        )
        if key[0] and key in seen_player_price:
            continue
        out.append(ev)
        seen_player_price.add(key)
    return out


def apply_cash_events(
    cash: dict[str, float],
    events: list[dict[str, Any]],
) -> dict[str, float]:
    """Comprador −precio; vendedor manager +precio. Mister no mueve caja propia."""
    out = dict(cash)
    for ev in events:
        price = float(ev.get("price") or 0)
        if price == 0:
            continue
        buyer = ev.get("to_uc")
        seller = ev.get("from_uc")
        if buyer:
            out[buyer] = out.get(buyer, 0.0) - price
        if seller:
            out[seller] = out.get(seller, 0.0) + price
    return out


def initial_vm_by_manager(
    profiles: list[dict[str, Any]],
    sorteo: date,
) -> dict[str, dict[str, Any]]:
    """Suma VM en fecha de sorteo por manager que recibió el jugador inicial."""
    by_uc: dict[str, dict[str, Any]] = {}
    for prof in profiles:
        oid = sorteo_owner_id(prof)
        if not oid:
            continue
        vm = vm_at_date(prof.get("values_chart") or [], sorteo)
        if vm is None:
            vm = 0
        row = by_uc.setdefault(oid, {"vm": 0, "count": 0, "player_ids": []})
        row["vm"] += vm
        row["count"] += 1
        row["player_ids"].append(str(prof.get("player_id")))
    return by_uc


def event_fingerprint(ev: dict[str, Any]) -> str:
    """Clave estable jugador+precio+from+to (dedup ficha vs feed)."""
    return "|".join(
        [
            str(ev.get("player_id") or ""),
            str(int(ev.get("price") or 0)),
            str(ev.get("from_uc") or ""),
            str(ev.get("to_uc") or ""),
        ]
    )


def _manager_ids(rivals: list[dict[str, Any]], me_uc: str | None) -> set[str]:
    managers: set[str] = set()
    if me_uc:
        managers.add(str(me_uc))
    for r in rivals:
        tid = str(r.get("team_id") or "")
        if tid:
            managers.add(tid)
    return managers


def _empty_init() -> dict[str, Any]:
    return {"vm": 0, "count": 0, "player_ids": []}


def _append_move(
    bucket: dict[str, list[dict[str, Any]]],
    uc: str | None,
    item: dict[str, Any],
    cap: int = RECENT_MOVES_CAP,
) -> None:
    if not uc:
        return
    lst = bucket.setdefault(uc, [])
    lst.append(item)
    if len(lst) > cap:
        del lst[:-cap]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(raw: Any) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def finance_snapshot_path(community_id: str, root: Path | None = None) -> Path:
    cid = str(community_id or "default").strip() or "default"
    if root is None:
        try:
            import config as _cfg

            root = _cfg.ROOT_DIR
        except Exception:  # noqa: BLE001
            root = Path.cwd()
    path_dir = Path(root) / "cache" / "rival_finances"
    path_dir.mkdir(parents=True, exist_ok=True)
    return path_dir / f"{cid}.json"


def load_finance_snapshot(
    community_id: str,
    root: Path | None = None,
) -> dict[str, Any] | None:
    path = finance_snapshot_path(community_id, root=root)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("snapshot finanzas ilegible %s: %s", path, exc)
        return None
    return raw if isinstance(raw, dict) else None


def save_finance_snapshot(
    community_id: str,
    snap: dict[str, Any],
    root: Path | None = None,
) -> Path:
    path = finance_snapshot_path(community_id, root=root)
    path.write_text(
        json.dumps(snap, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return path


def snapshot_needs_bootstrap(
    snap: dict[str, Any] | None,
    *,
    rivals: list[dict[str, Any]],
    me_uc: str | None,
    sorteo_date: date | None,
    start_mode: str,
    starting_budget: float,
    now: datetime | None = None,
    max_age_days: int = FEED_MAX_AGE_DAYS,
) -> tuple[bool, str]:
    """True si hay que volver a barrer todas las fichas (no vale el feed)."""
    if not snap or not isinstance(snap.get("cash"), dict) or not snap["cash"]:
        return True, "missing"
    if int(snap.get("version") or 0) != SNAPSHOT_VERSION:
        return True, "version"
    want_sorteo = (sorteo_date or DEFAULT_SORTEO).isoformat()
    if str(snap.get("sorteo_date") or "") != want_sorteo:
        return True, "sorteo"
    if str(snap.get("start_mode") or "") != str(start_mode):
        return True, "start_mode"
    try:
        if int(snap.get("starting_budget") or 0) != int(starting_budget):
            return True, "budget"
    except (TypeError, ValueError):
        return True, "budget"
    cash = snap.get("cash") or {}
    for uc in _manager_ids(rivals, me_uc):
        if uc not in cash:
            return True, "new_manager"
    updated = _parse_iso(snap.get("updated_at") or snap.get("bootstrapped_at"))
    if updated is None:
        return True, "no_timestamp"
    age = (now or datetime.now(timezone.utc)) - updated
    if age > timedelta(days=max(1, int(max_age_days))):
        return True, "stale"
    return False, "fresh"


def unseen_feed_events(
    events: list[dict[str, Any]],
    seen_ids: set[str],
    seen_fps: set[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in events:
        eid = str(ev.get("id") or "")
        fp = event_fingerprint(ev)
        if eid and eid in seen_ids:
            continue
        if ev.get("player_id") and fp in seen_fps:
            continue
        out.append(ev)
    return out


def unseen_prizes(
    prizes: list[dict[str, Any]],
    seen_ids: set[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in prizes:
        eid = str(p.get("id") or "")
        uc = str(p.get("uc") or "")
        gw = normalize_gameweek_id(p.get("gameweek_id"))
        gw_eid = prize_event_id(uc, gw) if gw and uc else ""
        if eid and eid in seen_ids:
            continue
        if gw_eid and gw_eid in seen_ids:
            continue
        out.append(p)
    return out


def annotate_rivals_from_state(
    rivals: list[dict[str, Any]],
    *,
    cash: dict[str, float],
    initials: dict[str, dict[str, Any]],
    prizes_by_uc: dict[str, float],
    buys_by_uc: dict[str, list[dict[str, Any]]],
    sells_by_uc: dict[str, list[dict[str, Any]]],
    starting_budget: float,
    start_mode: str,
    max_debt_level: Any,
) -> list[dict[str, Any]]:
    frac = debt_credit_fraction(max_debt_level)
    annotated: list[dict[str, Any]] = []
    for rival in rivals:
        row = dict(rival)
        uc = str(row.get("team_id") or "")
        squad_value = float(row.get("squad_value") or 0)
        estimated = cash.get(uc)
        init_info = initials.get(uc) or _empty_init()
        init_cash = float(starting_budget) - (
            float(init_info["vm"]) if start_mode == "random_minus_vm" else 0.0
        )
        buys = buys_by_uc.get(uc) or []
        sells = sells_by_uc.get(uc) or []
        net = sum(float(s.get("price") or 0) for s in sells) - sum(
            float(b.get("price") or 0) for b in buys
        )
        dq = dict(row.get("data_quality") or {})
        if estimated is None:
            row["liquidity_estimated"] = None
            row["bid_cap_estimated"] = None
            dq["liquidity"] = "missing"
        else:
            row["liquidity_estimated"] = int(round(estimated))
            row["bid_cap_estimated"] = int(round(estimated + squad_value * frac))
            dq["liquidity"] = "estimated"
        row["initial_cash_estimated"] = int(round(init_cash))
        row["initial_vm_estimated"] = int(round(float(init_info["vm"] or 0)))
        row["initial_players_count"] = int(init_info["count"] or 0)
        row["prizes_total"] = int(round(prizes_by_uc.get(uc, 0)))
        row["recent_buys"] = buys
        row["recent_sells"] = sells
        row["recent_net"] = int(round(net))
        row["debt_credit_fraction"] = frac
        abs_net = abs(net)
        row["activity"] = (
            "alta" if abs_net >= 8_000_000 else ("media" if abs_net >= 3_000_000 else "baja")
        )
        row["data_quality"] = dq
        annotated.append(row)
    return annotated


def _meta_from_cash(
    *,
    cash: dict[str, float],
    me_uc: str | None,
    me_balance: float | None,
    me_squad_value: float | None,
    max_debt_level: Any,
    start_mode: str,
    starting_budget: float,
    sorteo_date: date | None,
    profiles_n: int,
    ledger_n: int,
    prizes_n: int,
    source: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    frac = debt_credit_fraction(max_debt_level)
    me_estimated = cash.get(str(me_uc)) if me_uc else None
    me_error = None
    if me_estimated is not None and me_balance is not None:
        me_error = float(me_balance) - float(me_estimated)
    me_bid = None
    if me_estimated is not None and me_squad_value is not None:
        me_bid = rival_bid_cap(me_estimated, me_squad_value, max_debt_level)
    meta = {
        "source": source,
        "start_mode": start_mode,
        "starting_budget": int(starting_budget),
        "sorteo_date": (sorteo_date or DEFAULT_SORTEO).isoformat(),
        "max_debt_level": float(max_debt_level) if max_debt_level is not None else DEFAULT_DEBT_LEVEL,
        "debt_credit_fraction": frac,
        "profiles": profiles_n,
        "ledger_events": ledger_n,
        "prizes_events": prizes_n,
        "me_estimated": int(round(me_estimated)) if me_estimated is not None else None,
        "me_actual": int(round(float(me_balance))) if me_balance is not None else None,
        "me_error": int(round(me_error)) if me_error is not None else None,
        "me_bid_cap_estimated": int(round(me_bid)) if me_bid is not None else None,
    }
    if extra:
        meta.update(extra)
    return meta


def compute_bootstrap_state(
    *,
    profiles: list[dict[str, Any]],
    rivals: list[dict[str, Any]],
    me_uc: str | None,
    feed_transfers: list[dict[str, Any]] | None = None,
    feed_prizes: list[dict[str, Any]] | None = None,
    starting_budget: float = DEFAULT_STARTING_BUDGET,
    sorteo_date: date | None = DEFAULT_SORTEO,
    start_mode: str = "random_minus_vm",
) -> dict[str, Any]:
    """Ledger completo: iniciales (ficha) + historial de TODOS los jugadores + feed reciente."""
    managers = _manager_ids(rivals, me_uc)
    cash: dict[str, float] = {uc: float(starting_budget) for uc in managers}
    initials = initial_vm_by_manager(profiles, sorteo_date or DEFAULT_SORTEO)
    if start_mode == "random_minus_vm":
        for uc, info in initials.items():
            cash[uc] = cash.get(uc, float(starting_budget)) - float(info.get("vm") or 0)

    events = merge_ledger(ledger_from_profiles(profiles), feed_transfers or [])
    cash = apply_cash_events(cash, events)

    prizes_by_uc: dict[str, float] = {}
    seen_prize_gw: set[tuple[str, str]] = set()
    for p in feed_prizes or []:
        uc = str(p.get("uc") or "")
        if not uc:
            continue
        gw = normalize_gameweek_id(p.get("gameweek_id"))
        gw_key = (gw or str(p.get("id") or ""), uc)
        if gw_key in seen_prize_gw:
            continue
        seen_prize_gw.add(gw_key)
        prizes_by_uc[uc] = prizes_by_uc.get(uc, 0.0) + float(p.get("amount") or 0)
        cash[uc] = cash.get(uc, 0.0) + float(p.get("amount") or 0)

    buys_by_uc: dict[str, list[dict[str, Any]]] = {uc: [] for uc in managers}
    sells_by_uc: dict[str, list[dict[str, Any]]] = {uc: [] for uc in managers}
    seen_ids: list[str] = []
    seen_fps: list[str] = []
    for ev in events:
        item = {"player_id": ev.get("player_id"), "price": ev.get("price"), "type": ev.get("type")}
        if ev.get("to_uc"):
            _append_move(buys_by_uc, str(ev["to_uc"]), item)
        if ev.get("from_uc"):
            _append_move(sells_by_uc, str(ev["from_uc"]), item)
        eid = str(ev.get("id") or "")
        if eid:
            seen_ids.append(eid)
        fp = event_fingerprint(ev)
        if ev.get("player_id"):
            seen_fps.append(fp)
    for p in feed_prizes or []:
        eid = str(p.get("id") or "")
        if eid:
            seen_ids.append(eid)
        uc = str(p.get("uc") or "")
        gw = normalize_gameweek_id(p.get("gameweek_id"))
        gw_eid = prize_event_id(uc, gw) if gw and uc else ""
        if gw_eid and gw_eid != eid:
            seen_ids.append(gw_eid)
    return {
        "cash": cash,
        "initials": initials,
        "prizes_by_uc": prizes_by_uc,
        "buys": buys_by_uc,
        "sells": sells_by_uc,
        "seen_ids": seen_ids,
        "seen_fps": seen_fps,
        "events_n": len(events),
        "prizes_n": len(seen_prize_gw),
    }


def snapshot_from_bootstrap_state(
    state: dict[str, Any],
    *,
    community_id: str,
    starting_budget: float,
    sorteo_date: date | None,
    start_mode: str,
    max_debt_level: Any,
) -> dict[str, Any]:
    now = _now_iso()
    cash_out = {str(k): int(round(float(v))) for k, v in (state.get("cash") or {}).items()}
    return {
        "version": SNAPSHOT_VERSION,
        "community_id": str(community_id),
        "bootstrapped_at": now,
        "updated_at": now,
        "sorteo_date": (sorteo_date or DEFAULT_SORTEO).isoformat(),
        "start_mode": start_mode,
        "starting_budget": int(starting_budget),
        "max_debt_level": float(max_debt_level) if max_debt_level is not None else DEFAULT_DEBT_LEVEL,
        "cash": cash_out,
        "initials": state.get("initials") or {},
        "prizes_by_uc": {
            str(k): int(round(float(v))) for k, v in (state.get("prizes_by_uc") or {}).items()
        },
        "buys": state.get("buys") or {},
        "sells": state.get("sells") or {},
        "seen_ids": list(dict.fromkeys(state.get("seen_ids") or [])),
        "seen_fps": list(dict.fromkeys(state.get("seen_fps") or [])),
    }


def apply_feed_delta(
    snap: dict[str, Any],
    feed_transfers: list[dict[str, Any]] | None,
    feed_prizes: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Aplica solo movimientos/premios del feed que el snapshot aún no ha visto."""
    out = dict(snap)
    cash = {str(k): float(v) for k, v in (out.get("cash") or {}).items()}
    prizes_by_uc = {str(k): float(v) for k, v in (out.get("prizes_by_uc") or {}).items()}
    buys = {str(k): list(v) for k, v in (out.get("buys") or {}).items() if isinstance(v, list)}
    sells = {str(k): list(v) for k, v in (out.get("sells") or {}).items() if isinstance(v, list)}
    seen_ids = set(str(x) for x in (out.get("seen_ids") or []))
    seen_fps = set(str(x) for x in (out.get("seen_fps") or []))

    new_tx = unseen_feed_events(feed_transfers or [], seen_ids, seen_fps)
    new_pr = unseen_prizes(feed_prizes or [], seen_ids)
    cash = apply_cash_events(cash, new_tx)
    for ev in new_tx:
        item = {"player_id": ev.get("player_id"), "price": ev.get("price"), "type": ev.get("type")}
        if ev.get("to_uc"):
            _append_move(buys, str(ev["to_uc"]), item)
        if ev.get("from_uc"):
            _append_move(sells, str(ev["from_uc"]), item)
        eid = str(ev.get("id") or "")
        if eid:
            seen_ids.add(eid)
        fp = event_fingerprint(ev)
        if ev.get("player_id"):
            seen_fps.add(fp)
    for p in new_pr:
        uc = str(p.get("uc") or "")
        amt = float(p.get("amount") or 0)
        if uc:
            prizes_by_uc[uc] = prizes_by_uc.get(uc, 0.0) + amt
            cash[uc] = cash.get(uc, 0.0) + amt
        eid = str(p.get("id") or "")
        if eid:
            seen_ids.add(eid)
        gw = normalize_gameweek_id(p.get("gameweek_id"))
        gw_eid = prize_event_id(uc, gw) if gw and uc else ""
        if gw_eid:
            seen_ids.add(gw_eid)

    out["cash"] = {k: int(round(v)) for k, v in cash.items()}
    out["prizes_by_uc"] = {k: int(round(v)) for k, v in prizes_by_uc.items()}
    out["buys"] = buys
    out["sells"] = sells
    out["seen_ids"] = sorted(seen_ids)
    out["seen_fps"] = sorted(seen_fps)
    out["updated_at"] = _now_iso()
    delta = {"new_transfers": len(new_tx), "new_prizes": len(new_pr)}
    return out, delta


def _state_from_snapshot(snap: dict[str, Any]) -> dict[str, Any]:
    initials_raw = snap.get("initials") or {}
    initials: dict[str, dict[str, Any]] = {}
    for uc, info in initials_raw.items():
        if not isinstance(info, dict):
            continue
        initials[str(uc)] = {
            "vm": int(info.get("vm") or 0),
            "count": int(info.get("count") or 0),
            "player_ids": list(info.get("player_ids") or []),
        }
    buys = {str(k): list(v) for k, v in (snap.get("buys") or {}).items() if isinstance(v, list)}
    sells = {str(k): list(v) for k, v in (snap.get("sells") or {}).items() if isinstance(v, list)}
    return {
        "cash": {str(k): float(v) for k, v in (snap.get("cash") or {}).items()},
        "initials": initials,
        "prizes_by_uc": {str(k): float(v) for k, v in (snap.get("prizes_by_uc") or {}).items()},
        "buys": buys,
        "sells": sells,
    }


def run_rival_finances(
    *,
    community_id: str,
    rivals: list[dict[str, Any]],
    me_uc: str | None,
    me_balance: float | None = None,
    me_squad_value: float | None = None,
    profiles: list[dict[str, Any]] | None = None,
    feed_transfers: list[dict[str, Any]] | None = None,
    feed_prizes: list[dict[str, Any]] | None = None,
    starting_budget: float = DEFAULT_STARTING_BUDGET,
    sorteo_date: date | None = DEFAULT_SORTEO,
    start_mode: str = "random_minus_vm",
    max_debt_level: Any = DEFAULT_DEBT_LEVEL,
    snapshot: dict[str, Any] | None = None,
    persist: bool = True,
    root: Path | None = None,
    max_age_days: int = FEED_MAX_AGE_DAYS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Bootstrap (todas las fichas) o delta diario del feed.
    Persiste cache/rival_finances/{community_id}.json.
    """
    need, reason = snapshot_needs_bootstrap(
        snapshot,
        rivals=rivals,
        me_uc=me_uc,
        sorteo_date=sorteo_date,
        start_mode=start_mode,
        starting_budget=starting_budget,
        max_age_days=max_age_days,
    )
    use_bootstrap = need and bool(profiles)
    if need and not profiles:
        if snapshot and snapshot.get("cash"):
            log.warning(
                "Bootstrap finanzas (%s) sin fichas; sigo con snapshot + feed",
                reason,
            )
            use_bootstrap = False
        else:
            return rivals, {
                "source": "unavailable",
                "update_mode": "skipped",
                "bootstrap_reason": reason,
                "error": "sin snapshot ni fichas",
            }

    if use_bootstrap:
        state = compute_bootstrap_state(
            profiles=profiles or [],
            rivals=rivals,
            me_uc=me_uc,
            feed_transfers=feed_transfers,
            feed_prizes=feed_prizes,
            starting_budget=starting_budget,
            sorteo_date=sorteo_date,
            start_mode=start_mode,
        )
        snap = snapshot_from_bootstrap_state(
            state,
            community_id=community_id,
            starting_budget=starting_budget,
            sorteo_date=sorteo_date,
            start_mode=start_mode,
            max_debt_level=max_debt_level,
        )
        extra = {
            "update_mode": "bootstrap",
            "bootstrap_reason": reason,
        }
        source = "player_profiles+feed"
        profiles_n = len(profiles or [])
        ledger_n = int(state.get("events_n") or 0)
        prizes_n = int(state.get("prizes_n") or 0)
    else:
        snap, delta = apply_feed_delta(snapshot or {}, feed_transfers, feed_prizes)
        state = _state_from_snapshot(snap)
        extra = {
            "update_mode": "feed_incremental",
            "bootstrap_reason": reason if not need else "snapshot_fallback",
            **delta,
        }
        source = "feed_incremental"
        profiles_n = 0
        ledger_n = int(delta.get("new_transfers") or 0)
        prizes_n = int(delta.get("new_prizes") or 0)

    if persist:
        try:
            save_finance_snapshot(community_id, snap, root=root)
        except OSError as exc:
            log.warning("no se pudo guardar snapshot finanzas: %s", exc)

    annotated = annotate_rivals_from_state(
        rivals,
        cash=state["cash"],
        initials=state["initials"],
        prizes_by_uc=state["prizes_by_uc"],
        buys_by_uc=state["buys"],
        sells_by_uc=state["sells"],
        starting_budget=starting_budget,
        start_mode=start_mode,
        max_debt_level=max_debt_level,
    )
    meta = _meta_from_cash(
        cash=state["cash"],
        me_uc=me_uc,
        me_balance=me_balance,
        me_squad_value=me_squad_value,
        max_debt_level=max_debt_level,
        start_mode=start_mode,
        starting_budget=starting_budget,
        sorteo_date=sorteo_date,
        profiles_n=profiles_n,
        ledger_n=ledger_n,
        prizes_n=prizes_n,
        source=source,
        extra=extra,
    )
    return annotated, meta


def build_rival_finances(
    *,
    profiles: list[dict[str, Any]],
    rivals: list[dict[str, Any]],
    me_uc: str | None,
    me_balance: float | None = None,
    me_squad_value: float | None = None,
    feed_transfers: list[dict[str, Any]] | None = None,
    feed_prizes: list[dict[str, Any]] | None = None,
    starting_budget: float = DEFAULT_STARTING_BUDGET,
    sorteo_date: date | None = DEFAULT_SORTEO,
    start_mode: str = "random_minus_vm",
    max_debt_level: Any = DEFAULT_DEBT_LEVEL,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Recalcula desde fichas (tests / bootstrap). No persiste snapshot.
    start_mode: random_minus_vm (Patio) | cash (concursos 0 jugadores + 50M).
    """
    state = compute_bootstrap_state(
        profiles=profiles,
        rivals=rivals,
        me_uc=me_uc,
        feed_transfers=feed_transfers,
        feed_prizes=feed_prizes,
        starting_budget=starting_budget,
        sorteo_date=sorteo_date,
        start_mode=start_mode,
    )
    annotated = annotate_rivals_from_state(
        rivals,
        cash=state["cash"],
        initials=state["initials"],
        prizes_by_uc=state["prizes_by_uc"],
        buys_by_uc=state["buys"],
        sells_by_uc=state["sells"],
        starting_budget=starting_budget,
        start_mode=start_mode,
        max_debt_level=max_debt_level,
    )
    meta = _meta_from_cash(
        cash=state["cash"],
        me_uc=me_uc,
        me_balance=me_balance,
        me_squad_value=me_squad_value,
        max_debt_level=max_debt_level,
        start_mode=start_mode,
        starting_budget=starting_budget,
        sorteo_date=sorteo_date,
        profiles_n=len(profiles),
        ledger_n=int(state.get("events_n") or 0),
        prizes_n=int(state.get("prizes_n") or 0),
        source="player_profiles+feed",
        extra={"update_mode": "bootstrap"},
    )
    return annotated, meta


def profile_to_jsonable(profile: dict[str, Any]) -> dict[str, Any]:
    """date → ISO para cache en disco."""
    owners = []
    for row in profile.get("owners") or []:
        item = dict(row)
        d = item.get("date")
        item["date"] = d.isoformat() if isinstance(d, date) else d
        owners.append(item)
    chart = []
    for p in profile.get("values_chart") or []:
        d = p.get("date")
        chart.append(
            {
                "date": d.isoformat() if isinstance(d, date) else d,
                "value": p.get("value"),
            }
        )
    td = profile.get("transfer_date")
    return {
        **profile,
        "transfer_date": td.isoformat() if isinstance(td, date) else td,
        "owners": owners,
        "values_chart": chart,
    }


def profile_from_jsonable(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    out = dict(raw)
    out["transfer_date"] = parse_es_date(raw.get("transfer_date"))
    owners = []
    for row in raw.get("owners") or []:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item["date"] = parse_es_date(row.get("date"))
        owners.append(item)
    out["owners"] = owners
    chart = []
    for p in raw.get("values_chart") or []:
        if not isinstance(p, dict):
            continue
        d = parse_es_date(p.get("date"))
        if d is None:
            continue
        chart.append({"date": d, "value": int(p.get("value") or 0)})
    out["values_chart"] = chart
    return out


def start_mode_for_league(league_type: str | None, mode: str | None, override: str | None = None) -> str:
    if override in ("cash", "random_minus_vm"):
        return override
    if str(mode or "").lower() == "contest" or str(league_type or "").lower() == "lfm":
        return "cash"
    return "random_minus_vm"


def sorteo_date_from_ts(raw: Any, fallback: date = DEFAULT_SORTEO) -> date:
    try:
        ts = int(raw)
    except (TypeError, ValueError):
        return fallback
    if ts <= 0:
        return fallback
    try:
        return datetime.utcfromtimestamp(ts).date()
    except (OSError, OverflowError, ValueError):
        return fallback
