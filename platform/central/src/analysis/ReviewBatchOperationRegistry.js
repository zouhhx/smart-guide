import { randomUUID } from 'crypto';

function snapshot(operation) {
  return JSON.parse(JSON.stringify(operation));
}

export class ReviewBatchOperationRegistry {
  constructor({ maxOperations = 50 } = {}) {
    this.maxOperations = maxOperations;
    this.operations = new Map();
    this.activeKeys = new Map();
  }

  start({ fileNames, decision }, runner) {
    const names = [...new Set((fileNames || []).map(name => String(name || '').trim()).filter(Boolean))];
    const action = String(decision || '').trim().toLowerCase();
    const key = `${action}:${[...names].sort().join('|')}`;
    const activeId = this.activeKeys.get(key);
    if (activeId) return this.get(activeId);

    const now = new Date().toISOString();
    const operation = {
      id: randomUUID(),
      status: 'QUEUED',
      phase: 'queued',
      message: '任务已创建，等待开始',
      decision: action,
      fileNames: names,
      current: 0,
      total: names.length,
      percent: 0,
      results: [],
      error: null,
      createdAt: now,
      startedAt: null,
      completedAt: null,
    };
    this.operations.set(operation.id, operation);
    this.activeKeys.set(key, operation.id);
    this._trim();

    queueMicrotask(async () => {
      Object.assign(operation, {
        status: 'RUNNING',
        phase: 'validating',
        message: '正在校验待审核资料',
        startedAt: new Date().toISOString(),
        percent: 3,
      });
      const update = values => Object.assign(operation, values);
      try {
        const results = await runner(update);
        Object.assign(operation, {
          status: 'COMPLETED',
          phase: 'completed',
          message: `已完成 ${names.length} 份资料上架`,
          current: names.length,
          percent: 100,
          results: Array.isArray(results) ? results : [],
          completedAt: new Date().toISOString(),
        });
      } catch (error) {
        Object.assign(operation, {
          status: 'FAILED',
          phase: 'failed',
          message: '批量上架失败',
          error: error instanceof Error ? error.message : String(error),
          completedAt: new Date().toISOString(),
        });
      } finally {
        if (this.activeKeys.get(key) === operation.id) this.activeKeys.delete(key);
      }
    });

    return snapshot(operation);
  }

  get(id) {
    const operation = this.operations.get(String(id || ''));
    return operation ? snapshot(operation) : null;
  }

  _trim() {
    while (this.operations.size > this.maxOperations) {
      const oldest = this.operations.keys().next().value;
      if (!oldest) break;
      this.operations.delete(oldest);
    }
  }
}

export default ReviewBatchOperationRegistry;
