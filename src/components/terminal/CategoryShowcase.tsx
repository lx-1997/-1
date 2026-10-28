import React, { useState } from 'react';
import ContentCategories, { ALL_CONTENT, matchesContent, type ContentEntry, type ContentSelection } from './ContentCategories';

// Development-only samples; never fetched as research or bundled in production.
const SAMPLES: ContentEntry[] = [
  { title: '【分类样例】HBM 与 DRAM 存储芯片供需', source: '样例机构甲', tags: ['存储芯片'], publishedAt: '2026-10-28T08:00:00Z' },
  { title: '【分类样例】GPU 算力芯片与服务器', source: '样例机构乙', tags: ['算力芯片'], publishedAt: '2026-10-27T08:00:00Z' },
  { title: '【分类样例】半导体光刻设备与材料', source: '样例机构甲', tags: ['光刻'], publishedAt: '2026-10-26T08:00:00Z' },
  { title: '【分类样例】大模型智能体应用', source: '样例机构丙', tags: ['大模型'], publishedAt: '2026-10-28T08:00:00Z' },
  { title: '【分类样例】天然气与 LNG 运输', source: '样例机构乙', tags: ['LNG'], publishedAt: '2026-10-28T08:00:00Z' },
  { title: '【分类样例】创新药与 ADC 研发', source: '样例机构丙', tags: ['创新药'], publishedAt: '2026-10-27T08:00:00Z' },
  { title: '【分类样例】人形机器人与灵巧手', source: '样例机构甲', tags: ['机器人'], publishedAt: '2026-10-28T08:00:00Z' },
  { title: '【分类样例】白酒消费与渠道变化', source: '样例机构乙', tags: ['白酒'], publishedAt: '2026-10-28T08:00:00Z' },
];
export default function CategoryShowcase() {
  const [selection, setSelection] = useState<ContentSelection>(ALL_CONTENT);
  const [module, setModule] = useState('深度文章');
  const visible = SAMPLES.filter(entry => matchesContent(entry, selection));
  return <main className="dc-category-showcase bbt bbt-with-sidebar">
    <header><span className="dc-eyebrow">DAOCAIJING / 分类交互验收</span><h1>逐层找到你关心的研究</h1><p>以下均为隔离样例，用于检查分类联动；不代表实际研究资料或行情。</p></header>
    <nav aria-label="内容模块">{['市场快讯', '深度文章', '投行研报', '机构纪要'].map(label => <button type="button" key={label} aria-pressed={module === label} onClick={() => { setModule(label); setSelection(ALL_CONTENT); }}>{label}</button>)}</nav>
    <section className="dc-category-demo-panel" aria-label={module}>
      <h2>{module}</h2>
      <ContentCategories entries={SAMPLES} value={selection} onChange={setSelection} sourceLabel={module === '投行研报' ? '机构' : '来源'} />
      <div className="dc-category-demo-results" aria-live="polite">{visible.length ? visible.map(entry => <article key={entry.title}><small>{entry.source} · {entry.publishedAt?.slice(0,10)}</small><h3>{entry.title}</h3><p>用于验证当前分类路径是否准确筛选到这条内容。</p></article>) : <p>当前筛选下没有样例内容，可返回上一级或清除分类。</p>}</div>
    </section>
  </main>;
}
