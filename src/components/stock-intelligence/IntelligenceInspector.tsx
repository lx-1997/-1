import React, { useEffect, useId, useRef } from 'react';
import { createPortal } from 'react-dom';

export default function IntelligenceInspector({ title, category, previousTitle, viewKey, onClose, onBack, children, bodyRef, returnFocus }: {
  title: string; category: string; previousTitle?: string; viewKey: string; onClose: () => void; onBack: () => void;
  children: React.ReactNode; bodyRef: React.RefObject<HTMLDivElement>;
  returnFocus?: HTMLElement | null;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const close = useRef(onClose); close.current = onClose;
  const id = useId();
  useEffect(() => {
    const element = dialog.current;
    const trigger = returnFocus || (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    if (element?.showModal) element.showModal(); else element?.setAttribute('open', '');
    return () => {
      if (element?.close) element.close(); else element?.removeAttribute('open');
      document.body.style.overflow = previousOverflow;
      if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    };
  }, [returnFocus]);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, [viewKey]);
  return createPortal(<dialog ref={dialog} className="si-inspector" aria-modal="true" aria-labelledby={`${id}-title`} onCancel={event => { event.preventDefault(); close.current(); }}
    onClick={event => { if (event.target === event.currentTarget) { const box = event.currentTarget.getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) close.current(); } }}
    onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); close.current(); }
      if (event.key !== 'Tab') return;
      const buttons = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input, [tabindex="0"]') || []).filter(element => !element.closest('[hidden]'));
      const first = buttons[0], last = buttons[buttons.length - 1];
      if (!first) return;
      if (event.shiftKey && (document.activeElement === first || document.activeElement === heading.current)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }}>
    <header className="si-inspector-header"><div>{previousTitle && <button type="button" className="si-inspector-back" onClick={onBack}>← 返回{previousTitle}</button>}<span className="si-inspector-category">{category}</span><h3 id={`${id}-title`} ref={heading} tabIndex={-1}>{title}</h3></div><button type="button" className="si-inspector-close" aria-label="关闭详情" onClick={onClose}>×</button></header>
    <div ref={bodyRef} className="si-inspector-body">{children}</div>
  </dialog>, document.body);
}
