import React, { useCallback, useEffect, useLayoutEffect, useState } from 'react';
import { createPortal } from 'react-dom';

// 新手引导：首次进入自动播放的「聚光灯」导览。逐个高亮核心功能，平滑移动的光圈 + 卡片淡入。
// 看完/跳过后写 localStorage，不再自动弹；命令栏「?」可随时重看。
export const ONB_KEY = 'df_onboarded_v1';

interface OnbStep { selector?: string; emoji: string; title: string; body: string; }

// 精简核心路径：主题/语音/账号/说明书都有常驻入口，不占引导步数；邀请在结尾一行带过。
// 微信收快讯入口藏在账号下拉里（默认不在 DOM），故用无选择器的居中卡片介绍，不打光圈。
const STEPS: OnbStep[] = [
  { emoji: '👋', title: '欢迎来到 DeepFocus 金融终端', body: '不用登录就能看 A股 / 港股 / 美股实时行情和分钟级快讯，登录后再解锁 AI 解读与研报原文。下面带你认全核心功能，随时可跳过。' },
  { selector: '.bbt-cmd-input', emoji: '🔍', title: '搜索 / 添加标的', body: '输入代码或名称（A股 / 港股 / 美股），或按 “/” 唤起命令面板，回车即加入自选。' },
  { selector: '.bbt-grid .bbt-panel .bbt-ph', emoji: '⭐', title: '自选股票', body: '点任意行情行，下钻该标的的快讯 / 文章 / 研报；登录后自选自动云端同步，换设备也在。' },
  { selector: '.bbt-filters', emoji: '📰', title: '快讯 · 文章 · 研报', body: '一键切换三类资讯。「★自选」按个股分组只看你关心的；「研报」聚合全球投行报告、支持在线检索。' },
  { selector: '.bbt-chip[data-cat="研报"]', emoji: '🤖', title: 'AI 多模态解读', body: '点任意研报 / 文章，AI 读图读文给出买方观点、评级与目标价（登录后解锁，仅供研究参考）。' },
  { emoji: '🟢', title: '微信收快讯（尊享会员）', body: '尊享会员可在「账号菜单 → 绑定微信」扫码绑定，重要快讯就主动推到你微信（带利好 / 利空标注）。可选「全部新快讯」或「按关键词推送」——关键词支持多选、模糊匹配、不区分大小写，命中任意一个就推给你。' },
  { emoji: '🎉', title: '开始体验！', body: '行情和快讯现在就能免费看。注册（用户名 + 密码即可）再解锁 AI 解读、研报原文与自选云端同步；邀好友还能免费得会员。随时点右上角 ❔ 重看本引导。' },
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
