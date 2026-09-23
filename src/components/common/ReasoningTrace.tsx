import React, { useState } from 'react';
import { Typography } from 'antd';
import {
  CheckCircleTwoTone,
  ClockCircleOutlined,
  CloseCircleTwoTone,
  LoadingOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import type { OrchestratorReasoningStep } from '../../services/agentService';

const statusIcon = (status?: string) => {
  switch (status) {
    case 'working':
      return <LoadingOutlined style={{ color: 'var(--info)' }} />;
    case 'wait':
      return <ClockCircleOutlined style={{ color: 'var(--text-muted)' }} />;
    case 'error':
      return <CloseCircleTwoTone twoToneColor="#ef4444" />;
    default:
      return <CheckCircleTwoTone twoToneColor="#22c55e" />;
  }
};

interface ReasoningTraceProps {
  steps?: OrchestratorReasoningStep[];
  defaultOpen?: boolean;
}

const TOOL_LABELS: Record<string, string> = {
  compare_stocks: '公开行情/估值/财报/共识',
  get_stock_snapshot: '公开行情/估值/财报/共识',
  get_market_quote: '公开行情',
  get_market_data: '公开市场快照',
  get_valuation: '估值',
  get_financials: '最新财报',
  get_financial_statements: '三表/现金流',
  get_analyst_consensus: '卖方共识',
  search_our_content: '稻草财经快讯/文章',
  get_site_fast_news: '稻草财经快讯',
  get_site_articles: '稻草财经文章',
  get_recent_research: '稻草财经投行研报',
  get_daily_review: '稻草财经每日复盘',
  get_stock_news: '公开财经资讯',
  get_stock_announcements: '上市公司公告',
  get_market_snapshot: '市场全景快照',
  get_meitou_feed: '媒头最新资讯',
  search_meitou: '媒头精准检索',
  get_my_profile: '用户画像',
  get_my_watchlist: '自选股与关注',
  get_portfolio_review: '持仓组合复盘',
  get_daily_briefing: '每日投研简报',
  get_stock_research_bundle: '个股研究资料包',
  get_stock_risk_check: '个股风险核验',
  get_options_signals: '期权信号',
  get_customs_trade_snapshot: '海关贸易数据',
  get_ai_fund_snapshot: 'AI 资金流向',
  scan_cn_earnings: 'A 股财报扫描',
  scan_shareholder_changes: '股东变动扫描',
  scan_major_events: '重大事件扫描',
};

export function formatToolLabel(rawTitle: string): string {
  const raw = String(rawTitle || '').replace(/^调用\s*/, '').trim();
  return TOOL_LABELS[raw] || raw || '研究资料';
}

function toolName(step: OrchestratorReasoningStep): string {
  return formatToolLabel(step.title);
}

/**
 * 可审计推理轨迹：渲染 Orchestrator 返回的 reasoning_trace。
 * phase==='tool' 的步骤是 AI 原生 tool-use 的真实工具调用记录（不是隐藏推理原文），
 * 因此当出现工具调用时，标题突出「调用了 N 个工具」。
 */
const ReasoningTrace: React.FC<ReasoningTraceProps> = ({ steps, defaultOpen = false }) => {
  const [open, setOpen] = useState(defaultOpen);
  if (!steps || steps.length === 0) return null;

  const toolCount = steps.filter((s) => s.phase === 'tool').length;
  const sources = Array.from(new Set(
    steps.filter((s) => s.phase === 'tool').map(toolName).filter(Boolean),
  ));
  const sourceHint = sources.length > 0 ? ` · ${sources.slice(0, 2).join('、')}` : '';
  const summary = toolCount > 0
    ? `已核对 ${toolCount} 项证据${sourceHint}`
    : `推理 ${steps.length} 步`;

  return (
    <div style={{ marginTop: 6 }}>
      <span
        onClick={() => setOpen((o) => !o)}
        style={{
          cursor: 'pointer',
          fontSize: 12,
          color: 'var(--text-muted)',
          userSelect: 'none',
          display: 'inline-flex',
          alignItems: 'center',
          gap: 4,
        }}
      >
        <ThunderboltOutlined />
        {summary} · {open ? '收起' : '查看核对记录'}
      </span>
      {open && (
        <div style={{ marginTop: 6, paddingLeft: 8, borderLeft: '2px solid var(--border)' }}>
          {steps.map((step, i) => (
            <div
              key={i}
              style={{ display: 'flex', gap: 6, marginBottom: 4, fontSize: 12, lineHeight: 1.5 }}
            >
              <span style={{ flexShrink: 0, marginTop: 1 }}>{statusIcon(step.status)}</span>
              <span>
                <Typography.Text strong style={{ fontSize: 12 }}>
                  {step.phase === 'tool' ? toolName(step) : step.title}
                </Typography.Text>
                {step.detail ? (
                  <span style={{ color: 'var(--text-muted)' }}> — {step.detail}</span>
                ) : null}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default ReasoningTrace;
