import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AsyncButton, AsyncState, WatchlistPanel } from '../../components/market-ui';
import type { QuoteItem } from '../../components/market-ui';

function deferred() {
  let resolve!: () => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<void>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const quotes: readonly QuoteItem[] = [
  { id: 'tencent', symbol: '0700.HK', name: '腾讯控股', price: 0, changePercent: 0, quality: 'live', currency: 'HKD' },
  { id: 'apple', symbol: 'AAPL', name: '苹果', price: 240, changePercent: 3, quality: 'live', currency: 'USD' },
  { id: 'maotai', symbol: '600519.SH', name: '贵州茅台', price: 1600, changePercent: -2, quality: 'delayed', currency: 'CNY' },
  { id: 'meituan', symbol: '3690.HK', name: '美团', price: null, changePercent: null, quality: 'unavailable', currency: 'HKD' },
];

function displayedNames() {
  return screen.getAllByRole('heading')
    .map((heading) => heading.textContent)
    .filter((name) => quotes.some((quote) => quote.name === name));
}

describe('AsyncButton', () => {
  it('blocks duplicate submissions immediately while retaining keyboard focus during the request', async () => {
    const request = deferred();
    const onAction = jest.fn(() => request.promise);
    render(<AsyncButton onAction={onAction}>刷新行情</AsyncButton>);
    const button = screen.getByRole('button', { name: '刷新行情' });
    button.focus();

    // Both clicks occur before React can commit the busy render.
    act(() => {
      button.click();
      button.click();
    });

    expect(onAction).toHaveBeenCalledTimes(1);
    expect(button).toHaveFocus();
    expect(button).not.toBeDisabled();
    expect(button).toHaveAttribute('aria-disabled', 'true');
    expect(button).toHaveAttribute('aria-busy', 'true');

    await act(async () => request.resolve());
    await waitFor(() => expect(button).not.toHaveAttribute('aria-disabled', 'true'));
    fireEvent.click(button);
    expect(onAction).toHaveBeenCalledTimes(2);
    await act(async () => undefined);
  });

  it('reports a rejected action and permits a retry once the action settles', async () => {
    const request = deferred();
    const failure = new Error('行情服务暂时不可用');
    const onError = jest.fn();
    const onAction = jest.fn()
      .mockImplementationOnce(() => request.promise)
      .mockResolvedValueOnce(undefined);
    render(<AsyncButton onAction={onAction} onError={onError}>重试</AsyncButton>);
    const button = screen.getByRole('button', { name: '重试' });
    fireEvent.click(button);

    await act(async () => request.reject(failure));
    expect(onError).toHaveBeenCalledWith(failure);
    await waitFor(() => expect(button).not.toHaveAttribute('aria-disabled', 'true'));
    fireEvent.click(button);
    expect(onAction).toHaveBeenCalledTimes(2);
    await act(async () => undefined);
  });

  it('handles synchronous failures without leaving the button locked', async () => {
    const failure = new Error('无法提交');
    const onAction = jest.fn(() => { throw failure; });
    const onError = jest.fn();
    render(<AsyncButton onAction={onAction} onError={onError}>保存</AsyncButton>);
    const button = screen.getByRole('button', { name: '保存' });
    fireEvent.click(button);

    expect(onError).toHaveBeenCalledWith(failure);
    await waitFor(() => expect(button).not.toHaveAttribute('aria-disabled', 'true'));
  });

  it('respects both externally controlled busy and disabled states', () => {
    const onAction = jest.fn();
    const { rerender } = render(<AsyncButton onAction={onAction} busy>更新</AsyncButton>);
    const button = screen.getByRole('button', { name: '更新' });
    expect(button).toHaveAttribute('aria-busy', 'true');
    fireEvent.click(button);
    expect(onAction).not.toHaveBeenCalled();

    rerender(<AsyncButton onAction={onAction} disabled>更新</AsyncButton>);
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onAction).not.toHaveBeenCalled();
  });

  it('prevents repeated native form submits while a submit action is pending', async () => {
    const request = deferred();
    const onAction = jest.fn(() => request.promise);
    const onSubmit = jest.fn();
    render(
      <form onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
        <AsyncButton type="submit" onAction={onAction}>提交设置</AsyncButton>
      </form>,
    );
    const button = screen.getByRole('button', { name: '提交设置' });
    act(() => {
      button.click();
      button.click();
    });
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(button).not.toBeDisabled();

    await act(async () => request.resolve());
    act(() => button.click());
    expect(onAction).toHaveBeenCalledTimes(2);
    expect(onSubmit).toHaveBeenCalledTimes(2);
    await act(async () => undefined);
  });
});

describe('AsyncState', () => {
  it('announces the initial load and exposes an actionable failure instead of unfinished content', async () => {
    const user = userEvent.setup();
    const onRetry = jest.fn();
    const { rerender } = render(<AsyncState status="loading"><p>未完成的行情</p></AsyncState>);
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.queryByText('未完成的行情')).not.toBeInTheDocument();

    rerender(<AsyncState status="error" error="行情请求失败" onRetry={onRetry}><p>未完成的行情</p></AsyncState>);
    expect(screen.getByRole('alert')).toHaveTextContent('行情请求失败');
    expect(screen.queryByText('未完成的行情')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /重试/ }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('keeps the last successful content readable while reloading or reporting a refresh error', () => {
    const { rerender } = render(<AsyncState status="loading" hasData><p>上次行情：240.00</p></AsyncState>);
    expect(screen.getByText('上次行情：240.00')).toBeInTheDocument();

    rerender(<AsyncState status="error" hasData error="刷新失败，展示上次结果"><p>上次行情：240.00</p></AsyncState>);
    expect(screen.getByText('上次行情：240.00')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('刷新失败，展示上次结果');
  });
});

describe('WatchlistPanel', () => {
  it('offers keyboard-accessible search and matches symbols with whitespace and case normalized', async () => {
    const user = userEvent.setup();
    render(<WatchlistPanel items={quotes} />);
    await user.click(screen.getByRole('searchbox', { name: '搜索观察池' }));
    await user.type(screen.getByRole('searchbox', { name: '搜索观察池' }), '  aapl  ');

    expect(screen.getByRole('heading', { name: '苹果' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '腾讯控股' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '贵州茅台' })).not.toBeInTheDocument();
  });

  it('distinguishes an empty inventory from an empty search and lets users clear the filter', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<WatchlistPanel items={quotes} />);
    await user.type(screen.getByRole('searchbox', { name: '搜索观察池' }), '不存在的标的');
    expect(screen.getByText('没有匹配的标的')).toBeInTheDocument();
    expect(screen.queryByText('观察池为空')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '清除搜索' }));
    expect(screen.getByRole('searchbox', { name: '搜索观察池' })).toHaveValue('');
    expect(displayedNames()).toEqual(quotes.map((quote) => quote.name));

    rerender(<WatchlistPanel items={[]} emptyAction={<button type="button">添加标的</button>} />);
    expect(screen.getByText('观察池为空')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '添加标的' })).toBeInTheDocument();
  });

  it('sorts zero changes correctly, always leaves missing changes last, and preserves input order', async () => {
    const user = userEvent.setup();
    const originalIds = quotes.map((quote) => quote.id);
    render(<WatchlistPanel items={quotes} />);
    const sort = screen.getByRole('combobox', { name: '排序' });
    await user.selectOptions(sort, 'change-desc');
    expect(displayedNames()).toEqual(['苹果', '腾讯控股', '贵州茅台', '美团']);
    await user.selectOptions(sort, 'change-asc');
    expect(displayedNames()).toEqual(['贵州茅台', '腾讯控股', '苹果', '美团']);
    await user.selectOptions(sort, 'default');
    expect(displayedNames()).toEqual(quotes.map((quote) => quote.name));
    expect(quotes.map((quote) => quote.id)).toEqual(originalIds);
  });

  it('retains existing quotes when a refresh fails and exposes the failure to assistive technology', () => {
    render(<WatchlistPanel items={quotes} status="error" error="网络暂时不可用" />);
    expect(screen.getByRole('alert')).toHaveTextContent('网络暂时不可用');
    expect(screen.getByRole('heading', { name: '腾讯控股' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '苹果' })).toBeInTheDocument();
    expect(within(screen.getByRole('article', { name: '腾讯控股' })).getByText('缓存行情')).toBeInTheDocument();
    expect(screen.queryByText('实时行情')).not.toBeInTheDocument();
    expect(screen.queryByText('观察池为空')).not.toBeInTheDocument();
  });

  it('announces a valid zero distinctly from unavailable quotes', () => {
    render(<WatchlistPanel items={quotes} />);
    const zeroQuote = within(screen.getByRole('article', { name: '腾讯控股' }));
    expect(zeroQuote.getByText('价格 HKD 0.00')).toBeInTheDocument();
    expect(zeroQuote.getByText('持平 0.00%')).toBeInTheDocument();
    const missingQuote = within(screen.getByRole('article', { name: '美团' }));
    expect(missingQuote.getByText('暂无价格')).toBeInTheDocument();
    expect(missingQuote.getByText('暂无涨跌幅')).toBeInTheDocument();
    expect(missingQuote.getByText('暂无行情')).toBeInTheDocument();
  });

  it('lets keyboard users select a quote while exposing the current controlled selection', async () => {
    const user = userEvent.setup();
    const onSelect = jest.fn();
    const { rerender } = render(<WatchlistPanel items={quotes} onSelect={onSelect} selectedId="tencent" />);
    const current = screen.getByRole('button', { name: '查看 腾讯控股（0700.HK）行情' });
    const apple = screen.getByRole('button', { name: '查看 苹果（AAPL）行情' });
    expect(current).toHaveAttribute('aria-current', 'true');
    apple.focus();
    await user.keyboard('{Enter}');
    expect(onSelect).toHaveBeenCalledWith(quotes[1]);

    // Parent owns selection; a click alone must not silently change it.
    expect(current).toHaveAttribute('aria-current', 'true');
    rerender(<WatchlistPanel items={quotes} onSelect={onSelect} selectedId="apple" />);
    expect(apple).toHaveAttribute('aria-current', 'true');
    expect(current).not.toHaveAttribute('aria-current', 'true');
  });

  it('keeps watch state controlled and prevents repeated toggles without also selecting the quote', async () => {
    const request = deferred();
    const onToggleWatch = jest.fn(() => request.promise);
    const onSelect = jest.fn();
    const { rerender } = render(<WatchlistPanel items={quotes} watchedIds={['tencent']} onToggleWatch={onToggleWatch} onSelect={onSelect} />);
    const toggle = screen.getByRole('button', { name: '监控 腾讯控股（0700.HK）' });
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    toggle.focus();
    fireEvent.click(toggle);
    fireEvent.click(toggle);
    expect(onToggleWatch).toHaveBeenCalledTimes(1);
    expect(onToggleWatch).toHaveBeenCalledWith(quotes[0]);
    expect(onSelect).not.toHaveBeenCalled();
    expect(toggle).toHaveFocus();
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    await act(async () => request.resolve());

    rerender(<WatchlistPanel items={quotes} watchedIds={[]} onToggleWatch={onToggleWatch} onSelect={onSelect} />);
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
  });

  it('blocks repeated refreshes until the previous refresh settles', async () => {
    const request = deferred();
    const onRefresh = jest.fn(() => request.promise);
    render(<WatchlistPanel items={quotes} onRefresh={onRefresh} />);
    const refresh = screen.getByRole('button', { name: /刷新/ });
    fireEvent.click(refresh);
    fireEvent.click(refresh);
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('heading', { name: '苹果' })).toBeInTheDocument();
    expect(within(screen.getByRole('article', { name: '苹果' })).getByText('缓存行情')).toBeInTheDocument();
    expect(screen.queryByText('实时行情')).not.toBeInTheDocument();
    await act(async () => request.resolve());
    await waitFor(() => expect(refresh).not.toHaveAttribute('aria-disabled', 'true'));
  });

  it('shares a request lock between the error retry action and header refresh', async () => {
    const request = deferred();
    const onRefresh = jest.fn(() => request.promise);
    render(<WatchlistPanel items={quotes} status="error" error="连接失败" onRefresh={onRefresh} />);
    const retry = screen.getByRole('button', { name: '重试' });
    const refresh = screen.getByRole('button', { name: '刷新行情' });
    act(() => {
      retry.click();
      refresh.click();
    });
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(retry).toHaveAttribute('aria-disabled', 'true');
    expect(refresh).toHaveAttribute('aria-disabled', 'true');

    await act(async () => request.resolve());
    await waitFor(() => expect(refresh).not.toHaveAttribute('aria-disabled', 'true'));
  });

  it('keeps a pending watch request locked when its card is filtered out and shown again', async () => {
    const user = userEvent.setup();
    const request = deferred();
    const onToggleWatch = jest.fn(() => request.promise);
    render(<WatchlistPanel items={quotes} onToggleWatch={onToggleWatch} />);
    await user.click(screen.getByRole('button', { name: '监控 腾讯控股（0700.HK）' }));
    const search = screen.getByRole('searchbox', { name: '搜索观察池' });
    await user.type(search, 'aapl');
    expect(screen.queryByRole('button', { name: '监控 腾讯控股（0700.HK）' })).not.toBeInTheDocument();
    await user.clear(search);
    const restoredToggle = screen.getByRole('button', { name: '监控 腾讯控股（0700.HK）' });
    expect(restoredToggle).toHaveAttribute('aria-disabled', 'true');
    await user.click(restoredToggle);
    expect(onToggleWatch).toHaveBeenCalledTimes(1);

    await act(async () => request.resolve());
    await waitFor(() => expect(restoredToggle).not.toHaveAttribute('aria-disabled', 'true'));
  });

  it('clears a previous internal refresh failure when the parent supplies newer successful quotes', async () => {
    const user = userEvent.setup();
    const initialQuotes = quotes.map((quote) => ({ ...quote, updatedAt: '2026-10-12T01:00:00Z' }));
    const onRefresh = jest.fn().mockRejectedValue(new Error('网络错误'));
    const { rerender } = render(<WatchlistPanel items={initialQuotes} onRefresh={onRefresh} />);
    await user.click(screen.getByRole('button', { name: '刷新行情' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('刷新失败');
    expect(within(screen.getByRole('article', { name: '腾讯控股' })).getByText('缓存行情')).toBeInTheDocument();

    const updatedQuotes = initialQuotes.map((quote) => ({ ...quote, updatedAt: '2026-10-12T01:01:00Z' }));
    rerender(<WatchlistPanel items={updatedQuotes} status="ready" onRefresh={onRefresh} />);
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(within(screen.getByRole('article', { name: '腾讯控股' })).getByText('实时行情')).toBeInTheDocument();
  });

  it('keeps newly added quotes live and clears the refresh error when all failed quotes are removed', async () => {
    const user = userEvent.setup();
    const oldApple = { ...quotes[1], updatedAt: '2026-10-12T01:00:00Z' };
    const newTencent = { ...quotes[0], updatedAt: '2026-10-12T01:01:00Z' };
    const onRefresh = jest.fn().mockRejectedValue(new Error('网络错误'));
    const { rerender } = render(<WatchlistPanel items={[oldApple]} onRefresh={onRefresh} />);
    await user.click(screen.getByRole('button', { name: '刷新行情' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('刷新失败');

    rerender(<WatchlistPanel items={[oldApple, newTencent]} onRefresh={onRefresh} />);
    expect(within(screen.getByRole('article', { name: '苹果' })).getByText('缓存行情')).toBeInTheDocument();
    expect(within(screen.getByRole('article', { name: '腾讯控股' })).getByText('实时行情')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('刷新失败');

    rerender(<WatchlistPanel items={[newTencent]} onRefresh={onRefresh} />);
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(screen.queryByRole('article', { name: '苹果' })).not.toBeInTheDocument();
    expect(within(screen.getByRole('article', { name: '腾讯控股' })).getByText('实时行情')).toBeInTheDocument();
  });

  it('restores a partially refreshed quote to live while unchanged quotes remain cached', async () => {
    const user = userEvent.setup();
    const oldApple = { ...quotes[1], updatedAt: '2026-10-12T01:00:00Z' };
    const oldTencent = { ...quotes[0], updatedAt: '2026-10-12T01:00:00Z' };
    const onRefresh = jest.fn().mockRejectedValue(new Error('网络错误'));
    const { rerender } = render(<WatchlistPanel items={[oldApple, oldTencent]} onRefresh={onRefresh} />);
    await user.click(screen.getByRole('button', { name: '刷新行情' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('刷新失败');

    const updatedApple = { ...oldApple, updatedAt: '2026-10-12T01:01:00Z' };
    rerender(<WatchlistPanel items={[updatedApple, oldTencent]} onRefresh={onRefresh} />);
    expect(within(screen.getByRole('article', { name: '苹果' })).getByText('实时行情')).toBeInTheDocument();
    expect(within(screen.getByRole('article', { name: '腾讯控股' })).getByText('缓存行情')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('刷新失败');
  });
});
