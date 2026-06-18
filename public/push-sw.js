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
    data: { url: data.url || '/' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ('focus' in client) {
          client.focus();
          if (url && url !== '/' && 'navigate' in client) {
            client.navigate(url);
          }
          return undefined;
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(url);
      }
      return undefined;
    })
  );
});

// 立即接管，无需等到下次加载；不缓存任何资源。
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
