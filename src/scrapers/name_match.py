"""
Normalización y fuzzy matching de nombres Mister ↔ fuentes externas.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from thefuzz import fuzz

MATCH_THRESHOLD = 85


def strip_accents(text: str) -> str:
    nk = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nk if not unicodedata.combining(c))


def normalize_name(name: str) -> str:
    n = strip_accents(name or "").lower().strip()
    n = n.replace(".", " ")
    n = re.sub(r"[^a-z0-9\s\-']", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    # Quitar tokens ruidosos
    noise = {"jr", "junior", "sr", "ii", "iii"}
    parts = [p for p in n.split() if p not in noise]
    return " ".join(parts)


def normalize_team(team: str) -> str:
    t = normalize_name(team)
    aliases = {
        "atletico": "atletico madrid",
        "atletico de madrid": "atletico madrid",
        "athletic": "athletic club",
        "athletic bilbao": "athletic club",
        "real sociedad": "real sociedad",
        "real madrid": "real madrid",
        "barcelona": "barcelona",
        "barça": "barcelona",
        "barca": "barcelona",
        "betis": "real betis",
        "real betis": "real betis",
        "celta": "celta vigo",
        "villarreal": "villarreal",
        "sevilla": "sevilla",
        "valencia": "valencia",
        "osasuna": "osasuna",
        "getafe": "getafe",
        "girona": "girona",
        "mallorca": "mallorca",
        "rayo": "rayo vallecano",
        "rayo vallecano": "rayo vallecano",
        "alaves": "alaves",
        "espanyol": "espanyol",
        "valladolid": "valladolid",
        "levante": "levante",
        "elche": "elche",
    }
    return aliases.get(t, t)


def match_player(
    mister_name: str,
    mister_team: str | None,
    candidates: list[dict[str, Any]],
    threshold: int = MATCH_THRESHOLD,
) -> tuple[dict[str, Any] | None, int]:
    """
    Devuelve (candidato, score) o (None, 0).
    candidates: dicts con al menos 'name' y opcionalmente 'team'.
    """
    target = normalize_name(mister_name)
    team_n = normalize_team(mister_team or "")
    if not target or not candidates:
        return None, 0

    best: dict[str, Any] | None = None
    best_score = 0
    for cand in candidates:
        cname = normalize_name(str(cand.get("name") or ""))
        if not cname:
            continue
        score = fuzz.token_set_ratio(target, cname)
        # Bonus / empate por equipo
        cteam = normalize_team(str(cand.get("team") or ""))
        if team_n and cteam and (team_n in cteam or cteam in team_n):
            score = min(100, score + 5)
        # Bonus si la inicial coincide (L. Yamal vs Lamine Yamal)
        t_parts = target.split()
        c_parts = cname.split()
        if t_parts and c_parts and len(t_parts[0]) == 1 and t_parts[0] == c_parts[0][:1]:
            if t_parts[-1] == c_parts[-1]:
                score = max(score, fuzz.ratio(t_parts[-1], c_parts[-1]) + 10)
        if score > best_score:
            best_score = score
            best = cand

    best_score = min(100, best_score)
    if best is None or best_score < threshold:
        return None, best_score
    return best, best_score
