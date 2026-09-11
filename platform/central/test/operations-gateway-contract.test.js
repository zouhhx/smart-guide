import test from 'node:test';
import assert from 'node:assert/strict';
import {
  OPERATIONS_CAPABILITIES,
  normalizeOperationsRequest,
  validateOperationsRequest,
} from '../src/operations/OperationsContract.js';
import { OperationsGateway } from '../src/operations/OperationsGateway.js';
import { MockOperationsAdapter } from '../src/operations/MockOperationsAdapter.js';

test('operations contract exposes only the first stable capability whitelist', () => {
  assert.deepEqual(OPERATIONS_CAPABILITIES, [
    'avatar.speak',
    'tts.speak',
    'screen.command',
    'media.command',
    'monitor.status',
  ]);
  const request = normalizeOperationsRequest({
    requestId: 'request-1',
    eventId: 'event-1',
    capability: 'avatar.speak',
    target: 'screen-2',
    params: { text: '欢迎参观' },
  });
  assert.equal(validateOperationsRequest(request).ok, true);
});

test('gateway rejects undeclared capabilities before an adapter can run', async () => {
  const mock = new MockOperationsAdapter();
  const gateway = new OperationsGateway({ allowedCapabilities: ['monitor.status'] });
  gateway.register('avatar.speak', mock.handler('avatar.speak'));
  const result = await gateway.execute({
    requestId: 'request-2',
    eventId: 'event-2',
    capability: 'avatar.speak',
    params: { text: '你好' },
  });
  assert.equal(result.status, 'REJECTED');
  assert.equal(result.code, 'CAPABILITY_NOT_ALLOWED');
  assert.equal(mock.calls.length, 0);
});

test('gateway executes a whitelisted operation once and replays by eventId', async () => {
  const mock = new MockOperationsAdapter();
  const gateway = new OperationsGateway({ allowedCapabilities: ['tts.speak'] });
  gateway.register('tts.speak', mock.handler('tts.speak'));
  const request = {
    requestId: 'request-3',
    eventId: 'event-3',
    capability: 'tts.speak',
    target: 'all',
    params: { text: '欢迎参观展厅' },
  };
  const first = await gateway.execute(request);
  const replay = await gateway.execute(request);
  assert.equal(first.status, 'SUCCEEDED');
  assert.equal(replay.idempotentReplay, true);
  assert.equal(mock.calls.length, 1);
});

test('gateway rejects malformed commands and normalizes handler failures', async () => {
  const gateway = new OperationsGateway({ allowedCapabilities: ['screen.command'] });
  gateway.register('screen.command', async () => { throw new Error('screen offline'); });
  const malformed = await gateway.execute({
    requestId: 'request-4',
    eventId: 'event-4',
    capability: 'screen.command',
    params: {},
  });
  assert.equal(malformed.code, 'COMMAND_REQUIRED');
  const failed = await gateway.execute({
    requestId: 'request-5',
    eventId: 'event-5',
    capability: 'screen.command',
    params: { command: 'home' },
  });
  assert.equal(failed.status, 'FAILED');
  assert.equal(failed.code, 'HANDLER_FAILED');
});
