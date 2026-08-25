/*
 * InstantBoard Service Worker: offline caching for the app shell and static
 * assets. API and SSE requests are never intercepted.
 * Bump CACHE_VERSION to invalidate all previously cached content.
 */
const CACHE_VERSION = "v1";
const CACHE_NAME = `ib-static-${CACHE_VERSION}`;
const APP_SHELL = ["./", "./index.html"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key !== CACHE_NAME)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

const STATIC_DESTINATIONS = new Set(["script", "style", "image", "font"]);
const STATIC_EXTENSIONS =
  /\.(js|mjs|css|png|jpe?g|gif|svg|webp|avif|ico|woff2?|ttf|otf|eot)$/i;

function isStaticAsset(request, url) {
  if (STATIC_DESTINATIONS.has(request.destination)) return true;
  return STATIC_EXTENSIONS.test(url.pathname);
}

function isExcluded(request, url) {
  return (
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/stream") ||
    (request.headers.get("accept") || "").includes("text/event-stream")
  );
}

async function cacheFirstWithBackfill(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  if (cached) {
    backfill(cache, request);
    return cached;
  }
  const response = await fetch(request);
  if (response.ok && response.type === "basic") {
    cache.put(request, response.clone());
  }
  return response;
}

function backfill(cache, request) {
  fetch(request)
    .then((response) => {
      if (response.ok && response.type === "basic") {
        return cache.put(request, response);
      }
      return undefined;
    })
    .catch(() => {
      /* offline: keep serving the cached copy */
    });
}

async function networkFirstNavigation(request) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const response = await fetch(request);
    if (response.ok && response.type === "basic") {
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    const cached =
      (await cache.match(request)) || (await cache.match("./index.html"));
    return cached || Response.error();
  }
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);

  // Never intercept API or SSE traffic; let it pass through to the network.
  if (isExcluded(request, url)) return;

  // Only handle same-origin requests; everything else goes to the network.
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(networkFirstNavigation(request));
    return;
  }

  if (isStaticAsset(request, url)) {
    event.respondWith(cacheFirstWithBackfill(request));
  }
});
