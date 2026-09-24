const CACHE = "rsc-personal-warehouse-private-shell-v4";
const SHELL = [
  "/xx/",
  "/xx/manifest.webmanifest",
  "/brand/rsc-personal-warehouse-blue-64.png",
  "/brand/rsc-personal-warehouse-blue-192.png",
  "/brand/rsc-personal-warehouse-blue.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((key) => key.startsWith("rsc-personal-warehouse-private-shell-") && key !== CACHE).map((key) => caches.delete(key)))),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin
      || !url.pathname.startsWith("/xx/") || url.pathname.startsWith("/xx/api/")) return;
  event.respondWith(fetch(event.request).catch(async (error) => {
    const cache = await caches.open(CACHE);
    const cached = await cache.match(event.request);
    if (cached) return cached;
    if (event.request.mode === "navigate") {
      const shell = await cache.match("/xx/");
      if (shell) return shell;
    }
    throw error;
  }));
});
