/**
 * Proxy: PWA → repository_dispatch (refresh-data) en GitHub Actions.
 *
 * Secrets (wrangler secret put):
 *   GITHUB_TOKEN  — fine-grained PAT, Actions: write en el repo
 *   REFRESH_KEY   — clave compartida con public/refresh-config.json
 *   GITHUB_REPO   — opcional; default elexrallen/Mister (o var en wrangler.toml)
 *
 * Rate limit: 1 disparo / 2 min por IP.
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

export default {
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
    try {
      const body = await request.json();
      if (body && typeof body.league === "string" && body.league.trim()) {
        league = body.league.trim().toLowerCase();
      }
    } catch {
      // body vacío → all
    }

    if (!["all", "laliga-patio", "premier"].includes(league)) {
      league = "all";
    }

    const repo = (env.GITHUB_REPO || "elexrallen/Mister").replace(/^\/+|\/+$/g, "");
    const gh = await fetch(`https://api.github.com/repos/${repo}/dispatches`, {
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
        client_payload: { league },
      }),
    });

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
        message: "Workflow refresh-data disparado. Espera 2–6 min.",
      },
      202,
      origin
    );
  },
};
