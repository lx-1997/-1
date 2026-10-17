import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import TerminalHelp from '../../components/TerminalHelp';

/** TerminalHelp 是 UI Kit Modal 的首个业务落点，验证壳体行为与内容回归。 */
describe('TerminalHelp（UI Kit Modal 落点）', () => {
  it('建立 dialog 语义并渲染帮助内容；焦点移入弹窗', () => {
    render(<TerminalHelp onClose={() => undefined} onStartTour={() => undefined} />);
    const dialog = screen.getByRole('dialog', { name: /产品说明/ });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(screen.getByText('免费就能用（无需登录）')).toBeInTheDocument();
    expect(screen.getByText(/仅供研究与教育用途/)).toBeInTheDocument();
    // Kit Modal 初始聚焦弹窗内第一个可聚焦元素（头部关闭按钮）。
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it('引导按钮与关闭按钮回调；Esc 关闭；滚动锁定生效', () => {
    const onClose = jest.fn();
    const onStartTour = jest.fn();
    render(<TerminalHelp onClose={onClose} onStartTour={onStartTour} />);

    fireEvent.click(screen.getByRole('button', { name: /新手引导/ }));
    expect(onStartTour).toHaveBeenCalledTimes(1);

    expect(document.body.style.overflow).toBe('hidden');

    fireEvent.click(screen.getByRole('button', { name: '知道了' }));
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(screen.getByRole('dialog', { name: /产品说明/ }), { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
