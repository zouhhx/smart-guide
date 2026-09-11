import assert from 'node:assert/strict';
import test from 'node:test';
import { AvatarServiceController } from '../src/avatar/AvatarServiceController.js';

function response(body, ok = true, status = 200) {
  return { ok, status, json: async () => body };
}

test('status distinguishes the bridge process from its client session', async () => {
  const controller = new AvatarServiceController({
    fetch: async () => response({ ok: true, online: false }),
    platform: 'win32',
  });
  const status = await controller.status();
  assert.equal(status.running, true);
  assert.equal(status.clientOnline, false);
});

test('start temporarily enables the disabled scheduled task', async () => {
  const calls = [];
  const controller = new AvatarServiceController({
    platform: 'win32',
    execFile(file, args, options, callback) {
      calls.push({ file, args, options });
      callback(null, '', '');
    },
    fetch: async () => response({ ok: true, online: true }),
  });
  const result = await controller.control('start');
  assert.equal(result.running, true);
  assert.equal(calls[0].file, 'powershell.exe');
  assert.match(calls[0].args.at(-1), /Enable-ScheduledTask/);
  assert.match(calls[0].args.at(-1), /Disable-ScheduledTask/);
});

test('stop ends only the avatar control task', async () => {
  const calls = [];
  let requested = false;
  const controller = new AvatarServiceController({
    platform: 'win32',
    execFile(file, args, options, callback) {
      calls.push({ file, args, options });
      requested = true;
      callback(null, '', '');
    },
    fetch: async () => {
      if (requested) throw new Error('connection refused');
      return response({ ok: true, online: false });
    },
  });
  const result = await controller.control('stop');
  assert.equal(result.running, false);
  assert.deepEqual(calls[0].args, ['/End', '/TN', 'ExhibitionMofaAvatarService']);
});
