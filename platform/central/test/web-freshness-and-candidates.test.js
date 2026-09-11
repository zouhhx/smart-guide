import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { detectFreshnessIntent } from '../src/websearch/FreshnessIntentDetector.js';
import { WebSearchIntent } from '../src/websearch/WebSearchIntent.js';
import { WebQaCandidateStore } from '../src/qa/WebQaCandidateStore.js';
import {
  findApprovedWebQaMatch,
  promoteWebQaCandidate,
} from '../src/qa/WebQaPromotionService.js';
import { QaStore } from '../src/qa/QaStore.js';
import { shouldRetryWebAfterLlm } from '../src/websearch/LlmFreshnessFallback.js';

test('generic current-fact forms route to web without entity keyword rules', () => {
  const positives = [
    '现在的星河公司负责人是谁？',
    '云帆产品目前发布了吗',
    '马鞍山和南京现在通地铁了吗',
    '截至今天北方港口的运营状态怎么样？',
    '当前这家机构有多少名员工？',
  ];
  for (const question of positives) {
    assert.equal(detectFreshnessIntent(question)?.reason, 'EXPLICIT_CURRENT_FACT', question);
  }
});

test('commands, writing, deterministic time and local presentation context stay out of web', () => {
  const negatives = [
    '现在帮我写一段欢迎词',
    '现在打开宣传片',
    '现在北京时间几点',
    '南京当前天气怎么样',
    '介绍一下当前PPT',
    '当前这一页讲了什么',
  ];
  for (const question of negatives) {
    assert.equal(detectFreshnessIntent(question), null, question);
  }
});

test('current-fact detector integrates with existing WebSearchIntent as MUST_WEB', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'web-intent-'));
  const intent = new WebSearchIntent({ filePath: path.join(dir, 'rules.json') });
  const route = intent.classifyRoute('现在的陌生品牌负责人是谁？');
  assert.equal(route.mode, 'MUST_WEB');
  assert.equal(route.reason, 'EXPLICIT_CURRENT_FACT');
  assert.equal(route.intent.item.name, '通用当前事实');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('LLM knowledge-cutoff replies are eligible for one forced web fallback', () => {
  assert.equal(shouldRetryWebAfterLlm(
    '第99届颁奖礼最佳影片是什么？',
    '由于我的知识截止日期限制，无法提供最新结果。',
  ), true);
  assert.equal(shouldRetryWebAfterLlm(
    '帮我写一段未来城市欢迎词',
    '下面是一段欢迎词。',
  ), false);
});

test('public stable Web QA candidates do not require internal knowledge support', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'web-candidate-'));
  const store = new WebQaCandidateStore({ filePath: path.join(dir, 'candidates.json') });
  const input = {
    question: '海岳公司的总部在哪里？',
    answer: '海岳公司的总部位于南京。',
    sources: [{
      title: '公司介绍',
      url: 'https://example.gov.cn/company',
      domain: 'example.gov.cn',
      sourceType: 'official',
      authorityScore: 0.96,
    }],
  };
  assert.equal(store.record(input).eligibleForPromotion, false);
  assert.equal(store.record(input).eligibleForPromotion, false);
  const third = store.record(input);
  assert.equal(third.useCount, 3);
  assert.equal(third.candidateType, 'PUBLIC_STABLE');
  assert.equal(third.eligibilityPolicy.knowledgeRequired, false);
  assert.equal(third.eligibleForPromotion, true);
  assert.equal(store.list().length, 1);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('enterprise candidates still require internal knowledge support', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'web-enterprise-candidate-'));
  const store = new WebQaCandidateStore({ filePath: path.join(dir, 'candidates.json') });
  const input = {
    question: '我们公司内部的差旅报销上限是多少？',
    answer: '差旅报销上限为每晚500元。',
  };
  store.record(input);
  store.record(input);
  const unsupported = store.record(input);
  assert.equal(unsupported.candidateType, 'ENTERPRISE_KB');
  assert.equal(unsupported.eligibleForPromotion, false);
  assert.match(unsupported.unmetReasons.join(';'), /知识库支持/);

  const supported = store.record({
    ...input,
    knowledgeSupported: true,
    knowledgeSource: '企业知识库',
  });
  assert.equal(supported.knowledgeRequirementSatisfied, true);
  assert.equal(supported.eligibleForPromotion, true);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('time-bound public candidates use explicit year and candidate-level source verification', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'web-time-bound-candidate-'));
  const store = new WebQaCandidateStore({ filePath: path.join(dir, 'candidates.json') });
  const input = {
    question: '今年的上海世界人工智能大会是什么时候举办的？',
    answer: '2026年世界人工智能大会于7月17日至20日在上海举办。',
    sources: [{
      title: 'WAIC 2026',
      url: 'https://aiii.global/waic-2026',
      domain: 'aiii.global',
      sourceType: 'web',
      authorityScore: 0.5,
    }, {
      title: 'WAIC 2026 duplicate',
      url: 'https://aiii.global/waic-2026',
      domain: 'aiii.global',
      sourceType: 'web',
      authorityScore: 0.5,
    }],
  };
  store.record(input);
  store.record(input);
  const candidate = store.record(input);
  assert.equal(candidate.candidateType, 'PUBLIC_TIME_BOUND');
  assert.match(candidate.suggestedQuestion, /2026年/);
  assert.deepEqual(candidate.targetYears, [2026]);
  assert.equal(candidate.sources.length, 1);
  assert.equal(candidate.sourceCount, 1);
  assert.equal(candidate.reliableSourceCount, 0);
  assert.equal(candidate.eligibilityPolicy.knowledgeRequired, false);
  assert.equal(candidate.eligibleForPromotion, false);
  assert.match(candidate.unmetReasons.join(';'), /尚未达到可靠来源要求/);

  const verified = store.setSourceVerified(candidate.id, candidate.sources[0].url, true);
  assert.equal(verified.manuallyVerifiedSourceCount, 1);
  assert.equal(verified.reliableSourceCount, 1);
  assert.equal(verified.sourceRequirementSatisfied, true);
  assert.equal(verified.eligibleForPromotion, true);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('manual source verification enables immediate promotion and third-use auto promotion', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'web-manual-promotion-'));
  const store = new WebQaCandidateStore({ filePath: path.join(dir, 'candidates.json') });
  const input = {
    question: '海岳项目什么时候正式启用？',
    answer: '海岳项目于2026年8月1日正式启用。',
    sources: [{
      title: '项目公告',
      url: 'https://example.com/notice',
      domain: 'example.com',
      sourceType: 'web',
    }],
  };
  const first = store.record(input);
  assert.equal(first.useCount, 1);
  assert.equal(first.eligibleForPromotion, false);

  const verified = store.setSourceVerified(first.id, first.sources[0].url, true);
  assert.equal(verified.manualPromotionReady, true);
  assert.equal(verified.eligibleForPromotion, true);
  assert.equal(verified.autoPromotionReady, false);

  const second = store.record(input);
  assert.equal(second.useCount, 2);
  assert.equal(second.manualPromotionReady, true);
  assert.equal(second.autoPromotionReady, false);

  const third = store.record(input);
  assert.equal(third.useCount, 3);
  assert.equal(third.manualPromotionReady, true);
  assert.equal(third.autoPromotionReady, true);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('promotion writes one QA item and records manual or automatic promotion mode', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'web-promotion-service-'));
  const candidateStore = new WebQaCandidateStore({ filePath: path.join(dir, 'candidates.json') });
  const qa = new QaStore({ filePath: path.join(dir, 'qa.json') });
  const first = candidateStore.record({
    question: '海岳项目在哪里启用？',
    answer: '海岳项目在南京启用。',
    sources: [{ title: '公告', url: 'https://example.com/notice', sourceType: 'web' }],
  });
  const verified = candidateStore.setSourceVerified(first.id, first.sources[0].url, true);
  const promoted = promoteWebQaCandidate(verified, {
    qaStore: qa,
    candidateStore,
    promotionMode: 'AUTO_AFTER_MANUAL_REVIEW',
  });
  assert.equal(qa.list().length, 1);
  assert.equal(promoted.candidate.status, 'PROMOTED');
  assert.equal(promoted.candidate.promotionMode, 'AUTO_AFTER_MANUAL_REVIEW');
  assert.equal(promoted.candidate.promotedQaId, promoted.qaItem.id);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('approved Web QA exactly matches the original candidate before another web search', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'web-approved-qa-'));
  const candidateStore = new WebQaCandidateStore({ filePath: path.join(dir, 'candidates.json') });
  const qa = new QaStore({ filePath: path.join(dir, 'qa.json') });
  const first = candidateStore.record({
    question: '今年的星海大会什么时候举办？',
    answer: '星海大会于2026年8月1日至3日举办。',
    sources: [{ title: '大会公告', url: 'https://example.com/event', sourceType: 'web' }],
  });
  const verified = candidateStore.setSourceVerified(first.id, first.sources[0].url, true);
  const promoted = promoteWebQaCandidate(verified, {
    qaStore: qa,
    candidateStore,
    question: '2026年星海大会的举办时间是什么？',
    promotionMode: 'MANUAL',
  });

  const match = findApprovedWebQaMatch('今年的星海大会什么时候举办！', {
    qaStore: qa,
    candidateStore,
  });
  assert.equal(match?.item.id, promoted.qaItem.id);
  assert.equal(match?.approvedWebQa, true);
  assert.equal(findApprovedWebQaMatch('明年的星海大会什么时候举办？', {
    qaStore: qa,
    candidateStore,
  }), null);

  const repeated = candidateStore.record({
    question: '今年的星海大会什么时候举办？',
    answer: '星海大会于2026年8月1日至3日举办。',
    sources: [{ title: '大会公告', url: 'https://example.com/event', sourceType: 'web' }],
  });
  assert.equal(repeated.status, 'PROMOTED');
  assert.equal(repeated.promotedQaId, promoted.qaItem.id);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('approved Web QA does not bypass expiry', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'web-approved-qa-expiry-'));
  const candidateStore = new WebQaCandidateStore({ filePath: path.join(dir, 'candidates.json') });
  const qa = new QaStore({ filePath: path.join(dir, 'qa.json') });
  const candidate = candidateStore.record({
    question: '海岳项目什么时候启用？',
    answer: '海岳项目于2026年8月1日启用。',
    sources: [{ title: '项目公告', url: 'https://example.com/project', sourceType: 'web' }],
  });
  const verified = candidateStore.setSourceVerified(candidate.id, candidate.sources[0].url, true);
  promoteWebQaCandidate(verified, { qaStore: qa, candidateStore });

  assert.equal(findApprovedWebQaMatch(candidate.question, {
    qaStore: qa,
    candidateStore,
    now: new Date('2035-01-01T00:00:00.000Z'),
  }), null);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('volatile public facts remain short-lived but can be manually approved', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'web-volatile-candidate-'));
  const store = new WebQaCandidateStore({ filePath: path.join(dir, 'candidates.json') });
  const input = {
    question: '当前美元兑人民币汇率是多少？',
    answer: '当前汇率为测试值。',
    sources: [{
      title: '实时汇率',
      url: 'https://example.gov.cn/rate',
      domain: 'example.gov.cn',
      sourceType: 'official',
    }],
  };
  store.record(input);
  store.record(input);
  const candidate = store.record(input);
  assert.equal(candidate.candidateType, 'PUBLIC_VOLATILE');
  assert.equal(candidate.eligibleForPromotion, false);
  assert.match(candidate.unmetReasons.join(';'), /仅作短期缓存/);
  assert.ok(Date.parse(candidate.expiresAt) - Date.now() <= 24 * 60 * 60 * 1000);
  const verified = store.setSourceVerified(candidate.id, candidate.sources[0].url, true);
  assert.equal(verified.eligibleForPromotion, true);
  assert.equal(verified.manualPromotionReady, true);
  assert.ok(Date.parse(verified.expiresAt) - Date.now() <= 24 * 60 * 60 * 1000);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('current-anchor candidates use the resolved baseline date as a valid time scope', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'web-current-anchor-candidate-'));
  const store = new WebQaCandidateStore({ filePath: path.join(dir, 'candidates.json') });
  const input = {
    question: '南京现在的鼓楼区区长是谁？',
    answer: '测试人物。',
    sources: [{
      title: '政府页面',
      url: 'https://example.gov.cn/leader',
      domain: 'example.gov.cn',
      sourceType: 'official',
    }],
  };
  store.record(input);
  store.record(input);
  const candidate = store.record(input);
  assert.equal(candidate.candidateType, 'PUBLIC_TIME_BOUND');
  assert.match(candidate.suggestedQuestion, /基准日期：\d{4}-\d{2}-\d{2}/);
  assert.equal(candidate.eligibilityPolicy.yearScopeRequired, true);
  assert.doesNotMatch(candidate.unmetReasons.join(';'), /缺少明确年份或时间范围/);
  assert.equal(candidate.eligibleForPromotion, true);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('current-year outcome candidates are time-bound without domain keyword rules', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'web-current-year-candidate-'));
  const store = new WebQaCandidateStore({ filePath: path.join(dir, 'candidates.json') });
  const year = new Date().getFullYear();
  const candidate = store.record({
    question: `${year}年甲项目最终由谁胜出？`,
    answer: '甲方胜出。',
    sources: [{
      title: '结果公告',
      url: 'https://example.gov.cn/result',
      domain: 'example.gov.cn',
      sourceType: 'official',
    }],
  });
  assert.equal(candidate.candidateType, 'PUBLIC_TIME_BOUND');
  assert.deepEqual(candidate.targetYears, [year]);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('expired promoted QA entries do not answer', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'qa-expiry-'));
  const store = new QaStore({ filePath: path.join(dir, 'qa.json') });
  store.save({
    question: '当前测试状态是什么？',
    answer: '旧状态',
    expiresAt: new Date(Date.now() - 1000).toISOString(),
  });
  assert.equal(store.findAnswer('当前测试状态是什么？'), null);
  fs.rmSync(dir, { recursive: true, force: true });
});
