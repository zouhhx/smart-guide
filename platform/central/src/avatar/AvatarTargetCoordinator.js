import { config } from '../config/index.js';
import { deviceManager } from '../devices/DeviceManager.js';
import { wsServer } from '../ws/WsServer.js';
import { execFile } from 'node:child_process';
import fs from 'node:fs';

const RETRY_INTERVAL_MS = 1000;
const RECOVERY_CHECK_INTERVAL_MS = 5000;
const RECOVERY_RETRY_INTERVAL_MS = 15000;
const CENTRAL_AVATAR_TASK = 'ExhibitionMofaAvatarClient';
const TARGET_STATE_URL = new URL('../../data/avatar-target.json', import.meta.url);
const TARGET_STATE_TEMP_URL = new URL('../../data/avatar-target.json.tmp', import.meta.url);

class AvatarTargetCoordinator {
  constructor() {
    const saved = this._loadTargetState();
    this.selectedTarget = saved.target;
    this.activeScreenNumber = this._isCentralTarget(saved.target) ? null : this._screenNumber(saved.target);
    this.selectionChangedAt = saved.changedAt;
    this.selectionRequiresFreshSession = false;
    this.sayPromise = null;
    this.lastSayText = '';
    this.lastSayAt = 0;
    this.recoveryInFlight = false;
    this.lastRecoveryAttemptAt = 0;
    this.recoveryTimer = null;
    if (this._isCentralTarget(this.selectedTarget)) {
      this._setCentralAvatarActive(true);
    } else {
      this._setCentralAvatarActive(false);
      const restoreTimer = setInterval(() => {
        const selected = deviceManager.getByScreenNumber(this.activeScreenNumber);
        if (!selected?.online) return;
        clearInterval(restoreTimer);
        this.selectTarget(this.selectedTarget);
      }, 2000);
      restoreTimer.unref();
    }
    this._startRecoveryMonitor();
  }

  prepare() {
    return this.getTargetStatus();
  }

  selectTarget(target) {
    if (this._isCentralTarget(target)) {
      const switched = this.selectedTarget !== '61' || this.activeScreenNumber !== null;
      const deactivatedCount = this.deactivateAll();
      this._setCentralAvatarActive(true);
      this.selectedTarget = '61';
      this.selectionChangedAt = Date.now();
      this.selectionRequiresFreshSession = switched;
      this._saveTargetState();
      console.log(`[AvatarTarget] manually selected 61 central avatar; deactivated displays: ${deactivatedCount}`);
      return { ...this.getTargetStatus(), switched, deactivatedCount };
    }

    const screenNumber = this._screenNumber(target);
    if (!screenNumber) {
      return { ok: false, reason: 'avatar target must be 61 or a screen number' };
    }

    const selected = deviceManager.getByScreenNumber(screenNumber);
    if (!selected?.online) {
      return { ok: false, skipped: true, reason: `${screenNumber}号屏不在线`, screenNumber };
    }

    const previousScreenNumber = this.activeScreenNumber;
    const switched = previousScreenNumber !== screenNumber;
    this._setCentralAvatarActive(false);
    for (const screen of deviceManager.getOnlineScreens()) {
      const isSelected = Number(screen.screenNumber) === screenNumber;
      wsServer.sendToDevice(screen.deviceId, {
        type: 'command',
        action: isSelected ? 'avatar_activate' : 'avatar_deactivate',
        params: { screenNumber: String(screenNumber) },
        serverTime: Date.now(),
      });
    }
    this.activeScreenNumber = screenNumber;
    this.selectedTarget = String(screenNumber);
    this.selectionChangedAt = Date.now();
    this.selectionRequiresFreshSession = switched;
    this._saveTargetState();
    console.log(`[AvatarTarget] ${switched ? 'switched' : 'confirmed'} active screen: ${screenNumber}`);
    return { ...this.getTargetStatus(), switched, previousScreenNumber };
  }

  getTargetStatus() {
    const central = this.selectedTarget === '61';
    const screenNumber = central ? null : this._screenNumber(this.selectedTarget);
    const selected = screenNumber ? deviceManager.getByScreenNumber(screenNumber) : null;
    return {
      ok: true,
      target: this.selectedTarget,
      central,
      screenNumber,
      label: central ? '61主机' : `${screenNumber}号屏`,
      online: central ? true : Boolean(selected?.online),
      changedAt: this.selectionChangedAt,
    };
  }

  async say(text) {
    const value = String(text || '').trim();
    if (!value) throw new Error('digital-human text is required');

    const now = Date.now();
    if (value === this.lastSayText && now - this.lastSayAt < 5000) {
      return { ...this.getTargetStatus(), deduplicated: true };
    }
    if (this.sayPromise) {
      await this.sayPromise;
      return this.say(value);
    }

    this.lastSayText = value;
    this.lastSayAt = now;
    this.sayPromise = this._sayToSelectedTarget(value)
      .catch((error) => {
        this.lastSayText = '';
        this.lastSayAt = 0;
        throw error;
      })
      .finally(() => {
        this.sayPromise = null;
      });
    return this.sayPromise;
  }

  async _sayToSelectedTarget(text) {
    const selected = this.getTargetStatus();
    if (!selected.central && !selected.online) {
      throw new Error(`${selected.screenNumber}号屏不在线`);
    }

    const timeoutMs = Math.max(8000, Number(config.TTS.avatarTimeoutMs) || 30000);
    const deadline = Date.now() + timeoutMs;
    let lastError = null;

    let status = null;
    while (Date.now() < deadline) {
      try {
        status = await this._request('/health', null, 'GET');
        const connectedAt = Date.parse(status?.session?.connectedAt || '');
        const isCurrentSession = status?.online
          && (!this.selectionRequiresFreshSession || connectedAt >= this.selectionChangedAt - 2000);
        if (isCurrentSession) break;
      } catch (err) {
        lastError = err;
      }
      await this._delay(RETRY_INTERVAL_MS);
    }

    if (!status?.online) {
      throw new Error(`等待${selected.label}数字人上线超时: ${lastError?.message || 'no session'}`);
    }

    if (this.selectionRequiresFreshSession || !status.desiredOpen) {
      const opened = await this._request('/api/avatar/open', {});
      if (!opened?.ok) throw new Error(opened?.error || 'digital human open failed');
      await this._delay(4500);
    }
    this.selectionRequiresFreshSession = false;

    while (Date.now() < deadline) {
      try {
        const result = await this._request('/api/avatar/say', { text });
        if (result.ok === true) {
          return { ...result, ...this.getTargetStatus() };
        }
        lastError = new Error(result.error || 'digital human say failed');
      } catch (err) {
        lastError = err;
      }

      const remaining = deadline - Date.now();
      if (remaining <= 0) break;
      await this._delay(Math.min(RETRY_INTERVAL_MS, remaining));
    }

    throw new Error(`等待${selected.label}数字人上线超时: ${lastError?.message || 'no session'}`);
  }

  deactivateAll() {
    let count = 0;
    for (const screen of deviceManager.getOnlineScreens()) {
      if (wsServer.sendToDevice(screen.deviceId, {
        type: 'command',
        action: 'avatar_deactivate',
        params: {},
        serverTime: Date.now(),
      })) count += 1;
    }
    this.activeScreenNumber = null;
    return count;
  }

  _startRecoveryMonitor() {
    if (this.recoveryTimer) return;
    this.recoveryTimer = setInterval(() => {
      this._recoverSelectedTarget().catch(error => {
        console.warn(`[AvatarTarget] recovery check failed: ${error.message}`);
      });
    }, RECOVERY_CHECK_INTERVAL_MS);
    this.recoveryTimer.unref();
  }

  async _recoverSelectedTarget({ force = false } = {}) {
    if (this.recoveryInFlight || this._isCentralTarget(this.selectedTarget)) {
      return { ok: true, skipped: true, reason: 'recovery not required' };
    }

    const screenNumber = this._screenNumber(this.selectedTarget);
    const selected = deviceManager.getByScreenNumber(screenNumber);
    if (!selected?.online) {
      return { ok: true, skipped: true, reason: 'selected display is offline' };
    }

    this.recoveryInFlight = true;
    try {
      const health = await this._request('/health', null, 'GET');
      if (health?.online && health?.transportOnline !== false) {
        return { ok: true, skipped: true, reason: 'avatar client is online' };
      }

      const now = Date.now();
      if (!force && now - this.lastRecoveryAttemptAt < RECOVERY_RETRY_INTERVAL_MS) {
        return { ok: true, skipped: true, reason: 'recovery cooldown' };
      }
      this.lastRecoveryAttemptAt = now;

      const sent = wsServer.sendToDevice(selected.deviceId, {
        type: 'command',
        action: 'avatar_activate',
        params: { screenNumber: String(screenNumber) },
        serverTime: now,
      });
      if (!sent) return { ok: false, reason: 'display command was not delivered' };

      this.activeScreenNumber = screenNumber;
      this.selectionChangedAt = now;
      this.selectionRequiresFreshSession = true;
      console.warn(`[AvatarTarget] avatar client offline; reactivated screen ${screenNumber}`);
      return { ok: true, recovered: true, screenNumber };
    } finally {
      this.recoveryInFlight = false;
    }
  }

  _isCentralTarget(target) {
    const value = String(target ?? '').trim().toLowerCase();
    return value === '61' || value === 'central' || value === 'server';
  }

  _screenNumber(target) {
    const value = Number.parseInt(target, 10);
    return Number.isFinite(value) && value > 0 ? value : null;
  }

  _loadTargetState() {
    try {
      const saved = JSON.parse(fs.readFileSync(TARGET_STATE_URL, 'utf8'));
      const target = this._isCentralTarget(saved?.target) ? '61' : String(this._screenNumber(saved?.target) || '61');
      return { target, changedAt: Number(saved?.changedAt) || Date.now() };
    } catch {
      return { target: '61', changedAt: Date.now() };
    }
  }

  _saveTargetState() {
    const payload = { target: this.selectedTarget, changedAt: this.selectionChangedAt };
    fs.writeFileSync(TARGET_STATE_TEMP_URL, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
    fs.renameSync(TARGET_STATE_TEMP_URL, TARGET_STATE_URL);
  }

  _delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  _setCentralAvatarActive(active) {
    if (process.platform !== 'win32' || config.TTS.avatarTaskControlEnabled === false) return;
    const args = [active ? '/Run' : '/End', '/TN', CENTRAL_AVATAR_TASK];
    execFile('schtasks.exe', args, { windowsHide: true }, (error) => {
      if (error && !(error.code === 1 && !active)) {
        console.warn(`[AvatarTarget] failed to ${active ? 'start' : 'stop'} central avatar task: ${error.message}`);
      }
    });
  }

  async _request(path, body = {}, method = 'POST') {
    const options = { method, headers: {} };
    if (method !== 'GET') {
      options.headers['Content-Type'] = 'application/json; charset=utf-8';
      options.body = JSON.stringify(body);
    }
    const response = await fetch(`${config.TTS.avatarServiceUrl}${path}`, options);
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `digital human HTTP ${response.status}`);
    return result;
  }
}

export const avatarTargetCoordinator = new AvatarTargetCoordinator();
export default avatarTargetCoordinator;
