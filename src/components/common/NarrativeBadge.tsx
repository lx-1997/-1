import React from 'react';
import { Tag, Tooltip } from 'antd';

const PROVIDER_LABEL: Record<string, string> = {
  minimax: 'MiniMax',
  openai: 'OpenAI',
  'openai-compatible': 'AI 模型',
  cloud: 'AI 模型',
};

/**
 * 标注一段速判叙述是「AI 合成」还是「确定性规则模板」，让买方一眼分清来源。
 * 结论（verdict/score）始终由确定性引擎判定；AI 仅在既有证据上合成叙述、不改方向。
 */
const NarrativeBadge: React.FC<{ provider?: string }> = ({ provider }) => {
  if (!provider || provider === 'rule-template') {
    return (
      <Tooltip title="由确定性规则引擎按多维证据拼装，未经 AI 改写">
        <Tag style={{ fontSize: 11, marginInlineEnd: 0 }}>规则合成</Tag>
      </Tooltip>
    );
  }
  const label = PROVIDER_LABEL[provider] || provider;
  return (
    <Tooltip title={`由 ${label} 在确定性引擎判定的证据上合成叙述；结论仍由引擎判定、不可被改写`}>
      <Tag color="purple" style={{ fontSize: 11, marginInlineEnd: 0 }}>
        ✨ AI 合成 · {label}
      </Tag>
    </Tooltip>
  );
};

export default NarrativeBadge;
