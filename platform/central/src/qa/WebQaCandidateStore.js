import fs from 'fs';
import path from 'path';
import { createHash, randomUUID } from 'crypto';
import { config } from '../config/index.js';
import { isTimeSensitiveQuestion } from '../websearch/FreshnessIntentDetector.js';
import { resolveTemporalContext } from '../websearch/TemporalContextResolver.js';

const SCHEMA_VERSION = 2;
const CANDIDATE_TYPE = Object.freeze({
  ENTERPRISE: 'ENTERPRISE_KB',
  PUBLIC_STABLE: 'PUBLIC_STABLE',
  PUBLIC_TIME_BOUND: 'PUBLIC_TIME_BOUND',
  PUBLIC_VOLATILE: 'PUBLIC_VOLATILE',
});
const ENTERPRISE_SCOPE_PATTERN = /(?:本公司|我们公司|公司内部|内部制度|本项目|本方案|本展项|展厅|报价|采购|交付|员工制度)/i;
const VOLATILE_PATTERN = /(?:实时|当前|现在|目前|今天|最新).{0,24}(?:价格|汇率|股价|金价|天气|气温|新闻|热搜|比分|路况)|(?:价格|汇率|股价|金价|天气|气温|新闻|热搜|路况).{0,24}(?:实时|当前|现在|目前|今天|最新)/i;
const EVENT_SCOPE_PATTERN = /(?:大会|会议|赛事|比赛|奖项|颁奖|政策|法规|发布|上映|开幕|闭幕|举行|举办|结果|冠军|名单|赛程)/i;

function normalize(value) {
  return String(value || '').normalize('NFKC').toLowerCase()
    .replace(/\s+/g, '')
    .replace(/[，。！？、,.!?;；:"'“”‘’（）()[\]{}<>《》【】]/g, '');
}

function bigrams(value) {
  const text = normalize(value);
  if (text.length < 2) return text ? [text] : [];
  return Array.from({ length: text.length - 1 }, (_, index) => text.slice(index, index + 2));
}

function similarity(left, right) {
  const a = bigrams(left);
  const b = bigrams(right);
  if (!a.length || !b.length) return normalize(left) === normalize(right) ? 1 : 0;
  const counts = new Map();
  for (const item of b) counts.set(item, (counts.get(item) || 0) + 1);
  let overlap = 0;
  for (const item of a) {
    const count = counts.get(item) || 0;
    if (count > 0) {
      overlap += 1;
      counts.set(item, count - 1);
    }
  }
  return (2 * overlap) / (a.length + b.length);
}

function safeSources(sources = []) {
  const byUrl = new Map();
  for (const source of sources.slice(0, 16)) {
    const url = /^https?:\/\//i.test(String(source?.url || '')) ? String(source.url).slice(0, 1000) : '';
    if (!url) continue;
    let key = url;
    try {
      const parsed = new URL(url);
      parsed.hash = '';
      key = parsed.toString().replace(/\/$/, '');
    } catch {}
    const current = byUrl.get(key);
    const normalized = {
      title: String(source?.title || '').slice(0, 180),
      url,
      domain: String(source?.domain || '').slice(0, 160),
      sourceType: String(source?.sourceType || '').slice(0, 40),
      publishedDate: String(source?.publishedDate || '').slice(0, 80),
      authorityScore: Number(source?.authorityScore || 0),
      relevanceScore: Number(source?.relevanceScore || 0),
      canonicalOfficial: source?.canonicalOfficial === true,
      manuallyVerified: source?.manuallyVerified === true,
    };
    byUrl.set(key, current ? {
      ...current,
      ...normalized,
      title: normalized.title || current.title,
      domain: normalized.domain || current.domain,
      manuallyVerified: current.manuallyVerified || normalized.manuallyVerified,
      canonicalOfficial: current.canonicalOfficial || normalized.canonicalOfficial,
      authorityScore: Math.max(current.authorityScore, normalized.authorityScore),
      relevanceScore: Math.max(current.relevanceScore, normalized.relevanceScore),
    } : normalized);
  }
  return [...byUrl.values()].slice(0, 8);
}

function sourceMetrics(sources) {
  const sourceCount = new Set(sources.map(source => source.url)).size;
  const reliable = sources.filter(source =>
    source.manuallyVerified
    || source.canonicalOfficial
    || ['official', 'reputable'].includes(source.sourceType)
    || source.authorityScore >= 0.8);
  return {
    sourceCount,
    reliableSourceCount: new Set(reliable.map(source => source.domain || source.url)).size,
    officialSourceCount: new Set(sources
      .filter(source => source.canonicalOfficial || source.sourceType === 'official')
      .map(source => source.domain || source.url)).size,
    manuallyVerifiedSourceCount: new Set(sources
      .filter(source => source.manuallyVerified)
      .map(source => source.url)).size,
  };
}

function classifyCandidate(question, temporal) {
  if (VOLATILE_PATTERN.test(question)
    || (isTimeSensitiveQuestion(question) && /(?:价格|行情|新闻|天气|比分)/i.test(question))) {
    return CANDIDATE_TYPE.PUBLIC_VOLATILE;
  }
  if (ENTERPRISE_SCOPE_PATTERN.test(question)) return CANDIDATE_TYPE.ENTERPRISE;
  const hasCurrentOrFutureYear = temporal.targetYears.some(year => year >= temporal.currentYear);
  if (temporal.hasRelativeTemporal
    || hasCurrentOrFutureYear
    || (temporal.targetYears.length && EVENT_SCOPE_PATTERN.test(question))) {
    return CANDIDATE_TYPE.PUBLIC_TIME_BOUND;
  }
  return CANDIDATE_TYPE.PUBLIC_STABLE;
}

function expiryFor(candidateType, temporal, now) {
  if (candidateType === CANDIDATE_TYPE.PUBLIC_VOLATILE) {
    return new Date(now.getTime() + 24 * 60 * 60 * 1000).toISOString();
  }
  if (candidateType === CANDIDATE_TYPE.PUBLIC_TIME_BOUND) {
    const hasRelativeYear = ['今年', '明年', '去年'].some(key => temporal.relativeTerms?.[key]);
    if (hasRelativeYear && temporal.targetYears.length) {
      const year = Math.max(...temporal.targetYears);
      return new Date(Date.UTC(year, 11, 31, 15, 59, 59, 999)).toISOString();
    }
    return new Date(now.getTime() + 180 * 24 * 60 * 60 * 1000).toISOString();
  }
  const days = candidateType === CANDIDATE_TYPE.ENTERPRISE ? 365 : 180;
  return new Date(now.getTime() + days * 24 * 60 * 60 * 1000).toISOString();
}

function evaluateEligibility(item) {
  const requiredUses = 3;
  const metrics = sourceMetrics(item.sources || []);
  const candidateType = item.candidateType || CANDIDATE_TYPE.PUBLIC_STABLE;
  const manuallyVerified = metrics.manuallyVerifiedSourceCount >= 1;
  const sourceSatisfied = metrics.officialSourceCount >= 1
    || metrics.reliableSourceCount >= 2
    || manuallyVerified;
  const reasons = [];
  if (!manuallyVerified && Number(item.useCount || 0) < requiredUses) {
    reasons.push(`还需使用 ${requiredUses - Number(item.useCount || 0)} 次，或先完成人工可靠校验`);
  }
  if (!manuallyVerified && candidateType === CANDIDATE_TYPE.ENTERPRISE && !item.knowledgeSupported) {
    reasons.push('企业内部事实需要知识库支持');
  }
  if (!manuallyVerified
    && [CANDIDATE_TYPE.PUBLIC_STABLE, CANDIDATE_TYPE.PUBLIC_TIME_BOUND].includes(candidateType)
    && !sourceSatisfied) {
    reasons.push(metrics.sourceCount > 0
      ? `已有 ${metrics.sourceCount} 个来源，但尚未达到可靠来源要求`
      : '缺少联网来源');
  }
  const hasTimeScope = (item.targetYears || []).length > 0
    || item.temporalContext?.hasRelativeTemporal === true;
  if (!manuallyVerified && candidateType === CANDIDATE_TYPE.PUBLIC_TIME_BOUND && !hasTimeScope) {
    reasons.push('缺少明确年份或时间范围');
  }
  if (!manuallyVerified && candidateType === CANDIDATE_TYPE.PUBLIC_VOLATILE) {
    reasons.push('实时易变信息仅作短期缓存，不进入正式 QA');
  }
  const promotableType = candidateType !== CANDIDATE_TYPE.PUBLIC_VOLATILE || manuallyVerified;
  const knowledgeRequired = candidateType === CANDIDATE_TYPE.ENTERPRISE;
  const sourceRequired = [CANDIDATE_TYPE.PUBLIC_STABLE, CANDIDATE_TYPE.PUBLIC_TIME_BOUND]
    .includes(candidateType);
  return {
    ...metrics,
    eligibleForPromotion: promotableType && reasons.length === 0,
    manualPromotionReady: manuallyVerified,
    autoPromotionReady: manuallyVerified
      && Number(item.useCount || 0) >= requiredUses
      && item.status === 'CANDIDATE',
    unmetReasons: reasons,
    knowledgeRequirementSatisfied: !knowledgeRequired || Boolean(item.knowledgeSupported),
    sourceRequirementSatisfied: !sourceRequired || sourceSatisfied,
    eligibilityPolicy: {
      requiredUses,
      knowledgeRequired,
      sourceRequired,
      sourceRule: sourceRequired
        ? '1个官方来源，或2个独立可靠来源，或1个人工核验来源'
        : '不强制',
      manualVerificationOverrides: true,
      autoPromoteAfterUses: requiredUses,
      yearScopeRequired: candidateType === CANDIDATE_TYPE.PUBLIC_TIME_BOUND,
      promotable: promotableType,
    },
  };
}

export class WebQaCandidateStore {
  constructor(options = {}) {
    this.filePath = options.filePath || path.join(config.DATA_DIR, 'web-qa-candidates.json');
    this._data = null;
  }

  list() {
    return [...this._load().items].sort((a, b) => String(b.lastSeenAt).localeCompare(String(a.lastSeenAt)));
  }

  get(id) {
    return this._load().items.find(item => item.id === String(id || '')) || null;
  }

  record(input = {}) {
    const question = String(input.question || '').trim();
    const answer = String(input.answer || '').trim();
    if (!question || !answer) return null;

    const data = this._load();
    const now = new Date();
    const nowIso = now.toISOString();
    const temporal = resolveTemporalContext(question, { now });
    const suggestedQuestion = temporal.hasRelativeTemporal ? temporal.resolvedQuestion : question;
    const questionKey = normalize(suggestedQuestion);
    const rawQuestionKey = normalize(question);
    const existing = data.items.find(item =>
      item.status !== 'REJECTED'
      && (item.questionKey === questionKey
        || item.questionKey === rawQuestionKey
        || normalize(item.question) === rawQuestionKey));
    const consistent = existing ? similarity(existing.answer, answer) >= 0.72 : false;
    const useCount = existing && consistent ? Number(existing.useCount || 0) + 1 : 1;
    const knowledgeSupported = Boolean(input.knowledgeSupported || (existing && consistent && existing.knowledgeSupported));
    const sources = safeSources([
      ...(existing && consistent ? existing.sources || [] : []),
      ...safeSources(input.sources),
    ]);
    const candidateType = classifyCandidate(question, temporal);
    const freshnessClass = candidateType === CANDIDATE_TYPE.PUBLIC_VOLATILE
      ? 'TIME_SENSITIVE'
      : candidateType === CANDIDATE_TYPE.PUBLIC_TIME_BOUND ? 'TIME_BOUND' : 'STABLE';
    const baseItem = {
      id: existing?.id || randomUUID(),
      questionKey,
      question,
      suggestedQuestion,
      answer,
      answerHash: createHash('sha256').update(normalize(answer)).digest('hex'),
      status: existing?.status === 'PROMOTED' && consistent ? 'PROMOTED' : 'CANDIDATE',
      useCount,
      answerVariantCount: Number(existing?.answerVariantCount || 0) + (existing && !consistent ? 1 : 0),
      knowledgeSupported,
      knowledgeSource: String(input.knowledgeSource || '').slice(0, 180),
      sources,
      candidateType,
      freshnessClass,
      targetYears: temporal.targetYears,
      temporalContext: {
        currentDate: temporal.currentDate,
        timeZone: temporal.timeZone,
        relativeTerms: temporal.relativeTerms,
        hasRelativeTemporal: temporal.hasRelativeTemporal,
      },
      expiresAt: expiryFor(candidateType, temporal, now),
      firstSeenAt: existing && consistent ? existing.firstSeenAt : nowIso,
      lastSeenAt: nowIso,
      promotedAt: existing?.promotedAt || null,
      promotedQaId: existing?.promotedQaId || '',
      promotionMode: existing?.promotionMode || '',
    };
    const item = { ...baseItem, ...evaluateEligibility(baseItem) };

    if (existing) data.items[data.items.indexOf(existing)] = item;
    else data.items.push(item);
    this._save(data);
    return item;
  }

  markPromoted(id, metadata = {}) {
    const data = this._load();
    const item = data.items.find(row => row.id === String(id || ''));
    if (!item) return null;
    item.status = 'PROMOTED';
    item.promotedAt = new Date().toISOString();
    item.promotionMode = String(metadata.promotionMode || item.promotionMode || 'MANUAL');
    item.promotedQaId = String(metadata.qaId || item.promotedQaId || '');
    item.autoPromotionReady = false;
    this._save(data);
    return item;
  }

  reject(id) {
    const data = this._load();
    const item = data.items.find(row => row.id === String(id || ''));
    if (!item) return null;
    item.status = 'REJECTED';
    item.eligibleForPromotion = false;
    this._save(data);
    return item;
  }

  setSourceVerified(id, url, verified = true) {
    const data = this._load();
    const index = data.items.findIndex(row => row.id === String(id || ''));
    if (index < 0) return null;
    const item = data.items[index];
    const sourceUrl = String(url || '');
    if (!(item.sources || []).some(source => source.url === sourceUrl)) return null;
    const sources = safeSources((item.sources || []).map(source => ({
      ...source,
      manuallyVerified: source.url === sourceUrl ? verified === true : source.manuallyVerified,
    })));
    const updated = { ...item, sources, lastReviewedAt: new Date().toISOString() };
    data.items[index] = { ...updated, ...evaluateEligibility(updated) };
    this._save(data);
    return data.items[index];
  }

  _normalizeExistingItem(item) {
    const question = String(item?.question || '');
    const temporal = resolveTemporalContext(question);
    const sources = safeSources(item?.sources || []);
    const candidateType = item?.candidateType || classifyCandidate(question, temporal);
    const normalized = {
      ...item,
      questionKey: item?.questionKey || normalize(temporal.hasRelativeTemporal ? temporal.resolvedQuestion : question),
      suggestedQuestion: item?.suggestedQuestion || (temporal.hasRelativeTemporal ? temporal.resolvedQuestion : question),
      candidateType,
      freshnessClass: candidateType === CANDIDATE_TYPE.PUBLIC_VOLATILE
        ? 'TIME_SENSITIVE'
        : candidateType === CANDIDATE_TYPE.PUBLIC_TIME_BOUND ? 'TIME_BOUND' : 'STABLE',
      targetYears: Array.isArray(item?.targetYears) ? item.targetYears : temporal.targetYears,
      temporalContext: {
        ...(item?.temporalContext || {}),
        currentDate: item?.temporalContext?.currentDate || temporal.currentDate,
        timeZone: item?.temporalContext?.timeZone || temporal.timeZone,
        relativeTerms: item?.temporalContext?.relativeTerms || temporal.relativeTerms,
        hasRelativeTemporal: item?.temporalContext?.hasRelativeTemporal ?? temporal.hasRelativeTemporal,
      },
      sources,
    };
    return { ...normalized, ...evaluateEligibility(normalized) };
  }

  _load() {
    if (this._data) return this._data;
    try {
      if (fs.existsSync(this.filePath)) {
        const parsed = JSON.parse(fs.readFileSync(this.filePath, 'utf8').replace(/^\uFEFF/, ''));
        const items = Array.isArray(parsed) ? parsed : parsed.items || [];
        this._data = { items: items.map(item => this._normalizeExistingItem(item)) };
        return this._data;
      }
    } catch (err) {
      console.warn(`[WebQaCandidateStore] failed to read candidates: ${err.message}`);
    }
    this._data = { items: [] };
    return this._data;
  }

  _save(data) {
    fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
    fs.writeFileSync(this.filePath, JSON.stringify({
      schemaVersion: SCHEMA_VERSION,
      items: data.items,
      updatedAt: new Date().toISOString(),
    }, null, 2), 'utf8');
    this._data = data;
  }
}

export const webQaCandidateStore = new WebQaCandidateStore();
export default webQaCandidateStore;
