import { apiGet, apiPost } from './apiClient';

export type ProfessionalReportType = 'annual' | 'semiannual' | 'quarterly' | 'research' | 'transcript' | 'other';
export type ProfessionalCitationKind = 'chunk' | 'metric';

export interface ProfessionalReportRecord {
  id: string;
  source_item_id?: string | null;
  title: string;
  symbol?: string | null;
  report_type: string;
  period?: string | null;
  parser: string;
  char_count: number;
  metadata: Record<string, any>;
  metrics_count: number;
  chunks_count: number;
  created_at: string;
  updated_at: string;
}

export interface ProfessionalMetricRecord {
  id: string;
  report_id: string;
  symbol?: string | null;
  period?: string | null;
  metric_key: string;
  metric_label: string;
  value?: number | null;
  normalized_value?: number | null;
  unit?: string | null;
  raw_value: string;
  source_page?: number | null;
  source_excerpt: string;
  confidence: number;
  metadata: Record<string, any>;
  created_at: string;
}

export interface ProfessionalCitation {
  citation_id: string;
  kind: ProfessionalCitationKind;
  source_id: string;
  report_id: string;
  report_title: string;
  title: string;
  page?: number | null;
  text: string;
  score: number;
  metadata: Record<string, any>;
}

export interface ProfessionalRagQuery {
  question: string;
  symbol?: string;
  report_id?: string;
  period?: string;
  top_k?: number;
  use_cloud_model?: boolean;
}

export interface ProfessionalRagQueryResult {
  answer: string;
  citations: ProfessionalCitation[];
  metrics: ProfessionalMetricRecord[];
  confidence: number;
  missing: string[];
  disclaimer: string;
}

export interface ProfessionalReportAnalysis {
  report: ProfessionalReportRecord;
  summary: string;
  key_metrics: ProfessionalMetricRecord[];
  quality_flags: string[];
  risks: string[];
  follow_up_questions: string[];
  citations: ProfessionalCitation[];
  confidence: number;
  disclaimer: string;
}

export interface ProfessionalEvalCase {
  question: string;
  expected_text?: string | null;
  expected_metric_key?: string | null;
  expected_refusal?: boolean;
  must_cite?: boolean;
}

export interface ProfessionalEvalRunResult {
  generated_at: string;
  total: number;
  passed: number;
  pass_rate: number;
  citation_rate: number;
  answer_match_rate: number;
  refusal_guard_rate: number;
  cases: Array<{
    question: string;
    answer: string;
    passed: boolean;
    notes: string[];
    citations_present: boolean;
    answer_match: boolean;
    refusal_ok: boolean;
  }>;
}

export interface WorkbenchDownloadFile {
  name: string;
  size: number;
  mtime: string;
}

export interface WorkbenchDownloadsResponse {
  dir: string;
  exists: boolean;
  files: WorkbenchDownloadFile[];
  summary: {
    total: number;
    downloaded: number;
    failed: number;
    listed: number;
    sizeBytes: number;
  };
}

export async function uploadProfessionalReport(payload: {
  file: File;
  symbol?: string;
  title?: string;
  report_type?: ProfessionalReportType;
  period?: string;
  tags?: string[];
}): Promise<ProfessionalReportRecord> {
  const form = new FormData();
  form.append('file', payload.file);
  if (payload.symbol) form.append('symbol', payload.symbol);
  if (payload.title) form.append('title', payload.title);
  if (payload.report_type) form.append('report_type', payload.report_type);
  if (payload.period) form.append('period', payload.period);
  if (payload.tags?.length) form.append('tags', payload.tags.join(','));
  return apiPost<ProfessionalReportRecord>('/api/pro-research/reports/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 60000
  });
}

export async function ingestWorkbenchReportFile(payload: {
  filename: string;
  out?: string;
  symbol?: string;
  title?: string;
  report_type?: ProfessionalReportType;
  period?: string;
  tags?: string[];
}): Promise<ProfessionalReportRecord> {
  return apiPost<ProfessionalReportRecord>('/api/pro-research/reports/ingest-workbench-file', {
    report_type: 'research',
    tags: [],
    ...payload
  }, {
    timeout: 60000
  });
}

export async function ingestProfessionalReportItem(payload: {
  data_item_id: string;
  symbol?: string;
  title?: string;
  report_type?: ProfessionalReportType;
  period?: string;
  tags?: string[];
}): Promise<ProfessionalReportRecord> {
  return apiPost<ProfessionalReportRecord>('/api/pro-research/reports/ingest-item', {
    report_type: 'other',
    tags: [],
    ...payload
  });
}

export async function listProfessionalReports(filters: {
  symbol?: string;
  limit?: number;
} = {}): Promise<ProfessionalReportRecord[]> {
  const response = await apiGet<{ reports: ProfessionalReportRecord[] }>('/api/pro-research/reports', {
    params: filters
  });
  return response.reports;
}

export async function listWorkbenchDownloads(out = 'downloads/海外投行报告'): Promise<WorkbenchDownloadsResponse> {
  return apiGet<WorkbenchDownloadsResponse>('/research-workbench/api/downloads', {
    params: { out }
  });
}

export async function listProfessionalMetrics(filters: {
  report_id?: string;
  symbol?: string;
  metric_key?: string;
  limit?: number;
} = {}): Promise<ProfessionalMetricRecord[]> {
  const response = await apiGet<{ metrics: ProfessionalMetricRecord[] }>('/api/pro-research/metrics', {
    params: filters
  });
  return response.metrics;
}

export async function queryProfessionalRag(payload: ProfessionalRagQuery): Promise<ProfessionalRagQueryResult> {
  return apiPost<ProfessionalRagQueryResult>('/api/pro-research/rag/query', payload);
}

export async function analyzeProfessionalReport(
  reportId: string,
  payload: { focus?: string; use_cloud_model?: boolean } = {}
): Promise<ProfessionalReportAnalysis> {
  return apiPost<ProfessionalReportAnalysis>(`/api/pro-research/reports/${reportId}/analyze`, payload);
}

export async function runProfessionalEval(payload: {
  report_id?: string;
  symbol?: string;
  top_k?: number;
  cases?: ProfessionalEvalCase[];
}): Promise<ProfessionalEvalRunResult> {
  return apiPost<ProfessionalEvalRunResult>('/api/pro-research/evals/run', payload);
}
