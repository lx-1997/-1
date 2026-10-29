import React, { useEffect, useState } from 'react';

export interface GenerationActivityEvent { detail: string; at: number }
export function appendGenerationActivity(events: GenerationActivityEvent[] = [], detail: string, at = Date.now()): GenerationActivityEvent[] {
  const text = detail.trim();
  if (!text || events[events.length - 1]?.detail === text) return events;
  return [...events, { detail: text, at }].slice(-12);
}

/** Only request/service observations enter the timeline. Time never advances a stage. */
export default function GenerationActivity({ startedAt, events = [], detail, previewReady = false }: {
  startedAt?: number; events?: GenerationActivityEvent[]; detail?: string; previewReady?: boolean;
}) {
  const [mountedAt] = useState(Date.now());
  const origin = startedAt ?? mountedAt;
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [origin]);
  const elapsed = Math.max(0, Math.floor((now - origin) / 1000));
  const rows = events.length ? events : [{ detail: detail || '已提交解读请求，等待服务返回', at: origin }];
  const last = rows[rows.length - 1];
  const waiting = Math.max(0, Math.floor((now - last.at) / 1000));
  return <section className="bbt-generation-activity" aria-label="解读生成过程">
    <div className="bbt-generation-activity__head"><strong>{previewReady ? '速览已就绪 · 全文继续生成' : '解读进行中'}</strong><time>已用 {elapsed} 秒</time></div>
    <p role="status" aria-live="polite">{last.detail}</p>
    <ol aria-label="实际执行记录">{rows.map((row, index) => <li key={`${row.at}-${index}`} className={index === rows.length - 1 ? 'is-current' : ''}>
      <time>+{Math.max(0, Math.floor((row.at - origin) / 1000))}s</time><span>{row.detail}</span>
    </li>)}</ol>
    <small>{waiting >= 15 ? `距上次进度更新 ${waiting} 秒，仍在等待服务返回。` : '进度随实际服务事件更新，不代表完成百分比。'} 可关闭窗口继续浏览，完成后会提醒你。</small>
  </section>;
}
