import { getPremarketOpportunities } from '../../services/marketService';

jest.mock('../../services/apiClient', () => ({
  ...jest.requireActual('../../services/apiClient'),
  apiGet: jest.fn(),
}));

const sampleResponse = {
  generated_at: '2026-10-25T01:00:00Z',
  market_phase: '盘前',
  regime_score: 60,
  summary: '样本',
  themes: [],
  risk_watchlist: [],
  confirmation_checklist: [],
  warnings: [],
};

// marketService 的单飞/TTL 是模块级状态：每个用例在隔离的模块注册表里
// 重新加载 service 与被 mock 的 apiClient，保证用例互不污染。
function loadModule() {
  let service!: typeof import('../../services/marketService');
  let client!: typeof import('../../services/apiClient');
  jest.isolateModules(() => {
    client = require('../../services/apiClient');
    service = require('../../services/marketService');
  });
  return {
    fetch: service.getPremarketOpportunities,
    apiGetMock: client.apiGet as jest.Mock,
  };
}

describe('getPremarketOpportunities 单飞 + TTL 缓存', () => {
  test('并发调用共享同一 in-flight 请求，只发一次网络', async () => {
    const { fetch, apiGetMock } = loadModule();
    let resolveApi!: (value: unknown) => void;
    apiGetMock.mockImplementationOnce(() => new Promise(resolve => { resolveApi = resolve; }));

    const first = fetch();
    const second = fetch();
    const third = fetch();

    expect(apiGetMock).toHaveBeenCalledTimes(1);

    resolveApi(sampleResponse);
    const results = await Promise.all([first, second, third]);
    expect(results.every(item => item === sampleResponse)).toBe(true);
    expect(apiGetMock).toHaveBeenCalledTimes(1);
  });

  test('TTL 内重复调用命中缓存，不重新发起请求', async () => {
    const { fetch, apiGetMock } = loadModule();
    apiGetMock.mockResolvedValueOnce(sampleResponse);
    await fetch();

    const cached = await fetch();
    expect(cached).toBe(sampleResponse);
    expect(apiGetMock).toHaveBeenCalledTimes(1);
  });

  test('TTL 过期后重新回源', async () => {
    const { fetch, apiGetMock } = loadModule();
    const nowSpy = jest.spyOn(Date, 'now');
    nowSpy.mockReturnValue(1_000_000);
    apiGetMock.mockResolvedValueOnce(sampleResponse);
    await fetch();

    const next = { ...sampleResponse, regime_score: 80 };
    apiGetMock.mockResolvedValueOnce(next);
    nowSpy.mockReturnValue(1_000_000 + 45_001);
    const fresh = await fetch();

    expect(fresh).toBe(next);
    expect(apiGetMock).toHaveBeenCalledTimes(2);
    nowSpy.mockRestore();
  });

  test('失败不写缓存：并发共享同一拒绝，恢复后下次调用重新回源', async () => {
    const { fetch, apiGetMock } = loadModule();
    let rejectApi!: (error: Error) => void;
    apiGetMock.mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectApi = reject; }));

    const first = fetch();
    const second = fetch();
    expect(apiGetMock).toHaveBeenCalledTimes(1);

    rejectApi(new Error('network down'));
    await expect(first).rejects.toThrow('network down');
    await expect(second).rejects.toThrow('network down');

    const recovered = { ...sampleResponse, regime_score: 70 };
    apiGetMock.mockResolvedValueOnce(recovered);
    const third = await fetch();
    expect(third).toBe(recovered);
    expect(apiGetMock).toHaveBeenCalledTimes(2);
  });
});
