"""
Doctrina diaria: en qué fase del ciclo estamos y qué toca hacer hoy.

El ciclo de una jornada en Mister siempre es el mismo:

    jornada cerrada → mercado abierto → salen las previas → víspera → partidos

Cada fase premia acciones distintas. Comprar el día antes del cierre paga la
prima del pánico; ajustar el once cuando ya ha empezado el partido no puntúa.
Este módulo traduce `hours_to_jornada`, el estado del mercado y el diagnóstico
en una lista corta de cosas que hacer hoy.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("daily_playbook")

# Cortes en horas hasta el primer partido de la jornada
HOURS_MATCHDAY = 6.0
HOURS_EVE = 24.0
HOURS_CONFIRMATION = 48.0
HOURS_BUY_WINDOW = 96.0

PHASE_LABELS = {
    "dia_partido": "Día de partido",
    "visperas": "Víspera de jornada",
    "confirmacion": "Confirmación de alineaciones",
    "ventana_compra": "Ventana de compra",
    "post_jornada": "Después de la jornada",
    "pretemporada": "Pretemporada",
    "jornada_en_curso": "Jornada en curso",
}

PHASE_FOCUS = {
    "dia_partido": "El once ya casi no se toca: cierra capitán y suplencias antes de cada kickoff.",
    "visperas": "Cierra el once y asegura saldo positivo: en negativo no se puntúa.",
    "confirmacion": "Salen las previas. Confirma titularidades y corrige el once antes de que suban los precios.",
    "ventana_compra": "Gasta en el mejor 15 ahora. Liquidez = jugadores listados, no caja congelada.",
    "post_jornada": "Tras el cobro de la jornada, recompón el 15 y deja listados a los débiles.",
    "pretemporada": "Monta el mejor 15 posible con el mercado de hoy; lista débiles para el siguiente ciclo.",
    "jornada_en_curso": "Jornada disputándose: solo cambios en vivo si la liga los permite.",
}

PHASE_FOCUS_FIXED = {
    "ventana_compra": "Si hay upgrade en el mercado, vende al VM y ficha al momento. No vendas para dejar caja.",
    "post_jornada": "Recompón el 15 con el mercado de ahora. Vende solo si el recambio está listado hoy.",
    "pretemporada": "Monta el mejor 15 con el mercado de hoy. No vendas a quien sube de VM si no hay recambio.",
}


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_phase(
    *,
    hours_to_jornada: float | None,
    matchday: dict[str, Any] | None = None,
    competition_phase: str | None = None,
) -> str:
    """Fase del día a partir del reloj real de Mister."""
    md = matchday or {}
    if (competition_phase or "") == "preseason":
        return "pretemporada"

    hours = _f(hours_to_jornada)
    if md.get("is_live") and hours is not None and hours <= 0:
        return "jornada_en_curso"
    if hours is None:
        return "post_jornada" if md.get("gameweek_status") == "finished" else "ventana_compra"
    if hours <= HOURS_MATCHDAY:
        return "dia_partido"
    if hours <= HOURS_EVE:
        return "visperas"
    if hours <= HOURS_CONFIRMATION:
        return "confirmacion"
    if hours <= HOURS_BUY_WINDOW:
        return "ventana_compra"
    return "post_jornada"


def _next_kickoff(matchday: dict[str, Any], *, now_ts: float | None = None) -> str | None:
    """Primer partido aún no disputado de la jornada (por timestamp real)."""
    now = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
    best: tuple[float, str] | None = None
    for fx in matchday.get("fixtures") or []:
        ts = _f(fx.get("kickoff_ts"))
        kickoff = fx.get("kickoff")
        if ts is None or not kickoff or ts <= now:
            continue
        if best is None or ts < best[0]:
            best = (ts, str(kickoff))
    return best[1] if best else None


def _fmt_hours(hours: float | None) -> str:
    if hours is None:
        return "sin fecha fiable"
    if hours < 1:
        return "menos de 1 h"
    if hours < 48:
        return f"{hours:.0f} h"
    return f"{hours / 24:.1f} días"


def build_daily_playbook(
    *,
    hours_to_jornada: float | None,
    matchday: dict[str, Any] | None = None,
    competition_phase: str | None = None,
    action_plan: list[dict[str, Any]] | None = None,
    recommended_xi: dict[str, Any] | None = None,
    diagnostico: dict[str, Any] | None = None,
    me: dict[str, Any] | None = None,
    league_rules: dict[str, Any] | None = None,
    calibration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fase del día + checklist accionable, con el porqué de cada punto."""
    md = matchday or {}
    xi = recommended_xi or {}
    plan = action_plan or []
    rules = league_rules or {}
    diag = diagnostico or {}
    balance = _f((me or {}).get("balance")) or 0.0
    bootstrap = diag.get("bootstrap_xi") or {}
    market_cycle = diag.get("market_cycle") or {}
    fixed = str(rules.get("market_mode") or "").strip().lower() == "fixed"

    phase = resolve_phase(
        hours_to_jornada=hours_to_jornada,
        matchday=md,
        competition_phase=competition_phase,
    )
    hours = _f(hours_to_jornada)

    checklist: list[dict[str, Any]] = []
    warnings: list[str] = []

    def add(
        key: str,
        title: str,
        detail: str,
        *,
        priority: str = "Media",
        status: str = "todo",
        related: list[Any] | None = None,
    ) -> None:
        checklist.append(
            {
                "id": key,
                "title": title,
                "detail": detail,
                "priority": priority,
                "status": status,
                "related_player_ids": [str(pid) for pid in (related or []) if pid],
            }
        )

    buys = [a for a in plan if a.get("action") in ("buy_now", "clause_bid")]
    sells = [a for a in plan if a.get("action") == "sell"]
    avoid = [a for a in plan if a.get("action") == "avoid"]

    # --- Once y capitán: lo único que puntúa ---
    summary = xi.get("summary") or {}
    if bootstrap.get("active"):
        gaps = bootstrap.get("position_gaps") or {}
        gap_bits = [
            f"{pos}×{int(n)}"
            for pos, n in gaps.items()
            if int(n or 0) > 0
        ]
        gap_txt = ", ".join(gap_bits) if gap_bits else "varias líneas"
        mc_h = market_cycle.get("hours_to_end")
        mc_cycles = market_cycle.get("cycles_left_before_gw")
        cycle_bit = (
            f"Cierra el mercado en {_fmt_hours(float(mc_h))}"
            if mc_h is not None
            else "revisa el mercado de hoy"
        )
        cycles_bit = (
            f" · ~{mc_cycles} ciclo(s) de mercado antes de la jornada"
            if mc_cycles is not None
            else ""
        )
        add(
            "bootstrap_xi",
            "Prioridad: completar el once",
            f"Faltan {bootstrap.get('slots_short', 0)} jugador(es) para un once legal "
            f"({summary.get('xi_count', 0)}/{summary.get('xi_target', 11)}). "
            f"Huecos: {gap_txt}. {cycle_bit}{cycles_bit}. "
            "Ficha titulares del mercado actual. No congeles caja para un crack que no está listado.",
            priority="Alta",
        )
        warnings.append(
            "Modo bootstrap: completar once con el mercado de hoy."
            if fixed
            else "Modo bootstrap: completar once con el mercado de hoy; liquidez = listados."
        )
    elif not summary.get("complete") and phase not in ("pretemporada",):
        add(
            "xi_incompleto",
            "Once incompleto",
            f"Solo {summary.get('xi_count', 0)} de {summary.get('xi_target', 11)} huecos cubiertos "
            "con jugadores disponibles: cada hueco es un cero seguro.",
            priority="Alta",
        )
        warnings.append("El once recomendado no llega a 11 jugadores alineables.")

    risky = [r for r in (xi.get("risky_slots") or []) if isinstance(r, dict)]
    if risky and phase != "post_jornada":
        detail = "; ".join(
            f"{r.get('name')} ({r.get('position')}): {r.get('reason')}" for r in risky[:3]
        )
        switch = xi.get("formation_switch") or {}
        if switch.get("formation"):
            detail = f"{detail}. {switch.get('why')}"
        add(
            "xi_ceros",
            f"{len(risky)} hueco(s) del once con cero probable",
            f"{detail}. Un jugador que no sale son 0 puntos: pesa más que cualquier fichaje.",
            priority="Alta",
            related=[r.get("player_id") for r in risky],
        )
        warnings.append(
            f"{len(risky)} titular(es) del once recomendado tienen riesgo alto de no jugar."
        )

    doubts_rows = [
        r
        for r in (xi.get("xi") or [])
        if isinstance(r, dict) and r.get("signal") == "doubt" and not r.get("slot_risk")
    ]
    if doubts_rows and phase in ("confirmacion", "visperas", "dia_partido"):
        names = ", ".join(str(r.get("name")) for r in doubts_rows[:3])
        add(
            "xi_dudas",
            f"{len(doubts_rows)} duda(s) en el once",
            f"{names}. Revisa las previas antes del cierre y sustituye a quien no aparezca confirmado.",
            priority="Alta" if phase != "confirmacion" else "Media",
            related=[r.get("player_id") for r in doubts_rows],
        )

    ugly = [
        r
        for r in (xi.get("xi") or [])
        if isinstance(r, dict) and (_f(r.get("fdr")) or 0.0) >= 4.2 and not r.get("slot_risk")
    ]
    if ugly and phase in ("confirmacion", "visperas", "dia_partido"):
        names = ", ".join(
            f"{r.get('name')} vs {r.get('opponent_name') or 'su rival'}" for r in ugly[:3]
        )
        add(
            "xi_partido_feo",
            f"{len(ugly)} titular(es) con partido muy exigente",
            f"{names}. Juegan, pero el rival les recorta lo esperado: si tienes banquillo "
            "con partido más amable, es un cambio barato.",
            priority="Media",
            related=[r.get("player_id") for r in ugly],
        )

    captain = xi.get("captain")
    if xi.get("captain_enabled"):
        current_cap = (xi.get("current") or {}).get("captain_id")
        if not captain:
            add(
                "capitan_sin_datos",
                "Capitán sin recomendación",
                "No hay puntos esperados suficientes para elegir capitán con criterio.",
                priority="Media",
            )
        else:
            alt = captain.get("alternative") or {}
            detail = captain.get("why") or ""
            if alt.get("name"):
                detail = f"{detail}. Alternativa: {alt.get('name')} (+{alt.get('expected_gain')})"
            related = [captain.get("player_id"), alt.get("player_id")]
            if str(current_cap or "") == str(captain.get("player_id") or ""):
                add(
                    "capitan_ok",
                    f"Capitán: {captain.get('name')}",
                    detail,
                    priority="Baja",
                    status="done",
                    related=related,
                )
            else:
                add(
                    "capitan",
                    f"Poner capitán a {captain.get('name')}",
                    detail,
                    priority="Alta" if phase in ("visperas", "dia_partido") else "Media",
                    related=related,
                )

    # --- Plantilla 15: gastar ahora ---
    economy = rules.get("economy") if isinstance(rules.get("economy"), dict) else {}
    if not bootstrap.get("active") and phase in ("ventana_compra", "post_jornada", "pretemporada"):
        add(
            "gastar_15",
            "Gasta en el 15 ahora",
            (
                "Si hay upgrade en el mercado, vende al VM y ficha al momento. "
                "No vendas para dejar caja: si el VM sube, mañana vale más."
                if fixed
                else (
                    "El presupuesto se usa para montar ya el mejor 15. La liquidez para un upgrade "
                    "mañana son los débiles listados (oferta CPU al siguiente ciclo), no millones parados."
                )
            ),
            priority="Alta",
        )
    if economy.get("gw_cash_bonus") and phase not in ("jornada_en_curso",):
        expected = economy.get("expected_gw_cash") or 0
        src = economy.get("source") or "rewards"
        extra = (
            f"Estimado ~{expected:,.0f} € ({src}). "
            if expected
            else "El importe exacto se confirma al cierre. "
        )
        add(
            "bonus_jornada",
            "Ingreso de jornada (después del pitido)",
            f"{extra}No es saldo de hoy{' para pujar' if not fixed else ''}: alimenta el 15 de la siguiente ventana.",
            priority="Baja",
        )
    elif economy.get("no_gw_cash_bonus") and economy.get("credit_prizes"):
        add(
            "bonus_creditos",
            "Premios en créditos, no caja Mister",
            "El ranking de jornada paga créditos/tienda. No cuenta como dinero de plantilla.",
            priority="Baja",
        )

    # --- Dinero: en negativo no se puntúa ---
    if balance < 0 and phase in ("confirmacion", "visperas", "dia_partido"):
        add(
            "saldo_negativo",
            "Saldo negativo antes de la jornada",
            f"Tienes {balance:,.0f} €. En muchas ligas Mister un saldo negativo al arrancar "
            "la jornada anula los puntos: "
            + ("vende o cancela fichajes pendientes." if fixed else "vende o cancela pujas."),
            priority="Alta",
        )
        warnings.append("Saldo negativo con la jornada encima.")

    # --- Acciones de mercado según fase ---
    if phase in ("ventana_compra", "post_jornada", "pretemporada"):
        if buys:
            names = ", ".join(str(a.get("name")) for a in buys[:3])
            add(
                "fichar",
                f"{len(buys)} fichaje(s) en cola",
                f"{names}. Es la parte del ciclo donde el precio aún no lleva prima de víspera. "
                + (
                    "Si el recambio está en mercado, vende y ficha al momento."
                    if fixed
                    else "Si el swap cierra (caja + VM del listado), ficha."
                ),
                priority="Alta",
                related=[a.get("player_id") or a.get("id") for a in buys],
            )
        if sells:
            names = ", ".join(str(a.get("name")) for a in sells[:3])
            add(
                "listar_ventas",
                f"Listar {len(sells)} venta(s)",
                f"{names}. "
                + (
                    "Vende ahora solo para fichar el recambio que está en el mercado."
                    if fixed
                    else (
                        "Liquidez = jugadores listados, no reserva: la CPU ofertea al siguiente "
                        "ciclo y así puedes vender y pujar si sale el upgrade."
                    )
                ),
                priority="Media",
                related=[a.get("player_id") or a.get("id") for a in sells],
            )
    elif phase == "confirmacion":
        if buys:
            names = ", ".join(str(a.get("name")) for a in buys[:3])
            add(
                "fichar_ultima_llamada",
                "Última llamada para fichar",
                f"{names}. A partir de aquí el mercado sube por las previas.",
                priority="Media",
                related=[a.get("player_id") or a.get("id") for a in buys],
            )
        add(
            "revisar_previas",
            "Cruzar previas con tu plantilla",
            "Las alineaciones probables de Mister y FF ya son fiables: descarta a los que no aparecen.",
            priority="Alta",
        )
    elif phase in ("visperas", "dia_partido"):
        if buys:
            names = ", ".join(str(a.get("name")) for a in buys[:3])
            add(
                "fichar_vispera",
                f"{len(buys)} fichaje(s) en cola",
                f"{names}. A estas horas se paga prima: ficha si cierra el 15 o tapa un "
                "hueco del once, no sobrepujes por pánico.",
                priority="Media",
                related=[a.get("player_id") or a.get("id") for a in buys],
            )
        else:
            add(
                "no_fichar",
                "No fichar salvo urgencia",
                "La cola no pide compras. Cierra el once; no entres al mercado por pánico.",
                priority="Baja",
            )
        add(
            "cerrar_once",
            "Cerrar el once",
            "Cada jugador que no juega es un cero. Confirma titulares y banquillo.",
            priority="Alta",
        )
    elif phase == "jornada_en_curso":
        if rules.get("live_changes"):
            add(
                "cambios_vivo",
                "Cambios en vivo disponibles",
                "La liga permite cambios durante la jornada: aprovecha para cubrir a quien no salió.",
                priority="Media",
            )
        else:
            add(
                "sin_cambios",
                "Jornada bloqueada",
                "No hay cambios en vivo en esta liga: toca esperar al cierre.",
                priority="Baja",
                status="done",
            )

    if avoid and phase != "jornada_en_curso":
        names = ", ".join(str(a.get("name")) for a in avoid[:3])
        add(
            "evitar",
            f"{len(avoid)} jugador(es) marcados como evitar",
            f"{names}. Lesión, sanción o alerta de titularidad: no los fiches aunque estén baratos.",
            priority="Baja",
            related=[a.get("player_id") or a.get("id") for a in avoid],
        )

    # --- Balance del ciclo cerrado: ¿acertó el modelo? ---
    last_closed = (calibration or {}).get("last_closed") or {}
    if last_closed and phase in ("post_jornada", "ventana_compra"):
        hits = last_closed.get("hits") or []
        misses = (last_closed.get("overestimated") or []) + (
            last_closed.get("underestimated") or []
        )
        misses.sort(key=lambda r: -abs(_f(r.get("error")) or 0.0))
        detail = (calibration or {}).get("reading") or ""
        if misses:
            worst = ", ".join(
                f"{m.get('name')} ({m.get('xpts')} esperados vs {m.get('real')} reales)"
                for m in misses[:3]
            )
            detail = f"{detail}. Los mayores desvíos: {worst}"
        add(
            "balance_jornada",
            f"Balance J{last_closed.get('jornada')}: error medio {last_closed.get('mae')} pts",
            detail,
            priority="Baja",
            status="done",
            related=[r.get("player_id") for r in (misses[:3] + hits[:3])],
        )

    # --- Estructura de plantilla ---
    needs = diag.get("structural_needs") or []
    if needs and phase in ("ventana_compra", "post_jornada", "pretemporada"):
        first = needs[0] if isinstance(needs[0], dict) else {}
        add(
            "carencia",
            f"Carencia estructural: {first.get('position') or '?'}",
            f"{first.get('need') or 'refuerzo'} pendiente. Resolverla ahora sale más barato "
            "que hacerlo con la jornada encima.",
            priority=str(first.get("priority") or "Media"),
        )

    order = {"Alta": 0, "Media": 1, "Baja": 2}
    checklist.sort(key=lambda c: (order.get(str(c.get("priority")), 3), c.get("status") == "done"))

    next_kickoff = _next_kickoff(md)

    return {
        "phase": phase,
        "phase_label": PHASE_LABELS.get(phase, phase),
        "focus": (PHASE_FOCUS_FIXED.get(phase) or PHASE_FOCUS.get(phase, ""))
        if fixed
        else PHASE_FOCUS.get(phase, ""),
        "jornada": md.get("jornada"),
        "hours_to_jornada": round(hours, 1) if hours is not None else None,
        "countdown_label": _fmt_hours(hours),
        "next_kickoff": next_kickoff or md.get("first_match"),
        "gameweek_status": md.get("gameweek_status"),
        "checklist": checklist,
        "warnings": warnings,
        "model_error": (
            {
                "status": (calibration or {}).get("status"),
                "bias": (calibration or {}).get("bias"),
                "mae": (calibration or {}).get("mae"),
                "sample": (calibration or {}).get("sample"),
                "reading": (calibration or {}).get("reading"),
            }
            if calibration
            else None
        ),
        "counts": {
            "buys": len(buys),
            "sells": len(sells),
            "avoid": len(avoid),
            "todo": sum(1 for c in checklist if c.get("status") != "done"),
        },
        "bootstrap_xi": bootstrap if bootstrap.get("active") else None,
        "market_cycle": market_cycle or None,
    }


def playbook_to_recommendations(playbook: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Checklist → array `recommendations` del payload (formato de las notas de la PWA).
    """
    out: list[dict[str, Any]] = []
    phase_label = playbook.get("phase_label") or ""
    for item in playbook.get("checklist") or []:
        out.append(
            {
                "type": "playbook",
                "phase": playbook.get("phase"),
                "priority": item.get("priority") or "Media",
                "title": item.get("title"),
                "reason": item.get("detail"),
                "context": f"{phase_label} · {playbook.get('countdown_label')}",
                "status": item.get("status") or "todo",
                "suggested_action": None,
                "related_player_ids": item.get("related_player_ids") or [],
            }
        )
    return out
