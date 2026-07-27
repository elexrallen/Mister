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

  function isFixedMarket(data) {
    const d = data || DATA;
    if (!d) return false;
    const mode =
      (d.league && d.league.market_mode) ||
      (d.sources && d.sources.market_mode) ||
      (d.daily_package && d.daily_package.market_mode) ||
      "auction";
    return String(mode).toLowerCase() === "fixed";
  }

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
      ? ` <a class="ext-link" href="${escapeHtml(ext.profile_url)}" target="_blank" rel="noopener noreferrer" title="Abrir ficha">Fuente</a>`
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

  /** Forma Mister; en pretemporada (0) cae a media FF. Incluye PJ si hay muestra. */
  const formCell = (p) => {
    const mister = p.form != null ? Number(p.form) : p.mister_avg != null ? Number(p.mister_avg) : null;
    const ff = p.ff_mister_avg ?? (p.external || {}).ff_mister_avg;
    const apps = p.ff_apps ?? (p.external || {}).ff_apps;
    const appsBit =
      apps != null && !Number.isNaN(Number(apps))
        ? ` <span class="text-[10px] text-slate-500">· ${Number(apps)} PJ</span>`
        : "";
    if (mister != null && !Number.isNaN(mister) && mister > 0) {
      return `<span>${Number(mister).toFixed(1)}${appsBit}</span>`;
    }
    if (ff != null && !Number.isNaN(Number(ff))) {
      return `<span title="Media FF (ponderada por partidos)">${Number(ff).toFixed(1)} <span class="text-[10px] text-slate-500">FF</span>${appsBit}</span>`;
    }
    return "—";
  };

  const ffAvgAppsBadge = (p) => {
    const ff = p.ff_mister_avg ?? (p.external || {}).ff_mister_avg;
    if (ff == null) return "";
    const apps = p.ff_apps ?? (p.external || {}).ff_apps;
    const appsTxt = apps != null ? ` · ${Number(apps)} PJ` : "";
    return `<span class="badge badge-duda" title="Media FF y partidos">FF ${Number(ff).toFixed(1)}${appsTxt}</span>`;
  };

  const signalChips = (p) => {
    const ext = p.external || {};
    const chips = [];
    if (ext.is_chollo_ext) chips.push(`<span class="badge badge-mint">Chollo</span>`);
    if (ext.is_recommendation_ext) chips.push(`<span class="badge badge-titular">Reco</span>`);
    if ((p.is_top_ff || ext.is_top_ff) && !p.sample_thin) {
      chips.push(`<span class="badge badge-mint">TOP Mister</span>`);
    }
    if (p.sample_thin || (p.ff_apps != null && Number(p.ff_apps) < 8)) {
      chips.push(`<span class="badge badge-baja-ext" title="Pocos partidos FF">Muestra corta</span>`);
    }
    if (p.target_tier === "aspirational" || p.budget_fit === "blocked") {
      chips.push(`<span class="badge badge-baja">Fuera de caja</span>`);
    } else if (p.target_tier === "stretch") {
      chips.push(`<span class="badge badge-duda">Al límite</span>`);
    }
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
    // Click en jugador → abrir ficha FF / fuente; si no hay URL, resaltar en la app
    if (openPlayerSource(playerId)) return;
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

  /** Busca el registro del jugador en los listados del payload. */
  function findPlayerRecord(playerId) {
    if (playerId == null || !DATA) return null;
    const id = String(playerId);
    const pools = [
      DATA.market_opportunities,
      DATA.free_agents_top,
      (DATA.me && DATA.me.squad) || [],
      DATA.rival_upgrades,
      DATA.action_plan,
    ];
    for (const list of pools) {
      if (!list || !list.length) continue;
      const hit = list.find(
        (p) => String(p.id || p.player_id || "") === id
      );
      if (hit) return hit;
    }
    // Rivales: buscar en sus plantillas
    for (const r of DATA.rivals || []) {
      const hit = (r.squad || []).find((p) => String(p.id || "") === id);
      if (hit) return hit;
    }
    return null;
  }

  /**
   * URL de ficha externa: FF jugadores > JP jugador > profile_url > Mister.
   */
  function playerSourceUrl(p) {
    if (!p) return null;
    const candidates = [];
    const ext = p.external || {};
    if (ext.profile_url) candidates.push(String(ext.profile_url));
    if (p.profile_url) candidates.push(String(p.profile_url));
    if (ext.ff_profile_url) candidates.push(String(ext.ff_profile_url));

    const score = (u) => {
      const s = String(u || "");
      if (!s) return -1;
      if (s.includes("futbolfantasy.com/jugadores/")) return 50;
      if (s.includes("jornadaperfecta.com/jugador/")) return 40;
      if (s.includes("comuniate.com") && s.includes("jugador")) return 30;
      if (s.includes("/partido/")) return 5;
      if (s.startsWith("http")) return 20;
      return 10;
    };

    let best = null;
    let bestScore = -1;
    for (const u of candidates) {
      const sc = score(u);
      if (sc > bestScore) {
        bestScore = sc;
        best = u;
      }
    }
    if (best && bestScore >= 20) return best;

    // Fallback Mister
    const mid = p.id || p.player_id;
    if (mid) {
      const slug = String(p.name || "")
        .toLowerCase()
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "");
      return slug
        ? `https://mister.mundodeportivo.com/players/${mid}/${slug}`
        : `https://mister.mundodeportivo.com/players/${mid}`;
    }
    return best;
  }

  /** Abre la ficha en pestaña nueva. Devuelve true si abrió algo. */
  function openPlayerSource(playerIdOrRecord) {
    const rec =
      typeof playerIdOrRecord === "object" && playerIdOrRecord
        ? playerIdOrRecord
        : findPlayerRecord(playerIdOrRecord);
    const url = playerSourceUrl(rec);
    if (!url) return false;
    window.open(url, "_blank", "noopener,noreferrer");
    return true;
  }

  /** Click/teclado en filas y cards con data-player-id → ficha externa. */
  function bindPlayerOpenClicks(root, tab) {
    if (!root) return;
    root.querySelectorAll("[data-player-id]").forEach((el) => {
      if (el.dataset.boundOpen === "1") return;
      el.dataset.boundOpen = "1";
      const go = () => focusPlayer(el.getAttribute("data-player-id"), tab);
      el.addEventListener("click", (e) => {
        if (e.target.closest("a.ext-link")) return;
        go();
      });
      if (el.matches("[tabindex], button, [role='button'], [role='link']")) {
        el.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            go();
          }
        });
      }
    });
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

  function renderMatchday(data) {
    const panel = document.getElementById("matchday-panel");
    if (!panel) return;
    const md = data.matchday;
    const advice = Array.isArray(data.gw_xi_advice) ? data.gw_xi_advice : [];
    const srcOk =
      data.sources &&
      data.sources.external &&
      ["ok", "partial", "cache"].includes(String(data.sources.external.ff_matchday || ""));
    if (!md || !srcOk) {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;
    const j = md.jornada != null ? `Jornada ${md.jornada}` : "Jornada";
    const title = document.getElementById("matchday-title");
    if (title) title.textContent = `${j} · FF`;
    const fx = md.fixtures_count != null ? `${md.fixtures_count} partidos` : "";
    const summary = document.getElementById("matchday-summary");
    if (summary) {
      summary.textContent = fx
        ? `${fx} · posibles alineaciones Fútbol Fantasy`
        : "Posibles alineaciones Fútbol Fantasy";
    }
    const counts = { start: 0, doubt: 0, sit: 0 };
    for (const a of advice) {
      const k = a && a.advice;
      if (k && counts[k] != null) counts[k] += 1;
    }
    const elStart = document.getElementById("matchday-start");
    const elDoubt = document.getElementById("matchday-doubt");
    const elSit = document.getElementById("matchday-sit");
    if (elStart) elStart.textContent = String(counts.start);
    if (elDoubt) elDoubt.textContent = String(counts.doubt);
    if (elSit) elSit.textContent = String(counts.sit);
    const list = document.getElementById("matchday-advice");
    if (list) {
      const rows = advice.slice(0, 8).map((a) => {
        const badge =
          a.advice === "start"
            ? "badge-mint"
            : a.advice === "sit"
              ? "badge-baja-ext"
              : "badge-duda";
        const label =
          a.advice === "start" ? "Alinear" : a.advice === "sit" ? "No alinear" : "Duda";
        const prob = a.prob != null ? `${Math.round(Number(a.prob))}%` : "—";
        const why = a.why ? escapeHtml(a.why) : "";
        return `<li class="matchday-advice-item">
          <span class="badge ${badge}">${label}</span>
          <button type="button" class="player-link" data-player-id="${escapeHtml(
            String(a.player_id || "")
          )}">${escapeHtml(a.name || "—")}</button>
          <span class="matchday-prob">${prob}</span>
          <span class="matchday-why text-xs text-slate-400">${why}</span>
        </li>`;
      });
      list.innerHTML = rows.length
        ? rows.join("")
        : `<li class="matchday-advice-item text-slate-500">Sin señal GW en tu plantilla aún.</li>`;
      list.querySelectorAll("[data-player-id]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const id = btn.getAttribute("data-player-id");
          const squad = (data.me && data.me.squad) || [];
          const p = squad.find((x) => String(x.id) === String(id));
          if (p && typeof openPlayerSource === "function") openPlayerSource(p);
        });
      });
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
    renderMatchday(data);
    renderObjectives(data);
  }

  function renderObjectives(data) {
    const panel = document.getElementById("objectives-panel");
    if (!panel) return;
    const board = data.target_board;
    const squad = (board && board.perfect_squad) || [];
    if (!board || !squad.length) {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;
    const wealth = board.wealth || {};
    const totals = board.totals || {};
    const summary = board.summary || {};
    const title = document.getElementById("objectives-title");
    if (title) title.textContent = "Plantilla perfecta";
    const sumEl = document.getElementById("objectives-summary");
    if (sumEl) {
      const keep = summary.keep != null ? summary.keep : (board.moves && board.moves.keep || []).length;
      const buy = summary.buy != null ? summary.buy : (board.moves && board.moves.buy || []).length;
      const funded = totals.funded ? "financiable" : "faltan ventas/caja";
      sumEl.textContent = `${squad.length} plazas · ${keep} keep · ${buy} fichar · ${funded}`;
    }
    const elW = document.getElementById("objectives-wealth");
    const elC = document.getElementById("objectives-cost");
    const elEp = document.getElementById("objectives-ep");
    const elR = document.getElementById("objectives-reserve");
    if (elW) elW.textContent = formatMoney(wealth.total != null ? wealth.total : (board.balance || 0) + (board.squad_value || 0));
    if (elC) elC.textContent = formatMoney(totals.cost_sum);
    if (elEp) elEp.textContent = totals.ep_sum != null ? String(Math.round(Number(totals.ep_sum))) : "—";
    if (elR) elR.textContent = formatMoney(board.cash_reserved);

    const statusBadge = (st, role) => {
      const bits = [];
      if (st === "keep") bits.push(`<span class="badge badge-mint">Keep</span>`);
      else if (st === "buy") bits.push(`<span class="badge badge-alta">Fichar</span>`);
      else bits.push(`<span class="badge badge-duda">${escapeHtml(st || "")}</span>`);
      if (role === "starter") bits.push(`<span class="badge badge-titular">Titular</span>`);
      return bits.join(" ");
    };

    const grid = document.getElementById("objectives-grid");
    if (grid) {
      const byPos = { GK: [], DF: [], MF: [], FW: [] };
      for (const row of squad) {
        const pos = row.position || "MF";
        if (!byPos[pos]) byPos[pos] = [];
        byPos[pos].push(row);
      }
      const cols = ["GK", "DF", "MF", "FW"].map((pos) => {
        const rows = (byPos[pos] || [])
          .map((r) => {
            const delta =
              r.delta_5d != null ? `${(Number(r.delta_5d) * 100).toFixed(0)}%` : "—";
            return `<li class="objectives-player">
              <div class="objectives-player-head">${statusBadge(r.status, r.role)}</div>
              <button type="button" class="player-link" data-player-id="${escapeHtml(
                String(r.player_id || "")
              )}">${escapeHtml(r.name || "—")}</button>
              <span class="objectives-meta">${formatMoney(r.price)} · EP ${
                r.ep_score != null ? Math.round(Number(r.ep_score)) : "—"
              } · Δ ${delta}</span>
            </li>`;
          })
          .join("");
        return `<div class="objectives-col">
          <p class="objectives-col-title">${pos}</p>
          <ul class="objectives-col-list">${rows || `<li class="text-slate-500 text-xs">—</li>`}</ul>
        </div>`;
      });
      grid.innerHTML = cols.join("");
      grid.querySelectorAll("[data-player-id]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const id = btn.getAttribute("data-player-id");
          const all = [
            ...((data.me && data.me.squad) || []),
            ...(data.market_opportunities || []),
          ];
          const p = all.find((x) => String(x.id || x.player_id) === String(id));
          if (p && typeof openPlayerSource === "function") openPlayerSource(p);
        });
      });
    }

    const patches = board.daily_patches || [];
    const wrap = document.getElementById("objectives-patches-wrap");
    const list = document.getElementById("objectives-patches");
    if (wrap && list) {
      if (!patches.length) {
        wrap.hidden = true;
        list.innerHTML = "";
      } else {
        wrap.hidden = false;
        list.innerHTML = patches
          .map(
            (p) => `<li class="objectives-patch-item">
            <span class="badge badge-duda">${escapeHtml(p.position || "parche")}</span>
            <button type="button" class="player-link" data-player-id="${escapeHtml(
              String(p.player_id || "")
            )}">${escapeHtml(p.name || "—")}</button>
            <span class="objectives-meta">${formatMoney(p.price)} · EP ${
              p.ep_score != null ? Math.round(Number(p.ep_score)) : "—"
            }</span>
            <span class="objectives-why text-xs text-slate-400">${escapeHtml(p.why || "")}</span>
          </li>`
          )
          .join("");
        list.querySelectorAll("[data-player-id]").forEach((btn) => {
          btn.addEventListener("click", () => {
            const id = btn.getAttribute("data-player-id");
            const all = [
              ...((data.me && data.me.squad) || []),
              ...(data.market_opportunities || []),
            ];
            const p = all.find((x) => String(x.id || x.player_id) === String(id));
            if (p && typeof openPlayerSource === "function") openPlayerSource(p);
          });
        });
      }
    }

    const sells = ((board.moves || {}).sell || []).slice(0, 4);
    const sellEl = document.getElementById("objectives-sells");
    if (sellEl) {
      if (!sells.length) {
        sellEl.hidden = true;
        sellEl.textContent = "";
      } else {
        sellEl.hidden = false;
        sellEl.textContent = `Vender para financiar: ${sells
          .map((s) => s.name)
          .filter(Boolean)
          .join(" · ")}`;
      }
    }
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
    if (a.action === "sell" && (a.sell_reason === "fund_buy" || a.sell_reason === "fund_target" || a.budget_fit === "funding")) {
      return `<span class="badge badge-mint">${a.sell_reason === "fund_target" ? "Financia objetivo" : "Libera caja"}</span>`;
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
      fund_target: "Financia objetivo",
      injured_covered: "Lesión",
      form_drop: "Forma",
    };
    return `<span class="badge badge-duda">${map[reason] || reason}</span>`;
  };

  let queueExpanded = false;
  const QUEUE_PREVIEW = 6;
  let marketScope = "today"; // today | all

  function isDailyMarketPlayer(p) {
    return Boolean(p && (p.on_daily_market || p.seller === "market"));
  }

  function marketUniverse() {
    const all = DATA.market_opportunities || [];
    if (marketScope === "all") return all;
    return all.filter(isDailyMarketPlayer);
  }

  function updateMarketScopeUi() {
    document.querySelectorAll("[data-market-scope]").forEach((btn) => {
      const on = btn.getAttribute("data-market-scope") === marketScope;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
    const hint = document.getElementById("market-scope-hint");
    if (!hint || !DATA) return;
    const daily = (DATA.market_opportunities || []).filter(isDailyMarketPlayer).length;
    const total = (DATA.market_opportunities || []).length;
    const slots = (DATA.kpis || {}).market_day_slots;
    if (marketScope === "today") {
      hint.textContent = isFixedMarket()
        ? daily > 0
          ? `Mercado del día · precio fijo · ${daily}${slots != null ? ` (ref. ${slots} plazas)` : ""}`
          : "Sin mercado del día en los datos — prueba «Todos los libres»"
        : daily > 0
          ? `Solo pujables ahora · ${daily}${slots != null ? ` (ref. ${slots} plazas)` : ""}`
          : "Sin mercado del día en los datos — prueba «Todos los libres»";
    } else {
      hint.textContent = `Pool de libres enriquecido · ${total} jugadores`;
    }
  }

  function syncMarketModeUi(data) {
    const fixed = isFixedMarket(data);
    const actionsHint = document.getElementById("actions-hint");
    if (actionsHint) {
      actionsHint.textContent = fixed
        ? "Plan de hoy: hasta 2 fichajes al precio · el resto sin prisa"
        : "Plan de hoy: hasta 2 pujas compatibles · el resto es plan B";
    }
    const bidTh = document.getElementById("th-bid-label");
    if (bidTh) bidTh.textContent = fixed ? "Precio" : "Puja rec.";
    const techoTh = document.getElementById("th-techo");
    if (techoTh) {
      techoTh.hidden = fixed;
      techoTh.style.display = fixed ? "none" : "";
    }
    const radarPanel = document.getElementById("tab-radar");
    if (radarPanel) {
      const upgradesTitle = Array.from(radarPanel.querySelectorAll("h3")).find((h) =>
        /Objetivos en rivales/i.test(h.textContent || "")
      );
      const upgradeCards = document.getElementById("upgrade-cards");
      const upgradeTable = document.getElementById("table-rival-upgrades");
      const upgradeSort = radarPanel.querySelector('.list-sort[data-list="upgrades"]');
      [upgradesTitle, upgradeCards, upgradeTable && upgradeTable.closest(".table-wrap"), upgradeSort].forEach(
        (el) => {
          if (el) el.hidden = fixed;
        }
      );
    }
  }

  function renderActionQueue(data) {
    const box = document.getElementById("action-queue");
    if (!box) return;
    const plan = data.action_plan || [];
    const pkg = data.daily_package || {};
    const fixed = isFixedMarket(data);
    const labels = {
      buy_now: { title: fixed ? "Fichar" : "Pujar", cls: "act-buy" },
      clause_bid: { title: "Cláusula", cls: "act-clause" },
      sell: { title: "Vender", cls: "act-sell" },
      avoid: { title: "Evitar", cls: "act-avoid" },
      wait: { title: "Esperar", cls: "act-wait" },
      scout: { title: "Vigilar", cls: "act-scout" },
    };
    const roleChip = (role) => {
      if (role === "primary_target") return `<span class="badge badge-mint">Objetivo</span>`;
      if (role === "primary") return `<span class="badge badge-mint">Hacer</span>`;
      if (role === "secondary") return `<span class="badge badge-titular">También si cabe</span>`;
      if (role === "alt_if_lost") return `<span class="badge badge-duda">Plan B</span>`;
      if (role === "also_good") return `<span class="badge badge-duda">También</span>`;
      if (role === "do_not_stack") return `<span class="badge badge-baja">No acumular</span>`;
      if (role === "out_of_budget") return `<span class="badge badge-baja">Fuera de caja</span>`;
      return "";
    };

    if (!plan.length) {
      box.innerHTML = `<p class="queue-empty">Sin acciones claras hoy.</p>`;
      return;
    }

    const fp = data.funding_plan || {};
    const packageHint = pkg.primary
      ? `<p class="queue-package-hint">Plan de hoy: <strong>${escapeHtml(
          pkg.primary.name || ""
        )}</strong>${
          pkg.secondary ? ` + <strong>${escapeHtml(pkg.secondary.name || "")}</strong>` : ""
        } · gasto ~${formatMoney(pkg.spend_cap)} · queda ~${formatMoney(
          pkg.residual_after
        )}</p>`
      : pkg.note
        ? `<p class="queue-package-hint">${escapeHtml(pkg.note)}</p>`
        : "";
    const fundingHint =
      fp.target != null && Number(fp.target) > 0
        ? `<p class="queue-funding-hint">Reserva objetivos: ~${formatMoney(
            fp.cash_reserved != null ? fp.cash_reserved : fp.target
          )}${
            fp.shortfall != null && Number(fp.shortfall) > 0
              ? ` · faltan ~${formatMoney(fp.shortfall)}`
              : " · cubierto"
          }${
            (fp.primary_targets || []).length
              ? ` · ${(fp.primary_targets || [])
                  .slice(0, 2)
                  .map((t) => escapeHtml(t.name || ""))
                  .join(", ")}`
              : (fp.positions || []).length
                ? ` · ${escapeHtml((fp.positions || []).join(", "))}`
                : ""
          }</p>`
        : "";

    const doToday = plan.filter((a) => a.queue_role === "primary" || a.queue_role === "secondary");
    const planB = plan.filter((a) => a.queue_role === "alt_if_lost" || a.queue_role === "also_good");
    const noStack = plan.filter((a) => a.queue_role === "do_not_stack");
    const outOfBudget = plan.filter((a) => a.queue_role === "out_of_budget");
    const other = plan.filter(
      (a) =>
        ![
          "primary",
          "secondary",
          "alt_if_lost",
          "also_good",
          "do_not_stack",
          "out_of_budget",
        ].includes(a.queue_role)
    );

    const renderItem = (a, rankLabel, opts = {}) => {
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
        roleChip(a.queue_role),
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
        ffAvgAppsBadge(a),
        a.sample_thin
          ? `<span class="badge badge-baja-ext">Muestra corta</span>`
          : "",
        a.target_tier === "aspirational" || a.budget_fit === "blocked"
          ? `<span class="badge badge-baja">Fuera de caja</span>`
          : "",
        pointsTrendBadge(a.points_trend),
      ]
        .filter(Boolean)
        .join("");
      const muted =
        opts.muted ||
        a.queue_role === "do_not_stack" ||
        (a.line_already_covered && !a.is_upgrade && (a.action === "wait" || a.action === "scout"));
      const topCls = a.queue_role === "primary" || a.queue_role === "secondary" ? " is-top" : "";
      const rankDisplay = rankLabel != null ? String(rankLabel) : "·";
      return `<button type="button" class="queue-item ${meta.cls}${topCls}${
        muted ? " is-covered" : ""
      }${opts.hidden ? " is-collapsed-extra" : ""}" role="listitem" data-focus-id="${escapeHtml(
        a.player_id
      )}" data-focus-tab="${tab}" ${opts.hidden ? "hidden" : ""} aria-label="${escapeHtml(
        meta.title
      )} ${escapeHtml(a.name || "")}">
          <span class="queue-rank" aria-hidden="true">${escapeHtml(rankDisplay)}</span>
          <div class="queue-body">
            <div class="queue-primary">
              <div class="queue-headline">
                <span class="queue-action">${escapeHtml(meta.title)}</span>
                <span class="queue-name">${escapeHtml(a.name || "")}</span>
              </div>
              ${money ? `<span class="queue-money">${money}</span>` : ""}
            </div>
            ${
              a.package_note
                ? `<p class="queue-package-note">${escapeHtml(a.package_note)}</p>`
                : ""
            }
            ${a.why ? `<p class="queue-why">${escapeHtml(a.why)}</p>` : ""}
            ${
              (!fixed && rivals) || a.compared_to
                ? `<p class="queue-meta">${[
                    a.compared_to ? `Mejora a ${escapeHtml(a.compared_to)}` : "",
                    !fixed && rivals ? `Interesados: ${escapeHtml(rivals)}` : "",
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
    };

    const section = (title, items, rankMode) => {
      if (!items.length) return "";
      let n = 0;
      const body = items
        .map((a) => {
          if (rankMode === "numbered") {
            n += 1;
            return renderItem(a, n);
          }
          return renderItem(a, null, { muted: rankMode === "muted" });
        })
        .join("");
      return `<div class="queue-section">
        <h3 class="queue-section-title">${escapeHtml(title)}</h3>
        ${body}
      </div>`;
    };

    const OTHER_PREVIEW = 3;
    const hasMore = other.length > OTHER_PREVIEW;
    const expanded = queueExpanded || !hasMore;
    const otherVisible = expanded ? other : other.slice(0, OTHER_PREVIEW);
    const otherHidden = expanded ? [] : other.slice(OTHER_PREVIEW);

    const rest = other.length - OTHER_PREVIEW;
    const toggleHtml = hasMore
      ? `<button type="button" class="queue-expand-btn" id="queue-expand-btn" aria-expanded="${
          expanded ? "true" : "false"
        }">${
          expanded ? `Mostrar menos` : `Ver ${rest} acciones más (cláusulas / evitar / vigilar)`
        }</button>`
      : "";

    box.className = "action-queue-list" + (expanded ? " is-expanded" : "");
    box.setAttribute("role", "list");
    box.innerHTML =
      packageHint +
      fundingHint +
      section("Plan de hoy", doToday, "numbered") +
      section(fixed ? "También válidos" : "Si se van / plan B", planB, "muted") +
      section("No acumular con el paquete", noStack, "muted") +
      section("Fuera de caja / vigilar", outOfBudget, "muted") +
      section("Otras acciones", otherVisible, "muted") +
      otherHidden.map((a) => renderItem(a, null, { muted: true, hidden: true })).join("") +
      toggleHtml;

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
    updateMarketScopeUi();
    const tbody = document.querySelector("#table-market tbody");
    const cards = document.getElementById("market-cards");
    const f = getFilters();
    const rows = sortRows("market", marketUniverse().filter((p) => matchPlayer(p, f)));
    const emptyMsg =
      marketScope === "today"
        ? "Sin jugadores en el mercado de hoy con estos filtros."
        : "Sin resultados con estos filtros.";
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="${isFixedMarket() ? 10 : 11}" class="text-slate-500">${emptyMsg}</td></tr>`;
      if (cards) cards.innerHTML = `<p class="empty-state">${emptyMsg}</p>`;
      return;
    }
    tbody.innerHTML = rows
      .map((p) => {
        const d = Number(p.delta_5d || 0);
        return `<tr data-player-id="${escapeHtml(p.id)}" class="player-row is-clickable" title="Abrir ficha FF / fuente" role="link" tabindex="0">
          <td>
            <div class="font-medium text-white player-name-link">${escapeHtml(p.name)}</div>
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
          ${isFixedMarket() ? "" : `<td>${formatMoney(p.puja_techo)}</td>`}
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
          return `<article class="player-card is-clickable" data-player-id="${escapeHtml(p.id)}" role="button" tabindex="0" title="Abrir ficha FF / fuente">
            <div class="player-card-top">
              <div>
                <div class="font-medium text-white player-name-link">${escapeHtml(p.name)}</div>
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
              <div><div class="stat-label">${isFixedMarket() ? "Precio fichaje" : "Puja rec."}</div><div class="stat-value text-mint-400">${formatMoney(p.puja_recomendada)}</div></div>
              <div><div class="stat-label">Δ / tendencia</div><div class="stat-value ${d >= 0 ? "delta-up" : "delta-down"}">${deltaLabel}</div></div>
              <div><div class="stat-label">FotMob</div><div class="stat-value">${fotmobCell(p)}</div></div>
            </div>
          </article>`;
        })
        .join("");
      cards.querySelectorAll(".player-card").forEach((el) => {
        /* bound via bindPlayerOpenClicks */
      });
    }
    bindPlayerOpenClicks(tbody, "market");
    bindPlayerOpenClicks(cards, "market");
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
            (p) => `<tr data-player-id="${escapeHtml(p.id)}" class="player-row is-clickable" title="Abrir ficha FF / fuente" role="link" tabindex="0">
              <td>
                <div class="font-medium text-white player-name-link">${escapeHtml(p.name)}</div>
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
              (p) => `<article class="player-card is-clickable" data-player-id="${escapeHtml(p.id)}" role="button" tabindex="0" title="Abrir ficha FF / fuente">
            <div class="player-card-top">
              <div>
                <div class="font-medium text-white player-name-link">${escapeHtml(p.name)}</div>
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
      bindPlayerOpenClicks(cards, "squad");
    }
    bindPlayerOpenClicks(tbody, "squad");
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
              (u) => `<tr data-player-id="${escapeHtml(u.player_id)}" class="player-row is-clickable" title="Abrir ficha FF / fuente" role="link" tabindex="0">
            <td>
              <div class="font-medium text-white player-name-link">${escapeHtml(u.name)}</div>
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
              (u) => `<article class="player-card is-clickable" data-player-id="${escapeHtml(u.player_id)}" role="button" tabindex="0" title="Abrir ficha FF / fuente">
            <div class="player-card-top">
              <div>
                <div class="font-medium text-white player-name-link">${escapeHtml(u.name)}</div>
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
              (p) => `<tr data-player-id="${escapeHtml(p.id)}" class="player-row is-clickable" title="Abrir ficha FF / fuente" role="link" tabindex="0">
          <td>
            <div class="font-medium text-white player-name-link">${escapeHtml(p.name)}</div>
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
              (p) => `<article class="player-card is-clickable" data-player-id="${escapeHtml(p.id)}" role="button" tabindex="0" title="Abrir ficha FF / fuente">
            <div class="player-card-top">
              <div>
                <div class="font-medium text-white player-name-link">${escapeHtml(p.name)}</div>
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

    bindPlayerOpenClicks(upBody, "radar");
    bindPlayerOpenClicks(upCards, "radar");
    bindPlayerOpenClicks(freeBody, "radar");
    bindPlayerOpenClicks(freeCards, "radar");

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
    syncMarketModeUi(DATA);
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
  const LEAGUE_STORAGE_KEY = "mfa_league_slug";
  let LEAGUES_INDEX = null;
  let currentLeagueSlug = null;

  function dataUrlForSlug(slug) {
    if (!slug) return "./data/latest_data.json";
    return `./data/leagues/${encodeURIComponent(slug)}/latest_data.json`;
  }

  function fillLeagueSelect(index, selectedSlug) {
    const sel = document.getElementById("league-select");
    if (!sel) return;
    const leagues = (index && index.leagues) || [];
    if (!leagues.length) {
      sel.innerHTML = `<option value="">Liga actual</option>`;
      return;
    }
    sel.innerHTML = leagues
      .map((L) => {
        const label = `${L.name || L.slug}${L.competition ? ` · ${L.competition}` : ""}`;
        const selAttr = L.slug === selectedSlug ? " selected" : "";
        return `<option value="${escapeHtml(L.slug)}"${selAttr}>${escapeHtml(label)}</option>`;
      })
      .join("");
  }

  function updateLeagueChrome(data) {
    const league = (data && data.league) || {};
    const sources = (data && data.sources) || {};
    const comp =
      league.competition || sources.competition || (LEAGUES_INDEX && LEAGUES_INDEX.leagues
        ? (LEAGUES_INDEX.leagues.find((L) => L.slug === currentLeagueSlug) || {}).competition
        : null);
    const label = document.getElementById("league-competition-label");
    if (label) label.textContent = comp || league.name || "Liga privada";
  }

  async function loadLeaguesIndex() {
    try {
      const res = await fetch("./data/leagues.json", { cache: "no-cache" });
      if (!res.ok) return null;
      return await res.json();
    } catch {
      return null;
    }
  }

  async function loadData(slugOverride, { bustCache = false } = {}) {
    const errEl = document.getElementById("load-error");
    try {
      if (!LEAGUES_INDEX) LEAGUES_INDEX = await loadLeaguesIndex();
      const defaultSlug =
        (LEAGUES_INDEX && LEAGUES_INDEX.default_slug) ||
        ((LEAGUES_INDEX && LEAGUES_INDEX.leagues) || []).find((L) => L.default)?.slug ||
        "laliga-patio";
      const stored = localStorage.getItem(LEAGUE_STORAGE_KEY);
      const slug =
        slugOverride ||
        stored ||
        defaultSlug;
      currentLeagueSlug = slug;
      fillLeagueSelect(LEAGUES_INDEX, slug);

      const bust = bustCache ? `?t=${Date.now()}` : "";
      let res = await fetch(`${dataUrlForSlug(slug)}${bust}`, { cache: "no-store" });
      if (!res.ok && slug) {
        res = await fetch(`./data/latest_data.json${bust}`, { cache: "no-store" });
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      DATA = await res.json();
      localStorage.setItem(LEAGUE_STORAGE_KEY, slug);
      updateLeagueChrome(DATA);
      errEl.classList.add("hidden");
      renderAll();
      return DATA;
    } catch (err) {
      console.error(err);
      errEl.textContent =
        "No se pudo cargar los datos de la liga. Sirve public/ por HTTP y ejecuta el data engine (--league all).";
      errEl.classList.remove("hidden");
      return null;
    }
  }

  let refreshInFlight = false;

  function setRefreshUi(state, message) {
    const wrap = document.querySelector(".update-chip-wrap");
    const btn = document.getElementById("btn-refresh");
    const status = document.getElementById("refresh-status");
    if (wrap) wrap.classList.toggle("is-refreshing", state === "busy");
    if (btn) {
      btn.disabled = state === "busy";
      btn.textContent = state === "busy" ? "Actualizando…" : "Actualizar";
    }
    if (status) {
      if (!message) {
        status.hidden = true;
        status.textContent = "";
        status.classList.remove("is-error", "is-ok");
      } else {
        status.hidden = false;
        status.textContent = message;
        status.classList.toggle("is-error", state === "error");
        status.classList.toggle("is-ok", state === "ok");
      }
    }
  }

  async function loadRefreshConfig() {
    try {
      const res = await fetch("./refresh-config.json", { cache: "no-cache" });
      if (!res.ok) return null;
      const cfg = await res.json();
      if (!cfg || !cfg.url) return null;
      return cfg;
    } catch {
      return null;
    }
  }

  async function pollUntilUpdated(prevGeneratedAt, { timeoutMs = 8 * 60 * 1000, intervalMs = 12000 } = {}) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      await new Promise((r) => setTimeout(r, intervalMs));
      const data = await loadData(currentLeagueSlug, { bustCache: true });
      const next = data && data.generated_at;
      if (next && String(next) !== String(prevGeneratedAt || "")) {
        return true;
      }
      const elapsedMin = Math.round((Date.now() - started) / 60000);
      setRefreshUi(
        "busy",
        elapsedMin > 0
          ? `Esperando snapshot… (~${elapsedMin} min)`
          : "Workflow en marcha…"
      );
    }
    return false;
  }

  async function refreshData() {
    if (refreshInFlight) return;
    refreshInFlight = true;
    const prev = DATA && DATA.generated_at;
    const league = currentLeagueSlug || "all";
    try {
      setRefreshUi("busy", "Disparando actualización…");
      const cfg = await loadRefreshConfig();
      if (!cfg || !cfg.url) {
        await loadData(league);
        setRefreshUi(
          "error",
          "Sin refresh-config.json. Copia el example y despliega el Worker (ver workers/refresh-proxy)."
        );
        return;
      }

      const res = await fetch(cfg.url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Refresh-Key": cfg.key || "",
        },
        body: JSON.stringify({ league }),
      });
      let payload = null;
      try {
        payload = await res.json();
      } catch {
        payload = null;
      }

      if (res.status === 429) {
        const sec = (payload && payload.retry_after_seconds) || 120;
        setRefreshUi("error", `Espera ${sec}s antes de volver a actualizar.`);
        return;
      }
      if (!res.ok || (payload && payload.ok === false)) {
        const err = (payload && payload.error) || `HTTP ${res.status}`;
        setRefreshUi("error", `No se pudo disparar: ${err}`);
        return;
      }

      setRefreshUi("busy", "Regenerando en la nube (2–6 min)…");
      const ok = await pollUntilUpdated(prev);
      if (ok) {
        setRefreshUi("ok", "Datos actualizados.");
        setTimeout(() => setRefreshUi("idle", ""), 5000);
      } else {
        setRefreshUi(
          "error",
          "Timeout: revisa Actions. Puedes pulsar Actualizar más tarde."
        );
      }
    } catch (err) {
      console.error(err);
      setRefreshUi("error", "Error de red al actualizar.");
    } finally {
      refreshInFlight = false;
      const btn = document.getElementById("btn-refresh");
      const wrap = document.querySelector(".update-chip-wrap");
      if (wrap) wrap.classList.remove("is-refreshing");
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Actualizar";
      }
    }
  }

  function initRefreshButton() {
    const btn = document.getElementById("btn-refresh");
    if (!btn) return;
    btn.addEventListener("click", () => refreshData());
  }

  function initLeagueSwitcher() {
    const sel = document.getElementById("league-select");
    if (!sel) return;
    sel.addEventListener("change", () => {
      const slug = sel.value;
      if (!slug || slug === currentLeagueSlug) return;
      loadData(slug);
    });
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
    document.querySelectorAll("[data-market-scope]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const scope = btn.getAttribute("data-market-scope");
        if (!scope || scope === marketScope) return;
        marketScope = scope;
        renderMarket();
      });
    });
    updateFiltersSummary();
  }

  document.addEventListener("DOMContentLoaded", () => {
    initTabs();
    initCollapses();
    initFilters();
    initLeagueSwitcher();
    initSortControls();
    initRefreshButton();
    initPwa();
    loadData();
  });
})();
