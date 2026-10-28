import React, { useId } from 'react';
import './content-categories.css';
import { CONTENT_TAXONOMY, resolveTaxonomyPath, taxonomyMatches, type TaxonomyNode } from './contentTaxonomy';

export interface ContentEntry { title: string; tags?: string[]; source?: string | null; kind?: string; publishedAt?: string | null; categoryPaths?: string[][]; }
export interface ContentSelection { topic: string; subtopic?: string; detail?: string; source: string; kind: string; period?: string; }
export const ALL_CONTENT: ContentSelection = { topic: 'all', source: 'all', kind: 'all' };
export const NOTE_KINDS = [
  { key: 'meeting', label: '调研交流' }, { key: 'company', label: '公司点评' }, { key: 'sector', label: '行业观察' }, { key: 'other', label: '其他纪要' },
];
export const NEWS_KINDS = [
  { key: 'critical', label: '紧急' }, { key: 'success', label: '利好标记' }, { key: 'warning', label: '利空标记' }, { key: 'info', label: '一般资讯' },
];
export function hasContentSelection(value: ContentSelection): boolean {
  return [value.topic, value.subtopic, value.detail, value.source, value.kind, value.period].some(item => item != null && item !== 'all');
}
export function changeCategoryLevel(value: ContentSelection, level: number, key: string): ContentSelection {
  if (level === 0) return { ...value, topic: key, subtopic: 'all', detail: 'all' };
  if (level === 1) return { ...value, subtopic: key, detail: 'all' };
  return { ...value, detail: key };
}
export function matchesContent(entry: ContentEntry, selection: ContentSelection, now = new Date()): boolean {
  const path = resolveTaxonomyPath(selection.topic, selection.subtopic, selection.detail);
  const depth = selection.detail && selection.detail !== 'all' ? 3 : selection.subtopic && selection.subtopic !== 'all' ? 2 : 1;
  const matches = taxonomyMatches(entry);
  const topicMatch = selection.topic === 'all' ? depth === 1 : selection.topic === 'other' ? matches.size === 0 && depth === 1
    : path.length === depth && matches.has(path.map(node => node.key).join('/'));
  if (!topicMatch || (selection.source !== 'all' && (entry.source?.trim() || '未标明来源') !== selection.source)
    || (selection.kind !== 'all' && entry.kind !== selection.kind)) return false;
  if (selection.period && selection.period !== 'all') {
    const date = new Date(entry.publishedAt || '');
    if (!Number.isFinite(date.getTime())) return false;
    const day = (value: Date) => Date.parse(value.toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' }));
    const age = Math.round((day(now) - day(date)) / 86400000);
    const days = selection.period === 'today' ? 1 : selection.period === '3d' ? 3 : selection.period === '7d' ? 7 : 0;
    if (!days || age < 0 || age >= days) return false;
  }
  return true;
}
export function noteKind(title: string, tags: string[] = []): string {
  const text = [title, ...tags].join(' ');
  if (/调研|交流|会议|访谈|问答|\bQ&A\b/i.test(text)) return 'meeting';
  if (/行业|产业|板块|供需|赛道/.test(text)) return 'sector';
  if (/公司|业绩|订单|点评|财报/.test(text)) return 'company';
  return 'other';
}

export default function ContentCategories({ entries, value, onChange, sourceLabel, kinds, unit = '篇' }: {
  entries: ContentEntry[]; value: ContentSelection; onChange: (next: ContentSelection) => void;
  sourceLabel?: string; kinds?: Array<{ key: string; label: string }>; unit?: string;
}) {
  const id = useId();
  const count = (next: ContentSelection) => entries.filter(entry => matchesContent(entry, next)).length;
  const sources = Array.from(new Set(entries.map(entry => entry.source?.trim() || '未标明来源'))).sort((a,b) => a.localeCompare(b, 'zh-CN'));
  if (value.source !== 'all' && !sources.includes(value.source)) sources.push(value.source);
  const selected = hasContentSelection(value);
  const path = resolveTaxonomyPath(value.topic, value.subtopic, value.detail);
  const levels: Array<{ label: string; nodes: TaxonomyNode[]; selected: string; ready: boolean }> = [
    { label: '一级分类', nodes: [...CONTENT_TAXONOMY, { key: 'other', label: '其他主题' }], selected: value.topic, ready: true },
    { label: '二级行业', nodes: path[0]?.children || [], selected: value.subtopic || 'all', ready: Boolean(path[0]?.children?.length) },
    { label: '三级细分', nodes: path[1]?.children || [], selected: value.detail || 'all', ready: Boolean(path[1]?.children?.length) },
  ];
  return <section className="dc-categories" aria-label="内容分类">
    <nav className="dc-category-breadcrumb" aria-label="分类路径">
      <button type="button" onClick={() => onChange(changeCategoryLevel(value, 0, 'all'))}>全部内容</button>
      {path.map((node, index) => <React.Fragment key={node.key}><span aria-hidden="true">›</span><button type="button" aria-current={index === path.length - 1 ? 'location' : undefined} onClick={() => onChange(index === 0 ? changeCategoryLevel(value, 0, node.key) : index === 1 ? changeCategoryLevel(value, 1, node.key) : value)}>{node.label}</button></React.Fragment>)}
      {value.topic === 'other' && <><span aria-hidden="true">›</span><span>其他主题</span></>}
    </nav>
    <div className="dc-category-hierarchy" role="group" aria-label="内容主题">
      {levels.map((level, index) => <div key={level.label} className="dc-category-row" role="group" aria-label={level.label}><span>{level.label}</span>
      <select className="dc-category-mobile-level" aria-label={level.label} value={level.selected} disabled={!level.ready} onChange={event => onChange(changeCategoryLevel(value, index, event.target.value))}>
        <option value="all">{!level.ready ? (index === 1 ? '先选择一级分类' : '先选择二级行业') : '全部' + (index === 0 ? '分类' : index === 1 ? '行业' : '细分')}</option>
        {level.nodes.map(node => <option key={node.key} value={node.key}>{node.label}（{count(changeCategoryLevel(value, index, node.key))}）</option>)}
      </select>
      <div className="dc-category-options">
        {!level.ready ? <span className="dc-category-placeholder">{index === 1 ? '选择一级分类后查看行业' : '选择二级行业后查看细分'}</span>
          : [{ key: 'all', label: index === 0 ? '全部' : '全部' + (index === 1 ? '行业' : '细分') }, ...level.nodes].map(node => {
            const next = changeCategoryLevel(value, index, node.key);
            return <button type="button" key={node.key} aria-pressed={level.selected === node.key} onClick={() => onChange(next)}>{node.label}<small>{count(next)}</small></button>;
          })}
      </div></div>)}
    </div>
    <div className="dc-category-facets">
    {sourceLabel && <label className="dc-category-source" htmlFor={`${id}-source`}>{sourceLabel}<select id={`${id}-source`} value={value.source} onChange={event => onChange({ ...value, source: event.target.value })}>
      <option value="all">全部{sourceLabel}（{count({ ...value, source: 'all' })}）</option>
      {sources.map(source => <option key={source} value={source}>{source}（{count({ ...value, source })}）</option>)}
    </select></label>}
    <label className="dc-category-source" htmlFor={`${id}-period`}>时间<select id={`${id}-period`} value={value.period || 'all'} onChange={event => onChange({ ...value, period: event.target.value })}>
      <option value="all">全部时间</option><option value="today">今天</option><option value="3d">近 3 天</option><option value="7d">近 7 天</option>
    </select></label>
    </div>
    {kinds && <div className="dc-category-row" role="group" aria-label="内容类型"><span>类型</span><div>{[{ key: 'all', label: '全部' }, ...kinds].map(kind => { const amount = count({ ...value, kind: kind.key }); return <button type="button" key={kind.key} aria-pressed={value.kind === kind.key} disabled={!amount && value.kind !== kind.key && kind.key !== 'all'} onClick={() => onChange({ ...value, kind: kind.key })}>{kind.label}<small>{amount}</small></button>; })}</div></div>}
    <div className="dc-category-scope">已加载 {entries.length} {unit} · 匹配 {count(value)} {unit} · 标题/标签辅助归类，多主题可能交叉{selected && <button type="button" onClick={() => onChange(ALL_CONTENT)}>清除分类</button>}</div>
  </section>;
}

export function CategoryTabs({ label, value, options, onChange }: {
  label: string; value: string; options: Array<{ key: string; label: string; disabled?: boolean }>;
  onChange: (key: string) => void;
}) {
  return <div className="dc-categories"><div className="dc-category-row" role="group" aria-label={label}><span>{label}</span><div>{options.map(option => <button type="button" key={option.key} disabled={option.disabled && option.key !== value} aria-pressed={value === option.key} onClick={() => onChange(option.key)}>{option.label}</button>)}</div></div></div>;
}
