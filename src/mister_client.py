"""
Cliente HTTP para Mister Fantasy (mister.mundodeportivo.com).

Auth (DevTools → POST /ajax/balance):
  - Cookie: token=<JWT>, PHPSESSID=..., authenticated=true [, refresh-token=...]
  - Header: x-auth: <hash>

Importante: el mercado y la plantilla NO llegan como XHR en la carga inicial.
Mister los pinta en el HTML de /market y /team. Este cliente:
  1) POST /ajax/balance → saldo
  2) GET /market y /team → parsea jugadores del HTML
  3) Extrae _FG_user / _FG_cfg del JS embebido
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime
from typing import Any

import requests

import config
import mister_gameweek

log = logging.getLogger("mister_client")

POS_MAP = {"1": "GK", "2": "DF", "3": "MF", "4": "FW"}

# Mister rota `_FG_cfg.auth` al cambiar de comunidad. El secret estático
# (MISTER_X_AUTH) vale para la liga inicial; tras switch hay que renovarlo
# o /ajax/* responde 401 (HTML sigue OK con cookies).
_session_x_auth: str | None = None


def _strip_accents(text: str) -> str:
    nk = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nk if not unicodedata.combining(c))


def build_cookie_header() -> str:
    if config.MISTER_COOKIE:
        return config.MISTER_COOKIE
    parts: list[str] = []
    if config.MISTER_PHPSESSID:
        parts.append(f"PHPSESSID={config.MISTER_PHPSESSID}")
    if config.MISTER_TOKEN:
        parts.append(f"token={config.MISTER_TOKEN}")
    if config.MISTER_REFRESH_TOKEN:
        parts.append(f"refresh-token={config.MISTER_REFRESH_TOKEN}")
    parts.append("authenticated=true")
    return "; ".join(parts)


def current_x_auth() -> str:
    return (_session_x_auth or config.MISTER_X_AUTH or "").strip()


def apply_x_auth_from_cfg(fg_cfg: dict[str, Any] | None) -> bool:
    """Actualiza x-auth de sesión desde `_FG_cfg.auth` (JS embebido)."""
    if not isinstance(fg_cfg, dict):
        return False
    auth = fg_cfg.get("auth")
    if not isinstance(auth, str):
        return False
    auth = auth.strip()
    if not auth:
        return False
    global _session_x_auth
    if auth != _session_x_auth:
        prev = (_session_x_auth or config.MISTER_X_AUTH or "")[:8]
        _session_x_auth = auth
        log.info("x-auth sesión actualizado (%s… → %s…)", prev or "env", auth[:8])
    return True


def refresh_x_auth_from_html(html: str) -> bool:
    if not html:
        return False
    return apply_x_auth_from_cfg(_extract_js_object(html, "_FG_cfg"))


def mister_headers(referer: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Origin": config.MISTER_API_BASE,
        "Referer": referer or f"{config.MISTER_API_BASE}/feed",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/149.0.0.0 Safari/537.36"
        ),
        "Cookie": build_cookie_header(),
    }
    x_auth = current_x_auth()
    if x_auth:
        headers["x-auth"] = x_auth
    return headers


def ajax_headers() -> dict[str, str]:
    h = mister_headers()
    h["Accept"] = "*/*"
    h["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    h["X-Requested-With"] = "XMLHttpRequest"
    return h


def ajax_post(path: str, data: dict[str, Any] | None = None, timeout: int = 25) -> Any:
    url = f"{config.MISTER_API_BASE}{path}"
    resp = requests.post(url, headers=ajax_headers(), data=data or {}, timeout=timeout)
    if resp.status_code == 401:
        # Tras switch_community el auth del secret queda obsoleto.
        log.warning("ajax %s → 401; reintento tras renovar x-auth desde /team", path)
        try:
            html = fetch_html("/team", timeout=timeout)
            if refresh_x_auth_from_html(html):
                resp = requests.post(
                    url, headers=ajax_headers(), data=data or {}, timeout=timeout
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("Renovación x-auth falló: %s", exc)
    resp.raise_for_status()
    if not resp.content:
        return {}
    return resp.json()


def fetch_html(path: str, timeout: int = 30) -> str:
    url = f"{config.MISTER_API_BASE}{path}"
    resp = requests.get(url, headers=mister_headers(referer=url), timeout=timeout)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def fetch_feed_cards(
    *,
    page_size: int | None = None,
    max_pages: int | None = None,
    seen_ids: set[str] | None = None,
    seen_fps: set[str] | None = None,
    stop_on_caught_up: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    POST /ajax/feed — el HTML de /feed solo trae ~20 tarjetas; el resto
    llega al hacer scroll (offset += cardsPerPage) hasta status=end.
    """
    from rival_finances import parse_feed_ajax_cards, unseen_feed_events, unseen_prizes

    size = int(page_size or getattr(config, "MISTER_FEED_PAGE_SIZE", 20))
    cap = int(max_pages or getattr(config, "MISTER_FEED_MAX_PAGES", 40))
    cards: list[dict[str, Any]] = []
    seen_card: set[str] = set()
    pages = 0
    caught_up = False
    last_status = ""
    for page in range(max(1, cap)):
        offset = page * size
        try:
            raw = ajax_post(
                "/ajax/feed",
                {
                    "end": False,
                    "loading": False,
                    "offset": offset,
                    "cardsPerPage": size,
                },
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("ajax/feed offset=%s falló: %s", offset, exc)
            last_status = "error"
            break
        if not isinstance(raw, dict):
            last_status = "invalid"
            break
        last_status = str(raw.get("status") or "")
        if last_status == "end":
            break
        chunk = raw.get("data")
        if not isinstance(chunk, list) or not chunk:
            break
        pages += 1
        page_new: list[dict[str, Any]] = []
        for card in chunk:
            if not isinstance(card, dict):
                continue
            cid = str(card.get("id") or "")
            if cid and cid in seen_card:
                continue
            if cid:
                seen_card.add(cid)
            page_new.append(card)
            cards.append(card)
        if stop_on_caught_up and (seen_ids is not None or seen_fps is not None):
            tx, prizes = parse_feed_ajax_cards(page_new)
            new_tx = unseen_feed_events(tx, set(seen_ids or []), set(seen_fps or []))
            new_pr = unseen_prizes(prizes, set(seen_ids or []))
            if (tx or prizes) and not new_tx and not new_pr:
                caught_up = True
                log.info("Feed ajax: al día en offset=%s (página %s)", offset, pages)
                break
        if last_status != "ok":
            break
    meta = {
        "pages": pages,
        "cards": len(cards),
        "status": last_status or ("ok" if cards else "empty"),
        "caught_up": caught_up,
        "source": "ajax_feed",
    }
    log.info(
        "Feed ajax: cards=%s pages=%s status=%s caught_up=%s",
        meta["cards"],
        pages,
        meta["status"],
        caught_up,
    )
    return cards, meta


def fetch_player_community_info(player_id: str | int) -> dict[str, Any] | None:
    """
    POST /ajax/player-community-info — cláusula real (clause.value), valor y dueño.
    Fail-soft.
    """
    try:
        raw = ajax_post("/ajax/player-community-info", {"id_player": str(player_id)})
        if not isinstance(raw, dict):
            return None
        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        log.warning("player-community-info falló id=%s: %s", player_id, exc)
        return None


def clause_fields_from_community(info: dict[str, Any] | None) -> dict[str, Any]:
    """Normaliza cláusula/owner/valor/puntos desde player-community-info."""
    out: dict[str, Any] = {
        "clause": None,
        "clause_known": False,
        "clause_floor": None,
        "clause_multiplier": None,
        "market_value": None,
        "owner_id": None,
        "owner_name": None,
        "points": None,
        "mister_avg": None,
        "prior_points": None,
        "prior_avg": None,
        "team_id": None,
        "team_name": None,
    }
    if not info:
        return out
    clause = info.get("clause") if isinstance(info.get("clause"), dict) else {}
    raw_val = clause.get("value")
    try:
        cval = float(raw_val) if raw_val is not None else None
    except (TypeError, ValueError):
        cval = None
    if cval is not None and cval > 0:
        out["clause"] = cval
        out["clause_known"] = True
    try:
        out["clause_floor"] = float(clause["floor"]) if clause.get("floor") is not None else None
    except (TypeError, ValueError):
        out["clause_floor"] = None
    try:
        out["clause_multiplier"] = (
            float(clause["multiplier"]) if clause.get("multiplier") is not None else None
        )
    except (TypeError, ValueError):
        out["clause_multiplier"] = None
    mv = info.get("value")
    if mv is None and isinstance(info.get("market"), dict):
        mv = info["market"].get("price")
    try:
        out["market_value"] = float(mv) if mv is not None else None
    except (TypeError, ValueError):
        out["market_value"] = None
    owner = info.get("owner") if isinstance(info.get("owner"), dict) else {}
    out["owner_id"] = owner.get("id")
    out["owner_name"] = owner.get("name")
    team = info.get("team") if isinstance(info.get("team"), dict) else {}
    if team.get("id") is not None:
        out["team_id"] = str(team.get("id"))
    if team.get("name"):
        out["team_name"] = str(team.get("name"))
        if out["team_id"]:
            register_team_id(out["team_id"], out["team_name"])
    # Puntuación temporada actual
    if info.get("points") is not None:
        try:
            out["points"] = int(float(info["points"]))
        except (TypeError, ValueError):
            pass
    if info.get("avg") is not None:
        try:
            out["mister_avg"] = float(info["avg"])
        except (TypeError, ValueError):
            pass
    # Temporada anterior solo si el payload lo trae (sin inventar)
    for key in ("previousPoints", "priorPoints", "lastSeasonPoints", "pointsPrev"):
        if info.get(key) is not None:
            try:
                out["prior_points"] = float(info[key])
                break
            except (TypeError, ValueError):
                pass
    for key in ("previousAvg", "priorAvg", "lastSeasonAvg", "avgPrev", "previousAverage"):
        if info.get(key) is not None:
            try:
                out["prior_avg"] = float(info[key])
                break
            except (TypeError, ValueError):
                pass
    prev = info.get("previous") or info.get("lastSeason") or info.get("prevSeason")
    if isinstance(prev, dict):
        if out["prior_points"] is None and prev.get("points") is not None:
            try:
                out["prior_points"] = float(prev["points"])
            except (TypeError, ValueError):
                pass
        if out["prior_avg"] is None and prev.get("avg") is not None:
            try:
                out["prior_avg"] = float(prev["avg"])
            except (TypeError, ValueError):
                pass
    return out


def refresh_team_labels(
    players: list[dict[str, Any]],
    *,
    max_lookups: int = 20,
) -> int:
    """
    Corrige team/team_id con AJAX (1 lookup por team_id distinto).
    Evita mapas obsoletos (p.ej. id 6 = Deportivo, no Celta).
    """
    if not players:
        return 0
    # Un jugador representativo por escudo
    sample: dict[str, dict[str, Any]] = {}
    for p in players:
        tid = str(p.get("team_id") or "")
        if not tid or tid in sample:
            continue
        if p.get("id"):
            sample[tid] = p
    lookups = 0
    for tid, p in list(sample.items())[:max_lookups]:
        info = fetch_player_community_info(p["id"])
        fields = clause_fields_from_community(info)
        lookups += 1
        label = register_team_id(fields.get("team_id") or tid, fields.get("team_name"))
        if not label:
            continue
        real_tid = str(fields.get("team_id") or tid)
        for q in players:
            if str(q.get("team_id") or "") == tid or str(q.get("id")) == str(p.get("id")):
                q["team_id"] = real_tid
                q["team"] = label
    # Reaplicar mapa ya aprendido al resto
    for q in players:
        tid = str(q.get("team_id") or "")
        if tid and tid in LALIGA_TEAMS:
            q["team"] = LALIGA_TEAMS[tid]
    return lookups


def enrich_players_with_clauses(
    players: list[dict[str, Any]],
    *,
    max_lookups: int = 25,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Añade clause/clause_known vía AJAX (cap lookups)."""
    meta: dict[str, Any] = {"clauses": "skip", "known": 0, "lookups": 0}
    if not players:
        return players, meta
    out: list[dict[str, Any]] = []
    known = 0
    lookups = 0
    for p in players:
        new_p = dict(p)
        if "clause_known" not in new_p:
            new_p["clause"] = new_p.get("clause")
            new_p["clause_known"] = False
            new_p["market_value"] = new_p.get("market_value") or new_p.get("price")
        if lookups >= max_lookups or not new_p.get("id"):
            out.append(new_p)
            continue
        lookups += 1
        info = fetch_player_community_info(new_p["id"])
        fields = clause_fields_from_community(info)
        new_p["clause"] = fields["clause"]
        new_p["clause_known"] = fields["clause_known"]
        new_p["clause_floor"] = fields["clause_floor"]
        new_p["clause_multiplier"] = fields["clause_multiplier"]
        if fields["market_value"] is not None:
            new_p["market_value"] = fields["market_value"]
            if not new_p.get("price"):
                new_p["price"] = fields["market_value"]
        if fields["owner_id"] is not None:
            new_p["owner_id"] = fields["owner_id"]
        if fields["owner_name"]:
            new_p["owner_name"] = fields["owner_name"]
        if fields.get("points") is not None:
            new_p["points"] = fields["points"]
        if fields.get("mister_avg") is not None:
            new_p["mister_avg"] = fields["mister_avg"]
            new_p["form"] = fields["mister_avg"]
        if fields.get("team_id") or fields.get("team_name"):
            tid = fields.get("team_id") or new_p.get("team_id")
            label = register_team_id(tid, fields.get("team_name")) or team_label(tid)
            if tid:
                new_p["team_id"] = str(tid)
            if label:
                new_p["team"] = label
        if fields.get("prior_points") is not None:
            new_p["prior_points"] = fields["prior_points"]
        if fields.get("prior_avg") is not None:
            new_p["prior_avg"] = fields["prior_avg"]
        # HTML fallback en chunk si AJAX no dio valor
        if not new_p["clause_known"]:
            html_clause, html_known = parse_clause_from_html(str(new_p.get("_html_chunk") or ""))
            if html_known and html_clause:
                new_p["clause"] = html_clause
                new_p["clause_known"] = True
        if new_p.get("points_trend") in (None, "unknown") and new_p.get("recent_gw_points"):
            new_p["points_trend"] = points_trend_from_gw(new_p.get("recent_gw_points"))
        if new_p["clause_known"]:
            known += 1
        out.append(new_p)
    meta["lookups"] = lookups
    meta["known"] = known
    if known >= 5:
        meta["clauses"] = "ok"
    elif known > 0:
        meta["clauses"] = "partial"
    elif lookups > 0:
        meta["clauses"] = "fail"
    log.info("Clauses enrich lookups=%s known=%s status=%s", lookups, known, meta["clauses"])
    return out, meta


def _extract_js_object(html: str, var_name: str) -> dict[str, Any] | None:
    """Extrae `var _FG_user = {...};` con un contador de llaves (JSON-ish)."""
    marker = f"{var_name} ="
    idx = html.find(marker)
    if idx < 0:
        marker = f"var {var_name} ="
        idx = html.find(marker)
    if idx < 0:
        return None
    start = html.find("{", idx)
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    quote = ""
    for i in range(start, len(html)):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = html[start : i + 1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    # A veces trae trailing commas / undefined — fallback mínimo
                    try:
                        cleaned = re.sub(r",\s*}", "}", blob)
                        cleaned = re.sub(r",\s*]", "]", cleaned)
                        return json.loads(cleaned)
                    except json.JSONDecodeError:
                        log.warning("No se pudo parsear %s", var_name)
                        return None
    return None


def parse_euro_number(text: str) -> int:
    """'11.708.000' o '11,708,000' → int."""
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else 0


def parse_euro_amount(text: str) -> int:
    """Extrae solo el importe tras € (evita mezclar '14 jugadores' con el precio)."""
    if not text:
        return 0
    m = re.search(r"€\s*([\d.\s]+)", text)
    if m:
        return parse_euro_number(m.group(1))
    # Si el texto es solo cifras/puntos (underName limpio)
    plain = re.sub(r"<[^>]+>", "", text)
    plain = re.sub(r"[↓↑]", "", plain).strip()
    if re.fullmatch(r"[\d.\s]+", plain):
        return parse_euro_number(plain)
    m2 = re.search(r"(\d{1,3}(?:\.\d{3})+)", text)
    return parse_euro_number(m2.group(1)) if m2 else 0


def parse_float_es(text: str) -> float:
    """'0,0' / '6.5' → float."""
    t = (text or "").strip().replace(".", "").replace(",", ".")
    try:
        return float(re.sub(r"[^\d.\-]", "", t) or 0)
    except ValueError:
        return 0.0


def clean_player_name(raw: str) -> str:
    """
    Nombre visible Mister: puede incluir SVG de duda o emoji de cláusula dentro de .name.
    """
    t = re.sub(r"<[^>]+>", " ", raw or "")
    t = re.sub(r"\s+", " ", t).strip()
    return t


# Captura .name aunque haya hijos (emoji/SVG); luego clean_player_name
_NAME_UNDER_RE = (
    r'<div class="name">([\s\S]*?)</div>\s*'
    r'<div class="underName">([\s\S]*?)</div>'
)


def parse_streak_gw_points(html_chunk: str) -> list[int] | None:
    """
    Barras .streak de Mister: enteros por jornada, o None si solo hay '-'.
    No inventa valores.
    """
    if not html_chunk:
        return None
    m = re.search(r'class="[^"]*streak[^"]*"([\s\S]{0,900}?)</div>', html_chunk, re.I)
    if not m:
        return None
    nums: list[int] = []
    for tok in re.findall(r">\s*([^<]+?)\s*<", m.group(1)):
        t = tok.strip().replace(",", ".")
        if t in ("-", "–", "—", "", "?"):
            continue
        if re.fullmatch(r"-?\d+", t):
            nums.append(int(t))
        elif re.fullmatch(r"-?\d+[.,]\d+", t):
            nums.append(int(float(t.replace(",", "."))))
    return nums or None


def parse_streak_values(raw: Any) -> list[int | None] | None:
    """
    `streak` de /ajax/sw/players: [8, "-", 7] → [8, None, 7].
    None = jornada sin jugar (dato, no ausencia de dato).
    """
    if not isinstance(raw, list) or not raw:
        return None
    out: list[int | None] = []
    for item in raw:
        if item is None:
            out.append(None)
            continue
        if isinstance(item, (int, float)):
            out.append(int(item))
            continue
        text = str(item).strip().replace(",", ".")
        if text in ("-", "–", "—", "", "?"):
            out.append(None)
        elif re.fullmatch(r"-?\d+", text):
            out.append(int(text))
        elif re.fullmatch(r"-?\d+\.\d+", text):
            out.append(int(float(text)))
        else:
            out.append(None)
    return out if any(v is not None for v in out) else out


def played_gw_points(gw: list[int | None] | None) -> list[int]:
    """Solo jornadas disputadas (descarta los None de 'no jugó')."""
    if not gw:
        return []
    return [int(v) for v in gw if v is not None]


def points_trend_from_gw(gw: list[int | None] | None) -> str:
    """up|down|flat|unknown a partir de últimas jornadas numéricas."""
    gw = played_gw_points(gw)
    if not gw or len(gw) < 2:
        return "unknown"
    if len(gw) >= 4:
        mid = len(gw) // 2
        first = sum(gw[:mid]) / mid
        second = sum(gw[mid:]) / (len(gw) - mid)
        if second > first + 0.4:
            return "up"
        if second < first - 0.4:
            return "down"
        return "flat"
    if gw[-1] > gw[-2]:
        return "up"
    if gw[-1] < gw[-2]:
        return "down"
    return "flat"


def parse_scoring_tail(html_after_under: str) -> dict[str, Any]:
    """Lee .avg y .streak en la cola de la card (tras underName)."""
    avg_m = re.search(r'class="avg[^"]*"[^>]*>\s*([^<]+?)\s*<', html_after_under, re.I)
    mister_avg = parse_float_es(avg_m.group(1)) if avg_m else None
    gw = parse_streak_gw_points(html_after_under)
    return {
        "mister_avg": mister_avg,
        "form": mister_avg,
        "recent_gw_points": gw,
        "points_trend": points_trend_from_gw(gw),
    }


def _pos(raw: str | None) -> str:
    return POS_MAP.get(str(raw or "").strip(), "MF")


def trend_from_arrow(html_chunk: str) -> str | None:
    """Devuelve up/down/None según flecha Mister (sin inventar porcentaje)."""
    if "value-arrow green" in html_chunk or "↑" in html_chunk:
        return "up"
    if "value-arrow red" in html_chunk or "↓" in html_chunk:
        return "down"
    return None


def parse_clause_from_html(chunk: str) -> tuple[float | None, bool]:
    """
    Extrae cláusula de rescisión si aparece en el HTML (fail-soft).
    Nunca inventa: si no hay patrón claro → (None, False).
    """
    if not chunk:
        return None, False
    # Atributos data-*
    for pat in (
        r'data-clause=["\']([^"\']+)["\']',
        r'data-clausula=["\']([^"\']+)["\']',
        r'data-release[_-]?clause=["\']([^"\']+)["\']',
    ):
        m = re.search(pat, chunk, re.I)
        if m:
            val = parse_euro_amount(m.group(1)) or parse_euro_number(m.group(1))
            if val > 0:
                return float(val), True
    # Texto visible: "Cláusula: €12.000.000" / "clausula 12.000.000"
    m = re.search(
        r"cl[aá]usula(?:\s*de\s*rescisi[oó]n)?[^€\d]{0,40}"
        r"(?:€\s*)?([\d.\s]{4,}|\d+(?:[.,]\d+)?\s*[mM]?)",
        chunk,
        re.I,
    )
    if m:
        raw = m.group(1).strip()
        # "12.5M" / "12,5 M"
        mm = re.match(r"^([\d]+(?:[.,]\d+)?)\s*[mM]$", raw.replace(" ", ""))
        if mm:
            num = float(mm.group(1).replace(",", "."))
            return num * 1_000_000, True
        val = parse_euro_amount("€ " + raw) or parse_euro_number(raw)
        if val >= 100_000:  # evita falsos positivos (puntos, etc.)
            return float(val), True
    if re.search(r"cl[aá]usula|release.?clause", chunk, re.I):
        # Menciona cláusula pero sin importe parseable
        return None, False
    return None, False


# IDs de escudo Mister (cdn …/teams/{id}.png) — temporada actual (verificado vía AJAX).
# Los IDs cambian entre temporadas; si un id no está, se completa en runtime.
LALIGA_TEAMS: dict[str, str] = {
    "1": "Athletic",
    "2": "Atlético",
    "3": "Barcelona",
    "5": "Betis",
    "6": "Deportivo",
    "7": "Alavés",
    "8": "Espanyol",
    "9": "Getafe",
    "10": "Girona",
    "12": "Levante",
    "13": "Málaga",
    "14": "Rayo",
    "15": "Real Madrid",
    "16": "Real Sociedad",
    "17": "Sevilla",
    "18": "Valencia",
    "19": "Valencia",
    "20": "Villarreal",
    "23": "Elche",
    "48": "Alavés",
    "50": "Osasuna",
}


def shorten_mister_club_name(name: str | None) -> str:
    """Normaliza nombres largos del AJAX Mister a etiqueta corta de UI."""
    raw = (name or "").strip()
    if not raw:
        return ""
    key = _strip_accents(raw).lower()
    key = re.sub(r"\s+", " ", key).strip()
    aliases = {
        "athletic club": "Athletic",
        "athletic": "Athletic",
        "atletico": "Atlético",
        "atletico de madrid": "Atlético",
        "club atletico de madrid": "Atlético",
        "barcelona": "Barcelona",
        "fc barcelona": "Barcelona",
        "real betis": "Betis",
        "betis": "Betis",
        "celta": "Celta",
        "celta de vigo": "Celta",
        "rc celta": "Celta",
        "deportivo": "Deportivo",
        "deportivo da coruna": "Deportivo",
        "deportivo de la coruna": "Deportivo",
        "rc deportivo": "Deportivo",
        "alaves": "Alavés",
        "deportivo alaves": "Alavés",
        "espanyol": "Espanyol",
        "rcd espanyol": "Espanyol",
        "getafe": "Getafe",
        "girona": "Girona",
        "girona fc": "Girona",
        "levante": "Levante",
        "levante ud": "Levante",
        "malaga": "Málaga",
        "malaga cf": "Málaga",
        "mallorca": "Mallorca",
        "rayo vallecano": "Rayo",
        "rayo": "Rayo",
        "real madrid": "Real Madrid",
        "real sociedad": "Real Sociedad",
        "sevilla": "Sevilla",
        "sevilla fc": "Sevilla",
        "valencia": "Valencia",
        "valencia cf": "Valencia",
        "villarreal": "Villarreal",
        "villarreal cf": "Villarreal",
        "elche": "Elche",
        "elche cf": "Elche",
        "osasuna": "Osasuna",
        "ca osasuna": "Osasuna",
        "real oviedo": "Oviedo",
        "oviedo": "Oviedo",
        "valladolid": "Valladolid",
        "real valladolid": "Valladolid",
    }
    if key in aliases:
        return aliases[key]
    # Fallback: primera palabra significativa
    return raw.split()[0] if raw else ""


def register_team_id(team_id: str | int | None, team_name: str | None) -> str:
    """Registra/actualiza el mapa id→club y devuelve la etiqueta corta."""
    tid = str(team_id or "").strip()
    label = shorten_mister_club_name(team_name)
    if tid and label:
        LALIGA_TEAMS[tid] = label
    return label or (LALIGA_TEAMS.get(tid) if tid else "") or ""


def team_label(team_id: str | None) -> str:
    if not team_id:
        return ""
    tid = str(team_id)
    if tid in LALIGA_TEAMS:
        return LALIGA_TEAMS[tid]
    if tid in ("0", "1490"):
        return ""
    return f"Club {tid}"


def is_unknown_team_label(team: str | None) -> bool:
    t = (team or "").strip()
    return not t or t.lower().startswith("club ")


def resolve_team_label(team_id: str | None, fallback_name: str | None = None) -> str:
    """Etiqueta Mister; si el id es desconocido, usa fallback externo (FF, etc.)."""
    label = team_label(team_id)
    if not is_unknown_team_label(label):
        return label
    fb = shorten_mister_club_name(fallback_name) or (fallback_name or "").strip()
    return fb or label


def mister_player_photo_url(player_id: str | int | None) -> str | None:
    """URL pública estable del retrato oficial de Mister."""
    pid = str(player_id or "").strip()
    if not pid:
        return None
    return f"https://cdn-mister.mundodeportivo.com/file/cdn-common/players/{pid}.png"


def mister_team_logo_url(team_id: str | int | None) -> str | None:
    """URL pública estable del escudo oficial de Mister."""
    tid = str(team_id or "").strip()
    if not tid or tid in ("0", "1490"):
        return None
    return f"https://cdn-mister.mundodeportivo.com/file/cdn-common/teams/{tid}.png"


def parse_market_players(html: str) -> list[dict[str, Any]]:
    """
    Cards del mercado (orden real en HTML):
      team-logo → data-position → points → data-id_player → .name → .underName (VM)
      + botón .btn-bid (data-text = precio de puja / salida; data-id_owner si rival vende)

    underName ≈ valor de mercado; data-text puede ser mayor si el rival pide más.
    """
    players: list[dict[str, Any]] = []
    pattern = re.compile(
        r"teams/(\d+)\.png[\s\S]{0,250}?"
        r"data-position=['\"](\d)['\"][\s\S]{0,350}?"
        r"data-id_player=['\"](\d+)['\"][\s\S]{0,500}?"
        + _NAME_UNDER_RE,
        re.I,
    )
    bid_btn_re = re.compile(
        r"btn-bid[^>]*"
        r"data-id_owner=['\"](\d+)['\"][^>]*"
        r"data-id_player=['\"](\d+)['\"][^>]*"
        r"data-text=['\"]([^'\"]+)['\"]"
        r"|"
        r"btn-bid[^>]*"
        r"data-id_player=['\"](\d+)['\"][^>]*"
        r"data-id_owner=['\"](\d+)['\"][^>]*"
        r"data-text=['\"]([^'\"]+)['\"]",
        re.I,
    )
    seen: set[str] = set()
    for m in pattern.finditer(html):
        team_id, pos, pid, name_raw, under = m.groups()
        name = clean_player_name(name_raw)
        if not name or pid in seen:
            continue
        seen.add(pid)
        market_value = parse_euro_amount(under)
        trend = trend_from_arrow(under)
        # points antes del nombre; avg/streak suelen ir DESPUÉS de underName
        head = html[max(0, m.start() - 80) : m.end()]
        # El botón de puja suele ir justo después de la card
        tail = html[m.end() : m.end() + 900]
        pts_m = re.search(r'<div class="points">\s*([^<]+?)\s*</div>', head)
        points = int(parse_float_es(pts_m.group(1))) if pts_m else 0
        scoring = parse_scoring_tail(tail)
        # fallback avg en head por si el HTML cambia
        if scoring["mister_avg"] is None:
            avg_m = re.search(r'class="avg[^"]*"[^>]*>\s*([^<]+?)\s*<', head, re.I)
            if avg_m:
                scoring["mister_avg"] = parse_float_es(avg_m.group(1))
                scoring["form"] = scoring["mister_avg"]

        ask_price = market_value
        owner_id: str | None = None
        bid_m = bid_btn_re.search(tail)
        if not bid_m:
            # Atributos a veces en otro orden / más lejos en el <li>
            wider = html[max(0, m.start() - 40) : m.end() + 1400]
            bid_m = bid_btn_re.search(wider)
        if bid_m:
            g = bid_m.groups()
            if g[0] is not None:
                owner_id, bid_pid, bid_text = g[0], g[1], g[2]
            else:
                bid_pid, owner_id, bid_text = g[3], g[4], g[5]
            if str(bid_pid) == str(pid):
                parsed_ask = parse_euro_amount(bid_text)
                if parsed_ask > 0:
                    ask_price = parsed_ask
                if owner_id and str(owner_id) not in ("", "0"):
                    owner_id = str(owner_id)
                else:
                    owner_id = None
            else:
                owner_id = None

        # Coste de fichaje = precio del botón (salida/puja mín.); VM aparte
        price = ask_price if ask_price > 0 else market_value
        row: dict[str, Any] = {
            "id": pid,
            "name": name,
            "position": _pos(pos),
            "team": team_label(team_id),
            "team_id": team_id,
            "price": price,
            "market_value": market_value if market_value > 0 else price,
            "points": points,
            "form": scoring.get("form"),
            "mister_avg": scoring.get("mister_avg"),
            "recent_gw_points": scoring.get("recent_gw_points"),
            "points_trend": scoring.get("points_trend") or "unknown",
            "injury": False,
            "in_lineup": None,
            "trend": trend,
            "price_delta_5d": None,
            "min_bid": price,
            "seller": "market",
            "on_daily_market": True,
            "data_quality": {
                "price": "mister_ask" if ask_price != market_value else "mister",
                "points": "mister",
                "form": "mister" if scoring.get("mister_avg") is not None else "missing",
                "trend": "mister_arrow" if trend else "missing",
            },
        }
        if owner_id:
            row["owner_id"] = owner_id
            # Rival vs propio se resuelve en tag_own_market_listings(my_uc)
            row["listed_by_rival"] = True
            row["listed_by_me"] = False
        players.append(row)
    log.info("HTML /market → %s jugadores", len(players))
    return players


def fetch_offers_received() -> dict[str, Any]:
    """
    POST /ajax/sw/offers-received — buzón de ofertas (máquina/rivales).
    Fail-soft: dict vacío normalizado si falla.
    """
    from sales_state import parse_offers_received

    try:
        raw = ajax_post("/ajax/sw/offers-received", {"post": "offers-received"}, timeout=20)
    except Exception as exc:  # noqa: BLE001
        log.warning("ajax/sw/offers-received falló: %s", exc)
        return parse_offers_received({})
    parsed = parse_offers_received(raw)
    n = len(parsed.get("pending_offers") or [])
    log.info("Ofertas recibidas: pending=%s total=%s", n, (parsed.get("count") or {}).get("total"))
    return parsed


def parse_team_players(html: str) -> list[dict[str, Any]]:
    """Plantilla desde /team: once (lineup-player) + lista lateral (cards tipo mercado)."""
    players: list[dict[str, Any]] = []
    seen: set[str] = set()

    # 1) Once: atributos en el <button class="lineup-player...">
    for m in re.finditer(r"<button([^>]*lineup-player[^>]*)>", html, re.I):
        attrs = m.group(1)
        pid_m = re.search(r'data-id_player=["\'](\d+)["\']', attrs)
        pos_m = re.search(r'data-position=["\'](\d)["\']', attrs)
        if not pid_m or not pos_m:
            continue
        pid = pid_m.group(1)
        if pid in seen:
            continue
        after = html[m.end() : m.end() + 1600]
        end = after.find("</button>")
        body = after[:end] if end >= 0 else after
        name_m = re.search(r'<div class="name">([\s\S]*?)</div>', body)
        if not name_m:
            continue
        name = clean_player_name(name_m.group(1))
        if not name:
            continue
        seen.add(pid)
        team_m = re.search(r"teams/(\d+)\.png", body)
        # En once, .points suele ser la media (0,0), no totales
        avg_m = re.search(r'class="avg[^"]*"[^>]*>\s*([^<]+?)\s*<', body, re.I)
        pts_m = re.search(r'class="points[^"]*"[^>]*>\s*([^<]+?)\s*<', body, re.I)
        mister_avg = parse_float_es(avg_m.group(1)) if avg_m else None
        if mister_avg is None and pts_m:
            mister_avg = parse_float_es(pts_m.group(1))
        gw = parse_streak_gw_points(body)
        tid = team_m.group(1) if team_m else ""
        players.append({
            "id": pid,
            "name": name,
            "position": _pos(pos_m.group(1)),
            "team": team_label(tid),
            "team_id": tid,
            "price": 0,
            "points": 0,
            "form": mister_avg,
            "mister_avg": mister_avg,
            "recent_gw_points": gw,
            "points_trend": points_trend_from_gw(gw),
            "injury": "injured" in body.lower() or "lesionado" in body.lower(),
            "in_lineup": True,
            "from_lineup_only": True,
            "trend": None,
            "price_delta_5d": None,
            "data_quality": {
                "price": "missing",
                "form": "mister" if mister_avg is not None else "missing",
                "lineup": "mister",
                "position": "mister",
            },
        })

    # 2) Lista / banquillo: misma estructura que mercado (position con comillas simples/dobles)
    pattern = re.compile(
        r"teams/(\d+)\.png[\s\S]{0,250}?"
        r"data-position=['\"](\d)['\"][\s\S]{0,350}?"
        r"data-id_player=['\"](\d+)['\"][\s\S]{0,500}?"
        + _NAME_UNDER_RE,
        re.I,
    )
    for m in pattern.finditer(html):
        team_id, pos, pid, name_raw, under = m.groups()
        name = clean_player_name(name_raw)
        if not name:
            continue
        head = html[max(0, m.start() - 80) : m.end()]
        tail = html[m.end() : m.end() + 500]
        scoring = parse_scoring_tail(tail)
        pts_m = re.search(r'<div class="points">\s*([^<]+?)\s*</div>', head)
        points = int(parse_float_es(pts_m.group(1))) if pts_m else 0
        price = parse_euro_amount(under)
        if pid in seen:
            # Completar precio / scoring si el del once no lo tenía
            for p in players:
                if p["id"] == pid:
                    p["from_lineup_only"] = False
                    if not p.get("price"):
                        p["price"] = price
                        p["trend"] = trend_from_arrow(under)
                        dq = dict(p.get("data_quality") or {})
                        dq["price"] = "mister" if price else "missing"
                        p["data_quality"] = dq
                    if not p.get("points"):
                        p["points"] = points
                    # En pretemporada el once trae 0,0; la lista tiene el mismo avg
                    if scoring.get("mister_avg") is not None:
                        if p.get("mister_avg") is None:
                            p["mister_avg"] = scoring["mister_avg"]
                            p["form"] = scoring["mister_avg"]
                    if not p.get("recent_gw_points") and scoring.get("recent_gw_points"):
                        p["recent_gw_points"] = scoring["recent_gw_points"]
                        p["points_trend"] = scoring["points_trend"]
            continue
        seen.add(pid)
        players.append({
            "id": pid,
            "name": name,
            "position": _pos(pos),
            "team": team_label(team_id),
            "team_id": team_id,
            "price": price,
            "points": points,
            "form": scoring.get("form"),
            "mister_avg": scoring.get("mister_avg"),
            "recent_gw_points": scoring.get("recent_gw_points"),
            "points_trend": scoring.get("points_trend") or "unknown",
            "injury": bool(re.search(r"st-injured|#injured|lesionado", head, re.I)),
            "in_lineup": False,
            "from_lineup_only": False,
            "trend": trend_from_arrow(under),
            "price_delta_5d": None,
            "data_quality": {
                "price": "mister" if price else "missing",
                "form": "mister" if scoring.get("mister_avg") is not None else "missing",
                "lineup": "mister",
                "position": "mister",
            },
        })

    log.info(
        "HTML /team → %s jugadores (once=%s)",
        len(players),
        sum(1 for p in players if p.get("in_lineup")),
    )
    return players


def parse_standings(html: str, my_uc: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """
    Clasificación real desde /standings.
    Devuelve (rivals, me_row). Incluye profile_path si hay enlace users/id/slug.
    """
    blocks = re.findall(r'<div class="player-row">([\s\S]*?)</a>\s*</div>', html)
    rivals: list[dict[str, Any]] = []
    me_row: dict[str, Any] | None = None
    seen: set[str] = set()

    for body in blocks:
        uc_m = re.search(r"users/(\d+)/", body)
        pos_m = re.search(r'class="position">\s*(\d+)', body)
        name_m = re.search(r'class="name\s*">\s*([^<]+)', body)
        played_m = re.search(r'class="played">([\s\S]*?)</div>', body)
        pts_m = re.search(r'class="points">\s*([0-9]+)', body)
        href_m = re.search(r'href=["\'](users/\d+/[^"\']+)["\']', body)
        if not uc_m or not pos_m:
            continue
        uc = uc_m.group(1)
        if uc in seen:
            continue
        seen.add(uc)
        played = played_m.group(1) if played_m else ""
        sq_m = re.search(r"(\d+)\s*jugadores", played)
        value = parse_euro_amount(played)
        name = name_m.group(1).strip() if name_m else f"Manager {uc}"
        row = {
            "team_id": uc,
            "manager": name,
            "team_name": name,
            "rank": int(pos_m.group(1)),
            "points": int(pts_m.group(1)) if pts_m else 0,
            "squad_size": int(sq_m.group(1)) if sq_m else 0,
            "squad_value": value,
            "liquidity_estimated": None,
            "squad_value_shown": value,
            "profile_path": href_m.group(1) if href_m else f"users/{uc}",
            "recent_buys": [],
            "recent_sells": [],
            "squad_summary": {"GK": 0, "DF": 0, "MF": 0, "FW": 0},
            "key_players": [],
            "position_gaps": [],
            "squad": [],
            "activity": "desconocida",
            "data_quality": {
                "rank": "mister",
                "points": "mister",
                "squad_value": "mister",
                "liquidity": "missing",
                "squad": "missing",
            },
        }
        if my_uc and uc == str(my_uc):
            me_row = row
        else:
            rivals.append(row)

    rivals.sort(key=lambda r: r["rank"])
    log.info("HTML /standings → %s rivales (+me=%s)", len(rivals), bool(me_row))
    return rivals, me_row


def parse_user_squad(html: str) -> list[dict[str, Any]]:
    """
    Plantilla de un manager rival desde /users/{id}/{slug}.
    Parser laxo: data-id_player + .name (+ precio/posición si aparecen cerca).
    """
    players: list[dict[str, Any]] = []
    seen: set[str] = set()
    pattern = re.compile(
        r'data-id_player=["\'](\d+)["\']([\s\S]{0,900}?)'
        r'<div class="name">([\s\S]*?)</div>',
        re.I,
    )
    for m in pattern.finditer(html):
        pid, mid, name_raw = m.groups()
        name = clean_player_name(name_raw)
        if not name or pid in seen:
            continue
        seen.add(pid)
        window_before = html[max(0, m.start() - 350) : m.start()]
        window_after = mid + html[m.end() : m.end() + 350]
        pos_m = re.search(r"data-position=['\"](\d)['\"]", window_before)
        if not pos_m:
            pos_m = re.search(r"data-position=['\"](\d)['\"]", window_after)
        team_m = re.search(r"teams/(\d+)\.png", window_before) or re.search(
            r"teams/(\d+)\.png", window_after
        )
        under_m = re.search(r'<div class="underName">([\s\S]*?)</div>', window_after)
        price = parse_euro_amount(under_m.group(1)) if under_m else 0
        # Ampliar cola para avg/streak tras underName
        tail = html[m.end() : m.end() + 550]
        chunk = window_before + window_after + tail
        clause_val, clause_known = parse_clause_from_html(chunk)
        pts_m = re.search(r'<div class="points">\s*([^<]+?)\s*</div>', chunk)
        points = int(parse_float_es(pts_m.group(1))) if pts_m else 0
        scoring = parse_scoring_tail(tail if under_m else chunk)
        if scoring["mister_avg"] is None:
            avg_m = re.search(r'class="avg[^"]*"[^>]*>\s*([^<]+?)\s*<', chunk, re.I)
            if avg_m:
                scoring["mister_avg"] = parse_float_es(avg_m.group(1))
                scoring["form"] = scoring["mister_avg"]
        tid = team_m.group(1) if team_m else ""
        players.append({
            "id": pid,
            "name": name,
            "position": _pos(pos_m.group(1)) if pos_m else "MF",
            "team": team_label(tid),
            "team_id": tid,
            "price": price,
            "market_value": price,
            "points": points,
            "form": scoring.get("form"),
            "mister_avg": scoring.get("mister_avg"),
            "recent_gw_points": scoring.get("recent_gw_points"),
            "points_trend": scoring.get("points_trend") or "unknown",
            "clause": clause_val,
            "clause_known": clause_known,
            "in_lineup": None,
            "injury": False,
        })
    return players


def _position_gaps_from_squad(squad: list[dict[str, Any]]) -> tuple[dict[str, int], list[str]]:
    summary = {"GK": 0, "DF": 0, "MF": 0, "FW": 0}
    for p in squad:
        pos = p.get("position") or "MF"
        if pos in summary:
            summary[pos] += 1
    mins = {"GK": config.MIN_GK, "DF": config.MIN_DF, "MF": config.MIN_MF, "FW": config.MIN_FW}
    gaps = [pos for pos, need in mins.items() if summary.get(pos, 0) < need]
    return summary, gaps


def enrich_rivals_with_squads(rivals: list[dict[str, Any]], *, max_rivals: int = 10) -> list[dict[str, Any]]:
    """GET /users/... por rival y rellena squad / gaps / key_players (fail-soft)."""
    out: list[dict[str, Any]] = []
    for rival in rivals[:max_rivals]:
        row = dict(rival)
        path = rival.get("profile_path") or f"users/{rival.get('team_id')}"
        if not str(path).startswith("/"):
            path = "/" + str(path)
        try:
            html = fetch_html(path)
            squad = parse_user_squad(html)
            summary, gaps = _position_gaps_from_squad(squad)
            key = sorted(squad, key=lambda p: -float(p.get("price") or 0))[:5]
            row["squad"] = squad
            row["squad_summary"] = summary
            row["position_gaps"] = gaps
            row["key_players"] = [
                {"id": p["id"], "name": p["name"], "position": p["position"], "price": p.get("price")}
                for p in key
            ]
            dq = dict(row.get("data_quality") or {})
            dq["squad"] = "mister" if squad else "missing"
            row["data_quality"] = dq
            if squad and not row.get("squad_size"):
                row["squad_size"] = len(squad)
            log.info(
                "Rival %s squad=%s gaps=%s",
                row.get("team_name"),
                len(squad),
                gaps,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Plantilla rival %s falló: %s", path, exc)
        out.append(row)
    # rivales sin scrapear (si max)
    if len(rivals) > max_rivals:
        out.extend(rivals[max_rivals:])
    return out


def parse_free_agents_hint(html: str) -> list[dict[str, Any]]:
    """
    Intenta detectar jugadores libres en HTML (/players/0 o mercado).
    Solo incluye cards con señal explícita de libre / sin dueño.
    """
    if not html:
        return []
    players: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Ventana alrededor de cada card
    pattern = re.compile(
        r"teams/(\d+)\.png[\s\S]{0,250}?"
        r"data-position=['\"](\d)['\"][\s\S]{0,350}?"
        r"data-id_player=['\"](\d+)['\"][\s\S]{0,500}?"
        + _NAME_UNDER_RE,
        re.I,
    )
    for m in pattern.finditer(html):
        team_id, pos, pid, name_raw, under = m.groups()
        name = clean_player_name(name_raw)
        if not name or pid in seen:
            continue
        chunk = html[max(0, m.start() - 200) : m.end() + 200].lower()
        if not any(x in chunk for x in ("libre", "sin due", "sin dueño", "free agent", "owner\":0", "owner':0")):
            continue
        seen.add(pid)
        price = parse_euro_amount(under)
        players.append({
            "id": pid,
            "name": name,
            "position": _pos(pos),
            "team": team_label(team_id),
            "team_id": team_id,
            "price": price,
            "points": 0,
            "form": None,
            "injury": False,
            "trend": trend_from_arrow(under),
            "price_delta_5d": None,
            "seller": "free",
            "data_quality": {"price": "mister", "ownership": "mister_free_hint"},
        })
    return players


def fetch_free_agents_best_effort() -> tuple[list[dict[str, Any]], str]:
    """Best-effort libres. Devuelve (lista, note)."""
    try:
        html = fetch_html("/players/0")
        found = parse_free_agents_hint(html)
        if found:
            log.info("Libres detectados en /players/0: %s", len(found))
            return found, "mister_players0"
        # Fallback: mercado con etiqueta libre
        market_html = fetch_html("/market")
        found = parse_free_agents_hint(market_html)
        if found:
            return found, "mister_market_libre"
    except Exception as exc:  # noqa: BLE001
        log.warning("Libres best-effort falló: %s", exc)
    return [], "unavailable"


def _sw_players_form(offset: int = 0) -> dict[str, Any]:
    """
    Payload real de search-players.js → POST /ajax/sw/players.
    value_to alto evita filtrar el catálogo. No enviar clause_*: en ligas
    fixed/sin cláusulas (Premier) clause_to>0 deja players=[].
    """
    ceiling = int(getattr(config, "MISTER_POOL_VALUE_CEILING", 100_000_000))
    return {
        "post": "players",
        "offset": int(offset),
        "order": 0,
        "name": "",
        "filters[position]": 0,
        "filters[value_from]": 0,
        "filters[value_to]": ceiling,
        "filters[team]": 0,
        "filters[injured]": 0,
        "filters[favs]": 0,
        "filters[owner]": 0,
        "filters[benched]": 0,
        "filters[stealable]": 0,
    }


def normalize_sw_player(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normaliza un item de /ajax/sw/players al schema interno."""
    if not isinstance(raw, dict):
        return None
    pid = str(raw.get("id") or "").strip()
    name = clean_player_name(str(raw.get("name") or ""))
    if not pid or not name:
        return None
    team_id = str(raw.get("id_team") or "").strip()
    value = int(raw.get("value") or 0)
    id_uc = raw.get("id_uc")
    owner_id = None
    if id_uc not in (None, "", 0, "0", False):
        owner_id = str(id_uc)
    avg_raw = raw.get("avg")
    form: float | None
    try:
        form = float(avg_raw) if avg_raw is not None and avg_raw != "" else None
    except (TypeError, ValueError):
        form = None
    status = str(raw.get("status") or "").lower()
    injury = status in ("injured", "injured_out", "out", "doubtful") or "injur" in status
    clause_raw = raw.get("clause")
    try:
        clause = int(clause_raw) if clause_raw is not None else None
    except (TypeError, ValueError):
        clause = None
    is_free = owner_id is None
    photo_url = str(raw.get("photoUrl") or raw.get("photo_url") or "").strip()
    team_logo_url = str(raw.get("teamLogoUrl") or raw.get("team_logo_url") or "").strip()

    gw_points = parse_streak_values(raw.get("streak"))
    try:
        gw_points_sum = int(raw.get("streak_sum")) if raw.get("streak_sum") is not None else None
    except (TypeError, ValueError):
        gw_points_sum = None
    try:
        prev_value = int(raw.get("prev_value")) if raw.get("prev_value") is not None else None
    except (TypeError, ValueError):
        prev_value = None
    delta_1d = None
    if prev_value and value:
        delta_1d = round((value - prev_value) / float(prev_value), 6)
    try:
        clause_rank = int(raw.get("clausesRank")) if raw.get("clausesRank") is not None else None
    except (TypeError, ValueError):
        clause_rank = None

    match_info = raw.get("match_info") if isinstance(raw.get("match_info"), dict) else {}
    rival_id = match_info.get("rival_team_id")
    next_opponent_id = str(rival_id) if rival_id not in (None, "", 0, "0") else None
    next_is_home = bool(match_info.get("is_home")) if match_info else None

    return {
        "id": pid,
        "name": name,
        "position": _pos(raw.get("position")),
        "team": team_label(team_id) if team_id else "—",
        "team_id": team_id or None,
        "photo_url": photo_url or mister_player_photo_url(pid),
        "team_logo_url": team_logo_url or mister_team_logo_url(team_id),
        "price": value,
        "market_value": value,
        "points": int(raw.get("points") or 0),
        "form": form,
        "mister_avg": form,
        "injury": injury,
        "trend": ("up" if (delta_1d or 0) > 0 else "down" if (delta_1d or 0) < 0 else None),
        "price_delta_5d": None,
        "price_delta_1d": delta_1d,
        "prev_market_value": prev_value,
        "recent_gw_points": gw_points,
        "gw_points_sum": gw_points_sum,
        "points_trend": points_trend_from_gw(gw_points),
        "next_opponent_team_id": next_opponent_id,
        "next_is_home": next_is_home,
        "clause_rank": clause_rank,
        "seller": "free" if is_free else "owned",
        "owner_id": owner_id,
        "owner_name": (str(raw.get("uc_name")).strip() if raw.get("uc_name") else None),
        "clause": clause,
        "clause_known": clause is not None,
        "is_mine": flag_is_true(raw.get("is_mine")),
        "id_market": raw.get("id_market"),
        "min_bid": value if is_free else None,
        "data_quality": {
            "price": "mister",
            "ownership": "mister_sw_players",
            "clause": "mister" if clause is not None else "missing",
            "gw_points": "mister_sw_players" if gw_points else "missing",
        },
    }


def fetch_full_player_pool() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Catálogo completo de la competición vía /ajax/sw/players (paginado de a 50).
    Devuelve (jugadores normalizados, meta).
    """
    page_size = int(getattr(config, "MISTER_POOL_PAGE_SIZE", 50))
    max_offset = int(getattr(config, "MISTER_POOL_MAX_OFFSET", 2000))
    by_id: dict[str, dict[str, Any]] = {}
    offset = 0
    pages = 0
    last_batch = 0
    try:
        while offset <= max_offset:
            raw = ajax_post("/ajax/sw/players", _sw_players_form(offset))
            data = raw.get("data") if isinstance(raw, dict) else None
            batch = (data or {}).get("players") if isinstance(data, dict) else None
            if not isinstance(batch, list) or not batch:
                break
            pages += 1
            last_batch = len(batch)
            for item in batch:
                norm = normalize_sw_player(item)
                if norm:
                    by_id[norm["id"]] = norm
            if len(batch) < page_size:
                break
            offset += page_size
    except Exception as exc:  # noqa: BLE001
        log.warning("Pool /ajax/sw/players falló en offset=%s: %s", offset, exc)
        if not by_id:
            return [], {"source": "unavailable", "error": str(exc)}

    players = list(by_id.values())
    free_n = sum(1 for p in players if not p.get("owner_id"))
    owned_n = len(players) - free_n
    meta = {
        "source": "mister_sw_players",
        "pool_size": len(players),
        "free_count": free_n,
        "owned_count": owned_n,
        "pages": pages,
        "last_batch": last_batch,
    }
    log.info(
        "Pool Mister sw/players: %s jugadores (libres=%s owned=%s pages=%s)",
        len(players),
        free_n,
        owned_n,
        pages,
    )
    return players, meta


def mister_player_slug(name: str) -> str:
    """Slug aproximado para POST /ajax/sw/players (id basta; el slug ayuda)."""
    nk = unicodedata.normalize("NFKD", name or "")
    ascii_name = "".join(c for c in nk if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug


def fetch_player_sw_profile(player_id: str | int, slug: str = "") -> dict[str, Any] | None:
    """POST /ajax/sw/players con id — ficha (owners, values_chart, transfer)."""
    try:
        raw = ajax_post(
            "/ajax/sw/players",
            {
                "post": "players",
                "id": str(player_id),
                "slug": slug or "",
                "comments": 0,
            },
            timeout=25,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("ficha jugador id=%s falló: %s", player_id, exc)
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def _profile_cache_dir(community_id: str | None) -> Any:
    cid = str(community_id or "default").strip() or "default"
    path = config.ROOT_DIR / "cache" / "player_profiles" / cid
    path.mkdir(parents=True, exist_ok=True)
    return path


def fetch_player_profiles(
    pool: list[dict[str, Any]],
    extra_ids: list[str] | None = None,
    *,
    community_id: str | None = None,
    max_lookups: int | None = None,
    include_free: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Fichas ajax/sw/players. Bootstrap: include_free=True (toda la liga).
    Cachea por id: si el owner (o 'libre') no cambió, no refetch.
    """
    from rival_finances import parse_player_profile, profile_from_jsonable, profile_to_jsonable

    cap = int(
        max_lookups
        if max_lookups is not None
        else getattr(config, "MISTER_PROFILE_BOOTSTRAP_MAX", None)
        or getattr(config, "MISTER_PROFILE_MAX", 600)
    )
    cache_dir = _profile_cache_dir(community_id)
    wanted: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for p in pool or []:
        pid = str(p.get("id") or "").strip()
        oid = str(p.get("owner_id") or "").strip()
        if not pid or pid in seen:
            continue
        if not include_free and not oid:
            continue
        seen.add(pid)
        wanted.append((pid, mister_player_slug(str(p.get("name") or "")), oid))
    for pid in extra_ids or []:
        sid = str(pid or "").strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        wanted.append((sid, "", ""))
    wanted = wanted[: max(0, cap)]

    profiles: list[dict[str, Any]] = []
    fetched = 0
    cached_n = 0
    for pid, slug, owner_id in wanted:
        cache_path = cache_dir / f"{pid}.json"
        cached_blob: dict[str, Any] | None = None
        if cache_path.exists():
            try:
                cached_blob = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached_blob = None
        cached_prof = profile_from_jsonable(
            cached_blob.get("profile") if isinstance(cached_blob, dict) else None
        )
        cached_owner = str((cached_blob or {}).get("owner_id") or "")
        # Libre: owner_id vacío; si sigue libre, no hace falta volver a pedir la ficha.
        if cached_prof and cached_owner == owner_id:
            profiles.append(cached_prof)
            cached_n += 1
            continue
        raw = fetch_player_sw_profile(pid, slug)
        fetched += 1
        parsed = parse_player_profile(raw) if raw else None
        if not parsed:
            if cached_prof:
                profiles.append(cached_prof)
                cached_n += 1
            continue
        profiles.append(parsed)
        try:
            cache_path.write_text(
                json.dumps(
                    {
                        "owner_id": parsed.get("owner_id") or owner_id,
                        "profile": profile_to_jsonable(parsed),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("no se pudo cachear ficha %s: %s", pid, exc)
    meta = {
        "wanted": len(wanted),
        "profiles": len(profiles),
        "fetched": fetched,
        "cached": cached_n,
    }
    log.info(
        "Fichas jugador: %s (fetch=%s cache=%s cap=%s free=%s)",
        meta["profiles"],
        fetched,
        cached_n,
        cap,
        include_free,
    )
    return profiles, meta


def fetch_owned_player_profiles(
    pool: list[dict[str, Any]],
    extra_ids: list[str] | None = None,
    *,
    community_id: str | None = None,
    max_lookups: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Compat: solo jugadores con dueño."""
    return fetch_player_profiles(
        pool,
        extra_ids=extra_ids,
        community_id=community_id,
        max_lookups=max_lookups,
        include_free=False,
    )


#  Campos que solo trae el pool AJAX (el HTML de /market y /team no los pinta)
POOL_ONLY_FIELDS = (
    "recent_gw_points",
    "gw_points_sum",
    "prev_market_value",
    "price_delta_1d",
    "next_opponent_team_id",
    "next_is_home",
    "clause_rank",
)


def flag_is_true(value: Any) -> bool:
    """Mister manda is_mine como 0/1; un str '0' no debe contar como mío."""
    if value is True or value == 1:
        return True
    if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes"}:
        return True
    return False


def _owner_id(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    owner = row.get("owner_id")
    if owner in (None, "", 0, "0", False):
        return ""
    return str(owner)


def player_is_mine(row: dict[str, Any] | None, my_uc: str | None) -> bool:
    if not row:
        return False
    my = str(my_uc or "").strip()
    if flag_is_true(row.get("is_mine")):
        return True
    owner = _owner_id(row)
    return bool(my) and owner == my


def squad_player_ids(players: list[dict[str, Any]] | None) -> set[str]:
    out: set[str] = set()
    for p in players or []:
        pid = str(p.get("id") or p.get("player_id") or "").strip()
        if pid:
            out.add(pid)
    return out


def _pool_row_as_squad(src: dict[str, Any]) -> dict[str, Any]:
    row = dict(src)
    row["in_lineup"] = False
    row["from_lineup_only"] = False
    row["seller"] = "owned"
    return row


def reconcile_squad_with_pool(
    squad: list[dict[str, Any]],
    pool: list[dict[str, Any]],
    my_uc: str | None,
    *,
    foreign_ids: set[str] | None = None,
    roster_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """
    El HTML de /team deja vendidos en el once guardado (lineup-player) y a
    veces también en la lista lateral. El pool /ajax/sw/players es la
    autoridad de ownership (id_uc / is_mine). El perfil público /users/me y
    las plantillas rivales confirman ventas que el pool aún no ha movido.
    """
    squad = list(squad or [])
    by_id = {str(p.get("id")): p for p in (pool or []) if p.get("id")}
    my = str(my_uc or "").strip()
    foreign = {str(x).strip() for x in (foreign_ids or set()) if str(x).strip()}
    roster = {str(x).strip() for x in (roster_ids or set()) if str(x).strip()}
    roster_ok = len(roster) >= 8
    mine_from_pool = {
        pid for pid, src in by_id.items() if player_is_mine(src, my)
    }

    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    kept_ids: set[str] = set()
    added_n = 0

    for p in squad:
        pid = str(p.get("id") or "").strip()
        name = str(p.get("name") or pid)
        if not pid:
            kept.append(p)
            continue
        if roster_ok and pid not in roster:
            dropped.append(name)
            continue
        if pid in foreign:
            dropped.append(name)
            continue
        src = by_id.get(pid)
        if src is not None:
            if player_is_mine(src, my):
                kept.append(p)
                kept_ids.add(pid)
                continue
            dropped.append(name)
            continue
        ghost_xi = bool(p.get("from_lineup_only"))
        if ghost_xi and len(mine_from_pool) >= 11:
            dropped.append(name)
            continue
        kept.append(p)
        kept_ids.add(pid)

    for pid, src in by_id.items():
        if pid in kept_ids or not player_is_mine(src, my):
            continue
        if roster_ok and pid not in roster:
            continue
        if pid in foreign:
            continue
        kept.append(_pool_row_as_squad(src))
        kept_ids.add(pid)
        added_n += 1

    if dropped:
        log.info(
            "Plantilla: fuera %s vendido(s)/once fantasma: %s",
            len(dropped),
            ", ".join(dropped),
        )
    if added_n:
        log.info("Plantilla: +%s del pool (fichaje aún no pintado en /team)", added_n)
    return kept


def apply_pool_fields_to_players(
    players: list[dict[str, Any]],
    pool: list[dict[str, Any]] | dict[str, dict[str, Any]],
) -> int:
    """
    Completa in-place jugadores parseados del HTML con los campos ricos del pool
    (/ajax/sw/players): puntos por jornada, valor previo, rival de la próxima.
    """
    if not players or not pool:
        return 0
    by_id = pool if isinstance(pool, dict) else {str(p.get("id")): p for p in pool}
    filled = 0
    for p in players:
        src = by_id.get(str(p.get("id")))
        if not src:
            continue
        touched = False
        for key in POOL_ONLY_FIELDS:
            val = src.get(key)
            if val in (None, [], {}) or p.get(key) not in (None, [], {}):
                continue
            p[key] = val
            touched = True
        if src.get("recent_gw_points") and p.get("points_trend") in (None, "unknown"):
            p["points_trend"] = points_trend_from_gw(src["recent_gw_points"])
        if p.get("trend") is None and src.get("trend"):
            p["trend"] = src["trend"]
        if touched:
            dq = dict(p.get("data_quality") or {})
            dq["gw_points"] = "mister_sw_players"
            p["data_quality"] = dq
            filled += 1
    return filled


def apply_pool_squads_to_rivals(
    rivals: list[dict[str, Any]],
    pool: list[dict[str, Any]],
    my_uc: str | None,
) -> list[dict[str, Any]]:
    """Rellena plantillas rivales desde el pool (ownership id_uc), más completo que HTML."""
    if not pool or not rivals:
        return rivals
    by_owner: dict[str, list[dict[str, Any]]] = {}
    my = str(my_uc or "")
    for p in pool:
        oid = p.get("owner_id")
        if not oid or str(oid) == my:
            continue
        row = dict(p)
        row["seller"] = "rival"
        by_owner.setdefault(str(oid), []).append(row)

    out: list[dict[str, Any]] = []
    for r in rivals:
        row = dict(r)
        uc = str(row.get("team_id") or "")
        pool_squad = by_owner.get(uc) or []
        html_squad = list(row.get("squad") or [])
        if pool_squad and len(pool_squad) >= max(1, len(html_squad)):
            row["squad"] = pool_squad
            row["squad_size"] = len(pool_squad)
            dq = dict(row.get("data_quality") or {})
            dq["squad"] = "mister_sw_players"
            row["data_quality"] = dq
        out.append(row)
    return out


def fetch_gameweek_bundle(
    feed_html: str,
    *,
    id_competition: Any = None,
    competition: str | None = None,
) -> dict[str, Any]:
    """
    Jornada + calendario desde Mister (autoridad de fechas, rival y puntos).
    Fail-soft: si el AJAX cae, se usan los fixtures del propio HTML del feed.
    """
    gw_id = mister_gameweek.parse_feed_gameweek_id(feed_html)
    bundle: dict[str, Any] = {
        "gameweek_id": gw_id,
        "matchday": {"status": "unavailable", "source": "mister", "fixtures": []},
        "schedule": [],
        "preview": {},
        "points": {},
        "my_lineup": {},
        "table": {},
        "team_schedule": {},
        "played_opponents": {},
        "status": "unavailable",
    }
    if not gw_id:
        log.info("Feed sin bloque de jornada; sin datos de gameweek Mister")
        return bundle

    gw_data = mister_gameweek.fetch_gameweek(ajax_post, gw_id)
    if gw_data:
        bundle["matchday"] = mister_gameweek.build_matchday(
            gw_data, team_label=team_label, competition=competition
        )
        bundle["schedule"] = mister_gameweek.gameweek_schedule(gw_data)
        bundle["preview"] = mister_gameweek.extract_preview(gw_data)
        bundle["preview_teams"] = sorted(mister_gameweek.preview_coverage(gw_data))
        bundle["points"] = mister_gameweek.extract_gw_points(gw_data)
        bundle["my_lineup"] = mister_gameweek.extract_my_lineup(gw_data)
        bundle["status"] = "ok"
    else:
        fixtures = mister_gameweek.parse_feed_fixtures(feed_html)
        if fixtures:
            bundle["matchday"] = {
                "status": "partial",
                "source": "mister_feed_html",
                "gameweek_id": gw_id,
                "competition": competition,
                "fixtures_count": len(fixtures),
                "fixtures": [
                    {
                        "id": fx["id"],
                        "home": team_label(fx["home_id"]),
                        "away": team_label(fx["away_id"]),
                        "home_id": fx["home_id"],
                        "away_id": fx["away_id"],
                        "kickoff": mister_gameweek._iso_from_ts(fx["kickoff_ts"]),
                        "kickoff_ts": fx["kickoff_ts"],
                        "status": fx.get("status"),
                    }
                    for fx in fixtures
                ],
            }
            bundle["status"] = "partial"

    comp = mister_gameweek.fetch_competition(ajax_post, id_competition)
    if comp:
        bundle["table"] = mister_gameweek.build_standings_table(comp)
        jornada = mister_gameweek.coerce_jornada(
            (bundle.get("matchday") or {}).get("jornada")
        )
        bundle["team_schedule"] = mister_gameweek.build_team_schedule(
            comp, from_jornada=jornada
        )
        scoring_j = mister_gameweek.resolve_scoring_jornada(bundle["team_schedule"])
        if isinstance(bundle.get("matchday"), dict) and scoring_j is not None:
            bundle["matchday"]["scoring_jornada"] = scoring_j
        bundle["played_opponents"] = mister_gameweek.build_played_opponents(
            comp, before_jornada=jornada
        )
        bundle["played_fixtures"] = mister_gameweek.build_played_fixtures(
            comp, before_jornada=jornada
        )
    return bundle


def apply_gameweek_to_players(
    players: list[dict[str, Any]],
    bundle: dict[str, Any],
    *,
    now: datetime | None = None,
) -> int:
    """
    Vuelca en los jugadores las señales de jornada de Mister:
    once probable (`preview`), puntos reales y rival de esta GW (`gw_*`).
    El `next_*` es el rival de la jornada de scoring (primer kickoff no pitado
    de la liga), no el próximo partido cronológico de cada equipo.
    """
    preview = bundle.get("preview") or {}
    points = bundle.get("points") or {}
    schedule = bundle.get("team_schedule") if isinstance(bundle.get("team_schedule"), dict) else {}
    matchday = bundle.get("matchday") if isinstance(bundle.get("matchday"), dict) else {}
    current_j = matchday.get("jornada")
    scoring_j = mister_gameweek.resolve_scoring_jornada(schedule, now=now)
    if scoring_j is None:
        scoring_j = mister_gameweek.coerce_jornada(matchday.get("scoring_jornada"))
    if scoring_j is not None:
        matchday["scoring_jornada"] = scoring_j
    preview_teams = set(bundle.get("preview_teams") or [])
    touched = 0
    seen: set[int] = set()
    for p in players:
        if id(p) in seen:
            continue
        seen.add(id(p))
        pid = str(p.get("id") or p.get("player_id") or "")
        if not pid:
            continue
        pv = preview.get(pid)
        pts = points.get(pid)
        has_preview_of_his_match = str(p.get("team_id") or "") in preview_teams
        if pv:
            p["gw_probable_xi"] = True
            p["gw_confirmed"] = bool(pv.get("gw_confirmed"))
            p["gw_fixture_id"] = pv.get("gw_fixture_id")
            p["gw_kickoff"] = pv.get("gw_kickoff")
            if pv.get("gw_kickoff_ts") is not None:
                p["gw_kickoff_ts"] = pv.get("gw_kickoff_ts")
            p["gw_is_home"] = pv.get("gw_is_home")
            opp_id = pv.get("gw_opponent_id")
            if opp_id:
                p["gw_opponent_id"] = opp_id
                if not p.get("gw_opponent"):
                    p["gw_opponent"] = team_label(opp_id)
        elif has_preview_of_his_match:
            # Hay previa de su partido y no aparece: suplencia real, no falta de dato.
            p["gw_probable_xi"] = False
        if pts:
            p["gw_points"] = pts.get("points")
            p["gw_played"] = bool(pts.get("played"))
            p["gw_match_status"] = pts.get("status")
        if pv or pts or has_preview_of_his_match or p.get("team_id"):
            _stamp_next_fixture(
                p,
                schedule,
                current_jornada=current_j,
                scoring_jornada=scoring_j,
                now=now,
            )
            touched += 1
    return touched


def _clear_next_fixture(player: dict[str, Any]) -> None:
    player["next_opponent_team_id"] = None
    player["next_is_home"] = None
    player["next_jornada"] = None
    player["next_kickoff"] = None
    player.pop("next_kickoff_ts", None)


def _stamp_blank_for_scoring(player: dict[str, Any]) -> None:
    """Sin partido en la jornada de scoring: no puntúa; no usar rival de otra GW."""
    _clear_next_fixture(player)
    player["gw_blank"] = True
    player["gw_out"] = True
    player["gw_probable_xi"] = False
    ext = player.get("external")
    if isinstance(ext, dict):
        ext["gw_blank"] = True
        ext["gw_out"] = True
        ext["gw_starter"] = False


def _stamp_next_fixture(
    player: dict[str, Any],
    schedule: dict[str, list[dict[str, Any]]],
    *,
    current_jornada: Any = None,
    scoring_jornada: Any = None,
    now: datetime | None = None,
) -> None:
    """Escribe next_* del rival de la jornada de scoring. No pisa gw_*."""
    tid = str(player.get("team_id") or "")
    rows = list(schedule.get(tid) or []) if tid else []
    gw_row: dict[str, Any] | None = None
    if player.get("gw_opponent_id") or player.get("gw_kickoff") or player.get("gw_kickoff_ts"):
        gw_row = {
            "jornada": current_jornada,
            "opponent_id": player.get("gw_opponent_id"),
            "is_home": player.get("gw_is_home"),
            "kickoff": player.get("gw_kickoff"),
            "kickoff_ts": player.get("gw_kickoff_ts"),
            "status": "played" if player.get("gw_played") else player.get("gw_match_status"),
        }
        if not player.get("gw_played") and mister_gameweek.fixture_is_unplayed(gw_row, now=now):
            rows = [gw_row, *rows]
    target_j = mister_gameweek.coerce_jornada(scoring_jornada)
    if target_j is not None:
        nxt = mister_gameweek.fixture_for_jornada(rows, target_j, now=now)
    else:
        nxt = mister_gameweek.next_unplayed_fixture(rows, now=now)
    if nxt and nxt.get("opponent_id"):
        player["next_opponent_team_id"] = str(nxt["opponent_id"])
        player["next_is_home"] = nxt.get("is_home")
        player["next_jornada"] = nxt.get("jornada")
        player["next_kickoff"] = nxt.get("kickoff")
        if nxt.get("kickoff_ts") is not None:
            player["next_kickoff_ts"] = nxt.get("kickoff_ts")
        if player.get("gw_blank"):
            player["gw_blank"] = False
            player["gw_out"] = False
        ext = player.get("external")
        if isinstance(ext, dict) and ext.get("gw_blank"):
            ext["gw_blank"] = False
            ext["gw_out"] = False
        return
    if target_j is not None:
        _stamp_blank_for_scoring(player)
        return
    if player.get("gw_played"):
        _clear_next_fixture(player)


def fetch_balance() -> dict[str, Any]:
    try:
        data = ajax_post("/ajax/balance")
        if isinstance(data, dict) and data.get("status") == "ok":
            return data.get("data") or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("ajax/balance falló: %s", exc)
    return {}


def discover_leagues() -> list[dict[str, Any]]:
    """
    Lista comunidades a las que la cuenta está inscrita vía `_FG_user.communities`.
    Fail-soft: [] si no hay auth o el HTML no trae communities.
    """
    if not (config.MISTER_TOKEN or config.MISTER_COOKIE):
        return []
    html = ""
    for path in ("/team", "/feed", "/market"):
        try:
            html = fetch_html(path)
            refresh_x_auth_from_html(html)
            if html:
                break
        except Exception as exc:  # noqa: BLE001
            log.warning("discover_leagues GET %s falló: %s", path, exc)
    if not html:
        return []
    fg_user = _extract_js_object(html, "_FG_user") or {}
    communities = fg_user.get("communities")
    out: list[dict[str, Any]] = []
    if isinstance(communities, dict):
        items = list(communities.values())
    elif isinstance(communities, list):
        items = communities
    else:
        items = []

    # Si no hay mapa communities, al menos la comunidad activa
    if not items and fg_user.get("id_community"):
        items = [
            {
                "id": fg_user.get("id_community"),
                "name": fg_user.get("community"),
                "id_competition": fg_user.get("id_competition"),
                "mode": fg_user.get("mode"),
                "code": fg_user.get("code"),
            }
        ]

    active_cid = str(fg_user.get("id_community") or "")
    for raw in items:
        if not isinstance(raw, dict):
            continue
        cid = str(raw.get("id") or raw.get("id_community") or "").strip()
        if not cid:
            continue
        try:
            id_comp = int(raw["id_competition"]) if raw.get("id_competition") is not None else None
        except (TypeError, ValueError):
            id_comp = None
        # Normas de la comunidad activa vienen en el FG_user raíz
        row: dict[str, Any] = {
            "id_community": cid,
            "name": raw.get("name") or raw.get("community") or f"Liga {cid}",
            "id_competition": id_comp,
            "mode": raw.get("mode"),
            "code": raw.get("code"),
            "direct_transfer": raw.get("direct_transfer"),
            "balance": raw.get("balance"),
        }
        if cid == active_cid:
            row["type"] = fg_user.get("type")
            row["provider"] = fg_user.get("provider")
            row["team_limit"] = fg_user.get("team_limit")
            row["max_squad"] = fg_user.get("team_limit")
            row["clauses"] = fg_user.get("clauses")
            row["loans"] = fg_user.get("loans")
            row["market_speed"] = fg_user.get("market_speed")
            row["competition"] = fg_user.get("competition")
            # Inferencia temprana de market_mode
            try:
                from league_rules import infer_market_mode

                row["market_mode"] = infer_market_mode(
                    league_type=str(fg_user.get("type") or ""),
                    mode=str(fg_user.get("mode") or raw.get("mode") or ""),
                    direct_transfer=raw.get("direct_transfer"),
                )
            except Exception:  # noqa: BLE001
                pass
        out.append(row)

    log.info("discover_leagues: %s comunidades", len(out))
    return out


def fetch_admin_settings() -> dict[str, Any] | None:
    """
    POST /ajax/sw/admin — panel de normas (/feed#admin).
    Fail-soft: None si no eres admin o el endpoint falla.
    """
    try:
        raw = ajax_post("/ajax/sw/admin", {"post": "admin"}, timeout=20)
    except Exception as exc:  # noqa: BLE001
        log.info("ajax/sw/admin no disponible: %s", exc)
        return None
    if not isinstance(raw, dict):
        return None
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    if not isinstance(data, dict) or not data:
        log.info("ajax/sw/admin sin data usable")
        return None
    log.info("ajax/sw/admin OK keys=%s", list(data.keys())[:12])
    return data


def fg_user_rules_snapshot(fg_user: dict[str, Any] | None) -> dict[str, Any]:
    """Campos de normas presentes en `_FG_user` (para league_rules.normalize_rules)."""
    if not isinstance(fg_user, dict):
        return {}
    keys = (
        "provider",
        "team_limit",
        "type",
        "mode",
        "clauses",
        "loans",
        "loans_floor",
        "market_speed",
        "market_stay",
        "salaries",
        "live_changes",
        "show_balances",
        "custom_rules",
        "rewards",
        "prizes",
        "sale_limit",
        "max_debt",
        "admin",
        "purchase_clauses",
        "purchase_rescind",
        "id_competition",
        "competition",
        "id_community",
        "community",
    )
    return {k: fg_user[k] for k in keys if k in fg_user}


def fg_cfg_snapshot(fg_cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Flags de `_FG_cfg` que el motor sí usa (capitán, ventana de mercado)."""
    if not isinstance(fg_cfg, dict):
        return {}
    keys = (
        "season",
        "market_date",
        "market_lock",
        "id_competition",
        "provider",
        "FEATURE_CAPTAIN_ENABLED",
        "LEAGUE_CAPTAIN_ENABLED",
        "CAPTAIN_MULTIPLIER",
    )
    return {k: fg_cfg[k] for k in keys if k in fg_cfg}


def switch_community(id_community: str | int) -> dict[str, Any]:
    """
    Cambia la comunidad activa de la sesión Mister.
    GET /action/change?id_community=… → parsea _FG_user.
    """
    cid = str(id_community).strip()
    if not cid:
        return {}
    url = f"{config.MISTER_API_BASE}/action/change?id_community={cid}"
    try:
        resp = requests.get(url, headers=mister_headers(referer=url), timeout=30, allow_redirects=True)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        html = resp.text
    except Exception as exc:  # noqa: BLE001
        log.warning("switch_community(%s) falló: %s", cid, exc)
        return {}

    # Auth AJAX rota con la comunidad; sin esto /ajax/* → 401 tras el switch.
    refresh_x_auth_from_html(html)
    fg_user = _extract_js_object(html, "_FG_user") or {}
    got = str(fg_user.get("id_community") or "")
    if got and got != cid:
        log.warning("switch_community: pedí %s, sesión quedó en %s", cid, got)
    elif got == cid:
        log.info(
            "switch_community OK → %s (%s) uc=%s",
            fg_user.get("community"),
            cid,
            fg_user.get("id_uc"),
        )
    return fg_user


def fetch_live_league(community_id: str | int | None = None) -> dict[str, Any] | None:
    """Orquesta balance + HTML market/team/standings → schema interno (solo datos reales)."""
    if not (config.MISTER_TOKEN or config.MISTER_COOKIE):
        return None

    target_cid = str(community_id or config.MISTER_LEAGUE_ID or "").strip() or None
    if target_cid:
        # Peek sesión actual vía /team (ligero) o switch directo
        try:
            peek = fetch_html("/team")
            refresh_x_auth_from_html(peek)
            cur = _extract_js_object(peek, "_FG_user") or {}
            cur_cid = str(cur.get("id_community") or "")
            if cur_cid != target_cid:
                switched = switch_community(target_cid)
                if not switched or str(switched.get("id_community") or "") != target_cid:
                    log.warning("No se pudo activar comunidad %s", target_cid)
        except Exception as exc:  # noqa: BLE001
            log.warning("peek/switch comunidad falló: %s — intento switch directo", exc)
            switch_community(target_cid)

    balance_data = fetch_balance()
    market_html = team_html = standings_html = feed_html = ""
    try:
        market_html = fetch_html("/market")
    except Exception as exc:  # noqa: BLE001
        log.warning("GET /market falló: %s", exc)
    try:
        team_html = fetch_html("/team")
    except Exception as exc:  # noqa: BLE001
        log.warning("GET /team falló: %s", exc)
    try:
        standings_html = fetch_html("/standings")
    except Exception as exc:  # noqa: BLE001
        log.warning("GET /standings falló: %s", exc)
    try:
        feed_html = fetch_html("/feed")
    except Exception as exc:  # noqa: BLE001
        log.warning("GET /feed falló: %s", exc)

    if not market_html and not team_html and not balance_data:
        return None

    fg_user = _extract_js_object(market_html or team_html, "_FG_user") or {}
    fg_cfg = _extract_js_object(market_html or team_html, "_FG_cfg") or {}
    # Asegurar auth fresco antes del pool /ajax/sw/players (crítico multi-liga).
    apply_x_auth_from_cfg(fg_cfg)

    admin_data = fetch_admin_settings()
    rules_fg = fg_user_rules_snapshot(fg_user)
    rules_cfg = fg_cfg_snapshot(fg_cfg)
    auction_ends: list[float] = []
    try:
        from market_cycle import parse_auction_cycle_ends

        auction_ends = parse_auction_cycle_ends(market_html)
    except Exception:  # noqa: BLE001
        auction_ends = []

    bal_src: dict[str, Any] = {}
    if isinstance(balance_data, dict) and balance_data:
        bal_src = balance_data
    elif isinstance(fg_user.get("balance"), dict):
        bal_src = fg_user["balance"]

    def _bal_int(key: str) -> int | None:
        raw = bal_src.get(key)
        if raw is None:
            return None
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return None

    bal = int(_bal_int("current") or 0)
    bal_future = _bal_int("future")
    max_debt = _bal_int("maxDebt")

    market = parse_market_players(market_html) if market_html else []
    squad = parse_team_players(team_html) if team_html else []

    # Corregir clubes (mapa CDN cambia por temporada; p.ej. 6 = Deportivo)
    try:
        n_team = refresh_team_labels(list(squad) + list(market), max_lookups=18)
        log.info("Team labels refresh lookups=%s", n_team)
    except Exception as exc:  # noqa: BLE001
        log.warning("refresh_team_labels falló: %s", exc)

    market_by_id = {p["id"]: p for p in market}
    for p in squad:
        if not p.get("price") and p["id"] in market_by_id:
            p["price"] = market_by_id[p["id"]]["price"]
            dq = dict(p.get("data_quality") or {})
            dq["price"] = "mister"
            p["data_quality"] = dq

    # Fail-soft: precios aún faltantes vía AJAX player-community-info
    missing_price = [p for p in squad if not p.get("price")]
    if missing_price:
        filled, _ = enrich_players_with_clauses(missing_price, max_lookups=min(20, len(missing_price)))
        by_id = {str(x.get("id")): x for x in filled}
        for p in squad:
            hit = by_id.get(str(p.get("id")))
            if not hit:
                continue
            if not p.get("price") and hit.get("price"):
                p["price"] = hit["price"]
                p["market_value"] = hit.get("market_value") or hit.get("price")
                dq = dict(p.get("data_quality") or {})
                dq["price"] = "mister_ajax"
                p["data_quality"] = dq
            if hit.get("mister_avg") is not None and p.get("mister_avg") is None:
                p["mister_avg"] = hit["mister_avg"]
                p["form"] = hit["mister_avg"]
            if hit.get("clause_known"):
                p["clause"] = hit.get("clause")
                p["clause_known"] = True

    if not squad and not market:
        log.warning("HTML sin jugadores parseables")
        return None

    my_uc = str(fg_user.get("id_uc") or config.MISTER_TEAM_ID or "")
    try:
        from sales_state import build_sales_state, tag_own_market_listings

        market = tag_own_market_listings(market, my_uc)
    except Exception as exc:  # noqa: BLE001
        log.warning("tag_own_market_listings falló: %s", exc)

    offers_parsed: dict[str, Any] = {}
    try:
        offers_parsed = fetch_offers_received()
    except Exception as exc:  # noqa: BLE001
        log.warning("fetch_offers_received falló: %s", exc)
        offers_parsed = {}

    try:
        from sales_state import build_sales_state

        sales_state = build_sales_state(
            market=market,
            offers_payload=offers_parsed,
            squad=squad,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("build_sales_state falló: %s", exc)
        sales_state = {
            "listed": [],
            "listed_ids": [],
            "listed_count": 0,
            "offers_received": [],
            "pending_offers": [],
            "pending_count": 0,
            "count": {"total": 0, "pending": 0},
            "mister_offers_url": "https://mister.mundodeportivo.com/market#market/offers-received",
        }
    log.info(
        "sales_state listed=%s pending_offers=%s",
        sales_state.get("listed_count"),
        sales_state.get("pending_count"),
    )

    rivals, me_row = parse_standings(standings_html, my_uc) if standings_html else ([], None)
    if rivals:
        rivals = enrich_rivals_with_squads(rivals)
    # IDs rivales del HTML /users (antes de que el pool pise plantillas).
    # Si vendí a un rival, su perfil ya lo tiene aunque /team conserve el XI.
    html_foreign_ids: set[str] = set()
    for rival in rivals:
        html_foreign_ids |= squad_player_ids(rival.get("squad"))

    own_roster: list[dict[str, Any]] = []
    profile_path = (me_row or {}).get("profile_path") or (f"users/{my_uc}" if my_uc else "")
    if profile_path:
        path = str(profile_path)
        if not path.startswith("/"):
            path = "/" + path
        try:
            own_roster = parse_user_squad(fetch_html(path))
            log.info("Perfil propio /users → %s jugadores", len(own_roster))
        except Exception as exc:  # noqa: BLE001
            log.warning("Perfil propio %s falló: %s", path, exc)
            own_roster = []

    # Catálogo completo (~500): libres + ownership real por id_uc
    full_pool, pool_meta = fetch_full_player_pool()
    free_pool: list[dict[str, Any]] = []
    free_note = "unavailable"
    pool_fields_filled = 0
    market_foreign_ids = {
        str(p.get("id"))
        for p in market
        if p.get("id")
        and (
            p.get("listed_by_rival")
            or (
                _owner_id(p)
                and _owner_id(p) != str(my_uc or "")
            )
        )
    }
    if full_pool:
        free_pool = [p for p in full_pool if not p.get("owner_id")]
        free_note = str(pool_meta.get("source") or "mister_sw_players")
        rivals = apply_pool_squads_to_rivals(rivals, full_pool, my_uc)
    else:
        free_pool, free_note = fetch_free_agents_best_effort()

    pool_foreign_ids: set[str] = set()
    for rival in rivals:
        pool_foreign_ids |= squad_player_ids(rival.get("squad"))
    foreign_ids = html_foreign_ids | pool_foreign_ids | market_foreign_ids
    prev_n = len(squad)
    squad = reconcile_squad_with_pool(
        squad,
        full_pool or [],
        my_uc,
        foreign_ids=foreign_ids,
        roster_ids=squad_player_ids(own_roster),
    )
    if len(squad) != prev_n:
        log.info("Plantilla reconciliada: %s → %s", prev_n, len(squad))

    if full_pool:
        pool_by_id = {str(p.get("id")): p for p in full_pool}
        pool_fields_filled = apply_pool_fields_to_players(squad, pool_by_id)
        pool_fields_filled += apply_pool_fields_to_players(market, pool_by_id)
        log.info("Campos de jornada desde pool: %s jugadores", pool_fields_filled)

    # Reaplicar on_sale tras reconcile (el pool no trae listados; puede haber altas)
    try:
        from sales_state import build_sales_state

        sales_state = build_sales_state(
            market=market,
            offers_payload=offers_parsed or sales_state,
            squad=squad,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("build_sales_state (post-reconcile) falló: %s", exc)

    gameweek = fetch_gameweek_bundle(
        feed_html,
        id_competition=fg_user.get("id_competition"),
        competition=str(fg_user.get("competition") or "") or None,
    )
    if gameweek.get("preview") or gameweek.get("points") or gameweek.get("team_schedule"):
        n_gw = apply_gameweek_to_players(
            list(squad) + list(market) + list(full_pool),
            gameweek,
        )
        log.info("Señales de jornada Mister aplicadas a %s jugadores", n_gw)
    # Blank GW aunque no haya previa: equipo sin partido → icono prohibido Mister
    try:
        from mister_gameweek import apply_blank_gameweek

        n_blank = apply_blank_gameweek(
            list(squad) + list(market) + list(full_pool),
            gameweek.get("matchday") if isinstance(gameweek.get("matchday"), dict) else None,
            team_schedule=gameweek.get("team_schedule")
            if isinstance(gameweek.get("team_schedule"), dict)
            else None,
        )
        if n_blank:
            log.info("Blank GW aplicado a %s jugadores", n_blank)
    except Exception as exc:  # noqa: BLE001
        log.warning("Blank GW no aplicado: %s", exc)

    squad_value = sum(int(p.get("price") or 0) for p in squad)
    # Mister muestra en /standings el valor oficial de plantilla; la suma HTML
    # suele quedar corta si faltan precios en el once.
    if me_row and me_row.get("squad_value"):
        squad_value = int(me_row["squad_value"])

    owned: set[str] = {p["id"] for p in squad} | {p["id"] for p in market}
    if full_pool:
        owned |= {p["id"] for p in full_pool if p.get("owner_id")}
    for r in rivals:
        for p in r.get("squad") or []:
            if p.get("id"):
                owned.add(str(p["id"]))
    community = fg_user.get("id_community") or config.MISTER_LEAGUE_ID or "mister-live"
    team_name = (me_row or {}).get("team_name") or fg_user.get("uc_name") or fg_user.get("name") or "Mi equipo"
    manager = fg_user.get("name") or "Yo"
    competition = str(fg_user.get("competition") or "")
    id_competition = fg_user.get("id_competition")
    try:
        id_competition_i = int(id_competition) if id_competition is not None else None
    except (TypeError, ValueError):
        id_competition_i = None

    finance_meta: dict[str, Any] = {}
    try:
        from datetime import date as date_cls

        from rival_finances import (
            load_finance_snapshot,
            parse_feed_ajax_cards,
            parse_feed_prizes,
            parse_feed_transfers,
            run_rival_finances,
            snapshot_needs_bootstrap,
            sorteo_date_from_ts,
            start_mode_for_league,
        )

        cid = str(community)
        ov = dict(config.LEAGUE_OVERRIDES.get(cid) or {})
        raw_sorteo = ov.get("sorteo_date")
        if isinstance(raw_sorteo, str) and raw_sorteo:
            try:
                y, m, d = (int(x) for x in raw_sorteo.split("-")[:3])
                sorteo = date_cls(y, m, d)
            except (TypeError, ValueError):
                sorteo = sorteo_date_from_ts(
                    fg_user.get("created_ts") or fg_user.get("uc_created")
                )
        else:
            sorteo = sorteo_date_from_ts(
                fg_user.get("created_ts") or fg_user.get("uc_created")
            )
        start_budget = float(
            ov.get("starting_budget")
            or getattr(config, "DEFAULT_STARTING_BUDGET", 50_000_000)
        )
        comms = fg_user.get("communities") if isinstance(fg_user.get("communities"), dict) else {}
        comm_row = comms.get(cid) if isinstance(comms.get(cid), dict) else {}
        debt_lvl = comm_row.get("max_debt") if comm_row else None
        if debt_lvl is None:
            debt_lvl = (admin_data or {}).get("max_debt") if isinstance(admin_data, dict) else None
        smode = start_mode_for_league(
            str(fg_user.get("type") or ""),
            str(fg_user.get("mode") or ""),
            str(ov.get("start_mode") or "") or None,
        )
        max_age = int(getattr(config, "MISTER_FINANCE_FEED_MAX_AGE_DAYS", 25))
        snap = load_finance_snapshot(cid)
        need_boot, boot_reason = snapshot_needs_bootstrap(
            snap,
            rivals=rivals,
            me_uc=my_uc or None,
            sorteo_date=sorteo,
            start_mode=smode,
            starting_budget=start_budget,
            max_age_days=max_age,
        )
        seen_ids = set(str(x) for x in (snap or {}).get("seen_ids") or [])
        seen_fps = set(str(x) for x in (snap or {}).get("seen_fps") or [])
        # Incremental: paramos al llegar a tarjetas ya aplicadas. Bootstrap: feed entero.
        feed_cards, feed_ajax_meta = fetch_feed_cards(
            stop_on_caught_up=not need_boot,
            seen_ids=seen_ids,
            seen_fps=seen_fps,
        )
        feed_tx, feed_pr = parse_feed_ajax_cards(feed_cards)
        if not feed_tx and not feed_pr:
            feed_tx = parse_feed_transfers(feed_html)
            feed_pr = parse_feed_prizes(feed_html)
            feed_ajax_meta["fallback"] = "html"
        extra_ids = [str(e.get("player_id") or "") for e in feed_tx if e.get("player_id")]
        profiles: list[dict[str, Any]] = []
        prof_meta: dict[str, Any] = {
            "wanted": 0,
            "profiles": 0,
            "fetched": 0,
            "cached": 0,
            "skipped": True,
        }
        if need_boot:
            cap = int(getattr(config, "MISTER_PROFILE_BOOTSTRAP_MAX", 600))
            log.info(
                "Finanzas rivales: bootstrap fichas (%s, cap=%s, pool=%s)",
                boot_reason,
                cap,
                len(full_pool or []),
            )
            profiles, prof_meta = fetch_player_profiles(
                full_pool or [],
                extra_ids=extra_ids,
                community_id=cid,
                max_lookups=cap,
                include_free=True,
            )
        else:
            log.info("Finanzas rivales: snapshot fresco, solo feed")
        rivals, finance_meta = run_rival_finances(
            community_id=cid,
            rivals=rivals,
            me_uc=my_uc or None,
            me_balance=float(bal),
            me_squad_value=float(squad_value or 0),
            me_name=str(
                (me_row or {}).get("manager")
                or fg_user.get("uc_name")
                or fg_user.get("name")
                or ""
            )
            or None,
            profiles=profiles,
            feed_transfers=feed_tx,
            feed_prizes=feed_pr,
            starting_budget=start_budget,
            sorteo_date=sorteo,
            start_mode=smode,
            max_debt_level=debt_lvl,
            snapshot=snap,
            persist=True,
            max_age_days=max_age,
        )
        finance_meta["profiles_meta"] = prof_meta
        finance_meta["feed_ajax"] = feed_ajax_meta
        log.info(
            "Finanzas rivales: mode=%s reason=%s profiles=%s ledger=%s prizes=%s me_est=%s me_err=%s",
            finance_meta.get("update_mode"),
            finance_meta.get("bootstrap_reason"),
            finance_meta.get("profiles"),
            finance_meta.get("ledger_events"),
            finance_meta.get("prizes_events"),
            finance_meta.get("me_estimated"),
            finance_meta.get("me_error"),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Finanzas rivales falló: %s", exc)
        finance_meta = {"source": "unavailable", "error": str(exc)}

    notes = [
        "Saldo: /ajax/balance",
        "Plantilla: HTML /team",
        "Mercado: HTML /market",
        "Clasificación/rivales: HTML /standings (valor plantilla, no liquidez)",
        "Plantillas rivales: HTML /users/{id}/… + pool /ajax/sw/players",
        "Sin PPG multi-temporada inventado",
        f"Comunidad activa: {fg_user.get('community')} ({community}) · {competition or '?'}",
    ]
    if full_pool:
        notes.append(
            f"Pool completo Mister (/ajax/sw/players): {pool_meta.get('pool_size')} "
            f"(libres={pool_meta.get('free_count')}, owned={pool_meta.get('owned_count')})"
        )
        notes.append(
            "Puntos por jornada / valor previo / rival: pool Mister "
            f"({pool_fields_filled} jugadores completados)"
        )
    elif free_pool:
        notes.append(f"Libres detectados vía {free_note}: {len(free_pool)}")
    else:
        notes.append("Sin lista fiable de libres / pool global")
    if finance_meta.get("update_mode") == "bootstrap":
        notes.append(
            "Caja/puja rivales: bootstrap de todas las fichas "
            f"(reason={finance_meta.get('bootstrap_reason')}, "
            f"fichas={finance_meta.get('profiles')})"
        )
    elif finance_meta.get("update_mode") == "feed_incremental":
        notes.append(
            "Caja/puja rivales: snapshot + feed "
            f"(Δtransf={finance_meta.get('new_transfers')}, "
            f"Δpremios={finance_meta.get('new_prizes')})"
        )
    elif finance_meta.get("source") == "player_profiles+feed":
        notes.append(
            "Caja/puja rivales: fichas jugador + feed "
            f"(ledger={finance_meta.get('ledger_events')}, "
            f"premios={finance_meta.get('prizes_events')})"
        )

    return {
        "league": {
            "id": str(community),
            "name": str(fg_user.get("community") or "Liga Mister"),
            "total_managers": len(rivals) + (1 if me_row or squad else 0),
            "competition": competition or None,
            "id_competition": id_competition_i,
        },
        "me": {
            "team_id": my_uc or "me",
            "manager": manager,
            "team_name": team_name,
            "balance": bal,
            "balance_future": bal_future,
            "max_debt": max_debt,
            "squad_value": squad_value or int((me_row or {}).get("squad_value") or 0),
            "rank": int((me_row or {}).get("rank") or 0) or None,
            "points": int((me_row or {}).get("points") or 0),
            "formation": fg_user.get("formation"),
            "squad": squad,
        },
        "market": market,
        "sales_state": sales_state,
        "rivals": rivals,
        "owned_across_league": sorted(owned),
        "pool_top": free_pool,
        "pool_all": full_pool,
        "gameweek": gameweek,
        "_live_meta": {
            "balance_ok": bool(balance_data or bal),
            "team_ok": bool(squad),
            "market_ok": bool(market),
            "standings_ok": bool(rivals or me_row),
            "rivals_squads_ok": any(bool(r.get("squad")) for r in rivals),
            "free_agents_source": free_note,
            "pool_source": pool_meta.get("source") if full_pool else free_note,
            "pool_size": int(pool_meta.get("pool_size") or 0) if full_pool else 0,
            "pool_free_count": int(pool_meta.get("free_count") or len(free_pool)),
            "pool_owned_count": int(pool_meta.get("owned_count") or 0) if full_pool else 0,
            "pool_fields_filled": pool_fields_filled,
            "gameweek_source": gameweek.get("status"),
            "gameweek_id": gameweek.get("gameweek_id"),
            "competition_calendar_ok": bool(gameweek.get("team_schedule")),
            "id_community": str(community),
            "competition": competition or None,
            "id_competition": id_competition_i,
            "id_uc": my_uc or None,
            "source": "mister_html+ajax_balance+sw_players",
            "honest_mode": True,
            "notes": notes,
            "fg_user_rules": rules_fg,
            "fg_cfg_rules": rules_cfg,
            "admin_settings": admin_data,
            "market_auction_ends": auction_ends,
            "market_lock": rules_cfg.get("market_lock"),
            "provider": fg_user.get("provider"),
            "team_limit": fg_user.get("team_limit"),
            "league_type": fg_user.get("type"),
            "league_mode": fg_user.get("mode"),
            "rival_finances": finance_meta,
        },
    }
