/** Shared identity for report entry points, including older preview-only rows. */
export function reportInterpretationKey(report: { file_id?: string | null; preview_url?: string; filename?: string; title?: string }): string {
  const preview = report.preview_url || '';
  const fid = report.file_id || (preview.includes('wire-file')
    ? new URLSearchParams(preview.split('?')[1] || '').get('file_id') : '');
  return `report:${fid || report.filename || report.title}`;
}

export function interpretationAction(cached: boolean, status?: 'running' | 'done' | 'error', readingCache = false) {
  if (cached) return { label: '✓ 查看解读', title: '已有解读，点击直接查看', state: 'cached' };
  if (status === 'running' && readingCache) return { label: '读取解读…', title: '正在读取已有解读，点击查看', state: 'reading' };
  if (status === 'running') return { label: '解读中…', title: '正在生成，点击查看进度', state: 'running' };
  if (status === 'error') return { label: '↻ 重试解读', title: '上次解读未完成，点击重试', state: 'error' };
  return { label: 'AI 解读', title: '生成 AI 解读', state: 'idle' };
}
