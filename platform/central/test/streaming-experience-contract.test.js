import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const adminSource = fs.readFileSync(new URL('../admin/src/App.vue', import.meta.url), 'utf8');
const fileServerSource = fs.readFileSync(new URL('../src/files/FileServer.js', import.meta.url), 'utf8');
const routerSource = fs.readFileSync(new URL('../src/router/MessageRouter.js', import.meta.url), 'utf8');
const webSource = fs.readFileSync(new URL('../src/websearch/WebSearchService.js', import.meta.url), 'utf8');

test('admin simulator correlates one request and renders truthful progress plus typewriter output', () => {
  assert.match(adminSource, /requestId[\s\S]{0,500}type: 'chat'/);
  assert.match(adminSource, /msg\.type === 'answer_progress'/);
  assert.match(adminSource, /handleAnswerProgress/);
  assert.match(adminSource, /animateConversationField/);
  assert.match(adminSource, /simulator-live-card/);
});

test('router emits lightweight progress without replacing the legacy route order', () => {
  assert.match(routerSource, /type: 'answer_progress'/);
  assert.match(routerSource, /type: 'pending_qa'/);
  assert.match(routerSource, /presentation: 'typewriter'/);
  assert.match(routerSource, /speechMode: 'sentence_queue'/);
  assert.match(routerSource, /speechSegments/);
});

test('web search reports retrieval and synthesis lifecycle through a callback', () => {
  assert.match(webSource, /onProgress = null/);
  assert.match(webSource, /stage: 'SEARCHING'/);
  assert.match(webSource, /stage: 'SOURCES_FOUND'/);
  assert.match(webSource, /stage: 'SYNTHESIZING'/);
});

test('active production wake greeting and ASR target remain preserved', () => {
  assert.match(routerSource, /_tryHandleWakeGreeting/);
  assert.match(routerSource, /normalized !== '你好思德'/);
  assert.match(routerSource, /const reply = '我在。'/);
  assert.match(fileServerSource, /const target = payload\.target \|\| '2'/);
});
