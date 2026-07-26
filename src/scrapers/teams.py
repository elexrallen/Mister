"""Mapeo de nombres de club Mister → slugs FF / JP."""

from __future__ import annotations

from .name_match import normalize_team

# slug compartido (FF y JP usan casi los mismos)
_TEAM_SLUGS: dict[str, str] = {
    # --- LaLiga ---
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
    # --- Premier League (FF path /premier-league/equipos/{slug}) ---
    "arsenal": "arsenal",
    "aston villa": "aston-villa",
    "villa": "aston-villa",
    "bournemouth": "bournemouth",
    "afc bournemouth": "bournemouth",
    "brentford": "brentford",
    "brighton": "brighton",
    "brighton hove albion": "brighton",
    "brighton and hove albion": "brighton",
    "chelsea": "chelsea",
    "coventry": "coventrycity",
    "coventry city": "coventrycity",
    "crystal palace": "crystal-palace",
    "palace": "crystal-palace",
    "everton": "everton",
    "fulham": "fulham",
    "hull": "hullcity",
    "hull city": "hullcity",
    "ipswich": "ipswich",
    "ipswich town": "ipswich",
    "leeds": "leeds-united",
    "leeds united": "leeds-united",
    "liverpool": "liverpool",
    "manchester city": "manchester-city",
    "man city": "manchester-city",
    "manchester united": "manchester-united",
    "man united": "manchester-united",
    "man utd": "manchester-united",
    "newcastle": "newcastle",
    "newcastle united": "newcastle",
    "nottingham forest": "nottingham-forest",
    "nottm forest": "nottingham-forest",
    "forest": "nottingham-forest",
    "sunderland": "sunderland",
    "tottenham": "tottenham",
    "tottenham hotspur": "tottenham",
    "spurs": "tottenham",
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
    # FF a veces usa slug sin guiones (coventrycity)
    compact_nosep = key.replace(" ", "")
    if compact_nosep in _TEAM_SLUGS.values():
        return compact_nosep
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
        "arsenal": "Arsenal",
        "aston-villa": "Aston Villa",
        "bournemouth": "Bournemouth",
        "brentford": "Brentford",
        "brighton": "Brighton",
        "chelsea": "Chelsea",
        "coventrycity": "Coventry City",
        "crystal-palace": "Crystal Palace",
        "everton": "Everton",
        "fulham": "Fulham",
        "hullcity": "Hull City",
        "ipswich": "Ipswich",
        "leeds-united": "Leeds United",
        "liverpool": "Liverpool",
        "manchester-city": "Manchester City",
        "manchester-united": "Manchester United",
        "newcastle": "Newcastle",
        "nottingham-forest": "Nottingham Forest",
        "sunderland": "Sunderland",
        "tottenham": "Tottenham",
    }
    return rev.get(slug, slug.replace("-", " ").title())
