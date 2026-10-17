import React from 'react';
import { EmptyStateIcon } from './icons';
import { cx } from './types';

export interface EmptyStateProps {
  /** 自定义图标；不传时使用内置缺省图形。 */
  icon?: React.ReactNode;
  title: string;
  description?: string;
  /** 行动区，通常放一个 Button。 */
  action?: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}

/** 空状态占位：列表无数据、搜索无结果、加载失败后的重试入口。 */
export function EmptyState({ icon, title, description, action, className, style }: EmptyStateProps) {
  return (
    <div className={cx('dui-empty', className)} style={style}>
      <div className="dui-empty__icon" aria-hidden="true">
        {icon ?? <EmptyStateIcon />}
      </div>
      <div className="dui-empty__title">{title}</div>
      {description && <div className="dui-empty__desc">{description}</div>}
      {action && <div className="dui-empty__action">{action}</div>}
    </div>
  );
}
