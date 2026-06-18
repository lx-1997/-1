import React, { useEffect } from 'react';
import { createPortal } from 'react-dom';

// 产品说明书：一篇大白话读懂终端怎么用。匿名可看（不锁登录），头部 📖 入口 + 新手引导末步指引均可打开。
interface HelpItem { k?: string; v: string; }
interface HelpSection { icon: string; title: string; items: HelpItem[]; }

const SECTIONS: HelpSection[] = [
  {
    icon: '🌐', title: '这是什么',
    items: [
      { v: 'DeepFocus 金融终端把「全球行情 + 实时快讯 + 全球投行研报 + AI 解读」聚到一屏，像彭博终端那样盯盘。' },
      { v: '不登录就能看实时行情和快讯；登录后再解锁 AI 解读与研报原文。' },
    ],
  },
  {
    icon: '🆓', title: '免费就能用（无需登录）',
    items: [
      { k: '实时行情', v: 'A股 / 港股 / 美股，开盘自动刷新。' },
      { k: '实时快讯', v: '分钟级推送，可开 🔊 语音播报，盯盘不用一直盯屏幕。' },
      { k: '研报列表 · AI 头条', v: '全球投行报告标题、华尔街视角今日要闻。' },
      { k: '自选股', v: '加入你关注的标的，本地保存。' },
    ],
  },
  {
    icon: '🔑', title: '登录解锁',
    items: [
      { k: 'AI 多模态解读', v: '让 AI 读研报/快讯，给要点、评级、目标价（评级/目标价为研报原文转述）。' },
      { k: '查看研报原文', v: '打开投行报告原件。' },
      { k: '自选云端同步', v: '换设备登录也在。' },
      { k: '注册', v: '用户名 + 密码即可（手机或邮箱填一项就行）。' },
    ],
  },
  {
    icon: '🧭', title: '核心功能怎么用',
    items: [
      { k: '🔍 搜索 / 命令面板', v: '输代码或名称加自选；按 “/” 或 ⌘K 唤起命令面板。' },
      { k: '⭐ 自选股', v: '实时刷新；点某一行可下钻它的快讯 / 文章 / 研报。' },
      { k: '⭐ 自选专区（一股一区）', v: '资讯区的「自选」标签按个股分组：每只股票一个卡片，快讯 / 文章左右对照，表头带实时涨跌与分类计数；离开后新到的消息，回来时标红色「N 新」未读；点表头 / 分节标题可折叠。没有资讯的股票自动隐藏。' },
      { k: '📰 快讯 · 📄 文章 · 📑 研报', v: '顶部一键切换三类资讯；研报支持在线检索全市场。' },
      { k: '🤖 AI 解读', v: '点任意研报 / 文章，让 AI 读图读文，给出买方视角。' },
      { k: '🌗 主题', v: '深色 / 浅色一键切换，偏好会被记住。' },
    ],
  },
  {
    icon: '👑', title: '会员',
    items: [
      { k: '等级', v: '体验期 / 尊享会员 / 永久会员。' },
      { k: '开通 · 续费', v: '点右上角头像 → 💬 联系管理员。' },
    ],
  },
  {
    icon: '🎁', title: '邀请好友 · 得会员',
    items: [
      { k: '免费档', v: '每邀 10 个有效好友（注册并使用）= 1 张月卡，满 100 得年卡。' },
      { k: '付费档', v: '邀来年费付费用户：每 1 个得季卡，满 3 个得年卡。' },
      { k: '怎么兑换', v: '点顶部 🎁「邀请得会员」看进度与卡包，达标自助兑换、直接顺延会员（风控异常转人工复核）。' },
    ],
  },
];

const TerminalHelp: React.FC<{ onClose: () => void; onStartTour?: () => void }> = ({ onClose, onStartTour }) => {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); onClose(); } };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return createPortal(
    <div className="bbt-help-overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="bbt-help-modal" role="dialog" aria-modal="true" aria-label="产品说明书">
        <div className="bbt-help-head">
          <div className="bbt-help-h1">📖 DeepFocus 金融终端 · 产品说明</div>
          <button className="bbt-help-x" onClick={onClose} aria-label="关闭">✕</button>
        </div>
        <div className="bbt-help-scroll">
          <div className="bbt-help-tagline">一个页面盯全球盘 · 行情 + 实时快讯 + 全球投行研报 + AI 解读</div>
          {SECTIONS.map(sec => (
            <section className="bbt-help-sec" key={sec.title}>
              <h3 className="bbt-help-h2"><span className="bbt-help-ic">{sec.icon}</span>{sec.title}</h3>
              <ul className="bbt-help-list">
                {sec.items.map((it, idx) => (
                  <li key={idx}>{it.k && <b className="bbt-help-k">{it.k}</b>}<span>{it.v}</span></li>
                ))}
              </ul>
            </section>
          ))}
          <div className="bbt-help-disc">⚠ 平台提供行情 / 资讯 / 研报聚合与 AI 辅助解读，所有内容<b>仅供研究与教育用途，不构成投资建议</b>，据此操作风险自负。</div>
        </div>
        <div className="bbt-help-foot">
          {onStartTour ? <button className="bbt-help-tour" onClick={onStartTour}>▶ 看 30 秒新手引导</button> : <span />}
          <button className="bbt-help-done" onClick={onClose}>知道了</button>
        </div>
      </div>
    </div>,
    document.body
  );
};

export default TerminalHelp;
