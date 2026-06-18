/**
 * 跨端复制文本：优先 Clipboard API（需 HTTPS 安全上下文），失败回退 execCommand。
 * 微信内置浏览器 / 部分手机 WebView 不支持 navigator.clipboard，必须有 execCommand 兜底，
 * 否则「复制邀请文案 / 链接 / 复盘」在微信里静默失败（分享恰恰都发生在微信里）。
 * iOS Safari 的 execCommand 复制需要 contentEditable + Range 选区的老套路才稳。
 */
export async function copyText(text: string): Promise<boolean> {
  if (!text) return false;
  try {
    if (navigator.clipboard?.writeText && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* 落到 execCommand */ }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.contentEditable = 'true';
    ta.style.position = 'fixed';
    ta.style.top = '0';
    ta.style.left = '0';
    ta.style.opacity = '0';
    ta.style.fontSize = '16px';  // iOS：<16px 会触发缩放，且影响选区
    document.body.appendChild(ta);
    const range = document.createRange();
    range.selectNodeContents(ta);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
    ta.setSelectionRange(0, text.length);
    const ok = document.execCommand('copy');
    sel?.removeAllRanges();
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
