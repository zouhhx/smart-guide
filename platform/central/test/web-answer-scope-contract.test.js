import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildWebAnswerScope,
  constrainUncertainties,
  WEB_ANSWER_SCOPE_VERSION,
} from '../src/websearch/WebAnswerScope.js';
import { webSearchService } from '../src/websearch/WebSearchService.js';

test('result question closes unrelated uncertainties once the winner slot is complete', () => {
  const scope = buildWebAnswerScope(
    '2026年，美加墨世界杯的冠军是谁？',
    { type: 'RESULT_OUTCOME' },
    {
      requiredAspects: [{ id: 'result_winner', name: '已确认的获胜方或冠军' }],
      missingAspects: [],
    },
  );
  assert.equal(scope.version, WEB_ANSWER_SCOPE_VERSION);
  assert.equal(scope.answerType, 'RESULT_ENTITY');
  assert.equal(scope.complete, true);
  assert.deepEqual(constrainUncertainties([
    '未提及主办国在夺冠过程中的具体角色或影响。',
  ], scope), []);
});

test('incomplete core fields can still retain a useful uncertainty', () => {
  const scope = buildWebAnswerScope(
    '这家公司上市了吗？',
    { type: 'GENERAL' },
    {
      requiredAspects: [{ id: 'status', name: '上市状态' }],
      missingAspects: [{ id: 'status', name: '上市状态' }],
    },
  );
  assert.equal(scope.answerType, 'BOOLEAN_STATUS');
  assert.equal(scope.complete, false);
  assert.deepEqual(
    constrainUncertainties(['尚未找到能够确认上市状态的公告。'], scope),
    ['尚未找到能够确认上市状态的公告。'],
  );
});

test('list questions do not discard completeness caveats mechanically', () => {
  const scope = buildWebAnswerScope(
    '博物馆上线了哪些智慧服务？',
    { type: 'GENERAL' },
    {
      requiredAspects: [{ id: 'services', name: '已上线服务' }],
      missingAspects: [],
    },
  );
  assert.equal(scope.answerType, 'LIST');
  assert.equal(scope.closeUncertaintiesWhenComplete, false);
  assert.deepEqual(
    constrainUncertainties(['公开清单可能未覆盖全部试运行服务。'], scope),
    ['公开清单可能未覆盖全部试运行服务。'],
  );
});

test('result answer is focused on the requested winner instead of host roles or venue', () => {
  const focused = webSearchService._focusSimpleAnswer({
    status: 'ANSWERED',
    answer: '西班牙在2026年世界杯获得冠军，并在纽约新泽西的场馆击败阿根廷。',
    claims: [{
      claim_id: 'C1',
      claim: '西班牙赢得2026年世界杯冠军。',
      importance: 'core',
      evidence_ids: ['E1'],
    }, {
      claim_id: 'C2',
      claim: '决赛举办地为纽约新泽西。',
      importance: 'supporting',
      evidence_ids: ['E1'],
    }],
    uncertainties: [],
  }, '2026年，美加墨世界杯的冠军是谁？', { type: 'RESULT_OUTCOME' });

  assert.equal(focused.answer, '2026年美加墨世界杯冠军是西班牙。');
  assert.equal(focused.claims.length, 1);
  assert.doesNotMatch(focused.answer, /纽约|主办国|影响/);
});

test('answer-type inference works across entities, dates, locations and numbers', () => {
  const cases = [
    ['该公司负责人是谁？', 'ENTITY'],
    ['大会什么时候举办？', 'DATETIME'],
    ['活动在哪里举行？', 'LOCATION'],
    ['两项征集总额是多少？', 'NUMBER'],
  ];
  for (const [question, answerType] of cases) {
    assert.equal(buildWebAnswerScope(question, {}, {}).answerType, answerType);
  }
});

test('scoped winner extraction rejects venue and trailing predicate text', () => {
  assert.deepEqual(
    webSearchService._extractScopedOutcomeWinners(
      '西班牙在2026年世界杯获得冠军，并在纽约新泽西的场馆击败阿根廷。西班牙赢得2026年世界杯冠军。',
    ),
    ['西班牙'],
  );
});

test('result answer is focused when the model uses an entity-first champion sentence', () => {
  const focused = webSearchService._focusSimpleAnswer({
    status: 'ANSWERED',
    answer: '西班牙是2026年世界杯的冠军，他们以1-0战胜了阿根廷。',
    claims: [{
      claim_id: 'C1',
      claim: '西班牙是2026年世界杯的冠军，他们以1-0战胜了阿根廷。',
      importance: 'core',
      evidence_ids: ['E1'],
    }],
    uncertainties: [],
  }, '2026年，美加墨世界杯的冠军是谁？', { type: 'RESULT_OUTCOME' });

  assert.equal(focused.answer, '2026年美加墨世界杯冠军是西班牙。');
  assert.doesNotMatch(focused.answer, /1-0|阿根廷/);
});
