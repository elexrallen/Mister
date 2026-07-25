"""
Scraper Comuniate — notas / rachas fantasy-friendly (best-effort).
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin

from .http_util import get_soup

log = logging.getLogger("scrapers.comuniate")

BASE = "https://www.comuniate.com"
HOME = f"{BASE}/"
MARKET = f"{BASE}/mercado/fantasy"
MEJORES = f"{BASE}/mejores/jugadores"


def _abs(url: str | None) -> str | None:
    if not url:
        return None
    return urljoin(BASE, url)


def _parse_player_links(soup) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in soup.select("a[href*='/jugadores/']"):
        href = a.get("href") or ""
        if not re.search(r"/jugadores/\d+/", href):
            continue
        abs_url = _abs(href)
        if not abs_url or abs_url in seen:
            continue
        # Nombre: texto del enlace o slug
        raw = a.get_text(" ", strip=True)
        # A menudo "Pedri16.630.000€+1.410.000€0" → cortar en dígitos
        name = re.split(r"\d", raw, maxsplit=1)[0].strip() if raw else ""
        if not name or len(name) < 2:
            slug = abs_url.rstrip("/").split("/")[-1]
            name = slug.replace("-", " ").title()
        if len(name) < 2:
            continue
        seen.add(abs_url)
        parent_txt = ""
        parent = a.find_parent(["div", "li", "tr", "article"])
        if parent:
            parent_txt = parent.get_text(" ", strip=True).lower()
        is_reco = any(x in parent_txt for x in ("recomend", "clave", "chollo", " explota"))
        is_chollo = "chollo" in parent_txt or "subida" in parent_txt
        # Media fantasy si aparece un decimal razonable cerca
        sofascore_avg = None
        streak = "unknown"
        for m in re.finditer(r"\b(\d\.\d{1,2})\b", parent.get_text(" ", strip=True) if parent else ""):
            val = float(m.group(1))
            if 5.0 <= val <= 10.0:
                sofascore_avg = val
                break
        if "racha" in parent_txt and any(x in parent_txt for x in ("↑", "sube", "positiva")):
            streak = "up"
        elif "racha" in parent_txt and any(x in parent_txt for x in ("↓", "baja", "negativa")):
            streak = "down"
        out.append({
            "name": name,
            "team": None,
            "availability": "unknown",
            "lineup_prob": None,
            "is_chollo": is_chollo,
            "is_recommendation": is_reco,
            "profile_url": abs_url,
            "sofascore_avg_5": sofascore_avg,
            "points_streak": streak,
            "source": "comuniate",
        })
    return out


def _enrich_from_profile(url: str) -> dict[str, Any]:
    """Extrae media SofaScore / puntos de la ficha si está en HTML estático."""
    soup = get_soup(url)
    if not soup:
        return {}
    meta: dict[str, Any] = {}
    # Tabla histórico: buscar fila SofaScore con media
    text = soup.get_text("\n", strip=True)
    for line in text.split("\n"):
        if re.search(r"sofascore", line, re.I):
            nums = re.findall(r"\b(\d+\.\d+)\b", line)
            for n in nums:
                val = float(n)
                if 5.0 <= val <= 10.0:
                    meta["sofascore_avg_5"] = val
                    break
        if line.strip().upper() == "RACHA":
            meta.setdefault("points_streak", "flat")
    # Hidden sofascore id (útil para sofascore scraper)
    hid = soup.select_one("#id_sofascore, input[name=id_sofascore]")
    if hid and hid.get("value"):
        meta["sofascore_id"] = hid.get("value")
    # Media fantasy de temporada actual cerca de "Media"
    box = None
    for span in soup.select("span.detallejugador"):
        if "media" in span.get_text(strip=True).lower():
            box = span.find_parent(["tr", "div", "table"])
            break
    if box and "sofascore_avg_5" not in meta:
        nums = re.findall(r"\b(\d+\.\d+)\b", box.get_text(" ", strip=True))
        for n in nums:
            val = float(n)
            # Fantasy media puede ser >10; Sofascore típico 5-9.5
            if 5.0 <= val <= 9.8:
                meta["sofascore_avg_5"] = val
                break
    return meta


def enrich_profiles_for_names(
    names: list[str],
    catalog: list[dict[str, Any]] | None = None,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Para nombres Mister/mercado, localiza ficha Comuniate (por catálogo o slug)
    y extrae sofascore_id + media SofaScore de la ficha.
    """
    from .name_match import match_player, normalize_name

    catalog = catalog or fetch_comuniate(profile_limit=0)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_name in names:
        if len(out) >= limit:
            break
        key = normalize_name(raw_name)
        if not key or key in seen:
            continue
        hit, score = match_player(raw_name, None, catalog, threshold=80)
        url = hit.get("profile_url") if hit else None
        if not url:
            # slug heurístico
            slug = key.replace(" ", "-")
            # sin id numérico no podemos adivinar URL Comuniate
            continue
        seen.add(key)
        try:
            extra = _enrich_from_profile(url)
            rec = {
                "name": hit.get("name") or raw_name,
                "team": hit.get("team"),
                "availability": "unknown",
                "lineup_prob": None,
                "is_chollo": bool(hit.get("is_chollo")),
                "is_recommendation": bool(hit.get("is_recommendation")),
                "profile_url": url,
                "sofascore_avg_5": extra.get("sofascore_avg_5") or hit.get("sofascore_avg_5"),
                "points_streak": extra.get("points_streak") or hit.get("points_streak") or "unknown",
                "sofascore_id": extra.get("sofascore_id"),
                "source": "comuniate_sofa" if extra.get("sofascore_avg_5") else "comuniate",
                "match_score": score,
            }
            if rec.get("sofascore_id") or rec.get("sofascore_avg_5") is not None:
                out.append(rec)
        except Exception as exc:  # noqa: BLE001
            log.warning("enrich ficha %s: %s", url, exc)
    log.info("Comuniate fichas enriquecidas: %d", len(out))
    return out


def fetch_comuniate(profile_limit: int = 0) -> list[dict[str, Any]]:
    """
    Lista agregada desde home/mercado/mejores.
    profile_limit: si >0, enriquece las primeras N fichas (rate-limit).
    """
    try:
        by_url: dict[str, dict[str, Any]] = {}
        for url in (HOME, MARKET, MEJORES):
            soup = get_soup(url)
            if not soup:
                continue
            for rec in _parse_player_links(soup):
                u = rec.get("profile_url")
                if not u:
                    continue
                prev = by_url.get(u)
                if not prev:
                    by_url[u] = rec
                else:
                    if rec.get("is_chollo"):
                        prev["is_chollo"] = True
                    if rec.get("is_recommendation"):
                        prev["is_recommendation"] = True
                    if rec.get("sofascore_avg_5") and not prev.get("sofascore_avg_5"):
                        prev["sofascore_avg_5"] = rec["sofascore_avg_5"]

        results = list(by_url.values())
        if profile_limit > 0:
            for rec in results[:profile_limit]:
                try:
                    extra = _enrich_from_profile(rec["profile_url"])
                    rec.update({k: v for k, v in extra.items() if v is not None})
                    if extra.get("sofascore_avg_5") is not None:
                        rec["source"] = "comuniate_sofa"
                except Exception as exc:  # noqa: BLE001
                    log.warning("Comuniate ficha %s: %s", rec.get("profile_url"), exc)

        log.info("Comuniate registros: %d", len(results))
        return results
    except Exception as exc:  # noqa: BLE001
        log.warning("Comuniate scraper falló: %s", exc)
        return []
