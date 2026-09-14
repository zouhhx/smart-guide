import fs from 'fs';
import path from 'path';
import { randomUUID } from 'crypto';
import { config } from '../config/index.js';

function normalizeText(value) {
  return String(value || '')
    .toLowerCase()
    .replace(/\s+/g, '')
    .replace(/[，。！？、,.!?;；:"'“”‘’（）()[\]{}<>《》【】]/g, '');
}

export class QaStore {
  constructor(options = {}) {
    this.filePath = options.filePath || path.join(config.DATA_DIR, 'qa.json');
    this._data = null;
  }

  list() {
    return this._load().items;
  }

  save(input) {
    const data = this._load();
    const now = new Date().toISOString();
    const id = input.id || randomUUID();
    const source = String(input.source || 'manual').trim() || 'manual';
    const sourceFile = String(input.sourceFile || '').trim()
      || (source === 'robot-teaching' ? '机器人现场补充' : source === 'manual' ? '手动上传' : '');
    const item = {
      id,
      question: String(input.question || '').trim(),
      answer: String(input.answer || '').trim(),
      keywords: Array.isArray(input.keywords)
        ? input.keywords.map(String).map(s => s.trim()).filter(Boolean)
        : String(input.keywords || '').split(/[,，\n]/).map(s => s.trim()).filter(Boolean),
      enabled: input.enabled !== false,
      source,
      sourceUrls: Array.isArray(input.sourceUrls) ? input.sourceUrls.map(String).filter(Boolean).slice(0, 8) : [],
      freshnessClass: String(input.freshnessClass || '').trim(),
      expiresAt: input.expiresAt || null,
      reviewStatus: String(input.reviewStatus || '').trim(),
      useCount: Number(input.useCount || 0),
      sourcePage: Number(input.sourcePage) || null,
      sourceFile,
      deleted: input.deleted === true,
      updatedAt: now,
      createdAt: input.createdAt || now,
    };

    if (!item.question || !item.answer) {
      throw new Error('question and answer are required');
    }

    const index = data.items.findIndex(row => row.id === id);
    if (index >= 0) data.items[index] = { ...data.items[index], ...item };
    else data.items.push(item);

    this._save(data);
    return item;
  }

  saveByQuestion(input) {
    const data = this._load();
    const question = String(input.question || '').trim();
    const normalized = normalizeText(question);
    const existing = normalized
      ? data.items.find(item => normalizeText(item.question) === normalized)
      : null;
    return this.save({
      ...input,
      id: existing?.id || input.id,
      createdAt: existing?.createdAt || input.createdAt,
    });
  }

  remove(id) {
    const data = this._load();
    const before = data.items.length;
    data.items = data.items.filter(item => item.id !== id);
    this._save(data);
    return data.items.length !== before;
  }

  removeMany(ids = []) {
    const data = this._load();
    const idSet = new Set((ids || []).map(String).filter(Boolean));
    const before = data.items.length;
    data.items = data.items.filter(item => !idSet.has(item.id));
    const removed = before - data.items.length;
    if (removed > 0) this._save(data);
    return removed;
  }

  hideMany(items = []) {
    const data = this._load();
    const now = new Date().toISOString();
    let hidden = 0;

    for (const input of items || []) {
      const id = String(input?.id || '').trim();
      if (!id) continue;
      const index = data.items.findIndex(item => String(item.id) === id);
      const source = String(input.source || 'manual').trim() || 'manual';
      const sourceFile = String(input.sourceFile || '').trim()
        || (source === 'robot-teaching' ? '机器人现场补充' : source === 'manual' ? '手动上传' : '');
      const hiddenItem = {
        ...(index >= 0 ? data.items[index] : {}),
        id,
        question: String(input.question || '').trim(),
        answer: String(input.answer || '').trim(),
        keywords: Array.isArray(input.keywords) ? input.keywords.map(String).filter(Boolean) : [],
        enabled: false,
        deleted: true,
        source,
        sourceFile,
        sourcePage: Number(input.sourcePage) || null,
        createdAt: input.createdAt || input.raw_json?.createdAt || now,
        updatedAt: now,
      };
      if (!hiddenItem.question || !hiddenItem.answer) continue;
      if (index >= 0) data.items[index] = hiddenItem;
      else data.items.push(hiddenItem);
      hidden += 1;
    }

    if (hidden > 0) this._save(data);
    return hidden;
  }

  updateMany(ids = [], patch = {}) {
    const data = this._load();
    const idSet = new Set((ids || []).map(String).filter(Boolean));
    const now = new Date().toISOString();
    let updated = 0;
    data.items = data.items.map(item => {
      if (!idSet.has(item.id)) return item;
      updated += 1;
      return {
        ...item,
        ...patch,
        updatedAt: now,
      };
    });
    if (updated > 0) this._save(data);
    return updated;
  }

  findAnswer(text) {
    const query = normalizeText(text);
    if (!query) return null;

    const scored = this.list()
      .filter(item => item.enabled !== false)
      .filter(item => !item.expiresAt || Date.parse(item.expiresAt) > Date.now())
      .map(item => ({ item, score: this._score(item, query) }))
      .filter(entry => entry.score >= 80)
      .sort((a, b) => b.score - a.score);

    return scored[0] || null;
  }

  _score(item, query) {
    const question = normalizeText(item.question);
    const keywords = (item.keywords || []).map(normalizeText).filter(Boolean);
    let score = 0;

    if (query === question) score += 100;
    if (question && query !== question && (query.includes(question) || question.includes(query))) {
      const shorter = Math.min(query.length, question.length);
      const longer = Math.max(query.length, question.length);
      const ratio = longer ? shorter / longer : 0;
      score += ratio >= 0.88 ? 82 : ratio >= 0.75 ? 68 : 40;
    }

    const similarity = this._similarity(query, question);
    if (similarity >= 0.9) score += 82;
    else if (similarity >= 0.82) score += 70;
    else if (similarity >= 0.74) score += 54;

    for (const keyword of keywords) {
      if (query.includes(keyword)) score += keyword.length >= 4 ? 16 : 8;
    }

    const queryChars = new Set([...query]);
    const questionChars = new Set([...question]);
    let overlap = 0;
    for (const ch of queryChars) {
      if (questionChars.has(ch)) overlap++;
    }
    if (questionChars.size) score += Math.round((overlap / questionChars.size) * 12);

    return score;
  }

  _similarity(a, b) {
    const left = String(a || '');
    const right = String(b || '');
    if (!left || !right) return 0;
    if (left === right) return 1;

    const leftBigrams = this._bigrams(left);
    const rightBigrams = this._bigrams(right);
    if (!leftBigrams.length || !rightBigrams.length) {
      const shorter = Math.min(left.length, right.length);
      const longer = Math.max(left.length, right.length);
      return longer ? shorter / longer : 0;
    }

    const counts = new Map();
    for (const item of rightBigrams) counts.set(item, (counts.get(item) || 0) + 1);
    let overlap = 0;
    for (const item of leftBigrams) {
      const count = counts.get(item) || 0;
      if (count > 0) {
        overlap += 1;
        counts.set(item, count - 1);
      }
    }
    return (2 * overlap) / (leftBigrams.length + rightBigrams.length);
  }

  _bigrams(value) {
    const text = String(value || '');
    if (text.length < 2) return text ? [text] : [];
    const rows = [];
    for (let i = 0; i < text.length - 1; i++) rows.push(text.slice(i, i + 2));
    return rows;
  }

  _load() {
    if (this._data) return this._data;
    try {
      if (fs.existsSync(this.filePath)) {
        const content = fs.readFileSync(this.filePath, 'utf8').replace(/^\uFEFF/, '');
        const parsed = JSON.parse(content);
        this._data = {
          items: Array.isArray(parsed) ? parsed : parsed.items || [],
        };
        return this._data;
      }
    } catch (err) {
      console.warn(`[QaStore] failed to read qa store: ${err.message}`);
    }

    this._data = { items: [] };
    return this._data;
  }

  _save(data) {
    fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
    fs.writeFileSync(this.filePath, JSON.stringify({
      items: data.items,
      updatedAt: new Date().toISOString(),
    }, null, 2), 'utf8');
    this._data = data;
  }
}

export const qaStore = new QaStore();
export default qaStore;
