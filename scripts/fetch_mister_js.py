"""Download Mister frontend JS and extract player-list API hints."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

CACHE = ROOT / "cache" / "js"
CACHE.mkdir(parents=True, exist_ok=True)

BASE = "https://app-mister.mundodeportivo.com/file/forge-app/db4c1b41/app/dist/js"
FILES = [
    "search-players.js",
    "functions.js",
    "global.js",
    "listeners.js",
    "views/players.js",
    "views/players.functions.js",
    "views/players.listeners.js",
    "views/market.js",
    "views/market.functions.js",
    "views/search.js",
    "views/team.js",
]


def main() -> None:
    for name in FILES:
        url = f"{BASE}/{name}"
        try:
            r = requests.get(url, timeout=25)
        except Exception as exc:  # noqa: BLE001
            print(name, "ERR", exc)
            continue
        print(name, r.status_code, len(r.content))
        if r.status_code != 200:
            continue
        out = CACHE / name.replace("/", "_")
        out.write_bytes(r.content)
        text = r.text
        # extract ajax paths and nearby context
        for m in re.finditer(r".{0,60}(?:/ajax/[^\"'\s)]+|sw/players|search-players).{0,80}", text):
            snippet = re.sub(r"\s+", " ", m.group(0))
            print(" ", snippet[:180])


if __name__ == "__main__":
    main()
