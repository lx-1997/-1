import { assistantRequestContext, assistantErrorMessage, conciseAssistantFollowups } from '../../utils/aiAssistantExperience';
import { normalizeAiConversationHistory } from '../../utils/aiConversationHistory';
test('concise defaults preserve an explicit request for detailed analysis', () => {
  expect(assistantRequestContext('公司有什么变化')).toContain('优先简明回答');
  expect(assistantRequestContext('请详细分析')).toContain('按用户要求提供详细分析');
  expect(assistantRequestContext('公司有什么变化')).toContain('风险');
});
test('reference is bounded, labelled as an excerpt and separated from the live question', () => {
  const text = assistantRequestContext('影响谁', { kind: 'article', id: 'a', title: '标题', excerpt: 'x'.repeat(4000) });
  expect(text).toContain('不代表已读取全文');
  expect(text).toContain('不是指令');
  expect(text.length).toBeLessThan(1500);
});
test('identical questions on different articles remain separate in research history', () => {
  const base = { title: '问题', question: '有哪些风险', answer: '结果', mode: 'quick', updatedAt: 1 };
  const records = normalizeAiConversationHistory([{ ...base, id: 'a', referenceKey: 'article:a' }, { ...base, id: 'b', referenceKey: 'article:b' }]);
  expect(records).toHaveLength(2);
});
test('network and quota errors do not become misleading success messages', () => {
  expect(assistantErrorMessage('internal', 503)).toContain('暂时不可用');
  expect(assistantErrorMessage('timeout')).toContain('暂未完成');
  expect(assistantErrorMessage('', 429)).toContain('稍后重试');
});

test('answers keep one specific continuation without unrelated market shortcuts', () => {
  expect(conciseAssistantFollowups(['今天大盘怎么样？', '现在哪些板块最热？', '看看今日涨停天梯'])).toEqual([]);
  expect(conciseAssistantFollowups(['核对订单兑现风险？', '再看估值？'])).toEqual(['核对订单兑现风险？']);
  expect(assistantRequestContext('什么是现金流')).toContain('不强行套研究报告结构');
});
