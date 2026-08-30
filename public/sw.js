/**
 * Service Worker — Mister Fantasy Advisor
 * - Cache-first para el shell de la app (HTML/CSS/JS/manifest/iconos)
 * - Network-first (con fallback a cache) para data/latest_data.json
 */

const CACHE_VERSION = "mfa-v63";
const SHELL_CACHE = `${CACHE_VERSION}-shell`;
const DATA_CACHE = `${CACHE_VERSION}-data`;

const SHELL_ASSETS = [
  "./",
  "./index.html",
  "./app.js",
  "./styles.css",
  "./manifest.webmanifest",
  "./icons/favicon.svg",
  "./icons/icon-32.png",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k.startsWith("mfa-") && k !== SHELL_CACHE && k !== DATA_CACHE)
          .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

function isDataRequest(url) {
  const p = url.pathname;
  return (
    p.endsWith("/data/latest_data.json") ||
    p.endsWith("latest_data.json") ||
    p.endsWith("/data/leagues.json") ||
    p.endsWith("leagues.json") ||
    /\/data\/leagues\/[^/]+\/latest_data\.json$/.test(p)
  );
}

function isShellCodeRequest(url) {
  const p = url.pathname;
  return p.endsWith("/app.js") || p.endsWith("/styles.css") || p.endsWith("/sw.js");
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // Solo misma origen
  if (url.origin !== self.location.origin) return;

  if (isDataRequest(url) || isShellCodeRequest(url)) {
    // Network-first: datos y código de UI (evita hedges/roles nuevos pegados a JS viejo)
    event.respondWith(
      caches.open(isDataRequest(url) ? DATA_CACHE : SHELL_CACHE).then(async (cache) => {
        try {
          const fresh = await fetch(req);
          if (fresh && fresh.ok) {
            cache.put(req, fresh.clone());
          }
          return fresh;
        } catch {
          const cached = await cache.match(req);
          if (cached) return cached;
          if (isDataRequest(url)) {
            const alt = await cache.match("./data/latest_data.json");
            if (alt) return alt;
          }
          throw new Error("Sin red ni cache");
        }
      })
    );
    return;
  }

  // Cache-first para shell estático (html/iconos/manifest)
  event.respondWith(
    caches.match(req).then((cached) => {
      if (cached) return cached;
      return fetch(req).then((res) => {
        if (res && res.ok && req.url.startsWith(self.location.origin)) {
          const copy = res.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(req, copy));
        }
        return res;
      }).catch(() => caches.match("./index.html"));
    })
  );
});
