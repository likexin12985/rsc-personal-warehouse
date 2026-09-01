const CACHE = "rsc-personal-warehouse-shell-v2";
const SHELL = [
  "/",
  "/manifest.webmanifest",
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
    caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET" || event.request.url.includes("/api/")) return;
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request).then((r) => r || caches.match("/"))));
});
