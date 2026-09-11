import {
  buildOperationsResult,
  normalizeOperationsRequest,
  validateOperationsRequest,
} from './OperationsContract.js';

export class OperationsGateway {
  constructor(options = {}) {
    this.allowedCapabilities = new Set(options.allowedCapabilities || []);
    this.handlers = new Map();
    this.completedByEventId = new Map();
    this.inflightByEventId = new Map();
    this.maxIdempotencyEntries = Number(options.maxIdempotencyEntries) || 1000;
    this.idempotencyStore = options.idempotencyStore || null;
  }

  register(capability, handler) {
    if (typeof handler !== 'function') throw new TypeError('operations handler must be a function');
    this.handlers.set(String(capability || ''), handler);
    return this;
  }

  async execute(input, context = {}) {
    const request = normalizeOperationsRequest(input, context.defaults);
    const validation = validateOperationsRequest(request);
    if (!validation.ok) {
      return buildOperationsResult(request, {
        status: 'REJECTED',
        code: validation.errors[0],
        message: validation.errors.join(','),
      });
    }
    if (!this.allowedCapabilities.has(request.capability)) {
      return buildOperationsResult(request, {
        status: 'REJECTED',
        code: 'CAPABILITY_NOT_ALLOWED',
        message: `capability ${request.capability} is not enabled`,
      });
    }
    const persisted = this.idempotencyStore?.get(request.eventId);
    if (persisted) {
      this.completedByEventId.set(request.eventId, persisted);
    }
    if (this.completedByEventId.has(request.eventId)) {
      return {
        ...this.completedByEventId.get(request.eventId),
        idempotentReplay: true,
      };
    }
    if (this.inflightByEventId.has(request.eventId)) {
      return this.inflightByEventId.get(request.eventId);
    }

    const task = this._executeOnce(request, context);
    this.inflightByEventId.set(request.eventId, task);
    try {
      const result = await task;
      this._remember(request.eventId, result);
      return result;
    } finally {
      this.inflightByEventId.delete(request.eventId);
    }
  }

  async _executeOnce(request, context) {
    const handler = this.handlers.get(request.capability);
    if (!handler) {
      return buildOperationsResult(request, {
        status: 'REJECTED',
        code: 'HANDLER_NOT_REGISTERED',
        message: `no handler registered for ${request.capability}`,
      });
    }

    const startedAt = new Date().toISOString();
    let timeout;
    try {
      const handlerPromise = Promise.resolve(handler(request, context));
      const timeoutPromise = new Promise((_, reject) => {
        timeout = setTimeout(() => reject(new Error('OPERATIONS_TIMEOUT')), request.timeoutMs);
        timeout.unref?.();
      });
      const output = await Promise.race([handlerPromise, timeoutPromise]);
      return buildOperationsResult(request, {
        ...output,
        status: output?.status || 'SUCCEEDED',
        code: output?.code || 'OK',
        startedAt,
      });
    } catch (error) {
      const timedOut = error?.message === 'OPERATIONS_TIMEOUT';
      return buildOperationsResult(request, {
        status: timedOut ? 'TIMED_OUT' : 'FAILED',
        code: timedOut ? 'DEADLINE_EXCEEDED' : 'HANDLER_FAILED',
        message: timedOut ? `operation exceeded ${request.timeoutMs}ms` : String(error?.message || error),
        retryable: timedOut,
        startedAt,
      });
    } finally {
      clearTimeout(timeout);
    }
  }

  _remember(eventId, result) {
    this.completedByEventId.set(eventId, result);
    this.idempotencyStore?.set(eventId, result);
    if (this.completedByEventId.size <= this.maxIdempotencyEntries) return;
    const oldest = this.completedByEventId.keys().next().value;
    this.completedByEventId.delete(oldest);
  }
}

export default OperationsGateway;
