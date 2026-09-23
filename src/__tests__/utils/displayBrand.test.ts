import { normalizePublisherName } from '../../utils/displayBrand';

describe('normalizePublisherName', () => {
  it('uses 稻草财经 for DAO publisher variants in display copy', () => {
    expect(normalizePublisherName('DAO财经')).toBe('稻草财经');
    expect(normalizePublisherName('DAO 财经')).toBe('稻草财经');
    expect(normalizePublisherName('DAO')).toBe('稻草财经');
    expect(normalizePublisherName('路透社 · DAO财经')).toBe('路透社 · 稻草财经');
  });
});
