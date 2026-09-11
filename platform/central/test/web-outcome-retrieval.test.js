import test from 'node:test';
import assert from 'node:assert/strict';
import { webSearchService } from '../src/websearch/WebSearchService.js';

const intent = {
  query: '',
  options: { maxResults: 5, recencyDays: 0 },
};

function source(content, overrides = {}) {
  return {
    title: 'Result page',
    url: 'https://example.com/result',
    domain: 'example.com',
    content,
    snippet: content,
    score: 0.9,
    relevanceScore: 0.9,
    authorityScore: 0.96,
    sourceType: 'official',
    canonicalOfficial: true,
    ...overrides,
  };
}

test('result relation recognizes varied outcome questions without entity-specific rules', () => {
  const questions = [
    '2026年世界杯是哪个国家胜出了',
    '这届比赛哪支球队夺冠了？',
    '最终是谁赢了这场赛事？',
    '冠军是哪个国家？',
    'Which country won the tournament?',
  ];
  for (const question of questions) {
    assert.equal(
      webSearchService._classifyQuestion(question, intent).type,
      'RESULT_OUTCOME',
      question,
    );
  }
});

test('prediction questions are not treated as confirmed outcomes', () => {
  for (const question of ['谁会赢得2028年比赛？', '预测哪支球队将夺冠', '哪队夺冠概率最高？']) {
    assert.notEqual(
      webSearchService._classifyQuestion(question, intent).type,
      'RESULT_OUTCOME',
      question,
    );
  }
});

test('World Cup outcome search uses canonical result terms and avoids schedule URL pinning', () => {
  const question = '2026年世界杯是哪个国家胜出了';
  const profile = webSearchService._classifyQuestion(question, intent);
  const primary = webSearchService._buildPrimarySearch(question, { ...intent, query: question }, profile);
  assert.match(primary.query, /FIFA World Cup 2026/);
  assert.match(primary.query, /champion/);
  assert.match(primary.query, /winner/);
  assert.deepEqual(primary.options.preferredUrls, []);
  assert.equal(primary.options.maxResults, 6);
});

test('schedule-only evidence leaves winner missing and triggers one supplemental result search', () => {
  const question = '2026年世界杯是哪个国家胜出了';
  const profile = webSearchService._classifyQuestion(question, intent);
  const assessment = webSearchService._evaluateEvidence(question, [
    source('The FIFA World Cup 2026 final will kick off on 19 July at 15:00 EDT. Image: Argentina winner. Image: Spain winner.'),
  ], intent, { profile, supplemented: false });
  assert.equal(assessment.nextAction, 'SUPPLEMENT_SEARCH');
  assert.deepEqual(assessment.missingAspects.map(item => item.id), ['result_winner']);
  const supplement = webSearchService._buildSupplementalSearch(question, profile, assessment);
  assert.match(supplement.query, /champion/);
  assert.match(supplement.query, /winner/);
  assert.deepEqual(supplement.options.includeDomains, []);
});

test('explicit winner statement completes the outcome relation', () => {
  const question = '2026年世界杯是哪个国家胜出了';
  const profile = webSearchService._classifyQuestion(question, intent);
  const assessment = webSearchService._evaluateEvidence(question, [
    source('Spain have been crowned FIFA World Cup 2026 winners following a 1-0 victory over Argentina after extra-time.'),
  ], intent, { profile, supplemented: false });
  assert.equal(assessment.metrics.coverage, 1);
  assert.deepEqual(assessment.missingAspects, []);
  assert.equal(assessment.nextAction, '');
});

test('conflict status is only preserved when evidence contains explicit opposing winners', () => {
  assert.equal(webSearchService._normalizeSimpleGenerationStatus(
    'CONFLICTING_EVIDENCE',
    { metrics: { conflict: 0 } },
  ), 'INSUFFICIENT_EVIDENCE');

  const question = '2026年世界杯是哪个国家胜出了';
  const profile = webSearchService._classifyQuestion(question, intent);
  const assessment = webSearchService._evaluateEvidence(question, [
    source('Spain have been crowned FIFA World Cup 2026 winners.', { url: 'https://one.example/result', domain: 'one.example' }),
    source('Argentina have been crowned FIFA World Cup 2026 winners.', { url: 'https://two.example/result', domain: 'two.example' }),
  ], intent, { profile, supplemented: true });
  assert.equal(assessment.metrics.conflict, 1);
  assert.equal(webSearchService._normalizeSimpleGenerationStatus(
    'CONFLICTING_EVIDENCE',
    assessment,
  ), 'CONFLICTING_EVIDENCE');
});

test('winner aliases extracted from one page do not create a false conflict', () => {
  const question = '2026年世界杯是哪个国家胜出了';
  const profile = webSearchService._classifyQuestion(question, intent);
  const assessment = webSearchService._evaluateEvidence(question, [
    source('FIFA Organisation Spain crowned FIFA World Cup 2026 Champions. Spain have been crowned FIFA World Cup 2026 winners.'),
  ], intent, { profile, supplemented: true });
  assert.equal(assessment.metrics.conflict, 0);
  assert.equal(assessment.status, 'ANSWERED');
});
