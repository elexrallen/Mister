"""
Auditoría de rendimiento: ¿el consejo del advisor sigue siendo óptimo?

Tres capas, todas sobre el histórico diario ya materializado:

1. **Modelo** — xPts vs puntos reales (calibración) y ranking (Spearman + lift).
2. **Decisiones** — once recomendado vs once alineado vs naive por precio, y
   `buy_now`/`avoid` contra los puntos y el precio posteriores.
3. **Pipeline** — mock, 429, emparejados FF/FotMob, segundos de ciclo.

El módulo es puro sobre snapshots: se puede probar sin red. El CLI recorre
`public/data/leagues/*/history` y falla con código 1 si un umbral se rompe.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from pathlib import Path
from typing import Any

import config
from model_calibration import MIN_XPTS_FOR_SAMPLE, build_calibration, collect_pairs

log = logging.getLogger("performance_audit")

BUY_ACTIONS = frozenset({"buy_now", "clause_bid"})
AVOID_ACTIONS = frozenset({"avoid"})
# Pre-kickoff real: no incluir ""/None (un snapshot a mitad de GW sin status
# no es predicción). `unstarted` es el estado Mister antes del primer partido.
PRE_KICKOFF_STATUS = frozenset({"pending", "scheduled", "preview", "pre", "unstarted"})
LIVE_STATUS = frozenset({"ongoing", "live", "finished", "closed"})
# Comparar buy vs avoid exige muestra mínima por lado (2 jugadores no bastan).
MIN_MARKET_SIDE = 3
MIN_MARKET_ACTIONS = 4

# Umbrales: con una jornada mixto real (~J1 2026) el titular tiene MAE ~0.75 y
# Spearman > 0.3. Si caemos por debajo, el modelo ha dejado de ordenar.
DEFAULT_GATES = {
    "min_sample": 30,
    "min_spearman": 0.15,
    "min_lift": 1.15,
    "max_titular_mae": 3.5,
    "max_titular_optimistic_bias": 2.5,
    "max_xi_gap_pct": 15.0,
}


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _ranks(values: list[float]) -> list[float]:
    """Rangos 1-based; empates al promedio (Spearman)."""
    indexed = sorted(enumerate(values), key=lambda iv: iv[1])
    ranks = [0.0] * len(values)
    i = 0
    n = len(indexed)
    while i < n:
        j = i
        while j + 1 < n and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg
        i = j + 1
    return ranks


def spearman_rho(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    rho = pearson(_ranks(xs), _ranks(ys))
    return round(rho, 6) if rho is not None else None


def slim_decisions(payload: dict[str, Any]) -> dict[str, Any]:
    """Recorte de once / cola / plantilla para el snapshot diario."""
    xi_block = payload.get("recommended_xi") if isinstance(payload.get("recommended_xi"), dict) else {}
    xi_ids: list[str] = []
    for row in xi_block.get("xi") or []:
        if isinstance(row, dict) and row.get("player_id"):
            xi_ids.append(str(row["player_id"]))
    captain = xi_block.get("captain")
    captain_id = None
    if isinstance(captain, dict):
        captain_id = captain.get("player_id") or captain.get("id")
    elif isinstance(captain, str) and captain:
        captain_id = captain
    current = xi_block.get("current") if isinstance(xi_block.get("current"), dict) else {}
    squad = (payload.get("me") or {}).get("squad") or []
    squad_ids = [
        str(p.get("id"))
        for p in squad
        if isinstance(p, dict) and p.get("id") is not None
    ]
    actions: list[dict[str, Any]] = []
    for item in payload.get("action_plan") or []:
        if not isinstance(item, dict) or item.get("player_id") is None:
            continue
        actions.append(
            {
                "player_id": str(item["player_id"]),
                "action": item.get("action"),
                "price": _num(item.get("price") or item.get("market_value")),
                "name": item.get("name"),
            }
        )
    return {
        "xi_ids": xi_ids,
        "captain_id": str(captain_id) if captain_id else None,
        "formation": xi_block.get("formation"),
        "xpts_total": (xi_block.get("summary") or {}).get("xpts_total")
        if isinstance(xi_block.get("summary"), dict)
        else None,
        "current_starter_ids": [str(i) for i in (current.get("starter_ids") or []) if i],
        "current_points": current.get("points"),
        "current_rank": current.get("rank"),
        "squad_ids": squad_ids,
        "actions": actions,
    }


def slim_pipeline(payload: dict[str, Any]) -> dict[str, Any]:
    sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
    ext = sources.get("external") if isinstance(sources.get("external"), dict) else {}
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    live = meta.get("live_meta") if isinstance(meta.get("live_meta"), dict) else {}
    return {
        "mister": sources.get("mister") or live.get("source"),
        "honest_live": sources.get("honest_live"),
        "external_matched": ext.get("matched"),
        "fotmob_matched": ext.get("fotmob_matched"),
        "rate_limited": ext.get("rate_limited"),
        "ff": ext.get("futbolfantasy"),
        "fotmob": ext.get("fotmob"),
        "pipeline_seconds": meta.get("pipeline_seconds"),
    }


def load_history_snapshots(slug: str, days: int = 45) -> list[dict[str, Any]]:
    history_dir = config.league_history_dir(slug)
    if not history_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    cap = min(
        max(int(days or 45), int(days or 45) * 3),
        int(getattr(config, "HISTORY_SNAPSHOTS_MAX", 90) or 90),
    )
    for snap_path in sorted(history_dir.glob("*.json"))[-cap:]:
        try:
            snap = json.loads(snap_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(snap, dict):
            out.append(snap)
    return out


def load_latest_payload(slug: str) -> dict[str, Any] | None:
    path = config.league_data_path(slug)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def discover_league_slugs() -> list[str]:
    index_path = config.LEAGUES_INDEX_PATH
    if index_path.is_file():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            slugs = [
                str(row["slug"])
                for row in (data.get("leagues") or [])
                if isinstance(row, dict) and row.get("slug")
            ]
            if slugs:
                return slugs
        except (OSError, json.JSONDecodeError):
            pass
    root = config.LEAGUES_DIR
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def _prediction_quality(status: str) -> int:
    st = str(status or "").lower().strip()
    if st in LIVE_STATUS:
        return 0
    if st in PRE_KICKOFF_STATUS:
        return 1
    # status vacío / desconocido: no es predicción pre-partido
    return 0


def collect_closed_rows(
    snapshots: list[dict[str, Any]],
    *,
    current_jornada: Any = None,
) -> list[dict[str, Any]]:
    """Una fila por (jornada, jugador) con xPts pre-kickoff y puntos reales."""
    pairs = collect_pairs(snapshots)
    rows: list[dict[str, Any]] = []
    for jornada, by_pid in pairs.items():
        if current_jornada is not None and jornada == current_jornada:
            continue
        for pid, rec in by_pid.items():
            xpts = _num(rec.get("xpts"))
            real = _num(rec.get("real"))
            if xpts is None or real is None:
                continue
            if xpts < MIN_XPTS_FOR_SAMPLE and real <= 0:
                continue
            rows.append(
                {
                    "jornada": jornada,
                    "player_id": str(pid),
                    "xpts": xpts,
                    "real": real,
                    "p_play": _num(rec.get("p_play")),
                }
            )
    return rows


def ranking_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """¿Ordenar por xPts acierta el orden real? Spearman + lift cuartiles."""
    if len(rows) < 8:
        return {
            "status": "empty" if not rows else "thin",
            "sample": len(rows),
            "spearman": None,
            "lift": None,
            "top_mean": None,
            "bottom_mean": None,
            "reading": "Aún no hay suficientes pares xPts/real para juzgar el ranking.",
        }
    xs = [r["xpts"] for r in rows]
    ys = [r["real"] for r in rows]
    rho = spearman_rho(xs, ys)
    ordered = sorted(rows, key=lambda r: r["xpts"])
    q = max(1, len(ordered) // 4)
    bottom = [_num(r["real"]) or 0.0 for r in ordered[:q]]
    top = [_num(r["real"]) or 0.0 for r in ordered[-q:]]
    bottom_mean = _mean(bottom) or 0.0
    top_mean = _mean(top) or 0.0
    lift = (top_mean / bottom_mean) if bottom_mean > 0.05 else None
    status = "ok"
    if rho is None:
        status = "thin"
    elif rho < 0:
        status = "fail"
    reading = (
        f"Spearman {rho:.2f} entre xPts y puntos reales (n={len(rows)}). "
        if rho is not None
        else "No hay Spearman (varianza nula). "
    )
    if lift is not None:
        reading += (
            f"El cuartil alto de xPts puntúa {top_mean:.1f} vs {bottom_mean:.1f} "
            f"del bajo (lift {lift:.2f}×)."
        )
    return {
        "status": status,
        "sample": len(rows),
        "spearman": round(rho, 3) if rho is not None else None,
        "lift": round(lift, 2) if lift is not None else None,
        "top_mean": round(top_mean, 2),
        "bottom_mean": round(bottom_mean, 2),
        "reading": reading.strip(),
    }


def _sum_points(ids: list[str], gw_points: dict[str, Any]) -> tuple[float | None, int]:
    total = 0.0
    n = 0
    for pid in ids:
        pts = _num(gw_points.get(str(pid)))
        if pts is None:
            continue
        total += pts
        n += 1
    if not n:
        return None, 0
    return total, n


def _naive_top_ids(
    squad_ids: list[str],
    scores: dict[str, float],
    n: int = 11,
) -> list[str]:
    ranked = sorted(
        (pid for pid in squad_ids if pid in scores),
        key=lambda pid: scores[pid],
        reverse=True,
    )
    return ranked[:n]


def _decision_richness(snap: dict[str, Any]) -> int:
    """¿El snapshot guarda once/acciones auditables? 0 = solo precios/xPts."""
    decisions = snap.get("decisions") if isinstance(snap.get("decisions"), dict) else {}
    if decisions.get("xi_ids"):
        return 2
    if decisions.get("actions"):
        return 1
    return 0


def _last_prediction_snapshot(snaps: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Mejor snapshot de predicción: pre-kickoff gana, pero un `unstarted`
    sin `decisions` no debe tapar un mid-GW que sí guarda el once.
    """
    best = None
    best_key: tuple[int, int, int, str] = (-1, -1, -1, "")
    for snap in snaps:
        q = _prediction_quality(str(snap.get("gameweek_status") or ""))
        rich = _decision_richness(snap)
        date = str(snap.get("date") or "")
        # has_dec primero: sin consejos no sirve para once/mercado
        key = (1 if rich > 0 else 0, q, rich, date)
        if key > best_key:
            best = snap
            best_key = key
    return best


def _result_snapshot(snaps: list[dict[str, Any]]) -> dict[str, Any] | None:
    with_pts = [s for s in snaps if isinstance(s.get("gw_points"), dict) and s["gw_points"]]
    if not with_pts:
        return None
    return max(with_pts, key=lambda s: str(s.get("date") or ""))


def evaluate_xi(
    snapshots: list[dict[str, Any]],
    *,
    current_jornada: Any = None,
) -> dict[str, Any]:
    """Once recomendado vs alineado vs naive (precio) en jornadas ya cerradas."""
    by_gw: dict[Any, list[dict[str, Any]]] = {}
    for snap in snapshots:
        if not isinstance(snap, dict) or snap.get("jornada") is None:
            continue
        by_gw.setdefault(snap["jornada"], []).append(snap)

    per_gw: list[dict[str, Any]] = []
    for jornada, snaps in sorted(by_gw.items(), key=lambda kv: (kv[0] is None, kv[0])):
        if current_jornada is not None and jornada == current_jornada:
            continue
        pred = _last_prediction_snapshot(snaps)
        result = _result_snapshot(snaps)
        if not pred or not result:
            continue
        decisions = pred.get("decisions") if isinstance(pred.get("decisions"), dict) else {}
        xi_ids = [str(i) for i in (decisions.get("xi_ids") or []) if i]
        if not xi_ids:
            continue
        gw_points = result.get("gw_points") or {}
        rec_pts, rec_n = _sum_points(xi_ids, gw_points)
        current_ids = [str(i) for i in (decisions.get("current_starter_ids") or []) if i]
        cur_pts, cur_n = _sum_points(current_ids, gw_points)
        squad_ids = [str(i) for i in (decisions.get("squad_ids") or []) if i]
        prices = pred.get("prices") if isinstance(pred.get("prices"), dict) else {}
        price_scores = {
            str(pid): float(v) for pid, v in prices.items() if _num(v) is not None
        }
        n_slots = len(xi_ids) or 11
        naive_ids = _naive_top_ids(squad_ids or list(price_scores), price_scores, n=n_slots)
        naive_pts, naive_n = _sum_points(naive_ids, gw_points)
        if rec_pts is None:
            continue
        per_gw.append(
            {
                "jornada": jornada,
                "recommended": rec_pts,
                "recommended_n": rec_n,
                "current": cur_pts,
                "current_n": cur_n,
                "naive_price": naive_pts,
                "naive_n": naive_n,
                "captain_id": decisions.get("captain_id"),
                "captain_points": _num(gw_points.get(str(decisions["captain_id"])))
                if decisions.get("captain_id")
                else None,
            }
        )

    if not per_gw:
        return {
            "status": "empty",
            "sample_gws": 0,
            "recommended_pts": None,
            "current_pts": None,
            "naive_price_pts": None,
            "gap_vs_current_pct": None,
            "per_jornada": [],
            "reading": (
                "Aún no hay snapshots con once recomendado y jornada cerrada. "
                "A partir del próximo ciclo el histórico guarda `decisions`."
            ),
        }

    rec_total = sum(g["recommended"] for g in per_gw)
    cur_vals = [g["current"] for g in per_gw if g["current"] is not None]
    naive_vals = [g["naive_price"] for g in per_gw if g["naive_price"] is not None]
    cur_total = sum(cur_vals) if cur_vals else None
    naive_total = sum(naive_vals) if naive_vals else None
    gap_pct = None
    if cur_total is not None and cur_total != 0:
        gap_pct = (rec_total - cur_total) / abs(cur_total) * 100.0
    status = "ok"
    if gap_pct is not None and gap_pct < -1.0:
        status = "fail" if gap_pct <= -15.0 else "warn"
    reading = (
        f"Once recomendado {rec_total:.0f} pts en {len(per_gw)} jornada(s) cerrada(s)"
    )
    if cur_total is not None:
        reading += f" vs {cur_total:.0f} del once alineado"
        if gap_pct is not None:
            reading += f" ({gap_pct:+.1f}%)"
    if naive_total is not None:
        reading += f"; naive por precio {naive_total:.0f}"
    reading += "."
    return {
        "status": status,
        "sample_gws": len(per_gw),
        "recommended_pts": round(rec_total, 1),
        "current_pts": round(cur_total, 1) if cur_total is not None else None,
        "naive_price_pts": round(naive_total, 1) if naive_total is not None else None,
        "gap_vs_current_pct": round(gap_pct, 1) if gap_pct is not None else None,
        "per_jornada": per_gw,
        "reading": reading,
    }


def evaluate_market(
    snapshots: list[dict[str, Any]],
    *,
    current_jornada: Any = None,
) -> dict[str, Any]:
    """`buy_now` vs `avoid`: puntos de la jornada y delta de precio a 3–7 días."""
    ordered = sorted(
        (s for s in snapshots if isinstance(s, dict)),
        key=lambda s: str(s.get("date") or ""),
    )
    by_gw: dict[Any, list[dict[str, Any]]] = {}
    for snap in ordered:
        if snap.get("jornada") is None:
            continue
        by_gw.setdefault(snap["jornada"], []).append(snap)

    buy_pts: list[float] = []
    avoid_pts: list[float] = []
    buy_delta: list[float] = []
    avoid_delta: list[float] = []
    sample_actions = 0

    for jornada, snaps in by_gw.items():
        if current_jornada is not None and jornada == current_jornada:
            continue
        # Solo consejos previos al primer partido: mid-GW / finished no cuentan.
        pre_snaps = [
            s
            for s in snaps
            if _prediction_quality(str(s.get("gameweek_status") or "")) > 0
        ]
        pred = _last_prediction_snapshot(pre_snaps) if pre_snaps else None
        result = _result_snapshot(snaps)
        if not pred:
            continue
        decisions = pred.get("decisions") if isinstance(pred.get("decisions"), dict) else {}
        actions = decisions.get("actions") or []
        if not actions:
            continue
        gw_points = (result or {}).get("gw_points") or {}
        later = [s for s in ordered if str(s.get("date") or "") > str(pred.get("date") or "")]
        later_prices = later[-1].get("prices") if later else None
        pred_prices = pred.get("prices") if isinstance(pred.get("prices"), dict) else {}
        for act in actions:
            if not isinstance(act, dict):
                continue
            kind = str(act.get("action") or "")
            pid = str(act.get("player_id") or "")
            if not pid or kind not in BUY_ACTIONS | AVOID_ACTIONS:
                continue
            sample_actions += 1
            pts = _num(gw_points.get(pid))
            p0 = _num(pred_prices.get(pid)) if pred_prices else _num(act.get("price"))
            p1 = _num((later_prices or {}).get(pid)) if isinstance(later_prices, dict) else None
            delta = (p1 - p0) if p0 is not None and p1 is not None else None
            if kind in BUY_ACTIONS:
                if pts is not None:
                    buy_pts.append(pts)
                if delta is not None:
                    buy_delta.append(delta)
            else:
                if pts is not None:
                    avoid_pts.append(pts)
                if delta is not None:
                    avoid_delta.append(delta)

    if sample_actions < MIN_MARKET_ACTIONS:
        return {
            "status": "empty" if sample_actions == 0 else "thin",
            "sample": sample_actions,
            "buy_now_pts": None,
            "avoid_pts": None,
            "buy_now_price_delta": None,
            "avoid_price_delta": None,
            "reading": (
                "Aún no hay acciones `buy_now`/`avoid` pre-partido guardadas "
                "en el histórico para juzgar el mercado a posteriori."
            ),
        }

    buy_mean = _mean(buy_pts)
    avoid_mean = _mean(avoid_pts)
    status = "ok"
    thin_compare = (
        buy_mean is not None
        and avoid_mean is not None
        and (len(buy_pts) < MIN_MARKET_SIDE or len(avoid_pts) < MIN_MARKET_SIDE)
    )
    if thin_compare:
        status = "thin"
    elif buy_mean is not None and avoid_mean is not None and buy_mean + 0.4 < avoid_mean:
        status = "fail"
    elif buy_mean is not None and avoid_mean is not None and buy_mean < avoid_mean:
        status = "warn"
    reading = f"{sample_actions} acciones de mercado con desenlace."
    if buy_mean is not None:
        reading += f" buy_now {buy_mean:.1f} pts/jugador"
    if avoid_mean is not None:
        reading += f" vs avoid {avoid_mean:.1f}"
    buy_d = _mean(buy_delta)
    if buy_d is not None:
        reading += f"; Δprecio medio buy_now {buy_d:+.0f} €"
    if thin_compare:
        reading += (
            f" (muestra fina: buy={len(buy_pts)} avoid={len(avoid_pts)}; "
            f"hace falta ≥{MIN_MARKET_SIDE} por lado)"
        )
    reading += "."
    return {
        "status": status,
        "sample": sample_actions,
        "buy_now_pts": round(buy_mean, 2) if buy_mean is not None else None,
        "avoid_pts": round(avoid_mean, 2) if avoid_mean is not None else None,
        "buy_now_price_delta": round(buy_d) if buy_d is not None else None,
        "avoid_price_delta": round(_mean(avoid_delta) or 0) if avoid_delta else None,
        "reading": reading,
    }


def evaluate_pipeline(latest: dict[str, Any] | None) -> dict[str, Any]:
    if not latest:
        return {
            "status": "empty",
            "issues": [],
            "reading": "Sin latest_data.json para auditar el pipeline.",
        }
    pipe = slim_pipeline(latest)
    issues: list[str] = []
    mister = str(pipe.get("mister") or "")
    if "mock" in mister.lower():
        issues.append("fuente Mister = mock")
    if pipe.get("rate_limited"):
        issues.append(f"rate_limited={pipe['rate_limited']}")
    matched = pipe.get("external_matched")
    if isinstance(matched, (int, float)) and matched < 40:
        issues.append(f"FF emparejó solo {int(matched)} jugadores")
    seconds = pipe.get("pipeline_seconds")
    if isinstance(seconds, (int, float)) and seconds > 900:
        issues.append(f"ciclo lento ({seconds:.0f}s)")
    status = "ok"
    if any("mock" in i or "rate_limited" in i for i in issues):
        status = "fail"
    elif issues:
        status = "warn"
    reading = "Pipeline sano." if not issues else "Pipeline: " + "; ".join(issues) + "."
    if seconds is not None and status == "ok":
        reading = f"Pipeline sano ({seconds:.0f}s)." if isinstance(seconds, (int, float)) else reading
    return {
        "status": status,
        "issues": issues,
        "mister": pipe.get("mister"),
        "rate_limited": pipe.get("rate_limited"),
        "external_matched": pipe.get("external_matched"),
        "pipeline_seconds": pipe.get("pipeline_seconds"),
        "reading": reading,
    }


def _gate(gate_id: str, ok: bool, detail: str, *, skip: bool = False) -> dict[str, Any]:
    return {
        "id": gate_id,
        "ok": True if skip else bool(ok),
        "skip": skip,
        "detail": detail,
    }


def apply_gates(
    report: dict[str, Any],
    gates: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    g = {**DEFAULT_GATES, **(gates or {})}
    ranking = report.get("ranking") or {}
    calibration = report.get("calibration") or {}
    xi = report.get("xi") or {}
    market = report.get("market") or {}
    pipeline = report.get("pipeline") or {}
    sample = int(ranking.get("sample") or calibration.get("sample") or 0)
    thin = sample < int(g["min_sample"])

    titular = (calibration.get("by_p_play") or {}).get("titular") or {}
    titular_mae = _num(titular.get("mae"))
    titular_bias = _num(titular.get("bias"))
    rho = _num(ranking.get("spearman"))
    lift = _num(ranking.get("lift"))
    gap = _num(xi.get("gap_vs_current_pct"))

    out = [
        _gate(
            "spearman",
            rho is not None and rho >= float(g["min_spearman"]),
            f"Spearman={ranking.get('spearman')} (mín {g['min_spearman']})",
            skip=thin or rho is None,
        ),
        _gate(
            "lift",
            lift is not None and lift >= float(g["min_lift"]),
            f"lift={ranking.get('lift')} (mín {g['min_lift']})",
            skip=thin or lift is None,
        ),
        _gate(
            "titular_mae",
            titular_mae is not None and titular_mae <= float(g["max_titular_mae"]),
            f"MAE titular={titular.get('mae')} (máx {g['max_titular_mae']})",
            skip=thin or titular_mae is None,
        ),
        _gate(
            "titular_bias",
            titular_bias is None or titular_bias <= float(g["max_titular_optimistic_bias"]),
            f"sesgo titular={titular.get('bias')} (máx +{g['max_titular_optimistic_bias']})",
            skip=thin or titular_bias is None,
        ),
        _gate(
            "xi_vs_current",
            gap is None or gap >= -float(g["max_xi_gap_pct"]),
            f"once vs alineado {xi.get('gap_vs_current_pct')}% (suelo -{g['max_xi_gap_pct']}%)",
            skip=xi.get("status") in ("empty", "thin", None),
        ),
        _gate(
            "market_buy_vs_avoid",
            (market.get("status") or "ok") != "fail",
            market.get("reading") or "sin mercado",
            skip=market.get("status") in ("empty", "thin", None),
        ),
        _gate(
            "pipeline",
            (pipeline.get("status") or "ok") != "fail",
            pipeline.get("reading") or "sin pipeline",
            skip=pipeline.get("status") in ("empty", None),
        ),
    ]
    return out


def _overall_status(parts: list[str], gates: list[dict[str, Any]]) -> str:
    if any(not g["ok"] and not g.get("skip") for g in gates):
        return "fail"
    if "fail" in parts:
        return "fail"
    if all(p in ("empty", "thin") for p in parts):
        return parts[0] if parts else "empty"
    if "warn" in parts:
        return "warn"
    if "ok" in parts:
        return "ok"
    return "thin"


def _global_reading(status: str, ranking: dict, calibration: dict, xi: dict) -> str:
    if status == "fail":
        return (
            "Regresión de rendimiento: el modelo o las decisiones pierden "
            "puntos respecto a la referencia."
        )
    if status in ("empty", "thin"):
        return calibration.get("reading") or ranking.get("reading") or "Muestra insuficiente."
    bits = []
    for raw in (calibration.get("reading"), ranking.get("reading"), xi.get("reading")):
        if not raw:
            continue
        text = str(raw).strip()
        if text and text[-1] not in ".!?":
            text += "."
        bits.append(text)
    return " ".join(bits)


def audit_league(
    snapshots: list[dict[str, Any]],
    *,
    latest: dict[str, Any] | None = None,
    current_jornada: Any = None,
    names: dict[str, str] | None = None,
    gates: dict[str, Any] | None = None,
    slug: str | None = None,
) -> dict[str, Any]:
    if current_jornada is None and latest:
        matchday = latest.get("matchday") if isinstance(latest.get("matchday"), dict) else {}
        current_jornada = matchday.get("jornada")
    calibration = build_calibration(
        snapshots,
        names=names,
        current_jornada=current_jornada,
    )
    rows = collect_closed_rows(snapshots, current_jornada=current_jornada)
    ranking = ranking_quality(rows)
    xi = evaluate_xi(snapshots, current_jornada=current_jornada)
    market = evaluate_market(snapshots, current_jornada=current_jornada)
    pipeline = evaluate_pipeline(latest)
    report = {
        "slug": slug,
        "calibration": calibration,
        "ranking": ranking,
        "xi": xi,
        "market": market,
        "pipeline": pipeline,
    }
    gate_rows = apply_gates(report, gates)
    status = _overall_status(
        [
            ranking.get("status") or "empty",
            calibration.get("status") or "empty",
            xi.get("status") or "empty",
            market.get("status") or "empty",
            pipeline.get("status") or "empty",
        ],
        gate_rows,
    )
    report["status"] = status
    report["gates"] = gate_rows
    report["reading"] = _global_reading(status, ranking, calibration, xi)
    return report


def slim_report(report: dict[str, Any]) -> dict[str, Any]:
    """Versión corta para `meta.performance_audit` (sin filas por jornada)."""
    xi = dict(report.get("xi") or {})
    xi.pop("per_jornada", None)
    cal = report.get("calibration") or {}
    last = cal.get("last_closed") or {}
    return {
        "status": report.get("status"),
        "reading": report.get("reading"),
        "gates_failed": [g["id"] for g in (report.get("gates") or []) if not g.get("ok") and not g.get("skip")],
        "calibration": {
            "status": cal.get("status"),
            "sample": cal.get("sample"),
            "bias": cal.get("bias"),
            "mae": cal.get("mae"),
            "by_p_play": cal.get("by_p_play"),
            "reading": cal.get("reading"),
            "last_jornada": last.get("jornada"),
        },
        "ranking": report.get("ranking"),
        "xi": xi,
        "market": report.get("market"),
        "pipeline": report.get("pipeline"),
    }


def audit_all_leagues(
    slugs: list[str] | None = None,
    *,
    gates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    targets = slugs or discover_league_slugs()
    leagues: list[dict[str, Any]] = []
    worst = "empty"
    rank = {"empty": 0, "thin": 1, "ok": 2, "warn": 3, "fail": 4}
    for slug in targets:
        latest = load_latest_payload(slug)
        snapshots = load_history_snapshots(slug)
        report = audit_league(snapshots, latest=latest, slug=slug, gates=gates)
        leagues.append(report)
        if rank.get(report.get("status") or "empty", 0) > rank.get(worst, 0):
            worst = str(report.get("status") or "empty")
    failed = [
        g["id"]
        for rep in leagues
        for g in (rep.get("gates") or [])
        if not g.get("ok") and not g.get("skip")
    ]
    return {
        "status": worst if not failed else "fail",
        "leagues": leagues,
        "failed_gates": sorted(set(failed)),
        "reading": (
            f"{len(leagues)} liga(s) auditadas; estado {worst if not failed else 'fail'}."
        ),
    }


def format_markdown(bundle: dict[str, Any]) -> str:
    lines = [
        "# Auditoría de rendimiento",
        "",
        bundle.get("reading") or "",
        "",
    ]
    for report in bundle.get("leagues") or [bundle]:
        if report.get("leagues"):
            continue
        slug = report.get("slug") or "liga"
        lines += [f"## {slug} — `{report.get('status')}`", "", report.get("reading") or "", ""]
        cal = report.get("calibration") or {}
        rank = report.get("ranking") or {}
        xi = report.get("xi") or {}
        market = report.get("market") or {}
        pipe = report.get("pipeline") or {}
        lines += [
            "| Capa | Estado | Detalle |",
            "|------|--------|---------|",
            f"| Calibración xPts | {cal.get('status')} | n={cal.get('sample')} sesgo={cal.get('bias')} MAE={cal.get('mae')} |",
            f"| Ranking | {rank.get('status')} | Spearman={rank.get('spearman') if rank.get('spearman') is not None else '—'} lift={rank.get('lift') if rank.get('lift') is not None else '—'} |",
            f"| Once | {xi.get('status')} | rec={xi.get('recommended_pts') if xi.get('recommended_pts') is not None else '—'} vs XI={xi.get('current_pts') if xi.get('current_pts') is not None else '—'} naive={xi.get('naive_price_pts') if xi.get('naive_price_pts') is not None else '—'} |",
            f"| Mercado | {market.get('status')} | buy_now={market.get('buy_now_pts') if market.get('buy_now_pts') is not None else '—'} avoid={market.get('avoid_pts') if market.get('avoid_pts') is not None else '—'} |",
            f"| Pipeline | {pipe.get('status')} | {pipe.get('reading')} |",
            "",
        ]
        failed = [g for g in (report.get("gates") or []) if not g.get("ok") and not g.get("skip")]
        skipped = [g for g in (report.get("gates") or []) if g.get("skip")]
        if failed:
            lines.append("**Umbrales rotos**")
            for g in failed:
                lines.append(f"- `{g['id']}`: {g['detail']}")
            lines.append("")
        if skipped:
            lines.append("_Umbrales omitidos por muestra insuficiente:_ " + ", ".join(f"`{g['id']}`" for g in skipped))
            lines.append("")
        if cal.get("reading"):
            lines += [f"Calibración: {cal['reading']}", ""]
        last = cal.get("last_closed") or {}
        if last.get("underestimated"):
            names = ", ".join(
                f"{r.get('name')} ({r.get('xpts')}→{r.get('real')})"
                for r in last["underestimated"][:3]
            )
            lines += [f"Infravalorados J{last.get('jornada')}: {names}", ""]
    return "\n".join(lines).rstrip() + "\n"


def attach_audit_to_payload(
    payload: dict[str, Any],
    snapshots: list[dict[str, Any]],
    *,
    slug: str | None = None,
) -> dict[str, Any]:
    """Escribe `meta.performance_audit` (slim) y devuelve el informe completo."""
    report = audit_league(snapshots, latest=payload, slug=slug)
    payload.setdefault("meta", {})["performance_audit"] = slim_report(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audita xPts, once y mercado contra el histórico de la liga."
    )
    parser.add_argument("--league", default="all", help="slug o 'all'")
    parser.add_argument("--markdown", action="store_true", help="Imprime informe Markdown")
    parser.add_argument("--json-out", default="", help="Ruta del JSON del informe")
    parser.add_argument(
        "--fail-on-gates",
        action="store_true",
        help="Exit 1 si un umbral de rendimiento se rompe",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    league = str(args.league).strip().lower()
    slugs = None if league in ("all", "*", "") else [str(args.league).strip()]
    bundle = audit_all_leagues(slugs)
    text = format_markdown(bundle)
    if args.markdown or not args.json_out:
        sys.stdout.write(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as fh:
            fh.write(text)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    status = bundle.get("status")
    log.info("Auditoría: status=%s failed=%s", status, bundle.get("failed_gates"))
    if args.fail_on_gates and status == "fail":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
