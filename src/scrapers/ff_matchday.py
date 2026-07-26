"""
Fútbol Fantasy — posibles alineaciones por jornada.

Hub:  /{laliga|premier-league}/posibles-alineaciones
Previas: /partidos/{id}-{slug}

Fail-soft + caché disco (TTL 8h).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, unquote

from .http_util import get_soup
from .futbolfantasy import _ff_path, BASE

log = logging.getLogger("scrapers.ff_matchday")

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_TTL_HOURS = 8
MAX_FIXTURES = 12

MONTHS_ES = {
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _abs(url: str | None) -> str | None:
    if not url:
        return None
    return urljoin(BASE + "/", url)


def _cache_path(competition: str) -> Path:
    comp = (competition or "laliga").strip().lower()
    return CACHE_DIR / f"ff_matchday_{comp}.json"


def _load_cache(competition: str) -> dict[str, Any] | None:
    path = _cache_path(competition)
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        ts = data.get("fetched_at")
        if not ts:
            return None
        fetched = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if _now() - fetched > timedelta(hours=CACHE_TTL_HOURS):
            return None
        if data.get("competition") and data.get("competition") != (competition or "laliga"):
            return None
        return data
    except Exception as exc:  # noqa: BLE001
        log.warning("FF matchday cache ilegible (%s): %s", competition, exc)
        return None


def _save_cache(competition: str, payload: dict[str, Any]) -> None:
    path = _cache_path(competition)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.warning("FF matchday cache no escrita (%s): %s", competition, exc)


def _slug_to_name(slug: str) -> str:
    s = unquote(slug or "").strip("-")
    s = re.sub(r"-\d+$", "", s)
    parts = [p for p in s.split("-") if p]
    if not parts:
        return slug
    return " ".join(p[:1].upper() + p[1:] for p in parts)


def _name_from_player_url(href: str | None) -> str | None:
    if not href:
        return None
    m = re.search(r"/jugadores/([^/?#]+)", href)
    if not m:
        return None
    return _slug_to_name(m.group(1))


def _parse_jornada(soup) -> int | None:
    text = soup.get_text(" ", strip=True)
    m = re.search(r"Jornada\s*(\d{1,2})", text, re.I)
    if m:
        return int(m.group(1))
    # selector activo tipo J1
    for a in soup.select("a, button, span, li"):
        t = (a.get_text(" ", strip=True) or "").strip()
        if re.fullmatch(r"J\d{1,2}", t, re.I):
            classes = " ".join(a.get("class") or []).lower()
            if "active" in classes or "selected" in classes or "current" in classes:
                return int(t[1:])
    m2 = re.search(r"\bJ(\d{1,2})\b", text)
    if m2:
        return int(m2.group(1))
    return None


def _parse_kickoff_label(label: str) -> str | None:
    """Best-effort ISO-ish from labels like 'Sab 15/08 19:30h Previa'."""
    if not label:
        return None
    m = re.search(
        r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\s+(\d{1,2}):(\d{2})",
        label,
    )
    if not m:
        return None
    day, month, year_s, hour, minute = m.groups()
    year = int(year_s) if year_s else _now().year
    if year < 100:
        year += 2000
    try:
        return f"{year:04d}-{int(month):02d}-{int(day):02d}T{int(hour):02d}:{int(minute):02d}"
    except (TypeError, ValueError):
        return None


def _hub_fixtures(competition: str) -> tuple[int | None, list[dict[str, Any]]]:
    path = _ff_path(competition)
    url = f"{BASE}/{path}/posibles-alineaciones"
    soup = get_soup(url, timeout=20)
    if not soup:
        return None, []

    jornada = _parse_jornada(soup)
    fixtures: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.select("a[href*='/partidos/']"):
        href = a.get("href") or ""
        m = re.search(r"/partidos/(\d+)-([a-z0-9\-]+)", href, re.I)
        if not m:
            continue
        fid, slug = m.group(1), m.group(2).lower()
        if fid in seen:
            continue
        # Amistosos / no liga: slugs con clubs extranjeros frecuentes en bloque pretemporada
        # Nos quedamos con partidos que tienen "Previa" cerca o están en contenedores de liga.
        label = a.get_text(" ", strip=True)
        parent = a.find_parent(class_=True)
        parent_txt = parent.get_text(" ", strip=True) if parent else label
        is_previa = "previa" in (label + " " + parent_txt).lower()
        # Excluir amistosos obvios (julio en label cuando jornada es agosto+) — soft
        if not is_previa and "previa" not in parent_txt.lower():
            # Aún incluir si el slug parece partido de liga (dos equipos conocidos)
            if "-" not in slug:
                continue

        parts = slug.split("-")
        # home-away: split roughly in half by known pattern team-team
        home_slug, away_slug = _split_match_slug(slug)
        home = _slug_to_name(home_slug)
        away = _slug_to_name(away_slug)
        kickoff = _parse_kickoff_label(label) or _parse_kickoff_label(parent_txt)

        seen.add(fid)
        fixtures.append(
            {
                "id": fid,
                "slug": slug,
                "kickoff": kickoff,
                "home": home,
                "away": away,
                "url": _abs(href),
                "label": label[:80],
                "has_previa": is_previa,
            }
        )
        if len(fixtures) >= MAX_FIXTURES * 2:
            break

    # Preferir los que tienen Previa (jornada actual); completar con el resto del hub
    with_previa = [f for f in fixtures if f.get("has_previa")]
    without = [f for f in fixtures if not f.get("has_previa")]
    chosen = (with_previa + without)[:MAX_FIXTURES]

    log.info(
        "FF matchday hub [%s] jornada=%s fixtures=%d (previa=%d)",
        path,
        jornada,
        len(chosen),
        len(with_previa),
    )
    return jornada, chosen


def _split_match_slug(slug: str) -> tuple[str, str]:
    """Split 'alaves-getafe' / 'real-madrid-real-sociedad' into home/away slugs."""
    known_multi = [
        "real-madrid",
        "real-sociedad",
        "atletico-madrid",
        "athletic-club",
        "rayo-vallecano",
        "deportivo-alaves",
        "manchester-united",
        "manchester-city",
        "nottingham-forest",
        "west-ham",
        "crystal-palace",
        "newcastle-united",
        "brighton-and-hove",
        "wolverhampton-wanderers",
        "tottenham-hotspur",
        "sheffield-united",
        "aston-villa",
    ]
    s = slug.lower().strip("-")
    for km in sorted(known_multi, key=len, reverse=True):
        if s.startswith(km + "-"):
            return km, s[len(km) + 1 :]
        if s.endswith("-" + km):
            return s[: -(len(km) + 1)], km
    parts = s.split("-")
    if len(parts) < 2:
        return s, ""
    mid = len(parts) // 2
    return "-".join(parts[:mid]), "-".join(parts[mid:])


def _side_of(el) -> str | None:
    cur = el
    for _ in range(14):
        if cur is None:
            return None
        classes = " ".join(cur.get("class") or []).lower() if hasattr(cur, "get") else ""
        if "visitante" in classes:
            return "away"
        if re.search(r"\blocal\b", classes) and "visitante" not in classes:
            return "home"
        cur = getattr(cur, "parent", None)
    return None


def _role_of(el) -> str:
    cur = el
    for _ in range(14):
        if cur is None:
            break
        classes = " ".join(cur.get("class") or []).lower() if hasattr(cur, "get") else ""
        if "suplente" in classes:
            return "bench"
        cur = getattr(cur, "parent", None)
    return "starter"


def _parse_match_previa(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    url = fixture.get("url")
    if not url:
        return []
    soup = get_soup(str(url), timeout=18)
    if not soup:
        return []

    players: list[dict[str, Any]] = []
    seen: set[str] = set()

    for w in soup.select(".probabilidad-widget"):
        pct_m = re.search(r"(\d{1,3})\s*%", w.get_text(" ", strip=True))
        if not pct_m:
            continue
        pct = int(pct_m.group(1))
        if pct > 100:
            continue

        box = w
        link = None
        img = None
        for _ in range(7):
            box = getattr(box, "parent", None)
            if box is None:
                break
            link = box.select_one("a[href*='/jugadores/']")
            img = box.select_one("img[alt]")
            if link or (img and (img.get("alt") or "").strip()):
                break

        href = _abs(link.get("href")) if link else None
        name = None
        if img and (img.get("alt") or "").strip():
            alt = (img.get("alt") or "").strip()
            if alt.lower() not in ("jugador", "player", ""):
                name = alt
        if not name:
            name = _name_from_player_url(href)
        if not name and link:
            raw = link.get_text(" ", strip=True)
            # evitar "1.81 90%"
            if raw and not re.match(r"^[\d.,%\s]+$", raw) and len(raw) > 2:
                name = raw
        if not name or len(name) < 2:
            continue

        key = name.strip().lower()
        if key in seen:
            continue
        seen.add(key)

        side = _side_of(w)
        role = _role_of(w)
        team = fixture.get("home") if side == "home" else fixture.get("away") if side == "away" else None
        flags = []
        near = w.parent.get_text(" ", strip=True) if w.parent else ""
        if re.search(r"\bTRN\b", near, re.I):
            flags.append("TRN")

        players.append(
            {
                "name": name.strip(),
                "team": team,
                "side": side,
                "role": role,
                "lineup_prob": pct,
                "profile_url": href,
                "flags": flags,
                "source": "futbolfantasy_matchday",
                "availability": "available",
                "is_recommendation": pct >= 80,
                "is_chollo": False,
            }
        )

    log.info(
        "FF matchday previa %s: %d jugadores (%s-%s)",
        fixture.get("id"),
        len(players),
        fixture.get("home"),
        fixture.get("away"),
    )
    return players


def fetch_ff_matchday(
    *,
    competition: str = "laliga",
    use_cache: bool = True,
) -> dict[str, Any]:
    """
    Devuelve:
      status, competition, jornada, fixtures[], by_player{}, players[], fetched_at, cache_used
    """
    comp = (competition or "laliga").strip().lower() or "laliga"
    empty = {
        "status": "fail",
        "competition": comp,
        "jornada": None,
        "fixtures": [],
        "by_player": {},
        "players": [],
        "fetched_at": _now().isoformat(),
        "cache_used": False,
    }

    if use_cache:
        cached = _load_cache(comp)
        if cached and (cached.get("fixtures") or cached.get("players")):
            cached = dict(cached)
            cached["cache_used"] = True
            cached["status"] = cached.get("status") or "ok"
            return cached

    try:
        jornada, fixtures = _hub_fixtures(comp)
        all_players: list[dict[str, Any]] = []
        rich_fixtures: list[dict[str, Any]] = []

        for fx in fixtures:
            try:
                plist = _parse_match_previa(fx)
            except Exception as exc:  # noqa: BLE001
                log.warning("FF matchday previa falló %s: %s", fx.get("id"), exc)
                plist = []
            row = dict(fx)
            row["players"] = plist
            row["players_count"] = len(plist)
            rich_fixtures.append(row)
            for p in plist:
                p = dict(p)
                p["fixture_id"] = fx.get("id")
                p["opponent"] = fx.get("away") if p.get("side") == "home" else fx.get("home")
                p["kickoff"] = fx.get("kickoff")
                all_players.append(p)

        by_player: dict[str, dict[str, Any]] = {}
        for p in all_players:
            key = (p.get("name") or "").strip().lower()
            if not key:
                continue
            prev = by_player.get(key)
            # Conservar el de mayor % (titular vs doble aparición)
            if not prev or float(p.get("lineup_prob") or 0) >= float(prev.get("lineup_prob") or 0):
                by_player[key] = p

        status = "ok" if rich_fixtures and all_players else ("partial" if rich_fixtures else "fail")
        payload = {
            "status": status,
            "competition": comp,
            "jornada": jornada,
            "fixtures": rich_fixtures,
            "by_player": by_player,
            "players": list(by_player.values()),
            "fetched_at": _now().isoformat(),
            "cache_used": False,
            "source": "futbolfantasy_matchday",
        }
        if status != "fail":
            _save_cache(comp, payload)
        log.info(
            "FF matchday [%s] status=%s jornada=%s fixtures=%d players=%d",
            comp,
            status,
            jornada,
            len(rich_fixtures),
            len(by_player),
        )
        return payload
    except Exception as exc:  # noqa: BLE001
        log.warning("FF matchday [%s] falló: %s", comp, exc)
        cached = _load_cache(comp)
        if cached:
            cached = dict(cached)
            cached["cache_used"] = True
            cached["status"] = cached.get("status") or "cache"
            return cached
        empty["error"] = str(exc)
        return empty


__all__ = ["fetch_ff_matchday"]
