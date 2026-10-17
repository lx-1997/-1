import React, { forwardRef } from 'react';
import { cx, type DuiSize } from './types';
import type { ButtonVariant } from './Button';

export interface IconButtonProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'className' | 'aria-label'> {
  /**
   * 无障碍名称（必填）。图标按钮没有可见文本，
   * 读屏器完全依赖该名称，因此在类型层强制。
   */
  ariaLabel: string;
  variant?: ButtonVariant;
  size?: DuiSize;
  loading?: boolean;
  className?: string;
}

/** 图标按钮。仅承载图标内容，无障碍名称在 Props 层强制声明。 */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { ariaLabel, variant = 'ghost', size = 'md', loading = false, className, disabled, children, type, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type ?? 'button'}
      className={cx('dui-btn', `dui-btn--${variant}`, `dui-btn--${size}`, 'dui-iconbtn', className)}
      disabled={disabled || loading}
      aria-label={ariaLabel}
      aria-busy={loading || undefined}
      {...rest}
    >
      {children}
    </button>
  );
});
