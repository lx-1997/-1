import React from 'react';
import { act, render } from '@testing-library/react';
import TerminalClock from '../../components/terminal/TerminalClock';

it('updates the terminal clock without rendering its parent once per second', () => {
  jest.useFakeTimers(); jest.setSystemTime(new Date('2026-10-10T00:00:00Z'));
  const parentRender = jest.fn();
  function Parent() { parentRender(); return <section><TerminalClock /></section>; }
  const { container, unmount } = render(<Parent />);
  expect(container.textContent).toBe('08:00:00');
  act(() => jest.advanceTimersByTime(5000));
  expect(container.textContent).toBe('08:00:05');
  expect(parentRender).toHaveBeenCalledTimes(1);
  unmount(); jest.useRealTimers();
});
