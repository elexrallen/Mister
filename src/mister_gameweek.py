"""
Jornada y calendario desde el propio Mister.

Endpoints (descubiertos con `scripts/probe_mister_gameweek.py`):
  - GET  /feed                  → bloque `feed-top-gameweek` con el id de jornada
  - POST /ajax/sw/gameweek      → panel de jornada: 38 jornadas con fechas, partidos
                                  con kickoff exacto, alineaciones probables (`preview`),
                                  puntos reales por jugador y mi once (con capitán)
  - POST /ajax/sw/competition   → calendario completo de la temporada + clasificación

Mister es la autoridad para fechas, rival y puntos; FutbolFantasy sigue siendo la
autoridad para el % de titularidad previsto.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("mister_gameweek")

CACHE_DIR = Path(__file__).resolve().parent / "cache"
# El panel de jornada cambia en vivo; el calendario apenas.
GAMEWEEK_CACHE_TTL_MIN = 45
COMPETITION_CACHE_TTL_HOURS = 6

POSITION_BY_CODE = {1: "GK", 2: "DF", 3: "MF", 4: "FW"}

_FEED_GW_RE = re.compile(r'data-sw="gameweek/(\d+)"')
_FEED_MATCH_RE = re.compile(
    r'data-sw="gameweek/(\d+)/(\d+)"([^>]*)>([\s\S]{0,600}?)</button>',
    re.I,
)
_FEED_STATUS_RE = re.compile(r'data-status="([a-z]+)"', re.I)
_FEED_TEAM_RE = re.compile(r"teams/(\d+)\.png")
_FEED_TS_RE = re.compile(r'data-ts="(\d+)"')


# ---------------------------------------------------------------------------
# Caché en disco (fail-soft; el motor nunca debe caerse por esto)
# ---------------------------------------------------------------------------

def _cache_path(name: str) -> Path:
    return CACHE_DIR / name


def _cache_read(name: str, ttl_minutes: float) -> Any | None:
    path = _cache_path(name)
    if not path.exists():
        return None
    try:
        age_min = (datetime.now(timezone.utc).timestamp() - path.stat().st_mtime) / 60.0
        if age_min > ttl_minutes:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.debug("caché %s ilegible: %s", name, exc)
        return None


def _cache_write(name: str, payload: Any) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(name).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudo cachear %s: %s", name, exc)


# ---------------------------------------------------------------------------
# Feed HTML: id de jornada (+ fixtures como fallback si el AJAX falla)
# ---------------------------------------------------------------------------

def parse_feed_gameweek_id(html: str) -> str | None:
    """`data-sw="gameweek/3968"` del bloque superior del feed."""
    if not html:
        return None
    m = _FEED_GW_RE.search(html)
    return m.group(1) if m else None


def parse_feed_fixtures(html: str) -> list[dict[str, Any]]:
    """
    Partidos del bloque `gameweek-matches-inline`: ids de equipo por el escudo
    y kickoff en `data-ts` (unix). Fallback si /ajax/sw/gameweek no responde.
    """
    out: list[dict[str, Any]] = []
    if not html:
        return out
    for m in _FEED_MATCH_RE.finditer(html):
        gw_id, match_id, attrs, chunk = m.groups()
        teams = _FEED_TEAM_RE.findall(chunk or "")
        ts_m = _FEED_TS_RE.search(chunk or "")
        if len(teams) < 2 or not ts_m:
            continue
        status_m = _FEED_STATUS_RE.search(attrs or "")
        out.append(
            {
                "id": match_id,
                "gameweek_id": gw_id,
                "home_id": teams[0],
                "away_id": teams[1],
                "kickoff_ts": int(ts_m.group(1)),
                "status": status_m.group(1).lower() if status_m else None,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Normalización del panel de jornada
# ---------------------------------------------------------------------------

def _iso_from_ts(ts: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _iso_from_mister_date(text: Any) -> str | None:
    """'2026-08-15 19:30:00' (hora local Mister) → ISO sin tz."""
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").isoformat()
    except ValueError:
        return None


def _game_kickoff(game: dict[str, Any]) -> tuple[int | None, str | None]:
    date = game.get("date") if isinstance(game.get("date"), dict) else {}
    ts = date.get("ts")
    try:
        ts_i = int(ts) if ts is not None else None
    except (TypeError, ValueError):
        ts_i = None
    return ts_i, _iso_from_ts(ts_i) if ts_i else None


def build_matchday(
    gw_data: dict[str, Any] | None,
    *,
    team_label: Any = None,
    competition: str | None = None,
) -> dict[str, Any]:
    """
    Panel de jornada → bloque `matchday` del payload.
    Mantiene el contrato de `ff_matchday` (fixtures con `kickoff`, `home`, `away`).
    """
    label = team_label or (lambda tid: f"Club {tid}")
    if not isinstance(gw_data, dict) or not gw_data:
        return {"status": "unavailable", "source": "mister", "fixtures": []}

    status_blk = gw_data.get("gameweekStatus") if isinstance(gw_data.get("gameweekStatus"), dict) else {}
    fixtures: list[dict[str, Any]] = []
    for game in gw_data.get("games") or []:
        if not isinstance(game, dict):
            continue
        home_id = str(game.get("id_home") or "")
        away_id = str(game.get("id_away") or "")
        ts, iso = _game_kickoff(game)
        fixtures.append(
            {
                "id": str(game.get("id") or ""),
                "home": label(home_id),
                "away": label(away_id),
                "home_id": home_id or None,
                "away_id": away_id or None,
                "kickoff": iso,
                "kickoff_ts": ts,
                "status": game.get("status"),
                "goals_home": game.get("goals_home"),
                "goals_away": game.get("goals_away"),
            }
        )
    fixtures.sort(key=lambda f: (f.get("kickoff_ts") or 0))

    seconds_to_start = status_blk.get("secondsRemainingToStart")
    try:
        seconds_to_start = int(seconds_to_start) if seconds_to_start is not None else None
    except (TypeError, ValueError):
        seconds_to_start = None

    return {
        "status": "ok" if fixtures else "empty",
        "source": "mister",
        "gameweek_id": status_blk.get("id") or gw_data.get("id_gameweek"),
        "jornada": status_blk.get("gameweek"),
        "gameweek_status": status_blk.get("status"),
        "is_live": bool(status_blk.get("isLive")),
        "season": status_blk.get("season"),
        "competition": competition,
        "first_match": _iso_from_mister_date(status_blk.get("firstMatchDate")),
        "last_match": _iso_from_mister_date(status_blk.get("lastMatchDate")),
        "seconds_to_start": seconds_to_start,
        "fixtures_count": len(fixtures),
        "fixtures": fixtures,
    }


def gameweek_schedule(gw_data: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Las 38 jornadas con fechas (`gameweeks` del panel)."""
    out: list[dict[str, Any]] = []
    for row in (gw_data or {}).get("gameweeks") or []:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "id": row.get("id"),
                "jornada": row.get("gameweek"),
                "status": row.get("status"),
                "first_match": _iso_from_mister_date(row.get("firstMatchDate")),
                "last_match": _iso_from_mister_date(row.get("lastMatchDate")),
            }
        )
    return out


def extract_preview(gw_data: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """
    `preview[id_match] = {players: {id_team: [...]}, confirmed: 0|1}`
    → señales de jornada por jugador (once probable según Mister).
    """
    out: dict[str, dict[str, Any]] = {}
    preview = (gw_data or {}).get("preview")
    if not isinstance(preview, dict):
        return out

    fixtures_by_id = {
        str(g.get("id")): g for g in (gw_data or {}).get("games") or [] if isinstance(g, dict)
    }
    for match_id, block in preview.items():
        if not isinstance(block, dict):
            continue
        confirmed = bool(block.get("confirmed"))
        game = fixtures_by_id.get(str(match_id)) or {}
        home_id = str(game.get("id_home") or "")
        away_id = str(game.get("id_away") or "")
        ts, iso = _game_kickoff(game)
        teams = block.get("players") if isinstance(block.get("players"), dict) else {}
        for team_id, players in teams.items():
            if not isinstance(players, list):
                continue
            rival_id = away_id if str(team_id) == home_id else home_id
            for p in players:
                if not isinstance(p, dict) or not p.get("id"):
                    continue
                out[str(p["id"])] = {
                    "gw_probable_xi": True,
                    "gw_confirmed": confirmed or bool(p.get("confirmed")),
                    "gw_fixture_id": str(match_id),
                    "gw_opponent_id": rival_id or None,
                    "gw_is_home": str(team_id) == home_id if home_id else None,
                    "gw_kickoff": iso,
                    "gw_kickoff_ts": ts,
                }
    return out


def preview_coverage(gw_data: dict[str, Any] | None) -> set[str]:
    """
    Equipos con previa publicada. Sin esto no se puede distinguir
    "no está en el once probable" de "aún no hay previa de su partido".
    """
    teams: set[str] = set()
    preview = (gw_data or {}).get("preview")
    if not isinstance(preview, dict):
        return teams
    for block in preview.values():
        players = (block or {}).get("players") if isinstance(block, dict) else None
        if isinstance(players, dict):
            teams.update(str(t) for t in players)
    return teams


def playing_team_ids(matchday: dict[str, Any] | None) -> set[str]:
    """IDs de equipos con partido en la jornada actual."""
    ids: set[str] = set()
    for f in (matchday or {}).get("fixtures") or []:
        if not isinstance(f, dict):
            continue
        for key in ("home_id", "away_id", "id_home", "id_away"):
            v = f.get(key)
            if v not in (None, "", 0, "0"):
                ids.add(str(v))
    return ids


def apply_blank_gameweek(
    players: list[dict[str, Any]],
    matchday: dict[str, Any] | None,
    *,
    min_fixtures: int = 6,
) -> int:
    """
    Marca `gw_blank` / `gw_out` a jugadores cuyo equipo NO disputa esta jornada.

    Mister muestra el icono de prohibido (no puntúa). Sin esto, el motor puede
    alinearlos por % titular de temporada (p.ej. Hermansen 100% FF sin rival).
    Solo actúa si el panel trae suficientes partidos (evita falsos blanks).
    """
    if not isinstance(matchday, dict):
        return 0
    fixtures = matchday.get("fixtures") or []
    if not isinstance(fixtures, list) or len(fixtures) < min_fixtures:
        return 0
    playing = playing_team_ids(matchday)
    if len(playing) < min_fixtures:
        return 0

    touched = 0
    seen: set[int] = set()
    for p in players:
        if id(p) in seen:
            continue
        seen.add(id(p))
        tid = str(p.get("team_id") or "")
        if not tid:
            continue
        if tid in playing:
            if p.get("gw_blank"):
                p["gw_blank"] = False
            ext = p.get("external")
            if isinstance(ext, dict) and ext.get("gw_blank"):
                ext["gw_blank"] = False
            continue
        p["gw_blank"] = True
        p["gw_out"] = True
        p["gw_probable_xi"] = False
        ext = p.get("external")
        if isinstance(ext, dict):
            ext["gw_blank"] = True
            ext["gw_out"] = True
            ext["gw_starter"] = False
        touched += 1
    if touched:
        log.info(
            "Blank GW: %s jugadores sin partido esta jornada (%s equipos juegan)",
            touched,
            len(playing),
        )
    return touched


def extract_gw_points(gw_data: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """
    `players[id_match].all[id_team] = [{id, points, played, ...}]`
    → puntos reales de la jornada para toda la competición en una sola llamada.
    """
    out: dict[str, dict[str, Any]] = {}
    blocks = (gw_data or {}).get("players")
    if not isinstance(blocks, dict):
        return out
    for match_id, block in blocks.items():
        if not isinstance(block, dict):
            continue
        teams = block.get("all") if isinstance(block.get("all"), dict) else block
        if not isinstance(teams, dict):
            continue
        for team_id, players in teams.items():
            if not isinstance(players, list):
                continue
            for p in players:
                if not isinstance(p, dict) or not p.get("id"):
                    continue
                pts = p.get("points")
                if isinstance(pts, str):
                    pts = None if pts.strip() in ("?", "-", "") else pts
                try:
                    pts_i = int(pts) if pts is not None else None
                except (TypeError, ValueError):
                    pts_i = None
                out[str(p["id"])] = {
                    "points": pts_i,
                    "played": bool(p.get("played")),
                    "status": p.get("status"),
                    "id_match": str(match_id),
                    "id_team": str(team_id),
                }
    return out


def extract_my_lineup(gw_data: dict[str, Any] | None) -> dict[str, Any]:
    """Mi once de la jornada tal y como está guardado en Mister (incluye capitán)."""
    lineup = (gw_data or {}).get("lineup")
    positions = lineup.get("positions") if isinstance(lineup, dict) else None
    starters: list[dict[str, Any]] = []
    captain_id: str | None = None
    if isinstance(positions, dict):
        for pos_code, slots in positions.items():
            if not isinstance(slots, dict):
                continue
            for slot, p in slots.items():
                if not isinstance(p, dict) or not p.get("id"):
                    continue
                pid = str(p["id"])
                if p.get("captain"):
                    captain_id = pid
                starters.append(
                    {
                        "player_id": pid,
                        "name": p.get("name"),
                        "position": POSITION_BY_CODE.get(int(pos_code) if str(pos_code).isdigit() else 0),
                        "slot": p.get("slot") or slot,
                        "captain": bool(p.get("captain")),
                        "played": bool(p.get("played")),
                    }
                )
    bench = [
        {"player_id": str(p.get("id")), "name": p.get("name")}
        for p in (gw_data or {}).get("bench") or []
        if isinstance(p, dict) and p.get("id")
    ]
    gw_user = (gw_data or {}).get("gameweek_user") if isinstance((gw_data or {}).get("gameweek_user"), dict) else {}
    return {
        "lineup_size": (gw_data or {}).get("lineupSize"),
        "starters": starters,
        "bench": bench,
        "captain_id": captain_id,
        "captain_set": captain_id is not None,
        "points": gw_user.get("points"),
        "rank": gw_user.get("rank"),
    }


# ---------------------------------------------------------------------------
# Calendario completo de la competición
# ---------------------------------------------------------------------------

def build_standings_table(comp_data: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Clasificación real de la competición por id de equipo."""
    out: dict[str, dict[str, Any]] = {}
    for row in (comp_data or {}).get("table") or []:
        if not isinstance(row, dict) or row.get("id") is None:
            continue
        out[str(row["id"])] = {
            "team_id": str(row["id"]),
            "name": row.get("name"),
            "pos": row.get("pos"),
            "points": row.get("points"),
            "played": row.get("played"),
            "goals_for": row.get("goals_for"),
            "goals_against": row.get("goals_against"),
            "diff": row.get("diff"),
        }
    return out


def build_played_opponents(
    comp_data: dict[str, Any] | None,
    *,
    before_jornada: int | None = None,
) -> dict[str, list[str]]:
    """
    Rivales ya disputados por equipo, en orden de jornada.

    Sirve para saber contra quién sumó cada jugador su racha de puntos y, con
    eso, cuántos puntos fantasy concede realmente cada equipo.
    """
    games_by_gw = (comp_data or {}).get("games")
    if not isinstance(games_by_gw, dict):
        return {}
    try:
        gw_keys = sorted(games_by_gw, key=lambda k: int(k))
    except (TypeError, ValueError):
        return {}

    out: dict[str, list[str]] = {}
    for key in gw_keys:
        try:
            jornada = int(key)
        except (TypeError, ValueError):
            continue
        if before_jornada is not None and jornada >= before_jornada:
            break
        for game in games_by_gw.get(key) or []:
            if not isinstance(game, dict):
                continue
            home_id = str(game.get("id_home") or "")
            away_id = str(game.get("id_away") or "")
            if not home_id or not away_id:
                continue
            out.setdefault(home_id, []).append(away_id)
            out.setdefault(away_id, []).append(home_id)
    return out


def build_team_schedule(
    comp_data: dict[str, Any] | None,
    *,
    from_jornada: int | None = None,
    horizon: int = 5,
) -> dict[str, list[dict[str, Any]]]:
    """
    Próximos partidos por equipo: `{team_id: [{jornada, opponent_id, is_home, kickoff}]}`.
    Base para planificar más allá de la jornada en curso.
    """
    games_by_gw = (comp_data or {}).get("games")
    if not isinstance(games_by_gw, dict):
        return {}
    try:
        gw_keys = sorted(games_by_gw, key=lambda k: int(k))
    except (TypeError, ValueError):
        gw_keys = list(games_by_gw)

    out: dict[str, list[dict[str, Any]]] = {}
    taken = 0
    for key in gw_keys:
        try:
            jornada = int(key)
        except (TypeError, ValueError):
            continue
        if from_jornada is not None and jornada < from_jornada:
            continue
        taken += 1
        if taken > horizon:
            break
        for game in games_by_gw.get(key) or []:
            if not isinstance(game, dict):
                continue
            home_id = str(game.get("id_home") or "")
            away_id = str(game.get("id_away") or "")
            ts, iso = _game_kickoff(game)
            if home_id:
                out.setdefault(home_id, []).append(
                    {
                        "jornada": jornada,
                        "opponent_id": away_id or None,
                        "is_home": True,
                        "kickoff": iso,
                        "kickoff_ts": ts,
                        "status": game.get("status"),
                    }
                )
            if away_id:
                out.setdefault(away_id, []).append(
                    {
                        "jornada": jornada,
                        "opponent_id": home_id or None,
                        "is_home": False,
                        "kickoff": iso,
                        "kickoff_ts": ts,
                        "status": game.get("status"),
                    }
                )
    return out


# ---------------------------------------------------------------------------
# Fetch (requiere sesión de mister_client)
# ---------------------------------------------------------------------------

def fetch_gameweek(ajax_post: Any, gw_id: str | int | None) -> dict[str, Any] | None:
    """POST /ajax/sw/gameweek. Fail-soft con caché corta."""
    if not gw_id:
        return None
    cache_name = f"mister_gameweek_{gw_id}.json"
    cached = _cache_read(cache_name, GAMEWEEK_CACHE_TTL_MIN)
    if cached is not None:
        log.info("Jornada Mister %s desde caché", gw_id)
        return cached
    try:
        raw = ajax_post("/ajax/sw/gameweek", {"post": "gameweek", "id": str(gw_id)})
    except Exception as exc:  # noqa: BLE001
        log.warning("ajax/sw/gameweek falló (%s): %s", gw_id, exc)
        return None
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict) or not data:
        log.warning("ajax/sw/gameweek sin data usable")
        return None
    _cache_write(cache_name, data)
    return data


def fetch_competition(ajax_post: Any, id_competition: Any) -> dict[str, Any] | None:
    """POST /ajax/sw/competition. Fail-soft con caché de horas."""
    cache_name = f"mister_competition_{id_competition or 'x'}.json"
    cached = _cache_read(cache_name, COMPETITION_CACHE_TTL_HOURS * 60)
    if cached is not None:
        log.info("Calendario Mister (comp %s) desde caché", id_competition)
        return cached
    try:
        raw = ajax_post("/ajax/sw/competition", {"post": "competition"}, timeout=40)
    except Exception as exc:  # noqa: BLE001
        log.warning("ajax/sw/competition falló: %s", exc)
        return None
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict) or not data:
        log.warning("ajax/sw/competition sin data usable")
        return None
    _cache_write(cache_name, data)
    return data
