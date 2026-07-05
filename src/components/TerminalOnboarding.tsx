import React, { useCallback, useEffect, useLayoutEffect, useState } from 'react';
import { createPortal } from 'react-dom';

// 新手引导：首次进入自动播放的「聚光灯」导览。逐个高亮核心功能，平滑移动的光圈 + 卡片淡入。
// 看完/跳过后写 localStorage，不再自动弹；命令栏「?」可随时重看。
export const ONB_KEY = 'df_onboarded_v1';

interface OnbStep { selector?: string; emoji: string; title: string; body: string; }

// 精简为 3 步核心动作（搜股→AI 问答→开盯盘）：7 步文字导览在 10 秒心智窗口里讲不完，
// 反而盖脸劝退——次要功能（研报/微信/邀请）都有常驻入口，让用户用的时候自己发现。
const STEPS: OnbStep[] = [
  { selector: '.bbt-cmd-input', emoji: '🔍', title: '第一步：查一只股', body: '输入代码或名称（如 茅台 / 600519），不用登录就能看实时行情、真K线和它的快讯/研报。' },
  { selector: '.bbt-aiqa-entry', emoji: '🤖', title: '第二步：让 AI 帮你研判', body: '「AI 问答」会自动调行情/估值/资金/研报综合作答——问『它现在贵不贵』试试（免费额度每天都有）。' },
  { emoji: '🔔', title: '第三步：开盯盘，别错过', body: '把股票加进自选并开启盯盘提醒，你的股有快讯/异动会第一时间通知你；每天早8:30晨报、收盘15:35复盘准时见。随时点右上角 ❔ 重看引导。' },
];

const PAD = 8;
const CARD_W = 340;

const TerminalOnboarding: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  // 仅保留「无目标的欢迎页」+「目标存在的步骤」（如未登录无登录按钮则跳过该步）
  const [steps] = useState<OnbStep[]>(() => STEPS.filter(s => !s.selector || document.querySelector(s.selector)));
  const [i, setI] = useState(0);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const step = steps[i];

  const measure = useCallback(() => {
    if (!step?.selector) { setRect(null); return; }
    const el = document.querySelector(step.selector) as HTMLElement | null;
    if (!el) { setRect(null); return; }
    // 即时(非平滑)滚动到可见，再在布局/滚动稳定后测量 → 避免平滑滚动导致光圈错位
    el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    const grab = () => { const e2 = document.querySelector(step.selector!) as HTMLElement | null; if (e2) setRect(e2.getBoundingClientRect()); };
    grab();
    requestAnimationFrame(grab);
    window.setTimeout(grab, 220);  // 兜底：异步布局/滚动稳定后再校准
  }, [step]);

  useLayoutEffect(() => { measure(); }, [measure]);
  useEffect(() => {
    const onR = () => measure();
    window.addEventListener('resize', onR, { passive: true });
    window.addEventListener('scroll', onR, true);
    return () => { window.removeEventListener('resize', onR); window.removeEventListener('scroll', onR, true); };
  }, [measure]);

  const finish = useCallback(() => { try { localStorage.setItem(ONB_KEY, '1'); } catch { /* */ } onClose(); }, [onClose]);
  const next = useCallback(() => setI(v => { if (v >= steps.length - 1) { finish(); return v; } return v + 1; }), [steps.length, finish]);
  const prev = useCallback(() => setI(v => Math.max(0, v - 1)), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); finish(); }
      else if (e.key === 'Enter' || e.key === 'ArrowRight') { e.preventDefault(); next(); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); prev(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [next, prev, finish]);

  if (!step) return null;

  const spot = rect && rect.width > 0
    ? { left: rect.left - PAD, top: rect.top - PAD, width: rect.width + PAD * 2, height: rect.height + PAD * 2 }
    : null;

  const vw = typeof window !== 'undefined' ? window.innerWidth : 1280;
  const vh = typeof window !== 'undefined' ? window.innerHeight : 800;
  let cardStyle: React.CSSProperties;
  if (!spot) {
    cardStyle = { left: '50%', top: '50%', transform: 'translate(-50%, -50%)' };
  } else {
    const placeBelow = spot.top + spot.height + 190 < vh;
    const left = Math.min(Math.max(12, spot.left + spot.width / 2 - CARD_W / 2), vw - CARD_W - 12);
    const top = placeBelow ? spot.top + spot.height + 14 : Math.max(12, spot.top - 14 - 176);
    cardStyle = { left, top };
  }

  const last = i === steps.length - 1;

  return createPortal(
    <div className="bbt-onb" role="dialog" aria-modal="true" aria-label={step.title} onClick={e => { if (e.target === e.currentTarget) { /* 点遮罩不误关 */ } }}>
      {spot
        ? <div className="bbt-onb-spot" style={spot} />
        : <div className="bbt-onb-scrim" />}
      <div className="bbt-onb-card" style={{ width: CARD_W, ...cardStyle }} key={i}>
        <button className="bbt-onb-x" onClick={finish} aria-label="跳过引导" title="跳过">✕</button>
        <div className="bbt-onb-emoji">{step.emoji}</div>
        <div className="bbt-onb-title">{step.title}</div>
        <div className="bbt-onb-body">{step.body}</div>
        <div className="bbt-onb-foot">
          <div className="bbt-onb-dots" role="img" aria-label={`第 ${i + 1} / ${steps.length} 步`}>
            {steps.map((_, k) => <span key={k} className={k === i ? 'on' : ''} aria-hidden="true" />)}
            <span className="bbt-onb-count">{i + 1}/{steps.length}</span>
          </div>
          <div className="bbt-onb-btns">
            <button className="bbt-onb-skip" onClick={finish}>跳过</button>
            {i > 0 && <button className="bbt-onb-prev" onClick={prev}>上一步</button>}
            <button className="bbt-onb-next" onClick={next}>{last ? '开始使用' : '下一步'}</button>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
};

export default TerminalOnboarding;
