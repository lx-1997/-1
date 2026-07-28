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

export interface ContentOntologyTag {
  id: string;
  facet: string;
  facet_label: string;
  code: string;
  label: string;
  confidence: number;
  annotation_source: string;
  color: string;
}

export interface ContentOntologyEntity {
  id: string;
  type: string;
  canonical_key: string;
  label: string;
  market: string;
  role: string;
  confidence: number;
  annotation_source: string;
}

export interface ContentOntologyItem {
  id: string;
  content_type: 'flash' | 'article' | 'research' | 'institution_note' | 'evidence';
  content_type_label: string;
  title: string;
  summary: string;
  source_name: string;
  symbol: string;
  url: string;
  published_at: string;
  tags: ContentOntologyTag[];
  entities: ContentOntologyEntity[];
  tag_count: number;
  facet_count: number;
  annotation_quality: number;
}

export interface ContentOntologyFacet {
  facet: string;
  label: string;
  color: string;
  items: Array<ContentOntologyTag & { count: number }>;
  coverage: number;
}

export interface ContentOntologyGraphNode {
  id: string;
  kind: 'asset' | 'content' | 'tag';
  label: string;
  subtitle: string;
  facet: string;
  content_type?: ContentOntologyItem['content_type'];
  color: string;
  weight: number;
}

export interface ContentOntologyGraphEdge {
  id: string;
  source: string;
  target: string;
  type: 'ABOUT' | 'HAS_TAG';
  label: string;
  confidence: number;
}

export interface ContentOntologyMap {
  generated_at: string;
  security: {
    security_id: string;
    label: string;
    canonical_key: string;
    market: string;
  };
  items: ContentOntologyItem[];
  facets: ContentOntologyFacet[];
  graph: {
    nodes: ContentOntologyGraphNode[];
    edges: ContentOntologyGraphEdge[];
  };
  stats: {
    content_count: number;
    tag_count: number;
    unique_tag_count: number;
    relation_count: number;
    avg_tags_per_content: number;
    avg_facets_per_content: number;
    content_type_counts: Record<string, number>;
    ontology_coverage: number;
  };
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

export async function fetchContentOntologyMap(
  securityId?: string,
  limit = 48,
): Promise<ContentOntologyMap> {
  return apiGet<ContentOntologyMap>('/api/ontology/content-map', {
    params: {
      ...(securityId ? { security_id: securityId } : {}),
      limit,
    },
  });
}
