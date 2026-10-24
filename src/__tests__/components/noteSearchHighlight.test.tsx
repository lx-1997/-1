import React from 'react';
import { render, fireEvent, waitFor } from '@testing-library/react';
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
    const { getByPlaceholderText, container } = render(<TerminalZsxqStream />);
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
    const { getByPlaceholderText, getByLabelText, container } = render(<TerminalZsxqStream />);
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
