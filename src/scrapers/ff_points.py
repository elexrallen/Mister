"""
Fútbol Fantasy Analytics — puntos Mister Mixto por temporada.

Fuente: /analytics/estadisticas-puntos/{year}
Fail-soft + caché en disco (TTL 36h).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from .http_util import get_soup

log = logging.getLogger("scrapers.ff_points")

BASE = "https://www.futbolfantasy.com"
ANALYTICS = f"{BASE}/analytics/estadisticas-puntos"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_PATH = CACHE_DIR / "ff_mister_points.json"
CACHE_TTL_HOURS = 36

# Temporadas a scrapear: year en URL = fin de temporada (2026 → 2025/26)
DEFAULT_SEASONS = [2026, 2025]

MIN_APPS_TOP = 15
TOP_PERCENTILE = 0.85
TOP_AVG_FLOOR = 5.5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_num(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().replace("\xa0", "").replace(" ", "")
    if not s or s in ("-", "—", "–"):
        return None
    s = s.replace(".", "").replace(",", ".") if s.count(",") == 1 and s.count(".") > 1 else s.replace(",", ".")
    # European: 1.234,56 rare here; usually 4,50 or 202
    try:
        return float(s)
    except ValueError:
        m = re.search(r"-?\d+(?:[.,]\d+)?", s)
        if not m:
            return None
        try:
            return float(m.group(0).replace(",", "."))
        except ValueError:
            return None


def _col_indexes(soup) -> tuple[int, int, int] | None:
    """Return (apps_idx, mister_total_idx, mister_avg_idx) from thead titles."""
    ths = soup.select("table thead th")
    if not ths:
        return None
    apps_idx = None
    total_idx = None
    avg_idx = None
    for i, th in enumerate(ths):
        title = (th.get("title") or th.get_text(" ", strip=True) or "").strip()
        text = th.get_text(" ", strip=True)
        if text == "P" and apps_idx is None:
            apps_idx = i
        if title == "Total - Mister Mixto":
            total_idx = i
        if title == "Media - Mister Mixto":
            avg_idx = i
    if apps_idx is None or total_idx is None or avg_idx is None:
        log.warning(
            "FF points: columnas Mister Mixto no encontradas (apps=%s total=%s avg=%s)",
            apps_idx,
            total_idx,
            avg_idx,
        )
        return None
    return apps_idx, total_idx, avg_idx


def _parse_season(year: int) -> list[dict[str, Any]]:
    url = f"{ANALYTICS}/{year}"
    soup = get_soup(url, timeout=25)
    if not soup:
        return []
    idxs = _col_indexes(soup)
    if not idxs:
        return []
    apps_i, tot_i, avg_i = idxs
    out: list[dict[str, Any]] = []
    for tr in soup.select("table tbody tr"):
        tds = tr.select("td")
        if len(tds) <= max(apps_i, tot_i, avg_i):
            continue
        name_el = tr.select_one("a.player-name span.d-none.d-md-inline") or tr.select_one(
            "a.player-name"
        )
        name = (name_el.get_text(" ", strip=True) if name_el else "").strip()
        if not name:
            img = tr.select_one("img.player-foto[alt]")
            name = (img.get("alt") or "").strip() if img else ""
        if not name or len(name) < 2:
            continue
        team_el = tr.select_one(".player-equipo span")
        team = (team_el.get_text(" ", strip=True) if team_el else None) or None
        if not team:
            img_t = tr.select_one(".player-equipo img[alt]")
            team = (img_t.get("alt") or None) if img_t else None
        apps = _parse_num(tds[apps_i].get_text(" ", strip=True))
        points = _parse_num(tds[tot_i].get_text(" ", strip=True))
        avg = _parse_num(tds[avg_i].get_text(" ", strip=True))
        link = tr.select_one("a.player-name")
        out.append(
            {
                "name": name,
                "team": team,
                "apps": int(apps) if apps is not None else 0,
                "mister_points": points,
                "mister_avg": avg,
                "season": year,
                "season_label": f"{year - 1}/{str(year)[2:]}",
                "profile_url": link.get("href") if link else None,
                "source": "futbolfantasy_points",
            }
        )
    log.info("FF Mister Mixto %s: %d jugadores", year, len(out))
    return out


def _load_cache() -> dict[str, Any] | None:
    try:
        if not CACHE_PATH.is_file():
            return None
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        ts = data.get("fetched_at")
        if not ts:
            return None
        fetched = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if _now() - fetched > timedelta(hours=CACHE_TTL_HOURS):
            return None
        return data
    except Exception as exc:  # noqa: BLE001
        log.warning("FF points cache ilegible: %s", exc)
        return None


def _save_cache(payload: dict[str, Any]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.warning("FF points cache no escrita: %s", exc)


def compute_top_threshold(records: list[dict[str, Any]]) -> float:
    """Media mínima P85 entre jugadores con PJ suficientes (o floor)."""
    avgs = sorted(
        float(r["mister_avg"])
        for r in records
        if r.get("mister_avg") is not None and int(r.get("apps") or 0) >= MIN_APPS_TOP
    )
    if not avgs:
        return TOP_AVG_FLOOR
    idx = int(len(avgs) * TOP_PERCENTILE)
    idx = min(max(idx, 0), len(avgs) - 1)
    return max(TOP_AVG_FLOOR, round(avgs[idx], 2))


def is_top_production(avg: float | None, apps: int, threshold: float) -> bool:
    if avg is None:
        return False
    if apps < MIN_APPS_TOP and avg < TOP_AVG_FLOOR + 0.5:
        return False
    return float(avg) >= threshold


def production_score(
    *,
    avg: float | None,
    prior_avg: float | None,
    apps: int,
    lineup_prob: float | None = None,
    mister_avg: float | None = None,
    points_phase: str = "preseason",
) -> float:
    """
    Score 0–100 para gestión diaria.
    Pretemporada: FF (+ prior). Active: mezcla Mister vivo + FF.
    """
    score = 0.0
    primary = avg
    if points_phase == "active" and mister_avg is not None and float(mister_avg) > 0:
        # Temporada viva manda; FF como ancla
        primary = 0.65 * float(mister_avg) + 0.35 * float(avg or mister_avg)
    elif primary is None and prior_avg is not None:
        primary = prior_avg

    if primary is not None:
        # Media Mister típica ~2–8 → map a 0–70
        score += max(0.0, min(70.0, (float(primary) / 8.0) * 70.0))
    if prior_avg is not None and avg is not None and points_phase != "active":
        # Tendencia histórica suave
        delta = float(avg) - float(prior_avg)
        score += max(-5.0, min(5.0, delta * 3))
    elif prior_avg is not None and avg is None:
        score += max(0.0, min(55.0, (float(prior_avg) / 8.0) * 55.0))

    # Fiabilidad por partidos
    if apps >= 30:
        score += 15
    elif apps >= 15:
        score += 10
    elif apps >= 8:
        score += 5

    if lineup_prob is not None:
        lp = float(lineup_prob)
        if lp > 1.5:  # viene en %
            lp = lp / 100.0
        score += max(0.0, min(15.0, lp * 15.0))

    return round(max(0.0, min(100.0, score)), 1)


def fetch_ff_mister_points(
    seasons: list[int] | None = None,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    """
    Devuelve:
      {
        status, threshold, seasons,
        by_season: {2026: [records...], ...},
        records: flat list (latest season first),
        fetched_at
      }
    """
    seasons = seasons or list(DEFAULT_SEASONS)
    if use_cache:
        cached = _load_cache()
        if cached and cached.get("by_season"):
            log.info("FF Mister points desde caché (%s)", cached.get("fetched_at"))
            return cached

    by_season: dict[str, list[dict[str, Any]]] = {}
    status = "ok"
    try:
        for year in seasons:
            try:
                rows = _parse_season(year)
                by_season[str(year)] = rows
                if not rows:
                    status = "partial"
            except Exception as exc:  # noqa: BLE001
                log.warning("FF points season %s falló: %s", year, exc)
                by_season[str(year)] = []
                status = "partial"
    except Exception as exc:  # noqa: BLE001
        log.warning("FF points scraper falló: %s", exc)
        status = "fail"
        by_season = {str(y): [] for y in seasons}

    primary_year = str(seasons[0])
    primary = by_season.get(primary_year) or []
    threshold = compute_top_threshold(primary) if primary else TOP_AVG_FLOOR

    # Flat: primary then others
    flat: list[dict[str, Any]] = []
    for y in seasons:
        flat.extend(by_season.get(str(y)) or [])

    payload = {
        "status": status if any(by_season.values()) else "fail",
        "threshold": threshold,
        "seasons": seasons,
        "by_season": by_season,
        "records": flat,
        "fetched_at": _now().isoformat().replace("+00:00", "Z"),
        "source": "futbolfantasy_analytics",
    }
    if payload["status"] != "fail":
        _save_cache(payload)
    return payload


__all__ = [
    "fetch_ff_mister_points",
    "compute_top_threshold",
    "is_top_production",
    "production_score",
    "MIN_APPS_TOP",
    "TOP_AVG_FLOOR",
]
