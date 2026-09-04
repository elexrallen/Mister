"""
Normas de liga Mister Fantasy → factores del advisor.

Fuente canónica: `_FG_user` tras switch_community.
Best-effort: `POST /ajax/sw/admin` (solo si eres admin).
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

log = logging.getLogger("league_rules")

PROVIDER_LABELS: dict[str, str] = {
    "mix": "Mixto",
    "mix2": "Mixto 2",
    "mr": "SofaScore",
    "as": "Cronistas AS",
    "marca": "Cronistas MARCA",
    "marca_stats": "Cronistas MARCA + Estadísticas",
    "md": "Cronistas Mundo Deportivo",
    "cls": "Clásico",
}

# Mister provider → perfil FF (URL/columnas) + escala de media
PROVIDER_FF_HINT: dict[str, dict[str, Any]] = {
    "mix": {"prefer_competition": None, "avg_scale": 8.0, "label": "Mister Mixto", "factor": "scoring_mixto"},
    "mix2": {"prefer_competition": None, "avg_scale": 8.0, "label": "Mister Mixto 2", "factor": "scoring_mixto"},
    # prefer_competition solo se fuerza a Premier cuando la liga ES Premier (ver ff_hint_for_provider)
    "mr": {"prefer_competition": None, "avg_scale": 16.0, "label": "SofaScore / RPG-like", "factor": "scoring_sofascore"},
    "as": {"prefer_competition": None, "avg_scale": 8.0, "label": "Cronistas AS", "factor": "scoring_as"},
    "marca": {"prefer_competition": None, "avg_scale": 8.0, "label": "Cronistas MARCA", "factor": "scoring_marca"},
    "marca_stats": {"prefer_competition": None, "avg_scale": 8.0, "label": "MARCA + Stats", "factor": "scoring_marca"},
    "md": {"prefer_competition": None, "avg_scale": 8.0, "label": "Cronistas MD", "factor": "scoring_md"},
    "cls": {"prefer_competition": None, "avg_scale": 8.0, "label": "Clásico", "factor": "scoring_classic"},
}


def _truthy(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    s = str(val).strip().lower()
    return s not in ("", "0", "false", "none", "null", "no")


def infer_market_mode(
    *,
    league_type: str | None = None,
    mode: str | None = None,
    direct_transfer: Any = None,
    override: str | None = None,
) -> str:
    """comunio/private → auction; lfm/contest + transfer → fixed."""
    if override in ("auction", "fixed"):
        return override
    t = (league_type or "").strip().lower()
    m = (mode or "").strip().lower()
    if t == "lfm" or m == "contest":
        return "fixed"
    if t == "comunio" or m == "private":
        return "auction"
    if _truthy(direct_transfer) and t in ("", "lfm"):
        return "fixed"
    return "auction"


def provider_label(provider: str | None) -> str:
    key = (provider or "").strip().lower()
    return PROVIDER_LABELS.get(key, key or "desconocido")


def ff_hint_for_provider(provider: str | None, *, competition: str | None = None) -> dict[str, Any]:
    """
    Resuelve hint de scoring FF según provider Mister.
    `prefer_competition` None → usar la competición de la liga.
    """
    key = (provider or "").strip().lower()
    comp = (competition or "laliga").strip().lower()
    high_scale = comp in ("premier", "seriea")
    base = dict(PROVIDER_FF_HINT.get(key) or {
        "prefer_competition": None,
        "avg_scale": 16.0 if high_scale else 8.0,
        "label": provider_label(key) or "FF",
        "factor": f"scoring_{key or 'unknown'}",
    })
    if key == "mr" and comp == "premier":
        base["prefer_competition"] = "premier"
        base["avg_scale"] = 16.0
        base["label"] = "Fantasy RPG / SofaScore"
    elif key == "mr" and comp == "seriea":
        # Fantasy✨ en FF analytics; no forzar Premier
        base["prefer_competition"] = "seriea"
        base["avg_scale"] = 16.0
        base["label"] = "Fantasy✨ / SofaScore"
    elif key in ("mix", "mix2") and comp == "premier":
        # Premier a veces publica Mixto; preferir columnas Mixto si existen
        base["prefer_competition"] = "premier"
        base["force_mixto_columns"] = True
    elif key in ("mix", "mix2") and comp == "seriea":
        base["prefer_competition"] = "seriea"
        base["avg_scale"] = 16.0
    return base


def compute_factors(rules: dict[str, Any]) -> list[str]:
    """Lista explícita de factores que el advisor debe tener en cuenta."""
    factors: list[str] = []
    provider = str(rules.get("provider") or "").lower()
    hint = ff_hint_for_provider(provider, competition=rules.get("external") or rules.get("competition_key"))
    factors.append(str(hint.get("factor") or "scoring_unknown"))

    mode = rules.get("market_mode") or "auction"
    if mode == "fixed":
        factors.append("fixed_price_market")
    else:
        factors.append("auction_urgency")

    if rules.get("clauses"):
        factors.append("clause_bids")
    else:
        factors.append("no_clauses")

    if rules.get("loans"):
        factors.append("loans_liquidity")
    else:
        factors.append("no_loans")

    factors.append(f"max_squad_{int(rules.get('max_squad') or 0)}")

    speed = int(rules.get("market_speed") or 1)
    if speed >= 2:
        factors.append("fast_market")
    elif speed <= 0:
        factors.append("slow_market")
    else:
        factors.append("normal_market_cycle")

    if rules.get("salaries"):
        factors.append("salaries")
    economy = rules.get("economy") if isinstance(rules.get("economy"), dict) else {}
    if economy.get("gw_cash_bonus"):
        factors.append("gw_cash_bonus")
    elif economy.get("no_gw_cash_bonus"):
        factors.append("no_gw_cash_bonus")
    if economy.get("credit_prizes"):
        factors.append("credit_prizes_not_cash")
    sale_limit = economy.get("sale_limit")
    if sale_limit:
        factors.append(f"sale_limit_{int(sale_limit)}")
    if rules.get("custom_rules"):
        factors.append("custom_rules_text")
    if rules.get("show_balances"):
        factors.append("public_balances")
    captain = rules.get("captain")
    if isinstance(captain, dict) and captain.get("enabled"):
        mode = captain.get("mode") or "by_market_value"
        if mode == "fixed":
            factors.append(f"captain_x{captain.get('multiplier')}")
        else:
            factors.append("captain_by_value")
    return factors


_NO_GW_CASH_RE = re.compile(
    r"no hay bonificacion(?:es)? al cierre|sin bonificacion(?:es)? al cierre",
    re.I,
)
_CREDIT_PRIZE_RE = re.compile(r"cr[eé]ditos|paypal|camiseta", re.I)
_EURO_PRIZE_RE = re.compile(
    r"(?:premio|bonificaci[oó]n).{0,40}(?:€|euros?)|(?:€|euros?).{0,40}(?:cierre|jornada|puntos)",
    re.I,
)


def _plain_custom_rules(raw: Any) -> str:
    if raw is None:
        return ""
    text = str(raw)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _prize_cash_parts(prizes: Any) -> tuple[float, dict[str, float]]:
    if not isinstance(prizes, dict):
        return 0.0, {}
    parts: dict[str, float] = {}
    total = 0.0
    for key in ("points", "goals", "best_xi", "fixed"):
        try:
            val = float(prizes.get(key) or 0)
        except (TypeError, ValueError):
            val = 0.0
        if val > 0:
            parts[key] = val
            total += val
    return total, parts


def resolve_economy(
    rules: dict[str, Any],
    *,
    max_debt_raw: Any = None,
) -> dict[str, Any]:
    """
    Economía de jornada por liga: euros de plantilla vs créditos/tienda,
    salarios, tope de ventas y deuda.
    El ingreso esperado NUNCA es saldo de hoy para pujar.
    """
    custom_raw = rules.get("custom_rules")
    plain = _plain_custom_rules(custom_raw)
    prizes = rules.get("prizes") if isinstance(rules.get("prizes"), dict) else {}
    prize_cash, prize_parts = _prize_cash_parts(prizes)

    rewards_flag = rules.get("rewards")
    if rewards_flag is None:
        # Sin admin: ligas privadas con custom_rules vacío (Patio) suelen tener rewards on.
        rewards_on = not plain
    else:
        rewards_on = bool(rewards_flag)

    no_gw_text = bool(plain and _NO_GW_CASH_RE.search(plain))
    credit_prizes = bool(plain and _CREDIT_PRIZE_RE.search(plain) and prize_cash <= 0)
    euro_text = bool(plain and _EURO_PRIZE_RE.search(plain) and not no_gw_text)

    gw_cash_bonus = False
    source = "none"
    confidence = "high"
    if no_gw_text:
        gw_cash_bonus = False
        source = "custom_rules_no_bonus"
    elif prize_cash > 0:
        gw_cash_bonus = True
        source = "prizes_euros"
    elif credit_prizes:
        gw_cash_bonus = False
        source = "custom_rules_credits"
    elif rewards_on and not plain:
        gw_cash_bonus = True
        source = "rewards_default"
        confidence = "medium"
    elif rewards_on and euro_text:
        gw_cash_bonus = True
        source = "custom_rules_euros"
        confidence = "medium"
    elif rewards_on:
        # rewards=1 pero el texto no confirma euros de plantilla (p.ej. MD)
        gw_cash_bonus = False
        source = "rewards_on_no_cash_confirmed"
        confidence = "medium"
    else:
        gw_cash_bonus = False
        source = "rewards_off"

    expected = 0.0
    if gw_cash_bonus and prize_cash > 0:
        expected = prize_cash
        confidence = "medium"
    elif gw_cash_bonus:
        expected = 0.0
        confidence = "low"

    salaries_on = bool(rules.get("salaries"))
    if salaries_on and expected > 0:
        expected = 0.0
        confidence = "low"

    try:
        sale_limit = int(rules.get("sale_limit") or 5)
    except (TypeError, ValueError):
        sale_limit = 5
    if sale_limit <= 0:
        sale_limit = 5

    try:
        max_debt = float(max_debt_raw) if max_debt_raw is not None else None
    except (TypeError, ValueError):
        max_debt = None

    return {
        "gw_cash_bonus": gw_cash_bonus,
        "no_gw_cash_bonus": not gw_cash_bonus,
        "credit_prizes": credit_prizes,
        "expected_gw_cash": round(expected, 0),
        "source": source,
        "confidence": confidence,
        "when": "after_gameweek" if gw_cash_bonus else None,
        "prizes": prize_parts,
        "prize_cash_total": prize_cash,
        "salaries": salaries_on,
        "sale_limit": sale_limit,
        "max_debt": max_debt,
        "usable_for_bids_today": False,
    }


# Mister: multiplicador dinámico por valor de mercado (blog oficial).
# Override fijo opcional con `captain_multiplier` en LEAGUE_OVERRIDES / admin / fg_cfg.
CAPTAIN_PRICE_TIERS: tuple[tuple[float, float], ...] = (
    (5_000_000.0, 3.0),   # >0 y <5M → x3
    (10_000_000.0, 2.0),  # >=5M y <10M → x2
)
CAPTAIN_PRICE_TOP_MULTIPLIER = 1.5  # >=10M → x1.5
DEFAULT_CAPTAIN_MULTIPLIER = 2.0  # solo fallback si falta precio en modo by_value


def captain_multiplier_for_price(price: Any) -> float:
    """
    Multiplicador de capitán según valor de mercado Mister:
    <5M → x3, 5–10M → x2, ≥10M → x1.5.
    Sin precio válido → x2 (tramo medio conservador).
    """
    try:
        p = float(price) if price is not None else 0.0
    except (TypeError, ValueError):
        p = 0.0
    if p <= 0:
        return DEFAULT_CAPTAIN_MULTIPLIER
    for ceiling, mult in CAPTAIN_PRICE_TIERS:
        if p < ceiling:
            return mult
    return CAPTAIN_PRICE_TOP_MULTIPLIER


def resolve_captain_rule(
    *,
    fg_cfg: dict[str, Any] | None = None,
    admin_settings: dict[str, Any] | None = None,
    league_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Capitán de la liga: activado y modo de multiplicador.

    Por defecto Mister usa multiplicador por valor de mercado (`by_market_value`).
    Un `captain_multiplier` explícito fuerza modo `fixed` (tests / overrides).

    `is_captain_enabled` llega en /ajax/sw/admin (solo si eres admin) y
    `LEAGUE_CAPTAIN_ENABLED` en el `_FG_cfg` del HTML (siempre disponible).
    """
    cfg = league_cfg if isinstance(league_cfg, dict) else {}
    admin = admin_settings if isinstance(admin_settings, dict) else {}
    fgc = fg_cfg if isinstance(fg_cfg, dict) else {}

    enabled: bool | None = None
    source = "unknown"
    if cfg.get("captain_enabled") is not None:
        enabled = _truthy(cfg.get("captain_enabled"))
        source = "override"
    elif admin.get("is_captain_enabled") is not None:
        enabled = _truthy(admin.get("is_captain_enabled"))
        source = "admin"
    elif fgc.get("LEAGUE_CAPTAIN_ENABLED") is not None:
        enabled = _truthy(fgc.get("LEAGUE_CAPTAIN_ENABLED"))
        source = "fg_cfg"

    fixed_mult: float | None = None
    mult_source = "by_market_value"
    for candidate, src in (
        (cfg.get("captain_multiplier"), "override"),
        (admin.get("captain_multiplier"), "admin"),
        (fgc.get("CAPTAIN_MULTIPLIER"), "fg_cfg"),
    ):
        try:
            v = float(candidate) if candidate is not None else None
        except (TypeError, ValueError):
            v = None
        if v and v > 1:
            fixed_mult = v
            mult_source = src
            break

    if not enabled:
        return {
            "enabled": False,
            "known": enabled is not None,
            "mode": "off",
            "multiplier": 1.0,
            "source": source,
            "multiplier_source": mult_source if fixed_mult else "off",
        }

    if fixed_mult is not None:
        return {
            "enabled": True,
            "known": enabled is not None,
            "mode": "fixed",
            "multiplier": fixed_mult,
            "source": source,
            "multiplier_source": mult_source,
        }

    return {
        "enabled": True,
        "known": enabled is not None,
        "mode": "by_market_value",
        "multiplier": None,
        "source": source,
        "multiplier_source": "by_market_value",
    }


def normalize_rules(
    fg_user: dict[str, Any] | None = None,
    *,
    admin_data: dict[str, Any] | None = None,
    league_cfg: dict[str, Any] | None = None,
    external_key: str | None = None,
    fg_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Normaliza normas Mister a schema interno del advisor.
    Prioridad: override forzado en league_cfg > admin_data > fg_user > defaults.
    """
    fg = fg_user if isinstance(fg_user, dict) else {}
    admin = admin_data if isinstance(admin_data, dict) else {}
    cfg = league_cfg if isinstance(league_cfg, dict) else {}

    # Admin payload suele anidar settings; aceptar plano o anidado
    admin_settings = admin
    for nest_key in ("settings", "community", "data"):
        nested = admin.get(nest_key)
        if isinstance(nested, dict):
            admin_settings = {**admin_settings, **nested}

    def _pick(*keys: str, default: Any = None) -> Any:
        for k in keys:
            if k in cfg and cfg[k] is not None and k in ("market_mode", "max_squad") and cfg.get(f"_force_{k}"):
                return cfg[k]
            if k in admin_settings and admin_settings[k] is not None:
                return admin_settings[k]
            if k in fg and fg[k] is not None:
                return fg[k]
            if k in cfg and cfg[k] is not None:
                return cfg[k]
        return default

    provider = str(_pick("provider", default="mix") or "mix").strip().lower()
    team_limit_raw = _pick("team_limit", "max_squad", default=cfg.get("max_squad") or 25)
    try:
        max_squad = int(team_limit_raw)
    except (TypeError, ValueError):
        max_squad = int(cfg.get("max_squad") or 25)
    if max_squad <= 0:
        max_squad = 25

    forced_mode = cfg.get("market_mode") if cfg.get("market_mode") in ("auction", "fixed") else None
    # Si el override existía solo como fallback de discovery, no forzar: preferir FG type
    market_mode = infer_market_mode(
        league_type=str(_pick("type", default="") or ""),
        mode=str(_pick("mode", default="") or ""),
        direct_transfer=_pick("direct_transfer"),
        override=forced_mode if cfg.get("_force_market_mode") else (
            forced_mode if not fg.get("type") else None
        ),
    )
    # Si FG no dio type pero cfg sí tiene market_mode, usarlo
    if not fg.get("type") and not admin_settings.get("type") and forced_mode:
        market_mode = forced_mode

    clauses = _truthy(_pick("clauses", default=1))
    loans = _truthy(_pick("loans", default=0))
    try:
        loans_floor = int(_pick("loans_floor", default=0) or 0)
    except (TypeError, ValueError):
        loans_floor = 0
    try:
        market_speed = int(_pick("market_speed", default=1) or 1)
    except (TypeError, ValueError):
        market_speed = 1
    try:
        market_stay = int(_pick("market_stay", default=1) or 1)
    except (TypeError, ValueError):
        market_stay = 1

    custom_rules = _pick("custom_rules", default=None)
    if custom_rules is not None:
        custom_rules = str(custom_rules).strip() or None

    prizes_raw = _pick("prizes", default=None)
    try:
        sale_limit = int(_pick("sale_limit", default=5) or 5)
    except (TypeError, ValueError):
        sale_limit = 5
    if sale_limit <= 0:
        sale_limit = 5
    rewards_raw = _pick("rewards", default=None)
    max_debt_raw = _pick("max_debt", default=None)

    source = "fg_user"
    if admin:
        source = "fg_user+admin" if fg else "admin"
    elif not fg:
        source = "config"

    ext = (external_key or cfg.get("external") or "").strip().lower() or None
    rules: dict[str, Any] = {
        "provider": provider,
        "provider_label": provider_label(provider),
        "max_squad": max_squad,
        "market_mode": market_mode,
        "clauses": clauses,
        "loans": loans,
        "loans_floor": loans_floor,
        "market_speed": market_speed,
        "market_stay": market_stay,
        "salaries": _truthy(_pick("salaries", default=0)),
        "live_changes": _truthy(_pick("live_changes", default=0)),
        "show_balances": _truthy(_pick("show_balances", default=0)),
        "rewards": None if rewards_raw is None else _truthy(rewards_raw),
        "sale_limit": sale_limit,
        "prizes": prizes_raw if isinstance(prizes_raw, dict) else None,
        "custom_rules": custom_rules,
        "type": str(_pick("type", default="") or "") or None,
        "mode": str(_pick("mode", default="") or "") or None,
        "external": ext,
        "source": source,
        "is_admin": _truthy(fg.get("admin")),
    }
    rules["captain"] = resolve_captain_rule(
        fg_cfg=fg_cfg,
        admin_settings=admin_settings,
        league_cfg=cfg,
    )
    rules["economy"] = resolve_economy(rules, max_debt_raw=max_debt_raw)
    rules["factors"] = compute_factors({**rules, "competition_key": ext})
    # Urgencia derivada para el motor (1.0 = normal; >1 = más agresivo en buy_now)
    # market_speed Mister: 1=normal; valores mayores → mercado más rápido
    stay = max(1, market_stay)
    speed = max(0, market_speed)
    urgency = 1.0 + 0.25 * max(0, speed - 1) + (0.15 if stay <= 1 else 0.0)
    if market_mode == "fixed":
        urgency *= 0.85  # menos FOMO de subasta
    rules["market_urgency"] = round(urgency, 3)
    return rules


def merge_rules_into_league_cfg(league_cfg: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    """Actualiza league_cfg con max_squad / market_mode detectados (sin pisar force flags)."""
    out = dict(league_cfg)
    if not out.get("_force_market_mode"):
        out["market_mode"] = rules.get("market_mode") or out.get("market_mode")
    if not out.get("_force_max_squad"):
        out["max_squad"] = rules.get("max_squad") or out.get("max_squad")
    out["rules"] = rules
    return out
