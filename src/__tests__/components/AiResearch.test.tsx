import React from 'react';
import { render, fireEvent } from '@testing-library/react';
import type { AppState } from '../../types';
import { analyzeStock, checkAiApiHealth } from '../../services/researchService';

jest.mock('../../services/researchService', () => ({
  analyzeStock: jest.fn(),
  checkAiApiHealth: jest.fn()
}));

jest.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        'app.title': '深度焦点',
        'ai.title': 'AI 投研助手',
        'ai.placeholder': '输入你的问题',
        'ai.continueAsk': '继续提问...',
        'ai.sendHint': '按 Enter 发送，Shift + Enter 换行',
        'ai.hint1': '分析核心竞争力和护城河',
        'ai.hint2': '估值分析和风险评估',
        'ai.hint3': '催化因素与投资机会',
        'ai.failed': '生成失败',
        'header.search': '搜索股票代码或名称...',
      };
      return map[key] || key;
    },
    i18n: { language: 'zh' }
  })
}));

beforeAll(() => {
  Element.prototype.scrollIntoView = jest.fn();
});

const mockAppState: AppState = {
  user: null,
  selectedStock: null,
  selectedPost: null,
  stocks: [
    {
      symbol: 'AAPL',
      name: 'Apple Inc.',
      market: 'US', exchange: 'NASDAQ', currency: 'USD',
      sector: 'Technology', marketCap: 3000000000000,
      currentPrice: 175.0, changePercent: 1.5,
      description: 'Consumer electronics', focusLevel: 'high',
      totalPosts: 100, totalPaidPosts: 20, communityScore: 4.5
    }
  ],
  posts: [], comments: [], ratings: [], payments: [],
  purchasedPosts: [], likedPosts: [], isLoading: false, currentView: 'ai-research',
  platformBalance: 0, rechargeHistory: [], products: [],
  cart: [], orders: [], selectedProduct: null
};

const AiResearch = require('../../components/AiResearch').default;

describe('AiResearch', () => {
  beforeEach(() => {
    (checkAiApiHealth as jest.Mock).mockResolvedValue(null);
  });

  it('should render the AI Research component with stock selector', async () => {
    const { findByText, getByText } = render(<AiResearch appState={mockAppState} />);
    await findByText('AI 投研助手');
    expect(getByText('Apple Inc. (AAPL)')).toBeInTheDocument();
  });

  it('should render the empty state with hint chips', async () => {
    const { findByText, getByText } = render(<AiResearch appState={mockAppState} />);
    await findByText('分析核心竞争力和护城河');
    expect(getByText('估值分析和风险评估')).toBeInTheDocument();
    expect(getByText('催化因素与投资机会')).toBeInTheDocument();
  });

  it('should render the input area', () => {
    const { getByText } = render(<AiResearch appState={mockAppState} />);
    expect(getByText('按 Enter 发送，Shift + Enter 换行')).toBeInTheDocument();
  });

  it('mock 报告在卡片顶部显示「演示数据」可信度提示', async () => {
    const mockReport = {
      provider: 'mock',
      model: 'mock-research-analyst',
      generated_at: new Date().toISOString(),
      executive_summary: '【演示数据】示例投研摘要',
      sentiment_label: 'neutral' as const,
      sentiment_score: 0.1,
      risk_level: 'medium' as const,
      catalysts: [], risks: [], watch_items: [], suggested_questions: [],
      disclaimer: '仅供投研参考，不构成投资建议。',
      data_quality: {
        level: 'mock' as const,
        label: '演示数据',
        detail: '当前为本地演示模型（mock），不能作为投资依据。',
        reasons: []
      }
    };
    (analyzeStock as jest.Mock).mockResolvedValue(mockReport);

    const { container, findByText } = render(<AiResearch appState={mockAppState} />);
    fireEvent.click(container.querySelector('.ai-chat-send-btn') as HTMLElement);

    expect(await findByText('演示数据')).toBeInTheDocument();
    expect(await findByText('当前为本地演示模型（mock），不能作为投资依据。')).toBeInTheDocument();
  });

  it('真实云端报告（live）不显示可信度提示', async () => {
    const liveReport = {
      provider: 'minimax',
      model: 'MiniMax-M3',
      generated_at: new Date().toISOString(),
      executive_summary: '真实分析摘要',
      sentiment_label: 'positive' as const,
      sentiment_score: 0.6,
      risk_level: 'low' as const,
      catalysts: [], risks: [], watch_items: [], suggested_questions: [],
      disclaimer: '仅供投研参考，不构成投资建议。',
      data_quality: { level: 'live' as const, label: '', detail: '', reasons: [] }
    };
    (analyzeStock as jest.Mock).mockResolvedValue(liveReport);

    const { container, findByText, queryByText } = render(<AiResearch appState={mockAppState} />);
    fireEvent.click(container.querySelector('.ai-chat-send-btn') as HTMLElement);

    await findByText('真实分析摘要');
    expect(queryByText('演示数据')).toBeNull();
  });
});
