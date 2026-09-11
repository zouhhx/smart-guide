// Per-screen presentation fence and cancellation registry. No global mute state.
export class SpeechInterruption {
  constructor() {
    this.cutoffs = new Map();
    this.active = new Map();
    this.requests = new Map();
  }

  isStale(target, seq) {
    return Number.isFinite(Number(seq)) && Number(seq) <= (this.cutoffs.get(String(target)) ?? -1);
  }

  track(target, controller) {
    const key = String(target || '');
    if (!key) return () => {};
    const entries = this.active.get(key) || new Set();
    entries.add(controller);
    this.active.set(key, entries);
    return () => {
      entries.delete(controller);
      if (!entries.size && this.active.get(key) === entries) this.active.delete(key);
    };
  }

  cancel(target, cutoffSeq) {
    const key = String(target);
    this.cutoffs.set(key, Math.max(cutoffSeq, this.cutoffs.get(key) ?? -1));
    const entries = this.active.get(key);
    let canceledGenerations = 0;
    for (const controller of entries || []) {
      if (!controller.signal.aborted) {
        canceledGenerations++;
        controller.abort();
      }
    }
    return { cutoffSeq: this.cutoffs.get(key), canceledGenerations };
  }

  // Keep the promise as well as the result: concurrent retries perform one stop.
  runOnce(target, requestId, operation) {
    const now = Date.now();
    for (const [key, entry] of this.requests) {
      if (entry.expiresAt < now) this.requests.delete(key);
    }
    const key = `${target}\0${requestId}`;
    const previous = this.requests.get(key);
    if (previous) return previous.promise.then(result => ({ ...result, replayed: true }));
    if (this.requests.size >= 10000) {
      throw Object.assign(new Error('too many interrupt requests'), { statusCode: 429 });
    }
    const promise = Promise.resolve().then(operation);
    this.requests.set(key, { promise, expiresAt: now + 10 * 60 * 1000 });
    return promise.then(result => ({ ...result, replayed: false }));
  }
}

export const speechInterruption = new SpeechInterruption();
