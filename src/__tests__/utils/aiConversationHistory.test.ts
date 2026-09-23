import { normalizeAiConversationHistory, type AiHistoryRecord } from '../../utils/aiConversationHistory';

function record(overrides: Partial<AiHistoryRecord> = {}): AiHistoryRecord {
  return {
    id: 'r-1',
    title: '问题',
    question: ' 宁德时代和比亚迪谁更偏向？ ',
    answer: '',
    mode: 'quick' as const,
    updatedAt: 1,
    ...overrides,
  };
}

describe('normalizeAiConversationHistory', () => {
  it('keeps the newest completed attempt and removes pending retries', () => {
    const result = normalizeAiConversationHistory([
      record({ id: 'pending-old', updatedAt: 10 }),
      record({ id: 'pending-new', updatedAt: 20 }),
      record({ id: 'completed', answer: '结论', updatedAt: 15 }),
    ]);

    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('completed');
    expect(result[0].answer).toBe('结论');
  });

  it('merges distinct answered turns while collapsing identical retries', () => {
    const result = normalizeAiConversationHistory([
      record({ id: 'first', updatedAt: 10, turns: [{ id: 't1', question: '同题', answer: '结论 A', mode: 'quick', updatedAt: 10 }] }),
      record({ id: 'retry', updatedAt: 20, turns: [
        { id: 't2', question: ' 同题 ', answer: '结论 A', mode: 'quick', updatedAt: 20 },
        { id: 't3', question: '同题', answer: '结论 B', mode: 'quick', updatedAt: 21 },
      ] }),
    ]);

    expect(result).toHaveLength(1);
    expect(result[0].turns?.map(turn => turn.answer)).toEqual(['结论 A', '结论 B']);
  });

  it('does not merge different modes or different attached documents', () => {
    const result = normalizeAiConversationHistory([
      record({ id: 'quick', answer: '快答', updatedAt: 10 }),
      record({ id: 'deep', mode: 'deep', answer: '深研', updatedAt: 20 }),
      record({ id: 'file-a', answer: '文件 A', attachmentName: 'a.pdf', updatedAt: 30 }),
      record({ id: 'file-b', answer: '文件 B', attachmentName: 'b.pdf', updatedAt: 40 }),
    ]);

    expect(result.map(item => item.id)).toEqual(['file-b', 'file-a', 'deep', 'quick']);
  });

  it('keeps the latest pending record when every attempt failed', () => {
    const result = normalizeAiConversationHistory([
      record({ id: 'failed-old', updatedAt: 10 }),
      record({ id: 'failed-new', updatedAt: 20 }),
    ]);

    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('failed-new');
    expect(result[0].answer).toBe('');
  });
});
