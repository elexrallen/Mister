"""
Ficha de jugador Fútbol Fantasy — % titular / titulares vs suplente.
Fail-soft + caché en disco (TTL 7d). Se usa cuando no hay widget de alineación.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .http_util import get_soup

log = logging.getLogger("scrapers.ff_profile")

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "ff_profiles"
CACHE_TTL_HOURS = 24 * 7
MAX_FETCHES_PER_RUN = 48

_fetches_this_run = 0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cache_key(url: str) -> str:
    path = urlparse(url).path.strip("/").replace("/", "_")
    return re.sub(r"[^a-zA-Z0-9_\-]+", "", path)[:120] or "unknown"


def _cache_path(url: str) -> Path:
    return CACHE_DIR / f"{_cache_key(url)}.json"


def _load_cache(url: str) -> dict[str, Any] | None:
    path = _cache_path(url)
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
        return data
    except Exception as exc:  # noqa: BLE001
        log.warning("FF profile cache ilegible %s: %s", url, exc)
        return None


def _save_cache(url: str, payload: dict[str, Any]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(url).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("FF profile cache no escrita %s: %s", url, exc)


def parse_ff_profile_titular_text(text: str) -> dict[str, Any] | None:
    """Extrae % titular / PJ / titulares de texto de ficha FF."""
    if not text:
        return None
    titular_m = re.search(r"Titular\s*\(?\s*(\d{1,3})\s*%\s*\)?", text, re.I)
    suplente_m = re.search(r"Suplente\s*\(?\s*(\d{1,3})\s*%\s*\)?", text, re.I)
    apps_m = re.search(r"Partidos\s+jugados\s*(\d{1,3})", text, re.I)
    starts = None
    benches = None
    # Tras "Titular(88%)" suele ir el número de titularidades
    if titular_m:
        after = text[titular_m.end() : titular_m.end() + 40]
        sm = re.search(r"(\d{1,3})", after)
        if sm:
            starts = int(sm.group(1))
    if suplente_m:
        after = text[suplente_m.end() : suplente_m.end() + 40]
        sm = re.search(r"(\d{1,3})", after)
        if sm:
            benches = int(sm.group(1))
    pct = int(titular_m.group(1)) if titular_m else None
    if pct is not None and not (0 <= pct <= 100):
        pct = None
    apps = int(apps_m.group(1)) if apps_m else None
    if pct is None and starts is not None and apps and apps > 0:
        pct = int(min(100, round(100.0 * starts / float(apps))))
    if pct is None and starts is None and apps is None:
        return None
    return {
        "titular_pct": pct,
        "apps": apps,
        "starts": starts,
        "benches": benches,
        "suplente_pct": int(suplente_m.group(1)) if suplente_m else None,
    }


def fetch_ff_profile_titular(
    profile_url: str | None,
    *,
    use_cache: bool = True,
    force: bool = False,
) -> dict[str, Any] | None:
    """
    Devuelve {titular_pct, apps, starts, ...} o None.
    Respeta tope de fetches por run salvo force/caché hit.
    """
    global _fetches_this_run
    url = (profile_url or "").strip()
    if not url or "futbolfantasy.com/jugadores/" not in url:
        return None
    if use_cache:
        cached = _load_cache(url)
        if cached and cached.get("titular_pct") is not None:
            return cached
    if not force and _fetches_this_run >= MAX_FETCHES_PER_RUN:
        return None
    soup = get_soup(url, timeout=12)
    if not soup:
        return None
    _fetches_this_run += 1
    parsed = parse_ff_profile_titular_text(soup.get_text(" ", strip=True))
    if not parsed:
        return None
    payload = {
        **parsed,
        "profile_url": url,
        "fetched_at": _now().isoformat().replace("+00:00", "Z"),
        "source": "futbolfantasy_profile",
    }
    if use_cache:
        _save_cache(url, payload)
    return payload


def reset_ff_profile_fetch_budget() -> None:
    global _fetches_this_run
    _fetches_this_run = 0


__all__ = [
    "fetch_ff_profile_titular",
    "parse_ff_profile_titular_text",
    "reset_ff_profile_fetch_budget",
    "MAX_FETCHES_PER_RUN",
]
