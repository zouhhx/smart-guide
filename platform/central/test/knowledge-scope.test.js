import test from 'node:test';
import assert from 'node:assert/strict';
import {
  extractKnowledgeUnitId,
  isCurrentContextQuestion,
  matchesKnowledgeUnit,
} from '../src/knowledge/KnowledgeScope.js';

test('extracts unit id from imported FAQ frontmatter', () => {
  const item = {
    raw_json: {
      frontmatter: { unit_id: 'UNIT-DC-ZJ-002' },
    },
  };
  assert.equal(extractKnowledgeUnitId(item), 'UNIT-DC-ZJ-002');
  assert.equal(matchesKnowledgeUnit(item, 'unit-dc-zj-002'), true);
  assert.equal(matchesKnowledgeUnit(item, 'UNIT-DC-ZJ-003'), false);
});

test('extracts unit id from vector metadata and identifiers', () => {
  assert.equal(
    extractKnowledgeUnitId({ raw_json: { vector: { metadata: { unit_id: 'UNIT-DC-ZJ-010' } } } }),
    'UNIT-DC-ZJ-010',
  );
  assert.equal(extractKnowledgeUnitId({ id: 'DOC-UNIT-DC-ZJ-018-faq_bank:faq:3' }), 'UNIT-DC-ZJ-018');
});

test('recognizes questions that must stay in the current PPT scope', () => {
  const scoped = [
    '这个有什么价值？',
    '当前内容主要解决什么问题',
    '需要接哪些系统',
    '这个方案怎么落地',
    '本页有没有案例',
  ];
  for (const question of scoped) assert.equal(isCurrentContextQuestion(question), true, question);
  assert.equal(isCurrentContextQuestion('山东有哪些好吃的'), false);
  assert.equal(isCurrentContextQuestion('智慧园区主要解决什么问题'), false);
});
