import React, { useId, useState } from 'react';
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

function isExternalContentSource(source: string): boolean {
  return Boolean(source) && !/(?:稻草财经|daocaijing|dao\s*财经)/i.test(source);
}

function ContentPeriodSelect({ value, onChange }: { value?: string; onChange: (period: string) => void }) {
  return (
    <select aria-label="时间" value={value || 'all'} onChange={event => onChange(event.target.value)}>
      <option value="all">全部时间</option>
      <option value="today">今天</option>
      <option value="3d">近 3 天</option>
      <option value="7d">近 7 天</option>
    </select>
  );
}

export default function ContentCategories({ entries, value, onChange, sourceLabel, kinds, unit = '篇' }: {
  entries: ContentEntry[]; value: ContentSelection; onChange: (next: ContentSelection) => void;
  sourceLabel?: string; kinds?: Array<{ key: string; label: string }>; unit?: string;
}) {
  const id = useId();
  const [expanded, setExpanded] = useState(true);
  const count = (next: ContentSelection) => entries.filter(entry => matchesContent(entry, next)).length;
  const sources = Array.from(new Set(entries
    .map(entry => entry.source?.trim() || '')
    // This selector is for external publishers and institutions, not our own product label.
    .filter(isExternalContentSource)))
    .sort((a,b) => a.localeCompare(b, 'zh-CN'));
  if (value.source !== 'all' && !sources.includes(value.source)
    && isExternalContentSource(value.source)) sources.push(value.source);
  const selected = hasContentSelection(value);
  const path = resolveTaxonomyPath(value.topic, value.subtopic, value.detail);
  const levels: Array<{ label: string; nodes: TaxonomyNode[]; selected: string; ready: boolean }> = [
    { label: '一级分类', nodes: [...CONTENT_TAXONOMY, { key: 'other', label: '其他主题' }], selected: value.topic, ready: true },
    { label: '二级行业', nodes: path[0]?.children || [], selected: value.subtopic || 'all', ready: Boolean(path[0]?.children?.length) },
  ];
  const visiblePath = path.slice(0, 2);
  const summary = visiblePath.length ? visiblePath.map(node => node.label).join(' / ') : value.topic === 'other' ? '其他主题' : '全部行业';
  return <section className="dc-categories dc-categories-compact" aria-label="内容分类">
    <div className="dc-filter-summary">
      <button type="button" className="dc-filter-toggle" aria-expanded={expanded} aria-controls={`${id}-filters`} onClick={() => setExpanded(!expanded)}><span aria-hidden="true">☷</span><span>{summary}</span><span className="dc-filter-action">{expanded ? '收起筛选' : '筛选'}</span></button>
      <span className="dc-filter-count">{count(value)} {unit}</span>
      {selected && <button type="button" className="dc-filter-reset" onClick={() => onChange(ALL_CONTENT)}>清除分类</button>}
    </div>
    <div id={`${id}-filters`} className="dc-filter-panel" hidden={!expanded}>
      {visiblePath.length > 0 && <nav className="dc-category-breadcrumb" aria-label="分类路径"><button type="button" onClick={() => onChange(changeCategoryLevel(value, 0, 'all'))}>全部内容</button>{visiblePath.map((node, index) => <React.Fragment key={node.key}><span aria-hidden="true">›</span><button type="button" aria-current={index === visiblePath.length - 1 ? 'location' : undefined} onClick={() => onChange(changeCategoryLevel(value, index, node.key))}>{node.label}</button></React.Fragment>)}</nav>}
      <div className="dc-filter-fields" role="group" aria-label="内容主题">
        {levels.map((level,index) => <label key={level.label} className="dc-filter-field">{level.label}<select aria-label={level.label} value={level.selected} disabled={!level.ready} onChange={event => onChange(changeCategoryLevel(value,index,event.target.value))}>
          <option value="all">{!level.ready ? (index === 1 ? '先选择一级分类' : '先选择二级行业') : index === 0 ? '全部行业' : index === 1 ? '全部二级行业' : '全部细分'}</option>
          {level.nodes.map(node => <option key={node.key} value={node.key}>{node.label}（{count(changeCategoryLevel(value,index,node.key))}）</option>)}
        </select></label>)}
        {sourceLabel && <label className="dc-filter-field">{sourceLabel}<select aria-label={sourceLabel} value={value.source} onChange={event => onChange({...value,source:event.target.value})}><option value="all">全部{sourceLabel}</option>{sources.map(source => <option key={source} value={source}>{source}</option>)}</select></label>}
        <label className="dc-filter-field">时间<ContentPeriodSelect value={value.period} onChange={period => onChange({ ...value, period })} /></label>
        {kinds && <label className="dc-filter-field">类型<select aria-label="内容类型" value={value.kind} onChange={event => onChange({...value,kind:event.target.value})}><option value="all">全部类型</option>{kinds.map(kind => <option key={kind.key} value={kind.key}>{kind.label}</option>)}</select></label>}
      </div>
      <div className="dc-filter-footer"><span className="dc-category-scope">已加载 {entries.length} {unit} · 匹配 {count(value)} {unit}<span title="历史资料按标题与标签辅助归类，一篇内容可属于多个主题。">仅筛选已加载内容</span></span><button type="button" className="dc-filter-done" onClick={() => setExpanded(false)}>查看结果</button></div>
    </div>
  </section>;
}

const REPORT_TOPICS = [...CONTENT_TAXONOMY, { key: 'other', label: '其他主题' }];

function ReportFilterRow({ label, value, options, limit, onChange, children }: {
  label: string;
  value: string;
  options: Array<{ key: string; label: string }>;
  limit: number;
  onChange: (key: string) => void;
  children?: React.ReactNode;
}) {
  const visibleOptions = [{ key: 'all', label: '全部' }, ...options.slice(0, limit)];
  const inChips = visibleOptions.some(option => option.key === value);
  return (
    <div className="dc-report-filter-row">
      <div className="dc-report-filter-options" role="group" aria-label={`${label}筛选`}>
        <span className="dc-report-filter-label">{label}</span>
        <div className="dc-report-filter-chips">
          {visibleOptions.map(option => (
            <button key={option.key} type="button" aria-pressed={value === option.key}
              title={option.label} onClick={() => onChange(option.key)}>
              {option.label}
            </button>
          ))}
        </div>
        {options.length > limit && (
          <select aria-label={`更多${label}`} value={inChips ? '' : value}
            className={inChips ? undefined : 'dc-report-filter-selected'}
            onChange={event => onChange(event.target.value)}>
            <option value="" disabled>更多{label}</option>
            {options.map(option => <option key={option.key} value={option.key}>{option.label}</option>)}
          </select>
        )}
      </div>
      {children && <div className="dc-report-filter-extra">{children}</div>}
    </div>
  );
}

export function ReportCategoryFilters({ entries, matchedCount, value, onChange }: {
  entries: ContentEntry[];
  matchedCount: number;
  value: ContentSelection;
  onChange: (next: ContentSelection) => void;
}) {
  const institutions = React.useMemo(() => {
    const counts = new Map<string, number>();
    for (const entry of entries) {
      const source = entry.source?.trim() || '';
      if (isExternalContentSource(source)) counts.set(source, (counts.get(source) || 0) + 1);
    }
    if (value.source !== 'all' && isExternalContentSource(value.source) && !counts.has(value.source)) {
      counts.set(value.source, 0);
    }
    return Array.from(counts.entries())
      .sort(([a, aCount], [b, bCount]) => bCount - aCount || a.localeCompare(b, 'zh-CN'))
      .map(([name]) => ({ key: name, label: name }));
  }, [entries, value.source]);

  const subtopics = resolveTaxonomyPath(value.topic)[0]?.children || [];
  const hasSelection = hasContentSelection(value);
  return (
    <section className="dc-categories dc-report-filters" aria-label="投行研报筛选">
      <ReportFilterRow label="机构" options={institutions} value={value.source} limit={7}
        onChange={source => onChange({ ...value, source })}>
        <ContentPeriodSelect value={value.period} onChange={period => onChange({ ...value, period })} />
        <span className="dc-report-filter-count" title={`仅筛选已加载的 ${entries.length} 篇研报`}>
          {hasSelection ? `匹配 ${matchedCount} 篇` : `已加载 ${entries.length} 篇`}
        </span>
      </ReportFilterRow>
      <ReportFilterRow label="行业" options={REPORT_TOPICS} value={value.topic} limit={9}
        onChange={topic => onChange(changeCategoryLevel(value, 0, topic))}>
        {subtopics.length > 0 && (
          <select aria-label="二级行业" value={value.subtopic || 'all'}
            className={value.subtopic && value.subtopic !== 'all' ? 'dc-report-filter-selected' : undefined}
            onChange={event => onChange(changeCategoryLevel(value, 1, event.target.value))}>
            <option value="all">全部二级行业</option>
            {subtopics.map(node => <option key={node.key} value={node.key}>{node.label}</option>)}
          </select>
        )}
        {hasSelection && <button type="button" onClick={() => onChange(ALL_CONTENT)}>清除筛选</button>}
      </ReportFilterRow>
    </section>
  );
}

export function CategoryTabs({ label, value, options, onChange }: {
  label: string; value: string; options: Array<{ key: string; label: string; disabled?: boolean }>;
  onChange: (key: string) => void;
}) {
  return <div className="dc-categories"><div className="dc-category-row" role="group" aria-label={label}><span>{label}</span><div>{options.map(option => <button type="button" key={option.key} disabled={option.disabled && option.key !== value} aria-pressed={value === option.key} onClick={() => onChange(option.key)}>{option.label}</button>)}</div></div></div>;
}
