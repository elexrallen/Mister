"""
Acciones competitivas: ventas situacionales, presupuesto/riesgo,
y objetivos en plantillas rivales (cláusulas).
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import config
from league_rules import captain_multiplier_for_price
from scrapers.ff_points import THIN_APPS, resolve_avg_scale, scale_threshold
from squad_analyzer import (
    comparable_ff_signal,
    lacks_comparable_sample,
    quality_for_compare,
)

# Día antes de jornada: no endeudarse (Mister: saldo negativo → no puntúa).
SOLVENCY_STRICT_HOURS = 48
# Margen D-1: las ventas deben cobrar antes de (jornada - 24h).
SOLVENCY_D1_BUFFER_HOURS = 24


def _money(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def mister_bid_cap(balance: float, max_debt: float | None = None) -> float:
    """Techo de puja Mister: maxDebt si existe; si no, saldo actual."""
    bal = float(balance or 0)
    if max_debt is None:
        return max(0.0, bal)
    try:
        md = float(max_debt)
    except (TypeError, ValueError):
        return max(0.0, bal)
    return md


def liquidity_balance(balance: float, balance_future: float | None = None) -> float:
    """Mejor estimación de caja post-pujas pendientes."""
    if balance_future is not None:
        try:
            return float(balance_future)
        except (TypeError, ValueError):
            pass
    return float(balance or 0)


def _parse_kickoff_hours(raw: Any, *, now: datetime | None = None) -> float | None:
    """Horas hasta un kickoff ISO-ish (p.ej. 2026-08-15T19:30)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", s) and len(s) == 16:
            dt = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return (dt - base).total_seconds() / 3600.0


def resolve_hours_to_jornada(
    *,
    hours_to_jornada: float | None = None,
    days_to_kickoff: float | int | None = None,
    matchday: dict[str, Any] | None = None,
    now: datetime | None = None,
    season_start: str | None = None,
) -> float | None:
    """
    Horas hasta el próximo cierre/jornada.
    Preferencia: hours explícitas → kickoffs FF matchday (oficiales) → days_to_kickoff (J1).
    None = fecha no fiable.
    """
    if hours_to_jornada is not None:
        try:
            return float(hours_to_jornada)
        except (TypeError, ValueError):
            pass

    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)

    # Floor: no usar amistosos / kickoffs anteriores al inicio de temporada
    start_floor: datetime | None = None
    start_raw = (season_start or "").strip()
    if not start_raw and days_to_kickoff is not None:
        try:
            d_chk = float(days_to_kickoff)
        except (TypeError, ValueError):
            d_chk = None
        else:
            if d_chk > 0:
                # Derivar floor desde days_to_kickoff (medianoche UTC del J1)
                start_floor = (base + timedelta(days=int(d_chk))).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
    if start_raw:
        try:
            y, m, d = (int(x) for x in start_raw.split("-")[:3])
            start_floor = datetime(y, m, d, tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass

    earliest: float | None = None
    for fx in (matchday or {}).get("fixtures") or []:
        h = _parse_kickoff_hours((fx or {}).get("kickoff"), now=base)
        if h is None or h < 0:
            continue
        if start_floor is not None:
            raw = str((fx or {}).get("kickoff") or "").strip().replace("Z", "+00:00")
            try:
                if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", raw) and len(raw) == 16:
                    kdt = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
                else:
                    kdt = datetime.fromisoformat(raw)
                    if kdt.tzinfo is None:
                        kdt = kdt.replace(tzinfo=timezone.utc)
                if kdt < start_floor:
                    continue
            except (TypeError, ValueError):
                pass
        if earliest is None or h < earliest:
            earliest = h
    if earliest is not None:
        return earliest

    if days_to_kickoff is not None:
        try:
            d = float(days_to_kickoff)
        except (TypeError, ValueError):
            return None
        # Solo fiable como J1 futura; si ya empezó la temporada, sin FF → desconocido
        if d > 0:
            return d * 24.0
        return None
    return None


def solvency_strict_window(hours_to_jornada: float | None) -> bool:
    """True si no debemos endeudarnos (≤48h o sin fecha fiable)."""
    if hours_to_jornada is None:
        return True
    return float(hours_to_jornada) <= float(
        getattr(config, "SOLVENCY_STRICT_HOURS", SOLVENCY_STRICT_HOURS)
    )


def sells_settle_before_d1(
    *,
    hours_to_jornada: float | None,
    cash_lag_hours: float | None = None,
) -> bool:
    """¿El cobro de ventas (~48h) llega antes del día previo a la jornada?"""
    if hours_to_jornada is None:
        return False
    lag = float(
        cash_lag_hours
        if cash_lag_hours is not None
        else int(getattr(config, "MARKET_CYCLE_HOURS", 24) or 24) * 2
    )
    buffer = float(
        getattr(config, "SOLVENCY_D1_BUFFER_HOURS", SOLVENCY_D1_BUFFER_HOURS)
    )
    return lag <= max(0.0, float(hours_to_jornada) - buffer)


def evaluate_bid_finance(
    cost: float | None,
    balance: float,
    *,
    min_cost: float | None = None,
    max_debt: float | None = None,
    balance_future: float | None = None,
    hours_to_jornada: float | None = None,
    days_to_kickoff: float | int | None = None,
    matchday: dict[str, Any] | None = None,
    sell_proceeds_timely: float = 0.0,
    cash_lag_hours: float | None = None,
) -> dict[str, Any]:
    """
    Dos techos: bid_cap (Mister maxDebt) y solvencia pre-jornada.
    budget_fit: comfortable|tight|stretch|blocked
    """
    bal = float(balance or 0)
    bid_cap = mister_bid_cap(bal, max_debt)
    liquidity = liquidity_balance(bal, balance_future)
    hours = resolve_hours_to_jornada(
        hours_to_jornada=hours_to_jornada,
        days_to_kickoff=days_to_kickoff,
        matchday=matchday,
    )
    strict = solvency_strict_window(hours)
    sells_ok = float(sell_proceeds_timely or 0) > 0 and sells_settle_before_d1(
        hours_to_jornada=hours,
        cash_lag_hours=cash_lag_hours,
    )
    timely_sells = float(sell_proceeds_timely or 0) if sells_ok else 0.0

    out: dict[str, Any] = {
        "bid_cap": bid_cap,
        "liquidity": liquidity,
        "hours_to_jornada": hours,
        "solvency_strict": strict,
        "debt_risk": False,
        "solvency_ok": True,
        "solvency_blocked": False,
        "projected_after": liquidity,
        "budget_fit": "blocked",
        "sells_timely": sells_ok,
    }

    if cost is None:
        out["solvency_ok"] = liquidity >= 0
        return out

    c = float(cost)
    projected_no_sells = liquidity - c
    projected = projected_no_sells + timely_sells
    out["projected_after"] = projected
    out["debt_risk"] = projected_no_sells < 0 and c <= bid_cap

    if c <= 0:
        out["budget_fit"] = "comfortable"
        out["solvency_ok"] = liquidity >= 0
        out["debt_risk"] = False
        return out

    # Techo Mister
    if c > bid_cap:
        if min_cost is not None and float(min_cost) <= bid_cap:
            out["budget_fit"] = "stretch"
        else:
            out["budget_fit"] = "blocked"
        if projected < 0:
            out["solvency_ok"] = False
            if strict:
                out["solvency_blocked"] = True
        return out

    # Solvencia: no puntuar en negativo el día antes
    if projected < 0:
        out["solvency_ok"] = False
        out["debt_risk"] = True
        if strict:
            out["solvency_blocked"] = True
            out["budget_fit"] = "blocked"
            return out
        # Fuera de ventana sin cobro a tiempo → stretch (exige venta antes)
        out["budget_fit"] = "stretch"
        return out

    # Cabe (con liquidez o con ventas que cobran antes de D-1)
    if out["debt_risk"]:
        out["budget_fit"] = "tight"
        out["solvency_ok"] = True
        return out
    if c <= max(liquidity, 0.0) * 0.40:
        out["budget_fit"] = "comfortable"
    else:
        out["budget_fit"] = "tight"
    return out


def budget_fit(
    cost: float | None,
    balance: float,
    *,
    min_cost: float | None = None,
    max_debt: float | None = None,
    balance_future: float | None = None,
    hours_to_jornada: float | None = None,
    days_to_kickoff: float | int | None = None,
    matchday: dict[str, Any] | None = None,
    sell_proceeds_timely: float = 0.0,
    cash_lag_hours: float | None = None,
) -> str:
    """comfortable|tight|stretch|blocked (techo Mister + solvencia pre-jornada)."""
    return str(
        evaluate_bid_finance(
            cost,
            balance,
            min_cost=min_cost,
            max_debt=max_debt,
            balance_future=balance_future,
            hours_to_jornada=hours_to_jornada,
            days_to_kickoff=days_to_kickoff,
            matchday=matchday,
            sell_proceeds_timely=sell_proceeds_timely,
            cash_lag_hours=cash_lag_hours,
        ).get("budget_fit")
        or "blocked"
    )


def _cycle_hours_value(cycle_hours: float | None = None) -> float:
    if cycle_hours is not None:
        try:
            return float(cycle_hours)
        except (TypeError, ValueError):
            pass
    return float(getattr(config, "MARKET_CYCLE_HOURS", 24) or 24)


def sell_settlement_fields(
    price: float, *, cycle_hours: float | None = None
) -> dict[str, Any]:
    """
    Liquidez de venta al sistema (Mister): ask ≈ VM; cobro en ciclos de mercado.
    Lista → oferta (~1 ciclo) → aceptar → cobro (~1 ciclo más) ≈ 2 ciclos.
    """
    cycle = _cycle_hours_value(cycle_hours)
    proceeds = float(price or 0)
    return {
        "list_at": proceeds,
        "expected_proceeds": proceeds,
        "buyer_channel": "system",
        "settlement": "market_cycle",
        "cycle_hours": cycle,
        "cash_lag_hours": cycle * 2,
    }


def sell_cash_phrase(
    price: float, *, deferred: bool = False, cycle_hours: float | None = None
) -> str:
    """Texto corto de ask VM + plazo de caja (2 ciclos de la liga)."""
    cycle = _cycle_hours_value(cycle_hours)
    lag = int(round(cycle * 2))
    base = f"lista a ~{price:,.0f} € (VM)"
    if deferred:
        return f"{base}; caja en ~{lag}h (no liquida hoy)"
    return f"{base}; caja usable en ~{lag}h"


def rescind_instant_alt(price: float) -> dict[str, Any]:
    """Alternativa de liquidez inmediata: rescindir ≈ RESCIND_VALUE_RATIO del VM."""
    ratio = float(getattr(config, "RESCIND_VALUE_RATIO", 0.80) or 0.80)
    proceeds = round(float(price or 0) * ratio, 0)
    return {
        "action": "rescind",
        "expected_proceeds": proceeds,
        "settlement": "instant",
        "note": f"Rescindir ≈ {int(ratio * 100)}% VM al instante si urge el saldo",
    }


def target_tier_from_budget_fit(bf: str | None) -> str:
    """realistic | stretch | aspirational."""
    if bf in ("comfortable", "tight", "funding"):
        # funding = plan de caja diferida (venta al sistema; no liquida el mismo ciclo)
        return "realistic"
    if bf == "stretch":
        return "stretch"
    return "aspirational"


def _ext_avail(p: dict[str, Any]) -> str:
    ext = p.get("external") or {}
    return ext.get("availability") or ("injured" if p.get("injury") else "unknown")


def _lineup_pct(p: dict[str, Any]) -> float | None:
    ext = p.get("external") or {}
    if ext.get("lineup_prob_ext") is not None:
        try:
            return float(ext["lineup_prob_ext"])
        except (TypeError, ValueError):
            return None
    if p.get("lineup_prob") is not None:
        try:
            return float(p["lineup_prob"]) * 100.0
        except (TypeError, ValueError):
            return None
    return None


def _fotmob_rating(p: dict[str, Any]) -> float | None:
    fm = p.get("fotmob_stats") or {}
    if fm.get("rating_promedio") is not None:
        try:
            return float(fm["rating_promedio"])
        except (TypeError, ValueError):
            return None
    ext = p.get("external") or {}
    if ext.get("recent_rating") is not None:
        try:
            return float(ext["recent_rating"])
        except (TypeError, ValueError):
            return None
    return None


def _mister_avg(p: dict[str, Any] | None) -> float | None:
    if not p:
        return None
    for key in ("mister_avg", "form"):
        if p.get(key) is not None:
            try:
                return float(p[key])
            except (TypeError, ValueError):
                pass
    return None


def _mister_points(p: dict[str, Any] | None) -> float | None:
    if not p:
        return None
    if p.get("points") is None:
        return None
    try:
        return float(p["points"])
    except (TypeError, ValueError):
        return None


def _prior_avg(p: dict[str, Any] | None) -> float | None:
    if not p:
        return None
    for key in ("ff_prior_avg", "prior_avg"):
        if p.get(key) is not None:
            try:
                v = float(p[key])
                if v > 0:
                    return v
            except (TypeError, ValueError):
                pass
    ext = p.get("external") or {}
    if isinstance(ext, dict) and ext.get("ff_prior_avg") is not None:
        try:
            v = float(ext["ff_prior_avg"])
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return None


def _ff_avg(p: dict[str, Any] | None) -> float | None:
    if not p:
        return None
    for key in ("ff_mister_avg", "ff_prior_avg"):
        if p.get(key) is not None:
            try:
                return float(p[key])
            except (TypeError, ValueError):
                pass
    ext = p.get("external") or {}
    for key in ("ff_mister_avg", "ff_prior_avg"):
        if ext.get(key) is not None:
            try:
                return float(ext[key])
            except (TypeError, ValueError):
                pass
    return None


def _production_score(p: dict[str, Any] | None) -> float | None:
    if not p:
        return None
    v = p.get("production_score")
    if v is None:
        v = (p.get("external") or {}).get("production_score")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _points_trend(p: dict[str, Any] | None) -> str:
    if not p:
        return "unknown"
    t = p.get("points_trend") or "unknown"
    return str(t)


def detect_points_phase(players: list[dict[str, Any]]) -> str:
    """active si ≥30% tiene points>0 o mister_avg>0; si no preseason."""
    if not players:
        return "preseason"
    n = 0
    hit = 0
    for p in players:
        n += 1
        pts = _mister_points(p) or 0
        avg = _mister_avg(p) or 0
        if pts > 0 or avg > 0:
            hit += 1
    if n > 0 and (hit / n) >= 0.30:
        return "active"
    return "preseason"


def detect_competition_phase(
    *,
    now: datetime | date | None = None,
    season_start: str | None = None,
    points_phase: str | None = None,
) -> dict[str, Any]:
    """
    Fase de campeonato por calendario + heurística de puntos.
    preseason (>RAMP días) | ramp (≤RAMP hasta J1) | active (desde J1 o puntos).
    """
    start_raw = (season_start or getattr(config, "SEASON_START_DATE", "2026-08-15")).strip()
    try:
        y, m, d = (int(x) for x in start_raw.split("-")[:3])
        kickoff = date(y, m, d)
    except (TypeError, ValueError):
        kickoff = date(2026, 8, 15)

    if now is None:
        today = datetime.now(timezone.utc).date()
    elif isinstance(now, datetime):
        today = now.date()
    else:
        today = now

    days = (kickoff - today).days
    ramp_n = int(getattr(config, "RAMP_DAYS_BEFORE_KICKOFF", 7))
    if points_phase == "active" or days <= 0:
        phase = "active"
    elif days <= ramp_n:
        phase = "ramp"
    else:
        phase = "preseason"

    return {
        "competition_phase": phase,
        "season_start": kickoff.isoformat(),
        "days_to_kickoff": days,
        "points_phase_heuristic": points_phase or "preseason",
    }


def _is_top_ff(p: dict[str, Any]) -> bool:
    if p.get("is_top_ff"):
        return True
    return bool((p.get("external") or {}).get("is_top_ff"))


def _avg_scale(p: dict[str, Any] | None) -> float:
    return resolve_avg_scale(p)


def _lineup_titularidad_label(p: dict[str, Any] | None, lineup: float | None) -> str:
    """Texto de titularidad; marca proxy por PJ / ficha cuando no hay % de alineación."""
    if lineup is None:
        return "—%"
    src = ((p or {}).get("external") or {}).get("lineup_prob_source")
    if src == "ff_profile_titular":
        return f"{int(lineup)}% (titular FF)"
    if src in ("ff_apps_proxy", "ff_apps_proxy_fotmob"):
        suffix = "por PJ" if src == "ff_apps_proxy" else "por PJ+min"
        return f"≈{int(lineup)}% ({suffix})"
    return f"{int(lineup)}%"


def _is_star(p: dict[str, Any]) -> bool:
    """Estrella / pieza de once: TOP FF, o titular con buen rating/media."""
    if _is_top_ff(p):
        return True
    lineup = _lineup_pct(p)
    rating = _fotmob_rating(p)
    avg = _mister_avg(p)
    prod = _production_score(p)
    ff = _ff_avg(p)
    scale = _avg_scale(p)
    if lineup is not None and lineup >= 80:
        if rating is not None and rating >= 7.0:
            return True
        if avg is not None and avg >= scale_threshold(5.0, scale):
            return True
        if ff is not None and ff >= scale_threshold(5.0, scale):
            return True
        if prod is not None and prod >= 58:
            return True
    return False


def _is_reliable_starter(p: dict[str, Any]) -> bool:
    """Titular real (alineación ≥70%). Ignora el once fantasy de Mister."""
    if _ext_avail(p) in ("injured", "suspended") or p.get("injury"):
        return False
    lineup = _lineup_pct(p)
    return lineup is not None and lineup >= 70


def _recent_minutes(p: dict[str, Any]) -> float | None:
    fm = p.get("fotmob_stats") or {}
    if fm.get("minutos_ultimos_5") is None:
        return None
    try:
        return float(fm["minutos_ultimos_5"])
    except (TypeError, ValueError):
        return None


def _plays_little(p: dict[str, Any]) -> bool:
    """Poca titularidad real o minutos recientes bajos."""
    if _ext_avail(p) in ("injured", "suspended") or p.get("injury"):
        return True
    ext = p.get("external") or {}
    if p.get("gw_out") or ext.get("gw_out"):
        return True
    gw_prob = p.get("gw_lineup_prob")
    if gw_prob is None:
        gw_prob = ext.get("gw_lineup_prob")
    try:
        if gw_prob is not None and float(gw_prob) < 40:
            return True
    except (TypeError, ValueError):
        pass
    lineup = _lineup_pct(p)
    low_lp = getattr(config, "LINEUP_PROB_LOW", 0.40) * 100.0
    mins_low = getattr(config, "MINUTES_RECENT_LOW", 90)
    mins = _recent_minutes(p)
    if mins is not None and mins < mins_low:
        return True
    if lineup is not None and lineup < low_lp:
        # LP muy baja (<25%): juega poco aunque FotMob tenga minutos residuales
        if lineup < 25:
            return True
        if mins is not None and mins >= mins_low * 2:
            return False
        return True
    return False


def build_gw_xi_advice(
    squad: list[dict[str, Any]] | None,
    *,
    matchday: dict[str, Any] | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """
    Consejos de once para la jornada FF: start / sit / doubt.
    Solo jugadores de la plantilla con señal gw_*.
    """
    out: list[dict[str, Any]] = []
    jornada = (matchday or {}).get("jornada")
    for p in squad or []:
        ext = p.get("external") or {}
        prob = p.get("gw_lineup_prob")
        if prob is None:
            prob = ext.get("gw_lineup_prob")
        # Solo señal de jornada FF (no ficha/JP genérico)
        if prob is None and not (
            p.get("gw_starter")
            or p.get("gw_doubt")
            or p.get("gw_out")
            or ext.get("gw_starter")
            or ext.get("gw_doubt")
            or ext.get("gw_out")
        ):
            continue
        if prob is None:
            continue
        try:
            prob_f = float(prob)
        except (TypeError, ValueError):
            continue

        role = p.get("gw_role") or ext.get("gw_role")
        gw_starter = bool(p.get("gw_starter") or ext.get("gw_starter"))
        gw_doubt = bool(p.get("gw_doubt") or ext.get("gw_doubt"))
        gw_out = bool(p.get("gw_out") or ext.get("gw_out"))
        gw_blank = bool(p.get("gw_blank") or ext.get("gw_blank"))
        opponent = p.get("gw_opponent") or ext.get("gw_opponent")

        if gw_blank:
            advice = "sit"
            why = "Sin partido esta jornada (blank) — no alinear"
        elif gw_out or prob_f < 40:
            advice = "sit"
            why = f"FF jornada: {prob_f:.0f}% — mejor no alinear"
        elif gw_doubt or (40 <= prob_f < 70):
            advice = "doubt"
            why = f"FF jornada: {prob_f:.0f}% — duda de titularidad"
        elif gw_starter or prob_f >= 70:
            advice = "start"
            why = f"FF jornada: {prob_f:.0f}% titular probable"
        else:
            continue

        if opponent:
            why = f"{why} (vs {opponent})"
        if role == "bench" and advice == "start":
            why = f"{why}; aparece en banquillo FF pero con % alto"

        out.append(
            {
                "player_id": p.get("id"),
                "name": p.get("name"),
                "position": p.get("position"),
                "team": p.get("team"),
                "advice": advice,
                "prob": round(prob_f, 0),
                "role": role,
                "opponent": opponent,
                "jornada": jornada,
                "why": why,
                "in_lineup": bool(p.get("in_lineup")),
            }
        )

    order = {"sit": 0, "doubt": 1, "start": 2}
    out.sort(
        key=lambda x: (
            order.get(str(x.get("advice")), 9),
            -float(x.get("prob") or 0) if x.get("advice") == "start" else float(x.get("prob") or 0),
        )
    )
    return out[: max(1, limit)]


def _parse_formation(formation: str | None) -> dict[str, int]:
    """'1-4-4-2' / '4-3-3' → cupos por posición. Fallback IDEAL_XI."""
    default = dict(getattr(config, "IDEAL_XI", None) or {"GK": 1, "DF": 4, "MF": 3, "FW": 3})
    if not formation:
        return default
    parts = [p.strip() for p in str(formation).replace("–", "-").split("-") if p.strip()]
    nums: list[int] = []
    for p in parts:
        try:
            nums.append(int(p))
        except ValueError:
            return default
    if len(nums) == 4 and sum(nums) == 11:
        return {"GK": nums[0], "DF": nums[1], "MF": nums[2], "FW": nums[3]}
    if len(nums) == 3 and sum(nums) == 10:
        return {"GK": 1, "DF": nums[0], "MF": nums[1], "FW": nums[2]}
    return default


def _gw_prob_pct(p: dict[str, Any]) -> float | None:
    """Probabilidad de jugar esta jornada (0–100), si hay señal FF."""
    ext = p.get("external") or {}
    raw = p.get("gw_lineup_prob")
    if raw is None:
        raw = ext.get("gw_lineup_prob")
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    # Algunos payloads traen 0–1
    if 0.0 <= v <= 1.0:
        v *= 100.0
    return max(0.0, min(100.0, v))


def _xi_play_score(p: dict[str, Any]) -> tuple:
    """
    Ranking para el once de jornada desde plantilla propia.
    Con xPts calculados manda el puntos esperados; si no, señal GW / titularidad.
    """
    gw = _gw_prob_pct(p)
    lp = _lineup_pct(p)
    avail = _ext_avail(p)
    injured = avail in ("injured", "suspended") or bool(p.get("injury"))
    gw_blank = bool(p.get("gw_blank") or (p.get("external") or {}).get("gw_blank"))
    gw_out = bool(p.get("gw_out") or (p.get("external") or {}).get("gw_out")) or gw_blank

    if injured or gw_blank or gw_out:
        play = -50.0
        signal = "blank" if gw_blank and not injured else "out"
    elif gw is not None:
        play = float(gw)
        if gw >= 70:
            signal = "start"
        elif gw >= 40:
            signal = "doubt"
        else:
            signal = "sit"
            play = gw - 25.0  # castigo fuerte si FF dice fuera
    elif lp is not None:
        play = float(lp) * 0.85  # sin señal jornada: un poco menos peso
        signal = "start" if lp >= 70 else ("doubt" if lp >= 40 else "sit")
    else:
        play = 25.0  # desconocido: relleno solo si hace falta
        signal = "unknown"

    p_play = None
    try:
        raw_pp = p.get("xpts_p_play")
        p_play = float(raw_pp) if raw_pp is not None else None
    except (TypeError, ValueError):
        p_play = None
    if p_play is not None and not injured and not gw_out:
        play = p_play * 100.0
        signal = "start" if p_play >= 0.70 else ("doubt" if p_play >= 0.40 else "sit")

    xpts = None
    try:
        raw_x = p.get("xpts")
        xpts = float(raw_x) if raw_x is not None else None
    except (TypeError, ValueError):
        xpts = None

    if xpts is not None and not injured and not gw_out:
        scale = _avg_scale(p) or 8.0
        # xPts normalizados a escala Mixto y llevados al rango del score previo
        score = (xpts * (8.0 / scale)) * 12.0
    else:
        avg = _mister_avg(p) or 0.0
        pts = 0.0
        try:
            pts = float(p.get("ff_mister_points") or p.get("points") or 0)
        except (TypeError, ValueError):
            pts = 0.0
        rating = _fotmob_rating(p) or 0.0
        score = play + min(12.0, avg * 1.2) + min(8.0, pts / 40.0) + min(5.0, rating)
    return (score, play, signal, gw, lp, injured)


# Probabilidad de jugar por debajo de la cual alinear a alguien es asumir un cero
XI_RISK_P_PLAY = 0.45


def _play_probability(item: dict[str, Any]) -> float | None:
    """Probabilidad de jugar 0–1 del candidato, con la mejor señal disponible."""
    try:
        raw = item["player"].get("xpts_p_play")
        if raw is not None:
            return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        pass
    for value in (item.get("gw"), item.get("lp")):
        try:
            if value is not None:
                return max(0.0, min(1.0, float(value) / 100.0))
        except (TypeError, ValueError):
            continue
    return None


def _slot_risk(item: dict[str, Any]) -> tuple[bool, str | None]:
    """
    Un hueco es riesgo declarado cuando el que lo ocupa probablemente no juegue.
    Preferimos decirlo a vender el once como cerrado: un cero cuesta más que
    cualquier afinado de puntos esperados.
    """
    if item["injured"]:
        return True, "Lesionado o sancionado: cero casi seguro"
    if item["signal"] == "blank":
        return True, "Sin partido esta jornada (blank): no puntúa"
    if item["signal"] == "out":
        return True, "Descartado en la previa de la jornada"
    prob = _play_probability(item)
    if prob is None:
        return True, "Sin señal de titularidad: no sabemos si juega"
    if prob < XI_RISK_P_PLAY:
        return True, f"Solo {prob * 100:.0f}% de jugar"
    return False, None


def pick_captain(
    xi: list[dict[str, Any]],
    *,
    multiplier: float | None = None,
    mode: str = "by_market_value",
) -> dict[str, Any] | None:
    """
    Capitán = quien más puntos añade al multiplicar: `xpts * (mult - 1)`.

    Por defecto el multiplicador es por valor de mercado (Mister):
    <5M → x3, 5–10M → x2, ≥10M → x1.5. Con `mode="fixed"` se usa `multiplier`
    uniforme para todo el XI.

    Un capitán que no juega es el error más caro del juego, así que la
    probabilidad de jugar corta antes que el techo: por debajo de 0.6 no se
    capitanea salvo que no haya nadie mejor.
    """
    if not xi:
        return None
    fixed = mode == "fixed"
    if fixed:
        try:
            fixed_mult = float(multiplier) if multiplier is not None else 0.0
        except (TypeError, ValueError):
            fixed_mult = 0.0
        if fixed_mult <= 1:
            return None

    def _f(row: dict[str, Any], key: str) -> float | None:
        try:
            v = row.get(key)
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _row_mult(row: dict[str, Any]) -> float:
        if fixed:
            return float(fixed_mult)
        existing = _f(row, "captain_multiplier")
        if existing is not None and existing > 1:
            return existing
        price = row.get("price")
        if price is None:
            price = row.get("market_value")
        return captain_multiplier_for_price(price)

    scored: list[tuple[float, float, float, dict[str, Any]]] = []
    for row in xi:
        if row.get("injured"):
            continue
        if row.get("signal") == "blank":
            continue
        xpts = _f(row, "xpts")
        if xpts is None:
            continue
        mult = _row_mult(row)
        if mult <= 1:
            continue
        p_play = _f(row, "p_play")
        gain = xpts * (mult - 1.0)
        if p_play is not None and p_play < 0.6:
            gain *= 0.5  # riesgo de cero al cuadrado
        scored.append((gain, p_play if p_play is not None else 0.0, mult, row))

    # Nunca capitanear un hueco de riesgo si hay alguien que sí va a jugar
    safe = [t for t in scored if not t[3].get("slot_risk")]
    if safe:
        scored = safe

    if not scored:
        return None
    # A ganancia igual manda quien seguro juega y, después, el partido más amable
    scored.sort(key=lambda t: (-t[0], -t[1], -(_f(t[3], "fdr_multiplier") or 1.0)))
    gain, p_play, best_mult, best = scored[0]
    alt_t = scored[1] if len(scored) > 1 else None

    why = (
        f"+{gain:.1f} pts por el x{best_mult:g}"
        f" ({best.get('xpts')} esperados, {p_play * 100:.0f}% de jugar)"
    )
    if best.get("opponent_name"):
        where = "en casa" if best.get("is_home") else ("fuera" if best.get("is_home") is False else "")
        why = f"{why} vs {best['opponent_name']}{(' ' + where) if where else ''}"
    return {
        "player_id": best.get("player_id"),
        "name": best.get("name"),
        "position": best.get("position"),
        "team": best.get("team"),
        "xpts": best.get("xpts"),
        "p_play": best.get("p_play"),
        "price": best.get("price") if best.get("price") is not None else best.get("market_value"),
        "multiplier": float(best_mult),
        "expected_gain": round(gain, 2),
        "why": why,
        "alternative": (
            {
                "player_id": alt_t[3].get("player_id"),
                "name": alt_t[3].get("name"),
                "xpts": alt_t[3].get("xpts"),
                "multiplier": float(alt_t[2]),
                "expected_gain": round(alt_t[0], 2),
            }
            if alt_t
            else None
        ),
    }


def _better_formation(
    *,
    risky_now: int,
    current_shape: dict[str, int],
    fill: Any,
    row_out: Any,
    current_rows: list[dict[str, Any]],
    target: int,
) -> dict[str, Any] | None:
    """
    Si el once obliga a alinear ceros probables, buscar una formación válida de
    Mister que los evite tirando de otra línea. Devuelve None si no mejora.
    """
    if risky_now <= 0:
        return None
    seen: set[tuple] = {tuple(sorted(current_shape.items()))}
    best: tuple | None = None
    for name in getattr(config, "IDEAL_FORMATIONS", ()) or ():
        cand_shape = _parse_formation(name)
        key = tuple(sorted(cand_shape.items()))
        if key in seen:
            continue
        seen.add(key)
        rows = [row_out(item, slot=slot, role="xi") for slot, item in fill(cand_shape)]
        if len(rows) < target:
            continue
        risk = sum(1 for r in rows if r.get("slot_risk"))
        if risk >= risky_now:
            continue
        xpts = sum(float(r.get("xpts") or 0) for r in rows)
        rank = (risk, -xpts)
        if best is None or rank < best[0]:
            best = (rank, name, rows, risk, xpts)
    if not best:
        return None

    _, name, rows, risk, xpts = best

    def _brief(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "player_id": r.get("player_id"),
            "name": r.get("name"),
            "position": r.get("position"),
        }

    current_ids = {str(r.get("player_id")) for r in current_rows}
    new_ids = {str(r.get("player_id")) for r in rows}
    return {
        "formation": name,
        "risk_slots_now": risky_now,
        "risk_slots_after": risk,
        "xpts_after": round(xpts, 2),
        "adds": [_brief(r) for r in rows if str(r.get("player_id")) not in current_ids][:4],
        "drops": [_brief(r) for r in current_rows if str(r.get("player_id")) not in new_ids][:4],
        "why": (
            f"Con {name} bajas de {risky_now} a {risk} hueco(s) de riesgo "
            "tirando de una línea con más fondo."
        ),
    }


def build_recommended_gw_xi(
    squad: list[dict[str, Any]] | None,
    *,
    formation: str | None = None,
    matchday: dict[str, Any] | None = None,
    captain_rule: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Once recomendado (11) para la siguiente jornada usando SOLO la plantilla actual.
    Formación Mister (p.ej. 1-4-4-2) o IDEAL_XI. Ranking por % jornada FF + fallback titularidad.
    """
    shape = _parse_formation(formation)
    jornada = (matchday or {}).get("jornada")
    fixtures = (matchday or {}).get("fixtures_count")

    scored: list[dict[str, Any]] = []
    for p in squad or []:
        pos = str(p.get("position") or "").upper()
        if pos not in ("GK", "DF", "MF", "FW"):
            continue
        score, play, signal, gw, lp, injured = _xi_play_score(p)
        scored.append(
            {
                "player": p,
                "position": pos,
                "score": score,
                "play": play,
                "signal": signal,
                "gw": gw,
                "lp": lp,
                "injured": injured,
            }
        )

    by_pos: dict[str, list[dict[str, Any]]] = {"GK": [], "DF": [], "MF": [], "FW": []}
    for row in scored:
        by_pos[row["position"]].append(row)
    for pos in by_pos:
        # A xPts iguales, rival blando y local delante
        by_pos[pos].sort(
            key=lambda x: (
                -x["score"],
                -float(x["player"].get("fdr_multiplier") or 1.0),
                -float(x["gw"] or x["lp"] or 0),
            )
        )

    picked_ids: set[str] = set()
    xi: list[dict[str, Any]] = []

    def _row_out(item: dict[str, Any], *, slot: str, role: str = "xi") -> dict[str, Any]:
        p = item["player"]
        gw = item["gw"]
        lp = item["lp"]
        signal = item["signal"]
        opponent = p.get("gw_opponent") or (p.get("external") or {}).get("gw_opponent")
        xpts = p.get("xpts")
        if item["injured"]:
            why = "Lesionado/sancionado — solo si no hay alternativa"
        elif signal == "blank" or p.get("gw_blank") or (p.get("external") or {}).get("gw_blank"):
            why = "Sin partido esta jornada (blank) — no puntúa en Mister"
        elif xpts is not None:
            why = f"{float(xpts):.1f} pts esperados · {p.get('xpts_why') or ''}".strip(" ·")
        elif gw is not None:
            why = f"FF jornada {gw:.0f}%"
            if opponent:
                why = f"{why} vs {opponent}"
        elif lp is not None:
            why = f"Titularidad habitual {lp:.0f}% (sin previa FF)"
        else:
            why = "Sin % — mejor disponible en plantilla"
        risk, risk_reason = _slot_risk(item) if role == "xi" else (False, None)
        if signal == "blank" and role == "xi":
            risk = True
            risk_reason = risk_reason or "Sin partido esta jornada"
        if risk and risk_reason:
            why = f"{risk_reason} · {why}"
        elif signal == "doubt" and role == "xi":
            why = f"Duda · {why}"
        return {
            "slot": slot,
            "player_id": p.get("id"),
            "name": p.get("name"),
            "position": item["position"],
            "team": p.get("team"),
            "role": role,
            "signal": signal,
            "slot_risk": risk,
            "risk_reason": risk_reason,
            "prob": round(gw, 0) if gw is not None else (round(lp, 0) if lp is not None else None),
            "prob_source": "gw" if gw is not None else ("season" if lp is not None else None),
            "opponent": opponent or p.get("opponent_name"),
            "opponent_name": p.get("opponent_name"),
            "is_home": p.get("is_home"),
            "in_lineup": bool(p.get("in_lineup")),
            "injured": bool(item["injured"]),
            "score": round(item["score"], 1),
            "xpts": xpts,
            "xpts_floor": p.get("xpts_floor"),
            "p_play": p.get("xpts_p_play"),
            "fdr": p.get("fdr"),
            "fdr_label": p.get("fdr_label"),
            "fdr_multiplier": p.get("fdr_multiplier"),
            "fdr_why": p.get("fdr_why"),
            "price": _money(p.get("price") or p.get("market_value")) or None,
            "captain_multiplier": captain_multiplier_for_price(
                p.get("price") or p.get("market_value")
            )
            if (p.get("price") or p.get("market_value"))
            else None,
            "why": why,
            "jornada": jornada,
        }

    def _fill(shape_map: dict[str, int]) -> list[tuple[str, dict[str, Any]]]:
        """Reparte los cupos de una formación: sanos primero, lesionados al final."""
        chosen: list[tuple[str, dict[str, Any]]] = []
        used: set[str] = set()
        for pos in ("GK", "DF", "MF", "FW"):
            need = int(shape_map.get(pos, 0))
            pool = by_pos.get(pos) or []
            preferred = [
                x
                for x in pool
                if not x["injured"]
                and x["signal"] not in ("out", "blank")
            ]
            fallback = [x for x in pool if x not in preferred]
            n = 0
            for item in preferred + fallback:
                if n >= need:
                    break
                pid = str(item["player"].get("id") or "")
                if not pid or pid in used:
                    continue
                n += 1
                used.add(pid)
                chosen.append((f"{pos}{n}", item))
        # Plantilla corta en una línea: completar con lo mejor que quede sano
        target = sum(int(shape_map.get(p, 0)) for p in ("GK", "DF", "MF", "FW"))
        if len(chosen) < target:
            leftovers = [
                x
                for x in scored
                if str(x["player"].get("id") or "") not in used and not x["injured"]
            ]
            leftovers.sort(key=lambda x: (-x["score"], 0 if x["position"] != "GK" else 1))
            for item in leftovers:
                if len(chosen) >= target:
                    break
                pid = str(item["player"].get("id") or "")
                if pid in used:
                    continue
                used.add(pid)
                pos = item["position"]
                n = sum(1 for slot, _ in chosen if slot.startswith(pos)) + 1
                chosen.append((f"{pos}{n}", item))
        return chosen

    total_need = sum(int(shape.get(p, 0)) for p in ("GK", "DF", "MF", "FW"))
    chosen = _fill(shape)
    for slot, item in chosen:
        picked_ids.add(str(item["player"].get("id") or ""))
        xi.append(_row_out(item, slot=slot, role="xi"))

    # Orden visual GK→DF→MF→FW
    order_pos = {"GK": 0, "DF": 1, "MF": 2, "FW": 3}
    xi.sort(key=lambda r: (order_pos.get(str(r.get("position")), 9), str(r.get("slot") or "")))

    bench: list[dict[str, Any]] = []
    for item in scored:
        pid = str(item["player"].get("id") or "")
        if pid in picked_ids:
            continue
        if item["injured"] and item["signal"] == "out":
            continue
        bench.append(_row_out(item, slot=item["position"], role="bench"))
    bench.sort(key=lambda r: -float(r.get("score") or 0))
    bench = bench[:6]

    signals = {"start": 0, "doubt": 0, "sit": 0, "unknown": 0, "out": 0}
    gw_n = 0
    for r in xi:
        sig = str(r.get("signal") or "unknown")
        if sig in signals:
            signals[sig] += 1
        else:
            signals["unknown"] += 1
        if r.get("prob_source") == "gw":
            gw_n += 1

    form_label = formation or "-".join(
        str(shape[p]) for p in ("GK", "DF", "MF", "FW") if shape.get(p) is not None
    )
    # Etiqueta tipo fútbol: sin el 1 de GK si formation era 4-4-2
    if formation and str(formation).count("-") == 2:
        form_label = str(formation)
    elif formation:
        form_label = str(formation)

    risky = [
        {
            "player_id": r.get("player_id"),
            "name": r.get("name"),
            "position": r.get("position"),
            "slot": r.get("slot"),
            "prob": r.get("prob"),
            "p_play": r.get("p_play"),
            "reason": r.get("risk_reason"),
        }
        for r in xi
        if r.get("slot_risk")
    ]
    formation_switch = _better_formation(
        risky_now=len(risky),
        current_shape=shape,
        fill=_fill,
        row_out=_row_out,
        current_rows=xi,
        target=total_need,
    )

    captain = None
    cap_rule = captain_rule if isinstance(captain_rule, dict) else {}
    if cap_rule.get("enabled"):
        mode = str(cap_rule.get("mode") or "by_market_value")
        fixed = cap_rule.get("multiplier")
        try:
            fixed_f = float(fixed) if fixed is not None else None
        except (TypeError, ValueError):
            fixed_f = None
        captain = pick_captain(xi, multiplier=fixed_f, mode=mode)
        if captain:
            for row in xi:
                row["is_captain"] = row.get("player_id") == captain.get("player_id")

    total_xpts = 0.0
    for row in xi:
        try:
            v = float(row.get("xpts")) if row.get("xpts") is not None else 0.0
        except (TypeError, ValueError):
            v = 0.0
        total_xpts += v
    if captain:
        total_xpts += float(captain.get("expected_gain") or 0.0)

    return {
        "jornada": jornada,
        "fixtures_count": fixtures,
        "formation": form_label,
        "shape": shape,
        "xi": xi,
        "bench": bench,
        "captain": captain,
        "captain_enabled": bool(cap_rule.get("enabled")),
        "risky_slots": risky,
        "formation_switch": formation_switch,
        "summary": {
            "xi_count": len(xi),
            "xi_target": total_need,
            "complete": len(xi) >= total_need,
            "with_gw_signal": gw_n,
            "signals": signals,
            "risk_slots": len(risky),
            "safe_starters": len(xi) - len(risky),
            "xpts_total": round(total_xpts, 2) if total_xpts else None,
        },
    }


def _is_useful_patch(p: dict[str, Any]) -> bool:
    """Parche barato que juega de verdad: no vender salvo emergencia."""
    if _ext_avail(p) in ("injured", "suspended") or p.get("injury"):
        return False
    price = _money(p.get("price") or p.get("market_value"))
    if price <= 0 or price > 2_000_000:
        return False
    lineup = _lineup_pct(p)
    return lineup is not None and lineup >= 45


def _starter_floor(position: str) -> int:
    return {"GK": 1, "DF": 3, "MF": 3, "FW": 2}.get(position or "MF", 2)


def _healthy_count(squad: list[dict[str, Any]], position: str) -> int:
    n = 0
    for p in squad:
        if (p.get("position") or "") != position:
            continue
        avail = _ext_avail(p)
        if avail in ("injured", "suspended"):
            continue
        if p.get("injury"):
            continue
        n += 1
    return n


def _starter_count(squad: list[dict[str, Any]], position: str) -> int:
    return sum(
        1
        for p in squad
        if (p.get("position") or "") == position and _is_reliable_starter(p)
    )


def starters_after_sale(squad: list[dict[str, Any]], player: dict[str, Any]) -> int:
    pos = player.get("position") or "MF"
    others = [p for p in squad if str(p.get("id")) != str(player.get("id"))]
    return _starter_count(others, pos)


def protect_depth_if_sold(squad: list[dict[str, Any]], player: dict[str, Any]) -> bool:
    """
    True si vender deja la línea fina: cupo sano bajo MIN_* o titulares reales
    por debajo del suelo del once.

    Vender poco usados (no starter/regular) no cuenta como riesgo de once.
    """
    lineup = _lineup_pct(player)
    regular_lp = getattr(config, "LINEUP_PROB_REGULAR", 0.45) * 100.0
    is_regular = _is_reliable_starter(player) or (
        lineup is not None and lineup >= regular_lp
    )
    if not is_regular:
        return False
    pos = player.get("position") or "MF"
    others = [p for p in squad if str(p.get("id")) != str(player.get("id"))]
    healthy = _healthy_count(others, pos)
    starters = _starter_count(others, pos)
    min_need = _mins().get(pos, 2)
    if healthy < min_need:
        return True
    if starters < _starter_floor(pos):
        return True
    return False


def _xi_impact_if_sold(squad: list[dict[str, Any]], player: dict[str, Any]) -> str:
    """
    safe  → el once/línea aguanta sin él
    risk  → vendería un titular dejando la línea justa
    soft  → cobertura sana pero pierdes un regular
    """
    pos = player.get("position") or "MF"
    mins = _mins()
    min_need = mins.get(pos, 2)
    others = [p for p in squad if str(p.get("id")) != str(player.get("id"))]
    healthy = _healthy_count(others, pos)
    starters = _starter_count(others, pos)
    was_starter = _is_reliable_starter(player)
    starter_floor = _starter_floor(pos)

    if healthy < min_need:
        return "risk"
    if was_starter and starters < starter_floor:
        return "risk"
    if protect_depth_if_sold(squad, player):
        return "risk"
    if was_starter and starters < starter_floor + 1:
        return "soft"
    return "safe"


def _mins() -> dict[str, int]:
    return {
        "GK": config.MIN_GK,
        "DF": config.MIN_DF,
        "MF": config.MIN_MF,
        "FW": config.MIN_FW,
    }


_GAP_FALLBACK_COST = {
    "GK": 1_500_000.0,
    "DF": 3_000_000.0,
    "MF": 3_000_000.0,
    "FW": 4_000_000.0,
}


def estimate_gap_funding(
    structural_needs: list[dict[str, Any]] | None,
    market_opportunities: list[dict[str, Any]] | None,
    balance: float,
    *,
    top_n: int = 3,
    cycle_hours: float | None = None,
) -> dict[str, Any]:
    """
    Estima coste mínimo para cubrir needs Alta (multi-carencia).
    Prefiere candidatos asequibles (realistic) del pool; si no hay, marca shortfall.
    funding_target = suma de hasta top_n gaps; shortfall vs saldo.
    Ventas para cubrir shortfall liquidan en ~2 ciclos de mercado, no el mismo día.
    """
    needs = [n for n in (structural_needs or []) if n.get("priority") == "Alta"]
    market = list(market_opportunities or [])
    bal = max(0.0, float(balance or 0))

    gap_rows: list[dict[str, Any]] = []
    seen_pos: set[str] = set()

    for need in needs:
        pos = need.get("position")
        key = str(pos or need.get("need") or "")
        if not key or key in seen_pos:
            continue
        seen_pos.add(key)

        min_need_price = need.get("min_price")
        max_need_price = need.get("max_price")
        try:
            floor = float(min_need_price) if min_need_price is not None else None
        except (TypeError, ValueError):
            floor = None
        try:
            ceil = float(max_need_price) if max_need_price is not None else None
        except (TypeError, ValueError):
            ceil = None

        affordable_costs: list[float] = []
        any_costs: list[float] = []
        for o in market:
            opos = o.get("position")
            if pos and opos != pos:
                continue
            if not pos and need.get("need") == "patch_cheap":
                price = _money(o.get("puja_recomendada") or o.get("price"))
                if price <= 0 or price > float(ceil or 2_000_000):
                    continue
            elif pos:
                # Preferir quien cubre estructuralmente o tiene titularidad usable
                lp = None
                ext = o.get("external") or {}
                if ext.get("lineup_prob_ext") is not None:
                    try:
                        lp = float(ext["lineup_prob_ext"])
                    except (TypeError, ValueError):
                        lp = None
                elif o.get("lineup_prob") is not None:
                    try:
                        lp = float(o["lineup_prob"]) * 100.0
                    except (TypeError, ValueError):
                        lp = None
                fills = bool(o.get("fills_structural") or o.get("fills_need"))
                if not fills and lp is not None and lp < 45:
                    continue
                # Muestra corta: no anclar funding a medias poco fiables
                if o.get("sample_thin"):
                    continue
                price = _money(o.get("puja_recomendada") or o.get("price"))
                if price <= 0:
                    continue
                if floor is not None and price < floor * 0.5:
                    continue
                if ceil is not None and price > ceil:
                    continue
            else:
                continue
            price = _money(o.get("puja_recomendada") or o.get("price"))
            any_costs.append(price)
            tier = o.get("target_tier") or target_tier_from_budget_fit(
                o.get("budget_fit") or budget_fit(price, bal, min_cost=price)
            )
            if tier == "realistic" or price <= bal:
                affordable_costs.append(price)

        no_affordable = False
        if affordable_costs:
            cost = min(affordable_costs)
        elif any_costs:
            # Hay candidatos pero todos fuera de caja → shortfall explícito
            cost = min(any_costs)
            no_affordable = True
        elif floor is not None:
            cost = floor
            no_affordable = floor > bal
        elif ceil is not None:
            cost = min(ceil, _GAP_FALLBACK_COST.get(str(pos or ""), 2_000_000.0))
            no_affordable = cost > bal
        else:
            cost = _GAP_FALLBACK_COST.get(str(pos or ""), 2_000_000.0)
            no_affordable = cost > bal

        gap_rows.append(
            {
                "position": pos,
                "need": need.get("need"),
                "cost": cost,
                "label": need.get("reason") or need.get("need") or (pos or "gap"),
                "no_affordable_candidate": no_affordable,
            }
        )

    # Priorizar carencias más caras / críticas primero en el target (tope top_n)
    gap_rows.sort(key=lambda g: -float(g.get("cost") or 0))
    selected = gap_rows[: max(1, top_n)] if gap_rows else []
    funding_target = sum(float(g["cost"]) for g in selected)
    funding_shortfall = max(0.0, funding_target - bal)
    cheapest = min((float(g["cost"]) for g in gap_rows), default=None)
    cash_tight = funding_shortfall > 0 or (cheapest is not None and bal < cheapest)
    if any(g.get("no_affordable_candidate") for g in selected):
        cash_tight = True

    cycle = _cycle_hours_value(cycle_hours)
    lag = cycle * 2
    return {
        "funding_target": funding_target,
        "funding_shortfall": funding_shortfall,
        "cash_tight": cash_tight,
        "gap_costs": selected,
        "all_gap_costs": gap_rows,
        "positions": [g.get("position") for g in selected if g.get("position")],
        "cheapest_need": cheapest,
        "settlement": "market_cycle",
        "cycle_hours": cycle,
        "cash_lag_hours": lag,
        "liquidity_note": (
            f"Las ventas al sistema no liquidan hoy: oferta ~{int(round(cycle))}h, "
            f"cobro ~{int(round(cycle))}h tras aceptar "
            f"(caja usable en ~{int(round(lag))}h). Urgente: rescindir ≈ 80% VM o cláusula rival."
        ),
    }


def real_needy_positions(
    diagnosis: dict[str, Any] | None = None,
    structural_needs: list[dict[str, Any]] | None = None,
) -> set[str]:
    """Posiciones con cobertura thin/critical o carencia estructural Alta."""
    pos: set[str] = set()
    buckets: list[dict[str, Any]] = []
    if diagnosis:
        if isinstance(diagnosis.get("lineas"), dict):
            buckets.append(diagnosis["lineas"])
        if isinstance(diagnosis.get("by_position"), dict):
            buckets.append(diagnosis["by_position"])
    for bucket in buckets:
        for p, info in bucket.items():
            if not isinstance(info, dict):
                continue
            cov = str(info.get("coverage") or "")
            if cov in ("critical", "thin"):
                pos.add(str(p))
    for n in structural_needs or []:
        if n.get("priority") == "Alta" and n.get("position"):
            pos.add(str(n["position"]))
    return pos


def _coverage_urgency(
    pos: str,
    diagnosis: dict[str, Any] | None,
    structural_needs: list[dict[str, Any]] | None,
) -> int:
    """0 = critical / estructural Alta; 1 = thin."""
    for n in structural_needs or []:
        if n.get("priority") == "Alta" and str(n.get("position") or "") == pos:
            return 0
    buckets: list[dict[str, Any]] = []
    if diagnosis:
        if isinstance(diagnosis.get("lineas"), dict):
            buckets.append(diagnosis["lineas"])
        if isinstance(diagnosis.get("by_position"), dict):
            buckets.append(diagnosis["by_position"])
    for bucket in buckets:
        info = bucket.get(pos) or {}
        if not isinstance(info, dict):
            continue
        cov = str(info.get("coverage") or "")
        if cov == "critical":
            return 0
        if cov == "thin":
            return 1
    return 1


def other_gaps_min_cost(
    funding_info: dict[str, Any] | None = None,
    *,
    exclude_position: str | None = None,
    diagnosis: dict[str, Any] | None = None,
    structural_needs: list[dict[str, Any]] | None = None,
    opportunities: list[dict[str, Any]] | None = None,
) -> float:
    """Chollo on-market de la otra línea needy más urgente. 0 si hoy no hay candidato."""
    excl = str(exclude_position or "")
    if diagnosis is not None or structural_needs:
        needy = real_needy_positions(diagnosis, structural_needs)
        if excl:
            needy.discard(excl)
        if not needy:
            return 0.0
        fills: list[tuple[int, float]] = []
        market = list(opportunities or [])
        for pos in needy:
            costs = [
                _money(o.get("puja_recomendada") or o.get("price"))
                for o in market
                if (o.get("position") or "") == pos
                and (o.get("on_daily_market") or o.get("seller") == "market")
            ]
            costs = [c for c in costs if c > 0]
            if not costs:
                continue
            fills.append(
                (_coverage_urgency(pos, diagnosis, structural_needs), min(costs))
            )
        if not fills:
            return 0.0
        best_rank = min(r for r, _ in fills)
        return float(min(c for r, c in fills if r == best_rank))

    info = funding_info or {}
    skip_needs = {"perfect_buy_daily", "perfect_buy"}
    others = [
        float(g.get("cost") or 0)
        for g in (info.get("all_gap_costs") or info.get("gap_costs") or [])
        if not (excl and g.get("position") == excl)
        and g.get("need") not in skip_needs
        and float(g.get("cost") or 0) > 0
    ]
    others.sort()
    return float(others[0]) if others else 0.0


def rival_demand_for_position(
    rivals: list[dict[str, Any]],
    position: str,
    *,
    market_mode: str = "auction",
) -> list[dict[str, Any]]:
    if (market_mode or "auction") == "fixed":
        return []
    demand = []
    for r in rivals:
        gaps = r.get("position_gaps") or []
        if position in gaps:
            demand.append({
                "team_id": r.get("team_id"),
                "team_name": r.get("team_name"),
                "rank": r.get("rank"),
            })
    return demand


def wait_risk(
    o: dict[str, Any],
    rivals: list[dict[str, Any]],
    *,
    fills_need: bool,
    market_mode: str = "auction",
) -> str:
    if (market_mode or "auction") == "fixed":
        return "low"
    demand = rival_demand_for_position(rivals, o.get("position") or "", market_mode=market_mode)
    top_demand = sum(1 for d in demand if int(d.get("rank") or 99) <= 3)
    score = 0
    score += min(3, len(demand))
    score += min(2, top_demand)
    if o.get("trend") == "up" or (o.get("delta_5d") is not None and float(o["delta_5d"]) > 0.05):
        score += 1
    if o.get("external", {}).get("is_chollo_ext") or o.get("category") == "chollo_economico":
        score += 1
    if fills_need:
        score += 1
    if score >= 5:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


# Fase de jornada del ciclo en curso; la fija data_engine al empezar cada liga.
# Lejos del cierre el mercado construye patrimonio; pegado al cierre lo único
# que importa es quién puntúa esta jornada.
_MATCHDAY_PHASE = "ventana_compra"
CLOSING_PHASES = ("confirmacion", "visperas", "dia_partido")


def set_matchday_phase(phase: str | None) -> None:
    global _MATCHDAY_PHASE
    _MATCHDAY_PHASE = str(phase or "ventana_compra")


def is_closing_phase() -> bool:
    return _MATCHDAY_PHASE in CLOSING_PHASES


def priority_score_buy(item: dict[str, Any]) -> int:
    score = 0
    # Cerca del cierre, ser objetivo del board a tres semanas vale la mitad
    board_w = 0.5 if is_closing_phase() else 1.0
    overstock_blocks = bool(item.get("overstocked")) and not (
        item.get("fills_coverage_gap")
        or item.get("fills_structural")
        or item.get("upgrade_worth_buy")
    )
    # Jugador clave en mercado del día: por encima de cualquier parche
    if not overstock_blocks and item.get("is_key_market") and _is_daily_market_item(item):
        score += int(140 * board_w)
    elif not overstock_blocks and item.get("is_primary_target") and _is_daily_market_item(item):
        score += int(100 * board_w)
    elif not overstock_blocks and item.get("is_board_objective") and _is_daily_market_item(item):
        score += int(55 * board_w)
    if item.get("fills_need"):
        score += 35
    if item.get("fills_structural"):
        score += 20
    if item.get("fills_coverage_gap"):
        score += 22
    if item.get("on_daily_market") and item.get("fills_coverage_gap"):
        score += 10
    if overstock_blocks:
        score -= 80
    elif item.get("line_already_covered") and not item.get("is_upgrade"):
        score -= 40
    elif item.get("is_upgrade"):
        score += 15
        if item.get("upgrade_worth_buy"):
            score += 20
    risk = item.get("wait_risk") or item.get("risk") or "low"
    score += {"high": 25, "medium": 15, "low": 5}.get(str(risk), 5)
    bf = item.get("budget_fit") or "blocked"
    score += {"comfortable": 20, "tight": 10, "stretch": 0, "blocked": -40, "funding": 8}.get(str(bf), 0)
    tier = item.get("target_tier") or target_tier_from_budget_fit(str(bf))
    if tier == "aspirational":
        score -= 15
    elif tier == "stretch":
        score -= 5
    if item.get("crowds_out_gaps"):
        score -= 55
        cost = _money(item.get("cost") or item.get("bid") or item.get("price"))
        if cost >= 8_000_000:
            score -= 20  # caro que deja una línea needy sin margen
    elif item.get("leaves_gap_budget"):
        score += 18
    score += min(15, int(item.get("rival_demand") or 0) * 5)
    if item.get("improves_owned"):
        score += 20
    # Producción FF / Mister (castiga muestra corta)
    sample_thin = bool(item.get("sample_thin"))
    if sample_thin:
        score -= 8
    prod = _production_score(item)
    if prod is not None:
        score += int(min(25, prod / 4))
        if prod < 35:
            score -= 10
    ff = _ff_avg(item)
    if ff is not None and not sample_thin:
        scale = _avg_scale(item)
        score += int(min(12, ff * 1.5 * (8.0 / scale)))
        if ff < scale_threshold(3.5, scale) and _money(item.get("price") or item.get("market_value")) >= 5_000_000:
            score -= 12
    if (item.get("is_top_ff") or (item.get("external") or {}).get("is_top_ff")) and not sample_thin:
        score += 8
    # Capa puntos adicional cuando discrimina
    avg = item.get("mister_avg")
    try:
        if avg is not None and float(avg) > 0 and not sample_thin:
            scale = _avg_scale(item)
            score += min(15, int(float(avg) * 2 * (8.0 / scale)))
    except (TypeError, ValueError):
        pass
    trend = item.get("points_trend") or item.get("trend")
    if trend == "up":
        score += 8
    elif trend == "down":
        score -= 6
    # Puntos esperados de la jornada: lo que realmente se compra
    score += _xpts_bonus(item)
    # Capacidad de trueque / activo revendible
    score += int(min(20, trade_asset_score(item) / 2.5))
    return score


def _xpts_bonus(item: dict[str, Any]) -> int:
    """
    Aporte de los puntos esperados al score de compra, normalizado a escala Mixto
    para que Premier (escala ~16) no infle el ranking.

    En las fases pegadas al cierre el xPts (ya ajustado por rival y localía)
    pasa a mandar: se compra para puntuar el sábado, no para revender en tres
    semanas.
    """
    xpts = item.get("xpts")
    try:
        x = float(xpts) if xpts is not None else None
    except (TypeError, ValueError):
        x = None
    if x is None:
        return 0
    closing = is_closing_phase()
    scale = _avg_scale(item)
    normalized = x * (8.0 / scale) if scale else x
    bonus = int(min(80 if closing else 30, normalized * (10 if closing else 4)))
    p_play = item.get("xpts_p_play")
    try:
        if p_play is not None and float(p_play) < 0.35:
            bonus -= 50 if closing else 20  # comprar a quien no juega es tirar la jornada
    except (TypeError, ValueError):
        pass
    return bonus


def trade_asset_score(item: dict[str, Any]) -> float:
    """
    Valor como activo de trueque/mejora futura:
    producción por millón (acotada) + flecha de precio + categorías chollo/trading.
    """
    cost = _money(item.get("cost") or item.get("bid") or item.get("price") or item.get("market_value"))
    # Suelo 0.8M para no inflar chollos de 200k por encima de cracks
    price_m = max(cost / 1_000_000.0, 0.8)
    prod = _production_score(item) or 0.0
    ff = _ff_avg(item) or 0.0
    score = min(36.0, float(prod) / price_m) + (float(ff) * 2.0)
    delta = item.get("delta_5d")
    try:
        if delta is not None:
            d = float(delta)
            if d >= 0.05:
                score += 10.0
            elif d >= 0.02:
                score += 5.0
            elif d <= -0.06:
                score -= 8.0
            elif d < 0:
                score -= 3.0
    except (TypeError, ValueError):
        pass
    cats = item.get("categories") or []
    if isinstance(cats, list):
        if "chollo_economico" in cats:
            score += 6.0
        if "especulacion_trading" in cats:
            score += 5.0
        if "titular_garantizado" in cats:
            score += 3.0
    if item.get("is_upgrade"):
        score += 4.0
    if item.get("sample_thin"):
        score -= 6.0
    if item.get("appreciation_play"):
        score += 8.0
    return round(score, 1)


def appreciation_play_score(item: dict[str, Any]) -> tuple[float, list[str]]:
    """
    Puntuación de revalorización: sube de VM + perspectiva de seguir subiendo
    (titularidad / producción / no baja).
    """
    why: list[str] = []
    score = 0.0
    if item.get("gw_out") or (item.get("external") or {}).get("gw_out"):
        return 0.0, ["descartado en la previa"]
    avail = _ext_avail(item)
    if avail in ("injured", "suspended"):
        return 0.0, [f"baja ({avail})"]

    delta = None
    try:
        if item.get("delta_5d") is not None:
            delta = float(item["delta_5d"])
    except (TypeError, ValueError):
        delta = None
    trend = item.get("trend")
    rising = bool(trend == "up" or (delta is not None and delta >= float(
        getattr(config, "APPRECIATION_DELTA_MIN", 0.04)
    )))
    if not rising:
        return 0.0, ["sin revalorización clara"]

    if delta is not None:
        score += min(28.0, float(delta) * 120.0)
        why.append(f"sube {delta * 100:.1f}%")
    elif trend == "up":
        score += 10.0
        why.append("flecha al alza")

    lp = _lineup_pct(item)
    lineup_min = float(getattr(config, "APPRECIATION_LINEUP_MIN", 0.45)) * 100.0
    if lp is not None and lp >= 70:
        score += 14.0
        why.append(f"titular {lp:.0f}%")
    elif lp is not None and lp >= lineup_min:
        score += 8.0
        why.append(f"rotación usable {lp:.0f}%")
    elif item.get("gw_starter") or (item.get("external") or {}).get("gw_starter"):
        score += 10.0
        why.append("titular FF jornada")
    else:
        score -= 8.0
        why.append("poca perspectiva de minutos")

    if avail == "doubt":
        score -= 6.0
        why.append("duda física")

    ptrend = _points_trend(item)
    if ptrend == "up":
        score += 6.0
        why.append("racha puntos ↑")
    elif ptrend == "down":
        score -= 8.0
        why.append("racha puntos ↓")

    # Señal comparable (actual o temporada pasada) para no comprar humo
    sig = comparable_ff_signal(item)
    if sig.get("usable") and sig.get("avg") is not None:
        avg = float(sig["avg"])
        scale = resolve_avg_scale(item)
        if avg >= scale_threshold(5.0, scale):
            score += 10.0
            label = "prev" if sig.get("prior_backed") else "FF"
            why.append(f"{label} {avg:.1f}")
        elif avg >= scale_threshold(3.8, scale):
            score += 4.0
        else:
            score -= 4.0
    elif item.get("sample_thin"):
        score -= 10.0
        why.append("muestra corta sin previa")

    price = _money(item.get("price") or item.get("puja_recomendada") or item.get("cost"))
    max_price = float(getattr(config, "APPRECIATION_MAX_PRICE", 8_000_000))
    if price > max_price:
        score -= 12.0
        why.append(f"caro para flip ({price:,.0f} €)")
    elif 0 < price <= 3_000_000:
        score += 5.0
        why.append("precio manejable")

    if item.get("overstocked") and not item.get("fills_coverage_gap"):
        score -= 6.0
        why.append("línea sobrada")

    cats = item.get("categories") or []
    if isinstance(cats, list) and "especulacion_trading" in cats:
        score += 4.0

    return round(score, 1), why


def is_appreciation_candidate(item: dict[str, Any]) -> bool:
    """¿Candidato a fichar por revalorización (no por hueco/upgrade)?"""
    if not item.get("on_daily_market") and item.get("seller") != "market":
        return False
    if item.get("budget_fit") not in ("comfortable", "tight", None):
        return False
    if item.get("solvency_blocked") or item.get("debt_risk"):
        return False
    min_score = float(getattr(config, "APPRECIATION_MIN_SCORE", 18.0))
    score, _ = appreciation_play_score(item)
    return score >= min_score


def promote_appreciation_plays(
    plan: list[dict[str, Any]],
    *,
    max_buys: int | None = None,
) -> list[dict[str, Any]]:
    """
    Si no hay buy/swap estructural (hueco, upgrade, objetivo, clave),
    promueve wait→buy_now a los mejores revalorizándose del mercado de hoy.
    """
    if not plan:
        return plan
    strong = [
        i
        for i in plan
        if i.get("action") == "buy_now"
        and (
            i.get("fills_coverage_gap")
            or i.get("fills_structural")
            or i.get("fills_need")
            or i.get("is_upgrade")
            or i.get("upgrade_worth_buy")
            or i.get("is_key_market")
            or i.get("is_primary_target")
            or i.get("is_board_objective")
            or i.get("appreciation_play")
        )
    ]
    if strong:
        return plan

    cap = int(
        max_buys
        if max_buys is not None
        else getattr(config, "APPRECIATION_MAX_BUYS", 2)
    )
    if cap <= 0:
        return plan

    cands: list[tuple[float, dict[str, Any], list[str]]] = []
    for item in plan:
        if item.get("action") not in ("wait", "buy_now"):
            continue
        if not bool(item.get("on_daily_market")):
            continue
        if item.get("budget_fit") not in ("comfortable", "tight"):
            continue
        if item.get("solvency_blocked") or item.get("debt_risk"):
            continue
        # Línea muy sobrada: solo si el precio es flip barato
        price = _money(item.get("bid") or item.get("cost") or item.get("price"))
        if item.get("overstocked") and not item.get("fills_coverage_gap"):
            if price > 4_000_000:
                continue
        score, bits = appreciation_play_score(item)
        min_score = float(getattr(config, "APPRECIATION_MIN_SCORE", 18.0))
        if score < min_score:
            continue
        cands.append((score, item, bits))

    cands.sort(key=lambda x: (-x[0], _money(x[1].get("price"))))
    promoted_ids: set[str] = set()
    out: list[dict[str, Any]] = []
    used_pos: set[str] = set()
    for score, item, bits in cands:
        if len(promoted_ids) >= cap:
            break
        pid = str(item.get("player_id") or item.get("id") or "")
        pos = str(item.get("position") or "")
        if pid and pid in promoted_ids:
            continue
        # Diversificar: como máximo uno por línea en el lote de revalorización
        if pos and pos in used_pos:
            continue
        row = dict(item)
        row["action"] = "buy_now"
        row["appreciation_play"] = True
        row["urgency"] = row.get("urgency") or "medium"
        row["priority_score"] = int(row.get("priority_score") or 0) + int(min(35, score))
        tip = (
            "revalorización: sube de valor con buena perspectiva — "
            "fichar para vender más caro o como activo oportunidad"
        )
        detail = "; ".join(bits[:4])
        why = (row.get("why") or "").strip()
        row["why"] = f"{tip} ({detail}); {why}" if why else f"{tip} ({detail})"
        cats = list(row.get("categories") or [])
        if "especulacion_trading" not in cats:
            cats.insert(0, "especulacion_trading")
        row["categories"] = cats
        if pid:
            promoted_ids.add(pid)
        if pos:
            used_pos.add(pos)
        out.append(row)

    if not promoted_ids:
        return plan

    # Sustituir filas promovidas; el resto igual
    replaced: list[dict[str, Any]] = []
    seen: set[str] = set()
    by_id = {str(r.get("player_id") or r.get("id") or ""): r for r in out}
    for item in plan:
        pid = str(item.get("player_id") or item.get("id") or "")
        if pid and pid in by_id and pid not in seen:
            replaced.append(by_id[pid])
            seen.add(pid)
        elif not (pid and pid in promoted_ids):
            replaced.append(item)
    for pid, row in by_id.items():
        if pid and pid not in seen:
            replaced.append(row)
    return replaced


def is_key_market_candidate(
    o: dict[str, Any],
    *,
    is_primary_obj: bool,
    is_objective: bool,
    on_daily: bool,
    gw_out: bool,
    real_starter: bool,
    fills_gap: bool,
) -> bool:
    """
    Jugador clave del mercado de hoy: objetivo del ideal, o crack/top que cubre hueco.
    """
    if not on_daily or gw_out:
        return False
    # Línea sobrada sin gap real: no elevar a clave (salvo upgrade que renta)
    if (
        o.get("overstocked")
        and not o.get("fills_coverage_gap")
        and not o.get("fills_structural")
        and not o.get("upgrade_worth_buy")
    ):
        return False
    if is_primary_obj:
        return True
    if is_objective and (real_starter or bool(o.get("is_top_ff"))):
        return True
    # Upgrade rentable en línea sobrada: clave si es titular/top
    if o.get("upgrade_worth_buy") and (real_starter or bool(o.get("is_top_ff"))):
        return True
    # GK ya cubierto / sin tándem ni parche real: no elevar a clave por producción
    if (o.get("position") or "") == "GK":
        if o.get("line_already_covered") and not o.get("is_upgrade"):
            return False
        if not o.get("fills_coverage_gap") and not o.get("fills_structural"):
            return False
    if not fills_gap:
        return False
    if not real_starter and not o.get("is_top_ff"):
        return False
    prod = _production_score(o)
    ff = _ff_avg(o)
    try:
        pts = float(o.get("ff_mister_points") or 0)
    except (TypeError, ValueError):
        pts = 0.0
    if o.get("is_top_ff") and not o.get("sample_thin"):
        return True
    if prod is not None and float(prod) >= 65 and not o.get("sample_thin"):
        return True
    if ff is not None and float(ff) >= 6.0 and not o.get("sample_thin"):
        return True
    if pts >= 120 and real_starter:
        return True
    return False


def priority_score_sell(item: dict[str, Any]) -> int:
    """
    Prioriza ventas que mejoran el once: banquillo caro, baja producción,
    plan de caja diferida (2 ciclos de la liga); protege titulares TOP / once fiable.
    """
    score = 0
    reason = item.get("sell_reason") or ""
    score += {
        "expensive_bench": 42,
        "low_minutes": 40,
        "low_production": 36,
        "fund_buy": 32,
        "fund_target": 44,
        "free_slot": 48,
        "injured_covered": 28,
        "surplus_to_demand": 22,
        "form_drop": 16,
    }.get(reason, 10)

    if item.get("budget_fit") == "funding":
        score += 12
    demand = int(item.get("rival_demand") or 0)
    score += min(12, demand * 4)

    sell_risk = item.get("sell_risk") or item.get("wait_risk") or "medium"
    if sell_risk == "high":
        score -= 18
    elif sell_risk == "low":
        score += 5

    xi = item.get("xi_impact") or "soft"
    if xi == "safe":
        score += 10
    elif xi == "risk":
        score -= 30

    lineup = item.get("lineup_pct")
    try:
        lp = float(lineup) if lineup is not None else None
    except (TypeError, ValueError):
        lp = None
    if lp is not None:
        if lp < 40:
            score += 18
        elif lp < 60:
            score += 10
        elif lp >= 80:
            score -= 22

    prod = item.get("production_score")
    try:
        pr = float(prod) if prod is not None else None
    except (TypeError, ValueError):
        pr = None
    if pr is not None:
        if pr < 35:
            score += 16
        elif pr < 45:
            score += 8
        elif pr >= 60:
            score -= 16

    ff = item.get("ff_mister_avg")
    try:
        ffv = float(ff) if ff is not None else None
    except (TypeError, ValueError):
        ffv = None
    if ffv is not None:
        scale = resolve_avg_scale(item)
        if ffv < scale_threshold(3.5, scale):
            score += 10
        elif ffv >= scale_threshold(5.2, scale):
            score -= 12

    price = _money(item.get("price"))
    if price >= 5_000_000 and (lp is None or lp < 65):
        score += 12  # dinero estancado fuera del once
    if price >= 8_000_000 and (pr is not None and pr < 45):
        score += 8
    if item.get("plays_little"):
        score += 14
        if price >= 4_000_000:
            score += 8

    if item.get("is_top_ff") or item.get("keep_if_rank_top"):
        score -= 40
    if item.get("is_useful_patch"):
        score -= 20  # conservar versatilidad / fondo de armario

    return int(score)


def priority_score_clause(item: dict[str, Any]) -> int:
    score = priority_score_buy(item)
    if item.get("clause_known"):
        score += 10
    owner_rank = int(item.get("owner_rank") or 99)
    if owner_rank <= 3:
        score += 10
    return score


def xi_owned_ids(recommended_xi: dict[str, Any] | None) -> set[str]:
    ids: set[str] = set()
    for row in (recommended_xi or {}).get("xi") or []:
        pid = row.get("player_id") or row.get("id")
        if pid:
            ids.add(str(pid))
    return ids


def reconcile_avoid_conflicts(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Si un jugador está en avoid (lesión/sanción/ROI), no puede a la vez
    ser buy/wait/scout ni el objetivo de un swap de liquidez.
    """
    if not plan:
        return plan
    avoid_ids = {
        str(a.get("player_id") or "")
        for a in plan
        if a.get("action") == "avoid" and a.get("player_id")
    }
    if not avoid_ids:
        return plan

    out: list[dict[str, Any]] = []
    for item in plan:
        pid = str(item.get("player_id") or "")
        action = item.get("action")
        if pid in avoid_ids and action in ("buy_now", "wait", "scout", "clause_bid"):
            continue
        if action == "sell":
            funds = str(item.get("funds_for") or "")
            up = item.get("upgrade_profile") if isinstance(item.get("upgrade_profile"), dict) else {}
            up_id = str(up.get("player_id") or up.get("id") or "")
            if funds in avoid_ids or up_id in avoid_ids:
                # Quitar el chase del lesionado; si el sell era solo liquidez para él, degradar
                row = dict(item)
                row.pop("funds_for", None)
                row.pop("funds_for_name", None)
                row.pop("upgrade_profile", None)
                if row.get("sell_reason") == "liquidity_slot":
                    why = (row.get("why") or "").strip()
                    note = "objetivo de swap no disponible (lesión/sanción) — no perseguir"
                    if note not in why:
                        row["why"] = f"{note}; {why}" if why else note
                    # Mantener venta solo si sigue siendo liquidez genérica útil
                    row["sell_reason"] = "surplus_to_demand"
                out.append(row)
                continue
        out.append(item)
    return out


def swap_covers(balance: float, slot_vm: float, bid: float) -> bool:
    """¿Saldo usable + cobro VM del listado cubre la puja del upgrade?"""
    return float(balance or 0) + float(slot_vm or 0) >= float(bid or 0)


def promote_funded_swaps(
    plan: list[dict[str, Any]],
    *,
    balance: float,
    hours_to_jornada: float | None = None,
    cash_lag_hours: float | None = None,
) -> None:
    """Si el swap cierra (caja + VM del slot) y el cobro llega a tiempo, buy_now."""
    slots = [a for a in plan if a.get("action") == "sell" and a.get("sell_reason") == "liquidity_slot"]
    if not slots:
        return
    timely = sells_settle_before_d1(
        hours_to_jornada=hours_to_jornada,
        cash_lag_hours=cash_lag_hours,
    )
    by_target: dict[str, dict[str, Any]] = {}
    for s in slots:
        tid = str(s.get("funds_for") or "")
        if tid and tid not in by_target:
            by_target[tid] = s
    if not by_target:
        return
    for item in plan:
        if item.get("action") not in ("wait", "scout"):
            continue
        if not item.get("on_daily_market") and item.get("seller") != "market":
            continue
        if item.get("solvency_blocked"):
            continue
        # Nunca promover swap/buy de lesionados (evita contradicción con avoid)
        avail = str(
            (item.get("external") or {}).get("availability")
            or item.get("availability")
            or ""
        ).lower()
        if (
            item.get("injury")
            or avail in ("injured", "suspended")
            or item.get("gw_out")
            or (item.get("external") or {}).get("gw_out")
        ):
            continue
        pid = str(item.get("player_id") or "")
        slot = by_target.get(pid)
        if not slot:
            continue
        bid = _money(item.get("bid") or item.get("acquisition_cost") or item.get("price"))
        if bid <= 0:
            continue
        slot_vm = _money(slot.get("price") or slot.get("expected_proceeds"))
        if not swap_covers(balance, slot_vm, bid):
            continue
        if _money(balance) >= bid:
            continue
        if not timely:
            why = (item.get("why") or "").strip()
            note = "swap cubierto con listado, pero el cobro no llega antes del pitido"
            if note not in why:
                item["why"] = f"{why}; {note}" if why else note
            continue
        item["action"] = "buy_now"
        item["affordable"] = True
        item["budget_fit"] = "funding"
        item["swap_funded"] = True
        item["funds_from"] = (slot or {}).get("player_id")
        item["funds_from_name"] = (slot or {}).get("name")
        why = (item.get("why") or "").strip()
        swap_why = (
            f"swap operable: caja + VM de {(slot or {}).get('name') or 'listado'} "
            f"cubren {bid:,.0f} €"
        )
        item["why"] = f"{swap_why}; {why}" if why else swap_why


def _liquidity_prod(p: dict[str, Any] | None) -> float:
    if not p:
        return 0.0
    for key in ("ep_score", "production_score", "xpts"):
        try:
            if p.get(key) is not None:
                return float(p[key])
        except (TypeError, ValueError):
            pass
    ff = _ff_avg(p)
    if ff is not None:
        return float(ff) * 10.0
    prod = _production_score(p)
    return float(prod or 0)


def _bench_not_xi(
    squad: list[dict[str, Any]],
    xi_ids: set[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in squad:
        pid = str(p.get("id") or "")
        if not pid:
            continue
        if xi_ids and pid in xi_ids:
            continue
        if not xi_ids and _is_reliable_starter(p) and p.get("in_lineup"):
            continue
        out.append(p)
    return out


def pick_funding_slot(
    bench: list[dict[str, Any]],
    *,
    need_cash: float,
    prefer_pos: str | None = None,
) -> dict[str, Any] | None:
    """
    Slot de banquillo (fuera del XI) que hace viable el swap.
    Si el más débil de la línea cubre, se lista ese; si no, el más barato que sí cubra.
    """
    if not bench:
        return None

    def vm(p: dict[str, Any]) -> float:
        return _money(p.get("price") or p.get("market_value"))

    same = [
        p for p in bench
        if prefer_pos and (p.get("position") or "") == prefer_pos
    ]
    pools = [same, bench] if same else [bench]
    need = max(0.0, float(need_cash or 0))
    for pool in pools:
        weakest = sorted(pool, key=lambda p: (_liquidity_prod(p), vm(p)))
        if need <= 0:
            return weakest[0] if weakest else None
        if weakest and vm(weakest[0]) >= need:
            return weakest[0]
        covering = [p for p in pool if vm(p) >= need]
        if covering:
            covering.sort(key=lambda p: (vm(p), _liquidity_prod(p)))
            return covering[0]
    return None


def _upgrade_profile_worth(
    cand: dict[str, Any],
    ref: dict[str, Any] | None,
    cost: float,
) -> bool:
    """ΔEP/producción claro vs titular o banquillo de la línea, con ROI de la puja."""
    if ref is None:
        return True

    # Sin muestra actual ni previa fiable no se afirma que sea mejor por media/EP
    if lacks_comparable_sample(cand):
        return False
    cand_q = quality_for_compare(cand)
    ref_q = quality_for_compare(ref)
    # Preferir calidad comparable; si ambos a 0, caer a EP/prod crudo
    if cand_q > 0 or ref_q > 0:
        delta = cand_q - ref_q
    else:
        delta = _liquidity_prod(cand) - _liquidity_prod(ref)
    if delta < 6.0:
        return False
    roi = delta / max(cost / 1_000_000.0, 0.4)
    if roi < 1.2:
        return False
    # Anotar si la comparación va por temporada pasada
    sig = comparable_ff_signal(cand)
    if sig.get("prior_backed"):
        cand["value_note"] = (
            f"comparado con temp. pasada ({float(sig['avg']):.1f} · {int(sig['apps'])} PJ)"
        )
        cand["prior_backed"] = True
    return True


def _owned_line_ref(
    squad: list[dict[str, Any]],
    position: str,
    xi_ids: set[str],
) -> dict[str, Any] | None:
    line = [p for p in squad if (p.get("position") or "") == position]
    if not line:
        return None
    in_xi = [p for p in line if str(p.get("id") or "") in xi_ids]
    pool = in_xi or line
    return min(pool, key=_liquidity_prod)


def collect_upgrade_profiles(
    *,
    squad: list[dict[str, Any]],
    xi_ids: set[str],
    market_opportunities: list[dict[str, Any]] | None,
    target_board: dict[str, Any] | None,
    balance: float,
) -> list[dict[str, Any]]:
    """Perfiles que merecen persecución (mercado de hoy o scout)."""
    owned = {str(p.get("id") or "") for p in squad if p.get("id")}
    profiles: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_prof(raw: dict[str, Any], *, on_daily: bool, source: str) -> None:
        pid = str(raw.get("id") or raw.get("player_id") or "")
        pos = raw.get("position") or "MF"
        if pid and pid in owned:
            return
        # No perseguir lesionados / fuera de previa (evitar swap + avoid a la vez)
        avail = str(
            (raw.get("external") or {}).get("availability")
            or raw.get("availability")
            or ""
        ).lower()
        if (
            raw.get("injury")
            or avail in ("injured", "suspended")
            or raw.get("gw_out")
            or (raw.get("external") or {}).get("gw_out")
        ):
            return
        cost = _money(
            raw.get("puja_recomendada")
            or raw.get("price")
            or raw.get("buy_price")
            or raw.get("market_value")
        )
        if cost <= 0:
            return
        key = pid or f"{pos}:{int(cost)}:{source}"
        if key in seen:
            return
        ref = _owned_line_ref(squad, pos, xi_ids)
        worth = _upgrade_profile_worth(raw, ref, cost)
        mid = cost
        band = (max(0.0, mid * 0.85), mid * 1.15)
        seen.add(key)
        profiles.append(
            {
                "player_id": pid or None,
                "name": raw.get("name"),
                "position": pos,
                "bid": cost,
                "price_band": [round(band[0], 0), round(band[1], 0)],
                "on_daily_market": on_daily,
                "worth": worth,
                "source": source,
                "ep_score": raw.get("ep_score") or _liquidity_prod(raw),
                "ref_name": (ref or {}).get("name"),
                "ref_ep": _liquidity_prod(ref) if ref else None,
            }
        )

    for o in market_opportunities or []:
        if not (o.get("is_upgrade") or o.get("fills_structural") or o.get("fills_need")):
            continue
        add_prof(o, on_daily=bool(o.get("on_daily_market")), source="market")

    board = target_board or {}
    for t in list(board.get("primary_targets") or []) + list(board.get("aspirational_targets") or []):
        add_prof(
            t,
            on_daily=bool(t.get("on_daily_market")),
            source="board",
        )
    for b in ((board.get("moves") or {}).get("buy") or []):
        add_prof(b, on_daily=bool(b.get("on_daily_market")), source="board")

    profiles.sort(
        key=lambda p: (
            0 if p.get("worth") else 1,
            0 if p.get("on_daily_market") else 1,
            -float(p.get("ep_score") or 0),
        )
    )
    return profiles


def resolve_liquidity_slots(
    *,
    squad: list[dict[str, Any]],
    recommended_xi: dict[str, Any] | None,
    market_opportunities: list[dict[str, Any]] | None,
    target_board: dict[str, Any] | None,
    balance: float,
    sale_limit: int = 5,
) -> dict[str, Any]:
    """
    Elige listados de liquidez: cubren swaps de perfiles que rentan
    y rellenan con los más débiles fuera del XI, sin superar sale_limit.
    """
    xi_ids = xi_owned_ids(recommended_xi)
    bench = _bench_not_xi(squad, xi_ids)
    limit = max(1, int(sale_limit or 5))
    profiles = collect_upgrade_profiles(
        squad=squad,
        xi_ids=xi_ids,
        market_opportunities=market_opportunities,
        target_board=target_board,
        balance=balance,
    )
    chosen: list[dict[str, Any]] = []
    chosen_ids: set[str] = set()
    funded: list[dict[str, Any]] = []
    avoided: list[dict[str, Any]] = []

    for prof in profiles:
        if not prof.get("worth"):
            avoided.append({**prof, "avoid_reason": "delta_ep_or_roi"})
            continue
        need = max(0.0, float(prof["bid"]) - float(balance or 0))
        slot = pick_funding_slot(
            bench, need_cash=need, prefer_pos=str(prof.get("position") or "")
        )
        if slot is None:
            avoided.append({**prof, "avoid_reason": "inabordable_sin_romper_xi"})
            continue
        sid = str(slot.get("id") or "")
        prof = {
            **prof,
            "slot_id": sid,
            "slot_name": slot.get("name"),
            "slot_vm": _money(slot.get("price") or slot.get("market_value")),
            "pursue": True,
        }
        funded.append(prof)
        if sid and sid not in chosen_ids:
            chosen.append(slot)
            chosen_ids.add(sid)
        if len(chosen) >= limit:
            break

    weakest = sorted(bench, key=lambda p: (_liquidity_prod(p), _money(p.get("price"))))
    min_listed = min(2, limit, len(bench))
    for p in weakest:
        if len(chosen) >= limit:
            break
        if len(chosen) >= min_listed:
            break
        pid = str(p.get("id") or "")
        if not pid or pid in chosen_ids:
            continue
        chosen.append(p)
        chosen_ids.add(pid)

    return {
        "xi_ids": xi_ids,
        "slots": chosen,
        "slot_ids": chosen_ids,
        "profiles": funded,
        "avoided": avoided,
        "sale_limit": limit,
    }


def build_sell_opportunities(
    me: dict[str, Any],
    diagnosis: dict[str, Any],
    rivals: list[dict[str, Any]],
    *,
    price_series: dict[str, list[float]] | None = None,
    delta_fn=None,
    market_opportunities: list[dict[str, Any]] | None = None,
    points_phase: str = "preseason",
    diagnostico_plantilla: dict[str, Any] | None = None,
    target_board: dict[str, Any] | None = None,
    funding_info: dict[str, Any] | None = None,
    recommended_xi: dict[str, Any] | None = None,
    league_economy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Ventas orientadas a estrategia Fantasy:
    máxima puntuación, titularidades, once fiable y no estancar valor en banquillo.
    """
    price_series = price_series or {}
    squad = list(me.get("squad") or [])
    balance = _money(me.get("balance"))
    rank = int(me.get("rank") or 99)
    mins = _mins()
    diag = diagnostico_plantilla or {}
    finance = diag.get("financiero") or {}
    bench_info = finance.get("bench_inflated") or {}
    bench_inflated = bool(
        bench_info.get("status") == "alert" or bench_info.get("ok") is False
    )
    # Solo usar la lista del diagnóstico si el banquillo está realmente inflado
    expensive_bench_ids = (
        {
            str(p.get("id"))
            for p in (bench_info.get("players") or [])
            if p.get("id") is not None
        }
        if bench_inflated
        else set()
    )

    critical_pos = {
        a["position"] for a in diagnosis.get("alerts", []) if a.get("level") == "critical"
    }
    needy = {
        pos for pos, info in diagnosis.get("by_position", {}).items()
        if info.get("status") in ("critical", "warning")
    }
    funding = funding_info or estimate_gap_funding(
        diag.get("structural_needs") or [],
        market_opportunities,
        balance,
        top_n=3,
    )
    cash_tight = bool(funding.get("cash_tight"))
    funding_pressure = float(funding.get("funding_shortfall") or 0) > 0
    cycle_h = funding.get("cycle_hours")
    lag_h = funding.get("cash_lag_hours")
    if lag_h is None:
        lag_h = _cycle_hours_value(cycle_h) * 2
    lag_h = int(round(float(lag_h)))
    gap_labels = ", ".join(
        str(p) for p in (funding.get("positions") or []) if p
    ) or "carencias"

    def _cash_phrase(amount: float, *, deferred: bool = False) -> str:
        return sell_cash_phrase(amount, deferred=deferred, cycle_hours=cycle_h)

    sells: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: dict[str, Any]) -> None:
        pid = str(item["player_id"])
        if pid in seen:
            prev = next(x for x in sells if str(x["player_id"]) == pid)
            urg = {"high": 0, "medium": 1, "low": 2}
            # Preferir el motivo de mayor score estratégico
            if urg.get(item.get("urgency"), 9) < urg.get(prev.get("urgency"), 9):
                sells.remove(prev)
                seen.discard(pid)
            elif (item.get("_pref") or 0) > (prev.get("_pref") or 0):
                sells.remove(prev)
                seen.discard(pid)
            else:
                return
        seen.add(pid)
        sells.append(item)

    def base_item(p: dict[str, Any], *, reason: str, why: str, urgency: str, sell_risk: str) -> dict[str, Any]:
        pid = str(p.get("id") or "")
        pos = p.get("position") or "MF"
        price = _money(p.get("price") or p.get("market_value"))
        lineup = _lineup_pct(p)
        ff = _ff_avg(p)
        prod = _production_score(p)
        demand = rival_demand_for_position(rivals, pos)
        xi = _xi_impact_if_sold(squad, p)
        keep_top = rank <= 2 and _is_star(p) and _ext_avail(p) not in ("injured", "suspended")
        item = {
            "player_id": pid,
            "name": p.get("name"),
            "position": pos,
            "action": "sell",
            "sell_reason": reason,
            "bid": None,
            "wait_risk": "low",
            "sell_risk": sell_risk,
            "urgency": urgency,
            "why": why,
            "rival_demand": len(demand),
            "rival_targets": demand[:3],
            "budget_fit": "funding" if cash_tight else "comfortable",
            "keep_if_rank_top": keep_top,
            "price": price,
            "lineup_pct": lineup,
            "ff_mister_avg": ff,
            "production_score": prod,
            "is_top_ff": _is_top_ff(p),
            "xi_impact": xi,
            "is_useful_patch": _is_useful_patch(p),
            "in_lineup": bool(p.get("in_lineup")),
            "plays_little": _plays_little(p),
            "recent_minutes": _recent_minutes(p),
            "funding_shortfall": funding.get("funding_shortfall"),
            "funding_target": funding.get("funding_target"),
            **sell_settlement_fields(price, cycle_hours=cycle_h),
        }
        # Tip de urgencia: liquidez inmediata vía rescisión (no sustituye listar al VM)
        if urgency == "high" or reason in ("fund_buy", "fund_target"):
            item["instant_alt"] = rescind_instant_alt(price)
        return item

    squad_value = sum(_money(p.get("price") or p.get("market_value")) for p in squad) or 1.0

    for p in squad:
        pid = str(p.get("id") or "")
        if not pid:
            continue
        pos = p.get("position") or "MF"
        price = _money(p.get("price") or p.get("market_value"))
        avail = _ext_avail(p)
        lineup = _lineup_pct(p)
        rating = _fotmob_rating(p)
        healthy = _healthy_count(squad, pos)
        starters = _starter_count(squad, pos)
        min_need = mins.get(pos, 2)
        if avail in ("injured", "suspended"):
            covered_if_sold = healthy >= min_need
        else:
            covered_if_sold = healthy - 1 >= min_need

        demand = rival_demand_for_position(rivals, pos)
        top_demand = [d for d in demand if int(d.get("rank") or 99) <= 3]
        delta = None
        if delta_fn:
            delta = delta_fn(pid, price, price_series)

        is_star = _is_star(p)
        is_starter = _is_reliable_starter(p)
        plays_little = _plays_little(p)
        useful_patch = _is_useful_patch(p)
        keep_top = rank <= 2 and is_star and avail not in ("injured", "suspended")
        xi = _xi_impact_if_sold(squad, p)
        ff = _ff_avg(p)
        prod = _production_score(p)
        avg = _mister_avg(p)
        ptrend = _points_trend(p)
        recent_mins = _recent_minutes(p)
        protect_depth = protect_depth_if_sold(squad, p)
        starters_left = starters_after_sale(squad, p)

        # Solo proteger si es titular real y la línea quedaría bajo el suelo
        protect_xi = xi == "risk" and is_starter and avail not in ("injured", "suspended")
        # Conservar profundidad (titulares reales / cupo) salvo presión de caja multi-gap
        protect_depth_soft = protect_depth and not funding_pressure
        # Conservar parches útiles (versatilidad) salvo financiar crítico
        protect_patch = useful_patch and not (cash_tight and bool(critical_pos))

        # 1) Banquillo caro / valor estancado fuera del once real (≥3M o ≥7% plantilla)
        min_bench_price = max(3_000_000.0, squad_value * 0.07)
        bench_flag = (
            pid in expensive_bench_ids
            or (
                not is_starter
                and price >= min_bench_price
                and (prod is None or prod < 52)
                and not useful_patch
            )
        )
        if (
            bench_flag
            and covered_if_sold
            and not keep_top
            and not protect_xi
            and not protect_patch
            and not protect_depth_soft
            and price > 0
        ):
            urg = "high" if bench_inflated or price >= 6_000_000 or funding_pressure else "medium"
            bits = [
                f"fuera del once real (titularidad {_lineup_titularidad_label(p, lineup)})",
                _cash_phrase(price),
            ]
            if p.get("in_lineup"):
                bits.insert(0, "en tu once Mister pero sin titularidad real")
            if funding_pressure:
                bits.append(f"plan de caja diferida para {gap_labels}")
            if protect_depth and funding_pressure:
                bits.append(f"profundidad justa ({starters_left} titulares) pero falta caja")
            if ff is not None:
                bits.append(f"FF {ff:.1f}")
            item = base_item(
                p,
                reason="expensive_bench",
                why="; ".join(bits),
                urgency=urg,
                sell_risk="high" if protect_depth else ("low" if xi == "safe" else "medium"),
            )
            item["_pref"] = 50
            add(item)

        # 1b) Pocos minutos / titularidad baja a precio relevante
        if (
            plays_little
            and price >= 3_000_000
            and covered_if_sold
            and not keep_top
            and not protect_xi
            and not protect_patch
            and not protect_depth_soft
            and not is_star
        ):
            bits = []
            if lineup is not None:
                bits.append(f"titularidad {_lineup_titularidad_label(p, lineup)}")
            if recent_mins is not None:
                bits.append(f"{int(recent_mins)}' en últimos partidos")
            if p.get("in_lineup"):
                bits.append("está en tu once fantasy")
            bits.append(_cash_phrase(price, deferred=funding_pressure))
            if funding_pressure:
                bits.append(f"caja justa vs {gap_labels}")
            item = base_item(
                p,
                reason="low_minutes",
                why="; ".join(bits),
                urgency="high" if price >= 5_000_000 or funding_pressure else "medium",
                sell_risk="high" if protect_depth else ("low" if not is_starter else "medium"),
            )
            item["_pref"] = 48
            add(item)

        # 1c) No juega esta jornada (FF posibles alineaciones) — no vender TOP por duda media
        ext = p.get("external") or {}
        gw_out = bool(p.get("gw_out") or ext.get("gw_out"))
        gw_prob = p.get("gw_lineup_prob")
        if gw_prob is None:
            gw_prob = ext.get("gw_lineup_prob")
        try:
            gw_prob_f = float(gw_prob) if gw_prob is not None else None
        except (TypeError, ValueError):
            gw_prob_f = None
        if (
            gw_out
            and price >= 2_500_000
            and covered_if_sold
            and not keep_top
            and not protect_xi
            and not protect_patch
        ):
            opp = p.get("gw_opponent") or ext.get("gw_opponent")
            bits = [
                f"FF jornada: no juega / {int(gw_prob_f) if gw_prob_f is not None else 'bajo'}%"
            ]
            if opp:
                bits.append(f"vs {opp}")
            bits.append(f"{_cash_phrase(price)} si hay mejor opción")
            item = base_item(
                p,
                reason="low_minutes",
                why="; ".join(bits),
                urgency="medium",
                sell_risk="medium" if is_starter else "low",
            )
            item["_pref"] = 46
            add(item)

        # 2) Baja producción a precio alto → reinvertir en puntos
        low_prod = False
        scale = _avg_scale(p)
        if price >= 4_000_000 and not is_star:
            if ff is not None and ff < scale_threshold(4.0, scale):
                low_prod = True
            if prod is not None and prod < 42:
                low_prod = True
            if points_phase == "active" and avg is not None and avg < scale_threshold(4.2, scale):
                low_prod = True
        if (
            low_prod
            and covered_if_sold
            and not keep_top
            and not protect_xi
            and not protect_patch
            and not protect_depth_soft
        ):
            bits = [f"producción floja para {price / 1e6:.1f} M€"]
            if ff is not None:
                bits.append(f"FF {ff:.1f}")
            if prod is not None:
                bits.append(f"score {prod:.0f}")
            if not is_starter:
                bits.append("no asegura titularidad real")
            else:
                bits.append("mejorable vs objetivo de máxima puntuación")
            item = base_item(
                p,
                reason="low_production",
                why="; ".join(bits),
                urgency="medium" if is_starter else "high",
                sell_risk="medium" if is_starter or protect_depth else "low",
            )
            item["_pref"] = 45
            add(item)

        # 3) Lesionado/sancionado con cobertura
        if avail in ("injured", "suspended") and covered_if_sold and price > 0:
            item = base_item(
                p,
                reason="injured_covered",
                why=f"{avail} con cobertura en {pos}; {_cash_phrase(price)} sin romper el once.",
                urgency="medium",
                sell_risk="low",
            )
            item["_pref"] = 40
            add(item)

        # 4) Excedente → vender el de menor impacto (no titulares estrella)
        if (
            healthy > min_need
            and demand
            and not keep_top
            and not protect_xi
            and not protect_patch
            and not protect_depth_soft
        ):
            weak_surplus = (
                not is_starter
                or (lineup is not None and lineup < 75)
                or (prod is not None and prod < 50)
                or not is_star
            )
            starter_floor = _starter_floor(pos)
            starters_after = starters - (1 if is_starter else 0)
            if weak_surplus and starters_after >= starter_floor:
                urg = "high" if top_demand and not is_starter else "medium"
                names = ", ".join(
                    d["team_name"] for d in (top_demand or demand)[:2] if d.get("team_name")
                )
                item = base_item(
                    p,
                    reason="surplus_to_demand",
                    why=(
                        f"Excedente {pos} (sanos {healthy}/{min_need}, titulares {starters}); "
                        f"rivales con gap: {names or 'varios'}."
                    ),
                    urgency=urg,
                    sell_risk="low" if healthy > min_need + 1 and not is_starter else "medium",
                )
                item["_pref"] = 30
                add(item)

        # 5) Financiar carencias multi-gap (prioriza no titulares)
        if cash_tight and needy and covered_if_sold and not keep_top and not protect_xi:
            # Con presión de caja se puede vender aunque profundidad sea justa
            if protect_patch and not critical_pos and not funding_pressure:
                pass
            elif protect_depth_soft:
                pass
            else:
                essential = is_star or is_starter
                weak = (
                    (rating is not None and rating < 6.5)
                    or (lineup is not None and lineup < 75)
                    or not is_starter
                    or plays_little
                )
                if not essential or weak:
                    shortfall = float(funding.get("funding_shortfall") or 0)
                    item = base_item(
                        p,
                        reason="fund_buy",
                        why=(
                            f"Caja justa ({balance:,.0f} €) vs ~{funding.get('funding_target', 0):,.0f} € "
                            f"para {gap_labels}"
                            + (f" (faltan {shortfall:,.0f} €)" if shortfall else "")
                            + f"; {_cash_phrase(price, deferred=True)}."
                        ),
                        urgency="high" if critical_pos or funding_pressure else "medium",
                        sell_risk=(
                            "high"
                            if protect_depth
                            else ("medium" if is_starter or healthy <= min_need + 1 else "low")
                        ),
                    )
                    item["_pref"] = 35
                    add(item)

        # 5b) Financiar primary del target board con mínima pérdida de valor
        primary_targets = list(funding.get("primary_targets") or [])
        if not primary_targets and target_board:
            primary_targets = list(target_board.get("primary_targets") or [])
        for pt in primary_targets:
            if not pt.get("on_daily_market"):
                continue
            need_price = _money(pt.get("price"))
            if need_price <= 0:
                continue
            if need_price > balance:
                continue  # greedy no puja hoy a quien no cabe
            shortfall_pt = max(0.0, need_price - balance)
            if shortfall_pt <= 0:
                continue
            if not covered_if_sold or keep_top or protect_xi or protect_patch:
                continue
            if is_star or (is_starter and (prod is not None and prod >= 55)):
                continue
            # Preferir bajo EP / banquillo / Δ negativo (menor pérdida)
            low_ep = (prod is not None and prod < 45) or (
                ff is not None and ff < scale_threshold(3.8, _avg_scale(p))
            )
            delta_ok = delta is None or float(delta) <= 0.02  # no vender si está subiendo fuerte
            if not (low_ep or plays_little or not is_starter):
                continue
            if not delta_ok and price < 3_000_000:
                continue
            loss_note = (
                f"Δ {float(delta)*100:.0f}%"
                if delta is not None
                else "sin serie Δ"
            )
            item = base_item(
                p,
                reason="fund_target",
                why=(
                    f"Financia objetivo {pt.get('name')} (~{need_price:,.0f} €) en ~{lag_h}h; "
                    f"faltan ~{shortfall_pt:,.0f} €; {_cash_phrase(price, deferred=True)} ({loss_note})"
                ),
                urgency="high" if shortfall_pt >= need_price * 0.35 else "medium",
                sell_risk="low" if not is_starter else "medium",
            )
            item["_pref"] = 42
            item["funds_for"] = pt.get("player_id")
            item["funds_for_name"] = pt.get("name")
            add(item)
            break  # una venta fund_target por jugador owned basta vía add()

        # 6) Forma / tendencia a la baja
        scale = _avg_scale(p)
        form_bad = (lineup is not None and lineup < 50) or (rating is not None and rating < 6.0)
        if points_phase == "active":
            if avg is not None and avg < scale_threshold(4.0, scale):
                form_bad = True
            if ptrend == "down":
                form_bad = True
        if price >= 4_000_000 and (
            (ff is not None and ff < scale_threshold(3.8, scale)) or (prod is not None and prod < 38)
        ):
            form_bad = True
        delta_bad = delta is not None and float(delta) <= -0.08
        if (
            covered_if_sold
            and not keep_top
            and not protect_xi
            and not protect_patch
            and not protect_depth_soft
            and price >= 2_000_000
            and (form_bad or delta_bad)
        ):
            bits = []
            if lineup is not None and lineup < 50:
                bits.append(f"titularidad {_lineup_titularidad_label(p, lineup)}")
            if rating is not None and rating < 6.0:
                bits.append(f"nota {rating}")
            if points_phase == "active" and avg is not None and avg < scale_threshold(4.0, scale):
                bits.append(f"media Mister {avg}")
            if ff is not None and ff < scale_threshold(3.8, scale) and price >= 4_000_000:
                scoring = (p.get("external") or {}).get("ff_scoring") or p.get("ff_scoring") or "FF"
                bits.append(f"{scoring} {ff:.1f} baja para {price/1e6:.1f}M€")
            if ptrend == "down":
                bits.append("tendencia pts ↓")
            if delta_bad:
                bits.append(f"Δprecio {float(delta)*100:.1f}%")
            item = base_item(
                p,
                reason="form_drop",
                why="; ".join(bits) or "Forma/valor a la baja.",
                urgency="low",
                sell_risk="medium",
            )
            item["_pref"] = 20
            add(item)

    economy = league_economy if isinstance(league_economy, dict) else {}
    try:
        sale_limit = int(economy.get("sale_limit") or 5)
    except (TypeError, ValueError):
        sale_limit = 5
    liq = resolve_liquidity_slots(
        squad=squad,
        recommended_xi=recommended_xi,
        market_opportunities=market_opportunities,
        target_board=target_board,
        balance=balance,
        sale_limit=sale_limit,
    )
    profile_by_slot: dict[str, dict[str, Any]] = {}
    for prof in liq.get("profiles") or []:
        sid = str(prof.get("slot_id") or "")
        if sid and sid not in profile_by_slot:
            profile_by_slot[sid] = prof

    for slot in liq.get("slots") or []:
        pid = str(slot.get("id") or "")
        if not pid:
            continue
        prof = profile_by_slot.get(pid)
        price = _money(slot.get("price") or slot.get("market_value"))
        if prof:
            mid = float(prof.get("bid") or 0)
            why = (
                f"Liquidez para swap: {_cash_phrase(price)}. "
                f"Perseguir {prof.get('position')} ~{mid / 1e6:.1f}M"
            )
            if prof.get("name") and not prof.get("on_daily_market"):
                why += f" ({prof.get('name')} aún no listado)"
            elif prof.get("name"):
                why += f" ({prof.get('name')} en mercado)"
            why += f". Si sale, vende y puja (caja + VM cubren ~{mid:,.0f} €)."
        else:
            why = (
                f"Liquidez permanente: {_cash_phrase(price)}. "
                "Listado para oferta CPU al siguiente ciclo."
            )
        item = base_item(
            slot,
            reason="liquidity_slot",
            why=why,
            urgency="high" if prof else "medium",
            sell_risk="low",
        )
        item["_pref"] = 55 if prof else 38
        if prof:
            item["funds_for"] = prof.get("player_id")
            item["funds_for_name"] = prof.get("name")
            item["funds_for_position"] = prof.get("position")
            item["upgrade_profile"] = {
                "position": prof.get("position"),
                "bid": prof.get("bid"),
                "price_band": prof.get("price_band"),
                "on_daily_market": prof.get("on_daily_market"),
            }
        existing = next((x for x in sells if str(x.get("player_id")) == pid), None)
        if existing:
            sells.remove(existing)
            seen.discard(pid)
        add(item)

    scouts: list[dict[str, Any]] = []
    for prof in liq.get("profiles") or []:
        if prof.get("on_daily_market"):
            continue
        pos = prof.get("position") or "?"
        mid = float(prof.get("bid") or 0)
        slot_name = prof.get("slot_name") or "banquillo"
        why = (
            f"Perseguir {pos} ~{mid / 1e6:.1f}M"
            + (f" ({prof.get('name')})" if prof.get("name") else "")
            + f"; listado {slot_name} cubre el swap. Si sale, vende y puja."
        )
        scouts.append(
            {
                "player_id": prof.get("player_id"),
                "name": prof.get("name") or f"Perfil {pos}",
                "position": pos,
                "action": "scout",
                "bid": None,
                "price": mid,
                "acquisition_cost": mid,
                "why": why,
                "urgency": "medium",
                "affordable": swap_covers(balance, float(prof.get("slot_vm") or 0), mid),
                "budget_fit": "funding",
                "queue_role": "upgrade_profile",
                "is_board_objective": True,
                "priority_score": 45,
                "funds_from": prof.get("slot_id"),
                "funds_from_name": prof.get("slot_name"),
                "price_band": prof.get("price_band"),
                "ep_score": prof.get("ep_score"),
            }
        )
    for prof in (liq.get("avoided") or [])[:4]:
        if not prof.get("player_id"):
            continue
        reason = prof.get("avoid_reason") or "no_perseguir"
        if reason == "inabordable_sin_romper_xi":
            why = (
                f"No perseguir {prof.get('name') or prof.get('position')}: "
                "ni vendiendo banquillo de esa línea (sin romper el once) se llega a la banda."
            )
        else:
            why = (
                f"No perseguir {prof.get('name') or prof.get('position')}: "
                "el ΔEP/ROI no compensa la puja."
            )
        scouts.append(
            {
                "player_id": prof.get("player_id"),
                "name": prof.get("name"),
                "position": prof.get("position"),
                "action": "avoid" if reason == "delta_ep_or_roi" else "scout",
                "bid": None,
                "price": prof.get("bid"),
                "why": why,
                "urgency": "low",
                "affordable": False,
                "budget_fit": "blocked",
                "queue_role": "upgrade_profile_drop",
                "priority_score": 8,
            }
        )

    for s in sells:
        s.pop("_pref", None)
        s["priority_score"] = priority_score_sell(s)
    sells.sort(
        key=lambda x: (
            0 if x.get("sell_reason") == "liquidity_slot" else 1,
            -int(x.get("priority_score") or 0),
            0 if x.get("xi_impact") == "safe" else 1,
            -_money(x.get("price")),
        )
    )
    capped = sells[: max(1, sale_limit)]
    return capped + scouts


def _best_owned_reference(
    squad: list[dict[str, Any]],
    position: str,
    *,
    points_phase: str = "preseason",
) -> dict[str, Any] | None:
    candidates = [p for p in squad if (p.get("position") or "") == position]
    if not candidates:
        return None

    def key(p: dict[str, Any]) -> tuple:
        avail = _ext_avail(p)
        healthy = 0 if avail in ("injured", "suspended") or p.get("injury") else 1
        avg = _mister_avg(p) or 0
        pts = _mister_points(p) or 0
        lineup = _lineup_pct(p) or 0
        rating = _fotmob_rating(p) or 0
        prior = _prior_avg(p) or 0
        price = _money(p.get("price") or p.get("market_value"))
        if points_phase == "active":
            return (healthy, avg, pts, lineup, rating, price)
        return (healthy, lineup, rating, prior, avg, price)

    return max(candidates, key=key)


def compute_upgrade_score(
    cand: dict[str, Any],
    ref: dict[str, Any] | None,
    *,
    points_phase: str,
    fills: bool,
    critical_elsewhere: bool,
    balance: float,
    acquisition: float | None,
) -> tuple[float, bool, list[str]]:
    """
    Score adaptativo + improves_owned.
    Puntos son capa adicional (peso ~0 si ambos a 0).
    """
    score = 0.0
    why_extra: list[str] = []
    cand_avg = _mister_avg(cand)
    ref_avg = _mister_avg(ref) if ref else None
    cand_pts = _mister_points(cand)
    ref_pts = _mister_points(ref) if ref else None
    cand_lineup = _lineup_pct(cand)
    ref_lineup = _lineup_pct(ref) if ref else None
    cand_rating = _fotmob_rating(cand)
    ref_rating = _fotmob_rating(ref) if ref else None
    cand_prior = _prior_avg(cand)
    ref_prior = _prior_avg(ref) if ref else None
    ref_avail = _ext_avail(ref) if ref else "unknown"
    market_value = _money(cand.get("market_value") or cand.get("price"))
    ref_price = _money((ref or {}).get("price") or (ref or {}).get("market_value")) if ref else 0
    ptrend = _points_trend(cand)

    if fills:
        score += 28
        why_extra.append("carencia")
    if critical_elsewhere and not fills:
        score -= 22

    # Titularidad
    if cand_lineup is not None and ref_lineup is not None:
        score += (cand_lineup - ref_lineup) / (8.0 if points_phase == "preseason" else 12.0)
    elif cand_lineup is not None and points_phase == "preseason":
        score += cand_lineup / 20.0

    # FotMob: más peso en preseason
    fotmob_w = 10.0 if points_phase == "preseason" else 4.0
    if cand_rating is not None and ref_rating is not None:
        score += (cand_rating - ref_rating) * fotmob_w
    elif cand_rating is not None and points_phase == "preseason":
        score += 6

    # Temporada anterior: preseason o muestra actual corta con previa fiable
    cand_sig = comparable_ff_signal(cand)
    use_prior = points_phase == "preseason" or bool(cand_sig.get("prior_backed"))
    if use_prior and cand_prior is not None:
        if ref_prior is not None:
            score += (cand_prior - ref_prior) * (8 if points_phase == "preseason" else 6)
        else:
            score += min(10, float(cand_prior))
        why_extra.append(
            f"temp. pasada {float(cand_prior):.1f}"
            if cand_sig.get("prior_backed")
            else "prior season"
        )

    # Producción Fútbol Fantasy (actual si ≥5 PJ; si no, temp. pasada)
    ref_sig = comparable_ff_signal(ref) if ref else {}
    cand_ff = float(cand_sig["avg"]) if cand_sig.get("usable") else _ff_avg(cand)
    ref_ff = float(ref_sig["avg"]) if ref_sig.get("usable") else (_ff_avg(ref) if ref else None)
    cand_prod = quality_for_compare(cand) or _production_score(cand)
    ref_prod = (quality_for_compare(ref) if ref else 0) or (_production_score(ref) if ref else None)
    ff_w = 14.0 if points_phase == "preseason" or cand_sig.get("prior_backed") else 6.0
    if cand_ff is not None and ref_ff is not None:
        score += (cand_ff - ref_ff) * ff_w
        src = "prev" if cand_sig.get("prior_backed") else "FF"
        why_extra.append(f"{src} {cand_ff:.1f} vs {ref_ff:.1f}")
    elif cand_ff is not None:
        score += min(16, cand_ff * 2.2)
        why_extra.append(f"FF media {cand_ff:.1f}")
    if cand_prod is not None and ref_prod is not None:
        score += (float(cand_prod) - float(ref_prod)) / 8.0
    elif cand_prod is not None and points_phase == "preseason":
        score += float(cand_prod) / 12.0

    # Capa puntos actual — solo si discrimina
    points_signal = False
    if (cand_avg or 0) > 0 or (ref_avg or 0) > 0:
        points_signal = True
        ca = cand_avg or 0.0
        ra = ref_avg or 0.0
        w = 20.0 if points_phase == "active" else 8.0
        score += (ca - ra) * w
        why_extra.append(f"media {ca:.1f} vs {ra:.1f}")
    if (cand_pts or 0) > 0 or (ref_pts or 0) > 0:
        points_signal = True
        score += ((cand_pts or 0) - (ref_pts or 0)) / (8.0 if points_phase == "active" else 20.0)

    if ptrend == "up":
        score += 10 if points_phase == "active" else 4
    elif ptrend == "down":
        score -= 10 if points_phase == "active" else 3

    if ref_avail in ("injured", "suspended"):
        score += 14

    # Efficiency coste
    cost = acquisition if acquisition and acquisition > 0 else market_value
    if cost > 0 and score > 0:
        score += min(12, score / (1.0 + math.log10(max(cost, 10) / 1_000_000)))

    bf = budget_fit(acquisition, balance) if acquisition is not None else None
    if bf == "blocked":
        score -= 8
    elif bf == "stretch":
        score -= 4

    if acquisition and market_value > 0 and acquisition > market_value * 2.5 and not fills:
        score -= 12

    price_trend = cand.get("trend")
    if price_trend == "down" and not fills and points_phase == "preseason":
        score -= 6
    elif price_trend == "up":
        score += 2

    # improves_owned adaptativo
    improves = False
    if fills and score >= 5:
        improves = True
    elif points_phase == "active" and points_signal:
        avg_ok = (cand_avg or 0) >= (ref_avg or 0) - 0.15
        trend_ok = ptrend in ("up", "flat", "unknown")
        improves = score >= 12 and (avg_ok or trend_ok == "up" or ptrend == "up")
    else:
        # preseason: titularidad / fotmob / prior / carencia
        lineup_up = (
            cand_lineup is not None
            and ref_lineup is not None
            and cand_lineup >= ref_lineup + 5
        ) or (cand_lineup is not None and cand_lineup >= 75 and ref is None)
        fotmob_up = (
            cand_rating is not None
            and ref_rating is not None
            and cand_rating >= ref_rating + 0.25
        ) or (cand_rating is not None and cand_rating >= 7.0 and ref_rating is None)
        prior_up = (
            cand_prior is not None
            and ref_prior is not None
            and cand_prior > ref_prior
        )
        improves = score >= 10 and (lineup_up or fotmob_up or prior_up or fills or ref is None)

    return score, improves, why_extra


def clause_premium_ratio(clause: float | None, market_value: float | None) -> float:
    """Ratio cláusula/VM; 1.0 si no se puede calcular."""
    c = _money(clause)
    m = _money(market_value)
    if c <= 0 or m <= 0:
        return 1.0
    return c / m


def clause_roi(upgrade_score: float, clause: float | None) -> float:
    """upgrade_score por M€ de cláusula."""
    m = max(_money(clause) / 1_000_000.0, 0.5)
    return float(upgrade_score or 0) / m


def clause_roi_gate(
    *,
    upgrade_score: float,
    clause: float | None,
    market_value: float | None,
    fills: bool,
) -> tuple[bool, str | None]:
    """
    ¿La cláusula renta lo bastante para clause_bid?
    Prima alta o ROI bajo sin carencia → False (scout).
    """
    soft = float(getattr(config, "IDEAL_CLAUSE_PREMIUM_SOFT", 1.25))
    min_roi = float(getattr(config, "CLAUSE_MIN_UPGRADE_PER_M", 5.0))
    prem = clause_premium_ratio(clause, market_value)
    roi = clause_roi(upgrade_score, clause)
    if prem > soft and not fills:
        return False, f"mejora cara vs valor (prima {prem:.2f}× VM)"
    if roi < min_roi and not fills:
        return False, f"mejora cara vs valor (ROI {roi:.1f}/M€ < {min_roi:.0f})"
    if prem > soft and fills and roi < min_roi * 0.7:
        return False, f"prima alta y ROI flojo ({roi:.1f}/M€) pese a carencia"
    return True, None


def allocate_clause_bids(
    items: list[dict[str, Any]],
    balance: float,
    *,
    market_reserved: float = 0.0,
    cash_reserve: float | None = None,
) -> list[dict[str, Any]]:
    """
    Mutual exclusivity: ordena por ROI y asigna con saldo simulado.
    Máx. 1 cara (≥40% liquidez) + 1 barata si el residual lo permite.
    """
    bal = max(0.0, float(balance or 0))
    reserved = max(0.0, float(market_reserved or 0))
    sim = max(0.0, bal - reserved)
    expensive_floor = bal * 0.40
    n_exp = 0
    n_cheap = 0
    best_name: str | None = None

    clause_items = [i for i in items if i.get("action") == "clause_bid"]
    others = [i for i in items if i.get("action") != "clause_bid"]
    clause_items.sort(
        key=lambda x: (
            -float(x.get("clause_roi") or 0),
            -float(x.get("upgrade_score") or 0),
            _money(x.get("clause")),
        )
    )

    kept: list[dict[str, Any]] = []
    for item in clause_items:
        row = dict(item)
        cost = _money(row.get("clause") or row.get("bid") or row.get("acquisition_cost"))
        is_exp = cost >= expensive_floor and expensive_floor > 0
        why = (row.get("why") or "").strip()

        if cost <= 0 or cost > sim:
            row["action"] = "scout"
            row["bid"] = None
            row["affordable"] = False
            row["urgency"] = "low"
            note = (
                f"caja ya comprometida en mejor cláusula ({best_name})"
                if best_name
                else f"caja comprometida / residual {sim:,.0f} € < cláusula {cost:,.0f} €"
            )
            if note not in why:
                row["why"] = f"{why}; {note}" if why else note
            row["priority_score"] = max(
                5, priority_score_clause(row) // 2 + int(float(row.get("upgrade_score") or 0) // 3)
            )
            others.append(row)
            continue

        if is_exp:
            if n_exp >= 1:
                row["action"] = "scout"
                row["bid"] = None
                row["affordable"] = False
                row["urgency"] = "low"
                note = f"mejor cláusula ya elegida ({best_name}); esta queda en vigilante"
                if note not in why:
                    row["why"] = f"{why}; {note}" if why else note
                row["priority_score"] = max(
                    5,
                    priority_score_clause(row) // 2
                    + int(float(row.get("upgrade_score") or 0) // 3),
                )
                others.append(row)
                continue
        else:
            if n_cheap >= 1:
                row["action"] = "scout"
                row["bid"] = None
                row["affordable"] = False
                row["urgency"] = "low"
                note = "otra cláusula barata ya priorizada; esta queda en vigilante"
                if note not in why:
                    row["why"] = f"{why}; {note}" if why else note
                row["priority_score"] = max(
                    5,
                    priority_score_clause(row) // 2
                    + int(float(row.get("upgrade_score") or 0) // 3),
                )
                others.append(row)
                continue
            # Barata: no comerse la caja apartada para fichajes de mercado (el 15)
            if reserved > 0 and (sim - cost) < reserved:
                row["action"] = "scout"
                row["bid"] = None
                row["affordable"] = False
                row["urgency"] = "low"
                note = "upgrade bueno, pero reserva caja para carencias de mercado"
                if note not in why:
                    row["why"] = f"{why}; {note}" if why else note
                row["priority_score"] = max(
                    5,
                    priority_score_clause(row) // 2
                    + int(float(row.get("upgrade_score") or 0) // 3),
                )
                others.append(row)
                continue

        sim -= cost
        residual = sim
        roi = float(row.get("clause_roi") or clause_roi(float(row.get("upgrade_score") or 0), cost))
        prem = float(
            row.get("clause_premium")
            or clause_premium_ratio(cost, row.get("market_value"))
        )
        extra = f"ROI {roi:.1f}/M€ · prima {prem:.2f}× · residual {residual:,.0f} €"
        if extra not in why:
            row["why"] = f"{why}; {extra}" if why else extra
        row["residual_after_clause"] = residual
        if is_exp:
            n_exp += 1
        else:
            n_cheap += 1
        if best_name is None:
            best_name = str(row.get("name") or "")
        kept.append(row)

    out = kept + others
    out.sort(
        key=lambda x: (
            0 if x.get("action") == "clause_bid" else 1,
            -float(x.get("clause_roi") or 0),
            -int(x.get("priority_score") or 0),
            -float(x.get("upgrade_score") or 0),
        )
    )
    return out


def build_rival_upgrade_targets(
    me: dict[str, Any],
    diagnosis: dict[str, Any],
    rivals: list[dict[str, Any]],
    *,
    balance: float | None = None,
    points_phase: str | None = None,
    max_debt: float | None = None,
    balance_future: float | None = None,
    hours_to_jornada: float | None = None,
    days_to_kickoff: float | int | None = None,
    matchday: dict[str, Any] | None = None,
    market_reserved: float | None = None,
) -> list[dict[str, Any]]:
    """
    Objetivos en plantillas rivales.
    clause_bid solo si clause_known + finance OK + improves_owned + ROI/prima OK,
    con mutual exclusivity sobre el saldo (máx. 1 cara + 1 barata).
    """
    squad = list(me.get("squad") or [])
    bal = _money(balance if balance is not None else me.get("balance"))
    if max_debt is None:
        try:
            max_debt = float(me["max_debt"]) if me.get("max_debt") is not None else None
        except (TypeError, ValueError):
            max_debt = None
    if balance_future is None:
        try:
            balance_future = (
                float(me["balance_future"]) if me.get("balance_future") is not None else None
            )
        except (TypeError, ValueError):
            balance_future = None
    needy = {
        pos for pos, info in diagnosis.get("by_position", {}).items()
        if info.get("status") in ("critical", "warning")
        or info.get("coverage") in ("critical", "thin")
    }
    critical_pos = {
        a["position"] for a in diagnosis.get("alerts", []) if a.get("level") == "critical"
    }
    has_critical = bool(critical_pos)

    # Universo para fase
    universe: list[dict[str, Any]] = list(squad)
    for r in rivals:
        universe.extend(r.get("squad") or [])
    phase = points_phase or detect_points_phase(universe)

    candidates: list[dict[str, Any]] = []
    for r in rivals:
        owner_name = r.get("team_name") or r.get("manager")
        owner_rank = r.get("rank")
        pool = list(r.get("key_players") or [])
        squad_idx = {str(p.get("id")): p for p in (r.get("squad") or [])}
        for kp in pool:
            pid = str(kp.get("id") or "")
            base = dict(squad_idx.get(pid) or kp)
            base["owner_team"] = owner_name
            base["owner_rank"] = owner_rank
            base["owner_id"] = r.get("team_id")
            candidates.append(base)
        for p in sorted(r.get("squad") or [], key=lambda x: -_money(x.get("price")))[:3]:
            if str(p.get("id")) not in {str(c.get("id")) for c in candidates}:
                row = dict(p)
                row["owner_team"] = owner_name
                row["owner_rank"] = owner_rank
                row["owner_id"] = r.get("team_id")
                candidates.append(row)

    by_id: dict[str, dict[str, Any]] = {}
    for c in candidates:
        pid = str(c.get("id") or "")
        if not pid:
            continue
        prev = by_id.get(pid)
        if not prev or (c.get("clause_known") and not prev.get("clause_known")):
            by_id[pid] = c
        elif prev and _money(c.get("price")) > _money(prev.get("price")):
            by_id[pid] = {**prev, **c}

    results: list[dict[str, Any]] = []
    my_ids = {str(p.get("id")) for p in squad}

    for c in by_id.values():
        pid = str(c.get("id"))
        if pid in my_ids:
            continue
        pos = c.get("position") or "MF"
        ref = _best_owned_reference(squad, pos, points_phase=phase)
        market_value = _money(c.get("market_value") or c.get("price"))
        clause_known = bool(c.get("clause_known"))
        clause = _money(c.get("clause")) if clause_known else None
        acquisition = clause if clause_known else None

        avail = _ext_avail(c)
        if avail in ("injured", "suspended"):
            continue

        fills = pos in needy or pos in critical_pos
        critical_elsewhere = has_critical and not fills

        upgrade_score, improves, why_extra = compute_upgrade_score(
            c,
            ref,
            points_phase=phase,
            fills=fills,
            critical_elsewhere=critical_elsewhere,
            balance=bal,
            acquisition=acquisition,
        )
        if not improves:
            continue

        # Gate plantilla: lujo con carencia crítica otra pos → solo scout
        luxury_block = critical_elsewhere and clause_known

        compared_to = (ref or {}).get("name") if ref else None
        fin: dict[str, Any] | None = None
        bf: str | None = None
        if clause_known and acquisition is not None:
            fin = evaluate_bid_finance(
                acquisition,
                bal,
                min_cost=acquisition,
                max_debt=max_debt,
                balance_future=balance_future,
                hours_to_jornada=hours_to_jornada,
                days_to_kickoff=days_to_kickoff,
                matchday=matchday,
            )
            bf = str(fin.get("budget_fit") or "blocked")
            if fin.get("solvency_blocked"):
                bf = "blocked"

        prem = clause_premium_ratio(clause, market_value) if clause_known else 1.0
        roi = clause_roi(upgrade_score, clause) if clause_known else 0.0
        roi_ok, roi_why = (
            clause_roi_gate(
                upgrade_score=upgrade_score,
                clause=clause,
                market_value=market_value,
                fills=fills,
            )
            if clause_known
            else (True, None)
        )

        why_bits = []
        if fills:
            why_bits.append(f"cubre carencia {pos}")
        if compared_to:
            why_bits.append(f"mejora a {compared_to}")
        else:
            why_bits.append(f"refuerzo {pos}")
        why_bits.append(f"en {c.get('owner_team') or 'rival'} (#{c.get('owner_rank') or '?'})")
        if why_extra:
            why_bits.append(", ".join(why_extra[:2]))
        why_bits.append(f"señal:{phase}")

        avg_s = _mister_avg(c)
        pts_s = _mister_points(c)
        if avg_s is not None or pts_s is not None:
            why_bits.append(
                f"media {avg_s if avg_s is not None else '—'} · pts {int(pts_s) if pts_s is not None else '—'}"
            )

        if luxury_block:
            action = "scout"
            urgency = "low"
            why_bits.append("prioridad: cubre antes tu carencia crítica")
            risk = "low"
        elif clause_known and bf in ("comfortable", "tight") and roi_ok:
            action = "clause_bid"
            urgency = "high" if fills or bf == "comfortable" else "medium"
            why_bits.append(f"cláusula {clause:,.0f} €")
            risk = "medium" if bf == "tight" else "low"
        elif clause_known and bf in ("comfortable", "tight") and not roi_ok:
            action = "scout"
            urgency = "low"
            why_bits.append(roi_why or "mejora cara vs valor")
            if clause:
                why_bits.append(f"cláusula {clause:,.0f} €")
            risk = "low"
        elif clause_known and bf in ("stretch", "blocked"):
            action = "scout"
            urgency = "low"
            if fin and fin.get("solvency_blocked"):
                why_bits.append(
                    f"bloqueado: solvencia D-1 (cláusula {clause:,.0f} € / saldo {bal:,.0f} €)"
                    if clause
                    else "bloqueado: solvencia D-1"
                )
            else:
                why_bits.append(
                    f"mejora clara pero caja corta (cláusula {clause:,.0f} € / saldo {bal:,.0f} €)"
                    if clause
                    else "mejora clara pero caja corta"
                )
            risk = "low"
        else:
            action = "scout"
            urgency = "low"
            why_bits.append("ver cláusula en Mister")
            risk = "low"
            bf = None

        item = {
            "player_id": pid,
            "name": c.get("name"),
            "position": pos,
            "action": action,
            "bid": acquisition if action == "clause_bid" else None,
            "acquisition_cost": acquisition,
            "clause": clause,
            "clause_known": clause_known,
            "market_value": market_value,
            "owner_team": c.get("owner_team"),
            "owner_rank": c.get("owner_rank"),
            "improves_owned": True,
            "compared_to": compared_to,
            "upgrade_score": round(upgrade_score, 1),
            "clause_roi": round(roi, 2) if clause_known else None,
            "clause_premium": round(prem, 3) if clause_known else None,
            "fills_need": fills,
            "budget_fit": bf,
            "debt_risk": bool(fin.get("debt_risk")) if fin else False,
            "solvency_blocked": bool(fin.get("solvency_blocked")) if fin else False,
            "wait_risk": risk,
            "urgency": urgency,
            "trend": c.get("trend"),
            "mister_avg": avg_s,
            "points": pts_s,
            "points_trend": _points_trend(c),
            "points_phase": phase,
            "prior_avg": _prior_avg(c),
            "rival_demand": 0,
            "why": "; ".join(why_bits),
            "affordable": action == "clause_bid",
        }
        if action == "clause_bid":
            item["priority_score"] = (
                priority_score_clause(item)
                + int(upgrade_score // 2)
                + int(min(25, roi * 2))
            )
        else:
            item["priority_score"] = max(
                5, priority_score_clause(item) // 2 + int(upgrade_score // 3)
            )
        results.append(item)

    # Reserva caja de mercado si hay carencias (no gastar todo en cláusulas)
    reserved = float(market_reserved) if market_reserved is not None else 0.0

    results = allocate_clause_bids(results, bal, market_reserved=reserved)

    capped: list[dict[str, Any]] = []
    n_clause = 0
    n_scout = 0
    for r in results:
        if r["action"] == "clause_bid":
            if n_clause >= 2:
                continue
            n_clause += 1
        else:
            if n_scout >= 4:
                continue
            n_scout += 1
        capped.append(r)
    return capped


def tag_rival_market_listings(
    opportunities: list[dict[str, Any]],
    rivals: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Marca ofertas del mercado diario que un rival está vendiendo.

    La compra sigue siendo vía mercado (puja), no cláusula. Solo añade contexto
    UI: listed_by_rival / listed_by_name / clause_reference.
    """
    rival_owners: dict[str, dict[str, Any]] = {}
    for riv in rivals or []:
        owner_label = (
            riv.get("manager")
            or riv.get("team_name")
            or riv.get("name")
            or riv.get("team")
        )
        owner_id = riv.get("team_id") or riv.get("id")
        for p in riv.get("squad") or []:
            pid = str(p.get("id") or p.get("player_id") or "")
            if not pid:
                continue
            label = p.get("owner_name") or owner_label
            rival_owners[pid] = {
                "listed_by_name": label,
                "listed_by_owner_id": str(p.get("owner_id") or owner_id or "") or None,
                "clause_reference": p.get("clause"),
            }

    out: list[dict[str, Any]] = []
    for o in opportunities:
        row = dict(o)
        pid = str(row.get("id") or row.get("player_id") or "")
        on_daily = bool(row.get("on_daily_market") or row.get("seller") == "market")
        info = rival_owners.get(pid) if pid else None
        if on_daily and info:
            row["listed_by_rival"] = True
            if info.get("listed_by_name"):
                row["listed_by_name"] = info["listed_by_name"]
            if info.get("listed_by_owner_id"):
                row["listed_by_owner_id"] = info["listed_by_owner_id"]
            if info.get("clause_reference") is not None:
                row["clause_reference"] = info["clause_reference"]
            # Compra = mercado; no usar cláusula como precio
            row["seller"] = "market"
            row["on_daily_market"] = True
        else:
            row.setdefault("listed_by_rival", False)
        out.append(row)
    return out


def annotate_market_budget_risk(
    opportunities: list[dict[str, Any]],
    rivals: list[dict[str, Any]],
    balance: float,
    *,
    points_phase: str = "preseason",
    market_mode: str = "auction",
    max_debt: float | None = None,
    balance_future: float | None = None,
    hours_to_jornada: float | None = None,
    days_to_kickoff: float | int | None = None,
    matchday: dict[str, Any] | None = None,
    sell_proceeds_timely: float = 0.0,
) -> list[dict[str, Any]]:
    """Añade budget_fit, target_tier, wait_risk, priority_score y reordena mercado."""
    opportunities = tag_rival_market_listings(opportunities, rivals)
    out: list[dict[str, Any]] = []
    bal = _money(balance)
    mode = market_mode or "auction"
    cash_lag = float(int(getattr(config, "MARKET_CYCLE_HOURS", 24) or 24) * 2)
    for o in opportunities:
        row = dict(o)
        fills = bool(row.get("fills_need"))
        risk = wait_risk(row, rivals, fills_need=fills, market_mode=mode)
        cost = _money(row.get("puja_recomendada") or row.get("price"))
        min_c = _money(row.get("puja_minima") or row.get("price"))
        fin = evaluate_bid_finance(
            cost,
            bal,
            min_cost=min_c,
            max_debt=max_debt,
            balance_future=balance_future,
            hours_to_jornada=hours_to_jornada,
            days_to_kickoff=days_to_kickoff,
            matchday=matchday,
            sell_proceeds_timely=sell_proceeds_timely,
            cash_lag_hours=cash_lag,
        )
        bf = str(fin.get("budget_fit") or "blocked")
        tier = target_tier_from_budget_fit(bf)
        # Muestra corta: solo si tampoco hay temporada pasada usable
        if row.get("sample_thin") is None:
            apps = row.get("ff_apps")
            if apps is None:
                apps = (row.get("external") or {}).get("ff_apps")
            try:
                row["sample_thin"] = lacks_comparable_sample(row)
                cur = int(apps) if apps is not None else 0
                if row.get("ff_apps") is None and apps is not None:
                    row["ff_apps"] = cur
                row["current_sample_thin"] = 0 < cur < THIN_APPS
                prior = row.get("ff_prior_apps")
                if prior is None:
                    prior = (row.get("external") or {}).get("ff_prior_apps")
                prior_n = int(prior) if prior is not None else 0
                row["prior_backed"] = bool(0 < cur < THIN_APPS and prior_n >= THIN_APPS)
            except (TypeError, ValueError):
                row["sample_thin"] = False
                row["current_sample_thin"] = False
                row["prior_backed"] = False
        row["wait_risk"] = risk
        row["budget_fit"] = bf
        row["target_tier"] = tier
        row["bid_cap"] = fin.get("bid_cap")
        row["debt_risk"] = bool(fin.get("debt_risk"))
        row["solvency_ok"] = bool(fin.get("solvency_ok"))
        row["solvency_blocked"] = bool(fin.get("solvency_blocked"))
        row["solvency_strict"] = bool(fin.get("solvency_strict"))
        row["hours_to_jornada"] = fin.get("hours_to_jornada")
        row["rival_demand"] = len(
            rival_demand_for_position(rivals, row.get("position") or "", market_mode=mode)
        )
        row["mister_avg"] = _mister_avg(row)
        row["points_trend"] = _points_trend(row)
        row["points_phase"] = points_phase
        row["market_mode"] = mode
        row["priority_score"] = priority_score_buy({
            **row,
            "risk": risk,
            "budget_fit": bf,
            "target_tier": tier,
        })
        row["affordable"] = bf in ("comfortable", "tight")
        # Nunca Alta si aspiracional (refuerzo tras annotate)
        if tier == "aspirational" and row.get("priority") == "Alta":
            row["priority"] = "Media"
        if fin.get("solvency_blocked") and row.get("priority") == "Alta":
            row["priority"] = "Media"
        out.append(row)
    out.sort(key=lambda x: (-int(x.get("priority_score") or 0), -float(x.get("score") or 0)))
    return out


def _is_daily_market_item(item: dict[str, Any]) -> bool:
    return bool(item.get("on_daily_market") or item.get("seller") == "market")


def _item_buy_cost(item: dict[str, Any]) -> float:
    for key in ("cost", "bid", "puja_recomendada", "price"):
        if item.get(key) is not None:
            try:
                return float(item[key])
            except (TypeError, ValueError):
                pass
    return 0.0


def _item_min_bid(item: dict[str, Any]) -> float:
    """Suelo de puja Mister (mínimo de subasta), no la recomendada."""
    for key in ("puja_minima", "min_bid"):
        if item.get(key) is not None:
            try:
                val = float(item[key])
            except (TypeError, ValueError):
                continue
            if val > 0:
                return val
    for key in ("price", "market_value"):
        if item.get(key) is not None:
            try:
                val = float(item[key])
            except (TypeError, ValueError):
                continue
            if val > 0:
                return val
    # Sin mínimo explícito: no descontar (evita pujas ilegales)
    return _item_buy_cost(item)


def hedge_bid_amount(item: dict[str, Any]) -> float:
    """Puja reducida del hedge: ratio sobre la recomendada, nunca bajo el mínimo Mister."""
    full = _item_buy_cost(item)
    if full <= 0:
        return 0.0
    # Si ya se aplicó descuento, no recomprimir
    if item.get("hedge_bid_discount") and item.get("bid") is not None:
        try:
            return float(item["bid"])
        except (TypeError, ValueError):
            pass
    ratio = float(getattr(config, "PACKAGE_HEDGE_BID_RATIO", 0.85))
    min_c = _item_min_bid(item)
    # Nunca sugerir por debajo del mínimo legal de la subasta
    reduced = max(min_c, full * ratio)
    # Redondeo a 10k hacia ABAJO solo si seguimos >= mínimo; si no, usar el mínimo exacto
    step = 10_000.0
    if reduced >= step:
        floored = (reduced // step) * step
        reduced = floored if floored >= min_c else min_c
    if reduced < min_c:
        reduced = min_c
    return round(reduced, 0)


def apply_hedge_pricing(item: dict[str, Any]) -> float:
    """Ajusta bid/cost del hedge y guarda la puja llena de referencia."""
    full = _item_buy_cost(item)
    min_c = _item_min_bid(item)
    reduced = hedge_bid_amount(item)
    if full > 0 and not item.get("bid_full"):
        item["bid_full"] = full
    # Seguridad final: jamás por debajo del mínimo
    if min_c > 0 and reduced < min_c:
        reduced = min_c
    item["bid"] = reduced
    item["cost"] = reduced
    item["puja_minima"] = min_c if min_c > 0 else item.get("puja_minima")
    item["hedge_bid_discount"] = bool(full > 0 and reduced < full - 1)
    return reduced


def _wait_risk_rank(item: dict[str, Any]) -> int:
    risk = str(item.get("wait_risk") or item.get("risk") or "low").lower()
    return {"high": 2, "medium": 1, "low": 0}.get(risk, 0)


def _is_strong_intent(item: dict[str, Any], primary_ids: set[str]) -> bool:
    pid = str(item.get("player_id") or "")
    return bool(
        item.get("is_key_market")
        or item.get("is_primary_target")
        or pid in primary_ids
        or item.get("fills_structural")
    )


def _is_weak_intent(item: dict[str, Any], primary_ids: set[str]) -> bool:
    return not _is_strong_intent(item, primary_ids) and not bool(
        item.get("fills_coverage_gap") or item.get("fills_need")
    )


def _another_full_bid_ok(item: dict[str, Any], pos_full_count: dict[str, int]) -> bool:
    """Segundo fichaje lleno en la misma pos solo si la línea sigue con hueco estructural."""
    pos = str(item.get("position") or "")
    n = int(pos_full_count.get(pos, 0) or 0)
    if n <= 0 or n >= 2:
        return False
    return bool(item.get("fills_structural"))


def _intent_eligible(item: dict[str, Any]) -> bool:
    """Excluye líneas sobradas sin gap/upgrade rentable del pool de intents/primary."""
    if item.get("appreciation_play"):
        # Flip de valor: permitido aunque la línea esté cubierta (tope de precio aparte)
        return True
    if item.get("overstocked") and not (
        item.get("fills_coverage_gap")
        or item.get("fills_structural")
        or item.get("upgrade_worth_buy")
    ):
        return False
    return True


def _intent_sort_key(
    item: dict[str, Any],
    *,
    bal: float,
    cash_reserve: float,
    primary_ids: set[str],
) -> tuple:
    cost = _item_buy_cost(item)
    residual = bal - cost
    leaves = bool(item.get("leaves_gap_budget")) or residual >= cash_reserve
    crowds = bool(item.get("crowds_out_gaps"))
    fills = bool(
        item.get("fills_coverage_gap")
        or item.get("fills_structural")
        or item.get("fills_need")
    )
    pid = str(item.get("player_id") or "")
    overstock_blocks = not _intent_eligible(item)
    is_prim = (not overstock_blocks) and (
        bool(item.get("is_primary_target")) or pid in primary_ids
    )
    is_key = (not overstock_blocks) and (bool(item.get("is_key_market")) or is_prim)
    is_obj = (not overstock_blocks) and (
        bool(item.get("is_board_objective")) or is_prim
    )
    try:
        asset = float(item.get("trade_asset_score") or 0)
    except (TypeError, ValueError):
        asset = 0.0
    try:
        prod = float(item.get("production_score") or 0)
    except (TypeError, ValueError):
        prod = 0.0
    return (
        0 if overstock_blocks else 1,
        1 if is_key else 0,
        1 if is_prim else 0,
        1 if is_obj else 0,
        1 if fills else 0,
        1 if item.get("appreciation_play") else 0,
        int(prod),
        int(asset * 10),
        0 if crowds else 1,
        1 if leaves else 0,
        int(item.get("priority_score") or 0),
        int(item.get("_queue_rank") or 0),
        -cost,
    )


def _pkg_player_ref(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    return {
        "player_id": item.get("player_id"),
        "name": item.get("name"),
        "position": item.get("position"),
        "bid": item.get("bid") or item.get("cost"),
        "is_key_market": bool(item.get("is_key_market")),
        "trade_asset_score": item.get("trade_asset_score"),
        "wait_risk": item.get("wait_risk"),
    }


def select_intent_lines(
    daily_buys: list[dict[str, Any]],
    *,
    bal: float,
    cash_reserve: float,
    primary_ids: set[str],
    secondary_max: float,
    max_intents: int = 8,
) -> list[dict[str, Any]]:
    """Hasta N intents de posiciones distintas (clave → carencia → score/trueque)."""
    if not daily_buys or max_intents <= 0:
        return []

    def sort_key(item: dict[str, Any]) -> tuple:
        return _intent_sort_key(
            item, bal=bal, cash_reserve=cash_reserve, primary_ids=primary_ids
        )

    key_hit = [
        i
        for i in daily_buys
        if _intent_eligible(i)
        and (
            i.get("is_key_market")
            or str(i.get("player_id") or "") in primary_ids
            or i.get("is_primary_target")
        )
    ]
    gap_pool = [
        i
        for i in daily_buys
        if _intent_eligible(i)
        and (
            i.get("fills_coverage_gap")
            or i.get("fills_structural")
            or i.get("fills_need")
            or i.get("is_board_objective")
        )
    ]
    apprec_pool = [
        i for i in daily_buys if _intent_eligible(i) and i.get("appreciation_play")
    ]
    preferred = [
        i
        for i in daily_buys
        if _intent_eligible(i)
        and ((bal - _item_buy_cost(i)) >= cash_reserve or i.get("leaves_gap_budget"))
    ]
    eligible = [i for i in daily_buys if _intent_eligible(i)]
    pool = key_hit or gap_pool or apprec_pool or preferred or eligible
    if not pool:
        return []
    first = max(pool, key=sort_key)
    intents: list[dict[str, Any]] = [first]
    if max_intents < 2:
        return intents

    residual = max(0.0, bal - _item_buy_cost(first))
    first_pos = first.get("position")
    first_id = str(first.get("player_id") or "")
    for cand in sorted(daily_buys, key=sort_key, reverse=True):
        if not _intent_eligible(cand):
            continue
        if str(cand.get("player_id") or "") == first_id:
            continue
        if cand.get("position") == first_pos:
            continue
        if first.get("is_key_market") and cand.get("is_key_market"):
            if _item_buy_cost(cand) > secondary_max:
                continue
        cost = _item_buy_cost(cand)
        if cost <= 0 or cost > secondary_max:
            continue
        if cost > residual:
            continue
        if not (
            cand.get("fills_coverage_gap")
            or cand.get("fills_structural")
            or cand.get("fills_need")
            or cand.get("is_key_market")
            or cand.get("is_primary_target")
            or str(cand.get("player_id") or "") in primary_ids
        ):
            continue
        if residual - cost < cash_reserve * 0.45 and cost > 1_200_000:
            continue
        intents.append(cand)
        break
    return intents


def select_hedge_for(
    intent: dict[str, Any],
    daily_buys: list[dict[str, Any]],
    *,
    exclude_ids: set[str],
    bal: float,
    cash_reserve: float,
    primary_ids: set[str],
    fixed: bool,
) -> dict[str, Any] | None:
    """Mejor alt same-pos en mercado diario si el intent está disputado (solo auction)."""
    if fixed:
        return None
    if _wait_risk_rank(intent) < 1:
        return None
    if not _is_daily_market_item(intent):
        return None
    pos = intent.get("position")
    intent_id = str(intent.get("player_id") or "")

    def sort_key(item: dict[str, Any]) -> tuple:
        return _intent_sort_key(
            item, bal=bal, cash_reserve=cash_reserve, primary_ids=primary_ids
        )

    cands = [
        i
        for i in daily_buys
        if i.get("position") == pos
        and str(i.get("player_id") or "") not in exclude_ids
        and str(i.get("player_id") or "") != intent_id
        and _is_daily_market_item(i)
    ]
    if not cands:
        return None
    return max(cands, key=sort_key)


def finalize_action_plan(
    plan: list[dict[str, Any]],
    *,
    balance: float | None = None,
    funding_info: dict[str, Any] | None = None,
    market_mode: str = "auction",
    target_board: dict[str, Any] | None = None,
    squad_size: int | None = None,
    max_squad: int | None = None,
    bootstrap: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Cola operativa: fichar mientras quepan caja y plazas.
    Hedge same-day solo en subasta si el titular de esa línea está disputado.
    Devuelve (action_plan, daily_package).
    """
    cash_reserve = float(getattr(config, "PACKAGE_CASH_RESERVE", 0) or 0)
    # Reserva del paquete = 0 (liquidez = listados). Solo solvencia al pitido.
    bal = float(balance) if balance is not None else 0.0
    bootstrap_ctx = bootstrap
    if not bootstrap_ctx and isinstance(funding_info, dict):
        bootstrap_ctx = funding_info.get("bootstrap_xi_context")
    if funding_info and funding_info.get("cash_reserved") is not None:
        cash_reserve = max(0.0, float(funding_info.get("cash_reserved") or 0))
    package_id = datetime.now(timezone.utc).date().isoformat()
    cycle_h = None
    if isinstance(funding_info, dict):
        cycle_h = funding_info.get("cycle_hours")
    cash_lag_h = None
    if isinstance(funding_info, dict):
        cash_lag_h = funding_info.get("cash_lag_hours")
    if cash_lag_h is None:
        cash_lag_h = _cycle_hours_value(cycle_h) * 2
    fixed = (market_mode or "auction") == "fixed"
    if max_squad is None:
        max_squad = int(
            getattr(config, "MAX_SQUAD_SIZE_PREMIER", 22)
            if fixed
            else getattr(config, "MAX_SQUAD_SIZE_LALIGA", 25)
        )
    else:
        max_squad = int(max_squad)
    squad_n = int(squad_size) if squad_size is not None else 0
    free_slots = max(0, max_squad - squad_n)
    try:
        from market_cycle import bootstrap_buy_cap

        buy_cap = bootstrap_buy_cap(
            free_slots=free_slots,
            bootstrap=bootstrap_ctx if isinstance(bootstrap_ctx, dict) else None,
            fixed=fixed,
        )
    except Exception:  # noqa: BLE001
        buy_cap = min(8, free_slots if free_slots > 0 else 0)
    # Ideal aspiracional: solo scout / watching — no reserva caja ni fund_target
    # Filtrar primary_ids de posiciones sobradas (salvo upgrade que renta)
    overstock_pos = {
        i.get("position")
        for i in plan
        if i.get("position")
        and i.get("overstocked")
        and not (
            i.get("fills_coverage_gap")
            or i.get("fills_structural")
            or i.get("upgrade_worth_buy")
        )
    }
    worth_upgrade_ids = {
        str(i.get("player_id"))
        for i in plan
        if i.get("player_id") and i.get("upgrade_worth_buy")
    }
    primary_ids = {
        str(t.get("player_id"))
        for t in (target_board or {}).get("primary_targets") or []
        if t.get("player_id")
        and t.get("on_daily_market")
        and (
            t.get("position") not in overstock_pos
            or str(t.get("player_id")) in worth_upgrade_ids
        )
    }
    cash_reserved_targets = float((funding_info or {}).get("cash_reserved") or 0)
    cash_reserve = 0.0
    plan_ids = {str(i.get("player_id")) for i in plan if i.get("player_id")}
    for at in (target_board or {}).get("aspirational_targets") or []:
        pid = str(at.get("player_id") or "")
        if not pid or pid in plan_ids or pid in primary_ids:
            continue
        plan.append(
            {
                "player_id": pid,
                "name": at.get("name"),
                "position": at.get("position"),
                "action": "scout",
                "bid": None,
                "acquisition_cost": _money(at.get("price")),
                "price": _money(at.get("price")),
                "ep_score": at.get("ep_score"),
                "why": (
                    f"Ideal aspiracional (máx EP) · no reserva caja operable · "
                    f"EP {at.get('ep_score') or '—'} · ~{_money(at.get('price')):,.0f} €"
                ),
                "urgency": "low",
                "affordable": False,
                "budget_fit": "blocked",
                "target_tier": "aspirational",
                "is_primary_target": False,
                "is_board_objective": False,
                "queue_role": "aspirational_watch",
                "priority_score": 20,
            }
        )
        plan_ids.add(pid)

    action_base = {
        "buy_now": 1000,
        "clause_bid": 920,
        "sell": 840,
        "avoid": 760,
        "wait": 200,
        "scout": 160,
    }
    urg_bonus = {"high": 40, "medium": 15, "low": 0}
    for item in plan:
        if item.get("priority_score") is None:
            act = item.get("action")
            if act == "sell":
                item["priority_score"] = priority_score_sell(item)
            elif act in ("clause_bid", "scout"):
                item["priority_score"] = priority_score_clause(item)
            else:
                item["priority_score"] = priority_score_buy(item)
        base = action_base.get(item.get("action"), 0)
        need_boost = 0
        if item.get("fills_structural"):
            need_boost = 90
        elif item.get("fills_need"):
            need_boost = 45
        if item.get("leaves_gap_budget"):
            need_boost += 25
        if item.get("crowds_out_gaps"):
            need_boost -= 70
        daily_boost = 0
        board_w = 0.5 if is_closing_phase() else 1.0
        if item.get("action") in ("buy_now", "wait", "avoid"):
            if _is_daily_market_item(item):
                daily_boost = 120
                if item.get("is_key_market"):
                    daily_boost += int(180 * board_w)  # clave del día por encima de parches
                elif item.get("is_primary_target"):
                    daily_boost += int(100 * board_w)
            elif item.get("action") == "wait":
                daily_boost = -80
        rival_boost = 0 if fixed else min(20, int(item.get("rival_demand") or 0) * 4)
        # Sin clave: carencia + puntaje/trueque
        asset_boost = 0
        if not item.get("is_key_market") and item.get("action") == "buy_now":
            try:
                asset_boost = min(40, int(float(item.get("trade_asset_score") or 0)))
            except (TypeError, ValueError):
                asset_boost = 0
        item["_queue_rank"] = (
            base
            + int(item.get("priority_score") or 0)
            + urg_bonus.get(item.get("urgency"), 0)
            + rival_boost
            + need_boost
            + daily_boost
            + asset_boost
        )

    plan.sort(
        key=lambda x: (
            -int(x.get("_queue_rank") or 0),
            -int(x.get("priority_score") or 0),
        )
    )

    daily_buys = [
        i
        for i in plan
        if i.get("action") == "buy_now"
        and _is_daily_market_item(i)
        and (i.get("target_tier") or target_tier_from_budget_fit(i.get("budget_fit"))) == "realistic"
        and (i.get("budget_fit") in ("comfortable", "tight", None))
    ]

    def _rank_buy(item: dict[str, Any]) -> tuple:
        return _intent_sort_key(
            item, bal=bal, cash_reserve=cash_reserve, primary_ids=primary_ids
        )

    ranked_buys = sorted(
        [i for i in daily_buys if _intent_eligible(i)],
        key=_rank_buy,
        reverse=True,
    )

    sim = bal
    slots_left = free_slots
    funded_intent_ids: set[str] = set()
    funded_hedge_ids: set[str] = set()
    blocked_no_slot_ids: set[str] = set()
    intents: list[dict[str, Any]] = []
    pos_full_count: dict[str, int] = {}
    hedge_by_intent: dict[str, dict[str, Any]] = {}

    # Pase 1: fichajes llenos (posición nueva o hueco estructural restante)
    for item in ranked_buys:
        if len(funded_intent_ids) + len(funded_hedge_ids) >= buy_cap:
            break
        pid = str(item.get("player_id") or "")
        pos = str(item.get("position") or "")
        if not pid:
            continue
        n_pos = int(pos_full_count.get(pos, 0) or 0)
        if n_pos <= 0 or _another_full_bid_ok(item, pos_full_count):
            cost = _item_buy_cost(item)
            if slots_left <= 0:
                blocked_no_slot_ids.add(pid)
                continue
            if cost > sim:
                continue
            funded_intent_ids.add(pid)
            intents.append(item)
            pos_full_count[pos] = n_pos + 1
            sim -= cost
            slots_left -= 1

    # Pase 2: hedges same-pos solo en subasta si el titular está disputado
    intent_ids = set(funded_intent_ids)
    if not fixed:
        for intent in intents:
            iid = str(intent.get("player_id") or "")
            taken_hedges = {
                str(h.get("player_id") or "") for h in hedge_by_intent.values()
            }
            hedge = select_hedge_for(
                intent,
                daily_buys,
                exclude_ids=intent_ids | taken_hedges,
                bal=bal,
                cash_reserve=cash_reserve,
                primary_ids=primary_ids,
                fixed=fixed,
            )
            if not hedge:
                continue
            hid = str(hedge.get("player_id") or "")
            hedge_by_intent[iid] = hedge
            if hid:
                intent_ids.add(hid)
            cost = hedge_bid_amount(hedge)
            at_cap = len(funded_intent_ids) + len(funded_hedge_ids) >= buy_cap
            if slots_left <= 0 or at_cap:
                if hid:
                    blocked_no_slot_ids.add(hid)
                continue
            if cost > sim:
                continue
            funded_hedge_ids.add(hid)
            sim -= cost
            slots_left -= 1

    funded_intents = [
        i for i in intents if str(i.get("player_id") or "") in funded_intent_ids
    ]
    primary = funded_intents[0] if funded_intents else None
    secondary = funded_intents[1] if len(funded_intents) > 1 else None
    primary_id = str(primary.get("player_id") or "") if primary else ""

    # Mapa player_id → línea (intent id) para hedges / alts
    intent_name_by_id = {
        str(i.get("player_id") or ""): str(i.get("name") or "") for i in intents
    }
    hedge_id_to_intent: dict[str, str] = {
        str(h.get("player_id") or ""): iid for iid, h in hedge_by_intent.items()
    }
    demoted: list[dict[str, Any]] = []
    line_meta: list[dict[str, Any]] = []

    for intent in intents:
        iid = str(intent.get("player_id") or "")
        hedge = hedge_by_intent.get(iid)
        if iid not in funded_intent_ids:
            hedge_status = "not_needed"
            if hedge:
                hid = str(hedge.get("player_id") or "")
                hedge_status = (
                    "no_slot" if hid in blocked_no_slot_ids else "unfunded"
                )
            line_meta.append(
                {
                    "intent": _pkg_player_ref(intent),
                    "hedge": _pkg_player_ref(hedge),
                    "hedge_status": hedge_status,
                    "intent_funded": False,
                    "intent_blocked": "no_slot" if iid in blocked_no_slot_ids else "unfunded",
                }
            )
            continue
        if not hedge:
            hedge_status = "not_needed"
        elif str(hedge.get("player_id") or "") in funded_hedge_ids:
            hedge_status = "bid"
        elif str(hedge.get("player_id") or "") in blocked_no_slot_ids:
            hedge_status = "no_slot"
        else:
            hedge_status = "unfunded"
        line_meta.append(
            {
                "intent": _pkg_player_ref(intent),
                "hedge": _pkg_player_ref(hedge),
                "hedge_status": hedge_status,
                "intent_funded": True,
            }
        )

    n_intents_funded = len(funded_intent_ids)
    n_hedges_funded = len(funded_hedge_ids)
    slot_shortfall = len(blocked_no_slot_ids)
    # Cupo justo / sin plaza → priorizar ventas que abren hueco (rescindir libera ya)
    need_slot_sells = free_slots <= 2 or slot_shortfall > 0
    slot_sell_ids: set[str] = set()
    if need_slot_sells:
        sell_cands = [
            i
            for i in plan
            if i.get("action") == "sell"
            and i.get("xi_impact") != "risk"
            and not i.get("is_top_ff")
            and not i.get("keep_if_rank_top")
        ]
        sell_cands.sort(
            key=lambda x: (
                0 if x.get("xi_impact") == "safe" else 1,
                -int(x.get("priority_score") or 0),
                -float(x.get("price") or 0),
            )
        )
        # Al menos 1 venta si cupo ≤2; tantas como plazas que faltan para el paquete deseado
        n_slot_sells = max(1 if free_slots <= 2 else 0, min(3, slot_shortfall or (2 - free_slots if free_slots < 2 else 0)))
        if free_slots <= 0:
            n_slot_sells = max(n_slot_sells, 2)
        for s in sell_cands[:n_slot_sells]:
            sid = str(s.get("player_id") or "")
            if not sid:
                continue
            slot_sell_ids.add(sid)
            s["queue_role"] = "free_slot"
            s["opens_slot"] = True
            s["package_id"] = package_id
            s["urgency"] = "high" if free_slots <= 1 else (s.get("urgency") or "medium")
            cupo = f"{squad_n}/{max_squad}"
            prev_note = (s.get("package_note") or "").strip()
            note = (
                f"Abre plaza ({cupo}) — vende/rescinde antes de pujar de más"
                if free_slots <= 1 or slot_shortfall
                else f"Cupo justo ({cupo}) — venta para poder hedgear/fichar"
            )
            s["package_note"] = note
            why_prev = (s.get("why") or "").strip()
            if "Abre plaza" not in why_prev and "Cupo justo" not in why_prev:
                s["why"] = f"{note}; {why_prev}" if why_prev else note
            if not s.get("instant_alt"):
                s["instant_alt"] = rescind_instant_alt(float(s.get("price") or 0))
            # Motivo explícito para scoring/UI
            if not s.get("sell_reason") or s.get("sell_reason") in (
                "form_drop",
                "surplus_to_demand",
            ):
                s["sell_reason"] = "free_slot"
            s["priority_score"] = int(s.get("priority_score") or 0) + (
                50 if free_slots <= 0 else 28
            )

    for item in plan:
        if item.get("action") != "buy_now":
            continue
        pid = str(item.get("player_id") or "")
        item["package_id"] = package_id
        if pid in funded_intent_ids:
            if item.get("is_key_market") or item.get("is_primary_target") or pid in primary_ids:
                item["queue_role"] = "primary_target" if pid == primary_id else "secondary"
                item["package_note"] = (
                    "Clave del mercado — fichar al precio"
                    if fixed
                    else (
                        "Clave del mercado — pujar ya"
                        if pid == primary_id
                        else "2ª línea — clave / pujar"
                    )
                )
            elif pid == primary_id:
                item["queue_role"] = "primary"
                item["package_note"] = (
                    "Carencia prioritaria — fichar al precio"
                    if fixed
                    else "Carencia prioritaria — pujar (máx. puntaje/trueque)"
                )
            elif item.get("appreciation_play"):
                item["queue_role"] = "secondary"
                item["package_note"] = (
                    "Revalorización — fichar para vender más caro / activo oportunidad"
                    if fixed
                    else "Revalorización — pujar: sube de VM con buena perspectiva"
                )
            else:
                item["queue_role"] = "secondary"
                item["package_note"] = (
                    "2ª línea — fichar al precio"
                    if fixed
                    else "2ª línea — carencia con buen trueque"
                )
            item["alt_for"] = None
            continue
        if pid in funded_hedge_ids:
            parent_id = hedge_id_to_intent.get(pid, "")
            parent_name = intent_name_by_id.get(parent_id, "")
            reduced = apply_hedge_pricing(item)
            full = float(item.get("bid_full") or reduced)
            item["queue_role"] = "hedge"
            item["alt_for"] = parent_id or None
            discount_note = ""
            if full > reduced + 1:
                discount_note = f" · puja reducida {reduced:,.0f} € (vs {full:,.0f} €)"
            item["package_note"] = (
                (
                    f"Hedge por si pierdes {parent_name}{discount_note}. "
                    f"Si ganas ambos, vende el peor al ciclo siguiente"
                )
                if parent_name
                else (
                    f"Hedge same-day{discount_note}. "
                    f"Si ganas ambos, vende el peor al ciclo siguiente"
                )
            )
            item["urgency"] = "high"
            why_prev = (item.get("why") or "").strip()
            prefix = item["package_note"]
            item["why"] = f"{prefix}; {why_prev}" if why_prev else prefix
            continue

        # Sin plaza de plantilla (cupo)
        if pid in blocked_no_slot_ids:
            item["action"] = "wait"
            item["queue_role"] = "alt_no_slot"
            parent_id = hedge_id_to_intent.get(pid, "")
            parent_name = intent_name_by_id.get(parent_id, "")
            cupo = f"{squad_n}/{max_squad}"
            if parent_name:
                item["alt_for"] = parent_id or None
                item["package_note"] = (
                    f"Sin plaza en plantilla ({cupo}) para hedge de {parent_name} — vende antes"
                )
            else:
                item["alt_for"] = primary.get("player_id") if primary else None
                item["package_note"] = (
                    f"Sin plaza en plantilla ({cupo}) — vende antes de fichar"
                )
            item["urgency"] = "medium"
            why_prev = (item.get("why") or "").strip()
            prefix = item["package_note"]
            item["why"] = f"{prefix}; {why_prev}" if why_prev else prefix
            demoted.append(item)
            continue

        # Resto: demote a wait
        why_prev = (item.get("why") or "").strip()
        parent_intent = None
        for intent in funded_intents or intents:
            if item.get("position") == intent.get("position"):
                parent_intent = intent
                break
        parent_name = str(parent_intent.get("name") or "") if parent_intent else ""
        parent_pid = parent_intent.get("player_id") if parent_intent else None

        # Hedge candidato no financiado (misma pos que un intent disputado)
        is_unfunded_hedge = False
        for iid, h in hedge_by_intent.items():
            if str(h.get("player_id") or "") == pid and iid in funded_intent_ids:
                is_unfunded_hedge = True
                parent_name = intent_name_by_id.get(iid, parent_name)
                parent_pid = iid
                break

        if is_unfunded_hedge and not fixed:
            item["action"] = "wait"
            item["queue_role"] = "alt_unfunded"
            item["alt_for"] = parent_pid
            item["package_note"] = (
                f"Sin caja para hedge de {parent_name}; "
                f"si lo pierdes, este alt probablemente ya no esté"
            )
            item["urgency"] = "medium"
            prefix = item["package_note"]
            item["why"] = f"{prefix}; {why_prev}" if why_prev else prefix
        elif parent_intent and item.get("position") == parent_intent.get("position"):
            item["action"] = "wait"
            if fixed:
                item["queue_role"] = "also_good"
                item["alt_for"] = parent_pid
                item["package_note"] = "También válido — sin prisa (plantillas compartidas)"
                item["urgency"] = "low"
                prefix = "También válido — sin prisa"
            else:
                item["queue_role"] = "alt_if_lost"
                item["alt_for"] = parent_pid
                item["package_note"] = (
                    f"Alt de {parent_name} — riesgo bajo; no hedge same-day"
                    if _wait_risk_rank(parent_intent) < 1
                    else f"Alt de {parent_name} — sin puja extra hoy"
                )
                item["urgency"] = "low"
                prefix = item["package_note"]
            item["why"] = f"{prefix}; {why_prev}" if why_prev else prefix
        else:
            item["action"] = "wait"
            item["queue_role"] = "also_good"
            item["alt_for"] = primary.get("player_id") if primary else None
            item["package_note"] = "También válido — ya hay otros fichajes priorizados"
            item["urgency"] = "low"
            prefix = item["package_note"]
            item["why"] = f"{prefix}; {why_prev}" if why_prev else prefix
        demoted.append(item)

    # Sincronizar refs de hedge en lines con puja reducida
    priced_hedges = {
        str(a.get("player_id") or ""): a
        for a in plan
        if a.get("queue_role") == "hedge"
    }
    for line in line_meta:
        href = line.get("hedge") or {}
        hid = str(href.get("player_id") or "")
        if hid and hid in priced_hedges:
            line["hedge"] = _pkg_player_ref(priced_hedges[hid])
            line["exit_if_both"] = "sell_worse_next_cycle"

    buy_roles = ("primary", "primary_target", "secondary", "hedge")
    top_roles = buy_roles + ("free_slot", "sell_now")

    # Ventas normales (accionables hoy) — no dejarlas sin rol en el cajón "other"
    for item in plan:
        if item.get("action") != "sell":
            continue
        if item.get("queue_role") in ("free_slot", "sell_now"):
            continue
        item["queue_role"] = "sell_now"
        item["package_id"] = package_id
        if not item.get("package_note"):
            item["package_note"] = (
                f"Vender hoy — puedes listar ya (caja en ~{int(round(float(cash_lag_h)))}h "
                "salvo rescindir)"
            )

    # Waits del mercado de hoy same-pos / clave sin rol → etiquetar como alt visible
    primary_pos = primary.get("position") if primary else None
    primary_name_s = str(primary.get("name") or "") if primary else ""
    for item in plan:
        if item.get("action") != "wait":
            continue
        if item.get("queue_role"):
            continue
        if not _is_daily_market_item(item):
            continue
        same_pos = primary_pos and item.get("position") == primary_pos
        notable = bool(
            item.get("is_key_market")
            or item.get("is_primary_target")
            or item.get("fills_structural")
            or item.get("fills_need")
            or item.get("fills_coverage_gap")
        )
        if not (same_pos or notable):
            continue
        item["package_id"] = package_id
        if same_pos and primary_name_s:
            item["queue_role"] = "alt_if_lost"
            item["alt_for"] = primary.get("player_id")
            item["package_note"] = (
                f"No pujar hoy — alt de {primary_name_s} (mismo puesto / ya priorizado otro)"
            )
        else:
            item["queue_role"] = "also_good" if fixed else "alt_if_lost"
            item["package_note"] = (
                "No pujar hoy — ya hay fichajes priorizados en la cola"
            )
        why_prev = (item.get("why") or "").strip()
        prefix = item["package_note"]
        if prefix not in why_prev:
            item["why"] = f"{prefix}; {why_prev}" if why_prev else prefix

    # Aspiracionales / fuera de caja → no mezclar con plan de hoy
    for item in plan:
        tier = item.get("target_tier") or target_tier_from_budget_fit(item.get("budget_fit"))
        if tier != "aspirational" and item.get("budget_fit") != "blocked":
            continue
        if item.get("queue_role") in top_roles:
            continue
        if item.get("action") in ("buy_now", "clause_bid"):
            item["action"] = "wait"
        if not item.get("queue_role") or item.get("queue_role") in (
            "alt_if_lost",
            "alt_unfunded",
            "alt_no_slot",
            "also_good",
        ):
            item["queue_role"] = "out_of_budget"
            note = "Fuera de caja — vigilar / vender antes"
            item["package_note"] = note
            item["urgency"] = "low"
            why_prev = (item.get("why") or "").strip()
            if "Fuera de caja" not in why_prev:
                item["why"] = f"{note}; {why_prev}" if why_prev else note

    # Re-rank tras demote
    for item in plan:
        role = item.get("queue_role")
        if role == "free_slot":
            # Sin plazas: ventas antes que pujas; con cupo justo: tras intents, antes de hedges
            base = 10_500 if free_slots <= 0 else 9_200
            item["_queue_rank"] = base + int(item.get("priority_score") or 0)
        elif role in ("primary", "primary_target"):
            item["_queue_rank"] = 10_000 + int(item.get("priority_score") or 0)
        elif role == "secondary":
            item["_queue_rank"] = 9_000 + int(item.get("priority_score") or 0)
        elif role == "hedge":
            item["_queue_rank"] = 8_500 + int(item.get("priority_score") or 0)
        elif role == "sell_now":
            item["_queue_rank"] = 8_000 + int(item.get("priority_score") or 0)
        elif role == "alt_unfunded":
            item["_queue_rank"] = 750 + int(item.get("priority_score") or 0)
        elif role == "alt_no_slot":
            item["_queue_rank"] = 740 + int(item.get("priority_score") or 0)
        elif role in ("alt_if_lost", "also_good"):
            item["_queue_rank"] = 700 + int(item.get("priority_score") or 0)
        elif role == "out_of_budget":
            item["_queue_rank"] = 300 + int(item.get("priority_score") or 0) // 3

    plan.sort(
        key=lambda x: (
            -int(x.get("_queue_rank") or 0),
            -int(x.get("priority_score") or 0),
        )
    )

    capped: list[dict[str, Any]] = []
    per_action: dict[str, int] = {}
    # buy_cap definido arriba (bootstrap puede ampliar el tope en fixed)
    # Si no hay plazas, no emitir buy_now (todo demoted)
    limits = {
        "buy_now": buy_cap,
        "clause_bid": 0 if fixed else 2,
        "wait": 8,
        "avoid": 3,
        "sell": 5,
        "scout": 0 if fixed else 3,
    }
    max_pipeline_waits = 2
    max_alt_waits = 3
    max_oob_waits = 2
    pipeline_waits = 0
    alt_waits = 0
    oob_waits = 0
    max_total = sum(limits.values())
    used_ids: set[str] = set()
    sim_balance = float(balance) if balance is not None else None

    def _append(item: dict[str, Any]) -> bool:
        nonlocal sim_balance, pipeline_waits, alt_waits, oob_waits
        a = item.get("action") or ""
        pid = str(item.get("player_id") or "")
        role = item.get("queue_role")
        if pid and pid in used_ids:
            return False
        if per_action.get(a, 0) >= limits.get(a, 3):
            return False
        if a == "buy_now" and role not in buy_roles:
            return False
        if a == "sell" and role == "free_slot":
            pass  # siempre permitir ventas que abren plaza (cuenta en limits sell)
        if a == "buy_now" and sim_balance is not None:
            cost = _item_buy_cost(item)
            if cost > sim_balance:
                return False
        if a == "clause_bid" and sim_balance is not None:
            cost = _money(
                item.get("bid")
                or item.get("clause")
                or item.get("acquisition_cost")
            )
            if cost > sim_balance:
                return False
        if a == "wait":
            if role in ("alt_if_lost", "alt_unfunded", "alt_no_slot", "also_good"):
                if alt_waits >= max_alt_waits:
                    return False
                alt_waits += 1
            elif role == "out_of_budget":
                if oob_waits >= max_oob_waits:
                    return False
                oob_waits += 1
            elif not _is_daily_market_item(item):
                if pipeline_waits >= max_pipeline_waits:
                    return False
                pipeline_waits += 1
        if len(capped) >= max_total:
            return False
        per_action[a] = per_action.get(a, 0) + 1
        if pid:
            used_ids.add(pid)
        if a == "buy_now" and sim_balance is not None:
            sim_balance = max(0.0, sim_balance - _item_buy_cost(item))
        elif a == "clause_bid" and sim_balance is not None:
            sim_balance = max(
                0.0,
                sim_balance
                - _money(
                    item.get("bid")
                    or item.get("clause")
                    or item.get("acquisition_cost")
                ),
            )
        clean = {k: v for k, v in item.items() if k != "_queue_rank"}
        capped.append(clean)
        return True

    for item in plan:
        if item.get("queue_role") in top_roles:
            _append(item)
    for item in plan:
        if item.get("queue_role") in (
            "alt_if_lost",
            "alt_unfunded",
            "alt_no_slot",
            "also_good",
        ):
            _append(item)
    for item in plan:
        if item.get("queue_role") == "out_of_budget":
            _append(item)
    for item in plan:
        if item.get("queue_role") in (
            *top_roles,
            "alt_if_lost",
            "alt_unfunded",
            "alt_no_slot",
            "also_good",
            "out_of_budget",
        ):
            continue
        if len(capped) >= max_total:
            break
        _append(item)

    spend = 0.0
    for item in capped:
        if item.get("action") == "buy_now" and item.get("queue_role") in buy_roles:
            spend += _item_buy_cost(item)
        elif item.get("action") == "clause_bid":
            spend += _money(
                item.get("bid")
                or item.get("clause")
                or item.get("acquisition_cost")
            )
    residual_after = max(0.0, bal - spend)

    n_buys = n_intents_funded + n_hedges_funded
    if primary:
        hedge_bit = ""
        if n_hedges_funded:
            hedge_bit = (
                " Hedges same-day con puja reducida; "
                "si ganas ambos de una línea, vende el peor al ciclo siguiente."
            )
        note = (
            f"{n_buys} fichaje(s) · cupo {squad_n}/{max_squad} ({free_slots} libres)."
            f"{hedge_bit}"
        )
    elif free_slots <= 0:
        note = (
            f"Plantilla llena ({squad_n}/{max_squad}) — vende/rescinde antes de fichar."
        )
    else:
        note = "Gasta en el 15 ahora. Liquidez = jugadores listados, no reserva."
    if need_slot_sells and slot_sell_ids and primary:
        note = (
            f"{note} Prioriza venta(s) para liberar plaza "
            f"({squad_n}/{max_squad}, faltan ~{max(1, slot_shortfall)})."
        )

    # Refs de hedges ya con pricing aplicado en el plan
    hedge_plan_by_id = {
        str(a.get("player_id") or ""): a
        for a in plan
        if a.get("queue_role") == "hedge"
    }
    slot_sell_refs = [
        {
            "player_id": a.get("player_id"),
            "name": a.get("name"),
            "position": a.get("position"),
            "price": a.get("price"),
            "sell_reason": a.get("sell_reason"),
            "queue_role": "free_slot",
        }
        for a in plan
        if str(a.get("player_id") or "") in slot_sell_ids
    ]
    daily_package: dict[str, Any] = {
        "package_id": package_id,
        "market_mode": "fixed" if fixed else "auction",
        "n_buys": n_buys,
        "n_intents": n_intents_funded,
        "n_hedges": n_hedges_funded,
        "lines": line_meta,
        "squad_size": squad_n,
        "max_squad": max_squad,
        "free_slots": free_slots,
        "slot_shortfall": slot_shortfall,
        "slot_sells": slot_sell_refs,
        "primary": _pkg_player_ref(primary),
        "secondary": _pkg_player_ref(secondary),
        "hedges": [
            {
                **(
                    _pkg_player_ref(
                        hedge_plan_by_id.get(str(h.get("player_id") or "")) or h
                    )
                    or {}
                ),
                "alt_for": iid,
                "queue_role": "hedge",
                "bid_full": (hedge_plan_by_id.get(str(h.get("player_id") or "")) or {}).get(
                    "bid_full"
                ),
                "exit_if_both": "sell_worse_next_cycle",
            }
            for iid, h in hedge_by_intent.items()
            if str(h.get("player_id") or "") in funded_hedge_ids
        ],
        "alts": [
            {
                "player_id": a.get("player_id"),
                "name": a.get("name"),
                "position": a.get("position"),
                "queue_role": a.get("queue_role"),
                "alt_for": a.get("alt_for"),
            }
            for a in demoted[:6]
        ],
        "spend_cap": spend,
        "cash_reserve": cash_reserve,
        "residual_after": residual_after,
        "note": note,
        "cash_reserved_targets": cash_reserved_targets,
        "primary_is_target": bool(
            primary
            and (
                primary.get("is_primary_target")
                or primary.get("is_key_market")
                or str(primary.get("player_id") or "") in primary_ids
            )
        ),
        "policy": "fill_cash_slots",
    }

    return capped, daily_package
