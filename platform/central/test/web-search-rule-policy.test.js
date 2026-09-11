import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { WebSearchIntent } from '../src/websearch/WebSearchIntent.js';
import { UsageStore } from '../src/usage/UsageStore.js';

function withIntent(run) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'web-rule-policy-'));
  try {
    return run(new WebSearchIntent({ filePath: path.join(dir, 'rules.json') }), dir);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

test('year-event rule exposes rolling adjacent years without persisting fixed years', () => {
  withIntent((intent, dir) => {
    const currentYear = new Date().getFullYear();
    const rule = intent.list().find(item => item.id === 'builtin-year-event-v2');
    assert.deepEqual(rule.dynamicYears, [currentYear - 1, currentYear, currentYear + 1].map(String));
    for (const year of rule.dynamicYears) assert.ok(rule.keywords.includes(year));

    const persisted = JSON.parse(fs.readFileSync(path.join(dir, 'rules.json'), 'utf8'));
    const storedRule = persisted.items.find(item => item.id === 'builtin-year-event-v2');
    assert.equal(storedRule.keywords.some(item => /^20\d{2}$/.test(item)), false);
  });
});

test('schema migration removes stale fixed years and standalone publish keyword', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'web-rule-migration-'));
  try {
    const filePath = path.join(dir, 'rules.json');
    fs.writeFileSync(filePath, JSON.stringify({
      schemaVersion: 3,
      builtinRuleVersion: 2,
      items: [
        {
          id: 'builtin-year-event-v2',
          name: '年份实时事件',
          keywords: ['2025', '2026', '最近'],
          enabled: true,
        },
        {
          id: 'builtin-policy-update-v1',
          name: '政策与行业更新',
          keywords: ['发布', '法规'],
          enabled: true,
        },
      ],
    }), 'utf8');

    const intent = new WebSearchIntent({ filePath });
    intent.list();
    const persisted = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    const yearRule = persisted.items.find(item => item.id === 'builtin-year-event-v2');
    const policyRule = persisted.items.find(item => item.id === 'builtin-policy-update-v1');
    assert.equal(yearRule.keywords.some(item => /^20\d{2}$/.test(item)), false);
    assert.equal(policyRule.keywords.includes('发布'), false);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('recent year requires an event subject or factual relation', () => {
  withIntent(intent => {
    const year = new Date().getFullYear();
    assert.equal(intent.classifyRoute(`${year}年世界杯冠军是哪个国家？`).mode, 'MUST_WEB');
    assert.equal(intent.classifyRoute(`${year}年奥斯卡什么时候举行？`).mode, 'MUST_WEB');
    assert.equal(intent.classifyRoute(`${year}年欢迎词`).mode, 'NONE');
  });
});

test('broad temporal and publish words do not hijack content commands', () => {
  withIntent(intent => {
    const negatives = [
      '现在帮我发布一段欢迎词',
      '请帮我写最新产品欢迎文案',
      '最近帮我总结一下这个方案',
    ];
    for (const question of negatives) {
      assert.equal(intent.classifyRoute(question).mode, 'NONE', question);
    }
  });
});

test('temporal signal plus factual relation still reaches web without entity-specific rules', () => {
  withIntent(intent => {
    const positives = [
      '云帆公司最近发布了什么产品？',
      '当前美元兑人民币汇率是多少？',
      '最近一届星海杯冠军是谁？',
      '今天有什么重要新闻？',
    ];
    for (const question of positives) {
      assert.notEqual(intent.classifyRoute(question).mode, 'NONE', question);
    }
  });
});

test('rule statistics count each interaction once even when the rule appears twice in trace', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'web-rule-stats-'));
  try {
    const store = new UsageStore({ dataDir: dir });
    store.recordInteraction({
      question: '测试问题一',
      answer: '答案',
      finalRoute: 'WEB_ANSWERED',
      webSearchStatus: 'ANSWERED',
      routeTrace: [
        { handler: 'route_policy', matched: true, ruleId: 'rule-a', ruleName: 'A' },
        { handler: 'web_search_intent', matched: true, ruleId: 'rule-a', ruleName: 'A' },
      ],
    });
    store.recordInteraction({
      question: '测试问题二',
      answer: '未回答',
      finalRoute: 'WEB_ABSTAINED',
      webSearchStatus: 'INSUFFICIENT_EVIDENCE',
      routeTrace: [{ handler: 'route_policy', matched: true, ruleId: 'rule-a', ruleName: 'A' }],
    });

    const stats = store.getWebSearchRuleStats(['rule-a']).byRule['rule-a'];
    assert.deepEqual(stats, {
      matched: 2,
      answered: 1,
      notAnswered: 1,
      failed: 0,
      successRate: 50,
    });
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
