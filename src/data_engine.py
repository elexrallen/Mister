"""
Mister Fantasy Advisor — Motor de datos (data_engine.py)

Pipeline batch multi-fuente:
  1) Mister Fantasy (API o mock) → estado de la liga privada
  2) Snapshots slim de precios en public/data/leagues/<slug>/history/
     → tendencias de mercado (Δvalor 3–5 días)
  3) API-Football o seed local → rendimiento multi-temporada
  4) Algoritmos → carencias, oportunidades, libres TOP, recomendaciones

Salida: public/data/leagues/<slug>/latest_data.json (+ snapshot de precios del día).
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
from mister_client import (
    discover_leagues,
    enrich_players_with_clauses,
    fetch_live_league,
    mister_player_photo_url,
    mister_team_logo_url,
)
from league_rules import (
    ff_hint_for_provider,
    merge_rules_into_league_cfg,
    normalize_rules,
)
from daily_playbook import build_daily_playbook, playbook_to_recommendations, resolve_phase
from external_data import enrich_players_with_external, enrich_players_with_ff_production
from fixture_difficulty import (
    annotate_players_with_fdr,
    build_fantasy_conceded,
    build_team_prior,
    build_team_strength,
)
from expected_points import annotate_players_with_xpts
from model_calibration import build_calibration
from fotmob_service import enrich_players_with_fotmob
from scrapers.ff_points import THIN_APPS, resolve_avg_scale, scale_threshold
from scrapers.http_util import rate_limit_report, reset_rate_limits
from competitive_actions import (
    annotate_market_budget_risk,
    build_gw_xi_advice,
    build_recommended_gw_xi,
    build_rival_upgrade_targets,
    build_sell_opportunities,
    detect_competition_phase,
    detect_points_phase,
    estimate_gap_funding,
    evaluate_bid_finance,
    finalize_action_plan,
    is_key_market_candidate,
    liquidity_balance,
    mister_bid_cap,
    other_gaps_min_cost,
    promote_funded_swaps,
    promote_appreciation_plays,
    reconcile_avoid_conflicts,
    resolve_hours_to_jornada,
    rival_demand_for_position,
    sells_settle_before_d1,
    set_matchday_phase,
    solvency_strict_window,
    tag_rival_market_listings,
    trade_asset_score,
    wait_risk,
)
from target_board import (
    board_objective_ids,
    board_primary_ids,
    build_target_board,
    funding_plan_from_board,
    max_patch_spend,
    patches_allowed,
    save_target_board,
)
from squad_analyzer import (
    analyze_squad,
    apply_realistic_need_caps,
    assess_market_coverage,
    comparable_ff_signal,
    is_clear_overstock_upgrade,
    market_value_justification,
    merge_structural_into_diagnosis,
    structural_market_boost,
    upgrade_worth_buy,
)
from market_cycle import (
    adjust_funding_for_bootstrap,
    derive_cash_lag_hours,
    derive_cycle_hours,
    resolve_bootstrap_xi,
    resolve_market_cycle,
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

def attach_mister_assets(
    rows: list[dict[str, Any]],
    *,
    player_index: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Añade retrato y escudo oficiales sin hacer depender la UI del caché local."""
    idx = player_index or {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("id") or row.get("player_id") or "").strip()
        source = idx.get(pid) or {}
        team_id = str(row.get("team_id") or source.get("team_id") or "").strip()
        if team_id and not row.get("team_id"):
            row["team_id"] = team_id
        if not row.get("team") and source.get("team"):
            row["team"] = source.get("team")
        row["photo_url"] = (
            row.get("photo_url")
            or source.get("photo_url")
            or mister_player_photo_url(pid)
        )
        row["team_logo_url"] = (
            row.get("team_logo_url")
            or source.get("team_logo_url")
            or mister_team_logo_url(team_id)
        )
    return rows


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


FOTMOB_MARKET_FOCUS = 24


def fotmob_focus_ids(
    squad: list[dict[str, Any]],
    market: list[dict[str, Any]],
    *,
    market_limit: int = FOTMOB_MARKET_FOCUS,
) -> set[str]:
    """
    Jugadores que merecen una petición a FotMob: toda la plantilla propia y los
    candidatos de mercado con más papeletas de acabar en el board. El resto del
    universo se queda sin nota y no gasta presupuesto de red.
    """
    ids = {str(p.get("id")) for p in squad if p.get("id")}

    def _interest(p: dict[str, Any]) -> float:
        ext = p.get("external") or {}
        score = 0.0
        try:
            score += float(p.get("mister_avg") or p.get("form") or 0) * 2.0
        except (TypeError, ValueError):
            pass
        try:
            score += float(p.get("gw_points_sum") or 0) * 0.2
        except (TypeError, ValueError):
            pass
        prob = ext.get("gw_lineup_prob")
        try:
            score += float(prob) / 20.0 if prob is not None else 0.0
        except (TypeError, ValueError):
            pass
        if ext.get("is_top_ff"):
            score += 3.0
        return score

    ranked = sorted(
        (p for p in market if p.get("id") and str(p.get("id")) not in ids),
        key=_interest,
        reverse=True,
    )
    for p in ranked[: max(0, int(market_limit))]:
        ids.add(str(p.get("id")))
    return ids


# ---------------------------------------------------------------------------
# Capa 1 — Mister Fantasy (ajax live + mock)
# ---------------------------------------------------------------------------

def fetch_mister_league(community_id: str | None = None) -> tuple[dict[str, Any], str]:
    """
    Intenta leer Mister con cookie JWT (`token`) + header `x-auth`.
    Si community_id se pasa, cambia a esa comunidad antes del scrape.
    Si no hay credenciales o falla → mock local.
    """
    if config.USE_MISTER_MOCK:
        log.info("Sin MISTER_TOKEN/MISTER_COOKIE → mock (%s)", config.MOCK_DATA_PATH.name)
        return load_json(config.MOCK_DATA_PATH), "mock"

    try:
        log.info("Conectando a Mister (/ajax/*) community=%s...", community_id or "sesión")
        live = fetch_live_league(community_id=community_id)
        if not live or not all(k in live for k in ("me", "market", "rivals", "pool_top")):
            log.warning("Sesión OK parcial o shape incompleto → fallback a mock")
            return load_json(config.MOCK_DATA_PATH), "mock"
        meta = live.get("_live_meta", {})
        log.info("Mister live OK — meta=%s", {k: meta.get(k) for k in (
            "id_community", "competition", "id_competition", "balance_ok", "market_ok", "pool_size"
        )})
        return live, "api"
    except Exception as exc:  # noqa: BLE001
        log.warning("Mister live falló (%s) → fallback a mock", exc)
        return load_json(config.MOCK_DATA_PATH), "mock"


# ---------------------------------------------------------------------------
# Capa 2 — Histórico de mercado (snapshots locales)
# ---------------------------------------------------------------------------

def _player_price_pairs_from_payload(payload: dict[str, Any]) -> dict[str, float]:
    """Extrae id→precio de un latest_data (o snapshot legacy completo)."""
    prices: dict[str, float] = {}

    def _absorb(items: Any) -> None:
        if not isinstance(items, list):
            return
        for p in items:
            if not isinstance(p, dict):
                continue
            pid = p.get("id")
            price = p.get("price")
            if pid is None or price is None:
                continue
            try:
                prices[str(pid)] = float(price)
            except (TypeError, ValueError):
                continue

    _absorb(payload.get("market_opportunities"))
    _absorb((payload.get("me") or {}).get("squad"))
    _absorb(payload.get("free_agents_top"))
    for rival in payload.get("rivals") or []:
        if isinstance(rival, dict):
            _absorb(rival.get("squad"))
    return prices


def build_team_name_index(
    table: dict[str, dict[str, Any]] | None,
    pool: list[dict[str, Any]] | None,
) -> dict[str, str]:
    """`{team_id: nombre}` para poder decir 'vs Barcelona' y no 'vs 178'."""
    out: dict[str, str] = {}
    for tid, row in (table or {}).items():
        name = (row or {}).get("name") if isinstance(row, dict) else None
        if tid and name:
            out[str(tid)] = str(name)
    for p in pool or []:
        tid = str(p.get("team_id") or "").strip()
        name = p.get("team")
        if tid and name and tid not in out:
            out[tid] = str(name)
    return out


def merge_matchday_sources(
    mister_md: dict[str, Any] | None,
    ff_md: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """
    Mister manda en jornada, fechas y rival (dato oficial de la propia liga);
    FF aporta la previa con % de titularidad. Si Mister no responde, cae a FF.
    """
    if not mister_md or not (mister_md.get("fixtures") or []):
        if not ff_md:
            return None
        out = dict(ff_md)
        out.setdefault("source", "futbolfantasy")
        out["sources"] = ["futbolfantasy"]
        return out

    out = dict(mister_md)
    out["source"] = mister_md.get("source") or "mister"
    sources = [out["source"]]
    if ff_md:
        sources.append("futbolfantasy")
        out["ff_status"] = ff_md.get("status")
        out["ff_fixtures_count"] = ff_md.get("fixtures_count")
        if out.get("jornada") is None:
            out["jornada"] = ff_md.get("jornada")
        if not out.get("competition"):
            out["competition"] = ff_md.get("competition")
    out["sources"] = sources
    return out


def _player_points_from_payload(payload: dict[str, Any]) -> tuple[dict[str, list[int | None]], dict[str, int]]:
    """
    id → racha de puntos por jornada (`recent_gw_points`) y puntos de la jornada
    en curso (`gw_points`). None dentro de la racha = jornada sin jugar.
    """
    by_gw: dict[str, list[int | None]] = {}
    current: dict[str, int] = {}

    def _absorb(items: Any) -> None:
        if not isinstance(items, list):
            return
        for p in items:
            if not isinstance(p, dict):
                continue
            pid = p.get("id")
            if pid is None:
                continue
            pid = str(pid)
            streak = p.get("recent_gw_points")
            if isinstance(streak, list) and streak and pid not in by_gw:
                clean: list[int | None] = []
                for v in streak:
                    try:
                        clean.append(int(v) if v is not None else None)
                    except (TypeError, ValueError):
                        clean.append(None)
                by_gw[pid] = clean
            gw_pts = p.get("gw_points")
            if gw_pts is not None and pid not in current:
                try:
                    current[pid] = int(gw_pts)
                except (TypeError, ValueError):
                    pass

    _absorb(payload.get("market_opportunities"))
    _absorb((payload.get("me") or {}).get("squad"))
    _absorb(payload.get("free_agents_top"))
    for rival in payload.get("rivals") or []:
        if isinstance(rival, dict):
            _absorb(rival.get("squad"))
    return by_gw, current


def _player_xpts_from_payload(payload: dict[str, Any]) -> dict[str, list[float]]:
    """id → `[xpts, p_play]` predichos hoy, para poder juzgarlos tras la jornada."""
    out: dict[str, list[float]] = {}

    def _absorb(items: Any) -> None:
        if not isinstance(items, list):
            return
        for p in items:
            if not isinstance(p, dict) or p.get("id") is None:
                continue
            pid = str(p["id"])
            if pid in out or p.get("xpts") is None:
                continue
            try:
                xpts = round(float(p["xpts"]), 2)
            except (TypeError, ValueError):
                continue
            try:
                p_play = round(float(p.get("xpts_p_play")), 2)
            except (TypeError, ValueError):
                p_play = None
            out[pid] = [xpts, p_play] if p_play is not None else [xpts]

    _absorb((payload.get("me") or {}).get("squad"))
    _absorb(payload.get("market_opportunities"))
    _absorb(payload.get("free_agents_top"))
    return out


def build_price_history_snapshot(payload: dict[str, Any], *, day: str | None = None) -> dict[str, Any]:
    """
    Snapshot slim diario: precios + puntos por jornada (no el payload completo).
    Los puntos permiten medir a posteriori si las decisiones acertaron.
    """
    day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    points_by_gw, gw_points = _player_points_from_payload(payload)
    matchday = payload.get("matchday") if isinstance(payload.get("matchday"), dict) else {}
    return {
        "date": day,
        "league_slug": payload.get("league_slug"),
        "jornada": matchday.get("jornada"),
        "gameweek_status": matchday.get("gameweek_status"),
        "prices": _player_price_pairs_from_payload(payload),
        "points_by_gw": points_by_gw,
        "gw_points": gw_points,
        "xpts": _player_xpts_from_payload(payload),
    }


def load_history_snapshots(slug: str, days: int = 45) -> list[dict[str, Any]]:
    """Snapshots diarios de la liga, del más antiguo al más reciente."""
    history_dir = config.league_history_dir(slug)
    if not history_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for snap_path in sorted(history_dir.glob("*.json"))[-days:]:
        try:
            snap = load_json(snap_path)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(snap, dict):
            out.append(snap)
    return out


def load_points_history_for_league(slug: str, days: int = 30) -> dict[str, list[dict[str, Any]]]:
    """
    Histórico de puntos por jugador: `{player_id: [{date, jornada, points}]}`.
    Solo jornadas cerradas (una entrada por jornada, la última observada gana).
    """
    out: dict[str, dict[Any, dict[str, Any]]] = {}
    for snap in load_history_snapshots(slug, days):
        jornada = snap.get("jornada")
        date = snap.get("date")
        for pid, pts in (snap.get("gw_points") or {}).items():
            if pts is None:
                continue
            out.setdefault(str(pid), {})[jornada] = {
                "date": date,
                "jornada": jornada,
                "points": pts,
            }
    return {
        pid: sorted(rows.values(), key=lambda r: (r.get("jornada") is None, r.get("jornada"), r.get("date")))
        for pid, rows in out.items()
    }


def _prices_from_history_file(snap: dict[str, Any]) -> dict[str, float]:
    """Lee precios de snapshot slim o legacy (payload completo)."""
    raw = snap.get("prices")
    if isinstance(raw, dict) and raw:
        out: dict[str, float] = {}
        for pid, price in raw.items():
            try:
                out[str(pid)] = float(price)
            except (TypeError, ValueError):
                continue
        return out
    return _player_price_pairs_from_payload(snap)


def _series_from_history_dir(history_dir: Path, days: int) -> dict[str, list[float]]:
    if not history_dir.exists():
        return {}
    snaps = sorted(history_dir.glob("*.json"))[-days:]
    series: dict[str, list[float]] = {}
    for snap_path in snaps:
        try:
            snap = load_json(snap_path)
        except Exception:  # noqa: BLE001
            continue
        for pid, price in _prices_from_history_file(snap).items():
            series.setdefault(pid, []).append(price)
    return series


def load_recent_price_map(days: int = 5) -> dict[str, list[float]]:
    """
    Fallback: history global legacy (public/data/history/).
    Preferir load_recent_price_map_for_league.
    """
    return _series_from_history_dir(config.HISTORY_DIR, days)


def load_recent_price_map_for_league(slug: str, days: int = 5) -> dict[str, list[float]]:
    """Histórico de precios por liga; fallback al history global legacy."""
    series = _series_from_history_dir(config.league_history_dir(slug), days)
    if series:
        return series
    return load_recent_price_map(days=days)


def compute_delta_from_history(player_id: str, current_price: float, series: dict[str, list[float]]) -> float | None:
    prices = series.get(str(player_id)) or []
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
    market_mode: str = "auction",
    max_debt: float | None = None,
    balance_future: float | None = None,
    hours_to_jornada: float | None = None,
    days_to_kickoff: float | int | None = None,
    matchday: dict[str, Any] | None = None,
    sell_proceeds_timely: float = 0.0,
) -> list[dict[str, Any]]:
    """
    Clasifica oportunidades: carencias, titularidad, cobertura por posición.
    Insiste si falta profundidad; demota si la línea ya está cubierta.
    market_mode=fixed → precio listado sin sobrepuja.
    Techo Mister = max_debt; solvencia pre-jornada bloquea deuda en D-1.
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
    fixed = (market_mode or "auction") == "fixed"
    bid_ceiling_cap = mister_bid_cap(my_balance, max_debt)
    cash_lag = float(int(getattr(config, "MARKET_CYCLE_HOURS", 24) or 24) * 2)

    opportunities: list[dict[str, Any]] = []
    for raw in market:
        p = enrich_player(raw, perf_idx, allow_synthetic=allow_synthetic)
        price = float(p.get("price") or 0)
        delta = compute_delta_from_history(p["id"], price, price_series)
        if delta is None and p.get("price_delta_1d") is not None:
            # Δ real de Mister (value vs prev_value); menos ventana que 5d pero no inventado
            delta = float(p.get("price_delta_1d") or 0)
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
        overstocked = bool(cov.get("overstocked"))

        struct_bonus, fills_structural, struct_label = structural_market_boost(p, needs)

        # Línea needy solo si el jugador cubre gap real / need estructural
        # (overstock o GK con titular: no inflar a toda la posición)
        position_needy = p["position"] in needy
        if position_needy and not fills_coverage_gap and not fills_structural:
            if overstocked:
                position_needy = False
            elif p["position"] == "GK":
                gk_starters = 0
                try:
                    gk_starters = int(
                        ((diagnostico_plantilla or {}).get("lineas") or {})
                        .get("GK", {})
                        .get("starters_real")
                        or 0
                    )
                except (TypeError, ValueError):
                    gk_starters = 0
                if gk_starters <= 0 and squad:
                    gk_starters = sum(
                        1
                        for s in squad
                        if s.get("position") == "GK"
                        and not (s.get("injury") or (s.get("external") or {}).get("availability") in ("injured", "suspended"))
                        and float(s.get("lineup_prob") or 0) >= float(getattr(config, "LINEUP_PROB_TITULAR", 0.70))
                    )
                if gk_starters >= 1:
                    position_needy = False

        categories: list[str] = []
        if delta is not None and delta >= config.CHOLLO_DELTA_MIN and rel_price <= 1.15:
            categories.append("chollo_economico")
        elif trend == "up" and rel_price <= 1.1:
            categories.append("chollo_economico")
        scale = resolve_avg_scale(p)
        if (avg_ppg is not None and float(avg_ppg) >= scale_threshold(5.2, scale) and reliability and float(reliability) >= 0.5):
            categories.append("titular_garantizado")
        elif form is not None and float(form) >= scale_threshold(5.5, scale):
            categories.append("titular_garantizado")
        if delta is not None and delta >= 0.10:
            categories.append("especulacion_trading")
        elif trend == "up" and (form is None or float(form) < scale_threshold(4.0, scale)):
            categories.append("especulacion_trading")
        elif (
            (trend == "up" or (delta is not None and delta >= float(getattr(config, "APPRECIATION_DELTA_MIN", 0.04))))
            and float(p.get("lineup_prob") or 0) >= float(getattr(config, "APPRECIATION_LINEUP_MIN", 0.45))
        ):
            categories.append("especulacion_trading")
        if not categories:
            if position_needy or fills_coverage_gap:
                categories.append("titular_garantizado")
            elif rel_price < 0.85:
                categories.append("chollo_economico")
            else:
                categories.append("titular_garantizado")

        list_price = money(p.get("min_bid") or price)
        if fixed:
            premium = 0.0
            recommended = list_price
            bid_ceiling = list_price
        else:
            premium = 0.03
            if position_needy or fills_coverage_gap:
                premium += 0.05 if preseasonish else 0.04
            if fills_coverage_gap and on_daily:
                premium += 0.02
            if trend == "up":
                premium += 0.01
            if line_covered and not is_upgrade:
                premium = min(premium, 0.03)
            recommended = money(list_price * (1 + premium))
            bid_ceiling = money(list_price * (1 + premium + 0.05))
        min_bid = list_price
        # Capear techos de puja al maxDebt Mister
        if bid_ceiling_cap > 0:
            recommended = min(recommended, bid_ceiling_cap)
            bid_ceiling = min(bid_ceiling, bid_ceiling_cap)

        score = 0.0
        if delta is not None:
            score += float(delta) * 40
        elif trend == "up":
            score += 8
        elif trend == "down":
            score -= 4
        # Perspectiva de minutos: la flecha al alza es más creíble
        try:
            lp_frac = float(p.get("lineup_prob") or 0)
            if lp_frac > 1.5:
                lp_frac = lp_frac / 100.0
            if (trend == "up" or (delta is not None and delta >= 0.03)) and lp_frac >= float(
                getattr(config, "APPRECIATION_LINEUP_MIN", 0.45)
            ):
                score += 10
        except (TypeError, ValueError):
            pass
        if form is not None:
            scale = resolve_avg_scale(p)
            score += (float(form) * 3.0) * (8.0 / scale)
        if avg_ppg is not None:
            score += (float(avg_ppg) / resolve_avg_scale(p)) * 15
        if position_needy:
            score += 15
        if fills_coverage_gap:
            score += 22 if preseasonish else 16
        if on_daily and fills_coverage_gap:
            score += 10
        if overstocked and not fills_coverage_gap:
            if is_upgrade and is_clear_overstock_upgrade(p, squad, is_upgrade=True):
                score += 12  # upgrade claro: no penalizar como cupo muerto
            else:
                score -= 35
                if is_upgrade:
                    score -= 10  # upgrade flojo en línea sobrada: no urgencia
        elif line_covered and not is_upgrade:
            score -= 28
        elif is_upgrade:
            score += 12

        fin = evaluate_bid_finance(
            recommended,
            my_balance,
            min_cost=min_bid,
            max_debt=max_debt,
            balance_future=balance_future,
            hours_to_jornada=hours_to_jornada,
            days_to_kickoff=days_to_kickoff,
            matchday=matchday,
            sell_proceeds_timely=sell_proceeds_timely,
            cash_lag_hours=cash_lag,
        )
        blocked = fin.get("budget_fit") == "blocked" or bool(fin.get("solvency_blocked"))
        if blocked:
            score -= 40  # aspiracional: no empujar el plan del día
        elif bool(fin.get("debt_risk")):
            score -= 8
        elif recommended > float(fin.get("liquidity") or 0) * 0.85 and float(fin.get("liquidity") or 0) > 0:
            score -= 12

        ff_apps_raw = p.get("ff_apps")
        if ff_apps_raw is None:
            ff_apps_raw = (p.get("external") or {}).get("ff_apps")
        try:
            ff_apps = int(ff_apps_raw) if ff_apps_raw is not None else None
        except (TypeError, ValueError):
            ff_apps = None
        prior_apps_raw = p.get("ff_prior_apps")
        if prior_apps_raw is None:
            prior_apps_raw = (p.get("external") or {}).get("ff_prior_apps")
        try:
            prior_apps_i = int(prior_apps_raw) if prior_apps_raw is not None else None
        except (TypeError, ValueError):
            prior_apps_i = None
        # Si la actual está vacía, la muestra fiable es la previa
        eff_apps = ff_apps
        if (ff_apps is None or ff_apps <= 0) and prior_apps_i is not None:
            eff_apps = prior_apps_i
        current_thin = ff_apps is not None and 0 < ff_apps < THIN_APPS
        prior_ok = (prior_apps_i or 0) >= THIN_APPS
        if current_thin and prior_ok:
            sample_thin = False
        elif current_thin:
            sample_thin = True
        else:
            sample_thin = eff_apps is not None and eff_apps < THIN_APPS

        prod = p.get("production_score")
        ff_avg = p.get("ff_mister_avg")
        if ff_avg is None:
            ff_avg = (p.get("external") or {}).get("ff_mister_avg")
        # Media 0 / muestra corta: preferir previa si es fiable
        try:
            use_prior_avg = (
                (ff_avg is None or float(ff_avg) <= 0)
                and (ff_apps is None or ff_apps <= 0)
            ) or (current_thin and prior_ok)
            if use_prior_avg:
                pa = p.get("ff_prior_avg")
                if pa is None:
                    pa = (p.get("external") or {}).get("ff_prior_avg")
                if pa is not None and float(pa) > 0 and (prior_apps_i or 0) > 0:
                    ff_avg = float(pa)
        except (TypeError, ValueError):
            pass
        if prod is None:
            prod = (p.get("external") or {}).get("production_score")
        try:
            if prod is not None:
                score += (float(prod) / 100.0) * (18 if preseasonish else 28)
            elif ff_avg is not None and float(ff_avg) > 0:
                score += (float(ff_avg) / resolve_avg_scale(p)) * (14 if preseasonish else 22)
        except (TypeError, ValueError):
            pass
        if current_thin and prior_ok:
            score -= 3  # aviso suave: actual corta, anclado a previa
        elif sample_thin:
            score -= 8
        value_note = None
        try:
            just = market_value_justification(p, price)
            if just:
                value_note = just.get("note")
                if just.get("justified"):
                    score += 8
                elif just.get("expensive"):
                    score -= 12
            elif ff_avg is not None and price > 0 and not sample_thin:
                scale = resolve_avg_scale(p)
                roi = (float(ff_avg) / scale * 8.0) / max(price / 1_000_000, 0.4)
                if roi >= 1.2:
                    score += 8
                elif roi < 0.45 and price >= 5_000_000:
                    score -= 12
        except (TypeError, ValueError):
            pass
        is_top = bool(p.get("is_top_ff") or (p.get("external") or {}).get("is_top_ff"))
        if is_top and not sample_thin:
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
        # Señal jornada FF (posibles alineaciones): más fresca que ficha/JP
        gw_out = bool(p.get("gw_out") or ext.get("gw_out"))
        gw_doubt = bool(p.get("gw_doubt") or ext.get("gw_doubt"))
        gw_starter = bool(p.get("gw_starter") or ext.get("gw_starter"))
        if gw_out:
            score -= 28
            if "alerta_baja" not in categories:
                categories.insert(0, "alerta_baja")
        elif gw_doubt:
            score -= 10
        elif gw_starter:
            score += 14
            if position_needy:
                score += 6
        if ext.get("is_chollo_ext") or ext.get("is_recommendation_ext"):
            score += 10
        rating = ext.get("recent_rating")
        if rating is not None:
            try:
                sv = float(rating)
                if sv > 10:
                    sv = min(9.5, 5.0 + (sv - 5.0) * 0.35)
                if sv >= 7.0:
                    score += 8
                elif sv < 6.2:
                    score -= 8
            except (TypeError, ValueError):
                pass

        score += struct_bonus
        if fills_structural and struct_label:
            categories.insert(0, "ajuste_estructural")
        coverage_label = cov.get("coverage_label") or struct_label

        if avail in ("injured", "suspended") or gw_out:
            priority = "Baja"
        elif blocked:
            # Nunca Alta solo por producción/estructura si no hay caja
            priority = "Media" if score >= 25 else "Baja"
        elif line_covered and not is_upgrade:
            priority = "Baja"
        elif overstocked and not fills_coverage_gap:
            if is_upgrade and is_clear_overstock_upgrade(p, squad, is_upgrade=True):
                priority = "Alta" if score >= 35 else "Media"
            else:
                priority = "Baja" if not is_upgrade else "Media"
        elif fills_coverage_gap or fills_structural or score >= 35:
            priority = "Alta"
        elif is_top and not sample_thin and (
            position_needy or fills_structural or fills_coverage_gap
        ):
            priority = "Alta"
        elif score >= 18:
            priority = "Media"
        else:
            priority = "Baja"

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
                "especulacion_trading": "Revalorización / trading",
                "alerta_baja": "Alerta lesión/sanción",
                "ajuste_estructural": coverage_label or "Ajuste estructural",
            }.get(primary, primary),
            "puja_minima": min_bid,
            "puja_recomendada": recommended,
            "puja_techo": bid_ceiling,
            "priority": priority,
            "score": round(score, 1),
            "affordable": not blocked and fin.get("budget_fit") in ("comfortable", "tight"),
            "budget_fit": fin.get("budget_fit"),
            "bid_cap": fin.get("bid_cap"),
            "debt_risk": bool(fin.get("debt_risk")),
            "solvency_ok": bool(fin.get("solvency_ok")),
            "solvency_blocked": bool(fin.get("solvency_blocked")),
            "solvency_strict": bool(fin.get("solvency_strict")),
            "hours_to_jornada": fin.get("hours_to_jornada"),
            "ff_apps": ff_apps,
            "sample_thin": sample_thin,
            "current_sample_thin": current_thin,
            "prior_backed": bool(current_thin and prior_ok),
            "value_note": value_note,
            "fills_need": position_needy or fills_structural or fills_coverage_gap,
            "fills_structural": fills_structural,
            "structural_label": coverage_label or struct_label,
            "fills_coverage_gap": fills_coverage_gap,
            "line_already_covered": line_covered,
            "is_upgrade": is_upgrade,
            "overstocked": overstocked,
            "position_coverage": cov.get("position_coverage"),
            "on_daily_market": on_daily,
            "signal_basis": "mister_live" if not allow_synthetic else "mixed",
            "market_mode": "fixed" if fixed else "auction",
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
    balance: float | None = None,
    max_debt: float | None = None,
    balance_future: float | None = None,
    hours_to_jornada: float | None = None,
    days_to_kickoff: float | int | None = None,
    matchday: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Cracks del pool TOP no fichados. Lista vacía si no hay pool real."""
    if not pool_top:
        return []
    bal = float(balance) if balance is not None else None
    free: list[dict[str, Any]] = []
    for raw in pool_top:
        if raw["id"] in owned_ids:
            continue
        p = enrich_player(raw, perf_idx, allow_synthetic=allow_synthetic)
        price = float(p.get("price") or 1)
        ppg = float(p.get("avg_ppg") or 0)
        roi = ppg / (price / 1_000_000) if price else 0
        ff_apps_raw = p.get("ff_apps")
        if ff_apps_raw is None:
            ff_apps_raw = (p.get("external") or {}).get("ff_apps")
        try:
            ff_apps = int(ff_apps_raw) if ff_apps_raw is not None else None
        except (TypeError, ValueError):
            ff_apps = None
        prior_apps_raw = p.get("ff_prior_apps")
        if prior_apps_raw is None:
            prior_apps_raw = (p.get("external") or {}).get("ff_prior_apps")
        try:
            prior_apps_i = int(prior_apps_raw) if prior_apps_raw is not None else None
        except (TypeError, ValueError):
            prior_apps_i = None
        eff_apps = ff_apps
        if (ff_apps is None or ff_apps <= 0) and prior_apps_i is not None:
            eff_apps = prior_apps_i
        current_thin = ff_apps is not None and 0 < ff_apps < THIN_APPS
        prior_ok = (prior_apps_i or 0) >= THIN_APPS
        if current_thin and prior_ok:
            sample_thin = False
        elif current_thin:
            sample_thin = True
        else:
            sample_thin = eff_apps is not None and eff_apps < THIN_APPS
        row: dict[str, Any] = {
            **p,
            "roi_ppg_per_million": round(roi, 3),
            "why_free": "Aún no ha salido / nadie lo ha fichado en la liga",
            "ff_apps": ff_apps,
            "sample_thin": sample_thin,
            "current_sample_thin": current_thin,
            "prior_backed": bool(current_thin and prior_ok),
        }
        if bal is not None:
            fin = evaluate_bid_finance(
                price,
                bal,
                min_cost=price,
                max_debt=max_debt,
                balance_future=balance_future,
                hours_to_jornada=hours_to_jornada,
                days_to_kickoff=days_to_kickoff,
                matchday=matchday,
            )
            bf = str(fin.get("budget_fit") or "blocked")
            row["budget_fit"] = bf
            row["affordable"] = bf in ("comfortable", "tight")
            row["debt_risk"] = bool(fin.get("debt_risk"))
            row["solvency_blocked"] = bool(fin.get("solvency_blocked"))
            row["bid_cap"] = fin.get("bid_cap")
            if bf in ("comfortable", "tight"):
                row["target_tier"] = "realistic"
            elif bf == "stretch":
                row["target_tier"] = "stretch"
            else:
                row["target_tier"] = "aspirational"
        free.append(row)
    # Preferir asequibles y muestra fiable; PPG como desempate
    free.sort(
        key=lambda x: (
            0 if x.get("target_tier") == "realistic" else (1 if x.get("target_tier") == "stretch" else 2),
            1 if x.get("sample_thin") else 0,
            -float(x.get("production_score") or x.get("avg_ppg") or 0),
            -float(x.get("reliability") or 0),
        )
    )
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


def _rival_demand_for_position(
    rivals: list[dict[str, Any]],
    position: str,
    *,
    market_mode: str = "auction",
) -> list[dict[str, Any]]:
    return rival_demand_for_position(rivals, position, market_mode=market_mode)


def _wait_risk(
    o: dict[str, Any],
    rivals: list[dict[str, Any]],
    *,
    fills_need: bool,
    market_mode: str = "auction",
) -> str:
    return wait_risk(o, rivals, fills_need=fills_need, market_mode=market_mode)


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
    market_mode: str = "auction",
    target_board: dict[str, Any] | None = None,
    funding_info: dict[str, Any] | None = None,
    max_squad: int | None = None,
    league_rules: dict[str, Any] | None = None,
    recommended_xi: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Fuente de verdad diaria:
    buy_now | clause_bid | sell | avoid | wait | scout
    Empaquetado: líneas de necesidad + hedges (auction), respetando cupo de plantilla.
    Devuelve (action_plan, daily_package).
    """
    plan: list[dict[str, Any]] = []
    price_series = price_series or {}
    rules = league_rules if isinstance(league_rules, dict) else {}
    clauses_ok = bool(rules.get("clauses", True))
    loans_ok = bool(rules.get("loans", False))
    try:
        market_urgency = float(rules.get("market_urgency") or 1.0)
    except (TypeError, ValueError):
        market_urgency = 1.0
    balance = float(me.get("balance") or 0)
    max_debt_raw = me.get("max_debt")
    try:
        max_debt = float(max_debt_raw) if max_debt_raw is not None else None
    except (TypeError, ValueError):
        max_debt = None
    bal_future_raw = me.get("balance_future")
    try:
        balance_future = float(bal_future_raw) if bal_future_raw is not None else None
    except (TypeError, ValueError):
        balance_future = None
    days_to_kickoff = (diagnostico_plantilla or {}).get("days_to_kickoff")
    hours_to_jornada = (diagnostico_plantilla or {}).get("hours_to_jornada")
    matchday_ctx = (diagnostico_plantilla or {}).get("matchday")
    fixed = (market_mode or "auction") == "fixed"
    if max_squad is None:
        try:
            max_squad = int(rules.get("max_squad") or 0) or None
        except (TypeError, ValueError):
            max_squad = None
    if max_squad is None:
        max_squad = int(
            getattr(config, "MAX_SQUAD_SIZE_PREMIER", 22)
            if fixed
            else getattr(config, "MAX_SQUAD_SIZE_LALIGA", 25)
        )
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
    funding = funding_info or (
        funding_plan_from_board(target_board, balance=balance)
        if target_board
        else estimate_gap_funding(structural_needs, opportunities, balance, top_n=3)
    )
    gap_pos_labels = ", ".join(str(p) for p in (funding.get("positions") or []) if p) or "otras carencias"
    objective_ids = board_objective_ids(target_board)
    primary_ids = board_primary_ids(target_board)
    cash_reserved = float(funding.get("cash_reserved") or 0)
    patch_cap = max_patch_spend(target_board)
    allow_patches = patches_allowed(target_board)
    bootstrap = (diagnostico_plantilla or {}).get("bootstrap_xi") or {}
    market_cycle_ctx = (diagnostico_plantilla or {}).get("market_cycle") or {}
    if market_cycle_ctx.get("cash_lag_hours") is not None:
        cash_lag = float(market_cycle_ctx["cash_lag_hours"])
    elif funding.get("cash_lag_hours") is not None:
        cash_lag = float(funding.get("cash_lag_hours"))
    else:
        cash_lag = float(int(getattr(config, "MARKET_CYCLE_HOURS", 24) or 24) * 2)
    hours_resolved = resolve_hours_to_jornada(
        hours_to_jornada=float(hours_to_jornada) if hours_to_jornada is not None else None,
        days_to_kickoff=days_to_kickoff,
        matchday=matchday_ctx if isinstance(matchday_ctx, dict) else None,
        season_start=(diagnostico_plantilla or {}).get("season_start")
        if isinstance(diagnostico_plantilla, dict)
        else None,
    )
    # Plan de venta a tiempo: shortfall cubierto solo si el cobro llega antes de D-1
    sell_proceeds_timely = 0.0
    if sells_settle_before_d1(hours_to_jornada=hours_resolved, cash_lag_hours=cash_lag):
        sell_proceeds_timely = float(funding.get("funding_shortfall") or 0)

    for o in opportunities:
        ext = o.get("external") or {}
        avail = ext.get("availability") or ("injured" if o.get("injury") else "unknown")
        lineup = ext.get("lineup_prob_ext")
        if lineup is None and o.get("lineup_prob") is not None:
            lineup = float(o["lineup_prob"]) * 100
        fills = bool(o.get("fills_need") or o.get("fills_structural"))
        demand = _rival_demand_for_position(
            rivals, o.get("position") or "", market_mode=market_mode
        )
        risk = o.get("wait_risk") or _wait_risk(
            o, rivals, fills_need=fills, market_mode=market_mode
        )
        delta = o.get("delta_5d")
        rating = ext.get("recent_rating")
        cost = float(o.get("puja_recomendada") or o.get("price") or 0)
        min_c = float(o.get("puja_minima") or o.get("price") or 0)
        fin = evaluate_bid_finance(
            cost,
            balance,
            min_cost=min_c,
            max_debt=max_debt,
            balance_future=balance_future,
            hours_to_jornada=hours_resolved,
            days_to_kickoff=days_to_kickoff,
            matchday=matchday_ctx if isinstance(matchday_ctx, dict) else None,
            sell_proceeds_timely=sell_proceeds_timely,
            cash_lag_hours=cash_lag,
        )
        bf = str(fin.get("budget_fit") or o.get("budget_fit") or "blocked")
        debt_risk = bool(fin.get("debt_risk") or o.get("debt_risk"))
        solvency_blocked = bool(fin.get("solvency_blocked") or o.get("solvency_blocked"))

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
                "why": (
                    f"No disponible ({avail}). No fichar pese a precio/encaje."
                    if fixed
                    else f"No disponible ({avail}). No pujar pese a precio/encaje."
                ),
                "rival_demand": len(demand),
                "budget_fit": bf,
                "priority_score": 40,
            })
            continue

        buy_now = False
        why_parts: list[str] = []
        fills_cov = bool(o.get("fills_coverage_gap"))
        line_covered = bool(o.get("line_already_covered"))
        is_upgrade = bool(o.get("is_upgrade"))
        fills_structural_o = bool(o.get("fills_structural"))
        overstocked = bool(o.get("overstocked"))
        my_squad = list(me.get("squad") or [])
        cash_reserve = float(getattr(config, "PACKAGE_CASH_RESERVE", 0) or 0)
        worth_upgrade = upgrade_worth_buy(
            o,
            is_upgrade=is_upgrade,
            overstocked=overstocked,
            squad=my_squad,
            budget_fit=bf,
            debt_risk=debt_risk,
            solvency_blocked=solvency_blocked,
            leaves_gap_budget=leaves_budget if other_min > 0 else None,
            crowds_out_gaps=crowds_out,
            residual=residual if residual >= 0 else None,
            other_gaps_min=other_min,
            cash_reserve=cash_reserve,
        )
        # Alta en la posición ≠ este jugador cubre el hueco (p. ej. gk_tandem / overstock)
        structural_gap = pos in need_pos_alta
        if pos == "GK" or overstocked or line_covered:
            structural_gap = fills_structural_o or fills_cov
        real_starter_cand = lineup is not None and float(lineup) >= 70
        on_daily = bool(o.get("on_daily_market") or o.get("seller") == "market")
        ext_o = o.get("external") or {}
        gw_out = bool(o.get("gw_out") or ext_o.get("gw_out"))
        gw_starter = bool(o.get("gw_starter") or ext_o.get("gw_starter"))
        prod_ok = False
        has_ff_signal = False
        signal_avg = None
        signal_apps = 0
        signal_from_prior = False
        cur_apps = 0
        try:
            cur_avg = o.get("ff_mister_avg")
            if cur_avg is None:
                cur_avg = ext_o.get("ff_mister_avg")
            cur_apps = int(o.get("ff_apps") if o.get("ff_apps") is not None else (ext_o.get("ff_apps") or 0))
            prior_avg = o.get("ff_prior_avg")
            if prior_avg is None:
                prior_avg = ext_o.get("ff_prior_avg")
            prior_apps_n = o.get("ff_prior_apps")
            if prior_apps_n is None:
                prior_apps_n = ext_o.get("ff_prior_apps")
            try:
                prior_apps_n = int(prior_apps_n) if prior_apps_n is not None else 0
            except (TypeError, ValueError):
                prior_apps_n = 0

            # Señal FF: actual si ≥THIN_APPS; si no, temporada pasada fiable.
            sig = comparable_ff_signal(o)
            if sig.get("usable"):
                has_ff_signal = True
                signal_avg = float(sig["avg"])
                signal_apps = int(sig["apps"])
                signal_from_prior = bool(sig.get("prior_backed"))
            else:
                cur_known_empty = cur_apps == 0 and (
                    cur_avg is None or float(cur_avg) <= 0
                )
                if cur_avg is not None and float(cur_avg) > 0 and cur_apps > 0:
                    has_ff_signal = True
                    signal_avg = float(cur_avg)
                    signal_apps = cur_apps
                elif (
                    not cur_known_empty
                    and prior_avg is not None
                    and float(prior_avg) > 0
                    and prior_apps_n > 0
                ):
                    has_ff_signal = True
                    signal_avg = float(prior_avg)
                    signal_apps = prior_apps_n
                    signal_from_prior = True

            prod_ok = has_ff_signal and (
                float(o.get("production_score") or 0) >= 35
                or (bool(o.get("is_top_ff")) and not o.get("sample_thin"))
                or (signal_from_prior and signal_avg is not None and signal_avg >= 4.0)
            )
        except (TypeError, ValueError):
            prod_ok = False
            has_ff_signal = False
            signal_avg = None
            signal_apps = 0
            signal_from_prior = False

        if gw_out:
            why_parts.append("FF jornada: no titular probable — evitar fichar ahora")

        # Muestra actual corta: avisar y anclar a temporada pasada si existe
        if o.get("current_sample_thin") or (
            o.get("sample_thin")
            and has_ff_signal
            and signal_avg is not None
            and signal_avg > 0
            and 0 < signal_apps < THIN_APPS
            and not signal_from_prior
        ):
            if signal_from_prior or o.get("prior_backed"):
                why_parts.append(
                    f"Pocos PJ esta temporada ({cur_apps if cur_apps else o.get('ff_apps') or '?'}) — "
                    f"comparo con temp. pasada ({signal_avg:.1f} · {signal_apps} PJ)"
                )
            elif has_ff_signal and signal_avg is not None and signal_avg > 0:
                why_parts.append(
                    f"Media alta pero pocos partidos ({signal_apps} PJ) — poco fiable"
                )
        just = market_value_justification(
            o, o.get("puja_recomendada") or o.get("price")
        )
        if just and just.get("prior_backed"):
            if just.get("justified"):
                why_parts.append(f"VM justificado por {just['note']}")
            elif just.get("expensive"):
                why_parts.append(f"VM caro vs {just['note']}")
            elif just.get("note"):
                why_parts.append(just["note"])
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
            why_parts.append(
                "línea ya cubierta — no insistir"
                if fixed
                else "línea ya cubierta — no insistir en la puja"
            )
        elif overstocked and not fills_cov and not fills_structural_o and not worth_upgrade:
            # Sobrecupo: veto salvo upgrade que renta (caja + mejora clara)
            buy_now = False
            why_parts.append(
                "línea sobrada — upgrade en espera, no pujar hoy"
                if is_upgrade
                else (
                    "línea sobrada — no insistir"
                    if fixed
                    else "línea sobrada — no insistir en la puja"
                )
            )
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
            if not fixed and fills and risk == "high" and (lineup is None or float(lineup) >= 80):
                buy_now = True
                why_parts.append(f"demanda rival alta ({len(demand)} con gap {pos})")
            # Precio fijo: cobertura + caja basta (sin exigir rival risk / % titular)
            if fixed and on_daily and bf in ("comfortable", "tight") and (
                fills_cov or fills or structural_gap
            ):
                buy_now = True
                if not any("cubre" in w for w in why_parts):
                    why_parts.append("cubre hueco de plantilla — fichar al precio")
                if prod_ok and not any("producción" in w.lower() for w in why_parts):
                    why_parts.append("buena señal de producción FF")
            if (
                not fixed
                and o.get("priority") == "Alta"
                and bf in ("comfortable", "tight")
                and real_starter_cand
                and (fills or structural_gap or fills_cov)
                and risk in ("medium", "high")
            ):
                buy_now = True
                why_parts.append("titular real probable y prioridad alta")
            elif (
                not fixed
                and o.get("priority") == "Alta"
                and bf in ("comfortable", "tight")
                and (lineup is not None and float(lineup) >= 80)
                and risk in ("medium", "high")
                and (fills or fills_cov or is_upgrade)
            ):
                buy_now = True
                why_parts.append("titular probable y prioridad alta")
            if is_upgrade and bf in ("comfortable", "tight") and real_starter_cand:
                buy_now = True
                if worth_upgrade and overstocked:
                    why_parts.append("upgrade rentable — deja caja para otras carencias")
                else:
                    why_parts.append("upgrade claro vs lo que tienes en la línea")
            # Titular GW FF + hueco: refuerzo buy_now
            if (
                gw_starter
                and on_daily
                and bf in ("comfortable", "tight")
                and (fills or fills_cov or structural_gap)
            ):
                buy_now = True
                if not any("FF jornada" in w for w in why_parts):
                    why_parts.append("FF jornada: titular probable esta semana")

        pid = str(o.get("id") or "")
        is_objective = pid in objective_ids
        is_primary_obj = pid in primary_ids
        fills_gap_any = bool(fills or fills_cov or structural_gap)
        is_key = is_key_market_candidate(
            {
                **o,
                "upgrade_worth_buy": worth_upgrade,
            },
            is_primary_obj=is_primary_obj,
            is_objective=is_objective,
            on_daily=on_daily,
            gw_out=gw_out,
            real_starter=real_starter_cand,
            fills_gap=fills_gap_any or is_objective,
        )
        # Objetivo del board en mercado del día → priorizar buy_now
        # (línea sobrada sin gap real: no saltarse el techo de sobrecupo)
        overstock_blocks = (
            overstocked
            and not fills_cov
            and not fills_structural_o
            and not worth_upgrade
        )
        if (
            is_objective
            and on_daily
            and bf in ("comfortable", "tight")
            and not gw_out
            and not overstock_blocks
        ):
            buy_now = True
            if is_primary_obj:
                why_parts.insert(0, "objetivo primary del tablero — fichar si sale hoy")
            elif not any("objetivo" in w for w in why_parts):
                why_parts.append("objetivo del tablero (hueco estructural)")
        # Jugador clave en mercado (crack / top / ideal) → máxima prioridad
        if is_key and on_daily and bf in ("comfortable", "tight") and not gw_out:
            # Línea cubierta / sobrada: no forzar buy_now solo por producción/clave
            if not overstock_blocks and not (line_covered and not is_upgrade and not fills_cov):
                buy_now = True
                if not any("clave" in w for w in why_parts):
                    why_parts.insert(0, "jugador clave en mercado de hoy — prioridad")

        # Red de seguridad: línea sobrada o GK caro ya cubierto → nunca buy_now
        if buy_now and overstock_blocks:
            buy_now = False
            if not any("sobrada" in w.lower() or "ya cubierta" in w.lower() for w in why_parts):
                why_parts.append("línea sobrada — no insistir en la puja")
        # Bootstrap once: priorizar titulares del mercado actual que cierran huecos legales
        if (
            bootstrap.get("active")
            and on_daily
            and bf in ("comfortable", "tight")
            and not gw_out
            and not solvency_blocked
        ):
            gap_pos = bootstrap.get("position_gaps") or {}
            if pos in gap_pos and int(gap_pos.get(pos) or 0) > 0:
                starter_ok = real_starter_cand or (
                    lineup is not None and float(lineup) >= 45
                )
                line_ok = (pos != "GK" and starter_ok) or (
                    pos == "GK" and int(gap_pos.get("GK") or 0) > 0
                )
                if line_ok:
                    posture = bootstrap.get("posture") or "buy_now"
                    cycle_urgent = bool(bootstrap.get("cycle_urgent"))
                    if posture == "buy_now" or cycle_urgent or int(bootstrap.get("slots_short") or 0) > 0:
                        buy_now = True
                        h_end = market_cycle_ctx.get("hours_to_end")
                        tip = (
                            f"modo once: mercado cierra en {float(h_end):.0f}h"
                            if h_end is not None
                            else "modo once: completa el 11"
                        )
                        if not any("modo once" in w for w in why_parts):
                            why_parts.insert(0, tip)
        if buy_now and pos == "GK" and line_covered and not is_upgrade and not fills_structural_o:
            buy_now = False
            if not any("ya cubierta" in w.lower() for w in why_parts):
                why_parts.append("línea GK ya cubierta — no gastar caja en 2.º titular")
        # Upgrade en línea sobrada sin umbral de ROI → wait/scout
        if (
            buy_now
            and overstocked
            and is_upgrade
            and not fills_cov
            and not fills_structural_o
            and not worth_upgrade
        ):
            buy_now = False
            if not any("sobrada" in w.lower() for w in why_parts):
                why_parts.append("línea sobrada — upgrade en espera, no pujar hoy")
        # Si el upgrade no renta por caja residual → wait explícito
        if (
            is_upgrade
            and overstocked
            and not fills_cov
            and not fills_structural_o
            and not worth_upgrade
            and crowds_out
            and not any("prioriza otras" in w or "poca caja" in w for w in why_parts)
        ):
            why_parts.append(
                f"upgrade bueno, pero reserva caja para {gap_pos_labels}"
            )
        is_patchish = buy_now and on_daily and not is_primary_obj and not is_key and (
            cost <= patch_cap
            or (not real_starter_cand and cost < 2_500_000)
            or (o.get("categories") and "chollo_economico" in (o.get("categories") or []) and cost < 1_500_000)
        )
        if buy_now and not is_primary_obj and not is_key and is_patchish:
            if not allow_patches:
                buy_now = False
                why_parts.append("parche bloqueado: no mejora el 15 frente a objetivos de hoy")
            elif cost > patch_cap:
                buy_now = False
                why_parts.append(
                    f"parche por encima del techo ({patch_cap:,.0f} €)"
                )

        # Defensa extra: nunca buy_now fuera del mercado del día
        if buy_now and not on_daily:
            buy_now = False
            why_parts.append("aún no está en el mercado de hoy")
        if buy_now and gw_out:
            buy_now = False
            if not any("no titular" in w for w in why_parts):
                why_parts.append("FF jornada: no titular probable — evitar fichar ahora")

        # Sin caja / solvencia → no buy_now (stretch por crowding también queda fuera)
        if buy_now and bf not in ("comfortable", "tight"):
            buy_now = False
            if solvency_blocked:
                why_parts.append(
                    "bloqueado: no puntuarías en negativo el día antes de la jornada"
                )
            elif debt_risk:
                why_parts.append(
                    "deuda temporal — vende antes de la jornada para poder puntuar"
                )
            elif crowds_out:
                why_parts.append(
                    f"tras fichar quedaría poca caja para {gap_pos_labels} "
                    f"(residual {max(0, residual):,.0f} € vs ~{other_min:,.0f} €)"
                )
            else:
                why_parts.append(
                    f"supera techo Mister ({float(fin.get('bid_cap') or balance):,.0f} €)"
                    if cost > float(fin.get("bid_cap") or balance)
                    else f"caja insuficiente ({balance:,.0f} €)"
                )
        elif buy_now and debt_risk:
            why_parts.append(
                "deuda temporal — vende antes de la jornada para poder puntuar"
            )

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
            if overstocked and not fills_cov:
                if worth_upgrade:
                    prio_i += 15
                else:
                    prio_i -= 55
            elif line_covered and not is_upgrade:
                prio_i -= 40
            elif is_upgrade:
                prio_i += 10
            if is_primary_obj and on_daily and not overstock_blocks:
                prio_i += 80
            elif is_objective and on_daily and not overstock_blocks:
                prio_i += 40
            if is_key and on_daily and not overstock_blocks:
                prio_i += 90
            # Sin clave: premiar puntaje + capacidad de trueque en carencias
            if fills_gap_any and not is_key:
                prio_i += min(25, int(trade_asset_score(o)))

        asset_score = trade_asset_score(o)
        common = {
            "crowds_out_gaps": crowds_out,
            "leaves_gap_budget": leaves_budget,
            "residual_budget": residual if residual >= 0 else None,
            "other_gaps_min": other_min,
            "funding_target": funding.get("funding_target"),
            "funding_shortfall": funding.get("funding_shortfall"),
            "cost": cost,
            "price": o.get("price"),
            "min_bid": o.get("min_bid") or o.get("puja_minima"),
            "puja_minima": o.get("puja_minima"),
            "fills_coverage_gap": fills_cov,
            "line_already_covered": line_covered,
            "is_upgrade": is_upgrade,
            "overstocked": overstocked,
            "upgrade_worth_buy": worth_upgrade,
            "position_coverage": o.get("position_coverage"),
            "on_daily_market": on_daily,
            "market_mode": "fixed" if fixed else "auction",
            "listed_by_rival": bool(o.get("listed_by_rival")),
            "listed_by_name": o.get("listed_by_name"),
            "listed_by_owner_id": o.get("listed_by_owner_id"),
            "clause_reference": o.get("clause_reference"),
            "ff_apps": o.get("ff_apps"),
            "sample_thin": bool(o.get("sample_thin")),
            "current_sample_thin": bool(o.get("current_sample_thin")),
            "prior_backed": bool(o.get("prior_backed")),
            "value_note": o.get("value_note"),
            "ff_prior_avg": o.get("ff_prior_avg") or (o.get("external") or {}).get("ff_prior_avg"),
            "ff_prior_apps": o.get("ff_prior_apps") or (o.get("external") or {}).get("ff_prior_apps"),
            "target_tier": o.get("target_tier"),
            "is_board_objective": is_objective,
            "is_primary_target": is_primary_obj,
            "is_key_market": is_key,
            "trade_asset_score": asset_score,
            "appreciation_play": bool(o.get("appreciation_play")),
            "delta_5d": delta,
            "categories": list(o.get("categories") or []),
            "cash_reserved": cash_reserved,
            "debt_risk": debt_risk,
            "solvency_blocked": solvency_blocked,
            "solvency_ok": bool(fin.get("solvency_ok")),
            "bid_cap": fin.get("bid_cap"),
            "hours_to_jornada": fin.get("hours_to_jornada"),
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
                    if pos in critical_pos or structural_gap or fills_cov or (not fixed and risk == "high")
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
                src = (o.get("external") or {}).get("lineup_prob_source")
                if src == "ff_profile_titular":
                    wait_bits.append(f"titularidad {int(float(lineup))}% (titular FF)")
                elif src in ("ff_apps_proxy", "ff_apps_proxy_fotmob"):
                    wait_bits.append(f"titularidad ≈{int(float(lineup))}% (por PJ)")
                else:
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
            if rating is not None and float(rating) < 6.2:
                wait_bits.append(f"nota baja ({rating})")
            ff = o.get("ff_mister_avg")
            if ff is not None:
                wait_bits.append(f"FF media {float(ff):.1f}")
            if bf == "blocked":
                if solvency_blocked:
                    wait_bits.append(
                        "bloqueado: no puntuarías en negativo el día antes de la jornada"
                    )
                else:
                    wait_bits.append(
                        f"supera techo Mister (~{float(fin.get('bid_cap') or balance):,.0f} €)"
                        if cost > float(fin.get("bid_cap") or 0)
                        else f"sin saldo (hace falta ~{cost:,.0f} €)"
                    )
            elif bf == "stretch":
                if debt_risk:
                    wait_bits.append(
                        "deuda temporal — exige venta que cobre antes del día previo a la jornada"
                    )
                else:
                    wait_bits.append(
                        "al límite de caja / otras carencias"
                        if fixed
                        else "puja al límite de caja / otras carencias"
                    )
            elif debt_risk:
                wait_bits.append(
                    "deuda temporal — vende antes de la jornada para poder puntuar"
                )
            why_wait = "; ".join(dict.fromkeys(wait_bits)) or "Sin urgencia"
            if not fixed:
                why_wait += f" · riesgo de perderlo: {risk}"
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
                    else (
                        "medium"
                        if fills or structural_gap or fills_cov or (not fixed and risk != "low")
                        else "low"
                    )
                ),
                "why": why_wait,
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
        rivals if not fixed else [],
        price_series=price_series,
        delta_fn=compute_delta_from_history,
        market_opportunities=opportunities,
        points_phase=points_phase,
        diagnostico_plantilla=diagnostico_plantilla,
        target_board=target_board,
        funding_info=funding,
        recommended_xi=recommended_xi,
        league_economy=(rules.get("economy") if isinstance(rules.get("economy"), dict) else None),
    )
    plan.extend(sells)
    promote_funded_swaps(
        plan,
        balance=balance,
        hours_to_jornada=hours_resolved,
        cash_lag_hours=cash_lag,
    )

    # Cláusulas / scout rivales (solo auction)
    if not fixed:
        for u in rival_upgrades or []:
            item = dict(u)
            pid = str(item.get("player_id") or "")
            if pid in primary_ids:
                item["is_primary_target"] = True
                item["is_board_objective"] = True
                item["priority_score"] = int(item.get("priority_score") or 0) + 50
                why = (item.get("why") or "").strip()
                item["why"] = (
                    f"objetivo primary del tablero; {why}" if why else "objetivo primary del tablero"
                )
            elif pid in objective_ids:
                item["is_board_objective"] = True
            plan.append(item)

        # Amplificar wait_risk si rivales top tienen gap
        for o in opportunities:
            if o.get("priority") not in ("Alta", "Media"):
                continue
            demand = _rival_demand_for_position(
                rivals, o.get("position") or "", market_mode=market_mode
            )
            top = [d for d in demand if int(d.get("rank") or 99) <= 3]
            if not top:
                continue
            existing = next((x for x in plan if x["player_id"] == o["id"] and x["action"] == "wait"), None)
            if existing and existing.get("wait_risk") != "high":
                existing["wait_risk"] = "high"
                existing["why"] += f" · rivales top con gap: {', '.join(t['team_name'] for t in top[:2])}"
                existing["urgency"] = "medium"

    if bootstrap.get("active"):
        gap_pos = {
            p
            for p, n in (bootstrap.get("position_gaps") or {}).items()
            if int(n or 0) > 0
        }
        promoted_b: list[dict[str, Any]] = []
        for item in plan:
            if item.get("action") != "wait":
                promoted_b.append(item)
                continue
            pos = item.get("position")
            if (
                pos not in gap_pos
                or not item.get("on_daily_market")
                or item.get("budget_fit") not in ("comfortable", "tight")
                or item.get("solvency_blocked")
            ):
                promoted_b.append(item)
                continue
            posture = bootstrap.get("posture") or "buy_now"
            if (
                posture == "can_wait_cycle"
                and not bootstrap.get("cycle_urgent")
                and int(bootstrap.get("slots_short") or 0) <= 0
            ):
                promoted_b.append(item)
                continue
            row = dict(item)
            row["action"] = "buy_now"
            row["urgency"] = "high"
            why = (row.get("why") or "").strip()
            h_end = market_cycle_ctx.get("hours_to_end")
            tip = (
                f"modo once: mercado cierra en {float(h_end):.0f}h"
                if h_end is not None
                else "modo once: completa el 11"
            )
            row["why"] = f"{tip}; {why}" if why else tip
            promoted_b.append(row)
        plan = promoted_b

    # Mercado rápido: wait con alto riesgo → buy_now (si finanzas OK)
    if market_urgency >= 1.15 and not fixed:
        promoted: list[dict[str, Any]] = []
        for item in plan:
            if item.get("action") != "wait":
                promoted.append(item)
                continue
            risk = item.get("wait_risk")
            bf = item.get("budget_fit")
            on_daily = bool(item.get("on_daily_market"))
            if (
                risk == "high"
                and on_daily
                and bf in ("comfortable", "tight")
                and not item.get("solvency_blocked")
            ):
                row = dict(item)
                row["action"] = "buy_now"
                row["urgency"] = "high"
                why = (row.get("why") or "").strip()
                row["why"] = (
                    f"mercado rápido (speed={rules.get('market_speed')}): actuar ya; {why}"
                    if why
                    else f"mercado rápido (speed={rules.get('market_speed')}): actuar ya"
                )
                promoted.append(row)
            else:
                promoted.append(item)
        plan = promoted

    # Sin hueco/upgrade/objetivo: fichar revalorizaciones del mercado de hoy
    plan = promote_appreciation_plays(plan)
    # Evitar contradicciones: avoid gana sobre buy/swap del mismo jugador
    plan = reconcile_avoid_conflicts(plan)

    finalized = finalize_action_plan(
        plan,
        balance=balance,
        funding_info=funding,
        market_mode=market_mode,
        target_board=target_board,
        squad_size=len(me.get("squad") or []),
        max_squad=max_squad,
        bootstrap=bootstrap if bootstrap.get("active") else None,
    )
    action_plan, daily_package = finalized

    # Sin cláusulas en la liga: no emitir clause_bid
    if not clauses_ok:
        cleaned: list[dict[str, Any]] = []
        for item in action_plan:
            if item.get("action") != "clause_bid":
                cleaned.append(item)
                continue
            row = dict(item)
            row["action"] = "scout"
            why = (row.get("why") or "").strip()
            row["why"] = (
                f"cláusulas desactivadas en esta liga; {why}" if why else "cláusulas desactivadas en esta liga"
            )
            cleaned.append(row)
        action_plan = cleaned

    if isinstance(daily_package, dict):
        daily_package = dict(daily_package)
        daily_package["league_rules"] = {
            "provider": rules.get("provider"),
            "market_mode": market_mode,
            "max_squad": max_squad,
            "clauses": clauses_ok,
            "loans": loans_ok,
            "market_urgency": market_urgency,
            "factors": list(rules.get("factors") or []),
            "economy": rules.get("economy") if isinstance(rules.get("economy"), dict) else None,
            "sale_limit": (rules.get("economy") or {}).get("sale_limit")
            if isinstance(rules.get("economy"), dict)
            else rules.get("sale_limit"),
        }
        if not loans_ok:
            notes = list(daily_package.get("notes") or [])
            notes.append("Cesiones desactivadas: no cuentes con liquidez por loan.")
            daily_package["notes"] = notes

    return action_plan, daily_package


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

def build_payload(league_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    league_cfg = dict(league_cfg or config.get_league())
    slug = str(league_cfg.get("slug") or config.DEFAULT_LEAGUE_SLUG)
    season_start = str(
        league_cfg.get("season_start")
        or getattr(config, "SEASON_START_DATE", "2026-08-15")
    )
    id_competition = league_cfg.get("id_competition")
    try:
        id_competition_i = int(id_competition) if id_competition is not None else None
    except (TypeError, ValueError):
        id_competition_i = None
    is_laliga = id_competition_i == int(getattr(config, "LALIGA_COMPETITION_ID", 1))
    market_mode = config.league_market_mode(league_cfg)
    fixed_market = market_mode == "fixed"

    league, mister_source = fetch_mister_league(
        community_id=str(league_cfg.get("id_community") or "") or None
    )
    live_meta = league.pop("_live_meta", {}) if isinstance(league, dict) else {}
    # Normas Mister: FG_user (+ admin best-effort)
    external_key_early = config.external_competition_key(
        league_cfg=league_cfg,
        id_competition=id_competition_i,
    )
    league_rules = normalize_rules(
        live_meta.get("fg_user_rules") if isinstance(live_meta.get("fg_user_rules"), dict) else None,
        admin_data=live_meta.get("admin_settings")
        if isinstance(live_meta.get("admin_settings"), dict)
        else None,
        league_cfg=league_cfg,
        external_key=external_key_early,
        fg_cfg=live_meta.get("fg_cfg_rules") if isinstance(live_meta.get("fg_cfg_rules"), dict) else None,
    )
    league_cfg = merge_rules_into_league_cfg(league_cfg, league_rules)
    market_mode = config.league_market_mode(league_cfg)
    fixed_market = market_mode == "fixed"
    # Catálogo completo para plantilla ideal; no se persiste en latest_data.json
    full_pool: list[dict[str, Any]] = []
    gw_bundle: dict[str, Any] = {}
    if isinstance(league, dict):
        full_pool = list(league.pop("pool_all", None) or [])
        gw_bundle = league.pop("gameweek", None) or {}
    honest_live = mister_source == "api" or bool(live_meta.get("honest_mode"))

    # Preferir competición real de la sesión si vino en live_meta
    if live_meta.get("id_competition") is not None:
        try:
            id_competition_i = int(live_meta["id_competition"])
            is_laliga = id_competition_i == int(getattr(config, "LALIGA_COMPETITION_ID", 1))
        except (TypeError, ValueError):
            pass

    seed = load_performance_seed()
    relevant_ids: list[str] = []
    for p in league.get("me", {}).get("squad", []):
        relevant_ids.append(p["id"])
    for p in league.get("market", []):
        relevant_ids.append(p["id"])
    for p in league.get("pool_top", []):
        relevant_ids.append(p["id"])
    for p in full_pool:
        if p.get("id"):
            relevant_ids.append(p["id"])

    # En live no mezclamos seed de demo como si fuera histórico real del jugador
    if honest_live:
        perf_idx = {}
        perf_source = "disabled_for_live"
    else:
        perf_raw, perf_source = fetch_api_football_enrichment(relevant_ids, seed)
        perf_idx = index_performance(perf_raw)

    # Histórico de precios: preferir history de la liga
    price_series = load_recent_price_map_for_league(slug, days=config.TRADING_WINDOW_DAYS)

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
        "Mercado+libres [%s]: day=%s free_added=%s total=%s (pool_size=%s)",
        slug,
        n_market_day,
        len(market_combined) - n_market_day,
        len(market_combined),
        live_meta.get("pool_size") or 0,
    )
    market_raw = [
        enrich_player(p, perf_idx, allow_synthetic=not honest_live)
        for p in market_combined
    ]

    # Enriquecimiento externo (FF autoridad, JP solo respaldo)
    universe = squad + market_raw
    external_key = config.external_competition_key(
        league_cfg=league_cfg,
        id_competition=id_competition_i,
    )
    # Re-normalizar rules con external definitivo
    league_rules = normalize_rules(
        live_meta.get("fg_user_rules") if isinstance(live_meta.get("fg_user_rules"), dict) else None,
        admin_data=live_meta.get("admin_settings")
        if isinstance(live_meta.get("admin_settings"), dict)
        else None,
        league_cfg=league_cfg,
        external_key=external_key,
        fg_cfg=live_meta.get("fg_cfg_rules") if isinstance(live_meta.get("fg_cfg_rules"), dict) else None,
    )
    league_cfg = merge_rules_into_league_cfg(league_cfg, league_rules)
    market_mode = config.league_market_mode(league_cfg)
    fixed_market = market_mode == "fixed"
    ff_hint = ff_hint_for_provider(
        league_rules.get("provider"),
        competition=external_key,
    )
    log.info(
        "Normas [%s]: provider=%s mode=%s max_squad=%s clauses=%s factors=%s source=%s",
        slug,
        league_rules.get("provider"),
        market_mode,
        league_rules.get("max_squad"),
        league_rules.get("clauses"),
        league_rules.get("factors"),
        league_rules.get("source"),
    )
    if external_key:
        squad_teams = list({str(p.get("team") or "") for p in squad if p.get("team")})
        universe_ext, external_meta = enrich_players_with_external(
            universe,
            competition=external_key,
            squad_teams=squad_teams,
        )
    else:
        universe_ext = []
        for p in universe:
            row = dict(p)
            row.setdefault("external", {})
            universe_ext.append(row)
        external_meta = {
            "futbolfantasy": "skip",
            "jornadaperfecta": "skip",
            "ff_matchday": "skip",
            "matched": 0,
            "cache_used": False,
            "errors": [],
            "matchday": None,
            "note": (
                f"Sin scrapers externos para {league_cfg.get('competition') or 'esta competición'} "
                f"(id_competition={id_competition_i}). Usamos Mister (+ FotMob fail-soft)."
            ),
        }
        log.info("Skip externos: sin mapping para id_competition=%s", id_competition_i)

    # FotMob: nota / minutos / goles / xG últimos 5, solo plantilla + candidatos
    # reales del mercado (el resto del universo no llega nunca al board).
    universe_ext, fotmob_meta = enrich_players_with_fotmob(
        universe_ext,
        focus_ids=fotmob_focus_ids(universe_ext[: len(squad)], universe_ext[len(squad) :]),
    )
    external_meta["fotmob"] = fotmob_meta.get("fotmob", "skip")
    external_meta["fotmob_matched"] = fotmob_meta.get("matched", 0)
    external_meta["fotmob_filled"] = fotmob_meta.get("filled", 0)
    n_squad = len(squad)
    squad = universe_ext[:n_squad]
    market_ext = universe_ext[n_squad:]

    # Producción FF (según competición + provider Mister)
    pre_phase = detect_points_phase(list(squad) + list(market_ext))
    if external_key:
        universe_ff, ff_meta = enrich_players_with_ff_production(
            list(squad) + list(market_ext),
            points_phase=pre_phase,
            market_universe=market_ext,
            competition=external_key,
            mister_provider=str(league_rules.get("provider") or ""),
        )
        external_meta["ff_points"] = ff_meta.get("ff_points", "fail")
        external_meta["ff_matched"] = ff_meta.get("matched", 0)
        external_meta["ff_tops"] = ff_meta.get("top_count", 0)
        external_meta["ff_threshold"] = ff_meta.get("threshold")
        external_meta["ff_scoring"] = ff_meta.get("scoring") or ff_hint.get("label")
        external_meta["ff_avg_scale"] = ff_meta.get("avg_scale") or ff_hint.get("avg_scale")
        external_meta["mister_provider"] = league_rules.get("provider")
        squad = universe_ff[:n_squad]
        market_ext = universe_ff[n_squad:]
    else:
        external_meta["ff_points"] = "skip"
        external_meta["ff_matched"] = 0
        external_meta["ff_tops"] = 0
        external_meta["ff_threshold"] = None
        external_meta["ff_scoring"] = None
        external_meta["ff_avg_scale"] = None
        external_meta["mister_provider"] = league_rules.get("provider")

    # Dificultad de rival (calendario + clasificación Mister) y puntos esperados
    gw_table = gw_bundle.get("table") if isinstance(gw_bundle.get("table"), dict) else None
    team_prior = build_team_prior(full_pool or universe)
    fantasy_conceded = build_fantasy_conceded(
        full_pool or universe,
        played_opponents=(
            gw_bundle.get("played_opponents")
            if isinstance(gw_bundle.get("played_opponents"), dict)
            else None
        ),
    )
    team_strength = build_team_strength(gw_table, prior=team_prior, conceded=fantasy_conceded)
    team_names = build_team_name_index(gw_table, full_pool or universe)
    scoring_rules = {
        **league_rules,
        "avg_scale": external_meta.get("ff_avg_scale") or ff_hint.get("avg_scale"),
    }
    xpts_targets = list(squad) + list(market_ext) + list(full_pool)
    n_fdr = annotate_players_with_fdr(
        xpts_targets,
        strength=team_strength,
        team_schedule=gw_bundle.get("team_schedule") if isinstance(gw_bundle.get("team_schedule"), dict) else None,
        team_names=team_names,
    )
    n_xpts = annotate_players_with_xpts(xpts_targets, league_rules=scoring_rules)
    external_meta["fdr_confidence"] = team_strength.get("confidence")
    external_meta["fdr_source"] = team_strength.get("source")
    external_meta["fdr_table_weight"] = team_strength.get("table_weight")
    external_meta["fdr_prior_teams"] = team_prior.get("teams_ranked")
    external_meta["fdr_fantasy_teams"] = len((fantasy_conceded.get("teams") or {}))
    external_meta["xpts_players"] = n_xpts
    log.info(
        "xPts [%s]: %s jugadores (FDR %s, fuente=%s peso_tabla=%s prior=%s equipos, jugadas=%s)",
        slug,
        n_xpts,
        n_fdr,
        team_strength.get("source"),
        team_strength.get("table_weight"),
        team_prior.get("teams_ranked"),
        team_strength.get("max_played"),
    )

    me = {**me_raw, "squad": squad}
    log.info(
        "External match %s/%s (FF=%s JP=%s FotMob=%s filled=%s FFpts=%s tops=%s cache=%s)",
        external_meta.get("matched"),
        len(universe),
        external_meta.get("futbolfantasy"),
        external_meta.get("jornadaperfecta"),
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
    # Techos de need según caja real (antes de clasificar mercado)
    capped_needs = apply_realistic_need_caps(
        diagnostico_plantilla.get("structural_needs") or [],
        float(me.get("balance") or 0),
    )
    diagnostico_plantilla["structural_needs"] = capped_needs
    diagnostico_plantilla["realistic_price_cap"] = (
        int(capped_needs[0]["realistic_cap"]) if capped_needs else int(
            max(0.0, float(me.get("balance") or 0))
        )
    )
    diagnosis = merge_structural_into_diagnosis(diagnosis, diagnostico_plantilla)
    comp = detect_competition_phase(
        season_start=season_start,
        points_phase=points_phase,
    )
    competition_phase = str(comp.get("competition_phase") or "preseason")
    diagnostico_plantilla["competition_phase"] = competition_phase
    diagnostico_plantilla["days_to_kickoff"] = comp.get("days_to_kickoff")
    diagnostico_plantilla["season_start"] = comp.get("season_start")
    matchday_early = merge_matchday_sources(
        gw_bundle.get("matchday") if isinstance(gw_bundle.get("matchday"), dict) else None,
        external_meta.get("matchday") if isinstance(external_meta.get("matchday"), dict) else None,
    )
    if matchday_early and gw_bundle.get("schedule"):
        matchday_early["season_schedule"] = gw_bundle["schedule"]
    diagnostico_plantilla["matchday"] = matchday_early
    hours_j = resolve_hours_to_jornada(
        days_to_kickoff=comp.get("days_to_kickoff"),
        matchday=matchday_early,
        season_start=comp.get("season_start"),
    )
    diagnostico_plantilla["hours_to_jornada"] = hours_j
    # Pegado al cierre el mercado deja de ser patrimonio y pasa a ser jornada
    set_matchday_phase(
        resolve_phase(
            hours_to_jornada=hours_j,
            matchday=matchday_early,
            competition_phase=competition_phase,
        )
    )
    max_debt_me = me.get("max_debt")
    try:
        max_debt_f = float(max_debt_me) if max_debt_me is not None else None
    except (TypeError, ValueError):
        max_debt_f = None
    bal_future_me = me.get("balance_future")
    try:
        bal_future_f = float(bal_future_me) if bal_future_me is not None else None
    except (TypeError, ValueError):
        bal_future_f = None
    log.info(
        "Diagnóstico estructural salud=%s needs=%s consejos=%s phase=%s days_to_j1=%s hours_jornada=%s max_debt=%s",
        diagnostico_plantilla.get("salud_score"),
        len(diagnostico_plantilla.get("structural_needs") or []),
        len(diagnostico_plantilla.get("consejos") or []),
        competition_phase,
        comp.get("days_to_kickoff"),
        hours_j,
        max_debt_f,
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
        market_mode=market_mode,
        max_debt=max_debt_f,
        balance_future=bal_future_f,
        hours_to_jornada=hours_j,
        days_to_kickoff=comp.get("days_to_kickoff"),
        matchday=matchday_early,
    )
    rivals = [estimate_rival_liquidity(r) for r in league.get("rivals", [])]

    # FF production también en plantillas rivales (misma competición; skip en fixed)
    rival_flat: list[dict[str, Any]] = []
    for r in rivals:
        for p in r.get("squad") or []:
            rival_flat.append(dict(p))
    if rival_flat and external_key and not fixed_market:
        rival_ff, _ = enrich_players_with_ff_production(
            rival_flat,
            points_phase=points_phase,
            market_universe=market_ext,
            competition=external_key,
            mister_provider=str(league_rules.get("provider") or ""),
        )
        by_id = {str(p.get("id")): p for p in rival_ff if p.get("id")}
        for r in rivals:
            r["squad"] = [by_id.get(str(p.get("id")), p) for p in (r.get("squad") or [])]


    # Cláusulas: enriquecer top rivales solo si la liga las tiene activas (auction)
    clause_meta: dict[str, Any] = {"clauses": "skip", "known": 0}
    clauses_enabled = bool(league_rules.get("clauses")) and not fixed_market
    if clauses_enabled:
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
    else:
        if not league_rules.get("clauses"):
            clause_meta = {"clauses": "disabled_by_rules", "known": 0}
        for r in rivals:
            sq = r.get("squad") or []
            r["key_players"] = [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "position": p["position"],
                    "price": p.get("price"),
                    "clause": None,
                    "clause_known": False,
                    "market_value": p.get("market_value") or p.get("price"),
                    "points": p.get("points"),
                    "mister_avg": p.get("mister_avg") or p.get("form"),
                    "points_trend": p.get("points_trend"),
                    "prior_avg": p.get("prior_avg"),
                }
                for p in sorted(sq, key=lambda x: -float(x.get("price") or 0))[:5]
            ]

    phase_universe: list[dict[str, Any]] = list(squad) + list(market_ext)
    for r in rivals:
        phase_universe.extend(r.get("squad") or [])
    # Refinar fase con rivales (puede matizar active vs preseason)
    points_phase = detect_points_phase(phase_universe)
    comp = detect_competition_phase(
        season_start=season_start,
        points_phase=points_phase,
    )
    competition_phase = str(comp.get("competition_phase") or "preseason")
    diagnostico_plantilla["points_phase"] = points_phase
    diagnostico_plantilla["competition_phase"] = competition_phase
    diagnostico_plantilla["days_to_kickoff"] = comp.get("days_to_kickoff")
    diagnostico_plantilla["season_start"] = comp.get("season_start")
    hours_j = resolve_hours_to_jornada(
        days_to_kickoff=comp.get("days_to_kickoff"),
        matchday=matchday_early,
        season_start=comp.get("season_start"),
    )
    diagnostico_plantilla["hours_to_jornada"] = hours_j
    set_matchday_phase(
        resolve_phase(
            hours_to_jornada=hours_j,
            matchday=matchday_early,
            competition_phase=competition_phase,
        )
    )

    market_cycle = resolve_market_cycle(
        league_rules,
        market_mode=market_mode,
        hours_to_jornada=hours_j,
        auction_ends_ts=(
            live_meta.get("market_auction_ends")
            if isinstance(live_meta, dict)
            else None
        ),
        market_lock=(
            live_meta.get("market_lock") if isinstance(live_meta, dict) else None
        ),
    )
    diagnostico_plantilla["market_cycle"] = market_cycle

    recommended_xi = build_recommended_gw_xi(
        squad,
        formation=me.get("formation"),
        matchday=matchday_early or {},
        captain_rule=league_rules.get("captain")
        if isinstance(league_rules.get("captain"), dict)
        else None,
    )
    bootstrap_xi = resolve_bootstrap_xi(
        squad=squad,
        xi_summary=recommended_xi.get("summary") or {},
        hours_to_jornada=hours_j,
        market_cycle=market_cycle,
        competition_phase=competition_phase,
    )
    diagnostico_plantilla["bootstrap_xi"] = bootstrap_xi
    if bootstrap_xi.get("active"):
        log.info(
            "Bootstrap XI [%s]: %s/%s slots short=%s posture=%s cycle_end=%sh cycles_left=%s",
            slug,
            bootstrap_xi.get("xi_count"),
            bootstrap_xi.get("xi_target"),
            bootstrap_xi.get("slots_short"),
            bootstrap_xi.get("posture"),
            market_cycle.get("hours_to_end"),
            market_cycle.get("cycles_left_before_gw"),
        )

    opportunities = annotate_market_budget_risk(
        opportunities,
        rivals,
        float(me.get("balance") or 0),
        points_phase=points_phase,
        market_mode=market_mode,
        max_debt=max_debt_f,
        balance_future=bal_future_f,
        hours_to_jornada=hours_j,
        days_to_kickoff=comp.get("days_to_kickoff"),
        matchday=matchday_early,
    )
    rival_upgrades: list[dict[str, Any]] = []
    if not fixed_market:
        # Reserva para carencias de mercado antes de gastar en cláusulas
        market_reserved = 0.0
        needy_pos = {
            pos
            for pos, info in (diagnosis.get("by_position") or {}).items()
            if info.get("status") in ("critical", "warning")
            or info.get("coverage") in ("critical", "thin")
        }
        if needy_pos:
            market_reserved = max(0.0, float(me.get("balance") or 0) * 0.40)
        rival_upgrades = build_rival_upgrade_targets(
            me,
            diagnosis,
            rivals,
            balance=float(me.get("balance") or 0),
            points_phase=points_phase,
            max_debt=max_debt_f,
            balance_future=bal_future_f,
            hours_to_jornada=hours_j,
            days_to_kickoff=comp.get("days_to_kickoff"),
            matchday=matchday_early,
            market_reserved=market_reserved,
        )

    owned = set(league.get("owned_across_league") or [])
    owned.update(p["id"] for p in squad)
    owned.update(p["id"] for p in league.get("market", []))

    free_agents = find_free_agents_top(
        league.get("pool_top", []),
        owned,
        perf_idx,
        allow_synthetic=not honest_live,
        balance=float(me.get("balance") or 0),
        max_debt=max_debt_f,
        balance_future=bal_future_f,
        hours_to_jornada=hours_j,
        days_to_kickoff=comp.get("days_to_kickoff"),
        matchday=matchday_early,
    )

    # Se rellenan con la doctrina diaria una vez existen action_plan y once
    recommendations: list[dict[str, Any]] = []
    squad_notes: list[dict[str, Any]] = []

    # Tablero: plantilla perfecta desde TODO el pool de la liga
    my_uc = str(live_meta.get("id_uc") or me.get("team_id") or "")
    enriched_by_id: dict[str, dict[str, Any]] = {}
    for src in (squad, market_ext, opportunities):
        for p in src or []:
            pid = str(p.get("id") or p.get("player_id") or "")
            if pid:
                enriched_by_id[pid] = p

    board_candidates: list[dict[str, Any]] = []
    board_seen: set[str] = set()

    def _append_board_cand(
        raw: dict[str, Any],
        *,
        seller_hint: str | None = None,
        overwrite_as_market: bool = False,
    ) -> None:
        """Añade candidato al universo del tablero.

        overwrite_as_market: si ya existía como rival/cláusula (catálogo o HTML
        desfasado) pero hoy está libre en el mercado diario, el mercado gana.
        """
        pid = str(raw.get("id") or raw.get("player_id") or "")
        if not pid:
            return

        def _build_base(prev: dict[str, Any] | None = None) -> dict[str, Any]:
            base = dict(prev or enriched_by_id.get(pid) or raw)
            for k in (
                "id",
                "name",
                "position",
                "team",
                "team_id",
                "price",
                "market_value",
                "owner_id",
                "owner_name",
                "clause",
                "clause_known",
                "injury",
                "mister_avg",
                "form",
                "points",
                "min_bid",
                "puja_minima",
                "puja_recomendada",
                "puja_techo",
                "seller",
                "on_daily_market",
            ):
                if raw.get(k) is not None:
                    base[k] = raw[k]
            if overwrite_as_market:
                # Mercado del día gana: compra por puja, no por cláusula
                prev_was_rival = bool(
                    prev
                    and (
                        str(prev.get("seller") or "").lower() == "rival"
                        or (
                            prev.get("owner_id")
                            and str(prev.get("owner_id")) not in ("", "0")
                            and not prev.get("owned")
                        )
                    )
                )
                listed_name = None
                listed_oid = None
                clause_ref = None
                if prev_was_rival and prev:
                    listed_name = prev.get("owner_name") or prev.get("listed_by_name")
                    listed_oid = prev.get("owner_id") or prev.get("listed_by_owner_id")
                    clause_ref = prev.get("clause")
                base["seller"] = "market"
                base["on_daily_market"] = True
                raw_owner = raw.get("owner_id")
                if not raw_owner or str(raw_owner) in ("", "0"):
                    # Limpiar ownership para no disparar lógica de cláusula
                    base["owner_id"] = None
                    base["owner_name"] = None
                    base["clause"] = None
                    base["clause_known"] = False
                if prev_was_rival or raw.get("listed_by_rival"):
                    base["listed_by_rival"] = True
                    if listed_name or raw.get("listed_by_name"):
                        base["listed_by_name"] = raw.get("listed_by_name") or listed_name
                    if listed_oid or raw.get("listed_by_owner_id"):
                        base["listed_by_owner_id"] = (
                            raw.get("listed_by_owner_id") or listed_oid
                        )
                    if clause_ref is not None or raw.get("clause_reference") is not None:
                        base["clause_reference"] = (
                            raw.get("clause_reference")
                            if raw.get("clause_reference") is not None
                            else clause_ref
                        )
                else:
                    base.setdefault("listed_by_rival", False)
                if base.get("min_bid") is None and base.get("price"):
                    base["min_bid"] = base["price"]
                base["puja_recomendada"] = (
                    raw.get("puja_recomendada")
                    or base.get("puja_recomendada")
                    or base.get("min_bid")
                    or base.get("price")
                )
                return base
            owner_id = base.get("owner_id")
            is_mine = pid in owned or bool(raw.get("is_mine")) or (
                my_uc and str(owner_id or "") == my_uc
            )
            is_free = not owner_id or str(owner_id) in ("", "0")
            if is_mine:
                base["seller"] = "owned"
                base["on_daily_market"] = False
            elif is_free:
                base.setdefault("seller", seller_hint or "free")
                if base.get("on_daily_market") is None:
                    base["on_daily_market"] = base.get("seller") == "market"
                if base.get("min_bid") is None and base.get("price"):
                    base["min_bid"] = base["price"]
                base["puja_recomendada"] = (
                    base.get("puja_recomendada")
                    or base.get("min_bid")
                    or base.get("price")
                )
            else:
                base["seller"] = "rival"
                base["on_daily_market"] = False
                clause = (
                    base.get("clause")
                    if base.get("clause_known", base.get("clause") is not None)
                    else None
                )
                base["puja_recomendada"] = clause or base.get("price") or base.get("market_value")
                if clause is not None:
                    base["clause"] = clause
                    base["clause_known"] = True
            return base

        if pid in board_seen:
            if not overwrite_as_market:
                return
            for i, c in enumerate(board_candidates):
                if str(c.get("id") or c.get("player_id") or "") != pid:
                    continue
                if pid in owned or c.get("seller") == "owned":
                    return
                board_candidates[i] = _build_base(c)
                return
            return

        board_seen.add(pid)
        board_candidates.append(_build_base())

    # Primario: catálogo completo Mister
    for p in full_pool:
        _append_board_cand(p)
    # Mercado diario: puede corregir ownership rival obsoleto (jugador ya libre)
    for p in opportunities or []:
        daily = bool(p.get("on_daily_market") or p.get("seller") == "market")
        _append_board_cand(
            p,
            seller_hint="market" if daily else None,
            overwrite_as_market=daily,
        )
    for u in rival_upgrades or []:
        _append_board_cand(
            {
                "id": u.get("player_id"),
                "name": u.get("name"),
                "position": u.get("position"),
                "team": u.get("team"),
                "price": u.get("price") or u.get("market_value"),
                "puja_recomendada": u.get("clause") or u.get("bid"),
                "clause": u.get("clause"),
                "clause_known": u.get("clause_known", True),
                "owner_name": u.get("owner_name") or u.get("owner_team"),
                "owner_id": u.get("owner_id") or "rival",
                "on_daily_market": False,
                "seller": "rival",
                "production_score": u.get("production_score"),
                "ff_mister_avg": u.get("ff_mister_avg"),
                "external": u.get("external") or {},
                "lineup_prob": u.get("lineup_prob"),
                "sample_thin": u.get("sample_thin"),
            }
        )
    for riv in rivals or []:
        for p in riv.get("squad") or []:
            row = dict(p)
            row.setdefault("owner_id", riv.get("team_id") or "rival")
            row.setdefault("owner_name", riv.get("name") or riv.get("team"))
            _append_board_cand(row, seller_hint="rival")
    for p in league.get("pool_top") or []:
        _append_board_cand(p, seller_hint="free")

    # EP para jugadores del pool que no pasaron por el enrich de mercado
    missing_ff = [
        c
        for c in board_candidates
        if c.get("production_score") is None and not (c.get("external") or {}).get("production_score")
    ]
    if missing_ff and external_key:
        try:
            filled_ff, _ = enrich_players_with_ff_production(
                missing_ff,
                competition=external_key,
                market_universe=board_candidates,
                mister_provider=str(league_rules.get("provider") or ""),
            )
            by_ff = {str(p.get("id") or ""): p for p in filled_ff if p.get("id")}
            for i, c in enumerate(board_candidates):
                pid = str(c.get("id") or "")
                if pid in by_ff:
                    board_candidates[i] = by_ff[pid]
        except Exception as exc:  # noqa: BLE001
            log.warning("FF enrich board pool falló: %s", exc)

    log.info(
        "Board universe [%s]: pool_all=%s candidates=%s",
        slug,
        len(full_pool),
        len(board_candidates),
    )
    target_board = build_target_board(
        slug=slug,
        structural_needs=diagnostico_plantilla.get("structural_needs") or [],
        candidates=board_candidates,
        balance=float(me.get("balance") or 0),
        squad=squad,
        squad_value=float(me.get("squad_value") or 0) or None,
        price_series=price_series,
        market_mode=market_mode,
    )
    try:
        save_target_board(slug, target_board)
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo guardar target_board: %s", exc)

    funding_info = funding_plan_from_board(target_board, balance=float(me.get("balance") or 0))
    cycle_h = float(market_cycle.get("cycle_hours") or derive_cycle_hours(league_rules, market_mode=market_mode))
    cash_lag_h = float(
        market_cycle.get("cash_lag_hours")
        or derive_cash_lag_hours(cycle_h, league_rules)
    )
    funding_info["cycle_hours"] = cycle_h
    funding_info["cash_lag_hours"] = cash_lag_h
    if market_mode == "fixed":
        funding_info["liquidity_note"] = (
            "Mercado LFM: fichajes al precio listado "
            f"(ciclo ~{cycle_h:.0f}h). Gasta en el 15; liquidez = listados, no reserva."
        )
    funding_info = adjust_funding_for_bootstrap(
        funding_info,
        bootstrap=bootstrap_xi,
        balance=float(me.get("balance") or 0),
        opportunities=opportunities,
    )
    funding_info["bootstrap_xi_context"] = bootstrap_xi
    funding_info["market_cycle"] = market_cycle

    action_plan, daily_package = build_action_plan(
        me,
        diagnosis,
        opportunities,
        rivals,
        price_series=price_series,
        rival_upgrades=rival_upgrades,
        points_phase=points_phase,
        diagnostico_plantilla=diagnostico_plantilla,
        market_mode=market_mode,
        target_board=target_board,
        funding_info=funding_info,
        max_squad=config.league_max_squad(league_cfg),
        league_rules=league_rules,
        recommended_xi=recommended_xi,
    )

    # Recursos visuales oficiales. El índice del pool completa team_id/escudo
    # para acciones que solo conservan player_id, sin obligar al frontend a
    # conocer la forma de la respuesta privada de Mister.
    asset_sources: list[dict[str, Any]] = [
        *full_pool,
        *squad,
        *opportunities,
    ]
    for rival in rivals:
        asset_sources.extend(rival.get("squad") or [])
    attach_mister_assets(asset_sources)
    asset_index = {
        str(player.get("id") or player.get("player_id")): player
        for player in asset_sources
        if player.get("id") or player.get("player_id")
    }
    attach_mister_assets(squad, player_index=asset_index)
    attach_mister_assets(opportunities, player_index=asset_index)
    attach_mister_assets(action_plan, player_index=asset_index)
    attach_mister_assets(rival_upgrades, player_index=asset_index)
    attach_mister_assets(free_agents, player_index=asset_index)

    matchday_meta = matchday_early
    gw_xi_advice = build_gw_xi_advice(squad, matchday=matchday_meta or {})
    # recommended_xi ya calculado tras bootstrap (antes del action plan)
    my_gw_lineup = gw_bundle.get("my_lineup") if isinstance(gw_bundle.get("my_lineup"), dict) else None
    if my_gw_lineup:
        recommended_xi["current"] = {
            "captain_id": my_gw_lineup.get("captain_id"),
            "captain_set": bool(my_gw_lineup.get("captain_set")),
            "starter_ids": [r.get("player_id") for r in my_gw_lineup.get("starters") or []],
            "points": my_gw_lineup.get("points"),
            "rank": my_gw_lineup.get("rank"),
        }
    attach_mister_assets(gw_xi_advice, player_index=asset_index)
    attach_mister_assets(recommended_xi.get("players") or [], player_index=asset_index)

    # ¿Acertó el xPts la jornada pasada? Sin esto los pesos se afinan a ciegas
    calibration = build_calibration(
        load_history_snapshots(slug),
        names={pid: str(p.get("name") or "") for pid, p in asset_index.items() if p.get("name")},
        current_jornada=(matchday_meta or {}).get("jornada"),
    )
    log.info(
        "Calibración xPts [%s]: %s (n=%s sesgo=%s mae=%s)",
        slug,
        calibration.get("status"),
        calibration.get("sample"),
        calibration.get("bias"),
        calibration.get("mae"),
    )

    playbook = build_daily_playbook(
        hours_to_jornada=hours_j,
        matchday=matchday_meta,
        competition_phase=competition_phase,
        action_plan=action_plan,
        recommended_xi=recommended_xi,
        diagnostico=diagnostico_plantilla,
        me=me,
        league_rules=league_rules,
        calibration=calibration,
    )
    recommendations = playbook_to_recommendations(playbook)
    squad_notes = build_squad_notes(me, diagnosis, diagnostico_plantilla)
    log.info(
        "Playbook [%s]: fase=%s (%s) tareas=%s",
        slug,
        playbook.get("phase"),
        playbook.get("countdown_label"),
        (playbook.get("counts") or {}).get("todo"),
    )

    free_note = live_meta.get("free_agents_source") or ("seed" if free_agents and not honest_live else "unavailable")
    bal = float(me.get("balance") or 0)
    try:
        max_debt_out = float(me.get("max_debt")) if me.get("max_debt") is not None else max_debt_f
    except (TypeError, ValueError):
        max_debt_out = max_debt_f
    try:
        bal_future_out = (
            float(me.get("balance_future"))
            if me.get("balance_future") is not None
            else bal_future_f
        )
    except (TypeError, ValueError):
        bal_future_out = bal_future_f
    hours_out = diagnostico_plantilla.get("hours_to_jornada")
    if hours_out is None:
        hours_out = hours_j
    liquidity_now = liquidity_balance(bal, bal_future_out)
    solvency_ok = liquidity_now >= 0
    solvency_strict = solvency_strict_window(
        float(hours_out) if hours_out is not None else None
    )
    budget_pressure = "low"
    shortfall = float(funding_info.get("funding_shortfall") or 0)
    target = float(funding_info.get("funding_target") or 0)
    if shortfall > 0 and target > 0 and shortfall >= target * 0.35:
        budget_pressure = "high"
    elif shortfall > 0 or (target > 0 and bal < target):
        budget_pressure = "medium"
    elif solvency_strict and not solvency_ok:
        budget_pressure = "high"
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
        "Fútbol Fantasy: estado (lesión/sanción) y % de titularidad previsto; Jornada Perfecta solo si FF falla.",
        "FotMob: rating/minutos/goles/xG de los últimos 5 partidos, acotado a plantilla y candidatos del board.",
        (
            f"External matched={external_meta.get('matched')} "
            f"FF={external_meta.get('futbolfantasy')} "
            f"JP={external_meta.get('jornadaperfecta')} "
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
    if external_meta.get("note"):
        external_notes.append(str(external_meta["note"]))
    base_notes = live_meta.get("notes") if honest_live else [
        "Modo demo/mock: parte de PPG y libres TOP son seed local.",
    ]

    # Asegurar metadatos de liga en el objeto league del payload
    league_out = dict(league.get("league") or {})
    league_out["slug"] = slug
    league_out["id"] = league_out.get("id") or str(league_cfg.get("id_community") or "")
    league_out["name"] = league_out.get("name") or league_cfg.get("name")
    league_out["competition"] = (
        league_out.get("competition")
        or live_meta.get("competition")
        or league_cfg.get("competition")
    )
    league_out["id_competition"] = (
        league_out.get("id_competition")
        if league_out.get("id_competition") is not None
        else id_competition_i
    )
    league_out["market_mode"] = market_mode
    league_out["season_start"] = season_start
    league_out["max_squad"] = league_rules.get("max_squad") or config.league_max_squad(league_cfg)
    league_out["rules"] = league_rules
    league_out["provider"] = league_rules.get("provider")
    league_out["provider_label"] = league_rules.get("provider_label")

    now = datetime.now(timezone.utc)
    payload = {
        "generated_at": now.isoformat(),
        "league_slug": slug,
        "sources": {
            "mister": mister_source,
            "performance": perf_source,
            "honest_live": honest_live,
            "league_slug": slug,
            "id_community": live_meta.get("id_community") or league_cfg.get("id_community"),
            "competition": league_out.get("competition"),
            "id_competition": league_out.get("id_competition"),
            "market_mode": market_mode,
            "provider": league_rules.get("provider"),
            "rules_source": league_rules.get("source"),
            "factors": list(league_rules.get("factors") or []),
            "external": {
                "futbolfantasy": external_meta.get("futbolfantasy", "fail"),
                "jornadaperfecta": external_meta.get("jornadaperfecta", "skip"),
                "ff_matchday": external_meta.get("ff_matchday", "fail"),
                "fotmob": external_meta.get("fotmob", "skip"),
                "matched": external_meta.get("matched", 0),
                "fotmob_matched": external_meta.get("fotmob_matched", 0),
                "fotmob_filled": external_meta.get("fotmob_filled", 0),
                "cache_used": bool(external_meta.get("cache_used")),
                "rate_limited": rate_limit_report() or None,
                "note": external_meta.get("note"),
            },
            "fixture_difficulty": {
                "source": external_meta.get("fdr_source"),
                "table_weight": external_meta.get("fdr_table_weight"),
                "prior_teams": external_meta.get("fdr_prior_teams"),
                "fantasy_teams": external_meta.get("fdr_fantasy_teams"),
                "confidence": external_meta.get("fdr_confidence"),
            },
            "rivals_squads": bool(live_meta.get("rivals_squads_ok")),
            "clauses": clause_meta.get("clauses", "skip"),
            "clauses_known": clause_meta.get("known", 0),
            "points_phase": points_phase,
            "competition_phase": competition_phase,
            "season_start": comp.get("season_start") or season_start,
            "free_agents": free_note,
            "pool_size": live_meta.get("pool_size") or 0,
            "pool_free": live_meta.get("pool_free_count") or len(free_agents),
            "pool_owned": live_meta.get("pool_owned_count") or 0,
            "daily_market_count": n_market_day,
            "market_day_slots": int(getattr(config, "MARKET_DAY_SLOTS", 16)),
        },
        "league": league_out,
        "me": {
            "team_id": me.get("team_id"),
            "manager": me.get("manager"),
            "team_name": me.get("team_name"),
            "balance": me.get("balance"),
            "balance_future": bal_future_out,
            "max_debt": max_debt_out,
            "bid_cap": mister_bid_cap(bal, max_debt_out),
            "solvency_ok": solvency_ok,
            "solvency_strict": solvency_strict,
            "hours_to_jornada": hours_out,
            "squad_value": me.get("squad_value"),
            "rank": me.get("rank"),
            "points": me.get("points"),
            "formation": me.get("formation"),
            "squad": squad,
        },
        "kpis": {
            "balance": me.get("balance"),
            "balance_future": bal_future_out,
            "max_debt": max_debt_out,
            "bid_cap": mister_bid_cap(bal, max_debt_out),
            "solvency_ok": solvency_ok,
            "solvency_strict": solvency_strict,
            "hours_to_jornada": hours_out,
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
            "bootstrap_xi_active": bool(bootstrap_xi.get("active")),
            "xi_complete": bool((recommended_xi.get("summary") or {}).get("complete")),
            "market_cycle_hours": market_cycle.get("cycle_hours"),
            "market_hours_to_cycle_end": market_cycle.get("hours_to_end"),
            "market_cycles_before_gw": market_cycle.get("cycles_left_before_gw"),
        },
        "funding_plan": {
            "target": funding_info.get("funding_target"),
            "shortfall": funding_info.get("funding_shortfall"),
            "cash_tight": funding_info.get("cash_tight"),
            "cash_reserved": funding_info.get("cash_reserved"),
            "gaps": funding_info.get("gap_costs") or [],
            "positions": funding_info.get("positions") or [],
            "primary_targets": funding_info.get("primary_targets") or [],
            "from_target_board": bool(funding_info.get("from_target_board")),
            "wealth": funding_info.get("wealth"),
            "totals": funding_info.get("totals"),
            "settlement": funding_info.get("settlement") or "market_cycle",
            "cycle_hours": funding_info.get("cycle_hours")
            if funding_info.get("cycle_hours") is not None
            else int(getattr(config, "MARKET_CYCLE_HOURS", 24) or 24),
            "cash_lag_hours": funding_info.get("cash_lag_hours")
            if funding_info.get("cash_lag_hours") is not None
            else int(getattr(config, "MARKET_CYCLE_HOURS", 24) or 24) * 2,
            "liquidity_note": funding_info.get("liquidity_note")
            or (
                "Gasta en el 15 ahora. Liquidez = jugadores listados, no reserva de caja."
            ),
            "economy": (league_rules.get("economy") if isinstance(league_rules.get("economy"), dict) else None),
            "expected_gw_cash": (
                (league_rules.get("economy") or {}).get("expected_gw_cash")
                if isinstance(league_rules.get("economy"), dict)
                else None
            ),
            "gw_cash_bonus": (
                bool((league_rules.get("economy") or {}).get("gw_cash_bonus"))
                if isinstance(league_rules.get("economy"), dict)
                else False
            ),
        },
        "target_board": target_board,
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
        "daily_playbook": playbook,
        "matchday": matchday_meta,
        "gw_xi_advice": gw_xi_advice,
        "recommended_xi": recommended_xi,
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
            "model_calibration": calibration,
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


def prune_history(retention_days: int = config.HISTORY_RETENTION_DAYS, history_dir: Path | None = None) -> None:
    hdir = history_dir or config.HISTORY_DIR
    if not hdir.exists():
        return
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=retention_days)
    for path in hdir.glob("*.json"):
        try:
            day = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            path.unlink(missing_ok=True)
            log.info("Snapshot antiguo eliminado: %s", path.name)


def write_outputs(payload: dict[str, Any], *, league_cfg: dict[str, Any] | None = None) -> Path:
    """Escribe JSON de la liga + history slim de precios; si es default, copia a latest_data.json."""
    league_cfg = dict(league_cfg or config.get_league(payload.get("league_slug")))
    slug = str(league_cfg.get("slug") or config.DEFAULT_LEAGUE_SLUG)
    out_path = config.league_data_path(slug)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_json(out_path, payload)

    hist_dir = config.league_history_dir(slug)
    hist_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    save_json(hist_dir / f"{day}.json", build_price_history_snapshot(payload, day=day))
    prune_history(history_dir=hist_dir)

    is_default = slug == config.DEFAULT_LEAGUE_SLUG or bool(league_cfg.get("default"))
    if is_default:
        save_json(config.LATEST_DATA_PATH, payload)

    log.info("Escrito %s (slug=%s default=%s)", out_path, slug, is_default)
    return out_path


def write_leagues_index(entries: list[dict[str, Any]], *, merge: bool = False) -> None:
    """
    Escribe leagues.json.
    Si merge=True (p.ej. --league slug), conserva entradas de otras ligas ya indexadas.
    """
    by_slug: dict[str, dict[str, Any]] = {}
    if merge and config.LEAGUES_INDEX_PATH.is_file():
        try:
            prev = json.loads(config.LEAGUES_INDEX_PATH.read_text(encoding="utf-8"))
            for e in prev.get("leagues") or []:
                if isinstance(e, dict) and e.get("slug"):
                    by_slug[str(e["slug"])] = e
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo fusionar leagues.json previo: %s", exc)
    for e in entries:
        if e.get("slug"):
            by_slug[str(e["slug"])] = e
    # Orden del catálogo efectivo; el resto al final
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for L in config.get_effective_leagues():
        slug = str(L["slug"])
        if slug in by_slug:
            ordered.append(by_slug[slug])
            seen.add(slug)
    for slug, e in by_slug.items():
        if slug not in seen:
            ordered.append(e)
    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "default_slug": config.DEFAULT_LEAGUE_SLUG,
        "leagues": ordered,
    }
    save_json(config.LEAGUES_INDEX_PATH, index)
    log.info("Índice ligas → %s (%s)", config.LEAGUES_INDEX_PATH, len(ordered))


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Mister Fantasy Advisor — data engine")
    parser.add_argument(
        "--league",
        default="all",
        help="slug de liga, id_community, o 'all' (default)",
    )
    args = parser.parse_args(argv)

    log.info("=== Mister Fantasy Advisor — data engine ===")
    log.info("USE_MISTER_MOCK=%s USE_PERF_SEED=%s", config.USE_MISTER_MOCK, config.USE_PERF_SEED)

    # El throttling se comparte entre ligas: si FF nos corta en la primera, no
    # tiene sentido insistir en las tres siguientes.
    reset_rate_limits()

    discovered: list[dict[str, Any]] = []
    if not config.USE_MISTER_MOCK:
        try:
            discovered = discover_leagues()
        except Exception as exc:  # noqa: BLE001
            log.warning("discover_leagues falló: %s — uso overrides/fallback", exc)
            discovered = []
    resolved = config.resolve_leagues(discovered or None)
    config.set_effective_leagues(resolved)
    log.info(
        "Catálogo ligas (%s): %s",
        "discovery" if discovered else "overrides/fallback",
        ", ".join(f"{L['slug']}#{L.get('id_community')}" for L in resolved),
    )

    if str(args.league).strip().lower() in ("all", "*", ""):
        targets = [dict(L) for L in config.get_effective_leagues()]
        merge_index = False
    else:
        targets = [config.get_league(str(args.league))]
        merge_index = True

    index_entries: list[dict[str, Any]] = []
    for L in targets:
        slug = L["slug"]
        log.info("--- Liga %s (%s) ---", slug, L.get("name"))
        payload = build_payload(L)
        path = write_outputs(payload, league_cfg=L)
        rules = (payload.get("league") or {}).get("rules") or {}
        index_entries.append(
            {
                "slug": slug,
                "name": L.get("name") or (payload.get("league") or {}).get("name"),
                "competition": (payload.get("league") or {}).get("competition") or L.get("competition"),
                "id_community": L.get("id_community"),
                "id_competition": L.get("id_competition"),
                "season_start": L.get("season_start"),
                "default": bool(L.get("default")),
                "generated_at": payload.get("generated_at"),
                "path": f"leagues/{slug}/latest_data.json",
                "balance": (payload.get("me") or {}).get("balance"),
                "rank": (payload.get("me") or {}).get("rank"),
                "market_mode": rules.get("market_mode") or L.get("market_mode"),
                "max_squad": rules.get("max_squad") or L.get("max_squad"),
                "provider": rules.get("provider"),
                "provider_label": rules.get("provider_label"),
                "clauses": rules.get("clauses"),
                "loans": rules.get("loans"),
                "factors": list(rules.get("factors") or []),
                "rules": {
                    "provider": rules.get("provider"),
                    "provider_label": rules.get("provider_label"),
                    "max_squad": rules.get("max_squad"),
                    "market_mode": rules.get("market_mode"),
                    "clauses": rules.get("clauses"),
                    "loans": rules.get("loans"),
                    "market_speed": rules.get("market_speed"),
                    "factors": list(rules.get("factors") or []),
                    "source": rules.get("source"),
                },
            }
        )
        log.info(
            "OK [%s] — oportunidades=%s libres=%s buy_now=%s",
            slug,
            len(payload["market_opportunities"]),
            len(payload["free_agents_top"]),
            (payload.get("kpis") or {}).get("buy_now_count"),
        )

    write_leagues_index(index_entries, merge=merge_index)
    return 0


if __name__ == "__main__":
    sys.exit(main())
