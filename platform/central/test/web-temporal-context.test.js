import test from 'node:test';
import assert from 'node:assert/strict';
import { classifyTemporalEvidence, resolveTemporalContext } from '../src/websearch/TemporalContextResolver.js';
import { webSearchService } from '../src/websearch/WebSearchService.js';

const NOW = new Date('2026-07-30T03:00:00.000Z');

test('relative calendar terms resolve deterministically in Asia/Shanghai', () => {
  const current = resolveTemporalContext('今年的上海世界人工智能大会是什么时候举办的', { now: NOW });
  assert.equal(current.currentDate, '2026-07-30');
  assert.equal(current.resolvedQuestion, '2026年的上海世界人工智能大会是什么时候举办的');
  assert.deepEqual(current.targetYears, [2026]);
  assert.equal(current.relativeTerms['今年'], '2026年');

  assert.match(resolveTemporalContext('明年的大会什么时候举办', { now: NOW }).resolvedQuestion, /2027年/);
  assert.match(resolveTemporalContext('去年的大会在哪里举办', { now: NOW }).resolvedQuestion, /2025年/);
  assert.match(resolveTemporalContext('今天有什么新闻', { now: NOW }).resolvedQuestion, /2026年7月30日/);
  assert.match(resolveTemporalContext('本月有哪些新政策', { now: NOW }).resolvedQuestion, /2026年7月/);
});

test('cross-year boundary uses the configured request timezone', () => {
  const beforeShanghaiNewYear = resolveTemporalContext('今年的大会', {
    now: new Date('2026-12-31T15:59:59.000Z'),
  });
  const afterShanghaiNewYear = resolveTemporalContext('今年的大会', {
    now: new Date('2026-12-31T16:00:01.000Z'),
  });
  assert.equal(beforeShanghaiNewYear.currentYear, 2026);
  assert.equal(afterShanghaiNewYear.currentYear, 2027);
});

test('anchored questions reject evidence that only mentions another year', () => {
  const temporal = resolveTemporalContext('今年的大会什么时候举办', { now: NOW });
  assert.equal(classifyTemporalEvidence('WAIC 2026 will be held from July 17 to 20', temporal), 'MATCH');
  assert.equal(classifyTemporalEvidence('WAIC 2025 was held from July 26 to 28', temporal), 'OTHER_YEAR');
  assert.equal(classifyTemporalEvidence('World Artificial Intelligence Conference official schedule', temporal), 'UNKNOWN');
});

test('date-scoped retrieval drops other-year event pages but keeps target and yearless pages', () => {
  const temporal = resolveTemporalContext('今年的上海世界人工智能大会是什么时候举办的', { now: NOW });
  const profile = webSearchService._classifyQuestion(temporal.originalQuestion, {
    options: { recencyDays: 0, maxResults: 5 },
  });
  const rows = [
    {
      title: 'WAIC 2025',
      url: 'https://example.com/waic-2025',
      content: 'The 2025 conference was held from July 26 to 28.',
      score: 0.9,
    },
    {
      title: 'WAIC 2026',
      url: 'https://example.com/waic-2026',
      content: 'The 2026 conference is scheduled from July 17 to 20.',
      score: 0.9,
    },
    {
      title: 'WAIC official schedule',
      url: 'https://example.com/waic-schedule',
      content: 'The conference schedule is available on the official page.',
      score: 0.9,
    },
  ];
  const normalized = webSearchService._normalizeSources(rows, temporal.resolvedQuestion, {
    query: temporal.resolvedQuestion,
    pass: 'primary',
    profile,
    temporalContext: temporal,
  });
  assert.deepEqual(normalized.map(item => item.temporalStatus), ['MATCH', 'UNKNOWN']);
  assert.equal(normalized.some(item => item.url.includes('2025')), false);
});

test('generation prompt receives authoritative relative-time mapping', () => {
  const temporal = resolveTemporalContext('今年的上海世界人工智能大会是什么时候举办的', { now: NOW });
  const prompt = webSearchService._groundingUserPrompt(
    temporal.originalQuestion,
    [],
    { requiredAspects: [{ name: '活动举办日期' }] },
    temporal,
  );
  assert.match(prompt, /<RESOLVED_QUESTION>2026年的上海世界人工智能大会是什么时候举办的<\/RESOLVED_QUESTION>/);
  assert.match(prompt, /"current_date":"2026-07-30"/);
  assert.match(prompt, /"今年":"2026年"/);
});

test('false year ambiguity is removed without changing supported facts', () => {
  const temporal = resolveTemporalContext('今年的上海世界人工智能大会是什么时候举办的', { now: NOW });
  const normalized = webSearchService._normalizeTemporalGeneration({
    status: 'ANSWERED',
    answer: '资料没有明确指出“今年”是哪一年。2026年大会举办时间为7月17日至20日。',
    claims: [
      { claim_id: 'C1', claim: '今年的年份不明确。', evidence_ids: [] },
      { claim_id: 'C2', claim: '2026年大会举办时间为7月17日至20日。', evidence_ids: ['E1'] },
    ],
    uncertainties: ['用户没有指明具体年份。'],
  }, temporal);
  assert.equal(normalized.answer, '2026年大会举办时间为7月17日至20日。');
  assert.deepEqual(normalized.claims.map(item => item.claim_id), ['C2']);
  assert.deepEqual(normalized.uncertainties, []);
});

test('past event dates are not rendered with future tense', () => {
  const temporal = resolveTemporalContext('今年的上海世界人工智能大会是什么时候举办的', { now: NOW });
  const normalized = webSearchService._normalizeTemporalGeneration({
    status: 'ANSWERED',
    answer: '大会将于2026年7月17日至20日在上海举行。',
    claims: [{
      claim_id: 'C1',
      claim: '大会将于2026年7月17日至20日在上海举行。',
      evidence_ids: ['E1'],
    }],
    uncertainties: [],
  }, temporal);
  assert.equal(normalized.answer, '大会举办日期为2026年7月17日至20日，举办地为上海。');
  assert.equal(normalized.claims[0].claim, '大会举办日期为2026年7月17日至20日，举办地为上海。');
  assert.doesNotMatch(normalized.answer, /将于/);

  const future = webSearchService._normalizePastDatedFutureTense(
    '大会将于2027年7月17日至20日在上海举行。',
    temporal.currentDate,
  );
  assert.match(future, /将于2027年/);
});

test('model conflict label does not override a non-conflicting cited answer', () => {
  const assessment = { metrics: { conflict: 0 } };
  const result = {
    status: 'CONFLICTING_EVIDENCE',
    answer: '大会举办日期为2026年7月17日至20日。',
    claims: [{
      claim_id: 'C1',
      claim: '大会举办日期为2026年7月17日至20日。',
      evidence_ids: ['E1', 'E2'],
    }],
  };
  assert.equal(
    webSearchService._normalizeSimpleGenerationStatus(result.status, assessment, result),
    'ANSWERED',
  );
  assert.equal(
    webSearchService._normalizeSimpleGenerationStatus(
      result.status,
      { metrics: { conflict: 1 } },
      result,
    ),
    'CONFLICTING_EVIDENCE',
  );
});

test('date-only questions keep the cited date and remove unrelated expansion', () => {
  const focused = webSearchService._focusSimpleAnswer({
    status: 'CONFLICTING_EVIDENCE',
    answer: '上海世界人工智能大会举办日期为2026年7月17日至20日。地点在上海，主题为智能伙伴。',
    claims: [{
      claim_id: 'C1',
      claim: '大会举办日期为2026年7月17日至20日。',
      evidence_ids: ['E1', 'E2'],
    }, {
      claim_id: 'C2',
      claim: '大会地点在上海。',
      evidence_ids: ['E1'],
    }],
    uncertainties: ['“今年”已根据时间上下文解析为2026年。'],
  }, '今年的上海世界人工智能大会是什么时候举办的');
  assert.equal(focused.answer, '举办日期为2026年7月17日至20日。');
  assert.deepEqual(focused.claims.map(item => item.claim_id), ['C1']);
  assert.deepEqual(focused.uncertainties, []);

  const notDateOnly = webSearchService._focusSimpleAnswer({
    status: 'ANSWERED',
    answer: '举办日期为2026年7月17日至20日，地点在上海。',
    claims: [],
    uncertainties: [],
  }, '大会什么时候、在哪里举办？');
  assert.match(notDateOnly.answer, /地点在上海/);
});
