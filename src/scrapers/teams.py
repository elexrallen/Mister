"""Mapeo de nombres de club Mister → slugs FF / JP."""

from __future__ import annotations

from .name_match import normalize_team

# slug compartido (FF y JP usan casi los mismos)
_TEAM_SLUGS: dict[str, str] = {
    "alaves": "alaves",
    "deportivo alaves": "alaves",
    "athletic": "athletic",
    "athletic club": "athletic",
    "athletic bilbao": "athletic",
    "atletico": "atletico",
    "atletico madrid": "atletico",
    "atletico de madrid": "atletico",
    "barcelona": "barcelona",
    "barca": "barcelona",
    "fc barcelona": "barcelona",
    "betis": "betis",
    "real betis": "betis",
    "celta": "celta",
    "celta vigo": "celta",
    "rc celta": "celta",
    "deportivo": "deportivo",
    "deportivo la coruna": "deportivo",
    "elche": "elche",
    "espanyol": "espanyol",
    "rcd espanyol": "espanyol",
    "getafe": "getafe",
    "girona": "girona",
    "levante": "levante",
    "malaga": "malaga",
    "mallorca": "mallorca",
    "osasuna": "osasuna",
    "racing": "racing",
    "racing santander": "racing",
    "rayo": "rayo-vallecano",
    "rayo vallecano": "rayo-vallecano",
    "real madrid": "real-madrid",
    "real sociedad": "real-sociedad",
    "sevilla": "sevilla",
    "valencia": "valencia",
    "villarreal": "villarreal",
    "valladolid": "valladolid",
}


def team_slug(team: str | None) -> str | None:
    if not team:
        return None
    key = normalize_team(team)
    if key in _TEAM_SLUGS:
        return _TEAM_SLUGS[key]
    # intentar primer token / sin espacios
    compact = key.replace(" ", "-")
    if compact in _TEAM_SLUGS.values():
        return compact
    return _TEAM_SLUGS.get(key.split()[0]) if key else None


def display_team_from_slug(slug: str) -> str:
    rev = {
        "alaves": "Alavés",
        "athletic": "Athletic",
        "atletico": "Atlético",
        "barcelona": "Barcelona",
        "betis": "Betis",
        "celta": "Celta",
        "deportivo": "Deportivo",
        "elche": "Elche",
        "espanyol": "Espanyol",
        "getafe": "Getafe",
        "girona": "Girona",
        "levante": "Levante",
        "malaga": "Málaga",
        "mallorca": "Mallorca",
        "osasuna": "Osasuna",
        "racing": "Racing",
        "rayo-vallecano": "Rayo Vallecano",
        "real-madrid": "Real Madrid",
        "real-sociedad": "Real Sociedad",
        "sevilla": "Sevilla",
        "valencia": "Valencia",
        "villarreal": "Villarreal",
        "valladolid": "Valladolid",
    }
    return rev.get(slug, slug.replace("-", " ").title())
