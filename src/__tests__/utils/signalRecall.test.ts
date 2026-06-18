import {
  shouldRecall,
  evaluateAndNotify,
  saveRecallPrefs,
  DEFAULT_RECALL_PREFS,
  __resetRecallDedup,
  RecallPrefs,
} from '../../utils/signalRecall';
import type { RealtimeMessageRecord } from '../../services/eventService';

function msg(over: Partial<RealtimeMessageRecord> = {}): RealtimeMessageRecord {
  return {
    id: 'm1',
    title: '股价异动',
    content: '盘中快速拉升',
    topic: 'external-push',
    severity: 'warning',
    symbol: 'TSLA',
    tags: [],
    metadata: {},
    created_at: '2026-06-04T00:00:00Z',
    ...over,
  };
}

const enabled: RecallPrefs = { ...DEFAULT_RECALL_PREFS, browserEnabled: true };

describe('signalRecall.shouldRecall', () => {
  it('总开关关闭时不召回', () => {
    expect(shouldRecall(msg(), ['TSLA'], { ...enabled, browserEnabled: false })).toBe(false);
  });
  it('级别不在触发集合时不召回', () => {
    expect(shouldRecall(msg({ severity: 'info' }), ['TSLA'], enabled)).toBe(false);
  });
  it('「仅自选股」下非自选标的不召回', () => {
    expect(shouldRecall(msg({ symbol: 'NVDA' }), ['TSLA'], enabled)).toBe(false);
  });
  it('「仅自选股」下自选标的且级别命中则召回', () => {
    expect(shouldRecall(msg({ symbol: 'TSLA', severity: 'critical' }), ['TSLA'], enabled)).toBe(true);
  });
  it('「全部信号」下非自选标的也召回', () => {
    expect(shouldRecall(msg({ symbol: 'NVDA' }), ['TSLA'], { ...enabled, scope: 'all' })).toBe(true);
  });
});

describe('signalRecall.evaluateAndNotify', () => {
  let ctor: jest.Mock;

  beforeEach(() => {
    __resetRecallDedup();
    ctor = jest.fn();
    (ctor as unknown as { permission: string }).permission = 'granted';
    (window as unknown as { Notification: unknown }).Notification = ctor;
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
    saveRecallPrefs({ ...enabled, onlyWhenHidden: true });
  });

  it('命中偏好且页面隐藏时弹出桌面通知，标题带标的', () => {
    const fired = evaluateAndNotify(msg({ symbol: 'TSLA', severity: 'warning' }), ['TSLA']);
    expect(fired).toBe(true);
    expect(ctor).toHaveBeenCalledTimes(1);
    expect(String(ctor.mock.calls[0][0])).toContain('TSLA');
  });

  it('同一条信号只弹一次（去重）', () => {
    const m = msg({ id: 'dup', symbol: 'TSLA' });
    expect(evaluateAndNotify(m, ['TSLA'])).toBe(true);
    expect(evaluateAndNotify(m, ['TSLA'])).toBe(false);
    expect(ctor).toHaveBeenCalledTimes(1);
  });

  it('「仅离开时提醒」且页面可见时不打扰', () => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
    expect(evaluateAndNotify(msg({ id: 'visible' }), ['TSLA'])).toBe(false);
    expect(ctor).not.toHaveBeenCalled();
  });
});
