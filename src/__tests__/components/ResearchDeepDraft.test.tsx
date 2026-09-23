import React from 'react';
import { render } from '@testing-library/react';
import { normalizeResearchDeepDraft } from '../../components/ResearchDeepDraft';
import ResearchDeepDraft from '../../components/ResearchDeepDraft';

describe('ResearchDeepDraft contract normalizer', () => {
  it('normalizes the backend article shape without dropping evidence/table/watch fields', () => {
    const draft = normalizeResearchDeepDraft({
      title: 'AI 供应链深度稿',
      one_liner: '高规格零部件仍是瓶颈。',
      plain_language_summary: '简单说，需求还在，但供给端最关键的零部件跟不上。',
      decision_implication: '后续重点验证订单兑现和供给扩张速度。',
      metrics: [{ label: '订单增速', value: '18%', change: '+4pct', context: '比上期更快' }],
      glossary: [{ term: 'NET', meaning: '增加意愿减去减少意愿' }],
      source_count: 2,
      source_coverage: { pages_read: 8, total_pages: 20 },
      sections: [{
        id: 's1',
        title: '需求扩散',
        paragraphs: ['第一段。', '第二段。'],
        evidence: [{ page: 7, excerpt: '订单能见度延伸至 2027 年', source_id: 'a' }],
        tables: [{ title: '信号表', columns: ['板块', '信号'], rows: [['液冷', '高']] }]
      }],
      watchlist: [{ title: '跟踪订单', window: '未来 4–6 季度', metric: '订单', trigger: '连续上修' }],
      risks: [{ title: '需求风险', detail: '订单可能延后' }],
      sources: [{ title: '来源 A', source_id: 'a', page: 7 }]
    });

    expect(draft).not.toBeNull();
    expect(draft?.sections).toHaveLength(1);
    expect(draft?.sections[0].evidence?.[0].page).toBe(7);
    expect(draft?.sections[0].tables?.[0].rows?.[0]).toEqual(['液冷', '高']);
    expect(draft?.watchlist?.[0].trigger).toBe('连续上修');
    expect(draft?.risks?.[0]).toEqual(expect.objectContaining({ title: '需求风险' }));
    expect(draft?.source_coverage).toBe('证据覆盖 8/20');
    expect(draft?.metrics?.[0]).toEqual(expect.objectContaining({ label: '订单增速', value: '18%' }));
    expect(draft?.plain_language_summary).toContain('简单说');
    expect(draft?.glossary?.[0].term).toBe('NET');
  });

  it('keeps compact legacy reports readable while deep draft is unavailable', () => {
    const draft = normalizeResearchDeepDraft({
      title: '旧版研报',
      summary: '报告摘要',
      key_points: ['要点一', '要点二'],
      quality_flags: ['原文缺少估值口径'],
      follow_up_questions: ['下季度订单是否继续上修？']
    });

    expect(draft?.sections).toHaveLength(1);
    expect(draft?.sections[0].bullets).toEqual(['要点一', '要点二']);
    expect(draft?.risks?.[0]).toEqual('原文缺少估值口径');
    expect(draft?.watchlist?.[0].title).toContain('下季度订单');
  });

  it('returns null for empty/invalid input instead of rendering a broken article', () => {
    expect(normalizeResearchDeepDraft(null)).toBeNull();
    expect(normalizeResearchDeepDraft(undefined)).toBeNull();
    expect(normalizeResearchDeepDraft('not-an-object' as unknown as Record<string, unknown>)).toEqual(expect.objectContaining({
      title: '研报深度解读'
    }));
  });

  it('keeps nested legacy report payloads readable', () => {
    const draft = normalizeResearchDeepDraft({
      report: {
        title: '嵌套报告',
        summary: '嵌套摘要',
        core_logic: '需求到订单的传导',
        sections: [{ title: '核心判断', body: '第一段\n\n第二段' }]
      }
    });
    expect(draft?.title).toBe('嵌套报告');
    expect(draft?.executive_summary).toBe('嵌套摘要');
    expect(draft?.sections[0].paragraphs).toEqual(['第一段', '第二段']);
  });

  it('renders the full research draft directly without a quick-read mode', () => {
    const { getByText, queryByRole, queryByText } = render(
      <ResearchDeepDraft draft={{
        title: '消费趋势',
        one_liner: '需求保持韧性。',
        plain_language_summary: '简单说，消费者暂时没有明显收缩支出。',
        sections: [{ title: '需求拆解', paragraphs: ['这是完整正文。'] }]
      }} />
    );

    expect(getByText('这是完整正文。')).toBeInTheDocument();
    expect(getByText('深度研究稿')).toBeInTheDocument();
    expect(getByText('深读')).toBeInTheDocument();
    expect(queryByText('白话解释')).not.toBeInTheDocument();
    expect(queryByText('速读')).not.toBeInTheDocument();
    expect(queryByRole('button', { name: /深读/ })).not.toBeInTheDocument();
  });
});
