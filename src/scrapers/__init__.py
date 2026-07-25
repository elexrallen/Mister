"""Scrapers externos Fantasy (FF / JP / Comuniate). Sofascore API no se invoca."""

from __future__ import annotations

from typing import Any

from .comuniate import fetch_comuniate
from .futbolfantasy import fetch_futbolfantasy
from .jornadaperfecta import fetch_jornadaperfecta


def fetch_all_external(
    team_names: list[str] | None = None,
    *,
    sofascore_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Ejecuta scrapers hub fail-soft.
    sofascore_candidates se ignora (nota → FotMob en data_engine).
    """
    _ = sofascore_candidates  # legacy kw; no API Sofascore
    status: dict[str, str] = {}

    ff = fetch_futbolfantasy(team_names)
    status["futbolfantasy"] = "ok" if ff else "fail"

    jp = fetch_jornadaperfecta()
    status["jornadaperfecta"] = "ok" if jp else "fail"

    com = fetch_comuniate(profile_limit=0)
    status["comuniate"] = "ok" if com else "fail"

    status["sofascore"] = "skip"

    return {
        "futbolfantasy": ff,
        "jornadaperfecta": jp,
        "comuniate": com,
        "sofascore": [],
        "status": status,
    }


__all__ = ["fetch_all_external"]
