"""Diario de decisiones por liga, para leer al cabo de los días qué se puede mejorar."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import config

PROFIT = 0.03


def _path(slug: str, root: Path | None = None) -> Path:
    base = root or config.LEAGUES_DIR
    return base / slug / "behavior_log.json"


def empty_log(slug: str) -> dict[str, Any]:
    return {"league": slug, "cycles": [], "summary": {}}


def load_log(slug: str, root: Path | None = None) -> dict[str, Any]:
    path = _path(slug, root)
    if not path.is_file():
        return empty_log(slug)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_log(slug)
    if not isinstance(data, dict) or not isinstance(data.get("cycles"), list):
        return empty_log(slug)
    data.setdefault("league", slug)
    return data


def save_log(slug: str, log: dict[str, Any], root: Path | None = None) -> None:
    path = _path(slug, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _parse_at(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _prune(cycles: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    days = int(getattr(config, "BEHAVIOR_LOG_DAYS", 30) or 30)
    cutoff = now - timedelta(days=days)
    kept: list[dict[str, Any]] = []
    for cycle in cycles:
        at = _parse_at(cycle.get("at"))
        if at is None or at >= cutoff:
            kept.append(cycle)
    return kept


def summarize(log: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)

    def window(days: int) -> dict[str, Any]:
        start = ref - timedelta(days=days)
        bids = won = lost = lists = accepts = shorts = 0
        profit = 0.0
        reasons: dict[str, int] = {}
        for cycle in log.get("cycles") or []:
            at = _parse_at(cycle.get("at"))
            if at is None or at < start or at > ref + timedelta(seconds=1):
                continue
            for decision in cycle.get("decisions") or []:
                if not isinstance(decision, dict):
                    continue
                kind = decision.get("kind")
                if kind == "bid":
                    bids += 1
                    if decision.get("result") == "ganada":
                        won += 1
                    elif decision.get("result") == "perdida":
                        lost += 1
                elif kind == "list_for_sale":
                    lists += 1
                elif kind == "accept_offer":
                    buy = float(decision.get("buy_price") or 0)
                    amount = float(decision.get("amount") or 0)
                    if buy > 0 and amount > buy * (1.0 + PROFIT):
                        accepts += 1
                        profit += amount - buy
                    elif buy > 0:
                        shorts += 1
                elif kind == "decline_offer":
                    shorts += 1
                if decision.get("status") == "descartada":
                    why = str(decision.get("why") or "").strip()
                    if why:
                        reasons[why] = reasons.get(why, 0) + 1
                if decision.get("list_state") == "pendiente_listar":
                    reasons["pendiente de listar"] = reasons.get("pendiente de listar", 0) + 1
        top = sorted(reasons.items(), key=lambda item: (-item[1], item[0]))[:5]
        return {
            "bids": bids,
            "won": won,
            "lost": lost,
            "lists": lists,
            "accepts": accepts,
            "profit": round(profit),
            "short_offers": shorts,
            "top_skips": [{"why": why, "count": count} for why, count in top],
        }

    return {"d7": window(7), "d14": window(14)}


def record_cycle(log: dict[str, Any], entry: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    data = deepcopy(log) if isinstance(log, dict) else empty_log("")
    cycles = list(data.get("cycles") or [])
    cycles.append(dict(entry))
    ref = now or _parse_at(entry.get("at")) or datetime.now(timezone.utc)
    data["cycles"] = _prune(cycles, ref)
    data["summary"] = summarize(data, now=ref)
    data["updated_at"] = ref.isoformat()
    return data


def reconcile(
    log: dict[str, Any],
    *,
    squad_ids: set[str],
    market_ids: set[str],
    now: datetime | None = None,
    pending_list_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Cierra pujas ganadas o perdidas con la plantilla y el mercado de este run."""
    data = deepcopy(log) if isinstance(log, dict) else empty_log("")
    owned = {str(pid) for pid in squad_ids if pid}
    listed_market = {str(pid) for pid in market_ids if pid}
    waiting = {str(pid) for pid in (pending_list_ids or set()) if pid}
    for cycle in data.get("cycles") or []:
        for decision in cycle.get("decisions") or []:
            if not isinstance(decision, dict):
                continue
            pid = str(decision.get("player_id") or "")
            kind = decision.get("kind")
            if kind == "bid" and not decision.get("result") and pid:
                if pid in owned:
                    decision["result"] = "ganada"
                elif pid not in listed_market:
                    decision["result"] = "perdida"
            if kind == "bid" and decision.get("result") == "ganada" and pid in waiting:
                decision["list_state"] = "pendiente_listar"
            if kind == "accept_offer" and pid and pid not in owned and not decision.get("result"):
                buy = float(decision.get("buy_price") or 0)
                amount = float(decision.get("amount") or 0)
                if buy > 0 and amount > buy * (1.0 + PROFIT):
                    decision["result"] = "aceptada"
                    decision["profit"] = round(amount - buy)
    ref = now or datetime.now(timezone.utc)
    data["summary"] = summarize(data, now=ref)
    return data


def decisions_from_plan(plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    plan = plan if isinstance(plan, dict) else {}
    rows: list[dict[str, Any]] = []
    for move in plan.get("moves") or []:
        if not isinstance(move, dict):
            continue
        rows.append(
            {
                "kind": move.get("kind"),
                "player_id": str(move.get("player_id") or ""),
                "name": move.get("name"),
                "amount": move.get("amount"),
                "market_value": move.get("market_value"),
                "buy_price": move.get("buy_price"),
                "threshold": move.get("threshold"),
                "status": "recomendada",
                "why": move.get("why"),
            }
        )
    for skipped in plan.get("flip_skipped") or []:
        if not isinstance(skipped, dict):
            continue
        rows.append(
            {
                "kind": skipped.get("kind"),
                "player_id": str(skipped.get("player_id") or ""),
                "name": skipped.get("name"),
                "amount": skipped.get("amount"),
                "status": "descartada",
                "why": skipped.get("why"),
            }
        )
    return rows


def update_league_log(
    slug: str,
    *,
    mode: str,
    plan: dict[str, Any] | None,
    squad_ids: set[str],
    market_ids: set[str],
    pending_list_ids: set[str] | None = None,
    at: datetime | None = None,
    flip: dict[str, Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    ref = at or datetime.now(timezone.utc)
    log = load_log(slug, root)
    log = reconcile(
        log,
        squad_ids=squad_ids,
        market_ids=market_ids,
        now=ref,
        pending_list_ids=pending_list_ids,
    )
    log = record_cycle(
        log,
        {
            "at": ref.isoformat(),
            "league": slug,
            "mode": mode,
            "flip": flip or {},
            "decisions": decisions_from_plan(plan),
        },
        now=ref,
    )
    log["league"] = slug
    save_log(slug, log, root)
    return log


def stamp_execution(
    slug: str,
    operations: list[dict[str, Any]] | None,
    root: Path | None = None,
) -> dict[str, Any] | None:
    """El ejecutor marca en el último ciclo si la decisión se mandó, se bloqueó o falló."""
    log = load_log(slug, root)
    cycles = log.get("cycles") or []
    if not cycles:
        return None
    last = cycles[-1]
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for decision in last.get("decisions") or []:
        if isinstance(decision, dict):
            index[(str(decision.get("player_id") or ""), str(decision.get("kind") or ""))] = decision
    for op in operations or []:
        if not isinstance(op, dict):
            continue
        decision = index.get((str(op.get("player_id") or ""), str(op.get("kind") or "")))
        if decision is None:
            continue
        status = str(op.get("status") or "")
        if status in ("ok", "dry_run"):
            decision["status"] = "ejecutada"
        elif status in ("blocked", "blocked_unverified"):
            decision["status"] = "bloqueada"
            if op.get("error"):
                decision["error"] = op.get("error")
        elif status == "error":
            decision["status"] = "error"
            decision["error"] = op.get("error")
        if op.get("request"):
            decision["request"] = op.get("request")
        if op.get("response") is not None:
            decision["response"] = op.get("response")
        if op.get("http_status"):
            decision["http_status"] = op.get("http_status")
    log["summary"] = summarize(log)
    save_log(slug, log, root)
    return log
