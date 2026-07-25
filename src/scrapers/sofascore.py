"""
Sofascore API — best-effort con headers de navegador.
Fail-soft: 403/timeout → []; no abortar el pipeline.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .http_util import get_session, USER_AGENT

log = logging.getLogger("scrapers.sofascore")

API = "https://api.sofascore.com/api/v1"
MAX_LOOKUPS = 12
REQUEST_GAP_S = 0.35

SOFA_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Origin": "https://www.sofascore.com",
    "Referer": "https://www.sofascore.com/",
    "Cache-Control": "no-cache",
}


def _get_json(url: str, timeout: int = 10) -> tuple[Any | None, int | None]:
    """Devuelve (payload, status_code). status 403 → (None, 403)."""
    try:
        resp = get_session().get(url, timeout=timeout, headers=SOFA_HEADERS)
        if resp.status_code == 403:
            return None, 403
        if resp.status_code >= 400:
            log.warning("Sofascore HTTP %s %s", resp.status_code, url)
            return None, resp.status_code
        return resp.json(), resp.status_code
    except Exception as exc:  # noqa: BLE001
        log.warning("Sofascore GET falló %s (%s)", url, exc)
        return None, None


def _avg_from_events(payload: Any) -> float | None:
    if not payload:
        return None
    if isinstance(payload, list):
        events = payload
    else:
        events = (
            payload.get("events")
            or payload.get("lastRatings")
            or payload.get("ratings")
            or payload.get("tournamentSeasons")
            or []
        )
    vals: list[float] = []
    for ev in events[:8]:
        if not isinstance(ev, dict):
            continue
        for key in ("rating", "value", "score", "avgRating"):
            if ev.get(key) is not None:
                try:
                    v = float(ev[key])
                    if 4.0 <= v <= 10.0:
                        vals.append(v)
                except (TypeError, ValueError):
                    pass
                break
        player = ev.get("player") or {}
        if isinstance(player, dict) and player.get("rating") is not None:
            try:
                v = float(player["rating"])
                if 4.0 <= v <= 10.0:
                    vals.append(v)
            except (TypeError, ValueError):
                pass
    if vals:
        return round(sum(vals[:5]) / min(len(vals), 5), 2)
    if isinstance(payload, dict):
        for k in ("average", "avgRating", "proposedAverage"):
            if payload.get(k) is not None:
                try:
                    v = float(payload[k])
                    if 4.0 <= v <= 10.0:
                        return round(v, 2)
                except (TypeError, ValueError):
                    pass
        stats = payload.get("player") or payload.get("statistics") or {}
        if isinstance(stats, dict):
            for k in ("proposedAverage", "averageRating", "rating", "avgRating"):
                if stats.get(k) is not None:
                    try:
                        v = float(stats[k])
                        if 4.0 <= v <= 10.0:
                            return round(v, 2)
                    except (TypeError, ValueError):
                        pass
    return None


def _rating_for_id(sid: str | int) -> float | None:
    sid = str(sid)
    data, status = _get_json(f"{API}/player/{sid}/last-ratings")
    if status == 403:
        return None  # caller decides abort
    avg = _avg_from_events(data)
    if avg is not None:
        return avg
    data2, status2 = _get_json(f"{API}/player/{sid}")
    if status2 == 403:
        return None
    return _avg_from_events(data2)


def api_available() -> bool:
    """Probe con un jugador conocido (Yamal). No usa endpoint de calendario."""
    _, status = _get_json(f"{API}/player/1402912")
    if status == 403:
        log.info("Sofascore API 403 → no disponible")
        return False
    if status and status >= 400:
        log.info("Sofascore API HTTP %s → skip", status)
        return False
    return status is not None and status < 400


def fetch_sofascore_for_players(
    players: list[dict[str, Any]],
    *,
    max_lookups: int = MAX_LOOKUPS,
) -> tuple[list[dict[str, Any]], str]:
    """
    players: dicts con name/team y opcional sofascore_id.
    Devuelve (registros, status) status ∈ ok|partial|skip|fail.
    """
    out: list[dict[str, Any]] = []
    try:
        if not players:
            return [], "skip"
        if not api_available():
            return [], "skip"

        lookups = 0
        forbidden = False
        for p in players:
            if lookups >= max_lookups or forbidden:
                break
            sid = p.get("sofascore_id")
            name = p.get("name")
            avg = None
            if sid:
                lookups += 1
                time.sleep(REQUEST_GAP_S)
                # Detect 403 mid-batch
                data, status = _get_json(f"{API}/player/{sid}/last-ratings")
                if status == 403:
                    forbidden = True
                    break
                avg = _avg_from_events(data)
                if avg is None:
                    avg = _rating_for_id(sid)
            elif name:
                lookups += 1
                time.sleep(REQUEST_GAP_S)
                q = str(name).replace(" ", "%20")
                data, status = _get_json(f"{API}/search/players/{q}")
                if status == 403:
                    forbidden = True
                    break
                results = []
                if isinstance(data, dict):
                    results = data.get("results") or data.get("players") or []
                if results:
                    first = results[0]
                    entity = first.get("entity") or first.get("player") or first
                    sid = entity.get("id")
                    name = entity.get("name") or name
                    if sid:
                        time.sleep(REQUEST_GAP_S)
                        avg = _rating_for_id(sid)

            if avg is not None:
                out.append({
                    "name": name,
                    "team": p.get("team"),
                    "sofascore_avg_5": avg,
                    "points_streak": "unknown",
                    "availability": "unknown",
                    "lineup_prob": None,
                    "is_chollo": False,
                    "is_recommendation": False,
                    "profile_url": f"https://www.sofascore.com/player/{sid}" if sid else None,
                    "source": "sofascore",
                    "sofascore_id": str(sid) if sid else None,
                })

        if forbidden and out:
            status = "partial"
        elif forbidden and not out:
            status = "skip"
        elif out:
            status = "ok" if len(out) >= 3 else "partial"
        else:
            status = "skip"
        log.info("Sofascore enriquecidos: %d (%s)", len(out), status)
        return out, status
    except Exception as exc:  # noqa: BLE001
        log.warning("Sofascore falló: %s", exc)
        return out, "fail" if not out else "partial"
