import test from 'node:test';
import assert from 'node:assert/strict';
import {
  FAQ_TARGET_COUNT,
  assertFaqTarget,
  isNearDuplicateQuestion,
  mergeFaqItems,
  normalizeFaqItems,
  recoverFaqItemsFromMalformedJson,
} from '../src/analysis/FaqTargetPolicy.js';

test('FAQ policy removes invalid pages and duplicate questions', () => {
  const result = normalizeFaqItems([
    { question: '这是什么？', answer: '答案一', sourcePage: 1 },
    { question: ' 这 是 什么 ', answer: '重复答案', sourcePage: 1 },
    { question: '第二问', answer: '答案二', sourcePage: 2 },
    { question: '越界', answer: '答案', sourcePage: 99 },
  ], [1, 2]);
  assert.equal(result.length, 2);
  assert.deepEqual(result.map(item => item.sourcePage), [1, 2]);
});

test('FAQ policy caps merged output at exactly the configured target', () => {
  const items = Array.from({ length: 120 }, (_, index) => ({
    question: `问题${index + 1}`,
    answer: `答案${index + 1}`,
    sourcePage: 1,
  }));
  const result = mergeFaqItems([], items, [1], FAQ_TARGET_COUNT);
  assert.equal(result.length, 100);
  assert.equal(assertFaqTarget(result), result);
});

test('FAQ hard target rejects incomplete generation', () => {
  assert.throws(() => assertFaqTarget([{ question: '问题', answer: '答案', sourcePage: 1 }]), /需要 100 条/);
});

test('FAQ policy recovers complete objects from a malformed array', () => {
  const malformed = '{"faqs":[{"question":"问题一","answer":"答案一","sourcePage":1}{"question":"问题二","answer":"答案二","sourcePage":2},BROKEN';
  const recovered = recoverFaqItemsFromMalformedJson(malformed);
  assert.equal(recovered.length, 2);
  assert.deepEqual(recovered.map(item => item.question), ['问题一', '问题二']);
});

test('FAQ policy removes semantic near-duplicates and preserves intent metadata', () => {
  assert.equal(isNearDuplicateQuestion('客服如何保证回答的准确性？', '客服回答的准确性如何保证？'), true);
  assert.equal(isNearDuplicateQuestion('客服如何保证回答的准确性？', '客服如何保证回答的安全性？'), false);
  const result = normalizeFaqItems([
    { question: '客服如何保证回答的准确性？', answer: '依据知识库。', sourcePage: 1, intent: 'capability', sourceId: 'demo#page-1' },
    { question: '客服回答的准确性如何保证？', answer: '重复答案。', sourcePage: 1 },
  ], [1]);
  assert.equal(result.length, 1);
  assert.equal(result[0].intent, 'capability');
  assert.equal(result[0].sourceId, 'demo#page-1');
});
