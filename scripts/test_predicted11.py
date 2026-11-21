"""Regresión: Predicted11 cableado para Premier/Serie A y merge de titularidad."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from external_data import _apply_predicted11_overlay  # noqa: E402
from scrapers import PRED11_SUPPORTED, SUPPORTED_EXTERNAL, fetch_all_external  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

from scrapers.predicted11 import (  # noqa: E402
    PRED11_SLUG,
    _news_links_for_jornada,
    _parse_news_href,
    _pick_expert_urls,
    _records_from_counts,
    _records_from_popularidad,
)


def _assert(cond: bool, msg: str = "") -> None:
    if not cond:
        raise AssertionError(msg or "assertion failed")


def test_pred11_supported_keys() -> None:
    _assert(PRED11_SUPPORTED == frozenset({"premier", "seriea"}))
    _assert(PRED11_SLUG["premier"] == "premier-league")
    _assert(PRED11_SLUG["seriea"] == "serie-a")
    _assert("seriea" in SUPPORTED_EXTERNAL)


def test_pick_expert_urls_prefers_rank() -> None:
    ranked = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta"]
    news = [
        ("Zeta", "https://example/zeta"),
        ("Beta", "https://example/beta"),
        ("Alpha", "https://example/alpha"),
        ("Other", "https://example/other"),
    ]
    picked = _pick_expert_urls(ranked, news, limit=3)
    _assert(picked == [
        "https://example/alpha",
        "https://example/beta",
        "https://example/zeta",
    ], picked)


def test_records_from_counts_pct() -> None:
    counts = {
        ("Moreno", "Venezia"): 4,
        ("Bench", "Venezia"): 1,
    }
    rows = _records_from_counts(counts, n_experts=5)
    by_name = {r["name"]: r for r in rows}
    _assert(by_name["Moreno"]["lineup_prob"] == 80, by_name["Moreno"])
    _assert(by_name["Moreno"]["source"] == "predicted11")
    _assert(by_name["Moreno"]["is_recommendation"] is True)
    _assert(by_name["Bench"]["lineup_prob"] == 20)


def test_overlay_authority_premier() -> None:
    candidates = [
        {
            "name": "M. Moreno",
            "team": "Venezia",
            "availability": "available",
            "lineup_prob": 40,
            "sources": ["futbolfantasy"],
            "gw_lineup_prob": 40,
        }
    ]
    pred11 = [
        {
            "name": "Moreno",
            "team": "Venezia",
            "lineup_prob": 80,
            "source": "predicted11",
        }
    ]
    out = _apply_predicted11_overlay(candidates, pred11, authority=True)
    row = out[0]
    _assert(row["gw_lineup_prob"] == 80, row)
    _assert(row["lineup_prob"] == 80, row)
    _assert(row["lineup_prob_source"] == "predicted11", row)
    _assert(row["gw_starter"] is True, row)


def test_overlay_demotes_bench_when_team_covered() -> None:
    """J. Cunha (Nottingham) no está en XI Pred11 de Forest → no titular."""
    # 11 titulares Pred11 en Nott. Forest + Cunha solo en FF al 70%
    forest_xi = [
        "Murillo",
        "N. Williams",
        "Ola Aina",
        "Gibbs-White",
        "Ndoye",
        "Delap",
        "X. Schlager",
        "McAtee",
        "Sels",
        "Muñoz",
        "O. Diomande",
    ]
    pred11 = [
        {"name": n, "team": "Nott. Forest", "lineup_prob": 100, "source": "predicted11"}
        for n in forest_xi
    ]
    candidates = [
        {
            "name": "Jair Cunha",
            "team": "Nottingham",
            "availability": "available",
            "lineup_prob": 70,
            "sources": ["futbolfantasy"],
            "gw_lineup_prob": 70,
        },
        {
            "name": "Murillo",
            "team": "Nottingham Forest",
            "availability": "available",
            "lineup_prob": 80,
            "sources": ["futbolfantasy"],
            "gw_lineup_prob": 80,
        },
    ]
    out = _apply_predicted11_overlay(candidates, pred11, authority=True)
    by_name = {r["name"]: r for r in out}
    cunha = by_name["Jair Cunha"]
    murillo = by_name["Murillo"]
    _assert(cunha["gw_lineup_prob"] == 18, cunha)
    _assert(cunha["lineup_prob"] == 18, cunha)
    _assert(cunha["gw_starter"] is False, cunha)
    _assert(cunha["lineup_prob_source"] == "predicted11", cunha)
    _assert(murillo["gw_lineup_prob"] == 100, murillo)
    _assert(murillo["gw_starter"] is True, murillo)
    _assert(cunha.get("gw_out") is not True, cunha)
    _assert(murillo.get("gw_out") is not True, murillo)


def test_news_href_parses_new_and_old_slugs() -> None:
    slug = "premier-league"
    new_url = (
        "https://www.predicted11.com/es/premier-league/noticias/"
        "alineaciones-probables-de-futbolfantasy-para-la-jornada-11-de-la-premier-league"
    )
    old_url = (
        "https://www.predicted11.com/es/premier-league/noticias/"
        "alineaciones-probables-de-javiig99-jornada-7-de-premier-league"
    )
    serie_url = (
        "https://www.predicted11.com/es/serie-a/noticias/"
        "alineaciones-probables-de-javiig99-para-la-jornada-12-de-la-serie-a"
    )
    _assert(_parse_news_href(new_url, slug) == ("futbolfantasy", 11), _parse_news_href(new_url, slug))
    _assert(_parse_news_href(old_url, slug) == ("javiig99", 7), _parse_news_href(old_url, slug))
    _assert(_parse_news_href(serie_url, "serie-a") == ("javiig99", 12))
    _assert(_parse_news_href(new_url, "serie-a") is None)


def test_news_links_keep_current_jornada_only() -> None:
    """El regex viejo ignoraba J11 y reciclaba J7: titularidades de hace un mes."""
    html = """
    <a href="/es/premier-league/noticias/alineaciones-probables-de-futbolfantasy-para-la-jornada-11-de-la-premier-league">J11</a>
    <a href="/es/premier-league/noticias/alineaciones-probables-de-javiig99-jornada-7-de-premier-league">J7</a>
    """
    soup = BeautifulSoup(html, "lxml")
    news = _news_links_for_jornada(soup, "premier-league", 11)
    _assert(len(news) == 1, news)
    _assert("jornada-11" in news[0][1], news[0])
    stale = _news_links_for_jornada(soup, "premier-league", 11)
    _assert(all("jornada-7" not in url for _u, url in stale), stale)
    # Sin jornada conocida, sí se puede usar la más alta parseable.
    any_j = _news_links_for_jornada(soup, "premier-league", None)
    _assert(len(any_j) == 1, any_j)
    _assert("jornada-11" in any_j[0][1], any_j)


def test_records_from_popularidad_pct() -> None:
    payload = {
        "success": True,
        "jugadores": [
            {"nombre": "David Raya", "porcentaje": 100},
            {"nombre": "Viktor Gyökeres", "porcentaje": 3},
            {"nombre_corto": "Saka", "porcentaje_decimal": 86},
        ],
    }
    rows = _records_from_popularidad(payload, "Arsenal")
    by_name = {r["name"]: r for r in rows}
    _assert(by_name["David Raya"]["lineup_prob"] == 100)
    _assert(by_name["David Raya"]["team"] == "Arsenal")
    _assert(by_name["Viktor Gyökeres"]["lineup_prob"] == 3)
    _assert(by_name["Viktor Gyökeres"]["is_recommendation"] is False)
    _assert(by_name["Saka"]["lineup_prob"] == 86)
    _assert(_records_from_popularidad({"success": False}, "Arsenal") == [])


def test_fetch_prefers_popularidad_over_stale_news() -> None:
    """Hub J11 + API popularidad; las noticias J7 no deben entrar."""
    from scrapers import predicted11 as mod

    hub_html = """
    <html><body>
      <h1>Alineaciones Jornada 11</h1>
      <a href="/es/premier-league/equipo/arsenal" title="Arsenal" data-equipo-id="500"></a>
      <a href="/es/premier-league/noticias/alineaciones-probables-de-javiig99-jornada-7-de-premier-league">J7</a>
    </body></html>
    """
    team_html = """
    <html><body>
      <script>
        window.currentTemporada = 145;
        window.jornada = 11;
      </script>
    </body></html>
    """
    soup_hub = BeautifulSoup(hub_html, "lxml")
    soup_team = BeautifulSoup(team_html, "lxml")
    payload = {
        "success": True,
        "jugadores": [
            {"nombre": "David Raya", "porcentaje": 100},
            {"nombre": "Viktor Gyökeres", "porcentaje": 3},
        ],
    }

    def fake_soup(url: str, timeout: int = 12):
        if "/equipo/" in url:
            return soup_team
        return soup_hub

    with (
        patch.object(mod, "get_soup", side_effect=fake_soup),
        patch.object(mod, "get_json", return_value=payload) as m_json,
    ):
        rows = mod.fetch_predicted11(competition="premier")
    _assert(m_json.called)
    by_name = {r["name"]: r for r in rows}
    _assert(by_name["David Raya"]["lineup_prob"] == 100, rows)
    _assert(by_name["Viktor Gyökeres"]["lineup_prob"] == 3, rows)
    _assert(by_name["David Raya"]["team"] == "Arsenal")


def test_fetch_all_external_wires_pred11() -> None:
    fake_pred = [
        {
            "name": "Test Player",
            "team": "Test",
            "lineup_prob": 100,
            "source": "predicted11",
            "availability": "available",
            "is_recommendation": True,
            "is_chollo": False,
            "profile_url": None,
        }
    ]
    with (
        patch("scrapers.fetch_futbolfantasy", return_value=[{"name": "X", "source": "futbolfantasy"}]),
        patch(
            "scrapers.fetch_ff_matchday",
            return_value={"status": "ok", "fixtures": [], "players": []},
        ),
        patch("scrapers.fetch_jornadaperfecta", return_value=[]),
        patch("scrapers.fetch_predicted11", return_value=fake_pred) as m_pred,
    ):
        bundle = fetch_all_external(competition="seriea")
        _assert(m_pred.called)
        _assert(bundle["status"]["predicted11"] == "ok", bundle["status"])
        _assert(bundle["predicted11"] == fake_pred)
        bundle_ll = fetch_all_external(competition="laliga")
        _assert(bundle_ll["status"]["predicted11"] == "skip", bundle_ll["status"])
        _assert(bundle_ll["predicted11"] == [])


def main() -> int:
    test_pred11_supported_keys()
    test_pick_expert_urls_prefers_rank()
    test_records_from_counts_pct()
    test_overlay_authority_premier()
    test_overlay_demotes_bench_when_team_covered()
    test_news_href_parses_new_and_old_slugs()
    test_news_links_keep_current_jornada_only()
    test_records_from_popularidad_pct()
    test_fetch_prefers_popularidad_over_stale_news()
    test_fetch_all_external_wires_pred11()
    print("OK predicted11")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
