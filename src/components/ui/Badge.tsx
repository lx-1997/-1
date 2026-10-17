import React from 'react';
import { cx, type DuiSize, type DuiTone } from './types';

export interface BadgeProps {
  tone?: DuiTone;
  size?: DuiSize;
  /** 渲染一个实心状态圆点（同色），用于实时状态指示。 */
  dot?: boolean;
  className?: string;
  style?: React.CSSProperties;
  children?: React.ReactNode;
}

/** 语义状态徽标。色调走令牌，双主题与 accent 自动适配。 */
export function Badge({ tone = 'neutral', size = 'md', dot, className, style, children }: BadgeProps) {
  return (
    <span className={cx('dui-badge', `dui-badge--${tone}`, `dui-badge--${size}`, className)} style={style}>
      {dot && <span className="dui-badge__dot" aria-hidden="true" />}
      {children}
    </span>
  );
}
