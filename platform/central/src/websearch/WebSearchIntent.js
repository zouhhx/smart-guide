import fs from 'fs';
import path from 'path';
import { randomUUID } from 'crypto';
import { config } from '../config/index.js';
import { detectFreshnessIntent } from './FreshnessIntentDetector.js';
import { detectDynamicEventIntent } from './DynamicEventIntentDetector.js';

const SCHEMA_VERSION = 4;
const BUILTIN_RULE_VERSION = 3;

function normalizeText(value) {
  return String(value || '')
    .toLowerCase()
    .replace(/\s+/g, '')
    .replace(/[，。！？、,.!?;；:"'“”‘’（）()[\]{}<>《》【】]/g, '');
}

function normalizeList(value) {
  const rows = Array.isArray(value) ? value : String(value || '').split(/[,，\n]/);
  return [...new Set(rows.map(item => String(item || '').trim()).filter(Boolean))];
}

function normalizePatterns(value) {
  const rows = Array.isArray(value) ? value : String(value || '').split(/\r?\n/);
  return [...new Set(rows.map(item => String(item || '').trim()).filter(Boolean))];
}

function clampNumber(value, min, max, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(min, Math.min(max, number)) : fallback;
}

const HARD_BLOCK_PATTERNS = [
  /(打开|播放|展示)\s*第?\s*[一二三四五六七八九十两\d]+\s*个/,
  /(下一页|上一页|翻页|返回首页|黑屏|节能|关闭ppt|关闭PPT)/,
  /(切换|进入|打开).*(专场|大模型|小龙虾|ollama|openclaw)/i,
  /(讲一下|讲讲|介绍一下|介绍下).*(当前|这页|这个)ppt/i,
  /(?:这个|本展项|本方案|方案|产品|系统|平台|项目).{0,24}(?:价格|报价|预算|多少钱|费用|收费|数据安全|隐私|外传|工期|交付周期|能保证吗|效果保证)/,
  /(?:这个|本展项|本方案|方案|产品|系统|平台).{0,24}(?:是什么|有什么价值|有什么用|解决什么|方案优势|核心能力)$/,
  /^(?:现在|当前|目前)?(?:请|麻烦)?(?:帮我|给我|替我)?.{0,16}(?:写|改写|润色|翻译|总结|生成|创作|设计|制作|发送|打开|播放|切换|展示|发布)(?:一下|一段|一个|这段|这个|内容|文案|欢迎词)/i,
];

const TIME_ANCHOR_PATTERN = /(?:20\d{2}年?|今年|本周|本月|近期|最近|最新|今天|昨天|刚刚|实时|现在|当前|目前|截至|截止到)/i;
const EXPLICIT_NEWS_PATTERN = /(?:新闻|热搜|快讯|最新消息|最新动态)/i;
const FACT_RELATION_PATTERN = /(?:现任|负责人|董事长|首席执行官|ceo|发布|推出|公布|上线|上市|融资|财报|股价|价格|汇率|利率|政策|法规|通知|征集|标准|结果|冠军|获奖|得主|胜出|夺冠|比分|赛程|开球|开踢|决赛|开幕|举行|上映|通车|开通|状态|进展|排名|名单|任职|当选|数量|多少|何时|什么时候|几点|日期|是否|有没有|了吗)/i;
const EVENT_SUBJECT_PATTERN = /(?:世界杯|奥运会|赛事|比赛|联赛|杯赛|颁奖|奖项|奥斯卡|电影节|大会|峰会|发布会|选举|政策|法规|财报|汇率|股价|黄金|地铁|城际|铁路|航班)/i;

const DEFAULT_RULES = [
  {
    id: 'builtin-event-result-v1',
    name: '届次与事件结果',
    category: '实时',
    keywords: [],
    matchPatterns: [
      '第\\s*\\d+\\s*届.{0,24}(最佳|获奖|得主|冠军|结果|名单|排名)',
      '(本届|上一届|这一届|上届|最新一届).{0,24}(最佳|获奖|得主|冠军|结果|名单|排名)',
      '20\\d{2}年.{0,24}(最佳|获奖|得主|冠军|结果|名单|排名)',
      '(谁获得|谁赢得|谁夺得|哪部电影获得|最终结果|获奖名单|最佳男主角|最佳女主角)',
    ],
    excludeKeywords: ['来访时间'],
    queryTemplate: '{question}',
    routeMode: 'MUST_WEB',
    recencyDays: 0,
    maxResults: 8,
    answerPrefix: '根据公开结果',
  },
  {
    id: 'builtin-year-event-v2',
    name: '年份实时事件',
    category: '实时',
    keywords: ['今年', '本周', '本月', '近期', '最近'],
    matchPatterns: [
      '20\\d{2}年.{0,24}(决赛|开球|开踢|开幕|举行|颁奖|获奖|结果|时间|几点)',
    ],
    excludeKeywords: ['2026-06-10', '来访时间'],
    queryTemplate: '{question}',
    routeMode: 'MUST_WEB',
    recencyDays: 0,
    maxResults: 8,
    answerPrefix: '根据近期公开信息',
  },
  {
    id: 'builtin-news-hot-v1',
    name: '最新新闻与热点',
    category: '新闻',
    keywords: ['最新', '新闻', '热搜', '刚刚', '今天', '昨天', '实时', '快讯'],
    matchPatterns: [],
    excludeKeywords: ['打开', '播放', '下一页', '切换专场', '当前ppt'],
    queryTemplate: '{question}',
    routeMode: 'PREFER_WEB',
    recencyDays: 7,
    maxResults: 5,
    answerPrefix: '根据最新公开信息',
  },
  {
    id: 'builtin-policy-update-v1',
    name: '政策与行业更新',
    category: '政策',
    keywords: ['政策更新', '新政策', '最新政策', '法规', '通知', '征求意见'],
    matchPatterns: ['(现行|最新).{0,12}(政策|法规|规定|标准)'],
    excludeKeywords: ['这个方案', '当前ppt'],
    queryTemplate: '{question}',
    routeMode: 'PREFER_WEB',
    recencyDays: 30,
    maxResults: 5,
    answerPrefix: '根据公开政策信息',
  },
  {
    id: 'builtin-company-update-v1',
    name: '公司最新动态',
    category: '企业',
    keywords: ['最新动态', '最新消息', '最近怎么样', '现在怎么样', '融资', '发布会', '财报', '股价'],
    matchPatterns: [],
    excludeKeywords: ['价格多少钱', '怎么收费'],
    queryTemplate: '{question}',
    routeMode: 'PREFER_WEB',
    recencyDays: 30,
    maxResults: 5,
    answerPrefix: '根据公开资料',
  },
];

export class WebSearchIntent {
  constructor(options = {}) {
    this.filePath = options.filePath || path.join(config.DATA_DIR, 'web-search-rules.json');
    this._data = null;
  }

  list() {
    return this._load().items.map(item => this._withRuntimePolicy(item));
  }

  save(input) {
    const data = this._load();
    const now = new Date().toISOString();
    const id = String(input.id || randomUUID());
    const item = {
      id,
      name: String(input.name || '').trim(),
      category: String(input.category || '实时').trim() || '实时',
      keywords: this._normalizeStoredKeywords(id, input.keywords),
      matchPatterns: normalizePatterns(input.matchPatterns),
      excludeKeywords: normalizeList(input.excludeKeywords),
      queryTemplate: String(input.queryTemplate || '{question}').trim() || '{question}',
      matchMode: input.matchMode === 'all' ? 'all' : 'any',
      minKeywordHits: Math.round(clampNumber(input.minKeywordHits, 1, 20, 1)),
      threshold: Math.round(clampNumber(input.threshold, 0, 100, 60)),
      priority: Math.round(clampNumber(input.priority, 0, 999, 100)),
      routeMode: input.routeMode === 'MUST_WEB' ? 'MUST_WEB' : 'PREFER_WEB',
      recencyDays: Math.round(clampNumber(input.recencyDays, 0, 3650, 30)),
      maxResults: Math.round(clampNumber(input.maxResults, 1, 10, 5)),
      answerPrefix: String(input.answerPrefix || '根据最新公开信息').trim(),
      enabled: input.enabled !== false,
      updatedAt: now,
      createdAt: input.createdAt || now,
    };

    if (!item.name || (!item.keywords.length && !item.matchPatterns.length)) {
      throw new Error('name and keywords or matchPatterns are required');
    }

    const index = data.items.findIndex(row => row.id === id);
    if (index >= 0) data.items[index] = { ...data.items[index], ...item };
    else data.items.push(item);
    this._save(data);
    return item;
  }

  remove(id) {
    const data = this._load();
    const normalizedId = String(id || '');
    const index = data.items.findIndex(item => item.id === normalizedId);
    if (index < 0) return false;
    if (normalizedId.startsWith('builtin-')) {
      data.items[index] = { ...data.items[index], enabled: false, updatedAt: new Date().toISOString() };
    } else {
      data.items.splice(index, 1);
    }
    this._save(data);
    return true;
  }

  shouldSearch(text, context = {}) {
    const rawText = String(text || '').trim();
    const query = normalizeText(rawText);
    if (!query || this._isBlocked(rawText, query)) return null;

    const matches = this.list()
      .filter(item => item.enabled !== false)
      .map(item => this._score(item, query, rawText))
      .filter(Boolean)
      .sort((a, b) => b.item.priority - a.item.priority || b.score - a.score || b.hits - a.hits);

    const match = matches[0] || null;
    if (!match) return null;

    const renderedQuery = this._renderQuery(match.item.queryTemplate, {
      question: rawText,
      target: context.target || '',
    });

    return {
      ...match,
      routeMode: match.item.routeMode === 'MUST_WEB' ? 'MUST_WEB' : 'PREFER_WEB',
      query: renderedQuery || rawText,
      options: {
        maxResults: match.item.maxResults || 5,
        recencyDays: match.item.recencyDays || 0,
      },
    };
  }

  classifyRoute(text, context = {}) {
    const rawText = String(text || '').trim();
    const normalized = normalizeText(rawText);
    if (!normalized || this._isBlocked(rawText, normalized)) {
      return { mode: 'NONE', reason: normalized ? 'HARD_BLOCK' : 'EMPTY_QUERY', intent: null };
    }

    const intent = this.shouldSearch(rawText, context);
    const freshness = detectFreshnessIntent(rawText);
    if (freshness) {
      return {
        mode: freshness.mode,
        reason: freshness.reason,
        intent: intent || this._syntheticIntent(rawText, freshness.reason),
      };
    }
    const dynamicEvent = detectDynamicEventIntent(rawText);
    if (dynamicEvent) {
      return {
        mode: dynamicEvent.mode,
        reason: dynamicEvent.reason,
        intent: intent || this._syntheticIntent(rawText, dynamicEvent.reason),
      };
    }
    const mustReason = this._detectMustWeb(rawText);
    if (mustReason) {
      return {
        mode: 'MUST_WEB',
        reason: mustReason,
        intent: intent || this._syntheticIntent(rawText, mustReason),
      };
    }
    if (intent) {
      return {
        mode: intent.routeMode === 'MUST_WEB' ? 'MUST_WEB' : 'PREFER_WEB',
        reason: `RULE:${intent.item.id}`,
        intent,
      };
    }
    return { mode: 'NONE', reason: 'NO_MATCH', intent: null };
  }

  _detectMustWeb(text) {
    const normalized = String(text || '').replace(/\s+/g, '');
    const years = [...normalized.matchAll(/20\d{2}/g)].map(match => Number(match[0]));
    const recentYearFloor = new Date().getFullYear() - 1;
    if (
      years.some(year => year >= recentYearFloor)
      && (FACT_RELATION_PATTERN.test(normalized) || EVENT_SUBJECT_PATTERN.test(normalized))
    ) return 'RECENT_YEAR';
    if (/第\d+届/.test(normalized) && /(最佳|获奖|得主|冠军|结果|名单|排名)/.test(normalized)) {
      return 'ORDINAL_EVENT_RESULT';
    }
    if (/(获奖|得主|冠军|比赛结果|比分|现任|最新价格|最新政策|财报)/.test(normalized)) {
      return 'CURRENT_EVENT_OUTCOME';
    }
    if (
      /(开球|开踢|决赛|开幕|上映|发布会|举行).{0,16}(时间|几点|日期|什么时候)/.test(normalized)
      || /(时间|几点|日期|什么时候).{0,16}(开球|开踢|决赛|开幕|上映|发布会|举行)/.test(normalized)
    ) {
      return 'EVENT_SCHEDULE';
    }
    return '';
  }

  _syntheticIntent(question, reason) {
    const currentFact = reason === 'EXPLICIT_CURRENT_FACT';
    const dynamicEvent = reason === 'DYNAMIC_EVENT_STATE';
    const timeBoundEventFact = reason === 'TIME_BOUND_EVENT_FACT';
    return {
      item: {
        id: `builtin-router-${String(reason || 'must-web').toLowerCase()}`,
        name: currentFact
          ? '通用当前事实'
          : dynamicEvent
            ? '动态事件状态'
            : timeBoundEventFact
              ? '近期事件属性'
              : '前置联网路由',
        category: '实时',
        routeMode: 'MUST_WEB',
        recencyDays: currentFact ? 30 : 0,
        maxResults: currentFact || dynamicEvent || timeBoundEventFact ? 6 : 8,
        answerPrefix: '根据公开信息',
      },
      score: 100,
      hits: 1,
      matchedKeywords: [],
      matchedPatterns: [reason],
      routeMode: 'MUST_WEB',
      query: question,
      options: {
        maxResults: currentFact || dynamicEvent || timeBoundEventFact ? 6 : 8,
        recencyDays: currentFact ? 30 : 0,
      },
    };
  }

  _isBlocked(rawText, normalized) {
    const text = String(rawText || '');
    return HARD_BLOCK_PATTERNS.some(pattern => pattern.test(text) || pattern.test(normalized));
  }

  _score(item, query, rawText) {
    const excluded = (item.excludeKeywords || []).map(normalizeText).filter(Boolean);
    if (excluded.some(keyword => query.includes(keyword))) return null;

    const keywords = (item.keywords || []).map(normalizeText).filter(Boolean);
    const matched = keywords.filter(keyword => query.includes(keyword));
    const patterns = normalizePatterns(item.matchPatterns);
    const matchedPatterns = patterns.filter(source => {
      try {
        return new RegExp(source, 'i').test(rawText);
      } catch (err) {
        console.warn(`[WebSearchIntent] invalid match pattern in ${item.id}: ${err.message}`);
        return false;
      }
    });
    const requiredHits = item.matchMode === 'all'
      ? keywords.length
      : Math.min(keywords.length, Math.max(1, Number(item.minKeywordHits) || 1));
    const keywordMatched = keywords.length > 0 && matched.length >= requiredHits;
    if (!keywordMatched && !matchedPatterns.length) return null;
    if (!this._passesCompositionalPolicy(item, rawText, matched, matchedPatterns)) return null;

    const longest = matched.reduce((max, keyword) => Math.max(max, keyword.length), 0);
    const keywordScore = keywordMatched
      ? 55 + Math.min(25, longest * 4) + Math.max(0, matched.length - 1) * 10
      : 0;
    const score = Math.min(100, Math.max(keywordScore, matchedPatterns.length ? 96 : 0));
    if (score < Number(item.threshold || 0)) return null;
    return {
      item,
      score,
      hits: matched.length + matchedPatterns.length,
      matchedKeywords: matched,
      matchedPatterns,
    };
  }

  _renderQuery(template, values) {
    return String(template || '{question}')
      .replaceAll('{question}', values.question || '')
      .replaceAll('{target}', values.target || '')
      .trim();
  }

  _normalizeRule(item, index, now) {
    const id = String(item.id || randomUUID());
    return {
      ...item,
      id,
      name: String(item.name || '').trim(),
      category: String(item.category || '实时').trim() || '实时',
      keywords: this._normalizeStoredKeywords(id, item.keywords),
      matchPatterns: normalizePatterns(item.matchPatterns),
      excludeKeywords: normalizeList(item.excludeKeywords),
      queryTemplate: String(item.queryTemplate || '{question}').trim() || '{question}',
      matchMode: item.matchMode === 'all' ? 'all' : 'any',
      minKeywordHits: Math.round(clampNumber(item.minKeywordHits, 1, 20, 1)),
      threshold: Math.round(clampNumber(item.threshold, 0, 100, 60)),
      priority: Math.round(clampNumber(item.priority, 0, 999, 200 - index)),
      routeMode: item.routeMode === 'MUST_WEB' ? 'MUST_WEB' : 'PREFER_WEB',
      recencyDays: Math.round(clampNumber(item.recencyDays, 0, 3650, 30)),
      maxResults: Math.round(clampNumber(item.maxResults, 1, 10, 5)),
      answerPrefix: String(item.answerPrefix || '根据最新公开信息').trim(),
      enabled: item.enabled !== false,
      createdAt: item.createdAt || now,
      updatedAt: item.updatedAt || now,
    };
  }

  _normalizeStoredKeywords(id, keywords) {
    const items = normalizeList(keywords);
    if (id === 'builtin-year-event-v2') {
      return items.filter(item => !/^20\d{2}$/.test(item));
    }
    if (id === 'builtin-policy-update-v1') {
      return items.filter(item => item !== '发布');
    }
    return items;
  }

  _withRuntimePolicy(item) {
    if (item.id !== 'builtin-year-event-v2') return { ...item };
    const currentYear = new Date().getFullYear();
    const rollingYears = [currentYear - 1, currentYear, currentYear + 1].map(String);
    return {
      ...item,
      keywords: normalizeList([...(item.keywords || []), ...rollingYears]),
      dynamicYears: rollingYears,
    };
  }

  _passesCompositionalPolicy(item, rawText, matchedKeywords, matchedPatterns) {
    if (matchedPatterns.length) return true;
    const text = String(rawText || '');
    const matched = new Set(matchedKeywords);
    const hasFactRelation = FACT_RELATION_PATTERN.test(text);
    const hasEventSubject = EVENT_SUBJECT_PATTERN.test(text);

    if (item.id === 'builtin-year-event-v2') {
      return TIME_ANCHOR_PATTERN.test(text) && (hasFactRelation || hasEventSubject);
    }
    if (item.id === 'builtin-news-hot-v1') {
      const explicitNews = EXPLICIT_NEWS_PATTERN.test(text)
        || [...matched].some(keyword => /(?:新闻|热搜|快讯|最新消息|最新动态)/i.test(keyword));
      return explicitNews || hasFactRelation || hasEventSubject;
    }
    if (item.id === 'builtin-company-update-v1') {
      return hasFactRelation || EXPLICIT_NEWS_PATTERN.test(text);
    }
    return true;
  }

  _mergeSaved(parsed) {
    const now = new Date().toISOString();
    const rawItems = Array.isArray(parsed) ? parsed : (Array.isArray(parsed?.items) ? parsed.items : []);
    const consumed = new Set();
    const stableSchema = Number(parsed?.schemaVersion || 0) >= SCHEMA_VERSION;
    const builtins = DEFAULT_RULES.map((builtin, index) => {
      const savedIndex = rawItems.findIndex(item => item?.id === builtin.id || item?.name === builtin.name);
      if (savedIndex < 0) return this._normalizeRule(builtin, index, now);
      consumed.add(savedIndex);
      const saved = rawItems[savedIndex] || {};
      const stableRule = stableSchema && saved.id === builtin.id;
      const merged = stableRule
        ? { ...builtin, ...saved, id: builtin.id }
        : {
            ...builtin,
            ...saved,
            id: builtin.id,
            keywords: normalizeList([...(builtin.keywords || []), ...(saved.keywords || [])]),
            matchPatterns: normalizePatterns([...(builtin.matchPatterns || []), ...(saved.matchPatterns || [])]),
          };
      return this._normalizeRule(merged, index, now);
    });
    const custom = rawItems
      .map((item, index) => ({ item, index }))
      .filter(row => !consumed.has(row.index) && !DEFAULT_RULES.some(rule => rule.id === row.item?.id))
      .map((row, index) => this._normalizeRule(row.item || {}, DEFAULT_RULES.length + index, now))
      .filter(item => item.name && (item.keywords.length || item.matchPatterns.length));
    return {
      schemaVersion: SCHEMA_VERSION,
      builtinRuleVersion: BUILTIN_RULE_VERSION,
      items: [...builtins, ...custom],
    };
  }

  _load() {
    if (this._data) return this._data;
    let parsed = null;
    try {
      if (fs.existsSync(this.filePath)) {
        const content = fs.readFileSync(this.filePath, 'utf8').replace(/^\uFEFF/, '');
        parsed = JSON.parse(content);
      }
    } catch (err) {
      console.warn(`[WebSearchIntent] failed to read web search rules: ${err.message}`);
    }

    this._data = this._mergeSaved(parsed);
    const needsMigration = !parsed
      || Number(parsed?.schemaVersion || 0) !== SCHEMA_VERSION
      || Number(parsed?.builtinRuleVersion || 0) !== BUILTIN_RULE_VERSION
      || this._data.items.some(item => !Array.isArray(item.matchPatterns) || !item.routeMode);
    if (needsMigration) this._save(this._data);
    return this._data;
  }

  _save(data) {
    const normalized = {
      schemaVersion: SCHEMA_VERSION,
      builtinRuleVersion: BUILTIN_RULE_VERSION,
      items: data.items,
    };
    fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
    fs.writeFileSync(this.filePath, JSON.stringify({
      ...normalized,
      updatedAt: new Date().toISOString(),
    }, null, 2), 'utf8');
    this._data = normalized;
  }
}

export const webSearchIntent = new WebSearchIntent();
export default webSearchIntent;
