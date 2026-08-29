"""
Recorte del JSON público que consume la PWA.

El pipeline sigue calculando el universo completo en memoria (y el history
slim de precios se construye ANTES de recortar). A disco solo va la ficha
que pinta la app: cola, once, plantilla propia y listados de mercado con
campos de decisión. Sin plantillas rivales completas ni 100+ claves por
jugador del pool.
"""

from __future__ import annotations

from typing import Any

# Libres TOP que la pestaña Radar puede recorrer. El pool completo no pinta.
FREE_AGENTS_PUBLIC_CAP = 48
# Ranking Mister “más robado por cláusula”. El aviso oficial llega hasta 100;
# el dashboard enseña el top útil.
CLAUSES_RANKING_PUBLIC_CAP = 25

# Ficha de listado (mercado / libres / plantilla). Lo que app.js lee.
_PLAYER_KEYS = (
    "id",
    "player_id",
    "name",
    "position",
    "team",
    "team_id",
    "photo_url",
    "team_logo_url",
    "price",
    "form",
    "mister_avg",
    "avg_ppg",
    "reliability",
    "roi_ppg_per_million",
    "lineup_prob",
    "injury",
    "in_lineup",
    "trend",
    "delta_5d",
    "delta_cycle",
    "delta_1d",
    "accel",
    "decelerating",
    "rising",
    "consecutive_up",
    "abs_gain",
    "price_delta_1d",
    "points",
    "gw_points",
    "gw_blank",
    "gw_out",
    "gw_probable_xi",
    "points_trend",
    "ff_mister_avg",
    "ff_apps",
    "ff_prior_avg",
    "ff_prior_apps",
    "ff_display_source",
    "ff_display_avg",
    "ff_display_apps",
    "ff_note",
    "ff_no_history",
    "current_sample_thin",
    "sample_thin",
    "prior_backed",
    "is_top_ff",
    "production_score",
    "xpts",
    "xpts_floor",
    "p_play",
    "fdr",
    "fdr_label",
    "fdr_why",
    "opponent_name",
    "is_home",
    "gw_opponent",
    "priority",
    "priority_score",
    "category",
    "category_label",
    "categories",
    "action",
    "puja_recomendada",
    "puja_techo",
    "bid",
    "bid_ceiling",
    "min_bid",
    "on_daily_market",
    "seller",
    "owner_id",
    "owner_name",
    "fills_need",
    "fills_coverage_gap",
    "fills_structural",
    "is_upgrade",
    "line_already_covered",
    "overstocked",
    "upgrade_worth_buy",
    "structural_label",
    "why",
    "wait_risk",
    "urgency",
    "listed_by_rival",
    "listed_by_name",
    "appreciation_play",
    "target_tier",
    "budget_fit",
    "affordable",
    "is_key_market",
    "is_board_objective",
    "is_primary_target",
    "clause",
    "clause_known",
    "clause_rank",
    "clause_multiplier",
    "owner_kind",
    "profile_url",
    "queue_role",
    "package_note",
    "why_free",
    "owner_team",
    "owner_rank",
    "market_value",
    "compared_to",
    "upgrade_score",
    "top_reason",
    "lineup_pct",
)

_SQUAD_EXTRA = (
    "gw_played",
    "gw_match_status",
    "gw_kickoff",
    "gw_starter",
    "gw_confirmed",
    "xpts_why",
    "fdr_why",
    "fdr_multiplier",
)

_EXTERNAL_KEYS = (
    "availability",
    "lineup_prob_ext",
    "recent_rating",
    "profile_url",
    "ff_profile_url",
    "ff_mister_avg",
    "ff_apps",
    "ff_prior_avg",
    "ff_prior_apps",
    "is_top_ff",
)

_FOTMOB_KEYS = ("rating_promedio", "minutos_ultimos_5", "goles_ultimos_5")

_RIVAL_KEYS = (
    "team_id",
    "manager",
    "team_name",
    "rank",
    "points",
    "squad_size",
    "squad_value",
    "liquidity_estimated",
    "bid_cap_estimated",
    "recent_net",
    "position_gaps",
    "activity",
    "key_players",
)

_KEY_PLAYER_KEYS = ("id", "name", "position", "price", "team")


def _pick(src: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in keys:
        if key not in src:
            continue
        val = src[key]
        if val is None or val == "" or val == [] or val == {}:
            continue
        out[key] = val
    return out


def slim_external(ext: Any) -> dict[str, Any] | None:
    if not isinstance(ext, dict):
        return None
    picked = _pick(ext, _EXTERNAL_KEYS)
    return picked or None


def slim_fotmob(fm: Any) -> dict[str, Any] | None:
    if not isinstance(fm, dict):
        return None
    picked = _pick(fm, _FOTMOB_KEYS)
    return picked or None


def slim_player(player: dict[str, Any] | None, *, squad: bool = False) -> dict[str, Any]:
    """Ficha corta para listados. `squad=True` conserva señales de jornada propias."""
    if not isinstance(player, dict):
        return {}
    keys = _PLAYER_KEYS + (_SQUAD_EXTRA if squad else ())
    out = _pick(player, keys)
    ext = slim_external(player.get("external"))
    if ext:
        out["external"] = ext
    fm = slim_fotmob(player.get("fotmob_stats"))
    if fm:
        out["fotmob_stats"] = fm
    return out


def slim_player_list(rows: Any, *, squad: bool = False, cap: int | None = None) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    items = rows[:cap] if cap is not None else rows
    return [slim_player(p, squad=squad) for p in items if isinstance(p, dict)]


def slim_rival(rival: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(rival, dict):
        return {}
    out = _pick(rival, _RIVAL_KEYS)
    keys = rival.get("key_players")
    if isinstance(keys, list):
        out["key_players"] = [_pick(p, _KEY_PLAYER_KEYS) for p in keys if isinstance(p, dict)]
    return out


def _slim_diag_players(info: dict[str, Any]) -> dict[str, Any]:
    out = dict(info)
    for key in ("players", "starters_list", "alternates"):
        if isinstance(out.get(key), list):
            out[key] = slim_player_list(out[key], squad=True)
    return out


def slim_diagnostico(diag: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(diag, dict):
        return {}
    out = dict(diag)
    lineas = out.get("lineas")
    if isinstance(lineas, dict):
        out["lineas"] = {
            pos: _slim_diag_players(info) if isinstance(info, dict) else info
            for pos, info in lineas.items()
        }
    fin = out.get("financiero")
    if isinstance(fin, dict):
        fin = dict(fin)
        if isinstance(fin.get("top_players"), list):
            fin["top_players"] = slim_player_list(fin["top_players"], squad=True)
        bench = fin.get("bench_inflated")
        if isinstance(bench, dict) and isinstance(bench.get("players"), list):
            bench = dict(bench)
            bench["players"] = slim_player_list(bench["players"], squad=True)
            fin["bench_inflated"] = bench
        out["financiero"] = fin
    # Duplicados del payload raíz: no hacen falta en el JSON público
    for dup in ("matchday", "sales_state", "market_cycle", "bootstrap_xi"):
        out.pop(dup, None)
    return out


def slim_squad_diagnosis(diag: dict[str, Any] | None) -> dict[str, Any]:
    """Alertas + conteos por línea. La ficha de cada jugador vive en me.squad."""
    if not isinstance(diag, dict):
        return {}
    out = dict(diag)
    by_pos = out.get("by_position")
    if not isinstance(by_pos, dict):
        return out
    slim_pos: dict[str, Any] = {}
    for pos, info in by_pos.items():
        if not isinstance(info, dict):
            slim_pos[pos] = info
            continue
        info = dict(info)
        if isinstance(info.get("players"), list):
            info["players"] = slim_player_list(info["players"], squad=True)
        slim_pos[pos] = info
    out["by_position"] = slim_pos
    return out


def slim_public_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """
    Copia pública del snapshot. No muta el dict original.
    Idempotente: volver a recortar un payload ya slim no pierde campos de UI.
    """
    if not isinstance(payload, dict):
        return {}
    out = dict(payload)
    me = out.get("me")
    if isinstance(me, dict):
        me = dict(me)
        me["squad"] = slim_player_list(me.get("squad"), squad=True)
        out["me"] = me

    out["market_opportunities"] = slim_player_list(out.get("market_opportunities"))
    out["free_agents_top"] = slim_player_list(
        out.get("free_agents_top"), cap=FREE_AGENTS_PUBLIC_CAP
    )
    out["clauses_ranking"] = slim_player_list(
        out.get("clauses_ranking"), cap=CLAUSES_RANKING_PUBLIC_CAP
    )
    out["rivals"] = [
        slim_rival(r) for r in (out.get("rivals") or []) if isinstance(r, dict)
    ]
    if isinstance(out.get("rival_upgrades"), list):
        out["rival_upgrades"] = [
            slim_player(u) if isinstance(u, dict) else u
            for u in out["rival_upgrades"]
        ]
    if isinstance(out.get("diagnostico_plantilla"), dict):
        out["diagnostico_plantilla"] = slim_diagnostico(out["diagnostico_plantilla"])
    if isinstance(out.get("squad_diagnosis"), dict):
        out["squad_diagnosis"] = slim_squad_diagnosis(out["squad_diagnosis"])

    meta = dict(out.get("meta") or {})
    meta["payload"] = {
        "slim": True,
        "free_agents_cap": FREE_AGENTS_PUBLIC_CAP,
        "clauses_ranking_cap": CLAUSES_RANKING_PUBLIC_CAP,
        "rival_squads": False,
        "market_n": len(out.get("market_opportunities") or []),
        "free_n": len(out.get("free_agents_top") or []),
        "clauses_ranking_n": len(out.get("clauses_ranking") or []),
    }
    out["meta"] = meta
    return out
