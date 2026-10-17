import React, { createContext, useContext, useId, useRef, useState } from 'react';
import { cx } from './types';

export interface TabsItem {
  /** 稳定标识，同时作为 TabPanel 的关联键。 */
  key: string;
  label: React.ReactNode;
  disabled?: boolean;
  /** 附加计数/状态徽标内容。 */
  badge?: React.ReactNode;
}

export interface TabsProps {
  items: TabsItem[];
  /** 受控模式：当前激活的 key。 */
  activeKey?: string;
  /** 非受控模式：初始激活的 key。缺省取第一个未禁用项。 */
  defaultActiveKey?: string;
  onChange?: (key: string) => void;
  /**
   * automatic：方向键即切换（默认，适合低频切换）；
   * manual：方向键只移动焦点，Enter/Space 确认切换。
   */
  activation?: 'automatic' | 'manual';
  /** tablist 的无障碍名称。 */
  ariaLabel?: string;
  className?: string;
  /** 面板内容：<TabPanel> 必须作为 Tabs 的子元素渲染（继承 id 接线上下文）。 */
  children?: React.ReactNode;
}

export interface TabPanelProps {
  /** 对应 TabsItem.key。 */
  tabKey: string;
  /** 保持挂载（用 hidden 隐藏），适合内部状态昂贵/需要预渲染的面板。默认卸载。 */
  keepMounted?: boolean;
  children?: React.ReactNode;
  className?: string;
}

interface TabsContextValue {
  baseId: string;
  activeKey: string;
}

const TabsContext = createContext<TabsContextValue | null>(null);

function moveEnabledIndex(items: TabsItem[], from: number, step: 1 | -1): number {
  if (items.length === 0) return from;
  let idx = from;
  for (let i = 0; i < items.length; i += 1) {
    idx = (idx + step + items.length) % items.length;
    if (!items[idx].disabled) return idx;
  }
  return from;
}

/**
 * 标签页（WAI-ARIA tabs 模式）。
 *
 * - roving tabindex：激活页 tabIndex=0，其余 -1，Tab 键一次进出整组；
 * - 方向键 / Home / End 在未禁用项之间循环移动；
 * - Tab 与 Panel 通过内部 useId 自动接线 aria-controls / aria-labelledby。
 *
 * 配套 <TabPanel tabKey="..."> 渲染面板内容，TabPanel 必须置于
 * <Tabs> 的子树内（上下文提供 id 接线与激活状态）。
 */
export function Tabs({
  items,
  activeKey,
  defaultActiveKey,
  onChange,
  activation = 'automatic',
  ariaLabel,
  className,
  children,
}: TabsProps) {
  const uid = useId();
  const baseId = `dui-tabs-${uid}`;
  const tabRefs = useRef(new Map<string, HTMLButtonElement>());
  const [innerKey, setInnerKey] = useState<string | null>(null);

  const isControlled = activeKey !== undefined;
  const fallbackKey = defaultActiveKey ?? items.find((item) => !item.disabled)?.key ?? '';
  const currentKey = isControlled ? (activeKey as string) : innerKey ?? fallbackKey;

  const selectKey = (key: string) => {
    if (!isControlled) setInnerKey(key);
    onChange?.(key);
  };

  const focusTab = (key: string) => {
    tabRefs.current.get(key)?.focus();
  };

  const handleKeyDown = (event: React.KeyboardEvent) => {
    const currentIndex = items.findIndex((item) => item.key === currentKey);
    let nextIndex: number | null = null;

    switch (event.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        nextIndex = moveEnabledIndex(items, currentIndex, 1);
        break;
      case 'ArrowLeft':
      case 'ArrowUp':
        nextIndex = moveEnabledIndex(items, currentIndex, -1);
        break;
      case 'Home':
        nextIndex = items.findIndex((item) => !item.disabled);
        break;
      case 'End':
        nextIndex = (() => {
          for (let i = items.length - 1; i >= 0; i -= 1) {
            if (!items[i].disabled) return i;
          }
          return currentIndex;
        })();
        break;
      default:
        return;
    }

    event.preventDefault();
    if (nextIndex === null || nextIndex < 0 || nextIndex === currentIndex) return;
    const nextKey = items[nextIndex].key;
    focusTab(nextKey);
    if (activation === 'automatic') selectKey(nextKey);
  };

  return (
    <TabsContext.Provider value={{ baseId, activeKey: currentKey }}>
      <div
        role="tablist"
        aria-label={ariaLabel}
        className={cx('dui-tabs__list', className)}
        onKeyDown={handleKeyDown}
      >
        {items.map((item) => {
          const isActive = item.key === currentKey;
          return (
            <button
              key={item.key}
              ref={(node) => {
                if (node) tabRefs.current.set(item.key, node);
                else tabRefs.current.delete(item.key);
              }}
              type="button"
              role="tab"
              id={`${baseId}-tab-${item.key}`}
              aria-selected={isActive}
              aria-controls={`${baseId}-panel-${item.key}`}
              tabIndex={isActive ? 0 : -1}
              disabled={item.disabled}
              className={cx('dui-tabs__tab', isActive && 'dui-tabs__tab--active')}
              onClick={() => {
                if (!item.disabled) selectKey(item.key);
              }}
              // manual 模式下 Enter/Space 走原生 button 激活路径触发 onClick。
            >
              {item.label}
              {item.badge !== undefined && item.badge !== null && (
                <span className="dui-tabs__badge">{item.badge}</span>
              )}
            </button>
          );
        })}
      </div>
      {children}
    </TabsContext.Provider>
  );
}

/** 标签页内容面板。须置于对应 <Tabs> 内部以继承 id 接线与激活状态。 */
export function TabPanel({ tabKey, keepMounted = false, children, className }: TabPanelProps) {
  const ctx = useContext(TabsContext);
  if (!ctx) {
    throw new Error('TabPanel 必须置于 <Tabs> 内部使用');
  }
  const isActive = ctx.activeKey === tabKey;
  if (!isActive && !keepMounted) return null;

  return (
    <div
      role="tabpanel"
      id={`${ctx.baseId}-panel-${tabKey}`}
      aria-labelledby={`${ctx.baseId}-tab-${tabKey}`}
      tabIndex={0}
      hidden={!isActive || undefined}
      className={cx('dui-tabs__panel', className)}
    >
      {children}
    </div>
  );
}
