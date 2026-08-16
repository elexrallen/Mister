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
    "ventana_compra": "Momento de fichar: los precios aún no llevan la prima de la víspera.",
    "post_jornada": "Balance de la jornada, ventas de los que ya no cuentan y objetivos para el siguiente ciclo.",
    "pretemporada": "Construir plantilla: titularidad y producción histórica mandan sobre el precio.",
    "jornada_en_curso": "Jornada disputándose: solo cambios en vivo si la liga los permite.",
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
) -> dict[str, Any]:
    """Fase del día + checklist accionable, con el porqué de cada punto."""
    md = matchday or {}
    xi = recommended_xi or {}
    plan = action_plan or []
    rules = league_rules or {}
    diag = diagnostico or {}
    balance = _f((me or {}).get("balance")) or 0.0

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
    ) -> None:
        checklist.append(
            {
                "id": key,
                "title": title,
                "detail": detail,
                "priority": priority,
                "status": status,
            }
        )

    buys = [a for a in plan if a.get("action") in ("buy_now", "clause_bid")]
    sells = [a for a in plan if a.get("action") == "sell"]
    avoid = [a for a in plan if a.get("action") == "avoid"]

    # --- Once y capitán: lo único que puntúa ---
    summary = xi.get("summary") or {}
    if not summary.get("complete") and phase not in ("pretemporada",):
        add(
            "xi_incompleto",
            "Once incompleto",
            f"Solo {summary.get('xi_count', 0)} de {summary.get('xi_target', 11)} huecos cubiertos "
            "con jugadores disponibles: cada hueco es un cero seguro.",
            priority="Alta",
        )
        warnings.append("El once recomendado no llega a 11 jugadores alineables.")

    doubts = int((summary.get("signals") or {}).get("doubt") or 0)
    if doubts and phase in ("confirmacion", "visperas", "dia_partido"):
        add(
            "xi_dudas",
            f"{doubts} duda(s) en el once",
            "Revisa las previas antes del cierre y sustituye a quien no aparezca confirmado.",
            priority="Alta" if phase != "confirmacion" else "Media",
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
        elif str(current_cap or "") == str(captain.get("player_id") or ""):
            add(
                "capitan_ok",
                f"Capitán: {captain.get('name')}",
                captain.get("why") or "",
                priority="Baja",
                status="done",
            )
        else:
            add(
                "capitan",
                f"Poner capitán a {captain.get('name')}",
                captain.get("why") or "",
                priority="Alta" if phase in ("visperas", "dia_partido") else "Media",
            )

    # --- Dinero: en negativo no se puntúa ---
    if balance < 0 and phase in ("confirmacion", "visperas", "dia_partido"):
        add(
            "saldo_negativo",
            "Saldo negativo antes de la jornada",
            f"Tienes {balance:,.0f} €. En muchas ligas Mister un saldo negativo al arrancar "
            "la jornada anula los puntos: vende o cancela pujas.",
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
                f"{names}. Es la parte del ciclo donde el precio aún no lleva prima de víspera.",
                priority="Alta",
            )
        if sells:
            names = ", ".join(str(a.get("name")) for a in sells[:3])
            add(
                "listar_ventas",
                f"Listar {len(sells)} venta(s)",
                f"{names}. El dinero de una venta tarda un ciclo en estar disponible: "
                "listar hoy es poder fichar pasado mañana.",
                priority="Media",
            )
    elif phase == "confirmacion":
        if buys:
            add(
                "fichar_ultima_llamada",
                "Última llamada para fichar",
                f"{len(buys)} objetivo(s) en cola. A partir de aquí el mercado sube por las previas.",
                priority="Media",
            )
        add(
            "revisar_previas",
            "Cruzar previas con tu plantilla",
            "Las alineaciones probables de Mister y FF ya son fiables: descarta a los que no aparecen.",
            priority="Alta",
        )
    elif phase in ("visperas", "dia_partido"):
        if buys:
            add(
                "no_fichar",
                "No fichar salvo urgencia",
                f"{len(buys)} objetivo(s) siguen en cola, pero a estas horas se paga prima. "
                "Solo si tapa un hueco del once.",
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
        add(
            "evitar",
            f"{len(avoid)} jugador(es) marcados como evitar",
            "Lesión, sanción o alerta de titularidad: no los fiches aunque estén baratos.",
            priority="Baja",
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
        "focus": PHASE_FOCUS.get(phase, ""),
        "jornada": md.get("jornada"),
        "hours_to_jornada": round(hours, 1) if hours is not None else None,
        "countdown_label": _fmt_hours(hours),
        "next_kickoff": next_kickoff or md.get("first_match"),
        "gameweek_status": md.get("gameweek_status"),
        "checklist": checklist,
        "warnings": warnings,
        "counts": {
            "buys": len(buys),
            "sells": len(sells),
            "avoid": len(avoid),
            "todo": sum(1 for c in checklist if c.get("status") != "done"),
        },
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
                "related_player_ids": [],
            }
        )
    return out
