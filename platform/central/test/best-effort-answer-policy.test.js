import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const routerSource = fs.readFileSync(new URL('../src/router/MessageRouter.js', import.meta.url), 'utf8');
const promptSource = fs.readFileSync(new URL('../src/api/ModelPrompt.js', import.meta.url), 'utf8');
const ragPromptSource = fs.readFileSync(new URL('../src/knowledge/RagAnswerPrompt.js', import.meta.url), 'utf8');
const webSearchSource = fs.readFileSync(new URL('../src/websearch/WebSearchService.js', import.meta.url), 'utf8');
const answerMessagesSource = fs.readFileSync(new URL('../src/constants/AnswerMessages.js', import.meta.url), 'utf8');

test('web evidence abstention falls back to a non-citing best-effort model answer', () => {
  assert.match(routerSource, /webStatus === 'ANSWERED'/);
  assert.match(routerSource, /_buildWebBestEffortResult/);
  assert.match(routerSource, /webSearchStatus: 'BEST_EFFORT_ANSWERED'/);
  assert.match(routerSource, /finalRoute: 'WEB_BEST_EFFORT_ANSWERED'/);
  assert.match(routerSource, /禁止编造具体人名、时间、金额、比分、型号或结论/);
});

test('direct QA prompt requires partial useful output instead of pure refusal', () => {
  assert.match(promptSource, /必须尽力给出有帮助的回答/);
  assert.match(promptSource, /不要只回复“无法回答”/);
  assert.match(routerSource, /这是一次拒答后的尽力回答重试/);
  assert.match(routerSource, /RAG_REFUSAL_CONTINUE/);
  assert.match(routerSource, /知识库模型未形成有效回答，继续普通模型回答/);
});

test('RAG keeps grounded constraints while returning supported partial information', () => {
  assert.match(ragPromptSource, /rag-answer-v2-best-effort/);
  assert.match(ragPromptSource, /先回答能够确认的部分/);
  assert.doesNotMatch(ragPromptSource, /reply 必须是“资料里没有明确说明”/);
  assert.match(ragPromptSource, /不得把猜测、推断或其他实体信息包装成确定答案/);
});

test('all terminal unanswered paths share one user-facing reply', () => {
  assert.match(answerMessagesSource, /这个问题我还不会呢，请问一下旁边的专家吧/);
  assert.match(routerSource, /UNANSWERABLE_REPLY/);
  assert.match(webSearchSource, /UNANSWERABLE_REPLY/);
});
