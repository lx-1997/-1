export interface IntelligenceSource {
  id: string;
  label: string;
  url?: string | null;
  as_of?: string | null;
  fetched_at?: string | null;
  status: string;
  freshness?: string | null;
}

export interface IntelligenceScenario {
  key: 'optimistic' | 'base' | 'pessimistic';
  label: string;
  price: number | null;
  upside_pct: number | null;
  assumptions: string[];
  reasons: string[];
  invalidation: string[];
  source_ids: string[];
  platform_claim_ids?: string[];
  platform_context_claim_ids?: string[];
  reasoning?: ScenarioReasoning;
}

export type ScenarioAssessment = 'support' | 'counter' | 'mixed' | 'unknown';
export type ScenarioDriverKey = 'business' | 'fundamentals' | 'earnings' | 'technical' | 'capital' | 'news';

export interface ScenarioObservation {
  id: string;
  label: string;
  statement: string;
  value: number | string | null;
  unit: string;
  as_of: string | null;
  status: 'available' | 'stale' | 'unknown' | 'missing';
  origin: 'public' | 'platform';
  role: PlatformResearchRole;
  direction: 'support' | 'counter' | 'neutral' | 'unknown';
  source_ids: string[];
  platform_claim_ids: string[];
}

export interface ScenarioDriver {
  key: ScenarioDriverKey;
  label: string;
  assessment: ScenarioAssessment;
  observed: ScenarioObservation[];
  condition: string;
  transmission: string;
  source_ids: string[];
  platform_claim_ids: string[];
  counter_claim_ids: string[];
  context_claim_ids: string[];
  watch: string[];
  invalidation: string[];
  limitations: string[];
}

export interface ScenarioNumericBridge {
  mode: 'ready' | 'sensitivity' | 'insufficient';
  price: number | null;
  currency: string;
  reference_price: number | null;
  reference_as_of: string | null;
  forecast_year: number | null;
  base_eps: number | null;
  base_pe_ttm: number | null;
  eps_factor: number;
  pe_factor: number;
  scenario_eps: number | null;
  scenario_pe_ttm: number | null;
  base_price: number | null;
  eps_effect: number | null;
  pe_effect: number | null;
  combined_effect: number | null;
  upside_pct: number | null;
  source_ids: string[];
  formula: string;
  note: string;
  gaps: string[];
  forecast_sample?: {
    year: number | null;
    currency: string;
    eps_low: number | null;
    eps_median: number | null;
    eps_high: number | null;
    broker_count: number | null;
    as_of: string | null;
    source_ids: string[];
    scenario_vs_sample: 'within' | 'above' | 'below' | 'unknown';
    note: string;
  };
}

export interface ScenarioReasoning {
  version: 1;
  headline: string;
  card_summary?: string;
  assessment: ScenarioAssessment;
  numeric_bridge: ScenarioNumericBridge;
  drivers: ScenarioDriver[];
  source_ids: string[];
  platform_claim_ids: string[];
  counter_claim_ids: string[];
  context_claim_ids: string[];
  watch: string[];
  invalidation: string[];
  limitations: string[];
}

export interface ScenarioReasoningMethod {
  version: 1;
  method: string;
  coverage: { dimensions: number; observed_dimensions: number; current_company_claims: number; background_claims: number };
  source_ids: string[];
  limitations: string[];
}

export interface IntelligenceDimension {
  key: string;
  label: string;
  signal: string;
  headline: string;
  metrics: Array<{ label: string; value: string | number | null; unit?: string }>;
  evidence: string[];
  source_ids: string[];
  platform_claim_ids?: string[];
}

export interface IntelligenceEvidence {
  id: string;
  origin: 'platform' | 'public';
  kind: string;
  title: string;
  summary?: string;
  published_at?: string | null;
  url?: string | null;
  source: string;
  document_id?: string;
  original_id?: string;
  document_revision?: string;
  body_state?: PlatformResearchBodyState;
  role?: PlatformResearchRole;
}

export type PlatformResearchRole = 'fact' | 'opinion' | 'analysis' | 'context';
export type PlatformResearchBodyState = 'full' | 'excerpt' | 'teaser' | 'summary_only' | 'title_only';

export interface PlatformResearchCoverage {
  key: string;
  label: string;
  status: string;
  scanned: number;
  matched: number;
  read: number;
  deduped: number;
  complete: boolean;
  limit_hit: boolean;
  body_states: Partial<Record<PlatformResearchBodyState, number>>;
  read_chars: number;
  relevant_paragraphs: number;
  selected_claims: number;
  period: { from: string | null; to: string | null };
  window: { days: number | null; from: string | null; to: string | null };
  source_ids: string[];
  scope: string;
  reason: string;
}

export interface PlatformResearchCitation {
  id: string;
  source_id: string;
  document_id: string;
  document_revision: string;
  paragraph: number;
  quote: string;
  start: number;
  end: number;
}

export interface PlatformResearchDocument {
  id: string;
  original_id?: string;
  record_namespace?: string;
  revision: string;
  channel: string;
  relation_ids: string[];
  role: PlatformResearchRole;
  title: string;
  published_at: string | null;
  body_state: PlatformResearchBodyState;
  visible_chars: number;
  read_chars: number;
  source_id: string;
  source_name: string;
  excerpt: string;
  url?: string | null;
  relevant_paragraphs: number;
  selected_claims: number;
}

export interface PlatformResearchRelation {
  id: string;
  kind: 'company' | 'industry' | 'peer' | 'upstream' | 'downstream';
  label: string;
  symbol?: string | null;
  basis: string;
  verification: 'verified' | 'candidate' | 'context';
  citation_ids: string[];
  source_ids: string[];
}

export interface PlatformResearchClaim {
  id: string;
  dimension: string;
  topic: string;
  claim: string;
  transmission: string;
  transmission_basis: 'source_explicit' | 'rule_hypothesis';
  watch: string;
  watch_basis: 'source_explicit' | 'rule_hypothesis';
  stance: 'support' | 'counter' | 'mixed' | 'neutral';
  role: PlatformResearchRole;
  body_state: PlatformResearchBodyState;
  relation_ids: string[];
  citations: PlatformResearchCitation[];
  source_ids: string[];
  document_ids: string[];
  as_of: string | null;
  freshness: 'current' | 'historical' | 'unknown';
  period?: string | null;
  currency?: string | null;
  needs_review: boolean;
  verification: string;
}

export interface PlatformResearchConflict {
  id: string;
  dimension: string;
  topic: string;
  claim_ids: string[];
  type: 'different_period' | 'opposing_views' | 'unverified_comparability';
  summary: string;
  verification: 'candidate';
  source_ids: string[];
}

export interface PlatformResearchScenarioLink {
  key: IntelligenceScenario['key'];
  support_claim_ids: string[];
  counter_claim_ids: string[];
  context_claim_ids: string[];
  watch: string[];
  source_ids: string[];
}

export interface PlatformResearch {
  status: 'available' | 'partial' | 'insufficient';
  generated_at: string;
  coverage: PlatformResearchCoverage[];
  documents: PlatformResearchDocument[];
  relations: PlatformResearchRelation[];
  claims: PlatformResearchClaim[];
  citations: PlatformResearchCitation[];
  conflicts: PlatformResearchConflict[];
  scenario_links: PlatformResearchScenarioLink[];
  gaps: string[];
  sources: IntelligenceSource[];
  method: string;
}

export interface StockIntelligence {
  symbol: string;
  name: string;
  market: string;
  currency: string;
  generated_at: string;
  quote: { price: number | null; change_percent?: number | null; as_of?: string | null; source?: string | null };
  summary: { headline: string; stance: string; coverage: { available: number; total: number } };
  valuation: {
    status: 'ready' | 'sensitivity' | 'insufficient';
    method: string;
    horizon: string;
    anchor?: { price?: number | null; pe_ttm?: number | null; forecast_eps?: number | null; forecast_year?: number | null } | string | number | null;
    formula?: string;
    scenarios: IntelligenceScenario[];
    note: string;
    source_ids?: string[];
  };
  dimensions: IntelligenceDimension[];
  technical?: {
    as_of?: string | null;
    adjustment?: string;
    series?: Array<{ date: string; close: number }>;
    series_currency?: string | null;
    source_ids?: string[];
    range?: { lower: number | null; upper: number | null; horizon: string; method: string } | null;
    indicators?: Record<string, number | string | null>;
  };
  capital?: {
    status: 'available' | 'stale' | 'insufficient';
    currency: string | null;
    amount_unit: string;
    price_currency: string | null;
    price_adjustment: string;
    as_of?: string | null;
    fetched_at?: string | null;
    period: { from: string | null; to: string | null };
    series: Array<{ date: string; main_net: number | null; large_net: number | null; super_large_net: number | null; medium_net: number | null; small_net: number | null; close: number | null }>;
    source_ids: string[];
    note: string;
  };
  broker_ratings?: {
    status: 'available' | 'stale' | 'insufficient';
    distribution: Array<{ key: string; label: string; count: number }>;
    samples: Array<{ broker_id: string; broker: string; published_at: string; rating: string; rating_key: string; url?: string | null }>;
    sample_count: number;
    broker_count: number;
    period: { from: string | null; to: string | null };
    as_of?: string | null;
    source_ids: string[];
    note: string;
  };
  platform_research?: PlatformResearch;
  scenario_reasoning?: ScenarioReasoningMethod;
  evidence: IntelligenceEvidence[];
  catalysts: Array<{ event: string; watch: string; source_ids: string[] }>;
  risks: string[];
  data_gaps: string[];
  sources: IntelligenceSource[];
  disclaimer?: string;
}
