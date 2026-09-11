import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { QaStore } from '../src/qa/QaStore.js';

test('manual and robot FAQs receive stable association labels', t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'faq-association-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const store = new QaStore({ filePath: path.join(dir, 'qa.json') });

  const manual = store.save({ question: '手动问题', answer: '手动答案' });
  const robot = store.save({
    question: '机器人问题',
    answer: '机器人答案',
    source: 'robot-teaching',
  });
  const uploaded = store.save({
    question: '上传资料问题',
    answer: '上传资料答案',
    source: 'generated',
    sourceFile: '智慧课桌.pptx',
  });

  assert.equal(manual.source, 'manual');
  assert.equal(manual.sourceFile, '手动上传');
  assert.equal(robot.sourceFile, '机器人现场补充');
  assert.equal(uploaded.sourceFile, '智慧课桌.pptx');
});
