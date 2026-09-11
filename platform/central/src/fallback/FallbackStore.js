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

function normalizeList(value) {
  const rows = Array.isArray(value) ? value : String(value || '').split(/[,，\n]/);
  return [...new Set(rows.map(item => String(item || '').trim()).filter(Boolean))];
}

function clampNumber(value, min, max, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(min, Math.min(max, number)) : fallback;
}

const DEFAULT_RULES = [
  {
    name: '价格与商务咨询',
    category: '商务',
    keywords: ['多少钱', '怎么收费', '价格', '报价', '预算', '费用'],
    answer: '价格需要结合建设范围、系统接入、设备数量和服务要求进行测算。可以先确认具体需求，再由现场专家提供针对性的方案和报价。',
    actionType: 'handoff',
  },
  {
    name: '建设周期与交付',
    category: '工期',
    keywords: ['多久上线', '多久可以上线', '多长时间上线', '什么时候上线', '工期', '交付周期', '建设周期'],
    answer: '建设周期取决于数据接入、系统改造、现场部署和测试范围。通常建议先确定试点范围，再结合现场条件制定分阶段交付计划。',
    actionType: 'handoff',
  },
  {
    name: '指标与效果承诺',
    category: '指标',
    keywords: ['能保证吗', '准确率保证', '提升比例', '效果保证', '固定效果', '承诺效果'],
    answer: '展示中的指标用于说明能力和案例成效，实际效果会受到数据质量、部署范围、现场条件和验收标准影响，建议通过试点进行验证。',
    actionType: 'answer',
  },
  {
    name: '数据安全与个人隐私',
    category: '数据安全',
    keywords: ['数据外传', '数据会不会外传', '数据安全', '用户隐私', '个人隐私', '数据归属', '人脸指纹'],
    answer: '方案会按照使用目的和最小必要原则设计数据范围，并结合本地化部署、权限控制、日志审计和数据安全要求明确授权、存储及使用边界。',
    actionType: 'answer',
  },
  {
    name: '未公开案例与合同细节',
    category: '案例边界',
    keywords: ['客户合同', '合同范围', '内部数据', '案例细节', '未公开案例'],
    answer: '现场可以介绍已经公开的案例能力和应用成效，未公开的客户信息、合同条款及内部数据不在现场展开，具体内容可由案例负责人进一步沟通。',
    actionType: 'handoff',
  },
  {
    name: '技术接口与系统对接',
    category: '技术对接',
    keywords: ['怎么接入', '系统对接', '技术接口', '接口条件', '支持哪些车型'],
    answer: '接入方式需要结合现有系统架构、接口条件、账号权限和数据流程进行设计，可以先梳理当前系统和目标场景，再由技术人员确认对接方案。',
    actionType: 'handoff',
  },
  {
    name: 'AI替代人工边界',
    category: 'AI边界',
    keywords: ['替代人工', '替代执法', '自动清退', '完全无人'],
    answer: 'AI主要用于分析、预警、报告和流程辅助，不替代工作人员的专业判断、执法决定及关键处置，最终决策仍由相关人员按制度完成。',
    actionType: 'answer',
  },
];

class FallbackStore {
  constructor() {
    this.filePath = path.join(config.DATA_DIR, 'fallback-rules.json');
    this._data = null;
  }

  list() {
    return this._load().items;
  }

  save(input) {
    const data = this._load();
    const now = new Date().toISOString();
    const id = String(input.id || randomUUID());
    const item = {
      id,
      name: String(input.name || '').trim(),
      category: String(input.category || '通用').trim() || '通用',
      keywords: normalizeList(input.keywords),
      excludeKeywords: normalizeList(input.excludeKeywords),
      answer: String(input.answer || '').trim(),
      matchMode: input.matchMode === 'all' ? 'all' : 'any',
      minKeywordHits: Math.round(clampNumber(input.minKeywordHits, 1, 20, 1)),
      threshold: Math.round(clampNumber(input.threshold, 0, 100, 60)),
      priority: Math.round(clampNumber(input.priority, 0, 999, 100)),
      scopeType: ['global', 'ppt', 'special', 'screen'].includes(input.scopeType) ? input.scopeType : 'global',
      scopeValue: String(input.scopeValue || '').trim(),
      actionType: ['answer', 'handoff', 'redirect'].includes(input.actionType) ? input.actionType : 'answer',
      enabled: input.enabled !== false,
      updatedAt: now,
      createdAt: input.createdAt || now,
    };

    if (!item.name || !item.answer || !item.keywords.length) {
      throw new Error('name, answer and keywords are required');
    }
    if (item.scopeType !== 'global' && !item.scopeValue) {
      throw new Error('scopeValue is required for non-global rules');
    }

    const index = data.items.findIndex(row => row.id === id);
    if (index >= 0) data.items[index] = { ...data.items[index], ...item };
    else data.items.push(item);
    this._save(data);
    return item;
  }

  remove(id) {
    const data = this._load();
    const before = data.items.length;
    data.items = data.items.filter(item => item.id !== String(id || ''));
    if (data.items.length !== before) this._save(data);
    return data.items.length !== before;
  }

  findMatch(text, context = {}) {
    const query = normalizeText(text);
    if (!query) return null;

    const matches = this.list()
      .filter(item => item.enabled !== false && this._scopeMatches(item, context))
      .map(item => this._score(item, query))
      .filter(Boolean)
      .sort((a, b) => b.item.priority - a.item.priority || b.score - a.score || b.hits - a.hits);
    return matches[0] || null;
  }

  _scopeMatches(item, context) {
    if (item.scopeType === 'global') return true;
    const expected = normalizeText(item.scopeValue);
    if (!expected) return false;
    if (item.scopeType === 'screen') return normalizeText(context.target) === expected;
    if (item.scopeType === 'ppt') {
      return normalizeText(`${context.currentPptFileName || ''} ${context.currentPptTitle || ''}`).includes(expected);
    }
    if (item.scopeType === 'special') return normalizeText(context.activeSpecialName).includes(expected);
    return false;
  }

  _score(item, query) {
    const excluded = (item.excludeKeywords || []).map(normalizeText).filter(Boolean);
    if (excluded.some(keyword => query.includes(keyword))) return null;

    const keywords = (item.keywords || []).map(normalizeText).filter(Boolean);
    const matched = keywords.filter(keyword => query.includes(keyword));
    const requiredHits = item.matchMode === 'all'
      ? keywords.length
      : Math.min(keywords.length, Math.max(1, Number(item.minKeywordHits) || 1));
    if (!keywords.length || matched.length < requiredHits) return null;

    const longest = matched.reduce((max, keyword) => Math.max(max, keyword.length), 0);
    const score = Math.min(100, 55 + Math.min(25, longest * 4) + Math.max(0, matched.length - 1) * 10);
    if (score < Number(item.threshold || 0)) return null;
    return { item, score, hits: matched.length, matchedKeywords: matched };
  }

  _load() {
    if (this._data) return this._data;
    try {
      if (fs.existsSync(this.filePath)) {
        const content = fs.readFileSync(this.filePath, 'utf8').replace(/^\uFEFF/, '');
        const parsed = JSON.parse(content);
        this._data = { items: Array.isArray(parsed) ? parsed : parsed.items || [] };
        return this._data;
      }
    } catch (err) {
      console.warn(`[FallbackStore] failed to read fallback rules: ${err.message}`);
    }

    const now = new Date().toISOString();
    this._data = {
      items: DEFAULT_RULES.map((item, index) => ({
        id: randomUUID(),
        ...item,
        excludeKeywords: [],
        matchMode: 'any',
        minKeywordHits: 1,
        threshold: 60,
        priority: 100 - index,
        scopeType: 'global',
        scopeValue: '',
        enabled: true,
        createdAt: now,
        updatedAt: now,
      })),
    };
    this._save(this._data);
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

export const fallbackStore = new FallbackStore();
export default fallbackStore;
