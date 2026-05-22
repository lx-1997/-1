import { apiGet } from './apiClient';

export type ReadinessStatus = 'pass' | 'warn' | 'fail';
export type SystemReadinessState = 'ready' | 'degraded' | 'not_ready';

export interface SystemReadinessCheck {
  key: string;
  name: string;
  status: ReadinessStatus;
  detail: string;
  remediation?: string | null;
}

export interface SystemReadiness {
  status: SystemReadinessState;
  score: number;
  generated_at: string;
  checks: SystemReadinessCheck[];
  blockers: string[];
  warnings: string[];
}

export async function getSystemReadiness(): Promise<SystemReadiness> {
  return apiGet<SystemReadiness>('/api/system/readiness', { timeout: 8000 });
}
