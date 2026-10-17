/**
 * 稻草财经 UI Kit 组件测试。
 * 覆盖：加载态交互、焦点管理、键盘导航、ARIA 接线、通知计时与容量淘汰。
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import {
  Badge,
  Button,
  EmptyState,
  IconButton,
  Input,
  Modal,
  Skeleton,
  Spinner,
  TabPanel,
  Tabs,
  ToastProvider,
  Tooltip,
  useToast,
} from '../../components/ui';

describe('Button', () => {
  it('默认 type 为 button，点击触发回调', () => {
    const onClick = jest.fn();
    render(<Button onClick={onClick}>提交</Button>);
    const btn = screen.getByRole('button', { name: '提交' });
    expect(btn).toHaveAttribute('type', 'button');
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('loading 态强制禁用且不触发点击，并声明 aria-busy', () => {
    const onClick = jest.fn();
    render(
      <Button loading onClick={onClick}>
        提交
      </Button>,
    );
    const btn = screen.getByRole('button', { name: '提交' });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute('aria-busy', 'true');
    fireEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe('IconButton', () => {
  it('向读屏器暴露 ariaLabel', () => {
    render(
      <IconButton ariaLabel="刷新行情">
        <span aria-hidden="true">⟳</span>
      </IconButton>,
    );
    expect(screen.getByRole('button', { name: '刷新行情' })).toBeInTheDocument();
  });
});

describe('Input', () => {
  it('label 与输入框完成无障碍接线；error 置 aria-invalid 并关联描述', () => {
    const { rerender } = render(<Input label="预警金额" hint="触发后推送通知" />);
    const input = screen.getByLabelText('预警金额');
    expect(input).toHaveAccessibleDescription('触发后推送通知');

    rerender(<Input label="预警金额" error="请输入有效数值" />);
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input).toHaveAccessibleDescription('请输入有效数值');
  });

  it('required 输出 aria-required', () => {
    render(<Input label="昵称" required />);
    // 标签文本含必填星号，按前缀匹配。
    expect(screen.getByLabelText(/昵称/)).toHaveAttribute('aria-required', 'true');
  });
});

describe('Modal', () => {
  function Harness({ onClose }: { onClose: jest.Mock }) {
    const [open, setOpen] = React.useState(false);
    return (
      <>
        <button type="button" onClick={() => setOpen(true)}>
          打开设置
        </button>
        <Modal open={open} onClose={() => { setOpen(false); onClose(); }} title="账户设置">
          <input aria-label="昵称" />
          <button type="button">保存</button>
        </Modal>
      </>
    );
  }

  it('打开时移入焦点并建立 dialog 语义；Esc 关闭；焦点还原到触发元素', () => {
    const onClose = jest.fn();
    const { getByRole } = render(<Harness onClose={onClose} />);

    const trigger = getByRole('button', { name: '打开设置' });
    trigger.focus();
    fireEvent.click(trigger);

    const dialog = getByRole('dialog', { name: '账户设置' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog.contains(document.activeElement)).toBe(true);

    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(document.activeElement).toBe(trigger);
  });

  it('Tab 在弹窗内循环（焦点圈养）', () => {
    const { getByRole } = render(
      <Modal open onClose={() => undefined} title="循环">
        <input aria-label="一" />
        <button type="button">末尾按钮</button>
      </Modal>,
    );
    const dialog = getByRole('dialog', { name: '循环' });
    const last = getByRole('button', { name: '末尾按钮' });
    last.focus();
    fireEvent.keyDown(dialog, { key: 'Tab' });
    // 从最后一个可聚焦元素继续 Tab，应回到第一个（头部关闭按钮）。
    expect(document.activeElement).toBe(getByRole('button', { name: '关闭对话框' }));
  });

  it('遮罩点击触发关闭；模态内部点击不关闭', () => {
    const onClose = jest.fn();
    const { getByRole, getByText } = render(
      <Modal open onClose={onClose} title="遮罩">
        正文内容
      </Modal>,
    );
    const backdrop = getByRole('dialog', { name: '遮罩' }).parentElement as HTMLElement;
    fireEvent.click(backdrop);
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.click(getByText('正文内容'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('Tabs', () => {
  const items = [
    { key: 'a', label: '概览' },
    { key: 'b', label: '行情' },
    { key: 'c', label: '纪要', disabled: true },
    { key: 'd', label: '提醒' },
  ];

  function TabsHarness(props: Partial<React.ComponentProps<typeof Tabs>>) {
    const [tab, setTab] = React.useState('a');
    return (
      <Tabs items={items} activeKey={tab} onChange={setTab} ariaLabel="数据区" {...props}>
        <TabPanel tabKey="a">概览内容</TabPanel>
        <TabPanel tabKey="b">行情内容</TabPanel>
        <TabPanel tabKey="d">提醒内容</TabPanel>
      </Tabs>
    );
  }

  it('方向键切换且跳过禁用项；面板 ARIA 接线完整', () => {
    const { getByRole } = render(<TabsHarness />);
    const tablist = getByRole('tablist', { name: '数据区' });
    const overviewTab = getByRole('tab', { name: '概览' });

    expect(overviewTab).toHaveAttribute('aria-selected', 'true');
    expect(overviewTab).toHaveAttribute('aria-controls');
    expect(getByRole('tab', { name: '纪要' })).toBeDisabled();

    fireEvent.keyDown(tablist, { key: 'ArrowRight' });
    // “行情”标签带计数徽标，accessible name 为 “行情 12”，按前缀匹配。
    expect(getByRole('tab', { name: /行情/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('行情内容')).toBeInTheDocument();
    expect(screen.queryByText('概览内容')).not.toBeInTheDocument();

    fireEvent.keyDown(tablist, { key: 'ArrowRight' });
    // “纪要”被禁用，直接跳到“提醒”。
    expect(getByRole('tab', { name: '提醒' })).toHaveAttribute('aria-selected', 'true');
  });

  it('Home/End 键定位首尾可用项', () => {
    const { getByRole } = render(<TabsHarness defaultActiveKey="d" />);
    const tablist = getByRole('tablist', { name: '数据区' });
    fireEvent.keyDown(tablist, { key: 'Home' });
    expect(getByRole('tab', { name: '概览' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(tablist, { key: 'End' });
    expect(getByRole('tab', { name: '提醒' })).toHaveAttribute('aria-selected', 'true');
  });
});

describe('Toast', () => {
  function ToastHarness({ api }: { api: (t: ReturnType<typeof useToast>) => void }) {
    const toast = useToast();
    return (
      <button type="button" onClick={() => api(toast)}>
        触发
      </button>
    );
  }

  function renderToast(run: (t: ReturnType<typeof useToast>) => void) {
    return render(
      <ToastProvider>
        <ToastHarness api={run} />
      </ToastProvider>,
    );
  }

  it('success 以 role=status 呈现；error 以 role=alert 呈现', () => {
    renderToast((toast) => {
      toast.success('已保存');
      toast.error('同步失败', '请检查网络');
    });
    fireEvent.click(screen.getByRole('button', { name: '触发' }));
    expect(screen.getByRole('status')).toHaveTextContent('已保存');
    expect(screen.getByRole('alert')).toHaveTextContent('同步失败');
    expect(screen.getByRole('alert')).toHaveTextContent('请检查网络');
  });

  it('到时自动关闭；悬停暂停计时', () => {
    jest.useFakeTimers();
    try {
      renderToast((toast) => toast.info('提示信息', undefined, 1000));
      act(() => {
        fireEvent.click(screen.getByRole('button', { name: '触发' }));
      });
      const toastEl = screen.getByRole('status');

      act(() => {
        jest.advanceTimersByTime(500);
      });
      fireEvent.mouseEnter(toastEl);
      act(() => {
        jest.advanceTimersByTime(2000);
      });
      expect(screen.getByRole('status')).toHaveTextContent('提示信息');

      fireEvent.mouseLeave(toastEl);
      // 剩余 500ms 计时 + 180ms 离场动画。
      act(() => {
        jest.advanceTimersByTime(1000);
      });
      expect(screen.queryByRole('status')).not.toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });

  it('容量上限 4 条，超出丢弃最旧', () => {
    renderToast((toast) => {
      for (let i = 1; i <= 5; i += 1) toast.info(`通知${i}`, undefined, 0);
    });
    fireEvent.click(screen.getByRole('button', { name: '触发' }));
    expect(screen.getAllByRole('status')).toHaveLength(4);
    expect(screen.queryByText('通知1')).not.toBeInTheDocument();
    expect(screen.getByText('通知5')).toBeInTheDocument();
  });
});

describe('Tooltip', () => {
  it('键盘聚焦后延迟显示，aria-describedby 接线；Esc 立即隐藏', () => {
    jest.useFakeTimers();
    try {
      render(
        <Tooltip content="主按钮：每个视图内最多一个">
          <button type="button">提交</button>
        </Tooltip>,
      );
      const btn = screen.getByRole('button', { name: '提交' });
      fireEvent.focus(btn);
      expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();

      act(() => {
        jest.advanceTimersByTime(300);
      });
      const tooltip = screen.getByRole('tooltip');
      expect(tooltip).toHaveTextContent('主按钮：每个视图内最多一个');
      expect(btn.getAttribute('aria-describedby')).toBe(tooltip.id);

      fireEvent.keyDown(btn, { key: 'Escape' });
      expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });
});

describe('基础层', () => {
  it('Spinner 无 label 时对读屏器隐藏，有 label 时以 role=status 播报', () => {
    const { rerender } = render(<Spinner />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    rerender(<Spinner label="行情加载中" />);
    expect(screen.getByRole('status')).toHaveTextContent('行情加载中');
  });

  it('Skeleton 提供视觉隐藏加载声明', () => {
    render(<Skeleton variant="text" lines={3} srLabel="数据加载中" />);
    expect(screen.getByRole('status')).toHaveTextContent('数据加载中');
  });

  it('Badge 渲染语义色调与状态点', () => {
    render(<Badge tone="positive" dot>已连接</Badge>);
    expect(screen.getByText('已连接')).toBeInTheDocument();
  });

  it('EmptyState 渲染标题、描述与行动区', () => {
    render(
      <EmptyState
        title="暂无自选标的"
        description="加入自选后在此汇总展示"
        action={<Button size="sm" variant="secondary">去添加</Button>}
      />,
    );
    expect(screen.getByText('暂无自选标的')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '去添加' })).toBeInTheDocument();
  });
});

describe('受控行为回归', () => {
  it('Modal open=false 不渲染任何内容', async () => {
    render(
      <Modal open={false} onClose={() => undefined} title="隐藏">
        内容
      </Modal>,
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
});
