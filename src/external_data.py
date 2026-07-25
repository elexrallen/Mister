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

log = logging.getLogger("external_data")

SRC_DIR = Path(__file__).resolve().parent
CACHE_PATH = SRC_DIR / "cache" / "external_latest.json"
SEED_PATH = SRC_DIR / "external_seed.json"

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
            new = r["profile_url"]
            if not cur or ("/partido/" in cur and "/partido/" not in str(new)):
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
    }


def _load_candidates_from_cache_or_seed(meta: dict[str, Any]) -> list[dict[str, Any]]:
    cache = _load_json(CACHE_PATH)
    if isinstance(cache, dict) and cache.get("players"):
        meta["cache_used"] = True
        for k in ("futbolfantasy", "jornadaperfecta", "comuniate", "sofascore"):
            meta[k] = "cache"
        log.info("Usando caché externa (%d jugadores)", len(cache["players"]))
        return list(cache["players"])

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


def enrich_players_with_external(players: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Devuelve jugadores enriquecidos + meta
    {futbolfantasy, jornadaperfecta, comuniate, sofascore, matched, cache_used, errors}.
    """
    meta: dict[str, Any] = {
        "futbolfantasy": "fail",
        "jornadaperfecta": "fail",
        "comuniate": "fail",
        "sofascore": "skip",
        "matched": 0,
        "cache_used": False,
        "errors": [],
        "sofascore_filled": 0,
    }

    team_names = list({str(p.get("team") or "") for p in players if p.get("team")})
    candidates: list[dict[str, Any]] = []

    try:
        bundle = fetch_all_external(team_names, sofascore_candidates=None)
        status = dict(bundle.get("status") or {})
        com_catalog = bundle.get("comuniate") or []
        candidates = _merge_source_records(
            bundle.get("futbolfantasy") or [],
            bundle.get("jornadaperfecta") or [],
            com_catalog,
            [],
        )

        # 1) Fichas Comuniate para universo Mister → sofascore_id + media fallback
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
            # Si hay medias vía Comuniate, ya contamos partial sofa
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
            _save_json(CACHE_PATH, {
                "fetched_at": _now().isoformat(),
                "players": candidates,
                "status": status,
            })
        else:
            candidates = _load_candidates_from_cache_or_seed(meta)
    except Exception as exc:  # noqa: BLE001
        log.warning("Scrape externo falló: %s", exc)
        meta["errors"].append(str(exc))
        candidates = _load_candidates_from_cache_or_seed(meta)

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
            }
            if ext.get("sofascore_avg_5") is not None:
                sofa_on_players += 1
            if avail in ("injured", "suspended"):
                new_p["injury"] = True
            if ext.get("lineup_prob_ext") is not None:
                try:
                    new_p["lineup_prob"] = float(ext["lineup_prob_ext"]) / 100.0
                except (TypeError, ValueError):
                    pass
        new_p["external"] = ext
        enriched.append(new_p)

    meta["matched"] = matched
    meta["sofascore_filled"] = sofa_on_players
    if sofa_on_players >= 5 and meta.get("sofascore") == "skip":
        meta["sofascore"] = "partial"
    return enriched, meta
