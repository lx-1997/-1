import React from 'react';
import { cx } from './types';

export interface SkeletonProps {
  /** text：单行文本条；circular：圆形头像位（需给定 width 与 height）；rectangular：矩形块。 */
  variant?: 'text' | 'circular' | 'rectangular';
  width?: number | string;
  height?: number | string;
  /** 渲染多行，末行自动缩短到 60% 以模拟段落收尾；仅 text 变体有意义。默认 1。 */
  lines?: number;
  /** 提供时外层以 role="status" + 视觉隐藏文本向读屏器声明加载状态。 */
  srLabel?: string;
  className?: string;
  style?: React.CSSProperties;
}

/** 骨架占位。加载态专用，不对读屏器暴露形状细节。 */
export function Skeleton({
  variant = 'text',
  width,
  height,
  lines = 1,
  srLabel,
  className,
  style,
}: SkeletonProps) {
  const linesSafe = Math.max(1, Math.floor(lines));

  const bars = Array.from({ length: linesSafe }, (_, i) => {
    const isTrailing = linesSafe > 1 && i === linesSafe - 1;
    return (
      <div
        key={i}
        className={cx('dui-skeleton', `dui-skeleton--${variant}`, linesSafe > 1 && 'dui-skeleton__line')}
        style={{
          width: isTrailing && width === undefined ? '60%' : width,
          height,
        }}
      />
    );
  });

  const inner = (
    <div className={cx('dui-skeleton-group', className)} style={style}>
      {bars}
    </div>
  );

  if (!srLabel) {
    return (
      <div aria-hidden="true" data-dui-skeleton="">
        {inner}
      </div>
    );
  }

  return (
    <div role="status" aria-live="polite" data-dui-skeleton="">
      {inner}
      <span className="dui-visually-hidden">{srLabel}</span>
    </div>
  );
}
