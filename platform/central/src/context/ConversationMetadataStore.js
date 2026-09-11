import fs from 'fs';
import path from 'path';
import { config } from '../config/index.js';

export class ConversationMetadataStore {
  constructor(options = {}) {
    this.filePath = options.filePath || path.join(config.DATA_DIR, 'conversation-metadata.json');
    this.data = null;
  }

  getTitle(conversationId) {
    this._load();
    return String(this.data[String(conversationId || '')]?.title || '');
  }

  rename(conversationId, title) {
    this._load();
    const id = String(conversationId || '').trim();
    const normalizedTitle = String(title || '').replace(/[\r\n\t]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 60);
    if (!id) throw new Error('conversation id is required');
    if (!normalizedTitle) throw new Error('conversation title is required');
    this.data[id] = { title: normalizedTitle, updatedAt: new Date().toISOString() };
    this._save();
    return { conversationId: id, title: normalizedTitle };
  }

  remove(conversationId) {
    this._load();
    const id = String(conversationId || '').trim();
    if (!id || !this.data[id]) return false;
    delete this.data[id];
    this._save();
    return true;
  }

  _load() {
    if (this.data) return;
    this.data = {};
    try {
      if (fs.existsSync(this.filePath)) {
        const parsed = JSON.parse(fs.readFileSync(this.filePath, 'utf8'));
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) this.data = parsed;
      }
    } catch (err) {
      console.warn(`[ConversationMetadataStore] load skipped: ${err.message}`);
    }
  }

  _save() {
    fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
    fs.writeFileSync(this.filePath, `${JSON.stringify(this.data, null, 2)}\n`, 'utf8');
  }
}

export const conversationMetadataStore = new ConversationMetadataStore();
export default conversationMetadataStore;
