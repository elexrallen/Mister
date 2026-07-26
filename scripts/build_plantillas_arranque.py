# -*- coding: utf-8 -*-
"""Plantillas arranque: max puntos sobre pool Mister ~500."""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / "public/data/latest_data.json").read_text(encoding="utf-8"))

POS_SQUAD = {"GK": 2, "DF": 5, "MF": 5, "FW": 3}
XI_SHAPE = [("GK", 1), ("DF", 4), ("MF", 3), ("FW", 3)]


def m(n: float) -> float:
    return round(float(n) / 1_000_000, 2)


def fold(s: str) -> str:
    nk = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in nk if not unicodedata.combining(c)).lower()


def is_top(p: dict) -> bool:
    if p.get("is_top_ff") is True:
        return True
    ext = p.get("external") or {}
    if ext.get("is_top_ff") is True:
        return True
    avg = p.get("ff_mister_avg") or ext.get("ff_mister_avg")
    try:
        return avg is not None and float(avg) >= 5.5
    except (TypeError, ValueError):
        return False


def score(p: dict) -> float:
    for key in ("production_score",):
        if p.get(key) is not None:
            try:
                return float(p[key])
            except (TypeError, ValueError):
                pass
    ext = p.get("external") or {}
    if ext.get("production_score") is not None:
        try:
            return float(ext["production_score"])
        except (TypeError, ValueError):
            pass
    avg = p.get("ff_mister_avg") or ext.get("ff_mister_avg")
    if avg is not None:
        try:
            return float(avg) * 12
        except (TypeError, ValueError):
            pass
    form = p.get("form") or p.get("mister_avg") or p.get("avg_ppg")
    if form is not None:
        try:
            return float(form) * 10
        except (TypeError, ValueError):
            pass
    return min(45.0, float(p.get("price") or 0) / 400_000)


def lp(p: dict) -> float:
    ext = p.get("external") or {}
    if ext.get("lineup_prob_ext") is not None:
        try:
            return float(ext["lineup_prob_ext"])
        except (TypeError, ValueError):
            pass
    if p.get("lineup_prob") is not None:
        try:
            v = float(p["lineup_prob"])
            return v if v > 1 else v * 100
        except (TypeError, ValueError):
            pass
    return 50.0


def ok(p: dict) -> bool:
    ext = p.get("external") or {}
    if ext.get("availability") in ("injured", "suspended"):
        return False
    return not bool(p.get("injury"))


def collect() -> dict[str, dict]:
    by: dict[str, dict] = {}

    def add(p: dict, src: str, owner: str | None = None) -> None:
        pid = str(p.get("id") or "")
        if not pid or not p.get("name"):
            return
        row = dict(p)
        row["_src"] = src
        row["_owner"] = owner
        if p.get("clause_known") and p.get("clause") is not None:
            row["_clause"] = float(p["clause"])
        prev = by.get(pid)
        if prev and prev.get("_src") == "squad":
            if row.get("_clause") and not prev.get("_clause"):
                prev["_clause"] = row["_clause"]
            return
        if prev:
            prev_s = score(prev) + (10 if is_top(prev) else 0)
            new_s = score(row) + (10 if is_top(row) else 0)
            if new_s <= prev_s:
                if row.get("_clause") and not prev.get("_clause"):
                    prev["_clause"] = row["_clause"]
                if src in ("free", "market") and prev.get("_src") == "rival":
                    prev["_src"] = src
                return
        by[pid] = row

    for p in DATA["me"].get("squad") or []:
        add(p, "squad", "yo")
    for p in DATA.get("free_agents_top") or []:
        add(p, "free")
    for p in DATA.get("market_opportunities") or []:
        add(p, "free" if p.get("seller") == "free" else "market")
    for r in DATA.get("rivals") or []:
        owner = r.get("team_name") or "?"
        for p in r.get("squad") or []:
            add(p, "rival", owner)
    return by


def cost_of(p: dict) -> float | None:
    if p.get("_src") == "squad":
        return 0.0
    if p.get("_src") in ("free", "market"):
        return float(p.get("price") or 0)
    if p.get("_src") == "rival":
        if p.get("_clause") is None:
            return None
        return float(p["_clause"])
    return float(p.get("price") or 0)


def find_name(universe: dict[str, dict], *needles: str) -> dict | None:
    nds = [fold(x) for x in needles]
    for p in universe.values():
        n = fold(p.get("name") or "")
        if all(nd in n for nd in nds):
            return p
    return None


def best_free(
    universe: dict[str, dict],
    *,
    pos: str | None = None,
    max_cost: float | None = None,
    min_lp: float = 0,
    exclude: set[str] | None = None,
) -> list[dict]:
    exclude = exclude or set()
    out = []
    for p in universe.values():
        if p.get("_src") not in ("free", "market") or not ok(p):
            continue
        if p["id"] in exclude:
            continue
        if pos and p.get("position") != pos:
            continue
        c = cost_of(p) or 0
        if max_cost is not None and c > max_cost:
            continue
        if lp(p) < min_lp:
            continue
        out.append(p)
    out.sort(key=lambda p: (-score(p), -(1 if is_top(p) else 0), cost_of(p) or 0))
    return out


def best_clause(
    universe: dict[str, dict],
    *,
    pos: str | None = None,
    max_cost: float | None = None,
) -> list[dict]:
    out = []
    for p in universe.values():
        if p.get("_src") != "rival" or p.get("_clause") is None or not ok(p):
            continue
        if pos and p.get("position") != pos:
            continue
        c = float(p["_clause"])
        if max_cost is not None and c > max_cost:
            continue
        out.append(p)
    out.sort(key=lambda p: (-(score(p) / max(float(p["_clause"]) / 1e6, 0.15)), -score(p)))
    return out


def pick_xi(squad: list[dict]) -> list[dict]:
    buckets: dict[str, list] = {"GK": [], "DF": [], "MF": [], "FW": []}
    for p in squad:
        buckets.setdefault(p["pos"], []).append(p)
    for k in buckets:
        buckets[k].sort(key=lambda x: (-x["sc"], -x["lp"]))
    xi: list[dict] = []
    for pos, n in XI_SHAPE:
        xi.extend(buckets.get(pos, [])[:n])
    return xi


def row_of(p: dict, cost: float, src: str) -> dict:
    return {
        "id": p["id"],
        "name": p.get("name"),
        "pos": p.get("position"),
        "cost": cost,
        "sc": round(score(p), 1),
        "lp": round(lp(p)),
        "src": src,
        "owner": p.get("_owner"),
        "top": is_top(p),
    }


def finalize(
    *,
    name: str,
    tag: str,
    tone: str,
    thesis: str,
    why: str,
    cash: float,
    keep: list[dict],
    acquires: list[dict],
    sells: list[dict],
    universe: dict[str, dict],
) -> dict:
    sell_cash = sum(float(p.get("price") or 0) for p in sells)
    budget = cash + sell_cash
    spent = 0.0
    squad_rows: list[dict] = []
    moves: list[dict] = []
    ids: set[str] = set()

    for p in sells:
        moves.append(
            {
                "action": "Vender",
                "name": p.get("name"),
                "pos": p.get("position"),
                "cost_m": m(float(p.get("price") or 0)),
                "detail": "libera caja",
                "top": is_top(p),
            }
        )

    for p in keep:
        if p["id"] in ids:
            continue
        ids.add(p["id"])
        squad_rows.append(row_of(p, 0.0, "keep"))

    for p in acquires:
        if p["id"] in ids:
            continue
        c = cost_of(p)
        if c is None or spent + c > budget + 50_000:
            continue
        spent += c
        ids.add(p["id"])
        src = p.get("_src") or "free"
        squad_rows.append(row_of(p, c, src))
        moves.append(
            {
                "action": "Clausula" if src == "rival" else "Fichar",
                "name": p.get("name"),
                "pos": p.get("position"),
                "cost_m": m(c),
                "detail": (
                    f"clausula · {p.get('_owner')}"
                    if src == "rival"
                    else ("libre" if src == "free" else "mercado")
                ),
                "top": is_top(p),
            }
        )

    counts = {k: 0 for k in POS_SQUAD}
    for r in squad_rows:
        counts[r["pos"]] = counts.get(r["pos"], 0) + 1

    fillers = [
        p
        for p in universe.values()
        if ok(p)
        and p["id"] not in ids
        and cost_of(p) is not None
        and p.get("_src") in ("free", "market", "rival")
    ]
    fillers.sort(key=lambda p: (-(1 if lp(p) >= 70 else 0), -score(p), cost_of(p) or 9e9))

    for p in fillers:
        if len(squad_rows) >= 15:
            break
        pos = p.get("position") or "MF"
        if counts.get(pos, 0) >= POS_SQUAD[pos]:
            continue
        c = cost_of(p) or 0
        if spent + c > budget + 50_000:
            continue
        # no gastar megacracks en relleno
        if c >= 8_000_000 and not is_top(p):
            continue
        if c >= 15_000_000:
            continue
        spent += c
        counts[pos] = counts.get(pos, 0) + 1
        ids.add(p["id"])
        squad_rows.append(row_of(p, c, p.get("_src") or "free"))
        if c > 0:
            moves.append(
                {
                    "action": "Clausula" if p.get("_src") == "rival" else "Fichar",
                    "name": p.get("name"),
                    "pos": pos,
                    "cost_m": m(c),
                    "detail": "relleno titular" if lp(p) >= 70 else "cupo",
                    "top": is_top(p),
                }
            )

    xi = pick_xi(squad_rows)
    return {
        "name": name,
        "tag": tag,
        "tone": tone,
        "thesis": thesis,
        "why": why,
        "budget_m": m(budget),
        "spent_m": m(spent),
        "cash_after_m": m(budget - spent),
        "sell_cash_m": m(sell_cash),
        "sells": [p.get("name") for p in sells],
        "tops": sum(1 for r in squad_rows if r["top"]),
        "starters70": sum(1 for r in squad_rows if r["lp"] >= 70),
        "xi_score": round(sum(r["sc"] for r in xi), 1),
        "counts": counts,
        "xi": xi,
        "squad": sorted(squad_rows, key=lambda r: (r["pos"], -r["sc"])),
        "moves": moves,
    }


def uniq(players: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for p in players:
        if not p or p["id"] in seen:
            continue
        seen.add(p["id"])
        out.append(p)
    return out


def main() -> None:
    me = DATA["me"]
    cash = float(me.get("balance") or 0)
    squad_value = float(me.get("squad_value") or 0)
    universe = collect()
    print("universe", len(universe), "cash", m(cash), "sv", m(squad_value))

    squad = list(me.get("squad") or [])
    catena = find_name(universe, "catena")
    junior = find_name(universe, "junior")
    barren = find_name(universe, "barrenetxea")
    auba = find_name(universe, "aubameyang")
    oskar = find_name(universe, "oskarsson")
    guridi = find_name(universe, "guridi")

    mbappe = find_name(universe, "mbappe")
    yamal = find_name(universe, "yamal")
    vini = find_name(universe, "vinicius")
    oyar = find_name(universe, "oyarzabal")
    uche = find_name(universe, "uche")
    mikau = find_name(universe, "mikautadze")
    print("stars", bool(mbappe), bool(yamal), bool(vini), bool(oyar), bool(uche), "gk", bool(junior))

    dead = []
    for p in squad:
        n = fold(p.get("name") or "")
        if any(x in n for x in ("egiluz", "padilla", "macia", "vencedor", "gorosabel", "almada")):
            dead.append(p)
        if "sanchez" in n and p.get("position") == "FW" and float(p.get("price") or 0) < 500_000:
            dead.append(p)
    dead = uniq(dead)

    # Plan 1
    keep1 = uniq([p for p in [catena, junior, barren, auba, guridi] if p])
    sells1 = uniq([p for p in squad if p["id"] not in {x["id"] for x in keep1}])
    acq1 = [p for p in [oyar, uche] if p]
    for p in best_clause(universe, pos="GK", max_cost=1_500_000)[:1]:
        acq1.append(p)
    for p in best_clause(universe, max_cost=1_500_000)[:2]:
        if p.get("position") != "GK":
            acq1.append(p)
    for p in best_free(universe, pos="DF", max_cost=3_000_000, min_lp=70)[:2]:
        acq1.append(p)
    for p in best_free(universe, pos="MF", max_cost=3_000_000, min_lp=70)[:2]:
        acq1.append(p)
    acq1 = uniq(acq1)

    plan1 = finalize(
        name="1 - Dream asequible (Oyarzabal + Uche)",
        tag="Mejor techo sin hipoteca",
        tone="success",
        thesis=(
            "Con 27.6 M€ de caja y 371 libres del pool, el mejor equilibrio es "
            "Oyarzabal (TOP ~18.7 M€, 80% alineacion) + Uche (TOP ~7.3 M€, 85%). "
            "Mantienes Catena / Junior / Barrenetxea y rellenas con clausulas ROI y titulares baratos."
        ),
        why="2 TOP reales sin gastar Mbappe/Yamal. Colchon para mercado diario o clausulas.",
        cash=cash,
        keep=keep1,
        acquires=acq1,
        sells=sells1,
        universe=universe,
    )

    # Plan 2
    sells2 = uniq(dead + ([barren] if barren else []) + ([oskar] if oskar else []))
    keep2 = uniq([p for p in [catena, junior, auba, guridi] if p])
    budget2 = cash + sum(float(p.get("price") or 0) for p in sells2)
    if vini and uche and (cost_of(vini) or 0) + (cost_of(uche) or 0) <= budget2:
        acq2 = [vini, uche]
    elif mbappe and uche and (cost_of(mbappe) or 0) + (cost_of(uche) or 0) <= budget2:
        acq2 = [mbappe, uche]
    elif mbappe:
        acq2 = [mbappe]
    else:
        acq2 = [p for p in [vini, yamal, oyar] if p][:2]
    for p in best_clause(universe, pos="GK", max_cost=1_200_000)[:1]:
        acq2.append(p)
    for p in best_free(universe, pos="DF", max_cost=3_000_000, min_lp=75)[:2]:
        acq2.append(p)
    for p in best_free(universe, pos="MF", max_cost=3_500_000, min_lp=75)[:2]:
        acq2.append(p)
    acq2 = uniq(acq2)

    plan2 = finalize(
        name="2 - Saqueo estrellas (vender Barrenetxea)",
        tag="Maximo techo de puntos",
        tone="warning",
        thesis=(
            f"Vendes Barrenetxea (~{m(float((barren or {}).get('price') or 0))} M€) + lastre "
            f"y llegas a ~{m(budget2)} M€. Objetivo: Vinicius + Uche (o Mbappe + Uche), "
            "cracks libres que hoy nadie tiene en la liga."
        ),
        why="Mayor XI score esperado. Riesgo: caja fina y dependes de relleno barato titular.",
        cash=cash,
        keep=keep2,
        acquires=acq2,
        sells=sells2,
        universe=universe,
    )

    # Plan 3
    sells3 = uniq(dead + ([oskar] if oskar else []))
    keep3 = uniq([p for p in [catena, junior, barren, auba, guridi] if p])
    budget3 = cash + sum(float(p.get("price") or 0) for p in sells3)
    star = None
    for cand in [mbappe, vini, yamal, oyar]:
        if cand and (cost_of(cand) or 0) <= budget3:
            star = cand
            break
    acq3 = [star] if star else []
    # Solo relleno barato tras el mega (no segundo TOP caro)
    for p in best_clause(universe, pos="GK", max_cost=1_200_000)[:1]:
        acq3.append(p)
    for p in best_free(universe, pos="DF", max_cost=2_500_000, min_lp=70)[:3]:
        acq3.append(p)
    for p in best_free(universe, pos="MF", max_cost=2_500_000, min_lp=70)[:3]:
        acq3.append(p)
    for p in best_free(universe, pos="FW", max_cost=2_000_000, min_lp=60)[:1]:
        acq3.append(p)
    acq3 = uniq([p for p in acq3 if p])

    plan3 = finalize(
        name=f"3 - Raid 1 mega ({(star or {}).get('name', 'crack')})",
        tag="Apuesta a un crack libre",
        tone="info",
        thesis=(
            f"Un solo golpe al libre mas productivo que cabe "
            f"(~{m(cost_of(star) or 0) if star else 0} M€). "
            "Mantienes Barrenetxea/Catena y rellenas con titulares baratos del pool."
        ),
        why="Simple de ejecutar. Si el crack falla, el plan se cae mas que el equilibrado.",
        cash=cash,
        keep=keep3,
        acquires=acq3,
        sells=sells3,
        universe=universe,
    )

    # Plan 4
    keep4 = uniq([p for p in [catena, junior, barren, auba, guridi, oskar] if p])
    sells4 = uniq([p for p in dead if p["id"] not in {x["id"] for x in keep4}])
    acq4 = []
    for p in [uche, mikau]:
        if p:
            acq4.append(p)
    for p in best_free(universe, pos="MF", max_cost=13_000_000, min_lp=80)[:1]:
        if p["id"] not in {x["id"] for x in acq4}:
            acq4.append(p)
    for p in best_clause(universe, pos="GK", max_cost=1_200_000)[:1]:
        acq4.append(p)
    for p in best_clause(universe, max_cost=1_200_000)[:3]:
        if p.get("position") != "GK":
            acq4.append(p)
    for p in best_free(universe, pos="DF", max_cost=2_000_000, min_lp=80)[:2]:
        acq4.append(p)
    acq4 = uniq(acq4)

    plan4 = finalize(
        name="4 - Valor / ROI (TOP baratos + clausulas 1M)",
        tag="Mas puntos por millon",
        tone="neutral",
        thesis=(
            "Ignora Mbappe/Yamal y caza produccion barata: Uche + Mikautadze + MF titular "
            "del pool, mas clausulas ~1 M€. Conservas flexibilidad para el mercado diario."
        ),
        why="Menos techo absoluto, mas profundidad y caja para reaccionar.",
        cash=cash,
        keep=keep4,
        acquires=acq4,
        sells=sells4,
        universe=universe,
    )

    elite = []
    for p in best_free(universe)[:16]:
        elite.append(
            {
                "name": p.get("name"),
                "pos": p.get("position"),
                "price_m": m(cost_of(p) or 0),
                "sc": round(score(p), 1),
                "lp": round(lp(p)),
                "top": is_top(p),
                "ff": p.get("ff_mister_avg") or (p.get("external") or {}).get("ff_mister_avg"),
            }
        )

    clauses = []
    for p in best_clause(universe)[:12]:
        c = float(p["_clause"])
        clauses.append(
            {
                "name": p.get("name"),
                "pos": p.get("position"),
                "owner": p.get("_owner"),
                "clause_m": m(c),
                "sc": round(score(p), 1),
                "lp": round(lp(p)),
                "top": is_top(p),
                "roi": round(score(p) / max(c / 1e6, 0.1), 1),
            }
        )

    out = {
        "context": {
            "balance_m": m(cash),
            "squad_value_m": m(squad_value),
            "capital_m": m(cash + squad_value),
            "universe": len(universe),
            "pool_size": DATA.get("sources", {}).get("pool_size"),
            "free": DATA.get("sources", {}).get("pool_free"),
            "owned": DATA.get("sources", {}).get("pool_owned"),
            "current_squad": [
                {
                    "name": p.get("name"),
                    "pos": p.get("position"),
                    "price_m": m(float(p.get("price") or 0)),
                    "sc": round(score(p), 1),
                    "lp": round(lp(p)),
                    "top": is_top(p),
                }
                for p in squad
            ],
            "dead_weight": [p.get("name") for p in dead],
        },
        "elite_free": elite,
        "clause_roi": clauses,
        "plans": [plan1, plan2, plan3, plan4],
    }
    path = ROOT / "cache" / "plantillas_arranque_v2.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", path)
    for pl in out["plans"]:
        print(
            pl["name"],
            "| spent",
            pl["spent_m"],
            "after",
            pl["cash_after_m"],
            "| xi",
            pl["xi_score"],
            "tops",
            pl["tops"],
            "st70",
            pl["starters70"],
        )
        print("  XI:", " · ".join(x["name"] for x in pl["xi"]))


if __name__ == "__main__":
    main()
