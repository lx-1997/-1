import React, { forwardRef, useEffect, useId, useRef, useState } from 'react';

export interface AsyncButtonProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'onClick'> {
  onAction: () => void | Promise<void>;
  busy?: boolean;
  busyLabel?: string;
  onError?: (error: unknown) => void;
  variant?: 'primary' | 'secondary' | 'quiet';
}

/** Keeps keyboard focus during a pending action and guards same-frame double clicks. */
export const AsyncButton = forwardRef<HTMLButtonElement, AsyncButtonProps>(function AsyncButton(
  { onAction, busy = false, busyLabel = '处理中', onError, variant = 'secondary', disabled,
    children, className, type = 'button', ...rest }, ref,
) {
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState(false);
  const inFlight = useRef(false);
  const id = useId();
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const isBusy = busy || pending;

  const act = async () => {
    if (disabled || busy || inFlight.current) return;
    inFlight.current = true;
    setPending(true);
    setFailure(false);
    try {
      await onAction();
    } catch (error) {
      if (mounted.current) {
        setFailure(true);
        onError?.(error);
      }
    } finally {
      inFlight.current = false;
      if (mounted.current) setPending(false);
    }
  };

  return (
    <>
      <button {...rest} ref={ref} type={type} disabled={disabled}
        className={`dc-market-button dc-market-button--${variant} ${className || ''}`.trim()}
        aria-disabled={disabled || isBusy || undefined} aria-busy={isBusy || undefined}
        aria-describedby={[rest['aria-describedby'], isBusy ? `${id}-busy` : undefined].filter(Boolean).join(' ') || undefined}
        onClick={event => {
          if (disabled || busy || inFlight.current) { event.preventDefault(); return; }
          void act();
        }}>
        {isBusy && <span className="dc-market-spinner" aria-hidden="true" />}
        <span>{children}</span>
      </button>
      <span id={`${id}-busy`} role="status" className="dc-market-sr-only">{isBusy ? busyLabel : ''}</span>
      {failure && !onError && <span role="alert" className="dc-market-action-error">操作失败，请重试</span>}
    </>
  );
});
