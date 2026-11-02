import React, { useEffect, useRef } from 'react';
import ResearchWorkspace from './ResearchWorkspace';

export default function ResearchWorkspaceDialog({ onClose }: { onClose: () => void }) {
  const element = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = element.current;
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    if (dialog && !dialog.open) dialog.showModal();
    return () => {
      dialog?.close();
      document.body.style.overflow = overflow;
      previous?.focus();
    };
  }, []);
  return <dialog ref={element} className="rw-dialog" aria-label="投研研究空间" onCancel={event => { event.preventDefault(); onClose(); }}>
    <div className="rw-dialog-bar"><span>私有投研工作区</span><button type="button" onClick={onClose} autoFocus>关闭研究空间</button></div>
    <ResearchWorkspace />
  </dialog>;
}
