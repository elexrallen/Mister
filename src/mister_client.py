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
from typing import Any

import requests

import config

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


def points_trend_from_gw(gw: list[int] | None) -> str:
    """up|down|flat|unknown a partir de últimas jornadas numéricas."""
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
            row["listed_by_rival"] = True
        players.append(row)
    log.info("HTML /market → %s jugadores", len(players))
    return players


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
    return {
        "id": pid,
        "name": name,
        "position": _pos(raw.get("position")),
        "team": team_label(team_id) if team_id else "—",
        "team_id": team_id or None,
        "price": value,
        "points": int(raw.get("points") or 0),
        "form": form,
        "mister_avg": form,
        "injury": injury,
        "trend": None,
        "price_delta_5d": None,
        "seller": "free" if is_free else "owned",
        "owner_id": owner_id,
        "owner_name": (str(raw.get("uc_name")).strip() if raw.get("uc_name") else None),
        "clause": clause,
        "clause_known": clause is not None,
        "is_mine": bool(raw.get("is_mine")),
        "id_market": raw.get("id_market"),
        "min_bid": value if is_free else None,
        "data_quality": {
            "price": "mister",
            "ownership": "mister_sw_players",
            "clause": "mister" if clause is not None else "missing",
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


def fetch_balance() -> dict[str, Any]:
    try:
        data = ajax_post("/ajax/balance")
        if isinstance(data, dict) and data.get("status") == "ok":
            return data.get("data") or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("ajax/balance falló: %s", exc)
    return {}


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
    market_html = team_html = standings_html = ""
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

    if not market_html and not team_html and not balance_data:
        return None

    fg_user = _extract_js_object(market_html or team_html, "_FG_user") or {}
    fg_cfg = _extract_js_object(market_html or team_html, "_FG_cfg") or {}
    # Asegurar auth fresco antes del pool /ajax/sw/players (crítico multi-liga).
    apply_x_auth_from_cfg(fg_cfg)

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
    rivals, me_row = parse_standings(standings_html, my_uc) if standings_html else ([], None)
    if rivals:
        rivals = enrich_rivals_with_squads(rivals)

    # Catálogo completo (~500): libres + ownership real por id_uc
    full_pool, pool_meta = fetch_full_player_pool()
    free_pool: list[dict[str, Any]] = []
    free_note = "unavailable"
    if full_pool:
        free_pool = [p for p in full_pool if not p.get("owner_id")]
        free_note = str(pool_meta.get("source") or "mister_sw_players")
        rivals = apply_pool_squads_to_rivals(rivals, full_pool, my_uc)
    else:
        free_pool, free_note = fetch_free_agents_best_effort()

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
    elif free_pool:
        notes.append(f"Libres detectados vía {free_note}: {len(free_pool)}")
    else:
        notes.append("Sin lista fiable de libres / pool global")

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
        "rivals": rivals,
        "owned_across_league": sorted(owned),
        "pool_top": free_pool,
        "pool_all": full_pool,
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
            "id_community": str(community),
            "competition": competition or None,
            "id_competition": id_competition_i,
            "id_uc": my_uc or None,
            "source": "mister_html+ajax_balance+sw_players",
            "honest_mode": True,
            "notes": notes,
        },
    }
