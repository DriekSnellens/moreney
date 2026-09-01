"""PWA shell: manifest, service worker, icons for mobile home screen."""

from __future__ import annotations

MANIFEST_JSON = """{
  "name": "Moreney Live",
  "short_name": "Moreney",
  "description": "Live portfolio, PnL and micro-trading status",
  "start_url": "/live/dashboard",
  "scope": "/",
  "display": "standalone",
  "orientation": "portrait-primary",
  "background_color": "#0c1118",
  "theme_color": "#0c1118",
  "categories": ["finance", "utilities"],
  "icons": [
    {
      "src": "/live/icon.svg",
      "sizes": "any",
      "type": "image/svg+xml",
      "purpose": "any"
    },
    {
      "src": "/live/icon.svg",
      "sizes": "512x512",
      "type": "image/svg+xml",
      "purpose": "maskable"
    }
  ]
}
"""

SERVICE_WORKER_JS = """const CACHE = 'moreney-dash-v1';
const SHELL = ['/live/dashboard', '/live/icon.svg', '/live/manifest.webmanifest'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET') return;
  if (url.pathname.startsWith('/live/dashboard/history')) {
    event.respondWith(
      fetch(event.request).catch(() => caches.match(event.request))
    );
    return;
  }
  if (url.pathname === '/live/dashboard' || url.pathname === '/') {
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(event.request, copy));
          return res;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }
});
"""

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img">
  <defs>
    <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#1a2332"/>
      <stop offset="100%" stop-color="#0c1118"/>
    </linearGradient>
  </defs>
  <rect width="512" height="512" rx="108" fill="url(#g)"/>
  <path d="M96 344 L160 216 L224 280 L288 184 L352 248 L416 168" fill="none" stroke="#3ddc97" stroke-width="28" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="160" cy="216" r="18" fill="#3ddc97"/>
  <circle cx="288" cy="184" r="18" fill="#78a0dc"/>
  <text x="256" y="420" text-anchor="middle" fill="#f3f6fa" font-family="system-ui,sans-serif" font-size="72" font-weight="600">M</text>
</svg>
"""
