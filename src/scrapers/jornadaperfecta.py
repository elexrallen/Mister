"""
Scraper Jornada Perfecta — lesionados/dudas + titulares probables por partido.
Soporta LaLiga (raíz) y Premier (`/premier/...`).
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin

from .http_util import get_soup

log = logging.getLogger("scrapers.jp")

BASE = "https://www.jornadaperfecta.com"
MAX_MATCHES = 8
STARTER_PROB = 85

# competition → prefijo de ruta ("" = LaLiga en raíz del sitio)
JP_PREFIX: dict[str, str] = {
    "laliga": "",
    "premier": "premier",
}


def _jp_prefix(competition: str) -> str:
    key = (competition or "laliga").strip().lower()
    return JP_PREFIX.get(key, "")


def _jp_url(prefix: str, path: str) -> str:
    path = path.lstrip("/")
    if prefix:
        return f"{BASE}/{prefix}/{path}"
    return f"{BASE}/{path}"


def _abs(url: str | None) -> str | None:
    if not url:
        return None
    return urljoin(BASE, url)


def _status_from_alt(alt: str | None) -> str:
    a = (alt or "").lower()
    if "lesion" in a:
        return "injured"
    if "sancion" in a or "tarjeta" in a or "cumple" in a:
        return "suspended"
    if "duda" in a:
        return "doubt"
    if "disponible" in a or "observ" in a:
        return "doubt"
    return "unknown"


def _parse_lesionados(prefix: str) -> list[dict[str, Any]]:
    soup = get_soup(_jp_url(prefix, "lesionados/"))
    if not soup:
        return []
    out: list[dict[str, Any]] = []
    for j in soup.select(".lesionados-jugador"):
        if j.select_one(".lesionados-jugador-sanos"):
            continue
        name_a = j.select_one(".lesionados-jugador-nombre a")
        if not name_a:
            continue
        name = name_a.get_text(" ", strip=True)
        if not name:
            continue
        icon = j.select_one(".lesionados-jugador-iconos img")
        alt = icon.get("alt") if icon else None
        availability = _status_from_alt(alt)
        team = None
        for prev in j.find_all_previous(["div", "span"], limit=40):
            cls = " ".join(prev.get("class") or [])
            if "lesionados-equipo-nombre" in cls:
                team = prev.get_text(" ", strip=True)
                break
        # Duda: no inventar % (antes 40 fijo pisaba el real de Fútbol Fantasy vía match por apellido).
        lineup_prob = 0 if availability in ("injured", "suspended") else None
        out.append({
            "name": name,
            "team": team,
            "availability": availability,
            "lineup_prob": lineup_prob,
            "is_chollo": False,
            "is_recommendation": False,
            "profile_url": _abs(name_a.get("href")),
            "source": "jornadaperfecta",
        })
    log.info("JP [%s] lesionados/dudas: %d", prefix or "laliga", len(out))
    return out


def _match_links(prefix: str) -> list[str]:
    soup = get_soup(_jp_url(prefix, "onces-posibles/"))
    if not soup:
        return []
    links: list[str] = []
    seen: set[str] = set()
    for a in soup.select("a[href*='/partido/']"):
        href = _abs(a.get("href"))
        if not href or href in seen:
            continue
        if "/partido/" not in href:
            continue
        # Aislar competición: Premier solo /premier/partido/; LaLiga excluye /premier/
        if prefix == "premier":
            if "/premier/partido/" not in href:
                continue
        elif "/premier/" in href:
            continue
        seen.add(href)
        links.append(href)
    return links[:MAX_MATCHES]


def _parse_match(url: str) -> list[dict[str, Any]]:
    soup = get_soup(url)
    if not soup:
        return []
    campos = soup.select(".campo-futbol")
    title = (soup.title.string if soup.title else "") or ""
    title_teams = re.split(r"\s+[-–]\s+", title.split("|")[0].strip())
    out: list[dict[str, Any]] = []
    # Selectores: LaLiga usa .player-name-pintar; Premier a veces vacío (JS / pretemporada)
    name_sels = ".player-name-pintar, .player-name, .nombre-jugador"
    for i, campo in enumerate(campos[:2]):
        team = title_teams[i].strip() if i < len(title_teams) else None
        if team and "|" in team:
            team = team.split("|")[0].strip()
        if team and "alineacion" in team.lower():
            team = None
        for n in campo.select(name_sels):
            name = n.get_text(" ", strip=True)
            if not name:
                continue
            out.append({
                "name": name,
                "team": team,
                "availability": "available",
                "lineup_prob": STARTER_PROB,
                "is_chollo": False,
                "is_recommendation": True,
                "profile_url": url,
                "source": "jornadaperfecta",
            })
    return out


def fetch_jornadaperfecta(*, competition: str = "laliga") -> list[dict[str, Any]]:
    prefix = _jp_prefix(competition)
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
                    prev["lineup_prob"] = rec["lineup_prob"]
            elif rec.get("lineup_prob") is not None and prev.get("availability") == "available":
                prev["lineup_prob"] = max(prev.get("lineup_prob") or 0, rec["lineup_prob"])
            if rec.get("team") and not prev.get("team"):
                prev["team"] = rec["team"]
            if rec.get("profile_url") and (
                not prev.get("profile_url") or "/partido/" in str(prev.get("profile_url"))
            ):
                if "/jugador/" in str(rec.get("profile_url")):
                    prev["profile_url"] = rec["profile_url"]
            if rec.get("is_recommendation"):
                prev["is_recommendation"] = True

        for rec in _parse_lesionados(prefix):
            upsert(rec)
        for link in _match_links(prefix):
            try:
                for rec in _parse_match(link):
                    upsert(rec)
            except Exception as exc:  # noqa: BLE001
                log.warning("JP partido %s falló: %s", link, exc)

        result = list(by_key.values())
        log.info("JP [%s] total registros: %d", prefix or "laliga", len(result))
        return result
    except Exception as exc:  # noqa: BLE001
        log.warning("JP scraper [%s] falló: %s", prefix or "laliga", exc)
        return []
