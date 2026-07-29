"""
Plantilla perfecta diaria bajo presupuesto total (saldo + valor de plantilla).

Dos vistas: operable (oportunidad / EP€) y aspiracional (máx EP).
El action plan / funding usan solo el ideal operable.
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
                v = float(p[key])
                if v > 0:
                    return v
            except (TypeError, ValueError):
                pass
    ext = p.get("external") or {}
    if ext.get("ff_mister_avg") is not None:
        try:
            v = float(ext["ff_mister_avg"])
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None
    return None


def _ff_points(p: dict[str, Any]) -> float | None:
    for key in ("ff_mister_points", "points"):
        if p.get(key) is not None:
            try:
                v = float(p[key])
                if v > 0:
                    return v
            except (TypeError, ValueError):
                pass
    ext = p.get("external") or {}
    if ext.get("ff_mister_points") is not None:
        try:
            v = float(ext["ff_mister_points"])
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None
    return None


def _ff_apps(p: dict[str, Any]) -> int:
    for key in ("ff_apps", "apps"):
        if p.get(key) is not None:
            try:
                return max(0, int(p[key]))
            except (TypeError, ValueError):
                pass
    ext = p.get("external") or {}
    if ext.get("ff_apps") is not None:
        try:
            return max(0, int(ext["ff_apps"]))
        except (TypeError, ValueError):
            pass
    return 0


def _hist_floors(position: str, *, avg_scale: float = 8.0) -> tuple[float, float]:
    """Umbrales Mister Mixto (media / pts temporada), escalados si RPG."""
    pos = (position or "MF").upper()
    avg_floor = float((getattr(config, "MISTER_HIST_AVG_FLOOR", None) or {}).get(pos, 5.5))
    pts_floor = float((getattr(config, "MISTER_HIST_PTS_FLOOR", None) or {}).get(pos, 200))
    scale = float(avg_scale) if avg_scale and avg_scale > 0 else 8.0
    # Mister Mixto ~8; Fantasy RPG ~16
    factor = scale / 8.0
    return avg_floor * factor, pts_floor * factor


def _hist_quality(p: dict[str, Any]) -> tuple[float | None, bool]:
    """
    Calidad histórica 0–100 vs umbrales Mister por posición.
    Fiable (True) solo si media ≥ suelo posicional (con muestra mínima).
    """
    pos = str(p.get("position") or "MF")
    avg = _ff_avg(p)
    pts = _ff_points(p)
    apps = _ff_apps(p)
    # Detectar escala RPG vs Mixto: medias > 10 suelen ser RPG
    avg_scale = 16.0 if (avg is not None and avg > 10) else 8.0
    avg_floor, pts_floor = _hist_floors(pos, avg_scale=avg_scale)

    if avg is None and pts is None:
        return None, False

    # Media es la señal principal; pts corrigen temporadas cortas/largas
    avg_part = None
    if avg is not None and avg_floor > 0:
        # 0 en 0 pts/PJ · 50 en 70% del suelo · 70 en el suelo · 100 en 130%+
        ratio = float(avg) / avg_floor
        if ratio <= 0.7:
            avg_part = (ratio / 0.7) * 50.0
        elif ratio <= 1.0:
            avg_part = 50.0 + ((ratio - 0.7) / 0.3) * 20.0
        else:
            avg_part = 70.0 + min(30.0, ((ratio - 1.0) / 0.3) * 30.0)

    pts_part = None
    if pts is not None and pts_floor > 0:
        pr = float(pts) / pts_floor
        if pr <= 0.7:
            pts_part = (pr / 0.7) * 50.0
        elif pr <= 1.0:
            pts_part = 50.0 + ((pr - 0.7) / 0.3) * 20.0
        else:
            pts_part = 70.0 + min(30.0, ((pr - 1.0) / 0.3) * 30.0)

    if avg_part is not None and pts_part is not None:
        # Con pocos partidos, la media miente menos que el total
        w_avg = 0.75 if apps < 20 else 0.60
        hist = w_avg * avg_part + (1.0 - w_avg) * pts_part
    elif avg_part is not None:
        hist = avg_part
    else:
        hist = float(pts_part or 0.0)

    # Muestra corta: no declarar “fiable” aunque la media pinte bien
    reliable = bool(
        avg is not None
        and float(avg) >= avg_floor * 0.98
        and apps >= 15
    )
    # 1–7 PJ: media engañosa (p.ej. 8.0 en 1 partido)
    if apps < 8:
        hist *= 0.40
    elif apps < 15:
        hist *= 0.70
    return round(max(0.0, min(100.0, hist)), 1), reliable


def ep_score(p: dict[str, Any]) -> float:
    """Puntaje esperado 0–100 para plantilla ideal.

    Histórico = media/puntos Mister vs umbrales por posición (no el production_score 0–100).
    Titularidad pesa; % bajo castiga. Chollos con media 3 no parecen cracks.
    """
    hist, _reliable = _hist_quality(p)
    lp = _lineup_pct(p)
    parts: list[tuple[float, float]] = []
    if hist is not None:
        parts.append((0.55, hist))
    if lp is not None:
        # Sin histórico Mister, el % titular no basta para un EP alto
        lp_w = 0.45 if hist is not None else 0.35
        parts.append((lp_w, max(0.0, min(100.0, lp))))
    if not parts:
        prod = _production(p)
        if prod is not None:
            return round(max(0.0, min(40.0, float(prod) * 0.35)), 1)
        return 0.0
    wsum = sum(w for w, _ in parts)
    raw = sum(w * v for w, v in parts) / wsum
    if hist is None:
        # Desconocido en FF: techo bajo (no rellenar ideal solo con % alineación)
        raw = min(raw, 38.0)
    if lp is not None:
        if lp < 40:
            raw *= 0.50
        elif lp < 55:
            raw *= 0.72
        elif lp < 70:
            raw *= 0.88
    if hist is not None and hist < 50:
        raw = min(raw, 42.0)
    return round(raw, 1)


def _starter_fitness(row: dict[str, Any]) -> tuple:
    """Prioridad para badge Titular: solo cuenta quien tiene % alto."""
    lp = row.get("lineup_prob")
    try:
        lp_f = float(lp) if lp is not None else None
    except (TypeError, ValueError):
        lp_f = None
    # band: 3 titular claro · 2 regular · 1 flojo · 0 sin dato / no juega
    if lp_f is None:
        band = 0
    elif lp_f >= 70:
        band = 3
    elif lp_f >= 55:
        band = 2
    elif lp_f >= 40:
        band = 1
    else:
        band = 0
    return (band, float(row.get("ep_score") or 0))


def _assign_roles(rows_pos: list[dict[str, Any]], starter_slots: int) -> None:
    """Titular solo si % alineación ≥ 70. Si no hay dato o es bajo → banquillo."""
    min_lp = float(getattr(config, "LINEUP_PROB_TITULAR", 0.70)) * 100.0
    ordered = sorted(rows_pos, key=_starter_fitness, reverse=True)
    starters_left = int(starter_slots)
    for i, r in enumerate(ordered, start=1):
        r["slot"] = f"{r.get('position')}{i}"  # provisional; caller reescribe por pos
        lp = r.get("lineup_prob")
        try:
            lp_f = float(lp) if lp is not None else None
        except (TypeError, ValueError):
            lp_f = None
        if starters_left > 0 and lp_f is not None and lp_f >= min_lp:
            r["role"] = "starter"
            starters_left -= 1
        else:
            r["role"] = "bench"
    # Reordenar: titulares primero, luego por fitness
    ordered.sort(
        key=lambda r: (0 if r.get("role") == "starter" else 1, -_starter_fitness(r)[0], -_starter_fitness(r)[1])
    )
    for i, r in enumerate(ordered, start=1):
        r["slot"] = f"{r.get('position')}{i}"
    rows_pos[:] = ordered


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
    seller = p.get("seller")
    clause_known = bool(p.get("clause_known"))
    if owned:
        # Keep: el slot consume wealth a valor de mercado
        slot_cost = market if market > 0 else 100_000.0
        buy = slot_cost
    else:
        # Libre/mercado: coste = valor listado (no puja +8%); rival con cláusula = buy
        is_opp = str(seller or "").lower() in ("free", "market") or bool(
            p.get("on_daily_market")
        )
        if is_opp:
            slot_cost = market if market > 0 else buy
            buy = slot_cost
        else:
            slot_cost = buy if buy > 0 else market
        if slot_cost <= 0:
            return None
    ep = ep_score(p)
    hist, hist_ok = _hist_quality(p)
    delta = _delta_5d(p, price_series)
    price_m = max(slot_cost / 1_000_000.0, 0.4)
    apps = _ff_apps(p)
    sample_thin = bool(p.get("sample_thin")) or (0 < apps < 8)
    ext = p.get("external") or {}
    clause_val = p.get("clause") if clause_known else None
    return {
        "raw": p,
        "player_id": pid,
        "name": p.get("name"),
        "position": p.get("position") or "MF",
        "team": p.get("team"),
        "team_id": str(p.get("team_id") or "") or None,
        "owned": owned,
        "ep_score": ep,
        "hist_quality": hist,
        "hist_ok": hist_ok,
        "ff_mister_avg": _ff_avg(p),
        "ff_mister_points": _ff_points(p),
        "ff_apps": apps,
        "price": round(slot_cost, 0),
        "buy_price": round(buy if buy > 0 else slot_cost, 0),
        "market_value": round(market, 0) if market > 0 else round(slot_cost, 0),
        "value_ratio": round(ep / price_m, 2),
        "delta_5d": round(delta, 4) if delta is not None else None,
        "value_note": _value_note(delta),
        "lineup_prob": _lineup_pct(p),
        "gw_starter": bool(p.get("gw_starter") or ext.get("gw_starter")),
        "gw_lineup_prob": p.get("gw_lineup_prob") if p.get("gw_lineup_prob") is not None else ext.get("gw_lineup_prob"),
        "production_score": _production(p),
        "on_daily_market": bool(p.get("on_daily_market") or str(seller or "").lower() == "market"),
        "clause": clause_val,
        "clause_known": clause_known,
        "sample_thin": sample_thin,
        "seller": seller,
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


def _parse_formation_label(formation: str | None) -> dict[str, int]:
    """'4-3-3' / '1-4-4-2' / '4-2-3-1' → cupos GK/DF/MF/FW (suma 11)."""
    default = _starter_counts()
    if not formation:
        return default
    parts = [p.strip() for p in str(formation).replace("–", "-").split("-") if p.strip()]
    nums: list[int] = []
    for p in parts:
        try:
            nums.append(int(p))
        except ValueError:
            return default
    if len(nums) == 4 and sum(nums) == 11:
        return {"GK": nums[0], "DF": nums[1], "MF": nums[2], "FW": nums[3]}
    if len(nums) == 3 and sum(nums) == 10:
        return {"GK": 1, "DF": nums[0], "MF": nums[1], "FW": nums[2]}
    # 4-2-3-1 estilo: DF-MF_def-MF_ata-FW → DF + (MF_def+MF_ata) + FW
    if len(nums) == 4 and sum(nums) == 10:
        return {"GK": 1, "DF": nums[0], "MF": nums[1] + nums[2], "FW": nums[3]}
    return default


def _formation_label(xi: dict[str, int]) -> str:
    """Etiqueta tipo fútbol sin el 1 del GK: 4-3-3."""
    return f"{int(xi.get('DF', 0))}-{int(xi.get('MF', 0))}-{int(xi.get('FW', 0))}"


def _ideal_for_xi(xi: dict[str, int]) -> dict[str, int]:
    """Plantilla 15: once + GK2 + 3 plazas de profundidad (prioriza DF/MF)."""
    ideal = {p: int(xi.get(p, 0)) for p in ("GK", "DF", "MF", "FW")}
    ideal["GK"] = max(int(ideal.get("GK", 1)), 1) + 1
    remaining = max(0, 15 - sum(ideal.values()))
    # Reparto de banquillo: DF, MF, FW (y otra vuelta si hace falta)
    order = ("DF", "MF", "FW", "DF", "MF", "FW", "GK")
    i = 0
    while remaining > 0 and i < 40:
        pos = order[i % len(order)]
        ideal[pos] = int(ideal.get(pos, 0)) + 1
        remaining -= 1
        i += 1
    return ideal


def _bench_min_points() -> float:
    return float(getattr(config, "IDEAL_BENCH_MIN_POINTS", 100))


def _clause_premium(u: dict[str, Any]) -> float:
    """Ratio cláusula/mercado; 1.0 si no hay cláusula rival o es keep."""
    if u.get("owned"):
        return 1.0
    market = float(u.get("market_value") or 0)
    clause = u.get("clause")
    buy = float(u.get("buy_price") or u.get("price") or 0)
    try:
        clause_f = float(clause) if clause is not None else None
    except (TypeError, ValueError):
        clause_f = None
    if market <= 0:
        return 1.0
    if clause_f is not None and clause_f > 0:
        return clause_f / market
    if buy > market * 1.02:
        return buy / market
    return 1.0


def _is_opportunity_buy(u: dict[str, Any]) -> bool:
    """Libre, mercado diario o keep: oportunidad vs cláusula rival."""
    if u.get("owned"):
        return True
    seller = str(u.get("seller") or "").lower()
    if seller in ("free", "market") or u.get("on_daily_market"):
        return True
    # Sin dueño rival / sin cláusula conocida → trato oportunidad
    if not u.get("clause_known") and seller in ("", "none", "null"):
        return True
    if u.get("clause") is None and seller in ("free", "market", ""):
        return True
    return seller in ("free", "market")


def _has_lineup_signal(u: dict[str, Any]) -> bool:
    """Señal de once: LP ≥ 70, gw_starter o gw_lineup_prob ≥ 70."""
    min_lp = float(getattr(config, "LINEUP_PROB_TITULAR", 0.70)) * 100.0
    lp = u.get("lineup_prob")
    try:
        if lp is not None and float(lp) >= min_lp:
            return True
    except (TypeError, ValueError):
        pass
    if u.get("gw_starter"):
        return True
    gw = u.get("gw_lineup_prob")
    try:
        if gw is not None and float(gw) >= min_lp:
            return True
    except (TypeError, ValueError):
        pass
    return False


def _has_starter_quality(u: dict[str, Any]) -> bool:
    """Histórico suficiente: hist_ok o hist_quality ≥ umbral (excluye techo EP basura / sin FF)."""
    if u.get("hist_ok"):
        return True
    hist = u.get("hist_quality")
    min_hist = float(getattr(config, "IDEAL_STARTER_HIST_MIN", 35.0))
    try:
        if hist is not None and float(hist) >= min_hist:
            return True
    except (TypeError, ValueError):
        pass
    return False


def _is_starter_eligible(u: dict[str, Any]) -> bool:
    """Titular del ideal: señal de once (≥70%) + calidad hist; nunca solo hist_ok ni sample_thin."""
    if u.get("sample_thin"):
        return False
    if not _has_lineup_signal(u):
        return False
    return _has_starter_quality(u)


def _is_bench_eligible(u: dict[str, Any]) -> bool:
    """Banquillo del ideal (DF/MF/FW): historial Mister ≥ IDEAL_BENCH_MIN_POINTS.

    El GK suplente del tándem es excepción: no usa este filtro.
    """
    if (u.get("position") or "") == "GK":
        return True  # elegibilidad real del GK2 se resuelve por tándem / club
    pts = u.get("ff_mister_points")
    try:
        return pts is not None and float(pts) >= _bench_min_points()
    except (TypeError, ValueError):
        return False


def _is_outfield_bench_eligible(u: dict[str, Any]) -> bool:
    """Banquillo de campo: ≥ IDEAL_BENCH_MIN_POINTS (nunca GK)."""
    if (u.get("position") or "") == "GK":
        return False
    return _is_bench_eligible(u)


def _bench_slot_needs(ideal: dict[str, int], xi: dict[str, int]) -> dict[str, int]:
    return {p: max(0, int(ideal.get(p, 0)) - int(xi.get(p, 0))) for p in ("GK", "DF", "MF", "FW")}


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
        "ff_mister_points": n.get("ff_mister_points"),
        "ff_mister_avg": n.get("ff_mister_avg"),
        "hist_ok": bool(n.get("hist_ok")),
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


def _slot_cost_u(u: dict[str, Any]) -> float:
    return float(u.get("buy_price") or u.get("price") or 0)


def _best_opportunity_ep(
    universe: list[dict[str, Any]],
    *,
    pos: str,
    exclude: set[str],
    eligible,
) -> float:
    best = 0.0
    for u in universe:
        if u["position"] != pos or u["player_id"] in exclude:
            continue
        if not eligible(u):
            continue
        if not _is_opportunity_buy(u):
            continue
        best = max(best, float(u.get("ep_score") or 0))
    return best


def _accept_operable_candidate(
    u: dict[str, Any],
    *,
    universe: list[dict[str, Any]],
    exclude: set[str],
    eligible,
) -> bool:
    """Rechaza cláusulas caras si hay oportunidad con EP competitivo."""
    if _is_opportunity_buy(u):
        return True
    soft = float(getattr(config, "IDEAL_CLAUSE_PREMIUM_SOFT", 1.25))
    band = float(getattr(config, "IDEAL_EP_TIE_BAND", 5.0))
    if _clause_premium(u) <= soft:
        return True
    best_opp = _best_opportunity_ep(
        universe, pos=str(u.get("position") or "MF"), exclude=exclude, eligible=eligible
    )
    if best_opp <= 0:
        return True
    ep = float(u.get("ep_score") or 0)
    # Exigir superar claramente la mejor oportunidad
    return ep > best_opp + band


def _operable_upgrade_ok(u: dict[str, Any], victim: dict[str, Any]) -> bool:
    """Upgrade operable: cláusula cara solo si ΔEP/M€ justifica la prima."""
    if _is_opportunity_buy(u):
        return True
    soft = float(getattr(config, "IDEAL_CLAUSE_PREMIUM_SOFT", 1.25))
    if _clause_premium(u) <= soft:
        return True
    delta_ep = float(u.get("ep_score") or 0) - float(victim.get("ep_score") or 0)
    delta_cost = _slot_cost_u(u) - float(victim.get("price") or 0)
    if delta_cost <= 0:
        return True
    min_ep_m = float(getattr(config, "IDEAL_CLAUSE_MIN_EP_PER_M", 3.0))
    ep_per_m = delta_ep / (delta_cost / 1_000_000.0)
    return ep_per_m >= min_ep_m


def _assemble_perfect_squad(
    universe: list[dict[str, Any]],
    *,
    budget_cap: float,
    mode: str,
    owned_ids: set[str],
    prev_idx: dict[str, dict[str, Any]],
    starters_n: dict[str, int] | None = None,
    ideal: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """
    Ensambla IDEAL_SQUAD bajo budget_cap.
    mode=operable → oportunidad / EP€; aspirational → máx EP.
    """
    operable = mode == "operable"
    ideal = {str(k): int(v) for k, v in (ideal or _ideal_counts()).items()}
    starters_n = {str(k): int(v) for k, v in (starters_n or _starter_counts()).items()}
    bench_n = _bench_slot_needs(ideal, starters_n)
    perfect: list[dict[str, Any]] = []
    picked_ids: set[str] = set()
    spent = 0.0
    tie_band = float(getattr(config, "IDEAL_EP_TIE_BAND", 5.0))

    def _slot_cost(u: dict[str, Any]) -> float:
        return _slot_cost_u(u)

    def _count_pos(pos: str, *, role: str | None = None) -> int:
        rows = [r for r in perfect if r.get("position") == pos]
        if role:
            rows = [r for r in rows if r.get("role") == role]
        return len(rows)

    def _cheapest_eligible_cost(needs: dict[str, int], exclude: set[str], *, eligible) -> float:
        total = 0.0
        used = set(exclude)
        for pos, n in needs.items():
            if n <= 0:
                continue
            pool = sorted(
                [
                    u
                    for u in universe
                    if u["position"] == pos and u["player_id"] not in used and eligible(u)
                ],
                key=_slot_cost,
            )
            for u in pool[:n]:
                total += _slot_cost(u)
                used.add(u["player_id"])
        return total

    def _append_pick(u: dict[str, Any], *, pos: str, role: str) -> None:
        nonlocal spent
        slot_i = _count_pos(pos) + 1
        status = "keep" if u.get("owned") else "buy"
        row = _row_from_norm(
            u,
            slot=f"{pos}{slot_i}",
            role=role,
            status=status,
            added_at=(prev_idx.get(u["player_id"]) or {}).get("added_at"),
        )
        perfect.append(row)
        picked_ids.add(u["player_id"])
        spent += _slot_cost(u)

    def _starter_sort_key(u: dict[str, Any]) -> tuple:
        ep = float(u.get("ep_score") or 0)
        vr = float(u.get("value_ratio") or 0)
        opp = 0 if _is_opportunity_buy(u) else 1
        prem_bad = 1 if _clause_premium(u) > float(getattr(config, "IDEAL_CLAUSE_PREMIUM_SOFT", 1.25)) else 0
        if operable:
            # Oportunidad: EP + valor; preferir libre/mercado
            return (-ep, opp, prem_bad, -vr, _slot_cost(u))
        return (-ep, _slot_cost(u), -vr)

    def _bench_sort_key(u: dict[str, Any]) -> tuple:
        return (
            -float(u.get("value_ratio") or 0),
            -float(u.get("ep_score") or 0),
            _slot_cost(u),
        )

    def _sort_key(u: dict[str, Any]) -> tuple:
        return _bench_sort_key(u)

    def _passes_operable(u: dict[str, Any], eligible) -> bool:
        if not operable:
            return True
        return _accept_operable_candidate(
            u, universe=universe, exclude=picked_ids, eligible=eligible
        )

    starter_pool = [u for u in universe if _is_starter_eligible(u)]
    bench_pool = [u for u in universe if _is_outfield_bench_eligible(u)]

    def _reserve_bench_cost(needs: dict[str, int], exclude: set[str]) -> float:
        total = 0.0
        used = set(exclude)
        for pos, n in needs.items():
            if n <= 0:
                continue
            if pos == "GK":
                pool = sorted(
                    [
                        u
                        for u in universe
                        if u["position"] == "GK" and u["player_id"] not in used
                    ],
                    key=_slot_cost,
                )
            else:
                pool = sorted(
                    [
                        u
                        for u in universe
                        if u["position"] == pos
                        and u["player_id"] not in used
                        and _is_outfield_bench_eligible(u)
                    ],
                    key=_slot_cost,
                )
            for u in pool[:n]:
                total += _slot_cost(u)
                used.add(u["player_id"])
        return total

    gk_xi = int(starters_n.get("GK", 1))
    gk_bench_need = int(bench_n.get("GK", 0))
    if gk_xi > 0:
        remaining_xi = {p: int(starters_n.get(p, 0)) for p in ("GK", "DF", "MF", "FW")}
        remaining_xi["GK"] = max(0, remaining_xi["GK"] - gk_xi)
        reserve_xi = _cheapest_eligible_cost(
            remaining_xi, picked_ids, eligible=_is_starter_eligible
        )
        reserve_bench = _reserve_bench_cost(bench_n, picked_ids)
        gk_room = max(0.0, float(budget_cap) - spent - reserve_xi - reserve_bench)

        by_team: dict[str, list[dict[str, Any]]] = {}
        for u in universe:
            if u["position"] != "GK":
                continue
            key = _gk_team_key(u)
            if key:
                by_team.setdefault(key, []).append(u)

        best: tuple | None = None
        best_pair: tuple[dict[str, Any], dict[str, Any]] | None = None
        for _key, members in by_team.items():
            starters = [
                m
                for m in members
                if _is_starter_eligible(m) and _passes_operable(m, _is_starter_eligible)
            ]
            for a in starters:
                for b in members:
                    if a["player_id"] == b["player_id"]:
                        continue
                    cost = _slot_cost(a) + (_slot_cost(b) if gk_bench_need else 0)
                    if cost > gk_room + 1e-6:
                        continue
                    if operable:
                        score = (
                            float(a.get("ep_score") or 0),
                            0 if _is_opportunity_buy(a) else 1,
                            -_clause_premium(a),
                            float(a.get("value_ratio") or 0),
                            -_slot_cost(b),
                            float(b.get("ep_score") or 0),
                            -cost,
                        )
                    else:
                        score = (
                            float(a.get("ep_score") or 0),
                            -_slot_cost(b),
                            float(b.get("ep_score") or 0),
                            -cost,
                        )
                    if best is None or score > best:
                        best = score
                        best_pair = (a, b)

        if best_pair:
            a, b = best_pair
            a = dict(a)
            a["gk_tandem"] = True
            _append_pick(a, pos="GK", role="starter")
            if gk_bench_need > 0:
                b = dict(b)
                b["gk_tandem"] = True
                _append_pick(b, pos="GK", role="bench")
        else:
            cands = sorted(
                [
                    u
                    for u in starter_pool
                    if u["position"] == "GK"
                    and u["player_id"] not in picked_ids
                    and _passes_operable(u, _is_starter_eligible)
                ],
                key=_starter_sort_key,
            )
            for u in cands:
                rem_xi = {
                    p: int(starters_n.get(p, 0)) - _count_pos(p, role="starter")
                    for p in ("GK", "DF", "MF", "FW")
                }
                rem_xi["GK"] = max(0, rem_xi["GK"] - 1)
                rem_bench = {
                    p: int(bench_n.get(p, 0)) - _count_pos(p, role="bench")
                    for p in ("GK", "DF", "MF", "FW")
                }
                room = max(
                    0.0,
                    float(budget_cap)
                    - spent
                    - _cheapest_eligible_cost(
                        rem_xi, picked_ids | {u["player_id"]}, eligible=_is_starter_eligible
                    )
                    - _reserve_bench_cost(rem_bench, picked_ids | {u["player_id"]}),
                )
                if _slot_cost(u) <= room + 1e-6:
                    _append_pick(u, pos="GK", role="starter")
                    break

    for pos in ("GK", "DF", "MF", "FW"):
        need = int(starters_n.get(pos, 0))
        while _count_pos(pos, role="starter") < need:
            rem_xi = {
                p: int(starters_n.get(p, 0)) - _count_pos(p, role="starter")
                for p in ("GK", "DF", "MF", "FW")
            }
            rem_xi[pos] = max(0, rem_xi[pos] - 1)
            rem_bench = {
                p: int(bench_n.get(p, 0)) - _count_pos(p, role="bench")
                for p in ("GK", "DF", "MF", "FW")
            }
            reserve = _cheapest_eligible_cost(
                rem_xi, picked_ids, eligible=_is_starter_eligible
            ) + _reserve_bench_cost(rem_bench, picked_ids)
            room = max(0.0, float(budget_cap) - spent - reserve)
            cands = sorted(
                [
                    u
                    for u in starter_pool
                    if u["position"] == pos
                    and u["player_id"] not in picked_ids
                    and _slot_cost(u) <= room + 1e-6
                    and _passes_operable(u, _is_starter_eligible)
                ],
                key=_slot_cost,
            )
            if not cands:
                # Fallback: permitir cualquier elegible asequible (completar once)
                room2 = max(
                    0.0,
                    float(budget_cap)
                    - spent
                    - _cheapest_eligible_cost(rem_xi, picked_ids, eligible=_is_starter_eligible),
                )
                cands = sorted(
                    [
                        u
                        for u in starter_pool
                        if u["position"] == pos
                        and u["player_id"] not in picked_ids
                        and _slot_cost(u) <= room2 + 1e-6
                        and _passes_operable(u, _is_starter_eligible)
                    ],
                    key=_slot_cost,
                )
            if not cands:
                # Último recurso: sin filtro oportunidad (mejor incompleto que vacío)
                cands = sorted(
                    [
                        u
                        for u in starter_pool
                        if u["position"] == pos
                        and u["player_id"] not in picked_ids
                        and _slot_cost(u) <= room + 1e-6
                    ],
                    key=_slot_cost,
                )
            if not cands:
                # Completar once: solo presupuesto restante (reserva irreal si calidad es cara)
                room3 = max(0.0, float(budget_cap) - spent)
                cands = sorted(
                    [
                        u
                        for u in starter_pool
                        if u["position"] == pos
                        and u["player_id"] not in picked_ids
                        and _slot_cost(u) <= room3 + 1e-6
                    ],
                    key=_slot_cost,
                )
            if not cands:
                break
            _append_pick(cands[0], pos=pos, role="starter")

    def _upgrade(role: str, eligible, *, reserve_bench: bool = False) -> None:
        nonlocal spent, perfect, picked_ids
        need_map = starters_n if role == "starter" else bench_n
        sort_fn = _starter_sort_key if role == "starter" else _bench_sort_key
        upgraded = True
        guard = 0
        while upgraded and guard < 80:
            upgraded = False
            guard += 1
            avail = sorted(
                [
                    u
                    for u in universe
                    if u["player_id"] not in picked_ids
                    and eligible(u)
                    and (role != "starter" or _passes_operable(u, eligible))
                ],
                key=sort_fn,
            )
            for u in avail:
                pos = u["position"]
                need = int(need_map.get(pos, 0))
                rows = [
                    r
                    for r in perfect
                    if r.get("position") == pos and r.get("role") == role
                ]
                cost_new = _slot_cost(u)
                rem_bench = {
                    p: int(bench_n.get(p, 0)) - _count_pos(p, role="bench")
                    for p in ("GK", "DF", "MF", "FW")
                }
                hold = (
                    _reserve_bench_cost(rem_bench, picked_ids | {u["player_id"]})
                    if reserve_bench
                    else 0.0
                )
                room = max(0.0, float(budget_cap) - spent - hold)
                if len(rows) < need:
                    if cost_new <= room + 1e-6:
                        if pos == "GK":
                            other = [r for r in perfect if r.get("position") == "GK"]
                            if other:
                                okey = _gk_team_key(
                                    {
                                        "team_id": other[0].get("team_id"),
                                        "team": other[0].get("team"),
                                    }
                                )
                                if okey and _gk_team_key(u) != okey:
                                    continue
                        _append_pick(u, pos=pos, role=role)
                        upgraded = True
                        break
                    continue
                if not rows:
                    continue
                if operable and role == "starter":
                    # Preferir víctima por peor valor/EP (no solo peor EP)
                    victim = min(
                        rows,
                        key=lambda r: (
                            float(r.get("ep_score") or 0),
                            float(r.get("value_ratio") or 0),
                            -float(r.get("price") or 0),
                        ),
                    )
                else:
                    victim = min(
                        rows,
                        key=lambda r: (float(r.get("ep_score") or 0), -float(r.get("price") or 0)),
                    )
                ep_u = float(u.get("ep_score") or 0)
                ep_v = float(victim.get("ep_score") or 0)
                if operable and role == "starter":
                    # Empate cercano: solo upgrade si oportunidad mejor o EP claramente superior
                    if ep_u <= ep_v + 0.5:
                        continue
                    if ep_u <= ep_v + tie_band and not _is_opportunity_buy(u):
                        # No pagar cláusula por mejora marginal
                        if _is_opportunity_buy(
                            {
                                **u,
                                "owned": victim.get("owned"),
                                "seller": "keep" if victim.get("owned") else victim.get("seller"),
                            }
                        ) or victim.get("owned"):
                            # víctima keep/oportunidad: exigir más margen
                            if ep_u <= ep_v + tie_band:
                                continue
                    if not _operable_upgrade_ok(u, victim):
                        continue
                else:
                    if ep_u <= ep_v + 0.5:
                        continue
                if pos == "GK":
                    other = [
                        r
                        for r in perfect
                        if r.get("position") == "GK"
                        and str(r.get("player_id")) != str(victim.get("player_id"))
                    ]
                    if other:
                        okey = _gk_team_key(
                            {"team_id": other[0].get("team_id"), "team": other[0].get("team")}
                        )
                        if okey and _gk_team_key(u) != okey:
                            continue
                delta = cost_new - float(victim.get("price") or 0)
                if delta > room + 1e-6:
                    continue
                # Operable: tras el swap, el resto de cupos deben seguir rellenables
                if operable and role == "starter":
                    rem_xi = {
                        p: int(starters_n.get(p, 0)) - _count_pos(p, role="starter")
                        for p in ("GK", "DF", "MF", "FW")
                    }
                    # victim se quita, u se añade → cupos de pos se mantienen
                    trial_spent = spent - float(victim.get("price") or 0) + cost_new
                    trial_ids = (picked_ids | {u["player_id"]}) - {str(victim.get("player_id"))}
                    rem_bench2 = {
                        p: int(bench_n.get(p, 0)) - _count_pos(p, role="bench")
                        for p in ("GK", "DF", "MF", "FW")
                    }
                    need_rest = _cheapest_eligible_cost(
                        {k: v for k, v in rem_xi.items() if v > 0},
                        trial_ids,
                        eligible=_is_starter_eligible,
                    ) + _reserve_bench_cost(rem_bench2, trial_ids)
                    if trial_spent + need_rest > float(budget_cap) + 1e-6:
                        continue
                pid = str(victim.get("player_id") or "")
                perfect = [x for x in perfect if str(x.get("player_id")) != pid]
                picked_ids.discard(pid)
                spent = max(0.0, spent - float(victim.get("price") or 0))
                _append_pick(u, pos=pos, role=role)
                upgraded = True
                break

    _upgrade("starter", _is_starter_eligible, reserve_bench=True)

    for pos in ("GK", "DF", "MF", "FW"):
        need = int(bench_n.get(pos, 0))
        prefer_team = None
        if pos == "GK":
            for r in perfect:
                if r.get("position") == "GK" and r.get("role") == "starter":
                    prefer_team = _gk_team_key(
                        {"team_id": r.get("team_id"), "team": r.get("team")}
                    )
                    break
        while _count_pos(pos, role="bench") < need:
            rem_bench = {
                p: int(bench_n.get(p, 0)) - _count_pos(p, role="bench")
                for p in ("GK", "DF", "MF", "FW")
            }
            rem_bench[pos] = max(0, rem_bench[pos] - 1)
            reserve = _reserve_bench_cost(rem_bench, picked_ids)
            room = max(0.0, float(budget_cap) - spent - reserve)
            if pos == "GK":
                cands = [
                    u
                    for u in universe
                    if u["position"] == "GK"
                    and u["player_id"] not in picked_ids
                    and _slot_cost(u) <= room + 1e-6
                    and (not prefer_team or _gk_team_key(u) == prefer_team)
                ]
                if not cands and prefer_team:
                    room2 = max(0.0, float(budget_cap) - spent)
                    cands = [
                        u
                        for u in universe
                        if u["position"] == "GK"
                        and u["player_id"] not in picked_ids
                        and _slot_cost(u) <= room2 + 1e-6
                        and _gk_team_key(u) == prefer_team
                    ]
                if not cands:
                    room2 = max(0.0, float(budget_cap) - spent)
                    cands = [
                        u
                        for u in universe
                        if u["position"] == "GK"
                        and u["player_id"] not in picked_ids
                        and _slot_cost(u) <= room2 + 1e-6
                    ]
            else:
                cands = [
                    u
                    for u in bench_pool
                    if u["position"] == pos
                    and u["player_id"] not in picked_ids
                    and _slot_cost(u) <= room + 1e-6
                ]
                if not cands:
                    room2 = max(0.0, float(budget_cap) - spent)
                    cands = [
                        u
                        for u in bench_pool
                        if u["position"] == pos
                        and u["player_id"] not in picked_ids
                        and _slot_cost(u) <= room2 + 1e-6
                    ]
            if not cands:
                break
            cands.sort(
                key=lambda x: (
                    0 if (prefer_team and _gk_team_key(x) == prefer_team) else 1,
                    *_sort_key(x),
                )
            )
            pick = dict(cands[0])
            if pos == "GK" and prefer_team and _gk_team_key(pick) == prefer_team:
                pick["gk_tandem"] = True
                for r in perfect:
                    if r.get("position") == "GK":
                        r["gk_tandem"] = True
            _append_pick(pick, pos=pos, role="bench")

    _upgrade("bench", _is_bench_eligible)
    _upgrade("starter", _is_starter_eligible, reserve_bench=False)

    while spent > budget_cap + 1e-6:
        bench_rows = [
            r for r in perfect if r.get("role") == "bench" and r.get("position") != "GK"
        ]
        pool = bench_rows or [r for r in perfect if r.get("role") == "bench"] or perfect
        if not pool:
            break
        victim = min(
            pool,
            key=lambda r: (float(r.get("ep_score") or 0), -float(r.get("price") or 0)),
        )
        pid = str(victim.get("player_id") or "")
        spent = max(0.0, spent - float(victim.get("price") or 0))
        perfect = [x for x in perfect if str(x.get("player_id")) != pid]
        picked_ids.discard(pid)

    gk_rows = [r for r in perfect if r.get("position") == "GK"]
    tandem_ok = False
    if len(gk_rows) >= 2:
        keys = {_gk_team_key(r) for r in gk_rows}
        keys.discard(None)
        tandem_ok = len(keys) == 1 and len(gk_rows) >= 2
    for r in gk_rows:
        r["gk_tandem"] = tandem_ok

    for r in perfect:
        pid = str(r.get("player_id") or "")
        is_keep = pid in owned_ids
        r["status"] = "keep" if is_keep else "buy"
        r["owned"] = is_keep
        ep = float(r.get("ep_score") or 0)
        price = float(r.get("price") or 0)
        delta = r.get("delta_5d")
        tandem = " · tándem mismo club" if r.get("gk_tandem") else ""
        r["why"] = (
            f"EP {ep:.0f} · {price:,.0f} € · {r['status']} · {r.get('role')}"
            + tandem
            + (f" · Δ {delta * 100:.0f}%" if delta is not None else "")
        )

    perfect_sorted: list[dict[str, Any]] = []
    for pos in ("GK", "DF", "MF", "FW"):
        rows_pos = [r for r in perfect if r.get("position") == pos]
        rows_pos.sort(
            key=lambda r: (0 if r.get("role") == "starter" else 1, -float(r.get("ep_score") or 0))
        )
        for i, r in enumerate(rows_pos, start=1):
            r["slot"] = f"{pos}{i}"
            perfect_sorted.append(r)
    return perfect_sorted


def _squad_totals(
    perfect: list[dict[str, Any]],
    *,
    ideal: dict[str, int],
    bal: float,
    starters_n: dict[str, int] | None = None,
) -> dict[str, Any]:
    keep_rows = [r for r in perfect if r.get("status") == "keep"]
    buy_rows = [r for r in perfect if r.get("status") == "buy"]
    value_kept = sum(float(r.get("price") or 0) for r in keep_rows)
    cost_buys = sum(float(r.get("price") or 0) for r in buy_rows)
    spent = value_kept + cost_buys
    st = {str(k): int(v) for k, v in (starters_n or _starter_counts()).items()}
    bench_n = _bench_slot_needs(ideal, st)
    return {
        "ep_sum": round(sum(float(r.get("ep_score") or 0) for r in perfect), 1),
        "ep_sum_starters": round(
            sum(float(r.get("ep_score") or 0) for r in perfect if r.get("role") == "starter"),
            1,
        ),
        "cost_sum": round(spent, 0),
        "net_buys": round(cost_buys, 0),
        "slots_filled": len(perfect),
        "slots_target": sum(ideal.values()),
        "starters": sum(1 for r in perfect if r.get("role") == "starter"),
        "bench": sum(1 for r in perfect if r.get("role") == "bench"),
        "keep": len(keep_rows),
        "buy": len(buy_rows),
        "incomplete": (
            len(perfect) < sum(ideal.values())
            or sum(1 for r in perfect if r.get("role") == "starter") < sum(st.values())
            or sum(1 for r in perfect if r.get("role") == "bench") < sum(bench_n.values())
        ),
    }


def _pick_best_formation_squad(
    universe: list[dict[str, Any]],
    *,
    budget_cap: float,
    mode: str,
    owned_ids: set[str],
    prev_idx: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], str, dict[str, int], dict[str, int], list[dict[str, Any]]]:
    """
    Prueba IDEAL_FORMATIONS y elige la de mayor Σ EP del once (completo preferido).
    Devuelve (perfect, label, starters_n, ideal, trials).
    """
    raw = getattr(config, "IDEAL_FORMATIONS", None) or ("4-3-3",)
    labels = [str(x).strip() for x in raw if str(x).strip()]
    if not labels:
        labels = ["4-3-3"]

    trials: list[dict[str, Any]] = []
    best_squad: list[dict[str, Any]] | None = None
    best_label = labels[0]
    best_xi = _parse_formation_label(best_label)
    best_ideal = _ideal_for_xi(best_xi)
    best_key: tuple | None = None

    seen_shapes: set[tuple[int, int, int, int]] = set()
    for label in labels:
        xi = _parse_formation_label(label)
        shape_key = (
            int(xi.get("GK", 1)),
            int(xi.get("DF", 0)),
            int(xi.get("MF", 0)),
            int(xi.get("FW", 0)),
        )
        if shape_key in seen_shapes:
            continue
        seen_shapes.add(shape_key)
        if sum(shape_key) != 11:
            continue
        ideal = _ideal_for_xi(xi)
        display = _formation_label(xi)
        squad = _assemble_perfect_squad(
            universe,
            budget_cap=budget_cap,
            mode=mode,
            owned_ids=owned_ids,
            prev_idx=prev_idx,
            starters_n=xi,
            ideal=ideal,
        )
        starters = [r for r in squad if r.get("role") == "starter"]
        ep_xi = sum(float(r.get("ep_score") or 0) for r in starters)
        ep_all = sum(float(r.get("ep_score") or 0) for r in squad)
        cost = sum(float(r.get("price") or 0) for r in squad)
        n_st = len(starters)
        target_st = sum(xi.values())
        complete = n_st >= target_st
        trial = {
            "formation": display,
            "formation_raw": label,
            "shape": dict(xi),
            "ep_sum_starters": round(ep_xi, 1),
            "ep_sum": round(ep_all, 1),
            "cost_sum": round(cost, 0),
            "starters": n_st,
            "starters_target": target_st,
            "complete": complete,
        }
        trials.append(trial)
        # Preferir once completo; luego máx EP titulares; luego EP total; luego menos coste
        key = (
            1 if complete else 0,
            ep_xi,
            ep_all,
            -cost,
        )
        if best_key is None or key > best_key:
            best_key = key
            best_squad = squad
            best_label = display
            best_xi = xi
            best_ideal = ideal

    trials.sort(
        key=lambda t: (
            1 if t.get("complete") else 0,
            float(t.get("ep_sum_starters") or 0),
            float(t.get("ep_sum") or 0),
        ),
        reverse=True,
    )
    return best_squad or [], best_label, best_xi, best_ideal, trials


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
    Plantilla perfecta dual: operable (oportunidad) + aspiracional (máx EP).
    Funding / primary_targets salen solo del ideal operable.
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
        seen.add(n["player_id"])
        universe.append(n)

    ideal = _ideal_counts()
    starters_n = _starter_counts()
    bench_n = _bench_slot_needs(ideal, starters_n)

    perfect, form_op, starters_n, ideal, trials_op = _pick_best_formation_squad(
        universe,
        budget_cap=budget_cap,
        mode="operable",
        owned_ids=owned_ids,
        prev_idx=prev_idx,
    )
    perfect_asp, form_asp, starters_asp, ideal_asp, trials_asp = _pick_best_formation_squad(
        universe,
        budget_cap=budget_cap,
        mode="aspirational",
        owned_ids=owned_ids,
        prev_idx=prev_idx,
    )
    bench_n = _bench_slot_needs(ideal, starters_n)

    keep_rows = [r for r in perfect if r.get("status") == "keep"]
    buy_rows = [r for r in perfect if r.get("status") == "buy"]
    cost_buys = sum(float(r.get("price") or 0) for r in buy_rows)
    net_buys = max(0.0, cost_buys)
    value_kept = sum(float(r.get("price") or 0) for r in keep_rows)
    spent = value_kept + cost_buys

    picked_ids = {str(r.get("player_id")) for r in perfect if r.get("player_id")}
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
    # Rotación sugerida (ideal completo); shortfall de funding del DÍA más abajo
    sell_rows = list(sell_cands)
    ideal_buy_cost = round(net_buys, 0)
    funded_ideal = bal + sum(float(s.get("price") or 0) for s in sell_cands) >= net_buys

    primary_targets = _select_daily_primary_targets(buy_rows, balance=bal)
    # Enriquecer flags
    primary_targets = [
        {
            "player_id": r.get("player_id"),
            "name": r.get("name"),
            "need": "perfect_squad_daily",
            "position": r.get("position"),
            "price": r.get("price"),
            "ep_score": r.get("ep_score"),
            "status": "on_daily" if r.get("on_daily_market") else "affordable",
            "role": r.get("role"),
            "on_daily_market": bool(r.get("on_daily_market")),
        }
        for r in primary_targets
    ]
    cash_reserved = round(sum(float(t.get("price") or 0) for t in primary_targets), 0)
    residual_after = round(max(0.0, bal - cash_reserved), 0)
    shortfall = max(0.0, cash_reserved - bal)
    freed_for_fund = 0.0
    for s in sell_cands:
        if freed_for_fund >= shortfall:
            break
        freed_for_fund += float(s.get("price") or 0)
    funded = bal + freed_for_fund >= cash_reserved

    daily_patches = _build_daily_patches(
        structural_needs,
        universe,
        balance=bal,
        cash_reserved=cash_reserved,
        owned_ids=owned_ids,
    )

    operable_ids = {str(r.get("player_id")) for r in perfect if r.get("player_id")}
    aspirational_only = [
        {
            "player_id": r.get("player_id"),
            "name": r.get("name"),
            "position": r.get("position"),
            "price": r.get("price"),
            "ep_score": r.get("ep_score"),
            "role": r.get("role"),
            "status": r.get("status"),
            "need": "perfect_squad_aspirational",
        }
        for r in perfect_asp
        if r.get("status") == "buy" and str(r.get("player_id")) not in operable_ids
    ]

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
                    "note": "Parche diario sin romper reserva de objetivos de hoy",
                },
                "budget_envelope": {
                    "cash_reserve_for_slot": 0,
                    "min_price": None,
                    "max_price": patch.get("max_spend"),
                },
            }
        )

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
    op_totals = _squad_totals(perfect, ideal=ideal, bal=bal, starters_n=starters_n)
    asp_totals = _squad_totals(perfect_asp, ideal=ideal_asp, bal=bal, starters_n=starters_asp)
    asp_buy = [r for r in perfect_asp if r.get("status") == "buy"]
    asp_keep = [r for r in perfect_asp if r.get("status") == "keep"]

    board = {
        "generated_at": _now_iso(),
        "league_slug": slug,
        "market_mode": market_mode,
        "mode_default": "operable",
        "formation": form_op,
        "formation_aspirational": form_asp,
        "formation_shape": dict(starters_n),
        "formation_trials": trials_op[:8],
        "formation_trials_aspirational": trials_asp[:8],
        "balance": bal,
        "squad_value": sval,
        "wealth": {
            "balance": bal,
            "squad_value": sval,
            "total": round(wealth_total, 0),
            "liquidity_floor": round(floor, 0),
            "budget_cap": round(budget_cap, 0),
        },
        "budget_operable": {
            "budget_cap": round(budget_cap, 0),
            "note": "wealth − liquidez; prioriza oportunidad (EP/€, libres/mercado)",
        },
        "ideal_buy_cost": ideal_buy_cost,
        "perfect_squad": perfect,
        "perfect_squad_aspirational": perfect_asp,
        "moves": {
            "keep": keep_rows,
            "buy": buy_rows,
            "sell": sell_rows,
        },
        "totals": {
            **{k: op_totals[k] for k in (
                "ep_sum", "ep_sum_starters", "cost_sum", "net_buys",
                "slots_filled", "slots_target",
            )},
            "sell_to_fund": round(freed_for_fund if shortfall > 0 else 0.0, 0),
            "funded": funded,
            "funded_ideal": funded_ideal,
            "formation": form_op,
            "ideal_buy_cost": ideal_buy_cost,
            "daily_primary_count": len(primary_targets),
        },
        "totals_aspirational": {
            "ep_sum": asp_totals["ep_sum"],
            "ep_sum_starters": asp_totals["ep_sum_starters"],
            "cost_sum": asp_totals["cost_sum"],
            "net_buys": asp_totals["net_buys"],
            "slots_filled": asp_totals["slots_filled"],
            "slots_target": asp_totals["slots_target"],
            "funded": None,
            "formation": form_asp,
        },
        "daily_patches": daily_patches,
        "cash_reserved": cash_reserved,
        "residual_after_reserve": residual_after,
        "primary_targets": primary_targets,
        "aspirational_targets": aspirational_only,
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
            "starters": op_totals["starters"],
            "bench": op_totals["bench"],
            "starters_target": sum(starters_n.values()),
            "bench_target": sum(bench_n.values()),
            "incomplete": op_totals["incomplete"],
            "keep": op_totals["keep"],
            "buy": op_totals["buy"],
            "sell": len(sell_rows),
            "patches": len(daily_patches),
            "on_daily": sum(1 for r in buy_rows if r.get("on_daily_market")),
            "cash_reserved": cash_reserved,
            "ep_sum": op_totals["ep_sum"],
            "ep_sum_starters": op_totals["ep_sum_starters"],
            "mode": "operable",
            "formation": form_op,
            "xi_rule": f"formación {form_op} · titulares ≥70% + hist · oportunidad EP/€",
            "bench_min_points": int(_bench_min_points()),
        },
        "summary_aspirational": {
            "slots": len(perfect_asp),
            "starters": asp_totals["starters"],
            "bench": asp_totals["bench"],
            "incomplete": asp_totals["incomplete"],
            "keep": len(asp_keep),
            "buy": len(asp_buy),
            "ep_sum": asp_totals["ep_sum"],
            "ep_sum_starters": asp_totals["ep_sum_starters"],
            "mode": "aspirational",
            "formation": form_asp,
            "xi_rule": f"formación {form_asp} · titulares ≥70% + hist · máx Σ EP",
            "bench_min_points": int(_bench_min_points()),
        },
    }
    return board

def _select_daily_primary_targets(
    buy_rows: list[dict[str, Any]],
    *,
    balance: float,
) -> list[dict[str, Any]]:
    """
    Objetivos del día para cola/funding: mercado diario (máx 2) + buys asequibles
    con el saldo actual (sin exigir vender casi toda la plantilla).
    """
    bal = max(0.0, float(balance or 0))

    def _sort_key(r: dict[str, Any]) -> tuple:
        return (
            0 if r.get("role") == "starter" else 1,
            -float(r.get("ep_score") or 0),
            float(r.get("price") or 0),
        )

    on_daily = sorted(
        [r for r in buy_rows if r.get("on_daily_market")],
        key=_sort_key,
    )
    affordable = sorted(
        [
            r
            for r in buy_rows
            if not r.get("on_daily_market") and float(r.get("price") or 0) <= bal + 1e-6
        ],
        key=_sort_key,
    )

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    spent = 0.0

    # Hasta 2 del mercado de hoy (pueden requerir ventas si superan saldo)
    max_daily = int(getattr(config, "IDEAL_DAILY_MARKET_PRIMARIES", 2))
    for r in on_daily[: max(0, max_daily)]:
        pid = str(r.get("player_id") or "")
        if not pid or pid in seen:
            continue
        selected.append(r)
        seen.add(pid)
        spent += float(r.get("price") or 0)

    # Relleno con asequibles que quepan en el saldo restante
    room = max(0.0, bal - min(spent, bal))
    for r in affordable:
        pid = str(r.get("player_id") or "")
        if not pid or pid in seen:
            continue
        price = float(r.get("price") or 0)
        if price <= room + 1e-6:
            selected.append(r)
            seen.add(pid)
            room -= price

    return selected


def funding_plan_from_board(
    board: dict[str, Any] | None,
    *,
    balance: float | None = None,
) -> dict[str, Any]:
    """Funding del día = primary_targets operables (mercado/asequibles), no el rebuild completo."""
    bal = max(0.0, float(balance if balance is not None else (board or {}).get("balance") or 0))
    daily = list((board or {}).get("primary_targets") or [])
    if not daily:
        # Compat: si el board antiguo no separó daily, derivar de moves.buy
        buys = list(((board or {}).get("moves") or {}).get("buy") or [])
        daily = _select_daily_primary_targets(buys, balance=bal)

    gaps: list[dict[str, Any]] = []
    for b in daily:
        cost = _money(b.get("price"))
        if cost <= 0:
            continue
        gaps.append(
            {
                "position": b.get("position"),
                "need": "perfect_buy_daily",
                "cost": cost,
                "label": f"Hoy: {b.get('name') or b.get('position')}",
                "no_affordable_candidate": cost > bal,
                "primary_player_id": b.get("player_id"),
                "primary_name": b.get("name"),
                "ep_score": b.get("ep_score"),
                "status": b.get("status") or "buy",
                "on_daily_market": bool(b.get("on_daily_market")),
            }
        )
    gaps.sort(
        key=lambda g: (
            0 if g.get("on_daily_market") else 1,
            -float(g.get("ep_score") or 0),
            -float(g.get("cost") or 0),
        )
    )

    # Ideal completo (info): no mueve cash_reserved del día
    ideal_gaps: list[dict[str, Any]] = []
    for b in ((board or {}).get("moves") or {}).get("buy") or []:
        cost = _money(b.get("price"))
        if cost <= 0:
            continue
        ideal_gaps.append(
            {
                "position": b.get("position"),
                "need": "perfect_buy",
                "cost": cost,
                "label": f"Ideal: {b.get('name') or b.get('position')}",
                "primary_player_id": b.get("player_id"),
                "primary_name": b.get("name"),
                "ep_score": b.get("ep_score"),
            }
        )
    ideal_gaps.sort(key=lambda g: -float(g.get("cost") or 0))

    funding_target = float(
        (board or {}).get("cash_reserved")
        if (board or {}).get("cash_reserved") is not None
        else sum(float(g["cost"]) for g in gaps)
    )
    funding_shortfall = max(0.0, funding_target - bal)
    cheapest = min((float(g["cost"]) for g in gaps), default=None)
    return {
        "funding_target": funding_target,
        "funding_shortfall": funding_shortfall,
        "cash_tight": funding_shortfall > 0,
        "gap_costs": gaps[:5],
        "all_gap_costs": gaps,
        "ideal_gap_costs": ideal_gaps[:8],
        "ideal_buy_cost": float((board or {}).get("ideal_buy_cost") or sum(g["cost"] for g in ideal_gaps)),
        "positions": [g.get("position") for g in gaps[:5] if g.get("position")],
        "cheapest_need": cheapest,
        "primary_targets": daily,
        "cash_reserved": funding_target,
        "from_target_board": True,
        "wealth": (board or {}).get("wealth"),
        "totals": (board or {}).get("totals"),
        "formation": (board or {}).get("formation"),
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
    """Solo primary_targets del día (no todos los buys del ideal)."""
    return {
        str(t["player_id"])
        for t in (board or {}).get("primary_targets") or []
        if t.get("player_id")
    }


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
