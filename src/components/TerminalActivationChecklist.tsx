import React, { useEffect, useState } from 'react';

interface Props {
  loggedIn: boolean;
  hasStock: boolean;
  hasAi: boolean;
  alertsEnabled: boolean;
  onSearch: () => void;
  onAskAi: () => void;
  onSaveWatchlist: () => void;
  onEnableAlerts: () => void;
}

/**
 * 首日激活三步卡：把“看一眼”变成可回访的工作台。
 * 不做遮罩、不阻断内容；只在三步未完成时给出下一步，完成后自动消失。
 */
const TerminalActivationChecklist: React.FC<Props> = ({
  loggedIn, hasStock, hasAi, alertsEnabled, onSearch, onAskAi, onSaveWatchlist, onEnableAlerts,
}) => {
  const [dismissed, setDismissed] = useState(false);
  const [aiCompleted, setAiCompleted] = useState(() => {
    try { return localStorage.getItem('df.activation.ai.v1') === '1'; } catch { return false; }
  });
  useEffect(() => {
    if (!hasAi) return;
    setAiCompleted(true);
    try { localStorage.setItem('df.activation.ai.v1', '1'); } catch { /* 隐私模式不阻断使用 */ }
  }, [hasAi]);
  const steps = [
    { done: hasStock, label: '看一只股票', action: onSearch, cta: '搜索股票' },
    { done: aiCompleted, label: '问一次 AI', action: onAskAi, cta: '让 AI 研判' },
    { done: alertsEnabled, label: '开启回访提醒', action: loggedIn ? onEnableAlerts : onSaveWatchlist, cta: loggedIn ? '开启盯盘' : '登录保存自选' },
  ];
  const completed = steps.filter(step => step.done).length;
  if (dismissed || completed === steps.length) return null;
  const next = steps.find(step => !step.done) || steps[steps.length - 1];

  return (
    <section
      aria-label="首日激活任务"
      style={{
        margin: '12px 0 14px', padding: '14px 16px', borderRadius: 12,
        border: '1px solid rgba(245,185,66,.28)', background: 'linear-gradient(135deg, rgba(245,185,66,.10), rgba(255,255,255,.025))',
      }}
    >
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div>
          <div style={{ fontSize: 11, color: 'var(--text-soft,#aab4bf)', letterSpacing: '.04em' }}>首日激活 · {completed}/3</div>
          <b style={{ display: 'block', marginTop: 3, fontSize: 16 }}>完成 3 步，把一次访问变成每天有用的工作台</b>
          <div style={{ marginTop: 4, fontSize: 12, color: 'var(--text-soft,#aab4bf)' }}>下一步：{next.label}。不需要一次做完，进度会自动保留。</div>
        </div>
        <button type="button" onClick={() => setDismissed(true)} aria-label="收起首日激活任务" title="稍后再看" style={{ border: 0, background: 'transparent', color: 'var(--text-soft,#aab4bf)', cursor: 'pointer', fontSize: 18, lineHeight: 1 }}>×</button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,minmax(0,1fr))', gap: 8, marginTop: 12 }}>
        {steps.map((step, index) => (
          <button
            type="button"
            key={step.label}
            onClick={step.done ? undefined : step.action}
            disabled={step.done}
            style={{
              textAlign: 'left', padding: '9px 10px', borderRadius: 9, fontFamily: 'inherit',
              border: `1px solid ${step.done ? 'rgba(16,185,129,.35)' : index === steps.findIndex(item => !item.done) ? 'rgba(245,185,66,.55)' : 'var(--line-2,#233039)'}`,
              background: step.done ? 'rgba(16,185,129,.08)' : index === steps.findIndex(item => !item.done) ? 'rgba(245,185,66,.10)' : 'rgba(255,255,255,.025)',
              color: 'inherit', cursor: step.done ? 'default' : 'pointer', opacity: step.done ? .82 : 1,
            }}
          >
            <span style={{ display: 'block', fontSize: 12, fontWeight: 700 }}>{step.done ? '✓ ' : `${index + 1}. `}{step.label}</span>
            <span style={{ display: 'block', marginTop: 4, fontSize: 11, color: step.done ? 'var(--up,#10b981)' : 'var(--text-soft,#aab4bf)' }}>{step.done ? '已完成' : step.cta}</span>
          </button>
        ))}
      </div>
    </section>
  );
};

export default TerminalActivationChecklist;
