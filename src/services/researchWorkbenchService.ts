import { apiGet, apiPost } from './apiClient';

const DEFAULT_GROUP_ID = '88888142214212';
const DEFAULT_TAG = '海外投行报告';
const DEFAULT_OUT = 'downloads/海外投行报告';
const DEFAULT_SEARCH_PAGES = 100;

export interface ResearchWorkbenchSearchItem {
  fileId?: string;
  topicId?: string;
  name: string;
  size?: number;
  hashtag?: string;
  createTime?: string;
  topicCreateTime?: string;
  downloadCount?: number;
  score?: number;
}

export interface ResearchWorkbenchSearchResponse {
  items: ResearchWorkbenchSearchItem[];
  keyword?: string;
  count: number;
  scannedTopics?: number;
  hashtags?: Array<{ title: string; hashtagId?: string }>;
}

export interface ResearchWorkbenchJob {
  id: string;
  status: string;
  progress?: number;
}

export interface ResearchWorkbenchChatResponse {
  reply: string;
  skill?: string;
  usage?: unknown;
}

export interface ResearchWorkbenchDownloadFile {
  name: string;
  size: number;
  mtime: string;
}

export interface ResearchWorkbenchDownloadsResponse {
  files: ResearchWorkbenchDownloadFile[];
  summary?: {
    total: number;
    downloaded: number;
    failed: number;
    listed: number;
    sizeBytes: number;
  };
}

export interface ResearchWorkbenchSearchPayload {
  keyword: string;
  tag?: string;
  tags?: string[];
  group?: string;
  ext?: string;
  out?: string;
  searchPages?: number;
  resultLimit?: number;
}

export function searchResearchWorkbenchReports(
  payload: ResearchWorkbenchSearchPayload
): Promise<ResearchWorkbenchSearchResponse> {
  const tags = payload.tags?.length
    ? payload.tags
    : String(payload.tag || DEFAULT_TAG)
        .split(/[,，;；、\n\r]+/)
        .map(item => item.trim())
        .filter(Boolean);

  return apiPost<ResearchWorkbenchSearchResponse>('/research-workbench/api/search', {
    authMode: 'env',
    group: payload.group || DEFAULT_GROUP_ID,
    keyword: payload.keyword,
    tag: tags.join(','),
    tags,
    out: payload.out || DEFAULT_OUT,
    ext: payload.ext || 'pdf',
    searchPages: payload.searchPages ?? DEFAULT_SEARCH_PAGES,
    resultLimit: payload.resultLimit ?? 200
  }, { timeout: 600000 });
}

export function summarizeResearchWorkbenchHits(prompt: string): Promise<ResearchWorkbenchChatResponse> {
  return apiPost<ResearchWorkbenchChatResponse>('/research-workbench/api/chat', {
    messages: [{ role: 'user', content: prompt }],
    skill: 'chat',
    includeFile: false,
    prompt
  }, { timeout: 180000 });
}

export function startResearchWorkbenchDownload(
  selectedFiles: ResearchWorkbenchSearchItem[],
  options: { tag?: string; group?: string; out?: string } = {}
): Promise<ResearchWorkbenchJob> {
  return apiPost<ResearchWorkbenchJob>('/research-workbench/api/jobs', {
    authMode: 'env',
    group: options.group || DEFAULT_GROUP_ID,
    tag: options.tag || DEFAULT_TAG,
    out: options.out || DEFAULT_OUT,
    ext: 'pdf',
    selectedFiles,
    limit: 0,
    listOnly: false
  }, { timeout: 60000 });
}

export function listResearchWorkbenchDownloads(out = DEFAULT_OUT): Promise<ResearchWorkbenchDownloadsResponse> {
  return apiGet<ResearchWorkbenchDownloadsResponse>(
    `/research-workbench/api/downloads?out=${encodeURIComponent(out)}`,
    { timeout: 20000 }
  );
}
