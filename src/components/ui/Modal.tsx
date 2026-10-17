import React, { useEffect, useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import { IconButton } from './IconButton';
import { CloseIcon } from './icons';
import { cx, type DuiSize } from './types';

export interface ModalProps {
  /** 受控开关。false 时不渲染任何内容（无退出动画，行为可预期）。 */
  open: boolean;
  /** 关闭回调：Esc、遮罩点击、关闭按钮统一走这里。 */
  onClose: () => void;
  /** 标题。提供时作为对话框的无障碍名称（aria-labelledby）。 */
  title?: React.ReactNode;
  /** 底部操作区，通常是确认/取消按钮组。 */
  footer?: React.ReactNode;
  size?: DuiSize;
  /** Esc 关闭，默认开启。 */
  closeOnEsc?: boolean;
  /** 点击遮罩关闭，默认开启。 */
  closeOnBackdrop?: boolean;
  /** 是否显示右上角关闭按钮，默认显示。 */
  closable?: boolean;
  /** 打开后初始聚焦的元素。缺省时聚焦弹窗内第一个可聚焦元素，再退化为弹窗容器。 */
  initialFocusRef?: React.RefObject<HTMLElement>;
  /** 无 title 时的对话框无障碍名称。 */
  ariaLabel?: string;
  children?: React.ReactNode;
  className?: string;
}

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
  '[contenteditable="true"]',
].join(',');

function getFocusable(root: HTMLElement | null): HTMLElement[] {
  if (!root) return [];
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}

/* --- 模块级状态：Esc 只响应栈顶弹窗；滚动锁定按引用计数共享 --- */

const modalStack: symbol[] = [];
let scrollLockCount = 0;
let prevBodyOverflow = '';
let prevBodyPaddingRight = '';

function lockBodyScroll() {
  if (scrollLockCount === 0 && typeof document !== 'undefined') {
    const { body } = document;
    prevBodyOverflow = body.style.overflow;
    prevBodyPaddingRight = body.style.paddingRight;
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth;
    body.style.overflow = 'hidden';
    // 滚动条消失会引起布局跳动，用等宽内边距补偿。
    if (scrollbarWidth > 0) body.style.paddingRight = `${scrollbarWidth}px`;
  }
  scrollLockCount += 1;
}

function unlockBodyScroll() {
  scrollLockCount = Math.max(0, scrollLockCount - 1);
  if (scrollLockCount === 0 && typeof document !== 'undefined') {
    document.body.style.overflow = prevBodyOverflow;
    document.body.style.paddingRight = prevBodyPaddingRight;
  }
}

/**
 * 模态对话框。
 *
 * 可访问性契约（WAI-ARIA dialog 模式）：
 * - role="dialog" + aria-modal，名称来自 title（aria-labelledby）或 ariaLabel；
 * - 打开时焦点移入弹窗，Tab/Shift+Tab 被圈养在弹窗内；
 * - 关闭（含卸载）时焦点返回触发元素；
 * - body 滚动锁定并补偿滚动条宽度，弹窗嵌套时滚动锁按引用计数共享。
 */
export function Modal({
  open,
  onClose,
  title,
  footer,
  size = 'md',
  closeOnEsc = true,
  closeOnBackdrop = true,
  closable = true,
  initialFocusRef,
  ariaLabel,
  children,
  className,
}: ModalProps) {
  const uid = useId();
  const titleId = `dui-modal-title-${uid}`;
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const tokenRef = useRef<symbol | null>(null);
  const dragStartedInsideRef = useRef(false);

  useEffect(() => {
    if (!open) return undefined;

    const token = Symbol('dui-modal');
    tokenRef.current = token;
    modalStack.push(token);
    lockBodyScroll();

    restoreFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;

    const dialog = dialogRef.current;
    const focusTarget =
      initialFocusRef?.current ?? getFocusable(dialog)[0] ?? (dialog as HTMLElement | null);
    focusTarget?.focus();

    return () => {
      const idx = modalStack.indexOf(token);
      if (idx >= 0) modalStack.splice(idx, 1);
      tokenRef.current = null;
      unlockBodyScroll();
      restoreFocusRef.current?.focus();
      restoreFocusRef.current = null;
    };
    // onClose/initialFocusRef 变化不应重建焦点上下文，故依赖数组仅含 open。
  }, [open]);

  if (!open || typeof document === 'undefined') return null;

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape' && closeOnEsc) {
      // 多层弹窗时只有栈顶实例响应 Esc。
      if (modalStack[modalStack.length - 1] === tokenRef.current) onClose();
      return;
    }

    if (event.key !== 'Tab') return;

    // 焦点圈养：Tab 循环限制在弹窗内部。
    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusable = getFocusable(dialog);
    if (focusable.length === 0) {
      event.preventDefault();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;

    if (event.shiftKey && (active === first || !dialog.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return createPortal(
    <div
      className="dui-modal__backdrop"
      onMouseDown={(e) => {
        // 记录手势起点：模态内开始的选择/拖拽落到遮罩上时不应触发关闭。
        dragStartedInsideRef.current = e.target !== e.currentTarget;
      }}
      onClick={(e) => {
        if (
          closeOnBackdrop &&
          e.target === e.currentTarget &&
          !dragStartedInsideRef.current
        ) {
          onClose();
        }
        dragStartedInsideRef.current = false;
      }}
      onKeyDown={handleKeyDown}
      data-dui-modal-backdrop=""
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        aria-label={!title ? ariaLabel : undefined}
        className={cx('dui-modal', `dui-modal--${size}`, className)}
        tabIndex={-1}
      >
        {(title || closable) && (
          <div className="dui-modal__header">
            {title && (
              <h2 className="dui-modal__title" id={titleId}>
                {title}
              </h2>
            )}
            {closable && (
              <IconButton
                ariaLabel="关闭对话框"
                size="sm"
                className="dui-modal__close"
                onClick={onClose}
              >
                <CloseIcon />
              </IconButton>
            )}
          </div>
        )}

        <div className="dui-modal__body">{children}</div>

        {footer && <div className="dui-modal__footer">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}
