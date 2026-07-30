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
from competitive_actions import (  # noqa: E402
    build_recommended_gw_xi,
    detect_competition_phase,
    estimate_gap_funding,
)
from data_engine import (  # noqa: E402
    build_action_plan,
    classify_market_opportunities,
    diagnose_squad,
    save_json,
)
from squad_analyzer import analyze_squad, merge_structural_into_diagnosis  # noqa: E402
from target_board import (  # noqa: E402
    build_target_board,
    funding_plan_from_board,
    save_target_board,
)


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Refresh diagnosis without re-scraping Mister")
    parser.add_argument("--league", default="", help="slug de liga (default: latest_data / default slug)")
    args = parser.parse_args(argv)

    if args.league:
        L = config.get_league(args.league)
        path = config.league_data_path(L["slug"])
    else:
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
    season_start = (
        (data.get("sources") or {}).get("season_start")
        or (data.get("kpis") or {}).get("season_start")
        or getattr(config, "SEASON_START_DATE", "2026-08-15")
    )

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
        season_start=season_start,
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

    market_mode = str(
        data.get("market_mode")
        or (data.get("sources") or {}).get("market_mode")
        or "auction"
    )
    board_candidates: list = list(opportunities)
    for u in data.get("rival_upgrades") or []:
        board_candidates.append(
            {
                "id": u.get("player_id"),
                "name": u.get("name"),
                "position": u.get("position"),
                "team": u.get("team"),
                "price": u.get("price") or u.get("market_value"),
                "puja_recomendada": u.get("clause") or u.get("bid"),
                "clause": u.get("clause"),
                "on_daily_market": False,
                "seller": "rival",
                "production_score": u.get("production_score"),
                "ff_mister_avg": u.get("ff_mister_avg"),
                "external": u.get("external") or {},
                "lineup_prob": u.get("lineup_prob"),
                "sample_thin": u.get("sample_thin"),
            }
        )
    # Ampliar universo con pool / rivales para rellenar cupos IDEAL
    for riv in data.get("rivals") or []:
        for p in riv.get("squad") or []:
            board_candidates.append(
                {
                    **p,
                    "puja_recomendada": p.get("clause") or p.get("puja_recomendada") or p.get("price"),
                    "on_daily_market": False,
                    "seller": "rival",
                }
            )
    for p in (data.get("pool_top") or data.get("free_agents") or [])[:400]:
        board_candidates.append(p)

    slug = str(
        data.get("league_slug")
        or (args.league and config.get_league(args.league)["slug"])
        or config.DEFAULT_LEAGUE_SLUG
    )
    target_board = build_target_board(
        slug=slug,
        structural_needs=plant.get("structural_needs") or [],
        candidates=board_candidates,
        balance=balance,
        squad=squad,
        squad_value=float(me.get("squad_value") or 0) or None,
        price_series={},
        market_mode=market_mode,
    )
    try:
        save_target_board(slug, target_board)
    except Exception as exc:  # noqa: BLE001
        print(f"warn: no se pudo guardar target_board: {exc}")

    funding = funding_plan_from_board(target_board, balance=balance)
    if not funding.get("funding_target"):
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
        market_mode=market_mode,
        target_board=target_board,
        funding_info=funding,
        max_squad=config.league_max_squad(league),
    )

    n_daily = sum(1 for o in opportunities if o.get("on_daily_market"))

    data["squad_diagnosis"] = diagnosis
    data["diagnostico_plantilla"] = plant
    data["market_opportunities"] = opportunities
    data["target_board"] = target_board
    data["action_plan"] = action_plan
    data["daily_package"] = daily_package
    data["funding_plan"] = {
        "target": funding.get("funding_target"),
        "shortfall": funding.get("funding_shortfall"),
        "cash_tight": funding.get("cash_tight"),
        "cash_reserved": funding.get("cash_reserved"),
        "gaps": funding.get("gap_costs") or funding.get("selected_buys") or [],
        "positions": funding.get("positions") or [],
    }
    data["recommendations"] = []
    data["squad_notes"] = []
    data["recommended_xi"] = build_recommended_gw_xi(
        squad,
        formation=me.get("formation"),
        matchday=data.get("matchday") if isinstance(data.get("matchday"), dict) else {},
    )
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
    # History por liga si aplica
    if slug:
        hist_dir = config.league_history_dir(str(slug))
        hist_dir.mkdir(parents=True, exist_ok=True)
        save_json(hist_dir / f"{datetime.now(timezone.utc).date().isoformat()}.json", data)
    if not args.league or slug == config.DEFAULT_LEAGUE_SLUG:
        hist = config.HISTORY_DIR / f"{datetime.now(timezone.utc).date().isoformat()}.json"
        config.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        save_json(hist, data)
        if path != config.LATEST_DATA_PATH:
            save_json(config.LATEST_DATA_PATH, data)

    fw = diagnosis["by_position"]["FW"]
    buys = [a for a in action_plan if a.get("action") == "buy_now"]
    waits = [a for a in action_plan if a.get("action") == "wait"]
    sells = [a for a in action_plan if a.get("action") == "sell"]
    covered_waits = [a for a in waits if a.get("line_already_covered")]
    gap_buys = [a for a in buys if a.get("fills_coverage_gap")]
    prim = (daily_package or {}).get("primary") or {}
    sec = (daily_package or {}).get("secondary") or {}
    hedges = (daily_package or {}).get("hedges") or []
    tb = target_board or {}
    w = tb.get("wealth") or {}
    tot = tb.get("totals") or {}
    ps = tb.get("perfect_squad") or []
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
        f"  perfect_squad={len(ps)}/15 cost={tot.get('cost_sum')} "
        f"cap={w.get('budget_cap')} wealth={w.get('total')} "
        f"keep={len((tb.get('moves') or {}).get('keep') or [])} "
        f"buy={len((tb.get('moves') or {}).get('buy') or [])} "
        f"patches={len(tb.get('daily_patches') or [])} "
        f"reserved={tb.get('cash_reserved')}"
    )
    hedge_names = ", ".join(h.get("name") or "?" for h in hedges[:4]) or "—"
    print(
        f"  package combo={daily_package.get('combo')} "
        f"primary={prim.get('name')} secondary={sec.get('name')} "
        f"hedges={hedge_names} "
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
        if a.get("queue_role") in ("alt_if_lost", "alt_unfunded", "do_not_stack"):
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
