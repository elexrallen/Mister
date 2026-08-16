"""
Utilidades HTTP compartidas para scrapers externos.
Timeouts cortos y fail-soft: nunca tumbar el pipeline.

Todas las peticiones a webs externas pasan por aquí, así que este es el sitio
donde se controla el ritmo. Un ciclo completo pide a FutbolFantasy las páginas
de equipo, las previas y hasta 48 perfiles de jugador **por liga**: sin
espaciado eso es una ráfaga de varios cientos de peticiones que acaba en 429.
Aquí se hacen tres cosas:

  1. Espaciar las peticiones al mismo host.
  2. Reintentar un 429 respetando `Retry-After`.
  3. Si el host insiste en cortarnos, darlo por caído durante un rato y dejar
     de pedirle nada. Seguir insistiendo solo alarga el bloqueo y llena el log
     de ruido; el pipeline ya sabe caer a caché.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("scrapers.http")

DEFAULT_TIMEOUT = 12
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)

# Espaciado mínimo entre peticiones al mismo host, en segundos
HOST_MIN_GAP_S = {
    "www.futbolfantasy.com": 0.4,
    "futbolfantasy.com": 0.4,
    "www.jornadaperfecta.com": 0.3,
    "jornadaperfecta.com": 0.3,
}
DEFAULT_MIN_GAP_S = 0.0

# Reintentos ante 429 y espera base entre ellos
RATE_LIMIT_RETRIES = 2
RATE_LIMIT_BACKOFF_S = 3.0
# Nunca esperar más de esto aunque el Retry-After pida una barbaridad
RATE_LIMIT_MAX_WAIT_S = 15.0
# Racha de 429 tras la cual se da el host por caído
RATE_LIMIT_TRIP_AFTER = 3
RATE_LIMIT_COOLDOWN_S = 600.0

_session: requests.Session | None = None
_last_request_at: dict[str, float] = {}
_rate_limit_strikes: dict[str, int] = {}
_blocked_until: dict[str, float] = {}
_rate_limited_hosts: dict[str, int] = {}


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


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except ValueError:
        return ""


def _wait_turn(host: str) -> None:
    """Espacia las peticiones al mismo host para no disparar en ráfaga."""
    gap = HOST_MIN_GAP_S.get(host, DEFAULT_MIN_GAP_S)
    now = time.monotonic()
    if gap > 0:
        last = _last_request_at.get(host)
        if last is not None:
            pending = gap - (now - last)
            if pending > 0:
                time.sleep(pending)
                now = time.monotonic()
    _last_request_at[host] = now


def host_is_rate_limited(host: str) -> bool:
    """True mientras el host siga en penalización por 429."""
    until = _blocked_until.get(host)
    if until is None:
        return False
    if time.monotonic() >= until:
        _blocked_until.pop(host, None)
        _rate_limit_strikes.pop(host, None)
        return False
    return True


def rate_limit_report() -> dict[str, int]:
    """`{host: nº de 429}` del ciclo, para poder decirlo en el payload."""
    return dict(_rate_limited_hosts)


def reset_rate_limits() -> None:
    """Olvida el estado de throttling (tests y ejecuciones encadenadas)."""
    _last_request_at.clear()
    _rate_limit_strikes.clear()
    _blocked_until.clear()
    _rate_limited_hosts.clear()


def _retry_after_seconds(resp: requests.Response) -> float | None:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(str(raw).strip()))
    except (TypeError, ValueError):
        return None


def _handle_rate_limit(host: str, resp: requests.Response, attempt: int) -> bool:
    """Anota el 429 y decide si merece la pena reintentar."""
    _rate_limited_hosts[host] = _rate_limited_hosts.get(host, 0) + 1
    strikes = _rate_limit_strikes.get(host, 0) + 1
    _rate_limit_strikes[host] = strikes

    if strikes >= RATE_LIMIT_TRIP_AFTER:
        _blocked_until[host] = time.monotonic() + RATE_LIMIT_COOLDOWN_S
        log.warning(
            "%s nos está limitando (429 x%s): en pausa %.0f min, se tira de caché",
            host,
            strikes,
            RATE_LIMIT_COOLDOWN_S / 60.0,
        )
        return False
    if attempt >= RATE_LIMIT_RETRIES:
        return False

    delay = _retry_after_seconds(resp) or RATE_LIMIT_BACKOFF_S * (attempt + 1)
    delay = min(delay, RATE_LIMIT_MAX_WAIT_S)
    log.info("429 en %s: reintento en %.1f s", host, delay)
    time.sleep(delay)
    return True


def _request(url: str, timeout: int, **kwargs: Any) -> requests.Response | None:
    """GET con espaciado por host, reintento de 429 y corte si el host cae."""
    host = _host_of(url)
    if host_is_rate_limited(host):
        return None
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        _wait_turn(host)
        try:
            resp = get_session().get(url, timeout=timeout, **kwargs)
        except Exception as exc:  # noqa: BLE001
            log.warning("GET falló %s (%s)", url, exc)
            return None
        if resp.status_code == 429:
            if _handle_rate_limit(host, resp, attempt):
                continue
            return None
        try:
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("GET falló %s (%s)", url, exc)
            return None
        _rate_limit_strikes.pop(host, None)
        return resp
    return None


def get_text(url: str, timeout: int = DEFAULT_TIMEOUT, **kwargs: Any) -> str | None:
    resp = _request(url, timeout, **kwargs)
    if resp is None:
        return None
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def get_json(url: str, timeout: int = DEFAULT_TIMEOUT, **kwargs: Any) -> Any | None:
    headers = {**kwargs.pop("headers", {}), "Accept": "application/json"}
    resp = _request(url, timeout, headers=headers, **kwargs)
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError as exc:
        log.warning("GET JSON sin JSON válido %s (%s)", url, exc)
        return None


def get_soup(url: str, timeout: int = DEFAULT_TIMEOUT) -> BeautifulSoup | None:
    text = get_text(url, timeout=timeout)
    if not text:
        return None
    return BeautifulSoup(text, "lxml")
