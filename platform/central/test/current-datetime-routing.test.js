import test from 'node:test';
import assert from 'node:assert/strict';
import { classifyCurrentDateTimeQuery } from '../src/router/CurrentDateTimeIntent.js';
import { detectFreshnessIntent } from '../src/websearch/FreshnessIntentDetector.js';

test('pure current clock questions are handled deterministically and stay out of WebSearch', () => {
  const questions = [
    '现在北京时间是多少',
    '北京时间现在是多少？',
    '当前北京时间是什么时间',
    '中国时间现在几点',
    '现在的时间是多少',
    '此刻几点了',
  ];

  for (const question of questions) {
    const classification = classifyCurrentDateTimeQuery(question);
    assert.equal(classification.matched, true, question);
    assert.equal(classification.asksTime, true, question);
    assert.equal(detectFreshnessIntent(question), null, question);
  }
});

test('event times remain outside the deterministic clock handler', () => {
  const questions = [
    '现在世界杯决赛北京时间是多少',
    '世界杯决赛什么时候开球',
    '当前航班几点起飞',
  ];

  for (const question of questions) {
    const classification = classifyCurrentDateTimeQuery(question);
    assert.equal(classification.matched, false, question);
    assert.equal(classification.reason, 'EVENT_TIME_QUERY', question);
  }
});
