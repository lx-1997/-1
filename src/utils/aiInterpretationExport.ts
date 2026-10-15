import {
  normalizeResearchDeepDraft,
  type ResearchDeepDraftInput,
  type ResearchDeepDraftEvidence,
  type ResearchDeepDraftLogicLine,
  type ResearchDeepDraftTable,
} from '../components/ResearchDeepDraft';

export interface AiInterpretationTextInput {
  result?: unknown;
  deepDraft?: ResearchDeepDraftInput;
  title?: string;
  date?: string;
  site?: string;
}

export interface AiInterpretationImage {
  blob: Blob;
  page: number;
  total: number;
  width: number;
  height: number;
}

type Dict = Record<string, unknown>;
const record = (value: unknown): Dict => value && typeof value === 'object' && !Array.isArray(value) ? value as Dict : {};
const scalar = (value: unknown): string => typeof value === 'string' ? value.trim()
  : typeof value === 'number' && Number.isFinite(value) ? String(value)
    : typeof value === 'boolean' ? String(value) : '';
const strings = (value: unknown): string[] => (Array.isArray(value) ? value : [value]).map(scalar).filter(Boolean);
const pageLabel = (page: unknown, pages?: unknown): string => {
  const value = scalar(page) || (Array.isArray(pages) ? pages.map(scalar).filter(Boolean).join('、') : scalar(pages));
  return value ? (/^第/.test(value) || !/\d/.test(value) ? value : `第 ${value} 页`) : '';
};

const EXPORT_NOTICE = 'AI 生成，仅供研究参考，不构成投资建议。';
// Only exact sentences are duplicates. Keep decimals, currencies, signs,
// dates and opposing views intact; this is presentation cleanup, not a summary.
const sentenceKey = (value: string): string => value.replace(/\s+/g, '').replace(/。+$/, '');
const sentences = (value: unknown): string[] => scalar(value).replace(/\r\n?/g, '\n').match(/[^。\n]+(?:。[ \t]*(?:\n+)?|\n+|$)/g) || [];
const distinctProse = (value: unknown, seen: Set<string>): string => sentences(value).filter(item => {
  const key = sentenceKey(item);
  if (!key || seen.has(key)) return false;
  seen.add(key);
  return true;
}).join('').trim();
const GENERIC_SUBTITLES = new Set(['基于研报页面的结构化深度整理', '基于原文可见信息的结构化整理']);
const noticeKey = (value: string): string => sentenceKey(value).replace(/[，,；;。]+$/, '').replace(/[“”‘’]/g, "'");
const GENERIC_NOTICES = new Set([
  'AI 生成内容仅供研究参考，不构成投资建议', 'AI 生成，仅供研究参考，不构成投资建议',
  '本内容由 AI 生成，仅供参考，不构成投资建议', '仅供研究参考', '仅供参考', '不构成投资建议',
  '请回到原始材料核验关键数字与引用', '请以原始研报为准', '请以原文为准',
  '以下为对所列机构/研报观点的结构化整理，观点归属于原作者/机构，不代表 DeepFocus 投资建议',
  '本深度稿仅整理原文可见信息，非逐句法律/事实核验', "没有证据的字段会标注'原文未提供'",
  "没有证据的字段会标注'原文未提供'，请以原始研报为准",
].map(noticeKey));
// Remove known legal boilerplate, while retaining specific missing-material,
// partial-source and generation-failure warnings from the server.
const materialLimitations = (value: unknown): string => scalar(value).split(/[。；;\n]+/)
  .map(item => item.trim()).filter(item => item && !GENERIC_NOTICES.has(noticeKey(item))).join('。');

/** Export the reader's known presentation fields, never the raw API object. */
export function buildAiInterpretationText(input: AiInterpretationTextInput): string {
  const draft = normalizeResearchDeepDraft(input.deepDraft);
  const result = record(input.result);
  if (!draft && !Object.keys(result).length && !scalar(input.result)) return '';
  const lines: string[] = [];
  const add = (label: string, value: unknown) => {
    const content = scalar(value);
    if (content) lines.push('', `【${label}】`, content);
  };
  const addList = (label: string, value: unknown) => {
    const items = strings(value);
    if (items.length) lines.push('', `【${label}】`, ...items.map((item, index) => `${index + 1}. ${item}`));
  };
  const evidence = (items?: ResearchDeepDraftEvidence[]) => {
    (items || []).forEach((item, index) => {
      const page = pageLabel(item.page, item.pages);
      const label = item.label || item.source || page || `来源 ${index + 1}`;
      const heading = page && page !== label ? `${label} · ${page}` : label;
      lines.push(`证据：${heading}${item.excerpt || item.quote ? `：${item.excerpt || item.quote}` : ''}`);
    });
  };
  const logic = (items?: ResearchDeepDraftLogicLine[]) => {
    const visible = (items || []).filter(item => item.title || item.evidence || item.chain || item.impact || item.watch);
    if (!visible.length) return;
    lines.push('', '【关键逻辑线】');
    visible.forEach((item, index) => {
      lines.push(`${index + 1}. ${item.title || `逻辑线 ${index + 1}`}`);
      if (item.evidence) lines.push(`事实：${item.evidence}`);
      if (item.chain) lines.push(`传导：${item.chain}`);
      if (item.impact) lines.push(`影响：${item.impact}`);
      if (item.watch) lines.push(`验证：${item.watch}`);
    });
  };
  const table = (item: ResearchDeepDraftTable) => {
    if (item.title) lines.push('', `表：${item.title}`);
    const columns = item.columns || [];
    if (columns.length) lines.push(columns.map(column => typeof column === 'string' ? column : column.label).join(' | '));
    (item.rows || []).forEach(row => {
      const cells = Array.isArray(row) ? row : columns.map((column, index) => row[typeof column === 'string' ? `column-${index}` : column.key]);
      lines.push(cells.map(cell => scalar(cell) || '—').join(' | '));
    });
    if (item.note) lines.push(`注：${item.note}`);
  };
  lines.push(`AI 解读｜${scalar(input.title) || draft?.title || scalar(result.title) || scalar(record(result.report).title) || '解读结果'}`);
  const generatedAt = draft?.generated_at || scalar(result.generated_at);
  const generatedDate = generatedAt?.match(/^\d{4}-\d{2}-\d{2}/)?.[0];
  if (input.date) lines.push(`日期：${input.date}`);
  else if (generatedDate) lines.push(`AI 生成日期：${generatedDate}`);
  if (draft) {
    if (draft.subtitle && !GENERIC_SUBTITLES.has(sentenceKey(draft.subtitle))) lines.push(draft.subtitle);
    const metadata = Array.from(new Set([draft.subject, draft.symbol].filter(Boolean)));
    if (metadata.length) lines.push(metadata.join(' · '));
    const introSeen = new Set<string>();
    const intro = [draft.core_conclusion, draft.executive_summary, draft.one_liner, draft.thesis,
      draft.plain_language_summary, draft.decision_implication].map(value => distinctProse(value, introSeen)).filter(Boolean);
    if (intro.length) lines.push('', '【核心观点】', ...intro);
    if (draft.metrics?.length) {
      lines.push('', '【关键数字】');
      draft.metrics.forEach(item => {
        lines.push(`${item.label}${scalar(item.value) ? `：${scalar(item.value)}` : ''}${item.unit ? ` ${item.unit}` : ''}`);
        [item.change && `变化：${item.change}`, item.context && `说明：${item.context}`, item.period && `期间：${item.period}`, item.status && `状态：${item.status}`].filter(Boolean).forEach(value => lines.push(value as string));
        evidence(item.evidence);
      });
    }
    logic(draft.logic_lines);
    const rawDraft = record(input.deepDraft);
    const hasSourceSections = [rawDraft, record(rawDraft.report)].some(source =>
      ['sections', 'chapters', 'outline'].some(key => source[key] !== null && source[key] !== undefined
        && (!Array.isArray(source[key]) || (source[key] as unknown[]).length > 0)));
    const compatibilitySection = !hasSourceSections && draft.sections.length === 1
      && draft.sections[0].id === 'section-1' && draft.sections[0].title === '核心分析';
    let sectionNumber = 0;
    draft.sections.forEach(section => {
      // Only a proven synthesized compatibility chapter repeats the opening
      // fields. Real chapter titles can supply a different company or period
      // for otherwise identical sentences, so deduplicate those chapters locally.
      const seen = compatibilitySection ? new Set(introSeen) : new Set<string>();
      const summary = distinctProse(section.summary, seen);
      const paragraphs = (section.paragraphs || []).map(item => distinctProse(item, seen)).filter(Boolean);
      const bullets = (section.bullets || []).map(item => distinctProse(item, seen)).filter(Boolean);
      if (!summary && !paragraphs.length && !bullets.length && !section.logic_lines?.length && !section.tables?.length && !section.evidence?.length) return;
      sectionNumber += 1;
      lines.push('', `【${String(sectionNumber).padStart(2, '0')} ${section.title}】`);
      if (summary) lines.push(summary);
      lines.push(...paragraphs, ...bullets.map(item => `· ${item}`));
      logic(section.logic_lines);
      (section.tables || []).forEach(table);
      evidence(section.evidence);
    });
    if (draft.tables?.length) { lines.push('', '【数据与对比】'); draft.tables.forEach(table); }
    if (draft.watchlist?.length) {
      lines.push('', '【未来跟踪清单】');
      draft.watchlist.forEach((item, index) => {
        lines.push(`${index + 1}. ${item.title}`);
        [item.signal && `信号：${item.signal}`, (item.window || item.time_window) && `窗口：${item.window || item.time_window}`,
          item.metric && `指标：${item.metric}`, item.trigger && `触发/失效：${item.trigger}`, (item.why || item.rationale) && `为什么：${item.why || item.rationale}`]
          .filter(Boolean).forEach(value => lines.push(value as string));
        evidence(item.evidence);
      });
    }
    if (draft.risks?.length) {
      lines.push('', '【风险与反方】');
      draft.risks.forEach((item, index) => {
        if (typeof item === 'string') { lines.push(`${index + 1}. ${item}`); return; }
        lines.push(`${index + 1}. ${item.title || '风险提示'}`);
        if (item.detail) lines.push(item.detail);
        if (item.trigger) lines.push(`反证条件：${item.trigger}`);
        evidence(item.evidence);
      });
    }
    if (draft.glossary?.length) {
      lines.push('', '【术语解释】'); draft.glossary.forEach(item => lines.push(`${item.term}：${item.meaning}`));
    }
    if (draft.sources?.length) {
      lines.push('', '【来源】');
      draft.sources.forEach((item, index) => lines.push(`${index + 1}. ${Array.from(new Set([item.title || item.label, item.provider, pageLabel(item.page, item.pages)].filter(Boolean))).join(' · ')}`));
    }
    add('材料说明', materialLimitations(draft.disclaimer));
  } else if (typeof input.result === 'string') {
    lines.push('', input.result);
  } else {
    const metadata = [result.subject && `标的：${scalar(result.subject)}`, scalar(result.symbol)].filter(Boolean);
    if (metadata.length) lines.push(metadata.join(' · '));
    const institution = [scalar(result.rating) && `评级：${scalar(result.rating)}`, scalar(result.target_price) && `目标价：${scalar(result.target_price)}`].filter(Boolean);
    if (institution.length) add('机构观点', institution.join(' · '));
    addList('提及标的', result.instruments);
    add('核心结论', result.one_liner);
    if (scalar(result.summary) !== scalar(result.one_liner)) add('综合综述', result.summary);
    if (scalar(result.answer) !== scalar(result.summary)) add('引用问答', result.answer);
    const items = Array.isArray(result.logic_lines) ? result.logic_lines.map(item => {
      const value = record(item);
      return { title: scalar(value.title), evidence: scalar(value.evidence), chain: scalar(value.chain), impact: scalar(value.impact), watch: scalar(value.watch) };
    }) : [];
    logic(items);
    add('核心逻辑', result.core_logic);
    add('稻草财经视角', result.df_take);
    addList('关键利好', result.bullish);
    addList('关键要点', result.key_points);
    addList('主要风险', result.bearish);
    addList('风险与不确定性', result.risks);
    addList('解析红旗', result.quality_flags);
    addList('后续追问', result.follow_up_questions);
    addList('待补材料', result.missing);
    const metrics = Array.isArray(result.key_metrics) ? result.key_metrics : Array.isArray(result.metrics) ? result.metrics : [];
    if (metrics.length) {
      lines.push('', '【关键指标】');
      metrics.forEach((metric, index) => {
        const item = record(metric);
        const label = scalar(item.metric_label) || scalar(item.label) || scalar(item.metric_key) || `指标 ${index + 1}`;
        const value = scalar(item.raw_value) || scalar(item.value) || scalar(item.normalized_value);
        lines.push(`${index + 1}. ${label}${value ? `：${value}` : ''}${scalar(item.unit) && !scalar(item.raw_value) ? ` ${scalar(item.unit)}` : ''}`);
        if (scalar(item.period)) lines.push(`期间：${scalar(item.period)}`);
        const page = pageLabel(item.source_page);
        // Like the reader's citation chips, include a short evidence locator;
        // exporting an interpretation must not copy an entire source passage.
        const excerpt = scalar(item.source_excerpt).slice(0, 80);
        if (page || excerpt) lines.push(`证据：${[page, excerpt].filter(Boolean).join(' · ')}`);
      });
    }
    if (Array.isArray(result.citations) && result.citations.length) {
      lines.push('', '【引用证据】');
      result.citations.forEach((citation, index) => {
        const item = record(citation);
        const heading = [scalar(item.title) || scalar(item.report_title) || `来源 ${index + 1}`, scalar(item.report_title), pageLabel(item.page), scalar(item.citation_id)].filter(Boolean);
        lines.push(`${index + 1}. ${Array.from(new Set(heading)).join(' · ')}`);
        const excerpt = scalar(item.text).slice(0, 80);
        if (excerpt) lines.push(`证据：${excerpt}`);
      });
    }
    add('关注重点', result.takeaway);
    add('材料说明', result.source_note);
    add('材料说明', materialLimitations(result.disclaimer));
  }
  const content = lines.map(line => sentences(line).filter(item => !GENERIC_NOTICES.has(noticeKey(item))).join('').trim()).join('\n');
  return [content, '', EXPORT_NOTICE, '—— 稻草财经', scalar(input.site) || 'https://daocaijing.com'].join('\n');
}

const IMAGE_WIDTH = 900;
const MAX_IMAGE_HEIGHT = 2400;
const PADDING = 44;
const CONTENT_TOP = 82;
const FOOTER_SPACE = 32;
const FACE = '"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif';
type ImageLine = { text: string; height: number; font: string; heading: boolean };

/** Produce complete, numbered pages; a failed or stale export never returns partial images. */
export async function renderAiInterpretationImages(
  text: string,
  options: { title?: string; isCurrent?: () => boolean } = {},
): Promise<AiInterpretationImage[]> {
  const check = () => {
    if (options.isCurrent && !options.isCurrent()) {
      const error = new Error('解读已切换，图片生成已取消'); error.name = 'AbortError'; throw error;
    }
  };
  check();
  if (!text.trim()) return [];
  if (typeof document === 'undefined') throw new Error('当前环境不能生成解读图片');
  const canvas = document.createElement('canvas');
  // One small measuring canvas, then one image canvas at a time. No 2x giant canvas.
  canvas.width = 1; canvas.height = 1;
  try {
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('无法创建图片画布，请复制文字或重试');
    const pages: ImageLine[][] = [];
    let page: ImageLine[] = [], used = 0;
    const append = (line: ImageLine) => {
      if (page.length && used + line.height > MAX_IMAGE_HEIGHT - CONTENT_TOP - FOOTER_SPACE) {
        pages.push(page); page = []; used = 0;
      }
      page.push(line); used += line.height;
    };
    const firstLine = text.split(/\r?\n/, 1)[0];
    const source = options.title?.trim() && !firstLine.includes(options.title.trim()) ? `${options.title}\n\n${text}` : text;
    for (const paragraph of source.replace(/\r\n?/g, '\n').split('\n')) {
      check();
      const heading = /^【[^]*】$/.test(paragraph.trim()) || /^AI 解读[｜|]/.test(paragraph);
      const font = `${heading ? '700 22' : '400 20'}px ${FACE}`;
      const height = heading ? 38 : 32;
      ctx.font = font;
      if (!paragraph) { append({ text: '', height: 14, font, heading: false }); continue; }
      let line = '';
      // Array.from keeps surrogate pairs intact with the repository's ES5 target.
      for (const char of Array.from(paragraph)) {
        if (line && ctx.measureText(line + char).width > IMAGE_WIDTH - PADDING * 2) {
          append({ text: line, height, font, heading }); line = char;
        } else line += char;
      }
      append({ text: line, height, font, heading });
    }
    if (page.length) pages.push(page);
    const images: AiInterpretationImage[] = [];
    for (let index = 0; index < pages.length; index += 1) {
      check();
      const lines = pages[index];
      const height = Math.max(260, CONTENT_TOP + lines.reduce((total, line) => total + line.height, 0) + FOOTER_SPACE);
      canvas.width = IMAGE_WIDTH; canvas.height = height;
      ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, IMAGE_WIDTH, height);
      ctx.fillStyle = '#c48311'; ctx.fillRect(0, 0, IMAGE_WIDTH, 5);
      ctx.textBaseline = 'top';
      ctx.font = `700 18px ${FACE}`; ctx.fillStyle = '#966514'; ctx.fillText('稻草财经 · AI 解读', PADDING, 30);
      if (pages.length > 1) {
        ctx.font = `400 16px ${FACE}`; ctx.fillStyle = '#667085'; ctx.textAlign = 'right';
        ctx.fillText(`第 ${index + 1} / ${pages.length} 页`, IMAGE_WIDTH - PADDING, 32); ctx.textAlign = 'left';
      }
      let y = CONTENT_TOP;
      for (const line of lines) {
        ctx.font = line.font; ctx.fillStyle = line.heading ? '#173a64' : '#202630';
        if (line.text) ctx.fillText(line.text, PADDING, y);
        y += line.height;
      }
      const blob = await new Promise<Blob>((resolve, reject) => {
        try { canvas.toBlob(value => value ? resolve(value) : reject(new Error('图片生成失败，请复制文字或重试')), 'image/png'); }
        catch { reject(new Error('图片生成失败，请复制文字或重试')); }
      });
      // Release pixels before checking a switched account or generating the next page.
      canvas.width = 0; canvas.height = 0;
      check();
      images.push({ blob, page: index + 1, total: pages.length, width: IMAGE_WIDTH, height });
    }
    return images;
  } finally {
    canvas.width = 0; canvas.height = 0;
  }
}
