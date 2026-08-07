"""Scrapers externos Fantasy (FF / JP / Comuniate). Sofascore API no se invoca."""

from __future__ import annotations

from typing import Any

from .comuniate import fetch_comuniate
from .futbolfantasy import fetch_futbolfantasy
from .ff_matchday import fetch_ff_matchday
from .jornadaperfecta import fetch_jornadaperfecta

# Competiciones con scrapers FF/JP cableados
SUPPORTED_EXTERNAL = frozenset({"laliga", "premier"})


def fetch_all_external(
    team_names: list[str] | None = None,
    *,
    competition: str = "laliga",
    sofascore_candidates: list[dict[str, Any]] | None = None,
    priority_teams: list[str] | None = None,
) -> dict[str, Any]:
    """
    Ejecuta scrapers hub fail-soft.
    competition: `laliga` | `premier` (u otra → FF/JP vacíos + status skip).
    priority_teams: equipos de plantilla (FF sin tope).
    sofascore_candidates se ignora (nota → FotMob en data_engine).
    """
    _ = sofascore_candidates  # legacy kw; no API Sofascore
    comp = (competition or "laliga").strip().lower()
    status: dict[str, str] = {}

    if comp not in SUPPORTED_EXTERNAL:
        return {
            "futbolfantasy": [],
            "jornadaperfecta": [],
            "comuniate": [],
            "sofascore": [],
            "ff_matchday": {"status": "skip", "fixtures": [], "players": []},
            "status": {
                "futbolfantasy": "skip",
                "jornadaperfecta": "skip",
                "comuniate": "skip",
                "sofascore": "skip",
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

    jp = fetch_jornadaperfecta(competition=comp)
    status["jornadaperfecta"] = "ok" if jp else "fail"

    matchday = fetch_ff_matchday(competition=comp)
    md_status = str(matchday.get("status") or "fail")
    status["ff_matchday"] = md_status if md_status in ("ok", "partial", "cache") else "fail"

    # Comuniate cubre LaLiga Fantasy; Premier no tiene sección usable
    if comp == "laliga":
        com = fetch_comuniate(profile_limit=0)
        status["comuniate"] = "ok" if com else "fail"
    else:
        com = []
        status["comuniate"] = "skip"

    status["sofascore"] = "skip"

    return {
        "futbolfantasy": ff,
        "jornadaperfecta": jp,
        "comuniate": com,
        "sofascore": [],
        "ff_matchday": matchday,
        "status": status,
        "competition": comp,
    }


__all__ = ["fetch_all_external", "SUPPORTED_EXTERNAL", "fetch_ff_matchday"]
