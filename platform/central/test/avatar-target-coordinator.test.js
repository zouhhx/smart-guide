import test from 'node:test';
import assert from 'node:assert/strict';
import avatarTargetCoordinator from '../src/avatar/AvatarTargetCoordinator.js';
import { deviceManager } from '../src/devices/DeviceManager.js';
import { wsServer } from '../src/ws/WsServer.js';

test('an online open avatar starts speaking without reopening or waiting', async () => {
  const coordinator = avatarTargetCoordinator;
  const requests = [];
  const delays = [];

  coordinator.selectionRequiresFreshSession = false;
  coordinator.sayPromise = null;
  coordinator.lastSayText = '';
  coordinator.lastSayAt = 0;

  coordinator._request = async (path) => {
    requests.push(path);
    if (path === '/health') {
      return {
        online: true,
        desiredOpen: true,
        session: { connectedAt: new Date().toISOString() },
      };
    }
    if (path === '/api/avatar/say') return { ok: true };
    throw new Error(`unexpected request: ${path}`);
  };
  coordinator._delay = async (milliseconds) => {
    delays.push(milliseconds);
  };

  const result = await coordinator.say('test digital human response');

  assert.equal(result.ok, true);
  assert.deepEqual(requests, ['/health', '/api/avatar/say']);
  assert.deepEqual(delays, []);
});

test('an offline avatar session reactivates the selected online display', async () => {
  const coordinator = avatarTargetCoordinator;
  const originalRequest = coordinator._request;
  const originalGetByScreenNumber = deviceManager.getByScreenNumber;
  const originalSendToDevice = wsServer.sendToDevice;
  const originalState = {
    selectedTarget: coordinator.selectedTarget,
    activeScreenNumber: coordinator.activeScreenNumber,
    selectionChangedAt: coordinator.selectionChangedAt,
    selectionRequiresFreshSession: coordinator.selectionRequiresFreshSession,
    lastRecoveryAttemptAt: coordinator.lastRecoveryAttemptAt,
  };
  const commands = [];

  try {
    coordinator.selectedTarget = '2';
    coordinator.activeScreenNumber = 2;
    coordinator.selectionRequiresFreshSession = false;
    coordinator.lastRecoveryAttemptAt = 0;
    coordinator._request = async path => {
      assert.equal(path, '/health');
      return { online: false, transportOnline: false };
    };
    deviceManager.getByScreenNumber = screenNumber => ({
      deviceId: 'screen-2-device',
      screenNumber,
      online: true,
    });
    wsServer.sendToDevice = (deviceId, message) => {
      commands.push({ deviceId, message });
      return true;
    };

    const result = await coordinator._recoverSelectedTarget({ force: true });

    assert.equal(result.recovered, true);
    assert.equal(result.screenNumber, 2);
    assert.equal(coordinator.selectionRequiresFreshSession, true);
    assert.equal(commands.length, 1);
    assert.equal(commands[0].deviceId, 'screen-2-device');
    assert.equal(commands[0].message.action, 'avatar_activate');
    assert.equal(commands[0].message.params.screenNumber, '2');
  } finally {
    coordinator._request = originalRequest;
    deviceManager.getByScreenNumber = originalGetByScreenNumber;
    wsServer.sendToDevice = originalSendToDevice;
    Object.assign(coordinator, originalState, { recoveryInFlight: false });
  }
});

test('the recovery monitor leaves an online avatar session untouched', async () => {
  const coordinator = avatarTargetCoordinator;
  const originalRequest = coordinator._request;
  const originalGetByScreenNumber = deviceManager.getByScreenNumber;
  const originalSendToDevice = wsServer.sendToDevice;
  const originalTarget = coordinator.selectedTarget;
  let commandCount = 0;

  try {
    coordinator.selectedTarget = '2';
    coordinator.recoveryInFlight = false;
    coordinator._request = async () => ({ online: true, transportOnline: true });
    deviceManager.getByScreenNumber = () => ({ deviceId: 'screen-2-device', online: true });
    wsServer.sendToDevice = () => {
      commandCount += 1;
      return true;
    };

    const result = await coordinator._recoverSelectedTarget({ force: true });

    assert.equal(result.skipped, true);
    assert.equal(result.reason, 'avatar client is online');
    assert.equal(commandCount, 0);
  } finally {
    coordinator.selectedTarget = originalTarget;
    coordinator.recoveryInFlight = false;
    coordinator._request = originalRequest;
    deviceManager.getByScreenNumber = originalGetByScreenNumber;
    wsServer.sendToDevice = originalSendToDevice;
  }
});
