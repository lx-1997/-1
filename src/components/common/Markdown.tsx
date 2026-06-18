import React from 'react';
import './Markdown.css';

/**
 * 轻量 Markdown 渲染器（无第三方依赖），覆盖大模型聊天输出的常见语法：
 * 标题、加粗/斜体、行内代码、代码块、有序/无序列表、引用、分隔线、链接、段落。
 * 设计上对「流式半成品文本」健壮 —— 未闭合的标记按纯文本渲染。
 */

// 当前 Markdown 渲染的引用上下文（同步渲染期间有效）。
let activeCite: { valid: Set<number>; onClick?: (n: number) => void } | null = null;

type InlineRender = (m: RegExpExecArray, key: string) => React.ReactNode;

/** 行内解析：**加粗** *斜体* `代码` [文字](链接) [n]引用。
 * key 用「文本内位置」（同样文本→同样位置→同样 key），流式重渲染时 React 能复用节点，
 * 避免之前全局自增计数器导致的每 token 全量重建（O(n²)）和输入焦点/选区丢失。 */
function parseInline(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let remaining = text;
  let offset = 0;

  // 依次尝试匹配最靠前的标记；都不匹配则整段作为文本。
  const patterns: Array<{ re: RegExp; render: InlineRender }> = [
    ...(activeCite ? [{
      re: /\[(\d+)\]/,
      render: (m: RegExpExecArray, key: string): React.ReactNode => {
        const n = Number(m[1]);
        if (!activeCite || !activeCite.valid.has(n)) {
          return m[0]; // 非有效引用编号 → 当作普通文本
        }
        const onClick = activeCite.onClick;
        return (
          <sup
            key={key}
            className="dfx-md-cite"
            onClick={onClick ? () => onClick(n) : undefined}
            role={onClick ? 'button' : undefined}
          >{n}</sup>
        );
      },
    }] : []),
    { re: /`([^`]+)`/, render: (m, key) => <code key={key} className="dfx-md-code-inline">{m[1]}</code> },
    { re: /\*\*([^*]+)\*\*/, render: (m, key) => <strong key={key}>{parseInline(m[1])}</strong> },
    { re: /__([^_]+)__/, render: (m, key) => <strong key={key}>{parseInline(m[1])}</strong> },
    { re: /\*([^*\n]+)\*/, render: (m, key) => <em key={key}>{parseInline(m[1])}</em> },
    { re: /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/, render: (m, key) => (
      <a key={key} href={m[2]} target="_blank" rel="noopener noreferrer">{m[1]}</a>
    ) },
  ];

  while (remaining) {
    let best: { index: number; length: number; render: InlineRender; match: RegExpExecArray } | null = null;
    for (const { re, render } of patterns) {
      const match = re.exec(remaining);
      if (match && (best === null || match.index < best.index)) {
        best = { index: match.index, length: match[0].length, render, match };
      }
    }
    if (!best) {
      nodes.push(remaining);
      break;
    }
    if (best.index > 0) {
      nodes.push(remaining.slice(0, best.index));
    }
    nodes.push(best.render(best.match, `m${offset + best.index}`));
    const consumed = best.index + best.length;
    offset += consumed;
    remaining = remaining.slice(consumed);
  }

  return nodes;
}

type Block =
  | { type: 'heading'; level: number; text: string }
  | { type: 'code'; lang: string; lines: string[] }
  | { type: 'ul'; items: string[] }
  | { type: 'ol'; items: string[] }
  | { type: 'quote'; lines: string[] }
  | { type: 'table'; header: string[]; rows: string[][] }
  | { type: 'hr' }
  | { type: 'p'; lines: string[] };

const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/;

/** 把表格行 "| a | b |" 切成单元格。 */
function splitTableRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\||\|$/g, '')
    .split('|')
    .map(cell => cell.trim());
}

/** 块级解析：把整段文本切成块。 */
function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n/g, '\n').split('\n');
  const blocks: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // 代码块 ```lang
    const fence = line.match(/^```(.*)$/);
    if (fence) {
      const lang = fence[1].trim();
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].startsWith('```')) {
        body.push(lines[i]);
        i += 1;
      }
      i += 1; // 跳过结束 ```
      blocks.push({ type: 'code', lang, lines: body });
      continue;
    }

    // 空行
    if (line.trim() === '') {
      i += 1;
      continue;
    }

    // 标题
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      blocks.push({ type: 'heading', level: heading[1].length, text: heading[2].trim() });
      i += 1;
      continue;
    }

    // 分隔线
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line.trim())) {
      blocks.push({ type: 'hr' });
      i += 1;
      continue;
    }

    // 无序列表
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ''));
        i += 1;
      }
      blocks.push({ type: 'ul', items });
      continue;
    }

    // 有序列表
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+[.)]\s+/, ''));
        i += 1;
      }
      blocks.push({ type: 'ol', items });
      continue;
    }

    // 引用
    if (/^\s*>\s?/.test(line)) {
      const body: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        body.push(lines[i].replace(/^\s*>\s?/, ''));
        i += 1;
      }
      blocks.push({ type: 'quote', lines: body });
      continue;
    }

    // 表格（GFM）：表头行 + 分隔行 + 数据行
    if (line.includes('|') && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
      const header = splitTableRow(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim() !== '') {
        rows.push(splitTableRow(lines[i]));
        i += 1;
      }
      blocks.push({ type: 'table', header, rows });
      continue;
    }

    // 段落（连续非空、非块起始行）
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !/^```/.test(lines[i]) &&
      !/^#{1,6}\s/.test(lines[i]) &&
      !/^\s*[-*+]\s+/.test(lines[i]) &&
      !/^\s*\d+[.)]\s+/.test(lines[i]) &&
      !/^\s*>\s?/.test(lines[i])
    ) {
      para.push(lines[i]);
      i += 1;
    }
    blocks.push({ type: 'p', lines: para });
  }

  // 合并被空行拆开的相邻同类列表（模型常在编号项之间留空行 → 否则每项各自从 1 开始）。
  const merged: Block[] = [];
  for (const block of blocks) {
    const prev = merged[merged.length - 1];
    if (
      prev &&
      (block.type === 'ul' || block.type === 'ol') &&
      prev.type === block.type
    ) {
      (prev as { items: string[] }).items.push(...(block as { items: string[] }).items);
    } else {
      merged.push(block);
    }
  }

  return merged;
}

const renderItemLines = (lines: string[]): React.ReactNode[] =>
  lines.flatMap((line, index) => (index === 0 ? parseInline(line) : [<br key={`br${index}`} />, ...parseInline(line)]));

const Markdown: React.FC<{
  content: string;
  className?: string;
  citations?: number[];
  onCitationClick?: (n: number) => void;
}> = ({ content, className, citations, onCitationClick }) => {
  activeCite = citations && citations.length > 0
    ? { valid: new Set(citations), onClick: onCitationClick }
    : null;
  const blocks = parseBlocks(content);

  return (
    <div className={`dfx-md${className ? ` ${className}` : ''}`}>
      {blocks.map((block, index) => {
        switch (block.type) {
          case 'heading': {
            const Tag = (`h${Math.min(block.level + 2, 6)}`) as keyof JSX.IntrinsicElements;
            return <Tag key={index}>{parseInline(block.text)}</Tag>;
          }
          case 'code':
            return (
              <pre key={index} className="dfx-md-code-block">
                <code>{block.lines.join('\n')}</code>
              </pre>
            );
          case 'ul':
            return (
              <ul key={index}>
                {block.items.map((item, j) => <li key={j}>{parseInline(item)}</li>)}
              </ul>
            );
          case 'ol':
            return (
              <ol key={index}>
                {block.items.map((item, j) => <li key={j}>{parseInline(item)}</li>)}
              </ol>
            );
          case 'quote':
            return <blockquote key={index}>{renderItemLines(block.lines)}</blockquote>;
          case 'table':
            return (
              <div key={index} className="dfx-md-table-wrap">
                <table className="dfx-md-table">
                  <thead>
                    <tr>{block.header.map((cell, j) => <th key={j}>{parseInline(cell)}</th>)}</tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, r) => (
                      <tr key={r}>{row.map((cell, c) => <td key={c}>{parseInline(cell)}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case 'hr':
            return <hr key={index} />;
          case 'p':
          default:
            return <p key={index}>{renderItemLines((block as { lines: string[] }).lines)}</p>;
        }
      })}
    </div>
  );
};

export default React.memo(Markdown);
