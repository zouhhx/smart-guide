import fs from 'node:fs';
import path from 'node:path';

export class OperationsIdempotencyStore {
  constructor(options = {}) {
    this.filePath = options.filePath || '';
    this.maxEntries = Math.max(10, Number(options.maxEntries) || 1000);
    this.entries = new Map();
    this._load();
  }

  get(eventId) {
    return this.entries.get(String(eventId || '')) || null;
  }

  set(eventId, result) {
    const key = String(eventId || '');
    if (!key) return;
    this.entries.delete(key);
    this.entries.set(key, result);
    while (this.entries.size > this.maxEntries) {
      this.entries.delete(this.entries.keys().next().value);
    }
    this._save();
  }

  _load() {
    if (!this.filePath) return;
    try {
      const parsed = JSON.parse(fs.readFileSync(this.filePath, 'utf8'));
      for (const item of parsed.entries || []) {
        if (!item?.eventId || !item?.result) continue;
        this.entries.set(String(item.eventId), item.result);
      }
    } catch (error) {
      if (error?.code !== 'ENOENT') {
        console.warn(`[Operations] failed to load idempotency store: ${error.message}`);
      }
    }
  }

  _save() {
    if (!this.filePath) return;
    try {
      fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
      const tempPath = `${this.filePath}.tmp`;
      const payload = {
        version: 1,
        updatedAt: new Date().toISOString(),
        entries: [...this.entries].map(([eventId, result]) => ({ eventId, result })),
      };
      fs.writeFileSync(tempPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
      fs.rmSync(this.filePath, { force: true });
      fs.renameSync(tempPath, this.filePath);
    } catch (error) {
      console.warn(`[Operations] failed to persist idempotency store: ${error.message}`);
    }
  }
}

export default OperationsIdempotencyStore;
