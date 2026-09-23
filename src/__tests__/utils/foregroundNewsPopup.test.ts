import {
  DEFAULT_FOREGROUND_POPUP_TOPICS,
  foregroundPopupModeLabel,
  matchesForegroundPopupKeywords,
  nextForegroundPopupMode,
  shouldShowForegroundPopup,
  type ForegroundPopupMode,
} from '../../utils/foregroundNewsPopup';
import type { RealtimeMessageRecord } from '../../services/eventService';

const message = (overrides: Partial<RealtimeMessageRecord> = {}): RealtimeMessageRecord => ({
  id: 'news-1',
  title: '测试快讯',
  content: '',
  topic: '快讯',
  severity: 'info',
  tags: [],
  metadata: {},
  created_at: '2026-08-24T10:00:00+08:00',
  ...overrides,
});

describe('foregroundNewsPopup', () => {
  it('重要模式只弹紧急消息或自选相关的非中性消息', () => {
    expect(shouldShowForegroundPopup(message({ severity: 'critical' }), 'important', [])).toBe(true);
    expect(shouldShowForegroundPopup(message({ severity: 'warning' }), 'important', [])).toBe(true);
    expect(shouldShowForegroundPopup(message({ severity: 'success', symbol: '00700.HK' }), 'important', ['00700'])).toBe(true);
    expect(shouldShowForegroundPopup(message({ severity: 'success', symbol: '600519' }), 'important', ['000001'])).toBe(false);
    expect(shouldShowForegroundPopup(message({ severity: 'info', symbol: '600519' }), 'important', ['600519'])).toBe(false);
  });

  it('全部模式仍只处理快讯，关闭模式不弹', () => {
    expect(shouldShowForegroundPopup(message(), 'all', [])).toBe(true);
    expect(shouldShowForegroundPopup(message({ topic: '文章' }), 'all', [])).toBe(false);
    expect(shouldShowForegroundPopup(message({ severity: 'critical' }), 'off', [])).toBe(false);
  });

  it('按 仅重要 → 全部 → 关闭 循环偏好', () => {
    const sequence: ForegroundPopupMode[] = ['important', 'all', 'off'];
    expect(sequence.map(nextForegroundPopupMode)).toEqual(['all', 'off', 'important']);
    expect(foregroundPopupModeLabel.important).toBe('仅重要');
  });

  it('支持四类内容和关键词多选（命中任意关键词）', () => {
    const report = message({ topic: '研报', title: '苹果 AI 产业链更新', severity: 'info', tags: ['AI'] });
    expect(shouldShowForegroundPopup(report, 'all', [], DEFAULT_FOREGROUND_POPUP_TOPICS)).toBe(true);
    expect(shouldShowForegroundPopup(report, 'all', [], DEFAULT_FOREGROUND_POPUP_TOPICS, ['新能源', 'AI'])).toBe(true);
    expect(shouldShowForegroundPopup(report, 'all', [], DEFAULT_FOREGROUND_POPUP_TOPICS, ['新能源', '芯片'])).toBe(false);
    expect(shouldShowForegroundPopup(message({ topic: '机构纪要', severity: 'warning' }), 'important', [], DEFAULT_FOREGROUND_POPUP_TOPICS)).toBe(true);
    expect(matchesForegroundPopupKeywords(report, [])).toBe(true);
  });
});
