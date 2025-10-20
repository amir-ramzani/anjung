/* Anjung service worker: lets the app be installed and opens fast, without ever serving stale data.
 *  - Pages: always from the network; offline, a small "you are offline" page.
 *  - /assets/*: built files with a hash in the name, never change, so cached after first use.
 *  - Icons, manifest, offline page: cached at install.
 *  - Never cached: the API (/v1/), dev endpoints, documents (/anjung-docs/, presigned) and anything not GET.
 * Bump VERSION to drop old caches. */
const VERSION = "anjung-v1";
const SHELL = ["/offline.html", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (/^\/(v1|dev|anjung-docs)\//.test(url.pathname)) return; // live data and private files: the browser handles them

  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match("/offline.html")));
    return;
  }
  if (url.pathname.startsWith("/assets/") || SHELL.includes(url.pathname) || url.pathname.startsWith("/icons/")) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(VERSION).then((c) => c.put(req, copy)); }
        return res;
      })),
    );
  }
});
