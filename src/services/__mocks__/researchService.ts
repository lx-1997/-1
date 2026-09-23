export const analyzeStock = jest.fn();

export const checkAiApiHealth = jest.fn(() => Promise.resolve(null));

export const getFinGptCapabilities = jest.fn();

export const getModelConfig = jest.fn();

export const updateModelConfig = jest.fn();

export const extractFileText = jest.fn();

export const scoreSentiment = jest.fn();

export const runStockCheck = jest.fn();

export const summarizeNews = jest.fn();

export const analyzeReport = jest.fn();

export const ragQuery = jest.fn();

export const forecastStock = jest.fn();

export const assessCorridorRisk = jest.fn();

export const createAgentBrief = jest.fn();

export const uploadProfessionalReport = jest.fn();

export const ingestWorkbenchReportFile = jest.fn();

export const ingestProfessionalReportItem = jest.fn();

export const ingestProfessionalReportUrl = jest.fn();

export const listProfessionalReports = jest.fn();

export const listWorkbenchDownloads = jest.fn();

export const listProfessionalMetrics = jest.fn();

export const queryProfessionalRag = jest.fn();

export const analyzeProfessionalReport = jest.fn();

export const runProfessionalEval = jest.fn();

export const searchResearchWorkbenchReports = jest.fn();

export const summarizeResearchWorkbenchHits = jest.fn();

export const startResearchWorkbenchDownload = jest.fn();

export const listResearchWorkbenchDownloads = jest.fn();

// Deep-draft rollout: keep existing component tests that auto-mock this
// service from failing at import time when FinancialTerminal starts importing
// the new generator.  Individual tests can override the resolved article.
export const generateResearchDeepDraft = jest.fn();

export const visionAnalyzeReport = jest.fn();
