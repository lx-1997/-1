import React from 'react';
import { cx, type DuiSize } from './types';

export interface SpinnerProps {
  /** 尺寸档位，默认 md。 */
  size?: DuiSize;
  /** 无障碍名称。提供时外层以 role="status" 向读屏器播报；不提供时整个指示器对读屏器隐藏。 */
  label?: string;
  className?: string;
  style?: React.CSSProperties;
}

/**
 * 加载指示器。纯 CSS 实现，无脚本开销；
 * prefers-reduced-motion 下动画由全局规则降速。
 */
export function Spinner({ size = 'md', label, className, style }: SpinnerProps) {
  const glyph = (
    <span
      className={cx('dui-spinner', `dui-spinner--${size}`, className)}
      style={style}
      aria-hidden="true"
    />
  );

  if (!label) return glyph;

  return (
    <span role="status" data-dui-spinner-status="">
      {glyph}
      <span className="dui-visually-hidden">{label}</span>
    </span>
  );
}
