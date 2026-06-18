import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import * as authService from '../services/authService';
import { apiGet } from '../services/apiClient';
import { copyText } from '../utils/clipboard';
import { shareImageNative } from '../utils/share';

// 圆角矩形路径（海报卡片用）
function _roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

// 邀请得会员：等级 + 近在咫尺里程碑 + 每周限时 + 排行榜 + 卡包（5 档，可自助兑换；已取消永久会员档）。需登录。
const CARD_META: { key: authService.CardKey; label: string; emoji: string }[] = [
  { key: 'day3', label: '3天卡', emoji: '🎟️' },
  { key: 'week', label: '周卡', emoji: '🎫' },
  { key: 'month', label: '月卡', emoji: '📅' },
  { key: 'quarter', label: '季卡', emoji: '🎴' },
  { key: 'year', label: '年卡', emoji: '🏆' },
];

const STATUS_LABEL: Record<string, string> = { paid: '已付费', activated: '已激活', registered: '已注册' };
const LEVEL_EMOJI: Record<string, string> = { 青铜: '🥉', 白银: '🥈', 黄金: '🥇', 铂金: '💠', 钻石: '💎', 王者: '👑' };

const TerminalReferral: React.FC<{
  onClose: () => void;
  onChanged?: () => void;
  showToast?: (m: string) => void;
}> = ({ onClose, onChanged, showToast }) => {
  const [data, setData] = useState<authService.ReferralOverview | null>(null);
  const [board, setBoard] = useState<authService.ReferralLeaderRow[]>([]);
  const [camp, setCamp] = useState<authService.ReferralCampaign | null>(null);
  const [nowTs, setNowTs] = useState(Date.now());  // 驱动倒计时
  const loadTsRef = useRef(Date.now());            // 拉到活动数据的时刻（倒计时基准）
  const [loading, setLoading] = useState(true);
  const [redeeming, setRedeeming] = useState('');
  const [refTab, setRefTab] = useState<'invite' | 'rank'>('invite');  // 两个活动分页：得会员 / 冲榜赛
  const [copied, setCopied] = useState(false);
  // 邀请海报图片
  const [posterUrl, setPosterUrl] = useState('');
  const [posterBusy, setPosterBusy] = useState(false);
  const [posterTip, setPosterTip] = useState('');
  const [posterCoarse, setPosterCoarse] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [d, b, c] = await Promise.all([authService.fetchReferral(), authService.fetchReferralLeaderboard(), authService.fetchCampaign()]);
      setData(d); setBoard(b); setCamp(c); loadTsRef.current = Date.now();
    } catch { /* */ } finally { setLoading(false); }
  }, []);
  useEffect(() => { const t = window.setInterval(() => setNowTs(Date.now()), 1000); return () => window.clearInterval(t); }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); onClose(); } };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const link = data ? `${window.location.origin}/?ref=${data.code}` : '';
  const copyLink = async () => {
    const ok = await copyText(link);
    if (ok) { setCopied(true); setTimeout(() => setCopied(false), 1500); }
    else showToast?.('复制失败，请长按链接手动复制');
  };

  // 一键复制「双边钩子」邀请文案：突出好友能立得 3 天（利他），降低对方戒心；自己另得邀请奖励。
  const [msgCopied, setMsgCopied] = useState(false);
  const inviteMsg = data
    ? `🎁 送你 DeepFocus 金融终端 3 天尊享会员！\n用我的邀请码 ${data.code} 注册即得 —— 全球行情 · 实时快讯 · AI 研报解读，比别人早一步看懂行情。\n👉 ${link}`
    : '';
  const copyInvite = async () => {
    const ok = await copyText(inviteMsg);
    if (ok) { setMsgCopied(true); setTimeout(() => setMsgCopied(false), 2000); showToast?.('邀请文案已复制，去微信粘贴给好友即可'); }
    else showToast?.('复制失败，请长按文案手动复制');
  };

  // 生成一张「邀请海报」图片：品牌头 + 文案 + 大二维码（=邀请链接，扫码即注册并自动带邀请码）+ 邀请码 + 规则。
  const makePoster = useCallback(async () => {
    if (!data || posterBusy) return;
    setPosterBusy(true);
    try {
      const inviteUrl = link;
      let qr: { size: number; matrix: number[][] } | null = null;
      try { qr = await apiGet<{ size: number; matrix: number[][] }>('/api/qr', { params: { data: inviteUrl } }); } catch { qr = null; }

      const W = 660, H = 940, S = 2;   // S=2 高清
      const cv = document.createElement('canvas');
      cv.width = W * S; cv.height = H * S;
      const ctx = cv.getContext('2d');
      if (!ctx) { setPosterBusy(false); return; }
      ctx.scale(S, S);
      const CX = W / 2;
      const FN = (px: number, w = '400') => `${w} ${px}px -apple-system, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "Hiragino Sans GB", sans-serif`;
      const ctr = (t: string, y: number, font: string, color: string) => { ctx.font = font; ctx.fillStyle = color; ctx.textAlign = 'center'; ctx.fillText(t, CX, y); };

      // 背景：深色渐变 + 顶部金色光晕
      const bg = ctx.createLinearGradient(0, 0, 0, H);
      bg.addColorStop(0, '#0d1420'); bg.addColorStop(0.5, '#0a0f17'); bg.addColorStop(1, '#0b1119');
      ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);
      const glow = ctx.createRadialGradient(CX, 70, 20, CX, 70, 420);
      glow.addColorStop(0, 'rgba(255,176,0,0.16)'); glow.addColorStop(1, 'rgba(255,176,0,0)');
      ctx.fillStyle = glow; ctx.fillRect(0, 0, W, 340);
      // 外描边
      ctx.strokeStyle = 'rgba(255,176,0,0.28)'; ctx.lineWidth = 2; _roundRect(ctx, 12, 12, W - 24, H - 24, 22); ctx.stroke();

      ctx.textBaseline = 'alphabetic';
      // 品牌头
      ctr('🎁 邀你免费体验', 92, FN(34, '800'), '#ffffff');
      ctr('DEEPFOCUS 金融终端', 134, FN(20, '800'), '#ffb000');
      ctr('彭博风格终端 · 全球行情 · 实时快讯 · AI 研报解读', 168, FN(14.5, '500'), '#9aa3b0');

      // 中部主文案
      ctr('扫码注册 · 即享会员特权', 224, FN(21, '700'), '#ffd479');

      // 二维码白卡
      const qrBox = 320, qx = CX - qrBox / 2, qy = 258;
      ctx.fillStyle = '#ffffff'; _roundRect(ctx, qx, qy, qrBox, qrBox, 18); ctx.fill();
      if (qr) {
        const pad = 24, inner = qrBox - pad * 2, n = qr.size, cell = inner / n, ox = qx + pad, oy = qy + pad;
        ctx.fillStyle = '#0a0f17';
        for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) if (qr.matrix[i][j]) ctx.fillRect(ox + j * cell, oy + i * cell, cell + 0.6, cell + 0.6);
      } else {
        ctr('二维码生成失败', qy + qrBox / 2, FN(16, '600'), '#888');
      }
      ctr('👆 长按 / 扫码即可注册', qy + qrBox + 34, FN(15, '600'), '#cfd6e0');

      // 邀请码
      const codeY = qy + qrBox + 78;
      ctr('专属邀请码', codeY, FN(13, '500'), '#8a93a0');
      ctr(data.code, codeY + 40, `800 34px "SF Mono", "Roboto Mono", Menlo, monospace`, '#ffb000');

      // 规则胶囊
      const ry = codeY + 92;
      ctx.fillStyle = 'rgba(255,255,255,0.05)'; _roundRect(ctx, 60, ry, W - 120, 92, 14); ctx.fill();
      ctr('邀 1 人立得卡 · 邀满 30 人免费拿价值 ¥698 年卡', ry + 36, FN(15, '600'), '#ffd980');
      ctr('每过一档即得对应卡，没集满也能兑换 · 越邀越大', ry + 66, FN(15, '600'), '#e6ebf2');

      // 页脚
      ctr('扫码访问 · ' + inviteUrl.replace(/^https?:\/\//, ''), H - 36, FN(13, '500'), '#6f7886');

      ctx.textAlign = 'left';
      const url = cv.toDataURL('image/png');
      setPosterUrl(url);
      const coarse = typeof window !== 'undefined' && window.matchMedia && window.matchMedia('(pointer: coarse)').matches;
      setPosterCoarse(coarse);
      // 移动端优先原生分享面板（直接发好友/朋友圈），不支持/取消再回退长按存图
      if (coarse) {
        const blob: Blob | null = await new Promise(res => cv.toBlob(b => res(b), 'image/png'));
        if (blob) {
          const r = await shareImageNative(blob, { filename: 'DeepFocus邀请海报.png', title: '邀你免费体验 DeepFocus', text: '扫码注册，领 3 天尊享会员' });
          if (r === 'shared') { setPosterTip('👇 也可长按下方海报另存'); return; }
        }
      }
      // 尝试直接复制到剪贴板（桌面 Chrome/Edge 支持），失败则提示保存
      let copiedImg = false;
      try {
        if (!coarse && navigator.clipboard && (window as any).ClipboardItem) {
          const blob: Blob | null = await new Promise(res => cv.toBlob(b => res(b), 'image/png'));
          if (blob) { await navigator.clipboard.write([new (window as any).ClipboardItem({ 'image/png': blob })]); copiedImg = true; }
        }
      } catch { copiedImg = false; }
      setPosterTip(coarse
        ? '👇 长按下方海报，选择「存储图像 / 保存到相册」分享给好友'
        : (copiedImg ? '✓ 海报已复制，可直接粘贴到微信 / 朋友圈；或右键图片另存' : '右键图片选择「图片另存为」，或点下方「下载海报」'));
      if (copiedImg) showToast?.('邀请海报已复制为图片');
    } catch {
      showToast?.('生成海报失败，请重试');
    } finally {
      setPosterBusy(false);
    }
  }, [data, link, posterBusy, showToast]);

  const redeem = async (key: authService.CardKey) => {
    setRedeeming(key);
    try {
      const res = await authService.redeemReferralCard(key);
      showToast?.(res.message || (res.status === 'pending' ? '已提交，需人工复核' : '兑换成功，会员已顺延'));
      await load();
      onChanged?.();
    } catch (e: unknown) {
      showToast?.((e instanceof Error ? e.message : '') || '兑换失败');
    } finally {
      setRedeeming('');
    }
  };

  return createPortal(
    <div className="bbt-ref-overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="bbt-ref-modal" role="dialog" aria-modal="true" aria-label="邀请得会员">
        <div className="bbt-ref-head">
          <div className="bbt-ref-h1">🎁 邀好友 · 免费拿会员<span className="bbt-ref-h1-hl">最高 ¥698 年卡</span></div>
          <button className="bbt-ref-x" onClick={onClose} aria-label="关闭">✕</button>
        </div>

        <div className="bbt-ref-scroll">
          {loading && <div className="bbt-ref-loading">加载中…</div>}
          {!loading && data && (
            <>
              {/* 共享动作：复制邀请（得会员 / 冲榜 两个活动都靠它） */}
              <div className="bbt-ref-link">
                <button className="bbt-ref-invite-cta" onClick={copyInvite}>
                  {msgCopied ? '✓ 已复制，去微信发给好友' : '📋 复制邀请文案 · 发给好友'}
                </button>
                <div className="bbt-ref-link-row">
                  <input className="bbt-ref-link-input" readOnly value={link} onFocus={e => e.currentTarget.select()} />
                  <button className="bbt-ref-copy" onClick={copyLink}>{copied ? '已复制 ✓' : '复制链接'}</button>
                  <button className="bbt-ref-poster" onClick={makePoster} disabled={posterBusy}>{posterBusy ? '生成中…' : '🖼 海报'}</button>
                </div>
                <div className="bbt-ref-code">邀请码 <b>{data.code}</b> · 好友注册立得 3 天会员，你也有奖</div>
              </div>

              {(() => { const hasCamp = !!(camp && (camp.active || camp.upcoming)); return (<>
              {/* 两个活动分页（无冲榜赛时不显示 Tab，直接展示「得会员」） */}
              {hasCamp && (
                <div className="bbt-ref-tabs">
                  <button className={'bbt-ref-tab' + (refTab === 'invite' ? ' on' : '')} onClick={() => setRefTab('invite')}>🎁 邀请得会员</button>
                  <button className={'bbt-ref-tab bbt-ref-tab--camp' + (refTab === 'rank' ? ' on' : ' attract')} onClick={() => setRefTab('rank')}>
                    🏆 冲榜赛<span className="bbt-ref-tab-hot">赢大奖</span>
                  </button>
                </div>
              )}

              {/* 活动一：邀请得会员（累计邀请解锁会员卡，人人可得） */}
              {(!hasCamp || refTab === 'invite') && (
                <div className="bbt-ref-pane">
                  <div className="bbt-ref-pane-tip">📈 累计邀请，达标即解锁会员卡 —— <b>人人可得</b></div>
                  <div className="bbt-ref-hero">
                    <div className="bbt-ref-hero-lv">
                      <span className="bbt-ref-lv-badge">{LEVEL_EMOJI[data.level.name] || '🥉'} {data.level.name}</span>
                      <span className="bbt-ref-lv-next">已有效邀请 <b>{data.qualified_reg}</b> 人</span>
                    </div>
                    {data.next_milestone ? (() => {
                      const prev = [...data.ladder].reverse().find(m => m.reached)?.at || 0;
                      const span = data.next_milestone.at - prev;
                      const done = data.qualified_reg - prev;
                      return <>
                        <div className="bbt-ref-hero-big">再邀 <b>{data.next_milestone.remaining}</b> 人 → 解锁 <b className="bbt-ref-hero-card">{data.next_milestone.card_label}</b></div>
                        <div className="bbt-ref-hero-bar"><span style={{ width: `${Math.min(100, Math.max(4, done / span * 100))}%` }} /></div>
                      </>;
                    })() : <div className="bbt-ref-hero-big">🎉 你已邀满全部里程碑，封神！</div>}
                    {data.hero && !data.hero.reached && (
                      <div className="bbt-ref-hero-sub">🏆 终极：邀满 {data.hero.at} 人免费拿价值 <b>¥{data.hero.card_value}</b> {data.hero.card_label}</div>
                    )}
                  </div>
                  <div className="bbt-ref-ladder">
                    {data.ladder.map((m, i) => {
                      const isNext = !m.reached && data.next_milestone?.at === m.at;
                      return (
                        <div key={i} className={'bbt-ref-step' + (m.reached ? ' done' : '') + (isNext ? ' next' : '')}>
                          <div className="bbt-ref-step-card">{CARD_META.find(c => c.key === m.card)?.emoji} {m.card_label}</div>
                          <div className="bbt-ref-step-at">{m.reached ? '✓ 已达' : `${m.at}人`}</div>
                        </div>
                      );
                    })}
                  </div>
                  <div className="bbt-ref-wallet-t">🎴 我的卡包（解锁即可兑换）</div>
                  <div className="bbt-ref-wallet">
                    {CARD_META.map(c => {
                      const avail = data.available[c.key];
                      return (
                        <div className={'bbt-ref-card' + (avail > 0 ? ' on' : '')} key={c.key}>
                          <div className="bbt-ref-card-emoji">{c.emoji}</div>
                          <div className="bbt-ref-card-name">{c.label}<span className="bbt-ref-card-days">{`${data.card_days[c.key]}天`}</span></div>
                          <div className="bbt-ref-card-cnt">可兑换 <b>{avail}</b></div>
                          <button className="bbt-ref-redeem" disabled={avail <= 0 || redeeming === c.key} onClick={() => redeem(c.key)}>
                            {redeeming === c.key ? '兑换中…' : (avail > 0 ? '立即兑换' : '未解锁')}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                  <details className="bbt-ref-rules-d">
                    <summary>ℹ️ 规则</summary>
                    <div className="bbt-ref-rules">
                      累计解锁：{data.ladder.map(m => `${m.at}人→${m.card_label}`).join('、')}；没集满也能兑换已解锁的卡。「激活」=好友注册后次日再回来用一次（防刷）。
                    </div>
                  </details>
                </div>
              )}

              {/* 活动二：冲榜赛（限时排名，按名次拿奖） */}
              {hasCamp && refTab === 'rank' && (() => {
                const base = camp!.active ? camp!.ends_in : camp!.starts_in;
                const elapsed = Math.floor((nowTs - loadTsRef.current) / 1000);
                const left = Math.max(0, base - elapsed);
                const dd = Math.floor(left / 86400), hh = Math.floor((left % 86400) / 3600), mm = Math.floor((left % 3600) / 60), ss = left % 60;
                const me = camp!.me;
                return (
                  <div className="bbt-ref-pane">
                    <div className="bbt-ref-pane-tip">🏁 限时排名赛 · 邀得越多名次越高，<b>按名次拿奖</b></div>
                    <div className="bbt-camp">
                      <div className="bbt-camp-top">
                        <span className="bbt-camp-fire">🏆</span>
                        <span className="bbt-camp-title">{camp!.title}</span>
                        <span className="bbt-camp-cd">{camp!.active ? '距结束' : '距开始'} {dd}天 {String(hh).padStart(2, '0')}:{String(mm).padStart(2, '0')}:{String(ss).padStart(2, '0')}</span>
                      </div>
                      <div className="bbt-camp-prizes">
                        {camp!.prizes.map((p, i) => (
                          <span key={i} className={'bbt-camp-prize p' + i}>{p.emoji} {p.ranks} · <b>{p.label}</b></span>
                        ))}
                      </div>
                      {me && (
                        <div className="bbt-camp-me">
                          {me.rank ? <>我当前 <b>第 {me.rank} 名</b>（{me.count} 人）{me.prize
                            ? <span className="bbt-camp-win">· 当前可得 {me.prize.emoji}{me.prize.label}</span>
                            : <span className="bbt-camp-miss">· 暂未进奖励区</span>}</> : <>你还没上榜（活动期内已邀 {me.count} 人）</>}
                          <div className="bbt-camp-gap">
                            {me.to_next > 0 ? `再邀 ${me.to_next} 人即可反超上一名 ↑` : (me.rank === 1 ? '🥇 榜首！守住就赢' : '继续保持')}
                          </div>
                        </div>
                      )}
                    </div>
                    {camp!.leaderboard.length > 0 ? (
                      <div className="bbt-ref-board">
                        {camp!.leaderboard.slice(0, 10).map((row, i) => (
                          <div className={'bbt-ref-board-row' + (i < 3 ? ' top' : '') + (row.prize ? ' win' : '')} key={i}>
                            <span className="bbt-ref-board-rank">{['🥇', '🥈', '🥉'][i] || row.rank}</span>
                            <span className="bbt-ref-board-name">{row.name}</span>
                            {row.prize && <span className="bbt-ref-board-prize">{row.prize.emoji}{row.prize.label}</span>}
                            <span className="bbt-ref-board-cnt">{row.count} 人</span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="bbt-ref-board-empty">榜单刚清零重算 · 现在邀请，抢第一名 👑</div>
                    )}
                  </div>
                );
              })()}
              </>); })()}

              {data.suspicious && (
                <div className="bbt-ref-warn">⚠ 检测到部分邀请来自相同网络，兑换将转人工复核——请点头像 → 💬 联系管理员。</div>
              )}
            </>
          )}
        </div>

        <div className="bbt-ref-foot">
          <span className="bbt-ref-foot-note">完成任务可自助兑换；风控复核请联系管理员</span>
          <button className="bbt-ref-done" onClick={onClose}>知道了</button>
        </div>
      </div>

      {posterUrl && (
        <div className="bbt-doc-overlay" onMouseDown={() => setPosterUrl('')} style={{ zIndex: 100000 }}>
          <div className="bbt-shareimg" onMouseDown={e => e.stopPropagation()}>
            <div className={`bbt-shareimg-tip${posterCoarse ? ' bbt-shareimg-tip--big' : ''}`}>{posterTip}</div>
            <img className="bbt-shareimg-img" src={posterUrl} alt="邀请海报" />
            <div className="bbt-ai-actions">
              {!posterCoarse && <a className="bbt-ai-btn bbt-ai-btn--img" href={posterUrl} download="DEEPFOCUS邀请海报.png">⬇ 下载海报</a>}
              <button className="bbt-ai-btn bbt-ai-btn--close" onClick={() => setPosterUrl('')}>关闭</button>
            </div>
          </div>
        </div>
      )}
    </div>,
    document.body,
  );
};

export default TerminalReferral;
