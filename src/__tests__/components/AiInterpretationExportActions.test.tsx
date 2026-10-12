import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import AiInterpretationExportActions from '../../components/common/AiInterpretationExportActions';
import { copyText } from '../../utils/clipboard';
import { renderAiInterpretationImages } from '../../utils/aiInterpretationExport';
import { authenticateSession, invalidateAuthSession } from '../../state/authSession';

jest.mock('../../utils/clipboard', () => ({ copyText: jest.fn() }));
jest.mock('../../utils/aiInterpretationExport', () => ({ renderAiInterpretationImages: jest.fn() }));
const textCopy = copyText as jest.Mock;
const imageRender = renderAiInterpretationImages as jest.Mock;
const clipboardWrite = jest.fn();
const user = (id: string) => ({ id, username: id, email: null, role: 'viewer' as const, is_active: true, created_at: '' });
const fullText = '# 市场解读\n\n首段观点。\n\n最后一段以及风险提示也必须保留。';
const page = (index = 1, total = 1) => ({ blob: new Blob([`png-${index}`], { type: 'image/png' }), page: index, total, width: 900, height: 1200 });
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function clipboardSupport(supported = true) {
  Object.defineProperty(window, 'ClipboardItem', { configurable: true, writable: true,
    value: supported ? class { constructor(public data: Record<string, Blob>) {} } : undefined });
  Object.defineProperty(navigator, 'clipboard', { configurable: true,
    value: supported ? { write: clipboardWrite } : undefined });
}

beforeEach(() => {
  invalidateAuthSession();
  authenticateSession(user('A'), 'token-a', null);
  textCopy.mockReset().mockResolvedValue(true);
  imageRender.mockReset().mockResolvedValue([page()]);
  clipboardWrite.mockReset().mockResolvedValue(undefined);
  clipboardSupport();
});
afterEach(() => { act(() => invalidateAuthSession()); });

it('copies all supplied text and announces success only after the helper confirms it', async () => {
  const pending = deferred<boolean>();
  const copied = jest.fn();
  textCopy.mockReturnValue(pending.promise);
  render(<AiInterpretationExportActions text={fullText} onCopied={copied} />);
  fireEvent.click(screen.getByRole('button', { name: '复制文字' }));
  expect(textCopy).toHaveBeenCalledWith(fullText, expect.any(Function));
  expect(screen.queryByText('文字已复制')).not.toBeInTheDocument();
  expect(copied).not.toHaveBeenCalled();
  await act(async () => { pending.resolve(true); });
  expect(screen.getByRole('status')).toHaveTextContent('文字已复制');
  expect(copied).toHaveBeenCalledWith('text');
});

it.each(['false', 'throw'])('provides manual text copy without a false success when text copying returns %s', async (failure) => {
  const copied = jest.fn();
  if (failure === 'false') textCopy.mockResolvedValue(false);
  else textCopy.mockRejectedValue(new Error('Denied'));
  render(<AiInterpretationExportActions text={fullText} onCopied={copied} />);
  fireEvent.click(screen.getByRole('button', { name: '复制文字' }));
  expect(await screen.findByRole('textbox', { name: '手动复制 AI 解读文字' })).toHaveValue(fullText);
  expect(screen.getByRole('status')).toHaveTextContent('文字复制失败');
  expect(screen.queryByText('文字已复制')).not.toBeInTheDocument();
  expect(copied).not.toHaveBeenCalled();
});

it('blocks empty, disabled and duplicate operations', async () => {
  const pending = deferred<boolean>();
  const { rerender } = render(<AiInterpretationExportActions text=" " />);
  expect(screen.getByRole('button', { name: '复制文字' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '复制完整图片' })).toBeDisabled();
  rerender(<AiInterpretationExportActions text={fullText} disabled />);
  fireEvent.click(screen.getByRole('button', { name: '复制文字' }));
  expect(textCopy).not.toHaveBeenCalled();
  rerender(<AiInterpretationExportActions text={fullText} />);
  textCopy.mockReturnValue(pending.promise);
  const button = screen.getByRole('button', { name: '复制文字' });
  fireEvent.click(button); fireEvent.click(button);
  expect(textCopy).toHaveBeenCalledTimes(1);
  expect(imageRender).not.toHaveBeenCalled();
  await act(async () => { pending.resolve(true); });
});

it('copies one PNG and keeps a local preview plus a PNG download', async () => {
  const copied = jest.fn();
  const result = page();
  imageRender.mockResolvedValue([result]);
  render(<AiInterpretationExportActions text={fullText} title="完整解读" onCopied={copied} />);
  fireEvent.click(screen.getByRole('button', { name: '复制完整图片' }));
  const preview = await screen.findByRole('img', { name: '完整解读，第 1 / 1 张' });
  await waitFor(() => expect(copied).toHaveBeenCalledWith('image'));
  expect(preview.getAttribute('src')).toMatch(/^data:image\/png;base64,/);
  expect(imageRender).toHaveBeenCalledWith(fullText, { title: '完整解读', isCurrent: expect.any(Function) });
  expect(clipboardWrite).toHaveBeenCalledTimes(1);
  expect(clipboardWrite.mock.calls[0][0][0].data['image/png']).toBe(result.blob);
  expect(screen.getByRole('link', { name: '下载 PNG' })).toHaveAttribute('download', '完整解读.png');
  expect(screen.getByRole('status')).toHaveTextContent('图片已复制');
  expect(screen.getByText(/手机可长按上方图片保存/)).toBeInTheDocument();
});

it('falls back to a usable preview when the browser denies image clipboard writes', async () => {
  const copied = jest.fn();
  clipboardWrite.mockRejectedValue(new Error('NotAllowedError'));
  render(<AiInterpretationExportActions text={fullText} onCopied={copied} />);
  fireEvent.click(screen.getByRole('button', { name: '复制完整图片' }));
  await screen.findByRole('img');
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('浏览器未允许复制图片'));
  expect(screen.getByRole('button', { name: '复制这张图片' })).toBeEnabled();
  expect(screen.getByRole('link', { name: '下载 PNG' }).getAttribute('href')).toMatch(/^data:image\/png/);
  expect(copied).not.toHaveBeenCalled();
  clipboardWrite.mockResolvedValue(undefined);
  fireEvent.click(screen.getByRole('button', { name: '复制这张图片' }));
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('第 1 张图片已复制'));
  expect(copied).toHaveBeenCalledTimes(1);
});

it('offers PNG download and long-press saving without claiming image copy on an unsupported browser', async () => {
  clipboardSupport(false);
  const copied = jest.fn();
  render(<AiInterpretationExportActions text={fullText} onCopied={copied} />);
  fireEvent.click(screen.getByRole('button', { name: '复制完整图片' }));
  await screen.findByRole('img');
  expect(screen.getByRole('status')).toHaveTextContent('手机可长按图片保存');
  expect(copied).not.toHaveBeenCalled();
  expect(clipboardWrite).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '复制这张图片' }));
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('此浏览器不支持复制图片'));
  expect(copied).not.toHaveBeenCalled();
});

it('keeps every page of a long interpretation and copies only the page selected by the user', async () => {
  const result = [page(1, 3), page(2, 3), page(3, 3)];
  imageRender.mockResolvedValue(result);
  render(<AiInterpretationExportActions text={fullText} title="研究/解读" />);
  fireEvent.click(screen.getByRole('button', { name: '复制完整图片' }));
  await waitFor(() => expect(screen.getAllByRole('img')).toHaveLength(3));
  expect(clipboardWrite).not.toHaveBeenCalled();
  expect(screen.getByRole('status')).toHaveTextContent('已分为 3 张完整图片');
  expect(screen.getAllByRole('link', { name: '下载 PNG' }).map((link) => link.getAttribute('download')))
    .toEqual(['研究_解读-1.png', '研究_解读-2.png', '研究_解读-3.png']);
  fireEvent.click(screen.getAllByRole('button', { name: '复制这张图片' })[1]);
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('第 2 张图片已复制'));
  expect(clipboardWrite.mock.calls[0][0][0].data['image/png']).toBe(result[1].blob);
});

it('reports image generation failure without a partial or stale preview', async () => {
  imageRender.mockRejectedValue(new Error('Canvas failed'));
  const copied = jest.fn();
  render(<AiInterpretationExportActions text={fullText} onCopied={copied} />);
  fireEvent.click(screen.getByRole('button', { name: '复制完整图片' }));
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('图片生成失败'));
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: '下载 PNG' })).not.toBeInTheDocument();
  expect(clipboardWrite).not.toHaveBeenCalled();
  expect(copied).not.toHaveBeenCalled();
});

it('does not start multiple renders on repeated image clicks', async () => {
  const pending = deferred<ReturnType<typeof page>[]>();
  imageRender.mockReturnValue(pending.promise);
  render(<AiInterpretationExportActions text={fullText} />);
  const button = screen.getByRole('button', { name: '复制完整图片' });
  fireEvent.click(button); fireEvent.click(button);
  expect(imageRender).toHaveBeenCalledTimes(1);
  await act(async () => { pending.resolve([page(1, 2), page(2, 2)]); });
  await screen.findAllByRole('img');
});

it('invalidates late generation and clears previews on an account change', async () => {
  const pending = deferred<ReturnType<typeof page>[]>();
  imageRender.mockReturnValue(pending.promise);
  const copied = jest.fn();
  render(<AiInterpretationExportActions text={fullText} onCopied={copied} scopeKey="article-1" />);
  fireEvent.click(screen.getByRole('button', { name: '复制完整图片' }));
  const isCurrent = imageRender.mock.calls[0][1].isCurrent;
  expect(isCurrent()).toBe(true);
  act(() => authenticateSession(user('B'), 'token-b', null));
  expect(isCurrent()).toBe(false);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '复制完整图片' })).toBeEnabled();
  await act(async () => { pending.resolve([page()]); });
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  expect(clipboardWrite).not.toHaveBeenCalled();
  expect(copied).not.toHaveBeenCalled();
});

it('lets a new article export while the previous article render is unresolved', async () => {
  const old = deferred<ReturnType<typeof page>[]>();
  imageRender.mockReturnValueOnce(old.promise).mockResolvedValueOnce([page(1, 2), page(2, 2)]);
  const { rerender } = render(<AiInterpretationExportActions text={fullText} title="旧文章" scopeKey="old" />);
  fireEvent.click(screen.getByRole('button', { name: '复制完整图片' }));
  const oldIsCurrent = imageRender.mock.calls[0][1].isCurrent;
  rerender(<AiInterpretationExportActions text="新文章的完整内容" title="新文章" scopeKey="new" />);
  expect(oldIsCurrent()).toBe(false);
  expect(screen.getByRole('button', { name: '复制完整图片' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: '复制完整图片' }));
  await screen.findByRole('img', { name: '新文章，第 2 / 2 张' });
  await act(async () => { old.resolve([page()]); });
  expect(screen.getAllByRole('img')).toHaveLength(2);
  expect(screen.queryByRole('img', { name: /旧文章/ })).not.toBeInTheDocument();
  expect(clipboardWrite).not.toHaveBeenCalled();
});

it.each(['text', 'scope'])('clears an existing preview when its %s changes', async (change) => {
  imageRender.mockResolvedValue([page(1, 2), page(2, 2)]);
  const { rerender } = render(<AiInterpretationExportActions text={fullText} scopeKey="article" />);
  fireEvent.click(screen.getByRole('button', { name: '复制完整图片' }));
  await screen.findAllByRole('img');
  rerender(<AiInterpretationExportActions text={change === 'text' ? '内容已更新' : fullText} scopeKey={change === 'scope' ? 'other' : 'article'} />);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('ignores a late text copy receipt after an account switch', async () => {
  const pending = deferred<boolean>();
  textCopy.mockReturnValue(pending.promise);
  const copied = jest.fn();
  render(<AiInterpretationExportActions text={fullText} onCopied={copied} />);
  fireEvent.click(screen.getByRole('button', { name: '复制文字' }));
  act(() => authenticateSession(user('B'), 'token-b', null));
  await act(async () => { pending.resolve(true); });
  expect(screen.queryByText('文字已复制')).not.toBeInTheDocument();
  expect(copied).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: '复制文字' })).toBeEnabled();
});

it('closing during generation cancels its eventual preview and clipboard write', async () => {
  const pending = deferred<ReturnType<typeof page>[]>();
  imageRender.mockReturnValue(pending.promise);
  render(<AiInterpretationExportActions text={fullText} />);
  fireEvent.click(screen.getByRole('button', { name: '复制完整图片' }));
  fireEvent.click(screen.getByRole('button', { name: '关闭图片预览' }));
  expect(imageRender.mock.calls[0][1].isCurrent()).toBe(false);
  await act(async () => { pending.resolve([page()]); });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(clipboardWrite).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: '复制完整图片' })).toBeEnabled();
});

it('unmounting during generation prevents late clipboard side effects', async () => {
  const pending = deferred<ReturnType<typeof page>[]>();
  imageRender.mockReturnValue(pending.promise);
  const copied = jest.fn();
  const { unmount } = render(<AiInterpretationExportActions text={fullText} onCopied={copied} />);
  fireEvent.click(screen.getByRole('button', { name: '复制完整图片' }));
  unmount();
  expect(imageRender.mock.calls[0][1].isCurrent()).toBe(false);
  await act(async () => { pending.resolve([page()]); });
  expect(clipboardWrite).not.toHaveBeenCalled();
  expect(copied).not.toHaveBeenCalled();
});

it('ignores a pending image copy receipt after the preview has closed', async () => {
  const pending = deferred<void>();
  clipboardWrite.mockReturnValue(pending.promise);
  const copied = jest.fn();
  render(<AiInterpretationExportActions text={fullText} onCopied={copied} />);
  fireEvent.click(screen.getByRole('button', { name: '复制完整图片' }));
  await waitFor(() => expect(clipboardWrite).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole('button', { name: '关闭图片预览' }));
  await act(async () => { pending.resolve(undefined); });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByText('图片已复制')).not.toBeInTheDocument();
  expect(copied).not.toHaveBeenCalled();
});

it('keeps keyboard focus in the preview and restores it after Escape closes the preview', async () => {
  imageRender.mockResolvedValue([page(1, 2), page(2, 2)]);
  render(<AiInterpretationExportActions text={fullText} />);
  const trigger = screen.getByRole('button', { name: '复制完整图片' });
  trigger.focus();
  fireEvent.click(trigger);
  await screen.findAllByRole('img');
  const close = screen.getByRole('button', { name: '关闭图片预览' });
  expect(close).toHaveFocus();
  fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
  expect(screen.getAllByRole('link', { name: '下载 PNG' })[1]).toHaveFocus();
  fireEvent.keyDown(document, { key: 'Tab' });
  expect(close).toHaveFocus();
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

it('preserves a verified success even when a consumer notification callback throws', async () => {
  render(<AiInterpretationExportActions text={fullText} onCopied={() => { throw new Error('Notification unavailable'); }} />);
  fireEvent.click(screen.getByRole('button', { name: '复制文字' }));
  expect(await screen.findByRole('status')).toHaveTextContent('文字已复制');
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
});

it('deduplicates rapid copy clicks for a selected preview page', async () => {
  imageRender.mockResolvedValue([page(1, 2), page(2, 2)]);
  const pending = deferred<void>();
  clipboardWrite.mockReturnValue(pending.promise);
  render(<AiInterpretationExportActions text={fullText} />);
  fireEvent.click(screen.getByRole('button', { name: '复制完整图片' }));
  await screen.findAllByRole('img');
  const copy = screen.getAllByRole('button', { name: '复制这张图片' })[0];
  fireEvent.click(copy); fireEvent.click(copy);
  expect(clipboardWrite).toHaveBeenCalledTimes(1);
  await act(async () => { pending.resolve(undefined); });
  expect(screen.getByRole('status')).toHaveTextContent('第 1 张图片已复制');
});

it('uses the real text helper guard to prevent an old account execCommand fallback after a delayed denial', async () => {
  const pending = deferred<void>();
  const writeText = jest.fn().mockReturnValue(pending.promise);
  const legacyCopy = jest.fn().mockReturnValue(true);
  Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true });
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  Object.defineProperty(document, 'execCommand', { configurable: true, value: legacyCopy });
  const actualCopy = jest.requireActual('../../utils/clipboard').copyText;
  textCopy.mockImplementation(actualCopy);
  const copied = jest.fn();
  render(<AiInterpretationExportActions text={fullText} onCopied={copied} />);
  fireEvent.click(screen.getByRole('button', { name: '复制文字' }));
  expect(writeText).toHaveBeenCalledWith(fullText);
  act(() => authenticateSession(user('B'), 'token-b', null));
  await act(async () => { pending.reject(new Error('NotAllowedError')); });
  expect(legacyCopy).not.toHaveBeenCalled();
  expect(screen.queryByText('文字已复制')).not.toBeInTheDocument();
  expect(copied).not.toHaveBeenCalled();
});

it('disabling during image generation cancels the old export and enabling permits a fresh export', async () => {
  const pending = deferred<ReturnType<typeof page>[]>();
  imageRender.mockReturnValueOnce(pending.promise).mockResolvedValueOnce([page(1, 2), page(2, 2)]);
  const { rerender } = render(<AiInterpretationExportActions text={fullText} />);
  fireEvent.click(screen.getByRole('button', { name: '复制完整图片' }));
  const oldIsCurrent = imageRender.mock.calls[0][1].isCurrent;
  rerender(<AiInterpretationExportActions text={fullText} disabled />);
  expect(oldIsCurrent()).toBe(false);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '复制完整图片' })).toBeDisabled();
  await act(async () => { pending.resolve([page()]); });
  expect(clipboardWrite).not.toHaveBeenCalled();
  rerender(<AiInterpretationExportActions text={fullText} />);
  expect(screen.getByRole('button', { name: '复制完整图片' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: '复制完整图片' }));
  expect(await screen.findAllByRole('img')).toHaveLength(2);
  expect(clipboardWrite).not.toHaveBeenCalled();
});

it('consumes preview Escape before an enclosing window handler can close the interpretation', async () => {
  imageRender.mockResolvedValue([page(1, 2), page(2, 2)]);
  const enclosingEscape = jest.fn();
  window.addEventListener('keydown', enclosingEscape);
  try {
    render(<AiInterpretationExportActions text={fullText} />);
    fireEvent.click(screen.getByRole('button', { name: '复制完整图片' }));
    await screen.findAllByRole('img');
    fireEvent.keyDown(screen.getByRole('button', { name: '关闭图片预览' }), { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(enclosingEscape).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '复制完整图片' })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(enclosingEscape).toHaveBeenCalledTimes(1);
  } finally { window.removeEventListener('keydown', enclosingEscape); }
});
