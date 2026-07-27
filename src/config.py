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
HISTORY_DIR = DATA_DIR / "history"

# Entradas
MOCK_DATA_PATH = SRC_DIR / "mock_data.json"
PERFORMANCE_HISTORY_PATH = SRC_DIR / "performance_history.json"

# Salidas
LATEST_DATA_PATH = DATA_DIR / "latest_data.json"
LEAGUES_DIR = DATA_DIR / "leagues"
LEAGUES_INDEX_PATH = DATA_DIR / "leagues.json"

# Registro multi-liga (misma cuenta Mister, varias comunidades)
LEAGUES: list[dict] = [
    {
        "slug": "laliga-patio",
        "name": "Liga del patio",
        "id_community": "2500716",
        "id_competition": 1,
        "competition": "LaLiga",
        "external": "laliga",
        "market_mode": "auction",
        "season_start": "2026-08-15",
        "default": True,
    },
    {
        "slug": "premier",
        "name": "PREMIER LEAGUE",
        "id_community": "906674",
        "id_competition": 3,
        "competition": "Premier League",
        "external": "premier",
        "market_mode": "fixed",
        "season_start": "2026-08-21",
        "default": False,
    },
]


def league_market_mode(league_cfg: dict | None = None) -> str:
    """auction (subasta/exclusivo) | fixed (precio listado / plantillas compartidas)."""
    mode = str((league_cfg or {}).get("market_mode") or "auction").strip().lower()
    return mode if mode in ("auction", "fixed") else "auction"


def get_league(slug: str | None = None) -> dict:
    """Resuelve liga por slug; default si slug vacío/desconocido."""
    wanted = (slug or "").strip() or os.environ.get("MISTER_LEAGUE_SLUG", "").strip()
    if wanted:
        for L in LEAGUES:
            if L["slug"] == wanted or str(L["id_community"]) == wanted:
                return dict(L)
    for L in LEAGUES:
        if L.get("default"):
            return dict(L)
    return dict(LEAGUES[0])


def default_league_slug() -> str:
    env = os.environ.get("MISTER_LEAGUE_SLUG", "").strip()
    if env:
        return get_league(env)["slug"]
    for L in LEAGUES:
        if L.get("default"):
            return str(L["slug"])
    return str(LEAGUES[0]["slug"])


def league_data_path(slug: str) -> Path:
    return LEAGUES_DIR / slug / "latest_data.json"


def league_history_dir(slug: str) -> Path:
    return LEAGUES_DIR / slug / "history"


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
# Once fantasy de la plantilla perfecta (debe sumar 11); resto = banquillo
IDEAL_XI = {"GK": 1, "DF": 4, "MF": 3, "FW": 3}
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
# Días antes de J1 en los que la fase pasa a "ramp"
RAMP_DAYS_BEFORE_KICKOFF = 7
# Paquete del día: colchón de caja y tope del fichaje secundario (parche)
PACKAGE_CASH_RESERVE = 8_000_000
PACKAGE_SECONDARY_MAX = 2_500_000
# id_competition Mister → clave de scrapers externos (FF/JP)
LALIGA_COMPETITION_ID = 1
PREMIER_COMPETITION_ID = 3
EXTERNAL_BY_COMPETITION_ID: dict[int, str] = {
    LALIGA_COMPETITION_ID: "laliga",
    PREMIER_COMPETITION_ID: "premier",
}


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
CHOLLO_DELTA_MIN = 0.08
TRADING_WINDOW_DAYS = 5

# Pool completo Mister via POST /ajax/sw/players (páginas de 50)
MISTER_POOL_PAGE_SIZE = 50
MISTER_POOL_MAX_OFFSET = 2000
# Techo de filtros value/clause (0 en el filtro = nadie; hay que abrir rango)
MISTER_POOL_VALUE_CEILING = 100_000_000
