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
from competitive_actions import detect_competition_phase, estimate_gap_funding  # noqa: E402
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
    points_phase = (
        data.get("points_phase")
        or (data.get("kpis") or {}).get("points_phase")
        or (data.get("sources") or {}).get("points_phase")
        or "preseason"
    )
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

    comp = detect_competition_phase(
        season_start=getattr(config, "SEASON_START_DATE", "2026-08-15"),
        points_phase=points_phase,
    )
    competition_phase = str(comp.get("competition_phase") or "preseason")
    plant["competition_phase"] = competition_phase
    plant["days_to_kickoff"] = comp.get("days_to_kickoff")
    plant["season_start"] = comp.get("season_start")
    plant["points_phase"] = points_phase

    market_raw = data.get("market_opportunities") or []
    # Preservar flag de mercado del día si ya venía marcado
    for row in market_raw:
        if "on_daily_market" not in row:
            row["on_daily_market"] = row.get("seller") == "market"

    opportunities = classify_market_opportunities(
        market_raw,
        {},
        {},
        balance,
        diagnosis,
        allow_synthetic=False,
        structural_needs=plant.get("structural_needs") or [],
        diagnostico_plantilla=plant,
        squad=squad,
        competition_phase=competition_phase,
    )

    funding = estimate_gap_funding(
        plant.get("structural_needs") or [],
        opportunities,
        balance,
        top_n=3,
    )

    action_plan, daily_package = build_action_plan(
        me,
        diagnosis,
        opportunities,
        data.get("rivals") or [],
        price_series={},
        rival_upgrades=data.get("rival_upgrades") or [],
        points_phase=points_phase,
        diagnostico_plantilla=plant,
    )

    n_daily = sum(1 for o in opportunities if o.get("on_daily_market"))

    data["squad_diagnosis"] = diagnosis
    data["diagnostico_plantilla"] = plant
    data["market_opportunities"] = opportunities
    data["action_plan"] = action_plan
    data["daily_package"] = daily_package
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

    sources = data.get("sources") or {}
    sources["points_phase"] = points_phase
    sources["competition_phase"] = competition_phase
    sources["season_start"] = comp.get("season_start")
    sources["daily_market_count"] = n_daily
    sources["market_day_slots"] = int(getattr(config, "MARKET_DAY_SLOTS", 16))
    data["sources"] = sources

    meta = data.get("meta") or {}
    meta["season_start"] = comp.get("season_start")
    meta["days_to_kickoff"] = comp.get("days_to_kickoff")
    meta["competition_phase"] = competition_phase
    notes = list(meta.get("data_notes") or [])
    kick_note = (
        f"Campeonato: J1 {comp.get('season_start')} "
        f"(faltan {comp.get('days_to_kickoff')} días) · fase {competition_phase}."
    )
    depth_note = (
        "Objetivo plantilla 15 (GK2/DF5/MF5/FW3): pujar si falta cobertura; "
        "si la línea ya está cubierta, no insistir salvo upgrade."
    )
    mkt_note = (
        f"Mercado de hoy: {n_daily} jugadores "
        f"(referencia {getattr(config, 'MARKET_DAY_SLOTS', 16)} plazas/día)."
    )
    for n in (kick_note, depth_note, mkt_note):
        if n not in notes:
            notes.append(n)
    meta["data_notes"] = notes
    data["meta"] = meta

    kpis = data.get("kpis") or {}
    kpis["critical_alerts"] = sum(1 for a in diagnosis["alerts"] if a["level"] == "critical")
    kpis["market_count"] = len(opportunities)
    kpis["buy_now_count"] = sum(1 for a in action_plan if a["action"] == "buy_now")
    kpis["wait_count"] = sum(1 for a in action_plan if a["action"] == "wait")
    kpis["sell_count"] = sum(1 for a in action_plan if a["action"] == "sell")
    kpis["clause_bid_count"] = sum(1 for a in action_plan if a["action"] == "clause_bid")
    kpis["funding_target"] = funding.get("funding_target")
    kpis["funding_shortfall"] = funding.get("funding_shortfall")
    kpis["points_phase"] = points_phase
    kpis["competition_phase"] = competition_phase
    kpis["season_start"] = comp.get("season_start")
    kpis["days_to_kickoff"] = comp.get("days_to_kickoff")
    kpis["lines_ok"] = plant.get("lines_ok")
    kpis["depth_gaps"] = plant.get("depth_gaps")
    kpis["daily_market_count"] = n_daily
    kpis["market_day_slots"] = int(getattr(config, "MARKET_DAY_SLOTS", 16))
    kpis["ideal_squad"] = plant.get("ideal_squad") or getattr(
        config, "IDEAL_SQUAD", {"GK": 2, "DF": 5, "MF": 5, "FW": 3}
    )
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
    waits = [a for a in action_plan if a.get("action") == "wait"]
    sells = [a for a in action_plan if a.get("action") == "sell"]
    covered_waits = [a for a in waits if a.get("line_already_covered")]
    gap_buys = [a for a in buys if a.get("fills_coverage_gap")]
    prim = (daily_package or {}).get("primary") or {}
    sec = (daily_package or {}).get("secondary") or {}
    print(
        f"OK phase={competition_phase} days_to_j1={comp.get('days_to_kickoff')} "
        f"lines_ok={plant.get('lines_ok')} depth_gaps={plant.get('depth_gaps')} "
        f"funding_target={funding.get('funding_target'):,.0f} "
        f"shortfall={funding.get('funding_shortfall'):,.0f} "
        f"buys={len(buys)} gap_buys={len(gap_buys)} waits={len(waits)} "
        f"covered_waits={len(covered_waits)} sells={len(sells)} "
        f"FW_starters={fw.get('starters_real', fw.get('starters'))}"
    )
    print(
        f"  package primary={prim.get('name')} secondary={sec.get('name')} "
        f"spend={daily_package.get('spend_cap')} residual={daily_package.get('residual_after')}"
    )
    for a in buys[:12]:
        print(
            f"  buy [{a.get('queue_role')}] {a.get('name')} [{a.get('position')}] "
            f"cov={a.get('position_coverage')} gap={a.get('fills_coverage_gap')} "
            f"daily={a.get('on_daily_market')} urg={a.get('urgency')} "
            f"cost={a.get('cost') or a.get('bid')}"
        )
    for a in waits:
        if a.get("queue_role") in ("alt_if_lost", "do_not_stack"):
            print(
                f"  {a.get('queue_role')} {a.get('name')} [{a.get('position')}] "
                f"{(a.get('why') or '')[:90]}"
            )
    for a in covered_waits[:8]:
        print(
            f"  wait-covered {a.get('name')} [{a.get('position')}] "
            f"{(a.get('why') or '')[:90]}"
        )
    for s in sells[:6]:
        print(f"  sell {s.get('name')} [{s.get('sell_reason')}] {s.get('why', '')[:100]}")


if __name__ == "__main__":
    main()
