const CACHE = 'moealturej-static-v4';
const CORE_ASSETS = [
  '/static/logo.png',
  '/static/default.jpg',
  '/static/site.webmanifest',
  '/static/css/core.bundle.css?v=20260827',
  '/static/css/base-pre.css?v=20260827-full1',
  '/static/css/base-post.css?v=20260827-full1',
  '/static/css/refinements.bundle.css?v=20260827',
  '/static/css/site-upgrade.css?v=20260827-full1',
  '/static/js/site-core.js?v=20260827-full1',
  '/static/js/premium-v3.js?v=20260827'
];

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    await Promise.all(CORE_ASSETS.map(async url => {
      try {
        const response = await fetch(url, { cache: 'reload' });
        if (response.ok) await cache.put(url, response);
      } catch (_) {
        // One optional asset should never prevent the service worker installing.
      }
    }));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(key => key.startsWith('moealturej-') && key !== CACHE).map(key => caches.delete(key)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Never cache HTML, account data, checkout, API responses, or casino state.
  if (!url.pathname.startsWith('/static/')) return;

  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const cached = await cache.match(request);
    const network = fetch(request).then(response => {
      if (response.ok && (response.type === 'basic' || response.type === 'cors')) {
        cache.put(request, response.clone()).catch(() => {});
      }
      return response;
    });
    return cached || network;
  })());
});
