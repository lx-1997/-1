import React, { useState } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import ContentCategories, { ALL_CONTENT, matchesContent, noteKind, type ContentSelection } from '../../components/terminal/ContentCategories';

it('intersects topics and exact sources without guessing the institution from body text', () => {
  const selection = { ...ALL_CONTENT, topic: 'technology', source: '机构甲' };
  expect(matchesContent({ title: 'AI 芯片产业观察', source: '机构甲' }, selection)).toBe(true);
  expect(matchesContent({ title: 'AI 芯片产业观察', source: '机构乙' }, selection)).toBe(false);
  expect(matchesContent({ title: '石油供需观察', source: '机构甲' }, selection)).toBe(false);
  expect(matchesContent({ title: 'Airline earnings', source: '机构甲' }, selection)).toBe(false);
});
it('keeps unclassified content accessible and distinguishes notes by their stated topic', () => {
  expect(matchesContent({ title: '未提供主题的简报' }, { ...ALL_CONTENT, topic: 'other' })).toBe(true);
  expect(noteKind('公司电话交流会')).toBe('meeting');
  expect(noteKind('半导体行业供需观察')).toBe('sector');
  expect(noteKind('公司业绩点评')).toBe('company');
  expect(noteKind('机构纪要')).toBe('other');
});
it('updates result counts for intersected filters, and clears both dimensions together', () => {
  const entries = [{ title: 'AI 芯片产业', source: '甲' }, { title: '石油供需', source: '甲' }, { title: 'AI 软件', source: '乙' }];
  function Example() {
    const [value, setValue] = useState<ContentSelection>(ALL_CONTENT);
    return <><ContentCategories entries={entries} value={value} onChange={setValue} sourceLabel="来源" /><output>{entries.filter(entry => matchesContent(entry, value)).map(item => item.title).join('|')}</output></>;
  }
  render(<Example />);
  fireEvent.click(screen.getByRole('button', { name: /全部行业.*筛选/ }));
  fireEvent.change(screen.getByRole('combobox', { name: '一级分类' }), { target: { value: 'technology' } });
  fireEvent.change(screen.getByRole('combobox', { name: '来源' }), { target: { value: '甲' } });
  expect(screen.getByRole('status')).toHaveTextContent('AI 芯片产业');
  expect(screen.getByRole('status')).not.toHaveTextContent('AI 软件');
  expect(screen.getByText(/已加载 3 篇 · 匹配 1 篇/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '清除分类' }));
  expect(screen.getByRole('status')).toHaveTextContent('AI 芯片产业|石油供需|AI 软件');
});

it('filters descendants and propagates a leaf match to its ancestors without matching sibling leaves', () => {
  const memory = { title: 'HBM 供给与 DRAM 价格更新' };
  expect(matchesContent(memory, { ...ALL_CONTENT, topic: 'technology' })).toBe(true);
  expect(matchesContent(memory, { ...ALL_CONTENT, topic: 'technology', subtopic: 'semiconductors' })).toBe(true);
  expect(matchesContent(memory, { ...ALL_CONTENT, topic: 'technology', subtopic: 'semiconductors', detail: 'memory' })).toBe(true);
  expect(matchesContent(memory, { ...ALL_CONTENT, topic: 'technology', subtopic: 'semiconductors', detail: 'equipment' })).toBe(false);
  expect(matchesContent({ title: '科技行业总览' }, { ...ALL_CONTENT, topic: 'technology', subtopic: 'semiconductors', detail: 'memory' })).toBe(false);
  expect(matchesContent(memory, { ...ALL_CONTENT, topic: 'health', subtopic: 'semiconductors', detail: 'memory' })).toBe(false);
});

it('gives explicit validated classification precedence over incidental title keywords', () => {
  const entry = { title: 'AI 模型用于医疗研究', categoryPaths: [['health', 'drugs', 'innovative']] };
  expect(matchesContent(entry, { ...ALL_CONTENT, topic: 'health', subtopic: 'drugs', detail: 'innovative' })).toBe(true);
  expect(matchesContent(entry, { ...ALL_CONTENT, topic: 'technology' })).toBe(false);
});

it('reveals the next level and clears incompatible descendants when an ancestor changes', () => {
  function Example() {
    const [value, setValue] = useState<ContentSelection>(ALL_CONTENT);
    return <ContentCategories entries={[{ title: 'HBM 存储芯片价格' }, { title: 'GPU 算力芯片' }]} value={value} onChange={setValue} />;
  }
  render(<Example />);
  expect(screen.queryByRole('combobox', { name: '一级分类' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /全部行业.*筛选/ }));
  const choose = (name: string, value: string) => fireEvent.change(screen.getByRole('combobox', { name }), { target: { value } });
  choose('一级分类','technology'); choose('二级行业','semiconductors'); choose('三级细分','memory');
  expect(screen.getByRole('navigation', { name: '分类路径' })).toHaveTextContent('全部内容›科技与 AI›半导体›存储芯片');
  expect(screen.getByText(/已加载 2 篇 · 匹配 1 篇/)).toBeInTheDocument();
  choose('一级分类','health');
  expect(screen.getByRole('combobox', { name: '二级行业' })).toHaveValue('all');
  expect(screen.getByRole('combobox', { name: '三级细分' })).toBeDisabled();
  expect(screen.getByRole('navigation', { name: '分类路径' })).not.toHaveTextContent('半导体');
  fireEvent.click(screen.getByRole('button', { name: '查看结果' }));
  expect(screen.queryByRole('combobox', { name: '一级分类' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: /医药健康.*筛选/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '清除分类' }));
  expect(screen.getByRole('button', { name: /全部行业.*筛选/ })).toBeInTheDocument();
});

it('uses Beijing calendar boundaries for date ranges and excludes undated or future content', () => {
  const now = new Date('2026-10-28T02:00:00Z');
  const selection = { ...ALL_CONTENT, period: 'today' };
  expect(matchesContent({ title: '简报', publishedAt: '2026-10-27T17:00:00Z' }, selection, now)).toBe(true);
  expect(matchesContent({ title: '简报', publishedAt: '2026-10-27T15:00:00Z' }, selection, now)).toBe(false);
  expect(matchesContent({ title: '简报', publishedAt: '2026-10-29' }, selection, now)).toBe(false);
  expect(matchesContent({ title: '简报' }, selection, now)).toBe(false);
});

it('does not classify every broker report as finance just because its feed is tagged 海外投行', () => {
  const entry = { title: '石油手册：供应扰动与原油定价', tags: ['#海外投行报告#'] };
  expect(matchesContent(entry, { ...ALL_CONTENT, topic: 'finance' })).toBe(false);
  expect(matchesContent(entry, { ...ALL_CONTENT, topic: 'energy', subtopic: 'oil-gas', detail: 'crude' })).toBe(true);
  expect(matchesContent({ title: '证券与投行业务收入', tags: ['#海外投行报告#'] }, { ...ALL_CONTENT, topic: 'finance' })).toBe(true);
});
