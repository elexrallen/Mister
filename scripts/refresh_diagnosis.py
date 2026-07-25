"""
Recomputa diagnóstico / ventas / action_plan sobre latest_data.json
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
from data_engine import build_action_plan, diagnose_squad, save_json  # noqa: E402
from squad_analyzer import analyze_squad, merge_structural_into_diagnosis  # noqa: E402


def main() -> None:
    path = config.LATEST_DATA_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    me = data["me"]
    squad = list(me.get("squad") or [])
    points_phase = data.get("points_phase") or (data.get("kpis") or {}).get("points_phase") or "preseason"

    diagnosis = diagnose_squad(squad)
    plant = analyze_squad(
        squad,
        balance=float(me.get("balance") or 0),
        squad_value=float(me.get("squad_value") or 0),
        points_phase=points_phase,
        market_universe=data.get("market_opportunities") or [],
    )
    diagnosis = merge_structural_into_diagnosis(diagnosis, plant)

    action_plan = build_action_plan(
        me,
        diagnosis,
        data.get("market_opportunities") or [],
        data.get("rivals") or [],
        price_series={},
        rival_upgrades=data.get("rival_upgrades") or [],
        points_phase=points_phase,
        diagnostico_plantilla=plant,
    )

    data["squad_diagnosis"] = diagnosis
    data["diagnostico_plantilla"] = plant
    data["action_plan"] = action_plan
    data["recommendations"] = []
    data["squad_notes"] = []
    data["generated_at"] = datetime.now(timezone.utc).isoformat()

    kpis = data.get("kpis") or {}
    kpis["critical_alerts"] = sum(1 for a in diagnosis["alerts"] if a["level"] == "critical")
    kpis["buy_now_count"] = sum(1 for a in action_plan if a["action"] == "buy_now")
    kpis["wait_count"] = sum(1 for a in action_plan if a["action"] == "wait")
    kpis["sell_count"] = sum(1 for a in action_plan if a["action"] == "sell")
    kpis["clause_bid_count"] = sum(1 for a in action_plan if a["action"] == "clause_bid")
    data["kpis"] = kpis

    save_json(path, data)
    hist = config.HISTORY_DIR / f"{datetime.now(timezone.utc).date().isoformat()}.json"
    config.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    save_json(hist, data)

    fw = diagnosis["by_position"]["FW"]
    sells = [a for a in action_plan if a.get("action") == "sell"]
    print(
        f"OK FW status={fw['status']} starters={fw['starters']} "
        f"alerts={len(diagnosis['alerts'])} sells={len(sells)}"
    )
    for s in sells[:6]:
        print(f"  - {s.get('name')} [{s.get('sell_reason')}] {s.get('why', '')[:100]}")


if __name__ == "__main__":
    main()
