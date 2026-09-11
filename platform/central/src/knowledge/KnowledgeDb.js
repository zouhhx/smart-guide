import fs from 'fs';
import path from 'path';
import { createHash } from 'crypto';
import { config } from '../config/index.js';
import { extractKnowledgeUnitId, matchesKnowledgeUnit } from './KnowledgeScope.js';

// Upload analysis now produces 100 FAQs for every PPT page. Keep a generous
// document cap so page-scoped FAQ collections are not silently truncated.
const MAX_CONVERTIBLE_FAQS_PER_DOCUMENT = 100000;

function nowIso() {
  return new Date().toISOString();
}

function text(value) {
  return String(value ?? '').trim();
}

function cleanScalar(value) {
  return String(value ?? '').trim().replace(/^['"]|['"]$/g, '');
}

function splitList(value) {
  if (Array.isArray(value)) return value.map(text).filter(Boolean);
  return text(value)
    .split(/[,，、\n]/)
    .map(item => item.trim())
    .filter(Boolean);
}

function parseFrontmatter(markdown) {
  const source = String(markdown || '').replace(/^\uFEFF/, '');
  const match = source.match(/^---\s*\r?\n([\s\S]*?)\r?\n---\s*(?:\r?\n|$)([\s\S]*)$/);
  if (!match) return { data: {}, body: source };

  const data = {};
  let currentListKey = '';
  for (const line of match[1].split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    if (currentListKey && trimmed.startsWith('- ')) {
      data[currentListKey].push(cleanScalar(trimmed.slice(2)));
      continue;
    }
    currentListKey = '';
    const index = trimmed.indexOf(':');
    if (index < 0) continue;
    const key = trimmed.slice(0, index).trim();
    const rawValue = trimmed.slice(index + 1).trim();
    if (!rawValue) {
      data[key] = [];
      currentListKey = key;
      continue;
    }
    if (rawValue.startsWith('[') && rawValue.endsWith(']')) {
      data[key] = rawValue.slice(1, -1).split(',').map(cleanScalar).filter(Boolean);
    } else {
      data[key] = cleanScalar(rawValue);
    }
  }
  return { data, body: match[2].trim() };
}

function extractSection(markdown, title) {
  const lines = String(markdown || '').split(/\r?\n/);
  const start = lines.findIndex(line => new RegExp(`^##+\\s*${title}\\s*$`).test(line.trim()));
  if (start < 0) return '';
  const collected = [];
  for (let i = start + 1; i < lines.length; i += 1) {
    if (/^##+\s+/.test(lines[i].trim())) break;
    collected.push(lines[i]);
  }
  return collected.join('\n').trim();
}

function normalizeForSearch(value) {
  return text(value)
    .toLowerCase()
    .replace(/\s+/g, '')
    .replace(/[，。！？、,.!?;；:"'“”‘’（）()[\]{}<>《》【】]/g, '');
}

function normalizeQuestion(value) {
  return normalizeForSearch(value)
    .replace(/(请问|帮我|给我|一下|一些|哪些|什么|怎么|如何|是否|是不是|有没有|主要|核心|相关|问题|内容|资料|文件|呢|吗|吧|啊|呀|的)/g, '');
}

function questionIntent(value) {
  const normalized = normalizeForSearch(value);
  if (/(有什么用|有何用|用途|作用|价值|意义|干什么|能做什么|做什么)/.test(normalized)) return 'utility';
  if (/(解决|痛点|问题|难点)/.test(normalized)) return 'problem';
  if (/(适用|应用|场景|行业)/.test(normalized)) return 'scenario';
  if (/(核心|优势|特点|亮点)/.test(normalized)) return 'feature';
  return '';
}

function subjectText(value) {
  return normalizeQuestion(value)
    .replace(/(是什么|介绍|说明|有什么用|有何用|有用|有价值|用途|作用|价值|意义|干什么|能做什么|做什么|解决|痛点|问题|难点|适用|应用|场景|行业|核心|优势|特点|亮点|技术|资源|资料|文件|主要|里面|里|有|是)/g, '');
}

function hashId(prefix, value) {
  return `${prefix}:${createHash('sha1').update(String(value)).digest('hex').slice(0, 16)}`;
}

function normalizeVectorDocument(doc) {
  if (!doc?.id || !text(doc.text)) return null;
  const rawId = text(doc.id);
  const idHash = createHash('sha1').update(rawId).digest('hex').slice(0, 10);
  const safeId = rawId.replace(/[^a-zA-Z0-9:_-]/g, '_').slice(0, 160);
  return {
    id: `${safeId}:${idHash}`,
    text: text(doc.text),
    metadata: Object.fromEntries(Object.entries(doc.metadata || {}).map(([key, value]) => [
      key,
      Array.isArray(value) ? value.map(text).filter(Boolean).join('、') : text(value),
    ])),
  };
}

function faqVectorSource(item = {}) {
  return {
    id: `faq:${item.id}`,
    text: [
      `问题：${item.question}`,
      `答案：${item.answer}`,
      item.keywords?.length ? `关键词：${item.keywords.join('、')}` : '',
      item.tags?.length ? `标签：${item.tags.join('、')}` : '',
    ].filter(Boolean).join('\n'),
    metadata: {
      object_type: 'faq',
      item_id: item.id,
      question: item.question,
      answer: item.answer,
      title: item.question,
      tags: item.tags || item.keywords || [],
      source: item.source || 'markdown-system',
      status: item.status || 'published',
      expires_at: item.expiresAt || '',
      unit_id: extractKnowledgeUnitId(item),
      source_file: item.sourceFile || item.raw_json?.sourceFile || item.raw_json?.frontmatter?.resource || '',
      source_page: item.sourcePage || item.raw_json?.sourcePage || '',
    },
  };
}

class KnowledgeDb {
  constructor() {
    this.indexPath = path.resolve(config.DATA_DIR, 'markdown-index.json');
    this.obsidianVaultPath = config.OBSIDIAN_VAULT_PATH;
    this.index = this._emptyIndex();
    this._loaded = false;
  }

  open() {
    this._loadIndex();
    return this.index;
  }

  close() {
    this._persistIndex();
  }

  getStatus() {
    this._loadIndex();
    return {
      source: 'markdown',
      indexPath: this.indexPath,
      obsidianVaultPath: this.obsidianVaultPath,
      counts: this.index.counts,
      lastSyncAt: this.index.lastSyncAt || null,
      lastObsidianSyncAt: this.index.lastObsidianSyncAt || null,
      lastMissedQuestionAt: this.index.lastMissedQuestionAt || null,
    };
  }

  getVectorDocuments() {
    this._loadIndex();
    const docs = [];
    const pushDoc = (doc) => {
      const normalized = normalizeVectorDocument(doc);
      if (normalized) docs.push(normalized);
    };

    for (const item of this._answerableFaqItems()) {
      pushDoc(faqVectorSource(item));
    }

    for (const item of this.index.resources) {
      if (item.status && item.status !== 'published') continue;
      pushDoc({
        id: `resource:${item.id}`,
        text: [
          `文件标题：${item.title}`,
          `文件名：${item.file_name}`,
          `类型：${item.type}`,
          item.summary ? `概括：${item.summary}` : '',
          item.content,
          item.tags?.length ? `标签：${item.tags.join('、')}` : '',
        ].filter(Boolean).join('\n'),
        metadata: {
          object_type: 'resource',
          item_id: item.id,
          title: item.title,
          resource_id: item.resource_id || item.file_name,
          file_name: item.file_name,
          type: item.type,
          tags: item.tags || [],
          source: item.source || 'markdown-system',
          status: item.status || 'published',
        },
      });
    }

    for (const item of this.index.knowledgeObjects) {
      if (item.status !== 'published' || item.object_type === 'faq') continue;
      const chunks = this._chunkText([
        item.summary ? `概括：${item.summary}` : '',
        item.content,
        item.tags?.length ? `标签：${item.tags.join('、')}` : '',
      ].filter(Boolean).join('\n'), 700, 120);
      chunks.forEach((chunk, index) => pushDoc({
        id: `chunk:${item.id}:${index + 1}`,
        text: [
          `标题：${item.title}`,
          item.resource_id ? `资源：${item.resource_id}` : '',
          chunk,
        ].filter(Boolean).join('\n'),
        metadata: {
          object_type: 'chunk',
          item_id: item.id,
          title: item.title,
          resource_id: item.resource_id,
          type: item.object_type,
          chunk_index: String(index + 1),
          tags: item.tags || [],
          source: item.source || 'markdown-system',
          status: item.status || 'published',
        },
      }));
    }

    return docs;
  }

  buildVectorFaqDocument(item) {
    return normalizeVectorDocument(faqVectorSource(item));
  }

  syncFromCurrentStores({ mediaLibrary, qaStore, specialConfigStore }) {
    this._loadIndex();
    const syncedAt = nowIso();
    const resources = mediaLibrary.getAll();
    const qaItems = qaStore.list();
    const specials = specialConfigStore.list();

    this.index.resources = resources.filter(item => item.faqOnly !== true).map(item => this._resourceToIndex(item, syncedAt));
    this.index.qaItems = qaItems.map(item => this._qaToIndex(item, syncedAt));
    this.index.scenes = specials.map(item => this._sceneToIndex(item, syncedAt));
    this.index.sceneResources = this._specialAssignmentsToIndex(specials, syncedAt);
    this.index.knowledgeObjects = [
      ...this.index.resources.map(item => this._resourceKnowledgeObject(item, syncedAt)),
      ...this.index.qaItems.map(item => this._qaKnowledgeObject(item, syncedAt)),
      ...this.index.obsidianObjects,
    ];
    this.index.lastSyncAt = syncedAt;
    this._refreshCounts();
    this._persistIndex();
    return this.getStatus();
  }

  syncObsidian(options = {}) {
    this._loadIndex();
    const vaultPath = path.resolve(text(options.vaultPath || this.obsidianVaultPath || config.OBSIDIAN_VAULT_PATH));
    if (!fs.existsSync(vaultPath) || !fs.statSync(vaultPath).isDirectory()) {
      throw new Error(`Obsidian 目录不存在: ${vaultPath}`);
    }

    const syncedAt = nowIso();
    const files = this._listMarkdownFiles(vaultPath);
    const imported = {
      files: files.length,
      knowledgeObjects: 0,
      qaItems: 0,
      scenes: 0,
      skipped: 0,
    };

    const obsidianObjects = [];
    const obsidianQaItems = [];
    const obsidianScenes = [];

    for (const file of files) {
      const result = this._importObsidianMarkdown({ file, vaultPath, syncedAt });
      imported.knowledgeObjects += result.knowledgeObjects.length;
      imported.qaItems += result.qaItems.length;
      imported.scenes += result.scenes.length;
      imported.skipped += result.skipped;
      obsidianObjects.push(...result.knowledgeObjects);
      obsidianQaItems.push(...result.qaItems);
      obsidianScenes.push(...result.scenes);
    }

    this.index.obsidianObjects = obsidianObjects;
    this.index.obsidianQaItems = obsidianQaItems;
    this.index.obsidianScenes = obsidianScenes;
    this.index.knowledgeObjects = [
      ...this.index.resources.map(item => this._resourceKnowledgeObject(item, syncedAt)),
      ...this.index.qaItems.map(item => this._qaKnowledgeObject(item, syncedAt)),
      ...obsidianObjects,
    ];
    this.index.lastObsidianSyncAt = syncedAt;
    this.obsidianVaultPath = vaultPath;
    this.index.obsidianVaultPath = vaultPath;
    this._refreshCounts();
    this._persistIndex();

    return {
      ...this.getStatus(),
      obsidianVaultPath: vaultPath,
      obsidianImported: imported,
    };
  }

  search(query, options = {}) {
    this._loadIndex();
    const q = normalizeForSearch(query);
    if (!q) return [];
    const limit = Math.max(1, Math.min(Number(options.limit) || 12, 50));
    const status = text(options.status || 'published');
    const terms = this._extractTerms(query);

    return this.index.knowledgeObjects
      .filter(item => !status || item.status === status)
      .map(item => ({ item, score: this._score(item, q, terms) }))
      .filter(entry => entry.score > 0)
      .sort((a, b) => b.score - a.score || text(a.item.title).localeCompare(text(b.item.title), 'zh-CN'))
      .slice(0, limit)
      .map(entry => entry.item);
  }

  findRagSnippets(query, options = {}) {
    this._loadIndex();
    const q = normalizeForSearch(query);
    if (!q) return [];
    const limit = Math.max(1, Math.min(Number(options.limit) || 5, 12));
    const minScore = Number.isFinite(Number(options.minScore)) ? Number(options.minScore) : 12;
    const excerptLength = Math.max(120, Math.min(Number(options.excerptLength) || 320, 800));
    const terms = this._extractTerms(query);
    const rows = [];
    const seen = new Set();
    for (const entry of this.index.knowledgeObjects
      .filter(item => item.status === 'published')
      .filter(item => item.object_type !== 'faq')
      .map(item => ({ item, score: this._score(item, q, terms) }))
      .filter(entry => entry.score >= minScore)
      .sort((a, b) => b.score - a.score || text(a.item.title).localeCompare(text(b.item.title), 'zh-CN'))) {
      const key = text(entry.item.title || entry.item.resource_id || entry.item.file_name || entry.item.id);
      if (key && seen.has(key)) continue;
      if (key) seen.add(key);
      rows.push(entry);
      if (rows.length >= limit) break;
    }

    return rows.map(({ item, score }, index) => ({
      index: index + 1,
      id: item.id,
      title: text(item.title || item.resource_id || item.file_name || `资料${index + 1}`),
      type: text(item.object_type || item.type || 'resource'),
      source: text(item.source),
      resourceId: text(item.resource_id || item.file_name),
      file: text(item.raw_json?.file || item.file_name || item.file_path),
      score,
      summary: text(item.summary).slice(0, 160),
      excerpt: this._makeExcerpt(item, query, excerptLength),
      tags: (item.tags || []).slice(0, 8),
    })).filter(item => item.excerpt || item.summary);
  }

  findAnswer(query) {
    const match = this.findAnswerCandidates(query, { limit: 1, minScore: 8 })[0];
    return match || null;
  }

  hasQuestionAcrossKnowledgeUnits(query, minUnits = 2) {
    this._loadIndex();
    const normalized = normalizeForSearch(query);
    if (!normalized) return false;
    const units = new Set();
    for (const item of this._answerableFaqItems()) {
      if (normalizeForSearch(item.question) !== normalized) continue;
      const unitId = extractKnowledgeUnitId(item);
      if (unitId) units.add(unitId);
      if (units.size >= Math.max(2, Number(minUnits) || 2)) return true;
    }
    return false;
  }

  findAnswerCandidates(query, options = {}) {
    this._loadIndex();
    const normalized = normalizeForSearch(query);
    if (!normalized) return [];
    const limit = Math.max(1, Math.min(Number(options.limit) || 8, 30));
    const minScore = Number.isFinite(Number(options.minScore)) ? Number(options.minScore) : 8;
    const rows = this._answerableFaqItems()
      .filter(item => matchesKnowledgeUnit(item, options.unitId));

    return rows
      .map(item => ({ item, score: this._scoreQa(item, normalized) }))
      .filter(entry => entry.score >= minScore)
      .sort((a, b) => b.score - a.score)
      .slice(0, limit);
  }

  listFaqs() {
    this._loadIndex();
    const overriddenIds = new Set(this.index.qaItems.map(item => text(item.id)));
    return [
      ...this.index.qaItems.map(item => ({
        ...item,
        source: item.source || 'manual',
        sourceFile: this._faqAssociation(item),
        sourceFaqId: text(item.id),
        readOnly: false,
        enabled: item.enabled !== false,
      })),
      ...this.index.obsidianQaItems.filter(item => !overriddenIds.has(text(item.id))).map(item => ({
        ...item,
        source: 'obsidian',
        sourceFile: this._faqAssociation(item),
        sourceFaqId: text(item.id),
        readOnly: true,
        enabled: item.enabled !== false,
      })),
    ];
  }

  _faqAssociation(item = {}) {
    const source = text(item.source || item.raw_json?.source);
    if (source === 'robot-teaching') return '机器人现场补充';
    const associated = text(
      item.sourceFile
      || item.raw_json?.sourceFile
      || item.raw_json?.frontmatter?.resource
      || item.raw_json?.frontmatter?.source_file
      || item.raw_json?.file,
    );
    if (associated) return associated;
    return source === 'manual' ? '手动上传' : '';
  }

  _effectiveFaqItems() {
    const overriddenIds = new Set(this.index.qaItems.map(item => text(item.id)));
    return [
      ...this.index.qaItems,
      ...this.index.obsidianQaItems.filter(item => !overriddenIds.has(text(item.id))),
    ];
  }

  _answerableFaqItems() {
    return this._effectiveFaqItems()
      .filter(item => item.status === 'published' && item.enabled !== false)
      .filter(item => !item.expiresAt || Date.parse(item.expiresAt) > Date.now());
  }

  isReadOnlyFaq(id) {
    const target = text(id);
    return Boolean(target && this.index.obsidianQaItems.some(item => text(item.id) === target));
  }

  listObsidianAnalyses(options = {}) {
    this._loadIndex();
    const limit = Math.max(1, Math.min(Number(options.limit) || 80, 300));
    const offset = Math.max(0, Number(options.offset) || 0);
    const q = normalizeForSearch(options.q || '');
    const rows = this.index.obsidianObjects
      .filter(item => /##\s*AI分析结果/.test(item.content || ''))
      .map(item => this._analysisSummary(item))
      .filter(item => {
        if (!q) return true;
        const haystack = normalizeForSearch([
          item.title,
          item.file,
          item.summary,
          item.commentaryDraft,
          item.contentType,
          item.year,
          item.sourceFrom,
          item.caseRegion,
          (item.tags || []).join(' '),
        ].join(' '));
        return haystack.includes(q);
      })
      .sort((a, b) => text(b.updatedAt).localeCompare(text(a.updatedAt)) || text(a.title).localeCompare(text(b.title), 'zh-CN'));

    return {
      items: rows.slice(offset, offset + limit),
      total: rows.length,
      limit,
      offset,
    };
  }

  recordMissedQuestion({ question, sceneId = '', source = '' }) {
    this._loadIndex();
    const value = text(question);
    if (!value) return null;
    const now = nowIso();
    const key = `${sceneId || 'global'}::${value}`;
    let item = this.index.missedQuestions.find(row => row.id === key);
    if (!item) {
      item = {
        id: key,
        question: value,
        scene_id: sceneId,
        source,
        count: 0,
        status: 'pending',
        first_asked_at: now,
      };
      this.index.missedQuestions.push(item);
    }
    item.count += 1;
    item.source = source;
    item.last_asked_at = now;
    this.index.lastMissedQuestionAt = now;
    this._appendMissedQuestionMarkdown(item);
    this._refreshCounts();
    this._persistIndex();
    return item;
  }

  _emptyIndex() {
    return {
      source: 'markdown',
      version: 1,
      indexPath: this.indexPath,
      obsidianVaultPath: this.obsidianVaultPath,
      resources: [],
      qaItems: [],
      scenes: [],
      sceneResources: [],
      knowledgeObjects: [],
      obsidianObjects: [],
      obsidianQaItems: [],
      obsidianScenes: [],
      missedQuestions: [],
      counts: {
        resources: 0,
        qa_items: 0,
        scenes: 0,
        scene_resources: 0,
        knowledge_objects: 0,
        missed_questions: 0,
      },
      lastSyncAt: null,
      lastObsidianSyncAt: null,
      lastMissedQuestionAt: null,
    };
  }

  _loadIndex() {
    if (this._loaded) return;
    try {
      if (fs.existsSync(this.indexPath)) {
        const parsed = JSON.parse(fs.readFileSync(this.indexPath, 'utf8'));
        this.index = {
          ...this._emptyIndex(),
          ...parsed,
          resources: parsed.resources || [],
          qaItems: parsed.qaItems || [],
          scenes: parsed.scenes || [],
          sceneResources: parsed.sceneResources || [],
          knowledgeObjects: parsed.knowledgeObjects || [],
          obsidianObjects: parsed.obsidianObjects || [],
          obsidianQaItems: parsed.obsidianQaItems || [],
          obsidianScenes: parsed.obsidianScenes || [],
          missedQuestions: parsed.missedQuestions || [],
        };
        this.obsidianVaultPath = this.index.obsidianVaultPath || this.obsidianVaultPath;
      }
    } catch (err) {
      console.warn(`[MarkdownIndex] failed to read index: ${err.message}`);
      this.index = this._emptyIndex();
    }
    this._loaded = true;
    this._refreshCounts();
  }

  _persistIndex() {
    fs.mkdirSync(path.dirname(this.indexPath), { recursive: true });
    this.index.source = 'markdown';
    this.index.indexPath = this.indexPath;
    this.index.obsidianVaultPath = this.obsidianVaultPath;
    this._refreshCounts();
    fs.writeFileSync(this.indexPath, JSON.stringify(this.index, null, 2), 'utf8');
  }

  _refreshCounts() {
    this.index.counts = {
      resources: this.index.resources.length,
      qa_items: this.index.qaItems.length + this.index.obsidianQaItems.length,
      scenes: this.index.scenes.length + this.index.obsidianScenes.length,
      scene_resources: this.index.sceneResources.length,
      knowledge_objects: this.index.knowledgeObjects.length,
      missed_questions: this.index.missedQuestions.length,
    };
  }

  _resourceToIndex(item = {}, syncedAt) {
    const title = text(item.title || item.originalName || item.name);
    return {
      id: text(item.id || item.name || item.path || hashId('resource', title)),
      object_type: 'resource',
      title,
      summary: text(item.description || item.commentary || title).slice(0, 240),
      content: [
        title,
        item.name,
        item.originalName,
        item.description,
        item.commentary,
        this._joinCommentaryPages(item.commentaryPages),
        item.programTitle,
        item.keywords,
      ].filter(Boolean).join('\n'),
      resource_id: text(item.id || item.name || ''),
      file_name: text(item.name || ''),
      file_path: text(item.path || item.localPath || item.local_path || ''),
      type: text(item.type || ''),
      tags: splitList(item.tags || item.keywords),
      status: text(item.status || 'published'),
      version: text(item.version || 'v1.0'),
      source: 'markdown-system',
      raw_json: item,
      updated_at: text(item.updatedAt || syncedAt),
    };
  }

  _qaToIndex(item = {}, syncedAt) {
    const source = text(item.source || 'markdown-system');
    return {
      id: text(item.id || hashId('qa', `${item.question}:${item.answer}`)),
      question: text(item.question),
      answer: text(item.answer),
      keywords: splitList(item.keywords),
      tags: splitList(item.tags || item.keywords),
      status: item.enabled === false ? 'disabled' : 'published',
      source,
      sourcePage: Number(item.sourcePage) || null,
      sourceFile: text(item.sourceFile) || (source === 'robot-teaching' ? '机器人现场补充' : source === 'manual' ? '手动上传' : ''),
      expiresAt: item.expiresAt || null,
      raw_json: item,
      updated_at: text(item.updatedAt || syncedAt),
    };
  }

  _sceneToIndex(item = {}, syncedAt) {
    return {
      id: text(item.id || hashId('scene', item.name)),
      name: text(item.name),
      description: text(item.description),
      mode: text(item.mode),
      status: text(item.status || 'published'),
      source: 'markdown-system',
      raw_json: item,
      updated_at: text(item.updatedAt || syncedAt),
    };
  }

  _specialAssignmentsToIndex(specials, syncedAt) {
    const rows = [];
    for (const special of specials || []) {
      for (const assignment of special.assignments || []) {
        const files = assignment.fileNames?.length ? assignment.fileNames : [assignment.fileName].filter(Boolean);
        files.forEach((fileName, index) => rows.push({
          scene_id: special.id,
          resource_file_name: fileName,
          screen_number: assignment.screenNumber,
          sort_order: index,
          action: assignment.action || '',
          updated_at: syncedAt,
        }));
      }
    }
    return rows;
  }

  _resourceKnowledgeObject(item, syncedAt) {
    return {
      id: `resource:${item.id}`,
      object_type: 'resource',
      title: item.title,
      summary: item.summary,
      content: item.content,
      resource_id: item.resource_id || item.file_name,
      page_number: null,
      tags: item.tags || [],
      status: item.status || 'published',
      version: item.version || 'v1.0',
      source: item.source || 'markdown-system',
      raw_json: item.raw_json || item,
      updated_at: item.updated_at || syncedAt,
    };
  }

  _qaKnowledgeObject(item, syncedAt) {
    return {
      id: `qa:${item.id}`,
      object_type: 'faq',
      title: item.question,
      summary: item.answer.slice(0, 160),
      content: `${item.question}\n${item.answer}\n${(item.keywords || []).join(' ')}`,
      resource_id: text(item.sourceFile || item.raw_json?.sourceFile || item.raw_json?.frontmatter?.resource || ''),
      page_number: Number(item.sourcePage || item.raw_json?.sourcePage) || null,
      tags: item.tags || item.keywords || [],
      status: item.status || 'published',
      version: 'v1.0',
      source: item.source || 'markdown-system',
      raw_json: item.raw_json || item,
      updated_at: item.updated_at || syncedAt,
    };
  }

  _listMarkdownFiles(root) {
    const result = [];
    const visit = dir => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        if (entry.name.startsWith('.') || entry.name === 'node_modules') continue;
        const fullPath = path.join(dir, entry.name);
        if (entry.isDirectory()) visit(fullPath);
        else if (entry.isFile() && /\.md$/i.test(entry.name)) result.push(fullPath);
      }
    };
    visit(root);
    return result;
  }

  _importObsidianMarkdown({ file, vaultPath, syncedAt }) {
    const markdown = fs.readFileSync(file, 'utf8');
    const { data, body } = parseFrontmatter(markdown);
    const type = text(data.type || 'note').toLowerCase();
    const relativePath = path.relative(vaultPath, file).replace(/\\/g, '/');
    const id = text(data.id || data.serial || data.no || data.index || data['序号'] || `${type}:${relativePath}`);
    const title = text(data.title || data.name || data.file_name || data['文件名称'] || path.basename(file, path.extname(file)));
    const status = text(data.status || 'published');
    const domainTags = splitList(data.domain_tags || data.domainTags || data['领域标签']);
    const tags = [...splitList(data.tags), ...domainTags];
    const sceneIds = splitList(data.scene || data.scenes || data.specials);
    const version = text(data.version || 'v1.0');
    const raw = { frontmatter: data, file: relativePath };
    const sourceFile = text(data.resource || data.resource_id || data.source_file || data.file || data.file_path || data['文件名称'] || relativePath);
    const result = { knowledgeObjects: [], qaItems: [], scenes: [], skipped: 0 };

    if (!id || !title) {
      result.skipped = 1;
      return result;
    }

    if (type === 'faq_bank') {
      const tableFaqs = this._extractFaqTableRows(body);
      if (!tableFaqs.length) {
        result.skipped = 1;
        return result;
      }
      tableFaqs.forEach((faq, index) => {
        const qa = {
          id: `${id}:faq:${faq.rowId || index + 1}`,
          question: faq.question,
          answer: faq.answer,
          keywords: [...tags, ...sceneIds, title, text(data.unit_title), faq.intent].filter(Boolean),
          tags,
          status,
          scene_ids: sceneIds,
          source: 'obsidian',
          sourcePage: faq.sourcePage || null,
          sourceFile,
          unit_id: text(data.unit_id || data.unitId),
          raw_json: { ...raw, section: 'FAQ表格', row: faq.rowId || index + 1 },
          updated_at: syncedAt,
        };
        result.qaItems.push(qa);
        result.knowledgeObjects.push(this._qaKnowledgeObject(qa, syncedAt));
      });
      return result;
    }

    if (type === 'faq' || type === 'qa') {
      const objectRole = text(data.object_role || data.objectRole).toLowerCase();
      const isFaqPackage = objectRole === 'faq_package'
        || /(?:^|[_-])faq_package$/i.test(id)
        || /(?:^|\s)faq_package$/i.test(title);
      if (isFaqPackage) {
        result.skipped = 1;
        return result;
      }
      const question = text(extractSection(body, '问题') || data.question || title);
      const answer = text(extractSection(body, '答案') || data.answer || body);
      if (!question || !answer) {
        result.skipped = 1;
        return result;
      }
      const qa = {
        id,
        question,
        answer,
        keywords: [...tags, ...sceneIds],
        tags,
        status,
        scene_ids: sceneIds,
        source: 'obsidian',
        sourceFile,
        sourcePage: Number(data.page || data.page_number) || null,
        unit_id: text(data.unit_id || data.unitId),
        raw_json: raw,
        updated_at: syncedAt,
      };
      result.qaItems.push(qa);
      result.knowledgeObjects.push(this._qaKnowledgeObject(qa, syncedAt));
      return result;
    }

    if (type === 'scene' || type === 'special') {
      result.scenes.push({
        id,
        name: title,
        description: text(extractSection(body, '专场定位') || body).slice(0, 500),
        mode: text(data.mode || ''),
        status,
        source: 'obsidian',
        raw_json: { ...raw, body, version },
        updated_at: syncedAt,
      });
      return result;
    }

    const content = text(body);
    if (!content) {
      result.skipped = 1;
      return result;
    }
    const metadata = {
      serial: text(data.serial || data.no || data.index || data['序号']),
      planner: text(data.planner || data['策划']),
      year: text(data.year || data['年份']),
      source_from: text(data.source_from || data.sourceFrom || data.origin || data['出处']),
      domain_tags: domainTags,
      content_type: text(data.content_type || data.contentType || data.file_type || data.type_label || data['内容类型']),
      case_region: text(data.case_region || data.caseRegion || data.region || data['案例地区']),
      preview: text(data.preview || data.thumbnail || data.thumb || data['预览']),
    };
    const metadataText = [
      metadata.serial,
      metadata.planner,
      metadata.year,
      metadata.source_from,
      metadata.content_type,
      metadata.case_region,
      metadata.preview,
      metadata.domain_tags.join(' '),
    ].filter(Boolean).join('\n');
    const object = {
      id: `obsidian:${id}`,
      object_type: type,
      title,
      summary: text(data.summary || data.overview || data['概括'] || extractSection(content, '概括') || extractSection(content, '摘要') || content.slice(0, 160)),
      content: metadataText ? `${content}\n\n## 元数据\n\n${metadataText}` : content,
      resource_id: text(data.resource || data.resource_id || data.file || data.file_path || data['文件名称'] || ''),
      page_number: Number(data.page || data.page_number) || null,
      tags: [...tags, ...sceneIds],
      status,
      version,
      source: 'obsidian',
      raw_json: { ...raw, metadata },
      updated_at: syncedAt,
    };
    result.knowledgeObjects.push(object);

    const faqs = this._extractConvertibleFaqs(content);
    faqs.forEach((faq, index) => {
      const qa = {
        id: `${id}:faq:${index + 1}`,
        question: faq.question,
        answer: faq.answer,
        keywords: [...tags, ...sceneIds, title, faq.sourcePage ? `第${faq.sourcePage}页` : ''].filter(Boolean),
        tags: [...tags, ...sceneIds],
        status,
        scene_ids: sceneIds,
        source: 'obsidian',
        sourcePage: faq.sourcePage || null,
        sourceFile,
        unit_id: text(data.unit_id || data.unitId),
        raw_json: { ...raw, parent: object.id, section: '可转FAQ', sourcePage: faq.sourcePage || null },
        updated_at: syncedAt,
      };
      if (faq.intent) qa.keywords.push(faq.intent);
      qa.raw_json.intent = faq.intent || '';
      qa.raw_json.handoffRequired = faq.handoffRequired === true;
      qa.raw_json.sourceId = faq.sourceId || '';
      result.qaItems.push(qa);
      result.knowledgeObjects.push(this._qaKnowledgeObject(qa, syncedAt));
    });
    return result;
  }

  _extractTerms(query) {
    return text(query)
      .split(/[\s,，、。！？!?]+/)
      .map(item => item.trim())
      .filter(Boolean);
  }

  _makeExcerpt(item, query, maxLength = 520) {
    const source = text([
      item.title,
      item.summary,
      item.content,
      (item.tags || []).join(' '),
    ].filter(Boolean).join('\n'));
    if (!source) return '';
    const normalizedSource = normalizeForSearch(source);
    const candidates = [
      normalizeForSearch(query),
      normalizeQuestion(query),
      subjectText(query),
      ...this._extractTerms(query).map(normalizeForSearch),
    ].filter(Boolean).sort((a, b) => b.length - a.length);

    let sourceIndex = -1;
    for (const candidate of candidates) {
      const normalizedIndex = normalizedSource.indexOf(candidate);
      if (normalizedIndex < 0) continue;
      sourceIndex = this._roughOriginalIndex(source, normalizedIndex);
      break;
    }

    if (sourceIndex < 0) {
      return source.slice(0, maxLength);
    }

    const start = Math.max(0, sourceIndex - Math.floor(maxLength / 3));
    const end = Math.min(source.length, start + maxLength);
    const prefix = start > 0 ? '...' : '';
    const suffix = end < source.length ? '...' : '';
    return `${prefix}${source.slice(start, end)}${suffix}`;
  }

  _roughOriginalIndex(source, normalizedIndex) {
    let normalizedCount = 0;
    for (let i = 0; i < source.length; i += 1) {
      const ch = normalizeForSearch(source[i]);
      if (!ch) continue;
      if (normalizedCount >= normalizedIndex) return i;
      normalizedCount += ch.length;
    }
    return 0;
  }

  _extractConvertibleFaqs(content) {
    const section = extractSection(content, '可转FAQ');
    if (!section) return [];
    const rows = [];
    const parsedRows = [];
    const blocks = section.split(/(?=(?:^|\n)\s*(?:\d+[.、]\s*)?问[:：])/g);
    for (const block of blocks) {
      const questionMatch = block.match(/(?:^|\n)\s*(?:\d+[.、]\s*)?问[:：]\s*([^\n]+)/);
      const answerMatch = block.match(/\n\s*答[:：]\s*([\s\S]*?)(?=\n\s*(?:来源页|意图|需专家承接|来源标识)[:：]|\s*$)/);
      const question = text(questionMatch?.[1]).replace(/\s+/g, ' ');
      const answer = text(answerMatch?.[1]).replace(/\s+/g, ' ');
      if (!question || !answer) continue;
      const sourcePage = Number(block.match(/\n\s*来源页[:：]\s*(\d+)/)?.[1]) || null;
      const intent = text(block.match(/\n\s*意图[:：]\s*([^\n]*)/)?.[1]);
      const handoffRequired = /^(?:是|true|1)$/i.test(text(block.match(/\n\s*需专家承接[:：]\s*([^\n]*)/)?.[1]));
      const sourceId = text(block.match(/\n\s*来源标识[:：]\s*([^\n]*)/)?.[1]);
      parsedRows.push({ question, answer, sourcePage, intent, handoffRequired, sourceId });
    }
    if (parsedRows.length) return parsedRows.slice(0, MAX_CONVERTIBLE_FAQS_PER_DOCUMENT);
    const pattern = /(?:^|\n)\s*(?:\d+[.、]\s*)?问[:：]\s*([\s\S]*?)\n\s*答[:：]\s*([\s\S]*?)(?:\n\s*来源页[:：]\s*(\d+))?(?=\n\s*(?:\d+[.、]\s*)?问[:：]|\n\s*>|\s*$)/g;
    let match;
    while ((match = pattern.exec(section)) !== null) {
      const question = text(match[1]).replace(/\s+/g, ' ');
      const answer = text(match[2]).replace(/\s+/g, ' ');
      const sourcePage = Number(match[3]) || null;
      if (question && answer) rows.push({ question, answer, sourcePage });
    }
    return rows.slice(0, MAX_CONVERTIBLE_FAQS_PER_DOCUMENT);
  }

  _extractFaqTableRows(content) {
    const lines = String(content || '').split(/\r?\n/);
    const rows = [];
    for (let index = 0; index < lines.length - 1; index += 1) {
      if (!/^\s*\|/.test(lines[index]) || !/^\s*\|(?:\s*:?-{3,}:?\s*\|)+\s*$/.test(lines[index + 1])) continue;
      const headers = this._splitMarkdownTableRow(lines[index]);
      const questionIndex = headers.findIndex(header => /^(?:问题|用户可能提问)$/.test(text(header)));
      const answerIndex = headers.findIndex(header => /^(?:答案|建议回答)$/.test(text(header)));
      if (questionIndex < 0 || answerIndex < 0) continue;
      const idIndex = headers.findIndex(header => /^(?:ID|序号)$/i.test(text(header)));
      const intentIndex = headers.findIndex(header => /^意图$/.test(text(header)));
      const sourcePageIndex = headers.findIndex(header => /^(?:来源页|来源片段)$/.test(text(header)));
      for (let rowIndex = index + 2; rowIndex < lines.length && /^\s*\|/.test(lines[rowIndex]); rowIndex += 1) {
        const cells = this._splitMarkdownTableRow(lines[rowIndex]);
        const question = text(cells[questionIndex]).replace(/<br\s*\/?>/gi, '\n');
        const answer = text(cells[answerIndex]).replace(/<br\s*\/?>/gi, '\n');
        if (!question || !answer) continue;
        rows.push({
          rowId: idIndex >= 0 ? text(cells[idIndex]) : String(rows.length + 1),
          question,
          answer,
          intent: intentIndex >= 0 ? text(cells[intentIndex]) : '',
          sourcePage: sourcePageIndex >= 0 ? Number(cells[sourcePageIndex]) || null : null,
        });
      }
    }
    return rows;
  }

  _splitMarkdownTableRow(line) {
    const cells = [];
    let current = '';
    let escaped = false;
    for (const character of String(line || '').trim().replace(/^\||\|$/g, '')) {
      if (escaped) {
        current += character;
        escaped = false;
      } else if (character === '\\') {
        escaped = true;
      } else if (character === '|') {
        cells.push(current.trim());
        current = '';
      } else {
        current += character;
      }
    }
    if (escaped) current += '\\';
    cells.push(current.trim());
    return cells;
  }

  _chunkText(value, chunkSize = 700, overlap = 120) {
    const source = text(value).replace(/\r\n/g, '\n');
    if (!source) return [];
    const paragraphs = source.split(/\n{2,}/).map(item => item.trim()).filter(Boolean);
    const chunks = [];
    let current = '';
    for (const paragraph of paragraphs.length ? paragraphs : [source]) {
      if ((current + '\n\n' + paragraph).trim().length <= chunkSize) {
        current = (current ? `${current}\n\n` : '') + paragraph;
        continue;
      }
      if (current) chunks.push(current.trim());
      if (paragraph.length <= chunkSize) {
        current = current ? `${current.slice(-overlap)}\n\n${paragraph}` : paragraph;
      } else {
        for (let start = 0; start < paragraph.length; start += chunkSize - overlap) {
          chunks.push(paragraph.slice(start, start + chunkSize).trim());
        }
        current = '';
      }
    }
    if (current) chunks.push(current.trim());
    return chunks.filter(Boolean).slice(0, 20);
  }

  _scoreQa(item, normalizedQuery) {
    const question = normalizeForSearch(item.question || item.title);
    const looseQuery = normalizeQuestion(normalizedQuery);
    const looseQuestion = normalizeQuestion(item.question || item.title);
    const querySubject = subjectText(normalizedQuery);
    const questionSubject = subjectText(item.question || item.title);
    const queryIntent = questionIntent(normalizedQuery);
    const faqIntent = questionIntent(item.question || item.title);
    const answer = normalizeForSearch(item.answer || item.summary);
    const keywords = (item.keywords || item.tags || []).map(normalizeForSearch).filter(Boolean);
    let score = 0;

    if (normalizedQuery === question) score += 100;
    if (question && (normalizedQuery.includes(question) || question.includes(normalizedQuery))) score += 40;
    if (looseQuery && looseQuestion && looseQuery === looseQuestion) score += 72;
    if (looseQuery && looseQuestion && (looseQuestion.includes(looseQuery) || looseQuery.includes(looseQuestion))) score += 48;
    if (querySubject && questionSubject && (querySubject.includes(questionSubject) || questionSubject.includes(querySubject))) {
      const compatibleIntent =
        queryIntent === faqIntent ||
        (queryIntent === 'utility' && faqIntent === 'problem') ||
        (queryIntent === 'problem' && faqIntent === 'utility');
      score += compatibleIntent ? 70 : 32;
    }
    if (answer && answer.includes(normalizedQuery)) score += 8;

    for (const keyword of keywords) {
      if (normalizedQuery.includes(keyword)) score += keyword.length >= 4 ? 16 : 8;
      if (keyword.includes(normalizedQuery)) score += normalizedQuery.length >= 4 ? 14 : 6;
      if (querySubject && keyword.includes(querySubject)) score += querySubject.length >= 4 ? 18 : 8;
    }

    const queryChars = new Set([...(looseQuery || normalizedQuery)]);
    const questionChars = new Set([...(looseQuestion || question)]);
    let overlap = 0;
    for (const ch of queryChars) {
      if (questionChars.has(ch)) overlap += 1;
    }
    if (questionChars.size) score += Math.round((overlap / questionChars.size) * 18);
    if (queryChars.size) score += Math.round((overlap / queryChars.size) * 18);
    return score;
  }

  _score(item, normalizedQuery, terms) {
    const title = normalizeForSearch(item.title);
    const summary = normalizeForSearch(item.summary);
    const content = normalizeForSearch(item.content);
    const tags = normalizeForSearch((item.tags || []).join(' '));
    const resourceId = normalizeForSearch(item.resource_id || item.file_name);
    const looseQuery = normalizeQuestion(normalizedQuery);
    const querySubject = subjectText(normalizedQuery);
    const needles = [
      { value: normalizedQuery, weight: 1 },
      { value: looseQuery, weight: 0.8 },
      { value: querySubject, weight: 1.2 },
    ].filter(item => item.value && item.value.length >= 2);
    let score = 0;
    for (const needle of needles) {
      const value = needle.value;
      const weight = needle.weight;
      if (title && (title.includes(value) || value.includes(title))) score += Math.round(80 * weight);
      if (summary.includes(value)) score += Math.round(40 * weight);
      if (tags.includes(value)) score += Math.round(36 * weight);
      if (resourceId.includes(value)) score += Math.round(28 * weight);
      if (content.includes(value)) score += Math.round(16 * weight);
    }

    for (const rawTerm of terms) {
      const term = normalizeForSearch(rawTerm);
      if (!term) continue;
      if (title.includes(term)) score += term.length >= 3 ? 16 : 8;
      if (tags.includes(term)) score += term.length >= 3 ? 12 : 6;
      if (summary.includes(term)) score += term.length >= 3 ? 8 : 4;
      if (content.includes(term)) score += term.length >= 3 ? 4 : 2;
    }
    return score;
  }

  _analysisSummary(item) {
    const content = String(item.content || '');
    const aiBody = content.match(/\n*##\s*AI分析结果[\s\S]*$/)?.[0] || '';
    const aiTags = this._extractAiTags(extractSection(aiBody, '标签建议'));
    return {
      id: item.id,
      title: item.title,
      type: item.object_type,
      contentType: item.raw_json?.metadata?.content_type || item.object_type,
      status: item.status,
      source: item.source,
      file: item.raw_json?.file || '',
      resourceId: item.resource_id || '',
      serial: item.raw_json?.metadata?.serial || '',
      planner: item.raw_json?.metadata?.planner || '',
      year: item.raw_json?.metadata?.year || '',
      sourceFrom: item.raw_json?.metadata?.source_from || '',
      caseRegion: item.raw_json?.metadata?.case_region || '',
      preview: item.raw_json?.metadata?.preview || '',
      summary: text(extractSection(aiBody, '概括') || extractSection(aiBody, '摘要') || item.summary).slice(0, 240),
      analysisResult: text(extractSection(aiBody, '解析结果（qwen-vl-plus）') || extractSection(aiBody, '解析结果')).slice(0, 500),
      commentaryDraft: text(extractSection(aiBody, '讲解词草稿')).slice(0, 360),
      tags: aiTags.length ? aiTags : (item.tags || []).slice(0, 8),
      faqs: this._extractConvertibleFaqs(aiBody),
      updatedAt: item.updated_at,
    };
  }

  _extractAiTags(section) {
    return String(section || '')
      .split(/\r?\n/)
      .map(line => line.replace(/^[-*]\s*/, '').trim())
      .filter(Boolean)
      .filter(line => line !== '待人工补充')
      .slice(0, 8);
  }

  _joinCommentaryPages(pages = {}) {
    if (!pages || typeof pages !== 'object') return '';
    return Object.entries(pages)
      .sort(([a], [b]) => Number(a) - Number(b))
      .map(([page, value]) => `P${page}: ${value}`)
      .join('\n');
  }

  _appendMissedQuestionMarkdown(item) {
    const vaultPath = this.obsidianVaultPath || config.OBSIDIAN_VAULT_PATH;
    const dir = path.join(vaultPath, '06-运行日志');
    const file = path.join(dir, '未命中问题.md');
    try {
      fs.mkdirSync(dir, { recursive: true });
      if (!fs.existsSync(file)) {
        fs.writeFileSync(file, '# 未命中问题\n\n', 'utf8');
      }
      fs.appendFileSync(file, `- ${item.last_asked_at} | ${item.source || 'unknown'} | ${item.scene_id || 'global'} | ${item.question}\n`, 'utf8');
    } catch (err) {
      console.warn(`[MarkdownIndex] missed question write skipped: ${err.message}`);
    }
  }
}

export const knowledgeDb = new KnowledgeDb();
export default knowledgeDb;
