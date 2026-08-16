"""
¿Acierta el xPts? Comparación entre lo predicho y los puntos reales.

Cada snapshot diario guarda los puntos esperados de la jornada en curso y, más
tarde, los puntos que esa jornada acabó dando. Cruzando ambos se sabe si el
modelo tiene sesgo (predice de más o de menos), cuánto se equivoca de media y
en qué tramo de probabilidad de jugar falla, que es lo que permite decidir si
hay que tocar el FDR, la producción base o la titularidad, en vez de mover
pesos por intuición.

El módulo es puro: recibe la lista de snapshots ya cargados y devuelve el
informe. Así se puede probar sin tocar disco.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("model_calibration")

# Predicciones por debajo de esto no dicen nada: son suplentes declarados
MIN_XPTS_FOR_SAMPLE = 0.5
# Tramos de probabilidad de jugar donde se mide el error por separado
P_PLAY_BANDS = (
    ("titular", 0.70, 1.01),
    ("duda", 0.40, 0.70),
    ("suplente", 0.0, 0.40),
)
# Mínimo de pares predicho/real para que un informe sea publicable
MIN_SAMPLE = 8


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _stats(errors: list[float]) -> dict[str, Any]:
    """Sesgo (predicho − real) y error absoluto medio."""
    if not errors:
        return {"sample": 0, "bias": None, "mae": None}
    return {
        "sample": len(errors),
        "bias": round(sum(errors) / len(errors), 2),
        "mae": round(sum(abs(e) for e in errors) / len(errors), 2),
    }


def collect_pairs(snapshots: list[dict[str, Any]]) -> dict[Any, dict[str, dict[str, Any]]]:
    """
    `{jornada: {player_id: {xpts, p_play, real, predicted_on}}}`.

    De cada jornada se queda con la **última predicción antes del cierre**, que
    es la que el usuario tenía delante al montar el once, y con los puntos
    reales más recientes que se hayan observado.
    """
    ordered = sorted(
        (s for s in snapshots if isinstance(s, dict)),
        key=lambda s: str(s.get("date") or ""),
    )
    out: dict[Any, dict[str, dict[str, Any]]] = {}
    for snap in ordered:
        jornada = snap.get("jornada")
        if jornada is None:
            continue
        date = snap.get("date")
        status = str(snap.get("gameweek_status") or "")
        # Una predicción hecha con la jornada ya rodando no es una predicción:
        # solo se usa si nunca hubo una anterior al primer partido
        quality = 0 if status in ("ongoing", "live", "finished") else 1
        rows = out.setdefault(jornada, {})

        for pid, payload in (snap.get("xpts") or {}).items():
            if not isinstance(payload, (list, tuple)) or not payload:
                continue
            xpts = _num(payload[0])
            if xpts is None:
                continue
            row = rows.setdefault(str(pid), {})
            if quality < int(row.get("quality", -1)):
                continue
            row["quality"] = quality
            row["xpts"] = xpts
            row["p_play"] = _num(payload[1]) if len(payload) > 1 else None
            row["predicted_on"] = date

        for pid, pts in (snap.get("gw_points") or {}).items():
            real = _num(pts)
            if real is None:
                continue
            rows.setdefault(str(pid), {})["real"] = real
    return out


def build_calibration(
    snapshots: list[dict[str, Any]],
    *,
    names: dict[str, str] | None = None,
    current_jornada: Any = None,
) -> dict[str, Any]:
    """Informe de error del modelo, global y de la última jornada cerrada."""
    pairs = collect_pairs(snapshots)
    label = names or {}

    all_errors: list[float] = []
    band_errors: dict[str, list[float]] = {name: [] for name, _, _ in P_PLAY_BANDS}
    per_jornada: list[dict[str, Any]] = []

    for jornada in sorted(pairs, key=lambda j: (j is None, j)):
        if current_jornada is not None and jornada == current_jornada:
            continue  # la jornada en curso todavía no se puede juzgar
        rows = [
            (pid, r)
            for pid, r in pairs[jornada].items()
            if r.get("xpts") is not None and r.get("real") is not None
        ]
        rows = [(pid, r) for pid, r in rows if r["xpts"] >= MIN_XPTS_FOR_SAMPLE or r["real"] > 0]
        if not rows:
            continue
        errors = [r["xpts"] - r["real"] for _, r in rows]
        all_errors.extend(errors)
        for pid, r in rows:
            p_play = r.get("p_play")
            if p_play is None:
                continue
            for name, low, high in P_PLAY_BANDS:
                if low <= p_play < high:
                    band_errors[name].append(r["xpts"] - r["real"])
                    break
        per_jornada.append(
            {
                "jornada": jornada,
                **_stats(errors),
                "rows": rows,
            }
        )

    overall = _stats(all_errors)
    status = "ok" if overall["sample"] >= MIN_SAMPLE else ("thin" if overall["sample"] else "empty")

    last = per_jornada[-1] if per_jornada else None
    last_report: dict[str, Any] | None = None
    if last:
        rows = sorted(last["rows"], key=lambda kv: kv[1]["xpts"] - kv[1]["real"])

        def _brief(pid: str, r: dict[str, Any]) -> dict[str, Any]:
            return {
                "player_id": pid,
                "name": label.get(pid) or f"#{pid}",
                "xpts": round(r["xpts"], 2),
                "real": round(r["real"], 1),
                "error": round(r["xpts"] - r["real"], 2),
                "p_play": round(r["p_play"], 2) if r.get("p_play") is not None else None,
            }

        hits = sorted(last["rows"], key=lambda kv: abs(kv[1]["xpts"] - kv[1]["real"]))
        last_report = {
            "jornada": last["jornada"],
            "sample": last["sample"],
            "bias": last["bias"],
            "mae": last["mae"],
            # Predijimos poco y reventó / predijimos mucho y no apareció
            "underestimated": [_brief(pid, r) for pid, r in rows[:3]],
            "overestimated": [_brief(pid, r) for pid, r in reversed(rows[-3:])],
            "hits": [_brief(pid, r) for pid, r in hits[:3]],
        }

    return {
        "status": status,
        "jornadas_measured": [j["jornada"] for j in per_jornada],
        **overall,
        "by_p_play": {name: _stats(errs) for name, errs in band_errors.items()},
        "last_closed": last_report,
        "reading": _reading(status, overall, band_errors),
    }


def _reading(status: str, overall: dict[str, Any], bands: dict[str, list[float]]) -> str:
    """Una frase que diga qué hacer con el número, no solo cuál es."""
    if status == "empty":
        return "Aún no hay jornadas cerradas con predicción guardada."
    if status == "thin":
        return f"Solo {overall['sample']} comparaciones: ruido, no señal."
    bias = overall.get("bias") or 0.0
    mae = overall.get("mae")
    worst = max(
        ((name, _stats(errs)) for name, errs in bands.items() if errs),
        key=lambda kv: abs(kv[1].get("bias") or 0.0),
        default=None,
    )
    parts = [f"Error medio {mae} pts"]
    if bias > 0.7:
        parts.append(f"y sesgo optimista de {bias:+.1f}: el modelo promete de más")
    elif bias < -0.7:
        parts.append(f"y sesgo pesimista de {bias:+.1f}: el modelo se queda corto")
    else:
        parts.append(f"con sesgo casi nulo ({bias:+.1f})")
    if worst and abs(worst[1].get("bias") or 0.0) > 1.0:
        parts.append(f"; el fallo se concentra en el tramo '{worst[0]}'")
    return " ".join(parts)
