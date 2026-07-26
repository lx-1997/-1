import { apiGet, apiPost } from './apiClient';

export type OntologyEntityType =
  | 'Portfolio'
  | 'Issuer'
  | 'Security'
  | 'Position'
  | 'Thesis'
  | 'Event'
  | 'Evidence';

export interface OntologyNode {
  id: string;
  type: OntologyEntityType;
  label: string;
  canonical_key: string;
  market: string;
  attributes: Record<string, string | number | boolean | null>;
  position: { x: number; y: number };
}

export interface OntologyEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  polarity: -1 | 0 | 1;
  confidence: number;
}

export interface OntologyDemoAction {
  id: string;
  security_id: string;
  action_type: string;
  action_label: string;
  status: string;
  actor: string;
  reason: string;
  created_at: string;
}

export interface OntologyDemoSnapshot {
  mode: 'demo';
  generated_at: string;
  assets: Array<{ security_id: string; label: string; canonical_key: string }>;
  selected_security_id: string;
  identity: {
    issuer: OntologyNode;
    security: OntologyNode;
    aliases: Array<{ alias: string; scheme: string; market: string }>;
  };
  decision: {
    verdict: string;
    tone: 'positive' | 'warning' | 'neutral';
    change_summary: string;
    recommended_action: string;
    recommended_action_type: string;
    recommended_reason: string;
    thesis: OntologyNode;
    position: OntologyNode;
    supporting_paths: number;
    contradicting_paths: number;
  };
  graph: {
    nodes: OntologyNode[];
    edges: OntologyEdge[];
  };
  actions: OntologyDemoAction[];
  guardrails: string[];
}

export async function fetchOntologyDemo(securityId?: string): Promise<OntologyDemoSnapshot> {
  const query = securityId ? `?security_id=${encodeURIComponent(securityId)}` : '';
  return apiGet<OntologyDemoSnapshot>(`/api/ontology/demo${query}`);
}

export async function recordOntologyDemoAction(payload: {
  security_id: string;
  action_type: string;
  reason: string;
}): Promise<OntologyDemoAction> {
  return apiPost<OntologyDemoAction>('/api/ontology/demo/actions', payload);
}

