import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { LegacyOperationsAdapter } from '../src/operations/LegacyOperationsAdapter.js';
import { OperationsGateway } from '../src/operations/OperationsGateway.js';
import { OperationsHttpApi } from '../src/operations/OperationsHttpApi.js';
import { OperationsIdempotencyStore } from '../src/operations/OperationsIdempotencyStore.js';

function createDependencies() {
  const commands = [];
  const devices = {
    getSummary: () => ({ total: 1, online: 1, screens: [{ screenNumber: 2, online: true }] }),
    getByScreenNumber: number => Number(number) === 2 ? { id: 'screen-2', online: true } : null,
  };
  return {
    commands,
    dependencies: {
      deviceManager: devices,
      wsServer: {
        pushToClient: command => { commands.push({ target: 'all', command }); return 1; },
        sendToDevice: (id, command) => { commands.push({ target: id, command }); return true; },
      },
      avatarTargetCoordinator: {
        getTargetStatus: () => ({ ok: true, target: '2', screenNumber: 2, online: true }),
        say: async text => ({ ok: true, screenNumber: 2, text }),
      },
      ttsService: {
        synthesize: async () => ({ url: 'http://example.test/audio.mp3', voice: 'xiaoyan', provider: 'iflytek' }),
      },
      ttsConfigStore: {
        get: () => ({ provider: 'iflytek', voice: 'xiaoyan', avatarEnabled: false }),
      },
      mediaLibrary: {
        getAll: () => [{ id: 'ppt-1', name: 'demo.pptx', type: 'ppt', localPath: 'D:\\media\\demo.pptx' }],
      },
    },
  };
}

function request(capability, overrides = {}) {
  return {
    requestId: `request-${capability.replace('.', '-')}`,
    eventId: `event-${capability.replace('.', '-')}`,
    capability,
    target: '2',
    params: {},
    actor: { id: 'new-rag', type: 'rag-system' },
    ...overrides,
  };
}

test('legacy adapter keeps the manually selected avatar target authoritative', async () => {
  const { dependencies } = createDependencies();
  const adapter = new LegacyOperationsAdapter(dependencies);
  const rejected = await adapter.handler('avatar.speak')(request('avatar.speak', {
    target: '3', params: { text: '欢迎参观' },
  }));
  assert.equal(rejected.code, 'AVATAR_TARGET_NOT_SELECTED');
  const accepted = await adapter.handler('avatar.speak')(request('avatar.speak', {
    params: { text: '欢迎参观' },
  }));
  assert.equal(accepted.data.screenNumber, 2);
});

test('tts and screen commands use existing display protocol with command whitelist', async () => {
  const { dependencies, commands } = createDependencies();
  const adapter = new LegacyOperationsAdapter(dependencies);
  const tts = await adapter.handler('tts.speak')(request('tts.speak', {
    params: { text: '欢迎参观', delivery: 'screen' },
  }));
  assert.equal(tts.status, undefined);
  assert.equal(tts.data.delivered, 1);
  assert.equal(commands[0].command.action, 'speak');
  assert.equal(commands[0].command.eventId, 'event-tts-speak');

  const rejected = await adapter.handler('screen.command')(request('screen.command', {
    params: { command: 'format_disk' },
  }));
  assert.equal(rejected.code, 'COMMAND_NOT_ALLOWED');
});

test('media commands only open managed files with the matching media action', async () => {
  const { dependencies, commands } = createDependencies();
  const adapter = new LegacyOperationsAdapter(dependencies);
  const missing = await adapter.handler('media.command')(request('media.command', {
    params: { command: 'play_ppt', file: 'outside.pptx' },
  }));
  assert.equal(missing.code, 'MEDIA_NOT_FOUND');
  const mismatch = await adapter.handler('media.command')(request('media.command', {
    params: { command: 'play_video', fileId: 'ppt-1' },
  }));
  assert.equal(mismatch.code, 'MEDIA_COMMAND_MISMATCH');
  const accepted = await adapter.handler('media.command')(request('media.command', {
    params: { command: 'play_ppt', fileId: 'ppt-1' },
  }));
  assert.equal(accepted.data.delivered, 1);
  assert.equal(commands.at(-1).command.params.file, 'D:\\media\\demo.pptx');
});

test('idempotency results survive a gateway restart', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'operations-idempotency-'));
  const filePath = path.join(dir, 'events.json');
  let calls = 0;
  const firstStore = new OperationsIdempotencyStore({ filePath });
  const first = new OperationsGateway({ allowedCapabilities: ['monitor.status'], idempotencyStore: firstStore });
  first.register('monitor.status', async () => { calls += 1; return { data: { online: true } }; });
  const input = request('monitor.status', { target: 'all' });
  assert.equal((await first.execute(input)).status, 'SUCCEEDED');

  const secondStore = new OperationsIdempotencyStore({ filePath });
  const second = new OperationsGateway({ allowedCapabilities: ['monitor.status'], idempotencyStore: secondStore });
  second.register('monitor.status', async () => { calls += 1; return {}; });
  const replay = await second.execute(input);
  assert.equal(replay.idempotentReplay, true);
  assert.equal(calls, 1);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('HTTP facade requires token and caller whitelist and overrides actor identity', async () => {
  let executed = null;
  const settings = {
    enabled: true,
    token: 'test-token',
    allowedCallers: ['new-rag'],
  };
  const api = new OperationsHttpApi({
    settings,
    service: {
      describe: () => ({ ok: true, enabled: true }),
      execute: async (input, callerId) => {
        executed = { input, callerId };
        return { status: 'SUCCEEDED', code: 'OK' };
      },
    },
  });
  const server = http.createServer((req, res) => api.handle(req, res));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const unauthorized = await fetch(`${base}/api/operations/v1/health`);
    assert.equal(unauthorized.status, 401);
    const forbidden = await fetch(`${base}/api/operations/v1/health`, {
      headers: { Authorization: 'Bearer test-token', 'X-Operations-Caller': 'unknown' },
    });
    assert.equal(forbidden.status, 403);
    const accepted = await fetch(`${base}/api/operations/v1/execute`, {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-token',
        'X-Operations-Caller': 'new-rag',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ actor: { id: 'spoofed' }, requestId: 'r-1', eventId: 'e-1' }),
    });
    assert.equal(accepted.status, 200);
    assert.equal(executed.callerId, 'new-rag');
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
});
