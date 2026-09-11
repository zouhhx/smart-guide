import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { ConversationSessionStore } from '../src/context/ConversationSessionStore.js';
import {
  ContextResolver,
  buildContextualRetrievalText,
  isContextDependentFollowup,
} from '../src/context/ContextResolver.js';
import { UsageStore } from '../src/usage/UsageStore.js';
import { ConversationMetadataStore } from '../src/context/ConversationMetadataStore.js';
import { buildRagAnswerUserPrompt } from '../src/knowledge/RagAnswerPrompt.js';
import { ollamaBridge } from '../src/api/OllamaBridge.js';
import { openClawBridge } from '../src/api/OpenClawBridge.js';
import messageRouter from '../src/router/MessageRouter.js';
import { mediaLibrary } from '../src/media/MediaLibrary.js';
import { deviceManager } from '../src/devices/DeviceManager.js';

test('one participant keeps one session until the idle boundary and then starts a new session', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'conversation-session-'));
  const archivePath = path.join(dir, 'sessions.jsonl');
  const store = new ConversationSessionStore({ idleMs: 180000, archivePath });
  const first = store.beginTurn({ participantKey: 'admin-1', question: '介绍一下长鑫存储', now: 1000 });
  store.completeTurn(first.conversationId, {
    question: '介绍一下长鑫存储',
    answer: '长鑫存储是一家存储芯片企业。',
  }, 2000);
  const second = store.beginTurn({ participantKey: 'admin-1', question: '再展开说说', now: 120000 });
  assert.equal(second.conversationId, first.conversationId);
  assert.match(second.contextText, /介绍一下长鑫存储/);
  assert.match(second.contextText, /存储芯片企业/);

  const next = store.beginTurn({ participantKey: 'admin-1', question: '换一个问题', now: 400001 });
  assert.notEqual(next.conversationId, first.conversationId);
  assert.equal(store.snapshot(first.conversationId).archiveReason, 'IDLE_TIMEOUT');
  assert.equal(fs.readFileSync(archivePath, 'utf8').trim().split(/\r?\n/).length, 1);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('context resolver sends completed session turns as context without treating them as evidence', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'context-resolver-'));
  const store = new ConversationSessionStore({ archivePath: path.join(dir, 'sessions.jsonl') });
  const resolver = new ContextResolver({ store });
  const first = resolver.begin({
    text: '介绍一下这个项目',
    clientInfo: { deviceId: 'device-1' },
    source: 'chat',
  });
  resolver.complete(first.conversationId, { question: '介绍一下这个项目', answer: '这是智慧展厅项目。' });
  const followup = resolver.begin({
    text: '再展开说说',
    clientInfo: { deviceId: 'device-1' },
    source: 'chat',
  });
  const prompt = resolver.buildModelContext(followup);
  assert.match(prompt, /CONVERSATION_HISTORY/);
  assert.match(prompt, /这是智慧展厅项目/);
  assert.match(prompt, /不等于可靠事实证据/);
  assert.equal(followup.contextualFollowup, true);
  assert.match(followup.retrievalText, /介绍一下这个项目/);
  assert.equal(isContextDependentFollowup('中国移动有哪些产品？', followup.history), false);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('context-dependent followups bypass standalone QA and fallback matching', () => {
  assert.equal(isContextDependentFollowup('再展开具体说一说', [{ question: '上一问' }]), true);
  assert.equal(isContextDependentFollowup('好吃的呢？', [{ question: '上海有哪些好玩的？' }]), true);
  assert.equal(isContextDependentFollowup('住宿呢', [{ question: '上海有哪些好玩的？' }]), true);
  assert.equal(isContextDependentFollowup('好吃的呢？', []), false);
  assert.match(
    buildContextualRetrievalText('好吃的呢？', '上海有哪些好玩的？'),
    /沿用上一轮问题中的主体、地点、对象/,
  );
  const source = fs.readFileSync(new URL('../src/router/MessageRouter.js', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /!contextualFollowup && await this\._tryHandleQaAnswer/);
  assert.match(source, /const qaResult = await this\._tryHandleQaAnswer/);
  assert.match(source, /!contextualFollowup && await this\._tryHandleConfiguredFallback/);
  assert.match(source, /retrievalText: this\._retrievalTextFor/);
  assert.match(source, /当前问题承接上一轮对话/);
  assert.match(source, /this\._retrievalTextFor\(seq, text\)/);
});

test('an explicitly selected archived conversation resumes with its previous context', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'conversation-resume-'));
  const store = new ConversationSessionStore({ idleMs: 1000, archivePath: path.join(dir, 'sessions.jsonl') });
  const first = store.beginTurn({ participantKey: 'admin', conversationId: 'resume-1', question: '第一问', now: 1000 });
  store.completeTurn(first.conversationId, { question: '第一问', answer: '第一答' }, 1100);
  store.archive(first.conversationId, 'IDLE_TIMEOUT', 2200);

  const resumed = store.beginTurn({
    participantKey: 'admin',
    conversationId: first.conversationId,
    question: '继续展开',
    now: 5000,
  });
  assert.equal(resumed.conversationId, first.conversationId);
  assert.equal(resumed.turnCount, 1);
  assert.match(resumed.contextText, /第一问/);
  assert.equal(store.snapshot(first.conversationId).archivedAt, '');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('historical context can be restored after a process restart', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'conversation-restore-'));
  const store = new ConversationSessionStore({
    archivePath: path.join(dir, 'sessions.jsonl'),
    historyLoader: id => id === 'persisted-1'
      ? [{ question: '历史问题', answer: '历史答案', time: '2026-08-04T07:00:00.000Z' }]
      : [],
  });
  const restored = store.beginTurn({
    participantKey: 'admin',
    conversationId: 'persisted-1',
    question: '基于历史继续说明',
  });
  assert.equal(restored.conversationId, 'persisted-1');
  assert.equal(restored.turnCount, 1);
  assert.match(restored.contextText, /历史答案/);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('conversation deletion removes active context and persisted usage records', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'conversation-delete-'));
  const sessionStore = new ConversationSessionStore({ archivePath: path.join(dir, 'sessions.jsonl') });
  const turn = sessionStore.beginTurn({ participantKey: 'admin', conversationId: 'session-1', question: '问题' });
  sessionStore.completeTurn(turn.conversationId, { question: '问题', answer: '答案' });

  const usage = new UsageStore({ dataDir: dir });
  usage.recordInteraction({ seq: 1, conversationId: 'session-1', question: '问题', answer: '答案' });
  usage.recordInteraction({ seq: 2, conversationId: 'session-2', question: '保留', answer: '保留' });
  assert.equal(usage.getConversationTurns('session-1').at(0).answer, '答案');
  assert.equal(usage.deleteConversation('session-1').removed, 1);
  assert.deepEqual(usage.getRecentEvents(10).map(item => item.conversationId), ['session-2']);
  assert.equal(sessionStore.delete('session-1'), true);
  assert.equal(sessionStore.snapshot('session-1'), null);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('conversation titles are normalized and persist across store instances', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'conversation-title-'));
  const filePath = path.join(dir, 'metadata.json');
  const firstStore = new ConversationMetadataStore({ filePath });
  const renamed = firstStore.rename('session-1', '  建筑保险\n产品讨论  ');
  assert.equal(renamed.title, '建筑保险 产品讨论');
  const nextStore = new ConversationMetadataStore({ filePath });
  assert.equal(nextStore.getTitle('session-1'), '建筑保险 产品讨论');
  assert.equal(nextStore.remove('session-1'), true);
  assert.equal(nextStore.getTitle('session-1'), '');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('direct LLM and RAG prompts carry conversation context', () => {
  const context = '<CONVERSATION_HISTORY>用户：上一问</CONVERSATION_HISTORY>';
  assert.match(ollamaBridge._buildUserPrompt('继续', context), /上一问/);
  assert.match(openClawBridge._buildUserPrompt('继续', context), /上一问/);
  assert.match(buildRagAnswerUserPrompt('继续', [], { conversationContext: context }), /上一问/);
});

test('explicit file actions are recognized before knowledge or web answers', () => {
  const positives = [
    '打开中国移动相关PPT',
    '请播放展厅宣传视频',
    '把案例图片投屏到2号屏',
    '查看这个文件',
  ];
  for (const text of positives) assert.equal(messageRouter._isExplicitMediaActionText(text), true, text);
  const negatives = ['视频编码是什么？', '介绍一下PPT的工作原理', '有哪些相关文件？'];
  for (const text of negatives) assert.equal(messageRouter._isExplicitMediaActionText(text), false, text);

  const source = fs.readFileSync(new URL('../src/router/MessageRouter.js', import.meta.url), 'utf8');
  const externalBlock = source.slice(source.indexOf('async handleExternalText'), source.indexOf('_normalizeTopicAliases'));
  const chatBlock = source.slice(source.indexOf('async _handleChat'), source.indexOf('async _handleVoice'));
  const voiceBlock = source.slice(source.indexOf('async _handleVoice'), source.indexOf('async _handleCommand'));
  assert.ok(externalBlock.indexOf('_isExplicitMediaActionText') < externalBlock.indexOf("webRoute.mode === 'MUST_WEB'"));
  assert.ok(chatBlock.indexOf('_isExplicitMediaActionText') < chatBlock.indexOf("webRoute.mode === 'MUST_WEB'"));
  assert.ok(voiceBlock.indexOf('_isExplicitMediaActionText') < voiceBlock.indexOf("webRoute.mode === 'MUST_WEB'"));
});

test('current PPT introduction commands select the requested stored commentary tier', () => {
  const cases = [
    ['\u4ecb\u7ecd\u4e00\u4e0b\u5f53\u524dPPT', 'standard'],
    ['\u7cbe\u70bc\u7684\u4ecb\u7ecd\u4e00\u4e0b', 'concise'],
    ['\u8be6\u7ec6\u4ecb\u7ecd\u4e00\u4e0b', 'detailed'],
  ];

  for (const [text, expected] of cases) {
    assert.equal(messageRouter._parseCurrentPptCommentaryLevel(text), expected, text);
    assert.equal(messageRouter._parseRealtimeCurrentContentIntent(text), null, text);
  }

  const personalized = messageRouter._parseRealtimeCurrentContentIntent(
    '\u7ed3\u5408\u5ba2\u6237\u884c\u4e1a\u4ecb\u7ecd\u4e00\u4e0b\u5f53\u524dPPT',
  );
  assert.ok(personalized);
});

test('stored current PPT commentary is emitted as a TTS speak action without changing the view', async () => {
  const originalState = messageRouter._currentPptStateForTarget;
  const originalFindMediaItem = messageRouter._findMediaItem;
  const originalProcessModelResult = messageRouter._processModelResult;
  const commentary = [
    '\u3010\u7cbe\u70bc\u7248\u3011\u7cbe\u70bc\u64ad\u62a5\u6587\u672c',
    '',
    '\u3010\u6807\u51c6\u7248\u3011\u6807\u51c6\u64ad\u62a5\u6587\u672c',
    '',
    '\u3010\u8be6\u7ec6\u7248\u3011\u8be6\u7ec6\u64ad\u62a5\u6587\u672c',
  ].join('\n');
  messageRouter._currentPptStateForTarget = () => ({ fileName: 'demo.pptx', slideNumber: 2 });
  messageRouter._findMediaItem = () => ({ commentaryPages: { 2: commentary } });
  messageRouter._processModelResult = async () => {};

  try {
    const cases = [
      ['\u4ecb\u7ecd\u4e00\u4e0b\u5f53\u524dPPT', 'standard', '\u6807\u51c6\u64ad\u62a5\u6587\u672c'],
      ['\u7cbe\u70bc\u7684\u4ecb\u7ecd\u4e00\u4e0b', 'concise', '\u7cbe\u70bc\u64ad\u62a5\u6587\u672c'],
      ['\u8be6\u7ec6\u4ecb\u7ecd\u4e00\u4e0b', 'detailed', '\u8be6\u7ec6\u64ad\u62a5\u6587\u672c'],
    ];
    for (const [text, expectedLevel, expectedSpeech] of cases) {
      const result = await messageRouter._tryHandleCurrentPptCommentary({
        text,
        clientInfo: {},
        seq: 1,
        target: 2,
        source: 'chat',
        startedAt: Date.now(),
      });
      const action = result.actions[0];
      assert.equal(action.type, 'speak');
      assert.equal(action.target, 2);
      assert.equal(action.params.text, expectedSpeech);
      assert.equal(action.params.commentaryLevel, expectedLevel);
      assert.equal(action.params.commentaryMode, 'stored');
      assert.equal(action.params.preserveView, 'true');
      assert.equal(action.params.currentPptFileName, 'demo.pptx');
      assert.equal(action.params.currentPptSlideNumber, '2');
    }
  } finally {
    messageRouter._currentPptStateForTarget = originalState;
    messageRouter._findMediaItem = originalFindMediaItem;
    messageRouter._processModelResult = originalProcessModelResult;
  }
});

test('a recent page-turn command wins over a lagging client page report', () => {
  const screenNumber = 992;
  const originalGetByScreenNumber = deviceManager.getByScreenNumber;
  const originalState = messageRouter._currentPptStates.get(screenNumber);
  const now = Date.now();
  deviceManager.getByScreenNumber = () => ({
    status: {
      currentPptFileName: 'demo.pptx',
      currentPptSlideNumber: 1,
      updatedAt: now + 1,
    },
  });
  messageRouter._currentPptStates.set(screenNumber, {
    fileName: 'demo.pptx',
    slideNumber: 2,
    updatedAt: now,
  });

  try {
    assert.equal(messageRouter._currentPptStateForTarget(screenNumber).slideNumber, 2);
    messageRouter._currentPptStates.set(screenNumber, {
      fileName: 'demo.pptx',
      slideNumber: 2,
      updatedAt: now - 5000,
    });
    assert.equal(messageRouter._currentPptStateForTarget(screenNumber).slideNumber, 1);
  } finally {
    deviceManager.getByScreenNumber = originalGetByScreenNumber;
    if (originalState) messageRouter._currentPptStates.set(screenNumber, originalState);
    else messageRouter._currentPptStates.delete(screenNumber);
  }
});

test('page commentary never falls back to a different page', () => {
  const firstPage = '【标准版】第一页讲解词';
  assert.equal(messageRouter._commentaryTextForLevel({ commentaryPages: { 1: firstPage } }, 'standard', 2), '');
  assert.equal(messageRouter._commentaryTextForLevel({ commentaryPages: { 1: firstPage } }, 'standard', 1), '第一页讲解词');
});

test('an unambiguous named file is opened directly instead of becoming a QA answer', () => {
  const originalGetAll = mediaLibrary.getAll;
  const originalSearch = mediaLibrary.search;
  const item = {
    id: 'ppt-1',
    title: '中国移动案例',
    name: '中国移动案例.pptx',
    originalName: '中国移动案例.pptx',
    path: '中国移动案例.pptx',
    type: 'ppt',
    localPath: 'D:\\media\\中国移动案例.pptx',
  };
  mediaLibrary.getAll = () => [item];
  mediaLibrary.search = () => [item];
  try {
    assert.equal(messageRouter._findDirectMediaItem('请打开中国移动案例PPT')?.id, 'ppt-1');
    assert.equal(messageRouter._mediaActionForItem(item, 2).type, 'play_ppt');
  } finally {
    mediaLibrary.getAll = originalGetAll;
    mediaLibrary.search = originalSearch;
  }
});

test('admin exposes chat sessions, new conversation and route diagnostics while preserving the old log', () => {
  const source = fs.readFileSync(new URL('../admin/src/App.vue', import.meta.url), 'utf8');
  assert.match(source, /key: 'chat'/);
  assert.match(source, /conversationSessions/);
  assert.match(source, /startNewSimulatorConversation/);
  assert.match(source, /conversationId: simulatorConversationId\.value/);
  assert.match(source, /本轮诊断/);
  assert.match(source, /最近对话/);
  assert.match(source, /deleteConversationSession/);
  assert.match(source, /renameConversationSession/);
  assert.match(source, /重命名会话/);
  assert.match(source, /conversationTitle/);
  assert.match(source, /删除会话/);
  assert.match(source, /selectConversationSession/);
  assert.match(source, /提问时间：/);
  assert.match(source, /回答时间：/);
  assert.doesNotMatch(source, /<small>用户<\/small>/);
});
