/**
 * Proxy: PWA → repository_dispatch (refresh-data) en GitHub Actions.
 *
 * Secrets (wrangler secret put):
 *   GITHUB_TOKEN  — fine-grained PAT, Actions: write en el repo
 *   REFRESH_KEY   — clave compartida con public/refresh-config.json
 *   GITHUB_REPO   — opcional; default elexrallen/Mister (o var en wrangler.toml)
 *
 * Rate limit: 1 disparo / 2 min por IP.
 *
 * Cron (wrangler.toml): cada 2 horas, minuto 15 UTC, llama a scheduled()
 * y encola el mismo refresh-data sin pasar por el botón de la PWA.
 */

const RATE_WINDOW_MS = 2 * 60 * 1000;

/** @type {Map<string, number>} */
const lastByIp = new Map();

function corsHeaders(origin) {
  const allow =
    !origin ||
    origin.includes("github.io") ||
    origin.includes("localhost") ||
    origin.includes("127.0.0.1");
  return {
    "Access-Control-Allow-Origin": allow ? origin || "*" : "null",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-Refresh-Key",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
}

function json(body, status, origin) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      ...corsHeaders(origin),
    },
  });
}

function clientIp(request) {
  return (
    request.headers.get("CF-Connecting-IP") ||
    request.headers.get("X-Forwarded-For")?.split(",")[0]?.trim() ||
    "unknown"
  );
}

function flipReleaseIds(raw) {
  if (!Array.isArray(raw)) return null;
  const ids = [];
  const seen = new Set();
  for (const item of raw) {
    const id = String(item ?? "").trim();
    if (!/^[A-Za-z0-9_-]{1,32}$/.test(id) || seen.has(id)) continue;
    seen.add(id);
    ids.push(id);
    if (ids.length >= 30) break;
  }
  return ids.length ? ids : null;
}

async function dispatchRefresh(env, league, flipMode, flipRelease) {
  const repo = (env.GITHUB_REPO || "elexrallen/Mister").replace(/^\/+|\/+$/g, "");
  const client_payload = { league };
  if (flipMode !== null) client_payload.flip_mode = flipMode;
  if (flipRelease) client_payload.flip_release = flipRelease;
  return fetch(`https://api.github.com/repos/${repo}/dispatches`, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
      "User-Agent": "mister-refresh-proxy",
    },
    body: JSON.stringify({
      event_type: "refresh-data",
      client_payload,
    }),
  });
}

export default {
  async scheduled(_event, env, ctx) {
    if (!env.GITHUB_TOKEN) {
      console.log("scheduled refresh skipped: no GITHUB_TOKEN");
      return;
    }
    ctx.waitUntil(
      dispatchRefresh(env, "all", null, null).then(async (gh) => {
        if (!gh.ok) {
          const text = await gh.text();
          console.log(`scheduled dispatch failed ${gh.status} ${text.slice(0, 300)}`);
          return;
        }
        console.log("scheduled refresh-data dispatched");
      })
    );
  },

  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }

    if (request.method !== "POST") {
      return json({ ok: false, error: "method_not_allowed" }, 405, origin);
    }

    const key = request.headers.get("X-Refresh-Key") || "";
    if (!env.REFRESH_KEY || key !== env.REFRESH_KEY) {
      return json({ ok: false, error: "unauthorized" }, 401, origin);
    }

    if (!env.GITHUB_TOKEN) {
      return json({ ok: false, error: "server_misconfigured" }, 500, origin);
    }

    const ip = clientIp(request);
    const now = Date.now();
    const prev = lastByIp.get(ip) || 0;
    if (now - prev < RATE_WINDOW_MS) {
      const retrySec = Math.ceil((RATE_WINDOW_MS - (now - prev)) / 1000);
      return json(
        { ok: false, error: "rate_limited", retry_after_seconds: retrySec },
        429,
        origin
      );
    }

    let league = "all";
    let flipMode = null;
    let flipRelease = null;
    try {
      const body = await request.json();
      if (body && typeof body.league === "string" && body.league.trim()) {
        league = body.league.trim().toLowerCase();
      }
      if (body && typeof body.flip_mode === "boolean") {
        flipMode = body.flip_mode;
      }
      if (body && Array.isArray(body.flip_release)) {
        flipRelease = flipReleaseIds(body.flip_release);
      }
    } catch {
      // body vacío → all
    }

    if (!/^(all|[a-z0-9-]+)$/.test(league)) {
      league = "all";
    }

    const gh = await dispatchRefresh(env, league, flipMode, flipRelease);

    if (!gh.ok) {
      const text = await gh.text();
      return json(
        {
          ok: false,
          error: "github_dispatch_failed",
          status: gh.status,
          detail: text.slice(0, 300),
        },
        502,
        origin
      );
    }

    lastByIp.set(ip, now);
    // Evitar crecimiento infinito del Map
    if (lastByIp.size > 500) {
      const cutoff = now - RATE_WINDOW_MS * 2;
      for (const [k, t] of lastByIp) {
        if (t < cutoff) lastByIp.delete(k);
      }
    }

    return json(
      {
        ok: true,
        dispatched: true,
        league,
        flip_mode: flipMode,
        flip_release: flipRelease,
        accepted_at: new Date(now).toISOString(),
        recommended_poll_seconds: 15,
        expected_duration_minutes: 12,
        message: "Workflow encolado en GitHub Actions. La app comprobara cambios durante unos minutos.",
      },
      202,
      origin
    );
  },
};
