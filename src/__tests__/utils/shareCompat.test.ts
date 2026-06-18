import { copyText } from '../../utils/clipboard';
import { shareImageNative, shareTextNative, canNativeShare } from '../../utils/share';

// 还原全局，避免用例间串味
const origClipboard = (navigator as any).clipboard;
const origShare = (navigator as any).share;
const origCanShare = (navigator as any).canShare;
const origSecure = (window as any).isSecureContext;
const origExec = (document as any).execCommand;

afterEach(() => {
  (navigator as any).clipboard = origClipboard;
  (navigator as any).share = origShare;
  (navigator as any).canShare = origCanShare;
  (window as any).isSecureContext = origSecure;
  (document as any).execCommand = origExec;
  jest.restoreAllMocks();
});

describe('copyText 跨端复制', () => {
  test('Clipboard API 可用(安全上下文) → 用它，返回 true', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    (navigator as any).clipboard = { writeText };
    (window as any).isSecureContext = true;
    const ok = await copyText('hello');
    expect(ok).toBe(true);
    expect(writeText).toHaveBeenCalledWith('hello');
  });

  test('无 Clipboard API → execCommand 兜底，返回 true（微信/WebView 场景）', async () => {
    (navigator as any).clipboard = undefined;
    (window as any).isSecureContext = false;
    (document as any).execCommand = jest.fn().mockReturnValue(true);
    const ok = await copyText('fallback');
    expect(ok).toBe(true);
    expect((document as any).execCommand).toHaveBeenCalledWith('copy');
  });

  test('Clipboard 抛错 → 也回退 execCommand', async () => {
    (navigator as any).clipboard = { writeText: jest.fn().mockRejectedValue(new Error('denied')) };
    (window as any).isSecureContext = true;
    (document as any).execCommand = jest.fn().mockReturnValue(true);
    const ok = await copyText('x');
    expect(ok).toBe(true);
    expect((document as any).execCommand).toHaveBeenCalled();
  });

  test('两条路都失败 → 返回 false', async () => {
    (navigator as any).clipboard = undefined;
    (document as any).execCommand = jest.fn().mockReturnValue(false);
    const ok = await copyText('x');
    expect(ok).toBe(false);
  });

  test('空字符串 → 直接 false', async () => {
    expect(await copyText('')).toBe(false);
  });
});

describe('shareImageNative 原生分享', () => {
  const blob = new Blob(['x'], { type: 'image/png' });

  test('无 navigator.share → unsupported（自动回退长按）', async () => {
    (navigator as any).share = undefined;
    expect(await shareImageNative(blob)).toBe('unsupported');
  });

  test('canShare 判定不可分享文件 → unsupported', async () => {
    (navigator as any).share = jest.fn();
    (navigator as any).canShare = jest.fn().mockReturnValue(false);
    expect(await shareImageNative(blob)).toBe('unsupported');
    expect((navigator as any).share).not.toHaveBeenCalled();
  });

  test('share 成功 → shared', async () => {
    (navigator as any).share = jest.fn().mockResolvedValue(undefined);
    (navigator as any).canShare = jest.fn().mockReturnValue(true);
    expect(await shareImageNative(blob, { title: 't' })).toBe('shared');
  });

  test('用户取消(AbortError) → shared（不再回退弹图）', async () => {
    const err: any = new Error('cancel'); err.name = 'AbortError';
    (navigator as any).share = jest.fn().mockRejectedValue(err);
    (navigator as any).canShare = jest.fn().mockReturnValue(true);
    expect(await shareImageNative(blob)).toBe('shared');
  });

  test('其它报错 → failed（调用方回退长按）', async () => {
    (navigator as any).share = jest.fn().mockRejectedValue(new Error('boom'));
    (navigator as any).canShare = jest.fn().mockReturnValue(true);
    expect(await shareImageNative(blob)).toBe('failed');
  });
});

describe('shareTextNative / canNativeShare', () => {
  test('canNativeShare 反映 navigator.share 是否存在', () => {
    (navigator as any).share = jest.fn();
    expect(canNativeShare()).toBe(true);
    (navigator as any).share = undefined;
    expect(canNativeShare()).toBe(false);
  });

  test('shareTextNative 无 share → unsupported；成功 → shared', async () => {
    (navigator as any).share = undefined;
    expect(await shareTextNative({ text: 'hi' })).toBe('unsupported');
    (navigator as any).share = jest.fn().mockResolvedValue(undefined);
    expect(await shareTextNative({ text: 'hi', url: 'u' })).toBe('shared');
  });
});
