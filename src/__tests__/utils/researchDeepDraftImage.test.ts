import {
  buildResearchDeepDraftImageModel,
  researchDeepDraftToText,
} from '../../utils/researchDeepDraftImage';

describe('research deep draft export model', () => {
  const draft = {
    title: '数据中心观察：Token 支出持续攀升',
    subtitle: '从需求、价格到存储的完整证据链',
    subject: 'Nvidia',
    symbol: 'NVDA',
    source_count: 1,
    pages_analyzed: 10,
    one_liner: '需求仍在扩张，但价格分化需要验证。',
    plain_language_summary: '简单说，需求还在增长，但不同环节的价格表现不一样。',
    core_conclusion: '主线是算力需求先传导到租金，再传导到存储采购。',
    decision_implication: '后续重点验证订单和租金能否继续上行。',
    metrics: [{ label: 'Token增速', value: '47%', change: '+8pct', context: '需求仍在扩张' }],
    sections: [{
      title: 'Token 需求加速扩张',
      summary: '需求侧的独有摘要。',
      paragraphs: ['这是图片必须包含的章节独有段落，不能被 compact 投影替换。'],
      bullets: ['OpenRouter token 同比增长 47%'],
      evidence: [{ page: 3, excerpt: 'token 总量 +47%', label: 'Data Center Watch' }],
      tables: [{ title: '关键读数', columns: ['指标', '读数'], rows: [['Token', '+47%']] }],
    }],
    watchlist: [{ title: '观察 GPU 租金', window: '未来 2 个季度', metric: 'H100 租金', trigger: '连续两期回落', why: '验证需求是否透支' }],
    risks: [{ title: '价格回落风险', detail: '供给释放可能压低租金。', trigger: 'B200 价格继续下行' }],
    sources: [{ title: 'J.P. Morgan Data Center Watch', provider: 'J.P. Morgan', page: 3, kind: 'pdf' }],
    disclaimer: '仅供研究参考。',
  };

  it('keeps deep-only sections and supporting fields instead of compact bullets', () => {
    const model = buildResearchDeepDraftImageModel(draft);
    expect(model.title).toContain('Token');
    expect(model.sections[0].paragraphs[0]).toContain('章节独有段落');
    expect(model.sections[0].tables[0].rows[0]).toEqual(['Token', '+47%']);
    expect(model.watchlist[0].trigger).toContain('连续两期');
    expect(model.risks[0].detail).toContain('供给释放');
    expect(model.sources[0].provider).toBe('J.P. Morgan');
    expect(model.metrics[0].value).toBe('47%');
  });

  it('serializes the same article content for copy-text', () => {
    const output = researchDeepDraftToText(draft, { site: 'https://daocaijing.com' });
    expect(output).toContain('章节独有段落');
    expect(output).toContain('白话解释');
    expect(output).toContain('Token增速：47%');
    expect(output).toContain('未来跟踪清单');
    expect(output).toContain('连续两期回落');
    expect(output).toContain('价格回落风险');
    expect(output).toContain('J.P. Morgan Data Center Watch');
    expect(output).toContain('daocaijing.com');
  });

  it('supports nested/legacy body payloads and bounds oversized collections', () => {
    const sections = Array.from({ length: 30 }, (_, index) => ({
      title: `章节 ${index}`,
      body: `段落 ${index}`,
    }));
    const model = buildResearchDeepDraftImageModel({ report: { title: '嵌套稿', body: '正文兜底' }, sections });
    expect(model.title).toBe('嵌套稿');
    expect(model.sections.length).toBeLessThanOrEqual(16);
    expect(researchDeepDraftToText({ report: { title: '嵌套稿', body: '正文兜底' } })).toContain('正文兜底');
  });
});
