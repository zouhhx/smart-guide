import test from 'node:test';
import assert from 'node:assert/strict';
import { AnswerProgressTracker } from '../src/streaming/AnswerProgressTracker.js';

test('answer progress follows the lightweight presentation state machine', () => {
  const tracker = new AnswerProgressTracker();
  assert.equal(tracker.start({
    seq: 11,
    requestId: 'request-11',
    question: '今年大会什么时候举办？',
    source: 'admin-simulator',
  }).stage, 'RECEIVED');

  assert.equal(tracker.advance(11, 'ROUTING', { message: '正在判断回答方式…' }).stage, 'ROUTING');
  assert.equal(tracker.advance(11, 'SEARCHING', { query: '2026 大会 日期' }).stage, 'SEARCHING');
  assert.equal(tracker.advance(11, 'SOURCES_FOUND', { sourceCount: 3 }).sourceCount, 3);
  assert.equal(tracker.advance(11, 'SEARCHING', { message: '正在补充搜索…' }).stage, 'SEARCHING');
  assert.equal(tracker.advance(11, 'SYNTHESIZING').stage, 'SYNTHESIZING');
  assert.equal(tracker.advance(11, 'STREAMING', { answer: '7月17日至20日。' }).stage, 'STREAMING');
  assert.equal(tracker.advance(11, 'COMPLETED').terminal, true);
  assert.equal(tracker.advance(11, 'SEARCHING'), null);
});

test('answer progress rejects invalid regressions without affecting the request', () => {
  const tracker = new AnswerProgressTracker();
  tracker.start({ seq: 12 });
  tracker.advance(12, 'SYNTHESIZING');
  assert.equal(tracker.advance(12, 'ROUTING'), null);
  assert.equal(tracker.get(12).stage, 'SYNTHESIZING');
});
