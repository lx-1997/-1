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
    ['正文兼容尾部', '指标 | 数值', '净值 | 42', '表格注释', '逻辑链', '传导：传导', '验证：验证', '读数：9 %', '指标说明', '术语解释末尾', '证据覆盖 8/10'].forEach(value => expect(output).toContain(value));
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
    ['综述末尾', '核心逻辑末尾', '原创点评末尾', '补充启示末尾', '利好末尾', '利空末尾', '风险末尾', '关键点 101', 'XYZ', '市场：A股', '目标价：123', '72%', '材料说明末尾', '模型引擎', '18 页', '验证内容', '2026-10-12'].forEach(value => expect(output).toContain(value));
    expect(output).not.toMatch(/不要复制/);
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
    ['问答末尾', '84%', '营收：0 亿元', '第 7 页', '完整指标短证据', '回答原文短证据', 'citation-7', '相关度 0.93', '缺少利润率末尾', '问答原免责声明'].forEach(value => expect(output).toContain(value));
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
    const body = rendered.flat().filter(line => line !== '稻草财经 · AI 解读全文' && !/^第 \d+ \/ \d+ 页$/.test(line) && line !== 'AI 生成内容仅供研究参考，不构成投资建议。').join('');
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
