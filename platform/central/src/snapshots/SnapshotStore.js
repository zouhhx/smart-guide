import fs from 'fs';
import path from 'path';
import { config } from '../config/index.js';

class SnapshotStore {
  constructor() {
    this.dir = path.resolve(config.DATA_DIR || path.resolve(process.cwd(), 'data'), 'screen-snapshots');
    this.items = new Map();
    fs.mkdirSync(this.dir, { recursive: true });
  }

  save({ deviceId, screenNumber, imageBase64, width, height }) {
    if (!deviceId) throw new Error('deviceId is required');
    const clean = String(imageBase64 || '').replace(/^data:image\/\w+;base64,/, '');
    if (!clean) throw new Error('imageBase64 is required');

    const buffer = Buffer.from(clean, 'base64');
    if (!buffer.length) throw new Error('empty snapshot');
    if (buffer.length > 2 * 1024 * 1024) throw new Error('snapshot is too large');

    const safeId = deviceId.replace(/[^a-zA-Z0-9_-]/g, '_');
    const fileName = `${safeId}.jpg`;
    const filePath = path.join(this.dir, fileName);
    fs.writeFileSync(filePath, buffer);

    const item = {
      deviceId,
      screenNumber: Number.parseInt(screenNumber, 10) || null,
      fileName,
      url: `/screen-snapshots/${fileName}?t=${Date.now()}`,
      updatedAt: Date.now(),
      width: Number.parseInt(width, 10) || null,
      height: Number.parseInt(height, 10) || null,
      size: buffer.length,
    };
    this.items.set(deviceId, item);
    return item;
  }

  get(deviceId) {
    return this.items.get(deviceId) || null;
  }

  getFilePath(fileName) {
    const safeName = path.basename(fileName || '');
    const filePath = path.resolve(this.dir, safeName);
    if (!filePath.startsWith(`${this.dir}${path.sep}`)) return null;
    return filePath;
  }
}

export const snapshotStore = new SnapshotStore();
