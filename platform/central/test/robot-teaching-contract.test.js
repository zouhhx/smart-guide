import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { QaDraftStore } from '../src/teaching/QaDraftStore.js';
import { TeachingAnalysisService } from '../src/teaching/TeachingAnalysisService.js';
import fileServer from '../src/files/FileServer.js';
import messageRouter from '../src/router/MessageRouter.js';

test('robot answer contract separates answer, knowledge miss, staff-only and service failure', () => {
  const answered = fileServer._robotAnswerEnvelope({
    text: '介绍产品',
    result: { seq: 1, reply: '产品答案' },
    outcome: { reply: '产品答案', answerSource: 'RAG', context: {} },
  });
  assert.equal(answered.answerStatus, 'ANSWERED');
  assert.equal(answered.teachable, false);

  const missed = fileServer._robotAnswerEnvelope({
    text: '未知问题',
    result: { seq: 2 },
    outcome: { context: { refusalFallback: true }, answerSource: 'RAG' },
  });
  assert.equal(missed.reason, 'NO_KNOWLEDGE');
  assert.equal(missed.teachable, true);

  const sensitive = fileServer._robotAnswerEnvelope({
    text: '这套系统多少钱',
    result: { seq: 3 },
    outcome: { answerSource: '兜底库', context: {} },
  });
  assert.equal(sensitive.reason, 'STAFF_ONLY');
  assert.equal(sensitive.teachable, false);

  const failed = fileServer._robotAnswerEnvelope({
    text: '问题',
    result: { seq: 4 },
    outcome: { context: { technicalFallback: true } },
  });
  assert.equal(failed.answerStatus, 'FAILED');
  assert.equal(failed.teachable, false);

  const webFailure = fileServer._robotAnswerEnvelope({
    text: '最新结果',
    result: { seq: 5 },
    outcome: { context: { webSearchStatus: 'UNAVAILABLE' }, answerSource: '联网搜索异常' },
  });
  assert.equal(webFailure.answerStatus, 'FAILED');
  assert.equal(webFailure.teachable, false);

  const explicitMiss = fileServer._robotAnswerEnvelope({
    text: '企业内部项目的验收日期是什么？',
    result: { seq: 6 },
    outcome: { context: { robotAnswerStatus: 'NO_KNOWLEDGE' }, answerSource: 'LLM' },
  });
  assert.equal(explicitMiss.answerStatus, 'ABSTAINED');
  assert.equal(explicitMiss.reason, 'NO_KNOWLEDGE');
  assert.equal(explicitMiss.teachable, true);
});

test('robot direct LLM status is parsed from a strict structured contract', () => {
  const answered = messageRouter._normalizeRobotDirectLlmResult({
    reply: JSON.stringify({ answerStatus: 'ANSWERED', reply: '上海有外滩、豫园等景点。' }),
    context: { modelProvider: 'ollama' },
  });
  assert.equal(answered.context.robotAnswerStatus, 'ANSWERED');
  assert.equal(answered.reply, '上海有外滩、豫园等景点。');

  const missed = messageRouter._normalizeRobotDirectLlmResult({
    reply: JSON.stringify({ answerStatus: 'NO_KNOWLEDGE', reply: '' }),
  });
  assert.equal(missed.context.robotAnswerStatus, 'NO_KNOWLEDGE');
  assert.equal(missed.context.refusalFallback, true);
  assert.equal(missed.reply, '');

  const contradictoryMiss = messageRouter._normalizeRobotDirectLlmResult({
    reply: JSON.stringify({ answerStatus: 'NO_KNOWLEDGE', reply: '我猜是明天。' }),
  });
  assert.equal(contradictoryMiss.error, undefined);
  assert.equal(contradictoryMiss.context.robotAnswerStatus, 'NO_KNOWLEDGE');
  assert.equal(contradictoryMiss.reply, '');

  const aliasedMiss = messageRouter._normalizeRobotDirectLlmResult({
    reply: JSON.stringify({ answerStatus: 'ABSTAINED', reason: 'NO_KNOWLEDGE', reply: '' }),
  });
  assert.equal(aliasedMiss.context.robotAnswerStatus, 'NO_KNOWLEDGE');

  const plainAnswer = messageRouter._normalizeRobotDirectLlmResult({
    reply: '智慧家庭覆盖智能照明、安防、家电联动和环境控制。',
  });
  assert.equal(plainAnswer.context.robotAnswerStatus, 'ANSWERED');
  assert.match(plainAnswer.reply, /智能照明/);

  const truncatedStructuredAnswer = messageRouter._normalizeRobotDirectLlmResult({
    reply: '{"answerStatus":"ANSWERED","reply":"智慧家庭覆盖智能照明、安防和家电联动。',
  });
  assert.equal(truncatedStructuredAnswer.context.robotAnswerStatus, 'ANSWERED');
  assert.equal(truncatedStructuredAnswer.reply, '智慧家庭覆盖智能照明、安防和家电联动。');
  assert.doesNotMatch(truncatedStructuredAnswer.reply, /answerStatus/);

  const invalid = messageRouter._normalizeRobotDirectLlmResult({ reply: '{}' });
  assert.match(invalid.error, /invalid robot direct-answer contract/);
  assert.equal(invalid.context.robotAnswerStatus, 'FAILED');
  assert.equal(invalid.context.failureCode, 'ROBOT_DIRECT_ANSWER_CONTRACT_INVALID');
});

test('teaching analysis echoes correlation fields and never marks unsupported speech as verified', async () => {
  const model = {
    async chat() {
      return { reply: JSON.stringify({
        relevance: 'RELATED',
        completeness: 'SUFFICIENT',
        canReplay: true,
        replayAnswer: '该系统支持智能导览。',
        warnings: [],
      }) };
    },
  };
  const service = new TeachingAnalysisService({
    model,
    knowledge: { findRagSnippets: () => [] },
  });
  const result = await service.analyze({
    analysisId: 'analysis-1',
    teachingTurnId: 'turn-1',
    revision: 3,
    robotId: 'robot-1',
    sessionId: 'session-1',
    question: '系统有什么功能？',
    accumulatedTranscript: '该系统支持智能导览。',
    fragmentCount: 2,
    isFinal: true,
    completionReason: 'button',
  });
  assert.equal(result.analysisId, 'analysis-1');
  assert.equal(result.teachingTurnId, 'turn-1');
  assert.equal(result.revision, 3);
  assert.equal(result.canReplay, true);
  assert.equal(result.factuality, 'UNVERIFIED');
});

test('teaching drafts are pending-only and persistently idempotent', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'qa-drafts-'));
  const store = new QaDraftStore({ filePath: path.join(dir, 'qa-drafts.json') });
  const payload = {
    submissionId: 'submission-1',
    robotId: 'robot-1',
    sessionId: 'session-1',
    teachingTurnId: 'turn-1',
    question: '问题',
    rawAnswer: '工作人员原始回答',
    suggestedAnswer: '整理后的回答',
    source: 'EXHIBITION_ROBOT',
  };
  const first = store.save(payload);
  const replay = store.save(payload);
  assert.equal(first.item.status, 'PENDING_REVIEW');
  assert.equal(replay.item.draftId, first.item.draftId);
  assert.equal(replay.replayed, true);
  assert.throws(() => store.save({ ...payload, rawAnswer: '被更改的原始回答' }), /different content/);

  const reviewed = store.updateReview(first.item.draftId, {
    question: '审核后的问题',
    answer: '审核后的正式答案',
  }, { id: 'reviewer-1', displayName: '审核员' });
  assert.equal(reviewed.reviewQuestion, '审核后的问题');
  assert.equal(reviewed.reviewAnswer, '审核后的正式答案');
  assert.equal(reviewed.reviewedBy, 'reviewer-1');

  const published = store.setStatus(first.item.draftId, 'PUBLISHED', { qaId: 'qa-1' });
  assert.equal(published.status, 'PUBLISHED');
  assert.equal(store.get(first.item.draftId).qaId, 'qa-1');
  assert.throws(() => store.updateReview(first.item.draftId, { answer: '再次修改' }), /can no longer be edited/);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('knowledge provenance keeps PPT file and page separate from spoken answer', () => {
  const sources = messageRouter._knowledgeSourcesFromItems([
    { sourceFile: '移动云大会.pptx', sourcePage: 4 },
    { file: '移动云大会.pptx', pageNumber: 4 },
  ]);
  assert.deepEqual(sources, [{ fileName: '移动云大会.pptx', page: 4, type: 'PPT' }]);
  const adminSource = fs.readFileSync(new URL('../admin/src/App.vue', import.meta.url), 'utf8');
  assert.match(adminSource, /知识来源/);
  assert.match(adminSource, /knowledgeSources/);
  const viteSource = fs.readFileSync(new URL('../admin/vite.config.js', import.meta.url), 'utf8');
  assert.match(viteSource, /base:\s*['"]\/developer\/['"]/);
});

test('robot suppressTts is request-scoped and does not introduce a global mute switch', () => {
  const source = fs.readFileSync(new URL('../src/router/MessageRouter.js', import.meta.url), 'utf8');
  assert.match(source, /this\._requestPolicies\.get\(seq\)\?\.suppressActions/);
  assert.doesNotMatch(source, /globalSuppressTts|globalMute/);
});

test('robot requests always suppress host speech and screen actions', () => {
  const source = fs.readFileSync(new URL('../src/files/FileServer.js', import.meta.url), 'utf8');
  assert.match(source, /suppressActions:\s*robotMode\s*\|\|\s*payload\.suppressTts === true/);
  assert.match(source, /consumeCompletedOutcome\(result\.seq\)/);
});

test('robot draft review is admin-only and publishes into the formal FAQ', () => {
  const source = fs.readFileSync(new URL('../src/files/FileServer.js', import.meta.url), 'utf8');
  const bundle = fs.readFileSync(new URL('../admin-ui/assets/index-real-analysis-v22.js', import.meta.url), 'utf8');

  assert.match(source, /adminAccountStore\.require\(this\._getBearerToken\(req\), 'knowledge\.view'\)/);
  assert.match(source, /adminAccountStore\.require\(this\._getBearerToken\(req\), 'publish\.manage'\)/);
  assert.match(source, /vectorKnowledgeService\.upsertFaq\(qaItem\)/);
  assert.match(source, /qaStore\.save\(qaItem\)/);
  assert.match(bundle, /机器人现场录入/);
  assert.match(bundle, /审核并发布/);
  assert.match(bundle, /\/api\/knowledge\/qa-drafts/);
  assert.match(bundle, /reviewQuestion\|\|selectedRobot\.question/);
  assert.match(bundle, /reviewAnswer\|\|selectedRobot\.suggestedAnswer\|\|selectedRobot\.rawAnswer/);
  assert.doesNotMatch(bundle, /robot-drafts\.html/);
  assert.doesNotMatch(bundle, /knowledge\/teaching\/analyze/);
});
