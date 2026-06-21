const C = 'chessmon-clock-v68';
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
  if (u.pathname.endsWith('/ws') || u.pathname.startsWith('/tables')) return;  // never cache live data
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
