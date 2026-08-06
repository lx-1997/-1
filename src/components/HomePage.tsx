import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  BulbOutlined,
  ClockCircleOutlined,
  FileTextOutlined,
  PaperClipOutlined,
  ReloadOutlined,
  RobotOutlined,
  SearchOutlined,
  SendOutlined,
  ShareAltOutlined,
  TeamOutlined,
  UserOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import type { AppState, Product, Stock, ViewType } from '../types';
import type { MarketSymbolCandidate } from '../services/marketService';
import { useModuleContext } from '../contexts/ModuleContext';
import { runGeneralChatStream, runDulusRoundtable, LoopResearchEvent, DulusRoundtableResponse, ChatCitationSource } from '../services/agentService';
import { uploadDataFile } from '../services/infrastructureService';
import { getGreeting, shouldRunStockResearch } from '../utils/chatRouting';
import { archiveConversation, memoryPreview } from '../utils/conversationMemory';
import { logDecision, autoResolveWithPrices } from '../utils/decisionJournal';
import { buildHomeSuggestions } from '../utils/homeSuggestions';
import CitableSources from './common/CitableSources';
import {
  CONTEXT_PROVIDERS,
  composeAgentContext,
  defaultEnabledProviders,
  serializeContextForPrompt,
  ContextProviderInput,
} from '../utils/agentContextProviders';
import AgentLoopStream from './AgentLoopStream';
import Markdown from './common/Markdown';
import RoundtableMessage from './RoundtableMessage';
import ShareModal, { ShareTarget } from './ShareModal';
import './HomePage.css';

type AgentMode = 'analyst' | 'roundtable';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  roundtable?: DulusRoundtableResponse;
  error?: boolean;
  /** 该回答生成时 Agent 可见的信息集（已挂载的上下文源），用于机构级溯源透明度。 */
  contextUsed?: string[];
  /** 可引用的编号来源；回答里的 [n] 内联引用链到这里。 */
  sources?: ChatCitationSource[];
  /** 流式期间的临时状态文案（如「正在检索最新资料…」），有正文后清除。 */
  status?: string;
}

/** 把后端/网络错误转成对用户友好的提示。 */
function humanizeError(raw?: string): string {
  const text = (raw || '').toLowerCase();
  if (/connection|timeout|timed out|网络|econnrefused|fetch/.test(text)) {
    return '模型连接暂时中断了，点「重试」再试一次。';
  }
  if (/502|503|504|unavailable/.test(text)) {
    return '模型服务繁忙，稍候点「重试」。';
  }
  return raw || 'AI 服务暂时不可用，请重试。';
}

// 对话持久化（AI 原生记忆基础）：刷新后延续上次会话，「新对话」清空。
const CHAT_STORAGE_KEY = 'dfx_home_chat_v1';

function loadStoredMessages(): ChatMessage[] {
  try {
    const raw = window.localStorage.getItem(CHAT_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

interface HomePageProps {
  appState: AppState;
  onStockSelect: (stock: Stock, view?: ViewType) => void;
  onProductClick: (product: Product) => void;
  onAddToCart: (product: Product, variantId: string, quantity: number) => void;
  onViewChange: (view: ViewType) => void;
  onAddStock: (candidate: MarketSymbolCandidate) => Promise<void> | void;
  onRemoveStock: (symbol: string) => void;
  onToggleStockSubscription: (symbol: string) => void;
  onRefreshMarketData: () => void;
  isMarketDataRefreshing: boolean;
}

const recommendationLabels: Record<string, string> = {
  strong_buy: '强烈看好',
  buy: '看好',
  hold: '中性',
  reduce: '偏谨慎',
  sell: '看淡',
  strong_sell: '强烈看淡',
};

function formatHomeQuoteTime(value?: string | null): string {
  if (!value) {
    return '等待行情更新';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '等待行情更新';
  }
  return `更新于 ${date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`;
}

/** 把一次深研 Loop 的结果格式化成一条 assistant 消息。 */
function formatLoopResult(final: LoopResearchEvent): string {
  const recLabel = recommendationLabels[final.recommendation || ''] || '中性';
  const lines: string[] = [
    `分析评分：${recLabel} ｜ 置信度 ${Math.round((final.confidence || 0) * 100)}%`,
  ];
  if (final.executive_summary) {
    lines.push('', final.executive_summary);
  }
  if (final.target_rationale) {
    lines.push(`依据：${final.target_rationale}`);
  }
  if (final.key_catalysts?.length) {
    lines.push('', '关键催化剂：', ...final.key_catalysts.map(c => `· ${c}`));
  }
  if (final.data_issues?.length) {
    lines.push('', '数据提示：', ...final.data_issues.map(d => `· ${d}`));
  }
  return lines.join('\n');
}

/** 把一条 AI 回答（分析师 / 通用问答 / 圆桌）转成可分享的结论文案。 */
function buildShareTarget(messages: ChatMessage[], index: number): ShareTarget {
  const msg = messages[index];
  // 标题取触发本次回答的提问；找不到时退回品牌默认。
  let title = 'DeepFocus 投研结论';
  for (let i = index - 1; i >= 0; i -= 1) {
    if (messages[i].role === 'user') {
      const q = messages[i].content.trim().replace(/\s+/g, ' ');
      title = q.length > 38 ? `${q.slice(0, 38)}…` : q || title;
      break;
    }
  }
  let summary = (msg.roundtable?.synthesis || msg.content || '').trim();
  const cited = (msg.sources || []).map(s => s.title).filter(Boolean).slice(0, 3);
  if (cited.length) {
    summary += `\n\n来源：${cited.join('；')}`;
  }
  if (summary.length > 800) {
    summary = `${summary.slice(0, 800)}…`;
  }
  return { title, summary, byline: '由 DeepFocus 投研工作台生成' };
}

const HomePage: React.FC<HomePageProps> = ({
  appState,
  onStockSelect,
  onViewChange,
  onRefreshMarketData,
  isMarketDataRefreshing,
}) => {
  const { currentContext } = useModuleContext();
  const [messages, setMessages] = useState<ChatMessage[]>(loadStoredMessages);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [agentMode, setAgentMode] = useState<AgentMode>('analyst');
  const [loopSymbol, setLoopSymbol] = useState<string | null>(null);
  const [loopQuestion, setLoopQuestion] = useState<string | null>(null);
  const [shareTarget, setShareTarget] = useState<ShareTarget | null>(null);
  const [quickSymbol, setQuickSymbol] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const streamCancelRef = useRef<(() => void) | null>(null);
  const asyncContextCache = useRef<Record<string, Record<string, unknown>>>({});

  const providerInput = useMemo<ContextProviderInput>(() => ({
    stocks: appState.stocks,
    selectedStock: appState.selectedStock ?? null,
    moduleContext: currentContext,
  }), [appState.stocks, appState.selectedStock, currentContext]);

  // 可插拔 AI 上下文：默认挂载「可用且默认开启」的 provider，用户可在挂载条增删。
  const [enabledSources, setEnabledSources] = useState<Set<string>>(() => defaultEnabledProviders(providerInput));
  const [pendingSource, setPendingSource] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<Array<{ id: string; name: string; content: string; parsing?: boolean }>>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const TEXT_FILE = /\.(txt|md|markdown|csv|tsv|json|log|ya?ml|xml|html?|js|ts|tsx|jsx|py|java|c|cpp|go|rs|sql|sh|ini|conf)$/i;

  const handleFiles = useCallback(async (files: FileList | null) => {
    if (!files) {
      return;
    }
    for (const file of Array.from(files)) {
      const id = `${file.name}-${file.size}-${file.lastModified}`;
      const isText = TEXT_FILE.test(file.name) || file.type.startsWith('text/');

      if (isText) {
        if (file.size > 256 * 1024) {
          continue; // 文本文件上限 256KB
        }
        try {
          const content = await file.text();
          setAttachments(prev => (prev.some(a => a.id === id) ? prev : [...prev, { id, name: file.name, content }]));
        } catch {
          // 读取失败跳过
        }
        continue;
      }

      // 非文本（PDF / docx 等）→ 走后端入库解析，拿回解析文本。
      if (file.size > 8 * 1024 * 1024) {
        continue; // 上限 8MB
      }
      setAttachments(prev => (prev.some(a => a.id === id) ? prev : [...prev, { id, name: file.name, content: '', parsing: true }]));
      try {
        const item = await uploadDataFile(file, { title: file.name });
        const text = item.text || item.text_preview || '';
        setAttachments(prev => prev.map(a => (a.id === id ? { ...a, content: text, parsing: false } : a)));
      } catch {
        setAttachments(prev => prev.filter(a => a.id !== id)); // 解析失败移除
      }
    }
  }, [TEXT_FILE]);

  const removeAttachment = useCallback((id: string) => {
    setAttachments(prev => prev.filter(a => a.id !== id));
  }, []);

  const availableProviders = useMemo(
    () => CONTEXT_PROVIDERS.filter(p => p.available(providerInput)),
    [providerInput]
  );

  // 组合启用的 provider → agent 上下文（异步 provider 用缓存）。
  // query：本次提问文本，供「记忆」provider 做相关性召回（每次提交传入最新值）。
  const buildAgentContext = useCallback(
    (query?: string) => composeAgentContext({ ...providerInput, query }, enabledSources, asyncContextCache.current),
    [providerInput, enabledSources]
  );

  const toggleSource = useCallback(async (id: string) => {
    const provider = CONTEXT_PROVIDERS.find(p => p.id === id);
    const willEnable = !enabledSources.has(id);
    // 异步 provider 首次挂载时预拉取并缓存。
    if (willEnable && provider?.async && !asyncContextCache.current[id]) {
      setPendingSource(id);
      try {
        asyncContextCache.current[id] = await provider.build(providerInput);
      } catch {
        asyncContextCache.current[id] = {};
      } finally {
        setPendingSource(null);
      }
    }
    setEnabledSources(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, [enabledSources, providerInput]);

  useEffect(() => () => streamCancelRef.current?.(), []);

  useEffect(() => {
    // 自我进化：自选股价格更新时，按价格自动判定待兑现的历史判断（行情不可用则零判定，优雅降级）。
    const priceMap: Record<string, number> = {};
    for (const stock of appState.stocks) {
      if (typeof stock.currentPrice === 'number' && stock.currentPrice > 0) {
        priceMap[stock.symbol] = stock.currentPrice;
      }
    }
    if (Object.keys(priceMap).length > 0) {
      autoResolveWithPrices(priceMap, Date.now());
    }
  }, [appState.stocks]);

  const greeting = useMemo(() => getGreeting(), []);
  const hasConversation = messages.length > 0 || Boolean(loopSymbol);
  // 个性化建议：回到空首页时按自选股/历史判断/最近讨论重新生成（AI 原生「记得住你」）。
  const suggestions = useMemo(
    () => buildHomeSuggestions(appState.stocks, Date.now()),
    [appState.stocks, hasConversation]
  );

  // 首页概览：把方案里的「观察池 → 简报 → 风险 → 历史」串成一个可扫描的工作台。
  const overview = useMemo(() => {
    const watchlist = appState.stocks.slice(0, 5);
    const movers = appState.stocks.filter(stock => typeof stock.changePercent === 'number');
    const averageChange = movers.length > 0
      ? movers.reduce((sum, stock) => sum + stock.changePercent, 0) / movers.length
      : 0;
    const monitoredCount = appState.stocks.filter(stock => stock.isSubscribed !== false).length;
    const latestQuote = appState.stocks
      .map(stock => stock.quoteFetchedAt || stock.quoteMarketTime || '')
      .filter(Boolean)
      .sort()
      .slice(-1)[0];
    const tone = averageChange > 0.35 ? '偏强' : averageChange < -0.35 ? '偏弱' : '震荡';
    const toneClass = averageChange > 0.35 ? 'positive' : averageChange < -0.35 ? 'negative' : 'neutral';
    return { watchlist, averageChange, monitoredCount, latestQuote, tone, toneClass };
  }, [appState.stocks]);

  const recentResearch = useMemo(() => memoryPreview(Date.now(), 3), [hasConversation]);

  useEffect(() => {
    // 只在对话态把消息流滚到底部；空状态（首屏）保持顶部，避免遮住问候语和输入框。
    if (hasConversation && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, loading, loopSymbol, hasConversation]);

  useEffect(() => {
    // 流式进行中不频繁写盘，结束后再持久化最近 40 条。
    if (loading) {
      return;
    }
    try {
      window.localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(messages.slice(-40)));
    } catch {
      // 存储不可用时静默降级。
    }
  }, [messages, loading]);

  const setLastAssistant = (updater: (msg: ChatMessage) => ChatMessage) => {
    setMessages(prev => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (last && last.role === 'assistant') {
        next[next.length - 1] = updater(last);
      }
      return next;
    });
  };

  const submit = useCallback(async (raw: string) => {
    const trimmed = raw.trim();
    if (!trimmed || loading || loopSymbol) {
      return;
    }

    setInput('');
    setMessages(prev => [...prev, { role: 'user', content: trimmed }]);

    // 圆桌 Agent：多角色辩论 → 综合决策（同样吃可插拔上下文）。
    if (agentMode === 'roundtable') {
      setLoading(true);
      setMessages(prev => [...prev, { role: 'assistant', content: '' }]);
      try {
        const composed = await buildAgentContext(trimmed);
        const readyAttachments = attachments.filter(a => a.content && !a.parsing);
        if (readyAttachments.length > 0) {
          composed.attachments = readyAttachments.map(a => ({ name: a.name, content: a.content }));
        }
        const contextNote = serializeContextForPrompt(composed);
        const guardrail = '从证据/研究/风险/操作多视角辩论，标注证据缺口、反证与下一步验证；约束在投研工作流层面，不输出确定性买卖建议。';
        const result = await runDulusRoundtable({
          objective: trimmed,
          context: contextNote ? `${guardrail}\n\n【已挂载上下文】\n${contextNote}` : guardrail,
          stock: appState.selectedStock || undefined,
          enabled_tools: enabledSources.has('tools') ? ['mcp'] : undefined,
          mode: 'debate',
          locale: 'zh-CN',
        });
        setLastAssistant(last => ({ ...last, content: result.synthesis || '圆桌讨论已完成', roundtable: result }));
      } catch (err) {
        setLastAssistant(last => (
          last.content ? last : { ...last, content: humanizeError(err instanceof Error ? err.message : ''), error: true }
        ));
      } finally {
        setLoading(false);
      }
      return;
    }

    // 分析师 Agent · 能识别出标的的研究类问题 → 走个股深研 Loop（流式）。
    const researchSymbol = shouldRunStockResearch(trimmed);
    if (researchSymbol) {
      setLoopSymbol(researchSymbol);
      setLoopQuestion(trimmed);
      setLoading(true);
      return;
    }

    // 分析师 Agent · 其余走通用问答（流式）。
    setLoading(true);
    const history = messages.slice(-6).map(m => ({ role: m.role, content: m.content }));
    const context = await buildAgentContext(trimmed);
    const readyAttachments = attachments.filter(a => a.content && !a.parsing);
    if (readyAttachments.length > 0) {
      context.attachments = readyAttachments.map(a => ({ name: a.name, content: a.content }));
    }
    const usedLabels = availableProviders.filter(p => enabledSources.has(p.id)).map(p => p.label);
    if (readyAttachments.length > 0) {
      usedLabels.push(`附件×${readyAttachments.length}`);
    }

    // 先放一个空的 assistant 气泡，token 到达后逐步填充；记录本次可见信息集用于溯源。
    setMessages(prev => [...prev, { role: 'assistant', content: '', contextUsed: usedLabels }]);

    streamCancelRef.current = runGeneralChatStream(
      { message: trimmed, history, context, locale: 'zh-CN' },
      {
        onDelta: (text) => setLastAssistant(last => ({ ...last, content: last.content + text, status: undefined })),
        onSources: (sources) => setLastAssistant(last => ({ ...last, sources })),
        onStatus: (status) => setLastAssistant(last => (last.content ? last : { ...last, status })),
        onDone: () => {
          setLoading(false);
          streamCancelRef.current = null;
        },
        onError: (error) => {
          setLastAssistant(last => (
            last.content ? last : { ...last, content: humanizeError(error), error: true }
          ));
          setLoading(false);
          streamCancelRef.current = null;
        },
      }
    );
  }, [loading, loopSymbol, messages, agentMode, appState.selectedStock, buildAgentContext, attachments, enabledSources]);

  const handleLoopComplete = useCallback((final: LoopResearchEvent) => {
    // 深研结论转入对话历史时，保留可引用证据来源（带 url+可信度），与分析师快答一致地可溯源。
    setMessages(prev => [...prev, {
      role: 'assistant',
      content: formatLoopResult(final),
      sources: final.citable_sources && final.citable_sources.length > 0 ? final.citable_sources : undefined,
    }]);
    // 自我进化：把这次深研判断自动记入决策日志，供日后召回 + 置信度校准。
    const decisionSymbol = final.symbol || loopSymbol;
    if (decisionSymbol && final.recommendation) {
      // 记录当时价格（自选股快照里有则填），供日后价格可用时自动判定兑现。
      const priceAtLog = appState.stocks.find(s => s.symbol === decisionSymbol)?.currentPrice ?? null;
      logDecision({
        symbol: decisionSymbol,
        action: final.recommendation,
        confidence: final.confidence ?? 0,
        thesis: final.executive_summary || final.target_rationale || '',
        now: Date.now(),
        priceAtLog: typeof priceAtLog === 'number' && priceAtLog > 0 ? priceAtLog : null,
      });
    }
    setLoopSymbol(null);
    setLoopQuestion(null);
    setLoading(false);
  }, [loopSymbol, appState.stocks]);

  const handleLoopCancel = useCallback(() => {
    setLoopSymbol(null);
    setLoopQuestion(null);
    setLoading(false);
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void submit(input);
    }
  };

  // 搜股直达：输入代码 → 直接打开机构级速判卡（轻量，不走深研 Loop）。
  // 自选股命中则用真实 market_cap，否则最小构造（速判卡会自行拉取可得数据）。
  const goQuickSheet = useCallback((raw: string) => {
    const sym = raw.trim().toUpperCase();
    if (!sym) {
      return;
    }
    const existing = appState.stocks.find(s => s.symbol.toUpperCase() === sym);
    const stock: Stock = existing || {
      symbol: sym,
      name: sym,
      sector: '',
      marketCap: 0,
      currentPrice: 0,
      changePercent: 0,
      description: '',
      focusLevel: 'medium',
      totalPosts: 0,
      totalPaidPosts: 0,
      communityScore: 0,
    };
    setQuickSymbol('');
    onStockSelect(stock, 'stock-tear-sheet');
  }, [appState.stocks, onStockSelect]);

  // 失败后重试：移除报错气泡与对应提问，重新发起同一问题。
  const retryLast = useCallback(() => {
    if (loading) {
      return;
    }
    const lastUserIdx = messages.map(m => m.role).lastIndexOf('user');
    if (lastUserIdx < 0) {
      return;
    }
    const content = messages[lastUserIdx].content;
    setMessages(prev => prev.slice(0, lastUserIdx));
    void submit(content);
  }, [loading, messages, submit]);

  const resetConversation = () => {
    streamCancelRef.current?.();
    streamCancelRef.current = null;
    // 自我记忆：开新对话前把当前会话归档进历史库，供日后「记忆」provider 召回。
    archiveConversation(messages.map(m => ({ role: m.role, content: m.content })), Date.now());
    setMessages([]);
    setLoopSymbol(null);
    setLoopQuestion(null);
    setLoading(false);
    try {
      window.localStorage.removeItem(CHAT_STORAGE_KEY);
    } catch {
      // 忽略
    }
  };

  // 中断进行中的生成（流式问答或深研 Loop），保留已生成内容。
  const stopGenerating = () => {
    streamCancelRef.current?.();
    streamCancelRef.current = null;
    setLoopSymbol(null);
    setLoopQuestion(null);
    setLoading(false);
    // 若停在还没有任何输出的空 assistant 气泡，移除它（避免留空白）。
    setMessages(prev => {
      const last = prev[prev.length - 1];
      if (last && last.role === 'assistant' && !last.content) {
        return prev.slice(0, -1);
      }
      return prev;
    });
  };

  const composer = (
    <div className="dfx-home-composer">
      <textarea
        rows={hasConversation ? 1 : 3}
        value={input}
        onChange={e => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="问任何问题，或输入标的让 Agent 做深度研究（Enter 发送，Shift+Enter 换行）"
        disabled={loading}
        autoFocus
      />
      <div className="dfx-home-ctx" aria-label="可插拔上下文">
        <span className="dfx-home-ctx-label">上下文</span>
        {availableProviders.map(provider => {
          const on = enabledSources.has(provider.id);
          const pending = pendingSource === provider.id;
          return (
            <button
              key={provider.id}
              type="button"
              className={`dfx-home-ctx-chip${on ? ' on' : ''}`}
              onClick={() => void toggleSource(provider.id)}
              disabled={pending}
              title={on ? `已挂载：${provider.label}（点击卸载）` : `挂载：${provider.label}`}
            >
              {pending ? '…' : on ? '✓' : '+'} {provider.label}
            </button>
          );
        })}
        <button
          type="button"
          className="dfx-home-ctx-chip"
          onClick={() => fileInputRef.current?.click()}
          title="附加文本文件（txt/md/csv/json/代码等，≤256KB）"
        >
          <PaperClipOutlined /> 附件
        </button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          style={{ display: 'none' }}
          onChange={e => {
            void handleFiles(e.target.files);
            e.target.value = '';
          }}
        />
        {attachments.map(att => (
          <span key={att.id} className="dfx-home-attach-chip" title={att.name}>
            <PaperClipOutlined />
            <span className="dfx-home-attach-name">{att.name}{att.parsing ? '（解析中…）' : ''}</span>
            <button type="button" onClick={() => removeAttachment(att.id)} aria-label={`移除 ${att.name}`}>×</button>
          </span>
        ))}
      </div>
      <div className="dfx-home-composer-foot">
        <div className="dfx-home-mode" role="tablist" aria-label="Agent 模式">
          <button
            type="button"
            role="tab"
            aria-selected={agentMode === 'analyst'}
            className={`dfx-home-mode-btn${agentMode === 'analyst' ? ' active' : ''}`}
            onClick={() => setAgentMode('analyst')}
            title="分析师：单视角深度研究，调多源数据 + 工具，逐步推理"
          >
            <BulbOutlined /> 分析师
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={agentMode === 'roundtable'}
            className={`dfx-home-mode-btn${agentMode === 'roundtable' ? ' active' : ''}`}
            onClick={() => setAgentMode('roundtable')}
            title="圆桌：证据 / 研究 / 风险 / 操作多角色辩论，压力测试观点"
          >
            <TeamOutlined /> 圆桌
          </button>
        </div>
        <span className="dfx-home-composer-hint">
          已挂载 {enabledSources.size} 个上下文源
        </span>
        {loading ? (
          <button
            type="button"
            className="dfx-home-send dfx-home-send-stop"
            onClick={stopGenerating}
            aria-label="停止生成"
            title="停止生成"
          >
            <span className="dfx-home-stop-glyph" />
          </button>
        ) : (
          <button
            type="button"
            className="dfx-home-send"
            onClick={() => void submit(input)}
            disabled={!input.trim()}
            aria-label="发送"
          >
            <SendOutlined />
          </button>
        )}
      </div>
    </div>
  );

  return (
    <div className="dfx-home">
      <div className="dfx-home-topbar">
        <div className="dfx-home-topbar-title">
          <span className="dfx-home-brandmark">◆</span>
          <span>{hasConversation ? '当前对话' : 'DeepFocus 投研工作台'}</span>
        </div>
        {hasConversation && (
          <button type="button" className="dfx-home-chip" onClick={resetConversation}>
            新对话
          </button>
        )}
      </div>

      <div className="dfx-home-scroll" ref={scrollRef}>
        {!hasConversation ? (
          <div className="dfx-home-hero">
            <div className="dfx-home-greeting">
              <h1>{greeting}{appState.user?.username ? `，${appState.user.username}` : ''}</h1>
              <p>问任何投研问题，或直接输入标的让 Agent 做深度研究</p>
            </div>

            {composer}

            <div className="dfx-home-quicksheet">
              <SearchOutlined className="dfx-home-quicksheet-icon" />
              <input
                value={quickSymbol}
                onChange={e => setQuickSymbol(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    goQuickSheet(quickSymbol);
                  }
                }}
                placeholder="输入代码（如 AAPL）直达机构级速判卡"
                aria-label="搜股直达速判"
              />
              <button type="button" onClick={() => goQuickSheet(quickSymbol)} disabled={!quickSymbol.trim()}>
                速判
              </button>
            </div>

            <div className="dfx-home-suggestions">
              {suggestions.map(text => (
                <button key={text} type="button" className="dfx-home-chip" onClick={() => void submit(text)}>
                  {text}
                </button>
              ))}
            </div>

            <section className="dfx-home-overview" aria-label="个人投研概览">
              <div className="dfx-home-overview-head">
                <div>
                  <div className="dfx-home-overview-kicker">PERSONAL RESEARCH DESK</div>
                  <h2>今天先看什么</h2>
                </div>
                <div className="dfx-home-overview-meta">
                  <span className={`dfx-home-market-tone ${overview.toneClass}`}>
                    {overview.tone === '偏强' ? '↑' : overview.tone === '偏弱' ? '↓' : '→'} 市场{overview.tone}
                  </span>
                  <span>{formatHomeQuoteTime(overview.latestQuote)}</span>
                  <button
                    type="button"
                    className="dfx-home-refresh"
                    onClick={onRefreshMarketData}
                    disabled={isMarketDataRefreshing}
                    title="刷新观察池行情"
                  >
                    <ReloadOutlined spin={isMarketDataRefreshing} />
                    刷新
                  </button>
                </div>
              </div>

              <div className="dfx-home-overview-grid">
                <div className="dfx-home-watch-card">
                  <div className="dfx-home-card-head">
                    <div>
                      <span className="dfx-home-card-eyebrow">WATCHLIST</span>
                      <h3>我的观察池</h3>
                    </div>
                    <button type="button" className="dfx-home-text-btn" onClick={() => onViewChange('stocks')}>
                      管理观察池 →
                    </button>
                  </div>
                  {overview.watchlist.length > 0 ? (
                    <div className="dfx-home-watch-list">
                      {overview.watchlist.map(stock => {
                        const changeClass = stock.changePercent > 0 ? 'positive' : stock.changePercent < 0 ? 'negative' : 'neutral';
                        return (
                          <button
                            key={stock.symbol}
                            type="button"
                            className="dfx-home-watch-row"
                            onClick={() => onStockSelect(stock, 'stock-tear-sheet')}
                          >
                            <span className="dfx-home-watch-name">
                              <strong>{stock.symbol}</strong>
                              <small>{stock.name || stock.sector || '未命名标的'}</small>
                            </span>
                            <span className="dfx-home-watch-price">
                              {stock.currentPrice > 0 ? stock.currentPrice.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : '--'}
                            </span>
                            <span className={`dfx-home-watch-change ${changeClass}`}>
                              {stock.changePercent > 0 ? '+' : ''}{stock.changePercent.toFixed(2)}%
                            </span>
                            <span className={`dfx-home-watch-dot${stock.isSubscribed === false ? ' paused' : ''}`} title={stock.isSubscribed === false ? '监控已暂停' : '监控中'} />
                          </button>
                        );
                      })}
                    </div>
                  ) : (
                    <button type="button" className="dfx-home-empty-watch" onClick={() => onViewChange('stocks')}>
                      <SearchOutlined /> 添加第一只关注标的
                    </button>
                  )}
                  <div className="dfx-home-watch-footer">
                    <span><span className="dfx-home-footer-dot" /> {overview.monitoredCount} 个标的持续监控</span>
                    <span>平均涨跌 {overview.averageChange > 0 ? '+' : ''}{overview.averageChange.toFixed(2)}%</span>
                  </div>
                </div>

                <div className="dfx-home-action-card">
                  <div className="dfx-home-card-head">
                    <div>
                      <span className="dfx-home-card-eyebrow">WORKFLOW</span>
                      <h3>投研工作流</h3>
                    </div>
                    <span className="dfx-home-card-hint">一键进入</span>
                  </div>
                  <div className="dfx-home-action-list">
                    <button type="button" className="dfx-home-action-row" onClick={() => overview.watchlist[0] ? onStockSelect(overview.watchlist[0], 'stock-tear-sheet') : onViewChange('stocks')}>
                      <span className="dfx-home-action-icon teal"><FileTextOutlined /></span>
                      <span><strong>个股简评</strong><small>查看观察池首个标的的最新判断</small></span>
                      <span className="dfx-home-action-arrow">→</span>
                    </button>
                    <button type="button" className="dfx-home-action-row" onClick={() => onViewChange('briefing')}>
                      <span className="dfx-home-action-icon amber"><ClockCircleOutlined /></span>
                      <span><strong>投研晨报</strong><small>汇总关注标的与市场要闻</small></span>
                      <span className="dfx-home-action-arrow">→</span>
                    </button>
                    <button type="button" className="dfx-home-action-row" onClick={() => onViewChange('risk-dashboard')}>
                      <span className="dfx-home-action-icon red"><WarningOutlined /></span>
                      <span><strong>盘后风险摘要</strong><small>快速定位需要复核的风险信号</small></span>
                      <span className="dfx-home-action-arrow">→</span>
                    </button>
                  </div>
                </div>
              </div>

              <div className="dfx-home-history-strip">
                <div className="dfx-home-history-title">
                  <span className="dfx-home-card-eyebrow">MEMORY</span>
                  <strong>最近研究</strong>
                  <span>结论会自动留存，方便回看和核验</span>
                </div>
                {recentResearch.length > 0 ? (
                  <div className="dfx-home-history-list">
                    {recentResearch.map(item => (
                      <button key={`${item.title}-${item.when}`} type="button" className="dfx-home-history-item" onClick={() => onViewChange('research-workbench')}>
                        <span>{item.title}</span><small>{item.when}</small>
                      </button>
                    ))}
                  </div>
                ) : (
                  <button type="button" className="dfx-home-history-empty" onClick={() => onViewChange('research-workbench')}>
                    完成一次研究后，会在这里看到历史记录 →
                  </button>
                )}
              </div>
            </section>
          </div>
        ) : (
          <div className="dfx-home-thread">
            {messages.map((msg, i) => {
              const isStreamingPlaceholder =
                msg.role === 'assistant' && !msg.content && loading && i === messages.length - 1;
              return (
                <div key={i} className={`dfx-home-msg ${msg.role}`}>
                  <div className="dfx-home-msg-avatar">
                    {msg.role === 'user' ? <UserOutlined /> : <RobotOutlined />}
                  </div>
                  <div className="dfx-home-msg-body">
                    {msg.roundtable
                      ? <RoundtableMessage data={msg.roundtable} />
                      : msg.error
                        ? <span className="dfx-home-error">{msg.content}</span>
                        : msg.role === 'assistant' && msg.content
                          ? <Markdown
                              content={msg.content}
                              citations={msg.sources?.map(s => s.n)}
                              onCitationClick={(n) => {
                                const src = msg.sources?.find(s => s.n === n);
                                if (src?.url) window.open(src.url, '_blank', 'noopener');
                              }}
                            />
                          : msg.content}
                    {msg.role === 'assistant' && (
                      <CitableSources
                        sources={msg.sources}
                        label={msg.error ? '已检索到来源（生成中断，可重试）' : '来源'}
                      />
                    )}
                    {msg.error && i === messages.length - 1 && !loading && (
                      <button type="button" className="dfx-home-retry" onClick={retryLast}>
                        <ReloadOutlined /> 重试
                      </button>
                    )}
                    {isStreamingPlaceholder && (
                      <span className="dfx-home-thinking">
                        {msg.status || (agentMode === 'roundtable' ? '圆桌讨论中…' : '思考中…')}
                      </span>
                    )}
                    {!msg.roundtable && !msg.error && msg.role === 'assistant' && msg.content && loading && i === messages.length - 1 && (
                      <span className="dfx-home-caret" />
                    )}
                    {!msg.roundtable && !msg.error && msg.role === 'assistant' && msg.content
                      && (msg.contextUsed?.length ?? 0) > 0
                      && !(loading && i === messages.length - 1) && (
                      <div className="dfx-home-grounding" title="本回答生成时 Agent 可见的信息集">
                        可见信息集：{msg.contextUsed!.join(' · ')}
                      </div>
                    )}
                    {msg.role === 'assistant' && !msg.error && (Boolean(msg.content) || Boolean(msg.roundtable))
                      && !(loading && i === messages.length - 1) && (
                      <div className="dfx-home-actions">
                        <button
                          type="button"
                          className="dfx-home-share"
                          onClick={() => setShareTarget(buildShareTarget(messages, i))}
                          title="把这段结论生成分享文案"
                        >
                          <ShareAltOutlined /> 分享
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}

            {loopSymbol && loopQuestion && (
              <AgentLoopStream
                symbol={loopSymbol}
                question={loopQuestion}
                onComplete={handleLoopComplete}
                onCancel={handleLoopCancel}
              />
            )}
          </div>
        )}
      </div>

      {hasConversation && <div className="dfx-home-dock">{composer}</div>}

      <ShareModal
        visible={Boolean(shareTarget)}
        content={shareTarget ?? undefined}
        modalTitle="分享投研结论"
        onCancel={() => setShareTarget(null)}
      />
    </div>
  );
};

export default HomePage;
