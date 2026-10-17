import React, { forwardRef } from 'react';
import { cx, type DuiSize } from './types';
import { Spinner } from './Spinner';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';

export interface ButtonProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'className'> {
  variant?: ButtonVariant;
  size?: DuiSize;
  /**
   * 加载态：显示 Spinner 并强制禁用点击（异步提交防重复触发）。
   * 以 aria-busy="true" 对辅助技术声明。
   */
  loading?: boolean;
  /** 占满父容器宽度（表单主按钮、移动端场景）。 */
  block?: boolean;
  /** 左侧图标元素。 */
  icon?: React.ReactNode;
  className?: string;
}

/**
 * 主操作按钮。原生 <button> 实现，type 默认 "button"，
 * 避免在表单环境中意外触发提交。
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', loading = false, block, icon, className, disabled, children, type, ...rest },
  ref,
) {
  const isDisabled = disabled || loading;

  return (
    <button
      ref={ref}
      type={type ?? 'button'}
      className={cx(
        'dui-btn',
        `dui-btn--${variant}`,
        `dui-btn--${size}`,
        block && 'dui-btn--block',
        className,
      )}
      disabled={isDisabled}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? (
        <Spinner size="sm" />
      ) : (
        icon && <span className="dui-btn__icon" aria-hidden="true">{icon}</span>
      )}
      {children}
    </button>
  );
});
