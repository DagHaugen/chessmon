// Self-contained offline cache for the standalone clock. Its own directory keeps the scope
// (/app/clock/ or /clock/) from colliding with the main chessmon app's service worker.
const C = 'chessmon-soloclock-v2';
const SHELL = ['./', 'index.html', 'manifest.webmanifest', 'icon.svg', 'icon-maskable.svg'];

self.addEventListener('install', e =>
  e.waitUntil(caches.open(C)
    .then(c => c.addAll(SHELL.map(u => new Request(u, { cache: 'reload' }))))
    .then(() => self.skipWaiting())));

self.addEventListener('activate', e => e.waitUntil(
  caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== C).map(k => caches.delete(k))))
    .then(() => self.clients.claim())));

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  if (e.request.mode === 'navigate') {            // network-first for the page, so a new version shows up the next time you're online...
    e.respondWith(
      fetch(e.request)
        .then(r => { const copy = r.clone(); caches.open(C).then(c => c.put(e.request, copy)); return r; })
        .catch(() => caches.match(e.request).then(r => r || caches.match('./'))));   // ...with the cache as the offline fallback
    return;
  }
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));   // cache-first for the static assets
});
