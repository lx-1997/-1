import React from 'react';
import { cx, type DuiSize } from './types';

export interface CardProps {
  /** 内边距档位。传 "none" 时不施加内边距（自排版内容如表格、K线）。 */
  padding?: DuiSize | 'none';
  className?: string;
  style?: React.CSSProperties;
  children?: React.ReactNode;
}

/**
 * 基础表面容器。保持非交互语义：需要可点击卡片时，把 Button 或
 * 自行声明的交互元素放进内容区，不要让整个卡片变成 button。
 */
export function Card({ padding = 'md', className, style, children }: CardProps) {
  return (
    <div
      className={cx(
        'dui-card',
        padding !== 'none' && `dui-card--padding-${padding}`,
        className,
      )}
      style={style}
    >
      {children}
    </div>
  );
}
