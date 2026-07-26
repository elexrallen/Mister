# -*- coding: utf-8 -*-
import json
from collections import defaultdict
from pathlib import Path

d = json.loads(Path("public/data/latest_data.json").read_text(encoding="utf-8"))
me = d["me"]
cash = float(me["balance"] or 0)
print("cash_m", round(cash / 1e6, 2), "sv", round(float(me["squad_value"]) / 1e6, 2), "n", len(me["squad"]))
print("rivals by SV:")
for r in sorted(d.get("rivals") or [], key=lambda x: -(x.get("squad_value") or 0)):
    name = (r.get("team_name") or "?")[:22]
    print(f"  #{r.get('rank')} {name} sv={round((r.get('squad_value') or 0)/1e6, 1)}")

by: dict[str, list] = defaultdict(list)
for o in d.get("market_opportunities") or []:
    ext = o.get("external") or {}
    lp = ext.get("lineup_prob_ext")
    try:
        lp_f = float(lp) if lp is not None else None
    except (TypeError, ValueError):
        lp_f = None
    price = float(o.get("price") or 0)
    if not lp_f or lp_f < 70 or price <= 0:
        continue
    by[o.get("position") or "?"].append(
        (
            o.get("name"),
            round(price / 1e6, 2),
            int(lp_f),
            bool(o.get("is_top_ff")),
            o.get("seller"),
        )
    )
for pos in ("GK", "DF", "MF", "FW"):
    rows = sorted(by[pos], key=lambda x: x[1])
    print(pos, "n>=70", len(rows))
    for x in rows[:8]:
        print(" ", x)
