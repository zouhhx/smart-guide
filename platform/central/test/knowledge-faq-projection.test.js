import assert from 'node:assert/strict';
import test from 'node:test';
import knowledgeDb from '../src/knowledge/KnowledgeDb.js';

function faqSection(count, page = 1) {
  const rows = Array.from({ length: count }, (_, index) => [
    `${index + 1}. 问：第 ${index + 1} 个问题是什么？`,
    `   答：这是第 ${index + 1} 个问题的答案。`,
    `   来源页：${page}`,
  ].join('\n'));
  return ['正文', '', '## 可转FAQ', '', ...rows].join('\n\n');
}

test('projects all 100 generated FAQs from one published document', () => {
  const items = knowledgeDb._extractConvertibleFaqs(faqSection(100));
  assert.equal(items.length, 100);
  assert.equal(items[0].question, '第 1 个问题是什么？');
  assert.equal(items[99].question, '第 100 个问题是什么？');
});

test('keeps more than one page of generated FAQs and preserves page ownership', () => {
  const content = `${faqSection(100, 1)}\n\n${faqSection(100, 2).replace(/^.*?## 可转FAQ\s*/s, '')}`;
  const items = knowledgeDb._extractConvertibleFaqs(content);
  assert.equal(items.length, 200);
  assert.equal(items.filter(item => item.sourcePage === 1).length, 100);
  assert.equal(items.filter(item => item.sourcePage === 2).length, 100);
});
