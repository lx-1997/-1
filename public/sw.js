// DeepFocus sw.js —— 自毁开关 (self-destroying service worker)
//
// 取代历史遗留的 cache-first 版本：那个版本会把 '/'（app shell）缓存下来并对所有请求
// cache-first 拦截，导致老用户被永久钉在旧 app shell 上 → 白屏；更糟的是它拦截导航，
// 让带「注销 SW」修复逻辑的新 index.html 永远加载不出来，用户无法自愈。
//
// 本版本不缓存、不拦截任何请求：安装即接管 → 清空全部 caches → 注销自己。
// 注意：故意【不】主动 navigate/reload —— 早期版本在这里强制重载，与旧 cache-first SW
// 残留打架导致页面反复自动刷新。去掉后：注销 + 清缓存即完成，用户下次手动刷新就是干净页面。
self.addEventListener('install', function () {
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    (async function () {
      try {
        var keys = await caches.keys();
        await Promise.all(keys.map(function (k) { return caches.delete(k); }));
      } catch (e) { /* ignore */ }
      try {
        await self.registration.unregister();
      } catch (e) { /* ignore */ }
    })()
  );
});
