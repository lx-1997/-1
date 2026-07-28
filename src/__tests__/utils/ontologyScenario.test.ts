import {
  runOntologyScenario,
  SCENARIO_PRESETS,
} from '../../utils/ontologyScenario';

const common = {
  horizon: 4,
  thesisConfidence: 0.68,
  currentWeight: 12.5,
  riskBudget: 10,
};

describe('runOntologyScenario', () => {
  it('keeps the base case close to the current thesis and within the risk budget', () => {
    const result = runOntologyScenario({
      ...common,
      assumptions: SCENARIO_PRESETS[0].assumptions,
    });

    expect(result.tone).toBe('balanced');
    expect(result.confidence).toBeCloseTo(0.68);
    expect(result.suggestedWeight).toBeLessThanOrEqual(10);
    expect(result.projection).toHaveLength(5);
  });

  it('raises confidence in the upside case and cuts weight in the stress case', () => {
    const upside = runOntologyScenario({
      ...common,
      assumptions: SCENARIO_PRESETS[1].assumptions,
    });
    const stress = runOntologyScenario({
      ...common,
      assumptions: SCENARIO_PRESETS[2].assumptions,
    });

    expect(upside.tone).toBe('upside');
    expect(stress.tone).toBe('stress');
    expect(upside.confidence).toBeGreaterThan(stress.confidence);
    expect(stress.suggestedWeight).toBeLessThan(upside.suggestedWeight);
    expect(stress.portfolioImpact).toBeLessThan(0);
  });

  it('clamps extreme inputs to safe output ranges', () => {
    const result = runOntologyScenario({
      ...common,
      horizon: 99,
      assumptions: { demand: -999, pricing: -999, cost: 999, execution: -999 },
    });

    expect(result.expectedReturn).toBeGreaterThanOrEqual(-32);
    expect(result.invalidationRisk).toBeLessThanOrEqual(92);
    expect(result.suggestedWeight).toBeGreaterThanOrEqual(0);
    expect(result.projection).toHaveLength(9);
  });
});
