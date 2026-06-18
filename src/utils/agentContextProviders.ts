import type { Stock } from '../types';
import type { ModuleContextData } from '../contexts/ModuleContext';
import {
  listDataSources,
  listDataItems,
  listMcpServers,
  listMcpCapabilities,
} from '../services/infrastructureService';
import { AGENT_SKILLS } from '../config/skills';
import { recallRelevant, hasArchivedMemory } from './conversationMemory';
import { recallDecisions, hasDecisionsForSymbol } from './decisionJournal';

/**
 * 可插拔 AI 上下文 —— 每个 provider 是一个可挂载/卸载的数据源。
 * 用户在对话里勾选哪些 provider，agent 的上下文就由它们组合而成。
 * 新增一种上下文源只需在 CONTEXT_PROVIDERS 加一项（可拓展）。
 */

export interface ContextProviderInput {
  stocks: Stock[];
  selectedStock: Stock | null;
  moduleContext: ModuleContextData | null;
  /** 本次提问文本（提交时注入）——供需要查询相关性的 provider（如「记忆」召回）使用。 */
  query?: string;
}

export interface ContextProvider {
  id: string;
  label: string;
  /** 当前条件下是否可挂载（可用才在 UI 出现）。 */
  available: (input: ContextProviderInput) => boolean;
  /** 默认是否挂载。 */
  defaultOn: (input: ContextProviderInput) => boolean;
  /** 是否需要异步拉取（拉取后缓存，避免每条消息都打接口）。 */
  async?: boolean;
  /** 产出注入 agent context 的键值对。 */
  build: (input: ContextProviderInput) => Promise<Record<string, unknown>> | Record<string, unknown>;
}

export const CONTEXT_PROVIDERS: ContextProvider[] = [
  {
    id: 'view',
    label: '当前页面',
    available: input => !!input.moduleContext,
    defaultOn: input => !!input.moduleContext,
    build: input => (input.moduleContext
      ? {
          module: input.moduleContext.module,
          title: input.moduleContext.title,
          summary: input.moduleContext.summary,
          data: input.moduleContext.data || {},
        }
      : {}),
  },
  {
    id: 'watchlist',
    label: '自选股',
    available: input => input.stocks.length > 0,
    defaultOn: () => true,
    build: input => ({
      watchlist: input.stocks.slice(0, 12).map(stock => ({
        symbol: stock.symbol,
        name: stock.name,
        market: stock.market,
        change_percent: stock.changePercent,
      })),
    }),
  },
  {
    id: 'focused',
    label: '聚焦标的',
    available: input => !!input.selectedStock,
    defaultOn: input => !!input.selectedStock,
    build: input => (input.selectedStock ? { focused_symbol: input.selectedStock.symbol } : {}),
  },
  {
    id: 'evidence',
    label: '证据库',
    available: () => true,
    defaultOn: () => false,
    async: true,
    build: async input => {
      const [sources, items] = await Promise.all([
        listDataSources().catch(() => []),
        listDataItems({ symbol: input.selectedStock?.symbol, limit: 8 }).catch(() => []),
      ]);
      const connected = sources
        .filter(source => source.status === 'active')
        .map(source => ({
          name: source.name,
          category: source.category,
          trust: source.trust_level,
          items: source.items_count,
        }));
      const recentItems = items.map(item => ({
        title: item.title,
        symbol: item.symbol,
        source: item.source_name,
        url: item.url || '',
        credibility: item.credibility_score,
        preview: (item.text_preview || '').slice(0, 120),
      }));
      // recent_items 作兜底；retrieve=true 让后端按本次提问做相关性检索（真 RAG）。
      return { evidence_sources: { connected, recent_items: recentItems, retrieve: true } };
    },
  },
  {
    id: 'tools',
    label: '工具',
    available: () => true,
    defaultOn: () => false,
    async: true,
    build: async () => {
      const servers = await listMcpServers().catch(() => []);
      const connected = servers.filter(server => server.enabled && server.status !== 'error');
      if (connected.length === 0) {
        return {};
      }
      const capabilities = await listMcpCapabilities({ capability_type: 'tool' }).catch(() => []);
      const tools = capabilities
        .filter(cap => connected.some(server => server.id === cap.server_id))
        .slice(0, 20)
        .map(cap => ({ name: cap.title || cap.name, description: cap.description }));
      return {
        tools: {
          servers: connected.map(server => ({ name: server.name, tools: server.tool_count })),
          available_tools: tools,
        },
      };
    },
  },
  {
    id: 'skills',
    label: '技能',
    available: () => true,
    defaultOn: () => false,
    build: () => ({
      skills: AGENT_SKILLS.map(skill => ({ name: skill.name, description: skill.description })),
    }),
  },
  {
    // AI 原生「自我记忆」：按本次提问 + 聚焦标的召回相关的过往讨论，注入上下文。
    // 同步 provider（localStorage 快扫，不缓存）——每次提交用最新 query 重新召回。
    id: 'memory',
    label: '记忆',
    available: () => hasArchivedMemory(),
    defaultOn: () => hasArchivedMemory(),
    build: input => {
      const recalled = recallRelevant(input.query || '', input.selectedStock?.symbol, Date.now(), 2);
      return recalled.length > 0 ? { memory: { recalled } } : {};
    },
  },
  {
    // AI 原生「自我进化」：聚焦标的有历史判断时，召回过往决策 + 置信度校准，让模型自我感知/纠偏。
    id: 'decisions',
    label: '决策记录',
    available: input => hasDecisionsForSymbol(input.selectedStock?.symbol),
    defaultOn: input => hasDecisionsForSymbol(input.selectedStock?.symbol),
    build: input => {
      const recall = recallDecisions(input.selectedStock?.symbol, Date.now(), 3);
      return recall ? { decision_history: recall } : {};
    },
  },
];

/** 把启用的 provider 组合成 agent 上下文；异步 provider 用缓存。 */
export async function composeAgentContext(
  input: ContextProviderInput,
  enabled: Set<string>,
  asyncCache: Record<string, Record<string, unknown>>
): Promise<Record<string, unknown>> {
  const context: Record<string, unknown> = {};
  for (const provider of CONTEXT_PROVIDERS) {
    if (!enabled.has(provider.id) || !provider.available(input)) {
      continue;
    }
    const payload = provider.async
      ? asyncCache[provider.id] ?? (await provider.build(input))
      : await provider.build(input);
    Object.assign(context, payload);
  }
  return context;
}

/** 初始默认挂载的 provider 集合。 */
export function defaultEnabledProviders(input: ContextProviderInput): Set<string> {
  return new Set(
    CONTEXT_PROVIDERS.filter(p => p.available(input) && p.defaultOn(input)).map(p => p.id)
  );
}

/** 把组合后的上下文序列化成可读字符串，供只接受文本 context 的接口（如圆桌）使用。 */
export function serializeContextForPrompt(context: Record<string, unknown>): string {
  const parts: string[] = [];
  const focused = context.focused_symbol;
  if (typeof focused === 'string' && focused) {
    parts.push(`聚焦标的：${focused}`);
  }
  const watchlist = context.watchlist;
  if (Array.isArray(watchlist) && watchlist.length > 0) {
    const labels = watchlist.map((s: any) => {
      const change = typeof s.change_percent === 'number' ? ` ${s.change_percent >= 0 ? '+' : ''}${s.change_percent.toFixed(2)}%` : '';
      return `${s.symbol}${change}`;
    });
    parts.push(`自选股：${labels.join('、')}`);
  }
  if (typeof context.title === 'string' && context.title) {
    parts.push(`当前页面：${context.title}`);
  }
  const evidence = context.evidence_sources as any;
  if (evidence?.connected?.length) {
    parts.push(`已接入证据源：${evidence.connected.map((c: any) => c.name).join('、')}`);
  }
  const tools = context.tools as any;
  if (tools?.available_tools?.length) {
    parts.push(`可用工具：${tools.available_tools.map((t: any) => t.name).join('、')}`);
  }
  const skills = context.skills;
  if (Array.isArray(skills) && skills.length > 0) {
    parts.push(`可调度技能：${skills.map((s: any) => s.name).join('、')}`);
  }
  const memory = context.memory as any;
  if (memory?.recalled?.length) {
    const blocks = memory.recalled.map((m: any) => `· ${m.title}（${m.when}）：${m.summary}`);
    parts.push(`过往相关讨论：\n${blocks.join('\n')}`);
  }
  const decisions = context.decision_history as any;
  if (decisions?.past?.length) {
    const cal = decisions.calibration;
    const calLine = cal && cal.tendency
      ? `（历史校准：${cal.tendency}，命中 ${cal.correct}/${cal.resolved}）`
      : '';
    const blocks = decisions.past.map((d: any) => `· ${d.when}：${d.action}（置信 ${Math.round((d.confidence || 0) * 100)}%，${d.outcome}）`);
    parts.push(`你对该标的的历史判断${calLine}：\n${blocks.join('\n')}`);
  }
  const attachments = context.attachments;
  if (Array.isArray(attachments) && attachments.length > 0) {
    const blocks = attachments.map((a: any) => `【附件：${a.name}】\n${String(a.content || '').slice(0, 1500)}`);
    parts.push(`用户附件：\n${blocks.join('\n\n')}`);
  }
  return parts.join('\n');
}
