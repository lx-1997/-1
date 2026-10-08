import React from 'react';
import { render, fireEvent } from '@testing-library/react';
import Markdown from '../../components/common/Markdown';

describe('Markdown 内联标的识别（个股中心下钻）', () => {
  it('别名命中渲染为可点 chip 并回传原文', () => {
    const onSymbolClick = jest.fn();
    const { container } = render(
      <Markdown
        content={'苹果盘前涨 1%，英伟达跟随走高。'}
        symbolAliases={['苹果', '英伟达']}
        onSymbolClick={onSymbolClick}
      />
    );
    const chips = container.querySelectorAll('.dfx-md-sym');
    expect(chips.length).toBe(2);
    fireEvent.click(chips[0]);
    expect(onSymbolClick).toHaveBeenCalledWith('苹果');
  });

  it('带市场后缀代码可识别（AAPL.US / 00700.HK）', () => {
    const onSymbolClick = jest.fn();
    const { container } = render(
      <Markdown content={'关注 AAPL.US 与 00700.HK 的联动。'} symbolAliases={[]} onSymbolClick={onSymbolClick} />
    );
    const chips = container.querySelectorAll('.dfx-md-sym');
    expect(chips.length).toBe(2);
    expect(chips[0].textContent).toBe('AAPL.US');
    expect(chips[1].textContent).toBe('00700.HK');
  });

  it('未开启时不改写正文', () => {
    const { container } = render(<Markdown content={'苹果发布会回顾。'} />);
    expect(container.querySelectorAll('.dfx-md-sym').length).toBe(0);
  });

  it('长别名优先：苹果公司不被截断成 苹果+公司', () => {
    const onSymbolClick = jest.fn();
    const { container } = render(
      <Markdown content={'苹果公司发布新品。'} symbolAliases={['苹果', '苹果公司']} onSymbolClick={onSymbolClick} />
    );
    const chips = container.querySelectorAll('.dfx-md-sym');
    expect(chips.length).toBe(1);
    expect(chips[0].textContent).toBe('苹果公司');
  });

  it('代码块内不误标（行内 code 先于标的匹配）', () => {
    const { container } = render(
      <Markdown content={'代码 `AAPL.US` 仅为示例。'} symbolAliases={[]} onSymbolClick={() => {}} />
    );
    expect(container.querySelectorAll('.dfx-md-sym').length).toBe(0);
    expect(container.querySelector('.dfx-md-code-inline')).toBeTruthy();
  });
});
