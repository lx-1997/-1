import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import ShareButton from '../../components/common/ShareButton';

describe('ShareButton', () => {
  const target = {
    title: 'NVDA 投研体检',
    summary: '英伟达数据中心营收高增，毛利率维持高位。',
    byline: '由 DeepFocus 投研工作台生成',
  };

  it('渲染「分享」按钮，点击后打开弹窗并展示结论标题/摘要/署名', () => {
    render(<ShareButton target={target} modalTitle="分享投研结论" />);

    const trigger = screen.getByRole('button', { name: /分享/ });
    expect(trigger).toBeInTheDocument();

    fireEvent.click(trigger);

    expect(screen.getByText('分享投研结论')).toBeInTheDocument();
    expect(screen.getByText('NVDA 投研体检')).toBeInTheDocument();
    expect(screen.getByText(/英伟达数据中心营收高增/)).toBeInTheDocument();
    expect(screen.getByText('由 DeepFocus 投研工作台生成')).toBeInTheDocument();
    // 无现成链接的 AI 结论应提供「生成公开只读页」入口
    expect(screen.getByRole('button', { name: /生成公开只读页/ })).toBeInTheDocument();
  });

  it('无可访问链接时隐藏「分享链接」段，默认文案含标题+摘要+署名且不伪造链接', () => {
    render(<ShareButton target={target} />);
    fireEvent.click(screen.getByRole('button', { name: /分享/ }));

    // 无 url → 不出现"分享链接"区块
    expect(screen.queryByText('分享链接')).not.toBeInTheDocument();

    const textarea = document.querySelector('.ant-modal-body textarea') as HTMLTextAreaElement;
    expect(textarea).toBeTruthy();
    expect(textarea.placeholder).toContain('NVDA 投研体检');
    expect(textarea.placeholder).toContain('英伟达数据中心营收高增');
    expect(textarea.placeholder).toContain('由 DeepFocus 投研工作台生成');
    // 不应出现伪造的 http(s) 链接
    expect(textarea.placeholder).not.toMatch(/https?:\/\//);
  });

  it('支持惰性 target（函数）：点击时才构造', () => {
    const build = jest.fn(() => target);
    render(<ShareButton target={build} />);

    expect(build).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /分享/ }));
    expect(build).toHaveBeenCalledTimes(1);
    expect(screen.getByText('NVDA 投研体检')).toBeInTheDocument();
  });
});
