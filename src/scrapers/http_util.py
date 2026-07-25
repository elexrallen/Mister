"""
Utilidades HTTP compartidas para scrapers externos.
Timeouts cortos y fail-soft: nunca tumbar el pipeline.
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("scrapers.http")

DEFAULT_TIMEOUT = 12
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)

_session: requests.Session | None = None


def get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        })
    return _session


def get_text(url: str, timeout: int = DEFAULT_TIMEOUT, **kwargs: Any) -> str | None:
    try:
        resp = get_session().get(url, timeout=timeout, **kwargs)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except Exception as exc:  # noqa: BLE001
        log.warning("GET falló %s (%s)", url, exc)
        return None


def get_json(url: str, timeout: int = DEFAULT_TIMEOUT, **kwargs: Any) -> Any | None:
    try:
        headers = {"Accept": "application/json"}
        resp = get_session().get(url, timeout=timeout, headers=headers, **kwargs)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("GET JSON falló %s (%s)", url, exc)
        return None


def get_soup(url: str, timeout: int = DEFAULT_TIMEOUT) -> BeautifulSoup | None:
    text = get_text(url, timeout=timeout)
    if not text:
        return None
    return BeautifulSoup(text, "lxml")
