import { v4 as uuidv4 } from 'uuid';
import fs from 'fs';
import path from 'path';
import { config, HEARTBEAT, DEVICE_TYPES } from '../config/index.js';

const DEFAULT_SCREEN_SETTINGS = {
  name: '',
  snapshotIntervalSeconds: 5,
  statusIntervalSeconds: 1,
  transparentBackground: false,
  backgroundFit: 'cover',
  backgroundImageUrl: '',
  backgroundImageName: '',
  avatarBounds: { width: 960, height: 1080, x: 2880, y: 0 },
};

class DeviceManager {
  constructor() {
    this.devices = new Map();
    this._screenCounter = 0;
    this.storePath = path.join(config.DATA_DIR, 'screen-devices.json');
    this._loadRegisteredScreens();

    this._cleanupTimer = setInterval(() => this._cleanup(), HEARTBEAT.cleanupInterval);
    this._cleanupTimer.unref?.();
    this._log('info', 'DeviceManager initialized');
  }

  register(data) {
    const { type, name, os, version, deviceId, screenNumber, ipAddress } = data;

    if (!Object.values(DEVICE_TYPES).includes(type)) {
      throw new Error(`Unknown device type: ${type}`);
    }

    if (type === DEVICE_TYPES.CLIENT && deviceId) {
      const existing = this.devices.get(deviceId);
      if (existing && existing.type === DEVICE_TYPES.CLIENT) {
        existing.online = true;
        existing.lastHeartbeat = Date.now();
        existing.os = os || existing.os;
        existing.version = version || existing.version;
        existing.ipAddress = ipAddress || existing.ipAddress || '';
        existing.screenNumber = this._resolveScreenNumber(deviceId, screenNumber, existing.screenNumber);
        existing.baseName = name || existing.baseName || 'Display';
        existing.name = this._displayName(existing);
        existing.lastSeenAt = Date.now();
        this._saveRegisteredScreens();
        this._log('info', `Device reconnected: ${existing.name} (${existing.id})`);
        return existing;
      }

      if (screenNumber) {
        const byScreen = this.getByScreenNumber(screenNumber);
        if (byScreen) {
          byScreen.online = true;
          byScreen.lastHeartbeat = Date.now();
          byScreen.lastSeenAt = Date.now();
          byScreen.os = os || byScreen.os;
          byScreen.version = version || byScreen.version;
          byScreen.ipAddress = ipAddress || byScreen.ipAddress || '';
          byScreen.baseName = name || byScreen.baseName || 'Display';
          byScreen.name = this._displayName(byScreen);
          this._saveRegisteredScreens();
          this._log('info', `Device reconnected by screen number: ${byScreen.name} (${byScreen.id})`);
          return byScreen;
        }
      }
    }

    const device = {
      id: deviceId || uuidv4(),
      type,
      name: name || `${this._typeLabel(type)}-${this.devices.size + 1}`,
      os: os || 'unknown',
      version: version || '1.0.0',
      online: true,
      registeredAt: Date.now(),
      lastSeenAt: Date.now(),
      lastHeartbeat: Date.now(),
      ipAddress: ipAddress || '',
    };

    if (type === DEVICE_TYPES.CLIENT) {
      device.baseName = name || 'Display';
      device.settings = this._normalizeScreenSettings({});
      device.screenNumber = this._resolveScreenNumber(device.id, screenNumber);
      device.name = this._displayName(device);
    }

    this.devices.set(device.id, device);
    this._saveRegisteredScreens();
    this._log('info', `Device registered: ${device.name} (${device.id}) [${type}]`);
    return device;
  }

  heartbeat(deviceId) {
    const device = this.devices.get(deviceId);
    if (!device) {
      this._log('warn', `Heartbeat from unknown device: ${deviceId}`);
      return null;
    }
    device.lastHeartbeat = Date.now();
    device.online = true;
    return device;
  }

  getAll() {
    return Array.from(this.devices.values());
  }

  getByType(type) {
    return this.getAll().filter(d => d.type === type);
  }

  getOnline() {
    return this.getAll().filter(d => d.online);
  }

  getOnlineByType(type) {
    return this.getOnline().filter(d => d.type === type);
  }

  getByScreenNumber(num) {
    return this.getAll().find(d => d.type === DEVICE_TYPES.CLIENT && d.screenNumber === Number(num));
  }

  getOnlineScreens() {
    return this.getOnlineByType(DEVICE_TYPES.CLIENT)
      .map(d => this._screenView(d))
      .sort((a, b) => (Number(a.screenNumber) || 9999) - (Number(b.screenNumber) || 9999));
  }

  getScreens() {
    return this.getByType(DEVICE_TYPES.CLIENT)
      .map(d => this._screenView(d))
      .sort((a, b) => (Number(a.screenNumber) || 9999) - (Number(b.screenNumber) || 9999));
  }

  updateSnapshot(deviceId, snapshot) {
    const device = this.devices.get(deviceId);
    if (!device) return null;
    device.snapshot = snapshot;
    device.lastHeartbeat = Date.now();
    device.lastSeenAt = Date.now();
    device.online = true;
    this._saveRegisteredScreens();
    return device;
  }

  updateStatus(deviceId, status) {
    const device = this.devices.get(deviceId);
    if (!device) return null;
    device.status = {
      ...(device.status || {}),
      ...(status || {}),
      updatedAt: Date.now(),
    };
    device.lastHeartbeat = Date.now();
    device.lastSeenAt = Date.now();
    device.online = true;
    this._saveRegisteredScreens();
    return device;
  }

  updateScreenSettings(deviceId, input = {}) {
    const device = this.devices.get(deviceId);
    if (!device || device.type !== DEVICE_TYPES.CLIENT) {
      throw new Error('screen not found');
    }

    device.settings = this._normalizeScreenSettings({
      ...(device.settings || {}),
      ...input,
    });
    device.name = this._displayName(device);
    device.updatedAt = Date.now();
    this._saveRegisteredScreens();
    return this._screenView(device);
  }

  getScreenSettings(deviceId) {
    const device = this.devices.get(deviceId);
    if (!device || device.type !== DEVICE_TYPES.CLIENT) return null;
    return this._normalizeScreenSettings(device.settings || {});
  }

  get(deviceId) {
    return this.devices.get(deviceId);
  }

  offline(deviceId) {
    const device = this.devices.get(deviceId);
    if (device) {
      device.online = false;
      this._log('info', `Device offline: ${device.name} (${deviceId})`);
    }
  }

  online(deviceId) {
    const device = this.devices.get(deviceId);
    if (device) {
      device.online = true;
      this._log('info', `Device online: ${device.name} (${deviceId})`);
    }
  }

  remove(deviceId) {
    const device = this.devices.get(deviceId);
    if (device) {
      this.devices.delete(deviceId);
      this._log('info', `Device removed: ${device.name} (${deviceId})`);
    }
  }

  getSummary() {
    const all = this.getAll();
    const online = all.filter(d => d.online);
    return {
      total: all.length,
      online: online.length,
      offline: all.length - online.length,
      screens: this.getOnlineScreens(),
      byType: {
        [DEVICE_TYPES.MOBILE]: {
          total: all.filter(d => d.type === DEVICE_TYPES.MOBILE).length,
          online: online.filter(d => d.type === DEVICE_TYPES.MOBILE).length,
        },
        [DEVICE_TYPES.CLIENT]: {
          total: all.filter(d => d.type === DEVICE_TYPES.CLIENT).length,
          online: online.filter(d => d.type === DEVICE_TYPES.CLIENT).length,
        },
        [DEVICE_TYPES.ADMIN]: {
          total: all.filter(d => d.type === DEVICE_TYPES.ADMIN).length,
          online: online.filter(d => d.type === DEVICE_TYPES.ADMIN).length,
        },
      },
    };
  }

  _resolveScreenNumber(deviceId, requested, current = null) {
    const assigned = this._getAssignedScreenNumber(deviceId);
    const preferred = assigned || Number.parseInt(requested, 10) || current;

    if (preferred) {
      const existing = this.getByScreenNumber(preferred);
      if (!existing || existing.id === deviceId) {
        this._screenCounter = Math.max(this._screenCounter, preferred);
        return preferred;
      }
    }

    do {
      this._screenCounter += 1;
    } while (this.getByScreenNumber(this._screenCounter));

    return this._screenCounter;
  }

  _getAssignedScreenNumber(deviceId) {
    const value = config.SCREEN_ASSIGNMENTS?.[deviceId];
    const screenNumber = Number.parseInt(value, 10);
    return Number.isFinite(screenNumber) && screenNumber > 0 ? screenNumber : null;
  }

  _cleanup() {
    const now = Date.now();
    for (const [id, device] of this.devices) {
      if (device.online && now - device.lastHeartbeat > HEARTBEAT.timeout) {
        device.online = false;
        this._saveRegisteredScreens();
        this._log('warn', `Device timeout: ${device.name} (${id})`);
      }
    }
  }

  _screenView(d) {
    return {
      deviceId: d.id,
      screenNumber: d.screenNumber,
      name: d.name,
      online: Boolean(d.online),
      os: d.os,
      version: d.version,
      ipAddress: d.ipAddress || '',
      registeredAt: d.registeredAt,
      lastSeenAt: d.lastSeenAt || d.lastHeartbeat || null,
      snapshot: d.snapshot || null,
      status: d.status || null,
      settings: this._normalizeScreenSettings(d.settings || {}),
    };
  }

  _displayName(device) {
    const settingsName = String(device.settings?.name || '').trim();
    if (settingsName) return settingsName;
    return `${this._baseName(device.baseName || device.name || 'Display')} (${device.screenNumber}号屏)`;
  }

  _baseName(value) {
    return String(value || 'Display').replace(/\s*\(\d+\s*号屏\)\s*$/u, '').trim() || 'Display';
  }

  _normalizeScreenSettings(input = {}) {
    const snapshotIntervalSeconds = this._normalizeInterval(input.snapshotIntervalSeconds, DEFAULT_SCREEN_SETTINGS.snapshotIntervalSeconds);
    const statusIntervalSeconds = this._normalizeInterval(input.statusIntervalSeconds, DEFAULT_SCREEN_SETTINGS.statusIntervalSeconds);
    const backgroundFit = input.backgroundFit === 'contain' ? 'contain' : DEFAULT_SCREEN_SETTINGS.backgroundFit;
    const rawBounds = input.avatarBounds || {};
    const avatarBounds = {
      width: this._normalizeDimension(rawBounds.width, DEFAULT_SCREEN_SETTINGS.avatarBounds.width, 1, 7680),
      height: this._normalizeDimension(rawBounds.height, DEFAULT_SCREEN_SETTINGS.avatarBounds.height, 1, 4320),
      x: this._normalizeDimension(rawBounds.x, DEFAULT_SCREEN_SETTINGS.avatarBounds.x, -7680, 7680),
      y: this._normalizeDimension(rawBounds.y, DEFAULT_SCREEN_SETTINGS.avatarBounds.y, -4320, 4320),
    };
    return {
      name: String(input.name || '').trim().slice(0, 80),
      snapshotIntervalSeconds,
      statusIntervalSeconds,
      transparentBackground: input.transparentBackground === true,
      backgroundFit,
      backgroundImageUrl: String(input.backgroundImageUrl || '').trim().slice(0, 1200),
      backgroundImageName: String(input.backgroundImageName || '').trim().slice(0, 180),
      avatarBounds,
    };
  }

  _normalizeDimension(value, fallback, min, max) {
    const num = Number.parseInt(value, 10);
    if (!Number.isFinite(num)) return fallback;
    return Math.max(min, Math.min(max, num));
  }

  _normalizeInterval(value, fallback) {
    const num = Number.parseInt(value, 10);
    if (!Number.isFinite(num)) return fallback;
    return Math.max(1, Math.min(3600, num));
  }

  _loadRegisteredScreens() {
    try {
      if (!fs.existsSync(this.storePath)) return;
      const parsed = JSON.parse(fs.readFileSync(this.storePath, 'utf8'));
      for (const row of parsed.screens || []) {
        if (!row?.deviceId && !row?.id) continue;
        const id = row.deviceId || row.id;
        const device = {
          id,
          type: DEVICE_TYPES.CLIENT,
          baseName: this._baseName(row.baseName || row.name || 'Display'),
          os: row.os || 'unknown',
          version: row.version || '1.0.0',
          ipAddress: row.ipAddress || '',
          online: false,
          registeredAt: row.registeredAt || Date.now(),
          lastSeenAt: row.lastSeenAt || null,
          lastHeartbeat: 0,
          screenNumber: this._normalizeInterval(row.screenNumber, null),
          snapshot: row.snapshot || null,
          status: row.status || null,
          settings: this._normalizeScreenSettings(row.settings || {}),
        };
        device.name = this._displayName(device);
        this._screenCounter = Math.max(this._screenCounter, Number(device.screenNumber) || 0);
        this.devices.set(id, device);
      }
    } catch (err) {
      this._log('warn', `Load registered screens failed: ${err.message}`);
    }
  }

  _saveRegisteredScreens() {
    try {
      fs.mkdirSync(path.dirname(this.storePath), { recursive: true });
      const screens = this.getByType(DEVICE_TYPES.CLIENT).map(d => ({
        deviceId: d.id,
        screenNumber: d.screenNumber,
        name: d.name,
        baseName: d.baseName || '',
        os: d.os || 'unknown',
        version: d.version || '1.0.0',
        ipAddress: d.ipAddress || '',
        registeredAt: d.registeredAt || Date.now(),
        lastSeenAt: d.lastSeenAt || d.lastHeartbeat || null,
        snapshot: d.snapshot || null,
        status: d.status || null,
        settings: this._normalizeScreenSettings(d.settings || {}),
      }));
      fs.writeFileSync(this.storePath, JSON.stringify({ screens, updatedAt: Date.now() }, null, 2), 'utf8');
    } catch (err) {
      this._log('warn', `Save registered screens failed: ${err.message}`);
    }
  }

  _typeLabel(type) {
    const labels = {
      [DEVICE_TYPES.MOBILE]: 'Mobile',
      [DEVICE_TYPES.CLIENT]: 'Client',
      [DEVICE_TYPES.ADMIN]: 'Admin',
    };
    return labels[type] || 'Unknown';
  }

  _log(level, msg) {
    const ts = new Date().toISOString();
    console.log(`[${ts}] [${level.toUpperCase()}] [DeviceManager] ${msg}`);
  }
}

export const deviceManager = new DeviceManager();
export default deviceManager;
