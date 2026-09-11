import fs from 'fs';
import path from 'path';
import { createHash, randomUUID } from 'crypto';
import { config } from '../config/index.js';

function canonicalPayload(input) {
  return JSON.stringify({
    schemaVersion: String(input.schemaVersion || '1.0.0'),
    submissionId: String(input.submissionId || ''),
    createdAt: String(input.createdAt || ''),
    robotId: String(input.robotId || ''),
    sessionId: String(input.sessionId || ''),
    teachingTurnId: String(input.teachingTurnId || ''),
    question: String(input.question || '').trim(),
    rawAnswer: String(input.rawAnswer || '').trim(),
    suggestedAnswer: String(input.suggestedAnswer || '').trim(),
    analysis: input.analysis || {},
    completionReason: String(input.completionReason || ''),
    source: String(input.source || 'EXHIBITION_ROBOT'),
  });
}

export class QaDraftStore {
  constructor(options = {}) {
    this.filePath = options.filePath || path.join(config.DATA_DIR, 'qa-drafts.json');
  }

  list() {
    return [...this._load().items].sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
  }

  get(draftId) {
    const id = String(draftId || '').trim();
    return this._load().items.find(item => item.draftId === id) || null;
  }

  save(input = {}) {
    const submissionId = String(input.submissionId || '').trim();
    const question = String(input.question || '').trim();
    const rawAnswer = String(input.rawAnswer || '').trim();
    if (!submissionId || !question || !rawAnswer) {
      throw Object.assign(new Error('submissionId, question and rawAnswer are required'), { statusCode: 422 });
    }
    if (question.length > 1000 || rawAnswer.length > 16000 || String(input.suggestedAnswer || '').length > 4000) {
      throw Object.assign(new Error('teaching draft is too large'), { statusCode: 413 });
    }

    const data = this._load();
    const fingerprint = createHash('sha256').update(canonicalPayload(input)).digest('hex');
    const existing = data.items.find(item => item.submissionId === submissionId);
    if (existing) {
      if (existing.fingerprint !== fingerprint) {
        throw Object.assign(new Error('submissionId already exists with different content'), { statusCode: 409 });
      }
      return { item: existing, replayed: true };
    }

    const now = new Date().toISOString();
    const item = {
      ...JSON.parse(canonicalPayload(input)),
      draftId: `qa-draft-${randomUUID()}`,
      status: 'PENDING_REVIEW',
      fingerprint,
      createdAt: input.createdAt || now,
      updatedAt: now,
    };
    data.items.push(item);
    this._save(data);
    return { item, replayed: false };
  }

  updateReview(draftId, input = {}, reviewer = {}) {
    const data = this._load();
    const item = data.items.find(row => row.draftId === String(draftId || '').trim());
    if (!item) throw Object.assign(new Error('teaching draft not found'), { statusCode: 404 });
    if (!['PENDING_REVIEW', 'INDEX_FAILED'].includes(item.status)) {
      throw Object.assign(new Error('teaching draft can no longer be edited'), { statusCode: 409 });
    }

    const question = String(input.question ?? item.reviewQuestion ?? item.question ?? '').trim();
    const answer = String(input.answer ?? item.reviewAnswer ?? item.suggestedAnswer ?? item.rawAnswer ?? '').trim();
    if (!question || !answer) {
      throw Object.assign(new Error('question and answer are required'), { statusCode: 422 });
    }
    if (question.length > 1000 || answer.length > 4000) {
      throw Object.assign(new Error('reviewed teaching draft is too large'), { statusCode: 413 });
    }

    item.reviewQuestion = question;
    item.reviewAnswer = answer;
    item.reviewedBy = String(reviewer.id || reviewer.username || '').trim();
    item.reviewedByName = String(reviewer.displayName || reviewer.username || '').trim();
    item.updatedAt = new Date().toISOString();
    this._save(data);
    return item;
  }

  setStatus(draftId, status, patch = {}) {
    const data = this._load();
    const item = data.items.find(row => row.draftId === String(draftId || '').trim());
    if (!item) throw Object.assign(new Error('teaching draft not found'), { statusCode: 404 });
    Object.assign(item, patch, {
      status: String(status || item.status),
      updatedAt: new Date().toISOString(),
    });
    this._save(data);
    return item;
  }

  _load() {
    try {
      if (!fs.existsSync(this.filePath)) return { version: 1, items: [] };
      const parsed = JSON.parse(fs.readFileSync(this.filePath, 'utf8'));
      return { version: 1, items: Array.isArray(parsed.items) ? parsed.items : [] };
    } catch {
      return { version: 1, items: [] };
    }
  }

  _save(data) {
    fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
    const temporary = `${this.filePath}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(data, null, 2), 'utf8');
    fs.renameSync(temporary, this.filePath);
  }
}

export const qaDraftStore = new QaDraftStore();
export default qaDraftStore;
