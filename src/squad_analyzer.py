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
from scrapers.ff_points import THIN_APPS, resolve_avg_scale, scale_threshold

# --- Umbrales (buenas prácticas Fantasy) ---
TOP_COUNT_MIN = 3
TOP_COUNT_MAX = 4
TOP_SHARE_MIN = 0.50
TOP_SHARE_MAX = 0.60
BENCH_SHARE_ALERT = 0.15
PATCH_MAX_PRICE = 2_000_000
PATCH_MIN_COUNT = 2
PATCH_IDEAL_COUNT = 3
DF_STARTERS_MIN = int((getattr(config, "STARTERS_TARGET", None) or {}).get("DF", 3))
MF_STARTERS_MIN = int((getattr(config, "STARTERS_TARGET", None) or {}).get("MF", 3))
FW_TOP_MIN = int((getattr(config, "STARTERS_TARGET", None) or {}).get("FW", 2))
IDEAL_SQUAD = dict(getattr(config, "IDEAL_SQUAD", None) or {"GK": 2, "DF": 5, "MF": 5, "FW": 3})
STARTERS_TARGET = dict(
    getattr(config, "STARTERS_TARGET", None) or {"GK": 1, "DF": 3, "MF": 3, "FW": 2}
)
LINEUP_STARTER = getattr(config, "LINEUP_PROB_TITULAR", 0.70)
LINEUP_REGULAR = getattr(config, "LINEUP_PROB_REGULAR", 0.45)
LINEUP_LOW = getattr(config, "LINEUP_PROB_LOW", 0.40)
MINUTES_RECENT_LOW = getattr(config, "MINUTES_RECENT_LOW", 90)


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
    avail = (p.get("external") or {}).get("availability") or p.get("availability")
    return avail in ("injured", "suspended")


def _is_unavailable(p: dict[str, Any]) -> bool:
    """Lesión, sanción o descartado en la previa: no sirve para hueco/upgrade/swap."""
    if _is_injured(p):
        return True
    if p.get("gw_out") or (p.get("external") or {}).get("gw_out"):
        return True
    return False


def _lineup_frac(p: dict[str, Any]) -> float | None:
    """Probabilidad de alineación real 0–1 (ignora once Mister)."""
    ext = p.get("external") or {}
    if ext.get("lineup_prob_ext") is not None:
        try:
            return max(0.0, min(1.0, float(ext["lineup_prob_ext"]) / 100.0))
        except (TypeError, ValueError):
            pass
    if p.get("lineup_prob") is not None:
        try:
            v = float(p["lineup_prob"])
            # Algunos consumidores guardan 0–100
            if v > 1.0:
                v = v / 100.0
            return max(0.0, min(1.0, v))
        except (TypeError, ValueError):
            pass
    return None


def _recent_minutes(p: dict[str, Any]) -> float | None:
    fm = p.get("fotmob_stats") or {}
    if fm.get("minutos_ultimos_5") is None:
        return None
    try:
        return float(fm["minutos_ultimos_5"])
    except (TypeError, ValueError):
        return None


def _is_starter(p: dict[str, Any]) -> bool:
    """Titular real (≥70% alineación). No usa el once fantasy de Mister."""
    if _is_injured(p):
        return False
    lp = _lineup_frac(p)
    return lp is not None and lp >= LINEUP_STARTER


def _is_regular(p: dict[str, Any]) -> bool:
    """Titular o rota con frecuencia real (útil para parches)."""
    if _is_injured(p):
        return False
    lp = _lineup_frac(p)
    return lp is not None and lp >= LINEUP_REGULAR


def _plays_little(p: dict[str, Any]) -> bool:
    """Juega poco: minutos escasos, o % bajo sin evidencia de minutos altos."""
    if _is_injured(p):
        return True
    lp = _lineup_frac(p)
    mins = _recent_minutes(p)
    if mins is not None and mins < MINUTES_RECENT_LOW:
        return True
    if lp is not None and lp < LINEUP_LOW:
        # Minutos altos contradicen un % bajo engañoso / desfasado
        if mins is not None and mins >= MINUTES_RECENT_LOW * 2:
            return False
        return True
    return False


def _is_bench(p: dict[str, Any]) -> bool:
    return not _is_starter(p)


def _in_fantasy_xi(p: dict[str, Any]) -> bool:
    return p.get("in_lineup") is True


def _player_value(p: dict[str, Any]) -> float:
    return max(0.0, _f(p.get("price")))


def _form_score(p: dict[str, Any]) -> float:
    for key in ("mister_avg", "form", "avg_ppg", "prior_avg"):
        v = p.get(key)
        if v is not None and _f(v) > 0:
            return _f(v)
    return 0.0


def _ff_avg(p: dict[str, Any]) -> float | None:
    for key in ("ff_mister_avg",):
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
    if p.get("ff_prior_avg") is not None:
        try:
            return float(p["ff_prior_avg"])
        except (TypeError, ValueError):
            pass
    return None


def _is_top_player(p: dict[str, Any]) -> bool:
    if p.get("is_top_ff"):
        return True
    return bool((p.get("external") or {}).get("is_top_ff"))


def _ff_apps_count(p: dict[str, Any]) -> int:
    """PJ efectivos (temporada actual o previa) para juzgar muestra."""
    cur = _current_ff_apps(p)
    if cur > 0:
        return cur
    return _prior_ff_apps(p)


def _current_ff_apps(p: dict[str, Any]) -> int:
    for source in (p, p.get("external") if isinstance(p.get("external"), dict) else {}):
        for key in ("ff_apps", "apps"):
            if source.get(key) is None:
                continue
            try:
                n = int(source[key])
                if n > 0:
                    return n
            except (TypeError, ValueError):
                pass
    return 0


def _prior_ff_apps(p: dict[str, Any]) -> int:
    for source in (p, p.get("external") if isinstance(p.get("external"), dict) else {}):
        for key in ("ff_prior_apps", "prior_apps"):
            if source.get(key) is None:
                continue
            try:
                n = int(source[key])
                if n > 0:
                    return n
            except (TypeError, ValueError):
                pass
    return 0


def _current_ff_avg(p: dict[str, Any]) -> float | None:
    for source in (p, p.get("external") if isinstance(p.get("external"), dict) else {}):
        if source.get("ff_mister_avg") is None:
            continue
        try:
            v = float(source["ff_mister_avg"])
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return None


def _prior_ff_avg(p: dict[str, Any]) -> float | None:
    for source in (p, p.get("external") if isinstance(p.get("external"), dict) else {}):
        for key in ("ff_prior_avg", "prior_avg"):
            if source.get(key) is None:
                continue
            try:
                v = float(source[key])
                if v > 0:
                    return v
            except (TypeError, ValueError):
                pass
    if p.get("prior_avg") is not None:
        try:
            v = float(p["prior_avg"])
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return None


def comparable_ff_signal(p: dict[str, Any]) -> dict[str, Any]:
    """
    Señal usable para comparar jugadores / justificar VM.
    Preferencia: temporada actual con ≥ THIN_APPS; si no, temporada pasada con ≥ THIN_APPS.
    """
    cur_apps = _current_ff_apps(p)
    cur_avg = _current_ff_avg(p)
    prior_apps = _prior_ff_apps(p)
    prior_avg = _prior_ff_avg(p)
    current_thin = cur_apps < THIN_APPS

    if cur_apps >= THIN_APPS and cur_avg is not None:
        return {
            "usable": True,
            "source": "current",
            "avg": cur_avg,
            "apps": cur_apps,
            "current_thin": False,
            "prior_backed": False,
        }
    if prior_apps >= THIN_APPS and prior_avg is not None:
        return {
            "usable": True,
            "source": "prior",
            "avg": prior_avg,
            "apps": prior_apps,
            "current_thin": current_thin,
            "prior_backed": True,
        }
    return {
        "usable": False,
        "source": None,
        "avg": cur_avg if cur_apps > 0 else prior_avg,
        "apps": cur_apps if cur_apps > 0 else prior_apps,
        "current_thin": current_thin,
        "prior_backed": False,
    }


def lacks_comparable_sample(p: dict[str, Any]) -> bool:
    """True si ni la actual ni la previa aportan ≥ THIN_APPS PJ fiables."""
    return not bool(comparable_ff_signal(p).get("usable"))


def ff_display_fields(p: dict[str, Any]) -> dict[str, Any]:
    """
    Qué media enseñar: actual si ≥ THIN_APPS; si no, temporada pasada;
    si no hay historial en este scoring, solo titularidad.
    """
    sig = comparable_ff_signal(p)
    cur_apps = _current_ff_apps(p)
    if sig.get("source") == "current":
        return {
            "ff_display_source": "current",
            "ff_display_avg": sig.get("avg"),
            "ff_display_apps": sig.get("apps"),
            "ff_note": None,
            "ff_no_history": False,
        }
    if sig.get("source") == "prior":
        avg = sig.get("avg")
        apps = int(sig.get("apps") or 0)
        n_cur = int(cur_apps or 0)
        return {
            "ff_display_source": "prior",
            "ff_display_avg": avg,
            "ff_display_apps": apps,
            "ff_note": (
                f"esta temporada {n_cur} PJ (aún no comparable); "
                f"referencia temp. pasada {float(avg):.1f} · {apps} PJ"
            ),
            "ff_no_history": False,
        }
    if cur_apps > 0:
        return {
            "ff_display_source": "thin",
            "ff_display_avg": None,
            "ff_display_apps": cur_apps,
            "ff_note": f"esta temporada {cur_apps} PJ (aún no comparable)",
            "ff_no_history": False,
        }
    return {
        "ff_display_source": "none",
        "ff_display_avg": None,
        "ff_display_apps": None,
        "ff_note": "Sin historial FF en esta liga — solo titularidad",
        "ff_no_history": True,
    }


def _sample_thin(p: dict[str, Any]) -> bool:
    """True si la temporada actual tiene PJ pero < THIN_APPS (media engañosa sola)."""
    if p.get("sample_thin") is True:
        return True
    if p.get("sample_thin") is False:
        return False
    apps = _current_ff_apps(p)
    return 0 < apps < THIN_APPS


def quality_for_compare(p: dict[str, Any]) -> float:
    """
    Calidad 0–100 para decir si A es mejor que B.
    Con muestra actual corta usa producción si hay previa fiable embebida,
    o escala la media de la temporada pasada.
    """
    sig = comparable_ff_signal(p)
    if sig.get("source") == "current":
        return _prod(p)
    if sig.get("source") == "prior" and sig.get("avg") is not None:
        scale = resolve_avg_scale(p)
        # Misma banda aproximada que production_score (media → ~70 pts)
        return round(max(0.0, min(100.0, (float(sig["avg"]) / scale) * 70.0)), 1)
    if not sig.get("current_thin"):
        return _prod(p)
    return 0.0


def market_value_justification(
    p: dict[str, Any],
    price: float | None = None,
) -> dict[str, Any] | None:
    """ROI / nota de valor usando señal comparable (actual o temporada pasada)."""
    sig = comparable_ff_signal(p)
    if not sig.get("usable") or sig.get("avg") is None:
        return None
    try:
        cost = float(price if price is not None else (_player_value(p) or 0))
    except (TypeError, ValueError):
        cost = 0.0
    if cost <= 0:
        return None
    scale = resolve_avg_scale(p)
    avg = float(sig["avg"])
    roi = (avg / scale * 8.0) / max(cost / 1_000_000.0, 0.4)
    if sig.get("source") == "prior":
        label = f"temp. pasada {avg:.1f} · {int(sig['apps'])} PJ"
        note = f"VM vs {label} (ROI {roi:.1f}/M€)"
    else:
        label = f"media {avg:.1f} · {int(sig['apps'])} PJ"
        note = f"VM vs {label} (ROI {roi:.1f}/M€)"
    return {
        "source": sig.get("source"),
        "avg": avg,
        "apps": int(sig["apps"]),
        "roi": round(roi, 2),
        "justified": roi >= 1.2,
        "expensive": roi < 0.45 and cost >= 5_000_000,
        "note": note,
        "prior_backed": bool(sig.get("prior_backed")),
    }


def _prod(p: dict[str, Any]) -> float:
    v = p.get("production_score")
    if v is None:
        v = (p.get("external") or {}).get("production_score")
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _slim(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "position": p.get("position"),
        "team": p.get("team"),
        "team_id": p.get("team_id"),
        "price": _money(_player_value(p)),
        "lineup_prob": _lineup_frac(p),
        "in_lineup": p.get("in_lineup"),
        "plays_little": _plays_little(p),
        "is_real_starter": _is_starter(p),
        "recent_minutes": _recent_minutes(p),
        "form": p.get("form"),
        "mister_avg": p.get("mister_avg") or p.get("form"),
        "ff_mister_avg": _ff_avg(p),
        "production_score": _prod(p) or None,
        "is_top_ff": _is_top_player(p),
        "top_reason": p.get("top_reason") or (p.get("external") or {}).get("top_reason"),
    }


def _alternate_entry(p: dict[str, Any]) -> dict[str, Any]:
    lp = _lineup_frac(p)
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "price": _money(_player_value(p)),
        "lineup_pct": round((lp or 0) * 100) if lp is not None else None,
        "is_real_starter": _is_starter(p),
        "is_regular": _is_regular(p),
    }


def _enrich_line_depth(
    line: dict[str, Any],
    players: list[dict[str, Any]],
    *,
    position: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Añade cobertura ~15: starters_real, alternates, depth_ok, coverage.
    Emite needs depth_* si faltan alternativas aunque los titulares estén OK.
    """
    tips: list[dict] = []
    needs: list[dict] = []
    healthy = [p for p in players if not _is_injured(p)]
    starters = [p for p in healthy if _is_starter(p)]
    alternates = [p for p in healthy if _is_regular(p) and not _is_starter(p)]
    usable = starters + alternates
    ideal_n = int(IDEAL_SQUAD.get(position, 3))
    starter_tgt = int(STARTERS_TARGET.get(position, 1))
    # Banquillo usable objetivo = ideal - starters target (mín. 1 salvo FW con 1)
    depth_tgt = max(0, ideal_n - starter_tgt)

    starters_ok = len(starters) >= starter_tgt
    depth_ok = len(usable) >= ideal_n or (
        starters_ok and len(alternates) >= max(1, depth_tgt) if depth_tgt else starters_ok
    )
    # Si ideal es 2 (GK) y hay 1 starter + 1 alternate → depth_ok
    if position == "GK":
        depth_ok = len(usable) >= 2 and starters_ok

    if not starters_ok:
        coverage = "critical" if len(starters) == 0 else "thin"
    elif not depth_ok:
        coverage = "thin"
    else:
        coverage = "ok"

    # No bajar status estructural si ya es critical; sí elevar warning por depth
    status = line.get("status") or "ok"
    if coverage == "critical" and status == "ok":
        status = "critical"
    elif coverage == "thin" and status == "ok":
        status = "warning"

    if starters_ok and not depth_ok:
        tips.append(
            _advice(
                "suggestion",
                f"depth_{position.lower()}",
                f"Falta alternativa en {position}",
                (
                    f"Titulares OK ({len(starters)}/{starter_tgt}), pero solo "
                    f"{len(usable)}/{ideal_n} jugadores útiles en {position}. "
                    "Necesitas banquillo ante sanciones/lesiones/malos rivales."
                ),
                position=position,
            )
        )
        needs.append(
            {
                "need": f"depth_{position.lower()}",
                "position": position,
                "priority": "Media",
                "max_price": PATCH_MAX_PRICE * 2 if position != "FW" else None,
                "reason": f"Profundidad {position}: falta alternativa usable",
            }
        )

    out = {
        **line,
        "status": status,
        "starters_real": len(starters),
        "starters_target": starter_tgt,
        "alternates_count": len(alternates),
        "usable_count": len(usable),
        "ideal_count": ideal_n,
        "depth_ok": depth_ok,
        "coverage": coverage,
        "alternates": [_alternate_entry(p) for p in alternates[:4]],
        "starters_list": [_alternate_entry(p) for p in starters[:5]],
    }
    return out, tips, needs


def position_coverage_map(diagnostico: dict[str, Any] | None) -> dict[str, str]:
    """pos → critical|thin|ok desde diagnostico_plantilla.lineas."""
    out: dict[str, str] = {}
    lineas = (diagnostico or {}).get("lineas") or {}
    for pos in ("GK", "DF", "MF", "FW"):
        info = lineas.get(pos) or {}
        cov = info.get("coverage")
        if cov in ("critical", "thin", "ok"):
            out[pos] = cov
        else:
            st = info.get("status") or "ok"
            out[pos] = "critical" if st == "critical" else ("thin" if st == "warning" else "ok")
    return out


def _line_starters_real(
    position: str,
    diagnostico: dict[str, Any] | None,
    squad: list[dict[str, Any]] | None,
) -> int:
    """Titulares usables (≥ LINEUP_STARTER, sanos) en una posición."""
    pos = position or "MF"
    lineas = (diagnostico or {}).get("lineas") or {}
    info = lineas.get(pos) or {}
    try:
        n = int(info.get("starters_real") or 0)
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        return n
    if not squad:
        return 0
    return sum(
        1
        for p in squad
        if p.get("position") == pos and not _is_injured(p) and _is_starter(p)
    )


def _line_healthy_count(
    position: str,
    diagnostico: dict[str, Any] | None,
    squad: list[dict[str, Any]] | None,
) -> int:
    """Jugadores sanos en la posición (headcount, no usables)."""
    pos = position or "MF"
    lineas = (diagnostico or {}).get("lineas") or {}
    info = lineas.get(pos) or {}
    try:
        n = int(info.get("count") or 0)
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        return n
    if not squad:
        return 0
    return sum(
        1 for p in squad if p.get("position") == pos and not _is_injured(p)
    )


def _gk_starters_real(
    diagnostico: dict[str, Any] | None,
    squad: list[dict[str, Any]] | None,
) -> int:
    """Titulares GK usables (≥ LINEUP_STARTER, sanos)."""
    return _line_starters_real("GK", diagnostico, squad)


def is_line_overstocked(
    position: str,
    diagnostico: dict[str, Any] | None = None,
    squad: list[dict[str, Any]] | None = None,
) -> bool:
    """
    Línea sobrada: cupo ≥ ideal y titulares reales ≥ objetivo.
    Se recalcula cada snapshot (adaptativo; no hardcodea conteos).
    """
    pos = position or "MF"
    ideal = int(IDEAL_SQUAD.get(pos, 3))
    starter_tgt = int(STARTERS_TARGET.get(pos, 1))
    healthy = _line_healthy_count(pos, diagnostico, squad)
    starters = _line_starters_real(pos, diagnostico, squad)
    return healthy >= ideal and starters >= starter_tgt


def _player_matches_gk_tandem(
    player: dict[str, Any],
    diagnostico: dict[str, Any] | None,
) -> bool:
    """True si el GK del mercado encaja en need gk_tandem (mismo club)."""
    if player.get("position") != "GK":
        return False
    team = (player.get("team") or "").lower()
    team_id = str(player.get("team_id") or "")
    for need in (diagnostico or {}).get("structural_needs") or []:
        if need.get("need") != "gk_tandem":
            continue
        want_id = str(need.get("same_team_id") or "")
        want_team = (need.get("same_team_as") or "").lower()
        if want_id and team_id and team_id == want_id:
            return True
        if want_team and want_team in team:
            return True
    return False


def assess_market_coverage(
    player: dict[str, Any],
    diagnostico: dict[str, Any] | None,
    *,
    squad: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    ¿El fichaje cubre un hueco de cobertura o la línea ya está OK?
    is_upgrade: mejora clara vs el peor usable de la línea.

    GK con titular usable: el hueco real es tándem mismo club o parche ≤4M;
    un titular caro de otro club no cubre gap ni cuenta como upgrade.

    Outfield con titulares OK: solo profundidad barata (≤4M) cubre gap de depth.
    Línea overstocked (cupo≥ideal y starters≥target): no gap caro; upgrades no urgentes.
    """
    pos = player.get("position") or "MF"
    cov_map = position_coverage_map(diagnostico)
    coverage = cov_map.get(pos, "ok")
    overstocked = is_line_overstocked(pos, diagnostico, squad)

    # Lesionado / sancionado / fuera de previa: nunca “cubre hueco” ni upgrade
    if _is_unavailable(player):
        return {
            "position_coverage": coverage,
            "fills_coverage_gap": False,
            "line_already_covered": True,
            "is_upgrade": False,
            "coverage_label": "No disponible",
            "overstocked": overstocked,
        }

    fills_gap = coverage in ("critical", "thin")
    line_covered = coverage == "ok"

    lp = _lineup_frac(player)
    is_upgrade = False
    label_override: str | None = None
    block_upgrade = False
    starters_real = _line_starters_real(pos, diagnostico, squad)
    starter_tgt = int(STARTERS_TARGET.get(pos, 1))
    price = float(_player_value(player) or 0)
    depth_cap = float(PATCH_MAX_PRICE * 2)

    if pos == "GK" and coverage != "critical":
        if starters_real >= 1:
            if _player_matches_gk_tandem(player, diagnostico):
                fills_gap = True
                line_covered = False
                label_override = "Tándem portero"
            elif price > 0 and price <= depth_cap:
                fills_gap = True
                line_covered = False
            else:
                # Titular propio OK: no gastar caja en 2.º titular de otro club
                fills_gap = False
                line_covered = True
                block_upgrade = True
    elif pos in ("DF", "MF", "FW") and coverage != "critical":
        # Titulares OK: hueco real = profundidad barata, no otro titular caro
        if starters_real >= starter_tgt:
            if coverage == "thin" and price > 0 and price <= depth_cap:
                fills_gap = True
                line_covered = False
            else:
                fills_gap = False
                line_covered = True

    # Sobrecupo con cobertura ok: nunca gap (upgrades se calculan aparte)
    if overstocked and coverage == "ok":
        fills_gap = False
        line_covered = True

    if line_covered and squad and not block_upgrade:
        same = [
            p
            for p in squad
            if p.get("position") == pos and not _is_injured(p) and _is_regular(p)
        ]
        if same:
            worst_lp = min((_lineup_frac(p) or 0.0) for p in same)
            player_lp = lp or 0.0
            player_top = _is_top_player(player)
            worst_top = any(_is_top_player(p) for p in same)
            if player_lp >= LINEUP_STARTER and player_lp >= worst_lp + 0.15:
                is_upgrade = True
            elif player_top and not worst_top and not lacks_comparable_sample(player):
                is_upgrade = True
            elif (
                not lacks_comparable_sample(player)
                and quality_for_compare(player) >= 65
                and max(quality_for_compare(p) for p in same) < 50
            ):
                is_upgrade = True
        elif (
            lp is not None
            and lp >= LINEUP_STARTER
            and _is_top_player(player)
            and not lacks_comparable_sample(player)
        ):
            is_upgrade = True

    if is_upgrade and lacks_comparable_sample(player):
        is_upgrade = False

    label = None
    if label_override:
        label = label_override
    elif fills_gap:
        label = "Cubre hueco"
    elif is_upgrade:
        label = "Upgrade"
    elif line_covered:
        label = "Ya cubierto"

    return {
        "position_coverage": coverage,
        "fills_coverage_gap": fills_gap,
        "line_already_covered": line_covered and not is_upgrade,
        "is_upgrade": is_upgrade,
        "coverage_label": label,
        "overstocked": overstocked,
    }


def is_clear_overstock_upgrade(
    player: dict[str, Any],
    squad: list[dict[str, Any]] | None = None,
    *,
    is_upgrade: bool = False,
) -> bool:
    """
    Mejora clara en línea sobrada: titular ≥70% y (Δlineup ≥+0.20 vs peor titular,
    TOP FF, o prod≥65 con el peor de la línea <50).
    """
    if not is_upgrade or _is_unavailable(player):
        return False
    lp = _lineup_frac(player)
    if lp is None or lp < LINEUP_STARTER:
        return False
    pos = player.get("position") or "MF"
    same = [
        p
        for p in (squad or [])
        if p.get("position") == pos and not _is_injured(p) and _is_regular(p)
    ]
    if not same:
        return bool(_is_top_player(player))
    starters = [p for p in same if (_lineup_frac(p) or 0.0) >= LINEUP_STARTER]
    pool = starters or same
    worst_lp = min((_lineup_frac(p) or 0.0) for p in pool)
    if lp >= worst_lp + 0.20:
        return True
    if _is_top_player(player) and not lacks_comparable_sample(player):
        return True
    if (
        not lacks_comparable_sample(player)
        and quality_for_compare(player) >= 65
        and max(quality_for_compare(p) for p in same) < 50
    ):
        return True
    return False


def upgrade_worth_buy(
    player: dict[str, Any],
    *,
    is_upgrade: bool = False,
    overstocked: bool = False,
    squad: list[dict[str, Any]] | None = None,
    budget_fit: str | None = None,
    debt_risk: bool = False,
    solvency_blocked: bool = False,
    leaves_gap_budget: bool | None = None,
    crowds_out_gaps: bool | None = None,
    residual: float | None = None,
    other_gaps_min: float | None = None,
    cash_reserve: float | None = None,
) -> bool:
    """
    ¿Renta pujar un upgrade aunque la línea esté cubierta/sobrada?
    Exige mejora clara (si overstock), caja usable y residual para otras carencias.
    """
    if not is_upgrade:
        return False
    if solvency_blocked or debt_risk:
        return False
    if (budget_fit or "blocked") not in ("comfortable", "tight"):
        return False
    if crowds_out_gaps:
        return False

    reserve = float(
        cash_reserve
        if cash_reserve is not None
        else getattr(config, "PACKAGE_CASH_RESERVE", 0)
    )
    other_min = float(other_gaps_min or 0)
    if other_min > 0:
        if leaves_gap_budget is False:
            return False
        if leaves_gap_budget is None and residual is not None and residual < other_min:
            return False
    elif residual is not None and residual < reserve:
        return False

    if overstocked:
        return is_clear_overstock_upgrade(player, squad, is_upgrade=True)
    # Línea cubierta sin sobrecupo: basta is_upgrade + finanzas
    lp = _lineup_frac(player)
    return lp is not None and lp >= LINEUP_STARTER


def realistic_price_cap(balance: float) -> float:
    """Techo de gasto realista: el saldo usable hoy (sin congelar caja)."""
    reserve = float(getattr(config, "PACKAGE_CASH_RESERVE", 0) or 0)
    return max(0.0, float(balance or 0) - reserve)


def apply_realistic_need_caps(
    needs: list[dict[str, Any]] | None,
    balance: float,
) -> list[dict[str, Any]]:
    """
    Ancla max_price de needs a la caja real (saldo usable hoy).
    Needs sin techo (starters) heredan el cap; depth/parche se limitan más.
    """
    cap = realistic_price_cap(balance)
    depth_cap = min(4_000_000.0, cap) if cap > 0 else 0.0
    out: list[dict[str, Any]] = []
    for raw in needs or []:
        n = dict(raw)
        ntype = str(n.get("need") or "")
        prio = str(n.get("priority") or "Media")
        current = n.get("max_price")
        try:
            current_f = float(current) if current is not None else None
        except (TypeError, ValueError):
            current_f = None

        if ntype == "patch_cheap" or ntype.startswith("depth_"):
            target = depth_cap if depth_cap > 0 else float(PATCH_MAX_PRICE)
            if current_f is not None:
                target = min(current_f, target) if target > 0 else min(current_f, float(PATCH_MAX_PRICE))
            n["max_price"] = int(target)
        elif current_f is None:
            # Starters / fw_top / gk_*: techo = cap completo (Alta) o depth_cap
            n["max_price"] = int(cap if prio == "Alta" else depth_cap)
        else:
            n["max_price"] = int(min(current_f, cap) if cap > 0 else current_f)

        n["realistic_cap"] = int(cap)
        out.append(n)
    return out


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
    *,
    market_universe: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # TOP = élite por producción FF (is_top_ff); fallback percentil precio vs mercado
    top_players = [p for p in squad if _is_top_player(p)]
    if not top_players:
        universe = list(market_universe or []) + list(squad)
        prices = sorted(float(x.get("price") or 0) for x in universe if float(x.get("price") or 0) > 0)
        cut = prices[int(len(prices) * 0.85)] if len(prices) >= 10 else None
        if cut:
            top_players = [p for p in squad if _player_value(p) >= cut]
        else:
            # Último recurso: no inventar TOP por ranking interno salvo vacío total
            top_players = []

    # Ordenar TOP por producción luego precio
    top_players = sorted(
        top_players,
        key=lambda p: (_prod(p), _ff_avg(p) or 0, _player_value(p)),
        reverse=True,
    )
    top_ids = {str(p.get("id")) for p in top_players}
    top_value = sum(_player_value(p) for p in top_players)
    plantilla = max(squad_value, 1.0)
    top_share = top_value / plantilla if top_players else 0.0

    top_ok = (
        TOP_COUNT_MIN <= len(top_players) <= TOP_COUNT_MAX
        and TOP_SHARE_MIN <= top_share <= TOP_SHARE_MAX
    )
    if len(top_players) < TOP_COUNT_MIN or (top_players and top_share < TOP_SHARE_MIN):
        top_status = "critical" if len(top_players) < 2 or top_share < 0.35 else "warning"
    elif top_share > TOP_SHARE_MAX or len(top_players) > TOP_COUNT_MAX:
        top_status = "warning"
    else:
        top_status = "ok" if top_players else "warning"

    # Banquillo inflado: no titulares con valor alto
    bench_heavy = [
        p
        for p in squad
        if _is_bench(p) and _player_value(p) > 0 and str(p.get("id")) not in top_ids
    ]
    bench_tops = [p for p in top_players if _is_bench(p)]
    bench_flagged = sorted(
        {str(p.get("id")): p for p in (bench_heavy + bench_tops)}.values(),
        key=_player_value,
        reverse=True,
    )
    expensive_bench = [p for p in bench_flagged if _player_value(p) >= plantilla * 0.08]
    bench_value = sum(_player_value(p) for p in expensive_bench)
    if not expensive_bench:
        all_bench = [p for p in squad if _is_bench(p) and _player_value(p) > 0]
        bench_value = sum(_player_value(p) for p in all_bench)
        expensive_bench = all_bench
    bench_share = bench_value / plantilla
    bench_inflated = bench_share > BENCH_SHARE_ALERT and bench_value > 0

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
        v_mid = v_mid_known + residual
        v_bench = v_bench_known
    else:
        v_mid, v_bench = v_mid_known, v_bench_known
    denom = max(v_top + v_mid + v_bench, 1.0)

    top_msg = (
        f"{len(top_players)} estrellas FF concentran el {top_share * 100:.0f}% del valor de plantilla."
        if top_players
        else "Sin estrellas TOP por producción FF (ni fallback de mercado)."
    )

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
            "message": top_msg,
            "basis": "ff_mister_mixto",
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
            f"Defensa con {len(starters)} titulares reales sanos. "
            "Prioriza laterales/carrileros al fichar (más puntos Fantasy que centrales)."
        )
        tips.append(_advice("ok", "df_ok", "Defensa estable", message, position="DF"))
    elif len(starters) >= 2:
        status = "warning"
        message = (
            f"Solo {len(starters)} defensas con titularidad real (ideal >={DF_STARTERS_MIN}). "
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
        message = f"Línea defensiva crítica: {len(starters)} titulares reales sanos."
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
    # En temporada activa exigimos promedio; en pretemporada basta titularidad + FF
    if points_phase == "active":
        quality = [
            p
            for p in starters
            if _form_score(p) >= scale_threshold(4.5, resolve_avg_scale(p))
            or (_ff_avg(p) or 0) >= scale_threshold(4.5, resolve_avg_scale(p))
        ]
    else:
        quality = [
            p
            for p in starters
            if (_ff_avg(p) or 0) >= scale_threshold(4.0, resolve_avg_scale(p))
            or _prod(p) >= 45
            or _form_score(p) >= scale_threshold(4.5, resolve_avg_scale(p))
        ]
        if len(quality) < MF_STARTERS_MIN:
            # Ampliar a titulares aunque sin FF (datos incompletos)
            quality = starters

    if len(quality) >= MF_STARTERS_MIN:
        status = "ok"
        message = f"Centrocampo sólido: {len(quality)} titulares reales como motor del equipo."
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
    little = [p for p in healthy if _plays_little(p)]
    fantasy_xi = [p for p in healthy if _in_fantasy_xi(p)]
    top_ids = {str(p.get("id")) for p in finance.get("top_players") or []}
    # Referencias TOP: delanteros TOP FF o alta producción (sin fallback solo por precio)
    fw_tops = [
        p
        for p in players
        if _is_top_player(p)
        or str(p.get("id")) in top_ids
        or (_ff_avg(p) or 0) >= scale_threshold(5.5, resolve_avg_scale(p))
    ]
    if len(fw_tops) < FW_TOP_MIN:
        fw_sorted = sorted(
            players, key=lambda p: (_prod(p), _ff_avg(p) or 0, _player_value(p)), reverse=True
        )
        fw_tops = [
            p
            for p in fw_sorted
            if (_ff_avg(p) or 0) >= scale_threshold(4.8, resolve_avg_scale(p)) or _prod(p) >= 55
        ][:FW_TOP_MIN]

    suggested_formation = None
    n = len(players)
    n_real = len(starters)

    if len(fw_tops) >= FW_TOP_MIN and n_real >= 2:
        status = "ok"
        message = f"Delantera con {len(fw_tops)} referencias / TOP y {n_real} titulares reales."
        tips.append(_advice("ok", "fw_ok", "Punta de lanza OK", message, position="FW"))
    elif n >= 2 and n_real < 2:
        status = "critical" if n_real == 0 else "warning"
        fant = f" ({len(fantasy_xi)} en tu once Mister)" if fantasy_xi else ""
        message = (
            f"Tienes {n} delanteros, pero solo {n_real} con titularidad real{fant}. "
            f"{len(little)} juega(n) poco."
        )
        tips.append(
            _advice(
                "alert" if status == "critical" else "suggestion",
                "fw_low_minutes",
                "Delantera sin minutos",
                message,
                position="FW",
                related=[str(p.get("id")) for p in little[:3] if p.get("id")],
            )
        )
        needs.append(
            {
                "need": "fw_top",
                "position": "FW",
                "priority": "Alta",
                "max_price": None,
                "min_price": 4_000_000,
                "reason": "Faltan delanteros con titularidad real",
            }
        )
        if balance < 5_000_000:
            suggested_formation = "4-5-1 o 3-5-2"
    elif len(fw_tops) >= 1:
        status = "warning"
        can_buy_third = balance >= 5_000_000
        if not can_buy_third:
            suggested_formation = "4-5-1 o 3-5-2"
            message = (
                f"Solo {len(fw_tops)} delantero(s) referencia ({n_real} titular(es) real(es)). "
                f"Sin caja clara para un 3º TOP: valora sistema {suggested_formation}."
            )
        else:
            message = (
                f"Solo {len(fw_tops)} delantero referencia / {n_real} titulares reales "
                f"(ideal >={FW_TOP_MIN})."
            )
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
        message = (
            f"Sin delanteros TOP claros ({n_real} titulares reales). "
            "Remonta con un 9 o cambia a 4-5-1 / 3-5-2."
        )
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
            "count": n,
            "healthy": len(healthy),
            "starters": n_real,
            "plays_little": len(little),
            "fantasy_xi": len(fantasy_xi),
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
        if 0 < _player_value(p) <= PATCH_MAX_PRICE
        and _is_regular(p)
        and not _is_injured(p)
        and (
            _ff_avg(p) is None
            or (_ff_avg(p) or 0) >= scale_threshold(3.0, resolve_avg_scale(p))
            or _prod(p) >= 35
        )
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
            and (
                _prod(p) >= 30
                or (_ff_avg(p) or 0) >= scale_threshold(3.0, resolve_avg_scale(p))
                or _ff_avg(p) is None
            )
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
    market_universe: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Auditoría táctica + financiera completa.

    Returns:
        dict listo para `diagnostico_plantilla` en latest_data.json
    """
    plantilla = _squad_value_fallback(squad, squad_value)
    finance = _analyze_finance(
        squad,
        float(balance or 0),
        plantilla,
        market_universe=market_universe,
    )
    by_pos: dict[str, list] = {"GK": [], "DF": [], "MF": [], "FW": []}
    for p in squad:
        by_pos.setdefault(p.get("position") or "MF", []).append(p)

    tips: list[dict] = []
    needs: list[dict] = []
    lineas: dict[str, Any] = {}

    gk, t, n = _analyze_gk(by_pos["GK"])
    gk, t2, n2 = _enrich_line_depth(gk, by_pos["GK"], position="GK")
    lineas["GK"] = gk
    tips.extend(t)
    tips.extend(t2)
    needs.extend(n)
    needs.extend(n2)

    df, t, n = _analyze_df(by_pos["DF"])
    df, t2, n2 = _enrich_line_depth(df, by_pos["DF"], position="DF")
    lineas["DF"] = df
    tips.extend(t)
    tips.extend(t2)
    needs.extend(n)
    needs.extend(n2)

    mf, t, n = _analyze_mf(by_pos["MF"], points_phase=points_phase)
    mf, t2, n2 = _enrich_line_depth(mf, by_pos["MF"], position="MF")
    lineas["MF"] = mf
    tips.extend(t)
    tips.extend(t2)
    needs.extend(n)
    needs.extend(n2)

    fw, t, n = _analyze_fw(by_pos["FW"], finance, float(balance or 0))
    fw, t2, n2 = _enrich_line_depth(fw, by_pos["FW"], position="FW")
    lineas["FW"] = fw
    tips.extend(t)
    tips.extend(t2)
    needs.extend(n)
    needs.extend(n2)

    # Tips financieros
    tc = finance["top_check"]
    if tc["status"] == "ok":
        tips.insert(
            0,
            _advice(
                "ok",
                "top_balance",
                "Estrellas bien dimensionadas",
                tc["message"] + " (rango sano: 3–4 TOP por producción FF ≈ 50–60% del valor).",
            ),
        )
    elif tc["status"] != "ok":
        top_n = int(tc.get("count") or 0)
        if top_n <= 0:
            title = "Faltan estrellas TOP"
        elif top_n < TOP_COUNT_MIN or float(tc.get("share_pct") or 0) < TOP_SHARE_MIN * 100:
            title = "Faltan estrellas TOP"
        else:
            title = "Reequilibra las estrellas"
        tips.insert(
            0,
            _advice(
                "suggestion" if tc["status"] == "warning" else "alert",
                "top_imbalance",
                title,
                tc["message"] + " Ideal: 3–4 TOP FF con el 50–60% del valor de plantilla.",
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
        "ideal_squad": dict(IDEAL_SQUAD),
        "starters_target": dict(STARTERS_TARGET),
        "lines_ok": sum(1 for pos in ("GK", "DF", "MF", "FW") if (lineas.get(pos) or {}).get("coverage") == "ok"),
        "depth_gaps": sum(
            1
            for pos in ("GK", "DF", "MF", "FW")
            if (lineas.get(pos) or {}).get("coverage") in ("critical", "thin")
        ),
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
        cur["coverage"] = info.get("coverage") or new_status
        cur["starters_real"] = info.get("starters_real", info.get("starters"))
        cur["depth_ok"] = info.get("depth_ok")
        cur["alternates_count"] = info.get("alternates_count")
        cur["ideal_count"] = info.get("ideal_count")
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
    if not needs or _is_unavailable(player):
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

        # Precio por encima del techo realista del need → no cubre hueco prioritario
        max_p_need = need.get("max_price")
        try:
            max_p_f = float(max_p_need) if max_p_need is not None else None
        except (TypeError, ValueError):
            max_p_f = None
        if max_p_f is not None and price > 0 and price > max_p_f:
            continue

        if ntype in (
            "gk_backup",
            "df_starter",
            "mf_starter",
            "fw_top",
            "depth_gk",
            "depth_df",
            "depth_mf",
            "depth_fw",
        ) and npos and pos != npos:
            continue
        if ntype == "gk_tandem" and pos != "GK":
            continue

        if ntype == "patch_cheap":
            max_p = max_p_f if max_p_f is not None else float(PATCH_MAX_PRICE)
            if price <= 0 or price > max_p:
                continue
            # Parche: barato; aún mejor si parece titular/regular real
            lp = _lineup_frac(player)
            if lp is None:
                lp = _f(player.get("lineup_prob"))
            bonus = 22.0
            if lp is not None and lp >= LINEUP_REGULAR:
                bonus += 10.0
            this_label = "Parche estructural"
        elif ntype.startswith("depth_") and npos and pos == npos:
            lp = _lineup_frac(player)
            if lp is not None and lp < LINEUP_REGULAR:
                continue
            bonus = 18.0
            if lp is not None and lp >= LINEUP_STARTER:
                bonus += 6.0
            this_label = f"Profundidad {pos}"
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
            lp = _lineup_frac(player)
            if lp is not None and lp < LINEUP_REGULAR:
                continue  # no cubre carencia de titular real
            bonus = 25.0
            if lp is not None and lp >= LINEUP_STARTER:
                bonus += 8.0
            this_label = "Refuerzo defensa"
        elif ntype == "mf_starter" and pos == "MF":
            lp = _lineup_frac(player)
            if lp is not None and lp < LINEUP_REGULAR:
                continue
            bonus = 25.0
            if lp is not None and lp >= LINEUP_STARTER:
                bonus += 8.0
            this_label = "Motor mediocampo"
        elif ntype == "fw_top" and pos == "FW":
            lp = _lineup_frac(player)
            if lp is not None and lp < LINEUP_REGULAR:
                continue  # la carencia es de titularidad real, no de cupo FW
            min_p = need.get("min_price") or 0
            if price and price < min_p * 0.6:
                bonus = 12.0
                this_label = "Opción delantera"
            else:
                bonus = 30.0
                this_label = "Delantero referencia"
            if lp is not None and lp >= LINEUP_STARTER:
                bonus += 10.0
        else:
            continue

        if need.get("priority") == "Alta":
            bonus += 5.0
        if bonus > best:
            best = bonus
            label = this_label
            matched = True

    return best, matched, label
