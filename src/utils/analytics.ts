/**
 * 全站行为采集：统一上报到后端 /api/activity（登录用户精确到账号，匿名按本地会话id+IP归并）。
 * 失败静默——埋点绝不影响用户体验。供 App 视图切换与各中心关键动作复用。
 */
import { apiPost } from '../services/apiClient';

function sessionId(): string {
  try {
    let sess = localStorage.getItem('df_sess') || '';
    if (!sess) {
      sess = Math.random().toString(36).slice(2) + Date.now().toString(36);
      localStorage.setItem('df_sess', sess);
    }
    return sess;
  } catch {
    return ''; // 隐私模式无 localStorage，后端按 IP 归并
  }
}

/** 记一条操作流水。action 须在后端 _ACTIVITY_ACTIONS 白名单内（tab/search/open_report 等）。 */
export function track(action: string, target?: string): void {
  apiPost('/api/activity', {
    action,
    target: (target || '').slice(0, 180),
    session: sessionId(),
  }).catch(() => { /* 埋点失败忽略 */ });
}

let pageviewSent = false;

/** 页面访问打点：进程内只发一次（计数器 + 一条 pageview 流水，target 带 referrer/utm 来源便于归因）。 */
export function trackPageview(): void {
  if (pageviewSent) return;
  pageviewSent = true;
  apiPost('/api/metrics/pageview').catch(() => { /* 忽略 */ });
  let src = '';
  try {
    const q = new URLSearchParams(window.location.search);
    const utm = ['utm_source', 'utm_medium', 'utm_campaign'].map(k => q.get(k)).filter(Boolean).join('/');
    const ref = document.referrer && !document.referrer.includes(window.location.hostname) ? new URL(document.referrer).hostname : '';
    src = [utm, ref].filter(Boolean).join(' · ');
  } catch { /* 来源解析失败不影响打点 */ }
  track('pageview', src);
}
