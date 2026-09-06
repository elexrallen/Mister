"""Histograma de ofertas máquina (pct_of_vm) desde latest_data de cada liga.

Sirve para validar el spread CPU antes de retocar umbrales CPU_SPREAD_*.
Uso: py -3 scripts/analyze_cpu_offer_spread.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEAGUES = ROOT / "public" / "data" / "leagues"


def _bucket(pct: float) -> str:
    if pct < 0.95:
        return "<0.95"
    if pct < 1.0:
        return "0.95-1.00"
    if pct < 1.02:
        return "1.00-1.02"
    if pct <= 1.05:
        return "1.02-1.05"
    return ">1.05"


def main() -> int:
    all_pct: list[float] = []
    if not LEAGUES.is_dir():
        print("no leagues dir", LEAGUES, file=sys.stderr)
        return 1
    for path in sorted(LEAGUES.glob("*/latest_data.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        sales = data.get("sales_state") or {}
        offers = sales.get("offers_received") or sales.get("offers") or []
        pcts: list[float] = []
        for row in offers:
            if not isinstance(row, dict) or not row.get("from_machine"):
                continue
            raw = row.get("pct_of_vm")
            if raw is None:
                continue
            try:
                pct = float(raw)
            except (TypeError, ValueError):
                continue
            pcts.append(pct)
            all_pct.append(pct)
        print(f"{path.parent.name}: n={len(pcts)} {[round(p, 4) for p in pcts]}")

    print("---")
    print(f"total_machine_offers={len(all_pct)}")
    if not all_pct:
        return 0
    mean = sum(all_pct) / len(all_pct)
    print(f"min={min(all_pct):.4f} max={max(all_pct):.4f} mean={mean:.4f}")
    print(f"P(>1.0)={sum(1 for p in all_pct if p > 1.0) / len(all_pct):.2%}")
    print(f"P(>=0.98)={sum(1 for p in all_pct if p >= 0.98) / len(all_pct):.2%}")
    buckets = Counter(_bucket(p) for p in all_pct)
    print("buckets", dict(sorted(buckets.items())))
    print(
        "hint: CPU_SPREAD_EXPECTED_PREMIUM=0.025 / IMPATIENT=0.98 "
        "alineados con media~VM y primas escasas"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
