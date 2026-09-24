// Retire the old login homepage cache after switching the root to public knowledge.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((key) => key.startsWith("rsc-personal-warehouse-shell-")).map((key) => caches.delete(key)));
    await self.clients.claim();
  })());
});
// No fetch handler: public navigation always uses the current static site.
