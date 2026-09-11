const TERMINAL_STAGES = new Set(['COMPLETED', 'FAILED', 'CANCELLED']);

const ALLOWED_TRANSITIONS = Object.freeze({
  RECEIVED: new Set(['ROUTING', 'SEARCHING', 'SOURCES_FOUND', 'SYNTHESIZING', 'STREAMING', ...TERMINAL_STAGES]),
  ROUTING: new Set(['SEARCHING', 'SOURCES_FOUND', 'SYNTHESIZING', 'STREAMING', ...TERMINAL_STAGES]),
  SEARCHING: new Set(['SEARCHING', 'SOURCES_FOUND', 'SYNTHESIZING', 'STREAMING', ...TERMINAL_STAGES]),
  SOURCES_FOUND: new Set(['SEARCHING', 'SOURCES_FOUND', 'SYNTHESIZING', 'STREAMING', ...TERMINAL_STAGES]),
  SYNTHESIZING: new Set(['SEARCHING', 'SOURCES_FOUND', 'SYNTHESIZING', 'STREAMING', ...TERMINAL_STAGES]),
  STREAMING: new Set(['STREAMING', ...TERMINAL_STAGES]),
});

function clean(value, maxLength = 500) {
  return String(value || '').replace(/\s+/g, ' ').trim().slice(0, maxLength);
}

export class AnswerProgressTracker {
  constructor(options = {}) {
    this.ttlMs = Math.max(30_000, Number(options.ttlMs) || 5 * 60_000);
    this.states = new Map();
  }

  start(input = {}) {
    this._prune();
    const seq = Number(input.seq);
    if (!Number.isFinite(seq)) throw new Error('progress seq is required');
    const now = Date.now();
    const state = {
      seq,
      requestId: clean(input.requestId, 120),
      question: clean(input.question, 1000),
      target: input.target ?? 'all',
      source: clean(input.source, 120),
      stage: 'RECEIVED',
      message: clean(input.message || '已收到问题'),
      startedAt: now,
      updatedAt: now,
      terminal: false,
    };
    this.states.set(seq, state);
    return { ...state };
  }

  advance(seq, stage, input = {}) {
    const current = this.states.get(Number(seq));
    if (!current || current.terminal) return null;
    const nextStage = clean(stage, 40).toUpperCase();
    if (!nextStage) return null;
    const allowed = ALLOWED_TRANSITIONS[current.stage];
    if (nextStage !== current.stage && !allowed?.has(nextStage)) return null;

    const state = {
      ...current,
      stage: nextStage,
      message: clean(input.message || current.message),
      query: clean(input.query, 1000),
      sourceCount: Number.isFinite(Number(input.sourceCount)) ? Number(input.sourceCount) : undefined,
      answer: clean(input.answer, 4000),
      updatedAt: Date.now(),
      terminal: TERMINAL_STAGES.has(nextStage),
    };
    this.states.set(Number(seq), state);
    if (state.terminal) {
      setTimeout(() => this.states.delete(Number(seq)), Math.min(this.ttlMs, 60_000)).unref?.();
    }
    return { ...state };
  }

  get(seq) {
    const state = this.states.get(Number(seq));
    return state ? { ...state } : null;
  }

  _prune() {
    const cutoff = Date.now() - this.ttlMs;
    for (const [seq, state] of this.states) {
      if (state.updatedAt < cutoff) this.states.delete(seq);
    }
  }
}

export const answerProgressTracker = new AnswerProgressTracker();
export default answerProgressTracker;
