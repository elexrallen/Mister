"""
Scraper Predicted11 — % titular agregada de expertos (Premier / Serie A).

LaLiga sigue en Fútbol Fantasy. Aquí se leen los onces publicados de los top
expertos de la jornada y se convierte en lineup_prob = (# de onces / N) * 100.
Si no hay noticias de la jornada, se usa el once consenso de cada página de equipo.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any
from urllib.parse import urljoin

from .http_util import get_soup

log = logging.getLogger("scrapers.predicted11")

BASE = "https://www.predicted11.com"
PRED11_SUPPORTED = frozenset({"premier", "seriea"})
PRED11_SLUG: dict[str, str] = {
    "premier": "premier-league",
    "seriea": "serie-a",
}
TOP_EXPERTS = 5
CONSENSUS_STARTER_PROB = 85
MAX_TEAM_PAGES = 20


def _slug(competition: str) -> str | None:
    key = (competition or "").strip().lower()
    return PRED11_SLUG.get(key)


def _abs(url: str | None) -> str | None:
    if not url:
        return None
    return urljoin(BASE, url)


def _hub_url(slug: str) -> str:
    return f"{BASE}/es/{slug}"


def _parse_jornada_from_hub(soup: Any) -> int | None:
    text = soup.get_text(" ", strip=True)
    m = re.search(r"Jornada\s+(\d+)", text, flags=re.I)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _rank_experts(soup: Any, slug: str) -> list[str]:
    """Usuarios del ranking del hub, en orden (primera aparición)."""
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.select(f'a[href*="/es/usuario/"]'):
        href = a.get("href") or ""
        m = re.search(r"/es/usuario/([^/?#]+)", href)
        if not m:
            continue
        user = m.group(1).strip()
        if not user or user.lower() in seen:
            continue
        # Preferir enlaces del campeonato actual
        if f"campeonato={slug}" not in href and f"temporada={slug}" not in href:
            # Aun así aceptar el orden visual del hub
            pass
        seen.add(user.lower())
        out.append(user)
        if len(out) >= 15:
            break
    return out


def _news_links_for_jornada(soup: Any, slug: str, jornada: int | None) -> list[tuple[str, str]]:
    """[(user, url)] noticias de alineaciones de la jornada (o las más recientes)."""
    rows: list[tuple[str, str, int]] = []
    for a in soup.select(f'a[href*="/es/{slug}/noticias/alineaciones-probables-"]'):
        href = _abs(a.get("href"))
        if not href:
            continue
        m = re.search(
            rf"/noticias/alineaciones-probables-de-([^/]+)-jornada-(\d+)-de-{re.escape(slug)}",
            href,
            flags=re.I,
        )
        if not m:
            continue
        user = m.group(1)
        try:
            j = int(m.group(2))
        except ValueError:
            continue
        rows.append((user, href, j))
    if jornada is not None:
        cur = [(u, url) for u, url, j in rows if j == jornada]
        if cur:
            return cur
    # Fallback: jornada más alta disponible
    if not rows:
        return []
    max_j = max(j for _u, _url, j in rows)
    return [(u, url) for u, url, j in rows if j == max_j]


def _pick_expert_urls(
    ranked: list[str],
    news: list[tuple[str, str]],
    *,
    limit: int = TOP_EXPERTS,
) -> list[str]:
    by_user = {u.lower(): url for u, url in news}
    picked: list[str] = []
    for user in ranked:
        url = by_user.get(user.lower())
        if url and url not in picked:
            picked.append(url)
        if len(picked) >= limit:
            return picked
    for _u, url in news:
        if url not in picked:
            picked.append(url)
        if len(picked) >= limit:
            break
    return picked[:limit]


def _player_name_from_node(node: Any) -> str:
    img = node.select_one("img[alt]")
    if img and (img.get("alt") or "").strip():
        return str(img.get("alt")).strip()
    name_el = node.select_one(".player-on-field-name")
    if name_el:
        return name_el.get_text(" ", strip=True)
    return node.get_text(" ", strip=True)


def _parse_expert_xis(url: str) -> dict[str, list[str]]:
    soup = get_soup(url)
    if not soup:
        return {}
    out: dict[str, list[str]] = {}
    for h in soup.find_all(["h2", "h3", "h4"]):
        text = h.get_text(" ", strip=True)
        m = re.search(
            r"Alineaci[oó]n probable del\s+(.+?)\s+seg[uú]n",
            text,
            flags=re.I,
        )
        if not m:
            continue
        team = m.group(1).strip()
        campo = h.find_next(class_="campo-alineacion")
        if not campo:
            continue
        names: list[str] = []
        for p in campo.select(".player-on-field"):
            name = _player_name_from_node(p)
            if name and name not in names:
                names.append(name)
        if names:
            out[team] = names
    return out


def _team_links(soup: Any, slug: str) -> list[tuple[str, str]]:
    """[(team_slug, url)]"""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in soup.select(f'a[href*="/es/{slug}/equipo/"]'):
        href = _abs(a.get("href"))
        if not href:
            continue
        m = re.search(rf"/es/{re.escape(slug)}/equipo/([^/?#]+)", href)
        if not m:
            continue
        team_slug = m.group(1).strip().lower()
        if not team_slug or team_slug in seen:
            continue
        seen.add(team_slug)
        out.append((team_slug, href))
    return out[:MAX_TEAM_PAGES]


def _parse_team_consensus(url: str, team_label: str) -> list[str]:
    soup = get_soup(url)
    if not soup:
        return []
    names: list[str] = []
    for p in soup.select(".player-on-field"):
        name = _player_name_from_node(p)
        if name and name not in names:
            names.append(name)
    # Título de página a menudo "Venezia - Alineaciones..."
    if not team_label and soup.title and soup.title.string:
        team_label = str(soup.title.string).split("-")[0].strip()
    return names


def _records_from_counts(
    counts: dict[tuple[str, str], int],
    *,
    n_experts: int,
) -> list[dict[str, Any]]:
    if n_experts <= 0:
        return []
    out: list[dict[str, Any]] = []
    for (name, team), n in counts.items():
        pct = int(round(100.0 * n / float(n_experts)))
        pct = max(0, min(100, pct))
        out.append(
            {
                "name": name,
                "team": team or None,
                "availability": "available" if pct >= 40 else "unknown",
                "lineup_prob": pct,
                "is_chollo": False,
                "is_recommendation": pct >= 70,
                "profile_url": None,
                "source": "predicted11",
            }
        )
    return out


def _aggregate_expert_urls(urls: list[str]) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    used = 0
    for url in urls:
        try:
            xis = _parse_expert_xis(url)
        except Exception as exc:  # noqa: BLE001
            log.warning("Predicted11 noticia falló %s: %s", url, exc)
            continue
        if not xis:
            continue
        used += 1
        for team, names in xis.items():
            for name in names:
                counts[(name, team)] += 1
    return _records_from_counts(counts, n_experts=used)


def _aggregate_team_pages(team_links: list[tuple[str, str]]) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str], int] = {}
    for team_slug, url in team_links:
        try:
            names = _parse_team_consensus(url, team_slug)
        except Exception as exc:  # noqa: BLE001
            log.warning("Predicted11 equipo falló %s: %s", url, exc)
            continue
        # slug → etiqueta legible
        team = team_slug.replace("-", " ").title()
        if team.lower() in ("napoles", "nápoles"):
            team = "Napoli"
        for name in names:
            counts[(name, team)] = 1
    # Consenso de página = un "experto" sintético a STARTER_PROB fijo
    out: list[dict[str, Any]] = []
    for (name, team), _n in counts.items():
        out.append(
            {
                "name": name,
                "team": team,
                "availability": "available",
                "lineup_prob": CONSENSUS_STARTER_PROB,
                "is_chollo": False,
                "is_recommendation": True,
                "profile_url": None,
                "source": "predicted11",
            }
        )
    return out


def fetch_predicted11(*, competition: str) -> list[dict[str, Any]]:
    """
    Devuelve registros estilo FF/JP con lineup_prob 0–100 y source=predicted11.
    Solo premier / seriea; otras competiciones → [].
    """
    slug = _slug(competition)
    if not slug:
        return []
    try:
        hub = get_soup(_hub_url(slug))
        if not hub:
            log.warning("Predicted11 hub vacío [%s]", slug)
            return []
        jornada = _parse_jornada_from_hub(hub)
        ranked = _rank_experts(hub, slug)
        news = _news_links_for_jornada(hub, slug, jornada)
        expert_urls = _pick_expert_urls(ranked, news, limit=TOP_EXPERTS)
        records: list[dict[str, Any]] = []
        if expert_urls:
            records = _aggregate_expert_urls(expert_urls)
            log.info(
                "Predicted11 [%s] expertos=%d registros=%d jornada=%s",
                slug,
                len(expert_urls),
                len(records),
                jornada,
            )
        if not records:
            team_links = _team_links(hub, slug)
            records = _aggregate_team_pages(team_links)
            log.info(
                "Predicted11 [%s] fallback equipos=%d registros=%d",
                slug,
                len(team_links),
                len(records),
            )
        # Deduplicar por nombre+equipo quedándose con mayor %
        by_key: dict[str, dict[str, Any]] = {}
        for rec in records:
            key = f"{(rec.get('name') or '').strip().lower()}|{(rec.get('team') or '').strip().lower()}"
            if not key.strip("|"):
                continue
            prev = by_key.get(key)
            if not prev or int(rec.get("lineup_prob") or 0) > int(prev.get("lineup_prob") or 0):
                by_key[key] = rec
        return list(by_key.values())
    except Exception as exc:  # noqa: BLE001
        log.warning("Predicted11 scraper [%s] falló: %s", slug, exc)
        return []


__all__ = [
    "PRED11_SUPPORTED",
    "PRED11_SLUG",
    "TOP_EXPERTS",
    "fetch_predicted11",
]
