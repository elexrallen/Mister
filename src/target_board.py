"""
Plantilla perfecta diaria bajo presupuesto total (saldo + valor de plantilla).

Proxy de puntaje esperado: production_score + ff_mister_avg × titularidad.
Persiste entre runs en public/data/leagues/<slug>/target_board.json.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from competitive_actions import budget_fit, target_tier_from_budget_fit

log = logging.getLogger("target_board")

KEEP_EP_RATIO = 0.90  # legacy; ya no se usa para preferir owned en la selección
MAX_DROPPED_DAYS = 5
PATCH_MAX_FRACTION_OF_RESERVE = 0.15


def _money(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def target_board_path(slug: str) -> Path:
    return config.LEAGUES_DIR / slug / "target_board.json"


def load_previous_board(slug: str) -> dict[str, Any] | None:
    path = target_board_path(slug)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        log.warning("target_board ilegible (%s): %s", slug, exc)
        return None


def save_target_board(slug: str, board: dict[str, Any]) -> Path:
    path = target_board_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(board, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _lineup_pct(p: dict[str, Any]) -> float | None:
    ext = p.get("external") or {}
    if ext.get("lineup_prob_ext") is not None:
        try:
            return float(ext["lineup_prob_ext"])
        except (TypeError, ValueError):
            return None
    gw = p.get("gw_lineup_prob")
    if gw is None:
        gw = ext.get("gw_lineup_prob")
    if gw is not None:
        try:
            return float(gw)
        except (TypeError, ValueError):
            return None
    if p.get("lineup_prob") is not None:
        try:
            return float(p["lineup_prob"]) * 100.0
        except (TypeError, ValueError):
            return None
    return None


def _production(p: dict[str, Any]) -> float | None:
    if p.get("production_score") is not None:
        try:
            return float(p["production_score"])
        except (TypeError, ValueError):
            pass
    ext = p.get("external") or {}
    if ext.get("production_score") is not None:
        try:
            return float(ext["production_score"])
        except (TypeError, ValueError):
            return None
    return None


def _ff_avg(p: dict[str, Any]) -> float | None:
    for key in ("ff_mister_avg", "mister_avg", "form"):
        if p.get(key) is not None:
            try:
                return float(p[key])
            except (TypeError, ValueError):
                pass
    ext = p.get("external") or {}
    if ext.get("ff_mister_avg") is not None:
        try:
            return float(ext["ff_mister_avg"])
        except (TypeError, ValueError):
            return None
    return None


def ep_score(p: dict[str, Any]) -> float:
    """Puntaje esperado 0–100: producción + media FF + titularidad."""
    prod = _production(p)
    ff = _ff_avg(p)
    lp = _lineup_pct(p)
    parts: list[tuple[float, float]] = []
    if prod is not None:
        parts.append((0.55, max(0.0, min(100.0, prod))))
    if ff is not None:
        parts.append((0.25, max(0.0, min(100.0, (ff / 8.0) * 100.0))))
    if lp is not None:
        parts.append((0.20, max(0.0, min(100.0, lp))))
    if not parts:
        return 0.0
    wsum = sum(w for w, _ in parts)
    return round(sum(w * v for w, v in parts) / wsum, 1)


def _market_price(p: dict[str, Any]) -> float:
    """Precio de mercado / valor (para wealth y keep)."""
    return _money(p.get("price") or p.get("market_value") or p.get("puja_recomendada"))


def _buy_price(p: dict[str, Any]) -> float:
    """Coste de adquisición (puja / cláusula / precio)."""
    return _money(
        p.get("puja_recomendada") or p.get("clause") or p.get("price") or p.get("market_value")
    )


def _delta_5d(p: dict[str, Any], price_series: dict[str, list[float]] | None) -> float | None:
    if p.get("delta_5d") is not None:
        try:
            return float(p["delta_5d"])
        except (TypeError, ValueError):
            pass
    pid = str(p.get("id") or p.get("player_id") or "")
    series = (price_series or {}).get(pid) or []
    if len(series) < 2:
        return None
    try:
        base = float(series[0])
        cur = float(series[-1])
        if base <= 0:
            return None
        return (cur - base) / base
    except (TypeError, ValueError):
        return None


def _value_note(delta: float | None) -> str:
    if delta is None:
        return "stable"
    if delta >= 0.05:
        return "rising"
    if delta <= -0.05:
        return "falling"
    return "stable"


def _pid(p: dict[str, Any]) -> str:
    return str(p.get("id") or p.get("player_id") or "")


def _avail_ok(p: dict[str, Any]) -> bool:
    avail = (p.get("external") or {}).get("availability") or (
        "injured" if p.get("injury") else "unknown"
    )
    if avail in ("injured", "suspended"):
        return False
    if p.get("gw_out") or (p.get("external") or {}).get("gw_out"):
        return False
    return True


def _normalize_player(
    p: dict[str, Any],
    *,
    owned: bool,
    price_series: dict[str, list[float]] | None,
) -> dict[str, Any] | None:
    pid = _pid(p)
    if not pid:
        return None
    if not _avail_ok(p):
        return None
    market = _market_price(p)
    buy = _buy_price(p)
    if owned:
        # Keep: el slot consume wealth a valor de mercado
        slot_cost = market if market > 0 else 100_000.0
        buy = slot_cost
    else:
        slot_cost = buy if buy > 0 else market
        if slot_cost <= 0:
            return None
    ep = ep_score(p)
    delta = _delta_5d(p, price_series)
    price_m = max(slot_cost / 1_000_000.0, 0.4)
    return {
        "raw": p,
        "player_id": pid,
        "name": p.get("name"),
        "position": p.get("position") or "MF",
        "team": p.get("team"),
        "team_id": str(p.get("team_id") or "") or None,
        "owned": owned,
        "ep_score": ep,
        "price": round(slot_cost, 0),
        "buy_price": round(buy if buy > 0 else slot_cost, 0),
        "market_value": round(market, 0) if market > 0 else round(slot_cost, 0),
        "value_ratio": round(ep / price_m, 2),
        "delta_5d": round(delta, 4) if delta is not None else None,
        "value_note": _value_note(delta),
        "lineup_prob": _lineup_pct(p),
        "production_score": _production(p),
        "ff_mister_avg": _ff_avg(p),
        "on_daily_market": bool(p.get("on_daily_market") or p.get("seller") == "market"),
        "clause": p.get("clause") if p.get("clause_known") else None,
        "sample_thin": bool(p.get("sample_thin")),
        "seller": p.get("seller"),
    }


def _liquidity_floor(wealth: float, balance: float) -> float:
    reserve = float(getattr(config, "PACKAGE_CASH_RESERVE", 8_000_000))
    pct = max(0.05, min(0.10, wealth * 0.08 / max(wealth, 1))) * wealth
    # No exigir más liquidez que el saldo actual
    return min(balance, max(reserve * 0.25, min(reserve, pct)))


def _ideal_counts() -> dict[str, int]:
    ideal = getattr(config, "IDEAL_SQUAD", None) or {"GK": 2, "DF": 5, "MF": 5, "FW": 3}
    return {str(k): int(v) for k, v in ideal.items()}


def _starter_counts() -> dict[str, int]:
    """Once de la plantilla perfecta (11). No confundir con STARTERS_TARGET del diagnóstico."""
    st = getattr(config, "IDEAL_XI", None) or {"GK": 1, "DF": 4, "MF": 3, "FW": 3}
    return {str(k): int(v) for k, v in st.items()}


def _gk_team_key(u: dict[str, Any]) -> str | None:
    """Clave de club para tándem de porteros."""
    tid = str(u.get("team_id") or "").strip()
    if tid and tid not in ("0", "None"):
        return f"id:{tid}"
    team = str(u.get("team") or "").strip()
    if team and team not in ("—", "-", "?"):
        return f"name:{team.lower()}"
    return None


def _pick_gk_tandem(
    gks: list[dict[str, Any]],
    *,
    need_n: int,
    room: float,
    slot_cost,
) -> list[dict[str, Any]]:
    """
    Elige hasta need_n porteros prefiriendo tándem del mismo equipo
    (titular + suplente rotables ante lesión). Si no hay par asequible, fallback.
    """
    if need_n <= 0 or not gks:
        return []
    if need_n == 1:
        affordable = [u for u in gks if slot_cost(u) <= room + 1e-6]
        if not affordable:
            return []
        affordable.sort(
            key=lambda x: (-float(x.get("ep_score") or 0), -float(x.get("value_ratio") or 0), slot_cost(x))
        )
        return [affordable[0]]

    by_team: dict[str, list[dict[str, Any]]] = {}
    for u in gks:
        key = _gk_team_key(u)
        if not key:
            continue
        by_team.setdefault(key, []).append(u)

    best_pair: list[dict[str, Any]] | None = None
    best_score: tuple[float, float, float] | None = None
    for _key, members in by_team.items():
        if len(members) < 2:
            continue
        members = sorted(
            members,
            key=lambda x: (-float(x.get("ep_score") or 0), slot_cost(x)),
        )
        # Probar mejores combinaciones de 2 (top por EP)
        top = members[:6]
        for i, a in enumerate(top):
            for b in top[i + 1 :]:
                cost = slot_cost(a) + slot_cost(b)
                if cost > room + 1e-6:
                    continue
                ep_sum = float(a.get("ep_score") or 0) + float(b.get("ep_score") or 0)
                ep_main = max(float(a.get("ep_score") or 0), float(b.get("ep_score") or 0))
                score = (ep_sum, ep_main, -cost)
                if best_score is None or score > best_score:
                    best_score = score
                    # Titular = mayor EP
                    ordered = sorted(
                        [a, b],
                        key=lambda x: (-float(x.get("ep_score") or 0), slot_cost(x)),
                    )
                    best_pair = ordered

    if best_pair:
        return best_pair[:need_n]

    # Fallback: mejores GK individuales asequibles (equipos distintos)
    picked: list[dict[str, Any]] = []
    rem = room
    pool = sorted(
        gks,
        key=lambda x: (-float(x.get("ep_score") or 0), -float(x.get("value_ratio") or 0), slot_cost(x)),
    )
    for u in pool:
        if len(picked) >= need_n:
            break
        c = slot_cost(u)
        if c > rem + 1e-6:
            continue
        picked.append(u)
        rem -= c
    return picked


def _row_from_norm(
    n: dict[str, Any],
    *,
    slot: str,
    role: str,
    status: str,
    added_at: str | None = None,
) -> dict[str, Any]:
    return {
        "slot": slot,
        "position": n["position"],
        "role": role,
        "player_id": n["player_id"],
        "name": n["name"],
        "team": n.get("team"),
        "team_id": n.get("team_id"),
        "ep_score": n["ep_score"],
        "price": n["price"] if status == "keep" else n.get("buy_price") or n["price"],
        "status": status,
        "delta_5d": n.get("delta_5d"),
        "value_note": n.get("value_note"),
        "value_ratio": n.get("value_ratio"),
        "lineup_prob": n.get("lineup_prob"),
        "on_daily_market": n.get("on_daily_market"),
        "owned": bool(n.get("owned")),
        "gk_tandem": bool(n.get("gk_tandem")),
        "why": (
            f"EP {n['ep_score']:.0f} · {n['price']:,.0f} € · {status}"
            + (" · tándem mismo club" if n.get("gk_tandem") else "")
            + (f" · Δ {n['delta_5d']*100:.0f}%" if n.get("delta_5d") is not None else "")
        ),
        "added_at": added_at or _now_iso(),
    }


def _build_daily_patches(
    structural_needs: list[dict[str, Any]] | None,
    universe: list[dict[str, Any]],
    *,
    balance: float,
    cash_reserved: float,
    owned_ids: set[str],
) -> list[dict[str, Any]]:
    """Parches del mercado de hoy para carencias, sin romper reserva del ideal."""
    residual = max(0.0, balance - cash_reserved)
    if residual < 150_000:
        return []
    max_spend = min(
        residual,
        max(500_000.0, cash_reserved * PATCH_MAX_FRACTION_OF_RESERVE if cash_reserved else 2_000_000.0),
        float(getattr(config, "PACKAGE_SECONDARY_MAX", 2_500_000)),
    )
    needs = [
        n
        for n in (structural_needs or [])
        if n.get("priority") in ("Alta", "Media") and n.get("position")
    ]
    patches: list[dict[str, Any]] = []
    used: set[str] = set()
    for need in needs:
        pos = need.get("position")
        cands = [
            u
            for u in universe
            if u["position"] == pos
            and not u["owned"]
            and u.get("on_daily_market")
            and u["player_id"] not in owned_ids
            and u["player_id"] not in used
            and float(u.get("buy_price") or u["price"]) <= max_spend
            and float(u.get("ep_score") or 0) >= 25
        ]
        cands.sort(
            key=lambda x: (
                -float(x.get("ep_score") or 0),
                float(x.get("buy_price") or x["price"]),
            )
        )
        if not cands:
            continue
        best = cands[0]
        cost = float(best.get("buy_price") or best["price"])
        used.add(best["player_id"])
        patches.append(
            {
                "need": need.get("need"),
                "position": pos,
                "priority": need.get("priority"),
                "player_id": best["player_id"],
                "name": best["name"],
                "price": cost,
                "ep_score": best["ep_score"],
                "max_spend": round(max_spend, 0),
                "why": (
                    f"Parche {pos} hoy · EP {best['ep_score']:.0f} · {cost:,.0f} € "
                    f"(reserva ideal intacta: {cash_reserved:,.0f} €)"
                ),
            }
        )
    return patches[:4]


def _prev_perfect_index(prev: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not prev:
        return out
    for row in prev.get("perfect_squad") or []:
        pid = str(row.get("player_id") or "")
        if pid:
            out[pid] = row
    for bucket in ("keep", "buy", "sell"):
        for row in ((prev.get("moves") or {}).get(bucket) or []):
            pid = str(row.get("player_id") or "")
            if pid and pid not in out:
                out[pid] = row
    # legacy slots
    for slot in prev.get("slots") or []:
        for t in slot.get("targets") or []:
            pid = str(t.get("player_id") or "")
            if pid and pid not in out:
                out[pid] = t
        prim = slot.get("primary_target") or {}
        pid = str(prim.get("player_id") or "")
        if pid and pid not in out:
            out[pid] = prim
    return out


def build_target_board(
    *,
    slug: str,
    structural_needs: list[dict[str, Any]] | None,
    candidates: list[dict[str, Any]],
    balance: float,
    squad: list[dict[str, Any]] | None = None,
    squad_value: float | None = None,
    price_series: dict[str, list[float]] | None = None,
    previous: dict[str, Any] | None = None,
    market_mode: str = "auction",
) -> dict[str, Any]:
    """
    Plantilla perfecta IDEAL_SQUAD (15) desde el universo de liga bajo
    wealth = balance + squad_value. Ownership solo etiqueta keep/buy al final.
    """
    bal = max(0.0, float(balance or 0))
    squad = list(squad or [])
    sval = float(squad_value or 0) or sum(
        _money(p.get("price") or p.get("market_value")) for p in squad
    )
    wealth_total = bal + sval
    floor = _liquidity_floor(wealth_total, bal)
    budget_cap = max(0.0, wealth_total - floor)

    owned_ids = {_pid(p) for p in squad if _pid(p)}
    prev = previous if previous is not None else load_previous_board(slug)
    prev_idx = _prev_perfect_index(prev)

    universe: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in squad:
        n = _normalize_player(p, owned=True, price_series=price_series)
        if not n or n["player_id"] in seen:
            continue
        seen.add(n["player_id"])
        universe.append(n)
    for p in candidates:
        n = _normalize_player(p, owned=_pid(p) in owned_ids, price_series=price_series)
        if not n or n["player_id"] in seen:
            continue
        # sample_thin extremo: aún sirve para rellenar cupos baratos; no filtrar aquí
        seen.add(n["player_id"])
        universe.append(n)

    ideal = _ideal_counts()
    starters_n = _starter_counts()
    perfect: list[dict[str, Any]] = []
    picked_ids: set[str] = set()
    # Selección por EP bajo wealth — ownership solo se etiqueta al final
    spent = 0.0

    def _slot_cost(u: dict[str, Any]) -> float:
        return float(u.get("buy_price") or u.get("price") or 0)

    def _count_pos(pos: str) -> int:
        return len([r for r in perfect if r.get("position") == pos])

    def _cheapest_fill_cost(needs: dict[str, int], exclude: set[str]) -> float:
        """Coste mínimo para completar los cupos restantes (cualquier ownership)."""
        total = 0.0
        used = set(exclude)
        for pos, n in needs.items():
            if n <= 0:
                continue
            pool = sorted(
                [
                    u
                    for u in universe
                    if u["position"] == pos and u["player_id"] not in used
                ],
                key=_slot_cost,
            )
            for u in pool[:n]:
                total += _slot_cost(u)
                used.add(u["player_id"])
        return total

    def _append_pick(u: dict[str, Any], *, pos: str) -> None:
        nonlocal spent
        slot_i = _count_pos(pos) + 1
        starter_slots = int(starters_n.get(pos, 1))
        role = "starter" if slot_i <= starter_slots else "bench"
        status = "keep" if u.get("owned") else "buy"
        perfect.append(
            _row_from_norm(
                u,
                slot=f"{pos}{slot_i}",
                role=role,
                status=status,
                added_at=(prev_idx.get(u["player_id"]) or {}).get("added_at"),
            )
        )
        picked_ids.add(u["player_id"])
        spent += _slot_cost(u)

    # Pass 0: porteros como tándem del mismo club (óptimo ante lesiones)
    gk_need = int(ideal.get("GK", 0))
    if gk_need > 0:
        remaining_after_gk = {
            p: int(ideal.get(p, 0)) - (gk_need if p == "GK" else 0)
            for p in ("GK", "DF", "MF", "FW")
        }
        # Tras coger los GK, remaining GK = 0
        remaining_after_gk["GK"] = 0
        reserve_outfield = _cheapest_fill_cost(remaining_after_gk, picked_ids)
        gk_room = max(0.0, float(budget_cap) - spent - reserve_outfield)
        gk_pool = [u for u in universe if u["position"] == "GK" and u["player_id"] not in picked_ids]
        tandem = _pick_gk_tandem(gk_pool, need_n=gk_need, room=gk_room, slot_cost=_slot_cost)
        tandem_same = (
            len(tandem) >= 2
            and _gk_team_key(tandem[0]) is not None
            and _gk_team_key(tandem[0]) == _gk_team_key(tandem[1])
        )
        for u in tandem:
            u = dict(u)
            u["gk_tandem"] = tandem_same
            _append_pick(u, pos="GK")

    # Pass 1: densidad EP/€ bajo wealth (resto de líneas; GK ya cubiertos)
    picks_target = sum(int(ideal.get(p, 0)) for p in ("GK", "DF", "MF", "FW"))
    density = sorted(
        [u for u in universe if u["position"] != "GK"],
        key=lambda x: (
            -float(x.get("value_ratio") or 0),
            -float(x.get("ep_score") or 0),
            _slot_cost(x),
        ),
    )
    for u in density:
        if len(perfect) >= picks_target:
            break
        pos = u["position"]
        need_n = int(ideal.get(pos, 0))
        if _count_pos(pos) >= need_n:
            continue
        if u["player_id"] in picked_ids:
            continue
        remaining = {
            p: int(ideal.get(p, 0)) - _count_pos(p) for p in ("GK", "DF", "MF", "FW")
        }
        remaining[pos] = max(0, remaining[pos] - 1)
        reserve = _cheapest_fill_cost(remaining, picked_ids | {u["player_id"]})
        room = max(0.0, float(budget_cap) - spent - reserve)
        if _slot_cost(u) > room + 1e-6:
            continue
        _append_pick(u, pos=pos)

    # Completar huecos restantes por EP con margen de relleno
    for pos in ("GK", "DF", "MF", "FW"):
        need_n = int(ideal.get(pos, 0))
        while _count_pos(pos) < need_n:
            remaining = {
                p: int(ideal.get(p, 0)) - _count_pos(p) for p in ("GK", "DF", "MF", "FW")
            }
            remaining[pos] = max(0, remaining[pos] - 1)
            reserve = _cheapest_fill_cost(remaining, picked_ids)
            room = max(0.0, float(budget_cap) - spent - reserve)
            cands = [
                u
                for u in universe
                if u["position"] == pos
                and u["player_id"] not in picked_ids
                and _slot_cost(u) <= room + 1e-6
            ]
            if not cands:
                room2 = max(0.0, float(budget_cap) - spent)
                cands = [
                    u
                    for u in universe
                    if u["position"] == pos
                    and u["player_id"] not in picked_ids
                    and _slot_cost(u) <= room2 + 1e-6
                ]
            if not cands:
                break
            # GK: preferir mismo club que el portero ya elegido
            prefer_team: str | None = None
            if pos == "GK":
                for r in perfect:
                    if r.get("position") == "GK":
                        prefer_team = _gk_team_key(
                            {
                                "team_id": r.get("team_id"),
                                "team": r.get("team"),
                            }
                        )
                        if prefer_team:
                            break
            cands.sort(
                key=lambda x: (
                    0 if (prefer_team and _gk_team_key(x) == prefer_team) else 1,
                    -float(x.get("ep_score") or 0),
                    -float(x.get("value_ratio") or 0),
                    _slot_cost(x),
                )
            )
            pick = dict(cands[0])
            if pos == "GK" and prefer_team and _gk_team_key(pick) == prefer_team:
                pick["gk_tandem"] = True
                # Marcar el GK ya presente como tándem
                for r in perfect:
                    if r.get("position") == "GK":
                        r["gk_tandem"] = True
                        why = str(r.get("why") or "")
                        if "tándem mismo club" not in why:
                            r["why"] = why + " · tándem mismo club"
            _append_pick(pick, pos=pos)

    def _drop_lowest_ep() -> bool:
        nonlocal spent, perfect, picked_ids
        if not perfect:
            return False
        # Evitar romper tándem GK si hay otros candidatos a recortar
        non_gk = [r for r in perfect if r.get("position") != "GK"]
        pool = non_gk if non_gk else perfect
        victim = min(
            pool,
            key=lambda r: (float(r.get("ep_score") or 0), -float(r.get("price") or 0)),
        )
        pid = str(victim.get("player_id") or "")
        cost = float(victim.get("price") or 0)
        perfect = [x for x in perfect if str(x.get("player_id")) != pid]
        picked_ids.discard(pid)
        spent = max(0.0, spent - cost)
        return True

    while spent > budget_cap + 1e-6 and _drop_lowest_ep():
        pass

    # Pass 2: upgrades — sustituir por mejor EP si el delta cabe (GK solo dentro del mismo club)
    upgraded = True
    guard = 0
    while upgraded and guard < 60:
        upgraded = False
        guard += 1
        avail = [u for u in universe if u["player_id"] not in picked_ids]
        avail.sort(
            key=lambda x: (
                -float(x.get("ep_score") or 0),
                -float(x.get("value_ratio") or 0),
                _slot_cost(x),
            )
        )
        for u in avail:
            pos = u["position"]
            need_n = int(ideal.get(pos, 0))
            cost_new = _slot_cost(u)
            rows_pos = [r for r in perfect if r.get("position") == pos]
            room = max(0.0, float(budget_cap) - spent)
            if len(rows_pos) < need_n:
                if cost_new <= room + 1e-6:
                    pick = dict(u)
                    if pos == "GK":
                        existing_keys = {
                            _gk_team_key({"team_id": r.get("team_id"), "team": r.get("team")})
                            for r in rows_pos
                        }
                        existing_keys.discard(None)
                        if existing_keys and _gk_team_key(u) not in existing_keys:
                            continue
                        if existing_keys and _gk_team_key(u) in existing_keys:
                            pick["gk_tandem"] = True
                    _append_pick(pick, pos=pos)
                    upgraded = True
                    break
                continue
            victim = min(
                rows_pos,
                key=lambda r: (float(r.get("ep_score") or 0), -float(r.get("price") or 0)),
            )
            if float(u.get("ep_score") or 0) <= float(victim.get("ep_score") or 0) + 0.5:
                continue
            # GK: no romper tándem — solo upgrade dentro del mismo club
            if pos == "GK":
                vkey = _gk_team_key({"team_id": victim.get("team_id"), "team": victim.get("team")})
                ukey = _gk_team_key(u)
                other_gk = [r for r in rows_pos if str(r.get("player_id")) != str(victim.get("player_id"))]
                if other_gk:
                    okey = _gk_team_key(
                        {"team_id": other_gk[0].get("team_id"), "team": other_gk[0].get("team")}
                    )
                    if okey and ukey != okey:
                        continue
                elif vkey and ukey != vkey:
                    continue
            delta = cost_new - float(victim.get("price") or 0)
            if delta > room + 1e-6:
                continue
            pid = str(victim.get("player_id") or "")
            perfect = [x for x in perfect if str(x.get("player_id")) != pid]
            picked_ids.discard(pid)
            spent = max(0.0, spent - float(victim.get("price") or 0))
            pick = dict(u)
            if pos == "GK":
                pick["gk_tandem"] = True
            _append_pick(pick, pos=pos)
            upgraded = True
            break

    while spent > budget_cap + 1e-6 and _drop_lowest_ep():
        pass

    # Relabel keep/buy según ownership real (post-selección)
    for r in perfect:
        pid = str(r.get("player_id") or "")
        is_keep = pid in owned_ids
        r["status"] = "keep" if is_keep else "buy"
        r["owned"] = is_keep
        ep = float(r.get("ep_score") or 0)
        price = float(r.get("price") or 0)
        delta = r.get("delta_5d")
        r["why"] = (
            f"EP {ep:.0f} · {price:,.0f} € · {r['status']}"
            + (f" · Δ {delta * 100:.0f}%" if delta is not None else "")
        )

    # Reasignar slots/roles por EP
    perfect_sorted: list[dict[str, Any]] = []
    for pos in ("GK", "DF", "MF", "FW"):
        rows_pos = [r for r in perfect if r.get("position") == pos]
        rows_pos.sort(key=lambda r: -float(r.get("ep_score") or 0))
        starter_slots = int(starters_n.get(pos, 1))
        for i, r in enumerate(rows_pos, start=1):
            r["slot"] = f"{pos}{i}"
            r["role"] = "starter" if i <= starter_slots else "bench"
            perfect_sorted.append(r)
    perfect = perfect_sorted

    keep_rows = [r for r in perfect if r.get("status") == "keep"]
    buy_rows = [r for r in perfect if r.get("status") == "buy"]
    value_kept = sum(float(r.get("price") or 0) for r in keep_rows)
    cost_buys = sum(float(r.get("price") or 0) for r in buy_rows)
    net_buys = max(0.0, cost_buys)
    spent = value_kept + cost_buys

    # Sells: owned fuera del ideal, priorizar bajo EP / Δ caida
    sell_cands: list[dict[str, Any]] = []
    for u in universe:
        if not u["owned"] or u["player_id"] in picked_ids:
            continue
        delta = u.get("delta_5d")
        sell_cands.append(
            {
                "player_id": u["player_id"],
                "name": u["name"],
                "position": u["position"],
                "ep_score": u["ep_score"],
                "price": u["price"],
                "delta_5d": delta,
                "value_note": u.get("value_note"),
                "why": (
                    f"Fuera del ideal · EP {u['ep_score']:.0f} · libera {u['price']:,.0f} €"
                    + (f" · Δ {delta*100:.0f}%" if delta is not None else "")
                ),
            }
        )
    sell_cands.sort(
        key=lambda x: (
            0 if x.get("value_note") == "falling" else 1,
            float(x.get("ep_score") or 0),
            -float(x.get("price") or 0),
        )
    )
    # Suficientes ventas para cubrir net_buys - balance (si hace falta)
    shortfall = max(0.0, net_buys - bal)
    sell_rows: list[dict[str, Any]] = []
    freed = 0.0
    for s in sell_cands:
        if shortfall <= 0 and len(sell_rows) >= 3:
            break
        if shortfall > 0 and freed >= shortfall and len(sell_rows) >= 2:
            break
        sell_rows.append(s)
        freed += float(s.get("price") or 0)
        if shortfall <= 0 and len(sell_rows) >= 5:
            break
    if shortfall <= 0:
        # Aun así sugerir hasta 3 ventas claras de bajo EP
        sell_rows = sell_cands[:3]

    cash_reserved = round(net_buys, 0)
    residual_after = round(max(0.0, bal - cash_reserved), 0)
    funded = bal + freed >= net_buys

    daily_patches = _build_daily_patches(
        structural_needs,
        universe,
        balance=bal,
        cash_reserved=cash_reserved,
        owned_ids=owned_ids,
    )

    # Compat: primary_targets = buys del ideal (para action plan)
    primary_targets = [
        {
            "player_id": r.get("player_id"),
            "name": r.get("name"),
            "need": "perfect_squad",
            "position": r.get("position"),
            "price": r.get("price"),
            "ep_score": r.get("ep_score"),
            "status": "on_daily" if r.get("on_daily_market") else "watching",
            "role": r.get("role"),
        }
        for r in buy_rows
    ]

    # Compat slots ligeros para parches / UI legacy
    slots = []
    for patch in daily_patches:
        slots.append(
            {
                "need": patch.get("need") or f"patch_{patch.get('position')}",
                "position": patch.get("position"),
                "priority": patch.get("priority") or "Media",
                "reason": patch.get("why"),
                "primary_target": {
                    "player_id": patch.get("player_id"),
                    "name": patch.get("name"),
                    "price": patch.get("price"),
                    "ep_score": patch.get("ep_score"),
                    "status": "on_daily",
                    "tier": "realistic",
                    "afford_now": True,
                    "why": patch.get("why"),
                },
                "targets": [],
                "patch_policy": {
                    "allow": residual_after >= 200_000,
                    "max_spend": patch.get("max_spend"),
                    "note": "Parche diario sin romper reserva del ideal",
                },
                "budget_envelope": {
                    "cash_reserve_for_slot": 0,
                    "min_price": None,
                    "max_price": patch.get("max_spend"),
                },
            }
        )

    # Dropped del ideal previo
    current_ids = {str(r.get("player_id")) for r in perfect if r.get("player_id")}
    dropped: list[dict[str, Any]] = []
    for pid, old in prev_idx.items():
        if pid in current_ids or pid in owned_ids:
            continue
        miss = int(old.get("miss_days") or 0) + 1
        if miss > MAX_DROPPED_DAYS:
            continue
        row = dict(old)
        row["status"] = "dropped"
        row["miss_days"] = miss
        dropped.append(row)

    patch_allow = residual_after >= 200_000 and bool(daily_patches)
    board = {
        "generated_at": _now_iso(),
        "league_slug": slug,
        "market_mode": market_mode,
        "balance": bal,
        "squad_value": sval,
        "wealth": {
            "balance": bal,
            "squad_value": sval,
            "total": round(wealth_total, 0),
            "liquidity_floor": round(floor, 0),
            "budget_cap": round(budget_cap, 0),
        },
        "perfect_squad": perfect,
        "moves": {
            "keep": keep_rows,
            "buy": buy_rows,
            "sell": sell_rows,
        },
        "totals": {
            "ep_sum": round(sum(float(r.get("ep_score") or 0) for r in perfect), 1),
            "cost_sum": round(spent, 0),
            "net_buys": round(net_buys, 0),
            "sell_to_fund": round(freed if shortfall > 0 else sum(float(s.get("price") or 0) for s in sell_rows[:3]), 0),
            "funded": funded,
            "slots_filled": len(perfect),
            "slots_target": sum(ideal.values()),
        },
        "daily_patches": daily_patches,
        "cash_reserved": cash_reserved,
        "residual_after_reserve": residual_after,
        "primary_targets": primary_targets,
        "slots": slots,
        "dropped": dropped[:12],
        "patch_policy": {
            "allow": patch_allow,
            "max_spend": round(
                min(residual_after, float(getattr(config, "PACKAGE_SECONDARY_MAX", 2_500_000))),
                0,
            ),
        },
        "summary": {
            "slots": len(perfect),
            "keep": len(keep_rows),
            "buy": len(buy_rows),
            "sell": len(sell_rows),
            "patches": len(daily_patches),
            "on_daily": sum(1 for r in buy_rows if r.get("on_daily_market")),
            "cash_reserved": cash_reserved,
            "ep_sum": round(sum(float(r.get("ep_score") or 0) for r in perfect), 1),
        },
    }
    return board


def funding_plan_from_board(
    board: dict[str, Any] | None,
    *,
    balance: float | None = None,
) -> dict[str, Any]:
    """Funding = coste de buys del ideal (cash_reserved)."""
    bal = max(0.0, float(balance if balance is not None else (board or {}).get("balance") or 0))
    buys = list(((board or {}).get("moves") or {}).get("buy") or [])
    if not buys:
        buys = [
            {
                "position": t.get("position"),
                "price": t.get("price"),
                "player_id": t.get("player_id"),
                "name": t.get("name"),
                "ep_score": t.get("ep_score"),
            }
            for t in (board or {}).get("primary_targets") or []
        ]
    gaps: list[dict[str, Any]] = []
    for b in buys:
        cost = _money(b.get("price"))
        if cost <= 0:
            continue
        gaps.append(
            {
                "position": b.get("position"),
                "need": "perfect_buy",
                "cost": cost,
                "label": f"Ideal: {b.get('name') or b.get('position')}",
                "no_affordable_candidate": cost > bal,
                "primary_player_id": b.get("player_id"),
                "primary_name": b.get("name"),
                "ep_score": b.get("ep_score"),
                "status": "buy",
            }
        )
    gaps.sort(key=lambda g: -float(g.get("cost") or 0))
    selected = gaps[:5]
    funding_target = float((board or {}).get("cash_reserved") or sum(float(g["cost"]) for g in selected))
    funding_shortfall = max(0.0, funding_target - bal)
    cheapest = min((float(g["cost"]) for g in gaps), default=None)
    cash_tight = funding_shortfall > 0
    return {
        "funding_target": funding_target,
        "funding_shortfall": funding_shortfall,
        "cash_tight": cash_tight,
        "gap_costs": selected,
        "all_gap_costs": gaps,
        "positions": [g.get("position") for g in selected if g.get("position")],
        "cheapest_need": cheapest,
        "primary_targets": list((board or {}).get("primary_targets") or []),
        "cash_reserved": float((board or {}).get("cash_reserved") or funding_target),
        "from_target_board": True,
        "wealth": (board or {}).get("wealth"),
        "totals": (board or {}).get("totals"),
    }


def board_objective_ids(board: dict[str, Any] | None) -> set[str]:
    ids: set[str] = set()
    for r in (board or {}).get("perfect_squad") or []:
        if r.get("player_id") and r.get("status") == "buy":
            ids.add(str(r["player_id"]))
    for p in (board or {}).get("daily_patches") or []:
        if p.get("player_id"):
            ids.add(str(p["player_id"]))
    for t in (board or {}).get("primary_targets") or []:
        if t.get("player_id"):
            ids.add(str(t["player_id"]))
    return ids


def board_primary_ids(board: dict[str, Any] | None) -> set[str]:
    """Buys del ideal (prioridad alta) + patches no."""
    ids = {
        str(t["player_id"])
        for t in (board or {}).get("primary_targets") or []
        if t.get("player_id")
    }
    for r in ((board or {}).get("moves") or {}).get("buy") or []:
        if r.get("player_id"):
            ids.add(str(r["player_id"]))
    return ids


def max_patch_spend(board: dict[str, Any] | None) -> float:
    if not board:
        return float(getattr(config, "PACKAGE_SECONDARY_MAX", 2_500_000))
    pp = board.get("patch_policy") or {}
    if pp.get("max_spend") is not None:
        return float(pp["max_spend"])
    residual = float(board.get("residual_after_reserve") or 0)
    return min(residual, float(getattr(config, "PACKAGE_SECONDARY_MAX", 2_500_000)))


def patches_allowed(board: dict[str, Any] | None) -> bool:
    if not board:
        return True
    pp = board.get("patch_policy") or {}
    if "allow" in pp:
        return bool(pp.get("allow"))
    return float(board.get("residual_after_reserve") or 0) >= 200_000


__all__ = [
    "build_target_board",
    "funding_plan_from_board",
    "load_previous_board",
    "save_target_board",
    "target_board_path",
    "board_objective_ids",
    "board_primary_ids",
    "max_patch_spend",
    "patches_allowed",
    "ep_score",
]
