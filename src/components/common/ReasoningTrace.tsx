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

/**
 * 可审计推理轨迹：渲染 Orchestrator 返回的 reasoning_trace。
 * phase==='tool' 的步骤是 AI 原生 tool-use 的真实工具调用记录（不是隐藏推理原文），
 * 因此当出现工具调用时，标题突出「调用了 N 个工具」。
 */
const ReasoningTrace: React.FC<ReasoningTraceProps> = ({ steps, defaultOpen = false }) => {
  const [open, setOpen] = useState(defaultOpen);
  if (!steps || steps.length === 0) return null;

  const toolCount = steps.filter((s) => s.phase === 'tool').length;
  const summary = toolCount > 0 ? `调用了 ${toolCount} 个工具` : `推理 ${steps.length} 步`;

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
        {summary} · {open ? '收起' : '展开'}
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
                  {step.title}
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
