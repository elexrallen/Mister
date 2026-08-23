"""
Sonda: ofertas recibidas + listados propios en /market.

Vuelca a cache/probe/:
  - POST /ajax/sw/offers-received
  - POST /ajax/sw/offers-sent (opcional)
  - GET /market (fragmento con owners)
  - communities[].offers en _FG_user

Uso:
    py -3 scripts/probe_offers_received.py
    py -3 scripts/probe_offers_received.py --league 2510216
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

import mister_client as mc  # noqa: E402

OUT_DIR = ROOT / "cache" / "probe"
log = logging.getLogger("probe_offers")


def _write(name: str, payload: Any) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")
    return path


def _summarize(raw: Any) -> None:
    if not isinstance(raw, dict):
        print(f"  tipo={type(raw).__name__}")
        if isinstance(raw, str):
            print(f"  len={len(raw)} head={raw[:200]!r}")
        return
    print(f"  status={raw.get('status')} keys={list(raw.keys())[:20]}")
    data = raw.get("data")
    if isinstance(data, dict):
        print(f"  data.keys={list(data.keys())[:30]}")
    elif isinstance(data, str):
        print(f"  data=str len={len(data)} head={data[:200]!r}")
    elif isinstance(data, list):
        print(f"  data=list len={len(data)}")
        if data and isinstance(data[0], dict):
            print(f"  data[0].keys={list(data[0].keys())[:20]}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default=None)
    args = ap.parse_args()

    print("[auth] GET /feed")
    html = mc.fetch_html("/feed")
    mc.refresh_x_auth_from_html(html)
    fg_user = mc._extract_js_object(html, "_FG_user") or {}
    my_uc = str(fg_user.get("id_uc") or "")
    print(f"  id_uc={my_uc}")

    communities = fg_user.get("communities") or []
    offers_counts = []
    for c in communities if isinstance(communities, list) else []:
        if not isinstance(c, dict):
            continue
        offers_counts.append(
            {
                "id": c.get("id") or c.get("id_community"),
                "name": c.get("name"),
                "offers": c.get("offers"),
            }
        )
    _write("fg_user_offers_counts.json", offers_counts)
    print(f"  communities offers: {offers_counts[:5]}")

    if args.league:
        print(f"[liga] switch_community({args.league})")
        mc.switch_community(str(args.league))

    for label, post in (
        ("offers-received", "offers-received"),
        ("offers-sent", "offers-sent"),
    ):
        print(f"\n[{label}] POST /ajax/sw/{post}")
        try:
            raw = mc.ajax_post(f"/ajax/sw/{post}", {"post": post}, timeout=25)
        except Exception as exc:  # noqa: BLE001
            print(f"  FALLO: {exc}")
            continue
        _summarize(raw)
        _write(f"sw_{post.replace('-', '_')}.json", raw)
        data = raw.get("data") if isinstance(raw, dict) else None
        if isinstance(data, str) and ("<" in data or "offer" in data.lower()):
            _write(f"sw_{post.replace('-', '_')}.html", data)

    print("\n[market] GET /market")
    try:
        market_html = mc.fetch_html("/market")
    except Exception as exc:  # noqa: BLE001
        print(f"  FALLO: {exc}")
        return
    _write("market.html", market_html)
    owners = re.findall(
        r"data-id_owner=['\"](\d+)['\"][^>]{0,200}data-id_player=['\"](\d+)['\"]"
        r"|data-id_player=['\"](\d+)['\"][^>]{0,200}data-id_owner=['\"](\d+)['\"]",
        market_html,
        re.I,
    )
    mine = []
    for g in owners:
        if g[0]:
            oid, pid = g[0], g[1]
        else:
            pid, oid = g[2], g[3]
        if my_uc and oid == my_uc:
            mine.append({"owner_id": oid, "player_id": pid})
    print(f"  bid buttons={len(owners)} listed_by_me={len(mine)}")
    _write("market_listed_by_me.json", mine)

    # data-owner on li
    li_owners = re.findall(
        r"<li[^>]*data-owner=['\"](\d+)['\"][^>]*data-id_player=['\"](\d+)['\"]",
        market_html,
        re.I,
    )
    li_mine = [{"owner_id": o, "player_id": p} for o, p in li_owners if my_uc and o == my_uc]
    print(f"  li[data-owner] mine={len(li_mine)}")
    if li_mine:
        _write("market_li_listed_by_me.json", li_mine)


if __name__ == "__main__":
    main()
