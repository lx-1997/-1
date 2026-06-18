/**
 * 对话记忆（AI 原生「自我记忆」基础）。
 *
 * 当前会话只存单线程（HomePage 的 CHAT_STORAGE_KEY），「新对话」即清空。
 * 这里把过往会话归档成一个可检索的历史库，并提供「按当前提问/标的召回相关过往讨论」，
 * 让 agent 跨会话记住你聊过什么 —— 配合 RAG（证据库）形成完整的可插拔上下文。
 *
 * 全部客户端、确定性、无第三方依赖：召回靠 标的命中 + 提问词元重合 打分，不依赖 LLM。
 */

export interface ArchivedTurn {
  role: 'user' | 'assistant';
  content: string;
}

export interface ArchivedConversation {
  id: string;
  ts: number;
  title: string;
  turns: ArchivedTurn[];
}

/** 召回结果：注入 agent 上下文的紧凑过往讨论。 */
export interface RecalledMemory {
  title: string;
  when: string;
  summary: string;
}

const MEMORY_STORAGE_KEY = 'dfx_chat_memory_v1';
const MAX_ARCHIVED = 24;

function safeLoad(): ArchivedConversation[] {
  try {
    const raw = window.localStorage.getItem(MEMORY_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function safeSave(list: ArchivedConversation[]): void {
  try {
    window.localStorage.setItem(MEMORY_STORAGE_KEY, JSON.stringify(list.slice(0, MAX_ARCHIVED)));
  } catch {
    // 配额/隐私模式失败 → 静默放弃，不影响主流程。
  }
}

/** 词元化：CJK 连续段（按 2 字滑窗，缓解中文整段不匹配）+ ASCII 词，全部转小写。 */
function tokenize(text: string): string[] {
  const out: string[] = [];
  const runs = (text || '').toLowerCase().match(/[a-z0-9]{2,}|[一-鿿]+/g) || [];
  for (const run of runs) {
    if (/^[a-z0-9]+$/.test(run)) {
      out.push(run);
    } else {
      // 中文段做 2-gram，提升与过往讨论的重合命中率。
      if (run.length <= 2) {
        out.push(run);
      } else {
        for (let i = 0; i < run.length - 1; i += 1) {
          out.push(run.slice(i, i + 2));
        }
      }
    }
  }
  return Array.from(new Set(out));
}

function deriveTitle(turns: ArchivedTurn[]): string {
  const firstUser = turns.find(t => t.role === 'user');
  const text = (firstUser?.content || '').trim().replace(/\s+/g, ' ');
  return text.slice(0, 40) || '历史对话';
}

function relativeWhen(ts: number, now: number): string {
  const diff = Math.max(0, now - ts);
  const day = 24 * 60 * 60 * 1000;
  if (diff < 60 * 1000) return '刚刚';
  if (diff < 60 * 60 * 1000) return `${Math.floor(diff / (60 * 1000))} 分钟前`;
  if (diff < day) return `${Math.floor(diff / (60 * 60 * 1000))} 小时前`;
  if (diff < 30 * day) return `${Math.floor(diff / day)} 天前`;
  return `${Math.floor(diff / (30 * day))} 个月前`;
}

/**
 * 归档一段会话（在「新对话」/切换前调用）。只存含至少一轮问答的会话，按 id 去重，最新在前。
 */
export function archiveConversation(turns: ArchivedTurn[], now: number): void {
  const clean = (turns || []).filter(t => t && t.content && t.content.trim());
  const hasUser = clean.some(t => t.role === 'user');
  const hasAssistant = clean.some(t => t.role === 'assistant');
  if (!hasUser || !hasAssistant) {
    return; // 空会话或只有提问没回答 → 不值得记忆。
  }
  // 截断每轮，避免历史库膨胀。
  const trimmed = clean.slice(-12).map(t => ({ role: t.role, content: t.content.slice(0, 600) }));
  const signature = trimmed.map(t => `${t.role}:${t.content.slice(0, 60)}`).join('|');
  const id = `c_${now.toString(36)}`;

  const existing = safeLoad();
  // 与最近一条签名相同 → 视为重复归档，跳过。
  if (existing[0] && existing[0].turns.map(t => `${t.role}:${t.content.slice(0, 60)}`).join('|') === signature) {
    return;
  }
  const record: ArchivedConversation = { id, ts: now, title: deriveTitle(trimmed), turns: trimmed };
  safeSave([record, ...existing]);
}

/**
 * 按当前提问 + 聚焦标的召回相关过往讨论。
 * 打分：聚焦标的命中正文 +6；提问词元每个重合 +1。取分>0 的 top-N。
 */
export function recallRelevant(
  query: string,
  focusedSymbol: string | null | undefined,
  now: number,
  limit = 2
): RecalledMemory[] {
  const archive = safeLoad();
  if (archive.length === 0) {
    return [];
  }
  const tokens = tokenize(query);
  const symbol = (focusedSymbol || '').toLowerCase().trim();

  const scored = archive.map(conv => {
    const text = conv.turns.map(t => t.content).join('\n').toLowerCase();
    let score = 0;
    if (symbol && text.includes(symbol)) {
      score += 6;
    }
    for (const tok of tokens) {
      if (text.includes(tok)) {
        score += 1;
      }
    }
    return { conv, score };
  });

  return scored
    .filter(s => s.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map(({ conv }) => {
      const firstQ = conv.turns.find(t => t.role === 'user')?.content || '';
      const firstA = conv.turns.find(t => t.role === 'assistant')?.content || '';
      const summary = [
        firstQ ? `问：${firstQ.slice(0, 80)}` : '',
        firstA ? `答：${firstA.slice(0, 160)}` : '',
      ].filter(Boolean).join('　');
      return { title: conv.title, when: relativeWhen(conv.ts, now), summary };
    });
}

/** 历史库是否有归档（决定「记忆」provider 是否可挂载）。 */
export function hasArchivedMemory(): boolean {
  return safeLoad().length > 0;
}

/** 已归档会话数（设置里展示）。 */
export function memoryCount(): number {
  return safeLoad().length;
}

/** 最近若干条归档的简要（标题 + 时间），供设置里预览。 */
export function memoryPreview(now: number, limit = 6): Array<{ title: string; when: string }> {
  return safeLoad().slice(0, limit).map(c => ({ title: c.title, when: relativeWhen(c.ts, now) }));
}

/** 清空记忆库（设置/隐私入口可用）。 */
export function clearMemory(): void {
  try {
    window.localStorage.removeItem(MEMORY_STORAGE_KEY);
  } catch {
    // ignore
  }
}
