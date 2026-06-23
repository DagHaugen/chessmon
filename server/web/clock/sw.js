// Self-contained offline cache for the standalone clock. Lives in its own directory so its scope
// (e.g. /app/clock/ or /clock/) never collides with the main chessmon app's service worker.
const C = 'chessmon-soloclock-v1';
const SHELL = ['./', 'index.html', 'manifest.webmanifest', 'icon.svg', 'icon-maskable.svg'];

self.addEventListener('install', e =>
  e.waitUntil(caches.open(C)
    .then(c => c.addAll(SHELL.map(u => new Request(u, { cache: 'reload' }))))
    .then(() => self.skipWaiting())));

self.addEventListener('activate', e => e.waitUntil(
  caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== C).map(k => caches.delete(k))))
    .then(() => self.clients.claim())));

self.addEventListener('fetch', e => {                 // cache-first: the clock is fully static, so this makes it work offline
  if (e.request.method !== 'GET') return;
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
