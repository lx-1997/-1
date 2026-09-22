import React, { useCallback, useEffect, useLayoutEffect, useState } from 'react';
import { createPortal } from 'react-dom';

// 新手引导：首次进入自动播放的「聚光灯」导览。逐个高亮核心功能，平滑移动的光圈 + 卡片淡入。
// 看完/跳过后写 localStorage，不再自动弹；命令栏「?」可随时重看。
export const ONB_KEY = 'df_onboarded_v1';

interface OnbStep { selector?: string; selectors?: string[]; emoji: string; title: string; body: string; }

// 首步为无锚点价值步（讲清免费可得的当日价值），其后点亮搜索/AI 问答（免登录即可体验），末步把盯盘讲诚实：
// 晨报免费，自选快讯/异动推送、复盘推送与文章全文为会员权益——不把会员功能当普遍福利承诺。
const STEPS: OnbStep[] = [
  { emoji: '👋', title: '先看今天市场', body: '实时快讯、每日复盘免登录直接看；深度文章先读导语，会员解锁全文。看到感兴趣的标的，用搜索或 AI 继续深挖。' },
  { selectors: ['.bbt-cmd-input', '.bbt-hero-cta-primary'], emoji: '🔍', title: '查一只股', body: '输入代码或名称（如 茅台 / 600519），不用登录就能看实时行情、真K线和它的快讯/研报。' },
  { selector: '.bbt-aiqa-entry', emoji: '🤖', title: '让 AI 帮你研判', body: '「AI 问答」会自动调行情/估值/资金/研报综合作答——问『它现在贵不贵』试试（免费额度每天都有）。' },
  { emoji: '🔔', title: '开盯盘 · 晨报免费见', body: '把股票加进自选并绑定微信，每个交易日早 8:30 晨报免费推送；自选快讯/异动提醒、每日复盘推送与深度文章全文为会员权益，需要时在「更多」里升级。随时点「更多」重看本引导。' },
];

// 兜底欢迎步：窄屏(≤820px)隐藏顶栏搜索、登录用户无 hero 时锚点步会被过滤，不足 2 步时补在前面，
// 防止导览塌缩成单张订阅推销卡。
const WELCOME_FALLBACK: OnbStep = { emoji: '👋', title: '欢迎', body: '实时快讯、每日复盘免登录直接看；深度文章先读导语，会员解锁全文。点底部「更多」可随时重看本引导。' };

const PAD = 8;
const CARD_W = 340;

const isVisibleTarget = (selector: string) => {
  const el = document.querySelector(selector) as HTMLElement | null;
  if (!el) return false;
  const style = window.getComputedStyle(el);
  const rect = el.getBoundingClientRect();
  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
};

const TerminalOnboarding: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  // 仅保留「无目标的欢迎页」+「当前视口存在可见目标的步骤」；移动端隐藏顶栏搜索后自动改用 hero 按钮或跳过该步。
  const [steps] = useState<OnbStep[]>(() => {
    const visible = STEPS.flatMap(step => {
      const candidates = step.selectors || (step.selector ? [step.selector] : []);
      if (!candidates.length) return [step];
      const selector = candidates.find(isVisibleTarget);
      return selector ? [{ ...step, selector }] : [];
    });
    return visible.length >= 2 ? visible : [WELCOME_FALLBACK, ...visible];
  });
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
  const cardWidth = Math.min(CARD_W, Math.max(280, vw - 24));
  let cardStyle: React.CSSProperties;
  if (!spot) {
    cardStyle = { left: '50%', top: '50%', transform: 'translate(-50%, -50%)' };
  } else {
    const placeBelow = spot.top + spot.height + 190 < vh;
    const left = Math.min(Math.max(12, spot.left + spot.width / 2 - cardWidth / 2), vw - cardWidth - 12);
    const top = placeBelow ? spot.top + spot.height + 14 : Math.max(12, spot.top - 14 - 176);
    cardStyle = { left, top };
  }

  const last = i === steps.length - 1;
  const stepLabel = `第 ${i + 1} 步：${step.title}`;

  return createPortal(
    <div className="bbt-onb" role="dialog" aria-modal="true" aria-live="polite" aria-label={stepLabel} onClick={e => { if (e.target === e.currentTarget) { /* 点遮罩不误关 */ } }}>
      {spot
        ? <div className="bbt-onb-spot" style={spot} />
        : <div className="bbt-onb-scrim" />}
      <div className="bbt-onb-card" style={{ width: cardWidth, ...cardStyle }} key={i}>
        <button className="bbt-onb-x" onClick={finish} aria-label="跳过引导" title="跳过">✕</button>
        <div className="bbt-onb-emoji">{step.emoji}</div>
        <div className="bbt-onb-title">{stepLabel}</div>
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
