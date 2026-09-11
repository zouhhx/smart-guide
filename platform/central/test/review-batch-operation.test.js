import assert from 'node:assert/strict';
import test from 'node:test';
import { ReviewBatchOperationRegistry } from '../src/analysis/ReviewBatchOperationRegistry.js';

async function waitForTerminal(registry, id) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const operation = registry.get(id);
    if (operation?.status === 'COMPLETED' || operation?.status === 'FAILED') return operation;
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  throw new Error('operation did not finish');
}

test('batch review exposes live progress and a completed result', async () => {
  const registry = new ReviewBatchOperationRegistry();
  const phases = [];
  const created = registry.start({ fileNames: ['a.pptx', 'b.pptx'], decision: 'approve' }, async update => {
    update({ phase: 'publishing', current: 1, percent: 38, message: '正在发布 1/2' });
    phases.push(registry.get(created.id).phase);
    update({ phase: 'vector_index', current: 2, percent: 90, message: '正在重建向量索引' });
    phases.push(registry.get(created.id).phase);
    return [{ name: 'a.pptx' }, { name: 'b.pptx' }];
  });

  assert.equal(created.status, 'QUEUED');
  const completed = await waitForTerminal(registry, created.id);
  assert.deepEqual(phases, ['publishing', 'vector_index']);
  assert.equal(completed.status, 'COMPLETED');
  assert.equal(completed.percent, 100);
  assert.equal(completed.current, 2);
  assert.equal(completed.results.length, 2);
});

test('identical running batches reuse one operation', async () => {
  const registry = new ReviewBatchOperationRegistry();
  let release;
  const waiting = new Promise(resolve => { release = resolve; });
  const first = registry.start({ fileNames: ['b.pptx', 'a.pptx'], decision: 'approve' }, async () => {
    await waiting;
    return [];
  });
  const second = registry.start({ fileNames: ['a.pptx', 'b.pptx'], decision: 'approve' }, async () => []);
  assert.equal(second.id, first.id);
  release();
  await waitForTerminal(registry, first.id);
});

test('batch review records failures for polling clients', async () => {
  const registry = new ReviewBatchOperationRegistry();
  const created = registry.start({ fileNames: ['missing.pptx'], decision: 'approve' }, async () => {
    throw new Error('待审核资料不存在');
  });
  const failed = await waitForTerminal(registry, created.id);
  assert.equal(failed.status, 'FAILED');
  assert.equal(failed.phase, 'failed');
  assert.match(failed.error, /待审核资料不存在/);
});
