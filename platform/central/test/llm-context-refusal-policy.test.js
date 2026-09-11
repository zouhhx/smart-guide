import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildModelConversationMessages,
  classifyAssistantReply,
} from '../src/context/ConversationContextPolicy.js';
import { ConversationSessionStore } from '../src/context/ConversationSessionStore.js';
import { ContextResolver } from '../src/context/ContextResolver.js';
import { modelProvider } from '../src/api/ModelProvider.js';
import messageRouter from '../src/router/MessageRouter.js';

test('model context keeps useful QA turns while excluding control, refusals and technical fallbacks', () => {
  const history = [
    { question: '介绍南京', answer: '南京是江苏省省会，拥有丰富的历史文化。', answerSource: 'LLM' },
    { question: '下一页', answer: '已切换到下一页。', answerSource: '控制' },
    { question: '技术问题', answer: '这个问题我还不会呢，请问一下旁边的专家吧', answerSource: '规则兜底' },
    { question: '未知问题', answer: '抱歉，资料里没有相关信息。', answerSource: 'LLM' },
    { question: '产品进度', answer: '资料没有明确说明发布日期，但已经确认产品进入测试阶段。', answerSource: 'LLM' },
  ];
  const messages = buildModelConversationMessages(history);
  assert.deepEqual(messages.map(item => item.content), [
    '介绍南京',
    '南京是江苏省省会，拥有丰富的历史文化。',
    '产品进度',
    '资料没有明确说明发布日期，但已经确认产品进入测试阶段。',
  ]);
  assert.equal(classifyAssistantReply(history[4].answer).kind, 'PARTIAL_ANSWER');
  assert.equal(classifyAssistantReply(history[3].answer).kind, 'PURE_REFUSAL');
});

test('full session history remains stored while model context is filtered', () => {
  const store = new ConversationSessionStore({ archivePath: '/tmp/unused-conversation-archive.jsonl' });
  const resolver = new ContextResolver({ store });
  const first = resolver.begin({ text: '介绍项目', clientInfo: { deviceId: 'filter-1' } });
  resolver.complete(first.conversationId, {
    question: '介绍项目',
    answer: '这是智慧展厅项目。',
    answerSource: 'RAG',
  });
  resolver.complete(first.conversationId, {
    question: '下一页',
    answer: '已切换到下一页。',
    answerSource: '控制',
  });
  const next = resolver.begin({ text: '继续介绍', clientInfo: { deviceId: 'filter-1' } });
  assert.equal(next.history.length, 2);
  assert.match(next.contextText, /已切换到下一页/);
  assert.doesNotMatch(resolver.buildModelContext(next), /已切换到下一页/);
  assert.deepEqual(resolver.buildModelMessages(next).map(item => item.role), ['user', 'assistant']);
});

test('a followup after only a control result does not inherit the control command as its topic', () => {
  const store = new ConversationSessionStore({ archivePath: '/tmp/unused-control-history.jsonl' });
  const resolver = new ContextResolver({ store });
  const first = resolver.begin({ text: '下一页', clientInfo: { deviceId: 'control-only' } });
  resolver.complete(first.conversationId, {
    question: '下一页',
    answer: '已切换到下一页。',
    answerSource: '控制',
  });
  const followup = resolver.begin({ text: '再展开说说', clientInfo: { deviceId: 'control-only' } });
  assert.equal(followup.contextualFollowup, false);
  assert.equal(followup.retrievalText, '再展开说说');
  assert.deepEqual(resolver.buildModelMessages(followup), []);
});

test('partial answers are preserved and pure refusals become non-context technical fallbacks', () => {
  const partial = messageRouter._ensureHelpfulModelResult({
    reply: '目前没有明确发布日期，但已经确认该产品进入公开测试阶段。',
    actions: [],
  }, '产品什么时候发布？');
  assert.match(partial.reply, /公开测试阶段/);
  assert.equal(partial.context.contextEligible, true);

  const refusal = messageRouter._ensureHelpfulModelResult({
    reply: '抱歉，我无法回答这个问题。',
    actions: [],
  }, '普通问题');
  assert.equal(refusal.reply, '这个问题我还不会呢，请问一下旁边的专家吧');
  assert.equal(refusal.context.technicalFallback, false);
  assert.equal(refusal.context.refusalFallback, true);
  assert.equal(refusal.context.contextEligible, false);
});

test('knowledge-base absence wording is a refusal rather than a successful answer', () => {
  const classified = classifyAssistantReply('知识库中没有关于上海美食的信息。');
  assert.equal(classified.kind, 'PURE_REFUSAL');
  const unmentioned = classifyAssistantReply('知识库中未明确提及智慧家庭的应用场景。');
  assert.equal(unmentioned.kind, 'PURE_REFUSAL');
  assert.equal(classified.useful, false);
});

test('ordinary direct LLM pure refusal gets at most one clean-context retry', async () => {
  const originalChat = modelProvider.chat;
  const seq = 987654;
  const calls = [];
  modelProvider.chat = async args => {
    calls.push(args);
    return calls.length === 1
      ? { reply: '抱歉，我无法回答这个问题。', actions: [], context: {} }
      : { reply: '水在标准大气压下的沸点是100摄氏度。', actions: [], context: {} };
  };
  messageRouter._conversationTurns.set(seq, {
    contextualFollowup: false,
    history: [{ question: '旧问题', answer: '旧答案', answerSource: 'LLM' }],
  });
  try {
    const result = await messageRouter._chatDirectLlm({ text: '水的沸点是多少？', seq });
    assert.equal(calls.length, 2);
    assert.equal(calls[0].temperature, 0.1);
    assert.equal(calls[0].conversationMessages.length, 2);
    assert.equal(calls[1].conversationMessages.length, 0);
    assert.equal(result.context.directLlmCleanContextRetry, true);
    assert.match(result.reply, /100/);
  } finally {
    modelProvider.chat = originalChat;
    messageRouter._conversationTurns.delete(seq);
  }
});
