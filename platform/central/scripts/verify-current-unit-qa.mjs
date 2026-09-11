import fs from 'fs';
import path from 'path';
import { knowledgeDb } from '../src/knowledge/KnowledgeDb.js';
import { extractKnowledgeUnitId } from '../src/knowledge/KnowledgeScope.js';

const indexPath = path.resolve(process.env.DATA_DIR || 'data', 'markdown-index.json');
if (!fs.existsSync(indexPath)) {
  throw new Error(`Knowledge index not found: ${indexPath}`);
}

const raw = JSON.parse(fs.readFileSync(indexPath, 'utf8'));
const rows = [...(raw.qaItems || []), ...(raw.obsidianQaItems || [])];
const unitRows = new Map();
for (const row of rows) {
  const unitId = extractKnowledgeUnitId(row);
  if (!unitId) continue;
  if (!unitRows.has(unitId)) unitRows.set(unitId, []);
  unitRows.get(unitId).push(row);
}

const sharedQuestions = ['有没有案例', '需要接哪些系统', '这里有哪些核心能力'];
const failures = [];
let checks = 0;

for (const [unitId, items] of unitRows) {
  const exactQuestion = items[0]?.question;
  const questions = [exactQuestion, ...sharedQuestions].filter(Boolean);
  for (const question of questions) {
    checks += 1;
    const candidates = knowledgeDb.findAnswerCandidates(question, {
      unitId,
      limit: 8,
      minScore: 0,
    });
    if (!candidates.length) {
      failures.push({ unitId, question, reason: 'no scoped candidate' });
      continue;
    }
    const wrong = candidates.find(candidate => extractKnowledgeUnitId(candidate.item) !== unitId);
    if (wrong) {
      failures.push({
        unitId,
        question,
        reason: 'cross-unit candidate',
        candidateId: wrong.item?.id || '',
        candidateUnitId: extractKnowledgeUnitId(wrong.item),
      });
    }
  }
}

for (const question of sharedQuestions) {
  checks += 1;
  if (!knowledgeDb.hasQuestionAcrossKnowledgeUnits(question)) {
    failures.push({ question, reason: 'shared question collision not detected' });
  }
}

const result = {
  ok: failures.length === 0,
  indexPath,
  units: unitRows.size,
  qaWithUnitId: [...unitRows.values()].reduce((sum, items) => sum + items.length, 0),
  checks,
  failures,
};

console.log(JSON.stringify(result, null, 2));
if (!result.ok) process.exitCode = 1;
