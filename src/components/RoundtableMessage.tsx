import React from 'react';
import type { DulusRoundtableResponse, DulusDecision, DulusTurnStance } from '../services/agentService';
import Markdown from './common/Markdown';
import CitableSources from './common/CitableSources';
import './RoundtableMessage.css';

const DECISION_META: Record<DulusDecision, { label: string; tone: string }> = {
  candidate: { label: '候选机会', tone: 'positive' },
  watch: { label: '保持观察', tone: 'neutral' },
  research_more: { label: '继续研究', tone: 'info' },
  blocked: { label: '阻断', tone: 'negative' },
};

const STANCE_META: Record<DulusTurnStance, { label: string; icon: string }> = {
  evidence: { label: '证据', icon: '◆' },
  research: { label: '研究', icon: '◇' },
  risk: { label: '风险', icon: '▲' },
  operator: { label: '操作', icon: '⚙' },
  synthesis: { label: '综合', icon: '✦' },
};

/** 在对话流里渲染一次「圆桌」多角色辩论的结果。 */
const RoundtableMessage: React.FC<{ data: DulusRoundtableResponse }> = ({ data }) => {
  const decision = DECISION_META[data.decision] || { label: data.decision, tone: 'neutral' };
  const citeNums = data.citable_sources?.map(s => s.n);
  const onCite = (n: number) => {
    const src = data.citable_sources?.find(s => s.n === n);
    if (src?.url) window.open(src.url, '_blank', 'noopener');
  };

  return (
    <div className="dfx-rt">
      <div className="dfx-rt-head">
        <span className="dfx-rt-title">投研圆桌 · {data.mode === 'deep_research' ? '深度' : data.mode === 'fast' ? '快速' : '辩论'}</span>
        <span className={`dfx-rt-decision ${decision.tone}`}>{decision.label}</span>
        <span className="dfx-rt-conf">置信度 {Math.round((data.confidence || 0) * 100)}%</span>
      </div>

      <div className="dfx-rt-synthesis">
        <Markdown content={data.synthesis} citations={citeNums} onCitationClick={onCite} />
      </div>

      <div className="dfx-rt-turns">
        {data.turns.map((turn, index) => {
          const stance = STANCE_META[turn.stance] || { label: turn.stance, icon: '·' };
          return (
            <div key={turn.participant_id || index} className={`dfx-rt-turn ${turn.stance}`}>
              <div className="dfx-rt-turn-head">
                <span className="dfx-rt-stance">{stance.icon} {turn.participant_name}</span>
                <span className="dfx-rt-turn-conf">{Math.round((turn.confidence || 0) * 100)}%</span>
              </div>
              <div className="dfx-rt-turn-content">
                <Markdown content={turn.content} citations={citeNums} onCitationClick={onCite} />
              </div>
              {turn.key_points?.length > 0 && (
                <ul className="dfx-rt-points">
                  {turn.key_points.map((point, j) => <li key={j}>{point}</li>)}
                </ul>
              )}
            </div>
          );
        })}
      </div>

      <CitableSources sources={data.citable_sources} label="可溯源来源" />
      {data.sources?.length > 0 && (
        <div className="dfx-rt-sources">数据来源：{data.sources.join(' · ')}</div>
      )}
      {data.warnings?.length > 0 && (
        <div className="dfx-rt-warning">{data.warnings.join('；')}</div>
      )}
    </div>
  );
};

export default RoundtableMessage;
