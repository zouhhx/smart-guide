import fs from 'fs';
import path from 'path';
import { config } from '../config/index.js';

export function isClientMediaAvailable(item) {
  if (!item || item.clientDisplay === false) return false;

  const publicationStatus = String(item.publicationStatus || '').trim().toUpperCase();
  if (publicationStatus) return publicationStatus === 'PUBLISHED';

  const reviewStatus = String(item.reviewStatus || '').trim().toUpperCase();
  if (['PENDING', 'REJECTED', 'FAILED'].includes(reviewStatus)) return false;

  const status = String(item.status || '').trim().toLowerCase();
  return !status || status === 'published';
}

const PPT_EXTS = new Set(['.ppt', '.pptx']);
const DOCUMENT_EXTS = new Set(['.pdf', '.docx']);
const VIDEO_EXTS = new Set(['.mp4', '.webm', '.avi', '.mov', '.mkv']);
const IMAGE_EXTS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg']);
const STOP_WORDS = [
  '有哪些', '有什么', '可以', '看到', '看看', '播放', '打开', '相关', '文件',
  '资料', '内容', '关于', '一下', '一些', '哪些', '什么', '请问', '帮我', '给我',
  '我要', '我想', '我', '找一下', '找一些', '展示', '的'
];

const HINT_TERMS = [
  '案例介绍', '思德案例', '行业案例', '案例',
  '中国移动', '移动云', '移动',
  '云电脑', '物联网', '视联网', '5G-A', '5GA', '5G', 'RedCap',
  '低空物流', '物流', '低空', '招商', '思德', '小岛台', '大岛台',
];

export class MediaLibrary {
  constructor() {
    this.libraryPath = path.join(config.MEDIA_DIR, 'library.json');
    this._items = null;
    this._mtimeMs = 0;
  }

  getAll() {
    this._loadIfNeeded();
    return this._items || [];
  }

  refresh() {
    this._items = null;
    this._mtimeMs = 0;
  }

  registerUploadedFile(file) {
    const name = String(file?.name || '').trim();
    if (!name) throw new Error('uploaded file name is required');
    const raw = fs.existsSync(this.libraryPath)
      ? JSON.parse(fs.readFileSync(this.libraryPath, 'utf8'))
      : { files: [] };
    const rows = Array.isArray(raw) ? raw : raw.files || raw.items || [];
    const uploadedAt = new Date().toISOString();
    const existing = rows.find(row => this._sameFile(row, name));
    if (!existing) {
      rows.push({
        index: rows.length + 1,
        id: `upload:${Date.now()}:${name}`,
        title: path.basename(name, file.ext || path.extname(name)),
        name,
        originalName: name,
        url: file.url,
        localPath: file.localPath,
        ext: file.ext || path.extname(name).toLowerCase(),
        type: file.type || '',
        size: Number(file.size || 0),
        commentary: '',
        commentaryPages: {},
        source: 'upload',
        contentStatus: 'UPLOADED',
        status: 'processing',
        publicationStatus: 'PROCESSING',
        reviewStatus: 'NOT_STARTED',
        uploadedAt,
        isNew: false,
        faqOnly: file.faqOnly === true,
        clientDisplay: file.clientDisplay !== false,
        contentSha256: String(file.contentSha256 || ''),
      });
    } else {
      Object.assign(existing, {
        title: path.basename(name, file.ext || path.extname(name)),
        originalName: file.originalName || name,
        url: file.url,
        localPath: file.localPath,
        ext: file.ext || path.extname(name).toLowerCase(),
        type: file.type || existing.type || '',
        size: Number(file.size || 0),
        source: 'upload',
        uploadedAt,
        isNew: false,
        faqOnly: file.faqOnly === true,
        clientDisplay: file.clientDisplay !== false,
        contentSha256: String(file.contentSha256 || existing.contentSha256 || ''),
      });
    }
    if (Array.isArray(raw)) fs.writeFileSync(this.libraryPath, JSON.stringify(rows, null, 2), 'utf8');
    else {
      if (raw.files) raw.files = rows;
      else if (raw.items) raw.items = rows;
      else raw.files = rows;
      raw.updatedAt = new Date().toISOString();
      fs.writeFileSync(this.libraryPath, JSON.stringify(raw, null, 2), 'utf8');
    }
    this.refresh();
    return this.getAll().find(item => this._sameFile(item, name));
  }

  updateContentStatus(fileName, contentStatus, details = {}) {
    const target = String(fileName || '').trim();
    if (!target) throw new Error('fileName is required');
    const raw = fs.existsSync(this.libraryPath) ? JSON.parse(fs.readFileSync(this.libraryPath, 'utf8')) : { files: [] };
    const rows = Array.isArray(raw) ? raw : raw.files || raw.items || [];
    const item = rows.find(row => this._sameFile(row, target));
    if (!item) throw new Error(`file not found in library: ${target}`);
    item.contentStatus = String(contentStatus || item.contentStatus || 'UPLOADED');
    for (const key of [
      'analysisJobId', 'analysisError', 'analysisProgress', 'generatedFaqCount',
      'status', 'publicationStatus', 'reviewStatus', 'publishMode', 'uploadedAt',
      'analysisCompletedAt', 'reviewRequestedAt', 'publishedAt', 'rejectedAt', 'isNew',
      'faqOnly', 'clientDisplay', 'analysisMode', 'analysisUnitCount',
    ]) {
      if (Object.prototype.hasOwnProperty.call(details, key)) item[key] = details[key];
    }
    item.analysisUpdatedAt = new Date().toISOString();
    if (Array.isArray(raw)) fs.writeFileSync(this.libraryPath, JSON.stringify(rows, null, 2), 'utf8');
    else {
      if (raw.files) raw.files = rows; else if (raw.items) raw.items = rows; else raw.files = rows;
      raw.updatedAt = new Date().toISOString();
      fs.writeFileSync(this.libraryPath, JSON.stringify(raw, null, 2), 'utf8');
    }
    this.refresh();
    return this.getAll().find(entry => this._sameFile(entry, target));
  }

  getCommentaries() {
    return this.getAll()
      .filter(isClientMediaAvailable)
      .filter(item => this._hasCommentary(item))
      .map(item => ({
        file: item.name,
        commentary: item.commentary || this._joinCommentaryPages(item.commentaryPages),
        commentaryPages: item.commentaryPages || {},
        status: 'ok',
      }));
  }

  updateCommentary(fileName, commentary, options = {}) {
    this._loadIfNeeded();
    const target = String(fileName || '').trim();
    if (!target) throw new Error('fileName is required');

    const raw = fs.existsSync(this.libraryPath)
      ? JSON.parse(fs.readFileSync(this.libraryPath, 'utf8'))
      : { files: [] };
    const rows = Array.isArray(raw) ? raw : raw.files || raw.items || [];
    const item = rows.find(row => this._sameFile(row, target));

    if (!item) {
      throw new Error(`file not found in library: ${target}`);
    }

    item.commentary = String(commentary || '').trim();
    if (Object.prototype.hasOwnProperty.call(options, 'commentaryPages')) {
      item.commentaryPages = this._normalizeCommentaryPages(options.commentaryPages);
    }
    if (Number.isFinite(Number(options.slideCount)) && Number(options.slideCount) > 0) {
      item.slideCount = Math.max(1, Math.floor(Number(options.slideCount)));
    }

    if (Array.isArray(raw)) {
      fs.writeFileSync(this.libraryPath, JSON.stringify(raw, null, 2), 'utf8');
    } else {
      if (raw.files) raw.files = rows;
      else if (raw.items) raw.items = rows;
      else raw.files = rows;
      raw.updatedAt = new Date().toISOString();
      fs.writeFileSync(this.libraryPath, JSON.stringify(raw, null, 2), 'utf8');
    }

    this._items = null;
    this._mtimeMs = 0;
    return this.getAll().find(entry => entry.name === target || entry.path === target || entry.originalName === target);
  }

  remove(fileName) {
    const target = String(fileName || '').trim();
    if (!target) throw new Error('fileName is required');

    const raw = fs.existsSync(this.libraryPath)
      ? JSON.parse(fs.readFileSync(this.libraryPath, 'utf8'))
      : { files: [] };
    const rows = Array.isArray(raw) ? raw : raw.files || raw.items || [];
    const index = rows.findIndex(row => this._sameFile(row, target));
    const removed = index >= 0 ? rows.splice(index, 1)[0] : null;
    rows.forEach((row, rowIndex) => { row.index = rowIndex + 1; });

    if (Array.isArray(raw)) {
      fs.writeFileSync(this.libraryPath, JSON.stringify(rows, null, 2), 'utf8');
    } else {
      if (raw.files) raw.files = rows;
      else if (raw.items) raw.items = rows;
      else raw.files = rows;
      raw.updatedAt = new Date().toISOString();
      fs.writeFileSync(this.libraryPath, JSON.stringify(raw, null, 2), 'utf8');
    }

    this.refresh();
    return removed;
  }

  hasLibrary() {
    return fs.existsSync(this.libraryPath);
  }

  search(query, limit = 12) {
    const terms = this._extractTerms(query);
    const items = this.getAll().filter(isClientMediaAvailable);
    if (!terms.length) return items.slice(0, limit);

    let ranked = this._rank(items, terms);
    if (!ranked.length && terms.some(term => term.includes('中国移动'))) {
      ranked = this._rank(items, ['移动']);
    }
    if (!ranked.length && terms.some(term => term.includes('移动'))) {
      ranked = this._rank(items, ['5g', 'redcap', '移动']);
    }

    return ranked.slice(0, limit).map(entry => entry.item);
  }

  _rank(items, terms) {
    return items
      .map(item => ({ item, score: this._score(item, terms) }))
      .filter(entry => entry.score > 0)
      .sort((a, b) => b.score - a.score || a.item.index - b.item.index);
  }

  _score(item, terms) {
    const haystack = [
      item.title,
      item.name,
      item.originalName,
      item.description,
      item.keywords,
      item.commentary,
      this._joinCommentaryPages(item.commentaryPages),
      item.programTitle,
    ].filter(Boolean).join(' ').toLowerCase();

    let score = 0;
    for (const rawTerm of terms) {
      const term = rawTerm.toLowerCase();
      if (!term) continue;
      if (haystack.includes(term)) score += term.length >= 3 ? 4 : 2;
      if (item.title?.toLowerCase().includes(term)) score += 4;
      if (item.originalName?.toLowerCase().includes(term)) score += 3;
    }
    return score;
  }

  _extractTerms(query) {
    let text = String(query || '').trim();
    const original = text;
    for (const word of STOP_WORDS) {
      text = text.replaceAll(word, ' ');
    }
    text = text.replace(/[，。！？、,.!?;；:：]/g, ' ');

    const terms = text.split(/\s+/).map(t => t.trim()).filter(Boolean);
    for (const term of HINT_TERMS) {
      if ((original.includes(term) || text.includes(term)) && !terms.includes(term)) {
        terms.push(term);
      }
    }
    if (original.toLowerCase().includes('ppt') && !terms.includes('ppt')) terms.push('ppt');
    if (original.includes('视频') && !terms.includes('视频')) terms.push('视频');
    if (original.includes('图片') && !terms.includes('图片')) terms.push('图片');
    return [...new Set(terms)];
  }

  _loadIfNeeded() {
    if (!fs.existsSync(this.libraryPath)) {
      this._items = [];
      this._mtimeMs = 0;
      return;
    }

    const stat = fs.statSync(this.libraryPath);
    if (this._items && stat.mtimeMs === this._mtimeMs) return;

    const raw = JSON.parse(fs.readFileSync(this.libraryPath, 'utf8'));
    const rows = Array.isArray(raw) ? raw : raw.files || raw.items || [];
    const libraryItems = rows
      .map((item, idx) => this._normalize(item, idx))
      .filter(Boolean);

    this._items = this._mergeWithDisk(libraryItems);
    this._mtimeMs = stat.mtimeMs;
  }

  _normalize(item, idx) {
    const name = String(item.name || '').trim();
    const url = String(item.url || '').trim();
    const ext = String(item.ext || path.extname(name) || this._safeUrlExt(url)).toLowerCase();
    const type = this._typeFromExt(ext);
    if (!name || !url || !type) return null;

    const localPath = item.localPath || `${config.MEDIA_WIN_PATH}\\${name}`;
    const diskPath = path.join(config.MEDIA_DIR, name);
    const commentaryPages = this._normalizeCommentaryPages(item.commentaryPages);
    const commentary = item.commentary || this._joinCommentaryPages(commentaryPages);
    const slideCount = item.slideCount || this._pptSlideCount(diskPath, ext) || this._pageCountFromCommentary(commentaryPages);
    return {
      index: Number(item.index || idx + 1),
      id: item.id,
      title: item.title || path.basename(name, ext),
      name,
      path: name,
      originalName: item.originalName || name,
      description: item.description || '',
      keywords: item.keywords || '',
      commentary,
      commentaryPages,
      hasCommentary: this._hasCommentary({ commentary, commentaryPages }),
      programTitle: item.programTitle || '',
      unitId: item.unitId || item.unit_id || '',
      sourceFile: item.sourceFile || '',
      sourcePage: item.sourcePage || '',
      contentStatus: item.contentStatus || '',
      analysisJobId: item.analysisJobId || '',
      analysisError: item.analysisError || null,
      analysisProgress: item.analysisProgress || null,
      generatedFaqCount: Number(item.generatedFaqCount || 0),
      analysisUpdatedAt: item.analysisUpdatedAt || null,
      status: item.status || 'published',
      publicationStatus: item.publicationStatus || (item.contentStatus === 'AI_READY' ? 'PUBLISHED' : ''),
      reviewStatus: item.reviewStatus || '',
      publishMode: item.publishMode || '',
      uploadedAt: item.uploadedAt || null,
      analysisCompletedAt: item.analysisCompletedAt || null,
      reviewRequestedAt: item.reviewRequestedAt || null,
      publishedAt: item.publishedAt || null,
      rejectedAt: item.rejectedAt || null,
      isNew: item.isNew === true,
      faqOnly: item.faqOnly === true,
      clientDisplay: item.clientDisplay !== false,
      analysisMode: item.analysisMode || '',
      analysisUnitCount: Number(item.analysisUnitCount || 0),
      ext,
      type,
      size: item.size || 0,
      slideCount,
      url,
      thumbnailUrl: item.thumbnailUrl || '',
      localPath,
      existsLocal: this._existsInMediaDir(name),
      source: item.source || 'library',
    };
  }

  _mergeWithDisk(items) {
    const merged = [...items];
    const seen = new Set(items.map(item => String(item.name || item.path || '').toLowerCase()));

    for (const diskItem of this._scanDisk(config.MEDIA_DIR)) {
      const key = String(diskItem.name || diskItem.path || '').toLowerCase();
      const existing = merged.find(item => String(item.name || item.path || '').toLowerCase() === key);
      if (existing) {
        existing.existsLocal = true;
        existing.size = existing.size || diskItem.size;
        continue;
      }
      if (!seen.has(key)) {
        diskItem.index = merged.length + 1;
        merged.push(diskItem);
        seen.add(key);
      }
    }

    return merged.map((item, index) => ({
      ...item,
      index: index + 1,
      hasCommentary: this._hasCommentary(item),
    }));
  }

  _scanDisk(dir, prefix = '') {
    if (!fs.existsSync(dir)) return [];

    const files = [];
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.name === 'library.json') continue;
      const relPath = prefix ? `${prefix}/${entry.name}` : entry.name;
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        files.push(...this._scanDisk(fullPath, relPath));
        continue;
      }

      const ext = path.extname(entry.name).toLowerCase();
      const type = this._typeFromExt(ext);
      if (!type) continue;

      const stat = fs.statSync(fullPath);
      const encodedPath = relPath.split('/').map(part => encodeURIComponent(part)).join('/');
      files.push({
        index: files.length + 1,
        id: `disk:${relPath}`,
        title: path.basename(entry.name, ext),
        name: entry.name,
        path: relPath,
        originalName: entry.name,
        description: '',
        keywords: '',
        commentary: '',
        commentaryPages: {},
        hasCommentary: false,
        programTitle: '',
        ext,
        type,
        size: stat.size,
        slideCount: this._pptSlideCount(fullPath, ext),
        url: `${config.PUBLIC_FILE_BASE_URL}/${encodedPath}`,
        thumbnailUrl: '',
        localPath: `${config.MEDIA_WIN_PATH}\\${relPath.replace(/\//g, '\\')}`,
        existsLocal: true,
        source: 'disk',
      });
    }
    return files;
  }

  _safeUrlExt(url) {
    try {
      return path.extname(new URL(url).pathname);
    } catch {
      return '';
    }
  }

  _typeFromExt(ext) {
    if (PPT_EXTS.has(ext)) return 'ppt';
    if (DOCUMENT_EXTS.has(ext)) return 'document';
    if (VIDEO_EXTS.has(ext)) return 'video';
    if (IMAGE_EXTS.has(ext)) return 'image';
    return '';
  }

  _pptSlideCount(filePath, ext) {
    if (ext !== '.pptx' || !fs.existsSync(filePath)) return null;
    try {
      const text = fs.readFileSync(filePath).toString('latin1');
      const slides = new Set([...text.matchAll(/ppt[\/\\]slides[\/\\]slide(\d+)\.xml/g)].map(match => match[1]));
      return slides.size || null;
    } catch {
      return null;
    }
  }

  _normalizeCommentaryPages(value) {
    if (!value) return {};
    if (Array.isArray(value)) {
      return value.reduce((acc, text, index) => {
        const clean = String(text || '').trim();
        if (clean) acc[String(index + 1)] = clean;
        return acc;
      }, {});
    }
    if (typeof value !== 'object') return {};

    return Object.entries(value).reduce((acc, [page, text]) => {
      const pageNumber = Number(String(page).replace(/^p/i, ''));
      const clean = String(text || '').trim();
      if (Number.isFinite(pageNumber) && pageNumber > 0 && clean) {
        acc[String(Math.floor(pageNumber))] = clean;
      }
      return acc;
    }, {});
  }

  _joinCommentaryPages(pages) {
    const normalized = this._normalizeCommentaryPages(pages);
    return Object.keys(normalized)
      .map(Number)
      .sort((a, b) => a - b)
      .map(page => normalized[String(page)])
      .filter(Boolean)
      .join('&&');
  }

  _pageCountFromCommentary(pages) {
    const normalized = this._normalizeCommentaryPages(pages);
    const numbers = Object.keys(normalized).map(Number).filter(Number.isFinite);
    return numbers.length ? Math.max(...numbers) : null;
  }

  _hasCommentary(item) {
    return Boolean(String(item?.commentary || '').trim() || this._joinCommentaryPages(item?.commentaryPages).trim());
  }

  _existsInMediaDir(name) {
    return fs.existsSync(path.join(config.MEDIA_DIR, name));
  }

  _sameFile(item, target) {
    const values = [
      item.name,
      item.path,
      item.originalName,
      item.original_file_name,
      item.title,
    ].filter(Boolean).map(value => String(value).trim());
    return values.includes(target);
  }
}

export const mediaLibrary = new MediaLibrary();
export default mediaLibrary;
