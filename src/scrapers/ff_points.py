"""
Fútbol Fantasy Analytics — puntos fantasy por temporada.

LaLiga:  /analytics/estadisticas-puntos/{year}          → Mister Mixto
Premier: /analytics/premier-league/estadisticas-puntos/{year} → Fantasy RPG
         (fallback Futmondo Stats / Mister Mixto si aparecen)

Fail-soft + caché en disco (TTL 36h) por competición.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Any

from .http_util import get_soup

log = logging.getLogger("scrapers.ff_points")

BASE = "https://www.futbolfantasy.com"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_TTL_HOURS = 36

# year en URL = fin de temporada (2026 → 2025/26). Se calcula por calendario.
# Hasta junio inclusive: temporada en curso termina en `hoy.year`.
# Desde julio: nueva temporada (fin = hoy.year + 1); la previa completa es hoy.year.
DEFAULT_SEASONS = [2026, 2025]  # fallback estático; preferir default_ff_seasons()


def default_ff_seasons(today: date | None = None) -> list[int]:
    """
    Temporadas FF a scrapear: [fin_actual_o_próxima, fin_previa_completa].
    Ej. 2026-08-03 → [2027, 2026]  (26/27 vacía + 25/26).
    Ej. 2026-03-01 → [2026, 2025]  (25/26 en curso + 24/25).
    """
    d = today or date.today()
    if d.month >= 7:
        current_end = d.year + 1
    else:
        current_end = d.year
    return [current_end, current_end - 1]

MIN_APPS_TOP = 15
RELIABLE_APPS = 20  # muestra decente ≈ media temporada para confiar en la media
THIN_APPS = 8  # por debajo: muestra corta (sin bonus de volumen)
TOP_PERCENTILE = 0.85
TOP_AVG_FLOOR = 5.5  # LaLiga Mister Mixto

# Perfil por competición: URL, columnas de scoring (prioridad), escala media, floor TOP
COMPETITION_PROFILES: dict[str, dict[str, Any]] = {
    "laliga": {
        "url_tpl": f"{BASE}/analytics/estadisticas-puntos/{{year}}",
        "cache_name": "ff_mister_points.json",
        "score_columns": [
            ("Total - Mister Mixto", "Media - Mister Mixto"),
        ],
        "avg_scale": 8.0,
        "top_floor": 5.5,
        "label": "Mister Mixto",
    },
    "premier": {
        "url_tpl": f"{BASE}/analytics/premier-league/estadisticas-puntos/{{year}}",
        "cache_name": "ff_premier_points.json",
        "score_columns": [
            ("Total - Fantasy RPG", "Media - Fantasy RPG"),
            ("Total - Futmondo Stats", "Media - Futmondo Stats"),
            ("Total - Mister Mixto", "Media - Mister Mixto"),
        ],
        "avg_scale": 16.0,  # RPG ~8–18 → map similar a Mister Mixto 2–8
        "top_floor": 10.0,
        "label": "Fantasy RPG",
    },
}

# Partidos de liga por temporada (proxy titularidad fallback = apps / SEASON_GAMES).
# Preferir % Titular de ficha FF (llegadas a mitad de temporada).
SEASON_GAMES = 38
MIXTO_AVG_SCALE = 8.0


def _profile(competition: str) -> dict[str, Any]:
    key = (competition or "laliga").strip().lower()
    return COMPETITION_PROFILES.get(key, COMPETITION_PROFILES["laliga"])


def league_avg_scale(competition: str | None = None) -> float:
    """Media “buena” de referencia por competición (Mister Mixto ~8, Fantasy RPG ~16)."""
    return float(_profile(competition or "laliga")["avg_scale"])


def scale_threshold(mixto_value: float, avg_scale: float | None = None) -> float:
    """Escala un umbral pensado en Mister Mixto (~8) a la escala de la liga."""
    scale = float(avg_scale) if avg_scale and avg_scale > 0 else MIXTO_AVG_SCALE
    return float(mixto_value) * (scale / MIXTO_AVG_SCALE)


def apps_to_lineup_prob(apps: float | int | None, *, season_games: int = SEASON_GAMES) -> int | None:
    """Equivale PJ históricos a % titularidad (0–100). None si no hay muestra."""
    if apps is None:
        return None
    try:
        n = float(apps)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    games = max(1, int(season_games or SEASON_GAMES))
    return int(min(100, round(100.0 * n / float(games))))


def resolve_avg_scale(
    player: dict[str, Any] | None = None,
    *,
    competition: str | None = None,
    avg_scale: float | None = None,
    ff_scoring: str | None = None,
) -> float:
    """
    Resuelve escala de media fantasy (Mixto ~8 / RPG ~16).
    Prioridad: avg_scale explícito → jugador → scoring label → competition profile.
    """
    if avg_scale is not None:
        try:
            v = float(avg_scale)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    if player:
        for key in ("ff_avg_scale",):
            if player.get(key) is not None:
                try:
                    v = float(player[key])
                    if v > 0:
                        return v
                except (TypeError, ValueError):
                    pass
        ext = player.get("external") or {}
        if isinstance(ext, dict) and ext.get("ff_avg_scale") is not None:
            try:
                v = float(ext["ff_avg_scale"])
                if v > 0:
                    return v
            except (TypeError, ValueError):
                pass
        label = (
            ff_scoring
            or player.get("ff_scoring")
            or (ext.get("ff_scoring") if isinstance(ext, dict) else None)
        )
        if label and "rpg" in str(label).lower():
            return float(COMPETITION_PROFILES["premier"]["avg_scale"])
        if label and "mixto" in str(label).lower():
            return float(COMPETITION_PROFILES["laliga"]["avg_scale"])
    if ff_scoring and "rpg" in str(ff_scoring).lower():
        return float(COMPETITION_PROFILES["premier"]["avg_scale"])
    if competition:
        return league_avg_scale(competition)
    return MIXTO_AVG_SCALE


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_num(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().replace("\xa0", "").replace(" ", "")
    if not s or s in ("-", "—", "–"):
        return None
    s = s.replace(".", "").replace(",", ".") if s.count(",") == 1 and s.count(".") > 1 else s.replace(",", ".")
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


def _col_indexes(
    soup,
    score_columns: list[tuple[str, str]],
) -> tuple[int, int, int, str] | None:
    """
    Return (apps_idx, total_idx, avg_idx, scoring_label) from thead titles.
    score_columns: lista de (total_title, avg_title) en orden de preferencia.
    """
    ths = soup.select("table thead th")
    if not ths:
        return None
    apps_idx = None
    by_title: dict[str, int] = {}
    for i, th in enumerate(ths):
        title = (th.get("title") or "").strip()
        text = th.get_text(" ", strip=True)
        if title:
            by_title[title] = i
        # Partidos: title "Partidos" o texto "P"
        if apps_idx is None and (title == "Partidos" or text == "P"):
            apps_idx = i

    total_idx = avg_idx = None
    label = ""
    for tot_name, avg_name in score_columns:
        if tot_name in by_title and avg_name in by_title:
            total_idx = by_title[tot_name]
            avg_idx = by_title[avg_name]
            label = tot_name.replace("Total - ", "")
            break

    if apps_idx is None or total_idx is None or avg_idx is None:
        log.warning(
            "FF points: columnas no encontradas (apps=%s total=%s avg=%s tried=%s)",
            apps_idx,
            total_idx,
            avg_idx,
            [c[0] for c in score_columns],
        )
        return None
    return apps_idx, total_idx, avg_idx, label


def _parse_season(year: int, competition: str = "laliga") -> list[dict[str, Any]]:
    prof = _profile(competition)
    url = str(prof["url_tpl"]).format(year=year)
    soup = get_soup(url, timeout=25)
    if not soup:
        return []
    idxs = _col_indexes(soup, list(prof["score_columns"]))
    if not idxs:
        return []
    apps_i, tot_i, avg_i, scoring_label = idxs
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
                "scoring": scoring_label,
                "competition": (competition or "laliga").strip().lower(),
            }
        )
    log.info(
        "FF points [%s/%s] %s: %d jugadores (%s)",
        competition,
        year,
        scoring_label,
        len(out),
        url.split(".com")[-1],
    )
    return out


def _cache_path(competition: str) -> Path:
    return CACHE_DIR / str(_profile(competition)["cache_name"])


def _load_cache(competition: str) -> dict[str, Any] | None:
    path = _cache_path(competition)
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        ts = data.get("fetched_at")
        if not ts:
            return None
        fetched = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if _now() - fetched > timedelta(hours=CACHE_TTL_HOURS):
            return None
        # Evitar mezclar caches de otra competición
        if data.get("competition") and data.get("competition") != (competition or "laliga"):
            return None
        return data
    except Exception as exc:  # noqa: BLE001
        log.warning("FF points cache ilegible (%s): %s", competition, exc)
        return None


def _save_cache(competition: str, payload: dict[str, Any]) -> None:
    path = _cache_path(competition)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.warning("FF points cache no escrita (%s): %s", competition, exc)


def compute_top_threshold(
    records: list[dict[str, Any]],
    *,
    top_floor: float | None = None,
) -> float:
    """Media mínima P85 entre jugadores con PJ suficientes (o floor)."""
    floor = float(top_floor if top_floor is not None else TOP_AVG_FLOOR)
    avgs = sorted(
        float(r["mister_avg"])
        for r in records
        if r.get("mister_avg") is not None and int(r.get("apps") or 0) >= MIN_APPS_TOP
    )
    if not avgs:
        return floor
    idx = int(len(avgs) * TOP_PERCENTILE)
    idx = min(max(idx, 0), len(avgs) - 1)
    return max(floor, round(avgs[idx], 2))


def is_top_production(
    avg: float | None,
    apps: int,
    threshold: float,
    *,
    top_floor: float | None = None,
) -> bool:
    """Élite FF solo con muestra suficiente (≥ MIN_APPS_TOP)."""
    if avg is None:
        return False
    if int(apps or 0) < MIN_APPS_TOP:
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
    avg_scale: float = 8.0,
    prior_apps: int | None = None,
) -> float:
    """
    Score 0–100 para gestión diaria.
    Pretemporada: FF (+ prior). Active: mezcla Mister vivo + FF.
    avg_scale: media “buena” de referencia (Mister Mixto ~8, Fantasy RPG ~16).

    La media se pondera por volumen (shrinkage hacia media de liga) para que
    7 pts en 4 PJ no brille como 7 pts en 30 PJ.
    Sin PJ y media ≤0 (actual o previa) no inventa producción “buena”.
    """
    scale = float(avg_scale) if avg_scale and avg_scale > 0 else 8.0
    league_avg = 0.55 * scale
    n_apps = max(0, int(apps or 0))
    n_prior = max(0, int(prior_apps)) if prior_apps is not None else None
    score = 0.0

    def _positive(v: float | None) -> float | None:
        if v is None:
            return None
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return None
        return fv if fv > 0 else None

    # Media actual solo cuenta si hubo PJ; media 0 / 0 PJ no rellena con media de liga
    avg_u = _positive(avg) if n_apps > 0 else None
    # Previa con 0 PJ explícitos → descartar; si prior_apps es None, aceptar media >0
    if n_prior is not None and n_prior <= 0:
        prior_u = None
    else:
        prior_u = _positive(prior_avg)

    primary: float | None = None
    used_prior_only = False
    if points_phase == "active" and mister_avg is not None and float(mister_avg) > 0:
        primary = 0.65 * float(mister_avg) + 0.35 * float(avg_u if avg_u is not None else mister_avg)
    elif avg_u is not None:
        primary = avg_u
    elif prior_u is not None:
        primary = prior_u
        used_prior_only = True

    if primary is not None:
        if used_prior_only:
            sample = n_prior if n_prior is not None else (RELIABLE_APPS // 2)
            prior_w = min(0.5, max(0.15, float(sample) / float(RELIABLE_APPS)))
            effective = prior_w * float(primary) + (1.0 - prior_w) * league_avg
            score += max(0.0, min(55.0, (effective / scale) * 55.0))
        else:
            weight = min(1.0, n_apps / float(RELIABLE_APPS))
            effective = weight * float(primary) + (1.0 - weight) * league_avg
            score += max(0.0, min(70.0, (effective / scale) * 70.0))

    if (
        prior_u is not None
        and avg_u is not None
        and points_phase != "active"
        and n_apps >= THIN_APPS
    ):
        score += max(-5.0, min(5.0, (float(avg_u) - float(prior_u)) * 3))

    vol = n_prior if used_prior_only and n_prior is not None else n_apps
    if vol >= 30:
        score += 15
    elif vol >= 15:
        score += 10
    elif vol >= THIN_APPS:
        score += 5

    if lineup_prob is not None:
        lp = float(lineup_prob)
        if lp > 1.5:
            lp = lp / 100.0
        if primary is not None:
            score += max(0.0, min(15.0, lp * 15.0))
        elif lp > 0:
            score += max(0.0, min(8.0, lp * 8.0))

    return round(max(0.0, min(100.0, score)), 1)


def fetch_ff_mister_points(
    seasons: list[int] | None = None,
    *,
    competition: str = "laliga",
    use_cache: bool = True,
) -> dict[str, Any]:
    """
    Devuelve:
      {
        status, threshold, seasons, competition, scoring, avg_scale, top_floor,
        by_season: {2026: [records...], ...},
        records: flat list (latest season first),
        fetched_at
      }
    """
    comp = (competition or "laliga").strip().lower()
    prof = _profile(comp)
    seasons = list(seasons) if seasons else default_ff_seasons()
    if use_cache:
        cached = _load_cache(comp)
        if cached and cached.get("by_season"):
            cached_seasons = [int(x) for x in (cached.get("seasons") or [])]
            if cached_seasons == [int(x) for x in seasons]:
                log.info(
                    "FF points [%s] desde caché (%s)",
                    comp,
                    cached.get("fetched_at"),
                )
                return cached
            log.info(
                "FF points [%s] caché obsoleta (seasons %s ≠ %s) → rescrape",
                comp,
                cached_seasons,
                seasons,
            )

    by_season: dict[str, list[dict[str, Any]]] = {}
    status = "ok"
    scoring_used = str(prof.get("label") or "")
    try:
        for year in seasons:
            try:
                rows = _parse_season(year, competition=comp)
                by_season[str(year)] = rows
                if rows and rows[0].get("scoring"):
                    scoring_used = str(rows[0]["scoring"])
                if not rows:
                    status = "partial"
            except Exception as exc:  # noqa: BLE001
                log.warning("FF points [%s] season %s falló: %s", comp, year, exc)
                by_season[str(year)] = []
                status = "partial"
    except Exception as exc:  # noqa: BLE001
        log.warning("FF points scraper [%s] falló: %s", comp, exc)
        status = "fail"
        by_season = {str(y): [] for y in seasons}

    primary_year = str(seasons[0])
    primary = by_season.get(primary_year) or []
    # Si la temporada actual está vacía (pretemporada), usar prior para threshold
    thr_source = primary
    if not thr_source:
        for y in seasons[1:]:
            if by_season.get(str(y)):
                thr_source = by_season[str(y)]
                break
    top_floor = float(prof["top_floor"])
    threshold = compute_top_threshold(thr_source, top_floor=top_floor) if thr_source else top_floor

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
        "competition": comp,
        "scoring": scoring_used,
        "avg_scale": float(prof["avg_scale"]),
        "top_floor": top_floor,
    }
    if payload["status"] != "fail":
        _save_cache(comp, payload)
    return payload


__all__ = [
    "fetch_ff_mister_points",
    "compute_top_threshold",
    "is_top_production",
    "production_score",
    "league_avg_scale",
    "scale_threshold",
    "apps_to_lineup_prob",
    "resolve_avg_scale",
    "default_ff_seasons",
    "MIN_APPS_TOP",
    "TOP_AVG_FLOOR",
    "THIN_APPS",
    "SEASON_GAMES",
    "MIXTO_AVG_SCALE",
    "DEFAULT_SEASONS",
    "COMPETITION_PROFILES",
]
