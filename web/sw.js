// 由 build.py 自动生成，请勿手改

/* 生猪看板 Service Worker
   目标：已「添加到主屏幕」的 iPhone 在 Mac 关机 / 断网时，仍能打开最后一次同步的看板。
   策略：导航 network-first（在线永远拿最新），失败回落缓存；静态资源 stale-while-revalidate。

   ⚠ 全部使用**相对路径**：本站点可能部署在子路径下（如 GitHub Pages 的 /pigboard/），
     若写成 '/xxx' 会解析到域名根，导致预缓存全部 404。 */
const VERSION = 'v-20261110-e43014-9b05f9';
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
    const oldKeys = keys.filter(function (k) { return k !== VERSION; });
    await Promise.all(oldKeys.map(function (k) { return caches.delete(k); }));
    await self.clients.claim();
    // 从旧版本升级时强制刷新已打开的页面，避免“新 SW 已装好、页面还是旧缓存”
    if (oldKeys.length) {
      const cs = await self.clients.matchAll({ type: 'window' });
      for (const c of cs) {
        try { c.navigate(c.url); } catch (_) {}
      }
    }
  })());
});

self.addEventListener('fetch', function (e) {
  const req = e.request;
  if (req.method !== 'GET') return;
  let url;
  try { url = new URL(req.url); } catch (_) { return; }
  if (url.origin !== location.origin) return;
  // 一键更新接口不缓存、不拦截，始终直连本地服务
  if (url.pathname.indexOf('/api/') !== -1) return;

  // 心跳必须走网络：否则页面会拿旧缓存判断“已是最新”，永远发现不了新版本。
  // 网络不可用时才回落到最后一次缓存。
  if (/\/data\/heartbeat\.json$/.test(url.pathname)) {
    e.respondWith((async function () {
      try {
        return await fetch(req, { cache: 'no-store' });
      } catch (_) {
        const hit = await caches.match(req, { ignoreSearch: true });
        return hit || new Response('', { status: 504 });
      }
    })());
    return;
  }

  if (req.mode === 'navigate') {
    const APP_ROOT = new URL('./', self.registration.scope).href;
    // 8 秒只是“先给缓存”的阈值；网络请求不会取消，完成后仍会写入缓存并通知页面。
    // 原来的 3 秒阈值 + 后台静默刷新在 iOS 上经常来不及完成，导致页面永远停在旧缓存。
    const NET_TIMEOUT = 8000;

    e.respondWith((async function () {
      const cacheNav = async function (r) {
        try {
          const c = await caches.open(VERSION);
          // 同时按"本次请求 URL"和"应用根"保存，兼容子路径与根路径部署
          try { await c.put(req, r.clone()); } catch (_) {}
          try { await c.put(APP_ROOT, r.clone()); } catch (_) {}
        } catch (_) {}
      };
      const notify = function () {
        self.clients.matchAll({ type: 'window' }).then(function (cs) {
          cs.forEach(function (c) {
            try { c.postMessage({ type: 'pb-updated' }); } catch (_) {}
          });
        }).catch(function () {});
      };

      const network = fetch(req, { cache: 'no-store' }).then(function (r) {
        if (r && r.ok) {
          return cacheNav(r).then(function () { notify(); return r; });
        }
        return r;
      }).catch(function () { return null; });

      let timer;
      const timeout = new Promise(function (resolve) {
        timer = setTimeout(function () { resolve(null); }, NET_TIMEOUT);
      });
      const first = await Promise.race([network, timeout]);
      clearTimeout(timer);
      if (first) return first;

      // 超时先给缓存时，用 waitUntil 保住后台网络请求，确保它能写完缓存
      try { e.waitUntil(network.then(function () {}).catch(function () {})); } catch (_) {}

      const hit = (await caches.match(req)) || (await caches.match(APP_ROOT));
      if (hit) return hit;   // 后台的 network 仍会完成、写缓存并通知页面刷新

      const late = await network;
      if (late) return late;
      return new Response(
        '<!DOCTYPE html><meta charset="utf-8"><title>离线</title>' +
        '<body style="font:16px/1.7 -apple-system,sans-serif;padding:40px;color:#333">' +
        '<h2>暂时无法连接</h2><p>尚未缓存任何看板内容。请先在有网络时打开一次。</p></body>',
        { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
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

// 页面可以主动要求新 SW 立即接管 / 清空缓存（用于“获取最新版”与陈旧页面自愈）
self.addEventListener('message', function (e) {
  const d = e.data || {};
  if (d.type === 'SKIP_WAITING') self.skipWaiting();
  if (d.type === 'CLEAR_CACHES') {
    e.waitUntil(caches.keys().then(function (ks) {
      return Promise.all(ks.map(function (k) { return caches.delete(k); }));
    }));
  }
});
