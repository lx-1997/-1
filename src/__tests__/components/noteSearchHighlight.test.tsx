import React from 'react';
import { render, fireEvent, waitFor, screen, within } from '@testing-library/react';
import TerminalZsxqStream from '../../components/TerminalZsxqStream';
import { getZsxqStream, type ZsxqStreamResponse } from '../../services/zsxqStreamService';

jest.mock('../../services/zsxqStreamService');

const mockedStream = getZsxqStream as jest.MockedFunction<typeof getZsxqStream>;

const streamResponse = (text: string): ZsxqStreamResponse => ({
  items: [{
    id: 'n1', title: '', text,
    links: [], images: [], image_fulls: [],
    create_time: '2026-10-22T20:08:40', date: '2026-10-22',
    digested: false, comments_count: 0, comments: [], tags: [],
  }],
  group: '', groups: [], keyword: '', next_before: '', has_more: false, total: 1,
});

describe('机构纪要搜索关键词高亮', () => {
  beforeEach(() => {
    mockedStream.mockReset();
  });

  it('回车检索后正文命中词渲染为 <mark class=tzs-mark>', async () => {
    mockedStream.mockResolvedValue(streamResponse('覆铜板巨头建滔集团再度发布涨价通知，建滔电子布需求紧张。'));
    const { getByPlaceholderText, baseElement: container } = render(<TerminalZsxqStream />);
    await waitFor(() => expect(mockedStream).toHaveBeenCalledTimes(1));
    expect(container.querySelector('mark.tzs-mark')).toBeNull();

    const input = getByPlaceholderText(/搜机构纪要/);
    fireEvent.change(input, { target: { value: '建滔' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(mockedStream).toHaveBeenLastCalledWith(expect.objectContaining({ q: '建滔' })));
    await waitFor(() => {
      const marks = Array.from(container.querySelectorAll('mark.tzs-mark'));
      expect(marks.map(m => m.textContent)).toEqual(['建滔', '建滔']);
    });
  });

  it('清除搜索后高亮消失', async () => {
    mockedStream.mockResolvedValue(streamResponse('建滔集团发布涨价通知。'));
    const { getByPlaceholderText, getByLabelText, baseElement: container } = render(<TerminalZsxqStream />);
    await waitFor(() => expect(mockedStream).toHaveBeenCalledTimes(1));

    const input = getByPlaceholderText(/搜机构纪要/);
    fireEvent.change(input, { target: { value: '建滔' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(container.querySelectorAll('mark.tzs-mark').length).toBe(1));

    fireEvent.click(getByLabelText('清除搜索'));
    await waitFor(() => expect(mockedStream).toHaveBeenLastCalledWith(expect.objectContaining({ q: undefined })));
    await waitFor(() => expect(container.querySelector('mark.tzs-mark')).toBeNull());
  });
});


it('shows only a heading and introduction until a note is opened, then restores the list position and focus', async () => {
  const text = '公司调研纪要\n' + '简介与研究背景。'.repeat(90) + '\n全文末尾仅在详情显示';
  mockedStream.mockResolvedValue(streamResponse(text));
  const { baseElement: container } = render(<TerminalZsxqStream />);
  await screen.findByRole('heading', { name: '公司调研纪要' });
  expect(container.querySelector('.tzs-item-body')).toBeNull();
  expect(screen.queryByText('全文末尾仅在详情显示')).not.toBeInTheDocument();
  const list = container.querySelector('.tzs-scroll') as HTMLDivElement;
  list.scrollTop = 180;
  const outer = list.parentElement as HTMLElement;
  outer.scrollTop = 260;
  const open = screen.getByRole('button', { name: /查看全文/ });
  fireEvent.click(open);
  const reader = screen.getByRole('region', { name: '机构纪要全文' });
  expect(within(reader).getByText('全文末尾仅在详情显示')).toBeVisible();
  expect(list.scrollTop).toBe(0);
  outer.scrollTop = 20;
  expect(within(reader).queryByRole('button', { name: /展开全文/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '← 返回纪要列表' }));
  expect(screen.queryByRole('region', { name: '机构纪要全文' })).not.toBeInTheDocument();
  expect(list.scrollTop).toBe(180);
  expect(outer.scrollTop).toBe(260);
  expect(open).toHaveFocus();
});

it('uses Escape to leave the full note without propagating to terminal stock shortcuts', async () => {
  mockedStream.mockResolvedValue(streamResponse('测试纪要标题\n测试正文'));
  render(<TerminalZsxqStream />);
  fireEvent.click(await screen.findByRole('button', { name: /查看全文/ }));
  const globalKey = jest.fn();
  window.addEventListener('keydown', globalKey);
  fireEvent.keyDown(window, { key: 'Escape' });
  expect(screen.queryByRole('region', { name: '机构纪要全文' })).not.toBeInTheDocument();
  expect(globalKey).not.toHaveBeenCalled();
  window.removeEventListener('keydown', globalKey);
});

it('describes an image-only note in the list and loads original images only in the reader', async () => {
  const response = streamResponse('图片纪要标题');
  response.items[0].title = '图片纪要标题';
  response.items[0].images = ['https://images.zsxq.com/verified-original.jpg'];
  response.items[0].image_fulls = response.items[0].images;
  mockedStream.mockResolvedValue(response);
  const { baseElement: container } = render(<TerminalZsxqStream />);
  const open = await screen.findByRole('button', { name: /图片纪要标题.*图片纪要，含 1 张原始资料/ });
  expect(container.querySelector('.tzs-img')).toBeNull();
  fireEvent.click(open);
  expect(screen.getByRole('region', { name: '机构纪要全文' })).toBeInTheDocument();
  expect(container.querySelector('.tzs-img')).toHaveAttribute('src', expect.stringContaining('/api/zsxq/image?u='));
});

it('opens a dedicated reader, removes the repeated source heading, and restores page scrolling', async () => {
  mockedStream.mockResolvedValue(streamResponse('独立阅读标题\n完整正文与最后一段。'));
  const previousOverflow = document.body.style.overflow;
  render(<TerminalZsxqStream />);
  fireEvent.click(await screen.findByRole('button', { name: /独立阅读标题.*查看全文/ }));
  const dialog = screen.getByRole('dialog', { name: '阅读机构纪要' });
  expect(dialog).toHaveAttribute('aria-modal', 'true');
  expect(document.body.style.overflow).toBe('hidden');
  expect(dialog.querySelector('.tzs-item-body')).toHaveTextContent('完整正文与最后一段。');
  expect(dialog.querySelector('.tzs-item-body')).not.toHaveTextContent('独立阅读标题');
  const heading = within(dialog).getByRole('heading', { name: '独立阅读标题' });
  expect(heading).toHaveFocus();
  fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true });
  expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: /分享/ }));
  fireEvent.keyDown(window, { key: 'Escape' });
  expect(screen.queryByRole('dialog', { name: '阅读机构纪要' })).not.toBeInTheDocument();
  expect(document.body.style.overflow).toBe(previousOverflow);
});

it('allows Escape after a previous nested dialog has become hidden', async () => {
  mockedStream.mockResolvedValue(streamResponse('纪要标题\n完整内容'));
  render(<TerminalZsxqStream />);
  fireEvent.click(await screen.findByRole('button', { name: /查看全文/ }));
  const hiddenDialog = document.createElement('div');
  hiddenDialog.setAttribute('role', 'dialog'); hiddenDialog.style.display = 'none';
  document.body.appendChild(hiddenDialog);
  try { fireEvent.keyDown(window, { key: 'Escape' }); expect(screen.queryByRole('dialog', { name: '阅读机构纪要' })).not.toBeInTheDocument(); }
  finally { hiddenDialog.remove(); }
});
