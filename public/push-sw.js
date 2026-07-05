/*
 * DeepFocus 离线信号召回 Service Worker（push-only）。
 * ⚠️ 刻意不做任何缓存 / fetch 拦截——只处理 Web Push，避免旧 cache-first sw.js 的陈旧内容问题。
 * 用户离开页面（甚至关掉浏览器）后，盯的信号触发时也能把他叫回来——这是免费用户唯一的离线召回通道。
 */
self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: 'DeepFocus 信号', body: event.data ? event.data.text() : '' };
  }
  const title = data.title || 'DeepFocus 信号';
  const options = {
    body: data.body || '你盯的信号触发了，点开看详情。',
    icon: '/logo192.png',
    badge: '/logo192.png',
    tag: data.tag || 'dfx-recall',
    renotify: true,
    // url=App 深链(点击后导航/落地的真实页面)；track=可追踪点击端点(SW fetch 命中即记 CTR，绝不导航到它)。
    data: { url: data.url || '/', track: data.track || '' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const url = data.url || '/';      // App 深链：聚焦/新开后真正落地的页面
  const track = data.track || '';   // 可追踪点击端点：命中即记录回流(CTR)，但绝不导航到它

  // 点击回流信标：fire-and-forget 命中可追踪端点即记 CTR。
  // redirect:'manual' → 服务端在响应前已记录点击，无需跟随 302 再下载一遍 App；
  // keepalive → SW 被回收也能送达；失败一律吞掉，绝不阻断把用户带回 App。
  const beacon = track
    ? fetch(track, {
        method: 'GET',
        keepalive: true,
        cache: 'no-store',
        credentials: 'omit',
        redirect: 'manual',
      }).catch(() => undefined)
    : Promise.resolve();

  const route = self.clients
    .matchAll({ type: 'window', includeUncontrolled: true })
    .then((clients) => {
      for (const client of clients) {
        if ('focus' in client) {
          // 已有打开的标签页：聚焦并导航到 App 深链(而非可追踪端点，避免把标签页停在 /api/.../click 上)。
          client.focus();
          if (url && url !== '/' && 'navigate' in client) {
            return client.navigate(url).catch(() => undefined);
          }
          return undefined;
        }
      }
      // 无打开标签页：直接开到 App 深链(不再经 302 中转)。
      if (self.clients.openWindow) {
        return self.clients.openWindow(url);
      }
      return undefined;
    });

  event.waitUntil(Promise.all([beacon, route]));
});

// 立即接管，无需等到下次加载；不缓存任何资源。
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
