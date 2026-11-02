import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ResearchScenarioCalculator from '../../components/ResearchScenarioCalculator';

it('recalculates immediately and saves only after explicit annual/share-basis confirmation', async () => {
  const save = jest.fn().mockResolvedValue(undefined);
  render(<ResearchScenarioCalculator revisionId="snapshot-one" quote={{ currency: 'USD', fields: { price: { status: 'limited', value: 100 } } }} scenarios={[]} busy={false} blocked={false} onSave={save} />);
  expect(screen.getByLabelText('年度 EPS 假设')).toHaveValue(null);
  fireEvent.change(screen.getByLabelText('年度 EPS 假设'), { target: { value: '6' } });
  fireEvent.change(screen.getByLabelText('PE 假设'), { target: { value: '20' } });
  fireEvent.change(screen.getByLabelText('假设币种'), { target: { value: 'USD' } });
  expect(screen.getByText('假设情景价 120 USD')).toBeInTheDocument();
  expect(screen.getByText('相对所选版本行情 +20%')).toBeInTheDocument();
  expect(save).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: '保存这组情景' })).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: '保存这组情景' }));
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ annual_eps: '6', pe: '20', currency: 'USD', share_basis_confirmed: true, revision_id: 'snapshot-one' })));
  fireEvent.change(screen.getByLabelText('PE 假设'), { target: { value: '25' } });
  expect(screen.getByText('假设情景价 150 USD')).toBeInTheDocument();
});

it('refuses nonpositive EPS and does not compare different currencies', () => {
  render(<ResearchScenarioCalculator revisionId="one" quote={{ currency: 'USD', fields: { price: { status: 'usable', value: 100 } } }} scenarios={[]} busy={false} blocked={false} onSave={jest.fn()} />);
  fireEvent.change(screen.getByLabelText('年度 EPS 假设'), { target: { value: '-1' } });
  fireEvent.change(screen.getByLabelText('PE 假设'), { target: { value: '20' } });
  fireEvent.change(screen.getByLabelText('假设币种'), { target: { value: 'HKD' } });
  expect(screen.getByRole('button', { name: '保存这组情景' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('年度 EPS 假设'), { target: { value: '6' } });
  expect(screen.getByText('参考行情缺失或币种不同，不计算相对变化')).toBeInTheDocument();
});
