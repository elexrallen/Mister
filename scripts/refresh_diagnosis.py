"""
Recomputa diagnóstico, mercado, ventas y action_plan sobre latest_data.json
sin volver a scrapear Mister (útil tras cambios de lógica).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
from competitive_actions import estimate_gap_funding  # noqa: E402
from data_engine import (  # noqa: E402
    build_action_plan,
    classify_market_opportunities,
    diagnose_squad,
    save_json,
)
from squad_analyzer import analyze_squad, merge_structural_into_diagnosis  # noqa: E402


def main() -> None:
    path = config.LATEST_DATA_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    me = data["me"]
    squad = list(me.get("squad") or [])
    points_phase = data.get("points_phase") or (data.get("kpis") or {}).get("points_phase") or "preseason"
    balance = float(me.get("balance") or 0)

    diagnosis = diagnose_squad(squad)
    plant = analyze_squad(
        squad,
        balance=balance,
        squad_value=float(me.get("squad_value") or 0),
        points_phase=points_phase,
        market_universe=data.get("market_opportunities") or [],
    )
    diagnosis = merge_structural_into_diagnosis(diagnosis, plant)

    market_raw = data.get("market_opportunities") or []
    opportunities = classify_market_opportunities(
        market_raw,
        {},
        {},
        balance,
        diagnosis,
        allow_synthetic=False,
        structural_needs=plant.get("structural_needs") or [],
    )

    funding = estimate_gap_funding(
        plant.get("structural_needs") or [],
        opportunities,
        balance,
        top_n=3,
    )

    action_plan = build_action_plan(
        me,
        diagnosis,
        opportunities,
        data.get("rivals") or [],
        price_series={},
        rival_upgrades=data.get("rival_upgrades") or [],
        points_phase=points_phase,
        diagnostico_plantilla=plant,
    )

    data["squad_diagnosis"] = diagnosis
    data["diagnostico_plantilla"] = plant
    data["market_opportunities"] = opportunities
    data["action_plan"] = action_plan
    data["funding_plan"] = {
        "target": funding.get("funding_target"),
        "shortfall": funding.get("funding_shortfall"),
        "cash_tight": funding.get("cash_tight"),
        "gaps": funding.get("gap_costs") or [],
        "positions": funding.get("positions") or [],
    }
    data["recommendations"] = []
    data["squad_notes"] = []
    data["generated_at"] = datetime.now(timezone.utc).isoformat()

    kpis = data.get("kpis") or {}
    kpis["critical_alerts"] = sum(1 for a in diagnosis["alerts"] if a["level"] == "critical")
    kpis["market_count"] = len(opportunities)
    kpis["buy_now_count"] = sum(1 for a in action_plan if a["action"] == "buy_now")
    kpis["wait_count"] = sum(1 for a in action_plan if a["action"] == "wait")
    kpis["sell_count"] = sum(1 for a in action_plan if a["action"] == "sell")
    kpis["clause_bid_count"] = sum(1 for a in action_plan if a["action"] == "clause_bid")
    kpis["funding_target"] = funding.get("funding_target")
    kpis["funding_shortfall"] = funding.get("funding_shortfall")
    shortfall = float(funding.get("funding_shortfall") or 0)
    target = float(funding.get("funding_target") or 0)
    if shortfall > 0 and target > 0 and shortfall >= target * 0.35:
        kpis["budget_pressure"] = "high"
    elif shortfall > 0 or (target > 0 and balance < target):
        kpis["budget_pressure"] = "medium"
    else:
        kpis["budget_pressure"] = kpis.get("budget_pressure") or "low"
    data["kpis"] = kpis

    save_json(path, data)
    hist = config.HISTORY_DIR / f"{datetime.now(timezone.utc).date().isoformat()}.json"
    config.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    save_json(hist, data)

    fw = diagnosis["by_position"]["FW"]
    buys = [a for a in action_plan if a.get("action") == "buy_now"]
    sells = [a for a in action_plan if a.get("action") == "sell"]
    print(
        f"OK funding_target={funding.get('funding_target'):,.0f} "
        f"shortfall={funding.get('funding_shortfall'):,.0f} "
        f"buys={len(buys)} sells={len(sells)} FW_starters={fw['starters']}"
    )
    for a in buys:
        print(
            f"  buy {a.get('name')} [{a.get('position')}] "
            f"crowds={a.get('crowds_out_gaps')} leaves={a.get('leaves_gap_budget')} "
            f"cost={a.get('cost') or a.get('bid')} residual={a.get('residual_budget')}"
        )
    for s in sells:
        print(f"  sell {s.get('name')} [{s.get('sell_reason')}] {s.get('why', '')[:100]}")


if __name__ == "__main__":
    main()
