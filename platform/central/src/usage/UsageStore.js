// ============================================================
// 展厅使用情况记录
// - usage-events.jsonl 保存每次提问的原始事件
// - usage-summary.json 保存可快速查询的聚合统计
// ============================================================
import fs from 'fs';
import path from 'path';
import { config } from '../config/index.js';

const STOPWORDS = new Set([
  '什么', '怎么', '如何', '一下', '这个', '那个', '我们', '你们', '用户', '了解',
  '可以', '介绍', '播放', '打开', '展示', '帮我', '请问', '关于', '有没有', '是不是', '为什么',
  'the', 'and', 'for', 'with', 'this', 'that',
]);

const INTEREST_TERMS = [
  '政务大模型', '司法大模型', '海算', '多模态', '大模型', '应用场景', '解决方案',
  '产品', '案例', '技术', '公司', '优势', '功能', '价格', '合作', '部署',
  '安全', '隐私', '数据', '算力', '问答', '讲解', '展厅',
];

// These are server-side dispatch records. Without a client receipt they do not
// prove that a display, PPT player, TTS service, or avatar completed the action.
const GUIDE_DISPATCH_ACTIONS = new Set([
  'ppt_speak_commentary', 'speak_ppt_commentary', 'speak',
]);
const FILE_CALL_ACTIONS = new Set([
  'play_ppt', 'play_video', 'show_doc',
]);

function isControlOrFileInstruction(question) {
  const text = String(question || '').replace(/[\s，。,.!！?？]+/g, '').trim();
  if (!text) return false;
  const hasQuestionSignal = /什么|怎么|如何|为何|为什么|哪些|是否|能否|吗|呢|作用|用途|区别|原理|内容|能力|场景/;
  if (hasQuestionSignal.test(text)) return false;
  if (/^(?:请|帮我|麻烦)?(?:打开|关闭|播放|暂停|继续|停止|切换|翻到|跳到|返回|退出|启动|重启|投屏|展示|显示|隐藏|放大|缩小|静音|黑屏|亮屏|介绍)/.test(text)) return true;
  return /(?:上一页|下一页|前一页|后一页|首页)$/.test(text)
    || /(?:ppt|pptx|pdf|word|docx|文件|文档|演示稿)$/i.test(text);
}

export class UsageStore {
  constructor(options = {}) {
    this.dataDir = options.dataDir || config.DATA_DIR;
    this.eventsPath = path.join(this.dataDir, 'usage-events.jsonl');
    this.summaryPath = path.join(this.dataDir, 'usage-summary.json');
    this.summary = null;
  }

  recordInteraction(event) {
    this._ensureLoaded();

    const normalized = this._normalizeEvent(event);
    fs.appendFileSync(this.eventsPath, `${JSON.stringify(normalized)}\n`, 'utf8');
    this._applyEvent(normalized);
    this._saveSummary();

    return normalized;
  }

  getSummary() {
    this._ensureLoaded();
    const topKeywords = this._topEntries(this.summary.keywords, 20).map(([keyword, count]) => ({ keyword, count }));
    const topActions = this._topEntries(this.summary.byAction, 10).map(([action, count]) => ({ action, count }));
    const topSources = this._topEntries(this.summary.bySource, 10).map(([source, count]) => ({ source, count }));
    const topTargets = this._topEntries(this.summary.byTarget, 10).map(([target, count]) => ({ target, count }));

    return {
      ...this.summary,
      topKeywords,
      topActions,
      topSources,
      topTargets,
    };
  }

  getRecentEvents(limit = 50) {
    this._ensureLoaded();
    if (!fs.existsSync(this.eventsPath)) return [];

    const lines = fs.readFileSync(this.eventsPath, 'utf8').trim().split(/\r?\n/).filter(Boolean);
    return lines.slice(-limit).reverse().map(line => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    }).filter(Boolean);
  }

  getAnalytics({ from, to, source, target } = {}) {
    this._ensureLoaded();
    const fromDay = this._normalizeAnalyticsDay(from, 'from');
    const toDay = this._normalizeAnalyticsDay(to, 'to');
    if (fromDay && toDay && fromDay > toDay) {
      const error = new Error('from must not be after to');
      error.statusCode = 400;
      throw error;
    }

    const selectedSource = String(source || '').trim();
    const selectedTarget = String(target || '').trim();
    const allEvents = this._readEvents();
    const sources = new Set(allEvents.map(event => String(event.source || 'unknown')));
    const targets = new Set(allEvents.map(event => String(event.target || 'all')));
    const events = allEvents.filter(event => {
      const day = this._analyticsEventDay(event);
      if (fromDay && (!day || day < fromDay)) return false;
      if (toDay && (!day || day > toDay)) return false;
      if (selectedSource && String(event.source || 'unknown') !== selectedSource) return false;
      if (selectedTarget && String(event.target || 'all') !== selectedTarget) return false;
      return true;
    });

    let answeredInteractions = 0;
    let failedInteractions = 0;
    let guideDispatches = 0;
    let fileCalls = 0;
    let faqHits = 0;
    const durations = [];
    const seriesByDay = new Map();
    const questions = new Map();
    const sourceCounts = new Map();
    const targetCounts = new Map();

    for (const event of events) {
      const failed = event.ok === false || Boolean(event.error);
      const question = String(event.question || '').replace(/\s+/g, ' ').trim();
      const answer = String(event.answer || '').trim();
      const actions = (Array.isArray(event.actions) ? event.actions : [])
        .map(action => typeof action === 'string' ? action : action?.type || action?.action || '')
        .map(action => String(action || '').trim()).filter(Boolean);
      const eventGuideDispatches = actions.filter(action => GUIDE_DISPATCH_ACTIONS.has(action)).length;
      const isFaqInteraction = /(?:^|[^a-z0-9])(?:qa|faq)(?:$|[^a-z0-9])/i.test(String(event.answerSource || ''))
        || String(event.answerSource || '').includes('问答');

      if (!failed && answer) answeredInteractions += 1;
      if (failed) failedInteractions += 1;
      guideDispatches += eventGuideDispatches;
      fileCalls += actions.filter(action => FILE_CALL_ACTIONS.has(action)).length;
      if (isFaqInteraction) faqHits += 1;

      const rawDuration = event.durationMs;
      const duration = Number(rawDuration);
      if (rawDuration !== null && rawDuration !== undefined && rawDuration !== ''
        && Number.isFinite(duration) && duration >= 0) durations.push(duration);

      if (question && isFaqInteraction && !isControlOrFileInstruction(question)) {
        const key = question.toLocaleLowerCase('zh-CN');
        const current = questions.get(key);
        if (current) current.count += 1;
        else questions.set(key, { question, count: 1 });
      }

      const sourceName = String(event.source || 'unknown');
      const targetName = String(event.target || 'all');
      sourceCounts.set(sourceName, (sourceCounts.get(sourceName) || 0) + 1);
      targetCounts.set(targetName, (targetCounts.get(targetName) || 0) + 1);
      const bucket = this._analyticsEventDay(event);
      if (bucket) {
        const point = seriesByDay.get(bucket) || { bucket, interactions: 0, queries: 0, guideDispatches: 0 };
        point.interactions += 1;
        if (question) point.queries += 1;
        point.guideDispatches += eventGuideDispatches;
        seriesByDay.set(bucket, point);
      }
    }

    const sortedDurations = [...durations].sort((a, b) => a - b);
    const avgDurationMs = durations.length
      ? Math.round(durations.reduce((total, value) => total + value, 0) / durations.length) : null;
    const p95DurationMs = sortedDurations.length
      ? Math.round(sortedDurations[Math.max(0, Math.ceil(sortedDurations.length * 0.95) - 1)]) : null;
    const distribution = counts => [...counts.entries()]
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, 'zh-CN', { numeric: true }));

    return {
      generatedAt: new Date().toISOString(),
      range: { from: fromDay, to: toDay },
      filters: {
        sources: [...sources].sort((a, b) => a.localeCompare(b, 'zh-CN', { numeric: true })),
        targets: [...targets].sort((a, b) => a.localeCompare(b, 'zh-CN', { numeric: true })),
      },
      summary: {
        recordedInteractions: events.length,
        answeredInteractions,
        failedInteractions,
        guideDispatches,
        fileCalls,
        avgDurationMs,
        p95DurationMs,
        faqHits,
      },
      series: [...seriesByDay.values()].sort((a, b) => a.bucket.localeCompare(b.bucket)),
      topQuestions: [...questions.values()]
        .sort((a, b) => b.count - a.count || a.question.localeCompare(b.question, 'zh-CN')).slice(0, 5),
      sourceDistribution: distribution(sourceCounts),
      targetDistribution: distribution(targetCounts),
      metricDefinitions: {
        recordedInteractions: '筛选范围内写入服务端 usage-events.jsonl 的事件数',
        answeredInteractions: '未标记失败且保存了非空回答文本的事件数',
        failedInteractions: '明确标记 ok=false 或保存了 error 的事件数',
        guideDispatches: '服务端记录的讲解指令下发次数；不代表终端执行完成',
        fileCalls: '服务端记录的文件展示指令下发次数；不代表终端执行完成',
        duration: '中控服务记录的 durationMs；不是首字或播放完成耗时',
        faqHits: 'answerSource 明确包含 QA、FAQ 或问答标记的事件数',
      },
    };
  }

  getConversationTurns(conversationId, limit = 20) {
    this._ensureLoaded();
    const id = String(conversationId || '').trim();
    if (!id || !fs.existsSync(this.eventsPath)) return [];
    const turns = [];
    for (const line of fs.readFileSync(this.eventsPath, 'utf8').split(/\r?\n/)) {
      if (!line.trim()) continue;
      try {
        const event = JSON.parse(line);
        if (String(event.conversationId || '') !== id || event.ok === false || !event.answer) continue;
        turns.push({
          question: String(event.question || ''),
          answer: String(event.answer || ''),
          answerSource: String(event.answerSource || ''),
          finalRoute: String(event.finalRoute || ''),
          ok: event.ok !== false,
          technicalFallback: event.technicalFallback === true,
          refusalFallback: event.refusalFallback === true,
          contextEligible: event.contextEligible !== false,
          time: String(event.time || ''),
        });
      } catch {}
    }
    return turns.slice(-Math.max(1, Number(limit) || 20));
  }

  deleteConversation(conversationId) {
    this._ensureLoaded();
    const id = String(conversationId || '').trim();
    if (!id || !fs.existsSync(this.eventsPath)) return { removed: 0 };
    const legacyEventId = id.startsWith('legacy-') ? id.slice('legacy-'.length) : '';

    const rows = fs.readFileSync(this.eventsPath, 'utf8')
      .split(/\r?\n/)
      .filter(Boolean);
    const kept = [];
    let removed = 0;
    for (const row of rows) {
      try {
        const event = JSON.parse(row);
        if (String(event.conversationId || '') === id
          || (legacyEventId && String(event.id || '') === legacyEventId)) {
          removed += 1;
          continue;
        }
      } catch {}
      kept.push(row);
    }
    if (!removed) return { removed: 0 };

    fs.writeFileSync(this.eventsPath, kept.length ? `${kept.join('\n')}\n` : '', 'utf8');
    this.summary = this._emptySummary();
    this._rebuildFromEvents();
    this._saveSummary();
    return { removed };
  }

  getWebSearchRuleStats(ruleIds = [], limit = 2000) {
    const ids = new Set((Array.isArray(ruleIds) ? ruleIds : []).map(String).filter(Boolean));
    const byRule = Object.fromEntries([...ids].map(id => [id, {
      matched: 0,
      answered: 0,
      notAnswered: 0,
      failed: 0,
      successRate: null,
    }]));
    const events = this.getRecentEvents(Math.max(1, Number(limit) || 2000));

    for (const event of events) {
      const traces = Array.isArray(event.routeTrace) ? event.routeTrace : [];
      const ruleId = traces.map(item => String(item?.ruleId || '')).find(id => ids.has(id));
      if (!ruleId || !byRule[ruleId]) continue;

      const stats = byRule[ruleId];
      stats.matched += 1;
      const status = String(event.webSearchStatus || '').toUpperCase();
      const finalRoute = String(event.finalRoute || '').toUpperCase();
      const answered = status === 'ANSWERED' || finalRoute === 'WEB_ANSWERED';
      const failed = event.ok === false
        || ['FAILED', 'ERROR', 'UNAVAILABLE', 'TIMED_OUT'].includes(status);
      if (answered) stats.answered += 1;
      else stats.notAnswered += 1;
      if (failed) stats.failed += 1;
    }

    for (const stats of Object.values(byRule)) {
      stats.successRate = stats.matched > 0
        ? Number(((stats.answered / stats.matched) * 100).toFixed(1))
        : null;
    }

    return {
      byRule,
      scannedEvents: events.length,
      limit: Math.max(1, Number(limit) || 2000),
    };
  }

  getContextSummary() {
    const summary = this.getSummary();
    const topKeywords = summary.topKeywords.slice(0, 12).map(item => `${item.keyword}(${item.count})`).join('、') || '暂无';
    const recentQuestions = summary.recentQuestions
      .slice(0, 8)
      .map(item => `  - ${item.question}`)
      .join('\n') || '  - 暂无';

    return `\n【展厅使用情况统计】
累计提问 ${summary.totalQuestions} 次，成功回答 ${summary.totalAnswered} 次，失败 ${summary.totalFailed} 次。
用户高频兴趣点：${topKeywords}
最近典型问题：
${recentQuestions}
当用户问“大家关心什么/用户对什么感兴趣/提问最多的是什么”时，请优先依据本段统计回答。\n`;
  }

  _ensureLoaded() {
    if (this.summary) return;

    fs.mkdirSync(this.dataDir, { recursive: true });
    if (!fs.existsSync(this.eventsPath)) fs.writeFileSync(this.eventsPath, '', 'utf8');

    if (fs.existsSync(this.summaryPath)) {
      try {
        this.summary = JSON.parse(fs.readFileSync(this.summaryPath, 'utf8'));
        return;
      } catch {}
    }

    this.summary = this._emptySummary();
    this._rebuildFromEvents();
    this._saveSummary();
  }

  _emptySummary() {
    return {
      totalQuestions: 0,
      totalAnswered: 0,
      totalFailed: 0,
      firstSeenAt: null,
      lastSeenAt: null,
      byDay: {},
      bySource: {},
      byTarget: {},
      byAction: {},
      keywords: {},
      recentQuestions: [],
      recentErrors: [],
    };
  }

  _rebuildFromEvents() {
    const text = fs.existsSync(this.eventsPath) ? fs.readFileSync(this.eventsPath, 'utf8') : '';
    for (const line of text.split(/\r?\n/)) {
      if (!line.trim()) continue;
      try {
        this._applyEvent(JSON.parse(line));
      } catch {}
    }
  }

  _normalizeEvent(event) {
    const now = new Date();
    const question = String(event.question || '').trim();
    const answer = String(event.answer || '').trim();
    const actions = Array.isArray(event.actions) ? event.actions : [];
    const webSources = Array.isArray(event.webSources) ? event.webSources : [];
    const knowledgeSources = Array.isArray(event.knowledgeSources) ? event.knowledgeSources : [];
    const ok = event.ok !== false && !event.error;

    return {
      id: `${now.getTime()}-${event.seq || 0}-${Math.random().toString(36).slice(2, 8)}`,
      time: event.time || now.toISOString(),
      day: (event.time || now.toISOString()).slice(0, 10),
      seq: event.seq || null,
      source: event.source || 'unknown',
      deviceId: event.deviceId || null,
      deviceType: event.deviceType || null,
      conversationId: String(event.conversationId || '').slice(0, 100),
      target: event.target || 'all',
      question,
      answer,
      ok,
      error: event.error || null,
      durationMs: event.durationMs || null,
      answerSource: event.answerSource || '',
      technicalFallback: event.technicalFallback === true,
      refusalFallback: event.refusalFallback === true,
      contextEligible: event.contextEligible !== false,
      failureCode: String(event.failureCode || '').slice(0, 100),
      knowledgeUnitId: String(event.knowledgeUnitId || '').slice(0, 100),
      pptFileName: String(event.pptFileName || '').slice(0, 500),
      pptTitle: String(event.pptTitle || '').slice(0, 300),
      pptSlideNumber: Number.isFinite(Number(event.pptSlideNumber)) ? Number(event.pptSlideNumber) : null,
      qaScopedToCurrentUnit: event.qaScopedToCurrentUnit === true,
      qaCurrentUnitRequired: event.qaCurrentUnitRequired === true,
      finalRoute: String(event.finalRoute || '').slice(0, 80),
      routeTrace: (Array.isArray(event.routeTrace) ? event.routeTrace : []).slice(0, 20).map(item => ({
        handler: String(item?.handler || '').slice(0, 80),
        matched: item?.matched === true,
        status: String(item?.status || '').slice(0, 80),
        reason: String(item?.reason || '').slice(0, 200),
        ruleId: String(item?.ruleId || '').slice(0, 100),
        ruleName: String(item?.ruleName || '').slice(0, 160),
        error: String(item?.error || '').slice(0, 500),
      })).filter(item => item.handler),
      webSearchQuery: event.webSearchQuery || '',
      webSearchQueries: (Array.isArray(event.webSearchQueries) ? event.webSearchQueries : [])
        .map(item => String(item || '').slice(0, 500)).filter(Boolean),
      webSearchStatus: event.webSearchStatus || '',
      webSearchFallbackReason: String(event.webSearchFallbackReason || '').slice(0, 1000),
      webSearchClaims: (Array.isArray(event.webSearchClaims) ? event.webSearchClaims : []).map(claim => ({
        claimId: String(claim?.claim_id || claim?.claimId || '').slice(0, 40),
        claim: String(claim?.claim || '').slice(0, 500),
        type: String(claim?.type || '').slice(0, 60),
        importance: String(claim?.importance || '').slice(0, 30),
        verdict: String(claim?.verdict || '').slice(0, 40),
        evidenceIds: (Array.isArray(claim?.evidence_ids) ? claim.evidence_ids : claim?.evidenceIds || [])
          .map(id => String(id || '')).filter(Boolean),
      })).filter(claim => claim.claim),
      webSearchUncertainties: (Array.isArray(event.webSearchUncertainties) ? event.webSearchUncertainties : [])
        .map(item => String(item || '').slice(0, 500)).filter(Boolean),
      webSearchVerification: event.webSearchVerification && typeof event.webSearchVerification === 'object'
        ? {
            schemaVersion: Number(event.webSearchVerification.schemaVersion || 1),
            decision: String(event.webSearchVerification.decision || '').slice(0, 60),
            valid: event.webSearchVerification.valid === true,
            errors: (Array.isArray(event.webSearchVerification.errors) ? event.webSearchVerification.errors : [])
              .map(item => String(item || '').slice(0, 500)).filter(Boolean),
            reasonCodes: (Array.isArray(event.webSearchVerification.reasonCodes) ? event.webSearchVerification.reasonCodes : [])
              .map(item => String(item || '').slice(0, 100)).filter(Boolean),
            unsupportedClaimIds: (Array.isArray(event.webSearchVerification.unsupportedClaimIds)
              ? event.webSearchVerification.unsupportedClaimIds : [])
              .map(item => String(item || '').slice(0, 40)).filter(Boolean),
            contradictedClaimIds: (Array.isArray(event.webSearchVerification.contradictedClaimIds)
              ? event.webSearchVerification.contradictedClaimIds : [])
              .map(item => String(item || '').slice(0, 40)).filter(Boolean),
            coverageScore: Number.isFinite(Number(event.webSearchVerification.coverageScore))
              ? Number(event.webSearchVerification.coverageScore) : null,
            supportScore: Number.isFinite(Number(event.webSearchVerification.supportScore))
              ? Number(event.webSearchVerification.supportScore) : null,
            suggestedAction: String(event.webSearchVerification.suggestedAction || '').slice(0, 100),
            claimResults: (Array.isArray(event.webSearchVerification.claimResults)
              ? event.webSearchVerification.claimResults : []).slice(0, 12).map(item => ({
              claimId: String(item?.claimId || '').slice(0, 40),
              claim: String(item?.claim || '').slice(0, 500),
              type: String(item?.type || '').slice(0, 60),
              importance: String(item?.importance || '').slice(0, 30),
              evidenceIds: (Array.isArray(item?.evidenceIds) ? item.evidenceIds : [])
                .map(id => String(id || '').slice(0, 40)).filter(Boolean),
              verdict: String(item?.verdict || '').slice(0, 40),
              supportScore: Number.isFinite(Number(item?.supportScore)) ? Number(item.supportScore) : null,
              reasonCodes: (Array.isArray(item?.reasonCodes) ? item.reasonCodes : [])
                .map(code => String(code || '').slice(0, 100)).filter(Boolean),
              reason: String(item?.reason || '').slice(0, 500),
              supportedPart: String(item?.supportedPart || '').slice(0, 500),
              unsupportedPart: String(item?.unsupportedPart || '').slice(0, 500),
              suggestedRewrite: String(item?.suggestedRewrite || '').slice(0, 500),
            })),
            evidenceAssessment: event.webSearchVerification.evidenceAssessment
              && typeof event.webSearchVerification.evidenceAssessment === 'object'
              ? {
                  decision: String(event.webSearchVerification.evidenceAssessment.decision || '').slice(0, 60),
                  reason: String(event.webSearchVerification.evidenceAssessment.reason || '').slice(0, 500),
                  metrics: event.webSearchVerification.evidenceAssessment.metrics
                    && typeof event.webSearchVerification.evidenceAssessment.metrics === 'object'
                    ? Object.fromEntries(Object.entries(event.webSearchVerification.evidenceAssessment.metrics)
                      .slice(0, 20).map(([key, value]) => [String(key).slice(0, 80), Number(value)]))
                    : null,
                  requiredAspects: (Array.isArray(event.webSearchVerification.evidenceAssessment.requiredAspects)
                    ? event.webSearchVerification.evidenceAssessment.requiredAspects : []).slice(0, 12).map(item => ({
                    id: String(item?.id || '').slice(0, 40),
                    name: String(item?.name || '').slice(0, 200),
                    weight: Number(item?.weight || 0),
                  })),
                  missingAspects: (Array.isArray(event.webSearchVerification.evidenceAssessment.missingAspects)
                    ? event.webSearchVerification.evidenceAssessment.missingAspects : []).slice(0, 12).map(item => ({
                    id: String(item?.id || '').slice(0, 40),
                    name: String(item?.name || '').slice(0, 200),
                    weight: Number(item?.weight || 0),
                  })),
                }
              : null,
          }
        : null,
      webSearchRetrieval: event.webSearchRetrieval && typeof event.webSearchRetrieval === 'object'
        ? {
            profile: String(event.webSearchRetrieval.profile || '').slice(0, 60),
            supplemented: event.webSearchRetrieval.supplemented === true,
            unavailable: event.webSearchRetrieval.unavailable === true,
            queries: (Array.isArray(event.webSearchRetrieval.queries) ? event.webSearchRetrieval.queries : [])
              .map(item => String(item || '').slice(0, 500)).filter(Boolean),
            missingAspects: (Array.isArray(event.webSearchRetrieval.missingAspects)
              ? event.webSearchRetrieval.missingAspects : [])
              .map(item => String(item || '').slice(0, 200)).filter(Boolean),
            attempts: (Array.isArray(event.webSearchRetrieval.attempts) ? event.webSearchRetrieval.attempts : [])
              .slice(0, 3).map(item => ({
                pass: String(item?.pass || '').slice(0, 40),
                query: String(item?.query || '').slice(0, 500),
                options: item?.options && typeof item.options === 'object'
                  ? {
                      maxResults: Number(item.options.maxResults || 0),
                      recencyDays: Number(item.options.recencyDays || 0),
                      searchDepth: String(item.options.searchDepth || '').slice(0, 40),
                      includeRawContent: item.options.includeRawContent === true,
                      includeDomains: (Array.isArray(item.options.includeDomains) ? item.options.includeDomains : [])
                        .map(domain => String(domain || '').slice(0, 120)).filter(Boolean),
                    }
                  : null,
                resultCount: Number(item?.resultCount || 0),
                error: String(item?.error || '').slice(0, 500),
              })),
          }
        : null,
      webSearchModelRaw: String(event.webSearchModelRaw || '').slice(0, 4000),
      webSearchRepairRaw: String(event.webSearchRepairRaw || '').slice(0, 4000),
      webSearchVerifierRaw: String(event.webSearchVerifierRaw || '').slice(0, 4000),
      webSources: webSources.map(source => ({
        evidenceId: String(source.evidenceId || '').trim(),
        title: String(source.title || '').trim(),
        url: String(source.url || '').trim(),
        publishedDate: source.publishedDate || '',
        crawledDate: source.crawledDate || '',
        retrievedAt: source.retrievedAt || '',
        sourceType: source.sourceType || '',
        score: Number.isFinite(Number(source.score)) ? Number(source.score) : null,
        retrievalScore: Number.isFinite(Number(source.retrievalScore)) ? Number(source.retrievalScore) : null,
        relevanceScore: Number.isFinite(Number(source.relevanceScore)) ? Number(source.relevanceScore) : null,
        authorityScore: Number.isFinite(Number(source.authorityScore)) ? Number(source.authorityScore) : null,
        freshnessScore: Number.isFinite(Number(source.freshnessScore)) ? Number(source.freshnessScore) : null,
        retrievalPass: String(source.retrievalPass || '').slice(0, 80),
        retrievalQuery: String(source.retrievalQuery || '').slice(0, 500),
        snippet: String(source.snippet || '').trim(),
        content: String(source.content || source.snippet || '').slice(0, 2500).trim(),
      })).filter(source => source.title || source.url || source.snippet),
      knowledgeSources: knowledgeSources.map(source => ({
        fileName: String(source.fileName || '').trim(),
        page: Number(source.page) > 0 ? Number(source.page) : null,
        type: String(source.type || '').trim(),
      })).filter(source => source.fileName),
      actions: actions.map(action => {
        if (typeof action === 'string') return { type: action, target: event.target || 'all' };
        return {
          type: action.type || action.action || 'unknown',
          target: action.target || 'all',
        };
      }),
      keywords: this._extractKeywords(question),
    };
  }

  _applyEvent(event) {
    this.summary.totalQuestions += 1;
    if (event.ok) this.summary.totalAnswered += 1;
    else this.summary.totalFailed += 1;

    this.summary.firstSeenAt = this.summary.firstSeenAt || event.time;
    this.summary.lastSeenAt = event.time;
    this._inc(this.summary.byDay, event.day);
    this._inc(this.summary.bySource, event.source);
    this._inc(this.summary.byTarget, String(event.target || 'all'));

    for (const action of event.actions) this._inc(this.summary.byAction, action.type);
    for (const keyword of event.keywords) this._inc(this.summary.keywords, keyword);

    if (event.question) {
      this.summary.recentQuestions.unshift({
        time: event.time,
        question: event.question,
        answer: event.answer,
        answerSource: event.answerSource,
        source: event.source,
        target: event.target,
      });
      this.summary.recentQuestions = this.summary.recentQuestions.slice(0, 80);
    }

    if (!event.ok) {
      this.summary.recentErrors.unshift({
        time: event.time,
        question: event.question,
        error: event.error,
        source: event.source,
      });
      this.summary.recentErrors = this.summary.recentErrors.slice(0, 30);
    }
  }

  _extractKeywords(text) {
    const normalized = text
      .toLowerCase()
      .replace(/[，。！？、；："'“”‘’（）()\[\]{}<>《》,.!?;:]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();

    const keywords = new Set();
    for (const token of normalized.match(/[a-z0-9][a-z0-9_-]{1,}/g) || []) {
      if (!STOPWORDS.has(token)) keywords.add(token);
    }

    for (const term of INTEREST_TERMS) {
      if (normalized.includes(term.toLowerCase())) keywords.add(term);
    }

    for (const chunk of normalized.match(/[\p{Script=Han}]{2,}/gu) || []) {
      const cleaned = [...STOPWORDS].reduce((value, word) => value.replaceAll(word, ' '), chunk).trim();
      if (cleaned.length >= 2 && cleaned.length <= 8 && !STOPWORDS.has(cleaned)) keywords.add(cleaned);

      if (chunk.length <= 8) {
        for (const size of [3, 4]) {
          for (let i = 0; i <= chunk.length - size; i++) {
            const word = chunk.slice(i, i + size);
            if (!STOPWORDS.has(word)) keywords.add(word);
          }
        }
      }
    }

    return [...keywords].slice(0, 30);
  }

  _readEvents() {
    if (!fs.existsSync(this.eventsPath)) return [];
    const events = [];
    for (const line of fs.readFileSync(this.eventsPath, 'utf8').split(/\r?\n/)) {
      if (!line.trim()) continue;
      try {
        const event = JSON.parse(line);
        if (event && typeof event === 'object' && !Array.isArray(event)) events.push(event);
      } catch {}
    }
    return events;
  }

  _normalizeAnalyticsDay(value, fieldName) {
    const text = String(value || '').trim();
    if (!text) return null;
    const date = /^\d{4}-\d{2}-\d{2}$/.test(text) ? new Date(`${text}T00:00:00.000Z`) : new Date(text);
    const day = Number.isNaN(date.getTime()) ? '' : date.toISOString().slice(0, 10);
    if (!day || (/^\d{4}-\d{2}-\d{2}$/.test(text) && day !== text)) {
      const error = new Error(`${fieldName} must be a valid date`);
      error.statusCode = 400;
      throw error;
    }
    return day;
  }

  _analyticsEventDay(event) {
    const storedDay = String(event.day || '').trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(storedDay)) {
      const date = new Date(`${storedDay}T00:00:00.000Z`);
      if (!Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === storedDay) return storedDay;
    }
    const date = new Date(event.time || '');
    return Number.isNaN(date.getTime()) ? null : date.toISOString().slice(0, 10);
  }

  _inc(map, key) {
    if (!key) return;
    map[key] = (map[key] || 0) + 1;
  }

  _topEntries(map, limit) {
    return Object.entries(map || {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, limit);
  }

  _saveSummary() {
    fs.writeFileSync(this.summaryPath, JSON.stringify(this.summary, null, 2), 'utf8');
  }
}

export const usageStore = new UsageStore();
export default usageStore;
