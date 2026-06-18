/**
 * 对话路由工具：判断一条用户消息应该走「通用问答」还是「个股深度研究 Loop」。
 *
 * 这套逻辑原先在 AIChatPanel 里内联了一份，HomePage 也需要同样的判断。
 * 抽到这里统一维护，避免两处实现各自漂移。
 */

const COMMON_SYMBOL_PREFIXES = [
  'AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA', 'NVDA', 'META', 'NFLX', 'AMD', 'INTC',
  'BABA', 'JD', 'PDD', 'NIO', 'XPEV', 'LI', 'BIDU', 'TCEHY',
  'SPY', 'QQQ', 'IWM', 'DIA', 'VOO', 'ARKK', 'TLT', 'GLD', 'SLV', 'USO',
  '600', '601', '000', '002', '300', '688',
];

const RESEARCH_KEYWORDS = [
  '分析', '调研', '研究', '评估', '诊断', '怎么样', '怎么看', '如何',
  '财报', '基本面', '估值', '营收', '利润', '风险', '持仓', '前景', '推荐', '建议',
];

/** 从一段中文/英文混排的提问里，尽量抽出一个股票代码（美股 ticker 或 A 股代码前缀）。 */
export function extractStockSymbol(text: string): string | null {
  const patterns = [
    /\b([A-Z]{1,5})\b/g,
    /(?:分析|调研|研究|评估).*?\b([A-Z]{1,5})\b/i,
    /\b([A-Z]{1,5})\b.*?(?:分析|调研|研究|评估)/i,
  ];

  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (!match) {
      continue;
    }
    const candidate = (match[1] || match[0]).toUpperCase();
    const matchesCommon = COMMON_SYMBOL_PREFIXES.some(
      prefix => candidate.startsWith(prefix) || candidate === prefix
    );
    if (matchesCommon || candidate.length >= 2) {
      return candidate;
    }
  }

  return null;
}

/** 判断这条消息是不是「想对某个标的做研究」，而非闲聊或操作指令。 */
export function isResearchMessage(text: string): boolean {
  return RESEARCH_KEYWORDS.some(keyword => text.includes(keyword));
}

/** 一条消息是否应该触发个股深度研究 Loop：既能识别出标的，又是研究意图。 */
export function shouldRunStockResearch(text: string): string | null {
  const symbol = extractStockSymbol(text);
  if (symbol && isResearchMessage(text)) {
    return symbol;
  }
  return null;
}

/** 按当前时段返回中文问候语。 */
export function getGreeting(date = new Date()): string {
  const hour = date.getHours();
  if (hour < 6) return '夜深了';
  if (hour < 11) return '早上好';
  if (hour < 13) return '中午好';
  if (hour < 18) return '下午好';
  return '晚上好';
}
