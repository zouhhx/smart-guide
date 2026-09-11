import test from 'node:test';
import assert from 'node:assert/strict';
import { buildQaSystemPrompt } from '../src/api/ModelPrompt.js';

test('Direct LLM prompt requires general numeric and condition consistency without domain rules', () => {
  const prompt = buildQaSystemPrompt();
  assert.match(prompt, /问题条件/);
  assert.match(prompt, /数字/);
  assert.match(prompt, /数量级/);
  assert.match(prompt, /单位换算/);
  assert.match(prompt, /回答可确认的部分/);
  assert.doesNotMatch(prompt, /水的密度|世界杯|奥斯卡|长鑫|中国移动/);
});
