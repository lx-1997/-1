/**
 * 机构纪要分享长图：标题 + 完整正文 + 原图 合成一张竖版长图（2026-10-10 用户拍板完整版，
 * 软墙仅对 /note/{id} 落地页文本生效）。纯 canvas 绘制，无第三方依赖；图片经同源代理
 * /api/zsxq/image fetch 成 blob 再绘制，保证画布不被跨域污染、toBlob 不抛 SecurityError。
 */

export interface NoteShareImageInput {
  title: string;
  date?: string;
  text: string;
  /** 已是同源代理 URL（如 /api/zsxq/image?u=…）的原图地址，按顺序竖排。 */
  imageUrls: string[];
  site: string;
  qr?: { size: number; matrix: number[][] } | null;
  width?: number;
  scale?: number;
  maxTextChars?: number;
  maxImages?: number;
}

interface ImgSlot {
  el: HTMLImageElement;
  ratio: number; // h / w
}

const loadImage = (url: string, timeoutMs = 15000): Promise<HTMLImageElement | null> =>
  new Promise(resolve => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    fetch(url, { signal: controller.signal })
      .then(r => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      .then(blob => new Promise<string>((res, rej) => {
        const fr = new FileReader();
        fr.onload = () => res(String(fr.result || ''));
        fr.onerror = () => rej(fr.error);
        fr.readAsDataURL(blob);
      }))
      .then(dataUrl => new Promise<HTMLImageElement>((res, rej) => {
        const img = new Image();
        img.onload = () => res(img);
        img.onerror = rej;
        img.src = dataUrl;
      }))
      .then(img => resolve(img))
      .catch(() => resolve(null))
      .finally(() => clearTimeout(timer));
  });

type TextLine = { text: string; font: string; lh: number; color: string; mt: number };

export async function drawNoteShareImage(input: NoteShareImageInput): Promise<Blob | null> {
  const W = input.width || 760;
  const PAD = 40;
  const maxW = W - PAD * 2;
  const maxTextChars = input.maxTextChars ?? 6000;
  const maxImages = input.maxImages ?? 9;

  const cv = document.createElement('canvas');
  const ctx = cv.getContext('2d');
  if (!ctx) return null;
  const F = (size: number, weight = '400') => `${weight} ${size}px "PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif`;
  const wrap = (text: string, font: string, mw: number): string[] => {
    ctx.font = font;
    const out: string[] = [];
    let line = '';
    for (const ch of String(text || '')) {
      if (ch === '\n') { out.push(line); line = ''; continue; }
      if (line && ctx.measureText(line + ch).width > mw) { out.push(line); line = ch; } else line += ch;
    }
    if (line) out.push(line);
    return out;
  };

  let text = String(input.text || '').trim();
  if (text.length > maxTextChars) text = text.slice(0, maxTextChars).trimEnd() + '…\n（正文过长截断，扫码看完整版）';

  const imgs: ImgSlot[] = [];
  for (const url of (input.imageUrls || []).slice(0, maxImages)) {
    const el = await loadImage(url);
    if (!el || !el.naturalWidth || !el.naturalHeight) continue;
    imgs.push({ el, ratio: el.naturalHeight / el.naturalWidth });
  }

  // 版面预排：先量出文本行与图片占位高，得到总高后再开画布。
  const lines: TextLine[] = [];
  let h = PAD;
  const push = (t: string, font: string, lh: number, color: string, mt = 0) => {
    const wrapped = wrap(t, font, maxW);
    wrapped.forEach((ln, i) => {
      lines.push({ text: ln, font, lh, color, mt: i === 0 ? mt : 0 });
      h += (i === 0 ? mt : 0) + lh;
    });
  };
  push('稻草财经 · 机构纪要', F(15, '700'), 24, '#ffb000');
  push(String(input.title || '机构纪要'), F(21, '700'), 31, '#f4ecd6', 8);
  if (input.date) push(input.date, F(13), 20, '#8a8463', 4);
  // 星球纪要惯用「# 小节标题」行 → 琥珀加粗小节头；其余按普通段落
  for (const para of text.split('\n')) {
    const t = para.trim();
    if (!t) continue;
    if (/^#\s*/.test(t)) push(t.replace(/^#\s*/, ''), F(15, '700'), 24, '#ffce72', 10);
    else push(t, F(15), 26, '#e2e5ea', 4);
  }

  // 原图等宽竖排；超长截图压到高度上限并水平居中，避免单图吃掉整张长图
  const imgH: number[] = [];
  for (const it of imgs) {
    const drawH = Math.min(Math.round(maxW * it.ratio), 2200);
    imgH.push(drawH);
    h += 14 + drawH + 4;
  }

  const footTop = h + 20;
  const qrSize = input.qr ? 100 : 0;
  const totalH = footTop + Math.max(qrSize, 76) + PAD;

  // iOS/低内存 WebView 画布面积上限约 16M 像素：超限降为 1x，宁可略糊不可空白
  let SC = input.scale || 2;
  if (W * SC * totalH * SC > 14_000_000) SC = 1;
  cv.width = W * SC;
  cv.height = Math.ceil(totalH) * SC;
  ctx.scale(SC, SC);

  ctx.fillStyle = '#0a0d12';
  ctx.fillRect(0, 0, W, totalH);
  ctx.fillStyle = '#ffb000';
  ctx.fillRect(0, 0, W, 4);
  // 斜向平铺半透明品牌水印：标明我方出品（防盗用）
  ctx.save();
  ctx.globalAlpha = 0.04;
  ctx.fillStyle = '#ffb000';
  ctx.font = F(34, '800');
  ctx.translate(W / 2, totalH / 2);
  ctx.rotate(-Math.PI / 7);
  for (let wy = -totalH; wy < totalH; wy += 120) {
    for (let wx = -W; wx < W; wx += 360) ctx.fillText('稻草财经', wx, wy);
  }
  ctx.restore();
  ctx.textBaseline = 'top';

  let y = PAD;
  for (const ln of lines) {
    y += ln.mt;
    ctx.font = ln.font;
    ctx.fillStyle = ln.color;
    ctx.fillText(ln.text, PAD, y);
    y += ln.lh;
  }

  imgs.forEach((it, i) => {
    y += 14;
    const drawH = imgH[i];
    const drawW = drawH / it.ratio;
    const dx = (W - drawW) / 2;
    ctx.drawImage(it.el, dx, y, drawW, drawH);
    ctx.strokeStyle = '#1c2530';
    ctx.strokeRect(dx - 0.5, y - 0.5, drawW + 1, drawH + 1);
    y += drawH + 4;
  });

  ctx.strokeStyle = '#1c2530';
  ctx.beginPath();
  ctx.moveTo(PAD, footTop);
  ctx.lineTo(W - PAD, footTop);
  ctx.stroke();
  if (input.qr) {
    const n = input.qr.size;
    const cell = qrSize / n;
    const qx = W - PAD - qrSize;
    const qy = footTop + 16;
    ctx.fillStyle = '#fff';
    ctx.fillRect(qx - 6, qy - 6, qrSize + 12, qrSize + 12);
    ctx.fillStyle = '#000';
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        if (input.qr.matrix[i][j]) ctx.fillRect(qx + j * cell, qy + i * cell, cell + 0.6, cell + 0.6);
      }
    }
  }
  ctx.font = F(16, '800');
  ctx.fillStyle = '#ffb000';
  ctx.fillText('稻草财经 金融终端', PAD, footTop + 14);
  ctx.font = F(12);
  ctx.fillStyle = '#8a93a0';
  ctx.fillText('扫码看更多机构纪要 · ' + input.site.replace(/^https?:\/\//, ''), PAD, footTop + 40);
  ctx.font = F(10.5);
  ctx.fillStyle = '#5f6671';
  ctx.fillText('第三方调研纪要 · 仅供研究参考，不构成投资建议', PAD, footTop + 60);

  return await new Promise<Blob | null>(res => cv.toBlob(res, 'image/png'));
}
