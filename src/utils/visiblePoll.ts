/**
 * 可见性感知的轮询定时器：后台标签页 / 移动端熄屏时跳过 tick，回到前台后下一周期自动恢复。
 * 用于终端内各数据轮询（行情兜底/宏观/文章/自选/私信等），减少后台空转请求与服务端负载。
 * SSE 长连接本身由浏览器挂起，这里兜住的是纯 setInterval 轮询。
 */
export function visiblePoll(fn: () => void, intervalMs: number): number {
  return window.setInterval(() => {
    if (document.visibilityState !== 'hidden') fn();
  }, intervalMs);
}
