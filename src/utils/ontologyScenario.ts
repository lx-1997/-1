export type ScenarioPresetId = 'base' | 'upside' | 'stress' | 'custom';

export interface ScenarioAssumptions {
  demand: number;
  pricing: number;
  cost: number;
  execution: number;
}

export interface ScenarioInputs {
  assumptions: ScenarioAssumptions;
  horizon: number;
  thesisConfidence: number;
  currentWeight: number;
  riskBudget: number;
}

export interface ScenarioProjectionPoint {
  period: string;
  baseline: number;
  simulated: number;
  stress: number;
}

export interface ScenarioResult {
  score: number;
  tone: 'upside' | 'balanced' | 'stress';
  revenueDelta: number;
  marginDelta: number;
  confidence: number;
  confidenceDelta: number;
  expectedReturn: number;
  returnRange: [number, number];
  suggestedWeight: number;
  portfolioImpact: number;
  invalidationRisk: number;
  summary: string;
  action: string;
  projection: ScenarioProjectionPoint[];
}

export interface ScenarioPreset {
  id: Exclude<ScenarioPresetId, 'custom'>;
  label: string;
  helper: string;
  assumptions: ScenarioAssumptions;
}

export const SCENARIO_PRESETS: ScenarioPreset[] = [
  {
    id: 'base',
    label: '基准',
    helper: '延续当前趋势',
    assumptions: { demand: 0, pricing: 0, cost: 0, execution: 0 },
  },
  {
    id: 'upside',
    label: '乐观',
    helper: '需求与执行改善',
    assumptions: { demand: 12, pricing: 8, cost: -6, execution: 10 },
  },
  {
    id: 'stress',
    label: '压力',
    helper: '需求走弱、成本上升',
    assumptions: { demand: -16, pricing: -9, cost: 12, execution: -12 },
  },
];

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function round(value: number, digits = 1): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

export function runOntologyScenario({
  assumptions,
  horizon,
  thesisConfidence,
  currentWeight,
  riskBudget,
}: ScenarioInputs): ScenarioResult {
  const normalizedHorizon = clamp(Math.round(horizon), 1, 8);
  const demand = clamp(assumptions.demand, -20, 20);
  const pricing = clamp(assumptions.pricing, -15, 15);
  const cost = clamp(assumptions.cost, -15, 15);
  const execution = clamp(assumptions.execution, -20, 20);

  const revenueDelta = demand * 0.56 + pricing * 0.62 + execution * 0.18;
  const marginDelta = pricing * 0.2 - cost * 0.26 + execution * 0.12;
  const score = clamp(
    demand * 1.1 + pricing * 1.25 - cost * 0.85 + execution * 0.8,
    -50,
    50,
  );
  const horizonFactor = 0.74 + normalizedHorizon * 0.065;
  const expectedReturn = clamp(score * 0.42 * horizonFactor, -32, 32);
  const uncertainty = 5.5 + normalizedHorizon * 0.9 + Math.abs(score) * 0.055;
  const returnRange: [number, number] = [
    round(clamp(expectedReturn - uncertainty, -45, 45)),
    round(clamp(expectedReturn + uncertainty, -45, 45)),
  ];

  const confidenceDelta = clamp(score * 0.0028, -0.18, 0.18);
  const confidence = clamp(thesisConfidence + confidenceDelta, 0.25, 0.95);
  const invalidationRisk = clamp(50 - score * 0.78, 8, 92);

  const budgetMultiplier = score >= 12
    ? 1
    : score >= -5
      ? 0.82
      : score >= -20
        ? 0.58
        : 0.35;
  const suggestedWeight = round(clamp(
    Math.min(currentWeight, riskBudget) * budgetMultiplier,
    0,
    riskBudget,
  ));
  const portfolioImpact = round((currentWeight * expectedReturn) / 100, 2);
  const tone: ScenarioResult['tone'] = score >= 10
    ? 'upside'
    : score <= -10
      ? 'stress'
      : 'balanced';

  const summary = tone === 'upside'
    ? `经营假设整体改善，论点置信度可升至 ${Math.round(confidence * 100)}%。`
    : tone === 'stress'
      ? `压力假设削弱核心论点，失效风险升至 ${Math.round(invalidationRisk)}%。`
      : `正负变量大致抵消，当前证据不足以支持显著调整。`;
  const action = tone === 'upside'
    ? `仓位上限仍受 ${round(riskBudget)}% 风险预算约束，不因乐观情景追高。`
    : tone === 'stress'
      ? `建议把研究仓位收敛至 ${suggestedWeight}%，并优先验证失效条件。`
      : `建议维持或收敛至 ${suggestedWeight}%，等待关键变量形成方向。`;

  const projection = Array.from({ length: normalizedHorizon + 1 }, (_, index) => {
    if (index === 0) {
      return { period: '当前', baseline: 100, simulated: 100, stress: 100 };
    }
    const progress = index / normalizedHorizon;
    const curve = 0.58 * progress + 0.42 * progress * progress;
    const baselineGrowth = index * 1.25;
    return {
      period: `Q${index}`,
      baseline: round(100 + baselineGrowth),
      simulated: round(100 + baselineGrowth + expectedReturn * curve),
      stress: round(100 + baselineGrowth - (uncertainty + 4) * curve),
    };
  });

  return {
    score: round(score),
    tone,
    revenueDelta: round(revenueDelta),
    marginDelta: round(marginDelta),
    confidence: round(confidence, 3),
    confidenceDelta: round(confidenceDelta, 3),
    expectedReturn: round(expectedReturn),
    returnRange,
    suggestedWeight,
    portfolioImpact,
    invalidationRisk: round(invalidationRisk),
    summary,
    action,
    projection,
  };
}
