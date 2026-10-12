import React, { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import { getAuthSnapshot, subscribeAuth } from '../../state/authSession';
import { copyText } from '../../utils/clipboard';
import { renderAiInterpretationImages } from '../../utils/aiInterpretationExport';
import './AiInterpretationExportActions.css';

export interface AiInterpretationExportActionsProps {
  text: string;
  title?: string;
  disabled?: boolean;
  className?: string;
  onCopied?: (kind: 'text' | 'image') => void;
  scopeKey?: string;
}

interface PreviewPage {
  blob: Blob;
  page: number;
  total: number;
  width: number;
  height: number;
  url: string;
}
interface ExportState {
  key: string;
  busy: 'text' | 'image' | null;
  previewOpen: boolean;
  pages: PreviewPage[];
  feedback: string;
  textFallback: boolean;
}
const emptyState = (key: string): ExportState => ({
  key, busy: null, previewOpen: false, pages: [], feedback: '', textFallback: false,
});

function blobDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => typeof reader.result === 'string' && reader.result.startsWith('data:image/png')
      ? resolve(reader.result) : reject(new Error('图片预览不可用'));
    reader.onerror = () => reject(new Error('图片预览不可用'));
    reader.onabort = () => reject(new Error('图片预览已关闭'));
    reader.readAsDataURL(blob);
  });
}

function canCopyImage(): boolean {
  return typeof ClipboardItem !== 'undefined' && !!navigator.clipboard?.write;
}

/** Copy the complete interpretation locally, with a visible fallback for restricted WebViews. */
const AiInterpretationExportActions: React.FC<AiInterpretationExportActionsProps> = ({
  text, title = 'AI 解读', disabled = false, className, onCopied, scopeKey,
}) => {
  const revision = useSyncExternalStore(subscribeAuth,
    () => getAuthSnapshot().revision, () => getAuthSnapshot().revision);
  const key = useMemo(() => JSON.stringify([revision, scopeKey, text, title, disabled]), [revision, scopeKey, text, title, disabled]);
  const latestKey = useRef(key);
  latestKey.current = key;
  const mounted = useRef(false);
  const preview = useRef<HTMLDivElement>(null);
  const work = useRef<{ key: string; id: number } | null>(null);
  const sequence = useRef(0);
  const [state, setState] = useState<ExportState>(() => emptyState(key));
  const shown = state.key === key ? state : emptyState(key);
  const notifyCopied = (kind: 'text' | 'image') => {
    // A consumer callback must not turn a confirmed copy into a failure notice.
    try { onCopied?.(kind); } catch { /* Clipboard completion is already confirmed. */ }
  };

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; work.current = null; sequence.current += 1; };
  }, []);
  useEffect(() => {
    work.current = null;
    sequence.current += 1;
    setState(emptyState(key));
  }, [key]);

  const begin = () => {
    if (disabled || !text.trim() || !mounted.current || latestKey.current !== key
      || getAuthSnapshot().revision !== revision || work.current?.key === key) return null;
    const id = ++sequence.current;
    work.current = { key, id };
    const isCurrent = () => mounted.current && latestKey.current === key
      && getAuthSnapshot().revision === revision && work.current?.id === id;
    return { isCurrent, finish: () => { if (isCurrent()) work.current = null; } };
  };

  const copyInterpretationText = async () => {
    const operation = begin();
    if (!operation) return;
    setState({ ...emptyState(key), busy: 'text' });
    try {
      const copied = await copyText(text, operation.isCurrent);
      if (!operation.isCurrent()) return;
      setState({ ...emptyState(key), feedback: copied ? '文字已复制' : '文字复制失败，请手动选择下方内容复制。', textFallback: !copied });
      if (copied) notifyCopied('text');
    } catch {
      if (operation.isCurrent()) setState({ ...emptyState(key), feedback: '文字复制失败，请手动选择下方内容复制。', textFallback: true });
    } finally { operation.finish(); }
  };

  const copyPng = async (page: PreviewPage, isCurrent: () => boolean): Promise<boolean> => {
    if (!canCopyImage() || !isCurrent()) return false;
    const item = new ClipboardItem({ 'image/png': page.blob });
    // Check identity immediately before the irreversible clipboard write.
    if (!isCurrent()) return false;
    await navigator.clipboard.write([item]);
    return isCurrent();
  };

  const generateImages = async () => {
    const operation = begin();
    if (!operation) return;
    setState({ ...emptyState(key), busy: 'image', previewOpen: true, feedback: '正在生成完整图片…' });
    try {
      const rendered = await renderAiInterpretationImages(text, { title, isCurrent: operation.isCurrent });
      if (!operation.isCurrent()) return;
      if (!rendered.length) throw new Error('没有生成图片');
      const pages = await Promise.all(rendered.map(async (page) => ({ ...page, url: await blobDataUrl(page.blob) })));
      if (!operation.isCurrent()) return;
      let feedback = pages.length > 1
        ? `内容较长，已分为 ${pages.length} 张完整图片，可逐张复制或下载。`
        : '图片已生成，可下载 PNG；手机可长按图片保存。';
      setState({ key, busy: 'image', previewOpen: true, pages, feedback, textFallback: false });
      if (pages.length === 1 && canCopyImage()) {
        try {
          const copied = await copyPng(pages[0], operation.isCurrent);
          if (!operation.isCurrent()) return;
          if (copied) { feedback = '图片已复制'; notifyCopied('image'); }
        } catch {
          feedback = '浏览器未允许复制图片，请点击「复制这张图片」重试，或下载 PNG、长按图片保存。';
        }
      }
      if (operation.isCurrent()) setState({ key, busy: null, previewOpen: true, pages, feedback, textFallback: false });
    } catch {
      if (operation.isCurrent()) setState({ ...emptyState(key), previewOpen: true, feedback: '图片生成失败，请重试或先复制文字。' });
    } finally { operation.finish(); }
  };

  const copyPreviewPage = async (page: PreviewPage) => {
    const operation = begin();
    if (!operation) return;
    setState((previous) => ({ ...previous, busy: 'image', feedback: '正在复制图片…' }));
    try {
      const copied = await copyPng(page, operation.isCurrent);
      if (!operation.isCurrent()) return;
      setState((previous) => ({ ...previous, busy: null, feedback: copied
        ? `第 ${page.page} 张图片已复制` : '此浏览器不支持复制图片，请下载 PNG；手机可长按图片保存。' }));
      if (copied) notifyCopied('image');
    } catch {
      if (operation.isCurrent()) setState((previous) => ({ ...previous, busy: null,
        feedback: '浏览器未允许复制图片，请下载 PNG；手机可长按图片保存。' }));
    } finally { operation.finish(); }
  };

  const closePreview = useCallback(() => {
    work.current = null;
    sequence.current += 1;
    setState(emptyState(key));
  }, [key]);
  useEffect(() => {
    if (!shown.previewOpen) return;
    const previousFocus = document.activeElement;
    preview.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closePreview(); return; }
      if (event.key !== 'Tab') return;
      const controls = Array.from(preview.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], textarea:not(:disabled)') || []);
      if (!controls.length) return;
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && (document.activeElement === first || !preview.current?.contains(document.activeElement))) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !preview.current?.contains(document.activeElement))) {
        event.preventDefault(); first.focus();
      }
    };
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('keydown', handleKey);
      if (previousFocus instanceof HTMLElement && document.contains(previousFocus)
        && latestKey.current === key && getAuthSnapshot().revision === revision) previousFocus.focus();
    };
  }, [shown.previewOpen, closePreview, key, revision]);
  const unavailable = disabled || !text.trim() || !!shown.busy;
  const downloadName = title.replace(/[\\/:*?"<>|]/g, '_').slice(0, 70) || 'AI解读';

  return (
    <div className={`ai-interpretation-export${className ? ` ${className}` : ''}`}>
      <div className="ai-interpretation-export__actions">
        <button type="button" className="ai-interpretation-export__button" disabled={unavailable} onClick={copyInterpretationText}>
          {shown.busy === 'text' ? '正在复制…' : '复制文字'}
        </button>
        <button type="button" className="ai-interpretation-export__button ai-interpretation-export__button--image" disabled={unavailable} onClick={generateImages}>
          {shown.busy === 'image' ? '正在处理图片…' : '复制完整图片'}
        </button>
      </div>
      {!shown.previewOpen && shown.feedback && <p role="status" className="ai-interpretation-export__status">{shown.feedback}</p>}
      {shown.textFallback && <textarea className="ai-interpretation-export__text" aria-label="手动复制 AI 解读文字" readOnly value={text} onFocus={(event) => event.currentTarget.select()} />}
      {shown.previewOpen && createPortal((
        <div className="ai-interpretation-export__backdrop" onClick={(event) => { if (event.target === event.currentTarget) closePreview(); }}>
          <div ref={preview} className="ai-interpretation-export__preview" role="dialog" aria-modal="true" aria-label="AI 解读完整图片">
            <div className="ai-interpretation-export__header">
              <strong>AI 解读完整图片</strong>
              <button type="button" className="ai-interpretation-export__close" aria-label="关闭图片预览" onClick={closePreview}>关闭</button>
            </div>
            <p role="status" className="ai-interpretation-export__status">{shown.feedback}</p>
            {!shown.busy && !shown.pages.length && <button type="button" className="ai-interpretation-export__button" disabled={disabled} onClick={generateImages}>重新生成图片</button>}
            <div className="ai-interpretation-export__pages">
              {shown.pages.map((page) => (
                <figure className="ai-interpretation-export__page" key={page.page}>
                  <figcaption>第 {page.page} / {page.total} 张</figcaption>
                  <img src={page.url} alt={`${title}，第 ${page.page} / ${page.total} 张`} width={page.width} height={page.height} />
                  <div className="ai-interpretation-export__page-actions">
                    <button type="button" className="ai-interpretation-export__button" disabled={!!shown.busy || disabled} onClick={() => copyPreviewPage(page)}>复制这张图片</button>
                    <a className="ai-interpretation-export__button" href={page.url} download={`${downloadName}${page.total > 1 ? `-${page.page}` : ''}.png`}>下载 PNG</a>
                  </div>
                </figure>
              ))}
            </div>
            {!!shown.pages.length && <p className="ai-interpretation-export__hint">手机可长按上方图片保存，完整内容已保留。</p>}
          </div>
        </div>
      ), document.body)}
    </div>
  );
};

export default AiInterpretationExportActions;
