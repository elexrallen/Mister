"""
Sonda de descubrimiento: endpoints de jornada/capitán de Mister aún no cableados.

Vuelca a `cache/probe/` la respuesta cruda de:
  - GET  /feed                      → bloque feed-top-gameweek, _FG_user, _FG_cfg
  - POST /ajax/sw/gameweek          → panel de jornada (post=gameweek&id=<gwId>)
  - POST /ajax/sw/competition       → calendario de la competición
  - POST /ajax/player-gameweek      → desglose de puntos por jornada de un jugador
  - _FG_user + /ajax/sw/admin de la liga con capitán (MISTER_CAPTAIN_LEAGUE_ID)

Uso:
    py -3 scripts/probe_mister_gameweek.py
    py -3 scripts/probe_mister_gameweek.py --league 2510216 --player 15653
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
import mister_client as mc  # noqa: E402

OUT_DIR = ROOT / "cache" / "probe"

# Liga "MD con CAPITÁN" (public/data/leagues.json). Override con --league.
CAPTAIN_LEAGUE_ID = "2510216"

CAPTAIN_KEY_RE = re.compile(r"captain|capitan|multiplier|booster", re.I)
MARKET_KEY_RE = re.compile(r"market_date|market_lock|market_close|market_time", re.I)

log = logging.getLogger("probe")


def _write(name: str, payload: Any) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")
    return path


def _try_ajax(label: str, path: str, data: dict[str, Any]) -> Any:
    print(f"\n[{label}] POST {path} {data}")
    try:
        raw = mc.ajax_post(path, data, timeout=25)
    except Exception as exc:  # noqa: BLE001
        print(f"  FALLO: {exc}")
        return None
    if not isinstance(raw, dict):
        print(f"  respuesta no-dict: {type(raw).__name__}")
        return raw
    status = raw.get("status")
    body = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    keys = list(body.keys())[:20] if isinstance(body, dict) else []
    print(f"  status={status} keys={keys}")
    return raw


def _gameweek_ids(html: str) -> tuple[str | None, list[str]]:
    """`data-sw="gameweek/3968"` y `data-sw="gameweek/3968/37161"` del feed."""
    gw_ids = re.findall(r'data-sw="gameweek/(\d+)"', html)
    match_ids = re.findall(r'data-sw="gameweek/\d+/(\d+)"', html)
    return (gw_ids[0] if gw_ids else None), match_ids


def _scan_keys(label: str, blob: Any, pattern: re.Pattern[str]) -> list[str]:
    """Busca claves interesantes en un dict anidado (o texto)."""
    hits: list[str] = []

    def walk(node: Any, prefix: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                path = f"{prefix}.{k}" if prefix else str(k)
                if pattern.search(str(k)):
                    hits.append(f"{path} = {json.dumps(v, ensure_ascii=False)[:120]}")
                walk(v, path)
        elif isinstance(node, list):
            for i, v in enumerate(node[:50]):
                walk(v, f"{prefix}[{i}]")

    if isinstance(blob, str):
        for m in pattern.finditer(blob):
            start = max(0, m.start() - 40)
            hits.append(re.sub(r"\s+", " ", blob[start : m.end() + 60]))
    else:
        walk(blob, "")
    if hits:
        print(f"  {label}: {len(hits)} coincidencia(s)")
        for h in hits[:12]:
            print(f"    - {h}")
    else:
        print(f"  {label}: sin coincidencias")
    return hits


def probe_feed() -> tuple[str, dict[str, Any], str | None, list[str]]:
    print("\n[feed] GET /feed")
    html = mc.fetch_html("/feed")
    mc.refresh_x_auth_from_html(html)
    _write("feed.html", html)
    fg_user = mc._extract_js_object(html, "_FG_user") or {}
    fg_cfg = mc._extract_js_object(html, "_FG_cfg") or {}
    _write("feed_FG_user.json", fg_user)
    _write("feed_FG_cfg.json", {k: v for k, v in fg_cfg.items() if k != "i18n"})
    gw_id, match_ids = _gameweek_ids(html)
    print(f"  gameweek id={gw_id} partidos={len(match_ids)}")
    _scan_keys("market_* en _FG_user", fg_user, MARKET_KEY_RE)
    _scan_keys("captain en _FG_user", fg_user, CAPTAIN_KEY_RE)
    return html, fg_user, gw_id, match_ids


def probe_gameweek(gw_id: str | None) -> None:
    if not gw_id:
        print("\n[gameweek] sin id en el feed; se omite")
        return
    raw = _try_ajax("gameweek", "/ajax/sw/gameweek", {"post": "gameweek", "id": gw_id})
    if raw is not None:
        _write("sw_gameweek.json", raw)


def probe_competition() -> None:
    raw = _try_ajax("competition", "/ajax/sw/competition", {"post": "competition"})
    if raw is not None:
        _write("sw_competition.json", raw)


def probe_player_gameweek(player_id: str, gw_id: str | None) -> None:
    """`btn.data()` no está documentado: probamos las combinaciones plausibles."""
    variants: list[dict[str, Any]] = [
        {"id_player": player_id},
        {"id_player": player_id, "id_gameweek": gw_id or ""},
        {"player": player_id},
    ]
    for i, data in enumerate(variants):
        raw = _try_ajax(f"player-gameweek#{i}", "/ajax/player-gameweek", data)
        if isinstance(raw, dict) and raw.get("status") == "ok":
            _write("player_gameweek.json", raw)
            return
    print("  ninguna variante devolvió status=ok")


def probe_captain_league(league_id: str) -> None:
    print(f"\n[capitán] switch_community({league_id})")
    fg_user = mc.switch_community(league_id)
    if not fg_user:
        print("  no se pudo activar la comunidad")
        return
    _write("captain_FG_user.json", fg_user)
    _scan_keys("captain en _FG_user", fg_user, CAPTAIN_KEY_RE)
    _scan_keys("market_* en _FG_user", fg_user, MARKET_KEY_RE)

    admin = mc.fetch_admin_settings()
    if admin:
        _write("captain_admin_settings.json", admin)
        _scan_keys("captain en admin", admin, CAPTAIN_KEY_RE)
    else:
        print("  /ajax/sw/admin sin datos (¿no eres admin?)")

    try:
        team_html = mc.fetch_html("/team")
        _write("captain_team.html", team_html)
        _scan_keys("captain en HTML /team", team_html, CAPTAIN_KEY_RE)
    except Exception as exc:  # noqa: BLE001
        print(f"  GET /team falló: {exc}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", default=CAPTAIN_LEAGUE_ID, help="id_community de la liga con capitán")
    ap.add_argument("--player", default="", help="id_player para /ajax/player-gameweek")
    args = ap.parse_args()

    if not (config.MISTER_TOKEN or config.MISTER_COOKIE):
        raise SystemExit("Sin MISTER_TOKEN/MISTER_COOKIE: define los secrets en .env")

    print(f"Base: {config.MISTER_API_BASE}")
    html, fg_user, gw_id, match_ids = probe_feed()
    if match_ids:
        _write("feed_match_ids.json", {"gameweek": gw_id, "matches": match_ids})

    probe_gameweek(gw_id)
    probe_competition()

    player_id = args.player.strip()
    if not player_id:
        # Cualquier jugador del pool sirve para ver el schema del popup.
        pool, _ = mc.fetch_full_player_pool()
        if pool:
            player_id = str(pool[0]["id"])
            print(f"\n[player] usando {pool[0]['name']} ({player_id}) del pool")
    if player_id:
        probe_player_gameweek(player_id, gw_id)

    if args.league:
        probe_captain_league(args.league)

    print(f"\nListo. Revisa {OUT_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
