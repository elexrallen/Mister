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
        "atm": "atletico madrid",
        "athletic": "athletic club",
        "athletic bilbao": "athletic club",
        "real sociedad": "real sociedad",
        "r soc": "real sociedad",
        "real madrid": "real madrid",
        "barcelona": "barcelona",
        "barça": "barcelona",
        "barca": "barcelona",
        "betis": "real betis",
        "real betis": "real betis",
        "celta": "celta vigo",
        "celta de vigo": "celta vigo",
        "celta vigo": "celta vigo",
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
        "deportivo": "deportivo",
        "deportivo de la coruna": "deportivo",
        "deportivo la coruna": "deportivo",
        "rc deportivo": "deportivo",
        "depor": "deportivo",
        "oviedo": "real oviedo",
        "real oviedo": "real oviedo",
        "las palmas": "las palmas",
        "ud las palmas": "las palmas",
        "leganes": "leganes",
    }
    return aliases.get(t, t)


def _teams_compatible(a: str, b: str) -> bool | None:
    """True = mismo equipo, False = distinto, None = no se puede decidir."""
    if not a or not b:
        return None
    if a == b or a in b or b in a:
        return True
    # Tokens significativos (≥4) compartidos
    ta = {t for t in a.split() if len(t) >= 4}
    tb = {t for t in b.split() if len(t) >= 4}
    if ta and tb and ta & tb:
        return True
    return False


def _split_initial_surname(norm: str) -> tuple[str | None, str, list[str]]:
    """
    ('k', 'sanchez', ['k','sanchez']) para 'k sanchez'
    (None, 'juanlu', ['juanlu','sanchez']) → surname = last token
    """
    parts = [p for p in (norm or "").split() if p]
    if not parts:
        return None, "", []
    initial = parts[0] if len(parts[0]) == 1 and len(parts) >= 2 else None
    surname = parts[-1]
    return initial, surname, parts


def _initial_compatible(mister_initial: str | None, cand_parts: list[str]) -> bool:
    """
    Si Mister usa 'K. Sánchez', el candidato debe empezar por K
    (Kevin, Kike, K. …), no por J (Juanlu).
    """
    if not mister_initial:
        return True
    if not cand_parts:
        return False
    first = cand_parts[0]
    if len(first) == 1:
        return first == mister_initial
    # Nombre de pila o apodo compuesto pegado (juanlu, joselu…)
    if first.startswith(mister_initial):
        return True
    # A veces el primer token es apellido compuesto raro; revisar tokens dados
    for tok in cand_parts[:-1]:
        if tok.startswith(mister_initial):
            return True
    return False


def match_player(
    mister_name: str,
    mister_team: str | None,
    candidates: list[dict[str, Any]],
    threshold: int = MATCH_THRESHOLD,
) -> tuple[dict[str, Any] | None, int]:
    """
    Devuelve (candidato, score) o (None, 0).
    candidates: dicts con al menos 'name' y opcionalmente 'team'.

    Reglas anti-colisión (p.ej. K. Sánchez ≠ Juanlu Sánchez):
    - Si Mister viene como 'X. Apellido', exige inicial compatible.
    - Equipo distinto penaliza; mismo equipo bonifica.
    - No se acepta un match solo por apellido compartido.
    """
    target = normalize_name(mister_name)
    team_n = normalize_team(mister_team or "")
    if not target or not candidates:
        return None, 0

    t_initial, t_surname, t_parts = _split_initial_surname(target)

    best: dict[str, Any] | None = None
    best_score = 0
    for cand in candidates:
        cname = normalize_name(str(cand.get("name") or ""))
        if not cname:
            continue
        c_initial, c_surname, c_parts = _split_initial_surname(cname)

        # Hard gate: inicial abreviada incompatible → descartar
        if t_initial and not _initial_compatible(t_initial, c_parts):
            continue
        # Si el candidato también está abreviado y Mister no, simétrico
        if c_initial and not t_initial and not _initial_compatible(c_initial, t_parts):
            continue

        score = fuzz.token_set_ratio(target, cname)

        # Score más fiable cuando Mister es "X. Apellido"
        if t_initial and t_surname:
            if c_surname == t_surname and _initial_compatible(t_initial, c_parts):
                # Inicial + apellido exacto: base alta sin depender de token_set
                score = max(score, 92)
            elif fuzz.ratio(t_surname, c_surname) >= 90 and _initial_compatible(t_initial, c_parts):
                score = max(score, 88)
            else:
                # Sin apellido alineado, token_set solo no basta
                score = min(score, fuzz.token_sort_ratio(target, cname))

        # Bonus clásico por inicial + apellido (L. Yamal ↔ Lamine Yamal)
        if t_parts and c_parts and len(t_parts[0]) == 1 and t_parts[0] == c_parts[0][:1]:
            if t_parts[-1] == c_parts[-1]:
                score = max(score, min(100, fuzz.ratio(t_parts[-1], c_parts[-1]) + 12))

        cteam = normalize_team(str(cand.get("team") or ""))
        same_team = _teams_compatible(team_n, cteam)
        strong_abbrev = bool(
            t_initial
            and t_surname
            and c_surname == t_surname
            and _initial_compatible(t_initial, c_parts)
        )
        if same_team is True:
            score = min(100, score + 8)
        elif same_team is False:
            if strong_abbrev:
                # Mister a veces trae club desfasado; no tumbar identidad clara X.+apellido
                score -= 4
            else:
                # Penalización fuerte si el nombre es ambiguo (solo apellido fuzzy)
                score -= 28

        score = max(0, min(100, score))
        if score > best_score:
            best_score = score
            best = cand

    if best is None or best_score < threshold:
        return None, best_score
    return best, best_score
