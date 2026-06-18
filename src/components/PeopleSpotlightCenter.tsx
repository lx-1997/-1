import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  App as AntdApp,
  Button,
  Checkbox,
  Empty,
  Input,
  Popover,
  Segmented,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  ArrowLeftOutlined,
  BulbOutlined,
  CheckOutlined,
  ClockCircleOutlined,
  FireOutlined,
  HolderOutlined,
  LinkOutlined,
  RiseOutlined,
  RobotOutlined,
  SearchOutlined,
  SendOutlined,
  StarFilled,
  StarOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { formatGeneratedAt } from '../utils/datetime';
import DataCenter from './DataCenter';
import Markdown from './common/Markdown';
import CitableSources from './common/CitableSources';
import { pushRealtimeMessage } from '../services/eventService';
import { runGeneralChatStream, ChatCitationSource } from '../services/agentService';
import {
  aggregateTopicHeat,
  applyCustomOrder,
  buildSignalPayload,
  countTodayItems,
  matchesQuery,
  rankCrossFigureHeadlines,
  sortFigures,
  SortKey,
} from '../utils/peopleSpotlight';
import {
  getPeopleSpotlight,
  getPersonDigest,
  PeopleSpotlightResponse,
  PersonDigestResponse,
  PersonProfile,
  PersonVoiceItem,
} from '../services/peopleService';
import './PeopleSpotlightCenter.css';

const { Paragraph, Text, Title } = Typography;

const TAG_COLOR: Record<string, string> = {
  AI: 'geekblue',
  芯片: 'green',
  关税: 'volcano',
  政策: 'red',
  地缘: 'purple',
  资本: 'gold',
  产品: 'cyan',
  公司: 'blue',
};

const QUALITY_DOT: Record<string, string> = {
  live: '#16a34a',
  degraded: '#d97706',
  mock: '#dc2626',
};

const SORT_OPTIONS: Array<{ label: string; value: SortKey }> = [
  { label: '默认', value: 'default' },
  { label: '最新动态', value: 'latest' },
  { label: '发言数', value: 'count' },
];

const formatDate = (value?: string | null): string => {
  if (!value) return '--';
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format('MM-DD') : value;
};

const formatRelative = (value?: string | null): string => {
  if (!value) return '';
  const parsed = dayjs(value);
  if (!parsed.isValid()) return '';
  const days = dayjs().startOf('day').diff(parsed.startOf('day'), 'day');
  if (days <= 0) return '今天';
  if (days === 1) return '昨天';
  if (days <= 30) return `${days} 天前`;
  return parsed.format('YYYY-MM-DD');
};

// 已推送条目去重：持久化到 localStorage，跨会话记住，避免重复推送刷屏信号流。
const PUSHED_STORAGE_KEY = 'dfx_people_pushed_v1';

const loadPushedIds = (): Set<string> => {
  try {
    const raw = window.localStorage.getItem(PUSHED_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(parsed) ? parsed : []);
  } catch {
    return new Set();
  }
};

export interface SignalPusher {
  push: (figure: PersonProfile, item: PersonVoiceItem) => void;
  pushQuiet: (figure: PersonProfile, item: PersonVoiceItem) => Promise<boolean>;
  pushingId: string | null;
  isPushed: (id: string) => boolean;
}

// 推送到信号流的共享 hook：管理 pushing/已推状态 + 成功/失败提示 + 去重。
const usePushToSignal = (): SignalPusher => {
  const { message } = AntdApp.useApp();
  const [pushingId, setPushingId] = useState<string | null>(null);
  const [pushedIds, setPushedIds] = useState<Set<string>>(loadPushedIds);

  const isPushed = useCallback((id: string) => pushedIds.has(id), [pushedIds]);

  const recordPushed = useCallback((id: string) => {
    setPushedIds(prev => {
      if (prev.has(id)) return prev;
      const next = new Set(prev);
      next.add(id);
      try {
        window.localStorage.setItem(PUSHED_STORAGE_KEY, JSON.stringify(Array.from(next).slice(-500)));
      } catch {
        /* 存储不可用时静默降级 */
      }
      return next;
    });
  }, []);

  // 静默推送（不弹单条提示），供「跟踪」自动入库用；返回是否真正推送。
  const pushQuiet = useCallback(async (figure: PersonProfile, item: PersonVoiceItem): Promise<boolean> => {
    if (pushedIds.has(item.id)) return false;
    try {
      await pushRealtimeMessage(buildSignalPayload(figure, item));
      recordPushed(item.id);
      return true;
    } catch {
      return false;
    }
  }, [pushedIds, recordPushed]);

  const push = useCallback(async (figure: PersonProfile, item: PersonVoiceItem) => {
    if (pushedIds.has(item.id)) {
      message.info('该条已推送过');
      return;
    }
    setPushingId(item.id);
    try {
      await pushRealtimeMessage(buildSignalPayload(figure, item));
      recordPushed(item.id);
      message.success('已推送到信号流');
    } catch (error: any) {
      message.error(error?.message || '推送失败');
    } finally {
      setPushingId(null);
    }
  }, [message, pushedIds, recordPushed]);

  return { push, pushQuiet, pushingId, isPushed };
};

// 推送按钮的统一态：未推 → 发送图标；推送中 → Spin；已推 → 勾 + 禁用。
const PushButton: React.FC<{
  className: string;
  pushing: boolean;
  pushed: boolean;
  onPush: () => void;
  withLabel?: boolean;
}> = ({ className, pushing, pushed, onPush, withLabel }) => (
  <Tooltip title={pushed ? '已推送到信号流' : '推送到信号流'}>
    <button
      type="button"
      className={`${className}${pushed ? ' pushed' : ''}`}
      disabled={pushing || pushed}
      onClick={e => {
        e.stopPropagation();
        onPush();
      }}
      aria-label={pushed ? '已推送到信号流' : '推送到信号流'}
    >
      {pushing ? <Spin size="small" /> : pushed ? <><CheckOutlined />{withLabel ? ' 已推' : ''}</> : <><SendOutlined />{withLabel ? ' 推送' : ''}</>}
    </button>
  </Tooltip>
);

// 关注列表：存「隐藏」的人物 id（而非可见 id），这样后续往池子里加人时默认可见、不被旧记录埋掉。
const HIDDEN_STORAGE_KEY = 'dfx_people_hidden_v1';

const loadHiddenIds = (): Set<string> => {
  try {
    const raw = window.localStorage.getItem(HIDDEN_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(parsed) ? parsed : []);
  } catch {
    return new Set();
  }
};

const saveHiddenIds = (ids: Set<string>) => {
  try {
    window.localStorage.setItem(HIDDEN_STORAGE_KEY, JSON.stringify(Array.from(ids)));
  } catch {
    /* 存储不可用时静默降级 */
  }
};

// 跟踪：把人物设为长期跟踪，有新发言时自动进信号流。tracked=被跟踪的人物 id；
// seen=已纳入跟踪基线/已自动推过的条目 id（基线避免首次跟踪倒灌历史）；order=人物墙自定义顺序。
const TRACKED_STORAGE_KEY = 'dfx_people_tracked_v1';
const SEEN_STORAGE_KEY = 'dfx_people_seen_v1';
const ORDER_STORAGE_KEY = 'dfx_people_order_v1';

const loadIdSet = (key: string): Set<string> => {
  try {
    const raw = window.localStorage.getItem(key);
    const parsed = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(parsed) ? parsed : []);
  } catch {
    return new Set();
  }
};

const saveIdSet = (key: string, ids: Set<string>) => {
  try {
    window.localStorage.setItem(key, JSON.stringify(Array.from(ids).slice(-2000)));
  } catch {
    /* 存储不可用时静默降级 */
  }
};

const loadOrder = (): string[] => {
  try {
    const raw = window.localStorage.getItem(ORDER_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter(x => typeof x === 'string') : [];
  } catch {
    return [];
  }
};

const saveOrder = (order: string[]) => {
  try {
    window.localStorage.setItem(ORDER_STORAGE_KEY, JSON.stringify(order));
  } catch {
    /* 存储不可用时静默降级 */
  }
};

const QualityDot: React.FC<{ level?: string; label?: string; detail?: string }> = ({ level = 'live', label, detail }) => (
  <Tooltip title={detail || label || ''}>
    <span className="people-quality-dot" style={{ background: QUALITY_DOT[level] || QUALITY_DOT.live }} />
  </Tooltip>
);

const PUBLIC_URL = process.env.PUBLIC_URL || '';

const Avatar: React.FC<{ figure: PersonProfile; size: number }> = ({ figure, size }) => {
  // 真人头像优先（本地打包于 public/people/）；加载失败回退到 emoji + 字母底纹。
  const [imgFailed, setImgFailed] = useState(false);
  const showImage = Boolean(figure.image) && !imgFailed;
  return (
    <span
      className="people-avatar"
      style={{
        width: size,
        height: size,
        fontSize: Math.round(size * 0.42),
        // 用人物 accent 给头像描一圈彩环，恢复每人的色彩身份。
        boxShadow: `0 0 0 2px ${figure.accent}, 0 2px 8px rgba(0, 0, 0, 0.25)`,
        background: `linear-gradient(135deg, ${figure.accent} 0%, ${figure.accent}aa 100%)`,
      }}
      title={figure.name}
    >
      {showImage ? (
        <img
          className="people-avatar-img"
          src={`${PUBLIC_URL}${figure.image}`}
          alt={figure.name}
          loading="lazy"
          onError={() => setImgFailed(true)}
        />
      ) : (
        <>
          <span className="people-avatar-glyph" aria-hidden>{figure.avatar || figure.monogram}</span>
          <span className="people-avatar-monogram" aria-hidden>{figure.monogram}</span>
        </>
      )}
    </span>
  );
};

const renderTags = (tags: string[]) => (
  <Space size={4} wrap>
    {tags.map(tag => (
      <Tag key={tag} color={TAG_COLOR[tag] || 'default'} className="people-tag">{tag}</Tag>
    ))}
  </Space>
);

// 跟踪开关（星标）：开启后该人物有新发言时自动进信号流。
const TrackButton: React.FC<{ tracked: boolean; onToggle: () => void; className?: string; withLabel?: boolean }> = ({
  tracked,
  onToggle,
  className,
  withLabel,
}) => (
  <Tooltip title={tracked ? '已跟踪：有新发言自动进信号流（点击取消）' : '跟踪：有新发言自动推送到信号流'}>
    <button
      type="button"
      className={`people-track-btn${tracked ? ' tracked' : ''}${className ? ` ${className}` : ''}`}
      onClick={e => {
        e.stopPropagation();
        onToggle();
      }}
      aria-label={tracked ? '取消跟踪' : '跟踪'}
    >
      {tracked ? <StarFilled /> : <StarOutlined />}
      {withLabel ? <span className="people-track-label">{tracked ? '已跟踪' : '跟踪'}</span> : null}
    </button>
  </Tooltip>
);

const VoiceRow: React.FC<{
  item: PersonVoiceItem;
  pushing: boolean;
  pushed: boolean;
  onPush: (item: PersonVoiceItem) => void;
}> = ({ item, pushing, pushed, onPush }) => (
  <div className="people-voice-row">
    <a className="people-voice-main" href={item.url} target="_blank" rel="noreferrer">
      <Text strong className="people-voice-title">{item.title}</Text>
      <div className="people-voice-meta">
        <span className="people-voice-source">{item.source_name}</span>
        <span className="people-voice-dot">·</span>
        <ClockCircleOutlined />
        <span>{formatDate(item.reported_date || item.published_at)}</span>
        <span className="people-voice-rel">{formatRelative(item.reported_date || item.published_at)}</span>
        {item.importance_score >= 78 && (
          <span className="people-voice-heat"><FireOutlined /> {item.importance_score}</span>
        )}
        {item.tags.length > 0 && <span className="people-voice-tags">{renderTags(item.tags)}</span>}
      </div>
    </a>
    <div className="people-voice-actions">
      <PushButton className="people-voice-push" pushing={pushing} pushed={pushed} onPush={() => onPush(item)} />
      <a className="people-voice-open" href={item.url} target="_blank" rel="noreferrer">
        原文 <LinkOutlined />
      </a>
    </div>
  </div>
);

const humanizeAgentError = (raw?: string): string => {
  const text = (raw || '').toLowerCase();
  if (/connection|timeout|timed out|网络|econnrefused|fetch/.test(text)) return '模型连接中断了，点重试再试一次。';
  if (/40[13]|unauthorized|api key|key/.test(text)) return '云模型未配置或鉴权失败，请在「设置 → 模型配置」接入。';
  return raw || 'AI 服务暂时不可用，请重试。';
};

// Agent 深读：把该人物近期发言喂给分析师 Agent，流式出买方研判（不同于确定性 digest）。
const AgentReadPanel: React.FC<{ figure: PersonProfile }> = ({ figure }) => {
  const [started, setStarted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [text, setText] = useState('');
  const [status, setStatus] = useState('');
  const [sources, setSources] = useState<ChatCitationSource[]>([]);
  const [error, setError] = useState('');
  const cancelRef = useRef<null | (() => void)>(null);

  useEffect(() => () => cancelRef.current?.(), []);

  const run = useCallback(() => {
    if (loading || !figure.items.length) return;
    const headlines = figure.items.slice(0, 12).map(it => `- ${it.title}（${it.source_name}）`).join('\n');
    const prompt =
      `你是买方投研分析师。请基于「${figure.name}（${figure.role} · ${figure.org}）」近期的公开报道标题，研判其近期动向：\n` +
      `① 近期在做什么、释放了什么信号；② 对哪些标的或主题构成催化或风险（给方向与逻辑）；③ 接下来最该盯的 1-2 个验证点。\n` +
      `要求：只基于下面出现的事实归纳、不编造数字、不写免责声明、分点简洁、控制在 250 字内。\n\n近期报道：\n${headlines}`;
    setStarted(true);
    setLoading(true);
    setText('');
    setSources([]);
    setError('');
    setStatus('正在研判近期动向…');
    cancelRef.current = runGeneralChatStream(
      { message: prompt, locale: 'zh-CN' },
      {
        onDelta: t => setText(prev => prev + t),
        onSources: s => setSources(s),
        onStatus: s => setStatus(s),
        onDone: () => { setLoading(false); setStatus(''); cancelRef.current = null; },
        onError: e => { setError(humanizeAgentError(e)); setLoading(false); setStatus(''); cancelRef.current = null; },
      }
    );
  }, [figure, loading]);

  const stop = useCallback(() => {
    cancelRef.current?.();
    cancelRef.current = null;
    setLoading(false);
    setStatus('');
  }, []);

  return (
    <div className="people-agent">
      <div className="people-agent-head">
        <RobotOutlined />
        <span>Agent 深读近期动向</span>
        {started && (loading
          ? <button type="button" className="people-agent-ctrl" onClick={stop}>停止</button>
          : <button type="button" className="people-agent-ctrl" onClick={run}>重新解读</button>)}
      </div>

      {!started ? (
        <button type="button" className="people-agent-cta" onClick={run} disabled={!figure.items.length}>
          <RobotOutlined /> 让分析师 Agent 解读「{figure.name}」的近期动向
        </button>
      ) : error ? (
        <div className="people-agent-error">
          {error} <button type="button" className="people-agent-ctrl" onClick={run}>重试</button>
        </div>
      ) : (
        <>
          {text ? (
            <Markdown
              content={text}
              citations={sources.map(s => s.n)}
              onCitationClick={n => {
                const s = sources.find(x => x.n === n);
                if (s?.url) window.open(s.url, '_blank', 'noopener');
              }}
            />
          ) : (
            <span className="people-agent-thinking">{status || '思考中…'}</span>
          )}
          <CitableSources sources={sources} />
          {text && <div className="people-digest-note">AI 基于公开报道的研判，仅供研究参考，不构成投资建议；请以原文来源为准。</div>}
        </>
      )}
    </div>
  );
};

const PersonDetail: React.FC<{
  figure: PersonProfile;
  digest?: PersonDigestResponse;
  digestLoading: boolean;
  pusher: SignalPusher;
  tracked: boolean;
  onToggleTrack: () => void;
  onBack: () => void;
}> = ({ figure, digest, digestLoading, pusher, tracked, onToggleTrack, onBack }) => {
  const [activeTag, setActiveTag] = useState<string | null>(null);
  const { push, pushingId, isPushed } = pusher;

  const digestText = digest?.digest || figure.digest;
  const digestProvider = digest?.digest_provider || figure.digest_provider;
  const isAi = digestProvider && !['template', 'fallback', ''].includes(digestProvider);

  // 该人物近期条目的主题标签分布（按出现次数降序），供下方按看点过滤。
  const tagCounts = useMemo(() => {
    const counts = new Map<string, number>();
    figure.items.forEach(item => item.tags.forEach(tag => counts.set(tag, (counts.get(tag) || 0) + 1)));
    return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);
  }, [figure.items]);

  const visibleItems = activeTag ? figure.items.filter(item => item.tags.includes(activeTag)) : figure.items;

  return (
    <div className="people-detail">
      <button type="button" className="people-back" onClick={onBack}>
        <ArrowLeftOutlined /> 返回人物墙
      </button>

      <div className="people-detail-head" style={{ ['--accent' as any]: figure.accent }}>
        <Avatar figure={figure} size={84} />
        <div className="people-detail-id">
          <Title level={3} className="people-detail-name">
            {figure.name}
            <span className="people-detail-en">{figure.en_name}</span>
          </Title>
          <Text type="secondary">{figure.role} · {figure.org}</Text>
          <div className="people-detail-topics">{renderTags(figure.topics)}</div>
        </div>
        <div className="people-detail-head-actions">
          <TrackButton tracked={tracked} onToggle={onToggleTrack} className="people-track-detail" withLabel />
          <div className="people-detail-quality">
            <QualityDot level={figure.data_quality?.level} label={figure.data_quality?.label} detail={figure.data_quality?.detail} />
            <Text type="secondary">{figure.data_quality?.label || '实时聚合'}</Text>
          </div>
        </div>
      </div>

      <Paragraph className="people-detail-bio">{figure.bio}</Paragraph>

      <div className="people-why">
        <RiseOutlined />
        <span><strong>为何关注：</strong>{figure.why_it_matters}</span>
      </div>

      <div className="people-digest">
        <div className="people-digest-head">
          <BulbOutlined />
          <span>AI 近期观点综述</span>
          {digestText && (
            <Tag color={isAi ? 'purple' : 'default'} className="people-digest-badge">
              {isAi ? `AI 合成 · ${digestProvider}` : '规则模板兜底'}
            </Tag>
          )}
        </div>
        {digestLoading && !digestText ? (
          <div className="people-digest-loading"><Spin size="small" /> <span>正在综述近期观点…</span></div>
        ) : digestText ? (
          <Paragraph className="people-digest-text">{digestText}</Paragraph>
        ) : (
          <Text type="secondary">暂无可综述的近期观点。</Text>
        )}
        <Text type="secondary" className="people-digest-note">
          综述基于下方公开报道客观归纳，方向不可编造；仅供研究参考，不构成投资建议，请以原文来源为准。
        </Text>
      </div>

      <AgentReadPanel figure={figure} />

      <div className="people-voice-head">
        <Title level={5}>近期发言 / 相关报道</Title>
        <Text type="secondary">
          {activeTag ? `${visibleItems.length} / ${figure.item_count} 条` : `共 ${figure.item_count} 条`}
          {' · '}最新 {formatDate(figure.latest_date)}
        </Text>
      </div>

      {tagCounts.length > 0 && (
        <div className="people-voice-filter">
          <button
            type="button"
            className={`people-filter-chip${activeTag === null ? ' active' : ''}`}
            onClick={() => setActiveTag(null)}
          >
            全部 {figure.item_count}
          </button>
          {tagCounts.map(([tag, count]) => (
            <button
              key={tag}
              type="button"
              className={`people-filter-chip${activeTag === tag ? ' active' : ''}`}
              onClick={() => setActiveTag(prev => (prev === tag ? null : tag))}
            >
              {tag} {count}
            </button>
          ))}
        </div>
      )}

      {figure.warnings.length > 0 && (
        <Alert type="warning" showIcon className="people-detail-alert" message={figure.warnings.slice(0, 2).join('；')} />
      )}

      {visibleItems.length > 0 ? (
        <div className="people-voice-list">
          {visibleItems.map(item => (
            <VoiceRow
              key={item.id}
              item={item}
              pushing={pushingId === item.id}
              pushed={isPushed(item.id)}
              onPush={it => push(figure, it)}
            />
          ))}
        </div>
      ) : (
        <Empty description={activeTag ? `没有「${activeTag}」相关的发言` : '暂未取到该人物的近期公开报道，请稍后刷新'} />
      )}

      {figure.image && (
        <Text type="secondary" className="people-credit">
          头像：{figure.image_credit || 'Wikimedia Commons'}（自由许可）
        </Text>
      )}
    </div>
  );
};

// 今日跨人物要闻：把全部人物的近期发言拉平，按时效×影响力排出头条，可直接推送/溯源。
const TopNews: React.FC<{
  figures: PersonProfile[];
  onSelect: (id: string) => void;
  pusher: SignalPusher;
}> = ({ figures, onSelect, pusher }) => {
  const { push, pushingId, isPushed } = pusher;
  const headlines = useMemo(() => rankCrossFigureHeadlines(figures), [figures]);

  if (!headlines.length) return null;

  return (
    <div className="people-topnews">
      <div className="people-topnews-head">
        <FireOutlined />
        <span>今日跨人物要闻</span>
        <Text type="secondary" className="people-topnews-sub">按时效 × 影响力跨 {figures.length} 人聚合</Text>
      </div>
      <div className="people-topnews-track">
        {headlines.map(({ figure, item }) => (
          <div key={item.id} className="people-topnews-card" style={{ ['--accent' as any]: figure.accent }}>
            <button type="button" className="people-topnews-who" onClick={() => onSelect(figure.id)} title={`查看 ${figure.name}`}>
              <Avatar figure={figure} size={26} />
              <span className="people-topnews-who-name">{figure.name}</span>
              {item.importance_score >= 78 && (
                <span className="people-voice-heat"><FireOutlined /> {item.importance_score}</span>
              )}
            </button>
            <a className="people-topnews-title" href={item.url} target="_blank" rel="noreferrer" title={item.title}>
              {item.title}
            </a>
            <div className="people-topnews-foot">
              <Text type="secondary" className="people-topnews-meta" ellipsis>
                {item.source_name} · {formatRelative(item.reported_date || item.published_at)}
              </Text>
              <PushButton
                className="people-voice-push people-topnews-push"
                pushing={pushingId === item.id}
                pushed={isPushed(item.id)}
                onPush={() => push(figure, item)}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

const PersonCard: React.FC<{
  figure: PersonProfile;
  onSelect: (id: string) => void;
  onPrefetch: (id: string) => void;
  pusher: SignalPusher;
  tracked: boolean;
  onToggleTrack: () => void;
  today: string;
}> = ({ figure, onSelect, onPrefetch, pusher, tracked, onToggleTrack, today }) => {
  const { push, pushingId, isPushed } = pusher;
  const headline = figure.items[0];
  const todayCount = countTodayItems(figure, today);
  const open = () => onSelect(figure.id);
  return (
    <div
      role="button"
      tabIndex={0}
      className="people-card"
      style={{ ['--accent' as any]: figure.accent }}
      onClick={open}
      onMouseEnter={() => onPrefetch(figure.id)}
      onFocus={() => onPrefetch(figure.id)}
      onKeyDown={e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          open();
        }
      }}
    >
      <div className="people-card-top">
        <Avatar figure={figure} size={38} />
        <div className="people-card-id">
          <div className="people-card-name">
            {figure.name}
            <QualityDot level={figure.data_quality?.level} label={figure.data_quality?.label} detail={figure.data_quality?.detail} />
          </div>
          <Text type="secondary" className="people-card-role">{figure.role} · {figure.org}</Text>
        </div>
        <TrackButton tracked={tracked} onToggle={onToggleTrack} className="people-track-card" />
      </div>
      <div className="people-card-topics">{renderTags(figure.topics)}</div>
      {headline ? (
        <div className="people-card-headline">
          <Text className="people-card-headline-text" ellipsis={{ tooltip: headline.title }}>“{headline.title}”</Text>
          <Text type="secondary" className="people-card-headline-meta">
            {headline.source_name} · {formatRelative(headline.reported_date || headline.published_at)}
          </Text>
        </div>
      ) : (
        <div className="people-card-headline people-card-headline-empty">
          <Text type="secondary">暂无近期报道，点开刷新</Text>
        </div>
      )}
      <div className="people-card-foot">
        <Text type="secondary" className="people-card-count">
          {figure.item_count} 条近期发言
          {todayCount > 0 && <span className="people-card-today">今日 +{todayCount}</span>}
        </Text>
        <span className="people-card-foot-actions">
          {headline && (
            <PushButton
              className="people-card-push"
              pushing={pushingId === headline.id}
              pushed={isPushed(headline.id)}
              onPush={() => push(figure, headline)}
              withLabel
            />
          )}
          <span className="people-card-cta">查看 →</span>
        </span>
      </div>
    </div>
  );
};

// 跟踪自动入库：被跟踪人物出现「未见过」的发言时，自动静默推送到信号流（基线之后的新条目）。
const AutoTracker: React.FC<{
  figures: PersonProfile[];
  trackedIds: Set<string>;
  seenRef: React.MutableRefObject<Set<string>>;
  pushQuiet: (figure: PersonProfile, item: PersonVoiceItem) => Promise<boolean>;
}> = ({ figures, trackedIds, seenRef, pushQuiet }) => {
  const { message } = AntdApp.useApp();
  const pushQuietRef = useRef(pushQuiet);
  pushQuietRef.current = pushQuiet;

  useEffect(() => {
    if (trackedIds.size === 0) return;
    let cancelled = false;
    (async () => {
      const deltas: Array<{ figure: PersonProfile; item: PersonVoiceItem }> = [];
      for (const figure of figures) {
        if (!trackedIds.has(figure.id)) continue;
        for (const item of figure.items) {
          if (!seenRef.current.has(item.id)) deltas.push({ figure, item });
        }
      }
      if (!deltas.length) return;
      let pushed = 0;
      for (const { figure, item } of deltas) {
        if (cancelled) break;
        const ok = await pushQuietRef.current(figure, item);
        seenRef.current.add(item.id);
        if (ok) pushed += 1;
      }
      saveIdSet(SEEN_STORAGE_KEY, seenRef.current);
      if (!cancelled && pushed > 0) {
        message.success(`跟踪：已自动推送 ${pushed} 条新发言到信号流`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [figures, trackedIds, seenRef, message]);

  return null;
};

// 关注管理面板：勾选要展示的焦点人物 + 拖拽排序，存 localStorage。
const WatchlistManager: React.FC<{
  pool: PersonProfile[];
  hiddenIds: Set<string>;
  shownCount: number;
  onToggle: (id: string) => void;
  onShowAll: () => void;
  onReorder: (orderedIds: string[]) => void;
  hasCustomOrder: boolean;
  onResetOrder: () => void;
}> = ({ pool, hiddenIds, shownCount, onToggle, onShowAll, onReorder, hasCustomOrder, onResetOrder }) => {
  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);

  const handleDrop = (targetId: string) => {
    if (dragId && dragId !== targetId) {
      const ids = pool.map(f => f.id);
      const from = ids.indexOf(dragId);
      const to = ids.indexOf(targetId);
      if (from >= 0 && to >= 0) {
        ids.splice(to, 0, ids.splice(from, 1)[0]);
        onReorder(ids);
      }
    }
    setDragId(null);
    setOverId(null);
  };

  const panel = (
    <div className="people-watch-panel">
      <div className="people-watch-panel-head">
        <span>展示 {shownCount} / {pool.length} 位 · 拖动排序</span>
        <span className="people-watch-head-actions">
          {hasCustomOrder && (
            <button type="button" className="people-watch-all" onClick={onResetOrder}>重置顺序</button>
          )}
          <button type="button" className="people-watch-all" onClick={onShowAll} disabled={hiddenIds.size === 0}>
            全选
          </button>
        </span>
      </div>
      <div className="people-watch-list">
        {pool.map(figure => (
          <div
            key={figure.id}
            className={`people-watch-item${overId === figure.id ? ' drag-over' : ''}${dragId === figure.id ? ' dragging' : ''}`}
            draggable
            onDragStart={() => setDragId(figure.id)}
            onDragOver={e => { e.preventDefault(); setOverId(figure.id); }}
            onDragLeave={() => setOverId(prev => (prev === figure.id ? null : prev))}
            onDrop={() => handleDrop(figure.id)}
            onDragEnd={() => { setDragId(null); setOverId(null); }}
          >
            <HolderOutlined className="people-watch-handle" />
            <Checkbox checked={!hiddenIds.has(figure.id)} onChange={() => onToggle(figure.id)} />
            <Avatar figure={figure} size={24} />
            <span className="people-watch-name">{figure.name}</span>
          </div>
        ))}
      </div>
    </div>
  );
  return (
    <Popover content={panel} trigger="click" placement="bottomRight" title={null}>
      <Button size="small" icon={<TeamOutlined />}>
        关注 {shownCount}/{pool.length}
      </Button>
    </Popover>
  );
};

const PeopleSpotlightCenter: React.FC = () => {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [digests, setDigests] = useState<Record<string, PersonDigestResponse>>({});
  const [digestLoadingId, setDigestLoadingId] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<SortKey>('default');
  const [hiddenIds, setHiddenIds] = useState<Set<string>>(loadHiddenIds);
  const [trackedIds, setTrackedIds] = useState<Set<string>>(() => loadIdSet(TRACKED_STORAGE_KEY));
  const [order, setOrder] = useState<string[]>(loadOrder);
  const seenRef = useRef<Set<string>>(loadIdSet(SEEN_STORAGE_KEY));
  const pusher = usePushToSignal();

  const isTracked = useCallback((id: string) => trackedIds.has(id), [trackedIds]);

  // 跟踪开关：开启时把该人物当前条目纳入「已见」基线（避免倒灌历史），仅之后的新发言自动入库。
  const toggleTrack = useCallback((figure: PersonProfile) => {
    setTrackedIds(prev => {
      const next = new Set(prev);
      if (next.has(figure.id)) {
        next.delete(figure.id);
      } else {
        next.add(figure.id);
        figure.items.forEach(item => seenRef.current.add(item.id));
        saveIdSet(SEEN_STORAGE_KEY, seenRef.current);
      }
      saveIdSet(TRACKED_STORAGE_KEY, next);
      return next;
    });
  }, []);

  const reorderFigures = useCallback((orderedIds: string[]) => {
    setOrder(orderedIds);
    saveOrder(orderedIds);
    setSort('default');
  }, []);

  const resetOrder = useCallback(() => {
    setOrder([]);
    saveOrder([]);
  }, []);

  const toggleHidden = useCallback((id: string) => {
    setHiddenIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      saveHiddenIds(next);
      return next;
    });
  }, []);

  const showAllFigures = useCallback(() => {
    setHiddenIds(() => {
      const next = new Set<string>();
      saveHiddenIds(next);
      return next;
    });
  }, []);

  // 首次进入用缓存（秒开）；之后点「刷新发言」强制 refresh=true 拉新，
  // 这样若某人物上次瞬时降级，手动刷新能真正重试、不被缓存挡住。
  const initialLoad = useRef(true);
  const fetchData = useCallback(() => {
    const refresh = !initialLoad.current;
    initialLoad.current = false;
    return getPeopleSpotlight({ refresh });
  }, []);

  // 懒加载某人物的 AI 观点综述（已缓存或在途则跳过）。选中时触发，hover 卡片也预取，
  // 这样点开详情时综述多半已就绪、秒显。
  const prefetchingRef = useRef<Set<string>>(new Set());
  const loadDigest = useCallback((id: string) => {
    if (!id || digests[id] || prefetchingRef.current.has(id)) {
      return;
    }
    prefetchingRef.current.add(id);
    setDigestLoadingId(id);
    getPersonDigest(id)
      .then(res => setDigests(prev => ({ ...prev, [res.id]: res })))
      .catch(() => {
        /* 综述失败不阻断主流程，详情页仍展示原始条目 */
      })
      .finally(() => {
        prefetchingRef.current.delete(id);
        setDigestLoadingId(prev => (prev === id ? null : prev));
      });
  }, [digests]);

  useEffect(() => {
    if (selectedId) {
      loadDigest(selectedId);
    }
  }, [selectedId, loadDigest]);

  const subtitle = useMemo(
    () => '聚合全球科技与政经焦点人物的近期公开发言与观点，覆盖 AI、芯片、政策与资本主线，每条均带原文出处与时间。',
    []
  );

  return (
    <DataCenter<PeopleSpotlightResponse>
      className="people-spotlight-page"
      title="人物专题"
      subtitle={subtitle}
      fetchData={fetchData}
      refreshLabel="刷新发言"
      emptyText="正在聚合焦点人物近期发言"
      renderContent={(data) => {
        const tracker = (
          <AutoTracker
            figures={data.figures}
            trackedIds={trackedIds}
            seenRef={seenRef}
            pushQuiet={pusher.pushQuiet}
          />
        );

        const selected = selectedId ? data.figures.find(f => f.id === selectedId) : undefined;
        if (selected) {
          return (
            <>
              {tracker}
              <PersonDetail
                key={selected.id}
                figure={selected}
                digest={digests[selected.id]}
                digestLoading={digestLoadingId === selected.id}
                pusher={pusher}
                tracked={isTracked(selected.id)}
                onToggleTrack={() => toggleTrack(selected)}
                onBack={() => setSelectedId(null)}
              />
            </>
          );
        }

        const pool = applyCustomOrder(data.figures, order);
        const watchlisted = pool.filter(f => !hiddenIds.has(f.id));
        const visible = sortFigures(watchlisted.filter(f => matchesQuery(f, query)), sort);
        const today = dayjs().format('YYYY-MM-DD');
        const topicHeat = aggregateTopicHeat(watchlisted, today);

        return (
          <>
            {tracker}
            {data.warnings.length > 0 && (
              <Alert
                type="warning"
                showIcon
                className="people-wall-alert"
                message={data.warnings.slice(0, 2).join('；')}
              />
            )}

            {!query && watchlisted.length > 0 && (
              <TopNews figures={watchlisted} onSelect={setSelectedId} pusher={pusher} />
            )}

            {topicHeat.length > 0 && (
              <div className="people-heat">
                <span className="people-heat-label"><FireOutlined /> 今日热点</span>
                {topicHeat.map(({ tag, count }) => (
                  <button
                    key={tag}
                    type="button"
                    className={`people-heat-chip${query === tag ? ' active' : ''}`}
                    onClick={() => setQuery(query === tag ? '' : tag)}
                    title={`按「${tag}」筛选人物`}
                  >
                    {tag} <b>{count}</b>
                  </button>
                ))}
              </div>
            )}

            <div className="people-wall-toolbar">
              <Input
                allowClear
                value={query}
                onChange={e => setQuery(e.target.value)}
                prefix={<SearchOutlined />}
                placeholder="搜人物 / 机构 / 领域（如 AI、英伟达、苏姿丰）"
                className="people-wall-search"
              />
              <Space size={10} wrap>
                <WatchlistManager
                  pool={pool}
                  hiddenIds={hiddenIds}
                  shownCount={watchlisted.length}
                  onToggle={toggleHidden}
                  onShowAll={showAllFigures}
                  onReorder={reorderFigures}
                  hasCustomOrder={order.length > 0}
                  onResetOrder={resetOrder}
                />
                <Segmented
                  size="small"
                  value={sort}
                  onChange={value => setSort(value as SortKey)}
                  options={SORT_OPTIONS}
                />
                <Text type="secondary" className="people-wall-count">
                  {visible.length} / {watchlisted.length} 位 · 数据 {formatGeneratedAt(data.generated_at)}
                </Text>
              </Space>
            </div>

            {visible.length > 0 ? (
              <div className="people-wall">
                {visible.map(figure => (
                  <PersonCard
                    key={figure.id}
                    figure={figure}
                    onSelect={setSelectedId}
                    onPrefetch={loadDigest}
                    pusher={pusher}
                    tracked={isTracked(figure.id)}
                    onToggleTrack={() => toggleTrack(figure)}
                    today={today}
                  />
                ))}
              </div>
            ) : (
              <Empty
                description={
                  query
                    ? `没有匹配「${query}」的人物`
                    : watchlisted.length === 0
                      ? '已隐藏全部人物，点右上「关注」勾选要展示的人'
                      : '暂无人物'
                }
              />
            )}
          </>
        );
      }}
    />
  );
};

export default PeopleSpotlightCenter;
