import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { UsageStore } from '../src/usage/UsageStore.js';

test('hot questions contain only the five most frequent FAQ questions', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'usage-faq-ranking-'));
  const store = new UsageStore({ dataDir });

  try {
    for (let index = 1; index <= 6; index += 1) {
      for (let count = 0; count < 8 - index; count += 1) {
        store.recordInteraction({
          question: `FAQ问题${index}`,
          answer: `FAQ答案${index}`,
          answerSource: 'published_faq',
          source: 'robot',
          target: 2,
        });
      }
    }
    store.recordInteraction({
      question: '打开智慧课桌PPT',
      answer: '已下发',
      answerSource: 'command',
      actions: [{ type: 'play_ppt' }],
      source: 'robot',
      target: 2,
    });
    store.recordInteraction({
      question: '关闭。',
      answer: '用于关闭服务器主机。',
      answerSource: 'QA',
      actions: [{ type: 'show_qa' }],
      source: 'robot',
      target: 2,
    });
    store.recordInteraction({
      question: '介绍一下当前PPT',
      answer: '正在讲解。',
      answerSource: 'QA',
      actions: [{ type: 'show_qa' }],
      source: 'robot',
      target: 2,
    });
    store.recordInteraction({
      question: '关闭命令的作用是什么？',
      answer: '用于关闭服务器主机。',
      answerSource: 'QA',
      actions: [{ type: 'show_qa' }],
      source: 'robot',
      target: 2,
    });

    const analytics = store.getAnalytics();
    assert.equal(analytics.topQuestions.length, 5);
    assert.deepEqual(analytics.topQuestions.map(item => item.question), [
      'FAQ问题1', 'FAQ问题2', 'FAQ问题3', 'FAQ问题4', 'FAQ问题5',
    ]);
    assert.equal(analytics.topQuestions.some(item => item.question.includes('打开')), false);
    assert.equal(analytics.topQuestions.some(item => item.question === '关闭。'), false);
    assert.equal(analytics.topQuestions.some(item => item.question.includes('当前PPT')), false);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test('an explanatory FAQ about a command remains eligible for ranking', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'usage-command-faq-'));
  const store = new UsageStore({ dataDir });
  try {
    store.recordInteraction({
      question: '关闭命令的作用是什么？',
      answer: '用于关闭服务器主机。',
      answerSource: 'QA',
      actions: [{ type: 'show_qa' }],
    });
    assert.deepEqual(store.getAnalytics().topQuestions.map(item => item.question), ['关闭命令的作用是什么？']);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
