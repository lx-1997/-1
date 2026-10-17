import React, { useCallback, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { cx } from './types';

type Placement = 'top' | 'bottom';

export interface TooltipProps {
  /** 提示内容。纯展示，不承载交互。 */
  content: React.ReactNode;
  /** 期望方位，视口空间不足时自动翻转。默认 top。 */
  placement?: Placement;
  /** 显示延迟（毫秒），默认 250。 */
  delay?: number;
  /** 为 true 时不显示提示（例如按钮禁用期间）。 */
  disabled?: boolean;
  className?: string;
  children: React.ReactNode;
}

interface TooltipPosition {
  x: number;
  y: number;
  placement: Placement;
  arrowX: number;
}

const GAP = 8;
const EDGE_MARGIN = 8;

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), Math.max(min, max));
}

/**
 * 悬浮提示（hover / keyboard-focus 触发）。
 *
 * 定位与实现说明：
 * - Portal 渲染到 body，不受祖先 overflow 裁剪；固定定位，随 scroll/resize 重算；
 * - 子元素为原生标签（button/a/input 等）时以 cloneElement 注入事件与
 *   aria-describedby，不引入额外包裹节点；子元素为自定义组件时退化为
 *   inline-flex 包裹层，自定义组件须将 props 透传至实际 DOM 元素；
 * - Esc 或滚动时立即隐藏，避免提示与触发元素脱节。
 */
export function Tooltip({
  content,
  placement = 'top',
  delay = 250,
  disabled = false,
  className,
  children,
}: TooltipProps) {
  const uid = useId();
  const tooltipId = `dui-tooltip-${uid}`;
  const triggerRef = useRef<HTMLElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const showTimerRef = useRef<number | null>(null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<TooltipPosition | null>(null);

  const clearShowTimer = useCallback(() => {
    if (showTimerRef.current !== null) {
      window.clearTimeout(showTimerRef.current);
      showTimerRef.current = null;
    }
  }, []);

  const computePosition = useCallback((): TooltipPosition | null => {
    const trigger = triggerRef.current;
    const node = tooltipRef.current;
    if (!trigger || !node) return null;

    const rect = trigger.getBoundingClientRect();
    const width = node.offsetWidth || 0;
    const height = node.offsetHeight || 0;

    let finalPlacement = placement;
    const roomTop = rect.top - height - GAP;
    const roomBottom = window.innerHeight - rect.bottom - height - GAP;
    if (finalPlacement === 'top' && roomTop < EDGE_MARGIN && roomBottom >= EDGE_MARGIN) {
      finalPlacement = 'bottom';
    } else if (finalPlacement === 'bottom' && roomBottom < EDGE_MARGIN && roomTop >= EDGE_MARGIN) {
      finalPlacement = 'top';
    }

    const centerX = rect.left + rect.width / 2;
    const x = clamp(centerX - width / 2, EDGE_MARGIN, window.innerWidth - width - EDGE_MARGIN);
    const y = finalPlacement === 'top' ? rect.top - height - GAP : rect.bottom + GAP;
    const arrowX = clamp(centerX - x, 14, Math.max(14, width - 14));

    return { x, y, placement: finalPlacement, arrowX };
  }, [placement]);

  const openTooltip = useCallback(() => {
    if (disabled) return;
    clearShowTimer();
    showTimerRef.current = window.setTimeout(() => setOpen(true), delay);
  }, [clearShowTimer, delay, disabled]);

  const closeTooltip = useCallback(() => {
    clearShowTimer();
    setOpen(false);
  }, [clearShowTimer]);

  // 打开后测量并定位（两段式：先渲染再测量，避免 0,0 闪烁）。
  useEffect(() => {
    if (!open) {
      setPos(null);
      return undefined;
    }
    setPos(computePosition());

    const refresh = () => setPos(computePosition());
    window.addEventListener('resize', refresh);
    // capture：任何容器滚动都会改变触发元素视口位置。
    window.addEventListener('scroll', refresh, true);

    return () => {
      window.removeEventListener('resize', refresh);
      window.removeEventListener('scroll', refresh, true);
    };
  }, [open, computePosition]);

  useEffect(() => clearShowTimer, [clearShowTimer]);

  const eventProps = {
    onMouseEnter: (e: React.MouseEvent) => {
      triggerRef.current = e.currentTarget as HTMLElement;
      openTooltip();
    },
    onMouseLeave: closeTooltip,
    onFocus: (e: React.FocusEvent) => {
      triggerRef.current = e.currentTarget as HTMLElement;
      openTooltip();
    },
    onBlur: closeTooltip,
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.key === 'Escape') closeTooltip();
    },
  };

  const describedBy = open ? tooltipId : undefined;

  let triggerNode: React.ReactNode;
  if (React.isValidElement(children) && typeof children.type === 'string') {
    // 原生标签：直接注入，避免包裹层破坏 flex/grid 布局。
    const child = children as React.ReactElement<{
      'aria-describedby'?: string;
      onMouseEnter?: React.MouseEventHandler;
      onMouseLeave?: React.MouseEventHandler;
      onFocus?: React.FocusEventHandler;
      onBlur?: React.FocusEventHandler;
      onKeyDown?: React.KeyboardEventHandler;
    }>;
    triggerNode = React.cloneElement(child, {
      'aria-describedby': child.props['aria-describedby']
        ? `${child.props['aria-describedby']} ${describedBy ?? ''}`.trim()
        : describedBy,
      onMouseEnter: (e: React.MouseEvent) => {
        child.props.onMouseEnter?.(e);
        eventProps.onMouseEnter(e);
      },
      onMouseLeave: (e: React.MouseEvent) => {
        child.props.onMouseLeave?.(e);
        closeTooltip();
      },
      onFocus: (e: React.FocusEvent) => {
        child.props.onFocus?.(e);
        eventProps.onFocus(e);
      },
      onBlur: (e: React.FocusEvent) => {
        child.props.onBlur?.(e);
        closeTooltip();
      },
      onKeyDown: (e: React.KeyboardEvent) => {
        eventProps.onKeyDown(e);
        child.props.onKeyDown?.(e);
      },
    });
  } else {
    // 自定义组件：退化为包裹层（inline-flex，跟随内容尺寸）。
    triggerNode = (
      <span
        className="dui-tooltip-trigger"
        aria-describedby={describedBy}
        {...eventProps}
      >
        {children}
      </span>
    );
  }

  const tooltipNode =
    open && typeof document !== 'undefined'
      ? createPortal(
          <div
            ref={tooltipRef}
            id={tooltipId}
            role="tooltip"
            className={cx('dui-tooltip', className)}
            style={{
              left: pos?.x ?? 0,
              top: pos?.y ?? 0,
              visibility: pos ? 'visible' : 'hidden',
            }}
            data-placement={pos?.placement ?? placement}
          >
            {content}
            <span
              className="dui-tooltip__arrow"
              aria-hidden="true"
              style={{ left: pos ? pos.arrowX - 4 : 0 }}
            />
          </div>,
          document.body,
        )
      : null;

  return (
    <>
      {triggerNode}
      {tooltipNode}
    </>
  );
}
