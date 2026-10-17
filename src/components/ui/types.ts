/**
 * 稻草财经 UI Kit — 共享类型与工具
 *
 * 设计约束：
 * - 所有视觉值取自 src/index.css 的 CSS 令牌（--surface/--accent/--radius-* 等），
 *   不在组件内写死颜色，双主题与五色 accent 自动生效。
 * - 类名统一使用 dui- 前缀，避免与既有组件样式冲突。
 */

import type * as React from 'react';

/** 组件尺寸档位。 */
export type DuiSize = 'sm' | 'md' | 'lg';

/** 语义色调，映射到全局令牌 --positive/--negative/--warning/--info/--accent。 */
export type DuiTone = 'neutral' | 'accent' | 'positive' | 'negative' | 'warning' | 'info';

/** 极简 className 组合器：过滤假值并以空格连接。 */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}

/** 受控/非受控通用值模型。 */
export type DuiControlledValue<T> = {
  /** 受控模式：当前值。 */
  value?: T;
  /** 非受控模式：初始值。 */
  defaultValue?: T;
};

/** 合并外部传入的 ref 与内部 ref（用于 forwardRef 包装场景）。 */
export function mergeRefs<T>(
  ...refs: Array<React.Ref<T> | undefined>
): (node: T | null) => void {
  return (node) => {
    for (const ref of refs) {
      if (!ref) continue;
      if (typeof ref === 'function') {
        ref(node);
      } else {
        // RefObject 属于可变对象，直接写入 current 是官方允许的路径。
        (ref as React.MutableRefObject<T | null>).current = node;
      }
    }
  };
}
