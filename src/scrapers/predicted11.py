"""
Scraper Predicted11 — % titular agregada de expertos (Premier / Serie A).

LaLiga sigue en Fútbol Fantasy. Aquí manda la API de popularidad de la jornada
(`/api/p11/popularidad/{temporada}/{jornada}/{equipo}`): el % de pronósticos
que incluyen a cada jugador. Las noticias de alineaciones son respaldo, y
solo de la jornada actual: el slug cambió a `para-la-jornada-N-de-la-{liga}`
y el regex viejo acababa leyendo onces de jornadas anteriores.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any
from urllib.parse import urljoin

from .http_util import get_json, get_soup

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

# Viejo:  .../alineaciones-probables-de-{user}-jornada-{n}-de-{slug}
# Nuevo:  .../alineaciones-probables-de-{user}-para-la-jornada-{n}-de-la-{slug}
_NEWS_HREF_RE = (
    r"/noticias/alineaciones-probables-de-([^/]+?)"
    r"(?:-para-la)?-jornada-(\d+)-de-(?:la-)?{slug}"
)


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


def _parse_news_href(href: str | None, slug: str) -> tuple[str, int] | None:
    """(user, jornada) o None. Acepta el slug nuevo y el antiguo."""
    if not href or not slug:
        return None
    pat = _NEWS_HREF_RE.format(slug=re.escape(slug))
    m = re.search(pat, href, flags=re.I)
    if not m:
        return None
    user = (m.group(1) or "").strip()
    if not user:
        return None
    try:
        jornada = int(m.group(2))
    except (TypeError, ValueError):
        return None
    return user, jornada


def _rank_experts(soup: Any, slug: str) -> list[str]:
    """Usuarios del ranking del hub, en orden (primera aparición)."""
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.select('a[href*="/es/usuario/"]'):
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
    """[(user, url)] noticias de alineaciones de la jornada actual."""
    rows: list[tuple[str, str, int]] = []
    for a in soup.select(f'a[href*="/es/{slug}/noticias/alineaciones-probables-"]'):
        href = _abs(a.get("href"))
        parsed = _parse_news_href(href, slug)
        if not href or not parsed:
            continue
        user, j = parsed
        rows.append((user, href, j))
    if jornada is not None:
        # Nunca reciclar onces de una jornada anterior: titularidades viejas
        # pintan titulares fantasma y bajan a banquillo a quien sí juega.
        return [(u, url) for u, url, j in rows if j == jornada]
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
    return [
        (str(row["slug"]), str(row["url"]))
        for row in _team_entries(soup, slug)
    ]


def _team_entries(soup: Any, slug: str) -> list[dict[str, Any]]:
    """Equipos del hub: slug, url, nombre y data-equipo-id (API popularidad)."""
    out: list[dict[str, Any]] = []
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
        raw_id = a.get("data-equipo-id")
        equipo_id: int | None = None
        try:
            if raw_id not in (None, "", "null"):
                equipo_id = int(str(raw_id).strip())
        except (TypeError, ValueError):
            equipo_id = None
        name = (a.get("title") or a.get_text(" ", strip=True) or "").strip()
        if not name:
            name = team_slug.replace("-", " ").title()
        out.append(
            {
                "slug": team_slug,
                "url": href,
                "name": name,
                "equipo_id": equipo_id,
            }
        )
        if len(out) >= MAX_TEAM_PAGES:
            break
    return out


def _parse_team_page_meta(url: str) -> dict[str, int]:
    soup = get_soup(url)
    if not soup:
        return {}
    html = str(soup)
    out: dict[str, int] = {}
    for key in ("currentTemporada", "jornada", "currentJuegoId", "currentEquipoId"):
        m = re.search(rf"window\.{key}\s*=\s*(\d+)", html)
        if not m:
            continue
        try:
            out[key] = int(m.group(1))
        except ValueError:
            continue
    return out


def _parse_team_consensus(url: str, team_label: str) -> list[str]:
    soup = get_soup(url)
    if not soup:
        return []
    names: list[str] = []
    for p in soup.select(".player-on-field"):
        name = _player_name_from_node(p)
        if name and name not in names:
            names.append(name)
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


def _record(
    name: str,
    team: str | None,
    pct: int,
) -> dict[str, Any]:
    pct = max(0, min(100, int(pct)))
    return {
        "name": name,
        "team": team or None,
        "availability": "available" if pct >= 40 else "unknown",
        "lineup_prob": pct,
        "is_chollo": False,
        "is_recommendation": pct >= 70,
        "profile_url": None,
        "source": "predicted11",
    }


def _records_from_popularidad(
    payload: dict[str, Any] | None,
    team: str,
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not payload.get("success"):
        return []
    out: list[dict[str, Any]] = []
    for row in payload.get("jugadores") or []:
        if not isinstance(row, dict):
            continue
        name = (row.get("nombre") or row.get("nombre_corto") or "").strip()
        if not name:
            continue
        pct = row.get("porcentaje")
        if pct is None:
            pct = row.get("porcentaje_decimal")
        try:
            pct_i = int(round(float(pct)))
        except (TypeError, ValueError):
            continue
        out.append(_record(name, team, pct_i))
    return out


def _aggregate_popularidad(
    teams: list[dict[str, Any]],
    *,
    jornada: int | None,
) -> list[dict[str, Any]]:
    with_id = [t for t in teams if t.get("equipo_id")]
    if not with_id:
        return []
    meta = _parse_team_page_meta(str(with_id[0]["url"]))
    temporada = meta.get("currentTemporada")
    jornada_api = meta.get("jornada") or jornada
    if not temporada or not jornada_api:
        log.warning("Predicted11 popularidad: sin temporada/jornada en página de equipo")
        return []
    records: list[dict[str, Any]] = []
    ok_teams = 0
    for team in with_id:
        eid = team["equipo_id"]
        url = f"{BASE}/api/p11/popularidad/{temporada}/{jornada_api}/{eid}"
        try:
            payload = get_json(url)
        except Exception as exc:  # noqa: BLE001
            log.warning("Predicted11 popularidad falló %s: %s", url, exc)
            continue
        rows = _records_from_popularidad(payload if isinstance(payload, dict) else None, str(team["name"]))
        if not rows:
            continue
        ok_teams += 1
        records.extend(rows)
    if records:
        log.info(
            "Predicted11 popularidad temporada=%s jornada=%s equipos=%d registros=%d",
            temporada,
            jornada_api,
            ok_teams,
            len(records),
        )
    return records


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
        out.append(_record(name, team, CONSENSUS_STARTER_PROB))
    return out


def _dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for rec in records:
        key = f"{(rec.get('name') or '').strip().lower()}|{(rec.get('team') or '').strip().lower()}"
        if not key.strip("|"):
            continue
        prev = by_key.get(key)
        if not prev or int(rec.get("lineup_prob") or 0) > int(prev.get("lineup_prob") or 0):
            by_key[key] = rec
    return list(by_key.values())


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
        teams = _team_entries(hub, slug)
        records = _aggregate_popularidad(teams, jornada=jornada)
        if not records:
            ranked = _rank_experts(hub, slug)
            news = _news_links_for_jornada(hub, slug, jornada)
            expert_urls = _pick_expert_urls(ranked, news, limit=TOP_EXPERTS)
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
            team_links = [(str(t["slug"]), str(t["url"])) for t in teams] or _team_links(hub, slug)
            records = _aggregate_team_pages(team_links)
            log.info(
                "Predicted11 [%s] fallback equipos=%d registros=%d",
                slug,
                len(team_links),
                len(records),
            )
        return _dedupe_records(records)
    except Exception as exc:  # noqa: BLE001
        log.warning("Predicted11 scraper [%s] falló: %s", slug, exc)
        return []


__all__ = [
    "PRED11_SUPPORTED",
    "PRED11_SLUG",
    "TOP_EXPERTS",
    "fetch_predicted11",
    "_parse_news_href",
    "_news_links_for_jornada",
    "_pick_expert_urls",
    "_records_from_counts",
    "_records_from_popularidad",
]
