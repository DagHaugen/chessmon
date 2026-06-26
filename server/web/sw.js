const C = 'chessmon-clock-v97';
const PIECES = ['wK', 'wQ', 'wR', 'wB', 'wN', 'wP', 'bK', 'bQ', 'bR', 'bB', 'bN', 'bP']
  .map(p => 'pieces/' + p + '.svg');
const SHELL = ['clock.html', 'manifest.webmanifest', 'icon.svg', 'icon-maskable.svg', 'qrcode.min.js', ...PIECES];

self.addEventListener('install', e =>                       // {cache:'reload'} bypasses the HTTP cache so a version bump re-fetches CHANGED shell files (e.g. a new icon), not the stale cached copy
  e.waitUntil(caches.open(C).then(c => c.addAll(SHELL.map(u => new Request(u, { cache: 'reload' })))).then(() => self.skipWaiting())));

self.addEventListener('activate', e => e.waitUntil(
  caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== C).map(k => caches.delete(k))))
    .then(() => self.clients.claim())));

self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (u.origin !== location.origin) return;                                    // cross-origin -> the browser
  if (u.pathname.endsWith('/ws') || u.pathname.startsWith('/tables')) return;  // live data -> the browser
  const path = u.pathname.replace(/^\/app\//, '');                             // shell entries are stored relative to /app/
  if (!SHELL.includes(path)) return;            // only the cached clock shell is SW-served; the landing, camera, console
                                                // and API go to the network natively, so a cert/offline failure shows the
                                                // browser's own message instead of a cryptic FetchEvent.respondWith error
  e.respondWith(
    caches.match(e.request).then(r => r || fetch(e.request).catch(() =>
      e.request.mode === 'navigate' ? caches.match('clock.html') : Response.error()))
  );
});
