"""
Scrapers externos Fantasy.

Jerarquía tras consolidar en Mister:
  - Fútbol Fantasy: autoridad externa (lesionados, sancionados, previa de jornada).
  - Jornada Perfecta: solo respaldo, si la previa de FF no sale o sale incompleta.

Comuniate y Sofascore se retiraron: lo que aportaban (nota reciente, racha de
puntos, chollos) ya lo da Mister o FotMob con menos peticiones y sin bloqueos.
"""

from __future__ import annotations

from typing import Any

from .futbolfantasy import fetch_futbolfantasy
from .ff_matchday import fetch_ff_matchday
from .jornadaperfecta import fetch_jornadaperfecta

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
            "ff_matchday": {"status": "skip", "fixtures": [], "players": []},
            "status": {
                "futbolfantasy": "skip",
                "jornadaperfecta": "skip",
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

    return {
        "futbolfantasy": ff,
        "jornadaperfecta": jp,
        "ff_matchday": matchday,
        "status": status,
        "competition": comp,
    }


__all__ = ["fetch_all_external", "SUPPORTED_EXTERNAL", "FF_MATCHDAY_OK", "fetch_ff_matchday"]
