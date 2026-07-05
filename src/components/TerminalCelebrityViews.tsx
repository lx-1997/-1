import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  CelebrityProfile,
  CelebrityViewComment,
  CelebrityViewItem,
  CelebrityViewsResponse,
  CelebrityDigestResponse,
  getCelebrityViews,
  getCelebrityDigest,
  getCelebrityComments,
  getCelebrityMore,
  resolveMediaUrl,
} from '../services/celebrityService';
import './TerminalCelebrityViews.css';

/**
 * 名人观点弹层——投资大佬 / 名人的公开观点（图片 + 文字 + 可播放语音 + 出处）。
 * 复用「人物专题」的头像墙 → 详情 + AI 综述交互，但做成终端原生(零 antd)、并支持富媒体。
 * 数据源可插拔：当前 curated(运营录入)即时可用；知识星球等来源由后端配置后自动并入。
 * ⚠️ 仅为公开信息聚合 / 示例展示，不构成投资建议。只读公开接口 /api/celebrity/*。
 */

const DQ_LABEL: Record<string, string> = { live: '● 已聚合', degraded: '○ 部分降级', mock: '○ 占位' };

const fmtDate = (v?: string | null): string => {
  if (!v) return '';
  const s = String(v);
  return s.length >= 10 ? s.slice(0, 10) : s;
};

// 头像：真人头像优先（加载失败回退 emoji + 字母），用 accent 描边恢复人物色彩身份。
const Avatar: React.FC<{ figure: CelebrityProfile; size: number }> = ({ figure, size }) => {
  const [failed, setFailed] = useState(false);
  const showImg = Boolean(figure.image) && !failed;
  return (
    <span
      className="tcv-ava"
      style={{
        width: size, height: size, fontSize: Math.round(size * 0.42),
        boxShadow: `0 0 0 2px ${figure.accent}, 0 2px 8px rgba(0,0,0,.3)`,
        background: `linear-gradient(135deg, ${figure.accent} 0%, ${figure.accent}aa 100%)`,
      }}
      title={figure.name}
    >
      {showImg ? (
        <img className="tcv-ava-img" src={resolveMediaUrl(figure.image)} alt={figure.name} loading="lazy" onError={() => setFailed(true)} />
      ) : (
        <span className="tcv-ava-glyph">{figure.avatar || figure.monogram}</span>
      )}
    </span>
  );
};

// 正文按段落渲染：「问：」「答：」拆成带标签的独立块，其余作普通段落——
// 让每轮提问/回答、每段都清晰分隔、好读（知识星球帖子多为问答体）。
const renderBody = (body: string): React.ReactNode => {
  const blocks = body.split(/\n+/).map(s => s.trim()).filter(Boolean);
  return blocks.map((b, i) => {
    const q = /^问\s*[:：]/.test(b);
    const a = /^答\s*[:：]/.test(b);
    if (q || a) {
      return (
        <div className={`tcv-qa ${q ? 'tcv-q' : 'tcv-a'}`} key={i}>
          <span className="tcv-qa-tag">{q ? '问' : '答'}</span>
          <span className="tcv-qa-text">{b.replace(/^[问答]\s*[:：]\s*/, '')}</span>
        </div>
      );
    }
    return <p className="tcv-para" key={i}>{b}</p>;
  });
};

// 一条评论：作者(+楼中楼「回复 @某人」) + 置顶/点赞 + 时间 + 文字。
const CommentRow: React.FC<{ c: CelebrityViewComment }> = ({ c }) => (
  <div className={`tcv-cmt ${c.reply_to ? 'tcv-cmt--reply' : ''}`}>
    <div className="tcv-cmt-head">
      <span className="tcv-cmt-author">{c.author || '星球成员'}</span>
      {c.reply_to && <span className="tcv-cmt-replyto">回复 @{c.reply_to}</span>}
      {c.sticky && <span className="tcv-cmt-pin">置顶</span>}
      <span className="tcv-cmt-date">{fmtDate(c.create_time)}</span>
      {(c.likes_count || 0) > 0 && <span className="tcv-cmt-likes">👍{c.likes_count}</span>}
    </div>
    <div className="tcv-cmt-text">{c.text}</div>
  </div>
);

// 帖子的评论区：随帖预览评论直接展示；评论更多时给「加载全部 N 条」按需拉全。
const CommentsBlock: React.FC<{ celebId: string; item: CelebrityViewItem }> = ({ celebId, item }) => {
  const [full, setFull] = useState<CelebrityViewComment[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  useEffect(() => { setFull(null); setLoading(false); setErr(''); }, [item.id]);

  const preview = item.comments || [];
  const shown = full ?? preview;
  const total = Math.max(item.comments_count || 0, shown.length);
  if (!total) return null;
  const canLoadAll = !full && item.topic_id && total > preview.length;

  const loadAll = async () => {
    if (loading || !item.topic_id) return;
    setLoading(true);
    setErr('');
    try {
      const res = await getCelebrityComments(celebId, item.topic_id);
      if (res.error || !res.comments.length) setErr(res.error || '评论加载失败，请稍后重试');
      else setFull(res.comments);
    } catch {
      setErr('评论加载失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="tcv-cmts">
      <div className="tcv-cmts-h">💬 评论 <span className="tcv-muted">{shown.length} / {total} 条</span></div>
      {shown.map((c, i) => <CommentRow key={i} c={c} />)}
      {canLoadAll && (
        <button type="button" className="tcv-cmts-more" onClick={loadAll} disabled={loading}>
          {loading ? '加载中…' : `加载全部 ${total} 条评论`}
        </button>
      )}
      {err && <div className="tcv-cmt-err">⚠ {err}</div>}
    </div>
  );
};

// 单条观点(帖子)：每条独立卡片，含顶栏(类型/精华/日期)+正文(问答分块)+图片(可放大)+语音+评论+出处。
const ViewRow: React.FC<{ celebId: string; item: CelebrityViewItem; onZoom: (url: string) => void }> = ({ celebId, item, onZoom }) => (
  <div className="tcv-item">
    <div className="tcv-item-head">
      {item.tags?.includes('精华') && <span className="tcv-item-badge">精华</span>}
      <span className="tcv-item-date">{fmtDate(item.reported_date || item.published_at)}</span>
    </div>
    {/* body 已含首行，故有 body 时不再单列 title(避免重复)；纯标题(无正文)才显示 title */}
    {item.body ? (
      <div className="tcv-item-body">{renderBody(item.body)}</div>
    ) : (
      <div className="tcv-item-title">{item.title}</div>
    )}

    {item.image_urls && item.image_urls.length > 0 && (
      <div className="tcv-imgs">
        {item.image_urls.map((u, i) => (
          <button type="button" className="tcv-img-btn" key={i} onClick={() => onZoom(resolveMediaUrl((item.image_fulls && item.image_fulls[i]) || u))} title="点击放大">
            {/* 列表小图(large)省流；点开放大用原图(image_fulls)。知识星球图床有 referer 防盗链：必须 no-referrer */}
            <img className="tcv-img" src={resolveMediaUrl(u)} alt="" loading="lazy" referrerPolicy="no-referrer" />
          </button>
        ))}
      </div>
    )}

    {item.audio_url && (
      <div className="tcv-audio">
        <span className="tcv-audio-ic">🔊</span>
        <audio className="tcv-audio-el" controls preload="none" src={resolveMediaUrl(item.audio_url)}>
          <track kind="captions" />
        </audio>
      </div>
    )}

    <CommentsBlock celebId={celebId} item={item} />

    <div className="tcv-item-meta">
      {item.source_name && <span>{item.source_name}</span>}
      {item.tags?.filter(t => t !== '精华').map(t => <span className="tcv-tag" key={t}>{t}</span>)}
      {item.source_url && (
        <a className="tcv-src" href={item.source_url} target="_blank" rel="noreferrer">原文 ↗</a>
      )}
    </div>
  </div>
);

const Detail: React.FC<{
  figure: CelebrityProfile;
  digest?: CelebrityDigestResponse;
  digestLoading: boolean;
  onBack: () => void;
  onZoom: (url: string) => void;
}> = ({ figure, digest, digestLoading, onBack, onZoom }) => {
  const digestText = digest?.digest || figure.digest;
  const provider = digest?.digest_provider || figure.digest_provider;
  const isAi = provider && !['template', 'fallback', ''].includes(provider);

  // 分页：每页 PAGE_SIZE 条；首屏 figure.items 之后按需经游标拉更早的帖子并入池。
  const PAGE_SIZE = 10;
  const [extra, setExtra] = useState<CelebrityViewItem[]>([]);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const cursorRef = useRef('');
  const listTopRef = useRef<HTMLDivElement>(null);
  useEffect(() => { setExtra([]); setPage(0); setLoading(false); setHasMore(true); cursorRef.current = ''; }, [figure.id]);

  const seenIds = new Set(figure.items.map(i => i.id));
  const pool = [...figure.items, ...extra.filter(i => !seenIds.has(i.id))];
  const hasZsxq = pool.some(i => i.source_type === 'zsxq');
  const loadedPages = Math.max(1, Math.ceil(pool.length / PAGE_SIZE));
  const visible = pool.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE);
  const moreAvail = hasMore && hasZsxq;          // 还能往更早翻
  const canPrev = page > 0;
  const canNext = (page + 1) < loadedPages || moreAvail;

  const scrollTop = () => { window.setTimeout(() => listTopRef.current?.scrollIntoView({ block: 'nearest' }), 0); };
  const goPrev = useCallback(() => { setPage(p => (p > 0 ? p - 1 : p)); scrollTop(); }, []);
  const goNext = useCallback(async () => {
    if ((page + 1) < loadedPages) { setPage(page + 1); scrollTop(); return; }
    if (!moreAvail || loading) return;
    setLoading(true);
    const last = pool[pool.length - 1];
    const cursor = cursorRef.current || last?.published_at || last?.reported_date || '';
    try {
      const res = await getCelebrityMore(figure.id, cursor, 20);
      const known = new Set(pool.map(i => i.id));
      const fresh = res.items.filter(i => !known.has(i.id));
      if (fresh.length) { setExtra(prev => [...prev, ...fresh]); setPage(page + 1); scrollTop(); }
      cursorRef.current = res.next_before || cursor;
      setHasMore(Boolean(res.has_more) && (fresh.length > 0 || Boolean(res.next_before)));
    } catch {
      /* 失败保持当前页，可重试 */
    } finally {
      setLoading(false);
    }
  }, [page, loadedPages, moreAvail, loading, pool, figure.id]);

  return (
    <div className="tcv-detail">
      <button type="button" className="tcv-back" onClick={onBack}>← 返回名人墙</button>

      <div className="tcv-detail-head" style={{ ['--accent' as any]: figure.accent }}>
        <Avatar figure={figure} size={72} />
        <div className="tcv-detail-id">
          <div className="tcv-detail-name">{figure.name}{figure.en_name && <span className="tcv-en">{figure.en_name}</span>}</div>
          <div className="tcv-detail-role">{[figure.role, figure.org].filter(Boolean).join(' · ')}</div>
          {figure.topics.length > 0 && (
            <div className="tcv-topics">{figure.topics.map(t => <span className="tcv-tag" key={t}>{t}</span>)}</div>
          )}
        </div>
      </div>

      {figure.bio && <div className="tcv-bio">{figure.bio}</div>}
      {figure.why_it_matters && <div className="tcv-why"><b>为何关注：</b>{figure.why_it_matters}</div>}

      <div className="tcv-digest">
        <div className="tcv-digest-h">
          💡 AI 观点综述
          {digestText && <span className={`tcv-badge ${isAi ? 'ai' : ''}`}>{isAi ? `AI 合成 · ${provider}` : '规则模板'}</span>}
        </div>
        {digestLoading && !digestText ? (
          <div className="tcv-muted">正在综述近期观点…</div>
        ) : digestText ? (
          <div className="tcv-digest-t">{digestText}</div>
        ) : (
          <div className="tcv-muted">暂无可综述的观点条目。</div>
        )}
        <div className="tcv-note">综述基于下方条目客观归纳，方向不可编造；仅供研究参考，不构成投资建议。</div>
      </div>

      <div className="tcv-list-h" ref={listTopRef}>观点条目 <span className="tcv-muted">共 {pool.length}{moreAvail ? '+' : ''} 条{figure.latest_date ? ` · 最新 ${fmtDate(figure.latest_date)}` : ''}</span></div>
      {visible.length > 0 ? (
        <div className="tcv-list">{visible.map(it => <ViewRow key={it.id} celebId={figure.id} item={it} onZoom={onZoom} />)}</div>
      ) : (
        <div className="tcv-empty">暂未录入该名人的观点条目。</div>
      )}

      {pool.length > 0 && (
        <div className="tcv-pager">
          <button type="button" className="tcv-pager-btn" onClick={goPrev} disabled={!canPrev}>‹ 上一页</button>
          <span className="tcv-pager-ind">第 {page + 1} / {loadedPages}{moreAvail ? '+' : ''} 页</span>
          <button type="button" className="tcv-pager-btn" onClick={goNext} disabled={!canNext || loading}>
            {loading ? '加载中…' : '下一页 ›'}
          </button>
        </div>
      )}

      {figure.image && figure.image_credit && <div className="tcv-credit">头像：{figure.image_credit}</div>}
    </div>
  );
};

const Card: React.FC<{ figure: CelebrityProfile; onSelect: (id: string) => void }> = ({ figure, onSelect }) => {
  const headline = figure.items[0];
  return (
    <button type="button" className="tcv-card" style={{ ['--accent' as any]: figure.accent }} onClick={() => onSelect(figure.id)}>
      <div className="tcv-card-top">
        <Avatar figure={figure} size={40} />
        <div className="tcv-card-id">
          <div className="tcv-card-name">{figure.name}</div>
          <div className="tcv-card-role">{[figure.role, figure.org].filter(Boolean).join(' · ')}</div>
        </div>
      </div>
      {headline ? (
        <div className="tcv-card-headline">“{headline.title}”</div>
      ) : (
        <div className="tcv-card-headline tcv-muted">暂无观点条目</div>
      )}
      <div className="tcv-card-foot">
        <span>{figure.item_count} 条观点</span>
        <span className="tcv-card-cta">查看 →</span>
      </div>
    </button>
  );
};

const TerminalCelebrityViews: React.FC<{ onClose?: () => void; inline?: boolean }> = ({ onClose, inline = false }) => {
  const [data, setData] = useState<CelebrityViewsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [digests, setDigests] = useState<Record<string, CelebrityDigestResponse>>({});
  const [digestLoadingId, setDigestLoadingId] = useState<string | null>(null);
  const [zoom, setZoom] = useState<string | null>(null);
  const firstRef = useRef(true);
  const digestInflight = useRef<Set<string>>(new Set());

  const load = useCallback(async (refresh = false) => {
    if (firstRef.current) setLoading(true);
    if (refresh) setRefreshing(true);
    try { setData(await getCelebrityViews({ refresh })); setErr(''); }
    catch { setErr('名人观点加载失败，请稍后重试。'); }
    finally { setLoading(false); setRefreshing(false); firstRef.current = false; }
  }, []);

  useEffect(() => { void load(false); }, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      if (zoom) setZoom(null);
      else if (selectedId) setSelectedId(null);
      else if (!inline) onClose?.();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, selectedId, zoom, inline]);

  const loadDigest = useCallback((id: string) => {
    if (!id || digests[id] || digestInflight.current.has(id)) return;
    digestInflight.current.add(id);
    setDigestLoadingId(id);
    getCelebrityDigest(id)
      .then(res => setDigests(prev => ({ ...prev, [res.id]: res })))
      .catch(() => { /* 综述失败不阻断详情，仍展示原始条目 */ })
      .finally(() => { digestInflight.current.delete(id); setDigestLoadingId(prev => (prev === id ? null : prev)); });
  }, [digests]);

  useEffect(() => { if (selectedId) loadDigest(selectedId); }, [selectedId, loadDigest]);

  const selected = selectedId && data ? data.figures.find(f => f.id === selectedId) : undefined;
  const dq = data?.data_quality?.level || 'degraded';

  const head = (
    <div className="tcv-head">
      <span className="tcv-head-ic">🗣️</span>
      <div className="tcv-head-tt">
        名人观点
        <span className="tcv-head-sub">投资大佬 / 名人的公开观点 · 图文 + 语音</span>
      </div>
      <span className={`tcv-dq tcv-dq--${dq}`}>{DQ_LABEL[dq] || '加载中'}</span>
      <button className="tcv-refresh" onClick={() => load(true)} disabled={refreshing} title="刷新">
        {refreshing ? '刷新中…' : '⟳ 刷新'}
      </button>
      {!inline && <button className="tcv-x" onClick={() => onClose?.()} aria-label="关闭">✕</button>}
    </div>
  );

  const scroll = (
    <div className="tcv-scroll">
      {loading && <div className="tcv-loading">加载中…</div>}
      {err && !data && <div className="tcv-error">{err}</div>}

      {data && !selected && (
        <>
          {data.warnings.length > 0 && <div className="tcv-banner">⚠ {data.warnings.slice(0, 2).join('；')}</div>}
          {data.figures.length > 0 ? (
            <div className="tcv-wall">
              {data.figures.map(f => <Card key={f.id} figure={f} onSelect={setSelectedId} />)}
            </div>
          ) : (
            <div className="tcv-empty">暂无名人观点，待运营录入或数据源接入。</div>
          )}
          <div className="tcv-disclaimer">⚠ {data.data_quality?.detail || '名人观点为公开信息聚合或示例展示，不构成任何投资建议。'}</div>
        </>
      )}

      {data && selected && (
        <Detail
          figure={selected}
          digest={digests[selected.id]}
          digestLoading={digestLoadingId === selected.id}
          onBack={() => setSelectedId(null)}
          onZoom={setZoom}
        />
      )}
    </div>
  );

  const lightbox = zoom && (
    <div className="tcv-lightbox" onMouseDown={() => setZoom(null)} role="dialog" aria-label="查看图片">
      <img className="tcv-lightbox-img" src={zoom} alt="" referrerPolicy="no-referrer" />
      <button className="tcv-lightbox-x" onClick={() => setZoom(null)} aria-label="关闭">✕</button>
    </div>
  );

  // 内联模式：直接嵌进资讯流面板(研报旁的「名人观点」标签),不走 portal/遮罩。
  if (inline) {
    return (
      <div className="tcv-inline">
        {head}
        {scroll}
        {lightbox ? createPortal(lightbox, document.body) : null}
      </div>
    );
  }

  return createPortal(
    <div className="tcv-overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose?.(); }}>
      <div className="tcv-modal" role="dialog" aria-modal="true" aria-label="名人观点">
        {head}
        {scroll}
      </div>
      {lightbox}
    </div>,
    document.body,
  );
};

export default TerminalCelebrityViews;
