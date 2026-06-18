import { apiPost, getApiBaseUrls } from './apiClient';

/**
 * 公开只读分享页（拉新 / SEO）。把一条 AI 结论存成后端快照，
 * 拿到一个免登录、可被搜索引擎收录、社交可预览的公开 URL。
 */

export interface ShareSnapshotInput {
  title: string;
  summary: string;
  byline?: string;
  kind?: string;
}

export interface ShareSnapshotRecord {
  id: string;
  title: string;
  summary: string;
  byline?: string | null;
  kind: string;
  views: number;
  created_at: string;
}

export interface ShareSnapshotResult extends ShareSnapshotRecord {
  /** 拼好的绝对公开页地址：{后端 origin}/s/{id} */
  url: string;
}

export async function createShareSnapshot(input: ShareSnapshotInput): Promise<ShareSnapshotResult> {
  const record = await apiPost<ShareSnapshotRecord>('/api/share/snapshots', input);
  const base = (getApiBaseUrls()[0] || '').replace(/\/$/, '');
  return { ...record, url: `${base}/s/${record.id}` };
}
