"""
Configuración central del motor Mister Fantasy Advisor.

Lee credenciales desde variables de entorno (GitHub Secrets en CI)
y, en local, desde un archivo `.env` en la raíz del repo (si existe).
"""

from __future__ import annotations

import os
from pathlib import Path

# Raíz del repo (padre de src/)
ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
PUBLIC_DIR = ROOT_DIR / "public"
DATA_DIR = PUBLIC_DIR / "data"
# Legacy (ya no se escribe); solo fallback de lectura si existe.
HISTORY_DIR = DATA_DIR / "history"

# Entradas
MOCK_DATA_PATH = SRC_DIR / "mock_data.json"
PERFORMANCE_HISTORY_PATH = SRC_DIR / "performance_history.json"

# Salidas
LATEST_DATA_PATH = DATA_DIR / "latest_data.json"
LEAGUES_DIR = DATA_DIR / "leagues"
LEAGUES_INDEX_PATH = DATA_DIR / "leagues.json"

# id_competition Mister → metadatos de competición / scrapers externos
COMPETITION_MAP: dict[int, dict] = {
    1: {
        "external": "laliga",
        "competition": "LaLiga",
        "default_max_squad": 25,
        "default_market_mode": "auction",
    },
    3: {
        "external": "premier",
        "competition": "Premier League",
        "default_max_squad": 22,
        "default_market_mode": "fixed",
    },
}

# Overrides opcionales keyed por id_community (ganan sobre discovery en campos
# de metadatos: slug, season_start, default, market_mode forzado, etc.).
# Solo se aplican a comunidades que Mister sigue listando; no reintroducen
# una liga abandonada cuando discovery devolvió un catálogo no vacío.
LEAGUE_OVERRIDES: dict[str, dict] = {
    "2500716": {
        "slug": "laliga-patio",
        "name": "Liga del patio",
        "season_start": "2026-08-15",
        "default": True,
        "starting_budget": 50_000_000,
        "sorteo_date": "2026-07-24",
        "start_mode": "random_minus_vm",
    },
    "906674": {
        "slug": "premier",
        "name": "PREMIER LEAGUE",
        "season_start": "2026-08-21",
        "default": False,
    },
}

# Catálogo efectivo del run (se rellena con discovery + overrides).
# Sin auth: fallback estático para mock / clone sin secrets.
_effective_leagues: list[dict] | None = None


def _slugify_league(name: str, id_community: str) -> str:
    import re
    import unicodedata

    nk = unicodedata.normalize("NFKD", name or "")
    ascii_name = "".join(c for c in nk if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    if not slug:
        slug = "liga"
    cid = str(id_community or "").strip()
    suffix = cid[-4:] if len(cid) >= 4 else cid or "x"
    return f"{slug}-{suffix}"


def competition_meta(id_competition: int | None) -> dict:
    if id_competition is None:
        return {}
    try:
        return dict(COMPETITION_MAP.get(int(id_competition)) or {})
    except (TypeError, ValueError):
        return {}


def _fallback_leagues_from_overrides() -> list[dict]:
    """Catálogo offline: overrides + COMPETITION_MAP (sin discovery Mister)."""
    out: list[dict] = []
    for cid, ov in LEAGUE_OVERRIDES.items():
        row = dict(ov)
        row["id_community"] = str(cid)
        cid_i = None
        try:
            cid_i = int(row.get("id_competition") or 0) or None
        except (TypeError, ValueError):
            cid_i = None
        # Inferir competición conocida por override histórico
        if cid_i is None:
            if str(row.get("slug") or "").startswith("premier") or cid == "906674":
                cid_i = 3
            elif cid == "2500716":
                cid_i = 1
        meta = competition_meta(cid_i)
        row.setdefault("id_competition", cid_i)
        row.setdefault("competition", meta.get("competition"))
        row.setdefault("external", meta.get("external"))
        row.setdefault("max_squad", meta.get("default_max_squad") or 25)
        row.setdefault("market_mode", meta.get("default_market_mode") or "auction")
        row.setdefault("slug", _slugify_league(str(row.get("name") or "liga"), cid))
        row.setdefault("default", False)
        out.append(row)
    if not out:
        out.append(
            {
                "slug": "demo",
                "name": "Demo",
                "id_community": "0",
                "id_competition": 1,
                "competition": "LaLiga",
                "external": "laliga",
                "market_mode": "auction",
                "max_squad": 25,
                "season_start": "2026-08-15",
                "default": True,
            }
        )
    if not any(L.get("default") for L in out):
        out[0]["default"] = True
    return out


def resolve_leagues(discovered: list[dict] | None = None) -> list[dict]:
    """
    Fusiona comunidades descubiertas en Mister con LEAGUE_OVERRIDES.
    Discovery aporta id/name/competition; overrides aportan slug/season_start/default
    y pueden forzar market_mode / external / max_squad.
    """
    if not discovered:
        return _fallback_leagues_from_overrides()

    out: list[dict] = []
    seen_cid: set[str] = set()
    for raw in discovered:
        if not isinstance(raw, dict):
            continue
        cid = str(raw.get("id_community") or raw.get("id") or "").strip()
        if not cid or cid in seen_cid:
            continue
        seen_cid.add(cid)
        ov = dict(LEAGUE_OVERRIDES.get(cid) or {})
        try:
            id_comp = int(raw.get("id_competition") if raw.get("id_competition") is not None else ov.get("id_competition"))
        except (TypeError, ValueError):
            id_comp = None
        meta = competition_meta(id_comp)
        name = str(ov.get("name") or raw.get("name") or f"Liga {cid}")
        slug = str(ov.get("slug") or "").strip() or _slugify_league(name, cid)
        row: dict = {
            "slug": slug,
            "name": name,
            "id_community": cid,
            "id_competition": id_comp,
            "competition": ov.get("competition") or raw.get("competition") or meta.get("competition"),
            "external": ov.get("external") or meta.get("external"),
            "season_start": ov.get("season_start"),
            "default": bool(ov.get("default", False)),
            "mode": raw.get("mode"),
            "type": raw.get("type"),
            "code": raw.get("code"),
        }
        # market_mode / max_squad: override forzado > discovery > COMPETITION_MAP
        if ov.get("market_mode"):
            row["market_mode"] = ov["market_mode"]
        elif raw.get("market_mode"):
            row["market_mode"] = raw["market_mode"]
        else:
            row["market_mode"] = meta.get("default_market_mode") or "auction"
        if ov.get("max_squad") is not None:
            row["max_squad"] = ov["max_squad"]
        elif raw.get("max_squad") is not None:
            row["max_squad"] = raw["max_squad"]
        elif raw.get("team_limit") is not None:
            row["max_squad"] = raw["team_limit"]
        else:
            row["max_squad"] = meta.get("default_max_squad") or 25
        out.append(row)

    # Con discovery no vacío: solo comunidades que Mister sigue listando.
    # No reinyectar LEAGUE_OVERRIDES ausentes (liga abandonada no debe volver al catálogo).
    # Sin discovery, el early-return usa _fallback_leagues_from_overrides().

    if not out:
        return _fallback_leagues_from_overrides()
    if not any(L.get("default") for L in out):
        out[0]["default"] = True
    # Una sola default
    seen_default = False
    for L in out:
        if L.get("default"):
            if seen_default:
                L["default"] = False
            else:
                seen_default = True
    return out


def set_effective_leagues(leagues: list[dict]) -> list[dict]:
    """Fija el catálogo del proceso y refresca DEFAULT_LEAGUE_SLUG."""
    global _effective_leagues, LEAGUES, DEFAULT_LEAGUE_SLUG
    resolved = [dict(L) for L in leagues if isinstance(L, dict) and L.get("slug")]
    if not resolved:
        resolved = _fallback_leagues_from_overrides()
    _effective_leagues = resolved
    LEAGUES = resolved
    DEFAULT_LEAGUE_SLUG = default_league_slug()
    return list(resolved)


def get_effective_leagues() -> list[dict]:
    if _effective_leagues is not None:
        return [dict(L) for L in _effective_leagues]
    return [dict(L) for L in LEAGUES]


def league_market_mode(league_cfg: dict | None = None) -> str:
    """auction (subasta/exclusivo) | fixed (precio listado / plantillas compartidas)."""
    mode = str((league_cfg or {}).get("market_mode") or "auction").strip().lower()
    return mode if mode in ("auction", "fixed") else "auction"


def get_league(slug: str | None = None) -> dict:
    """Resuelve liga por slug; default si slug vacío/desconocido."""
    catalog = get_effective_leagues()
    wanted = (slug or "").strip() or os.environ.get("MISTER_LEAGUE_SLUG", "").strip()
    if wanted:
        for L in catalog:
            if L["slug"] == wanted or str(L["id_community"]) == wanted:
                return dict(L)
    for L in catalog:
        if L.get("default"):
            return dict(L)
    return dict(catalog[0])


def default_league_slug() -> str:
    env = os.environ.get("MISTER_LEAGUE_SLUG", "").strip()
    catalog = get_effective_leagues() if _effective_leagues is not None else None
    if catalog is None:
        # Durante import inicial LEAGUES aún es el fallback
        catalog = list(LEAGUES) if LEAGUES else _fallback_leagues_from_overrides()
    if env:
        for L in catalog:
            if L["slug"] == env or str(L.get("id_community")) == env:
                return str(L["slug"])
    for L in catalog:
        if L.get("default"):
            return str(L["slug"])
    return str(catalog[0]["slug"])


def league_data_path(slug: str) -> Path:
    return LEAGUES_DIR / slug / "latest_data.json"


def league_history_dir(slug: str) -> Path:
    return LEAGUES_DIR / slug / "history"


# Catálogo inicial (mock / hasta discovery en runtime)
LEAGUES: list[dict] = _fallback_leagues_from_overrides()


def _load_dotenv(path: Path) -> None:
    """
    Carga KEY=VALUE desde `.env` a os.environ sin sobrescribir
    variables ya definidas (CI / shell tienen prioridad).
    No imprime valores.
    """
    if not path.is_file() or path.stat().st_size == 0:
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if not key:
            continue
        # No pisa secrets ya presentes en el entorno
        if key not in os.environ or os.environ.get(key, "") == "":
            os.environ[key] = val


_load_dotenv(ROOT_DIR / ".env")

DEFAULT_LEAGUE_SLUG = default_league_slug()

# --- Credenciales / Secrets ---
# Auth real de Mister (DevTools → Network → /ajax/*):
#   cookie `token=...`          → MISTER_TOKEN
#   header  `x-auth: ...`       → MISTER_X_AUTH
#   cookie `PHPSESSID=...`      → MISTER_PHPSESSID (recomendado)
#   cookie `refresh-token=...`  → MISTER_REFRESH_TOKEN (opcional)
# Alternativa: pegar la cookie completa en MISTER_COOKIE
MISTER_TOKEN = os.environ.get("MISTER_TOKEN", "").strip()
MISTER_X_AUTH = os.environ.get("MISTER_X_AUTH", "").strip()
MISTER_PHPSESSID = os.environ.get("MISTER_PHPSESSID", "").strip()
MISTER_REFRESH_TOKEN = os.environ.get("MISTER_REFRESH_TOKEN", "").strip()
MISTER_COOKIE = os.environ.get("MISTER_COOKIE", "").strip()
MISTER_LEAGUE_ID = os.environ.get("MISTER_LEAGUE_ID", "").strip()
MISTER_TEAM_ID = os.environ.get("MISTER_TEAM_ID", "").strip()
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY", "").strip()

# Sin JWT (`token`) → mock. x-auth suele ser necesario también para /ajax/*
USE_MISTER_MOCK = not bool(MISTER_TOKEN or MISTER_COOKIE)
USE_PERF_SEED = not bool(FOOTBALL_API_KEY)

# Base real de Mister (endpoints bajo /ajax/...)
MISTER_API_BASE = os.environ.get(
    "MISTER_API_BASE", "https://mister.mundodeportivo.com"
).rstrip("/")

# API-Football (api-sports.io) — LaLiga = league id 140
FOOTBALL_API_BASE = "https://v3.football.api-sports.io"
FOOTBALL_LEAGUE_ID = 140
FOOTBALL_SEASONS = [2023, 2024, 2025]

# Reglas de diagnóstico de plantilla
MIN_GK = 2
MIN_DF = 4
MIN_MF = 4
MIN_FW = 2
# Plantilla objetivo ~15 con alternativas por línea
IDEAL_SQUAD = {"GK": 2, "DF": 5, "MF": 5, "FW": 3}
# Once fantasy (suma 11); el resto del IDEAL_SQUAD es banquillo
IDEAL_XI = {"GK": 1, "DF": 4, "MF": 3, "FW": 3}
# Formaciones Mister a probar para el ideal (elige la de más Σ EP del once)
IDEAL_FORMATIONS = (
    "4-3-3",
    "4-4-2",
    "4-5-1",
    "3-5-2",
    "3-4-3",
    "5-3-2",
    "5-4-1",
    "4-2-3-1",  # se interpreta como DF4 MF5 FW1 si no encaja 4 números
)
# Banquillo del ideal: mínimo puntos Mister (FF) por temporada
IDEAL_BENCH_MIN_POINTS = 100
# Ideal operable: prima cláusula/mercado por encima de la cual se exige más EP vs libre/mercado
IDEAL_CLAUSE_PREMIUM_SOFT = 1.25
# Banda de empate EP: preferir oportunidad (libre/mercado) si la diferencia ≤ esto
IDEAL_EP_TIE_BAND = 5.0
# Upgrade operable con cláusula cara: mínimo ΔEP por M€ de sobrecoste
IDEAL_CLAUSE_MIN_EP_PER_M = 3.0
# Recomendación clause_bid: mínimo upgrade_score por M€ de cláusula
CLAUSE_MIN_UPGRADE_PER_M = 5.0
# Hist quality mínima (0–100) para titular sin hist_ok (evita techo EP 42 basura)
IDEAL_STARTER_HIST_MIN = 35.0
# Umbrales Mister Mixto: producción histórica “fiable / titular top” por posición
# (media pts/partido y pts totales/temporada). Premier RPG se escala × avg_scale/8.
MISTER_HIST_AVG_FLOOR = {"GK": 4.5, "DF": 5.0, "MF": 6.0, "FW": 6.5}
MISTER_HIST_PTS_FLOOR = {"GK": 160, "DF": 180, "MF": 220, "FW": 230}
# Titulares reales mínimos por línea para diagnóstico de profundidad
STARTERS_TARGET = {"GK": 1, "DF": 3, "MF": 3, "FW": 2}
LINEUP_PROB_TITULAR = 0.70
LINEUP_PROB_REGULAR = 0.45
LINEUP_PROB_LOW = 0.40
# Minutos acumulados en últimos ~5 partidos (FotMob) bajo los cuales cuenta como "juega poco"
MINUTES_RECENT_LOW = 90

# Campeonato / pretemporada (fallback global; cada liga puede sobreescribir)
SEASON_START_DATE = os.environ.get("SEASON_START_DATE", "2026-08-15").strip()
# Plazas típicas del mercado diario (meta UI; el HTML puede traer más/menos)
MARKET_DAY_SLOTS = 16
# Ciclo de mercado de la liga (Normal = 24h). Oferta Mister en el siguiente ciclo;
# cobro tras aceptar = otro ciclo → caja usable ≈ 2 * MARKET_CYCLE_HOURS desde listar.
MARKET_CYCLE_HOURS = 24
# Rescindir / despedir: liquidez inmediata a este % del valor de mercado
RESCIND_VALUE_RATIO = 0.80
# Días antes de J1 en los que la fase pasa a "ramp"
RAMP_DAYS_BEFORE_KICKOFF = 7
# Paquete del día: ya no se congela caja para un crack que no está listado.
# Liquidez = jugadores en venta (oferta CPU al siguiente ciclo). 0 = no reservar.
PACKAGE_CASH_RESERVE = 0
PACKAGE_SECONDARY_MAX = 2_500_000
# Hedge same-day: puja reducida vs recomendada (mínimo = puja_minima/precio)
PACKAGE_HEDGE_BID_RATIO = 0.85
# Bootstrap once: ventana máxima (h) para priorizar completar 11
BOOTSTRAP_XI_MAX_HOURS = float(os.environ.get("BOOTSTRAP_XI_MAX_HOURS", "240"))
# Si queda menos de esto en el ciclo actual → comprar ya (h)
BOOTSTRAP_CYCLE_END_URGENT_HOURS = float(
    os.environ.get("BOOTSTRAP_CYCLE_END_URGENT_HOURS", "3")
)

# Solvencia: positivo al INICIO de la jornada que puntúa (esta o la siguiente).
# Ventana strict = deadline cercano sin plan de cobro a tiempo.
SOLVENCY_STRICT_HOURS = 48
# Margen de seguridad para cobro de ventas antes del kickoff de jornada (h).
SOLVENCY_SETTLE_BUFFER_HOURS = 2
# Legacy alias (antes D-1); preferir SOLVENCY_SETTLE_BUFFER_HOURS.
SOLVENCY_D1_BUFFER_HOURS = SOLVENCY_SETTLE_BUFFER_HOURS
# Cupo máximo de plantilla Mister (fallback si no hay team_limit)
MAX_SQUAD_SIZE_LALIGA = 25
MAX_SQUAD_SIZE_PREMIER = 22
# id_competition Mister → clave de scrapers externos (FF/JP)
LALIGA_COMPETITION_ID = 1
PREMIER_COMPETITION_ID = 3
EXTERNAL_BY_COMPETITION_ID: dict[int, str] = {
    cid: str(meta["external"])
    for cid, meta in COMPETITION_MAP.items()
    if meta.get("external")
}


def league_max_squad(league_cfg: dict | None = None) -> int:
    """Tope de jugadores en plantilla: 25 LaLiga / 22 Premier."""
    cfg = league_cfg or {}
    raw = cfg.get("max_squad")
    if raw is not None:
        try:
            n = int(raw)
            if n > 0:
                return n
        except (TypeError, ValueError):
            pass
    ext = external_competition_key(league_cfg=cfg)
    if ext == "premier":
        return int(MAX_SQUAD_SIZE_PREMIER)
    try:
        cid = int(cfg["id_competition"]) if cfg.get("id_competition") is not None else None
    except (TypeError, ValueError):
        cid = None
    if cid == int(PREMIER_COMPETITION_ID):
        return int(MAX_SQUAD_SIZE_PREMIER)
    return int(MAX_SQUAD_SIZE_LALIGA)


def external_competition_key(
    *,
    league_cfg: dict | None = None,
    id_competition: int | None = None,
) -> str | None:
    """Resuelve `laliga` | `premier` | None para scrapers externos."""
    cfg = league_cfg or {}
    ext = (cfg.get("external") or "").strip().lower()
    if ext in ("laliga", "premier"):
        return ext
    cid = id_competition
    if cid is None and cfg.get("id_competition") is not None:
        try:
            cid = int(cfg["id_competition"])
        except (TypeError, ValueError):
            cid = None
    if cid is not None:
        return EXTERNAL_BY_COMPETITION_ID.get(int(cid))
    return None

HISTORY_RETENTION_DAYS = 30
# ~3 snapshots/día × 30 días; el prune corta por fecha, no por recuento
HISTORY_SNAPSHOTS_MAX = 90
CHOLLO_DELTA_MIN = 0.08
TRADING_WINDOW_DAYS = 5
# 5 días × 3 ciclos de mercado
TRADING_WINDOW_SNAPSHOTS = 15
# Revalorización: sin hueco/upgrade claro, fichar flechas al alza con perspectiva
APPRECIATION_DELTA_MIN = 0.04  # +4% en ~5d
APPRECIATION_MAX_BUYS = 2
APPRECIATION_MAX_PRICE = 8_000_000  # no meter pasta gorda solo por Δprecio
APPRECIATION_MIN_SCORE = 18.0
APPRECIATION_LINEUP_MIN = 0.45  # regular usable para que el VM siga subiendo
# Subida viva: no puntuar picos de un cierre (160k→210k de quien no juega)
APPRECIATION_MIN_CONSECUTIVE_UP = 2
APPRECIATION_MIN_VM = 400_000
APPRECIATION_MIN_ABS_GAIN = 80_000
# Plan de ciclo: oferta del sistema por debajo de esto = outlier (no el “un poco”)
CYCLE_OFFER_OUTLIER_PCT = 0.82
# Señal fuerte de subida (el 10% del ejemplo; 8% ya merece rotar)
CYCLE_STRONG_RISE = 0.08
# Para listar a alguien que aún sube fuerte: el mercado debe superarlo por este margen
CYCLE_LIST_SWAP_MARGIN = 0.08
CYCLE_MAX_BIDS = 3
CYCLE_MAX_LISTS = 5

# Pool completo Mister via POST /ajax/sw/players (páginas de 50)
MISTER_POOL_PAGE_SIZE = 50
MISTER_POOL_MAX_OFFSET = 2000
# Techo de filtros value/clause (0 en el filtro = nadie; hay que abrir rango)
MISTER_POOL_VALUE_CEILING = 100_000_000
# Bootstrap de caja rival: barrido de TODAS las fichas (plantilla + libres).
# Los refresh posteriores solo aplican el feed sobre cache/rival_finances/{cid}.json.
MISTER_PROFILE_MAX = 600
MISTER_PROFILE_BOOTSTRAP_MAX = 600
MISTER_FINANCE_FEED_MAX_AGE_DAYS = 25
MISTER_FEED_PAGE_SIZE = 20
MISTER_FEED_MAX_PAGES = 40
DEFAULT_STARTING_BUDGET = 50_000_000
