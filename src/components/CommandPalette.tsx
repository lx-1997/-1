import React, { useEffect, useMemo, useRef, useState } from 'react';
import { SearchOutlined, EnterOutlined } from '@ant-design/icons';
import type { Stock, ViewType } from '../types';
import { WORKSPACE_SECTIONS } from '../config/workspaces';
import './CommandPalette.css';

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  onNavigate: (view: ViewType) => void;
  onSelectStock: (stock: Stock) => void;
  stocks: Stock[];
}

type Command =
  | { kind: 'view'; id: string; label: string; detail: string; group: string; view: ViewType }
  | { kind: 'stock'; id: string; label: string; detail: string; group: string; stock: Stock };

/** 大小写无关的子序列匹配（"asc" 命中 "AiSupplyChain"）。 */
function fuzzyMatch(haystack: string, needle: string): boolean {
  if (!needle) return true;
  const h = haystack.toLowerCase();
  const n = needle.toLowerCase();
  if (h.includes(n)) return true;
  let i = 0;
  for (const ch of h) {
    if (ch === n[i]) {
      i += 1;
      if (i === n.length) return true;
    }
  }
  return false;
}

const CommandPalette: React.FC<CommandPaletteProps> = ({ open, onClose, onNavigate, onSelectStock, stocks }) => {
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const commands = useMemo<Command[]>(() => {
    const views: Command[] = WORKSPACE_SECTIONS.flatMap(section =>
      section.views.map(view => ({
        kind: 'view' as const,
        id: `view:${view.view}`,
        label: view.label,
        detail: view.detail,
        group: section.title,
        view: view.view,
      }))
    );
    const account: Command[] = [
      { kind: 'view', id: 'view:shop', label: '研究商城', detail: '研报 / 模板 / 数据资产', group: '账户', view: 'shop' },
      { kind: 'view', id: 'view:orders', label: '我的订单', detail: '已购研究资产', group: '账户', view: 'orders' },
    ];
    const stockCommands: Command[] = stocks.map(stock => ({
      kind: 'stock' as const,
      id: `stock:${stock.symbol}`,
      label: `${stock.symbol} ${stock.name}`,
      detail: '打开个股研究',
      group: '标的',
      stock,
    }));
    return [...views, ...account, ...stockCommands];
  }, [stocks]);

  const filtered = useMemo(
    () => (query ? commands.filter(cmd => fuzzyMatch(`${cmd.label} ${cmd.detail}`, query)) : commands),
    [commands, query]
  );

  useEffect(() => {
    if (open) {
      // a11y：记住打开前的焦点元素（通常是触发按钮），关闭时还原，键盘用户不丢位置。
      const previouslyFocused = document.activeElement as HTMLElement | null;
      setQuery('');
      setActiveIndex(0);
      const id = window.setTimeout(() => inputRef.current?.focus(), 30);
      return () => {
        window.clearTimeout(id);
        previouslyFocused?.focus?.();
      };
    }
    return undefined;
  }, [open]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  useEffect(() => {
    const node = listRef.current?.querySelector<HTMLElement>('.dfx-cmd-item.active');
    node?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex, filtered]);

  if (!open) {
    return null;
  }

  const runCommand = (cmd: Command) => {
    if (cmd.kind === 'view') {
      onNavigate(cmd.view);
    } else {
      onSelectStock(cmd.stock);
    }
    onClose();
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex(i => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex(i => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const cmd = filtered[activeIndex];
      if (cmd) runCommand(cmd);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    }
  };

  let lastGroup = '';

  return (
    <div className="dfx-cmd-overlay" onMouseDown={onClose}>
      <div className="dfx-cmd-panel" onMouseDown={e => e.stopPropagation()}>
        <div className="dfx-cmd-search">
          <SearchOutlined className="dfx-cmd-search-icon" />
          <input
            ref={inputRef}
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="跳转到任意视图或标的…"
            aria-label="命令面板搜索"
          />
          <kbd className="dfx-cmd-kbd">Esc</kbd>
        </div>

        <div className="dfx-cmd-list" ref={listRef}>
          {filtered.length === 0 && <div className="dfx-cmd-empty">没有匹配项</div>}
          {filtered.map((cmd, index) => {
            const showGroup = !query && cmd.group !== lastGroup;
            lastGroup = cmd.group;
            return (
              <React.Fragment key={cmd.id}>
                {showGroup && <div className="dfx-cmd-group">{cmd.group}</div>}
                <button
                  type="button"
                  className={`dfx-cmd-item${index === activeIndex ? ' active' : ''}`}
                  onMouseMove={() => setActiveIndex(index)}
                  onClick={() => runCommand(cmd)}
                >
                  <span className="dfx-cmd-item-label">{cmd.label}</span>
                  <span className="dfx-cmd-item-detail">{cmd.detail}</span>
                  {index === activeIndex && <EnterOutlined className="dfx-cmd-item-enter" />}
                </button>
              </React.Fragment>
            );
          })}
        </div>

        <div className="dfx-cmd-foot">
          <span><kbd>↑</kbd><kbd>↓</kbd> 选择</span>
          <span><kbd>↵</kbd> 打开</span>
          <span><kbd>⌘</kbd><kbd>K</kbd> 唤起</span>
        </div>
      </div>
    </div>
  );
};

export default CommandPalette;
