/**
 * Normalize the persisted AI conversation list.
 *
 * A retry can create a second record for the same prompt before the first
 * request finishes.  Keep one record per normalized prompt/mode (and file
 * when a file is attached), retain the newest completed result, and merge
 * distinct turns so a real follow-up is never lost.
 */

export type AiHistoryMode = string;

export interface AiHistoryTurn {
  id: string;
  question: string;
  answer: string;
  mode: AiHistoryMode;
  updatedAt: number;
}

export interface AiHistoryRecord {
  id: string;
  title: string;
  question: string;
  answer: string;
  mode: AiHistoryMode;
  updatedAt: number;
  attachmentName?: string;
  turns?: AiHistoryTurn[];
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

/** Stable comparison form for user-entered prompts and generated answers. */
export function normalizeAiHistoryText(value: unknown): string {
  return text(value).replace(/\s+/g, ' ').toLocaleLowerCase();
}

function recordTurns(record: AiHistoryRecord): AiHistoryTurn[] {
  const turns = Array.isArray(record.turns)
    ? record.turns.filter(turn => turn && text(turn.question))
    : [];
  if (turns.length || !text(record.answer)) return turns;
  return [{
    id: `${record.id}-legacy`,
    question: record.question,
    answer: record.answer,
    mode: record.mode,
    updatedAt: record.updatedAt,
  }];
}

function hasCompletedAnswer(record: AiHistoryRecord): boolean {
  return Boolean(text(record.answer) || recordTurns(record).some(turn => text(turn.answer)));
}

function historyKey(record: AiHistoryRecord): string {
  // An uploaded document changes the meaning of an otherwise identical
  // question, so partition those records by attachment as well.
  return [
    normalizeAiHistoryText(record.question),
    normalizeAiHistoryText(record.mode),
    normalizeAiHistoryText(record.attachmentName),
  ].join('\u001f');
}

function turnKey(turn: AiHistoryTurn): string {
  return [
    normalizeAiHistoryText(turn.question),
    normalizeAiHistoryText(turn.mode),
    normalizeAiHistoryText(turn.answer) || '<pending>',
  ].join('\u001f');
}

function timestamp(value: unknown): number {
  return Number.isFinite(Number(value)) ? Number(value) : 0;
}

/**
 * De-duplicate and repair persisted history. The returned records are new
 * objects, making this safe to call from React state updates.
 */
export function normalizeAiConversationHistory<T extends AiHistoryRecord>(
  input: readonly T[],
  limit = 30,
): T[] {
  const groups = new Map<string, T[]>();
  for (const candidate of input || []) {
    if (!candidate || !text(candidate.id) || !text(candidate.question)) continue;
    const record = { ...candidate, question: text(candidate.question), answer: text(candidate.answer) } as T;
    const key = historyKey(record);
    const group = groups.get(key);
    if (group) group.push(record);
    else groups.set(key, [record]);
  }

  const normalized: T[] = [];
  for (const group of Array.from(groups.values())) {
    const ordered = [...group].sort((a, b) => timestamp(b.updatedAt) - timestamp(a.updatedAt));
    // A failed/pending retry must not replace a valid answer that already
    // exists for the same prompt. If every attempt failed, keep the latest
    // pending attempt so the user can see what needs retrying.
    const winner = ordered.find(hasCompletedAnswer) || ordered[0];
    if (!winner) continue;

    const turnsByKey = new Map<string, AiHistoryTurn>();
    for (const record of group) {
      for (const turn of recordTurns(record)) {
        const cleanTurn = { ...turn, question: text(turn.question), answer: text(turn.answer) };
        const key = turnKey(cleanTurn);
        const previous = turnsByKey.get(key);
        if (!previous || timestamp(cleanTurn.updatedAt) >= timestamp(previous.updatedAt)) {
          turnsByKey.set(key, cleanTurn);
        }
      }
    }
    const turns = Array.from(turnsByKey.values())
      .sort((a, b) => timestamp(a.updatedAt) - timestamp(b.updatedAt))
      .slice(-20);
    const latestAnswerTurn = [...turns].reverse().find(turn => text(turn.answer));
    const answer = text(winner.answer) || text(latestAnswerTurn?.answer);
    const record = {
      ...winner,
      answer,
      updatedAt: timestamp(winner.updatedAt),
      turns,
    } as T;
    normalized.push(record);
  }

  return normalized
    .sort((a, b) => timestamp(b.updatedAt) - timestamp(a.updatedAt))
    .slice(0, Math.max(0, limit));
}
