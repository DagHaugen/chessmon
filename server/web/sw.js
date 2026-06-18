const C = 'chessmon-clock-v8';
const SHELL = ['clock.html', 'manifest.webmanifest', 'icon.svg', 'qrcode.min.js'];

self.addEventListener('install', e =>
  e.waitUntil(caches.open(C).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())));

self.addEventListener('activate', e => e.waitUntil(
  caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== C).map(k => caches.delete(k))))
    .then(() => self.clients.claim())));

self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (u.pathname.endsWith('/ws') || u.pathname.startsWith('/tables')) return;  // never cache live data
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
