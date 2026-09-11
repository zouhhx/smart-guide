import { config } from '../config/index.js';
import { knowledgeDb } from './KnowledgeDb.js';

export class VectorKnowledgeService {
  constructor() {
    this.baseUrl = String(config.VECTOR_SEARCH.serviceUrl || '').replace(/\/+$/, '');
    this.enabled = Boolean(config.VECTOR_SEARCH.enabled && this.baseUrl);
    this._lastSync = null;
    this._lastError = '';
    this._unavailableUntil = 0;
    this._rebuildChain = Promise.resolve();
  }

  async status() {
    if (!this.enabled) {
      return { enabled: false, online: false, error: 'vector search disabled' };
    }
    try {
      const data = await this._request('/health', { method: 'GET' });
      return {
        enabled: true,
        online: true,
        lastSync: this._lastSync,
        lastError: this._lastError,
        ...data,
      };
    } catch (err) {
      this._lastError = err.message;
      return { enabled: true, online: false, lastSync: this._lastSync, error: err.message };
    }
  }

  async rebuildFromKnowledgeDb() {
    const run = () => this._rebuildNow();
    this._rebuildChain = this._rebuildChain.then(run, run);
    return this._rebuildChain;
  }

  async upsertFaq(item) {
    if (!this.enabled) {
      return { ok: false, skipped: true, reason: 'vector search disabled' };
    }
    const document = knowledgeDb.buildVectorFaqDocument(item);
    if (!document) throw new Error('invalid FAQ vector document');
    try {
      const result = await this._request('/upsert', {
        method: 'POST',
        body: { documents: [document] },
        timeoutMs: Math.max(config.VECTOR_SEARCH.timeoutMs || 2500, 30000),
      });
      this._lastSync = new Date().toISOString();
      this._lastError = '';
      return { ok: true, documents: 1, ...result };
    } catch (err) {
      this._lastError = err.message;
      return { ok: false, documents: 1, error: err.message };
    }
  }

  async _rebuildNow() {
    if (!this.enabled) return { ok: false, skipped: true, reason: 'vector search disabled' };
    const documents = knowledgeDb.getVectorDocuments();
    try {
      await this._request('/reset', { method: 'POST', body: {} });
      const result = await this._request('/upsert', {
        method: 'POST',
        body: { documents },
        timeoutMs: Math.max(config.VECTOR_SEARCH.timeoutMs || 2500, 300000),
      });
      this._lastSync = new Date().toISOString();
      this._lastError = '';
      return { ok: true, documents: documents.length, ...result };
    } catch (err) {
      this._lastError = err.message;
      console.warn(`[VectorSearch] rebuild skipped: ${err.message}`);
      return { ok: false, documents: documents.length, error: err.message };
    }
  }

  async searchFaq(query, options = {}) {
    const rows = await this.search(query, {
      ...options,
      objectTypes: ['faq'],
      limit: options.limit || 8,
      minSimilarity: options.minSimilarity ?? config.VECTOR_SEARCH.faqMinSimilarity,
      metadataFilters: options.unitId ? { unit_id: options.unitId } : options.metadataFilters,
    });
    return rows
      .filter(row => row.metadata?.question && row.metadata?.answer)
      .filter(row => !row.metadata?.expires_at || Date.parse(row.metadata.expires_at) > Date.now())
      .map(row => ({
        item: {
          id: row.metadata.item_id || row.id,
          question: row.metadata.question,
          answer: row.metadata.answer,
          tags: this._splitMetaList(row.metadata.tags),
          status: row.metadata.status || 'published',
          source: row.metadata.source || 'vector',
          sourceFile: row.metadata.source_file || row.metadata.resource_id || '',
          sourcePage: Number(row.metadata.source_page || row.metadata.page_number) || null,
          expiresAt: row.metadata.expires_at || null,
          unit_id: row.metadata.unit_id || '',
          raw_json: { vector: row },
        },
        score: Math.round((row.similarity || 0) * 100),
        vector: true,
        similarity: row.similarity,
      }));
  }

  async searchRag(query, options = {}) {
    const rows = await this.search(query, {
      ...options,
      objectTypes: ['chunk', 'resource'],
      limit: options.limit || 5,
      minSimilarity: options.minSimilarity ?? config.VECTOR_SEARCH.ragMinSimilarity,
    });
    return rows.map((row, index) => ({
      index: index + 1,
      id: row.id,
      title: row.metadata?.title || `资料${index + 1}`,
      type: row.metadata?.object_type || 'chunk',
      source: row.metadata?.source || 'vector',
      resourceId: row.metadata?.resource_id || row.metadata?.file_name || '',
      file: row.metadata?.file_name || row.metadata?.resource_id || '',
      sourceFile: row.metadata?.source_file || row.metadata?.file_name || row.metadata?.resource_id || '',
      sourcePage: Number(row.metadata?.source_page || row.metadata?.page_number) || null,
      score: Math.round((row.similarity || 0) * 100),
      similarity: row.similarity,
      summary: String(row.document || '').slice(0, 160),
      excerpt: String(row.document || '').slice(0, options.excerptLength || 320),
      tags: this._splitMetaList(row.metadata?.tags).slice(0, 8),
      vector: true,
    }));
  }

  async searchResources(query, options = {}) {
    const rows = await this.search(query, {
      ...options,
      objectTypes: ['resource'],
      limit: options.limit || 12,
      minSimilarity: options.minSimilarity ?? config.VECTOR_SEARCH.mediaMinSimilarity,
    });
    return rows.map(row => ({
      id: row.metadata?.item_id || row.id,
      fileName: row.metadata?.file_name || row.metadata?.resource_id || '',
      title: row.metadata?.title || '',
      type: row.metadata?.type || '',
      similarity: row.similarity,
      score: Math.round((row.similarity || 0) * 100),
    }));
  }

  async search(query, options = {}) {
    if (!this.enabled || !String(query || '').trim()) return [];
    if (Date.now() < this._unavailableUntil) return [];
    try {
      const data = await this._request('/search', {
        method: 'POST',
        body: {
          query,
          limit: options.limit || 10,
          object_types: options.objectTypes || [],
          min_similarity: options.minSimilarity ?? 0,
          metadata_filters: options.metadataFilters || {},
        },
      });
      return Array.isArray(data.results) ? data.results : [];
    } catch (err) {
      this._lastError = err.message;
      this._unavailableUntil = Date.now() + 10000;
      console.warn(`[VectorSearch] query skipped: ${err.message}`);
      return [];
    }
  }

  async _request(path, { method = 'POST', body, timeoutMs } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs || config.VECTOR_SEARCH.timeoutMs || 2500);
    try {
      const response = await fetch(`${this.baseUrl}${path}`, {
        method,
        signal: controller.signal,
        headers: { 'Content-Type': 'application/json' },
        body: method === 'GET' ? undefined : JSON.stringify(body || {}),
      });
      const text = await response.text();
      const data = text ? JSON.parse(text) : {};
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      return data;
    } finally {
      clearTimeout(timer);
    }
  }

  _splitMetaList(value) {
    return String(value || '')
      .split(/[、,，\n]/)
      .map(item => item.trim())
      .filter(Boolean);
  }
}

export const vectorKnowledgeService = new VectorKnowledgeService();
export default vectorKnowledgeService;
