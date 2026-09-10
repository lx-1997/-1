export interface ArticleOriginalParagraph {
  text: string;
  original?: string;
  language: 'zh' | 'en' | 'mixed';
}

export interface ArticleOriginalSection {
  heading?: string;
  paragraphs: ArticleOriginalParagraph[];
}

export interface ArticleOriginalDocument {
  kicker?: string;
  englishTitle?: string;
  byline?: string;
  publishedAt?: string;
  takeaways: ArticleOriginalParagraph[];
  sections: ArticleOriginalSection[];
  filteredLines: number;
}

const CJK_RE = /[\u3400-\u9fff]/g;
const LATIN_RE = /[A-Za-z]/g;
const SENTENCE_END_RE = /[。！？!?；;.][’”"'）)\]」』]*$/;
const VIEW_ORIGINAL_TOGGLE_RE = /^(?:查看英文原文|view\s+english\s+original)\s*·\s*english\s+original$/i;
const NAV_MENU_LABEL_RE = /^(?:home\s+主页|btv\+|market\s+data\s+市场数据|opinion\s+意见\/看法|audio\s+音频|originals\s+原创作品\/独家作品|magazine\s+杂志\/期刊|events\s+活动\/事件|news\s+新闻|markets\s+市场\/行情|economics\s+经济学|technology\s+技术|politics\s+政治|green\s+绿色|crypto\s+加密货币|ai\s+人工智能|work\s*&\s*life\s+工作与生活|wealth\s+财富|pursuits\s+追求\/努力实现的目标|citylab|sports\s+体育运动|equality\s+平等|management\s*&\s*work\s+管理与工作|stocks\s+股票\/证券|commodities\s+大宗商品|rates\s*&\s*bonds\s+利率与债券|currencies\s+货币\/现金|futures\s+期货|sectors\s+行业\/领域|economic\s+calendar\s+经济日历)$/i;
const AUTHOR_LINE_RE = /^(?:by\b|作者\s*[:：]|撰文\s*[:：]|记者\s*[:：])/i;
const DATE_RE = /^(?:(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}|\d{4}[-/]\d{1,2}[-/]\d{1,2})(?:\s+at\s+|\s+\d{1,2}:\d{2}|$)/i;
const ARTICLE_NEWSLETTER_RE = /^(?:jumpstart\s+your\s+morning\b|learn\s+about\s+.*\bnewsletter\b|通过《.*》的订阅服务\b|subscribe\s+to\b)/i;
const ARTICLE_BYLINE_RE = /^(?:by\b|作者\s*[:：]|撰文\s*[:：]|记者\s*[:：]|根据.{0,18}(?:报道|消息))/i;
const ARTICLE_DATELINE_RE = /^(?:[A-Z][A-Za-z .'-]{2,48},\s+)?(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}\b.*\((?:Reuters|Bloomberg)\)\s*-/i;

function cleanLine(value: string): string {
  return String(value || '')
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, ' ')
    .replace(/<\s*(?:br|\/p|\/div|\/li|\/h[1-6]|\/blockquote)\b[^>]*>/gi, '\n')
    .replace(/<\s*[^>]+>/g, ' ')
    .replace(/&nbsp;|&#160;/gi, ' ')
    .replace(/&amp;/gi, '&').replace(/&quot;/gi, '"').replace(/&#39;|&apos;/gi, "'")
    .replace(/\u00a0/g, ' ')
    .replace(/[ \t\u3000]+/g, ' ')
    .trim();
}

function stripHeadingMarker(value: string): string {
  return cleanLine(value)
    .replace(/^#{1,6}\s+/, '')
    .replace(/^\*{2,3}\s*/, '')
    .replace(/\s*\*{2,3}$/, '')
    .replace(/^[•▪◦·]\s+/, '')
    .trim();
}

function isViewOriginalToggle(value: string): boolean {
  // The reader renders a collapsed <details> whose summary text ("查看英文
  // 原文 · English original") is page chrome, not article copy. Text copied
  // from the rendered reader carries it back into the paragraph stream.
  return VIEW_ORIGINAL_TOGGLE_RE.test(cleanLine(value).replace(/\s+/g, ' ').trim());
}

function isNavMenuLabel(value: string): boolean {
  // Bloomberg's full-page footer menu: a long run of bilingual section labels
  // following the story body. Each line alone looks like a short heading, so
  // drop the lines by name and cut the window when a whole run appears.
  return NAV_MENU_LABEL_RE.test(cleanLine(value).replace(/\s+/g, ' ').trim());
}

function isAuthorCreditLine(value: string): boolean {
  // Byline lines ("作者：…") belong in the header meta, never in the body.
  return AUTHOR_LINE_RE.test(cleanLine(value).trim());
}

function compareKey(value: string): string {
  return stripHeadingMarker(value)
    .replace(/[\s\p{P}\p{S}]+/gu, '')
    .toLowerCase();
}

function isChinese(value: string): boolean {
  const cjk = (value.match(CJK_RE) || []).length;
  const latin = (value.match(LATIN_RE) || []).length;
  return cjk >= 4 && cjk >= latin;
}

function isEnglish(value: string): boolean {
  const latin = (value.match(LATIN_RE) || []).length;
  const cjk = (value.match(CJK_RE) || []).length;
  return latin >= 10 && latin > cjk * 1.5;
}

function isPair(a: string, b: string): boolean {
  return Math.min(a.length, b.length) >= 12
    && ((isEnglish(a) && isChinese(b)) || (isChinese(a) && isEnglish(b)));
}

function toParagraph(a: string, b?: string): ArticleOriginalParagraph {
  if (!b) {
    return { text: a, language: isChinese(a) ? 'zh' : isEnglish(a) ? 'en' : 'mixed' };
  }
  if (isChinese(a)) return { text: a, original: b, language: 'zh' };
  return { text: b, original: a, language: 'zh' };
}

function sharedPrefix(a: string, b: string): number {
  const left = a.toLowerCase();
  const right = b.toLowerCase();
  let index = 0;
  while (index < left.length && index < right.length && left[index] === right[index]) index += 1;
  return index;
}

function findPairPartner(lines: string[], index: number, consumed: Set<number>): number {
  // Look a bounded distance ahead for the other language of a bilingual pair,
  // skipping reader chrome (toggle labels, author credits) that OCR or a
  // copy-from-reader paste can interleave between the two halves.
  const current = stripHeadingMarker(lines[index]);
  for (let step = 1; step <= 3; step += 1) {
    const candidateIndex = index + step;
    if (candidateIndex >= lines.length) break;
    if (consumed.has(candidateIndex)) continue;
    const candidate = stripHeadingMarker(lines[candidateIndex]);
    if (!candidate) continue;
    if (isViewOriginalToggle(candidate) || isAuthorCreditLine(candidate)) continue;
    if (isPair(current, candidate)) return candidateIndex;
    // The translation normally sits directly after the English paragraph; a
    // different-language non-pair line here means the pairing is simply off.
    if (candidate.length >= 12) break;
  }
  return -1;
}

function isTakeawayHeading(value: string): boolean {
  return /takeaways?\s+by|要点总结|文章要点|主要观点|key takeaways/i.test(value);
}

function isTakeawayIntro(value: string): boolean {
  return /^(?:由|来自|提供|summary|key takeaways)/i.test(value)
    && /(AI|要点|summary|takeaways|观点)/i.test(value);
}

function isByline(value: string): boolean {
  return /^(?:by\b|作者\s*[:：]|撰文\s*[:：]|记者\s*[:：])/i.test(value)
    || /根据.{0,18}(?:报道|消息)/.test(value);
}

function isRelatedStoryCard(lines: string[], index: number): boolean {
  // "Related reads" cards alternate a short headline with an author credit
  // (the reader's collapsed-original toggle label can sit between them).
  // A card only appears after body prose, so also require the previous line
  // to read like prose rather than header chrome.
  const current = stripHeadingMarker(lines[index]);
  if (!current || current.length > 60 || SENTENCE_END_RE.test(current)) return false;
  if (isViewOriginalToggle(current) || isAuthorCreditLine(current) || isPageChromeLine(current) || isPublisherChromeLine(current)) return false;
  const prev = index > 0 ? stripHeadingMarker(lines[index - 1]) : '';
  if (!prev || !(prev.length >= 40 || SENTENCE_END_RE.test(prev))) return false;
  for (let step = 1; step <= 2; step += 1) {
    const candidate = index + step < lines.length ? stripHeadingMarker(lines[index + step]) : '';
    if (!candidate) break;
    if (isViewOriginalToggle(candidate) || isPageChromeLine(candidate)) continue;
    return isAuthorCreditLine(candidate) || isByline(candidate);
  }
  // Bloomberg-style cards also drop the author credit entirely; a short
  // unpunctuated line following prose can then be the next card headline.
  // Only treat it as a card when a later card in the block carries a credit.
  return false;
}

function isRelatedCardHeadline(lines: string[], index: number): boolean {
  // Pure-headline card variant: no author credit follows. Detect it as a
  // block of >=2 consecutive short unpunctuated lines right after prose.
  const current = stripHeadingMarker(lines[index]);
  if (!current || current.length > 60 || SENTENCE_END_RE.test(current)) return false;
  if (isViewOriginalToggle(current) || isAuthorCreditLine(current) || isPageChromeLine(current) || isPublisherChromeLine(current)) return false;
  const prev = index > 0 ? stripHeadingMarker(lines[index - 1]) : '';
  if (!isViewOriginalToggle(prev) && !(prev && (prev.length >= 40 || SENTENCE_END_RE.test(prev)))) return false;
  const next = index + 1 < lines.length ? stripHeadingMarker(lines[index + 1]) : '';
  if (!isViewOriginalToggle(next)) return false;
  return true;
}

function isFooterStart(value: string): boolean {
  return /^(?:more\s+from\b|更多来自|top\s+reads?\b|热门阅读|suggested\s+topics?\b|read\s+next\b|latest\b|our\s+standards\b|purchase\s+licen[cs]ing\s+rights\b|lseg\s+products\b|stay\s+informed\b|follow\s+us\b|home\s+首页|首页\s+news|terms?\s+of\s+service|manage\s+cookies|trademarks?|privacy\s+policy|careers\b|advertise\b|ad\s+choices|©\s*\d{4})/i.test(value);
}

const PUBLISHER_CHROME_LINES = new Set([
  'exclusive news, data and analytics for financial market professionals',
  'lseg', 'reuters', 'bloomberg', 'my news', 'sign in', 'subscribe', 'browse', 'home',
  'authors', 'archive', 'legal', 'business', 'world', 'markets', 'technology',
  'download the app (ios)', 'download the app (android)', 'newsletters',
  'advertise with us', 'careers', 'reuters news agency', 'advertising guidelines',
]);
const PAGE_CHROME_LINES = new Set([
  'daocaijing', '稻财经', 'deepfocus ai', '收起', '主菜单', '我的股票', '深度文章', '机构纪要',
  '投行研报', '财报', '财经资讯', '文章', '快讯', '研报', '公告', '行情', 'markets 市场', '每日复盘',
  '搜索深度文章', '★ 头条', 'ai 解读', '原文', '全文', '分享', '资讯同步', '浅色模式',
  '登录', '注册', '更多', '来源 · dao财经', 'subscribe 订阅',
  'save 保存 translate 翻译结果', 'takeaways 外卖食品',
]);
const INLINE_CHROME_RE = /^(?:translate\s+翻译结果|\d{1,2}:\d{2}\s*\/\s*\d{1,2}:\d{2})$/i;
const PAGE_CHROME_RE = /^(?:≡\s*)?bloomberg$|^the company\s*&\s*its products\b|^bloomberg terminal demo request\b|^bloomberg anywhere login\b|^customer support$|^subscribe\b|^\[?免费翻译服务\]?|^建议升级为\s*pro\s*会员|^错误原因\s*[:：]|^免费试用\s*pro\s*会员|^切换到.*翻译.*重试$|^.*(?:save\s+保存|translate\s+翻译结果).*$|^.*\d{1,2}:\d{2}\s*\/\s*\d{1,2}:\d{2}.*$|^\*?photographer\s*[:：]|^摄影师\s*[:：]|^(?:↗\s*)?current market cap$|^[a-z][a-z ]{0,30}['’]s\s+market cap shrinks$|^[\$]?\s*\d+(?:\.\d+)?\s*[bm]?$|^[\d\s$,.]+$/i;

function isPublisherChromeLine(value: string): boolean {
  const key = stripHeadingMarker(value).toLowerCase();
  return PUBLISHER_CHROME_LINES.has(key);
}

function isPageChromeLine(value: string): boolean {
  const key = stripHeadingMarker(value).toLowerCase();
  return PAGE_CHROME_LINES.has(key)
    || key.startsWith('daocaijing ')
    || INLINE_CHROME_RE.test(key)
    || PAGE_CHROME_RE.test(key)
    || /^(?:subscribe\s+订阅|save\s+保存(?:\s+translate\s+翻译结果)?)$/i.test(key)
    || /^来源\s*[·•|]\s*dao财经$/i.test(key);
}

function isExplicitHeading(value: string): boolean {
  return /^(?:#{1,6}\s+|\*{2,3}\s*)/.test(value);
}

function isNavigationLine(value: string): boolean {
  const key = value.toLowerCase();
  const hits = ['home', 'news', 'market data', 'markets', 'stocks', 'opinion', 'audio', 'originals', 'magazine', 'economics', 'technology', 'politics', 'green', 'crypto', 'sports', '首页', '新闻', '市场数据', '股票', '意见', '音频', '原作', '技术', '政治', '体育'];
  return isPublisherChromeLine(value) || hits.filter(item => key.includes(item)).length >= 5;
}

function trimArticleWindow(lines: string[], title = ''): string[] {
  if (!lines.length) return [];

  const titleKey = compareKey(title);
  const titleMatches = titleKey.length >= 8
    ? lines.reduce<number[]>((matches, line, index) => {
      const lineKey = compareKey(line);
      if (lineKey === titleKey || (titleKey.length > 16 && (lineKey.includes(titleKey) || titleKey.includes(lineKey)))) {
        matches.push(index);
      }
      return matches;
    }, [])
    : [];
  const anchors = lines.reduce<number[]>((matches, line, index) => {
    if (ARTICLE_BYLINE_RE.test(line) || ARTICLE_DATELINE_RE.test(line)) matches.push(index);
    return matches;
  }, []);

  let start = 0;
  // Full-page captures often contain more bylines in “Top Reads” than in the
  // opened story. Pair the first header anchor with the closest title instead
  // of blindly using the last byline on the page.
  const pairedAnchor = anchors.find(anchor => titleMatches.some(index => index <= anchor && anchor - index <= 14));
  if (pairedAnchor !== undefined) {
    const nearbyTitles = titleMatches.filter(index => index <= pairedAnchor && pairedAnchor - index <= 14);
    start = nearbyTitles[nearbyTitles.length - 1];
  } else if (titleMatches.length) {
    start = titleMatches[titleMatches.length - 1];
  } else if (anchors.length) {
    start = Math.max(0, anchors[0] - 4);
  }

  let end = lines.length;
  for (let index = start + 1; index < lines.length; index += 1) {
    if (isViewOriginalToggle(lines[index])) continue;
    if (isFooterStart(lines[index]) || ARTICLE_NEWSLETTER_RE.test(lines[index])) {
      end = index;
      break;
    }
    // A run of Bloomberg footer-menu labels means the story already ended.
    if (isNavMenuLabel(lines[index]) && isNavMenuLabel(lines[index + 1] ?? '')) {
      end = index;
      break;
    }
    // Recommendation cards (short headline + author credit) after real body prose.
    if (index > start + 2 && isRelatedStoryCard(lines, index)) {
      end = index;
      break;
    }
    // Pure-headline cards: a short unpunctuated line sandwiched between prose
    // (or a toggle) and the next toggle label is a recommendation headline.
    if (index > start + 2 && isRelatedCardHeadline(lines, index)) {
      end = index;
      break;
    }
  }
  return lines.slice(start, end).filter(line => (
    !isPageChromeLine(line)
    && !isViewOriginalToggle(line)
    && !isNavMenuLabel(line)
  ));
}

function isKicker(value: string): boolean {
  return value.length >= 4 && value.length <= 90 && value.includes('|') && !SENTENCE_END_RE.test(value);
}

function isHeadingCandidate(value: string): boolean {
  const text = stripHeadingMarker(value);
  if (text.length < 4 || text.length > 88 || SENTENCE_END_RE.test(text)) return false;
  if (/^(?:by\b|来源|原文|bloomberg\b|author\b)/i.test(text)) return false;
  if (isNavigationLine(text) || DATE_RE.test(text) || isTakeawayHeading(text)) return false;
  // A long English sentence without terminal punctuation is still usually body copy.
  if (isEnglish(text) && text.split(/\s+/).length > 11) return false;
  return true;
}

function normalizeInput(paragraphs?: string[], content?: string, title?: string): { lines: string[]; filteredLines: number } {
  const source = Array.isArray(paragraphs) && paragraphs.length > 0
    ? paragraphs
    : String(content || '').replace(/\r/g, '').split(/\n+/);
  const lines: string[] = [];
  for (const raw of source) {
    const cleanedLines = cleanLine(raw).split(/\n+/);
    for (const line of cleanedLines) {
      if (!line) continue;
      if (lines[lines.length - 1] === line) continue;
      lines.push(line);
    }
  }
  const trimmed = trimArticleWindow(lines, title);
  return { lines: trimmed, filteredLines: Math.max(0, lines.length - trimmed.length) };
}

function readTakeaways(lines: string[], headingIndex: number, endIndex: number): { items: ArticleOriginalParagraph[]; consumed: Set<number> } {
  const items: ArticleOriginalParagraph[] = [];
  const consumed = new Set<number>([headingIndex]);
  let index = headingIndex + 1;
  while (index < endIndex && (
    isTakeawayIntro(lines[index])
    || isPageChromeLine(lines[index])
    || isPublisherChromeLine(lines[index])
  )) {
    consumed.add(index);
    index += 1;
  }

  const sourceLines: string[] = [];
  while (index < endIndex && items.length < 5) {
    const current = lines[index];
    const next = lines[index + 1];
    if (next && isPair(current, next)) {
      // Bloomberg-style takeaways repeat the first body claim with more detail.
      // Treat that repeated claim as the start of the article body, not another takeaway.
      if (sourceLines.some(source => sharedPrefix(source, current) >= 28)) break;
      items.push(toParagraph(current, next));
      sourceLines.push(current);
      consumed.add(index); consumed.add(index + 1);
      index += 2;
      continue;
    }
    if (!items.length && current.length >= 24 && !isHeadingCandidate(current)) {
      items.push(toParagraph(current));
      consumed.add(index);
      index += 1;
      continue;
    }
    break;
  }
  return { items, consumed };
}

export function parseArticleOriginal(options: { paragraphs?: string[]; content?: string; title?: string }): ArticleOriginalDocument {
  const normalized = normalizeInput(options.paragraphs, options.content, options.title);
  const input = normalized.lines;
  if (!input.length) return { takeaways: [], sections: [], filteredLines: 0 };

  const titleKey = compareKey(options.title || '');
  let footerIndex = input.length;
  for (let index = 0; index < input.length; index += 1) {
    if (isFooterStart(input[index])) { footerIndex = index; break; }
  }
  const lines = input.slice(0, footerIndex);
  const filteredLines = normalized.filteredLines + input.length - lines.length;
  const consumed = new Set<number>();
  let kicker = '';
  let englishTitle = '';
  let byline = '';
  let publishedAt = '';

  lines.forEach((line, index) => {
    const plain = stripHeadingMarker(line);
    const key = compareKey(plain);
    if (titleKey && (key === titleKey || (key.length > 16 && (key.includes(titleKey) || titleKey.includes(key))))) {
      consumed.add(index);
      return;
    }
    if (isKicker(plain) && !kicker) { kicker = plain; consumed.add(index); return; }
    if (isExplicitHeading(line) && !englishTitle && isEnglish(plain)) { englishTitle = plain; consumed.add(index); return; }
    if (isExplicitHeading(line) && isChinese(plain)) { consumed.add(index); return; }
    if (isByline(plain) && !byline) { byline = plain; consumed.add(index); return; }
    if (DATE_RE.test(plain) && !publishedAt) { publishedAt = plain; consumed.add(index); }
    if (isNavigationLine(plain) || isPublisherChromeLine(plain)) consumed.add(index);
  });

  const takeawayIndex = lines.findIndex((line, index) => !consumed.has(index) && isTakeawayHeading(line));
  let takeaways: ArticleOriginalParagraph[] = [];
  if (takeawayIndex >= 0) {
    const parsed = readTakeaways(lines, takeawayIndex, lines.length);
    takeaways = parsed.items;
    parsed.consumed.forEach(index => consumed.add(index));
  }

  const sections: ArticleOriginalSection[] = [];
  let current: ArticleOriginalSection = { paragraphs: [] };
  const pushCurrent = () => {
    if (current.heading || current.paragraphs.length) sections.push(current);
    current = { paragraphs: [] };
  };

  for (let index = 0; index < lines.length; index += 1) {
    if (consumed.has(index)) continue;
    const line = stripHeadingMarker(lines[index]);
    if (!line || isFooterStart(line) || isNavigationLine(line) || isPublisherChromeLine(line)) continue;
    if (isViewOriginalToggle(line) || isAuthorCreditLine(line)) continue;
    if (isExplicitHeading(lines[index]) && isHeadingCandidate(line)) {
      if (current.heading || current.paragraphs.length) pushCurrent();
      current.heading = line;
      continue;
    }
    if (isHeadingCandidate(line) && current.paragraphs.length === 0) {
      current.heading = line;
      continue;
    }
    const next = index + 1 < lines.length ? stripHeadingMarker(lines[index + 1]) : '';
    if (!consumed.has(index + 1) && next && isPair(line, next)) {
      current.paragraphs.push(toParagraph(line, next));
      index += 1;
      continue;
    }
    // OCR order can place the translation a line or two after the English
    // paragraph (a toggle label or author credit may sit between). Look ahead
    // past those chrome lines for the nearest pairable line.
    if (isEnglish(line) || isChinese(line)) {
      const partnerIndex = findPairPartner(lines, index, consumed);
      if (partnerIndex > index) {
        const partner = stripHeadingMarker(lines[partnerIndex]);
        current.paragraphs.push(toParagraph(line, partner));
        consumed.add(partnerIndex);
        continue;
      }
    }
    current.paragraphs.push(toParagraph(line));
  }
  pushCurrent();

  return { kicker: kicker || undefined, englishTitle: englishTitle || undefined, byline: byline || undefined, publishedAt: publishedAt || undefined, takeaways, sections, filteredLines };
}
