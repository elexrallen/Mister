"""
Backtest de no regresión de las reglas de presupuesto.

Dos partes:

1. A/B sobre los payloads completos de cada liga. Los items de `action_plan`
   ya traen todas las señales que consume el orden de intents, así que se puede
   comparar el pick antiguo con el nuevo sobre datos reales.
2. Concentración de gasto por ciclo en el histórico. Los snapshots guardan las
   acciones con precio, que es lo que necesita la métrica.

Uso:
    python scripts/backtest_budget_rules.py [--slug laliga-patio] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
from competitive_actions import (  # noqa: E402
    _intent_sort_key,
    _item_buy_cost,
    gap_reserve_cost,
    is_rival_market_listing,
    spend_share_cap,
    xi_gap_slots,
)

BUY_ACTIONS = {"buy_now", "bid", "swap_in"}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _league_slugs() -> list[str]:
    root = ROOT / "public" / "data" / "leagues"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "latest_data.json").is_file())


# ---------------------------------------------------------------------------
# 1. A/B del orden de intents y del tope de concentración
# ---------------------------------------------------------------------------

def ab_on_payload(slug: str) -> dict[str, Any]:
    payload = _load(ROOT / "public" / "data" / "leagues" / slug / "latest_data.json")
    me = payload.get("me") or {}
    balance = float(me.get("balance") or 0)
    diag = payload.get("diagnostico_plantilla") or {}
    needs = diag.get("structural_needs") or []
    ops = payload.get("market_opportunities") or []
    plan = payload.get("action_plan") or []

    buys = [i for i in plan if i.get("action") in BUY_ACTIONS and _item_buy_cost(i) > 0]
    slots = xi_gap_slots(diag, needs)
    gap_n = sum(slots.values())
    cap = spend_share_cap(balance, gap_n)

    # Los payloads guardados traen crowds_out_gaps/leaves_gap_budget calculados
    # con la regla antigua. Para que el A/B sea honesto hay que recalcularlos.
    rescored: list[dict[str, Any]] = []
    for item in buys:
        cost = _item_buy_cost(item)
        reserve = gap_reserve_cost(
            exclude_position=item.get("position"),
            diagnosis=diag,
            structural_needs=needs,
            opportunities=ops,
            balance=balance,
        )
        residual = balance - cost if cost <= balance else -1.0
        rescored.append(
            {
                **item,
                "crowds_out_gaps": residual >= 0 and reserve > 0 and residual < reserve,
                "leaves_gap_budget": residual >= 0 and reserve > 0 and residual >= reserve,
            }
        )

    def pick(pool: list[dict[str, Any]], *, multi_gap: bool) -> dict[str, Any] | None:
        if not pool:
            return None
        return max(
            pool,
            key=lambda i: _intent_sort_key(
                i,
                bal=balance,
                cash_reserve=0.0,
                primary_ids=set(),
                multi_gap=multi_gap,
            ),
        )

    capped = (
        [i for i in rescored if _item_buy_cost(i) <= cap] if cap is not None else rescored
    )
    # Un listado de rival no cubre carencia: el dueño puede no aceptar.
    if cap is not None:
        capped = [
            i
            for i in capped
            if not is_rival_market_listing(i) and not i.get("conditional_offer")
        ]
    before = pick(buys, multi_gap=False)
    after = pick(capped, multi_gap=cap is not None)
    ranking_after = sorted(
        capped,
        key=lambda i: _intent_sort_key(
            i, bal=balance, cash_reserve=0.0, primary_ids=set(), multi_gap=cap is not None
        ),
        reverse=True,
    )[:5]

    def _ref(item: dict[str, Any] | None) -> dict[str, Any] | None:
        if not item:
            return None
        cost = _item_buy_cost(item)
        return {
            "player_id": item.get("player_id"),
            "name": item.get("name"),
            "position": item.get("position"),
            "cost": cost,
            "spend_share": round(cost / balance, 4) if balance > 0 else None,
            "is_key_market": bool(item.get("is_key_market")),
            "fills_gap": bool(
                item.get("fills_coverage_gap")
                or item.get("fills_structural")
                or item.get("fills_need")
            ),
            "production_score": item.get("production_score"),
            "priority_score": item.get("priority_score"),
            "crowds_out_gaps": bool(item.get("crowds_out_gaps")),
        }

    reserves = {
        pos: round(
            gap_reserve_cost(
                exclude_position=pos,
                diagnosis=diag,
                structural_needs=needs,
                opportunities=ops,
                balance=balance,
            )
        )
        for pos in ("GK", "DF", "MF", "FW")
    }

    blocked = []
    if cap is not None:
        for item in buys:
            cost = _item_buy_cost(item)
            if cost > cap:
                blocked.append(
                    {
                        "name": item.get("name"),
                        "position": item.get("position"),
                        "cost": cost,
                        "spend_share": round(cost / balance, 4) if balance > 0 else None,
                    }
                )

    return {
        "slug": slug,
        "balance": balance,
        "xi_gap_slots": slots,
        "xi_gap_count": gap_n,
        "spend_cap": round(cap) if cap is not None else None,
        "gap_reserve_by_excluded_position": reserves,
        "buy_candidates": len(buys),
        "blocked_by_spend_cap": blocked,
        "pick_before": _ref(before),
        "pick_after": _ref(after),
        "ranking_after": [_ref(i) for i in ranking_after],
        "changed": (before or {}).get("player_id") != (after or {}).get("player_id"),
    }


# ---------------------------------------------------------------------------
# 2. Concentración de gasto por ciclo en el histórico
# ---------------------------------------------------------------------------

def spend_concentration(slug: str) -> dict[str, Any]:
    history_dir = ROOT / "public" / "data" / "leagues" / slug / "history"
    if not history_dir.is_dir():
        return {"slug": slug, "snapshots": 0, "cycles_with_buys": 0}

    rows: list[dict[str, Any]] = []
    for path in sorted(history_dir.glob("*.json")):
        try:
            snap = _load(path)
        except (OSError, json.JSONDecodeError):
            continue
        actions = (snap.get("decisions") or {}).get("actions") or []
        costs = [
            float(a.get("price") or 0)
            for a in actions
            if a.get("action") in BUY_ACTIONS and float(a.get("price") or 0) > 0
        ]
        if not costs:
            continue
        total = sum(costs)
        rows.append(
            {
                "date": snap.get("date") or path.stem,
                "buys": len(costs),
                "total": total,
                "max_single": max(costs),
                "concentration": round(max(costs) / total, 4) if total > 0 else None,
            }
        )

    if not rows:
        return {"slug": slug, "snapshots": 0, "cycles_with_buys": 0}

    multi = [r for r in rows if r["buys"] >= 2]
    conc = [r["concentration"] for r in multi if r["concentration"] is not None]
    worst = sorted(multi, key=lambda r: -(r["concentration"] or 0))[:5]
    return {
        "slug": slug,
        "snapshots": len(list(history_dir.glob("*.json"))),
        "cycles_with_buys": len(rows),
        "cycles_multi_buy": len(multi),
        "mean_concentration": round(sum(conc) / len(conc), 4) if conc else None,
        "worst_cycles": worst,
    }


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", action="append", help="liga(s) a auditar")
    ap.add_argument("--json", action="store_true", help="salida JSON")
    args = ap.parse_args()

    slugs = args.slug or _league_slugs()
    report = {
        "ab": [ab_on_payload(s) for s in slugs],
        "history": [spend_concentration(s) for s in slugs],
        "thresholds": {
            "spend_share_cap_multi_gap": getattr(config, "SPEND_SHARE_CAP_MULTI_GAP", None),
            "spend_share_cap_min_gaps": getattr(config, "SPEND_SHARE_CAP_MIN_GAPS", None),
            "gap_reserve_max_share": getattr(config, "GAP_RESERVE_MAX_SHARE", None),
        },
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 78)
    print("A/B del orden de compra sobre payloads reales")
    print("=" * 78)
    for row in report["ab"]:
        print(f"\n[{row['slug']}]")
        print(f"  saldo             {row['balance']:>14,.0f} €")
        print(f"  huecos del once   {row['xi_gap_count']}  {row['xi_gap_slots']}")
        cap = row["spend_cap"]
        print(f"  tope por compra   {f'{cap:,.0f} €' if cap else 'no aplica'}")
        print(f"  candidatos compra {row['buy_candidates']}")
        before, after = row["pick_before"], row["pick_after"]

        def _fmt(p: dict[str, Any] | None) -> str:
            if not p:
                return "ninguno (guarda caja)"
            share = p["spend_share"]
            share_txt = f"{share * 100:.0f}% del saldo" if share is not None else "—"
            return f"{p['name']} ({p['position']}) {p['cost']:,.0f} € · {share_txt}"

        print(f"  antes             {_fmt(before)}")
        print(f"  después           {_fmt(after)}")
        print(f"  cambia            {'SÍ' if row['changed'] else 'no'}")
        if len(row.get("ranking_after") or []) > 1:
            print("  ranking nuevo:")
            for i, p in enumerate(row["ranking_after"], 1):
                tags = []
                if p and p["is_key_market"]:
                    tags.append("clave")
                if p and p["fills_gap"]:
                    tags.append("tapa hueco")
                if p:
                    tags.append(f"prod {p['production_score']}")
                    tags.append(f"prio {p['priority_score']}")
                print(f"    {i}. {_fmt(p)}{'  [' + ', '.join(tags) + ']' if tags else ''}")
        if row["blocked_by_spend_cap"]:
            print("  bloqueados por concentración:")
            for b in row["blocked_by_spend_cap"]:
                share = b["spend_share"]
                share_txt = f"{share * 100:.0f}%" if share is not None else "—"
                print(f"    - {b['name']} ({b['position']}) {b['cost']:,.0f} € · {share_txt}")

    print()
    print("=" * 78)
    print("Concentración de gasto por ciclo en el histórico")
    print("=" * 78)
    for row in report["history"]:
        print(f"\n[{row['slug']}] snapshots={row['snapshots']} "
              f"ciclos con compra={row['cycles_with_buys']} "
              f"multi-compra={row.get('cycles_multi_buy', 0)}")
        if row.get("mean_concentration") is not None:
            print(f"  concentración media en ciclos multi-compra: "
                  f"{row['mean_concentration'] * 100:.0f}%")
        for w in row.get("worst_cycles") or []:
            print(f"    {w['date']}  {w['buys']} compras  "
                  f"total {w['total']:,.0f} €  mayor {w['max_single']:,.0f} €  "
                  f"({(w['concentration'] or 0) * 100:.0f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
