import assert from 'node:assert/strict';
import test from 'node:test';
import { ReviewOperationRegistry } from '../src/analysis/ReviewOperationRegistry.js';

test('concurrent identical review requests share one operation', async () => {
  const registry = new ReviewOperationRegistry();
  let executions = 0;
  const execute = async () => {
    executions += 1;
    await new Promise(resolve => setTimeout(resolve, 20));
    return { name: 'demo.pptx', decision: 'approve' };
  };

  const first = registry.run('approve:demo.pptx', execute);
  const second = registry.run('approve:demo.pptx', execute);
  const [firstResult, secondResult] = await Promise.all([first, second]);

  assert.equal(executions, 1);
  assert.deepEqual(firstResult, { name: 'demo.pptx', decision: 'approve' });
  assert.deepEqual(secondResult, firstResult);
  assert.equal(registry.operations.size, 0);
});

test('different review decisions are not coalesced', async () => {
  const registry = new ReviewOperationRegistry();
  let executions = 0;
  const execute = async decision => {
    executions += 1;
    return { name: 'demo.pptx', decision };
  };

  await Promise.all([
    registry.run('approve:demo.pptx', () => execute('approve')),
    registry.run('reject:demo.pptx', () => execute('reject')),
  ]);

  assert.equal(executions, 2);
});
