import { apiGet, apiPost } from '../../services/apiClient';
import { analyzeStock, checkAiApiHealth } from '../../services/researchService';
import type { Stock } from '../../types';

jest.mock('../../services/apiClient', () => ({
  apiGet: jest.fn(),
  apiPost: jest.fn(),
  formatErrorMessage: jest.fn(),
  getApiBaseUrls: jest.fn()
}));

const mockedApiGet = apiGet as jest.MockedFunction<typeof apiGet>;
const mockedApiPost = apiPost as jest.MockedFunction<typeof apiPost>;

const mockStock: Stock = {
  symbol: 'AAPL',
  name: 'Apple Inc.',
  market: 'US',
  exchange: 'NASDAQ',
  currency: 'USD',
  sector: 'Technology',
  marketCap: 3000000000000,
  currentPrice: 175.0,
  changePercent: 1.5,
  description: 'Consumer electronics company',
  focusLevel: 'high',
  totalPosts: 100,
  totalPaidPosts: 20,
  communityScore: 4.5
};

const mockPosts = [
  {
    title: 'Apple Q1 Results',
    summary: 'Strong quarter',
    content: 'Details...',
    category: 'earnings' as const,
    tags: ['tech', 'earnings'],
    qualityScore: 4.0,
    publishTime: '2025-01-01T00:00:00Z'
  }
];

describe('analyzeStock', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('should call the stock analysis API and return the report', async () => {
    const mockResponse = {
      provider: 'openai',
      model: 'gpt-4',
      generated_at: '2025-01-01T00:00:00Z',
      executive_summary: 'AAPL is a strong buy',
      sentiment_label: 'positive' as const,
      sentiment_score: 0.85,
      risk_level: 'low' as const,
      catalysts: ['New product launch'],
      risks: ['Regulatory pressure'],
      watch_items: ['Earnings date'],
      suggested_questions: ['What about competition?'],
      disclaimer: 'This is not financial advice'
    };

    mockedApiPost.mockResolvedValueOnce(mockResponse);

    const result = await analyzeStock({
      stock: mockStock,
      posts: mockPosts,
      question: 'How is AAPL doing?',
      locale: 'zh-CN'
    });

    expect(result).toEqual(mockResponse);
    expect(mockedApiPost).toHaveBeenCalledTimes(1);
    expect(mockedApiPost).toHaveBeenCalledWith('/api/ai/stock-analysis', {
      stock: mockStock,
      posts: mockPosts,
      question: 'How is AAPL doing?',
      locale: 'zh-CN'
    });
  });

  it('should propagate API errors', async () => {
    mockedApiPost.mockRejectedValueOnce(new Error('Network Error'));

    await expect(
      analyzeStock({
        stock: mockStock,
        posts: mockPosts
      })
    ).rejects.toThrow('Network Error');
  });
});

describe('checkAiApiHealth', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('should return API health status', async () => {
    const mockHealth = {
      status: 'ok',
      provider: 'openai',
      model: 'gpt-4'
    };

    mockedApiGet.mockResolvedValueOnce(mockHealth);

    const result = await checkAiApiHealth();

    expect(result).toEqual(mockHealth);
    expect(mockedApiGet).toHaveBeenCalledTimes(1);
    expect(mockedApiGet).toHaveBeenCalledWith('/health');
  });

  it('should throw when health check fails', async () => {
    mockedApiGet.mockRejectedValueOnce(new Error('Connection refused'));

    await expect(checkAiApiHealth()).rejects.toThrow('Connection refused');
  });
});