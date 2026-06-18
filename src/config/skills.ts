/**
 * Agent 可调用技能目录 —— 把分散在 /api/skills/* 与 RAG/扫描器的能力汇成一份清单，
 * 供「技能」上下文 provider 注入，让 Agent 知道自己有哪些可调度的专业能力。
 */
export interface AgentSkill {
  id: string;
  name: string;
  description: string;
}

export const AGENT_SKILLS: AgentSkill[] = [
  { id: 'rag', name: '证据检索 RAG', description: '从研报/资料库按问题检索可引用证据' },
  { id: 'sentiment', name: '市场情绪扫描', description: '聚合社区与资讯情绪，给出看涨/看跌信号' },
  { id: 'cn-earnings', name: 'A股财报扫描', description: '扫描A股年报/季报/预告并抽取核心财务指标' },
  { id: 'filing-parse', name: '财报与公告拆解', description: '解析PDF公告，提取要点与红旗' },
  { id: 'scenario', name: '情景矩阵生成', description: '生成牛/基准/熊三情景与触发条件' },
  { id: 'risk-discipline', name: '风险纪律审查', description: '识别亏损路径、失效条件与仓位纪律' },
  { id: 'watchlist-monitor', name: '观察名单监控', description: '盯盘自选标的的价格/事件/告警' },
  { id: 'shareholder', name: '股东增减持扫描', description: '扫描A股控股股东/董监高增减持公告' },
  { id: 'portfolio-review', name: '组合复盘', description: '多市场组合的盈亏归因与再平衡建议' },
  { id: 'options-signal', name: '期权异动雷达', description: '从期权链快照识别异常大单与关键OI墙' },
];
