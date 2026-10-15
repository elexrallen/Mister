"""
Scrapers externos Fantasy.

Jerarquía tras consolidar en Mister:
  - Fútbol Fantasy: autoridad externa (lesionados, sancionados, previa de jornada).
    En LaLiga también manda la titularidad / previa.
  - Predicted11: titularidad agregada de expertos en Premier y Serie A (FF suele
    ir fino ahí; JP no cubre Serie A).
  - Jornada Perfecta: solo respaldo LaLiga/Premier si la previa de FF no sale.

Comuniate y Sofascore se retiraron: lo que aportaban (nota reciente, racha de
puntos, chollos) ya lo da Mister o FotMob con menos peticiones y sin bloqueos.
"""

from __future__ import annotations

from typing import Any

from .futbolfantasy import fetch_futbolfantasy
from .ff_matchday import fetch_ff_matchday
from .jornadaperfecta import fetch_jornadaperfecta
from .predicted11 import PRED11_SUPPORTED, fetch_predicted11

# Competiciones con scrapers FF cableados (JP solo LaLiga/Premier)
SUPPORTED_EXTERNAL = frozenset({"laliga", "premier", "seriea"})
# JP no tiene rutas Serie A reales (redirige a LaLiga)
JP_SUPPORTED = frozenset({"laliga", "premier"})
# Estados de la previa FF que hacen innecesario el respaldo de Jornada Perfecta
FF_MATCHDAY_OK = frozenset({"ok", "cache"})


def fetch_all_external(
    team_names: list[str] | None = None,
    *,
    competition: str = "laliga",
    priority_teams: list[str] | None = None,
) -> dict[str, Any]:
    """
    Ejecuta los scrapers hub fail-soft.
    competition: `laliga` | `premier` | `seriea` (u otra → vacíos + status skip).
    priority_teams: equipos de plantilla propia (FF sin tope de páginas).
    """
    comp = (competition or "laliga").strip().lower()
    status: dict[str, str] = {}

    if comp not in SUPPORTED_EXTERNAL:
        return {
            "futbolfantasy": [],
            "jornadaperfecta": [],
            "predicted11": [],
            "ff_matchday": {"status": "skip", "fixtures": [], "players": []},
            "status": {
                "futbolfantasy": "skip",
                "jornadaperfecta": "skip",
                "predicted11": "skip",
                "ff_matchday": "skip",
            },
            "competition": comp,
        }

    ff = fetch_futbolfantasy(
        team_names,
        competition=comp,
        priority_teams=priority_teams,
    )
    status["futbolfantasy"] = "ok" if ff else "fail"

    matchday = fetch_ff_matchday(competition=comp)
    md_status = str(matchday.get("status") or "fail")
    status["ff_matchday"] = md_status if md_status in ("ok", "partial", "cache") else "fail"

    # Jornada Perfecta solo si FF no cubre la previa: es un scrape caro y redundante.
    # Serie A: JP no tiene hub real → skip siempre.
    if comp not in JP_SUPPORTED:
        jp = []
        status["jornadaperfecta"] = "skip"
    elif md_status in FF_MATCHDAY_OK and ff:
        jp = []
        status["jornadaperfecta"] = "skip"
    else:
        jp = fetch_jornadaperfecta(competition=comp)
        status["jornadaperfecta"] = "ok" if jp else "fail"

    # Predicted11: titularidad en Premier/Serie A (LaLiga se queda en FF).
    if comp in PRED11_SUPPORTED:
        pred11 = fetch_predicted11(competition=comp)
        status["predicted11"] = "ok" if pred11 else "fail"
    else:
        pred11 = []
        status["predicted11"] = "skip"

    return {
        "futbolfantasy": ff,
        "jornadaperfecta": jp,
        "predicted11": pred11,
        "ff_matchday": matchday,
        "status": status,
        "competition": comp,
    }


__all__ = [
    "fetch_all_external",
    "SUPPORTED_EXTERNAL",
    "JP_SUPPORTED",
    "PRED11_SUPPORTED",
    "FF_MATCHDAY_OK",
    "fetch_ff_matchday",
]
