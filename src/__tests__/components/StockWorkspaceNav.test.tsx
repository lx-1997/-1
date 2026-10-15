import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import StockWorkspaceNav from '../../components/StockWorkspaceNav';

it('keeps just one return action in the focused stock navigation', () => {
  const onBack = jest.fn();
  render(<StockWorkspaceNav onBack={onBack} />);
  expect(screen.getAllByRole('button')).toHaveLength(1);
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /返回自选/ }));
  expect(onBack).toHaveBeenCalledTimes(1);
});
