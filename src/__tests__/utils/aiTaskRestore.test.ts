/**
 * aiTaskRestore 规划器单测：穷举恢复三路分流与云端合并的边界。
 * 这些判定曾在组件内以 effect 形态存在，StrictMode 双挂载下出过
 * 「合法记录被误判幽灵删光」的回归——纯函数化后在此固化正确行为。
 */
import { planLocalRestores, planCloudRestores, INTERRUPTED_RESUME_TEXT, FAILED_RESUME_TEXT } from '../../utils/aiTaskRestore';
import type { AiTaskHistoryEntry } from '../../utils/aiInterpretCache';

const WIRE = { title: '研报甲', filename: 'a.pdf' };

function hist(partial: Partial<AiTaskHistoryEntry> & { key: string }): AiTaskHistoryEntry {
  return {
    kind: 'report', title: partial.key, report: WIRE, meta: null,
    startedAt: 1000, ...partial,
  } as AiTaskHistoryEntry;
}

describe('planLocalRestores', () => {
  const cache = (result: unknown) => new Map([['report:done-1', { result, deepDraft: undefined }]]);

  it('done + 缓存 → done 任务（携带缓存结果，可回看）', () => {
    const plan = planLocalRestores([hist({ key: 'report:done-1' })], cache('结论'));
    expect(plan.tasks).toHaveLength(1);
    expect(plan.tasks[0]).toMatchObject({ key: 'report:done-1', status: 'done', result: '结论', error: '' });
    expect(plan.staleHistoryKeys).toEqual([]);
  });

  it('done 但缓存淘汰 → 真幽灵，进入自愈清除名单', () => {
    const plan = planLocalRestores([hist({ key: 'report:ghost' })], new Map());
    expect(plan.tasks).toHaveLength(0);
    expect(plan.staleHistoryKeys).toEqual(['report:ghost']);
  });

  it('running → 可续跑任务，文案为「中断，点击继续」', () => {
    const plan = planLocalRestores([hist({ key: 'report:run-1', status: 'running' })], new Map());
    expect(plan.tasks[0]).toMatchObject({ key: 'report:run-1', status: 'error', error: INTERRUPTED_RESUME_TEXT });
    expect(plan.staleHistoryKeys).toEqual([]);   // 中断条目绝不是幽灵
  });

  it('error → 可续跑任务，保留原始失败原因', () => {
    const plan = planLocalRestores([hist({ key: 'report:err-1', status: 'error', error: '解读超时' })], new Map());
    expect(plan.tasks[0]).toMatchObject({ status: 'error', error: '解读超时' });
  });

  it('旧版无 status 字段的条目按 done 处理（向后兼容）', () => {
    const entry = hist({ key: 'report:done-1' });
    delete (entry as Partial<AiTaskHistoryEntry>).status;
    const withCache = new Map([['report:done-1', { result: 'x' }]]);
    expect(planLocalRestores([entry], withCache).tasks[0].status).toBe('done');
    expect(planLocalRestores([entry], new Map()).staleHistoryKeys).toEqual(['report:done-1']);
  });

  it('无 status 且无缓存且无 error 的旧条目 → 兜底文案而不是 undefined', () => {
    const entry = hist({ key: 'report:legacy', status: 'error' });
    delete (entry as Partial<AiTaskHistoryEntry>).error;
    const plan = planLocalRestores([entry], new Map());
    expect(plan.tasks[0].error).toBe(FAILED_RESUME_TEXT);
  });

  it('内存中已存在的键（skipKeys）跳过——StrictMode 双挂载/同 key 任务不重复注入', () => {
    const plan = planLocalRestores(
      [hist({ key: 'report:done-1' }), hist({ key: 'report:run-1', status: 'running' })],
      cache('结论'),
      new Set(['report:done-1']),
    );
    expect(plan.tasks.map(t => t.key)).toEqual(['report:run-1']);
  });

  it('同一规划重复执行结果一致（幂等，双挂载安全）', () => {
    const history = [hist({ key: 'report:done-1' }), hist({ key: 'report:run-1', status: 'running' })];
    const a = planLocalRestores(history, cache('结论'));
    const b = planLocalRestores(history, cache('结论'));
    expect(b).toEqual(a);
  });

  it('快讯中断条目携带 content（续跑需要原文）', () => {
    const plan = planLocalRestores(
      [{ ...hist({ key: 'news:42', kind: 'news', status: 'running' }), content: '快讯原文' }],
      new Map(),
    );
    expect(plan.tasks[0]).toMatchObject({ kind: 'news', content: '快讯原文', status: 'error' });
  });
});

describe('planCloudRestores', () => {
  const memory = (over: Record<string, unknown> = {}) => ({
    id: 'm1', hall: 'ai_interpret', title: '云端解读',
    content: JSON.stringify({ key: 'report:cloud-1', kind: 'report', title: '云端解读', report: WIRE, meta: { org: 'X' }, startedAt: 5000, ...over }),
    created_at: '2026-10-11T08:00:00Z',
    ...over,
  });

  it('ai_interpret 记录 → 云端任务种子（cloud 标记，done 态无结果）', () => {
    const plan = planCloudRestores([memory()]);
    expect(plan).toHaveLength(1);
    expect(plan[0]).toMatchObject({ key: 'report:cloud-1', cloud: true, status: 'done' });
    expect(plan[0].result).toBeUndefined();
    expect(plan[0].startedAt).toBe(Date.parse('2026-10-11T08:00:00Z'));
  });

  it('非 ai_interpret hall、非研报类、缺 report 的记录全部跳过', () => {
    const plan = planCloudRestores([
      { hall: 'ai_chat', content: JSON.stringify({ key: 'report:x', kind: 'report', report: WIRE }) },
      { hall: 'ai_interpret', content: JSON.stringify({ key: 'news:1', kind: 'news', report: WIRE }) },
      { hall: 'ai_interpret', content: JSON.stringify({ key: 'report:y', kind: 'report' }) },
      { hall: 'ai_interpret', content: '{broken json' },
    ]);
    expect(plan).toHaveLength(0);
  });

  it('skipKeys 以本机优先：同 key 云端记录让位', () => {
    const plan = planCloudRestores([memory()], new Set(['report:cloud-1']));
    expect(plan).toHaveLength(0);
  });

  it('同批次内重复 key 只取首条（多设备重复上行去重）', () => {
    const plan = planCloudRestores([memory(), memory({ id: 'm2' })]);
    expect(plan).toHaveLength(1);
  });

  it('created_at 非法时回落 payload.startedAt', () => {
    const plan = planCloudRestores([{ ...memory(), created_at: 'not-a-date' }]);
    expect(plan[0].startedAt).toBe(5000);
  });
});
