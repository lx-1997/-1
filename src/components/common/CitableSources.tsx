import React from 'react';
import type { ChatCitationSource } from '../../services/agentService';
import { credibilityMeta } from '../../utils/credibility';
import './CitableSources.css';

/**
 * 可引用来源列表 —— 全站统一的「可溯源来源」展示（分析师快答 / 深研 / 圆桌共用，消除重复）。
 * 编号 + 标题（可点链接）+ 来源 + 可信度徽标。无来源不渲染。
 */
const CitableSources: React.FC<{
  sources?: ChatCitationSource[] | null;
  /** 区块标题，默认「来源」；失败兜底/深研等场景可自定义。 */
  label?: string;
}> = ({ sources, label = '来源' }) => {
  if (!sources || sources.length === 0) {
    return null;
  }
  return (
    <div className="dfx-citables">
      <div className="dfx-citables-label">{label}</div>
      {sources.map(src => {
        const meta = credibilityMeta(src.credibility);
        return (
          <a
            key={src.n}
            className="dfx-citable"
            href={src.url || undefined}
            target="_blank"
            rel="noopener noreferrer"
            onClick={src.url ? undefined : (e) => e.preventDefault()}
          >
            <span className="dfx-citable-n">{src.n}</span>
            <span className="dfx-citable-title">{src.title}</span>
            <span className="dfx-citable-from">{src.source}</span>
            {meta && (
              <span
                className={`dfx-citable-cred ${meta.tier}`}
                title={`来源可信度 ${Math.round((src.credibility as number) * 100)}%`}
              >{meta.label}</span>
            )}
          </a>
        );
      })}
    </div>
  );
};

export default CitableSources;
