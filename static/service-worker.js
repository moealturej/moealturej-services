const CACHE = 'moealturej-static-v5';

// Do not prefetch the whole site during installation. The previous worker
// forced a network reload of every core asset on first visit, duplicating
// bandwidth that the page had just spent. Assets are cached only when used.
self.addEventListener('install', event => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(
      keys
        .filter(key => key.startsWith('moealturej-') && key !== CACHE)
        .map(key => caches.delete(key))
    );
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin || !url.pathname.startsWith('/static/')) return;

  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const cached = await cache.match(request);

    // True cache-first: when an asset is cached, do not also start a background
    // network fetch. Versioned URLs and one-year immutable server caching make
    // this safe while eliminating repeat Render HTTP responses.
    if (cached) return cached;

    const response = await fetch(request);
    if (response.ok && (response.type === 'basic' || response.type === 'cors')) {
      cache.put(request, response.clone()).catch(() => {});
    }
    return response;
  })());
});
