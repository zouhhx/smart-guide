import fs from 'fs';
import path from 'path';
import { randomUUID } from 'crypto';
import { config } from '../config/index.js';

export class SpecialConfigStore {
  constructor() {
    this.filePath = path.join(config.DATA_DIR, 'specials.json');
    this._data = null;
  }

  list() {
    return this._load().specials;
  }

  getActiveId() {
    return this._load().activeId || null;
  }

  get(id) {
    return this.list().find(item => item.id === id) || null;
  }

  findByName(name) {
    const value = String(name || '').trim();
    if (!value) return null;
    return this.list().find(item => item.name === value || item.name.includes(value) || value.includes(item.name)) || null;
  }

  save(input) {
    const data = this._load();
    const now = new Date().toISOString();
    const id = input.id || randomUUID();
    const next = {
      id,
      name: String(input.name || '').trim() || '未命名专场',
      description: String(input.description || '').trim(),
      commentaryLevel: this._normalizeCommentaryLevel(input.commentaryLevel),
      assignments: this._normalizeAssignments(input.assignments),
      mode: String(input.mode || '').trim(),
      updatedAt: now,
      createdAt: input.createdAt || now,
    };

    const index = data.specials.findIndex(item => item.id === id);
    if (index >= 0) data.specials[index] = { ...data.specials[index], ...next };
    else data.specials.push(next);

    this._save(data);
    return next;
  }

  remove(id) {
    const data = this._load();
    const before = data.specials.length;
    data.specials = data.specials.filter(item => item.id !== id);
    if (data.activeId === id) data.activeId = null;
    this._save(data);
    return before !== data.specials.length;
  }

  removeFileReferences(fileName) {
    const target = String(fileName || '').trim();
    if (!target) return 0;
    const data = this._load();
    let changed = 0;

    data.specials = data.specials.map(special => ({
      ...special,
      assignments: (special.assignments || []).map(assignment => {
        const previousNames = this._normalizeFileNames(assignment);
        const fileNames = previousNames.filter(name => name !== target);
        const fileId = assignment.fileId === target ? '' : assignment.fileId || '';
        if (fileNames.length !== previousNames.length || fileId !== (assignment.fileId || '')) changed += 1;
        return {
          ...assignment,
          fileId,
          fileName: fileNames[0] || '',
          fileNames,
        };
      }).filter(assignment => assignment.action || assignment.fileId || assignment.fileName || assignment.fileNames.length),
    }));

    if (changed > 0) this._save(data);
    return changed;
  }

  setActive(id) {
    const data = this._load();
    if (!data.specials.some(item => item.id === id)) {
      throw new Error('special not found');
    }
    data.activeId = id;
    data.activeAt = new Date().toISOString();
    this._save(data);
    return this.get(id);
  }

  _normalizeAssignments(assignments) {
    if (Array.isArray(assignments)) {
      return assignments
        .map(item => this._normalizeAssignment(item))
        .filter(item => Number.isFinite(item.screenNumber) && item.screenNumber > 0 && (item.fileId || item.fileName || item.fileNames.length || item.action));
    }

    return Object.entries(assignments || {})
      .map(([screenNumber, item]) => this._normalizeAssignment({
        ...(typeof item === 'string' ? { fileName: item } : item || {}),
        screenNumber,
      }))
      .filter(item => Number.isFinite(item.screenNumber) && item.screenNumber > 0 && (item.fileId || item.fileName || item.fileNames.length || item.action));
  }

  _normalizeAssignment(item = {}) {
    const fileNames = this._normalizeFileNames(item);
    return {
      screenNumber: Number.parseInt(item.screenNumber, 10),
      fileId: item.fileId || '',
      fileName: fileNames[0] || '',
      fileNames,
      action: item.action || '',
    };
  }

  _normalizeCommentaryLevel(value) {
    const level = String(value || '').trim().toLowerCase();
    if (['concise', 'standard', 'detailed'].includes(level)) return level;
    if (['精炼', '精炼版', '普通', '普通客户'].includes(level)) return 'concise';
    if (['标准', '标准版', '重点', '重点客户'].includes(level)) return 'standard';
    if (['详细', '详细版', '专业', '专业客户'].includes(level)) return 'detailed';
    return 'standard';
  }

  _normalizeFileNames(item = {}) {
    const values = [];
    if (Array.isArray(item.fileNames)) {
      values.push(...item.fileNames);
    } else if (Array.isArray(item.files)) {
      values.push(...item.files.map(file => (
        typeof file === 'string'
          ? file
          : file?.fileName || file?.name || file?.path || ''
      )));
    } else if (item.fileName) {
      values.push(item.fileName);
    } else if (item.name) {
      values.push(item.name);
    }

    return [...new Set(values
      .map(value => String(value || '').trim())
      .filter(Boolean))];
  }

  _load() {
    if (this._data) return this._data;
    try {
      if (fs.existsSync(this.filePath)) {
        this._data = JSON.parse(fs.readFileSync(this.filePath, 'utf8'));
        this._data.specials ||= [];
        this._ensureDefaultSpecials(this._data);
        return this._data;
      }
    } catch (err) {
      console.warn(`[SpecialConfigStore] Failed to read specials: ${err.message}`);
    }

    this._data = { activeId: null, specials: [] };
    this._ensureDefaultSpecials(this._data);
    return this._data;
  }

  _ensureDefaultSpecials(data) {
    const now = new Date().toISOString();
    const defaults = [{
      id: 'energy-saving-special',
      name: '节能专场',
      description: '选择后所有展示屏幕显示黑色画面。',
      mode: 'blackout',
      assignments: [],
      createdAt: now,
      updatedAt: now,
      builtin: true,
    }];

    let changed = false;
    for (const item of defaults) {
      const index = data.specials.findIndex(special => special.id === item.id || special.name === item.name);
      if (index >= 0) {
        const previous = data.specials[index];
        data.specials[index] = { ...item, ...data.specials[index], id: item.id, mode: item.mode, builtin: true };
        if (previous.id !== item.id || previous.mode !== item.mode || previous.builtin !== true) changed = true;
      } else {
        data.specials.push(item);
        changed = true;
      }
    }
    if (changed) this._save(data);
  }

  _save(data) {
    fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
    fs.writeFileSync(this.filePath, JSON.stringify(data, null, 2), 'utf8');
    this._data = data;
  }
}

export const specialConfigStore = new SpecialConfigStore();
export default specialConfigStore;
