import fs from 'fs';
import path from 'path';
import { execFile } from 'child_process';
import { randomUUID } from 'crypto';
import { fileURLToPath } from 'url';
import { config } from '../config/index.js';
import { ollamaBridge } from '../api/OllamaBridge.js';
import { mediaLibrary } from '../media/MediaLibrary.js';
import { qaStore } from '../qa/QaStore.js';
import { specialConfigStore } from '../specials/SpecialConfigStore.js';
import { knowledgeDb } from '../knowledge/KnowledgeDb.js';
import { vectorKnowledgeService } from '../knowledge/VectorKnowledgeService.js';
import { ReviewOperationRegistry } from './ReviewOperationRegistry.js';
import { ReviewBatchOperationRegistry } from './ReviewBatchOperationRegistry.js';
import {
  FAQ_TARGET_COUNT,
  mergeFaqItems,
  recoverFaqItemsFromMalformedJson,
} from './FaqTargetPolicy.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const JOBS_PATH = path.join(config.DATA_DIR, 'upload-analysis-jobs.json');
const PUBLISH_SETTINGS_PATH = path.join(config.DATA_DIR, 'content-publish-settings.json');
const EXTRACTOR_PATH = path.resolve(__dirname, '../../scripts/extract-pptx-text.py');
const DOCUMENT_EXTRACTOR_PATH = path.resolve(__dirname, '../../scripts/extract-document-text.py');
const PYTHON_EXE = process.env.PPT_ANALYSIS_PYTHON || 'C:\\Progra~1\\Python312\\python.exe';
const SUPPORTED_EXTENSIONS = new Set(['.pptx', '.pdf', '.docx']);
const FAQ_ONLY_EXTENSIONS = new Set(['.pdf', '.docx']);
const BATCH_SIZE = 3;
// Production Ollama currently runs with a 4096-token context. Small FAQ batches
// leave enough room for source text and JSON output without silent truncation.
const FAQ_BATCH_TARGET = 15;
// Aim for 100 while keeping analysis bounded. Sparse pages retain their actual
// de-duplicated result after these rounds instead of manufacturing filler.
const FAQ_MAX_ROUNDS = 14;

function extractJson(value) {
  const source = String(value || '').replace(/```(?:json)?/gi, '').replace(/```/g, '').trim();
  const start = source.indexOf('{');
  if (start < 0) return null;
  let depth = 0; let quoted = false; let escaped = false;
  for (let index = start; index < source.length; index += 1) {
    const char = source[index];
    if (escaped) { escaped = false; continue; }
    if (char === '\\' && quoted) { escaped = true; continue; }
    if (char === '"') { quoted = !quoted; continue; }
    if (quoted) continue;
    if (char === '{') depth += 1;
    if (char === '}' && --depth === 0) return source.slice(start, index + 1);
  }
  return null;
}

function safeSegment(value) {
  return String(value || '').replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').replace(/[. ]+$/g, '').slice(0, 100) || 'uploaded-ppt';
}

function markdownCell(value) {
  return String(value || '')
    .replace(/\\/g, '\\\\')
    .replace(/\|/g, '\\|')
    .replace(/\r?\n/g, '<br>')
    .trim();
}

export class UploadAnalysisService {
  constructor() {
    this.jobs = [];
    this.running = false;
    this.reviewOperations = new ReviewOperationRegistry();
    this.reviewBatchOperations = new ReviewBatchOperationRegistry();
    this._load();
    for (const job of this.jobs) {
      if (job.status === 'RUNNING') Object.assign(job, { status: 'QUEUED', phase: 'queued', recoveredAt: new Date().toISOString() });
    }
    this._save();
    setTimeout(() => this._drain(), 1000);
  }

  getPublishSettings() {
    try {
      const parsed = JSON.parse(fs.readFileSync(PUBLISH_SETTINGS_PATH, 'utf8'));
      return { mode: parsed.mode === 'AUTO_PUBLISH' ? 'AUTO_PUBLISH' : 'REVIEW_REQUIRED', updatedAt: parsed.updatedAt || null };
    } catch {
      return { mode: 'REVIEW_REQUIRED', updatedAt: null };
    }
  }

  updatePublishSettings(mode) {
    const settings = { mode: mode === 'AUTO_PUBLISH' ? 'AUTO_PUBLISH' : 'REVIEW_REQUIRED', updatedAt: new Date().toISOString() };
    fs.mkdirSync(path.dirname(PUBLISH_SETTINGS_PATH), { recursive: true });
    fs.writeFileSync(PUBLISH_SETTINGS_PATH, JSON.stringify(settings, null, 2), 'utf8');
    return settings;
  }

  listReviews() {
    return mediaLibrary.getAll()
      .filter(file => file.publicationStatus === 'PENDING_REVIEW')
      .sort((a, b) => Date.parse(b.reviewRequestedAt || b.analysisCompletedAt || 0) - Date.parse(a.reviewRequestedAt || a.analysisCompletedAt || 0));
  }

  async review(fileName, decision) {
    const name = String(fileName || '').trim();
    const action = String(decision || '').trim().toLowerCase();
    const operationKey = `${action}:${name}`;
    return this.reviewOperations.run(operationKey, () => this._reviewOnce(name, action));
  }

  async _reviewOnce(fileName, decision) {
    const file = mediaLibrary.getAll().find(item => item.name === fileName);
    if (!file) throw new Error('review file not found');
    if (decision === 'seen') {
      mediaLibrary.updateContentStatus(file.name, file.contentStatus, { isNew: false });
      return mediaLibrary.getAll().find(item => item.name === file.name);
    }
    if (decision === 'approve' && file.publicationStatus === 'PUBLISHED' && file.reviewStatus === 'APPROVED') {
      return file;
    }
    if (decision === 'reject' && file.publicationStatus === 'REJECTED' && file.reviewStatus === 'REJECTED') {
      return file;
    }
    if (file.publicationStatus !== 'PENDING_REVIEW') throw new Error('file is not pending review');
    const now = new Date().toISOString();
    if (decision === 'approve') {
      this._applyReviewState(file, decision, now);
      knowledgeDb.syncFromCurrentStores({ mediaLibrary, qaStore, specialConfigStore });
      knowledgeDb.syncObsidian();
      const vector = await vectorKnowledgeService.rebuildFromKnowledgeDb();
      if (!vector?.ok && !vector?.skipped) throw new Error(`向量索引失败：${vector?.error || 'unknown error'}`);
    } else if (decision === 'reject') {
      this._applyReviewState(file, decision, now);
      knowledgeDb.syncFromCurrentStores({ mediaLibrary, qaStore, specialConfigStore });
      knowledgeDb.syncObsidian();
    } else {
      throw new Error('unsupported review decision');
    }
    return mediaLibrary.getAll().find(item => item.name === file.name);
  }

  startBatchReview(fileNames, decision = 'approve') {
    const names = [...new Set((fileNames || []).map(name => String(name || '').trim()).filter(Boolean))];
    const action = String(decision || '').trim().toLowerCase();
    if (action !== 'approve') throw new Error('batch review currently supports approve only');
    if (!names.length) throw new Error('at least one review file is required');
    if (names.length > 100) throw new Error('batch review supports at most 100 files');
    return this.reviewBatchOperations.start({ fileNames: names, decision: action }, update => (
      this._runBatchReview(names, action, update)
    ));
  }

  getBatchReviewOperation(id) {
    return this.reviewBatchOperations.get(id);
  }

  async _runBatchReview(fileNames, decision, update) {
    const currentFiles = mediaLibrary.getAll();
    const files = fileNames.map(name => {
      const file = currentFiles.find(item => item.name === name);
      if (!file) throw new Error(`待审核资料不存在：${name}`);
      if (file.publicationStatus !== 'PENDING_REVIEW') throw new Error(`资料已不在待审核状态：${name}`);
      return file;
    });

    const now = new Date().toISOString();
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      update({
        phase: 'publishing',
        message: `正在发布 ${index + 1}/${files.length}：${file.name}`,
        current: index,
        percent: Math.max(5, Math.round(5 + (index / files.length) * 65)),
      });
      this._applyReviewState(file, decision, now);
      update({ current: index + 1, percent: Math.round(5 + ((index + 1) / files.length) * 65) });
    }

    update({ phase: 'knowledge_sync', message: '正在同步正式知识库', percent: 76 });
    knowledgeDb.syncFromCurrentStores({ mediaLibrary, qaStore, specialConfigStore });
    update({ phase: 'obsidian_sync', message: '正在同步 Obsidian 知识库', percent: 84 });
    knowledgeDb.syncObsidian();
    update({ phase: 'vector_index', message: '正在重建向量索引，请稍候', percent: 90 });
    const vector = await vectorKnowledgeService.rebuildFromKnowledgeDb();
    if (!vector?.ok && !vector?.skipped) throw new Error(`向量索引失败：${vector?.error || 'unknown error'}`);
    update({ phase: 'finalizing', message: '正在确认上架结果', percent: 98 });
    return mediaLibrary.getAll().filter(item => fileNames.includes(item.name));
  }

  _applyReviewState(file, decision, now) {
    if (decision === 'approve') {
      this._setGeneratedMarkdownStatus(file.analysisJobId, 'published');
      mediaLibrary.updateContentStatus(file.name, 'AI_READY', {
        status: 'published', publicationStatus: 'PUBLISHED', reviewStatus: 'APPROVED',
        publishedAt: now, isNew: true, analysisError: null,
      });
      return;
    }
    if (decision === 'reject') {
      this._setGeneratedMarkdownStatus(file.analysisJobId, 'rejected');
      mediaLibrary.updateContentStatus(file.name, 'REVIEW_REJECTED', {
        status: 'rejected', publicationStatus: 'REJECTED', reviewStatus: 'REJECTED', rejectedAt: now, isNew: false,
      });
      return;
    }
    throw new Error('unsupported review decision');
  }

  enqueue(file) {
    const fileName = String(file?.name || '').trim();
    const extension = path.extname(fileName).toLowerCase();
    const supported = SUPPORTED_EXTENSIONS.has(extension);
    const faqOnly = FAQ_ONLY_EXTENSIONS.has(extension);
    const job = {
      id: randomUUID(), fileName, status: supported ? 'QUEUED' : 'FAILED', phase: supported ? 'queued' : 'failed',
      analysisMode: faqOnly ? 'faq_only' : 'commentary_faq', faqOnly,
      model: config.OLLAMA.model, publishMode: this.getPublishSettings().mode, totalSlides: 0, processedSlides: 0,
      generatedFaqs: 0, generatedFaqsByPage: {}, faqTargetCount: 0, warnings: [], attempts: 0,
      createdAt: new Date().toISOString(), startedAt: null, completedAt: supported ? null : new Date().toISOString(),
      error: supported ? null : '本地AI分析当前仅支持 PPTX、PDF 和 DOCX 文件',
    };
    this.jobs.push(job);
    this.jobs = this.jobs.slice(-200);
    this._save();
    mediaLibrary.updateContentStatus(fileName, supported ? 'AI_QUEUED' : 'AI_FAILED', {
      analysisJobId: job.id, analysisError: job.error, analysisProgress: this._progress(job), publishMode: job.publishMode,
      generatedFaqCount: 0,
      status: supported ? 'processing' : 'failed',
      publicationStatus: supported ? 'PROCESSING' : 'FAILED',
      reviewStatus: supported ? 'NOT_STARTED' : 'FAILED',
      analysisCompletedAt: null,
      reviewRequestedAt: null,
      publishedAt: null,
      rejectedAt: null,
      isNew: false,
      analysisMode: job.analysisMode,
      faqOnly,
      clientDisplay: !faqOnly,
    });
    this._drain();
    return this.get(job.id);
  }

  get(id) { return this.jobs.find(job => job.id === String(id || '')) || null; }
  list(limit = 50) { return this.jobs.slice(-Math.max(1, Number(limit) || 50)).reverse(); }

  retry(id) {
    const job = this.get(id);
    if (!job || job.status !== 'FAILED' || !SUPPORTED_EXTENSIONS.has(path.extname(job.fileName).toLowerCase())) return job;
    Object.assign(job, {
      status: 'QUEUED', phase: 'queued', error: null, processedSlides: 0,
      generatedFaqs: 0, generatedFaqsByPage: {}, faqTargetCount: 0,
      warnings: [], startedAt: null, completedAt: null,
    });
    this._publishProgress(job, 'AI_QUEUED', {
      analysisError: null,
      status: 'processing',
      publicationStatus: 'PROCESSING',
      reviewStatus: 'NOT_STARTED',
      analysisCompletedAt: null,
      reviewRequestedAt: null,
      publishedAt: null,
      rejectedAt: null,
      isNew: false,
    });
    this._drain();
    return this.get(id);
  }

  assertFileRemovable(fileName) {
    const target = String(fileName || '').trim();
    const running = this.jobs.find(job => job.fileName === target && job.status === 'RUNNING');
    if (running) {
      const error = new Error('文件正在解析，请等待当前分析结束后再删除');
      error.code = 'FILE_ANALYSIS_RUNNING';
      throw error;
    }
    return true;
  }

  removeFileArtifacts(fileName, analysisJobId = '') {
    const target = String(fileName || '').trim();
    this.assertFileRemovable(target);

    const relatedJobs = this.jobs.filter(job => job.fileName === target);
    const jobIds = new Set([
      String(analysisJobId || '').trim(),
      ...relatedJobs.map(job => String(job.id || '').trim()),
    ].filter(Boolean));
    this.jobs = this.jobs.filter(job => job.fileName !== target);
    this._save();

    const uploadRoot = path.join(config.OBSIDIAN_VAULT_PATH, '10-units', '上传资料');
    const removedDirectories = [];
    if (fs.existsSync(uploadRoot)) {
      for (const entry of fs.readdirSync(uploadRoot, { withFileTypes: true })) {
        if (!entry.isDirectory()) continue;
        const fullPath = path.join(uploadRoot, entry.name);
        const markdownPath = path.join(fullPath, '本地AI分析.md');
        const idMatch = [...jobIds].some(id => entry.name.startsWith(`${id}-`));
        let resourceMatch = false;
        if (!idMatch && fs.existsSync(markdownPath)) {
          const markdown = fs.readFileSync(markdownPath, 'utf8');
          const resourceLine = markdown.split(/\r?\n/).find(line => /^resource:\s*/.test(line.trim()));
          const resource = String(resourceLine || '')
            .replace(/^\s*resource:\s*/, '')
            .replace(/^['"]|['"]$/g, '')
            .replace(/\\"/g, '"')
            .trim();
          resourceMatch = resource === target;
        }
        if (!idMatch && !resourceMatch) continue;
        fs.rmSync(fullPath, { recursive: true, force: true });
        removedDirectories.push(entry.name);
      }
    }

    return {
      removedJobs: relatedJobs.length,
      removedJobIds: [...jobIds],
      removedDirectories,
    };
  }

  async _drain() {
    if (this.running) return;
    const job = this.jobs.find(item => item.status === 'QUEUED');
    if (!job) return;
    this.running = true;
    try { await this._run(job); }
    finally { this.running = false; setTimeout(() => this._drain(), 10); }
  }

  async _run(job) {
    const extension = path.extname(job.fileName).toLowerCase();
    const faqOnly = job.faqOnly === true || FAQ_ONLY_EXTENSIONS.has(extension);
    Object.assign(job, {
      status: 'RUNNING',
      phase: 'extract',
      analysisMode: faqOnly ? 'faq_only' : 'commentary_faq',
      faqOnly,
      startedAt: new Date().toISOString(),
      completedAt: null,
      error: null,
      attempts: Number(job.attempts || 0) + 1,
    });
    this._publishProgress(job, 'AI_ANALYZING');
    try {
      const units = await this._extractUnits(path.join(config.MEDIA_DIR, job.fileName));
      if (!units.length) {
        throw new Error(faqOnly
          ? '文档中没有提取到可用于FAQ的文字；扫描版PDF请先进行OCR'
          : 'PPTX 中没有可分析的页面');
      }
      job.totalSlides = units.length;
      job.phase = 'analyze';
      const commentaryPages = {};
      let faqs = [];
      const readableUnits = units.filter(unit => String(unit.text || '').trim());
      job.faqTargetCount = faqOnly ? FAQ_TARGET_COUNT : readableUnits.length * FAQ_TARGET_COUNT;
      job.generatedFaqsByPage = {};
      const emptyPages = units.filter(unit => !String(unit.text || '').trim()).map(unit => unit.page);
      if (emptyPages.length) job.warnings.push(`第 ${emptyPages.join('、')} 个内容单元未提取到文字，未生成FAQ`);

      if (!faqOnly) {
        for (const unit of units) {
          if (!String(unit.text || '').trim()) commentaryPages[String(unit.page)] = '本页未提取到可用于本地AI分析的文字内容，可能包含图片或嵌入视频，请人工补充。';
        }
        for (let offset = 0; offset < readableUnits.length; offset += BATCH_SIZE) {
          const batch = readableUnits.slice(offset, offset + BATCH_SIZE);
          const generated = await this._generateBatch(job.fileName, batch);
          for (const page of generated.pages) {
            commentaryPages[String(page.page)] = [`【精炼版】\n${page.brief}`, `【标准版】\n${page.standard}`, `【详细版】\n${page.detailed}`].join('\n\n');
          }
          job.processedSlides += batch.length;
          this._publishProgress(job, 'AI_ANALYZING');
        }
      }
      job.processedSlides = units.length;
      job.phase = 'faq_generate';
      this._publishProgress(job, 'AI_ANALYZING');
      faqs = faqOnly
        ? await this._generateDocumentFaqs(job.fileName, readableUnits, job)
        : await this._generatePageFaqs(job.fileName, readableUnits, job);
      job.generatedFaqs = faqs.length;
      job.phase = 'write';
      this._publishProgress(job, 'AI_WRITING');
      this._writeGeneratedMarkdown(job, units, commentaryPages, faqs, { faqOnly });
      if (!faqOnly) {
        mediaLibrary.updateCommentary(job.fileName, Object.keys(commentaryPages).map(Number).sort((a, b) => a - b).map(page => commentaryPages[String(page)]).join('&&'), { commentaryPages, slideCount: units.length });
      }

      job.phase = 'index';
      this._publishProgress(job, 'AI_INDEXING', {
        status: job.publishMode === 'REVIEW_REQUIRED' ? 'reviewing' : 'published',
        publicationStatus: job.publishMode === 'REVIEW_REQUIRED' ? 'PENDING_REVIEW' : 'PROCESSING',
        reviewStatus: job.publishMode === 'REVIEW_REQUIRED' ? 'PENDING' : 'NOT_REQUIRED',
        publishMode: job.publishMode,
        analysisMode: job.analysisMode,
        faqOnly,
        clientDisplay: !faqOnly,
        analysisUnitCount: units.length,
      });
      knowledgeDb.syncFromCurrentStores({ mediaLibrary, qaStore, specialConfigStore });
      knowledgeDb.syncObsidian();
      const completedAt = new Date().toISOString();
      if (job.publishMode === 'REVIEW_REQUIRED') {
        Object.assign(job, { status: 'COMPLETED', phase: 'review_pending', nextAction: 'OPEN_REVIEW', completedAt });
        this._publishProgress(job, 'PENDING_REVIEW', {
          generatedFaqCount: job.generatedFaqs, analysisError: null, status: 'reviewing',
          publicationStatus: 'PENDING_REVIEW', reviewStatus: 'PENDING', publishMode: job.publishMode,
          analysisCompletedAt: completedAt, reviewRequestedAt: completedAt, isNew: false,
        });
      } else {
        const vector = await vectorKnowledgeService.rebuildFromKnowledgeDb();
        if (!vector?.ok && !vector?.skipped) throw new Error(`向量索引失败：${vector?.error || 'unknown error'}`);
        Object.assign(job, { status: 'COMPLETED', phase: 'completed', nextAction: faqOnly ? 'OPEN_FAQ' : 'OPEN_FILE', completedAt });
        this._publishProgress(job, 'AI_READY', {
          generatedFaqCount: job.generatedFaqs, analysisError: null, status: 'published',
          publicationStatus: 'PUBLISHED', reviewStatus: 'NOT_REQUIRED', publishMode: job.publishMode,
          analysisCompletedAt: completedAt, publishedAt: completedAt, isNew: true,
          analysisMode: job.analysisMode, faqOnly, clientDisplay: !faqOnly, analysisUnitCount: units.length,
        });
      }
    } catch (error) {
      Object.assign(job, { status: 'FAILED', phase: 'failed', error: error.message, completedAt: new Date().toISOString() });
      try {
        this._publishProgress(job, 'AI_FAILED', {
          analysisError: job.error,
          status: 'failed',
          publicationStatus: 'FAILED',
          reviewStatus: 'FAILED',
          analysisCompletedAt: job.completedAt,
          isNew: false,
          analysisMode: job.analysisMode,
          faqOnly,
          clientDisplay: !faqOnly,
        });
      } catch {}
    }
    this._save();
  }

  _progress(job) {
    if (job.phase === 'faq_generate') {
      return {
        phase: job.phase,
        current: Number(job.generatedFaqs || 0),
        total: Number(job.faqTargetCount || (Number(job.totalSlides || 0) * FAQ_TARGET_COUNT)),
        byPage: job.generatedFaqsByPage || {},
      };
    }
    return { phase: job.phase, current: Number(job.processedSlides || 0), total: Number(job.totalSlides || 0) };
  }
  _publishProgress(job, contentStatus, extra = {}) {
    this._save();
    mediaLibrary.updateContentStatus(job.fileName, contentStatus, { analysisJobId: job.id, analysisProgress: this._progress(job), ...extra });
  }

  _writeGeneratedMarkdown(job, slides, commentaryPages, faqs, options = {}) {
    const baseName = path.basename(job.fileName, path.extname(job.fileName));
    const dir = path.join(config.OBSIDIAN_VAULT_PATH, '10-units', '上传资料', `${job.id}-${safeSegment(baseName)}`);
    fs.mkdirSync(dir, { recursive: true });
    const target = path.join(dir, '本地AI分析.md');
    const temp = `${target}.${process.pid}.tmp`;
    const publicationStatus = job.publishMode === 'REVIEW_REQUIRED' ? 'reviewing' : 'published';
    let content;
    if (options.faqOnly) {
      const faqRows = faqs.filter(faq => faq.question && faq.answer).map((faq, index) => (
        `| ${index + 1} | ${markdownCell(faq.question)} | ${markdownCell(faq.answer)} | ${Number(faq.sourcePage) || 1} | ${markdownCell(faq.intent || '')} |`
      ));
      content = [
        '---',
        'type: faq_bank',
        `status: ${publicationStatus}`,
        `title: "${safeSegment(baseName)}"`,
        `resource: "${job.fileName.replace(/"/g, '\\"')}"`,
        `source_job_id: "${job.id}"`,
        `generated_by: "ollama/${job.model}"`,
        'faq_only: true',
        '---',
        '',
        `# ${baseName} FAQ`,
        '',
        '| ID | 问题 | 答案 | 来源片段 | 意图 |',
        '| --- | --- | --- | --- | --- |',
        ...faqRows,
        '',
      ].join('\n');
    } else {
      const faqText = faqs.filter(faq => faq.question && faq.answer).map((faq, index) => (
        `${index + 1}. 问：${faq.question}\n   答：${faq.answer}\n   来源页：${Number(faq.sourcePage) || 1}\n   意图：${faq.intent || ''}\n   需专家承接：${faq.handoffRequired ? '是' : '否'}\n   来源标识：${faq.sourceId || `${job.fileName}#page-${Number(faq.sourcePage) || 1}`}`
      )).join('\n\n');
      const pagesText = slides.map(slide => `## 第${slide.page}页 ${slide.title || ''}\n\n### 页面原文\n\n${slide.text || '（未提取到文字）'}\n\n### 本地AI讲解词\n\n${commentaryPages[String(slide.page)] || ''}`).join('\n\n');
      content = ['---', 'type: knowledge', `status: ${publicationStatus}`, `title: "${safeSegment(baseName)}"`, `resource: "${job.fileName.replace(/"/g, '\\"')}"`, `source_job_id: "${job.id}"`, `generated_by: "ollama/${job.model}"`, '---', '', `# ${baseName}`, '', pagesText, '', '## 可转FAQ', '', faqText || '（本次未生成有依据的FAQ）', ''].join('\n');
    }
    fs.writeFileSync(temp, content, 'utf8');
    fs.renameSync(temp, target);
  }

  _setGeneratedMarkdownStatus(jobId, status) {
    const uploadRoot = path.join(config.OBSIDIAN_VAULT_PATH, '10-units', '上传资料');
    if (!fs.existsSync(uploadRoot)) throw new Error('generated upload directory not found');
    const dir = fs.readdirSync(uploadRoot, { withFileTypes: true }).find(entry => entry.isDirectory() && entry.name.startsWith(`${jobId}-`));
    if (!dir) throw new Error('generated analysis directory not found');
    const target = path.join(uploadRoot, dir.name, '本地AI分析.md');
    const markdown = fs.readFileSync(target, 'utf8');
    const updated = markdown.replace(/^status:\s*[^\r\n]+/m, `status: ${status}`);
    if (updated === markdown) throw new Error('generated markdown status not found');
    fs.writeFileSync(target, updated, 'utf8');
  }

  _extractUnits(filePath) {
    return new Promise((resolve, reject) => {
      const extension = path.extname(filePath).toLowerCase();
      const extractorPath = extension === '.pptx' ? EXTRACTOR_PATH : DOCUMENT_EXTRACTOR_PATH;
      execFile(PYTHON_EXE, [extractorPath, filePath], {
        encoding: 'utf8', maxBuffer: 20 * 1024 * 1024, timeout: 120000, windowsHide: true,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
      }, (error, stdout, stderr) => {
        if (error) return reject(new Error(`文件文本提取失败：${stderr || error.message}`));
        try {
          const parsed = JSON.parse(String(stdout || '').replace(/^\uFEFF/, ''));
          resolve(Array.isArray(parsed.slides) ? parsed.slides : Array.isArray(parsed.units) ? parsed.units : []);
        } catch (parseError) {
          reject(new Error(`文件文本解析失败：${parseError.message}`));
        }
      });
    });
  }

  async _generateBatch(fileName, slides) {
    const input = slides.map(slide => ({ page: slide.page, title: String(slide.title || '').slice(0, 200), text: String(slide.text || '').slice(0, 3000) }));
    const result = await ollamaBridge.chat({
      text: [
        '根据以下PPT页面原文生成展厅讲解内容。只能使用原文事实，不得补充外部数字或结论。',
        '每页输出精炼版(30-60字)、标准版(100-180字)、详细版(360-600字)。',
        '标准版和详细版应尽量形成“客户问题→中国移动能力→场景价值→下一步交流”的闭环；原文没有对应事实时应省略，绝不能补造。',
        '输出必须是自然、可直接播报的前台话术，不出现“PPT、材料、知识包、来源状态、置信度”等后台表达。',
        '严格输出JSON：{"pages":[{"page":1,"brief":"","standard":"","detailed":""}]}',
        `文件：${fileName}`,
        `页面：${JSON.stringify(input)}`,
      ].join('\n'),
      systemPrompt: '你是展厅PPT讲解词生成器。严格依据输入原文，只输出JSON。', preserveRaw: true, temperature: 0.2, maxTokens: 3000, timeoutMs: 180000,
    });
    if (result.error) throw new Error(`本地AI调用失败：${result.error}`);
    const json = extractJson(result.reply);
    if (!json) throw new Error('本地AI未返回有效JSON');
    let parsed;
    try { parsed = JSON.parse(json); } catch (error) { throw new Error(`本地AI JSON解析失败：${error.message}`); }
    const expected = new Set(slides.map(slide => Number(slide.page)));
    const normalized = (Array.isArray(parsed.pages) ? parsed.pages : []).map(page => ({
      page: Number(page.page), brief: String(page.brief || '').trim(), standard: String(page.standard || '').trim(), detailed: String(page.detailed || '').trim(),
    })).filter(page => expected.has(page.page) && page.brief && page.standard && page.detailed);
    if (normalized.length !== slides.length) throw new Error('本地AI返回的页面数量或讲解词不完整');
    return { pages: normalized.sort((a, b) => a.page - b.page) };
  }

  async _generatePageFaqs(fileName, slides, job) {
    if (!slides.length) throw new Error('PPTX 中没有可用于生成FAQ的文字内容');
    const allFaqs = [];
    for (const slide of slides) {
      let pageFaqs = [];
      for (let round = 0; round < FAQ_MAX_ROUNDS && pageFaqs.length < FAQ_TARGET_COUNT; round += 1) {
        const needed = Math.min(FAQ_BATCH_TARGET, FAQ_TARGET_COUNT - pageFaqs.length);
        // When only one or two rows are missing, asking the model for exactly
        // that number makes a duplicate response very likely. Oversample a
        // normal batch and let mergeFaqItems select only the unique tail.
        const requested = needed < FAQ_BATCH_TARGET ? FAQ_BATCH_TARGET : needed;
        let generated = [];
        try {
          generated = await this._generateFaqBatch(fileName, [slide], {
            count: requested,
            round,
            // Tail-fill requests must see every existing question on the page;
            // otherwise the model can keep returning an older duplicate and
            // leave the page permanently at 98/100 or 99/100.
            existingQuestions: pageFaqs.map(item => item.question).slice(-FAQ_TARGET_COUNT),
          });
        } catch (error) {
          job.warnings.push(`第 ${slide.page} 页 FAQ 第 ${round + 1} 批生成异常，已自动继续：${error.message}`);
        }
        pageFaqs = mergeFaqItems(pageFaqs, generated, [Number(slide.page)], FAQ_TARGET_COUNT);
        job.generatedFaqsByPage[String(slide.page)] = pageFaqs.length;
        job.generatedFaqs = allFaqs.length + pageFaqs.length;
        this._publishProgress(job, 'AI_ANALYZING');
      }
      if (!pageFaqs.length) throw new Error(`第 ${slide.page} 页未生成有效FAQ`);
      if (pageFaqs.length < FAQ_TARGET_COUNT) {
        job.warnings.push(`第 ${slide.page} 页原文信息有限，保留 ${pageFaqs.length} 条去重后的有效FAQ，不再机械补齐到 ${FAQ_TARGET_COUNT} 条`);
      }
      allFaqs.push(...pageFaqs);
    }
    return allFaqs;
  }

  async _generateDocumentFaqs(fileName, units, job) {
    if (!units.length) throw new Error('文档中没有可用于生成FAQ的文字内容');
    const sampleLimit = Math.min(18, units.length);
    const sampled = sampleLimit === units.length
      ? units
      : Array.from({ length: sampleLimit }, (_, index) => units[Math.round((index * (units.length - 1)) / (sampleLimit - 1))]);
    const allowedPages = sampled.map(unit => Number(unit.page));
    let faqs = [];
    for (let round = 0; round < FAQ_MAX_ROUNDS && faqs.length < FAQ_TARGET_COUNT; round += 1) {
      const needed = Math.min(FAQ_BATCH_TARGET, FAQ_TARGET_COUNT - faqs.length);
      const requested = needed < FAQ_BATCH_TARGET ? FAQ_BATCH_TARGET : needed;
      const batch = Array.from({ length: Math.min(BATCH_SIZE, sampled.length) }, (_, index) => (
        sampled[(round * BATCH_SIZE + index) % sampled.length]
      ));
      let generated = [];
      try {
        generated = await this._generateFaqBatch(fileName, batch, {
          count: requested,
          round,
          existingQuestions: faqs.map(item => item.question).slice(-FAQ_TARGET_COUNT),
          completeAnswers: true,
        });
      } catch (error) {
        job.warnings.push(`文档FAQ第 ${round + 1} 批生成异常，已自动继续：${error.message}`);
      }
      faqs = mergeFaqItems(faqs, generated, allowedPages, FAQ_TARGET_COUNT);
      job.generatedFaqs = faqs.length;
      job.generatedFaqsByPage = faqs.reduce((counts, faq) => {
        const key = String(Number(faq.sourcePage) || 1);
        counts[key] = (counts[key] || 0) + 1;
        return counts;
      }, {});
      this._publishProgress(job, 'AI_ANALYZING');
    }
    if (!faqs.length) throw new Error('文档未生成有效FAQ');
    if (faqs.length < FAQ_TARGET_COUNT) {
      job.warnings.push(`文档保留 ${faqs.length} 条去重后的有效FAQ，不机械补齐重复问题`);
    }
    return faqs;
  }

  async _generateFaqBatch(fileName, slides, { count, round, existingQuestions, completeAnswers = false }) {
    const source = slides.map(slide => ({
      page: Number(slide.page),
      title: String(slide.title || '').slice(0, 160),
      text: String(slide.text || '').slice(0, completeAnswers ? 900 : 700),
    }));
    const dimensions = ['核心事实与定义', '功能与能力', '应用场景', '流程与方法', '价值与效果', '数据与指标', '比较与关系', '现场讲解追问'];
    dimensions.push('边界与人工承接');
    const result = await ollamaBridge.chat({
      text: [
        `为文件生成恰好 ${count} 条新的FAQ。`,
        '硬性要求：每条答案必须能从给定页面原文直接得到；不得使用外部知识，不得虚构数字、效果、案例或结论。',
        '问题应简洁自然，每个问题不超过24个中文字符，并覆盖不同表达和理解角度，但不得仅替换同义词制造重复问题。',
        '问题必须有明确主语，优先使用文件名、主题名、产品名或实体名；禁止使用“这个文件、该材料、本页、它说明了什么”等脱离上下文就无法理解的指代式问法。',
        `若询问文件整体内容，应写成“${String(fileName).replace(/\.[^.]+$/, '')}说明了什么？”这类包含明确主题的问法。`,
        '答案要比问题丰富：在原文允许时组织为60-140字、2-4个分句，完整保留条件、范围、数字、功能和结论；不得为了简短而遗漏关键信息。',
        `本轮优先角度：${dimensions[round % dimensions.length]}。`,
        'sourcePage 必须填写答案所依据的页面编号。',
        `不要重复这些已有问题：${JSON.stringify(existingQuestions)}`,
        '严格输出JSON：{"faqs":[{"question":"","answer":"","keywords":[],"sourcePage":1}]}',
        `文件：${fileName}`,
        `页面原文：${JSON.stringify(source)}`,
        'FAQ生成先判断意图，再组织答案；intent 必须是 positioning、technical_term、painpoint、value、audience、provider_capability、capability、case_detail、metric、landing、comparison、boundary、handoff、online_search 之一。',
        '答案长度按意图控制：定位/术语/受众不超过120字；痛点/价值/能力/指标不超过150字；落地/比较/边界/人工承接不超过160字；案例细节不超过180字。若与前述长度要求冲突，以本条为准。',
        '涉及价格、工期、合同、接口、安全、未发布信息或强效果承诺时，intent 应为 boundary 或 handoff；只给评估框架或建议转专家，不得编造确定答案。',
        '每条必须给出 handoffRequired；需要销售、技术或项目专家确认时为 true，否则为 false。',
        `每条 sourceId 使用“${fileName}#page-来源页码”格式，必须与 sourcePage 一致。`,
        '严格输出JSON：{"faqs":[{"question":"","answer":"","keywords":[],"sourcePage":1,"intent":"capability","handoffRequired":false,"sourceId":"文件名#page-1"}]}',
        '不要输出“PPT、材料、知识包、来源状态、置信度”等后台表达；答案必须是可直接面对客户的自然话术。',
        '同一事实不得仅靠换同义词重复生成问题；问题意图或答案事实相近时应换用其他维度。',
      ].join('\n'),
      systemPrompt: '你是企业知识库FAQ生成器。严格依据页面原文，只输出JSON；必须完成指定数量。',
      preserveRaw: true,
      temperature: 0.35,
      maxTokens: 2100,
      timeoutMs: 180000,
    });
    if (result.error) throw new Error(`FAQ生成调用失败：${result.error}`);
    const json = extractJson(result.reply);
    if (!json) {
      const recovered = recoverFaqItemsFromMalformedJson(result.reply);
      if (recovered.length) return recovered;
      throw new Error('FAQ生成未返回有效JSON');
    }
    try {
      const parsed = JSON.parse(json);
      return Array.isArray(parsed.faqs) ? parsed.faqs : [];
    } catch (error) {
      const recovered = recoverFaqItemsFromMalformedJson(json);
      if (recovered.length) return recovered;
      throw new Error(`FAQ生成JSON解析失败：${error.message}`);
    }
  }

  _load() {
    try { const parsed = JSON.parse(fs.readFileSync(JOBS_PATH, 'utf8')); this.jobs = Array.isArray(parsed.jobs) ? parsed.jobs : []; }
    catch { this.jobs = []; }
  }
  _save() {
    fs.mkdirSync(path.dirname(JOBS_PATH), { recursive: true });
    const temp = `${JOBS_PATH}.${process.pid}.tmp`;
    fs.writeFileSync(temp, JSON.stringify({ jobs: this.jobs, updatedAt: new Date().toISOString() }, null, 2), 'utf8');
    fs.renameSync(temp, JOBS_PATH);
  }
}

export const uploadAnalysisService = new UploadAnalysisService();
export default uploadAnalysisService;
