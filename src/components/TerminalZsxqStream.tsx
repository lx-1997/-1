import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  ZsxqComment,
  ZsxqTopic,
  ZsxqStreamResponse,
  getZsxqStream,
  getZsxqTopicComments,
} from '../services/zsxqStreamService';
import ShareButton from './common/ShareButton';
import './TerminalZsxqStream.css';

// 知识星球图走后端代理：绕客户端防盗链/token 失效，让长图能高清放大 + 下载成可查看的图片文件。
// dl=1 → 附件下载。仅对 zsxq 图床 URL 生效（后端 host 白名单）。
// v 版本号:图片处理策略变更时 +1，强制浏览器绕过 24h 缓存拉新版（v2=底部品牌栏替代原叠加水印）
const zsxqImg = (url: string, dl = false): string =>
  `/api/zsxq/image?u=${encodeURIComponent(url)}&v=2${dl ? '&dl=1' : ''}`;

// 机构纪要分享钩子：只取标题 + ≤100 字导语（第三方付费内容不外泄全文）。落地页 /note/{id} 同口径 noindex 软墙。
const noteShareTarget = (item: ZsxqTopic) => {
  const flat = (item.text || '').replace(/\s+/g, ' ').trim();
  const lead = flat.length > 100 ? flat.slice(0, 100) + '…' : flat;
  const site = (typeof window !== 'undefined' && window.location.origin) || 'https://daocaijing.com';
  return { kind: 'article', title: (item.title || '机构纪要').trim(), summary: lead, url: `${site}/note/${item.id}` };
};

/**
 * 星球纪要——知识星球普通帖子的独立信息流（调研会议纪要 / 个股动态点评 / 组合观点）。
 * 「研报」标签只覆盖星球【文件】(PDF)；星球里大量高价值内容是【普通帖子】，在此独立展示。
 * 可见范围：所有人含匿名（用户拍板放开 2026-07-06，去白名单+去登录墙）——后端匿名只给缓存首页
 * （护共享星球 cookie），登录用户完整搜索/翻页。单条可分享成 /note/{id} 软墙落地页（已放开 SEO 收录）。
 * ⚠️ 第三方付费社群内容：仅供研究参考，不构成投资建议。
 */

const fmtDate = (v?: string | null): string => {
  if (!v) return '';
  const s = String(v);
  return s.length >= 10 ? s.slice(0, 10) : s;
};

// 机构纪要卡头带到「时:分:秒」：星球 create_time 形如 2026-07-06T15:01:11.583+0800（已是北京时间）
// → 直接切，不做时区换算；无时间部分则回退纯日期。
const fmtDateTime = (v?: string | null): string => {
  if (!v) return '';
  const m = String(v).match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})/);
  return m ? `${m[1]} ${m[2]}` : fmtDate(v);
};

// 正文分段渲染：`# xxx` 行作小节标题（星球纪要惯用），「问：/答：」拆问答块，其余普通段落。
const renderBody = (text: string): React.ReactNode => {
  const blocks = text.split(/\n+/).map(s => s.trim()).filter(Boolean);
  return blocks.map((b, i) => {
    const h = /^#\s*/.test(b);
    if (h) return <div className="tzs-h" key={i}>{b.replace(/^#\s*/, '')}</div>;
    const q = /^问\s*[:：]/.test(b);
    const a = /^答\s*[:：]/.test(b);
    if (q || a) {
      return (
        <div className={`tzs-qa ${q ? 'tzs-q' : 'tzs-a'}`} key={i}>
          <span className="tzs-qa-tag">{q ? '问' : '答'}</span>
          <span className="tzs-qa-text">{b.replace(/^[问答]\s*[:：]\s*/, '')}</span>
        </div>
      );
    }
    return <p className="tzs-para" key={i}>{b}</p>;
  });
};

const CommentRow: React.FC<{ c: ZsxqComment }> = ({ c }) => (
  <div className={`tzs-cmt ${c.reply_to ? 'tzs-cmt--reply' : ''}`}>
    <div className="tzs-cmt-head">
      <span className="tzs-cmt-author">{c.author || '星球成员'}</span>
      {c.reply_to && <span className="tzs-cmt-replyto">回复 @{c.reply_to}</span>}
      {c.sticky && <span className="tzs-cmt-pin">置顶</span>}
      <span className="tzs-cmt-date">{fmtDate(c.create_time)}</span>
      {(c.likes_count || 0) > 0 && <span className="tzs-cmt-likes">👍{c.likes_count}</span>}
    </div>
    <div className="tzs-cmt-text">{c.text}</div>
  </div>
);

// 评论区：随帖预览直接展示；更多时「加载全部 N 条」按需拉全。
const CommentsBlock: React.FC<{ item: ZsxqTopic }> = ({ item }) => {
  const [full, setFull] = useState<ZsxqComment[] | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  useEffect(() => { setFull(null); setOpen(false); setLoading(false); setErr(''); }, [item.id]);

  const preview = item.comments || [];
  const shown = full ?? preview;
  const total = Math.max(item.comments_count || 0, shown.length);
  if (!total) return null;
  const canLoadAll = !full && item.id && total > preview.length;

  const loadAll = async () => {
    if (loading || !item.id) return;
    setLoading(true);
    setErr('');
    try {
      const res = await getZsxqTopicComments(item.id);
      if (res.error || !res.comments.length) setErr(res.error || '评论加载失败，请稍后重试');
      else setFull(res.comments);
    } catch {
      setErr('评论加载失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  };

  if (!open) {
    return (
      <button type="button" className="tzs-cmts-toggle" onClick={() => setOpen(true)}>
        💬 {total} 条评论 · 展开
      </button>
    );
  }
  return (
    <div className="tzs-cmts">
      <div className="tzs-cmts-h">
        💬 评论 <span className="tzs-muted">{shown.length} / {total} 条</span>
        <button type="button" className="tzs-cmts-fold" onClick={() => setOpen(false)}>收起</button>
      </div>
      {shown.map((c, i) => <CommentRow key={i} c={c} />)}
      {canLoadAll && (
        <button type="button" className="tzs-cmts-more" onClick={loadAll} disabled={loading}>
          {loading ? '加载中…' : `加载全部 ${total} 条评论`}
        </button>
      )}
      {err && <div className="tzs-cmt-err">⚠ {err}</div>}
    </div>
  );
};

const COLLAPSE_CHARS = 420; // 长帖默认折叠的字符阈值

// 单条帖子卡片：作者/日期/精华 + 正文(长帖折叠) + 图片(可放大) + 评论 + 原文出处。
const TopicRow: React.FC<{ item: ZsxqTopic; onZoom: (url: string) => void }> = ({ item, onZoom }) => {
  const [expanded, setExpanded] = useState(false);
  const long = (item.text || '').length > COLLAPSE_CHARS;
  const bodyText = !long || expanded ? item.text : item.text.slice(0, COLLAPSE_CHARS).trimEnd() + '…';
  return (
    <div className="tzs-item">
      <div className="tzs-item-head">
        <span className="tzs-item-kind">机构纪要</span>
        {item.digested && <span className="tzs-item-badge">精华</span>}
        <span className="tzs-item-date">{fmtDateTime(item.create_time || item.date)}</span>
        {item.id && (item.text || '').trim() && (
          // 纯图片动态无正文→落地页会空,不给分享钮(且不外泄第三方图床)
          <ShareButton
            className="tzs-share"
            modalTitle="分享机构纪要"
            tooltip="分享这条机构纪要（仅标题+摘要，落地页不含全文）"
            simple
            target={() => noteShareTarget(item)}
          >分享</ShareButton>
        )}
      </div>

      {item.text && <div className="tzs-item-body">{renderBody(bodyText)}</div>}
      {long && (
        <button type="button" className="tzs-expand" onClick={() => setExpanded(v => !v)}>
          {expanded ? '收起 ▴' : '展开全文 ▾'}
        </button>
      )}

      {item.images.length > 0 && (
        <div className="tzs-imgs">
          {item.images.map((u, i) => (
            <button type="button" className="tzs-img-btn" key={i} onClick={() => onZoom((item.image_fulls && item.image_fulls[i]) || u)} title="点击放大">
              {/* 知识星球图床有 referer 防盗链：必须 no-referrer */}
              <img className="tzs-img" src={u} alt="" loading="lazy" referrerPolicy="no-referrer" />
            </button>
          ))}
        </div>
      )}

      <CommentsBlock item={item} />
    </div>
  );
};

const TerminalZsxqStream: React.FC<{ inline?: boolean }> = ({ inline = false }) => {
  const [data, setData] = useState<ZsxqStreamResponse | null>(null);
  const [pool, setPool] = useState<ZsxqTopic[]>([]);
  const [group, setGroup] = useState('');
  const [query, setQuery] = useState('');       // 输入框即时值
  const [applied, setApplied] = useState('');   // 已生效的搜索词（回车/按钮触发）
  const [loading, setLoading] = useState(true);
  const [more, setMore] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState('');
  const [zoom, setZoom] = useState<string | null>(null);
  const [fitWin, setFitWin] = useState(false);   // 灯箱:false=铺满宽度可竖向滚动看清长图(默认)/true=整图适应窗口
  const cursorRef = useRef('');
  const hasMoreRef = useRef(false);

  const load = useCallback(async (opts: { group?: string; q?: string; refresh?: boolean } = {}) => {
    setLoading(true);
    if (opts.refresh) setRefreshing(true);
    setErr('');
    try {
      const res = await getZsxqStream({ group: opts.group, q: opts.q, limit: 20, refresh: opts.refresh });
      setData(res);
      setPool(res.items);
      setGroup(res.group);
      cursorRef.current = res.next_before || '';
      hasMoreRef.current = Boolean(res.has_more);
    } catch (e: any) {
      const status = e?.response?.status;
      setErr(status === 403 ? '机构纪要暂未对你的账号开放' : '机构纪要加载失败，请稍后重试。');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { void load({}); }, [load]);

  const applySearch = useCallback((q: string) => {
    const kw = q.trim();
    setApplied(kw);
    void load({ group, q: kw || undefined });
  }, [group, load]);

  const switchGroup = useCallback((gid: string) => {
    if (gid === group) return;
    setGroup(gid);
    setQuery('');
    setApplied('');
    void load({ group: gid });
  }, [group, load]);

  const loadEarlier = useCallback(async () => {
    if (more || !hasMoreRef.current) return;
    setMore(true);
    try {
      const res = await getZsxqStream({
        group, q: applied || undefined, limit: 20,
        before: cursorRef.current || pool[pool.length - 1]?.create_time || '',
      });
      const known = new Set(pool.map(i => i.id));
      const fresh = res.items.filter(i => !known.has(i.id));
      if (fresh.length) setPool(prev => [...prev, ...fresh]);
      cursorRef.current = res.next_before || cursorRef.current;
      hasMoreRef.current = Boolean(res.has_more) && (fresh.length > 0 || Boolean(res.next_before));
    } catch {
      /* 失败保持现状，可重试 */
    } finally {
      setMore(false);
    }
  }, [more, group, applied, pool]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && zoom) { e.preventDefault(); setZoom(null); } };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [zoom]);

  const groups = data?.groups || [];

  const lightbox = zoom && (
    <div className="tzs-lightbox" onMouseDown={() => setZoom(null)} role="dialog" aria-label="查看图片">
      {/* 长图默认铺满宽度、可竖向滚动看清；点图切换「适应窗口」整图 */}
      <div className={'tzs-lightbox-scroll' + (fitWin ? ' fit' : '')} onMouseDown={e => e.stopPropagation()}>
        <img className={'tzs-lightbox-img' + (fitWin ? ' fit' : '')} src={zsxqImg(zoom)} alt=""
             onClick={() => setFitWin(v => !v)} title={fitWin ? '点击放大阅读' : '点击适应窗口'} />
      </div>
      <div className="tzs-lightbox-bar" onMouseDown={e => e.stopPropagation()}>
        <button className="tzs-lightbox-btn" onClick={() => setFitWin(v => !v)}>{fitWin ? '🔍 放大阅读' : '⤢ 适应窗口'}</button>
        <a className="tzs-lightbox-btn" href={zsxqImg(zoom, true)} download target="_blank" rel="noreferrer">⬇ 下载原图</a>
      </div>
      <button className="tzs-lightbox-x" onClick={() => setZoom(null)} aria-label="关闭">✕</button>
    </div>
  );

  return (
    <div className={inline ? 'tzs-inline' : 'tzs-wrap'}>
      <div className="tzs-bar">
        {groups.length > 1 && (
          <span className="tzs-groups">
            {groups.map(g => (
              <button key={g.id} type="button" className={`tzs-gchip ${g.id === group ? 'on' : ''}`} onClick={() => switchGroup(g.id)}>
                {g.name}
              </button>
            ))}
          </span>
        )}
        <span className="tzs-search">
          <input
            className="tzs-input"
            value={query}
            placeholder="🔍 搜机构纪要（回车检索）…"
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') applySearch(query); }}
          />
          {(query || applied) && (
            <button className="tzs-clear" aria-label="清除搜索" title="清除搜索" onClick={() => { setQuery(''); if (applied) applySearch(''); }}>✕</button>
          )}
        </span>
        <button className="tzs-refresh" onClick={() => load({ group, q: applied || undefined, refresh: true })} disabled={refreshing} title="刷新">
          {refreshing ? '刷新中…' : '⟳'}
        </button>
      </div>

      <div className="tzs-scroll">
        {loading && pool.length === 0 && <div className="tzs-loading">加载中…</div>}
        {err && pool.length === 0 && !loading && <div className="tzs-error">{err}</div>}
        {!loading && !err && pool.length === 0 && (
          <div className="tzs-empty">{applied ? `无「${applied}」相关帖子` : '暂无帖子'}</div>
        )}

        {pool.length > 0 && (
          <div className="tzs-list">
            {pool.map(it => <TopicRow key={it.id} item={it} onZoom={u => { setFitWin(false); setZoom(u); }} />)}
          </div>
        )}

        {pool.length > 0 && hasMoreRef.current && (
          <button type="button" className="tzs-more" onClick={loadEarlier} disabled={more}>
            {more ? '加载中…' : '加载更早 ▾'}
          </button>
        )}

        {pool.length > 0 && (
          <div className="tzs-disclaimer">⚠ 机构调研纪要聚合，仅供研究参考，不构成投资建议。</div>
        )}
      </div>

      {lightbox ? createPortal(lightbox, document.body) : null}
    </div>
  );
};

export default TerminalZsxqStream;
