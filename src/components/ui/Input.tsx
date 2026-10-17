import React, { forwardRef, useId } from 'react';
import { cx, type DuiSize } from './types';

export interface InputProps
  extends Omit<
    React.InputHTMLAttributes<HTMLInputElement>,
    'size' | 'className' | 'prefix' | 'suffix'
  > {
  /** 字段标签。与内部自动生成的 id 关联（htmlFor/aria 完整接线）。 */
  label?: string;
  /** 必填标记：视觉上显示红星，语义上输出 aria-required。 */
  required?: boolean;
  /** 辅助说明，位于输入框下方。 */
  hint?: string;
  /** 校验错误。提供时输入框以 aria-invalid + error 描述关联。 */
  error?: string;
  size?: DuiSize;
  /** 左侧前缀（单位、币种、图标等）。 */
  prefix?: React.ReactNode;
  /** 右侧后缀（单位、清除按钮等）。 */
  suffix?: React.ReactNode;
  /** 占满父容器宽度，默认 true。 */
  block?: boolean;
  className?: string;
}

/**
 * 文本输入字段。label/hint/error 与输入框之间的
 * aria-labelledby、aria-describedby、aria-invalid 关系全部自动接线，
 * 调用方只声明数据，不手工维护 id。
 */
export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, required, hint, error, size = 'md', prefix, suffix, block = true, className, id, disabled, ...rest },
  ref,
) {
  const autoId = useId();
  const inputId = id ?? `dui-field-${autoId}`;
  const hintId = hint ? `${inputId}-hint` : undefined;
  const errorId = error ? `${inputId}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(' ') || undefined;

  return (
    <div className={cx('dui-field', block && 'dui-field--block', className)}>
      {label && (
        <label className="dui-field__label" htmlFor={inputId}>
          {label}
          {required && (
            <span className="dui-field__required" aria-hidden="true">
              *
            </span>
          )}
        </label>
      )}

      <div
        className={cx(
          'dui-field__control',
          `dui-field__control--${size}`,
          error && 'dui-field__control--invalid',
          disabled && 'dui-field__control--disabled',
        )}
      >
        {prefix && <span className="dui-field__affix">{prefix}</span>}
        <input
          ref={ref}
          id={inputId}
          className="dui-field__input"
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          aria-required={required || undefined}
          aria-describedby={describedBy}
          {...rest}
        />
        {suffix && <span className="dui-field__affix">{suffix}</span>}
      </div>

      {hint && !error && (
        <div className="dui-field__hint" id={hintId}>
          {hint}
        </div>
      )}
      {error && (
        <div className="dui-field__error" id={errorId}>
          {error}
        </div>
      )}
    </div>
  );
});
