"""
Scraper Fútbol Fantasy — lesionados/sancionados + % titularidad por equipo.
Fail-soft: ante DOM distinto → [] + warning.
Soporta LaLiga (`laliga`) y Premier (`premier` → path `/premier-league/`).
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin

from .http_util import get_soup
from .teams import display_team_from_slug, team_slug

log = logging.getLogger("scrapers.ff")

BASE = "https://www.futbolfantasy.com"
MAX_TEAM_PAGES = 12

# competition key → path segment en futbolfantasy.com
FF_PATH: dict[str, str] = {
    "laliga": "laliga",
    "premier": "premier-league",
}


def _ff_path(competition: str) -> str:
    key = (competition or "laliga").strip().lower()
    return FF_PATH.get(key, FF_PATH["laliga"])


def _abs(url: str | None) -> str | None:
    if not url:
        return None
    return urljoin(BASE, url)


def _parse_lesionados(path: str) -> list[dict[str, Any]]:
    soup = get_soup(f"{BASE}/{path}/lesionados")
    if not soup:
        return []
    out: list[dict[str, Any]] = []
    for a in soup.select("a.jugador"):
        name = a.get_text(" ", strip=True)
        if not name:
            continue
        href = _abs(a.get("href"))
        row = a.find_parent(class_="row") or a.parent
        row_txt = row.get_text(" ", strip=True) if row else ""
        team = None
        m = re.match(r"^(.+?)\s+\d{1,3}%\s+", row_txt)
        if m:
            team = m.group(1).strip()
        pcts = [int(x) for x in re.findall(r"(\d{1,3})\s*%", row_txt)]
        lineup_prob = pcts[0] if pcts else 0
        out.append({
            "name": name,
            "team": team,
            "availability": "injured",
            "lineup_prob": lineup_prob,
            "is_chollo": False,
            "is_recommendation": False,
            "profile_url": href,
            "source": "futbolfantasy",
        })
    log.info("FF [%s] lesionados: %d", path, len(out))
    return out


def _parse_sancionados(path: str) -> list[dict[str, Any]]:
    soup = get_soup(f"{BASE}/{path}/sancionados")
    if not soup:
        return []
    out: list[dict[str, Any]] = []
    for a in soup.select("a.jugador"):
        name = a.get_text(" ", strip=True)
        if not name:
            continue
        out.append({
            "name": name,
            "team": None,
            "availability": "suspended",
            "lineup_prob": 0,
            "is_chollo": False,
            "is_recommendation": False,
            "profile_url": _abs(a.get("href")),
            "source": "futbolfantasy",
        })
    log.info("FF [%s] sancionados: %d", path, len(out))
    return out


def _parse_team_page(path: str, slug: str) -> list[dict[str, Any]]:
    url = f"{BASE}/{path}/equipos/{slug}"
    soup = get_soup(url)
    if not soup:
        return []
    team_name = display_team_from_slug(slug)
    out: list[dict[str, Any]] = []
    for el in soup.select(".elemento_jugador.filters_ok, .elemento_jugador.clickable"):
        img = el.select_one("img[alt]")
        name = (img.get("alt") or "").strip() if img else ""
        if not name or name.lower() == "jugador":
            link = el.select_one("a[href*='/jugadores/']")
            if link:
                name = link.get_text(" ", strip=True)
        if not name or len(name) < 2:
            continue
        prob_el = el.select_one(".probabilidad-widget")
        prob_txt = prob_el.get_text(" ", strip=True) if prob_el else ""
        m = re.search(r"(\d{1,3})\s*%", prob_txt)
        if not m:
            m = re.search(r"(\d{1,3})\s*%", el.get_text(" ", strip=True))
        lineup_prob = int(m.group(1)) if m else None
        link = el.select_one("a[href*='/jugadores/']")
        is_reco = lineup_prob is not None and lineup_prob >= 80
        out.append({
            "name": name,
            "team": team_name,
            "availability": "available",
            "lineup_prob": lineup_prob,
            "is_chollo": False,
            "is_recommendation": is_reco,
            "profile_url": _abs(link.get("href")) if link else url,
            "source": "futbolfantasy",
        })
    return out


def fetch_futbolfantasy(
    team_names: list[str] | None = None,
    *,
    competition: str = "laliga",
    priority_teams: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Devuelve registros de jugadores FF.
    priority_teams: equipos de plantilla propia (sin tope; siempre se scrapean).
    team_names: resto (mercado/universo); se añaden hasta MAX_TEAM_PAGES.
    competition: `laliga` | `premier`.
    """
    path = _ff_path(competition)
    try:
        by_key: dict[str, dict[str, Any]] = {}

        def upsert(rec: dict[str, Any]) -> None:
            key = (rec.get("name") or "").strip().lower()
            if not key:
                return
            prev = by_key.get(key)
            if not prev:
                by_key[key] = rec
                return
            prio = {"suspended": 3, "injured": 2, "doubt": 1, "available": 0, "unknown": 0}
            if prio.get(rec.get("availability"), 0) > prio.get(prev.get("availability"), 0):
                prev["availability"] = rec["availability"]
            if rec.get("lineup_prob") is not None:
                if prev.get("lineup_prob") is None or rec["lineup_prob"] < prev.get("lineup_prob", 999):
                    if rec.get("availability") in ("injured", "suspended"):
                        prev["lineup_prob"] = rec["lineup_prob"]
                    elif prev.get("availability") not in ("injured", "suspended"):
                        prev["lineup_prob"] = max(prev.get("lineup_prob") or 0, rec["lineup_prob"])
            if rec.get("profile_url") and not prev.get("profile_url"):
                prev["profile_url"] = rec["profile_url"]
            if rec.get("team") and not prev.get("team"):
                prev["team"] = rec["team"]
            if rec.get("is_recommendation"):
                prev["is_recommendation"] = True
            if rec.get("is_chollo"):
                prev["is_chollo"] = True

        for rec in _parse_lesionados(path):
            upsert(rec)
        for rec in _parse_sancionados(path):
            upsert(rec)

        def _collect_slugs(names: list[str] | None) -> list[str]:
            out: list[str] = []
            seen_local: set[str] = set()
            for t in names or []:
                slug = team_slug(t)
                if slug and slug not in seen_local:
                    seen_local.add(slug)
                    out.append(slug)
            return out

        priority_slugs = _collect_slugs(priority_teams)
        other_slugs = [
            s for s in _collect_slugs(team_names) if s not in set(priority_slugs)
        ][:MAX_TEAM_PAGES]
        slugs = priority_slugs + other_slugs

        for slug in slugs:
            try:
                for rec in _parse_team_page(path, slug):
                    upsert(rec)
            except Exception as exc:  # noqa: BLE001
                log.warning("FF team %s/%s falló: %s", path, slug, exc)

        result = list(by_key.values())
        log.info(
            "FF [%s] total registros: %d (priority_teams=%d + market_cap=%d)",
            path,
            len(result),
            len(priority_slugs),
            len(other_slugs),
        )
        return result
    except Exception as exc:  # noqa: BLE001
        log.warning("FF scraper [%s] falló: %s", path, exc)
        return []
