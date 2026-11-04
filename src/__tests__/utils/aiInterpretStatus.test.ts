import { interpretationAction, reportInterpretationKey } from '../../utils/aiInterpretStatus';

test('preview-only and file-id reports share their local cache identity', () => {
  expect(reportInterpretationKey({ file_id: 'abc' })).toBe(reportInterpretationKey({ preview_url: '/api/research/wire-file?file_id=abc&name=x' }));
});

test('completed metadata alone does not claim a usable cache', () => {
  expect(interpretationAction(false, 'done').state).toBe('idle');
  expect(interpretationAction(true, 'done').label).toBe('✓ 查看解读');
  expect(interpretationAction(false, 'running').label).toBe('解读中…');
  expect(interpretationAction(false, 'error').label).toBe('↻ 重试解读');
});

test('reading an existing server result is distinct from model generation', () => {
  expect(interpretationAction(false, 'running', true).label).toBe('读取解读…');
  expect(interpretationAction(false, 'running', false).label).toBe('解读中…');
});
