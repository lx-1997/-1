import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import IntelligenceInspector from '../../components/stock-intelligence/IntelligenceInspector';

function renderDetails(nested = false) {
  const onClose = jest.fn();
  const ref = React.createRef<HTMLDivElement>();
  render(<IntelligenceInspector title="情景推演" category="测试" viewKey="scenario" onBack={() => {}} onClose={onClose} bodyRef={ref}>
    <details><summary>盈利兑现条件</summary>{nested ? <details open><summary>来源细节</summary><button>核对原文</button></details> : <button>核对原文</button>}</details>
  </IntelligenceInspector>);
  return { onClose, heading: screen.getByRole('heading', { name: '情景推演' }), summary: screen.getByText('盈利兑现条件'), close: screen.getByRole('button', { name: '关闭详情' }) };
}

it('traps focus on visible summaries and only admits evidence controls after expanding details', () => {
  const { heading, summary, close } = renderDetails();
  const globalKey = jest.fn();
  window.addEventListener('keydown', globalKey);
  try {
    fireEvent.keyDown(heading, { key: 'Tab', shiftKey: true });
    expect(summary).toHaveFocus();
    fireEvent.keyDown(summary, { key: 'Tab' });
    expect(close).toHaveFocus();
    summary.closest('details')!.open = true;
    fireEvent.keyDown(heading, { key: 'Tab', shiftKey: true });
    expect(screen.getByRole('button', { name: '核对原文' })).toHaveFocus();
    expect(globalKey).not.toHaveBeenCalled();
  } finally { window.removeEventListener('keydown', globalKey); }
});

it('does not enter an open nested detail whose parent is collapsed', () => {
  const { heading, summary, onClose } = renderDetails(true);
  fireEvent.keyDown(heading, { key: 'Tab', shiftKey: true });
  expect(summary).toHaveFocus();
  fireEvent.keyDown(summary, { key: 'Escape' });
  expect(onClose).toHaveBeenCalledTimes(1);
});
