import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../admin/src/App.vue', import.meta.url), 'utf8');
const start = source.indexOf('const answerSourceTags = [');
const end = source.indexOf('\\n]\\n\\nconst config', start);
const tags = source.slice(start, end);

test('source help includes deterministic datetime and every current answer family', () => {
  const names = [
    '唤醒应答', '控制', '控制忽略', 'QA', '联网搜索', '联网搜索异常', '日期时间',
    '天气缓存', '天气服务', '天气兜底', '讲解词规则', '实时生成讲解词',
    '详细版讲解词兜底', '专场文件', '文件打开', 'ChromaDB 文件', '文件检索',
    'ChromaDB QA', 'ChromaDB QA+AI', 'QA+AI', '兜底库', '本地知识库',
    'ChromaDB RAG', 'RAG', 'LLM', '规则兜底',
  ];
  for (const name of names) {
    assert.ok(tags.includes(`name: '${name}'`), name);
  }
});

test('source help follows the current sequential-router stages', () => {
  const orderedNames = [
    '唤醒应答', '控制', 'QA', '联网搜索', '日期时间', '天气缓存',
    '讲解词规则', '专场文件', '文件打开', 'ChromaDB 文件', '文件检索',
    'ChromaDB QA', 'ChromaDB QA+AI', 'QA+AI', '兜底库', '本地知识库',
    'ChromaDB RAG', 'RAG', 'LLM', '规则兜底',
  ];
  let previous = -1;
  for (const name of orderedNames) {
    const index = tags.indexOf(`name: '${name}'`);
    assert.ok(index > previous, `${name} should appear after the previous routing stage`);
    previous = index;
  }
});

test('source help descriptions reflect the actual project behavior', () => {
  assert.ok(tags.includes('Asia/Shanghai'));
  assert.match(tags, /WebSearch 专用 Prompt/);
  assert.match(tags, /rag-answer-v1 Prompt/);
  assert.match(tags, /返回候选 QA 中保存的原答案/);
  assert.match(tags, /所有前置控制、联网、日期时间、天气、媒体、QA、兜底和 RAG 均未命中/);
});
