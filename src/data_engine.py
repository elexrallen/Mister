"""
Mister Fantasy Advisor — Motor de datos (data_engine.py)

Pipeline batch multi-fuente:
  1) Mister Fantasy (API o mock) → estado de la liga privada
  2) Snapshots diarios en public/data/history/ → tendencias de mercado
  3) API-Football o seed local → rendimiento multi-temporada
  4) Algoritmos → carencias, oportunidades, libres TOP, recomendaciones

Salida: public/data/latest_data.json (+ snapshot del día).
"""

from __future__ import annotations

import json
import logging
import math
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests

import config
from mister_client import fetch_live_league, enrich_players_with_clauses
from external_data import enrich_players_with_external, enrich_players_with_ff_production
from fotmob_service import enrich_players_with_fotmob
from competitive_actions import (
    annotate_market_budget_risk,
    budget_fit,
    build_rival_upgrade_targets,
    build_sell_opportunities,
    detect_competition_phase,
    detect_points_phase,
    estimate_gap_funding,
    finalize_action_plan,
    other_gaps_min_cost,
    rival_demand_for_position,
    wait_risk,
)
from squad_analyzer import (
    analyze_squad,
    assess_market_coverage,
    merge_structural_into_diagnosis,
    structural_market_boost,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("data_engine")


# ---------------------------------------------------------------------------
# Utilidades I/O
# ---------------------------------------------------------------------------

def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def money(n: float | int) -> int:
    return int(round(n))


# ---------------------------------------------------------------------------
# Capa 1 — Mister Fantasy (ajax live + mock)
# ---------------------------------------------------------------------------

def fetch_mister_league() -> tuple[dict[str, Any], str]:
    """
    Intenta leer Mister con cookie JWT (`token`) + header `x-auth`.
    Si no hay credenciales o falla → mock local.
    """
    if config.USE_MISTER_MOCK:
        log.info("Sin MISTER_TOKEN/MISTER_COOKIE → mock (%s)", config.MOCK_DATA_PATH.name)
        return load_json(config.MOCK_DATA_PATH), "mock"

    try:
        log.info("Conectando a Mister (/ajax/*) con sesión...")
        live = fetch_live_league()
        if not live or not all(k in live for k in ("me", "market", "rivals", "pool_top")):
            log.warning("Sesión OK parcial o shape incompleto → fallback a mock")
            return load_json(config.MOCK_DATA_PATH), "mock"
        meta = live.get("_live_meta", {})
        log.info("Mister live OK — meta=%s", meta)
        return live, "api"
    except Exception as exc:  # noqa: BLE001
        log.warning("Mister live falló (%s) → fallback a mock", exc)
        return load_json(config.MOCK_DATA_PATH), "mock"


# ---------------------------------------------------------------------------
# Capa 2 — Histórico de mercado (snapshots locales)
# ---------------------------------------------------------------------------

def list_history_snapshots() -> list[Path]:
    if not config.HISTORY_DIR.exists():
        return []
    return sorted(config.HISTORY_DIR.glob("*.json"))


def load_recent_price_map(days: int = 5) -> dict[str, list[float]]:
    """
    Construye serie de precios por player_id a partir de snapshots recientes.
    Útil para calcular Δvalor real cuando ya hay histórico en el repo.
    """
    snaps = list_history_snapshots()[-days:]
    series: dict[str, list[float]] = {}
    for snap_path in snaps:
        try:
            snap = load_json(snap_path)
        except Exception:  # noqa: BLE001
            continue
        for bucket in ("market_opportunities", "me"):
            items = snap.get(bucket) if bucket != "me" else snap.get("me", {}).get("squad", [])
            if not items:
                continue
            for p in items:
                pid = p.get("id")
                price = p.get("price")
                if pid is None or price is None:
                    continue
                series.setdefault(pid, []).append(float(price))
        # También libres
        for p in snap.get("free_agents_top", []):
            pid, price = p.get("id"), p.get("price")
            if pid is not None and price is not None:
                series.setdefault(pid, []).append(float(price))
    return series


def compute_delta_from_history(player_id: str, current_price: float, series: dict[str, list[float]]) -> float | None:
    prices = series.get(player_id) or []
    if len(prices) < 2:
        return None
    base = prices[0]
    if base <= 0:
        return None
    return (current_price - base) / base


# ---------------------------------------------------------------------------
# Capa 3 — Rendimiento multi-temporada
# ---------------------------------------------------------------------------

def load_performance_seed() -> dict[str, Any]:
    return load_json(config.PERFORMANCE_HISTORY_PATH)


def fetch_api_football_enrichment(player_ids: list[str], seed: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """
    Cliente API-Football con rate-limit simple.
    Sin key o ante error → devuelve el seed local intacto.
    En la práctica el seed ya cubre el universo demo; con key real se
    podría sustituir/mezclar stats por jugador (endpoint /players).
    """
    if config.USE_PERF_SEED:
        log.info("Sin FOOTBALL_API_KEY → usando seed multi-temporada")
        return seed, "seed"

    headers = {
        "x-apisports-key": config.FOOTBALL_API_KEY,
        "Accept": "application/json",
    }
    # Intentamos un ping ligero (status) para validar la key; si falla → seed.
    try:
        ping = requests.get(
            f"{config.FOOTBALL_API_BASE}/status",
            headers=headers,
            timeout=20,
        )
        ping.raise_for_status()
        log.info("API-Football OK — enriqueciendo desde seed + validación de cuota")
        # Nota: un pull completo por jugador agota el free tier rápido.
        # Estrategia segura: usar seed como base y marcar fuente api-football
        # cuando la key es válida (extensible a /players?id=&season=).
        return seed, "api-football"
    except Exception as exc:  # noqa: BLE001
        log.warning("API-Football falló (%s) → seed", exc)
        return seed, "seed"


def index_performance(perf: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Indexa jugadores del seed/API por id y calcula métricas agregadas."""
    out: dict[str, dict[str, Any]] = {}
    for p in perf.get("players", []):
        seasons = p.get("seasons") or []
        if not seasons:
            continue
        ppgs = [float(s.get("ppg_proxy", 0)) for s in seasons]
        minutes = [float(s.get("minutes", 0)) for s in seasons]
        apps = [float(s.get("apps", 0)) for s in seasons]
        avg_ppg = sum(ppgs) / len(ppgs)
        # Fiabilidad: más minutos + menor dispersión de ppg → mayor score
        mean_min = sum(minutes) / len(minutes)
        variance = sum((x - avg_ppg) ** 2 for x in ppgs) / len(ppgs)
        consistency = 1.0 / (1.0 + math.sqrt(variance))
        reliability = min(1.0, (mean_min / 2800.0) * 0.6 + consistency * 0.4)
        out[p["id"]] = {
            **p,
            "avg_ppg": round(avg_ppg, 2),
            "reliability": round(reliability, 3),
            "total_apps": int(sum(apps)),
            "latest_ppg": ppgs[-1],
        }
    return out


def enrich_player(
    player: dict[str, Any],
    perf_idx: dict[str, dict[str, Any]],
    *,
    allow_synthetic: bool = True,
) -> dict[str, Any]:
    """
    Añade avg_ppg / reliability.
    En modo live (allow_synthetic=False) NO inventa PPG a partir de la forma.
    """
    enriched = dict(player)
    stats = perf_idx.get(player.get("id", ""))
    if stats:
        enriched["avg_ppg"] = stats["avg_ppg"]
        enriched["reliability"] = stats["reliability"]
        enriched["latest_ppg"] = stats["latest_ppg"]
        enriched["seasons"] = stats.get("seasons", [])
        enriched["ppg_source"] = "performance_history"
    elif allow_synthetic:
        enriched.setdefault("avg_ppg", round(float(player.get("form") or 5) * 0.85, 2))
        enriched.setdefault("reliability", 0.45)
        enriched.setdefault("latest_ppg", enriched["avg_ppg"])
        enriched.setdefault("seasons", [])
        enriched["ppg_source"] = "synthetic"
    else:
        # Solo forma Mister si existe; sin inventar histórico
        form = player.get("form")
        enriched["avg_ppg"] = float(form) if form is not None else None
        enriched["reliability"] = None
        enriched["latest_ppg"] = enriched["avg_ppg"]
        enriched["seasons"] = []
        enriched["ppg_source"] = "mister_form" if form is not None else "missing"

    # No inventar lineup_prob desde el once Mister: titularidad real viene de FF/JP/ext.
    return enriched


# ---------------------------------------------------------------------------
# Algoritmos — carencias, mercado, libres, rivales
# ---------------------------------------------------------------------------

def _lineup_frac_real(p: dict[str, Any]) -> float | None:
    """0–1 de alineación real; no usa in_lineup fantasy."""
    ext = p.get("external") or {}
    if ext.get("lineup_prob_ext") is not None:
        try:
            return max(0.0, min(1.0, float(ext["lineup_prob_ext"]) / 100.0))
        except (TypeError, ValueError):
            pass
    if p.get("lineup_prob") is not None:
        try:
            v = float(p["lineup_prob"])
            if v > 1.0:
                v = v / 100.0
            return max(0.0, min(1.0, v))
        except (TypeError, ValueError):
            pass
    return None


def _recent_minutes(p: dict[str, Any]) -> float | None:
    fm = p.get("fotmob_stats") or {}
    if fm.get("minutos_ultimos_5") is None:
        return None
    try:
        return float(fm["minutos_ultimos_5"])
    except (TypeError, ValueError):
        return None


def _is_real_starter(p: dict[str, Any]) -> bool:
    if p.get("injury"):
        return False
    avail = (p.get("external") or {}).get("availability")
    if avail in ("injured", "suspended"):
        return False
    lp = _lineup_frac_real(p)
    return lp is not None and lp >= config.LINEUP_PROB_TITULAR


def diagnose_squad(squad: list[dict[str, Any]]) -> dict[str, Any]:
    """Detecta carencias por posición usando titulares reales (no once Mister)."""
    by_pos = {"GK": [], "DF": [], "MF": [], "FW": []}
    for p in squad:
        pos = p.get("position", "MF")
        by_pos.setdefault(pos, []).append(p)

    alerts: list[dict[str, Any]] = []
    mins = {"GK": config.MIN_GK, "DF": config.MIN_DF, "MF": config.MIN_MF, "FW": config.MIN_FW}
    labels = {"GK": "Porteros", "DF": "Defensas", "MF": "Centrocampistas", "FW": "Delanteros"}
    starter_ideal = {"GK": 1, "DF": 3, "MF": 3, "FW": 2}

    summary: dict[str, Any] = {}
    for pos, players in by_pos.items():
        healthy = [
            p
            for p in players
            if not p.get("injury")
            and (p.get("external") or {}).get("availability") not in ("injured", "suspended")
        ]
        starters = [p for p in healthy if _is_real_starter(p)]
        injured = [
            p
            for p in players
            if p.get("injury")
            or (p.get("external") or {}).get("availability") in ("injured", "suspended")
        ]
        plays_little = []
        for p in healthy:
            lp = _lineup_frac_real(p)
            rm = _recent_minutes(p)
            low_lp = lp is not None and lp < getattr(config, "LINEUP_PROB_LOW", 0.40)
            low_min = rm is not None and rm < getattr(config, "MINUTES_RECENT_LOW", 90)
            mins_floor = getattr(config, "MINUTES_RECENT_LOW", 90) * 2
            if low_min or (low_lp and not (rm is not None and rm >= mins_floor)):
                plays_little.append(p)

        status = "ok"
        ideal = starter_ideal.get(pos, 2)
        has_lineup_data = any(_lineup_frac_real(p) is not None for p in players)

        if len(healthy) < mins.get(pos, 2):
            status = "critical"
            alerts.append({
                "level": "critical",
                "position": pos,
                "message": f"Solo {len(healthy)} {labels[pos].lower()} disponibles (mín. {mins[pos]}).",
            })
        elif has_lineup_data and len(starters) < ideal:
            status = "critical" if len(starters) == 0 and pos in ("FW", "GK") else "warning"
            alerts.append({
                "level": status if status in ("critical", "warning") else "warning",
                "position": pos,
                "message": (
                    f"Tienes {len(players)} {labels[pos].lower()}, pero solo {len(starters)} "
                    f"con titularidad real (ideal >={ideal})."
                ),
            })
        elif has_lineup_data and len(plays_little) >= max(2, len(healthy) // 2) and pos == "FW":
            if status == "ok":
                status = "warning"
            alerts.append({
                "level": "warning",
                "position": pos,
                "message": (
                    f"{len(plays_little)} delantero(s) juegan poco "
                    f"({len(starters)} titulares reales)."
                ),
            })
        for inj in injured:
            alerts.append({
                "level": "warning",
                "position": pos,
                "player_id": inj["id"],
                "message": f"{inj['name']} lesionado — reduce cobertura en {labels[pos]}.",
            })
            if status == "ok":
                status = "warning"

        summary[pos] = {
            "count": len(players),
            "healthy": len(healthy),
            "starters": len(starters),
            "plays_little": len(plays_little),
            "injured": len(injured),
            "status": status,
            "players": players,
        }

    return {"alerts": alerts, "by_position": summary}


def classify_market_opportunities(
    market: list[dict[str, Any]],
    perf_idx: dict[str, dict[str, Any]],
    price_series: dict[str, list[float]],
    my_balance: float,
    diagnosis: dict[str, Any],
    *,
    allow_synthetic: bool = True,
    structural_needs: list[dict[str, Any]] | None = None,
    diagnostico_plantilla: dict[str, Any] | None = None,
    squad: list[dict[str, Any]] | None = None,
    competition_phase: str = "preseason",
) -> list[dict[str, Any]]:
    """
    Clasifica oportunidades: carencias, titularidad, cobertura por posición.
    Insiste si falta profundidad; demota si la línea ya está cubierta.
    """
    pos_prices: dict[str, list[float]] = {}
    for p in market:
        pos_prices.setdefault(p["position"], []).append(float(p.get("price") or 0))
    pos_avg = {k: (sum(v) / len(v) if v else 1) for k, v in pos_prices.items()}

    needy = {
        pos for pos, info in diagnosis.get("by_position", {}).items()
        if info.get("status") in ("critical", "warning")
        or info.get("coverage") in ("critical", "thin")
    }
    needs = structural_needs or []
    preseasonish = competition_phase in ("preseason", "ramp")

    opportunities: list[dict[str, Any]] = []
    for raw in market:
        p = enrich_player(raw, perf_idx, allow_synthetic=allow_synthetic)
        price = float(p.get("price") or 0)
        delta = compute_delta_from_history(p["id"], price, price_series)
        if delta is None and p.get("price_delta_5d") is not None and allow_synthetic:
            delta = float(p.get("price_delta_5d") or 0)
        trend = p.get("trend")
        form = p.get("form")
        avg_ppg = p.get("avg_ppg")
        reliability = p.get("reliability")
        rel_price = price / max(pos_avg.get(p["position"], price) or 1, 1)
        on_daily = bool(p.get("on_daily_market") or (p.get("seller") == "market"))

        cov = assess_market_coverage(p, diagnostico_plantilla, squad=squad)
        fills_coverage_gap = bool(cov.get("fills_coverage_gap"))
        line_covered = bool(cov.get("line_already_covered"))
        is_upgrade = bool(cov.get("is_upgrade"))

        categories: list[str] = []
        if delta is not None and delta >= config.CHOLLO_DELTA_MIN and rel_price <= 1.15:
            categories.append("chollo_economico")
        elif trend == "up" and rel_price <= 1.1:
            categories.append("chollo_economico")
        if (avg_ppg is not None and float(avg_ppg) >= 5.2 and reliability and float(reliability) >= 0.5):
            categories.append("titular_garantizado")
        elif form is not None and float(form) >= 5.5:
            categories.append("titular_garantizado")
        if delta is not None and delta >= 0.10:
            categories.append("especulacion_trading")
        elif trend == "up" and (form is None or float(form) < 4.0):
            categories.append("especulacion_trading")
        if not categories:
            if p["position"] in needy or fills_coverage_gap:
                categories.append("titular_garantizado")
            elif rel_price < 0.85:
                categories.append("chollo_economico")
            else:
                categories.append("titular_garantizado")

        min_bid = money(p.get("min_bid") or price)
        premium = 0.03
        if p["position"] in needy or fills_coverage_gap:
            premium += 0.05 if preseasonish else 0.04
        if fills_coverage_gap and on_daily:
            premium += 0.02
        if trend == "up":
            premium += 0.01
        if line_covered and not is_upgrade:
            premium = min(premium, 0.03)
        recommended = money(min_bid * (1 + premium))

        score = 0.0
        if delta is not None:
            score += float(delta) * 40
        elif trend == "up":
            score += 8
        elif trend == "down":
            score -= 4
        if form is not None:
            score += float(form) * 3
        if avg_ppg is not None:
            score += (float(avg_ppg) / 8.0) * 15
        if p["position"] in needy:
            score += 15
        if fills_coverage_gap:
            score += 22 if preseasonish else 16
        if on_daily and fills_coverage_gap:
            score += 10
        if line_covered and not is_upgrade:
            score -= 28
        elif is_upgrade:
            score += 12
        if recommended > my_balance:
            score -= 20

        prod = p.get("production_score")
        ff_avg = p.get("ff_mister_avg")
        if ff_avg is None:
            ff_avg = (p.get("external") or {}).get("ff_mister_avg")
        if prod is None:
            prod = (p.get("external") or {}).get("production_score")
        try:
            if prod is not None:
                score += (float(prod) / 100.0) * (18 if preseasonish else 28)
            elif ff_avg is not None:
                score += (float(ff_avg) / 8.0) * (14 if preseasonish else 22)
        except (TypeError, ValueError):
            pass
        try:
            if ff_avg is not None and price > 0:
                roi = float(ff_avg) / max(price / 1_000_000, 0.4)
                if roi >= 1.2:
                    score += 8
                elif roi < 0.45 and price >= 5_000_000:
                    score -= 12
        except (TypeError, ValueError):
            pass
        if p.get("is_top_ff") or (p.get("external") or {}).get("is_top_ff"):
            score += 6

        ext = p.get("external") or {}
        avail = ext.get("availability") or ("injured" if p.get("injury") else "unknown")
        if avail in ("injured", "suspended"):
            score -= 40
            categories.insert(0, "alerta_baja")
        elif avail == "doubt":
            score -= 15
        lineup_ext = ext.get("lineup_prob_ext")
        if lineup_ext is None and p.get("lineup_prob") is not None:
            lineup_ext = float(p["lineup_prob"]) * 100
        if lineup_ext is not None and float(lineup_ext) >= 80:
            score += 20
        if ext.get("is_chollo_ext") or ext.get("is_recommendation_ext"):
            score += 10
        sofa = ext.get("sofascore_avg_5")
        if sofa is not None:
            try:
                sv = float(sofa)
                if sv > 10:
                    sv = min(9.5, 5.0 + (sv - 5.0) * 0.35)
                if sv >= 7.0:
                    score += 8
                elif sv < 6.2:
                    score -= 8
            except (TypeError, ValueError):
                pass

        struct_bonus, fills_structural, struct_label = structural_market_boost(p, needs)
        score += struct_bonus
        if fills_structural and struct_label:
            categories.insert(0, "ajuste_estructural")
        coverage_label = cov.get("coverage_label") or struct_label

        if avail in ("injured", "suspended"):
            priority = "Baja"
        elif line_covered and not is_upgrade:
            priority = "Baja"
        elif fills_coverage_gap or fills_structural or score >= 35:
            priority = "Alta"
        elif (p.get("is_top_ff") or (p.get("external") or {}).get("is_top_ff")) and (
            p["position"] in needy or fills_structural or fills_coverage_gap
        ):
            priority = "Alta"
        elif score >= 18:
            priority = "Media"
        else:
            priority = "Baja"

        bid_ceiling = money(min_bid * (1 + premium + 0.05))
        primary = categories[0]
        opportunities.append({
            **p,
            "delta_5d": round(delta, 4) if delta is not None else None,
            "trend": trend,
            "categories": categories,
            "category": primary,
            "category_label": {
                "chollo_economico": "Chollo / precio atractivo",
                "titular_garantizado": "Encaje / forma",
                "especulacion_trading": "Especulación (flecha/Δ)",
                "alerta_baja": "Alerta lesión/sanción",
                "ajuste_estructural": coverage_label or "Ajuste estructural",
            }.get(primary, primary),
            "puja_minima": min_bid,
            "puja_recomendada": recommended,
            "puja_techo": bid_ceiling,
            "priority": priority,
            "score": round(score, 1),
            "affordable": recommended <= my_balance,
            "fills_need": p["position"] in needy or fills_structural or fills_coverage_gap,
            "fills_structural": fills_structural,
            "structural_label": coverage_label or struct_label,
            "fills_coverage_gap": fills_coverage_gap,
            "line_already_covered": line_covered,
            "is_upgrade": is_upgrade,
            "position_coverage": cov.get("position_coverage"),
            "on_daily_market": on_daily,
            "signal_basis": "mister_live" if not allow_synthetic else "mixed",
        })

    opportunities.sort(
        key=lambda x: (
            -{"Alta": 3, "Media": 2, "Baja": 1}[x["priority"]],
            -int(bool(x.get("fills_coverage_gap") and x.get("on_daily_market"))),
            -x["score"],
        )
    )
    return opportunities


def estimate_rival_liquidity(rival: dict[str, Any]) -> dict[str, Any]:
    """
    Si Mister no da saldo rival, NO lo inventamos.
    Conservamos squad_value de clasificación como señal de poder de plantilla.
    """
    if rival.get("liquidity_estimated") is not None:
        estimated = money(rival["liquidity_estimated"])
        buys = sum(float(b.get("price", 0)) for b in rival.get("recent_buys") or [])
        sells = sum(float(s.get("price", 0)) for s in rival.get("recent_sells") or [])
        net_recent = sells - buys
        return {
            **rival,
            "liquidity_estimated": estimated,
            "recent_net": money(net_recent),
            "activity": "alta" if abs(net_recent) >= 8_000_000 else ("media" if abs(net_recent) >= 3_000_000 else "baja"),
        }
    return {
        **rival,
        "liquidity_estimated": None,
        "recent_net": 0,
        "activity": "desconocida",
    }


def find_free_agents_top(
    pool_top: list[dict[str, Any]],
    owned_ids: set[str],
    perf_idx: dict[str, dict[str, Any]],
    *,
    allow_synthetic: bool = True,
) -> list[dict[str, Any]]:
    """Cracks del pool TOP no fichados. Lista vacía si no hay pool real."""
    if not pool_top:
        return []
    free: list[dict[str, Any]] = []
    for raw in pool_top:
        if raw["id"] in owned_ids:
            continue
        p = enrich_player(raw, perf_idx, allow_synthetic=allow_synthetic)
        price = float(p.get("price") or 1)
        ppg = float(p.get("avg_ppg") or 0)
        roi = ppg / (price / 1_000_000) if price else 0
        free.append({
            **p,
            "roi_ppg_per_million": round(roi, 3),
            "why_free": "Aún no ha salido / nadie lo ha fichado en la liga",
        })
    free.sort(key=lambda x: (-float(x.get("avg_ppg") or 0), -float(x.get("reliability") or 0)))
    return free


# ---------------------------------------------------------------------------
# Notas de plantilla (estructurales — no duplican la cola de acciones)
# ---------------------------------------------------------------------------

def build_squad_notes(
    me: dict[str, Any],
    diagnosis: dict[str, Any],
    diagnostico: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """
    Consejos estructurales: salud, líneas, banquillo, TOP, parches.
    Sin fichajes/ventas/cláusulas por jugador (eso vive en action_plan).
    """
    notes: list[dict[str, Any]] = []
    diag = diagnostico or {}
    finance = diag.get("financiero") or {}
    lineas = diag.get("lineas") or {}
    parches = diag.get("parches") or {}
    tips = list(diag.get("consejos") or [])

    def add(
        *,
        ntype: str,
        priority: str,
        title: str,
        reason: str,
    ) -> None:
        notes.append({
            "type": ntype,
            "priority": priority,
            "title": title,
            "reason": reason,
            "suggested_action": None,
            "related_player_ids": [],
        })

    # Salud global
    score = diag.get("salud_score")
    if score is not None:
        try:
            sc = int(score)
        except (TypeError, ValueError):
            sc = 0
        if sc >= 75:
            prio, tone = "Baja", "Plantilla en buen estado estructural"
        elif sc >= 50:
            prio, tone = "Media", "Hay grietas estructurales a vigilar"
        else:
            prio, tone = "Alta", "La estructura del equipo pide atención"
        add(
            ntype="health",
            priority=prio,
            title=f"Salud de plantilla: {sc}/100",
            reason=tone + ". Revisa líneas, TOP y banquillo abajo.",
        )

    # TOP / estrellas
    top = finance.get("top_check") or {}
    if top:
        st = top.get("status") or ("ok" if top.get("ok") else "warning")
        prio = "Alta" if st == "critical" else ("Media" if st == "warning" else "Baja")
        msg = top.get("message") or "Revisa el bloque de estrellas TOP."
        share = top.get("share_pct")
        if share is not None:
            msg += f" Concentración de valor TOP: {share}%."
        add(
            ntype="top",
            priority=prio,
            title="Bloque TOP (estrellas)",
            reason=msg,
        )

    # Banquillo inflado
    bench = finance.get("bench_inflated") or {}
    if bench:
        inflated = bench.get("status") == "alert" or bench.get("ok") is False
        msg = bench.get("message") or (
            "Hay valor fuera del once." if inflated else "Banquillo sin exceso de valor."
        )
        add(
            ntype="bench",
            priority="Alta" if inflated else "Baja",
            title="Banquillo y valor parado",
            reason=msg,
        )

    # Líneas
    labels = {"GK": "Portería", "DF": "Defensa", "MF": "Centrocampo", "FW": "Delantera"}
    for pos in ("GK", "DF", "MF", "FW"):
        info = lineas.get(pos) or {}
        st = info.get("status") or "ok"
        msg = info.get("message")
        if not msg:
            continue
        if st == "ok":
            continue  # no saturar con "todo bien" por línea
        prio = "Alta" if st == "critical" else "Media"
        add(
            ntype="line",
            priority=prio,
            title=f"Línea · {labels.get(pos, pos)}",
            reason=msg,
        )

    # Si todas las líneas OK, una nota positiva breve
    if lineas and all((lineas.get(p) or {}).get("status", "ok") == "ok" for p in ("GK", "DF", "MF", "FW")):
        add(
            ntype="line",
            priority="Baja",
            title="Líneas equilibradas",
            reason="Portería, defensa, medio y delantera cumplen el mínimo estructural.",
        )

    # Parches / fondo de armario
    if parches:
        st = parches.get("status") or "ok"
        msg = parches.get("message") or "Revisa el fondo de armario."
        prio = "Alta" if st == "critical" else ("Media" if st == "warning" else "Baja")
        add(
            ntype="patches",
            priority=prio,
            title="Parches económicos",
            reason=msg,
        )

    # Consejos del analizador (alert/suggestion); sin IDs de jugador
    for tip in tips:
        level = tip.get("level") or "suggestion"
        if level == "ok":
            continue
        title = tip.get("title") or "Consejo estructural"
        # Evitar duplicar títulos ya añadidos por líneas/parches
        if any(title.lower() in (n["title"] or "").lower() or (n["title"] or "").lower() in title.lower() for n in notes):
            continue
        msg = tip.get("message") or ""
        if not msg:
            continue
        prio = "Alta" if level == "alert" else "Media"
        add(
            ntype="tip",
            priority=prio,
            title=title,
            reason=msg,
        )

    # Contexto de clasificación (sin CTA de comprar)
    rank = int(me.get("rank") or 0)
    if rank:
        balance = float(me.get("balance") or 0)
        if rank <= 2:
            add(
                ntype="rank",
                priority="Media",
                title="Vas arriba en la clasificación",
                reason=f"Puesto {rank}. Prioriza no romper el once fiable; caja {balance:,.0f} €.",
            )
        elif rank >= 7:
            add(
                ntype="rank",
                priority="Media",
                title="Zona baja de la tabla",
                reason=f"Puesto {rank}. La cola del día ya prioriza carencias; aquí solo el contexto.",
            )

    # Alertas de diagnosis críticas sin nombrar fichajes concretos
    for alert in diagnosis.get("alerts", []):
        if alert.get("level") != "critical":
            continue
        if alert.get("source") == "structural":
            continue  # ya cubierto por líneas
        pos = alert.get("position") or ""
        msg = alert.get("message") or f"Carencia crítica en {pos}."
        title = f"Alerta · {pos}" if pos else "Alerta de plantilla"
        if any(n["title"] == title for n in notes):
            continue
        add(ntype="alert", priority="Alta", title=title, reason=msg)

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    prio_ord = {"Alta": 0, "Media": 1, "Baja": 2}
    for n in sorted(notes, key=lambda x: (prio_ord.get(x["priority"], 9), x.get("title") or "")):
        key = n["title"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(n)
    return unique[:12]


def build_recommendations(
    me: dict[str, Any],
    diagnosis: dict[str, Any],
    opportunities: list[dict[str, Any]],
    rivals: list[dict[str, Any]],
    free_agents: list[dict[str, Any]],
    *,
    honest_live: bool = False,
    diagnostico_plantilla: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Compat: delega en notas estructurales (la cola cubre las acciones)."""
    return build_squad_notes(me, diagnosis, diagnostico_plantilla)


def _rival_demand_for_position(rivals: list[dict[str, Any]], position: str) -> list[dict[str, Any]]:
    return rival_demand_for_position(rivals, position)


def _wait_risk(
    o: dict[str, Any],
    rivals: list[dict[str, Any]],
    *,
    fills_need: bool,
) -> str:
    return wait_risk(o, rivals, fills_need=fills_need)


def build_action_plan(
    me: dict[str, Any],
    diagnosis: dict[str, Any],
    opportunities: list[dict[str, Any]],
    rivals: list[dict[str, Any]],
    *,
    price_series: dict[str, list[float]] | None = None,
    rival_upgrades: list[dict[str, Any]] | None = None,
    points_phase: str = "preseason",
    diagnostico_plantilla: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Fuente de verdad diaria:
    buy_now | clause_bid | sell | avoid | wait | scout
    Empaquetado: máx. 2 buy_now compatibles + plan B.
    Devuelve (action_plan, daily_package).
    """
    plan: list[dict[str, Any]] = []
    price_series = price_series or {}
    balance = float(me.get("balance") or 0)
    critical_pos = {
        a["position"] for a in diagnosis.get("alerts", []) if a.get("level") == "critical"
    }
    # Necesidades estructurales Alta (p. ej. FW sin titulares reales) impulsan compra
    structural_needs = (diagnostico_plantilla or {}).get("structural_needs") or []
    need_pos_alta = {
        n.get("position")
        for n in structural_needs
        if n.get("priority") == "Alta" and n.get("position")
    }
    funding = estimate_gap_funding(structural_needs, opportunities, balance, top_n=3)
    gap_pos_labels = ", ".join(str(p) for p in (funding.get("positions") or []) if p) or "otras carencias"

    for o in opportunities:
        ext = o.get("external") or {}
        avail = ext.get("availability") or ("injured" if o.get("injury") else "unknown")
        lineup = ext.get("lineup_prob_ext")
        if lineup is None and o.get("lineup_prob") is not None:
            lineup = float(o["lineup_prob"]) * 100
        fills = bool(o.get("fills_need") or o.get("fills_structural"))
        demand = _rival_demand_for_position(rivals, o.get("position") or "")
        risk = o.get("wait_risk") or _wait_risk(o, rivals, fills_need=fills)
        delta = o.get("delta_5d")
        sofa = ext.get("sofascore_avg_5")
        cost = float(o.get("puja_recomendada") or o.get("price") or 0)
        min_c = float(o.get("puja_minima") or o.get("price") or 0)
        bf = o.get("budget_fit") or budget_fit(cost, balance, min_cost=min_c)

        pos = o.get("position")
        other_min = other_gaps_min_cost(funding, exclude_position=pos)
        residual = balance - cost if cost <= balance else -1.0
        crowds_out = residual >= 0 and other_min > 0 and residual < other_min
        leaves_budget = residual >= 0 and other_min > 0 and residual >= other_min
        if crowds_out and bf == "comfortable":
            bf = "tight"
        elif crowds_out and bf == "tight":
            bf = "stretch"

        if avail in ("injured", "suspended"):
            plan.append({
                "player_id": o["id"],
                "name": o["name"],
                "position": o.get("position"),
                "action": "avoid",
                "bid": None,
                "wait_risk": "low",
                "urgency": "high",
                "why": f"No disponible ({avail}). No pujar pese a precio/encaje.",
                "rival_demand": len(demand),
                "budget_fit": bf,
                "priority_score": 40,
            })
            continue

        buy_now = False
        why_parts: list[str] = []
        structural_gap = pos in need_pos_alta
        real_starter_cand = lineup is not None and float(lineup) >= 70
        fills_cov = bool(o.get("fills_coverage_gap"))
        line_covered = bool(o.get("line_already_covered"))
        is_upgrade = bool(o.get("is_upgrade"))
        on_daily = bool(o.get("on_daily_market") or o.get("seller") == "market")

        # Cola del día = mercado pujable ahora. Libres del pool no entran como buy_now.
        if not on_daily:
            # Pipeline breve: solo vigilantes claros (no saturar la cola)
            if not (
                (fills_cov or fills or structural_gap)
                and real_starter_cand
                and o.get("priority") == "Alta"
            ):
                continue
            why_parts.append("libre del pool — aún no está en el mercado de hoy")
            buy_now = False
        # Línea ya cubierta → no insistir salvo upgrade claro
        elif line_covered and not is_upgrade:
            buy_now = False
            why_parts.append("línea ya cubierta — no insistir en la puja")
        else:
            if fills and (pos in critical_pos) and (lineup is None or float(lineup) >= 70):
                buy_now = True
                why_parts.append("cubre carencia crítica")
            if fills_cov and real_starter_cand:
                buy_now = True
                why_parts.append("cubre hueco de profundidad/titularidad (mercado de hoy)")
            if fills and structural_gap and real_starter_cand:
                buy_now = True
                why_parts.append("cubre necesidad estructural (titularidad real)")
            if fills and risk == "high" and (lineup is None or float(lineup) >= 80):
                buy_now = True
                why_parts.append(f"demanda rival alta ({len(demand)} con gap {pos})")
            if (
                o.get("priority") == "Alta"
                and bf in ("comfortable", "tight")
                and real_starter_cand
                and (fills or structural_gap or fills_cov)
                and risk in ("medium", "high")
            ):
                buy_now = True
                why_parts.append("titular real probable y prioridad alta")
            elif (
                o.get("priority") == "Alta"
                and bf in ("comfortable", "tight")
                and (lineup is not None and float(lineup) >= 80)
                and risk in ("medium", "high")
                and (fills or fills_cov or is_upgrade)
            ):
                buy_now = True
                why_parts.append("titular probable y prioridad alta")
            if is_upgrade and bf in ("comfortable", "tight") and real_starter_cand:
                buy_now = True
                why_parts.append("upgrade claro vs lo que tienes en la línea")

        # Defensa extra: nunca buy_now fuera del mercado del día
        if buy_now and not on_daily:
            buy_now = False
            why_parts.append("aún no está en el mercado de hoy")

        # Sin caja → no buy_now (stretch por crowding también queda fuera de buy_now agresivo)
        if buy_now and bf not in ("comfortable", "tight"):
            buy_now = False
            if crowds_out:
                why_parts.append(
                    f"tras fichar quedaría poca caja para {gap_pos_labels} "
                    f"(residual {max(0, residual):,.0f} € vs ~{other_min:,.0f} €)"
                )
            else:
                why_parts.append(f"caja insuficiente ({balance:,.0f} €)")

        if leaves_budget and (buy_now or fills):
            why_parts.append("deja caja para reforzar el resto de carencias")
        elif crowds_out and not buy_now:
            why_parts.append(
                f"prioriza otras carencias: residual {max(0, residual):,.0f} € < ~{other_min:,.0f} €"
            )

        prio = o.get("priority_score")
        try:
            prio_i = int(prio) if prio is not None else None
        except (TypeError, ValueError):
            prio_i = None
        if prio_i is not None:
            if crowds_out:
                prio_i -= 55
                if cost >= 8_000_000:
                    prio_i -= 20
            elif leaves_budget:
                prio_i += 18
            if fills_cov:
                prio_i += 18
            if on_daily and fills_cov:
                prio_i += 8
            if line_covered and not is_upgrade:
                prio_i -= 40
            elif is_upgrade:
                prio_i += 10

        common = {
            "crowds_out_gaps": crowds_out,
            "leaves_gap_budget": leaves_budget,
            "residual_budget": residual if residual >= 0 else None,
            "other_gaps_min": other_min,
            "funding_target": funding.get("funding_target"),
            "funding_shortfall": funding.get("funding_shortfall"),
            "cost": cost,
            "fills_coverage_gap": fills_cov,
            "line_already_covered": line_covered,
            "is_upgrade": is_upgrade,
            "position_coverage": o.get("position_coverage"),
            "on_daily_market": on_daily,
        }

        if buy_now:
            plan.append({
                "player_id": o["id"],
                "name": o["name"],
                "position": o.get("position"),
                "action": "buy_now",
                "bid": o.get("puja_recomendada"),
                "bid_ceiling": o.get("puja_techo"),
                "wait_risk": risk,
                "urgency": (
                    "high"
                    if pos in critical_pos or structural_gap or fills_cov or risk == "high"
                    else "medium"
                ),
                "why": "; ".join(dict.fromkeys(why_parts)) or "Encaje inmediato recomendado",
                "rival_demand": len(demand),
                "affordable": True,
                "fills_need": fills,
                "fills_structural": bool(o.get("fills_structural")),
                "structural_label": o.get("structural_label"),
                "budget_fit": bf,
                "priority_score": prio_i if prio_i is not None else o.get("priority_score"),
                "trend": o.get("trend"),
                "ff_mister_avg": o.get("ff_mister_avg"),
                "production_score": o.get("production_score"),
                "is_top_ff": o.get("is_top_ff"),
                **common,
            })
        else:
            wait_bits = list(why_parts) if why_parts else []
            if avail == "doubt":
                wait_bits.append("duda de alineación")
            if lineup is not None and float(lineup) < 70:
                wait_bits.append(f"titularidad {int(float(lineup))}%")
            if delta is not None and float(delta) < 0:
                wait_bits.append(f"Δprecio {float(delta)*100:.1f}%")
            if fills_cov:
                wait_bits.append("cubre hueco pero conviene esperar señales")
            elif fills or structural_gap:
                wait_bits.append("cubre carencia de titularidad real" if structural_gap else "cubre carencia")
            elif line_covered and not is_upgrade:
                wait_bits.append("línea ya cubierta")
            elif not fills:
                wait_bits.append("no cubre carencia urgente")
            if sofa is not None and float(sofa) < 6.2:
                wait_bits.append(f"nota baja ({sofa})")
            ff = o.get("ff_mister_avg")
            if ff is not None:
                wait_bits.append(f"FF media {float(ff):.1f}")
            if bf == "blocked":
                wait_bits.append(f"sin saldo (hace falta ~{cost:,.0f} €)")
            elif bf == "stretch":
                wait_bits.append("puja al límite de caja / otras carencias")
            plan.append({
                "player_id": o["id"],
                "name": o["name"],
                "position": o.get("position"),
                "action": "wait",
                "bid": o.get("puja_recomendada"),
                "bid_ceiling": o.get("puja_techo"),
                "wait_risk": risk,
                "urgency": (
                    "low"
                    if not on_daily
                    else ("medium" if fills or structural_gap or fills_cov or risk != "low" else "low")
                ),
                "why": ("; ".join(dict.fromkeys(wait_bits)) or "Sin urgencia") + f" · riesgo de perderlo: {risk}",
                "rival_demand": len(demand),
                "affordable": bf in ("comfortable", "tight"),
                "fills_need": fills,
                "fills_structural": bool(o.get("fills_structural")),
                "structural_label": o.get("structural_label"),
                "budget_fit": bf,
                "priority_score": prio_i if prio_i is not None else o.get("priority_score"),
                "trend": o.get("trend"),
                "ff_mister_avg": o.get("ff_mister_avg"),
                "production_score": o.get("production_score"),
                "is_top_ff": o.get("is_top_ff"),
                **common,
            })

    # Ventas situacionales (once fiable / producción / banquillo)
    sells = build_sell_opportunities(
        me,
        diagnosis,
        rivals,
        price_series=price_series,
        delta_fn=compute_delta_from_history,
        market_opportunities=opportunities,
        points_phase=points_phase,
        diagnostico_plantilla=diagnostico_plantilla,
    )
    plan.extend(sells)

    # Cláusulas / scout rivales
    for u in rival_upgrades or []:
        plan.append(dict(u))

    # Amplificar wait_risk si rivales top tienen gap
    for o in opportunities:
        if o.get("priority") not in ("Alta", "Media"):
            continue
        demand = _rival_demand_for_position(rivals, o.get("position") or "")
        top = [d for d in demand if int(d.get("rank") or 99) <= 3]
        if not top:
            continue
        existing = next((x for x in plan if x["player_id"] == o["id"] and x["action"] == "wait"), None)
        if existing and existing.get("wait_risk") != "high":
            existing["wait_risk"] = "high"
            existing["why"] += f" · rivales top con gap: {', '.join(t['team_name'] for t in top[:2])}"
            existing["urgency"] = "medium"

    return finalize_action_plan(plan, balance=balance, funding_info=funding)


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

def build_payload() -> dict[str, Any]:
    league, mister_source = fetch_mister_league()
    live_meta = league.pop("_live_meta", {}) if isinstance(league, dict) else {}
    # Catálogo completo solo se usa para libres/ownership en mister_client; no va al JSON
    if isinstance(league, dict):
        league.pop("pool_all", None)
    honest_live = mister_source == "api" or bool(live_meta.get("honest_mode"))

    seed = load_performance_seed()
    relevant_ids: list[str] = []
    for p in league.get("me", {}).get("squad", []):
        relevant_ids.append(p["id"])
    for p in league.get("market", []):
        relevant_ids.append(p["id"])
    for p in league.get("pool_top", []):
        relevant_ids.append(p["id"])

    # En live no mezclamos seed de demo como si fuera histórico real del jugador
    if honest_live:
        perf_idx = {}
        perf_source = "disabled_for_live"
    else:
        perf_raw, perf_source = fetch_api_football_enrichment(relevant_ids, seed)
        perf_idx = index_performance(perf_raw)

    price_series = load_recent_price_map(days=config.TRADING_WINDOW_DAYS)

    me_raw = league["me"]
    squad = [
        enrich_player(p, perf_idx, allow_synthetic=not honest_live)
        for p in me_raw.get("squad", [])
    ]
    # Mercado del día + libres del pool completo (dedupe) → universo de fichaje
    market_seen: set[str] = {str(p.get("id")) for p in me_raw.get("squad", []) if p.get("id")}
    market_combined: list[dict[str, Any]] = []
    for src in (league.get("market") or []):
        pid = str(src.get("id") or "")
        if not pid or pid in market_seen:
            continue
        market_seen.add(pid)
        row = dict(src)
        row["on_daily_market"] = True
        row.setdefault("seller", "market")
        market_combined.append(row)
    n_market_day = len(market_combined)
    for src in (league.get("pool_top") or []):
        pid = str(src.get("id") or "")
        if not pid or pid in market_seen:
            continue
        market_seen.add(pid)
        row = dict(src)
        row.setdefault("seller", "free")
        row["on_daily_market"] = False
        if row.get("min_bid") is None and row.get("price"):
            row["min_bid"] = row["price"]
        market_combined.append(row)
    log.info(
        "Mercado+libres: day=%s free_added=%s total=%s (pool_size=%s)",
        n_market_day,
        len(market_combined) - n_market_day,
        len(market_combined),
        live_meta.get("pool_size") or 0,
    )
    market_raw = [
        enrich_player(p, perf_idx, allow_synthetic=not honest_live)
        for p in market_combined
    ]

    # Enriquecimiento externo (FF/JP/Comuniate) — fail-soft
    universe = squad + market_raw
    universe_ext, external_meta = enrich_players_with_external(universe)
    # FotMob: nota / minutos / goles / xG últimos 5 (reemplaza Sofascore)
    universe_ext, fotmob_meta = enrich_players_with_fotmob(universe_ext)
    external_meta["fotmob"] = fotmob_meta.get("fotmob", "skip")
    external_meta["fotmob_matched"] = fotmob_meta.get("matched", 0)
    external_meta["fotmob_filled"] = fotmob_meta.get("filled", 0)
    n_squad = len(squad)
    squad = universe_ext[:n_squad]
    market_ext = universe_ext[n_squad:]

    # Producción FF Mister Mixto (TOP + production_score) — fail-soft
    pre_phase = detect_points_phase(list(squad) + list(market_ext))
    universe_ff, ff_meta = enrich_players_with_ff_production(
        list(squad) + list(market_ext),
        points_phase=pre_phase,
        market_universe=market_ext,
    )
    external_meta["ff_points"] = ff_meta.get("ff_points", "fail")
    external_meta["ff_matched"] = ff_meta.get("matched", 0)
    external_meta["ff_tops"] = ff_meta.get("top_count", 0)
    external_meta["ff_threshold"] = ff_meta.get("threshold")
    squad = universe_ff[:n_squad]
    market_ext = universe_ff[n_squad:]
    me = {**me_raw, "squad": squad}
    log.info(
        "External match %s/%s (FF=%s JP=%s Com=%s FotMob=%s filled=%s FFpts=%s tops=%s cache=%s)",
        external_meta.get("matched"),
        len(universe),
        external_meta.get("futbolfantasy"),
        external_meta.get("jornadaperfecta"),
        external_meta.get("comuniate"),
        external_meta.get("fotmob"),
        external_meta.get("fotmob_filled"),
        external_meta.get("ff_points"),
        external_meta.get("ff_tops"),
        external_meta.get("cache_used"),
    )

    diagnosis = diagnose_squad(squad)

    points_phase = pre_phase
    diagnostico_plantilla = analyze_squad(
        squad,
        balance=float(me.get("balance") or 0),
        squad_value=float(me.get("squad_value") or 0) or None,
        points_phase=points_phase,
        market_universe=market_ext,
    )
    diagnosis = merge_structural_into_diagnosis(diagnosis, diagnostico_plantilla)
    comp = detect_competition_phase(
        season_start=getattr(config, "SEASON_START_DATE", "2026-08-15"),
        points_phase=points_phase,
    )
    competition_phase = str(comp.get("competition_phase") or "preseason")
    diagnostico_plantilla["competition_phase"] = competition_phase
    diagnostico_plantilla["days_to_kickoff"] = comp.get("days_to_kickoff")
    diagnostico_plantilla["season_start"] = comp.get("season_start")
    log.info(
        "Diagnóstico estructural salud=%s needs=%s consejos=%s phase=%s days_to_j1=%s",
        diagnostico_plantilla.get("salud_score"),
        len(diagnostico_plantilla.get("structural_needs") or []),
        len(diagnostico_plantilla.get("consejos") or []),
        competition_phase,
        comp.get("days_to_kickoff"),
    )

    opportunities = classify_market_opportunities(
        market_ext,
        perf_idx,
        price_series,
        float(me.get("balance") or 0),
        diagnosis,
        allow_synthetic=not honest_live,
        structural_needs=diagnostico_plantilla.get("structural_needs") or [],
        diagnostico_plantilla=diagnostico_plantilla,
        squad=squad,
        competition_phase=competition_phase,
    )
    rivals = [estimate_rival_liquidity(r) for r in league.get("rivals", [])]

    # FF production también en plantillas rivales (upgrades / clauses)
    rival_flat: list[dict[str, Any]] = []
    for r in rivals:
        for p in r.get("squad") or []:
            rival_flat.append(dict(p))
    if rival_flat:
        rival_ff, _ = enrich_players_with_ff_production(
            rival_flat,
            points_phase=points_phase,
            market_universe=market_ext,
        )
        by_id = {str(p.get("id")): p for p in rival_ff if p.get("id")}
        for r in rivals:
            r["squad"] = [by_id.get(str(p.get("id")), p) for p in (r.get("squad") or [])]


    # Cláusulas: enriquecer top jugadores de plantillas rivales (AJAX fail-soft)
    clause_targets: list[dict[str, Any]] = []
    seen_clause: set[str] = set()
    for r in rivals:
        for p in sorted(r.get("squad") or [], key=lambda x: -float(x.get("price") or 0))[:5]:
            pid = str(p.get("id") or "")
            if not pid or pid in seen_clause:
                continue
            seen_clause.add(pid)
            clause_targets.append(dict(p))
    clause_targets, clause_meta = enrich_players_with_clauses(clause_targets, max_lookups=24)
    clause_by_id = {str(p["id"]): p for p in clause_targets if p.get("id")}
    for r in rivals:
        new_squad = []
        for p in r.get("squad") or []:
            enriched = clause_by_id.get(str(p.get("id")))
            new_squad.append({**p, **enriched} if enriched else p)
        r["squad"] = new_squad
        r["key_players"] = [
            {
                "id": p["id"],
                "name": p["name"],
                "position": p["position"],
                "price": p.get("price"),
                "clause": p.get("clause"),
                "clause_known": p.get("clause_known"),
                "market_value": p.get("market_value") or p.get("price"),
                "points": p.get("points"),
                "mister_avg": p.get("mister_avg") or p.get("form"),
                "points_trend": p.get("points_trend"),
                "prior_avg": p.get("prior_avg"),
            }
            for p in sorted(new_squad, key=lambda x: -float(x.get("price") or 0))[:5]
        ]

    phase_universe: list[dict[str, Any]] = list(squad) + list(market_ext)
    for r in rivals:
        phase_universe.extend(r.get("squad") or [])
    # Refinar fase con rivales (puede matizar active vs preseason)
    points_phase = detect_points_phase(phase_universe)
    comp = detect_competition_phase(
        season_start=getattr(config, "SEASON_START_DATE", "2026-08-15"),
        points_phase=points_phase,
    )
    competition_phase = str(comp.get("competition_phase") or "preseason")
    diagnostico_plantilla["points_phase"] = points_phase
    diagnostico_plantilla["competition_phase"] = competition_phase
    diagnostico_plantilla["days_to_kickoff"] = comp.get("days_to_kickoff")
    diagnostico_plantilla["season_start"] = comp.get("season_start")

    opportunities = annotate_market_budget_risk(
        opportunities,
        rivals,
        float(me.get("balance") or 0),
        points_phase=points_phase,
    )
    rival_upgrades = build_rival_upgrade_targets(
        me,
        diagnosis,
        rivals,
        balance=float(me.get("balance") or 0),
        points_phase=points_phase,
    )

    owned = set(league.get("owned_across_league") or [])
    owned.update(p["id"] for p in squad)
    owned.update(p["id"] for p in league.get("market", []))

    free_agents = find_free_agents_top(
        league.get("pool_top", []),
        owned,
        perf_idx,
        allow_synthetic=not honest_live,
    )

    recommendations: list[dict[str, Any]] = []
    squad_notes: list[dict[str, Any]] = []

    action_plan, daily_package = build_action_plan(
        me,
        diagnosis,
        opportunities,
        rivals,
        price_series=price_series,
        rival_upgrades=rival_upgrades,
        points_phase=points_phase,
        diagnostico_plantilla=diagnostico_plantilla,
    )

    free_note = live_meta.get("free_agents_source") or ("seed" if free_agents and not honest_live else "unavailable")
    bal = float(me.get("balance") or 0)
    funding_info = estimate_gap_funding(
        diagnostico_plantilla.get("structural_needs") or [],
        opportunities,
        bal,
        top_n=3,
    )
    budget_pressure = "low"
    shortfall = float(funding_info.get("funding_shortfall") or 0)
    target = float(funding_info.get("funding_target") or 0)
    if shortfall > 0 and target > 0 and shortfall >= target * 0.35:
        budget_pressure = "high"
    elif shortfall > 0 or (target > 0 and bal < target):
        budget_pressure = "medium"
    else:
        med_bids = sorted(
            float(o.get("puja_recomendada") or o.get("price") or 0)
            for o in opportunities
            if o.get("fills_need")
        )
        if med_bids:
            mid = med_bids[len(med_bids) // 2]
            if bal < mid * 0.5:
                budget_pressure = "high"
            elif bal < mid:
                budget_pressure = "medium"

    external_notes = [
        "Fuentes externas (FF/JP/Comuniate): estado, % titular y chollos; fail-soft con caché/seed.",
        "FotMob: rating/minutos/goles/xG de los últimos 5 partidos (fuente primaria de nota reciente).",
        (
            f"External matched={external_meta.get('matched')} "
            f"FF={external_meta.get('futbolfantasy')} "
            f"JP={external_meta.get('jornadaperfecta')} "
            f"Com={external_meta.get('comuniate')} "
            f"FotMob={external_meta.get('fotmob')} "
            f"notas={external_meta.get('fotmob_filled', 0)}"
            + (" (caché FF/JP)" if external_meta.get("cache_used") else "")
        ),
        (
            f"Cláusulas Mister: {clause_meta.get('clauses')} "
            f"(known={clause_meta.get('known')}/{clause_meta.get('lookups')})."
        ),
    ]
    if live_meta.get("pool_size"):
        external_notes.append(
            f"Pool Mister completo: {live_meta.get('pool_size')} "
            f"(libres={live_meta.get('pool_free_count')}, "
            f"owned={live_meta.get('pool_owned_count')})."
        )
    if not free_agents:
        external_notes.append(
            "Libres: no disponibles de forma fiable — el KPI no inventa cracks."
        )
    base_notes = live_meta.get("notes") if honest_live else [
        "Modo demo/mock: parte de PPG y libres TOP son seed local.",
    ]

    now = datetime.now(timezone.utc)
    payload = {
        "generated_at": now.isoformat(),
        "sources": {
            "mister": mister_source,
            "performance": perf_source,
            "honest_live": honest_live,
            "external": {
                "futbolfantasy": external_meta.get("futbolfantasy", "fail"),
                "jornadaperfecta": external_meta.get("jornadaperfecta", "fail"),
                "comuniate": external_meta.get("comuniate", "fail"),
                "sofascore": "skip",
                "fotmob": external_meta.get("fotmob", "skip"),
                "matched": external_meta.get("matched", 0),
                "sofascore_filled": external_meta.get("sofascore_filled", 0),
                "fotmob_matched": external_meta.get("fotmob_matched", 0),
                "fotmob_filled": external_meta.get("fotmob_filled", 0),
                "cache_used": bool(external_meta.get("cache_used")),
            },
            "rivals_squads": bool(live_meta.get("rivals_squads_ok")),
            "clauses": clause_meta.get("clauses", "skip"),
            "clauses_known": clause_meta.get("known", 0),
            "points_phase": points_phase,
            "competition_phase": competition_phase,
            "season_start": comp.get("season_start") or getattr(config, "SEASON_START_DATE", "2026-08-15"),
            "free_agents": free_note,
            "pool_size": live_meta.get("pool_size") or 0,
            "pool_free": live_meta.get("pool_free_count") or len(free_agents),
            "pool_owned": live_meta.get("pool_owned_count") or 0,
            "daily_market_count": n_market_day,
            "market_day_slots": int(getattr(config, "MARKET_DAY_SLOTS", 16)),
        },
        "league": league.get("league", {}),
        "me": {
            "team_id": me.get("team_id"),
            "manager": me.get("manager"),
            "team_name": me.get("team_name"),
            "balance": me.get("balance"),
            "squad_value": me.get("squad_value"),
            "rank": me.get("rank"),
            "points": me.get("points"),
            "formation": me.get("formation"),
            "squad": squad,
        },
        "kpis": {
            "balance": me.get("balance"),
            "squad_value": me.get("squad_value"),
            "rank": me.get("rank"),
            "top_free_remaining": len(free_agents),
            "pool_size": live_meta.get("pool_size") or 0,
            "critical_alerts": sum(1 for a in diagnosis["alerts"] if a["level"] == "critical"),
            "market_count": len(opportunities),
            "rivals_count": len(rivals),
            "buy_now_count": sum(1 for a in action_plan if a["action"] == "buy_now"),
            "wait_count": sum(1 for a in action_plan if a["action"] == "wait"),
            "sell_count": sum(1 for a in action_plan if a["action"] == "sell"),
            "clause_bid_count": sum(1 for a in action_plan if a["action"] == "clause_bid"),
            "budget_pressure": budget_pressure,
            "funding_target": funding_info.get("funding_target"),
            "funding_shortfall": funding_info.get("funding_shortfall"),
            "points_phase": points_phase,
            "competition_phase": competition_phase,
            "season_start": comp.get("season_start"),
            "days_to_kickoff": comp.get("days_to_kickoff"),
            "lines_ok": diagnostico_plantilla.get("lines_ok"),
            "depth_gaps": diagnostico_plantilla.get("depth_gaps"),
            "daily_market_count": n_market_day,
            "market_day_slots": int(getattr(config, "MARKET_DAY_SLOTS", 16)),
            "ideal_squad": diagnostico_plantilla.get("ideal_squad")
            or getattr(config, "IDEAL_SQUAD", {"GK": 2, "DF": 5, "MF": 5, "FW": 3}),
        },
        "funding_plan": {
            "target": funding_info.get("funding_target"),
            "shortfall": funding_info.get("funding_shortfall"),
            "cash_tight": funding_info.get("cash_tight"),
            "gaps": funding_info.get("gap_costs") or [],
            "positions": funding_info.get("positions") or [],
        },
        "daily_package": daily_package,
        "action_plan": action_plan,
        "rival_upgrades": rival_upgrades,
        "market_opportunities": opportunities,
        "squad_diagnosis": diagnosis,
        "diagnostico_plantilla": diagnostico_plantilla,
        "rivals": rivals,
        "free_agents_top": free_agents,
        "recommendations": recommendations,
        "squad_notes": squad_notes,
        "meta": {
            "filters_hint": {
                "positions": ["GK", "DF", "MF", "FW"],
                "priorities": ["Alta", "Media", "Baja"],
                "categories": [
                    "ajuste_estructural",
                    "chollo_economico",
                    "titular_garantizado",
                    "especulacion_trading",
                    "alerta_baja",
                ],
                "actions": ["buy_now", "clause_bid", "wait", "avoid", "sell", "scout"],
            },
            "history_retention_days": config.HISTORY_RETENTION_DAYS,
            "live_meta": live_meta,
            "free_agents_note": (
                (
                    f"Libres del pool Mister: {len(free_agents)} "
                    f"(universo={live_meta.get('pool_size') or len(free_agents)})."
                )
                if free_agents
                else "Sin pool de libres fiable hoy (modo honesto: no inventamos TOP)."
            ),
            "pool_size": live_meta.get("pool_size") or 0,
            "season_start": comp.get("season_start"),
            "days_to_kickoff": comp.get("days_to_kickoff"),
            "competition_phase": competition_phase,
            "data_notes": list(base_notes or []) + external_notes + [
                (
                    f"Campeonato: J1 {comp.get('season_start')} "
                    f"(faltan {comp.get('days_to_kickoff')} días) · fase {competition_phase}."
                ),
                (
                    "Objetivo plantilla 15 (GK2/DF5/MF5/FW3): pujar si falta cobertura; "
                    "si la línea ya está cubierta, no insistir salvo upgrade."
                ),
                (
                    f"Mercado de hoy: {n_market_day} jugadores "
                    f"(referencia {getattr(config, 'MARKET_DAY_SLOTS', 16)} plazas/día)."
                ),
            ],
        },
    }
    return payload


def prune_history(retention_days: int = config.HISTORY_RETENTION_DAYS) -> None:
    if not config.HISTORY_DIR.exists():
        return
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=retention_days)
    for path in config.HISTORY_DIR.glob("*.json"):
        try:
            day = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            path.unlink(missing_ok=True)
            log.info("Snapshot antiguo eliminado: %s", path.name)


def write_outputs(payload: dict[str, Any]) -> None:
    save_json(config.LATEST_DATA_PATH, payload)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Snapshot ligero para series de precios (no duplicar todo el diagnóstico si se desea,
    # pero guardamos el payload completo para análisis competitivo histórico).
    snap_path = config.HISTORY_DIR / f"{day}.json"
    save_json(snap_path, payload)
    prune_history()
    log.info("Escrito %s", config.LATEST_DATA_PATH)
    log.info("Snapshot %s", snap_path)


def main() -> int:
    log.info("=== Mister Fantasy Advisor — data engine ===")
    log.info("USE_MISTER_MOCK=%s USE_PERF_SEED=%s", config.USE_MISTER_MOCK, config.USE_PERF_SEED)
    payload = build_payload()
    write_outputs(payload)
    log.info(
        "OK — oportunidades=%s libres=%s recomendaciones=%s",
        len(payload["market_opportunities"]),
        len(payload["free_agents_top"]),
        len(payload["recommendations"]),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
