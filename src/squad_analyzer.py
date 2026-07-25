"""
Diagnóstico táctico y financiero de plantilla (Fantasy).

Audita:
  - Distribución del presupuesto (estrellas TOP vs banquillo inflado)
  - Estructura por líneas (GK tándem, DF/MF/FW titulares)
  - Fondo de armario / parches económicos
  - Necesidades estructurales para priorizar mercado

Salida pensada para `diagnostico_plantilla` en latest_data.json y la UI.
"""

from __future__ import annotations

from typing import Any

import config

# --- Umbrales (buenas prácticas Fantasy) ---
TOP_COUNT_MIN = 3
TOP_COUNT_MAX = 4
TOP_SHARE_MIN = 0.50
TOP_SHARE_MAX = 0.60
BENCH_SHARE_ALERT = 0.15
PATCH_MAX_PRICE = 2_000_000
PATCH_MIN_COUNT = 2
PATCH_IDEAL_COUNT = 3
DF_STARTERS_MIN = 3
MF_STARTERS_MIN = 4
FW_TOP_MIN = 2
LINEUP_STARTER = getattr(config, "LINEUP_PROB_TITULAR", 0.70)
LINEUP_REGULAR = 0.45  # juega con cierta regularidad (parches)


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _money(n: float) -> int:
    return int(round(n))


def _is_injured(p: dict[str, Any]) -> bool:
    if p.get("injury"):
        return True
    avail = (p.get("external") or {}).get("availability")
    return avail in ("injured", "suspended")


def _is_starter(p: dict[str, Any]) -> bool:
    if _is_injured(p):
        return False
    if p.get("in_lineup") is True:
        return True
    return _f(p.get("lineup_prob")) >= LINEUP_STARTER


def _is_regular(p: dict[str, Any]) -> bool:
    """Titular o rota con frecuencia (útil para parches)."""
    if _is_injured(p):
        return False
    if p.get("in_lineup") is True:
        return True
    return _f(p.get("lineup_prob")) >= LINEUP_REGULAR


def _is_bench(p: dict[str, Any]) -> bool:
    return not _is_starter(p)


def _player_value(p: dict[str, Any]) -> float:
    return max(0.0, _f(p.get("price")))


def _form_score(p: dict[str, Any]) -> float:
    for key in ("mister_avg", "form", "avg_ppg", "prior_avg"):
        v = p.get(key)
        if v is not None and _f(v) > 0:
            return _f(v)
    return 0.0


def _slim(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "position": p.get("position"),
        "team": p.get("team"),
        "team_id": p.get("team_id"),
        "price": _money(_player_value(p)),
        "lineup_prob": p.get("lineup_prob"),
        "in_lineup": p.get("in_lineup"),
        "form": p.get("form"),
        "mister_avg": p.get("mister_avg") or p.get("form"),
    }


def _advice(
    level: str,
    code: str,
    title: str,
    message: str,
    *,
    position: str | None = None,
    related: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "level": level,  # ok | suggestion | alert
        "code": code,
        "title": title,
        "message": message,
        "position": position,
        "related_player_ids": related or [],
    }


def _squad_value_fallback(squad: list[dict[str, Any]], squad_value: float | None) -> float:
    if squad_value is not None and squad_value > 0:
        return float(squad_value)
    return sum(_player_value(p) for p in squad)


# ---------------------------------------------------------------------------
# Financiero
# ---------------------------------------------------------------------------

def _analyze_finance(
    squad: list[dict[str, Any]],
    balance: float,
    squad_value: float,
) -> dict[str, Any]:
    priced = sorted(squad, key=_player_value, reverse=True)
    # TOP = los N más caros con valor > 0 (hasta TOP_COUNT_MAX)
    with_price = [p for p in priced if _player_value(p) > 0]
    top_n = min(TOP_COUNT_MAX, max(TOP_COUNT_MIN, len(with_price) // 4 or TOP_COUNT_MIN))
    top_n = min(top_n, len(with_price)) if with_price else 0
    # Preferimos evaluar exactamente 3–4 estrellas
    if len(with_price) >= TOP_COUNT_MAX:
        top_players = with_price[:TOP_COUNT_MAX]
    elif len(with_price) >= TOP_COUNT_MIN:
        top_players = with_price[: len(with_price)]
    else:
        top_players = with_price[:]

    top_ids = {str(p.get("id")) for p in top_players}
    top_value = sum(_player_value(p) for p in top_players)
    plantilla = max(squad_value, 1.0)
    top_share = top_value / plantilla

    top_ok = (
        TOP_COUNT_MIN <= len(top_players) <= TOP_COUNT_MAX
        and TOP_SHARE_MIN <= top_share <= TOP_SHARE_MAX
    )
    if len(top_players) < TOP_COUNT_MIN or top_share < TOP_SHARE_MIN:
        top_status = "critical" if len(top_players) < 2 or top_share < 0.35 else "warning"
    elif top_share > TOP_SHARE_MAX or len(top_players) > TOP_COUNT_MAX:
        top_status = "warning"
    else:
        top_status = "ok"

    # Banquillo inflado: no titulares con valor alto
    bench_heavy = [
        p
        for p in squad
        if _is_bench(p) and _player_value(p) > 0 and str(p.get("id")) not in top_ids
    ]
    # Incluir TOPs que no juegan (dinero muerto)
    bench_tops = [p for p in top_players if _is_bench(p)]
    bench_flagged = sorted(
        {str(p.get("id")): p for p in (bench_heavy + bench_tops)}.values(),
        key=_player_value,
        reverse=True,
    )
    # Solo alertar por jugadores de banquillo cuyo valor individual o conjunto sea relevante
    expensive_bench = [p for p in bench_flagged if _player_value(p) >= plantilla * 0.08]
    bench_value = sum(_player_value(p) for p in expensive_bench)
    # Si no hay ninguno ≥8%, mirar suma de todo el banquillo con precio
    if not expensive_bench:
        all_bench = [p for p in squad if _is_bench(p) and _player_value(p) > 0]
        bench_value = sum(_player_value(p) for p in all_bench)
        expensive_bench = all_bench
    bench_share = bench_value / plantilla
    bench_inflated = bench_share > BENCH_SHARE_ALERT and bench_value > 0

    # Distribución visual: TOP / titulares medios / banquillo+parches
    # Mister a menudo no trae precio del once → residual del squad_value oficial
    # se reparte hacia titulares sin precio (no inflar artificialmente las estrellas).
    starters_mid = [
        p for p in squad if _is_starter(p) and str(p.get("id")) not in top_ids
    ]
    bench_patches = [
        p for p in squad if str(p.get("id")) not in top_ids and p not in starters_mid
    ]
    v_top = min(top_value, plantilla)
    v_mid_known = sum(_player_value(p) for p in starters_mid)
    v_bench_known = sum(_player_value(p) for p in bench_patches)
    residual = max(0.0, plantilla - v_top - v_mid_known - v_bench_known)
    unpriced_starters = sum(1 for p in starters_mid if _player_value(p) <= 0)
    unpriced_bench = sum(1 for p in bench_patches if _player_value(p) <= 0)
    unpriced_n = unpriced_starters + unpriced_bench
    if residual > 0 and unpriced_n > 0:
        per = residual / unpriced_n
        v_mid = v_mid_known + per * unpriced_starters
        v_bench = v_bench_known + per * unpriced_bench
    elif residual > 0:
        # Sin huecos sin precio: residual a titulares medios (conservador)
        v_mid = v_mid_known + residual
        v_bench = v_bench_known
    else:
        v_mid, v_bench = v_mid_known, v_bench_known
    denom = max(v_top + v_mid + v_bench, 1.0)

    return {
        "valor_plantilla": _money(plantilla),
        "saldo": _money(balance),
        "valor_total_equipo": _money(plantilla + balance),
        "top_players": [_slim(p) for p in top_players],
        "top_share_pct": round(top_share * 100, 1),
        "top_check": {
            "ok": top_ok,
            "status": top_status,
            "count": len(top_players),
            "ideal_min": TOP_COUNT_MIN,
            "ideal_max": TOP_COUNT_MAX,
            "share_pct": round(top_share * 100, 1),
            "ideal_share_min_pct": int(TOP_SHARE_MIN * 100),
            "ideal_share_max_pct": int(TOP_SHARE_MAX * 100),
            "message": (
                f"{len(top_players)} estrellas concentran el {top_share * 100:.0f}% del valor de plantilla."
                if top_players
                else "No hay suficientes jugadores con precio para evaluar estrellas TOP."
            ),
        },
        "bench_inflated": {
            "ok": not bench_inflated,
            "status": "alert" if bench_inflated else "ok",
            "value": _money(bench_value),
            "share_pct": round(bench_share * 100, 1),
            "threshold_pct": int(BENCH_SHARE_ALERT * 100),
            "players": [_slim(p) for p in expensive_bench[:5]],
            "message": (
                f"Dinero estancado en el banquillo: {_money(bench_value) / 1e6:.1f} M€ "
                f"({bench_share * 100:.0f}% del valor de plantilla)."
                if bench_inflated
                else "El banquillo no concentra demasiado valor."
            ),
        },
        "budget_distribution": {
            "estrellas_top": {
                "label": "Estrellas TOP",
                "value": _money(v_top),
                "pct": round(100 * v_top / denom, 1),
            },
            "titulares_medios": {
                "label": "Titulares medios",
                "value": _money(v_mid),
                "pct": round(100 * v_mid / denom, 1),
            },
            "banquillo_parches": {
                "label": "Banquillo / parches",
                "value": _money(v_bench),
                "pct": round(100 * v_bench / denom, 1),
            },
        },
    }


# ---------------------------------------------------------------------------
# Líneas
# ---------------------------------------------------------------------------

def _analyze_gk(players: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict], list[dict]]:
    tips: list[dict] = []
    needs: list[dict] = []
    healthy = [p for p in players if not _is_injured(p)]
    starters = [p for p in healthy if _is_starter(p)]
    by_team: dict[str, list] = {}
    for p in players:
        tid = str(p.get("team_id") or p.get("team") or "")
        if tid:
            by_team.setdefault(tid, []).append(p)
    tandem_team = next((t for t, ps in by_team.items() if len(ps) >= 2), None)
    tandem = tandem_team is not None

    if len(starters) >= 1 and tandem:
        status = "ok"
        message = "Tienes tándem de porteros del mismo equipo (cobertura ante sanción)."
        tips.append(_advice("ok", "gk_tandem", "Portería cubierta", message, position="GK"))
    elif len(starters) >= 1 and len(healthy) >= 2:
        status = "warning"
        message = "Tienes 2 porteros, pero no son del mismo club (sin tándem directo)."
        tips.append(
            _advice(
                "suggestion",
                "gk_no_tandem",
                "Busca el suplente del titular",
                message + " Ideal: el segundo portero del mismo equipo.",
                position="GK",
                related=[str(starters[0].get("id"))] if starters else [],
            )
        )
        needs.append(
            {
                "need": "gk_tandem",
                "position": "GK",
                "priority": "Alta",
                "same_team_as": starters[0].get("team"),
                "same_team_id": starters[0].get("team_id"),
                "max_price": PATCH_MAX_PRICE * 2,
                "reason": "Completar tándem del portero titular",
            }
        )
    elif len(healthy) <= 1:
        status = "critical"
        message = "Solo dispones de 1 portero sano. Si es sancionado te quedas a 0."
        tips.append(_advice("alert", "gk_single", "Portería en riesgo", message, position="GK"))
        needs.append(
            {
                "need": "gk_backup",
                "position": "GK",
                "priority": "Alta",
                "max_price": None,
                "reason": "Falta portero de respaldo",
            }
        )
    else:
        status = "warning"
        message = "Portería irregular: revisa titularidad y cobertura."
        tips.append(_advice("suggestion", "gk_irregular", "Revisa portería", message, position="GK"))

    return (
        {
            "status": status,
            "count": len(players),
            "healthy": len(healthy),
            "starters": len(starters),
            "tandem": tandem,
            "tandem_team": next(
                (p.get("team") for p in players if str(p.get("team_id") or p.get("team")) == tandem_team),
                None,
            ),
            "message": message,
            "players": [_slim(p) for p in players],
        },
        tips,
        needs,
    )


def _analyze_df(players: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict], list[dict]]:
    tips: list[dict] = []
    needs: list[dict] = []
    healthy = [p for p in players if not _is_injured(p)]
    starters = [p for p in healthy if _is_starter(p)]
    # Sin rol lateral/central en Mister: priorizamos nº de titulares fijos sanos
    if len(starters) >= DF_STARTERS_MIN:
        status = "ok"
        message = (
            f"Defensa con {len(starters)} titulares fijos sanos. "
            "Prioriza laterales/carrileros al fichar (más puntos Fantasy que centrales)."
        )
        tips.append(_advice("ok", "df_ok", "Defensa estable", message, position="DF"))
    elif len(starters) >= 2:
        status = "warning"
        message = (
            f"Solo {len(starters)} defensas titulares fijos (ideal ≥{DF_STARTERS_MIN}). "
            "Prioriza laterales sobre centrales."
        )
        tips.append(_advice("suggestion", "df_thin", "Refuerza la zaga", message, position="DF"))
        needs.append(
            {
                "need": "df_starter",
                "position": "DF",
                "priority": "Alta",
                "prefer_role": "lateral",
                "max_price": None,
                "reason": "Faltan titulares fijos en defensa",
            }
        )
    else:
        status = "critical"
        message = f"Línea defensiva crítica: {len(starters)} titulares sanos."
        tips.append(_advice("alert", "df_critical", "Defensa insuficiente", message, position="DF"))
        needs.append(
            {
                "need": "df_starter",
                "position": "DF",
                "priority": "Alta",
                "prefer_role": "lateral",
                "max_price": None,
                "reason": "Carencia crítica de defensas titulares",
            }
        )

    return (
        {
            "status": status,
            "count": len(players),
            "healthy": len(healthy),
            "starters": len(starters),
            "message": message,
            "note": "Mister no distingue lateral/central; el consejo de laterales es heurístico.",
            "players": [_slim(p) for p in players],
        },
        tips,
        needs,
    )


def _analyze_mf(
    players: list[dict[str, Any]],
    *,
    points_phase: str,
) -> tuple[dict[str, Any], list[dict], list[dict]]:
    tips: list[dict] = []
    needs: list[dict] = []
    healthy = [p for p in players if not _is_injured(p)]
    starters = [p for p in healthy if _is_starter(p)]
    # En temporada activa exigimos promedio; en pretemporada basta titularidad
    if points_phase == "active":
        quality = [p for p in starters if _form_score(p) >= 4.5]
    else:
        quality = starters

    if len(quality) >= MF_STARTERS_MIN:
        status = "ok"
        message = f"Centrocampo sólido: {len(quality)} titulares fijos como motor del equipo."
        tips.append(_advice("ok", "mf_ok", "Medular equilibrada", message, position="MF"))
    elif len(quality) >= 3:
        status = "warning"
        message = (
            f"Centrocampo justo ({len(quality)}/{MF_STARTERS_MIN} titulares de nivel). "
            "Es el motor: prioriza fichajes MF titulares."
        )
        tips.append(_advice("suggestion", "mf_thin", "Refuerza el centro", message, position="MF"))
        needs.append(
            {
                "need": "mf_starter",
                "position": "MF",
                "priority": "Alta",
                "max_price": None,
                "reason": "Faltan centrocampistas titulares de nivel",
            }
        )
    else:
        status = "critical"
        message = f"Centrocampo débil: solo {len(quality)} titulares fiables."
        tips.append(_advice("alert", "mf_critical", "Motor en peligro", message, position="MF"))
        needs.append(
            {
                "need": "mf_starter",
                "position": "MF",
                "priority": "Alta",
                "max_price": None,
                "reason": "Carencia crítica en mediocampo",
            }
        )

    return (
        {
            "status": status,
            "count": len(players),
            "healthy": len(healthy),
            "starters": len(starters),
            "quality_starters": len(quality),
            "points_phase": points_phase,
            "message": message,
            "players": [_slim(p) for p in players],
        },
        tips,
        needs,
    )


def _analyze_fw(
    players: list[dict[str, Any]],
    finance: dict[str, Any],
    balance: float,
) -> tuple[dict[str, Any], list[dict], list[dict]]:
    tips: list[dict] = []
    needs: list[dict] = []
    healthy = [p for p in players if not _is_injured(p)]
    starters = [p for p in healthy if _is_starter(p)]
    top_ids = {str(p.get("id")) for p in finance.get("top_players") or []}
    # Referencias TOP: delanteros entre las estrellas o los 2 más caros de la línea
    fw_sorted = sorted(players, key=_player_value, reverse=True)
    fw_tops = [p for p in fw_sorted if str(p.get("id")) in top_ids or _player_value(p) >= 4_000_000]
    if len(fw_tops) < FW_TOP_MIN:
        # Fallback: los 2 más caros de la línea si tienen precio
        fw_tops = [p for p in fw_sorted if _player_value(p) > 0][:FW_TOP_MIN]

    suggested_formation = None
    if len(fw_tops) >= FW_TOP_MIN and len(starters) >= 2:
        status = "ok"
        message = f"Delantera con {len(fw_tops)} referencias / TOP."
        tips.append(_advice("ok", "fw_ok", "Punta de lanza OK", message, position="FW"))
    elif len(fw_tops) >= 1:
        status = "warning"
        # Si no hay caja para un 3er delantero TOP, sugerir sistema
        can_buy_third = balance >= 5_000_000
        if not can_buy_third:
            suggested_formation = "4-5-1 o 3-5-2"
            message = (
                f"Solo {len(fw_tops)} delantero(s) referencia. "
                f"Sin caja clara para un 3º TOP: valora sistema {suggested_formation}."
            )
        else:
            message = f"Solo {len(fw_tops)} delantero referencia (ideal ≥{FW_TOP_MIN})."
        tips.append(
            _advice("suggestion", "fw_thin", "Mejora la delantera", message, position="FW")
        )
        needs.append(
            {
                "need": "fw_top",
                "position": "FW",
                "priority": "Alta",
                "max_price": None,
                "min_price": 4_000_000,
                "reason": "Falta delantero referencia / TOP",
            }
        )
    else:
        status = "critical"
        suggested_formation = "4-5-1 o 3-5-2"
        message = "Sin delanteros TOP claros. Remonta con un 9 o cambia a 4-5-1 / 3-5-2."
        tips.append(_advice("alert", "fw_critical", "Delantera vacía", message, position="FW"))
        needs.append(
            {
                "need": "fw_top",
                "position": "FW",
                "priority": "Alta",
                "max_price": None,
                "min_price": 3_000_000,
                "reason": "Carencia crítica de delanteros",
            }
        )

    return (
        {
            "status": status,
            "count": len(players),
            "healthy": len(healthy),
            "starters": len(starters),
            "top_references": len(fw_tops),
            "suggested_formation": suggested_formation,
            "message": message,
            "players": [_slim(p) for p in players],
        },
        tips,
        needs,
    )


def _analyze_patches(squad: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict], list[dict]]:
    tips: list[dict] = []
    needs: list[dict] = []
    patches = [
        p
        for p in squad
        if 0 < _player_value(p) <= PATCH_MAX_PRICE and _is_regular(p) and not _is_injured(p)
    ]
    # Si precios incompletos, aceptar regulares baratos o sin precio en banquillo rotatorio
    if len(patches) < PATCH_MIN_COUNT:
        soft = [
            p
            for p in squad
            if _is_regular(p)
            and not _is_injured(p)
            and _player_value(p) <= PATCH_MAX_PRICE
            and p not in patches
        ]
        patches = patches + soft

    n = len(patches)
    if n >= PATCH_IDEAL_COUNT:
        status = "ok"
        message = f"Fondo de armario sano: {n} parches fijos de bajo coste."
        tips.append(_advice("ok", "patches_ok", "Parches listos", message))
    elif n >= PATCH_MIN_COUNT:
        status = "warning"
        message = f"Tienes {n} parches (ideal {PATCH_IDEAL_COUNT}). Amplía el fondo de armario."
        tips.append(_advice("suggestion", "patches_few", "Más parches", message))
        needs.append(
            {
                "need": "patch_cheap",
                "position": None,
                "priority": "Media",
                "max_price": PATCH_MAX_PRICE,
                "reason": "Reforzar parches económicos que jueguen",
            }
        )
    else:
        status = "critical" if n == 0 else "warning"
        message = (
            f"Solo {n} parche(s) económico(s) regular(es). "
            "Sin ellos, cualquier baja te obliga a gastar de más."
        )
        tips.append(
            _advice(
                "alert" if n == 0 else "suggestion",
                "patches_missing",
                "Fondo de armario débil",
                message,
            )
        )
        needs.append(
            {
                "need": "patch_cheap",
                "position": None,
                "priority": "Alta",
                "max_price": PATCH_MAX_PRICE,
                "reason": "Faltan parches fijos < 2M que jueguen",
            }
        )

    return (
        {
            "status": status,
            "count": n,
            "ideal_min": PATCH_MIN_COUNT,
            "ideal": PATCH_IDEAL_COUNT,
            "max_price": PATCH_MAX_PRICE,
            "players": [_slim(p) for p in patches[:6]],
            "message": message,
        },
        tips,
        needs,
    )


def _salud_score(
    finance: dict[str, Any],
    lineas: dict[str, Any],
    parches: dict[str, Any],
) -> int:
    score = 100
    weights = {"ok": 0, "warning": -12, "critical": -22, "alert": -18}
    tc = finance.get("top_check") or {}
    score += weights.get(tc.get("status", "ok"), -10)
    bi = finance.get("bench_inflated") or {}
    if not bi.get("ok", True):
        score -= 15
    for pos in ("GK", "DF", "MF", "FW"):
        score += weights.get((lineas.get(pos) or {}).get("status", "ok"), -10)
    score += weights.get(parches.get("status", "ok"), -10)
    return max(0, min(100, score))


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def analyze_squad(
    squad: list[dict[str, Any]],
    *,
    balance: float = 0.0,
    squad_value: float | None = None,
    points_phase: str = "preseason",
) -> dict[str, Any]:
    """
    Auditoría táctica + financiera completa.

    Returns:
        dict listo para `diagnostico_plantilla` en latest_data.json
    """
    plantilla = _squad_value_fallback(squad, squad_value)
    finance = _analyze_finance(squad, float(balance or 0), plantilla)

    by_pos: dict[str, list] = {"GK": [], "DF": [], "MF": [], "FW": []}
    for p in squad:
        by_pos.setdefault(p.get("position") or "MF", []).append(p)

    tips: list[dict] = []
    needs: list[dict] = []
    lineas: dict[str, Any] = {}

    gk, t, n = _analyze_gk(by_pos["GK"])
    lineas["GK"] = gk
    tips.extend(t)
    needs.extend(n)

    df, t, n = _analyze_df(by_pos["DF"])
    lineas["DF"] = df
    tips.extend(t)
    needs.extend(n)

    mf, t, n = _analyze_mf(by_pos["MF"], points_phase=points_phase)
    lineas["MF"] = mf
    tips.extend(t)
    needs.extend(n)

    fw, t, n = _analyze_fw(by_pos["FW"], finance, float(balance or 0))
    lineas["FW"] = fw
    tips.extend(t)
    needs.extend(n)

    # Tips financieros
    tc = finance["top_check"]
    if tc["status"] == "ok":
        tips.insert(
            0,
            _advice(
                "ok",
                "top_balance",
                "Estrellas bien dimensionadas",
                tc["message"] + " (rango sano: 3–4 jugadores ≈ 50–60% del valor).",
            ),
        )
    elif tc["status"] != "ok":
        tips.insert(
            0,
            _advice(
                "suggestion" if tc["status"] == "warning" else "alert",
                "top_imbalance",
                "Reequilibra las estrellas",
                tc["message"] + " Ideal: 3–4 TOP con el 50–60% del valor de plantilla.",
                related=[str(p.get("id")) for p in finance.get("top_players") or []],
            ),
        )

    bi = finance["bench_inflated"]
    if not bi.get("ok", True):
        names = ", ".join(p["name"] for p in (bi.get("players") or [])[:2])
        tips.append(
            _advice(
                "alert",
                "bench_inflated",
                "Dinero en el banquillo",
                bi["message"]
                + (f" Revisa: {names}." if names else "")
                + " Vende o mueve ese valor a una carencia.",
                related=[str(p.get("id")) for p in (bi.get("players") or [])],
            )
        )

    parches, t, n = _analyze_patches(squad)
    tips.extend(t)
    needs.extend(n)

    # Orden consejos: alert → suggestion → ok
    order = {"alert": 0, "suggestion": 1, "ok": 2}
    tips.sort(key=lambda x: order.get(x.get("level"), 9))

    return {
        "financiero": finance,
        "lineas": lineas,
        "parches": parches,
        "consejos": tips,
        "structural_needs": needs,
        "salud_score": _salud_score(finance, lineas, parches),
        "points_phase": points_phase,
    }


def merge_structural_into_diagnosis(
    diagnosis: dict[str, Any],
    diagnostico: dict[str, Any],
) -> dict[str, Any]:
    """
    Eleva `by_position.status` cuando el análisis estructural detecta
    critical/warning, para que `fills_need` del mercado lo refleje.
    """
    by_pos = dict(diagnosis.get("by_position") or {})
    alerts = list(diagnosis.get("alerts") or [])
    lineas = diagnostico.get("lineas") or {}
    rank = {"ok": 0, "warning": 1, "critical": 2}

    for pos, info in lineas.items():
        cur = by_pos.get(pos) or {
            "count": 0,
            "healthy": 0,
            "starters": 0,
            "injured": 0,
            "status": "ok",
            "players": [],
        }
        new_status = info.get("status") or "ok"
        if rank.get(new_status, 0) > rank.get(cur.get("status"), 0):
            cur = {**cur, "status": new_status}
        cur["structural_message"] = info.get("message")
        by_pos[pos] = cur
        if new_status in ("warning", "critical"):
            level = "critical" if new_status == "critical" else "warning"
            msg = info.get("message")
            if msg and not any(a.get("message") == msg for a in alerts):
                alerts.append(
                    {
                        "level": level,
                        "position": pos,
                        "source": "structural",
                        "message": msg,
                    }
                )

    # Parches: no es posición, pero añadimos alerta global
    parches = diagnostico.get("parches") or {}
    if parches.get("status") in ("warning", "critical"):
        alerts.append(
            {
                "level": "warning" if parches["status"] == "warning" else "critical",
                "position": None,
                "source": "structural",
                "message": parches.get("message") or "Faltan parches económicos.",
            }
        )

    return {**diagnosis, "alerts": alerts, "by_position": by_pos}


def structural_market_boost(
    player: dict[str, Any],
    needs: list[dict[str, Any]],
) -> tuple[float, bool, str | None]:
    """
    Bonus de score + flag fills_structural + etiqueta corta si el jugador
    del mercado encaja en una necesidad estructural.
    """
    if not needs:
        return 0.0, False, None

    pos = player.get("position")
    price = _player_value(player)
    team = (player.get("team") or "").lower()
    team_id = str(player.get("team_id") or "")
    best = 0.0
    label = None
    matched = False

    for need in needs:
        ntype = need.get("need")
        npos = need.get("position")
        bonus = 0.0
        this_label = None

        if ntype in ("gk_backup", "df_starter", "mf_starter", "fw_top") and npos and pos != npos:
            continue
        if ntype == "gk_tandem" and pos != "GK":
            continue

        if ntype == "patch_cheap":
            max_p = need.get("max_price") or PATCH_MAX_PRICE
            if price <= 0 or price > max_p:
                continue
            # Parche: barato; aún mejor si parece titular/regular
            lp = _f(player.get("lineup_prob"))
            bonus = 22.0
            if lp >= LINEUP_REGULAR:
                bonus += 10.0
            this_label = "Parche estructural"
        elif ntype == "gk_tandem":
            want_team = (need.get("same_team_as") or "").lower()
            want_id = str(need.get("same_team_id") or "")
            if want_id and team_id and team_id == want_id:
                bonus = 35.0
                this_label = "Tándem portero"
            elif want_team and want_team in team:
                bonus = 30.0
                this_label = "Tándem portero"
            else:
                continue
        elif ntype == "gk_backup" and pos == "GK":
            bonus = 28.0
            this_label = "Cubre portería"
        elif ntype == "df_starter" and pos == "DF":
            bonus = 25.0
            this_label = "Refuerzo defensa"
        elif ntype == "mf_starter" and pos == "MF":
            bonus = 25.0
            this_label = "Motor mediocampo"
        elif ntype == "fw_top" and pos == "FW":
            min_p = need.get("min_price") or 0
            if price and price < min_p * 0.6:
                # Demasiado barato para ser "referencia"
                bonus = 12.0
                this_label = "Opción delantera"
            else:
                bonus = 30.0
                this_label = "Delantero referencia"
        else:
            continue

        if need.get("priority") == "Alta":
            bonus += 5.0
        if bonus > best:
            best = bonus
            label = this_label
            matched = True

    return best, matched, label
