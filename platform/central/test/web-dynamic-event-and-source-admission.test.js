import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { detectDynamicEventIntent } from '../src/websearch/DynamicEventIntentDetector.js';
import { WebSearchIntent } from '../src/websearch/WebSearchIntent.js';
import { shouldRetryWebAfterLlm } from '../src/websearch/LlmFreshnessFallback.js';
import { admitVisibleSources, extractExplicitWebEntity } from '../src/websearch/VisibleSourceAdmission.js';
import { webSearchService } from '../src/websearch/WebSearchService.js';
import { tavilyMcpClient } from '../src/websearch/TavilyMcpClient.js';

test('dynamic event state questions route to web without entity-specific keywords', () => {
  const positives = [
    '合肥长鑫存储上市了吗？什么时候？',
    '星河公司的新产品发布了吗',
    '滨江地铁什么时候开通？',
    '新的管理办法生效了吗？',
    '海岳项目获批了吗',
  ];
  for (const question of positives) {
    assert.equal(detectDynamicEventIntent(question)?.reason, 'DYNAMIC_EVENT_STATE', question);
  }

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dynamic-event-intent-'));
  const intent = new WebSearchIntent({ filePath: path.join(dir, 'rules.json') });
  const route = intent.classifyRoute(positives[0]);
  assert.equal(route.mode, 'MUST_WEB');
  assert.equal(route.reason, 'DYNAMIC_EVENT_STATE');
  assert.equal(route.intent.item.name, '动态事件状态');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('definitions, procedures, writing and controls are not treated as dynamic event facts', () => {
  const negatives = [
    '上市是什么意思？',
    '企业上市需要什么流程？',
    '帮我发布一段欢迎词',
    '怎么上线一个网站？',
    '打开上市公司的宣传片',
  ];
  for (const question of negatives) {
    assert.equal(detectDynamicEventIntent(question), null, question);
  }
});

test('recent completed events asking for an attribute route to web without domain keywords', () => {
  const positives = [
    '2026年7月，中国首次成功实施重复使用运载火箭回收任务使用了哪型火箭？',
    '2026年星河项目完成首次试运行采用了什么型号的设备？',
    '今年滨江工程正式启用时使用了哪款控制系统？',
    '2026年新管理办法试点由谁负责实施？',
  ];
  for (const question of positives) {
    const result = detectDynamicEventIntent(question);
    assert.equal(result?.reason, 'TIME_BOUND_EVENT_FACT', question);
    assert.ok(result?.requestedAttribute, question);
  }

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'time-bound-event-fact-'));
  const intent = new WebSearchIntent({ filePath: path.join(dir, 'rules.json') });
  const route = intent.classifyRoute(positives[0]);
  assert.equal(route.mode, 'MUST_WEB');
  assert.equal(route.reason, 'TIME_BOUND_EVENT_FACT');
  assert.equal(route.intent.item.name, '近期事件属性');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('event attribute routing keeps historical, explanatory, writing and control questions out', () => {
  const negatives = [
    '2020年重复使用运载火箭是什么？',
    '2026年项目应该如何实施？',
    '帮我写一份2026年项目实施方案',
    '播放2026年火箭回收任务的视频',
    '介绍一下重复使用运载火箭的工作原理',
  ];
  for (const question of negatives) {
    assert.equal(detectDynamicEventIntent(question), null, question);
  }
});

test('dynamic-event uncertainty from direct LLM triggers one web fallback', () => {
  assert.equal(shouldRetryWebAfterLlm(
    '合肥长鑫存储上市了吗？什么时候？',
    '截至目前，没有公开信息表明公司已成功上市，需要关注官方公告或权威财经新闻源获取最新进展。',
  ), true);
});

test('visible source admission removes unrelated same-year pages for explicit entities', () => {
  const question = '六合博物馆2026年上线了哪些AI智慧观展服务？';
  assert.equal(extractExplicitWebEntity(question), '六合博物馆');
  const relevant = {
    title: '六合博物馆上线AI智慧导览服务',
    content: '六合区博物馆于2026年上线AI讲解、智慧导览和互动问答服务。',
    url: 'https://example.gov.cn/liuhe-museum-ai',
    temporalStatus: 'MATCH',
  };
  const unrelated = {
    title: '中研院推出AI互动展',
    content: '中央研究院在2026年国际博物馆日推出万象召唤AI互动展。',
    url: 'https://sinica.edu.tw/ai-show',
    temporalStatus: 'MATCH',
  };
  const result = admitVisibleSources(question, [unrelated, relevant]);
  assert.deepEqual(result.admitted.map(item => item.url), [relevant.url]);
  assert.equal(result.rejected[0].reason, 'ENTITY_MISMATCH');
});

test('explicit entity source with wrong year or unrelated relation is not user-visible', () => {
  const question = '六合博物馆2026年上线了哪些AI智慧观展服务？';
  const result = admitVisibleSources(question, [{
    title: '六合博物馆2025年度工作总结',
    content: '六合博物馆2025年完成藏品整理。',
    temporalStatus: 'OTHER_YEAR',
  }, {
    title: '六合博物馆2026年度藏品征集',
    content: '六合博物馆发布藏品征集公告。',
    temporalStatus: 'MATCH',
  }]);
  assert.equal(result.admitted.length, 0);
  assert.deepEqual(result.rejected.map(item => item.reason), ['TIME_SCOPE_MISMATCH', 'RELATION_MISMATCH']);
});

test('questions without a strong explicit entity preserve existing source behavior', () => {
  const sources = [{ title: '行业报告', content: '人工智能行业发展概况。' }];
  const result = admitVisibleSources('人工智能行业有哪些趋势？', sources);
  assert.deepEqual(result.admitted, sources);
  assert.equal(result.rejected.length, 0);

  const eventSources = [{ title: 'WAIC 2026', content: '世界人工智能大会举办日期为2026年7月17日至20日。' }];
  const eventResult = admitVisibleSources('今年的上海世界人工智能大会是什么时候举办的？', eventSources);
  assert.equal(eventResult.entity, '');
  assert.deepEqual(eventResult.admitted, eventSources);

  const euSources = [{ title: 'European Commission AI calls', content: 'The European Commission announced two calls.' }];
  const euResult = admitVisibleSources('欧盟委员会2026年为AI推出的两项征集总额是多少？', euSources);
  assert.equal(euResult.entity, '');
  assert.deepEqual(euResult.admitted, euSources);
});

test('WebSearchService does not expose an irrelevant source after abstaining', async () => {
  const originalSearch = tavilyMcpClient.search;
  const progress = [];
  tavilyMcpClient.search = async () => ({
    results: [{
      title: '中央研究院推出AI互动展',
      url: 'https://www.sinica.edu.tw/news_content/55/3790',
      content: '中央研究院在2026年国际博物馆日推出万象召唤AI互动及科普数字展。',
      score: 0.95,
    }],
  });
  try {
    const result = await webSearchService.answer({
      text: '六合博物馆2026年上线了哪些AI智慧观展服务？',
      force: true,
      context: { now: '2026-07-30T10:00:00+08:00' },
      onProgress: event => progress.push(event),
    });
    assert.equal(result.context.webSearchStatus, 'INSUFFICIENT_EVIDENCE');
    assert.equal(result.context.sources.length, 0);
    assert.equal(result.context.webSearchSourceAdmission.admittedCount, 0);
    assert.ok(result.context.webSearchSourceAdmission.rejectedCount >= 1);
    assert.equal(progress[0].stage, 'SEARCHING');
    assert.ok(progress.some(event => event.stage === 'SOURCES_FOUND'));
    assert.equal(progress.at(-1).stage, 'SYNTHESIZING');
  } finally {
    tavilyMcpClient.search = originalSearch;
  }
});
