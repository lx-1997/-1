/**
 * 搜索关键词高亮切段器——纯函数，无副作用。
 *
 * 把文本按关键词切成段落序列（hit=true 的段渲染层包 <mark>），不直接返回 JSX：
 * 切分规则可脱离 React 单测穷举边界，渲染层保持「拼接节点」而非 dangerouslySetInnerHTML
 * （正文含第三方付费内容，绝不引入 HTML 注入面）。
 *
 * 口径：关键词按空白分词，任一词命中即标出；拉丁字母忽略大小写；正则元字符按字面匹配。
 */

export interface HighlightSegment {
  text: string;
  hit: boolean;
}

export function splitByKeywords(text: string, keyword: string): HighlightSegment[] {
  if (!text) return [];
  const tokens = String(keyword || '')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  if (!tokens.length) return [{ text, hit: false }];
  const re = new RegExp(`(${tokens.join('|')})`, 'gi');
  const out: HighlightSegment[] = [];
  // split 带捕获组：奇数下标 = 命中词本身；空串跳过但不占序位（先按原始下标判 hit 再过滤）
  text.split(re).forEach((part, i) => {
    if (!part) return;
    out.push({ text: part, hit: i % 2 === 1 });
  });
  return out;
}
