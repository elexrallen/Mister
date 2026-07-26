/**
 * Mister Fantasy Advisor — frontend (ES6+)
 * Carga public/data/latest_data.json, renderiza KPIs / recomendaciones / tabs
 * y registra la PWA (service worker + prompt de instalación).
 */

(() => {
  "use strict";

  /** @type {any} */
  let DATA = null;
  /** @type {BeforeInstallPromptEvent | null} */
  let deferredPrompt = null;

  const POS_ORDER = { GK: 0, DF: 1, MF: 2, FW: 3 };
  const PRIO_ORDER = { Alta: 0, Media: 1, Baja: 2 };

  /** Estado de ordenación por listado */
  const SORT = {
    market: { key: "priority", dir: "asc" },
    squad: { key: "position", dir: "asc" },
    upgrades: { key: "score", dir: "desc" },
    free: { key: "roi", dir: "desc" },
    rivals: { key: "rank", dir: "asc" },
  };

  const SORT_OPTIONS = {
    market: [
      { key: "priority", label: "Prioridad" },
      { key: "price", label: "Precio" },
      { key: "bid", label: "Puja rec." },
      { key: "mister", label: "Media Mister" },
      { key: "fotmob", label: "FotMob" },
      { key: "delta", label: "Tendencia" },
      { key: "name", label: "Nombre" },
      { key: "position", label: "Posición" },
      { key: "category", label: "Categoría" },
    ],
    squad: [
      { key: "position", label: "Posición" },
      { key: "price", label: "Precio" },
      { key: "form", label: "Forma" },
      { key: "lineup", label: "Alineación" },
      { key: "mister", label: "Media Mister" },
      { key: "fotmob", label: "FotMob" },
      { key: "name", label: "Nombre" },
    ],
    upgrades: [
      { key: "score", label: "Mejor upgrade" },
      { key: "clause", label: "Cláusula" },
      { key: "value", label: "Valor" },
      { key: "mister", label: "Media Mister" },
      { key: "name", label: "Nombre" },
      { key: "position", label: "Posición" },
      { key: "owner", label: "Dueño" },
      { key: "action", label: "Acción" },
    ],
    free: [
      { key: "roi", label: "ROI / M€" },
      { key: "ppg", label: "PPG" },
      { key: "price", label: "Precio" },
      { key: "reliability", label: "Fiabilidad" },
      { key: "name", label: "Nombre" },
      { key: "position", label: "Posición" },
    ],
    rivals: [
      { key: "rank", label: "Clasificación" },
      { key: "points", label: "Puntos" },
      { key: "liquidity", label: "Liquidez / valor" },
      { key: "team", label: "Equipo" },
      { key: "manager", label: "Manager" },
      { key: "activity", label: "Actividad" },
    ],
  };

  const SORT_GETTERS = {
    market: {
      name: (p) => String(p.name || "").toLowerCase(),
      position: (p) => POS_ORDER[p.position] ?? 9,
      price: (p) => Number(p.price) || 0,
      delta: (p) =>
        p.delta_5d != null ? Number(p.delta_5d) : p.trend === "up" ? 1 : p.trend === "down" ? -1 : 0,
      fotmob: (p) => {
        const fm = p.fotmob_stats || {};
        const v = fm.rating_promedio ?? (p.external || {}).sofascore_avg_5;
        return v != null ? Number(v) : -1;
      },
      bid: (p) => Number(p.puja_recomendada) || 0,
      priority: (p) => PRIO_ORDER[p.priority] ?? 9,
      mister: (p) => {
        const v = p.ff_mister_avg ?? (p.external || {}).ff_mister_avg;
        return v != null ? Number(v) : -1;
      },
      category: (p) => String(p.category_label || p.category || "").toLowerCase(),
    },
    squad: {
      name: (p) => String(p.name || "").toLowerCase(),
      position: (p) => POS_ORDER[p.position] ?? 9,
      price: (p) => Number(p.price) || 0,
      form: (p) => {
        const mister = p.form != null ? Number(p.form) : p.mister_avg != null ? Number(p.mister_avg) : null;
        if (mister != null && mister > 0) return mister;
        const ff = p.ff_mister_avg ?? (p.external || {}).ff_mister_avg;
        return ff != null ? Number(ff) : -1;
      },
      lineup: (p) => (p.lineup_prob != null ? Number(p.lineup_prob) : -1),
      fotmob: (p) => {
        const fm = p.fotmob_stats || {};
        const v = fm.rating_promedio ?? (p.external || {}).sofascore_avg_5;
        return v != null ? Number(v) : -1;
      },
      mister: (p) => {
        const v = p.ff_mister_avg ?? (p.external || {}).ff_mister_avg;
        return v != null ? Number(v) : -1;
      },
    },
    upgrades: {
      name: (p) => String(p.name || "").toLowerCase(),
      position: (p) => POS_ORDER[p.position] ?? 9,
      owner: (p) => String(p.owner_team || "").toLowerCase(),
      value: (p) => Number(p.market_value) || 0,
      mister: (p) => {
        const v = p.ff_mister_avg ?? p.mister_avg;
        return v != null ? Number(v) : -1;
      },
      clause: (p) => (p.clause_known && p.clause != null ? Number(p.clause) : Number.POSITIVE_INFINITY),
      action: (p) => (p.action === "clause_bid" ? 0 : 1),
      score: (p) => Number(p.upgrade_score ?? p.priority_score) || 0,
    },
    free: {
      name: (p) => String(p.name || "").toLowerCase(),
      position: (p) => POS_ORDER[p.position] ?? 9,
      price: (p) => Number(p.price) || 0,
      ppg: (p) => (p.avg_ppg != null ? Number(p.avg_ppg) : -1),
      reliability: (p) => (p.reliability != null ? Number(p.reliability) : -1),
      roi: (p) => (p.roi_ppg_per_million != null ? Number(p.roi_ppg_per_million) : -1),
    },
    rivals: {
      rank: (p) => Number(p.rank) || 99,
      team: (p) => String(p.team_name || "").toLowerCase(),
      manager: (p) => String(p.manager || "").toLowerCase(),
      points: (p) => Number(p.points) || 0,
      liquidity: (p) =>
        p.liquidity_estimated != null
          ? Number(p.liquidity_estimated)
          : p.squad_value != null
            ? Number(p.squad_value)
            : 0,
      activity: (p) => String(p.activity || "").toLowerCase(),
    },
  };

  function sortRows(listId, rows) {
    const state = SORT[listId] || { key: "name", dir: "asc" };
    const getter = (SORT_GETTERS[listId] || {})[state.key];
    if (!getter || !rows.length) return rows;
    const dir = state.dir === "desc" ? -1 : 1;
    return [...rows].sort((a, b) => {
      const va = getter(a);
      const vb = getter(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      if (typeof va === "string" || typeof vb === "string") {
        return String(va).localeCompare(String(vb), "es", { sensitivity: "base" }) * dir;
      }
      if (va === vb) {
        const na = String(a.name || a.team_name || "");
        const nb = String(b.name || b.team_name || "");
        return na.localeCompare(nb, "es", { sensitivity: "base" });
      }
      return va < vb ? -1 * dir : 1 * dir;
    });
  }

  function setSort(listId, key, { toggleDir = false, dir } = {}) {
    if (!SORT[listId]) return;
    if (key && SORT[listId].key === key && toggleDir) {
      SORT[listId].dir = SORT[listId].dir === "asc" ? "desc" : "asc";
    } else if (key) {
      const prev = SORT[listId].key;
      SORT[listId].key = key;
      if (dir) {
        SORT[listId].dir = dir;
      } else if (prev !== key) {
        const textKeys = new Set(["name", "team", "manager", "owner", "category", "activity", "position", "priority", "action", "rank"]);
        SORT[listId].dir = textKeys.has(key) ? "asc" : "desc";
      }
    } else if (toggleDir) {
      SORT[listId].dir = SORT[listId].dir === "asc" ? "desc" : "asc";
    }
    syncSortUI(listId);
    if (!DATA) return;
    if (listId === "market") renderMarket();
    else if (listId === "squad") renderSquad();
    else renderRadar();
  }

  function syncSortUI(listId) {
    const state = SORT[listId];
    if (!state) return;
    document.querySelectorAll(`.list-sort[data-list="${listId}"]`).forEach((bar) => {
      const sel = bar.querySelector(".sort-select");
      const btn = bar.querySelector(".sort-dir");
      if (sel) sel.value = state.key;
      if (btn) {
        btn.textContent = state.dir === "asc" ? "↑ Asc" : "↓ Desc";
        btn.setAttribute("aria-label", state.dir === "asc" ? "Orden ascendente" : "Orden descendente");
      }
    });
    document.querySelectorAll(`.th-sort[data-list="${listId}"]`).forEach((btn) => {
      const active = btn.getAttribute("data-key") === state.key;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-sort", active ? (state.dir === "asc" ? "ascending" : "descending") : "none");
      const mark = btn.querySelector(".sort-mark");
      if (mark) mark.textContent = active ? (state.dir === "asc" ? " ↑" : " ↓") : "";
    });
  }

  function buildSortBars() {
    document.querySelectorAll(".list-sort[data-list]").forEach((bar) => {
      const listId = bar.getAttribute("data-list");
      const opts = SORT_OPTIONS[listId] || [];
      const state = SORT[listId] || { key: opts[0]?.key, dir: "asc" };
      bar.innerHTML = `
        <label class="sort-label" for="sort-${listId}">Ordenar</label>
        <select id="sort-${listId}" class="sort-select" data-list="${listId}">
          ${opts
            .map(
              (o) =>
                `<option value="${escapeHtml(o.key)}" ${o.key === state.key ? "selected" : ""}>${escapeHtml(
                  o.label
                )}</option>`
            )
            .join("")}
        </select>
        <button type="button" class="sort-dir" data-list="${listId}">${
          state.dir === "asc" ? "↑ Asc" : "↓ Desc"
        }</button>`;
    });
    document.querySelectorAll(".th-sort").forEach((btn) => {
      if (btn.querySelector(".sort-mark")) return;
      const mark = document.createElement("span");
      mark.className = "sort-mark";
      mark.setAttribute("aria-hidden", "true");
      btn.appendChild(mark);
    });
    Object.keys(SORT).forEach(syncSortUI);
  }

  function initSortControls() {
    buildSortBars();
    document.addEventListener("change", (e) => {
      const t = e.target;
      if (!(t instanceof HTMLSelectElement) || !t.classList.contains("sort-select")) return;
      setSort(t.getAttribute("data-list"), t.value);
    });
    document.addEventListener("click", (e) => {
      const t = e.target;
      if (!(t instanceof Element)) return;
      const dirBtn = t.closest(".sort-dir");
      if (dirBtn) {
        setSort(dirBtn.getAttribute("data-list"), null, { toggleDir: true });
        return;
      }
      const thBtn = t.closest(".th-sort");
      if (thBtn) {
        setSort(thBtn.getAttribute("data-list"), thBtn.getAttribute("data-key"), { toggleDir: true });
      }
    });
  }

  const formatMoney = (n) => {
    if (n == null || Number.isNaN(Number(n))) return "—";
    const v = Number(n);
    if (Math.abs(v) >= 1_000_000) {
      return `${(v / 1_000_000).toFixed(v % 1_000_000 === 0 ? 0 : 1)} M€`;
    }
    return new Intl.NumberFormat("es-ES", {
      style: "currency",
      currency: "EUR",
      maximumFractionDigits: 0,
    }).format(v);
  };

  const pct = (n) => {
    if (n == null) return "—";
    const v = Number(n) * 100;
    const sign = v > 0 ? "+" : "";
    return `${sign}${v.toFixed(1)}%`;
  };

  const fmtDate = (iso) => {
    try {
      return new Intl.DateTimeFormat("es-ES", {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: "Europe/Madrid",
      }).format(new Date(iso));
    } catch {
      return iso || "—";
    }
  };

  const priorityBadge = (p) => {
    const cls =
      p === "Alta" ? "badge-alta" : p === "Media" ? "badge-media" : "badge-baja";
    return `<span class="badge ${cls}">${p || "—"}</span>`;
  };

  const posChip = (pos) => `<span class="pos-chip">${pos || "—"}</span>`;

  const externalStatusBadge = (p) => {
    const ext = p.external || {};
    const avail = ext.availability || (p.injury ? "injured" : "unknown");
    const lineupExt =
      ext.lineup_prob_ext != null
        ? Number(ext.lineup_prob_ext)
        : p.lineup_prob != null
          ? Number(p.lineup_prob) * 100
          : null;
    const mins = Number((p.fotmob_stats || {}).minutos_ultimos_5);
    const hasMins = Number.isFinite(mins) && mins >= 0 && (p.fotmob_stats || {}).minutos_ultimos_5 != null;
    let badge = `<span class="badge badge-baja">—</span>`;
    if (avail === "injured") {
      badge = `<span class="badge badge-baja-ext">Lesionado</span>`;
    } else if (avail === "suspended") {
      badge = `<span class="badge badge-baja-ext">Sancionado</span>`;
    } else if (avail === "doubt") {
      badge = `<span class="badge badge-duda">Duda</span>`;
    } else if (lineupExt != null && lineupExt >= 70) {
      badge = `<span class="badge badge-titular">Titular ${Math.round(lineupExt)}%</span>`;
    } else if (lineupExt != null && lineupExt < 40) {
      badge = `<span class="badge badge-baja-ext">Titular ${Math.round(lineupExt)}%</span>`;
    } else if (lineupExt != null) {
      badge = `<span class="badge badge-duda">Titular ${Math.round(lineupExt)}%</span>`;
    } else if (avail === "available") {
      badge = `<span class="badge badge-mint">OK</span>`;
    }
    const extras = [];
    if (p.in_lineup) {
      extras.push(`<span class="badge badge-once">Once</span>`);
    }
    const lowMins = hasMins && mins < 90;
    const lowPct = lineupExt != null && lineupExt < 40 && !(hasMins && mins >= 180);
    if (lowMins || lowPct) {
      extras.push(`<span class="badge badge-baja-ext">Pocos min</span>`);
    }
    const link = ext.profile_url
      ? ` <a class="ext-link" href="${escapeHtml(ext.profile_url)}" target="_blank" rel="noopener noreferrer">FF/JP</a>`
      : "";
    return `${badge}${extras.join(" ")}${link}`;
  };

  const fotmobCell = (p) => {
    const fm = p.fotmob_stats || {};
    let rating = fm.rating_promedio;
    if (rating == null && p.external && p.external.sofascore_avg_5 != null) {
      rating = p.external.sofascore_avg_5;
    }
    if (rating == null) return "—";
    const mins = Number(fm.minutos_ultimos_5 || 0);
    const goals = Number(fm.goles_ultimos_5 || 0);
    const extra =
      mins > 0 || goals > 0
        ? `<div class="text-[10px] text-slate-500 leading-tight">${mins}' · ${goals}G</div>`
        : "";
    return `<div class="leading-tight"><span>${Number(rating).toFixed(1)}</span>${extra}</div>`;
  };

  /** Forma Mister; en pretemporada (0) cae a media FF Mister Mixto. */
  const formCell = (p) => {
    const mister = p.form != null ? Number(p.form) : p.mister_avg != null ? Number(p.mister_avg) : null;
    const ff = p.ff_mister_avg ?? (p.external || {}).ff_mister_avg;
    if (mister != null && !Number.isNaN(mister) && mister > 0) {
      return Number(mister).toFixed(1);
    }
    if (ff != null && !Number.isNaN(Number(ff))) {
      return `<span title="Media FF Mister Mixto (pretemporada)">${Number(ff).toFixed(1)} <span class="text-[10px] text-slate-500">FF</span></span>`;
    }
    return "—";
  };

  const signalChips = (p) => {
    const ext = p.external || {};
    const chips = [];
    if (ext.is_chollo_ext) chips.push(`<span class="badge badge-mint">Chollo</span>`);
    if (ext.is_recommendation_ext) chips.push(`<span class="badge badge-titular">Reco</span>`);
    if (p.is_top_ff || ext.is_top_ff) chips.push(`<span class="badge badge-mint">TOP Mister</span>`);
    if (ext.points_streak === "up") chips.push(`<span class="badge badge-mint">Racha ↑</span>`);
    if (ext.points_streak === "down") chips.push(`<span class="badge badge-baja-ext">Racha ↓</span>`);
    if (p.fills_structural && p.structural_label) {
      chips.push(`<span class="badge badge-mint">${escapeHtml(p.structural_label)}</span>`);
    } else if (p.fills_need) {
      chips.push(`<span class="badge badge-duda">Carencia</span>`);
    }
    const cov = coverageChips(p);
    if (cov) chips.push(cov);
    if (p.affordable === false) chips.push(`<span class="badge badge-baja">Sin saldo</span>`);
    return chips.length ? chips.join(" ") : `<span class="text-slate-600 text-xs">—</span>`;
  };

  const ffAvgLine = (p) => {
    const avg = p.ff_mister_avg ?? (p.external && p.external.ff_mister_avg);
    if (avg == null) return "";
    return `<span class="badge badge-duda">Mister ${Number(avg).toFixed(1)}</span>`;
  };

  const riskBadge = (risk) => {
    const cls =
      risk === "high" ? "badge-baja-ext" : risk === "medium" ? "badge-duda" : "badge-mint";
    const label = risk === "high" ? "Riesgo alto" : risk === "medium" ? "Riesgo medio" : "Riesgo bajo";
    return `<span class="badge ${cls}">${label}</span>`;
  };

  function selectTab(id) {
    document.querySelectorAll(".tab, .mobile-nav-btn").forEach((t) => {
      const match = t.getAttribute("data-tab") === id;
      t.classList.toggle("active", match);
      if (t.classList.contains("tab")) {
        t.setAttribute("aria-selected", match ? "true" : "false");
      }
    });
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      const match = panel.id === `tab-${id}`;
      panel.classList.toggle("active", match);
      panel.hidden = !match;
    });
    const browse = document.getElementById("browse-section");
    if (browse && window.matchMedia("(max-width: 767px)").matches) {
      browse.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function focusPlayer(playerId, tab) {
    if (!playerId) return;
    selectTab(tab || "market");
    const preferCards = window.matchMedia("(max-width: 767px)").matches;
    const sel = preferCards
      ? `.player-card[data-player-id="${CSS.escape(String(playerId))}"], tr[data-player-id="${CSS.escape(String(playerId))}"]`
      : `tr[data-player-id="${CSS.escape(String(playerId))}"], .player-card[data-player-id="${CSS.escape(String(playerId))}"]`;
    const row = document.querySelector(sel);
    if (row) {
      row.classList.add("row-highlight");
      row.scrollIntoView({ behavior: "smooth", block: "center" });
      setTimeout(() => row.classList.remove("row-highlight"), 2500);
      return;
    }
    const q = document.getElementById("filter-q");
    const item =
      (DATA.action_plan || []).find((a) => String(a.player_id) === String(playerId)) ||
      (DATA.market_opportunities || []).find((p) => String(p.id) === String(playerId)) ||
      (DATA.rival_upgrades || []).find((p) => String(p.player_id) === String(playerId));
    if (item && q) {
      q.value = item.name || "";
      applyFilters();
      const retry = document.querySelector(sel);
      if (retry) {
        retry.classList.add("row-highlight");
        retry.scrollIntoView({ behavior: "smooth", block: "center" });
        setTimeout(() => retry.classList.remove("row-highlight"), 2500);
      }
    }
  }

  // ---- Filtros compartidos ----
  function getFilters() {
    return {
      q: (document.getElementById("filter-q").value || "").trim().toLowerCase(),
      pos: document.getElementById("filter-pos").value,
      priceMax: Number(document.getElementById("filter-price").value) || null,
      formMin: Number(document.getElementById("filter-form").value) || null,
      avail: document.getElementById("filter-avail").value,
    };
  }

  function matchPlayer(p, f) {
    if (f.q) {
      const hay = `${p.name || ""} ${p.team || ""}`.toLowerCase();
      if (!hay.includes(f.q)) return false;
    }
    if (f.pos && p.position !== f.pos) return false;
    if (f.priceMax != null && Number(p.price) > f.priceMax * 1_000_000) return false;
    if (f.formMin != null && Number(p.form || p.avg_ppg || 0) < f.formMin) return false;
    const extAvail = (p.external && p.external.availability) || null;
    const injured =
      p.injury ||
      extAvail === "injured" ||
      extAvail === "suspended";
    const lineupProb =
      p.external && p.external.lineup_prob_ext != null
        ? Number(p.external.lineup_prob_ext) / 100
        : Number(p.lineup_prob || 0);
    if (f.avail === "ok" && (injured || extAvail === "doubt")) return false;
    if (f.avail === "injury" && !injured) return false;
    if (f.avail === "titular" && lineupProb < 0.7) return false;
    return true;
  }

  // ---- Render ----
  function coverageChips(p) {
    if (!p) return "";
    const chips = [];
    if (p.fills_coverage_gap) {
      chips.push(`<span class="badge badge-alta">Cubre hueco</span>`);
    } else if (p.is_upgrade) {
      chips.push(`<span class="badge badge-mint">Upgrade</span>`);
    } else if (p.line_already_covered) {
      chips.push(`<span class="badge badge-duda">Ya cubierto</span>`);
    }
    if (p.on_daily_market) {
      chips.push(`<span class="badge badge-titular">Mercado hoy</span>`);
    }
    return chips.join(" ");
  }

  function phaseLabel(phase) {
    const map = {
      preseason: "Pretemporada",
      ramp: "Recta final a J1",
      active: "Liga en curso",
    };
    return map[phase] || phase || "—";
  }

  function renderCampaign(data) {
    const banner = document.getElementById("campaign-banner");
    if (!banner) return;
    const k = data.kpis || {};
    const meta = data.meta || {};
    const days = k.days_to_kickoff ?? meta.days_to_kickoff;
    const phase = k.competition_phase || meta.competition_phase || "preseason";
    const season = k.season_start || meta.season_start || "2026-08-15";
    const linesOk = k.lines_ok;
    const gaps = k.depth_gaps;
    const daily = k.daily_market_count;
    const slots = k.market_day_slots;

    banner.hidden = false;
    const cd = document.getElementById("campaign-countdown");
    if (cd) {
      if (days == null) cd.textContent = `J1 ${season}`;
      else if (Number(days) > 0) cd.textContent = `J1 en ${days} días (15 ago)`;
      else if (Number(days) === 0) cd.textContent = `J1 hoy · ${season}`;
      else cd.textContent = `Liga en curso · arranque ${season}`;
    }
    const ph = document.getElementById("campaign-phase");
    if (ph) {
      const copy =
        phase === "active"
          ? "Prioriza puntos y formaciones de jornada."
          : "Prepara plantilla para el 15 ago · dobla líneas, no derroches en posiciones ya cubiertas.";
      ph.textContent = `${phaseLabel(phase)} · ${copy}`;
    }
    const linesEl = document.getElementById("campaign-lines");
    if (linesEl) linesEl.textContent = linesOk != null ? `${linesOk}/4` : "—";
    const gapsEl = document.getElementById("campaign-gaps");
    if (gapsEl) gapsEl.textContent = gaps != null ? String(gaps) : "—";
    const mkt = document.getElementById("campaign-market");
    if (mkt) {
      mkt.textContent =
        daily != null ? (slots != null ? `${daily}/${slots}` : String(daily)) : "—";
    }
  }

  function renderKpis(data) {
    const k = data.kpis || {};
    document.getElementById("kpi-balance").textContent = formatMoney(k.balance);
    document.getElementById("kpi-value").textContent = formatMoney(k.squad_value);
    document.getElementById("kpi-rank").textContent = k.rank != null ? `${k.rank}º` : "—";
    document.getElementById("kpi-free").textContent =
      k.top_free_remaining != null ? String(k.top_free_remaining) : "—";
    renderCampaign(data);
  }

  function renderMeta(data) {
    document.getElementById("updated-at").textContent = fmtDate(data.generated_at);
  }

  const pointsTrendBadge = (trend) => {
    if (!trend || trend === "unknown") return `<span class="text-slate-600 text-xs">—</span>`;
    if (trend === "up") return `<span class="badge badge-mint">Pts ↑</span>`;
    if (trend === "down") return `<span class="badge badge-baja-ext">Pts ↓</span>`;
    return `<span class="badge badge-duda">Pts →</span>`;
  };

  const scoringLine = (u) => {
    const avg = u.mister_avg != null ? Number(u.mister_avg).toFixed(1) : "—";
    const pts = u.points != null ? String(Math.round(Number(u.points))) : "—";
    const phase = u.points_phase === "active" ? "" : " · pretemporada";
    return `<span class="text-xs text-slate-400">${avg} / ${pts}${phase}</span>`;
  };

  const budgetBadge = (bf) => {
    if (!bf) return "";
    const map = {
      comfortable: ["badge-mint", "Caja OK"],
      tight: ["badge-duda", "Ajusta"],
      stretch: ["badge-duda", "Al límite"],
      blocked: ["badge-baja-ext", "Sin saldo"],
      funding: ["badge-mint", "Libera caja"],
    };
    const [cls, label] = map[bf] || ["badge-duda", bf];
    return `<span class="badge ${cls}">${label}</span>`;
  };

  const fundingChip = (a) => {
    if (!a) return "";
    if (a.action === "sell" && (a.sell_reason === "fund_buy" || a.budget_fit === "funding")) {
      return `<span class="badge badge-mint">Libera caja</span>`;
    }
    if (a.leaves_gap_budget) {
      return `<span class="badge badge-mint">Deja caja</span>`;
    }
    if (a.crowds_out_gaps) {
      return `<span class="badge badge-duda">Aprieta carencias</span>`;
    }
    return "";
  };

  const sellReasonBadge = (reason) => {
    if (!reason) return "";
    const map = {
      expensive_bench: "Banquillo caro",
      low_minutes: "Pocos minutos",
      low_production: "Baja prod.",
      surplus_to_demand: "Excedente",
      fund_buy: "Financiar carencias",
      injured_covered: "Lesión",
      form_drop: "Forma",
    };
    return `<span class="badge badge-duda">${map[reason] || reason}</span>`;
  };

  let queueExpanded = false;
  const QUEUE_PREVIEW = 6;

  function renderActionQueue(data) {
    const box = document.getElementById("action-queue");
    if (!box) return;
    const plan = data.action_plan || [];
    const labels = {
      buy_now: { title: "Pujar", cls: "act-buy" },
      clause_bid: { title: "Cláusula", cls: "act-clause" },
      sell: { title: "Vender", cls: "act-sell" },
      avoid: { title: "Evitar", cls: "act-avoid" },
      wait: { title: "Esperar", cls: "act-wait" },
      scout: { title: "Vigilar", cls: "act-scout" },
    };

    if (!plan.length) {
      box.innerHTML = `<p class="queue-empty">Sin acciones claras hoy.</p>`;
      return;
    }

    const fp = data.funding_plan || {};
    const fundingHint =
      fp.target != null && Number(fp.target) > 0
        ? `<p class="queue-funding-hint">Caja vs carencias Alta: objetivo ~${formatMoney(
            fp.target
          )}${
            fp.shortfall != null && Number(fp.shortfall) > 0
              ? ` · faltan ~${formatMoney(fp.shortfall)}`
              : " · cubierto"
          }${
            (fp.positions || []).length
              ? ` · ${escapeHtml((fp.positions || []).join(", "))}`
              : ""
          }</p>`
        : "";

    const hasMore = plan.length > QUEUE_PREVIEW;
    const expanded = queueExpanded || !hasMore;

    const itemsHtml = plan
      .map((a, idx) => {
        const meta = labels[a.action] || { title: a.action || "Acción", cls: "act-wait" };
        const tab =
          a.action === "sell" ? "squad" : a.action === "clause_bid" || a.action === "scout" ? "radar" : "market";
        const rivals = (a.rival_targets || [])
          .map((t) => t.team_name)
          .filter(Boolean)
          .slice(0, 2)
          .join(", ");
        const money =
          a.action === "clause_bid" && a.clause != null
            ? formatMoney(a.clause)
            : a.bid != null
              ? formatMoney(a.bid)
              : "";
        const primaryChips = [
          riskBadge(a.wait_risk || a.sell_risk),
          budgetBadge(a.budget_fit),
          fundingChip(a),
          coverageChips(a),
          a.action === "scout" ? `<span class="badge badge-duda">Ver cláusula</span>` : "",
        ]
          .filter(Boolean)
          .join("");
        const secondaryChips = [
          sellReasonBadge(a.sell_reason),
          a.fills_structural && a.structural_label
            ? `<span class="badge badge-mint">${escapeHtml(a.structural_label)}</span>`
            : a.fills_need
              ? `<span class="badge badge-duda">Carencia</span>`
              : "",
          a.in_lineup ? `<span class="badge badge-once">Once</span>` : "",
          a.plays_little || (a.lineup_pct != null && Number(a.lineup_pct) < 40)
            ? `<span class="badge badge-baja-ext">Pocos min</span>`
            : a.lineup_pct != null
              ? `<span class="badge badge-duda">Titular ${Math.round(Number(a.lineup_pct))}%</span>`
              : "",
          a.mister_avg != null || a.points != null ? scoringLine(a) : "",
          a.ff_mister_avg != null
            ? `<span class="badge badge-duda">Mister ${Number(a.ff_mister_avg).toFixed(1)}</span>`
            : "",
          pointsTrendBadge(a.points_trend),
        ]
          .filter(Boolean)
          .join("");
        const muted =
          a.line_already_covered && !a.is_upgrade && (a.action === "wait" || a.action === "scout");
        const hidden = !expanded && idx >= QUEUE_PREVIEW;
        const topCls = idx < 3 ? " is-top" : "";
        return `<button type="button" class="queue-item ${meta.cls}${topCls}${
          muted ? " is-covered" : ""
        }${hidden ? " is-collapsed-extra" : ""}" role="listitem" data-focus-id="${escapeHtml(
          a.player_id
        )}" data-focus-tab="${tab}" ${hidden ? "hidden" : ""} aria-label="${escapeHtml(
          meta.title
        )} ${escapeHtml(a.name || "")}, prioridad ${idx + 1}">
          <span class="queue-rank" aria-hidden="true">${idx + 1}</span>
          <div class="queue-body">
            <div class="queue-primary">
              <div class="queue-headline">
                <span class="queue-action">${escapeHtml(meta.title)}</span>
                <span class="queue-name">${escapeHtml(a.name || "")}</span>
              </div>
              ${money ? `<span class="queue-money">${money}</span>` : ""}
            </div>
            ${a.why ? `<p class="queue-why">${escapeHtml(a.why)}</p>` : ""}
            ${
              rivals || a.compared_to
                ? `<p class="queue-meta">${[
                    a.compared_to ? `Mejora a ${escapeHtml(a.compared_to)}` : "",
                    rivals ? `Interesados: ${escapeHtml(rivals)}` : "",
                  ]
                    .filter(Boolean)
                    .join(" · ")}</p>`
                : ""
            }
            ${
              primaryChips || secondaryChips
                ? `<div class="queue-chips">
              <span class="queue-chips-main">${primaryChips}</span>
              ${secondaryChips ? `<span class="queue-chips-more">${secondaryChips}</span>` : ""}
            </div>`
                : ""
            }
          </div>
        </button>`;
      })
      .join("");

    const rest = plan.length - QUEUE_PREVIEW;
    const toggleHtml = hasMore
      ? `<button type="button" class="queue-expand-btn" id="queue-expand-btn" aria-expanded="${
          expanded ? "true" : "false"
        }">${
          expanded
            ? `Mostrar menos`
            : `Ver las ${rest} restantes · ${plan.length} en total`
        }</button>`
      : "";

    box.className = "action-queue-list" + (expanded ? " is-expanded" : "");
    box.setAttribute("role", "list");
    box.innerHTML = fundingHint + itemsHtml + toggleHtml;

    box.querySelectorAll("[data-focus-id]").forEach((btn) => {
      btn.addEventListener("click", () => {
        focusPlayer(btn.getAttribute("data-focus-id"), btn.getAttribute("data-focus-tab") || "market");
      });
    });

    const expandBtn = document.getElementById("queue-expand-btn");
    if (expandBtn) {
      expandBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        queueExpanded = !queueExpanded;
        renderActionQueue(data);
        const again = document.getElementById("queue-expand-btn");
        if (again) again.focus();
      });
    }
  }

  function renderMarket() {
    const tbody = document.querySelector("#table-market tbody");
    const cards = document.getElementById("market-cards");
    const f = getFilters();
    const rows = sortRows(
      "market",
      (DATA.market_opportunities || []).filter((p) => matchPlayer(p, f))
    );
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="11" class="text-slate-500">Sin resultados con estos filtros.</td></tr>`;
      if (cards) cards.innerHTML = `<p class="empty-state">Sin resultados con estos filtros.</p>`;
      return;
    }
    tbody.innerHTML = rows
      .map((p) => {
        const d = Number(p.delta_5d || 0);
        return `<tr data-player-id="${escapeHtml(p.id)}">
          <td>
            <div class="font-medium text-white">${escapeHtml(p.name)}</div>
            <div class="text-xs text-slate-500">${escapeHtml(p.team || "")}</div>
          </td>
          <td>${posChip(p.position)}</td>
          <td>${externalStatusBadge(p)}</td>
          <td>${signalChips(p)} ${ffAvgLine(p)}</td>
          <td class="text-slate-300 text-xs">${escapeHtml(p.category_label || p.category || "")}</td>
          <td>${formatMoney(p.price)}</td>
          <td class="${d >= 0 ? "delta-up" : "delta-down"}">${
            p.delta_5d != null
              ? pct(d)
              : p.trend === "up"
                ? "↑"
                : p.trend === "down"
                  ? "↓"
                  : "—"
          }</td>
          <td>${fotmobCell(p)}</td>
          <td class="text-mint-400 font-medium">${formatMoney(p.puja_recomendada)}</td>
          <td>${formatMoney(p.puja_techo)}</td>
          <td>${priorityBadge(p.priority)}</td>
        </tr>`;
      })
      .join("");
    if (cards) {
      cards.innerHTML = rows
        .map((p) => {
          const d = Number(p.delta_5d || 0);
          const deltaLabel =
            p.delta_5d != null ? pct(d) : p.trend === "up" ? "↑" : p.trend === "down" ? "↓" : "—";
          return `<article class="player-card" data-player-id="${escapeHtml(p.id)}" role="button" tabindex="0">
            <div class="player-card-top">
              <div>
                <div class="font-medium text-white">${escapeHtml(p.name)}</div>
                <div class="text-xs text-slate-500 mt-0.5">${escapeHtml(p.team || "")}</div>
              </div>
              ${priorityBadge(p.priority)}
            </div>
            <div class="player-card-meta">
              ${posChip(p.position)}
              ${externalStatusBadge(p)}
              ${ffAvgLine(p)}
              ${signalChips(p)}
            </div>
            <div class="player-card-stats">
              <div><div class="stat-label">Precio</div><div class="stat-value">${formatMoney(p.price)}</div></div>
              <div><div class="stat-label">Puja rec.</div><div class="stat-value text-mint-400">${formatMoney(p.puja_recomendada)}</div></div>
              <div><div class="stat-label">Δ / tendencia</div><div class="stat-value ${d >= 0 ? "delta-up" : "delta-down"}">${deltaLabel}</div></div>
              <div><div class="stat-label">FotMob</div><div class="stat-value">${fotmobCell(p)}</div></div>
            </div>
          </article>`;
        })
        .join("");
      cards.querySelectorAll(".player-card").forEach((el) => {
        const go = () => focusPlayer(el.getAttribute("data-player-id"), "market");
        el.addEventListener("click", go);
        el.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") go();
        });
      });
    }
  }

  function renderSquadHealth() {
    const diag = DATA.diagnostico_plantilla;
    const scoreEl = document.getElementById("health-score");
    const barsEl = document.getElementById("budget-bars");
    const linesEl = document.getElementById("line-status");
    const tipsEl = document.getElementById("health-tips");
    if (!scoreEl || !barsEl || !linesEl || !tipsEl) return;

    if (!diag) {
      scoreEl.textContent = "—";
      barsEl.innerHTML = "";
      linesEl.innerHTML = "";
      tipsEl.innerHTML = `<p class="text-slate-500 text-sm">Sin diagnóstico estructural todavía. Regenera los datos.</p>`;
      return;
    }

    const score = Number(diag.salud_score ?? 0);
    scoreEl.textContent = String(score);
    scoreEl.className = `health-score ${
      score >= 75 ? "health-good" : score >= 50 ? "health-mid" : "health-bad"
    }`;

    const fin = diag.financiero || {};
    const dist = fin.budget_distribution || {};
    const segments = [
      ["estrellas_top", "var(--mint)", dist.estrellas_top],
      ["titulares_medios", "#38bdf8", dist.titulares_medios],
      ["banquillo_parches", "#fb923c", dist.banquillo_parches],
    ];
    barsEl.innerHTML = `
      <div class="budget-meta">
        <span>Plantilla ${formatMoney(fin.valor_plantilla)} · Saldo ${formatMoney(fin.saldo)}</span>
        <span class="text-mint-400">Total equipo ${formatMoney(fin.valor_total_equipo)}</span>
      </div>
      <div class="budget-track" role="img" aria-label="Distribución del valor de plantilla">
        ${segments
          .map(([, color, seg]) => {
            const pct = Math.max(0, Number(seg?.pct) || 0);
            if (pct <= 0) return "";
            return `<div class="budget-seg" style="width:${pct}%;background:${color}" title="${escapeHtml(
              seg?.label || ""
            )}: ${pct}%"></div>`;
          })
          .join("")}
      </div>
      <ul class="budget-legend">
        ${segments
          .map(
            ([, color, seg]) => `<li>
            <span class="legend-dot" style="background:${color}"></span>
            <span>${escapeHtml(seg?.label || "—")}</span>
            <strong>${Number(seg?.pct) || 0}%</strong>
            <span class="text-slate-500">${formatMoney(seg?.value)}</span>
          </li>`
          )
          .join("")}
      </ul>
      ${
        fin.top_check
          ? `<p class="budget-note">${escapeHtml(fin.top_check.message || "")}${
              fin.top_check.basis === "ff_mister_mixto" ? " · Base: FF Mister Mixto" : ""
            }</p>`
          : ""
      }
      ${
        (fin.top_players || []).length
          ? `<ul class="top-players-list">${(fin.top_players || [])
              .slice(0, 6)
              .map((tp) => {
                const avg = tp.ff_mister_avg != null ? Number(tp.ff_mister_avg).toFixed(1) : "—";
                const why = tp.top_reason ? ` · ${escapeHtml(tp.top_reason)}` : "";
                return `<li><strong>${escapeHtml(tp.name || "")}</strong> · FF ${avg}${why}</li>`;
              })
              .join("")}</ul>`
          : ""
      }
    `;

    const lineas = diag.lineas || {};
    const labels = { GK: "Portería", DF: "Defensa", MF: "Medio", FW: "Delantera" };
    const covBadge = (cov) => {
      if (cov === "critical") return ["badge-alta", "critical"];
      if (cov === "thin") return ["badge-media", "thin"];
      return ["badge-mint", "ok"];
    };
    linesEl.innerHTML = ["GK", "DF", "MF", "FW"]
      .map((pos) => {
        const L = lineas[pos] || {};
        const cov = L.coverage || (L.status === "critical" ? "critical" : L.status === "warning" ? "thin" : "ok");
        const [badgeCls, covKey] = covBadge(cov);
        const starters = L.starters_real ?? L.starters ?? 0;
        const starterTgt = L.starters_target ?? { GK: 1, DF: 3, MF: 3, FW: 2 }[pos];
        const alts = L.alternates || [];
        const ideal = L.ideal_count ?? { GK: 2, DF: 5, MF: 5, FW: 3 }[pos];
        const usable = L.usable_count ?? starters + (L.alternates_count || alts.length);
        const altHtml = alts.length
          ? `<ul class="line-alts">${alts
              .slice(0, 3)
              .map((a) => {
                const pct =
                  a.lineup_pct != null
                    ? `${Math.round(Number(a.lineup_pct))}%`
                    : a.lineup_prob != null
                      ? `${Math.round(Number(a.lineup_prob) * 100)}%`
                      : "—";
                return `<li><span>${escapeHtml(a.name || "")}</span><span>${pct} · ${formatMoney(
                  a.price
                )}</span></li>`;
              })
              .join("")}</ul>`
          : `<p class="line-depth">Sin alternativas usables</p>`;
        return `<div class="line-chip status-${covKey}">
          <div class="line-chip-top">
            <span>${labels[pos]}</span>
            <span class="badge ${badgeCls}">${cov}</span>
          </div>
          <p>${escapeHtml(L.message || "—")}</p>
          <p class="line-depth">Titulares ${starters}/${starterTgt} · usable ${usable}/${ideal}</p>
          ${altHtml}
        </div>`;
      })
      .join("");

    const parches = diag.parches || {};
    const tips = diag.consejos || [];
    const levelClass = { ok: "tip-ok", suggestion: "tip-suggestion", alert: "tip-alert" };
    const levelIcon = { ok: "Acierto", suggestion: "Sugerencia", alert: "Alerta" };
    const patchTone =
      parches.status === "ok" ? "ok" : parches.status === "critical" ? "alert" : "suggestion";
    tipsEl.innerHTML =
      (parches.message
        ? `<div class="tip-card tip-${patchTone}">
            <div class="tip-label">Parches · ${parches.count ?? 0}/${parches.ideal ?? 3}</div>
            <p>${escapeHtml(parches.message)}</p>
          </div>`
        : "") +
      (tips.length
        ? tips
            .map((t) => {
              const lv = t.level || "suggestion";
              return `<article class="tip-card ${levelClass[lv] || "tip-suggestion"}" ${
                (t.related_player_ids || [])[0]
                  ? `data-focus-id="${escapeHtml((t.related_player_ids || [])[0])}" role="button" tabindex="0"`
                  : ""
              }>
            <div class="tip-label">${levelIcon[lv] || "Nota"} · ${escapeHtml(t.title || "")}</div>
            <p>${escapeHtml(t.message || "")}</p>
          </article>`;
            })
            .join("")
        : `<p class="text-slate-500 text-sm">Sin consejos adicionales.</p>`);

    tipsEl.querySelectorAll("[data-focus-id]").forEach((el) => {
      const go = () => focusPlayer(el.getAttribute("data-focus-id"), "squad");
      el.addEventListener("click", go);
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") go();
      });
    });
  }

  function renderSquad() {
    renderSquadHealth();
    const diagnosis = DATA.squad_diagnosis || {};
    const alertsBox = document.getElementById("squad-alerts");
    const alerts = diagnosis.alerts || [];
    alertsBox.innerHTML = alerts.length
      ? alerts
          .map(
            (a) => `<div class="badge ${a.level === "critical" ? "badge-critical" : "badge-warning"} !text-xs !normal-case !tracking-normal !px-3 !py-2 !rounded-lg">
              ${escapeHtml(a.message)}
            </div>`
          )
          .join("")
      : `<p class="text-slate-500 text-sm">Sin alertas de carencia.</p>`;

    const posBox = document.getElementById("squad-positions");
    const byPos = diagnosis.by_position || {};
    const order = ["GK", "DF", "MF", "FW"];
    const idealMap = (DATA.kpis || {}).ideal_squad || { GK: 2, DF: 5, MF: 5, FW: 3 };
    posBox.innerHTML = order
      .map((pos) => {
        const info = byPos[pos] || { count: 0, healthy: 0, starters: 0, status: "ok" };
        const cov = info.coverage || (info.status === "critical" ? "critical" : info.status === "warning" ? "thin" : "ok");
        const starters = info.starters_real ?? info.starters ?? 0;
        const altsN = info.alternates_count;
        const ideal = info.ideal_count ?? idealMap[pos];
        return `<div class="pos-block ${info.status}">
          <div class="flex justify-between items-center mb-2">
            <span class="font-semibold text-white">${pos}</span>
            <span class="badge ${
              cov === "critical" ? "badge-alta" : cov === "thin" ? "badge-media" : "badge-mint"
            }">${cov}</span>
          </div>
          <p class="text-sm text-slate-400">${info.count} jugadores · ${starters} titulares reales${
            altsN != null ? ` · ${altsN} alternativas` : ""
          } · objetivo ${ideal}</p>
        </div>`;
      })
      .join("");

    const f = getFilters();
    const tbody = document.querySelector("#table-squad tbody");
    const cards = document.getElementById("squad-cards");
    const rows = sortRows(
      "squad",
      (DATA.me?.squad || []).filter((p) => matchPlayer(p, f))
    );
    tbody.innerHTML = rows.length
      ? rows
          .map(
            (p) => `<tr data-player-id="${escapeHtml(p.id)}">
              <td>
                <div class="font-medium text-white">${escapeHtml(p.name)}</div>
                <div class="text-xs text-slate-500">${escapeHtml(p.team || "")}</div>
              </td>
              <td>${posChip(p.position)}</td>
              <td>${formatMoney(p.price)}</td>
              <td>${formCell(p)}</td>
              <td>${p.lineup_prob != null ? `${Math.round(Number(p.lineup_prob) * 100)}%` : "—"}</td>
              <td>${fotmobCell(p)}</td>
              <td>${externalStatusBadge(p)} ${signalChips(p)} ${ffAvgLine(p)}</td>
            </tr>`
          )
          .join("")
      : `<tr><td colspan="7" class="text-slate-500">Sin resultados.</td></tr>`;
    if (cards) {
      cards.innerHTML = rows.length
        ? rows
            .map(
              (p) => `<article class="player-card" data-player-id="${escapeHtml(p.id)}">
            <div class="player-card-top">
              <div>
                <div class="font-medium text-white">${escapeHtml(p.name)}</div>
                <div class="text-xs text-slate-500 mt-0.5">${escapeHtml(p.team || "")}</div>
              </div>
              ${posChip(p.position)}
            </div>
            <div class="player-card-meta">${externalStatusBadge(p)} ${ffAvgLine(p)} ${signalChips(p)}</div>
            <div class="player-card-stats">
              <div><div class="stat-label">Precio</div><div class="stat-value">${formatMoney(p.price)}</div></div>
              <div><div class="stat-label">Forma</div><div class="stat-value">${formCell(p)}</div></div>
              <div><div class="stat-label">Alineación</div><div class="stat-value">${p.lineup_prob != null ? `${Math.round(Number(p.lineup_prob) * 100)}%` : "—"}</div></div>
              <div><div class="stat-label">FotMob</div><div class="stat-value">${fotmobCell(p)}</div></div>
            </div>
          </article>`
            )
            .join("")
        : `<p class="empty-state">Sin resultados.</p>`;
    }
  }

  function renderRadar() {
    const f = getFilters();
    const upBody = document.querySelector("#table-rival-upgrades tbody");
    const upCards = document.getElementById("upgrade-cards");
    const ups = sortRows(
      "upgrades",
      (DATA.rival_upgrades || []).filter((p) => {
        const fake = { name: p.name, position: p.position, team: p.owner_team, price: p.market_value };
        return matchPlayer(fake, f);
      })
    );
    if (upBody) {
      upBody.innerHTML = ups.length
        ? ups
            .map(
              (u) => `<tr data-player-id="${escapeHtml(u.player_id)}">
            <td>
              <div class="font-medium text-white">${escapeHtml(u.name)}</div>
              ${u.compared_to ? `<div class="text-xs text-mint-500/80">Mejora a ${escapeHtml(u.compared_to)}</div>` : ""}
            </td>
            <td>${posChip(u.position)}</td>
            <td>${escapeHtml(u.owner_team || "")} <span class="text-slate-500">#${u.owner_rank ?? "—"}</span></td>
            <td>${formatMoney(u.market_value)}</td>
            <td>${scoringLine(u)}</td>
            <td>${pointsTrendBadge(u.points_trend)}</td>
            <td>${u.clause_known && u.clause != null ? formatMoney(u.clause) : "—"}</td>
            <td><span class="badge ${u.action === "clause_bid" ? "badge-mint" : "badge-duda"}">${escapeHtml(
              u.action === "clause_bid" ? "Cláusula" : "Vigilar"
            )}</span></td>
            <td class="text-xs text-slate-400">${escapeHtml(u.why || "")}</td>
          </tr>`
            )
            .join("")
        : `<tr><td colspan="9" class="text-slate-500">Sin upgrades claros en rivales hoy.</td></tr>`;
    }
    if (upCards) {
      upCards.innerHTML = ups.length
        ? ups
            .map(
              (u) => `<article class="player-card" data-player-id="${escapeHtml(u.player_id)}">
            <div class="player-card-top">
              <div>
                <div class="font-medium text-white">${escapeHtml(u.name)}</div>
                <div class="text-xs text-slate-500 mt-0.5">${escapeHtml(u.owner_team || "")} · #${u.owner_rank ?? "—"}</div>
                ${u.compared_to ? `<div class="text-xs text-mint-500/80 mt-0.5">Mejora a ${escapeHtml(u.compared_to)}</div>` : ""}
              </div>
              <span class="badge ${u.action === "clause_bid" ? "badge-mint" : "badge-duda"}">${
                u.action === "clause_bid" ? "Cláusula" : "Vigilar"
              }</span>
            </div>
            <div class="player-card-meta">
              ${posChip(u.position)}
              ${scoringLine(u)}
              ${pointsTrendBadge(u.points_trend)}
            </div>
            <div class="player-card-stats">
              <div><div class="stat-label">Valor</div><div class="stat-value">${formatMoney(u.market_value)}</div></div>
              <div><div class="stat-label">Cláusula</div><div class="stat-value">${
                u.clause_known && u.clause != null ? formatMoney(u.clause) : "—"
              }</div></div>
            </div>
            ${u.why ? `<p class="text-xs text-slate-400 mt-2">${escapeHtml(u.why)}</p>` : ""}
          </article>`
            )
            .join("")
        : `<p class="empty-state">Sin upgrades claros en rivales hoy.</p>`;
    }

    const freeBody = document.querySelector("#table-free tbody");
    const freeCards = document.getElementById("free-cards");
    const free = sortRows(
      "free",
      (DATA.free_agents_top || []).filter((p) => matchPlayer(p, f))
    );
    const freeNote = (DATA.meta && DATA.meta.free_agents_note) || null;
    const freeEmpty = escapeHtml(freeNote || "Sin libres TOP con estos filtros.");
    if (freeBody) {
      freeBody.innerHTML = free.length
        ? free
            .map(
              (p) => `<tr data-player-id="${escapeHtml(p.id)}">
          <td>
            <div class="font-medium text-white">${escapeHtml(p.name)}</div>
            <div class="text-xs text-slate-500">${escapeHtml(p.team || "")}</div>
          </td>
          <td>${posChip(p.position)}</td>
          <td>${formatMoney(p.price)}</td>
          <td>${p.avg_ppg != null ? Number(p.avg_ppg).toFixed(1) : "—"}</td>
          <td>${p.reliability != null ? Number(p.reliability).toFixed(2) : "—"}</td>
          <td class="text-mint-400">${p.roi_ppg_per_million != null ? Number(p.roi_ppg_per_million).toFixed(2) : "—"}</td>
        </tr>`
            )
            .join("")
        : `<tr><td colspan="6" class="text-slate-500">${freeEmpty}</td></tr>`;
    }
    if (freeCards) {
      freeCards.innerHTML = free.length
        ? free
            .map(
              (p) => `<article class="player-card" data-player-id="${escapeHtml(p.id)}">
            <div class="player-card-top">
              <div>
                <div class="font-medium text-white">${escapeHtml(p.name)}</div>
                <div class="text-xs text-slate-500 mt-0.5">${escapeHtml(p.team || "")}</div>
              </div>
              ${posChip(p.position)}
            </div>
            <div class="player-card-stats">
              <div><div class="stat-label">Precio</div><div class="stat-value">${formatMoney(p.price)}</div></div>
              <div><div class="stat-label">PPG</div><div class="stat-value">${p.avg_ppg != null ? Number(p.avg_ppg).toFixed(1) : "—"}</div></div>
              <div><div class="stat-label">Fiabilidad</div><div class="stat-value">${p.reliability != null ? Number(p.reliability).toFixed(2) : "—"}</div></div>
              <div><div class="stat-label">ROI / M€</div><div class="stat-value text-mint-400">${
                p.roi_ppg_per_million != null ? Number(p.roi_ppg_per_million).toFixed(2) : "—"
              }</div></div>
            </div>
          </article>`
            )
            .join("")
        : `<p class="empty-state">${freeEmpty}</p>`;
    }

    const rivalsBody = document.querySelector("#table-rivals tbody");
    const rivalsCards = document.getElementById("rivals-cards");
    const rivals = sortRows("rivals", [...(DATA.rivals || [])]);
    if (rivalsBody) {
      rivalsBody.innerHTML = rivals
        .map(
          (r) => `<tr>
        <td class="text-slate-400">${r.rank ?? "—"}</td>
        <td class="font-medium text-white">${escapeHtml(r.team_name || "")}</td>
        <td>${escapeHtml(r.manager || "")}</td>
        <td>${r.points ?? "—"}</td>
        <td class="text-mint-400">${
          r.liquidity_estimated != null
            ? formatMoney(r.liquidity_estimated)
            : r.squad_value != null
              ? formatMoney(r.squad_value) + " val."
              : "—"
        }</td>
        <td><span class="badge badge-baja">${escapeHtml(r.activity || "—")}</span></td>
        <td>${
          (r.position_gaps || []).length
            ? (r.position_gaps || []).map(posChip).join(" ")
            : '<span class="text-slate-600">—</span>'
        }</td>
      </tr>`
        )
        .join("");
    }
    if (rivalsCards) {
      rivalsCards.innerHTML = rivals.length
        ? rivals
            .map(
              (r) => `<article class="player-card">
            <div class="player-card-top">
              <div>
                <div class="font-medium text-white">#${r.rank ?? "—"} · ${escapeHtml(r.team_name || "")}</div>
                <div class="text-xs text-slate-500 mt-0.5">${escapeHtml(r.manager || "")}</div>
              </div>
              <span class="badge badge-baja">${escapeHtml(r.activity || "—")}</span>
            </div>
            <div class="player-card-stats">
              <div><div class="stat-label">Puntos</div><div class="stat-value">${r.points ?? "—"}</div></div>
              <div><div class="stat-label">Caja / valor</div><div class="stat-value text-mint-400">${
                r.liquidity_estimated != null
                  ? formatMoney(r.liquidity_estimated)
                  : r.squad_value != null
                    ? formatMoney(r.squad_value)
                    : "—"
              }</div></div>
            </div>
            <div class="player-card-meta mt-2">
              ${(r.position_gaps || []).length ? (r.position_gaps || []).map(posChip).join(" ") : '<span class="text-slate-600 text-xs">Sin carencias</span>'}
            </div>
          </article>`
            )
            .join("")
        : `<p class="empty-state">Sin datos de rivales.</p>`;
    }
  }

  function renderAll() {
    if (!DATA) return;
    renderMeta(DATA);
    renderKpis(DATA);
    renderActionQueue(DATA);
    renderMarket();
    renderSquad();
    renderRadar();
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ---- Tabs + móvil ----
  function initTabs() {
    document.querySelectorAll(".tab, .mobile-nav-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.getAttribute("data-tab");
        if (id) selectTab(id);
      });
    });
  }

  function updateFiltersSummary() {
    const f = getFilters();
    const parts = [];
    if (f.q) parts.push(`«${f.q}»`);
    if (f.pos) parts.push(f.pos);
    if (f.priceMax != null) parts.push(`≤${f.priceMax} M€`);
    if (f.formMin != null) parts.push(`forma ≥${f.formMin}`);
    if (f.avail) parts.push(f.avail);
    const el = document.getElementById("filters-summary");
    if (el) el.textContent = parts.length ? parts.join(" · ") : "Sin filtros activos";
  }

  function applyFilters() {
    updateFiltersSummary();
    if (!DATA) return;
    renderMarket();
    renderSquad();
    renderRadar();
  }

  function setExpanded(btn, panel, open) {
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    panel.hidden = !open;
    panel.classList.toggle("is-collapsed", !open);
  }

  function initCollapses() {
    const isMobile = () => window.matchMedia("(max-width: 767px)").matches;

    const filtersBtn = document.getElementById("filters-toggle");
    const filtersBody = document.getElementById("filters-body");
    if (filtersBtn && filtersBody) {
      setExpanded(filtersBtn, filtersBody, !isMobile());
      filtersBtn.addEventListener("click", () => {
        setExpanded(filtersBtn, filtersBody, filtersBtn.getAttribute("aria-expanded") !== "true");
      });
    }
  }

  // ---- PWA ----
  function isIos() {
    return /iphone|ipad|ipod/i.test(navigator.userAgent);
  }

  function isStandalone() {
    return (
      window.matchMedia("(display-mode: standalone)").matches ||
      window.navigator.standalone === true
    );
  }

  function showInstallBanner({ title, text, showButton }) {
    const banner = document.getElementById("install-banner");
    document.getElementById("install-title").textContent = title;
    document.getElementById("install-text").textContent = text;
    document.getElementById("install-btn").classList.toggle("hidden", !showButton);
    banner.classList.remove("hidden");
  }

  function initPwa() {
    if ("serviceWorker" in navigator) {
      const isSecure =
        location.protocol === "https:" ||
        location.hostname === "localhost" ||
        location.hostname === "127.0.0.1";
      if (isSecure) {
        navigator.serviceWorker.register("./sw.js").catch((err) => {
          console.warn("SW register failed", err);
        });
      }
    }

    window.addEventListener("beforeinstallprompt", (e) => {
      e.preventDefault();
      deferredPrompt = e;
      if (!isStandalone()) {
        showInstallBanner({
          title: "Instalar app",
          text: "Añade Mister Advisor a tu pantalla de inicio.",
          showButton: true,
        });
      }
    });

    document.getElementById("install-btn").addEventListener("click", async () => {
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      await deferredPrompt.userChoice;
      deferredPrompt = null;
      document.getElementById("install-banner").classList.add("hidden");
    });

    document.getElementById("install-dismiss").addEventListener("click", () => {
      document.getElementById("install-banner").classList.add("hidden");
    });

    // Tip iOS (no hay beforeinstallprompt)
    if (isIos() && !isStandalone()) {
      showInstallBanner({
        title: "Añadir a inicio",
        text: "En Safari: Compartir → Añadir a pantalla de inicio.",
        showButton: false,
      });
    }
  }

  // ---- Boot ----
  async function loadData() {
    const errEl = document.getElementById("load-error");
    try {
      const res = await fetch("./data/latest_data.json", { cache: "no-cache" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      DATA = await res.json();
      errEl.classList.add("hidden");
      renderAll();
    } catch (err) {
      console.error(err);
      errEl.textContent =
        "No se pudo cargar latest_data.json. Sirve la carpeta public/ por HTTP y ejecuta el data engine.";
      errEl.classList.remove("hidden");
    }
  }

  function initFilters() {
    ["filter-q", "filter-pos", "filter-price", "filter-form", "filter-avail"].forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener("input", applyFilters);
      el.addEventListener("change", applyFilters);
    });
    const clearBtn = document.getElementById("filters-clear");
    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        ["filter-q", "filter-pos", "filter-price", "filter-form", "filter-avail"].forEach((id) => {
          const el = document.getElementById(id);
          if (el) el.value = "";
        });
        applyFilters();
      });
    }
    updateFiltersSummary();
  }

  document.addEventListener("DOMContentLoaded", () => {
    initTabs();
    initCollapses();
    initFilters();
    initSortControls();
    initPwa();
    loadData();
  });
})();
