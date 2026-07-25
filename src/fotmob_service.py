"""
Cliente FotMob — forma reciente (rating / minutos / goles / xG).

Reemplaza Sofascore como fuente de nota. Fail-soft: nunca tumba el pipeline.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any
from urllib.parse import quote

import requests

from scrapers.http_util import USER_AGENT
from scrapers.name_match import match_player, normalize_name

MATCH_THRESHOLD = 78
log = logging.getLogger("fotmob")

SEARCH_URL = "https://apigw.fotmob.com/searchapi/suggest"
PLAYER_URL = "https://www.fotmob.com/api/playerData"
PLAYER_PAGE_URL = "https://www.fotmob.com/players/{id}"
DEFAULT_TIMEOUT = 12
REQUEST_GAP_S = 0.3
MAX_LOOKUPS_DEFAULT = 40

_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Origin": "https://www.fotmob.com",
    "Referer": "https://www.fotmob.com/",
}

_HTML_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Referer": "https://www.fotmob.com/",
}

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)

# Caché proceso: nombre normalizado → fotmob_id
_id_cache: dict[str, int | None] = {}


def _defaults(*, source: str = "skip") -> dict[str, Any]:
    return {
        "rating_promedio": None,
        "minutos_ultimos_5": 0,
        "goles_ultimos_5": 0,
        "xg_promedio": 0.0,
        "fotmob_id": None,
        "matched_name": None,
        "match_score": 0,
        "source": source,
    }


def _get_json(url: str, *, params: dict[str, Any] | None = None) -> Any | None:
    try:
        resp = requests.get(
            url,
            params=params,
            headers=_HEADERS,
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code >= 400:
            log.warning("FotMob HTTP %s %s", resp.status_code, url)
            return None
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("FotMob GET falló %s (%s)", url, exc)
        return None


def _append_candidate(
    out: list[dict[str, Any]],
    *,
    pid: Any,
    name: Any,
    team: Any = None,
) -> None:
    if pid is None or not name:
        return
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return
    if isinstance(team, dict):
        team = team.get("name")
    if team is not None and not isinstance(team, str):
        team = None
    out.append({"id": pid_i, "name": str(name), "team": team})


def _extract_player_suggestions(payload: Any) -> list[dict[str, Any]]:
    """Normaliza shapes variables del suggest API (incl. squadMemberSuggest)."""
    out: list[dict[str, Any]] = []
    if payload is None:
        return out

    # Shape actual apigw: squadMemberSuggest[].options[].{text, payload}
    if isinstance(payload, dict):
        for suggest_key in ("squadMemberSuggest", "playerSuggest", "squadMembersSuggest"):
            blocks = payload.get(suggest_key)
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                options = block.get("options") or []
                if not isinstance(options, list):
                    continue
                for opt in options:
                    if not isinstance(opt, dict):
                        continue
                    pl = opt.get("payload") if isinstance(opt.get("payload"), dict) else {}
                    if pl.get("isCoach"):
                        continue
                    text = str(opt.get("text") or block.get("text") or "")
                    name = text.split("|", 1)[0].strip() or pl.get("name")
                    pid = pl.get("id") or pl.get("playerId")
                    if pid is None and "|" in text:
                        try:
                            pid = text.split("|", 1)[1].strip()
                        except IndexError:
                            pid = None
                    _append_candidate(
                        out,
                        pid=pid,
                        name=name,
                        team=pl.get("teamName") or pl.get("team"),
                    )

    buckets: list[Any] = []
    if isinstance(payload, list):
        buckets = payload
    elif isinstance(payload, dict):
        for key in ("squadMembers", "players", "suggest", "results", "data"):
            val = payload.get(key)
            if isinstance(val, list):
                buckets.extend(val)
        if not buckets and not out:
            for v in payload.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    if any(k in v[0] for k in ("id", "playerId", "name", "title", "options")):
                        buckets.extend(v)

    for item in buckets:
        if not isinstance(item, dict):
            continue
        # Nested options already handled above; skip suggest wrappers
        if "options" in item and "payload" not in item:
            continue
        typ = str(item.get("type") or item.get("entityType") or "player").lower()
        if typ and typ not in ("player", "players", "squadmember", ""):
            if "team" in typ or "league" in typ or "match" in typ:
                continue
        pid = item.get("id") or item.get("playerId") or item.get("Id")
        name = item.get("name") or item.get("title") or item.get("fullName")
        team = item.get("teamName") or item.get("team")
        _append_candidate(out, pid=pid, name=name, team=team)
    return out


def _best_match(
    mister_name: str,
    mister_team: str | None,
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, int]:
    """Delega en name_match (inicial + equipo) para evitar colisiones de apellido."""
    return match_player(mister_name, mister_team, candidates, threshold=MATCH_THRESHOLD)

def _collect_recent_matches(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Intenta varias rutas del JSON playerData."""
    matches: list[Any] = []

    def take(lst: Any) -> None:
        if isinstance(lst, list):
            matches.extend(lst)

    # Rutas conocidas / defensivas
    take(payload.get("recentMatches"))
    take(payload.get("matches"))
    last = payload.get("lastMatches") or payload.get("latestMatches")
    take(last)

    # Estructura anidada frecuente en FotMob
    for key in ("statOverview", "overview", "playerStats", "stats"):
        block = payload.get(key)
        if isinstance(block, dict):
            take(block.get("matches"))
            take(block.get("recentMatches"))

    # Lista de temporadas → matches
    seasons = payload.get("seasonStats") or payload.get("seasons") or []
    if isinstance(seasons, list):
        for s in seasons[:2]:
            if isinstance(s, dict):
                take(s.get("matches"))

    # matchData / tournamentHistory
    for key in ("matchData", "tournamentHistory", "history"):
        block = payload.get(key)
        if isinstance(block, dict):
            take(block.get("matches"))
            take(block.get("recentMatches"))
        elif isinstance(block, list):
            take(block)

    # Deduplicar por id de partido si existe
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for m in matches:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("matchId") or m.get("id") or m.get("match_id") or id(m))
        if mid in seen:
            continue
        seen.add(mid)
        unique.append(m)
    return unique


def _num(val: Any) -> float | None:
    if val is None or val == "":
        return None
    if isinstance(val, dict):
        # a veces { "value": 7.2 } o { "rating": ... }
        for k in ("value", "rating", "avg", "score"):
            if k in val:
                return _num(val[k])
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _match_minutes(m: dict[str, Any]) -> float:
    for k in ("minutesPlayed", "minutes", "mins", "min"):
        v = _num(m.get(k))
        if v is not None:
            return max(0.0, v)
    # nested performance
    perf = m.get("performance") or m.get("stats") or {}
    if isinstance(perf, dict):
        for k in ("minutesPlayed", "minutes", "mins"):
            v = _num(perf.get(k))
            if v is not None:
                return max(0.0, v)
    return 0.0


def _match_rating(m: dict[str, Any]) -> float | None:
    # Shape actual de __NEXT_DATA__: ratingProps.rating
    props = m.get("ratingProps")
    if isinstance(props, dict):
        v = _num(props.get("rating"))
        if v is not None and 0 < v <= 10:
            return v
    for k in ("rating", "matchRating", "fotMobRating", "grade"):
        v = _num(m.get(k))
        if v is not None and 0 < v <= 10:
            return v
    perf = m.get("performance") or m.get("stats") or {}
    if isinstance(perf, dict):
        for k in ("rating", "matchRating"):
            v = _num(perf.get(k))
            if v is not None and 0 < v <= 10:
                return v
    return None


def _match_goals(m: dict[str, Any]) -> int:
    for k in ("goals", "goal", "scored"):
        v = _num(m.get(k))
        if v is not None:
            return int(v)
    perf = m.get("performance") or m.get("stats") or {}
    if isinstance(perf, dict):
        v = _num(perf.get("goals"))
        if v is not None:
            return int(v)
    return 0


def _match_xg(m: dict[str, Any]) -> float | None:
    for k in ("expectedGoals", "xg", "xG", "expected_goals"):
        v = _num(m.get(k))
        if v is not None:
            return v
    perf = m.get("performance") or m.get("stats") or {}
    if isinstance(perf, dict):
        for k in ("expectedGoals", "xg", "xG"):
            v = _num(perf.get(k))
            if v is not None:
                return v
    return None


def _stats_from_matches(recent: list[dict[str, Any]]) -> dict[str, Any]:
    played = [m for m in recent if _match_minutes(m) > 0][:5]
    if not played:
        # algunos feeds ponen rating sin minutes explícitos
        played = [m for m in recent if _match_rating(m) is not None][:5]
    ratings = [r for r in (_match_rating(m) for m in played) if r is not None]
    minutes = sum(_match_minutes(m) for m in played)
    goals = sum(_match_goals(m) for m in played)
    xgs = [x for x in (_match_xg(m) for m in played) if x is not None]
    return {
        "rating_promedio": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "minutos_ultimos_5": int(round(minutes)),
        "goles_ultimos_5": int(goals),
        "xg_promedio": round(sum(xgs) / len(xgs), 2) if xgs else 0.0,
    }


def _search_terms(player_name: str) -> list[str]:
    """Genera variantes de búsqueda (Mister usa iniciales: 'P. Aubameyang')."""
    name = (player_name or "").strip()
    if not name:
        return []
    terms: list[str] = [name]
    # Quitar iniciales tipo "P. " / "A. "
    no_initial = re.sub(r"^[A-Za-zÀ-ÿ]\.\s+", "", name).strip()
    if no_initial and no_initial not in terms:
        terms.append(no_initial)
    # Varias iniciales: "J. M. Foo"
    no_all_init = re.sub(r"(?:^|\s)[A-Za-zÀ-ÿ]\.\s*", " ", name).strip()
    no_all_init = re.sub(r"\s+", " ", no_all_init)
    if no_all_init and no_all_init not in terms:
        terms.append(no_all_init)
    # Solo apellido (última palabra significativa)
    parts = [p for p in re.split(r"\s+", no_initial or name) if p and p != "."]
    if parts:
        last = parts[-1].strip(".,")
        if len(last) >= 3 and last not in terms:
            terms.append(last)
        if len(parts) >= 2:
            last_two = f"{parts[-2]} {parts[-1]}".strip("., ")
            if last_two not in terms:
                terms.append(last_two)
    return terms


def search_fotmob_player(
    player_name: str,
    team: str | None = None,
) -> tuple[dict[str, Any] | None, int]:
    """Busca jugador en FotMob. Devuelve (candidato, score) o (None, 0)."""
    key = normalize_name(player_name)
    if not key:
        return None, 0
    if key in _id_cache and _id_cache[key] is not None:
        return {"id": _id_cache[key], "name": player_name, "team": team}, 100

    candidates: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for term in _search_terms(player_name):
        payload = _get_json(SEARCH_URL, params={"term": term})
        if payload is None:
            payload = _get_json(f"{SEARCH_URL}?term={quote(term)}")
        for c in _extract_player_suggestions(payload):
            cid = int(c["id"])
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            candidates.append(c)
        if candidates and term != player_name:
            # Ya hay hits con variante más limpia; no hace falta más términos
            best_tmp, score_tmp = _best_match(player_name, team, candidates)
            if best_tmp and score_tmp >= MATCH_THRESHOLD:
                break
        time.sleep(0.08)

    best, score = _best_match(player_name, team, candidates)
    if best:
        _id_cache[key] = int(best["id"])
    else:
        _id_cache[key] = None
    return best, score

def _player_data_from_html(fotmob_id: int) -> dict[str, Any] | None:
    """Fallback: pageProps.data embebido en la ficha HTML (__NEXT_DATA__)."""
    url = PLAYER_PAGE_URL.format(id=fotmob_id)
    try:
        resp = requests.get(url, headers=_HTML_HEADERS, timeout=DEFAULT_TIMEOUT)
        if resp.status_code >= 400:
            log.warning("FotMob player page HTTP %s %s", resp.status_code, url)
            return None
        m = _NEXT_DATA_RE.search(resp.text or "")
        if not m:
            log.warning("FotMob sin __NEXT_DATA__ id=%s", fotmob_id)
            return None
        payload = json.loads(m.group(1))
        data = (
            payload.get("props", {})
            .get("pageProps", {})
            .get("data")
        )
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        log.warning("FotMob HTML falló id=%s (%s)", fotmob_id, exc)
        return None


def fetch_player_data(fotmob_id: int) -> dict[str, Any] | None:
    # HTML SSR (__NEXT_DATA__) es la ruta fiable; la API playerData suele 404/auth.
    data = _player_data_from_html(fotmob_id)
    if isinstance(data, dict) and (data.get("recentMatches") or data.get("id")):
        return data
    api = _get_json(PLAYER_URL, params={"id": fotmob_id})
    return api if isinstance(api, dict) else None


def get_player_fotmob_stats(
    player_name: str,
    team: str | None = None,
) -> dict[str, Any]:
    """
    Stats FotMob para un nombre Mister.
    Siempre devuelve dict; nunca lanza hacia el caller.
    """
    try:
        if not (player_name or "").strip():
            return _defaults(source="skip")

        hit, score = search_fotmob_player(player_name, team)
        if not hit:
            return {**_defaults(source="skip"), "match_score": score}

        time.sleep(REQUEST_GAP_S)
        data = fetch_player_data(int(hit["id"]))
        if not data:
            return {
                **_defaults(source="fail"),
                "fotmob_id": hit["id"],
                "matched_name": hit.get("name"),
                "match_score": score,
            }

        recent = _collect_recent_matches(data)
        stats = _stats_from_matches(recent)

        # Fallback: rating de temporada (mainLeague.stats) si no hay partidos parseados
        if stats["rating_promedio"] is None:
            main = data.get("mainLeague") if isinstance(data.get("mainLeague"), dict) else {}
            for row in main.get("stats") or []:
                if not isinstance(row, dict):
                    continue
                title = str(row.get("title") or row.get("localizedTitleId") or "").lower()
                if "rating" in title:
                    v = _num(row.get("value"))
                    if v is not None and 0 < v <= 10:
                        stats["rating_promedio"] = round(v, 2)
                        break
            if stats["rating_promedio"] is None:
                for path in (
                    ("careerStatistics",),
                    ("mainLeague", "stats"),
                    ("statOverview", "stats"),
                ):
                    node: Any = data
                    ok = True
                    for p in path:
                        if isinstance(node, dict) and p in node:
                            node = node[p]
                        else:
                            ok = False
                            break
                    if not ok:
                        continue
                    if isinstance(node, dict):
                        for k in ("rating", "averageRating", "avgRating"):
                            v = _num(node.get(k))
                            if v is not None and 0 < v <= 10:
                                stats["rating_promedio"] = round(v, 2)
                                break
                    if stats["rating_promedio"] is not None:
                        break

        return {
            **stats,
            "fotmob_id": hit["id"],
            "matched_name": hit.get("name"),
            "match_score": score,
            "source": "fotmob",
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("FotMob stats falló para %s: %s", player_name, exc)
        return _defaults(source="fail")


def enrich_players_with_fotmob(
    players: list[dict[str, Any]],
    *,
    max_lookups: int = MAX_LOOKUPS_DEFAULT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Añade fotmob_stats a cada jugador (batch fail-soft).
    Meta: {fotmob, matched, filled, errors}.
    """
    meta: dict[str, Any] = {
        "fotmob": "skip",
        "matched": 0,
        "filled": 0,
        "errors": [],
        "lookups": 0,
    }
    if not players:
        return players, meta

    out: list[dict[str, Any]] = []
    lookups = 0
    filled = 0
    matched = 0

    for p in players:
        new_p = dict(p)
        if lookups >= max_lookups:
            new_p["fotmob_stats"] = _defaults(source="skip")
            out.append(new_p)
            continue
        try:
            lookups += 1
            # Preferir nombre completo de match externo (FF/JP) si existe
            search_name = str(p.get("name") or "")
            ext = p.get("external") or {}
            if ext.get("matched_name"):
                search_name = str(ext["matched_name"])
            stats = get_player_fotmob_stats(
                search_name,
                str(p.get("team") or "") or None,
            )
            # Si falló con matched_name, reintentar con nombre Mister
            if stats.get("source") == "skip" and search_name != str(p.get("name") or ""):
                stats = get_player_fotmob_stats(
                    str(p.get("name") or ""),
                    str(p.get("team") or "") or None,
                )
            new_p["fotmob_stats"] = stats
            if stats.get("fotmob_id") and stats.get("match_score", 0) >= MATCH_THRESHOLD:
                matched += 1
            if stats.get("rating_promedio") is not None:
                filled += 1
                # Compat scoring / UI legacy
                ext2 = dict(new_p.get("external") or {})
                ext2["sofascore_avg_5"] = stats["rating_promedio"]
                new_p["external"] = ext2
            if lookups < max_lookups and lookups < len(players):
                time.sleep(REQUEST_GAP_S * 0.5)
        except Exception as exc:  # noqa: BLE001
            meta["errors"].append(str(exc))
            new_p["fotmob_stats"] = _defaults(source="fail")
        out.append(new_p)

    meta["lookups"] = lookups
    meta["matched"] = matched
    meta["filled"] = filled
    if filled >= 5:
        meta["fotmob"] = "ok"
    elif filled > 0:
        meta["fotmob"] = "partial"
    elif matched > 0:
        meta["fotmob"] = "partial"
    elif lookups > 0:
        meta["fotmob"] = "fail"
    else:
        meta["fotmob"] = "skip"

    log.info(
        "FotMob enrich lookups=%s matched=%s filled=%s status=%s",
        lookups,
        matched,
        filled,
        meta["fotmob"],
    )
    return out, meta
