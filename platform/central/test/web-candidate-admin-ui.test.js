import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const appSource = fs.readFileSync(new URL('../admin/src/App.vue', import.meta.url), 'utf8');
const fileServerSource = fs.readFileSync(new URL('../src/files/FileServer.js', import.meta.url), 'utf8');
const routerSource = fs.readFileSync(new URL('../src/router/MessageRouter.js', import.meta.url), 'utf8');

test('admin exposes Web QA candidates inside knowledge between QA and fallback', () => {
  assert.doesNotMatch(appSource, /key: 'webCandidates', label: '联网候选'/);
  assert.match(appSource, /knowledgeMode === 'qa'[\s\S]{0,350}knowledgeMode === 'webCandidates'[\s\S]{0,350}knowledgeMode === 'fallback'/);
  assert.match(appSource, /联网回答候选池/);
  assert.match(appSource, /审核联网候选/);
  assert.match(appSource, /确认提升到 QA/);
  assert.match(appSource, /拒绝候选/);
});

test('promotion supports immediate manual review and third-use auto promotion', () => {
  assert.match(appSource, /webCandidateTypeLabel/);
  assert.match(appSource, /无需知识库支持/);
  assert.match(appSource, /已有来源/);
  assert.match(appSource, /item\.eligibleForPromotion/);
  assert.match(appSource, /isWebCandidateExpired\(item\)/);
  assert.match(appSource, /ElMessageBox\.confirm\('确认将核对后的问题和答案写入正式 QA？'/);
  assert.match(appSource, /第 3 次出现相同答案时将自动进入 QA/);
  assert.match(fileServerSource, /promoteWebQaCandidate\(candidate/);
});

test('candidate review can verify a source only for the selected candidate', () => {
  assert.match(appSource, /标记为已核验可靠/);
  assert.match(appSource, /webCandidateSourceLabel/);
  assert.match(appSource, /\/api\/web-qa-candidates\/verify-source/);
  assert.match(fileServerSource, /webQaCandidateStore\.setSourceVerified/);
});

test('promotion endpoint persists the administrator-reviewed question and answer', () => {
  assert.match(fileServerSource, /const promotedQuestion = String\(payload\.question \|\| candidate\.question/);
  assert.match(fileServerSource, /const promotedAnswer = String\(payload\.answer \|\| candidate\.answer/);
  assert.match(fileServerSource, /question: promotedQuestion/);
  assert.match(fileServerSource, /answer: promotedAnswer/);
});

test('promoted QA becomes visible immediately and exact approved QA precedes MUST_WEB', () => {
  assert.match(appSource, /String\(right\.updatedAt \|\| right\.createdAt \|\| ''\)/);
  assert.match(appSource, /qaKeyword\.value = question[\s\S]{0,120}knowledgeMode\.value = 'qa'/);
  assert.match(routerSource, /_tryHandleApprovedWebQaAnswer\([\s\S]{0,350}if \(webRoute\.mode === 'MUST_WEB'\)/);
  assert.match(routerSource, /reason: 'MANUAL_PROMOTION_EXACT'/);
});

test('web rule cards expose real route outcome statistics', () => {
  assert.match(appSource, /实际命中 \{\{ item\.stats\?\.matched/);
  assert.match(appSource, /成功回答 \{\{ item\.stats\?\.answered/);
  assert.match(appSource, /未回答 \{\{ item\.stats\?\.notAnswered/);
  assert.match(appSource, /技术失败 \{\{ item\.stats\?\.failed/);
  assert.match(appSource, /成功率 \{\{ item\.stats\?\.successRate/);
  assert.match(fileServerSource, /usageStore\.getWebSearchRuleStats/);
});
