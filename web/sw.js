// 由 build.py 自动生成，请勿手改

/* 生猪看板 Service Worker
   目标：已「添加到主屏幕」的 iPhone 在 Mac 关机 / 断网时，仍能打开最后一次同步的看板。
   策略：导航 network-first（在线永远拿最新），失败回落缓存；静态资源 stale-while-revalidate。

   ⚠ 全部使用**相对路径**：本站点可能部署在子路径下（如 GitHub Pages 的 /pigboard/），
     若写成 '/xxx' 会解析到域名根，导致预缓存全部 404。 */
const VERSION = 'v-20261023-79f344';
const CORE = [
  './',
  './manifest.webmanifest',
  './icons/apple-touch-icon.png',
  './icons/icon-192.png',
  './icons/icon-512.png'
];

self.addEventListener('install', function (e) {
  self.skipWaiting();
  e.waitUntil((async function () {
    const c = await caches.open(VERSION);
    for (const u of CORE) {
      try {
        await c.add(new Request(u, { cache: 'reload' }));
      } catch (_) {
        try { await c.add(u); } catch (__) {}
      }
    }
  })());
});

self.addEventListener('activate', function (e) {
  e.waitUntil((async function () {
    const keys = await caches.keys();
    await Promise.all(keys.filter(function (k) { return k !== VERSION; })
                          .map(function (k) { return caches.delete(k); }));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', function (e) {
  const req = e.request;
  if (req.method !== 'GET') return;
  let url;
  try { url = new URL(req.url); } catch (_) { return; }
  if (url.origin !== location.origin) return;

  if (req.mode === 'navigate') {
    const APP_ROOT = new URL('./', self.registration.scope).href;
    const NET_TIMEOUT = 3000;  // 网络慢时先用缓存秒开，后台再更新

    e.respondWith((async function () {
      const cacheNav = async function (r) {
        try {
          const c = await caches.open(VERSION);
          // 同时按"本次请求 URL"和"应用根"保存，兼容子路径与根路径部署
          try { await c.put(req, r.clone()); } catch (_) {}
          try { await c.put(APP_ROOT, r.clone()); } catch (_) {}
        } catch (_) {}
      };

      let timer;
      try {
        const net = await Promise.race([
          fetch(req),
          new Promise(function (_, rej) {
            timer = setTimeout(function () { rej(new Error('net-timeout')); }, NET_TIMEOUT);
          })
        ]);
        clearTimeout(timer);
        if (net && net.ok) cacheNav(net);
        return net;
      } catch (_) {
        clearTimeout(timer);
        const hit = (await caches.match(req)) || (await caches.match(APP_ROOT));
        if (hit) {
          // 命中缓存：立即返回，同时后台静默更新
          fetch(req).then(cacheNav).catch(function () {});
          return hit;
        }
        try {
          const net = await fetch(req);
          if (net && net.ok) cacheNav(net);
          return net;
        } catch (__) { /* 落到离线页 */ }
        return new Response(
          '<!DOCTYPE html><meta charset="utf-8"><title>离线</title>' +
          '<body style="font:16px/1.7 -apple-system,sans-serif;padding:40px;color:#333">' +
          '<h2>暂时无法连接</h2><p>尚未缓存任何看板内容。请先在有网络时打开一次。</p></body>',
          { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
      }
    })());
    return;
  }

  e.respondWith((async function () {
    // 带查询串的请求（如心跳 ?_=时间戳）不写入缓存，否则每次访问都会新增一条，无限膨胀
    const cacheable = !url.search;
    const hit = await caches.match(req, { ignoreSearch: true });
    if (hit) {
      // 后台更新，不阻塞返回
      fetch(req).then(async function (r) {
        if (r && r.ok && cacheable) {
          const c = await caches.open(VERSION);
          c.put(req, r.clone()).catch(function () {});
        }
      }).catch(function () {});
      return hit;
    }
    try {
      const r = await fetch(req);
      if (r && r.ok && cacheable) {
        const c = await caches.open(VERSION);
        c.put(req, r.clone()).catch(function () {});
      }
      return r;
    } catch (_) {
      return new Response('', { status: 504 });
    }
  })());
});
