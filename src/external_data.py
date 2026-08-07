"""
Fachada de enriquecimiento externo Fantasy.

Fusiona Fútbol Fantasy + Jornada Perfecta + Comuniate (best-effort).
Sofascore API desactivada; la nota reciente la aporta FotMob en data_engine.
Caché en disco (TTL 12h) y seed de respaldo. Nunca tumba el pipeline.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scrapers import fetch_all_external
from scrapers.comuniate import enrich_profiles_for_names
from scrapers.name_match import match_player
from scrapers.ff_points import (
    THIN_APPS,
    apps_to_lineup_prob,
    default_ff_seasons,
    fetch_ff_mister_points,
    is_top_production,
    production_score,
)
from scrapers.ff_profile import fetch_ff_profile_titular, reset_ff_profile_fetch_budget

log = logging.getLogger("external_data")

SRC_DIR = Path(__file__).resolve().parent
CACHE_PATH = SRC_DIR / "cache" / "external_latest.json"  # legacy LaLiga
SEED_PATH = SRC_DIR / "external_seed.json"


def _cache_path_for(competition: str) -> Path:
    comp = (competition or "laliga").strip().lower()
    if comp in ("", "laliga"):
        return CACHE_PATH
    return SRC_DIR / "cache" / f"external_latest_{comp}.json"

AVAIL_PRIO = {
    "suspended": 4,
    "injured": 3,
    "doubt": 2,
    "available": 1,
    "unknown": 0,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_json(path: Path) -> Any | None:
    try:
        if not path.is_file():
            return None
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo leer %s: %s", path, exc)
        return None


def _save_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo escribir %s: %s", path, exc)


def _merge_source_records(
    ff: list[dict[str, Any]],
    jp: list[dict[str, Any]],
    com: list[dict[str, Any]],
    sofa: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}

    def ensure(r: dict[str, Any]) -> dict[str, Any] | None:
        key = (r.get("name") or "").strip().lower()
        if not key:
            return None
        if key not in by_name:
            by_name[key] = {
                "name": r.get("name"),
                "team": r.get("team"),
                "availability": r.get("availability") or "unknown",
                "lineup_prob": r.get("lineup_prob"),
                "is_chollo": bool(r.get("is_chollo")),
                "is_recommendation": bool(r.get("is_recommendation")),
                "sofascore_avg_5": r.get("sofascore_avg_5"),
                "points_streak": r.get("points_streak") or "unknown",
                "profile_url": r.get("profile_url"),
                "sofascore_id": r.get("sofascore_id"),
                "sources": [r["source"]] if r.get("source") else [],
                "_ff_prob": r.get("source") == "futbolfantasy" and r.get("lineup_prob") is not None,
            }
            return by_name[key]
        return by_name[key]

    def touch_meta(existing: dict[str, Any], r: dict[str, Any]) -> None:
        src = r.get("source")
        if src and src not in existing["sources"]:
            existing["sources"].append(src)
        if r.get("team") and not existing.get("team"):
            existing["team"] = r["team"]
        if r.get("is_chollo"):
            existing["is_chollo"] = True
        if r.get("is_recommendation"):
            existing["is_recommendation"] = True
        if r.get("sofascore_avg_5") is not None:
            existing["sofascore_avg_5"] = r["sofascore_avg_5"]
        if r.get("points_streak") and r["points_streak"] != "unknown":
            if existing.get("points_streak") in (None, "unknown"):
                existing["points_streak"] = r["points_streak"]
        if r.get("sofascore_id"):
            existing["sofascore_id"] = r["sofascore_id"]
        if r.get("profile_url"):
            cur = existing.get("profile_url") or ""
            new = str(r["profile_url"])
            # Preferir ficha de jugador FF; evitar quedarse con /partido/ de JP
            def _rank(u: str) -> int:
                s = str(u or "")
                if "futbolfantasy.com/jugadores/" in s:
                    return 5
                if "jornadaperfecta.com/jugador/" in s:
                    return 4
                if "comuniate.com" in s and "/jugador" in s:
                    return 3
                if "/partido/" in s:
                    return 0
                if s:
                    return 2
                return 0

            if _rank(new) >= _rank(cur):
                existing["profile_url"] = new

    for r in ff:
        existing = ensure(r)
        if not existing:
            continue
        touch_meta(existing, r)
        if r.get("lineup_prob") is not None:
            existing["lineup_prob"] = r["lineup_prob"]
            existing["_ff_prob"] = True
        if r.get("availability") and r["availability"] != "unknown":
            existing["availability"] = r["availability"]

    for r in jp:
        existing = ensure(r)
        if not existing:
            continue
        touch_meta(existing, r)
        if AVAIL_PRIO.get(r.get("availability"), 0) > AVAIL_PRIO.get(existing.get("availability"), 0):
            existing["availability"] = r["availability"]
        if not existing.get("_ff_prob") and r.get("lineup_prob") is not None:
            if existing.get("lineup_prob") is None:
                existing["lineup_prob"] = r["lineup_prob"]
            elif existing.get("availability") == "available":
                existing["lineup_prob"] = max(existing.get("lineup_prob") or 0, r["lineup_prob"])

    for r in com + sofa:
        existing = ensure(r)
        if not existing:
            continue
        touch_meta(existing, r)

    for rec in by_name.values():
        rec.pop("_ff_prob", None)
    return list(by_name.values())


def _empty_external() -> dict[str, Any]:
    return {
        "availability": "unknown",
        "lineup_prob_ext": None,
        "is_chollo_ext": False,
        "is_recommendation_ext": False,
        "sofascore_avg_5": None,
        "points_streak": "unknown",
        "profile_url": None,
        "matched_name": None,
        "match_score": 0,
        "ff_mister_avg": None,
        "ff_mister_points": None,
        "ff_apps": None,
        "ff_season": None,
        "ff_prior_avg": None,
        "ff_prior_season": None,
        "ff_prior_apps": None,
        "is_top_ff": False,
        "top_reason": None,
        "production_score": None,
        "gw_lineup_prob": None,
        "gw_role": None,
        "gw_starter": False,
        "gw_doubt": False,
        "gw_out": False,
        "gw_opponent": None,
        "gw_fixture_id": None,
        "ff_avg_scale": None,
        "ff_scoring": None,
        "lineup_prob_source": None,
    }


def _load_candidates_from_cache_or_seed(
    meta: dict[str, Any],
    *,
    competition: str = "laliga",
) -> list[dict[str, Any]]:
    cache = _load_json(_cache_path_for(competition))
    if isinstance(cache, dict) and cache.get("players"):
        meta["cache_used"] = True
        for k in ("futbolfantasy", "jornadaperfecta", "comuniate", "sofascore"):
            meta[k] = "cache"
        log.info(
            "Usando caché externa [%s] (%d jugadores)",
            competition,
            len(cache["players"]),
        )
        return list(cache["players"])

    # Seed solo LaLiga (nombres/clubes españoles)
    if (competition or "laliga").strip().lower() == "laliga":
        seed = _load_json(SEED_PATH)
        if isinstance(seed, list) and seed:
            meta["cache_used"] = False
            for k in ("futbolfantasy", "jornadaperfecta", "comuniate"):
                meta[k] = "fail"
            meta["sofascore"] = "skip"
            meta["errors"].append("fallback_seed")
            log.info("Usando external_seed.json (%d)", len(seed))
            return seed
    return []


def _overlay_sofa_like(candidates: list[dict[str, Any]], recs: list[dict[str, Any]]) -> None:
    for s in recs:
        hit, score = match_player(s.get("name") or "", s.get("team"), candidates, threshold=80)
        if hit and s.get("sofascore_avg_5") is not None:
            hit["sofascore_avg_5"] = s["sofascore_avg_5"]
            if s.get("sofascore_id"):
                hit["sofascore_id"] = s["sofascore_id"]
            if s.get("profile_url"):
                hit["profile_url"] = hit.get("profile_url") or s["profile_url"]
            src = s.get("source") or "sofascore"
            if src not in hit.get("sources", []):
                hit.setdefault("sources", []).append(src)
            if s.get("points_streak") and s["points_streak"] != "unknown":
                hit["points_streak"] = s["points_streak"]
        elif s.get("sofascore_avg_5") is not None or s.get("sofascore_id"):
            candidates.append({
                "name": s.get("name"),
                "team": s.get("team"),
                "availability": "unknown",
                "lineup_prob": None,
                "is_chollo": bool(s.get("is_chollo")),
                "is_recommendation": bool(s.get("is_recommendation")),
                "sofascore_avg_5": s.get("sofascore_avg_5"),
                "points_streak": s.get("points_streak") or "unknown",
                "profile_url": s.get("profile_url"),
                "sofascore_id": s.get("sofascore_id"),
                "sources": [s.get("source") or "sofascore"],
            })


def _apply_matchday_overlay(
    candidates: list[dict[str, Any]],
    matchday: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """
    Pisa lineup_prob con % de la previa de jornada (más fresco).
    Añade flags gw_role / gw_lineup_prob en el candidato.
    """
    if not matchday:
        return candidates
    md_players = list(matchday.get("players") or [])
    if not md_players:
        return candidates

    # Índice por nombre lower
    by_key: dict[str, dict[str, Any]] = {
        (c.get("name") or "").strip().lower(): c for c in candidates if c.get("name")
    }

    for mp in md_players:
        name = (mp.get("name") or "").strip()
        key = name.lower()
        if not key:
            continue
        pct = mp.get("lineup_prob")
        try:
            pct_i = int(pct) if pct is not None else None
        except (TypeError, ValueError):
            pct_i = None
        role = mp.get("role") or "bench"
        gw_starter = bool(role == "starter" and pct_i is not None and pct_i >= 70)
        gw_doubt = bool(pct_i is not None and 40 <= pct_i < 70)
        gw_out = bool(pct_i is not None and pct_i < 40)

        existing = by_key.get(key)
        if existing:
            # Matchday pisa % (salvo lesionado/sancionado FF con prio)
            avail = existing.get("availability") or "unknown"
            if avail not in ("injured", "suspended") and pct_i is not None:
                existing["lineup_prob"] = pct_i
            elif avail in ("injured", "suspended") and pct_i is not None and pct_i < (
                existing.get("lineup_prob") or 999
            ):
                existing["lineup_prob"] = pct_i
            existing["gw_lineup_prob"] = pct_i
            existing["gw_role"] = role
            existing["gw_starter"] = gw_starter
            existing["gw_doubt"] = gw_doubt
            existing["gw_out"] = gw_out
            existing["gw_opponent"] = mp.get("opponent")
            existing["gw_fixture_id"] = mp.get("fixture_id")
            if pct_i is not None and pct_i >= 80:
                existing["is_recommendation"] = True
            src = "futbolfantasy_matchday"
            if src not in (existing.get("sources") or []):
                existing.setdefault("sources", []).append(src)
            if mp.get("profile_url") and (
                not existing.get("profile_url")
                or "/partido/" in str(existing.get("profile_url"))
            ):
                existing["profile_url"] = mp["profile_url"]
            if mp.get("team") and not existing.get("team"):
                existing["team"] = mp["team"]
        else:
            rec = {
                "name": name,
                "team": mp.get("team"),
                "availability": "available",
                "lineup_prob": pct_i,
                "is_chollo": False,
                "is_recommendation": bool(pct_i is not None and pct_i >= 80),
                "sofascore_avg_5": None,
                "points_streak": "unknown",
                "profile_url": mp.get("profile_url"),
                "sofascore_id": None,
                "sources": ["futbolfantasy_matchday"],
                "gw_lineup_prob": pct_i,
                "gw_role": role,
                "gw_starter": gw_starter,
                "gw_doubt": gw_doubt,
                "gw_out": gw_out,
                "gw_opponent": mp.get("opponent"),
                "gw_fixture_id": mp.get("fixture_id"),
            }
            by_key[key] = rec
            candidates.append(rec)

    return candidates


def enrich_players_with_external(
    players: list[dict[str, Any]],
    *,
    competition: str = "laliga",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Devuelve jugadores enriquecidos + meta
    {futbolfantasy, jornadaperfecta, comuniate, sofascore, ff_matchday, matched, ...}.
    competition: `laliga` | `premier`.
    """
    comp = (competition or "laliga").strip().lower() or "laliga"
    meta: dict[str, Any] = {
        "futbolfantasy": "fail",
        "jornadaperfecta": "fail",
        "comuniate": "fail",
        "sofascore": "skip",
        "ff_matchday": "fail",
        "matched": 0,
        "cache_used": False,
        "errors": [],
        "sofascore_filled": 0,
        "competition": comp,
        "matchday": None,
    }

    team_names = list({str(p.get("team") or "") for p in players if p.get("team")})
    candidates: list[dict[str, Any]] = []
    matchday: dict[str, Any] | None = None

    try:
        bundle = fetch_all_external(
            team_names,
            competition=comp,
            sofascore_candidates=None,
        )
        status = dict(bundle.get("status") or {})
        com_catalog = bundle.get("comuniate") or []
        matchday = bundle.get("ff_matchday") if isinstance(bundle.get("ff_matchday"), dict) else None
        candidates = _merge_source_records(
            bundle.get("futbolfantasy") or [],
            bundle.get("jornadaperfecta") or [],
            com_catalog,
            [],
        )
        candidates = _apply_matchday_overlay(candidates, matchday)

        # 1) Fichas Comuniate (solo LaLiga) → sofascore_id + media fallback
        if comp == "laliga" and com_catalog:
            mister_names = [str(p.get("name") or "") for p in players if p.get("name")]
            com_enriched = enrich_profiles_for_names(
                mister_names,
                catalog=com_catalog,
                limit=min(20, max(8, len(mister_names))),
            )
            if com_enriched:
                _overlay_sofa_like(candidates, com_enriched)
                if status.get("comuniate") == "ok":
                    status["comuniate"] = "ok"
                n_com_avg = sum(1 for c in com_enriched if c.get("sofascore_avg_5") is not None)
                log.info("Comuniate medias en fichas: %s", n_com_avg)

        # Sofascore API desactivada (403 habitual). La nota la aporta FotMob
        # en data_engine; Comuniate puede dejar medias parciales como fallback.
        status["sofascore"] = "skip"
        filled = sum(1 for c in candidates if c.get("sofascore_avg_5") is not None)
        meta["sofascore_filled"] = filled
        for k, v in status.items():
            meta[k] = v

        if candidates:
            _save_json(_cache_path_for(comp), {
                "fetched_at": _now().isoformat(),
                "players": candidates,
                "status": status,
                "competition": comp,
                "matchday": {
                    "jornada": (matchday or {}).get("jornada"),
                    "fixtures_count": len((matchday or {}).get("fixtures") or []),
                    "status": (matchday or {}).get("status"),
                },
            })
        else:
            candidates = _load_candidates_from_cache_or_seed(meta, competition=comp)
    except Exception as exc:  # noqa: BLE001
        log.warning("Scrape externo [%s] falló: %s", comp, exc)
        meta["errors"].append(str(exc))
        candidates = _load_candidates_from_cache_or_seed(meta, competition=comp)

    enriched: list[dict[str, Any]] = []
    matched = 0
    sofa_on_players = 0
    for p in players:
        ext = _empty_external()
        best, score = match_player(p.get("name") or "", p.get("team"), candidates)
        new_p = dict(p)
        if best:
            matched += 1
            avail = best.get("availability") or "unknown"
            ext = {
                "availability": avail,
                "lineup_prob_ext": best.get("lineup_prob"),
                "is_chollo_ext": bool(best.get("is_chollo")),
                "is_recommendation_ext": bool(best.get("is_recommendation")),
                "sofascore_avg_5": best.get("sofascore_avg_5"),
                "points_streak": best.get("points_streak") or "unknown",
                "profile_url": best.get("profile_url"),
                "matched_name": best.get("name"),
                "match_score": score,
                "gw_lineup_prob": best.get("gw_lineup_prob"),
                "gw_role": best.get("gw_role"),
                "gw_starter": bool(best.get("gw_starter")),
                "gw_doubt": bool(best.get("gw_doubt")),
                "gw_out": bool(best.get("gw_out")),
                "gw_opponent": best.get("gw_opponent"),
                "gw_fixture_id": best.get("gw_fixture_id"),
            }
            # Mister a veces trae escudos nuevos (Club 50…) → completar con club FF/Comuniate
            team_now = str(new_p.get("team") or "")
            ext_team = (best.get("team") or "").strip()
            if ext_team and (not team_now or team_now.lower().startswith("club ")):
                new_p["team"] = ext_team
                new_p["team_resolved_from"] = "external"
            if ext.get("sofascore_avg_5") is not None:
                sofa_on_players += 1
            if avail in ("injured", "suspended"):
                new_p["injury"] = True
            if ext.get("lineup_prob_ext") is not None:
                try:
                    new_p["lineup_prob"] = float(ext["lineup_prob_ext"]) / 100.0
                except (TypeError, ValueError):
                    pass
            # Top-level GW flags for scoring convenience
            new_p["gw_lineup_prob"] = ext.get("gw_lineup_prob")
            new_p["gw_role"] = ext.get("gw_role")
            new_p["gw_starter"] = ext.get("gw_starter")
            new_p["gw_doubt"] = ext.get("gw_doubt")
            new_p["gw_out"] = ext.get("gw_out")
        new_p["external"] = ext
        enriched.append(new_p)

    meta["matched"] = matched
    meta["sofascore_filled"] = sofa_on_players
    if sofa_on_players >= 5 and meta.get("sofascore") == "skip":
        meta["sofascore"] = "partial"

    # Resumen matchday para payload (sin listas enormes de jugadores por fixture)
    if matchday and matchday.get("status") not in (None, "fail", "skip"):
        meta["matchday"] = {
            "status": matchday.get("status"),
            "jornada": matchday.get("jornada"),
            "competition": matchday.get("competition") or comp,
            "fixtures_count": len(matchday.get("fixtures") or []),
            "players_count": len(matchday.get("players") or []),
            "fetched_at": matchday.get("fetched_at"),
            "cache_used": bool(matchday.get("cache_used")),
            "fixtures": [
                {
                    "id": f.get("id"),
                    "home": f.get("home"),
                    "away": f.get("away"),
                    "kickoff": f.get("kickoff"),
                    "url": f.get("url"),
                    "players_count": f.get("players_count") or len(f.get("players") or []),
                }
                for f in (matchday.get("fixtures") or [])[:12]
            ],
        }
        meta["ff_matchday"] = matchday.get("status") or meta.get("ff_matchday")
    return enriched, meta


def enrich_players_with_ff_production(
    players: list[dict[str, Any]],
    *,
    points_phase: str = "preseason",
    market_universe: list[dict[str, Any]] | None = None,
    competition: str = "laliga",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Añade medias FF (Mister Mixto / Fantasy RPG) + is_top_ff + production_score.
    Fail-soft: si scrape falla, intenta fallback TOP por percentil de precio de mercado.
    competition: `laliga` | `premier`.
    """
    comp = (competition or "laliga").strip().lower() or "laliga"
    meta: dict[str, Any] = {
        "ff_points": "fail",
        "matched": 0,
        "top_count": 0,
        "threshold": None,
        "fallback_price": False,
        "competition": comp,
        "scoring": None,
        "avg_scale": None,
    }
    bundle = fetch_ff_mister_points(competition=comp)
    meta["ff_points"] = bundle.get("status") or "fail"
    meta["threshold"] = bundle.get("threshold")
    meta["scoring"] = bundle.get("scoring")
    avg_scale = float(bundle.get("avg_scale") or 8.0)
    meta["avg_scale"] = avg_scale
    top_floor = float(bundle.get("top_floor") or 5.5)
    scoring_label = str(bundle.get("scoring") or ("Fantasy RPG" if comp == "premier" else "Mister Mixto"))

    seasons = bundle.get("seasons") or default_ff_seasons()
    by_season = bundle.get("by_season") or {}
    primary_key = str(seasons[0])
    prior_key = str(seasons[1]) if len(seasons) > 1 else None
    primary_recs = list(by_season.get(primary_key) or [])
    prior_recs = list(by_season.get(prior_key) or []) if prior_key else []
    # Pretemporada: si la temporada nueva está vacía/casi vacía, la previa completa es primaria
    if len(primary_recs) < 20 and prior_recs:
        primary_recs, prior_recs = prior_recs, primary_recs
        primary_key, prior_key = (prior_key or primary_key), primary_key
    threshold = float(bundle.get("threshold") or top_floor)

    reset_ff_profile_fetch_budget()

    # Índice prior por nombre lower
    prior_by_name: dict[str, dict[str, Any]] = {}
    for r in prior_recs:
        k = (r.get("name") or "").strip().lower()
        if k:
            prior_by_name[k] = r

    enriched: list[dict[str, Any]] = []
    matched = 0
    top_n = 0

    for p in players:
        new_p = dict(p)
        ext = dict(new_p.get("external") or _empty_external())
        hit = None
        score = 0
        profile_src = None
        if primary_recs:
            hit, score = match_player(p.get("name") or "", p.get("team"), primary_recs, threshold=82)
        avg = None
        points = None
        apps = 0
        prior_apps: int | None = None
        season = None
        prior_avg = None
        prior_season = None

        if hit:
            matched += 1
            profile_src = hit
            avg = hit.get("mister_avg")
            points = hit.get("mister_points")
            apps = int(hit.get("apps") or 0)
            season = hit.get("season_label") or hit.get("season")
            # Completar club desconocido (Club 50, etc.) con el de FF
            team_now = str(new_p.get("team") or "")
            ff_team = (hit.get("team") or "").strip()
            if ff_team and (not team_now or team_now.lower().startswith("club ")):
                new_p["team"] = ff_team
                new_p["team_resolved_from"] = "ff_mister"
            # prior por mismo nombre matcheado
            pk = (hit.get("name") or "").strip().lower()
            pref = prior_by_name.get(pk)
            if not pref:
                pref, _ = match_player(hit.get("name") or "", hit.get("team"), prior_recs, threshold=85) if prior_recs else (None, 0)
            if pref:
                prior_avg = pref.get("mister_avg")
                prior_season = pref.get("season_label") or pref.get("season")
                prior_apps = int(pref.get("apps") or 0)
                if not profile_src.get("profile_url") and pref.get("profile_url"):
                    profile_src = pref

        # Si no hay primary pero sí prior
        if avg is None and prior_recs and not hit:
            phit, pscore = match_player(p.get("name") or "", p.get("team"), prior_recs, threshold=82)
            if phit:
                matched += 1
                profile_src = phit
                prior_avg = phit.get("mister_avg")
                prior_season = phit.get("season_label") or phit.get("season")
                apps = int(phit.get("apps") or 0)
                prior_apps = apps
                score = pscore

        mister_form = None
        try:
            if p.get("mister_avg") is not None and float(p["mister_avg"]) > 0:
                mister_form = float(p["mister_avg"])
            elif p.get("form") is not None and float(p["form"]) > 0:
                mister_form = float(p["form"])
        except (TypeError, ValueError):
            mister_form = None

        lp = ext.get("lineup_prob_ext")
        lp_source = ext.get("lineup_prob_source")
        if lp is None and p.get("lineup_prob") is not None:
            try:
                lp = float(p["lineup_prob"]) * 100.0
                if lp_source is None:
                    lp_source = "mister_or_ext"
            except (TypeError, ValueError):
                lp = None

        # URL de ficha FF (analytics) para titular real si no hay widget de alineación
        ff_url = str((profile_src or {}).get("profile_url") or "") or None
        if not ff_url:
            ff_url = str(ext.get("profile_url") or "") or None
        if ff_url and "futbolfantasy.com/jugadores/" not in ff_url:
            ff_url = None

        # Proxy titularidad si FF/JP no dieron %
        # apps/38 castiga fichajes a mitad de temporada (Gallagher 16 PJ → 42% falso;
        # ficha FF: Titular 88%). Preferir % Titular de ficha; si no, apps/38 + suelo FotMob.
        apps_for_proxy: float | None = None
        apps_samples: list[float] = []
        if hit and apps >= THIN_APPS:
            apps_samples.append(float(apps))
        if prior_apps is not None and prior_apps >= THIN_APPS:
            apps_samples.append(float(prior_apps))
        elif prior_apps is not None and not apps_samples and prior_apps > 0:
            apps_samples.append(float(prior_apps))
        if not apps_samples and apps > 0:
            apps_samples.append(float(apps))
        if apps_samples:
            apps_for_proxy = sum(apps_samples) / len(apps_samples)

        if lp is None:
            profile_pct = None
            # Siempre preferir % Titular de ficha FF; apps/38 solo si FF no aporta valor usable
            if ff_url:
                prof = fetch_ff_profile_titular(ff_url)
                if prof and prof.get("titular_pct") is not None:
                    try:
                        profile_pct = int(prof["titular_pct"])
                    except (TypeError, ValueError):
                        profile_pct = None
                    try:
                        profile_apps_n = int(prof["apps"]) if prof.get("apps") is not None else 0
                    except (TypeError, ValueError):
                        profile_apps_n = 0
                    if profile_pct is not None:
                        ext["ff_starts"] = prof.get("starts")
                        ext["ff_profile_apps"] = prof.get("apps")
                    # Titular(100%) con 1 PJ (p.ej. Padilla) no es titularidad habitual
                    if profile_pct is not None and profile_apps_n < THIN_APPS:
                        profile_pct = None

            if profile_pct is not None:
                lp = float(profile_pct)
                lp_source = "ff_profile_titular"
            else:
                proxy_lp = apps_to_lineup_prob(apps_for_proxy)
                # Suelo: minutos recientes altos ⇒ no tratar como banquillo eterno
                fm = p.get("fotmob_stats") or {}
                try:
                    recent_mins = float(fm["minutos_ultimos_5"]) if fm.get("minutos_ultimos_5") is not None else None
                except (TypeError, ValueError):
                    recent_mins = None
                apps_ok_for_floor = (
                    apps_for_proxy is not None and float(apps_for_proxy) >= float(THIN_APPS)
                )
                # En pretemporada / muestra corta el suelo FotMob infla suplentes con minutos
                # puntuales (amistosos, copa, baja del titular).
                allow_fotmob_floor = points_phase == "active" or apps_ok_for_floor
                if (
                    proxy_lp is not None
                    and recent_mins is not None
                    and recent_mins >= 270
                    and allow_fotmob_floor
                ):
                    # ≥54'/partido en últimos 5 ⇒ al menos regular/titular usable
                    floored = max(int(proxy_lp), 70)
                    if floored > int(proxy_lp):
                        proxy_lp = floored
                        lp_source = "ff_apps_proxy_fotmob"
                    else:
                        lp_source = "ff_apps_proxy"
                elif proxy_lp is not None:
                    lp_source = "ff_apps_proxy"
                if proxy_lp is not None:
                    lp = float(proxy_lp)

            if lp is not None:
                ext["lineup_prob_ext"] = int(round(float(lp)))
                try:
                    new_p["lineup_prob"] = float(lp) / 100.0
                except (TypeError, ValueError):
                    pass

        ref_avg = avg if avg is not None else prior_avg
        is_top = (
            is_top_production(ref_avg, apps, threshold, top_floor=top_floor)
            if ref_avg is not None
            else False
        )
        reason = None
        if is_top and ref_avg is not None:
            reason = (
                f"FF {scoring_label} {float(ref_avg):.2f} · {apps} PJ "
                f"({season or prior_season or 'hist'})"
            )
            top_n += 1

        prod = production_score(
            avg=float(avg) if avg is not None else None,
            prior_avg=float(prior_avg) if prior_avg is not None else None,
            apps=apps,
            prior_apps=prior_apps,
            lineup_prob=float(lp) if lp is not None else None,
            mister_avg=mister_form,
            points_phase=points_phase,
            avg_scale=avg_scale,
        )

        ext.update(
            {
                "ff_mister_avg": round(float(avg), 2) if avg is not None else None,
                "ff_mister_points": round(float(points), 1) if points is not None else None,
                "ff_apps": apps if hit or prior_avg is not None else None,
                "ff_season": season,
                "ff_prior_avg": round(float(prior_avg), 2) if prior_avg is not None else None,
                "ff_prior_season": prior_season,
                "ff_prior_apps": prior_apps,
                "is_top_ff": is_top,
                "top_reason": reason,
                "production_score": prod,
                "ff_match_score": score if hit else None,
                "ff_scoring": scoring_label,
                "ff_avg_scale": avg_scale,
                "lineup_prob_source": lp_source,
            }
        )
        # Preferir ficha FF de analytics si no hay URL de jugador o solo hay link a partido
        if ff_url:
            cur = str(ext.get("profile_url") or "")
            better = (
                not cur
                or "/partido/" in cur
                or (
                    "futbolfantasy.com/jugadores/" in ff_url
                    and "futbolfantasy.com/jugadores/" not in cur
                )
            )
            if better:
                ext["profile_url"] = ff_url
        new_p["external"] = ext
        new_p["ff_mister_avg"] = ext["ff_mister_avg"]
        new_p["ff_mister_points"] = ext["ff_mister_points"]
        new_p["ff_apps"] = ext["ff_apps"]
        new_p["ff_prior_avg"] = ext["ff_prior_avg"]
        new_p["ff_prior_apps"] = prior_apps
        new_p["production_score"] = prod
        new_p["is_top_ff"] = is_top
        new_p["top_reason"] = reason
        new_p["ff_avg_scale"] = avg_scale
        new_p["ff_scoring"] = scoring_label
        enriched.append(new_p)

    # Fallback TOP por percentil de precio vs universo mercado si casi no hubo matches FF
    if matched < max(3, len(players) // 5) and meta["ff_points"] == "fail":
        meta["fallback_price"] = True
        universe = list(market_universe or []) + list(enriched)
        prices = sorted(
            float(x.get("price") or 0)
            for x in universe
            if float(x.get("price") or 0) > 0
        )
        cut = prices[int(len(prices) * 0.85)] if len(prices) >= 10 else None
        if cut:
            top_n = 0
            for new_p in enriched:
                price = float(new_p.get("price") or 0)
                is_top = price >= cut
                new_p["is_top_ff"] = is_top
                if is_top:
                    top_n += 1
                    reason = f"Fallback precio mercado ≥ {cut/1e6:.1f}M€ (P85)"
                    new_p["top_reason"] = reason
                    ext = dict(new_p.get("external") or {})
                    ext["is_top_ff"] = True
                    ext["top_reason"] = reason
                    new_p["external"] = ext

    meta["matched"] = matched
    meta["top_count"] = top_n
    log.info(
        "FF production [%s/%s] match=%s/%s tops=%s thr=%s status=%s",
        comp,
        scoring_label,
        matched,
        len(players),
        top_n,
        threshold,
        meta["ff_points"],
    )
    return enriched, meta
