/**
 * 分享：
 * - App 内（Capacitor 原生）：写入临时文件 → 调系统分享面板（可发微信/朋友圈/存相册），最稳，绕开 WebView 长按不确定性。
 * - 网页：Web Share API（带 files，支持的机型弹原生面板）。
 * - 都不支持 → 返回 unsupported/failed，由调用方回退「长按存图」。
 */
import { Capacitor } from '@capacitor/core';

type ShareResult = 'shared' | 'unsupported' | 'failed';

function isNativeApp(): boolean {
  try { return !!(Capacitor as any)?.isNativePlatform?.(); } catch { return false; }
}

// blob → 纯 base64（去掉 data: 前缀，Filesystem 需要）
function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => { const s = String(fr.result || ''); resolve(s.includes(',') ? s.split(',')[1] : s); };
    fr.onerror = reject;
    fr.readAsDataURL(blob);
  });
}

// App 内：Capacitor 原生分享（插件未装/失败则抛错，上层回退）
async function nativeAppShareImage(blob: Blob, opts: { filename?: string; title?: string; text?: string }): Promise<ShareResult> {
  const [{ Share }, fs] = await Promise.all([import('@capacitor/share'), import('@capacitor/filesystem')]);
  const { Filesystem, Directory } = fs;
  const base64 = await blobToBase64(blob);
  const name = opts.filename || `deepfocus_${Date.now()}.png`;
  const written = await Filesystem.writeFile({ path: name, data: base64, directory: Directory.Cache });
  await Share.share({ title: opts.title, text: opts.text, files: [written.uri] });
  return 'shared';
}

export async function shareImageNative(
  blob: Blob,
  opts: { filename?: string; title?: string; text?: string } = {},
): Promise<ShareResult> {
  // 1) App 内优先原生分享
  if (isNativeApp()) {
    try { return await nativeAppShareImage(blob, opts); }
    catch (e: any) { if (e && e.name === 'AbortError') return 'shared'; /* 插件缺失/失败 → 落到 web */ }
  }
  // 2) 网页 Web Share API（带文件）
  try {
    const nav = navigator as any;
    if (!nav?.share) return 'unsupported';
    const file = new File([blob], opts.filename || 'deepfocus.png', { type: blob.type || 'image/png' });
    if (nav.canShare && !nav.canShare({ files: [file] })) return 'unsupported';
    await nav.share({ files: [file], title: opts.title, text: opts.text });
    return 'shared';
  } catch (e: any) {
    if (e && e.name === 'AbortError') return 'shared';
    return 'failed';
  }
}

export async function shareTextNative(
  opts: { text: string; title?: string; url?: string },
): Promise<ShareResult> {
  // App 内原生分享文本/链接
  if (isNativeApp()) {
    try { const { Share } = await import('@capacitor/share'); await Share.share({ text: opts.text, title: opts.title, url: opts.url }); return 'shared'; }
    catch (e: any) { if (e && e.name === 'AbortError') return 'shared'; }
  }
  try {
    const nav = navigator as any;
    if (!nav?.share) return 'unsupported';
    await nav.share({ text: opts.text, title: opts.title, url: opts.url });
    return 'shared';
  } catch (e: any) {
    if (e && e.name === 'AbortError') return 'shared';
    return 'failed';
  }
}

export function canNativeShare(): boolean {
  try { return isNativeApp() || (typeof navigator !== 'undefined' && !!(navigator as any).share); } catch { return false; }
}
