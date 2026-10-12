export interface PlatformActionField { label: string; value: string }

const FIELD_LABELS: Record<string, string> = {
  symbol: '股票代码', symbols: '标的列表', name: '股票名称', names: '股票名称', asset_name: '标的名称',
  quantity: '数量', price: '价格', entry_price: '买入价格', exit_price: '卖出价格', exit_reason: '卖出原因',
  title: '名称', objective: '研究目标', content: '内容', message: '消息内容', text: '文本', note: '备注',
  market: '市场', currency: '币种', direction: '方向', horizon: '研究周期', horizon_days: '观察天数',
  start_date: '开始日期', end_date: '结束日期', date: '日期', date_str: '日期', interval: '时间间隔',
  stop_loss: '止损价格', take_profit: '止盈价格', stop_loss_pct: '止损比例', take_profit_pct: '止盈比例',
  position_size_pct: '仓位比例', initial_capital: '初始资金', strategy: '策略', strategy_type: '策略类型',
  period: '周期', limit: '数量上限', page: '页码', offset: '起始位置', query: '搜索条件', keyword: '关键词',
  keywords: '关键词', q: '搜索内容', tags: '标签', topic: '主题', topics: '关注主题', enabled: '是否开启',
  email: '接收邮箱', url: '网址', source: '来源', source_name: '来源名称', description: '说明', summary: '摘要',
  investor_profile: '风险偏好', priority: '优先级', engine: '研究引擎', engine_config: '引擎设置',
  task_type: '任务类型', context: '背景信息', status: '状态', mode: '模式', card_type: '奖励类型', code: '兑换码',
  message_id: '资讯编号', position_id: '持仓编号', task_id: '任务编号', job_id: '实验编号',
  source_id: '数据源编号', item_id: '资料编号', report_id: '研报编号', subscription_id: '订阅编号',
  sub_id: '订阅编号', call_id: '判断编号', backtest_id: '回测编号', reason: '原因',
  delta: '本次变更', added: '添加标的', removed: '移除标的',
  tool: '研究工具', input: '输入内容', identifier: '标的名称或代码',
  sector: '行业', conviction: '确信程度', stance: '观点方向', type: '类型', category: '分类',
  api_key: '访问密钥', secret: '密钥', token: '访问凭证', password: '密码', authorization: '授权凭证',
};
const WRAPPERS = new Set(['body', 'path_params', 'query']);
const VALUE_LABELS: Record<string, string> = {
  bull: '看多', bear: '看空', long: '多头', short: '空头',
  CN: 'A 股', HK: '港股', US: '美股', CNY: '人民币', HKD: '港币', USD: '美元',
};
const labelFor = (key: string) => FIELD_LABELS[key] || key.replace(/_/g, ' ');
const scalar = (value: unknown): string => value == null ? '未填写' : typeof value === 'boolean' ? (value ? '是' : '否') : VALUE_LABELS[String(value)] || String(value);

/** Translate operation arguments into fields a user can review, without HTTP containers. */
export function platformActionFields(argumentsValue: Record<string, unknown>): PlatformActionField[] {
  const rows: PlatformActionField[] = [];
  const visit = (key: string, value: unknown, prefix = '') => {
    if (WRAPPERS.has(key) && value && typeof value === 'object' && !Array.isArray(value)) {
      Object.entries(value).forEach(([childKey, child]) => visit(childKey, child, prefix));
      return;
    }
    const label = prefix ? `${prefix} · ${labelFor(key)}` : labelFor(key);
    if (/password|secret|token|api_key|authorization/i.test(key)) { rows.push({ label, value: '已填写（隐藏）' }); return; }
    if (key === 'names' && value && typeof value === 'object' && !Array.isArray(value)) {
      rows.push({ label, value: Object.entries(value).map(([symbol, name]) => `${symbol}：${scalar(name)}`).join('、') || '无' });
    } else if (Array.isArray(value)) {
      if (value.every(item => item == null || typeof item !== 'object')) rows.push({ label, value: value.map(scalar).join('、') || '无' });
      else value.forEach((item, index) => visit(String(index + 1), item, label));
    } else if (value && typeof value === 'object') {
      Object.entries(value).forEach(([childKey, child]) => visit(childKey, child, label));
    } else rows.push({ label, value: scalar(value) });
  };
  Object.entries(argumentsValue || {}).forEach(([key, value]) => visit(key, value));
  return rows;
}
