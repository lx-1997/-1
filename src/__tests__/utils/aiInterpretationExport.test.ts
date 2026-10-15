import { buildAiInterpretationText, renderAiInterpretationImages } from '../../utils/aiInterpretationExport';

describe('AI interpretation complete text export', () => {
  test('keeps the seventeenth section, long paragraph tail and every table row', () => {
    const output = buildAiInterpretationText({
      title: '正在阅读的完整解读',
      deepDraft: {
        title: '服务端标题',
        core_conclusion: '完整结论',
        sections: Array.from({ length: 17 }, (_, index) => ({
          title: `章节 ${index + 1}`,
          paragraphs: [index === 0 ? '长'.repeat(1900) + '段落末尾标记' : `章节正文 ${index + 1}`],
          ...(index === 16 ? { bullets: ['第十七章尾部'] } : {}),
        })),
        tables: [{ title: '完整表格', columns: ['序号', '结果'], rows: Array.from({ length: 29 }, (_, index) => [index + 1, index === 28 ? '第二十九行末尾' : `行 ${index + 1}`]) }],
        watchlist: [{ title: '跟踪末尾', window: '下一季度', metric: '订单', trigger: '失效条件', why: '验证假设' }],
        risks: [{ title: '风险末尾', detail: '所有风险说明', trigger: '反证条件' }],
        sources: [{ title: '来源末尾', provider: '研究机构', page: 32 }],
      },
      result: { summary: '不可代替深度正文的 compact 投影' },
      site: 'https://daocaijing.com',
    });
    ['正在阅读的完整解读', '完整结论', '段落末尾标记', '第十七章尾部', '第二十九行末尾', '跟踪末尾', '失效条件', '验证假设', '风险末尾', '反证条件', '来源末尾', '第 32 页'].forEach(value => expect(output).toContain(value));
    expect(output).not.toContain('compact 投影');
    expect(output.match(/【\d+ 章节/g)).toHaveLength(17);
  });

  test('shares nested compatibility normalization with the reader and retains keyed table rows', () => {
    const output = buildAiInterpretationText({ deepDraft: { report: {
      title: '嵌套稿', body: '正文兼容尾部',
      tables: [{ columns: [{ key: 'metric', label: '指标' }, { key: 'value', label: '数值' }], rows: [{ metric: '净值', value: 42 }], note: '表格注释' }],
      logicLines: [{ title: '逻辑链', evidence: '事实', chain: '传导', impact: '影响', watch: '验证' }],
      metrics: [{ label: '读数', value: 9, unit: '%', context: '指标说明' }],
      glossary: { 术语: '术语解释末尾' },
      source_coverage: { pages_read: 8, total_pages: 10 },
    } } });
    ['正文兼容尾部', '指标 | 数值', '净值 | 42', '表格注释', '逻辑链', '传导：传导', '验证：验证', '读数：9 %', '指标说明', '术语解释末尾'].forEach(value => expect(output).toContain(value));
    expect(output).not.toContain('证据覆盖');
  });

  test('retains all ordinary analysis fields and collection tails without copying API credentials', () => {
    const output = buildAiInterpretationText({ result: {
      title: '新闻标题', subject: 'ABC', summary: '综述'.repeat(1900) + '综述末尾', one_liner: '核心结论',
      core_logic: '核心逻辑末尾', df_take: '原创点评末尾', takeaway: '补充启示末尾',
      bullish: ['利好末尾'], bearish: ['利空末尾'], risks: ['风险末尾'],
      key_points: Array.from({ length: 101 }, (_, index) => `关键点 ${index + 1}`),
      instruments: ['ABC', 'XYZ'], market: 'A股', rating: '关注', target_price: '123', confidence: 0.72,
      source_note: '材料说明末尾', provider: '模型引擎', pages_analyzed: 18,
      logic_lines: [{ title: '逻辑线标题', evidence: '事实内容', chain: '传导内容', impact: '影响内容', watch: '验证内容', api_key: '不要复制的密码' }],
      authorization: '不要复制的令牌', raw_response: { unknown: '不要复制的原始字段' },
    }, date: '2026-10-12' });
    ['综述末尾', '核心逻辑末尾', '原创点评末尾', '补充启示末尾', '利好末尾', '利空末尾', '风险末尾', '关键点 101', 'XYZ', '评级：关注', '目标价：123', '材料说明末尾', '验证内容', '2026-10-12'].forEach(value => expect(output).toContain(value));
    expect(output).not.toMatch(/不要复制|证据置信度|模型引擎|分析 18 页/);
  });

  test('exports displayed scalars but never dumps private object fields or hidden source URLs', () => {
    const output = buildAiInterpretationText({ deepDraft: {
      title: '可见稿', api_key: 'private-key',
      sections: [{ title: '可见章节', paragraphs: ['可见正文'], tables: [{ columns: ['单元格'], rows: [[{ token: 'private-cell' }]] }] }],
      sources: [{ title: '可见来源', url: 'https://example.com/report?token=private-url', provider: '机构' }],
      metrics: [{ label: '可见指标', value: { password: 'private-metric' } }],
    } });
    expect(output).toContain('可见正文');
    expect(output).toContain('可见来源');
    expect(output).not.toMatch(/private-|token|password|api_key/);
  });

  test('accepts plain AI prose and handles an absent result', () => {
    expect(buildAiInterpretationText({ result: '完整自然语言回答末尾', title: '回答' })).toContain('完整自然语言回答末尾');
    expect(buildAiInterpretationText({})).toBe('');
  });

  test('keeps professional metrics, red flags, risks, questions and every citation together', () => {
    const output = buildAiInterpretationText({ result: {
      report: { title: '专业分析', file_path: 'private-report-path' }, summary: '专业综述末尾',
      key_metrics: Array.from({ length: 7 }, (_, index) => ({ metric_label: `指标${index + 1}`, raw_value: `${index + 1}%`, period: '2026Q3', source_page: index + 1, source_excerpt: '指标证据'.repeat(50), metadata: { api_key: 'private-key' } })),
      quality_flags: Array.from({ length: 5 }, (_, index) => `红旗${index + 1}`),
      risks: ['独立风险末尾'], follow_up_questions: ['完整追问末尾'],
      citations: Array.from({ length: 7 }, (_, index) => ({ title: `引用${index + 1}`, report_title: '研报来源', page: index + 1, citation_id: `cite-${index + 1}`, text: `引用证据${index + 1}` + '文'.repeat(100) + '不复制长原文尾部', metadata: { token: 'private-token' } })),
      disclaimer: '专业分析原免责声明末尾',
    } });
    ['专业分析', '专业综述末尾', '指标7：7%', '红旗5', '独立风险末尾', '完整追问末尾', '引用7', '引用证据7', 'cite-7', '专业分析原免责声明末尾'].forEach(value => expect(output).toContain(value));
    expect(output).not.toMatch(/private-|不复制长原文尾部/);
  });

  test('exports a RAG answer with its metrics, citation evidence and missing-material list', () => {
    const output = buildAiInterpretationText({ title: '引用问答', result: {
      answer: '回答'.repeat(1900) + '问答末尾', confidence: 0.84,
      metrics: [{ metric_label: '营收', value: 0, unit: '亿元', source_page: 7, source_excerpt: '完整指标短证据' }],
      citations: [{ title: '回答来源', report_title: '引用报告', page: 7, citation_id: 'citation-7', text: '回答原文短证据', score: 0.93 }],
      missing: ['缺少未来订单明细', '缺少利润率末尾'], disclaimer: '问答原免责声明',
    } });
    ['问答末尾', '营收：0 亿元', '第 7 页', '完整指标短证据', '回答原文短证据', 'citation-7', '缺少利润率末尾', '问答原免责声明'].forEach(value => expect(output).toContain(value));
    expect(output).not.toMatch(/证据置信度|相关度/);
  });

  test('combines opening fields once while preserving real chapter context', () => {
    const output = buildAiInterpretationText({ deepDraft: {
      title: 'ASML', core_conclusion: '毛利率上行。需求延伸至2029年。',
      executive_summary: '毛利率上行。估值采用28x 2028年PE或1x PEG。',
      one_liner: ' 毛利率 上行。', thesis: '需求延伸至2029年。',
      plain_language_summary: '价格与产品组合支持盈利。', decision_implication: '关注订单落地。',
      sections: [
        { title: '完全重复', summary: '毛利率上行。', paragraphs: ['需求延伸至2029年。'] },
        { title: '独有事实', summary: '需求延伸至2029年。', paragraphs: ['估值采用28x 2028年PE或1x PEG。现价€1,509.60，目标价€2,200。'] },
      ],
    } });
    expect(output.match(/【核心观点】/g)).toHaveLength(1);
    expect(output).not.toMatch(/【核心结论】|【核心判断】|【编辑摘要】|【主线】|【白话解释】|【研究启示】/);
    const intro = output.split('【01 ')[0];
    ['毛利率上行。', '需求延伸至2029年。', '估值采用28x 2028年PE或1x PEG。'].forEach(value => expect(intro.split(value)).toHaveLength(2));
    ['价格与产品组合支持盈利。', '关注订单落地。', '现价€1,509.60，目标价€2,200。', '【01 完全重复】', '【02 独有事实】'].forEach(value => expect(output).toContain(value));
  });

  test('retains equal figures for different companies and periods in real or nested chapters', () => {
    const sections = [
      { title: '2025年 A公司', summary: '营业利润率25%。', paragraphs: ['营业利润率25%。'] },
      { title: '2026年 B公司', paragraphs: ['营业利润率25%。'] },
    ];
    const variants = [
      { title: '经营对比', core_conclusion: '营业利润率25%。', sections },
      { report: { title: '经营对比', core_conclusion: '营业利润率25%。', sections } },
      { report: { title: '经营对比', core_conclusion: '营业利润率25%。', chapters: sections } },
      { report: { title: '经营对比', core_conclusion: '营业利润率25%。', outline: sections } },
    ];
    variants.forEach(deepDraft => {
      const output = buildAiInterpretationText({ deepDraft });
      expect(output).toContain('【01 2025年 A公司】\n营业利润率25%。');
      expect(output).toContain('【02 2026年 B公司】\n营业利润率25%。');
      // One opening statement, plus one statement under each chapter's context.
      expect(output.split('营业利润率25%。')).toHaveLength(4);
    });
  });

  test('preserves financial number, currency, sign, date and opposing-view differences', () => {
    const distinct = ['收入€11.668bn。', '收入$11.668bn。', '毛利率55%。', '毛利率56%。', '同比+3%。', '同比-3%。', '2027年扩产。', '2028年扩产。', '维持看多。', '转为看空。'];
    const output = buildAiInterpretationText({ deepDraft: {
      title: '数字与方向', core_conclusion: distinct.slice(0, 5).join(''), executive_summary: distinct.slice(5).join(''),
      sections: [{ title: '机构观点', paragraphs: ['J.P. Morgan 覆盖 ASML.AS，EPS €12.9 vs €10.87。'] }],
    } });
    [...distinct, 'J.P. Morgan 覆盖 ASML.AS，EPS €12.9 vs €10.87。'].forEach(value => expect(output).toContain(value));
  });

  test('does not repeat the normalizer-generated compatibility chapter or join newline facts', () => {
    const output = buildAiInterpretationText({ deepDraft: {
      title: '兼容稿', core_conclusion: 'Revenue €11.668bn\nEPS €12.9', executive_summary: '独有订单事实。',
    } });
    expect(output).toContain('Revenue €11.668bn\nEPS €12.9');
    expect(output.split('Revenue €11.668bn')).toHaveLength(2);
    expect(output.split('独有订单事实。')).toHaveLength(2);
    expect(output).not.toContain('核心分析');
  });

  test('keeps real subtitles, source attribution and material limits while hiding technical boilerplate', () => {
    const disclaimer = '本内容由 AI 生成，仅供参考，不构成投资建议。以下为对所列机构/研报观点的结构化整理，观点归属于原作者/机构，不代表 DeepFocus 投资建议；本深度稿仅整理原文可见信息，非逐句法律/事实核验；没有证据的字段会标注‘原文未提供’，请以原始研报为准。仅解析前4页，后6页图表未读取。视觉解析失败，缺少订单明细。';
    const output = buildAiInterpretationText({ date: '2026-10-14', deepDraft: {
      title: 'ASML', subtitle: '基于研报页面的结构化深度整理', subject: 'ASML Holding NV', symbol: 'ASML.AS',
      source_count: 1, pages_analyzed: 4, read_time_minutes: 3, confidence: 0.45,
      source_coverage: { pages_read: 4, total_pages: 10 }, provider: '内部模型', generated_at: '2026-10-15T10:00:00+08:00',
      sections: [{ title: '经营变化', paragraphs: ['收入€11.668bn。'] }],
      sources: [{ title: 'ASML经营观察', provider: 'Citi Research', page: 4, kind: 'pdf' }], disclaimer,
    } });
    ['日期：2026-10-14', 'ASML Holding NV · ASML.AS', '【来源】', 'Citi Research', '第 4 页', '仅解析前4页，后6页图表未读取', '视觉解析失败，缺少订单明细'].forEach(value => expect(output).toContain(value));
    expect(output).not.toMatch(/报告日期|收录日期|AI 生成时间|AI 生成日期|10:00:00/);
    expect(output).not.toMatch(/结构化深度整理|个来源|分析 4 页|分钟阅读|证据置信度|证据覆盖|内部模型|生成引擎|观点归属于|非逐句法律|没有证据的字段|· pdf/);
    expect(output.match(/AI 生成，仅供研究参考，不构成投资建议。/g)).toHaveLength(1);
    expect(output.match(/不构成投资建议/g)).toHaveLength(1);
    expect(output.match(/https:\/\/daocaijing.com/g)).toHaveLength(1);
    expect(buildAiInterpretationText({ deepDraft: { title: '主题稿', subtitle: '从需求、价格到存储的完整证据链', sections: [] } })).toContain('从需求、价格到存储的完整证据链');
  });

  test('uses a short, explicitly AI-generated date only when no input date exists', () => {
    const output = buildAiInterpretationText({ deepDraft: {
      title: '日期口径', generated_at: '2026-10-15T10:00:00+08:00', sections: [],
    } });
    expect(output).toContain('AI 生成日期：2026-10-15');
    expect(output).not.toMatch(/报告日期|收录日期|10:00:00|08:00/);
  });

  test('replaces an existing standard notice in plain prose with one export notice', () => {
    const output = buildAiInterpretationText({ result: '正文。AI 生成内容仅供研究参考，不构成投资建议。' });
    expect(output).toContain('正文。');
    expect(output.match(/不构成投资建议/g)).toHaveLength(1);
  });
});

describe('AI interpretation paginated PNG export', () => {
  let canvases: HTMLCanvasElement[];
  let rendered: string[][];
  let dimensions: Array<{ width: number; height: number }>;
  let context: CanvasRenderingContext2D;
  let toBlob: jest.SpyInstance;

  beforeEach(() => {
    canvases = []; rendered = [[]]; dimensions = [];
    context = {
      measureText: jest.fn((value: string) => ({ width: Array.from(value).length * 10 })),
      fillRect: jest.fn(),
      fillText: jest.fn((value: string) => rendered[rendered.length - 1].push(value)),
    } as unknown as CanvasRenderingContext2D;
    jest.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function(this: HTMLCanvasElement) {
      canvases.push(this); return context;
    } as unknown as typeof HTMLCanvasElement.prototype.getContext);
    toBlob = jest.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(function(this: HTMLCanvasElement, callback: BlobCallback) {
      dimensions.push({ width: this.width, height: this.height });
      rendered.push([]); callback(new Blob(['png'], { type: 'image/png' }));
    });
  });

  afterEach(() => jest.restoreAllMocks());

  test('keeps the final content on numbered pages within safe canvas bounds and releases pixels', async () => {
    const fullText = buildAiInterpretationText({ deepDraft: {
      title: '长篇完整解读',
      sections: Array.from({ length: 17 }, (_, index) => ({ title: `章节${index + 1}`, paragraphs: ['正文'.repeat(1200), ...(index === 16 ? ['LAST_PAGE_CONTENT_MARKER'] : [])] })),
    } });
    const images = await renderAiInterpretationImages(fullText);
    expect(images.length).toBeGreaterThan(1);
    images.forEach((image, index) => {
      expect(image.page).toBe(index + 1); expect(image.total).toBe(images.length);
      expect(image.blob.type).toBe('image/png');
      expect(image.width).toBe(900); expect(image.height).toBeLessThanOrEqual(2400);
      expect(image.width * image.height).toBeLessThanOrEqual(2_160_000);
      expect(dimensions[index]).toEqual({ width: image.width, height: image.height });
      expect(rendered[index]).toContain(`第 ${index + 1} / ${images.length} 页`);
    });
    const tailPage = rendered.findIndex(lines => lines.some(line => line.includes('LAST_PAGE_CONTENT_MARKER')));
    expect(tailPage).toBe(images.length - 1);
    expect(canvases.every(canvas => canvas.width === 0 && canvas.height === 0)).toBe(true);
  });

  test('wraps long prose without omitting any characters or splitting surrogate pairs', async () => {
    const content = '中😀文'.repeat(200) + '独有尾部';
    await renderAiInterpretationImages(content);
    const body = rendered.flat().filter(line => line !== '稻草财经 · AI 解读' && !/^第 \d+ \/ \d+ 页$/.test(line)).join('');
    expect(body).toBe(content);
    rendered.flat().forEach(line => expect(line).not.toMatch(/^[\udc00-\udfff]|[\ud800-\udbff]$/));
  });

  test('includes an optional title in full and avoids repeating a title already present', async () => {
    await renderAiInterpretationImages('正文末尾', { title: '图片标题' });
    expect(rendered.flat()).toContain('图片标题');
    rendered = [[]];
    await renderAiInterpretationImages('AI 解读｜图片标题\n正文末尾', { title: '图片标题' });
    expect(rendered.flat().filter(line => line.includes('图片标题'))).toHaveLength(1);
  });

  test('omits one-page numbering and never injects notices into arbitrary text', async () => {
    const pages = await renderAiInterpretationImages('任意正文末尾');
    expect(pages).toHaveLength(1);
    expect(rendered.flat()).toContain('稻草财经 · AI 解读');
    expect(rendered.flat()).toContain('任意正文末尾');
    expect(rendered.flat().join('')).not.toMatch(/第 1 \/ 1 页|不构成投资建议/);
  });

  test('renders the one body notice only once across all numbered pages', async () => {
    const fullText = buildAiInterpretationText({ deepDraft: {
      title: '完整长文', sections: [{ title: '正文', paragraphs: ['全量正文'.repeat(1500) + '正文最后标记'] }],
    } });
    const pages = await renderAiInterpretationImages(fullText);
    expect(pages.length).toBeGreaterThan(1);
    expect(rendered.flat().join('')).toContain('正文最后标记');
    expect(rendered.flat().join('').match(/AI 生成，仅供研究参考，不构成投资建议。/g)).toHaveLength(1);
    expect(rendered.flat().filter(line => /^第 \d+ \/ \d+ 页$/.test(line))).toHaveLength(pages.length);
  });

  test('an unavailable canvas or encoding failure rejects rather than returning empty or partial success', async () => {
    (HTMLCanvasElement.prototype.getContext as jest.Mock).mockReturnValueOnce(null);
    await expect(renderAiInterpretationImages('内容')).rejects.toThrow('画布');
    toBlob.mockImplementationOnce((callback: BlobCallback) => callback(null));
    await expect(renderAiInterpretationImages('内容')).rejects.toThrow('图片生成失败');
    expect(canvases.every(canvas => canvas.width === 0 && canvas.height === 0)).toBe(true);
  });

  test('an encoding exception releases the canvas', async () => {
    toBlob.mockImplementationOnce(() => { throw new Error('Canvas budget exceeded'); });
    await expect(renderAiInterpretationImages('内容')).rejects.toThrow('图片生成失败');
    expect(canvases.every(canvas => canvas.width === 0 && canvas.height === 0)).toBe(true);
  });

  test('a later page failure does not report already encoded pages as a complete result', async () => {
    toBlob.mockImplementationOnce((callback: BlobCallback) => callback(new Blob(['first'])));
    toBlob.mockImplementationOnce((callback: BlobCallback) => callback(null));
    await expect(renderAiInterpretationImages('很长的正文'.repeat(2000))).rejects.toThrow('图片生成失败');
    expect(toBlob).toHaveBeenCalledTimes(2);
    expect(canvases.every(canvas => canvas.width === 0 && canvas.height === 0)).toBe(true);
  });

  test('a switched account before generation allocates no canvas', async () => {
    await expect(renderAiInterpretationImages('旧账号正文', { isCurrent: () => false })).rejects.toMatchObject({ name: 'AbortError' });
    expect(canvases).toHaveLength(0); expect(toBlob).not.toHaveBeenCalled();
  });

  test('a switched account while encoding discards the old image and releases pixels', async () => {
    let current = true;
    toBlob.mockImplementationOnce((callback: BlobCallback) => { current = false; callback(new Blob(['old-account'])); });
    await expect(renderAiInterpretationImages('旧账号正文', { isCurrent: () => current })).rejects.toMatchObject({ name: 'AbortError' });
    expect(canvases.every(canvas => canvas.width === 0 && canvas.height === 0)).toBe(true);
  });

  test('empty text produces no image', async () => {
    expect(await renderAiInterpretationImages(' \n ')).toEqual([]);
    expect(toBlob).not.toHaveBeenCalled();
  });
});
