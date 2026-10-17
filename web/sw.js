// 由 build.py 自动生成，请勿手改

/* 生猪看板 Service Worker —— 让已「添加到主屏幕」的 iPhone 在 Mac 关机/断网时
   仍能打开最后一次成功同步的看板。
   策略：导航请求 network-first（在线永远拿最新），失败回落缓存；
        静态资源 stale-while-revalidate。 */
const VERSION = 'v-20261017-9f3ca1';
const CORE = ['/', '/manifest.webmanifest', '/icons/apple-touch-icon.png',
              '/icons/icon-192.png', '/icons/icon-512.png'];

self.addEventListener('install', function (e) {
  self.skipWaiting();
  e.waitUntil(caches.open(VERSION).then(function (c) {
    return Promise.all(CORE.map(function (u) {
      return c.add(new Request(u, { cache: 'reload' })).catch(function () {});
    }));
  }));
});

self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== VERSION; })
                          .map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener('fetch', function (e) {
  const req = e.request;
  if (req.method !== 'GET') return;
  let url;
  try { url = new URL(req.url); } catch (_) { return; }
  if (url.origin !== location.origin) return;

  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req).then(function (r) {
        const cp = r.clone();
        caches.open(VERSION).then(function (c) { c.put('/', cp); });
        return r;
      }).catch(function () {
        return caches.match('/').then(function (r) {
          return r || new Response('<h1>离线</h1><p>尚无缓存，请先连接一次。</p>',
            { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
        });
      })
    );
    return;
  }

  e.respondWith(caches.match(req).then(function (hit) {
    const net = fetch(req).then(function (r) {
      if (r && r.ok) {
        const cp = r.clone();
        caches.open(VERSION).then(function (c) { c.put(req, cp); });
      }
      return r;
    }).catch(function () { return hit; });
    return hit || net;
  }));
});
