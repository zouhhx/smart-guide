import { config } from '../config/index.js';
import { wsServer } from '../ws/WsServer.js';
import { modelProvider } from '../api/ModelProvider.js';
import { deviceManager } from '../devices/DeviceManager.js';
import { fileServer } from '../files/FileServer.js';
import { usageStore } from '../usage/UsageStore.js';
import { isClientMediaAvailable, mediaLibrary } from '../media/MediaLibrary.js';
import { specialConfigStore } from '../specials/SpecialConfigStore.js';
import { qaStore } from '../qa/QaStore.js';
import { webQaCandidateStore } from '../qa/WebQaCandidateStore.js';
import { findApprovedWebQaMatch, promoteWebQaCandidate } from '../qa/WebQaPromotionService.js';
import { fallbackStore } from '../fallback/FallbackStore.js';
import { knowledgeDb } from '../knowledge/KnowledgeDb.js';
import { vectorKnowledgeService } from '../knowledge/VectorKnowledgeService.js';
import { weatherService } from '../weather/WeatherService.js';
import { webSearchService } from '../websearch/WebSearchService.js';
import { shouldRetryWebAfterLlm } from '../websearch/LlmFreshnessFallback.js';
import { classifyCurrentDateTimeQuery } from './CurrentDateTimeIntent.js';
import { avatarTargetCoordinator } from '../avatar/AvatarTargetCoordinator.js';
import { ttsConfigStore } from '../tts/TtsConfigStore.js';
import { isCurrentContextQuestion } from '../knowledge/KnowledgeScope.js';
import { assessKnowledgeEntityConsistency } from '../knowledge/KnowledgeEntityConsistencyPolicy.js';
import {
  buildRagAnswerSystemPrompt,
  buildRagAnswerUserPrompt,
  RAG_ANSWER_PROMPT_VERSION,
  RAG_ANSWER_SCHEMA,
} from '../knowledge/RagAnswerPrompt.js';
import { answerProgressTracker } from '../streaming/AnswerProgressTracker.js';
import { contextResolver } from '../context/ContextResolver.js';
import { conversationSessionStore } from '../context/ConversationSessionStore.js';
import { classifyAssistantReply } from '../context/ConversationContextPolicy.js';
import { buildQaSystemPrompt } from '../api/ModelPrompt.js';
import { UNANSWERABLE_REPLY } from '../constants/AnswerMessages.js';

const ROBOT_DIRECT_ANSWER_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['answerStatus', 'reply'],
  properties: {
    answerStatus: { type: 'string', enum: ['ANSWERED', 'NO_KNOWLEDGE'] },
    reply: { type: 'string' },
  },
};

const ROBOT_DIRECT_SYSTEM_PROMPT = [
  '你是展厅机器人的问答助手。只输出符合指定 Schema 的 JSON。',
  '能可靠回答时返回 answerStatus=ANSWERED，并在 reply 中给出简洁中文答案。',
  '问题依赖企业内部、项目内部、未公开或特定展品资料，而当前上下文没有依据时，必须返回 answerStatus=NO_KNOWLEDGE 且 reply 为空。',
  '不得把猜测、常识推断或建议咨询包装成答案；不得在 reply 中输出拒答话术。',
  '普通常识、解释和写作问题可以直接回答；不确定的具体事实不得编造。',
  '不要生成动作、Markdown、标题或 Schema 以外的字段。',
].join('\n');

import { speechInterruption } from '../tts/SpeechInterruption.js';

class MessageRouter {
  constructor() {
    this._seq = 0;
    this._mediaResultSets = new Map();
    this._screenModes = new Map();
    this._filePickerStates = new Map();
    this._conversationTopics = new Map();
    this._recentControlActions = new Map();
    this._currentPptStates = new Map();
    this._routeTraces = new Map();
    this._conversationTurns = new Map();
    this._requestPolicies = new Map();
    this._completedOutcomes = new Map();
    conversationSessionStore.setHistoryLoader((conversationId, limit) =>
      usageStore.getConversationTurns(conversationId, limit));
    conversationSessionStore.onArchive(session => {
      wsServer.pushToAdmin({
        type: 'conversation_session_archived',
        session,
        serverTime: Date.now(),
      });
    });
  }

  _beginRouteTrace(seq, route) {
    const trace = [{
      handler: 'route_policy',
      matched: route?.mode !== 'NONE',
      status: route?.mode || 'NONE',
      reason: route?.reason || 'NO_MATCH',
      ruleId: route?.intent?.item?.id || '',
      ruleName: route?.intent?.item?.name || '',
    }];
    if (this._isContextDependentFollowup(seq)) {
      trace.push({
        handler: 'context_resolver',
        matched: true,
        status: 'CONTEXTUAL_FOLLOWUP',
        reason: '承接当前会话；明确 FAQ 命中仍优先，未命中时再使用上下文检索',
      });
    }
    this._routeTraces.set(seq, trace);
  }

  _traceRoute(seq, entry) {
    if (!this._routeTraces.has(seq)) this._routeTraces.set(seq, []);
    this._routeTraces.get(seq).push(entry);
  }

  _normalizePendingReviewTopic(value) {
    return String(value || '')
      .normalize('NFKC')
      .toLowerCase()
      .replace(/\.(?:pptx?|pdf|docx?|xlsx?)$/i, '')
      .replace(/[？?！!。,.，、；;：:\s“”"'‘’（）()[\]{}【】_—–-]+/g, '');
  }

  _pendingReviewTopicFor(text) {
    const normalizedText = this._normalizePendingReviewTopic(text);
    if (!normalizedText) return null;
    const genericAliases = new Set([
      '演示文稿', '介绍', '方案', '材料', '文件', '文档', '资料', '党政',
    ].map(value => this._normalizePendingReviewTopic(value)));

    for (const item of mediaLibrary.getAll()) {
      const status = String(item.status || '').trim().toLowerCase();
      const publicationStatus = String(item.publicationStatus || '').trim().toUpperCase();
      const reviewStatus = String(item.reviewStatus || '').trim().toUpperCase();
      const isPending = status === 'reviewing'
        || publicationStatus === 'PENDING_REVIEW'
        || reviewStatus === 'PENDING';
      if (!isPending) continue;

      const aliases = new Set();
      for (const rawName of [item.title, item.name, item.originalName]) {
        const baseName = String(rawName || '')
          .replace(/^.*[\\/]/, '')
          .replace(/\.(?:pptx?|pdf|docx?|xlsx?)$/i, '');
        const full = this._normalizePendingReviewTopic(baseName);
        if (full.length >= 4 && !genericAliases.has(full)) aliases.add(full);
        for (const segment of baseName.split(/[-_—–（）()【】\[\]\s]+/)) {
          const alias = this._normalizePendingReviewTopic(segment);
          if (alias.length >= 4 && !genericAliases.has(alias)) aliases.add(alias);
        }
      }

      const matchedAlias = [...aliases]
        .sort((a, b) => b.length - a.length)
        .find(alias => normalizedText.includes(alias));
      if (matchedAlias) return { item, alias: matchedAlias };
    }
    return null;
  }

  async _tryHandlePendingReviewTopic({
    text, clientInfo, seq, target, source, startedAt, echoToRequester = true,
  }) {
    const matched = this._pendingReviewTopicFor(text);
    if (!matched) return null;
    this._traceRoute(seq, {
      handler: 'pending_review_topic',
      matched: true,
      status: 'BLOCKED',
      reason: 'EXPLICIT_PENDING_REVIEW_TOPIC',
      ruleName: matched.item.title || matched.item.name || '',
    });
    const blockedResult = {
      reply: UNANSWERABLE_REPLY,
      actions: [],
      context: {
        answerSource: '待审核知识',
        robotAnswerStatus: 'NO_KNOWLEDGE',
        refusalFallback: true,
        contextEligible: false,
        pendingReviewTopic: true,
        pendingReviewFile: matched.item.name || matched.item.originalName || '',
        finalRoute: 'PENDING_REVIEW_TOPIC',
      },
    };
    const processed = await this._processModelResult(clientInfo, blockedResult, seq, {
      echoToRequester,
      fallbackQuestion: text,
      fallbackTarget: target,
      source,
      durationMs: Date.now() - startedAt,
      answerSource: '待审核知识',
    });
    return {
      seq,
      reply: processed?.reply || UNANSWERABLE_REPLY,
      actions: processed?.actions || [],
      target,
      answerSource: '待审核知识',
      context: processed?.context || blockedResult.context,
    };
  }

  route(clientInfo, msg) {
    const seq = this._nextSeq();
    console.log(`[Router] [seq=${seq}] type=${msg.type}, device=${clientInfo.deviceId}, clientType=${clientInfo.type}`);

    switch (msg.type) {
      case 'chat':
        return this._handleChat(clientInfo, msg, seq);
      case 'voice':
        return this._handleVoice(clientInfo, msg, seq);
      case 'command':
        return this._handleCommand(clientInfo, msg, seq);
      case 'status':
        return this._handleStatus(clientInfo, seq);
      case 'sync':
        return this._handleSync(clientInfo, seq);
      default:
        console.warn(`[Router] Unknown message type: ${msg.type}`);
    }
  }

  async handleExternalText({
    text,
    target = 'all',
    source = 'external',
    requestId = '',
    conversationId = '',
    suppressActions = false,
    clientMode = '',
  }) {
    const seq = this._nextSeq();
    const startedAt = Date.now();
    const clientInfo = {
      clientId: null,
      deviceId: source,
      type: config.DEVICE_TYPES.MOBILE,
    };
    this._requestPolicies.set(seq, {
      suppressActions: suppressActions === true,
      clientMode: String(clientMode || '').toUpperCase(),
    });
    const robotMode = String(clientMode || '').toUpperCase() === 'ROBOT';
    this._beginConversationTurn(seq, {
      text,
      clientInfo,
      source,
      conversationId,
    });
    this._startAnswerProgress({
      seq, requestId, question: text, target, source, clientInfo,
    });
    this._emitAnswerProgress(seq, 'ROUTING', {
      message: '正在判断回答方式…',
      clientInfo,
    });
    const wakeGreetingResult = await this._tryHandleWakeGreeting({
      text,
      clientInfo,
      seq,
      target,
      source,
      startedAt,
    });
    if (wakeGreetingResult) return wakeGreetingResult;

    if (!suppressActions && ttsConfigStore.get().avatarEnabled) {
      const prepared = avatarTargetCoordinator.prepare(target);
      if (!prepared.ok && !prepared.skipped) {
        console.warn(`[AvatarTarget] prepare failed: ${prepared.reason}`);
      }
    }
    this._rememberConversationTopic(text, clientInfo, source);
    const webRoute = webSearchService.classifyRoute(text, { target });
    this._beginRouteTrace(seq, webRoute);
    const preferWebSearch = webRoute.mode !== 'NONE' && webSearchService.shouldBypassLocalQa(text, { target });

    const switchResult = !robotMode && await this._tryHandleModelSwitch({
      text,
      clientInfo,
      seq,
      target,
      source,
      startedAt,
      echoToRequester: false,
    });
    if (switchResult) return switchResult;

    const directControlResult = !robotMode && await this._tryHandleDirectControlCommand({
      text,
      clientInfo,
      seq,
      target,
      source,
      startedAt,
      echoToRequester: false,
    });
    if (directControlResult) return directControlResult;

    const specialResult = !robotMode && await this._tryHandleSpecialSwitch({
      text,
      clientInfo,
      seq,
      source,
      startedAt,
      echoToRequester: false,
    });
    if (specialResult) return specialResult;

    if (!robotMode && this._isExplicitMediaActionText(text)) {
      const explicitMediaResult = await this._tryHandleMediaCommand({
        text, clientInfo, seq, target, source, startedAt, echoToRequester: false,
      });
      if (explicitMediaResult) return explicitMediaResult;
    }

    const pendingReviewResult = await this._tryHandlePendingReviewTopic({
      text, clientInfo, seq, target, source, startedAt, echoToRequester: false,
    });
    if (pendingReviewResult) return pendingReviewResult;

    const approvedWebQaResult = await this._tryHandleApprovedWebQaAnswer({
      text, clientInfo, seq, target, source, startedAt, echoToRequester: false,
    });
    if (approvedWebQaResult) return approvedWebQaResult;

    if (webRoute.mode === 'MUST_WEB') {
      const mustWebResult = await this._tryHandleWebSearch({
        text, clientInfo, seq, target, source, startedAt, echoToRequester: false, forceIntent: true,
      });
      if (mustWebResult) return mustWebResult;
    }

    const dateTimeResult = await this._tryHandleDateTimeQuery({
      text,
      clientInfo,
      seq,
      target,
      source,
      startedAt,
      echoToRequester: false,
    });
    if (dateTimeResult) return dateTimeResult;

    const weatherResult = await this._tryHandleWeatherQuery({
      text,
      clientInfo,
      seq,
      target,
      source,
      startedAt,
      echoToRequester: false,
    });
    if (weatherResult) return weatherResult;

    const commentaryResult = await this._tryHandleCurrentPptCommentary({
      text,
      clientInfo,
      seq,
      target,
      source,
      startedAt,
      echoToRequester: false,
    });
    if (commentaryResult) return commentaryResult;

    const mediaResult = !robotMode && await this._tryHandleMediaCommand({
      text,
      clientInfo,
      seq,
      target,
      source,
      startedAt,
      echoToRequester: false,
    });
    if (mediaResult) return mediaResult;

    if (preferWebSearch) {
      const priorityWebResult = await this._tryHandleWebSearch({
        text, clientInfo, seq, target, source, startedAt, echoToRequester: false,
      });
      if (priorityWebResult) return priorityWebResult;
    }

    const contextualFollowup = this._isContextDependentFollowup(seq);
    const qaResult = await this._tryHandleQaAnswer({
      text, clientInfo, seq, target, source, startedAt, echoToRequester: false,
    });
    if (qaResult) return qaResult;

    const configuredFallbackResult = !contextualFollowup && await this._tryHandleConfiguredFallback({
      text, clientInfo, seq, target, source, startedAt, echoToRequester: false,
    });
    if (configuredFallbackResult) return configuredFallbackResult;

    const webSearchResult = !preferWebSearch && await this._tryHandleWebSearch({
      text,
      clientInfo,
      seq,
      target,
      source,
      startedAt,
      echoToRequester: false,
    });
    if (webSearchResult) return webSearchResult;

    const ragResult = await this._tryHandleMarkdownRag({
      text,
      retrievalText: this._retrievalTextFor(seq, text),
      clientInfo,
      seq,
      target,
      source,
      startedAt,
      echoToRequester: false,
    });
    if (ragResult) return ragResult;

    this._dispatchPendingQuestion({ text, target, clientInfo, seq });

    const result = await this._chatDirectLlm({
      text,
      seq,
      deviceId: source,
      deviceType: config.DEVICE_TYPES.MOBILE,
      clientId: null,
      isVoice: true,
      robotStatusContract: robotMode,
    });

    if (result.error) {
      this._recordFailure({ seq, source, deviceId: source, deviceType: config.DEVICE_TYPES.MOBILE, target, question: text, error: result.error, startedAt });
      const fallbackResult = this._buildHelpfulShowQaResult(text, target, { modelError: result.error });
      const processed = await this._processModelResult(clientInfo, fallbackResult, seq, {
        echoToRequester: false,
        fallbackQuestion: text,
        fallbackTarget: target,
        source,
        durationMs: Date.now() - startedAt,
        answerSource: '规则兜底',
      });
      return { seq, reply: fallbackResult.reply, actions: processed?.actions || fallbackResult.actions, target };
    }

    if (robotMode && result.context?.robotAnswerStatus === 'NO_KNOWLEDGE') {
      const noKnowledgeResult = {
        reply: UNANSWERABLE_REPLY,
        actions: [],
        context: {
          ...(result.context || {}),
          answerSource: 'LLM',
          refusalFallback: true,
          contextEligible: false,
          finalRoute: 'LLM_NO_KNOWLEDGE',
        },
      };
      const processed = await this._processModelResult(clientInfo, noKnowledgeResult, seq, {
        echoToRequester: false,
        fallbackQuestion: text,
        fallbackTarget: target,
        source,
        durationMs: Date.now() - startedAt,
        answerSource: 'LLM',
      });
      return {
        seq,
        reply: processed?.reply || UNANSWERABLE_REPLY,
        actions: processed?.actions || [],
        target,
        answerSource: 'LLM',
        context: processed?.context || noKnowledgeResult.context,
      };
    }

    const llmWebFallback = await this._tryHandleLlmFreshnessFallback({
      text,
      rawReply: result.reply,
      clientInfo,
      seq,
      target,
      source,
      startedAt,
      echoToRequester: false,
    });
    if (llmWebFallback) return llmWebFallback;

    const normalized = this._limitAiReplyResult(this._ensureHelpfulModelResult(this._applyDefaultTarget(result, target), text));
    normalized.context = { ...(normalized.context || {}), answerSource: normalized.context?.answerSource || 'LLM', attachRelatedMedia: true };
    const cachedQa = this._saveAutoAnswerToQa({
      question: text,
      answer: normalized.reply,
      sourceLabel: 'llm-cache',
      actions: normalized.actions,
    });
    if (cachedQa) normalized.context.cachedQaId = cachedQa.id;
    const processed = await this._processModelResult(clientInfo, normalized, seq, {
      echoToRequester: false,
      fallbackQuestion: text,
      fallbackTarget: target,
      source,
      durationMs: Date.now() - startedAt,
      answerSource: normalized.context.answerSource,
    });

    return {
      seq,
      reply: normalized.reply || '',
      actions: processed?.actions || normalized.actions || [],
      target,
      answerSource: processed?.answerSource || normalized.context?.answerSource || 'LLM',
      context: processed?.context || normalized.context || {},
    };
  }

  consumeCompletedOutcome(seq) {
    const outcome = this._completedOutcomes.get(seq) || null;
    this._completedOutcomes.delete(seq);
    return outcome;
  }

  async handleRemoteControl({ action, target = 'all', source = 'external' }) {
    const seq = this._nextSeq();
    const clientInfo = {
      clientId: null,
      deviceId: source,
      type: config.DEVICE_TYPES.MOBILE,
    };

    if (String(action || '').startsWith('gesture_')) {
      return this._handleGestureControl({ action, target, source, clientInfo, seq });
    }

    if (action === 'special_files_show') {
      return this._showSpecialFilesForTarget({ target, clientInfo, seq, source });
    }

    const actionMap = {
      special_files_next: 'special_file_next',
      special_files_prev: 'special_file_prev',
      special_files_open: 'special_file_open_selected',
      ppt_next: 'next_slide',
      ppt_prev: 'prev_slide',
      ppt_speak_commentary: 'ppt_speak_commentary',
    };
    const mapped = actionMap[action];
    if (!mapped) {
      return { seq, error: `unknown remote action: ${action}` };
    }

    if (this._isDuplicateControlAction({ source, target, action: mapped })) {
      return { seq, action: mapped, target, ignored: true, duplicate: true };
    }

    this._dispatchAction({ type: mapped, target, params: this._commentaryParamsForTarget(target) }, clientInfo, seq);
    return { seq, action: mapped, target };
  }

  handleFaceWelcome({ person, source = 'face-camera', cameraId = '', target = 'all' }) {
    const seq = this._nextSeq();
    const clientInfo = {
      clientId: null,
      deviceId: source,
      type: config.DEVICE_TYPES.MOBILE,
    };
    const result = {
      seq,
      personId: person.personId,
      name: person.name,
      cameraId,
      target,
      special: null,
      welcomeText: person.welcomeText || `欢迎${person.name}莅临参观。`,
    };

    if (person.specialName) {
      try {
        const applied = this.applySpecial(person.specialName, { clientInfo, seq, source });
        result.special = applied.special?.name || person.specialName;
      } catch (err) {
        result.specialError = err.message;
      }
    }

    this._dispatchAction({
      type: 'speak',
      target,
      params: { text: result.welcomeText, preserveView: 'true' },
    }, clientInfo, seq, result.welcomeText);

    wsServer.pushToAdmin({
      type: 'face_event',
      event: 'recognized',
      personId: person.personId,
      name: person.name,
      score: person.score,
      cameraId,
      target,
      specialName: result.special,
      welcomeText: result.welcomeText,
      seq,
      serverTime: Date.now(),
    });
    return result;
  }

  _handleGestureControl({ action, target, source, clientInfo, seq }) {
    const screenNumber = Number.parseInt(target, 10);
    if (!Number.isFinite(screenNumber) || screenNumber <= 0) {
      return { seq, error: '请先选择一个具体屏幕' };
    }

    if (action === 'gesture_triple_tap') {
      const result = this._showSpecialFilesForTarget({ target: screenNumber, clientInfo, seq, source, logToAdmin: false });
      this._recordGestureInteraction({
        seq,
        source,
        target: screenNumber,
        sourceAction: action,
        mode: 'normal',
        mappedAction: result.error ? '' : 'show_special_file_strip',
        ignored: Boolean(result.error),
        error: result.error,
      });
      return result;
    }

    if (action === 'gesture_swipe_up') {
      this._dispatchAction({ type: 'home', target: screenNumber, params: {} }, clientInfo, seq);
      this._recordGestureInteraction({
        seq,
        source,
        target: screenNumber,
        sourceAction: action,
        mode: 'return',
        mappedAction: 'home',
      });
      return { seq, action: 'home', sourceAction: action, target: screenNumber, mode: 'return' };
    }

    const mode = this._getScreenMode(screenNumber);
    const filePickerMap = {
      gesture_swipe_left: 'special_file_prev',
      gesture_swipe_right: 'special_file_next',
      gesture_swipe_down: 'special_file_next',
      gesture_double_tap: 'special_file_open_selected',
    };
    const pptMap = {
      gesture_swipe_left: 'next_slide',
      gesture_swipe_right: 'prev_slide',
      gesture_double_tap: 'ppt_speak_commentary',
    };

    const mapped = mode === 'file_picker'
      ? filePickerMap[action]
      : mode === 'ppt'
        ? pptMap[action]
        : null;

    if (!mapped) {
      this._recordGestureInteraction({
        seq,
        source,
        target: screenNumber,
        sourceAction: action,
        mode,
        ignored: true,
      });
      return { seq, action, target: screenNumber, ignored: true, mode };
    }

    if (this._isDuplicateControlAction({ source, target: screenNumber, action: mapped })) {
      this._recordGestureInteraction({
        seq,
        source,
        target: screenNumber,
        sourceAction: action,
        mode,
        mappedAction: mapped,
        ignored: true,
        error: '重复手势已忽略',
      });
      return { seq, action: mapped, sourceAction: action, target: screenNumber, mode, ignored: true, duplicate: true };
    }

    this._dispatchAction({ type: mapped, target: screenNumber, params: this._commentaryParamsForTarget(screenNumber) }, clientInfo, seq);
    this._recordGestureInteraction({
      seq,
      source,
      target: screenNumber,
      sourceAction: action,
      mode,
      mappedAction: mapped,
    });
    return { seq, action: mapped, sourceAction: action, target: screenNumber, mode };
  }

  _isDuplicateControlAction({ source, target, action }) {
    if (!['next_slide', 'prev_slide'].includes(action)) return false;

    const now = Date.now();
    const key = `${source || 'unknown'}:${target || 'all'}:${action}`;
    const lastAt = this._recentControlActions.get(key) || 0;
    this._recentControlActions.set(key, now);

    for (const [itemKey, itemAt] of this._recentControlActions) {
      if (now - itemAt > 5000) this._recentControlActions.delete(itemKey);
    }

    return now - lastAt < 800;
  }

  _recordGestureInteraction({ seq, source, target, sourceAction, mode, mappedAction = '', ignored = false, error = '' }) {
    const gestureLabel = this._gestureLabel(sourceAction);
    const modeLabel = this._screenModeLabel(mode);
    const mappedLabel = ignored
      ? (error || '当前状态下无对应功能，已忽略')
      : this._controlActionLabel(mappedAction);
    const question = `APK手势：${gestureLabel}（${target}号屏）`;
    const answer = `当前模式：${modeLabel}；中控解析：${mappedLabel}`;
    const actions = mappedAction ? [{ type: mappedAction, target }] : [];

    usageStore.recordInteraction({
      seq,
      source,
      deviceId: source,
      deviceType: config.DEVICE_TYPES.MOBILE,
      target,
      question,
      answer,
      actions,
      answerSource: ignored ? '控制忽略' : '控制',
      ok: !error,
    });

    wsServer.pushToAdmin({
      type: 'conversation_log',
      deviceId: source,
      input: question,
      reply: answer,
      actions,
      seq,
      answerSource: ignored ? '控制忽略' : '控制',
    });
  }

  _gestureLabel(action) {
    return {
      gesture_triple_tap: '三击',
      gesture_double_tap: '双击',
      gesture_swipe_left: '左滑',
      gesture_swipe_right: '右滑',
      gesture_swipe_up: '上滑',
      gesture_swipe_down: '下滑',
    }[action] || action || '未知手势';
  }

  _screenModeLabel(mode) {
    return {
      file_picker: '文件选择',
      ppt: 'PPT播放',
      return: '返回控制',
      normal: '普通展示',
    }[mode] || mode || '普通展示';
  }

  _controlActionLabel(action) {
    return {
      show_special_file_strip: '显示当前专场文件列表',
      special_file_prev: '文件列表选择上一个',
      special_file_next: '文件列表选择下一个',
      special_file_open_selected: '打开当前选中文件',
      next_slide: 'PPT下一页',
      prev_slide: 'PPT上一页',
      ppt_speak_commentary: '播放当前PPT讲解词',
      home: '返回首页/屏保',
    }[action] || action || '无动作';
  }

  _nextSeq() {
    return ++this._seq;
  }

  interruptSpeech(target, requestId) {
    const canceled = speechInterruption.cancel(target, this._seq);
    const device = deviceManager.getByScreenNumber(Number(target));
    const seq = this._nextSeq();
    const delivered = Boolean(device?.online && wsServer.sendToDevice(device.id, {
      type: 'command', action: 'stop_speech', params: { requestId },
      seq, serverTime: Date.now(),
    }));
    console.log(`[SpeechInterrupt] target=${target} cutoff=${canceled.cutoffSeq} delivered=${delivered} canceled=${canceled.canceledGenerations}`);
    return { ...canceled, seq, delivered };
  }

  _beginConversationTurn(seq, { text, clientInfo, source, conversationId = '', forceNew = false }) {
    const turn = contextResolver.begin({
      text,
      clientInfo,
      source,
      conversationId,
      forceNew,
    });
    this._conversationTurns.set(seq, turn);
    return turn;
  }

  _conversationTurnFor(seq) {
    return this._conversationTurns.get(seq) || null;
  }

  _modelContextFor(seq) {
    return contextResolver.buildModelContext(this._conversationTurnFor(seq));
  }

  _modelMessagesFor(seq) {
    return contextResolver.buildModelMessages(this._conversationTurnFor(seq));
  }

  async _chatDirectLlm({ text, seq, robotStatusContract = false, ...requestContext }) {
    const contextualFollowup = this._isContextDependentFollowup(seq);
    const modelQuestion = contextualFollowup
      ? [
          '当前问题承接上一轮对话。请沿用上一轮中的主体、地点、对象和限定范围，只回答当前追问的新关注点。',
          this._retrievalTextFor(seq, text),
        ].join('\n')
      : text;
    const request = {
      text: modelQuestion,
      ...requestContext,
      context: '',
      conversationMessages: this._modelMessagesFor(seq),
      temperature: 0.1,
    };
    if (robotStatusContract) {
      const result = await modelProvider.chat({
        ...request,
        systemPrompt: ROBOT_DIRECT_SYSTEM_PROMPT,
        responseSchema: ROBOT_DIRECT_ANSWER_SCHEMA,
        preserveRaw: true,
        maxTokens: 260,
      });
      return this._normalizeRobotDirectLlmResult(result);
    }
    const first = await modelProvider.chat(request);
    const firstClassification = classifyAssistantReply(first?.reply);
    const firstContext = {
      ...(first?.context || {}),
      directLlmAttemptCount: 1,
      directLlmReplyKind: first.error ? 'ERROR' : firstClassification.kind,
    };
    if (first.error || firstClassification.kind !== 'PURE_REFUSAL' || !this._canRetryDirectLlmWithoutHistory(text, seq)) {
      return { ...first, context: firstContext };
    }

    const retried = await modelProvider.chat({
      ...request,
      conversationMessages: this._isContextDependentFollowup(seq)
        ? request.conversationMessages
        : [],
      systemPrompt: `${buildQaSystemPrompt()}\n这是一次拒答后的尽力回答重试：请直接回答当前问题；不确定的内容可以明确标注，但不得只输出拒答或转人工提示。`,
    });
    const retryClassification = classifyAssistantReply(retried?.reply);
    if (!retried.error && retryClassification.useful) {
      return {
        ...retried,
        context: {
          ...(retried.context || {}),
          directLlmAttemptCount: 2,
          directLlmCleanContextRetry: true,
          directLlmReplyKind: retryClassification.kind,
        },
      };
    }
    return {
      ...first,
      context: {
        ...firstContext,
        directLlmAttemptCount: 2,
        directLlmCleanContextRetry: true,
        directLlmRetryKind: retried.error ? 'ERROR' : retryClassification.kind,
      },
    };
  }


  _extractPartialRobotDirectAnswer(value) {
    const source = String(value || '').trim();
    const status = source.match(/"(?:answerStatus|status)"\s*:\s*"([^"]+)"/i)?.[1] || '';
    const marker = /"(?:reply|answer|text)"\s*:\s*"/i.exec(source);
    if (!marker) return { answerStatus: status, reply: '' };
    const remainder = source.slice(marker.index + marker[0].length);
    let end = -1;
    let escaped = false;
    for (let index = 0; index < remainder.length; index += 1) {
      const character = remainder[index];
      if (escaped) {
        escaped = false;
      } else if (character === '\\') {
        escaped = true;
      } else if (character === '"') {
        end = index;
        break;
      }
    }
    const encoded = (end >= 0 ? remainder.slice(0, end) : remainder).replace(/\\$/, '');
    try {
      return { answerStatus: status, reply: JSON.parse(`"${encoded}"`) };
    } catch {
      return {
        answerStatus: status,
        reply: encoded.replace(/\\n/g, '\n').replace(/\\"/g, '"').replace(/\\\\/g, '\\'),
      };
    }
  }
  _normalizeRobotDirectLlmResult(result = {}) {
    if (result.error) return result;
    const parsed = this._extractJsonObject(result.reply);
    const partial = parsed ? null : this._extractPartialRobotDirectAnswer(result.reply);
    const rawStatus = String(parsed?.answerStatus || parsed?.status || partial?.answerStatus || '').trim().toUpperCase();
    const rawReply = String(parsed?.reply || parsed?.answer || parsed?.text || partial?.reply || '').trim();
    const statusAliases = {
      ANSWER: 'ANSWERED',
      SUCCESS: 'ANSWERED',
      ABSTAINED: 'NO_KNOWLEDGE',
      INSUFFICIENT_EVIDENCE: 'NO_KNOWLEDGE',
    };
    let answerStatus = statusAliases[rawStatus] || rawStatus;
    let reply = rawReply;

    if (answerStatus === 'NO_KNOWLEDGE') {
      reply = '';
    } else if (answerStatus === 'ANSWERED' && !reply) {
      answerStatus = 'NO_KNOWLEDGE';
    } else if (!['ANSWERED', 'NO_KNOWLEDGE'].includes(answerStatus)) {
      const resultText = String(result.reply || '').trim();
      const looksStructured = /^\s*[\[{]/.test(resultText) || /"answerStatus"\s*:/.test(resultText);
      const plainReply = parsed || looksStructured ? rawReply : resultText;
      const classification = classifyAssistantReply(plainReply);
      if (classification.kind === 'PURE_REFUSAL') {
        answerStatus = 'NO_KNOWLEDGE';
        reply = '';
      } else if (classification.useful && plainReply) {
        answerStatus = 'ANSWERED';
        reply = plainReply;
      }
    }

    if (!['ANSWERED', 'NO_KNOWLEDGE'].includes(answerStatus)) {
      return {
        error: 'invalid robot direct-answer contract',
        context: {
          ...(result.context || {}),
          robotAnswerStatus: 'FAILED',
          technicalFallback: true,
          failureCode: 'ROBOT_DIRECT_ANSWER_CONTRACT_INVALID',
        },
      };
    }
    return {
      ...result,
      reply,
      actions: [],
      context: {
        ...(result.context || {}),
        robotAnswerStatus: answerStatus,
        refusalFallback: answerStatus === 'NO_KNOWLEDGE',
        contextEligible: answerStatus === 'ANSWERED',
      },
    };
  }

  _canRetryDirectLlmWithoutHistory(text, seq) {
    if (this._isExplicitMediaActionText(text)) return false;
    return true;
  }

  _isContextDependentFollowup(seq) {
    return this._conversationTurnFor(seq)?.contextualFollowup === true;
  }

  _retrievalTextFor(seq, fallbackText = '') {
    return String(this._conversationTurnFor(seq)?.retrievalText || fallbackText || '').trim();
  }

  _startAnswerProgress({ seq, requestId = '', question = '', target = 'all', source = '', clientInfo }) {
    const state = answerProgressTracker.start({
      seq,
      requestId,
      question,
      target,
      source,
      message: '已收到问题',
    });
    this._publishAnswerProgress(state, clientInfo);
    return state;
  }

  _emitAnswerProgress(seq, stage, options = {}) {
    const state = answerProgressTracker.advance(seq, stage, options);
    if (!state) return null;
    this._publishAnswerProgress(state, options.clientInfo);
    if (options.showOnScreen && !state.terminal) {
      const clientInfo = options.clientInfo || {
        deviceId: state.source || 'answer-progress',
        type: config.DEVICE_TYPES.ADMIN,
      };
      this._dispatchAction({
        type: 'pending_qa',
        target: state.target,
        params: {
          question: state.question,
          answer: state.message,
          status: state.stage,
          statusText: state.message,
          query: state.query || '',
          sourceCount: state.sourceCount ?? '',
          streaming: true,
        },
      }, clientInfo, state.seq);
    }
    return state;
  }

  _publishAnswerProgress(state, clientInfo) {
    if (!state) return;
    const event = {
      type: 'answer_progress',
      requestId: state.requestId || '',
      seq: state.seq,
      stage: state.stage,
      message: state.message,
      question: state.question,
      target: state.target,
      source: state.source,
      query: state.query || '',
      sourceCount: state.sourceCount,
      answer: state.answer || '',
      terminal: state.terminal === true,
      startedAt: state.startedAt,
      serverTime: Date.now(),
      conversationId: this._conversationTurnFor(state.seq)?.conversationId || '',
    };
    wsServer.pushToAdmin(event);
    if (clientInfo?.clientId && clientInfo.type !== config.DEVICE_TYPES.ADMIN) {
      wsServer.sendTo(clientInfo.clientId, event);
    }
  }

  _withStreamingPresentation(actions, seq) {
    return (actions || []).map(action => {
      if (action?.type !== 'show_qa') return action;
      const speechSegments = this._splitSpeechSegments(action.params?.answer);
      return {
        ...action,
        params: {
          ...(action.params || {}),
          presentation: 'typewriter',
          charIntervalMs: 28,
          progressSeq: seq,
          speechMode: 'sentence_queue',
          speechSegments,
        },
      };
    });
  }

  _splitSpeechSegments(value) {
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    if (!text) return [];
    const sentences = text.match(/[^。！？!?；;\n]+[。！？!?；;]?/g) || [text];
    const segments = [];
    for (const sentence of sentences) {
      const cleanSentence = sentence.trim();
      if (!cleanSentence) continue;
      if (cleanSentence.length <= 90) {
        segments.push(cleanSentence);
        continue;
      }
      const clauses = cleanSentence.match(/[^，,：:]+[，,：:]?/g) || [cleanSentence];
      let buffer = '';
      for (const clause of clauses) {
        if (buffer && buffer.length + clause.length > 90) {
          segments.push(buffer.trim());
          buffer = '';
        }
        buffer += clause;
      }
      if (buffer.trim()) segments.push(buffer.trim());
    }
    return segments.slice(0, 20);
  }

  _applyDefaultTarget(result, target) {
    const actions = (result.actions || []).map(action => ({
      ...action,
      target: action.target && action.target !== 'all' ? action.target : target,
    }));
    return { ...result, actions };
  }

  _limitAiReplyResult(result) {
    const reply = this._shortDisplayReply(result.reply, result.fallbackQuestion || '');
    const actions = (result.actions || []).map(action => {
      const params = { ...(action.params || {}) };
      if (params.answer) params.answer = this._shortDisplayReply(params.answer, params.question || result.fallbackQuestion || '');
      if (params.text) params.text = this._shortDisplayReply(params.text, result.fallbackQuestion || '');
      return { ...action, params };
    });
    return { ...result, reply, actions };
  }

  _ensureHelpfulModelResult(result, question) {
    const fallback = this._buildGenericFallbackReply(question);
    const rawReply = String(result?.reply || '').trim();
    const replyClassification = classifyAssistantReply(rawReply);
    const offTopic = this._isOffTopicModelReply(rawReply, question);
    const useFallback = !replyClassification.useful || offTopic;
    const reply = useFallback
      ? fallback
      : this._shortDisplayReply(rawReply, question);
    const actions = (result?.actions || []).map(action => {
      if (action?.type !== 'show_qa') return action;
      const params = { ...(action.params || {}) };
      if (this._isUnhelpfulModelReply(params.answer) || this._isOffTopicModelReply(params.answer, question)) {
        params.answer = fallback;
      } else {
        params.answer = this._shortDisplayReply(params.answer, question);
      }
      return { ...action, params };
    });

    return {
      ...result,
      reply: reply || fallback,
      actions,
      fallbackQuestion: result?.fallbackQuestion || question,
      context: {
        ...(result?.context || {}),
        replyKind: replyClassification.kind,
        technicalFallback: replyClassification.technicalFallback === true,
        refusalFallback: replyClassification.kind === 'PURE_REFUSAL',
        invalidModelAnswer: offTopic,
        contextEligible: !useFallback,
      },
    };
  }

  _isUnhelpfulModelReply(reply) {
    return !classifyAssistantReply(reply).useful;
  }

  _shortDisplayReply(reply, question = '') {
    const raw = String(reply || '').trim();
    if (!raw) return '';
    const cleaned = this._cleanGeneratedPptFaqReply(this._cleanModelReplyText(raw), question);
    if (this._isShortScreenReply(cleaned, question)) return cleaned;

    return this._makeCompleteShortSentence(cleaned, question);
  }

  _cleanModelReplyText(reply) {
    return String(reply || '')
      .replace(/```[\s\S]*?```/g, ' ')
      .split(/\r?\n/)
      .filter(line => {
        const value = line.trim();
        if (!value) return false;
        if (/^\|.*\|$/.test(value)) return false;
        if (/^[-*_]{3,}$/.test(value)) return false;
        return true;
      })
      .join(' ')
      .replace(/\*\*/g, '')
      .replace(/#{1,6}\s*/g, '')
      .replace(/[|`>*_•💡🌾📚🚀]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  _cleanGeneratedPptFaqReply(reply, question = '') {
    let value = String(reply || '').trim();
    if (!value) return '';

    const genericLead = value.match(/^本页围绕(.{1,100}?)展开说明[。,.，]?\s*(.*)$/);
    if (!genericLead) return value;

    const title = genericLead[1].trim().replace(/[“”"]/g, '');
    const rest = genericLead[2].trim();
    const useful = this._firstMeaningfulPptPhrase(rest, question, title);
    if (useful) return `${title}主要展示${useful}`;
    return `${title}主要介绍页面中的核心能力、应用场景和价值收益。`;
  }

  _firstMeaningfulPptPhrase(text, question = '', title = '') {
    const stop = new Set(['客户价值', '运营商价值', '生态价值', '页面原始信息包括', '页面显性文字']);
    const q = String(question || '').replace(/\s+/g, '');
    const source = String(text || '')
      .replace(/[“”"]/g, '')
      .replace(/\s+/g, ' ')
      .trim();
    if (!source) return '';

    const parts = source
      .split(/(?:(?:\s+)|[，,；;。！？!?、]+)/)
      .map(item => item.trim())
      .filter(item => item.length >= 2)
      .filter(item => !stop.has(item))
      .filter(item => !title || !title.includes(item))
      .filter(item => !q.includes(item) || item.length >= 5);

    const selected = [];
    for (const part of parts) {
      if (selected.join('，').length + part.length > 72) break;
      selected.push(part);
      if (selected.length >= 5) break;
    }
    return selected.join('，');
  }

  _extractFaqPackageAnswer(answer, question = '') {
    const text = String(answer || '');
    if (!/\|\s*ID\s*\|/.test(text) || !/\|\s*建议回答\s*\|/.test(text)) return '';

    const keywords = this._questionKeywords(question);
    const rows = text
      .split(/\r?\n/)
      .map(line => line.trim())
      .filter(line => /^\|\s*FAQ-[^|]+\|/.test(line))
      .map(line => line
        .replace(/^\|/, '')
        .replace(/\|$/, '')
        .split('|')
        .map(cell => cell.trim()))
      .filter(cells => cells.length >= 4);

    if (!rows.length) return '';

    const scored = rows.map(cells => {
      const userQuestion = cells[1] || '';
      const intent = cells[2] || '';
      const suggestedAnswer = cells[3] || '';
      const haystack = `${userQuestion}${intent}${suggestedAnswer}`.toLowerCase();
      const score = keywords.reduce((total, keyword) => total + (haystack.includes(keyword.toLowerCase()) ? keyword.length : 0), 0);
      return { score, suggestedAnswer };
    });

    scored.sort((a, b) => b.score - a.score);
    return (scored[0]?.score > 0 ? scored[0].suggestedAnswer : rows[0][3]) || '';
  }

  _questionKeywords(question) {
    const value = String(question || '')
      .replace(/[？?！!。,.，、:：;；"'“”‘’（）()[\]{}<>《》【】]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    if (!value) return [];
    const tokens = [
      ...(value.match(/[A-Za-z0-9+_-]{2,}/g) || []),
      ...(value.match(/[\u4e00-\u9fa5]{2,}/g) || []),
    ];
    const stopWords = new Set(['什么', '是什么', '介绍一下', '说一下', '讲一下', '中国移动']);
    return [...new Set(tokens.map(item => item.trim()).filter(item => item.length >= 2 && !stopWords.has(item)))];
  }

  _isShortScreenReply(reply, question = '') {
    const value = String(reply || '').trim();
    if (!value) return false;
    if (/[#*|`💡🌾📚🚀]/.test(value)) return false;
    if (/[（(][^）)]*$/.test(value)) return false;
    if (this._isLikelyHeadingSentence(value, question)) return false;
    return value.length <= 100;
  }

  _extractCoreReplySentence(text, question = '') {
    const value = String(text || '').trim();
    const coreMatch = value.match(/核心(?:观点|结论)[:：]\s*([^。！？!?]{8,120}[。！？!?]?)/);
    if (coreMatch?.[1]) return this._normalizeSentence(coreMatch[1]);

    const q = String(question || '').replace(/\s+/g, '');
    const keywords = [
      ...q.match(/[A-Za-z0-9]+/g) || [],
      ...q.match(/[\u4e00-\u9fa5]{2,}/g) || [],
    ].map(item => item.toLowerCase()).filter(item => item.length >= 2);

    const sentences = value
      .replace(/([。！？!?])\s*/g, '$1\n')
      .split(/\n+/)
      .map(item => this._normalizeSentence(item))
      .filter(item => item.length >= 8 && !/^(根据您的问题|如果您需要|建议关注|总结与建议)/.test(item))
      .filter(item => !this._isLikelyHeadingSentence(item, question));

    const related = sentences.find(sentence => {
      const lower = sentence.toLowerCase();
      return keywords.some(keyword => lower.includes(keyword));
    });
    return related || sentences[0] || '';
  }

  _makeCompleteShortSentence(text, question = '') {
    const maxLength = 100;
    const source = this._normalizeSentence(text);
    const parts = source
      .replace(/([。！？!?])\s*/g, '$1\n')
      .split(/\n+/)
      .map(item => this._normalizeSentence(item))
      .filter(Boolean);
    let result = '';
    for (const part of parts) {
      const candidate = result ? `${result}${part}` : part;
      if (candidate.length > maxLength) break;
      result = candidate;
    }
    if (!result || result.length < 6) {
      result = this._firstCompleteSlice(source, maxLength);
    }
    return this._normalizeSentence(result);
  }

  _isLikelyHeadingSentence(text, question = '') {
    const value = String(text || '').replace(/[。！？!?]+$/g, '').trim();
    if (!value) return false;
    if (/^(什么是|何为|关于|一、|二、|三、|核心观点|核心结论)/.test(value)) return true;
    if (/[？?]$/.test(value)) return true;

    const normalized = value.replace(/\s+/g, '').replace(/[，。！？、,.!?;；:"'“”‘’（）()[\]{}<>《》【】]/g, '').toLowerCase();
    const q = String(question || '').replace(/\s+/g, '').replace(/[，。！？、,.!?;；:"'“”‘’（）()[\]{}<>《》【】]/g, '').toLowerCase();
    if (q && normalized && (normalized === q || q.includes(normalized))) return true;
    return false;
  }

  _firstCompleteSlice(text, maxLength = 100) {
    const source = String(text || '').replace(/[。！？!?]+$/g, '').trim();
    if (source.length <= maxLength) return source;
    const cut = source.slice(0, maxLength);
    const lastBreak = Math.max(
      cut.lastIndexOf('，'),
      cut.lastIndexOf('、'),
      cut.lastIndexOf('；'),
      cut.lastIndexOf(';'),
      cut.lastIndexOf(','),
    );
    return (lastBreak >= 12 ? cut.slice(0, lastBreak) : cut).trim();
  }

  _normalizeSentence(text) {
    let value = String(text || '')
      .replace(/^\s*(?:\d+[.、]\s*)+/, '')
      .replace(/\s+/g, '')
      .replace(/[。！？!?]+$/g, '')
      .trim();
    if (!value) return '';
    return `${value}。`;
  }

  _isOffTopicModelReply(reply, question) {
    const value = String(reply || '').replace(/\s+/g, '');
    const q = this._normalizeTopicAliases(question);
    if (!value || !q) return false;
    if (/(灵犀|数智员工|数字员工|AI员工|ai员工|智能员工)/.test(q)) {
      return !/(灵犀|数智员工|数字员工|AI员工|ai员工|智能员工|AI办公|智能化员工|办公效率|流程协同)/.test(value);
    }
    if (/(6g|6G).*?(智慧)?农业|(智慧)?农业.*?(6g|6G)/.test(q)) {
      return !/(感知|联网|决策|作业|精准|空天|无人机|农业)/.test(value) || /(前沿.*领域|广阔.*领域)/.test(value);
    }
    return false;
  }

  _buildHelpfulFallbackReply(question) {
    return this._buildGenericFallbackReply(question);
  }

  _buildGenericFallbackReply(question) {
    return UNANSWERABLE_REPLY;
  }

  _topicSpecificFallbackReply(topic) {
    const value = this._normalizeTopicAliases(topic);
    if (!value) return '';

    const rules = [
      {
        test: /西安.*?(景点|旅游|游玩)|(?:景点|旅游|游玩).*?西安/,
        reply: '西安景点包括兵马俑、大雁塔、城墙、华清宫和钟鼓楼。',
      },
      {
        test: /(灵犀|数智员工|数字员工|AI员工|ai员工|智能员工)/,
        reply: '“灵犀”数智员工是AI办公助手，用于问答、内容生成和流程协同。',
      },
      {
        test: /(数字人|虚拟人|智能讲解|虚拟讲解|ai讲解|AI讲解)/,
        reply: '有，思德科技方案包含数字人和智能讲解能力，可结合知识库、大模型与中控屏幕完成互动讲解。',
      },
      {
        test: /(6g|6G).*?(智慧)?农业|(智慧)?农业.*?(6g|6G)/,
        reply: '6G可提升农业感知、联网和智能决策能力，支撑精准作业。',
      },
      {
        test: /(智慧)?农业|农田|种植|养殖|农机|乡村/,
        reply: '智慧农业用物联感知和数据分析，让种养更精准高效。',
      },
      {
        test: /(智慧)?工厂/,
        reply: '智慧工厂连接设备和生产数据，提升效率、质量和管理水平。',
      },
      {
        test: /(智慧)?工业|工厂|制造|产线|车间|工业互联网/,
        reply: '智慧工业连接设备和产线数据，实现自动化与质量管控。',
      },
      {
        test: /(智慧)?文旅|旅游|景区|博物馆|展馆|文博/,
        reply: '智慧文旅用数字导览和互动展示，提升体验与运营效率。',
      },
      {
        test: /(智慧)?医疗|医院|诊疗|健康/,
        reply: '智慧医疗用数据互联和智能辅助，提升诊疗服务效率。',
      },
      {
        test: /(智慧)?教育|校园|课堂|教学/,
        reply: '智慧教育融合资源、互动和学习数据，提升教学效果。',
      },
      {
        test: /(智慧)?交通|车路|道路|公交|高速/,
        reply: '智慧交通用感知和调度优化车流路况，提升通行效率。',
      },
      {
        test: /(低空经济|无人机|低空)/,
        reply: '低空经济围绕无人机应用，可服务巡检、物流和应急。',
      },
    ];

    return rules.find(rule => rule.test.test(value))?.reply || '';
  }

  _normalizeTopicAliases(text) {
    return String(text || '')
      .replace(/\s+/g, '')
      .replace(/[Ｇｇ]/g, 'G')
      .replace(/([零一二两三四五六七八九十]{1,3})[gG]/g, (_, value) => `${this._parseSmallChineseNumber(value)}G`)
      .replace(/(\d+)[gG]/g, (_, value) => `${value}G`);
  }

  _parseSmallChineseNumber(value) {
    const text = String(value || '');
    const digits = { 零: 0, 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };
    if (!text) return '';
    if (digits[text] !== undefined) return digits[text];
    if (text === '十') return 10;
    const tenIndex = text.indexOf('十');
    if (tenIndex >= 0) {
      const left = text.slice(0, tenIndex);
      const right = text.slice(tenIndex + 1);
      const tens = left ? digits[left] || 0 : 1;
      const ones = right ? digits[right] || 0 : 0;
      return tens * 10 + ones;
    }
    return text;
  }

  _buildHelpfulShowQaResult(question, target, context = {}) {
    const reply = this._buildHelpfulFallbackReply(question);
    return {
      reply,
      actions: [{
        type: 'show_qa',
        target,
        params: { question, answer: reply },
      }],
      context: {
        fallback: true,
        technicalFallback: true,
        contextEligible: false,
        ...context,
      },
    };
  }

  async _handleChat(clientInfo, msg, seq) {
    const text = msg.text || '';
    const startedAt = Date.now();
    if (!this._ensureRegistered(clientInfo, seq)) return;
    this._beginConversationTurn(seq, {
      text,
      clientInfo,
      source: msg.source || 'chat',
      conversationId: msg.conversationId || '',
      forceNew: msg.newConversation === true,
    });
    this._startAnswerProgress({
      seq,
      requestId: msg.requestId,
      question: text,
      target: msg.target || 'all',
      source: msg.source || 'chat',
      clientInfo,
    });
    this._emitAnswerProgress(seq, 'ROUTING', {
      message: '正在判断回答方式…',
      clientInfo,
    });
    this._rememberConversationTopic(text, clientInfo, 'chat');
    const webRoute = webSearchService.classifyRoute(text, { target: msg.target || 'all' });
    this._beginRouteTrace(seq, webRoute);
    const preferWebSearch = webRoute.mode !== 'NONE'
      && webSearchService.shouldBypassLocalQa(text, { target: msg.target || 'all' });

    const switchResult = await this._tryHandleModelSwitch({
      text,
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'chat',
      startedAt,
    });
    if (switchResult) return;

    const directControlResult = await this._tryHandleDirectControlCommand({
      text,
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'chat',
      startedAt,
    });
    if (directControlResult) return;

    const specialResult = await this._tryHandleSpecialSwitch({
      text,
      clientInfo,
      seq,
      source: 'chat',
      startedAt,
    });
    if (specialResult) return;

    if (this._isExplicitMediaActionText(text)) {
      const explicitMediaResult = await this._tryHandleMediaCommand({
        text,
        clientInfo,
        seq,
        target: msg.target || 'all',
        source: 'chat',
        startedAt,
      });
      if (explicitMediaResult) return;
    }

    const pendingReviewResult = await this._tryHandlePendingReviewTopic({
      text,
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'chat',
      startedAt,
    });
    if (pendingReviewResult) return;

    const approvedWebQaResult = await this._tryHandleApprovedWebQaAnswer({
      text,
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'chat',
      startedAt,
    });
    if (approvedWebQaResult) return;

    if (webRoute.mode === 'MUST_WEB') {
      const mustWebResult = await this._tryHandleWebSearch({
        text, clientInfo, seq, target: msg.target || 'all', source: 'chat', startedAt, forceIntent: true,
      });
      if (mustWebResult) return;
    }

    const dateTimeResult = await this._tryHandleDateTimeQuery({
      text,
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'chat',
      startedAt,
    });
    if (dateTimeResult) return;

    const weatherResult = await this._tryHandleWeatherQuery({
      text,
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'chat',
      startedAt,
    });
    if (weatherResult) return;

    const commentaryResult = await this._tryHandleCurrentPptCommentary({
      text,
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'chat',
      startedAt,
    });
    if (commentaryResult) return;

    const mediaResult = await this._tryHandleMediaCommand({
      text,
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'chat',
      startedAt,
    });
    if (mediaResult) return;

    if (preferWebSearch) {
      const priorityWebResult = await this._tryHandleWebSearch({
        text, clientInfo, seq, target: msg.target || 'all', source: 'chat', startedAt,
      });
      if (priorityWebResult) return;
    }

    const contextualFollowup = this._isContextDependentFollowup(seq);
    const qaResult = await this._tryHandleQaAnswer({
      text, clientInfo, seq, target: msg.target || 'all', source: 'chat', startedAt,
    });
    if (qaResult) return;

    const configuredFallbackResult = !contextualFollowup && await this._tryHandleConfiguredFallback({
      text, clientInfo, seq, target: msg.target || 'all', source: 'chat', startedAt,
    });
    if (configuredFallbackResult) return;

    const webSearchResult = !preferWebSearch && await this._tryHandleWebSearch({
      text,
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'chat',
      startedAt,
    });
    if (webSearchResult) return;

    const ragResult = await this._tryHandleMarkdownRag({
      text,
      retrievalText: this._retrievalTextFor(seq, text),
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'chat',
      startedAt,
    });
    if (ragResult) return;

    this._dispatchPendingQuestion({ text, target: msg.target || 'all', clientInfo, seq });
    const result = await this._chatDirectLlm({
      text,
      seq,
      deviceId: clientInfo.deviceId,
      deviceType: clientInfo.type,
      clientId: clientInfo.clientId,
    });

    if (result.error) {
      this._recordFailure({ seq, source: 'chat', deviceId: clientInfo.deviceId, deviceType: clientInfo.type, target: msg.target || 'all', question: text, error: result.error, startedAt });
      const fallbackResult = this._buildHelpfulShowQaResult(text, msg.target || 'all', { modelError: result.error });
      await this._processModelResult(clientInfo, fallbackResult, seq, {
        fallbackQuestion: text,
        fallbackTarget: msg.target || 'all',
        source: 'chat',
        durationMs: Date.now() - startedAt,
      });
      return;
    }

    const llmWebFallback = await this._tryHandleLlmFreshnessFallback({
      text,
      rawReply: result.reply,
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'chat',
      startedAt,
    });
    if (llmWebFallback) return;

    const normalized = this._limitAiReplyResult(this._ensureHelpfulModelResult(result, text));
    normalized.context = { ...(normalized.context || {}), answerSource: normalized.context?.answerSource || 'LLM' };
    normalized.context.attachRelatedMedia = true;
    const cachedQa = this._saveAutoAnswerToQa({
      question: text,
      answer: normalized.reply,
      sourceLabel: 'llm-cache',
      actions: normalized.actions,
    });
    if (cachedQa) normalized.context.cachedQaId = cachedQa.id;

    await this._processModelResult(clientInfo, normalized, seq, {
      fallbackQuestion: text,
      fallbackTarget: msg.target || 'all',
      source: 'chat',
      durationMs: Date.now() - startedAt,
    });
  }

  async _handleVoice(clientInfo, msg, seq) {
    const transcription = msg.text || '（语音消息，ASR 未提供文本）';
    const startedAt = Date.now();
    if (!this._ensureRegistered(clientInfo, seq)) return;
    this._beginConversationTurn(seq, {
      text: transcription,
      clientInfo,
      source: msg.source || 'voice',
      conversationId: msg.conversationId || '',
      forceNew: msg.newConversation === true,
    });
    this._startAnswerProgress({
      seq,
      requestId: msg.requestId,
      question: transcription,
      target: msg.target || 'all',
      source: msg.source || 'voice',
      clientInfo,
    });
    this._emitAnswerProgress(seq, 'ROUTING', {
      message: '正在判断回答方式…',
      clientInfo,
    });
    this._rememberConversationTopic(transcription, clientInfo, 'voice');
    const webRoute = webSearchService.classifyRoute(transcription, { target: msg.target || 'all' });
    this._beginRouteTrace(seq, webRoute);
    const preferWebSearch = webRoute.mode !== 'NONE'
      && webSearchService.shouldBypassLocalQa(transcription, { target: msg.target || 'all' });

    wsServer.sendTo(clientInfo.clientId, { type: 'voice_text', text: transcription, seq });

    const switchResult = await this._tryHandleModelSwitch({
      text: transcription,
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'voice',
      startedAt,
    });
    if (switchResult) return;

    const directControlResult = await this._tryHandleDirectControlCommand({
      text: transcription,
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'voice',
      startedAt,
    });
    if (directControlResult) return;

    const specialResult = await this._tryHandleSpecialSwitch({
      text: transcription,
      clientInfo,
      seq,
      source: 'voice',
      startedAt,
    });
    if (specialResult) return;

    if (this._isExplicitMediaActionText(transcription)) {
      const explicitMediaResult = await this._tryHandleMediaCommand({
        text: transcription,
        clientInfo,
        seq,
        target: msg.target || 'all',
        source: 'voice',
        startedAt,
      });
      if (explicitMediaResult) return;
    }

    const pendingReviewResult = await this._tryHandlePendingReviewTopic({
      text: transcription,
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'voice',
      startedAt,
    });
    if (pendingReviewResult) return;

    const approvedWebQaResult = await this._tryHandleApprovedWebQaAnswer({
      text: transcription,
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'voice',
      startedAt,
    });
    if (approvedWebQaResult) return;

    if (webRoute.mode === 'MUST_WEB') {
      const mustWebResult = await this._tryHandleWebSearch({
        text: transcription,
        clientInfo,
        seq,
        target: msg.target || 'all',
        source: 'voice',
        startedAt,
        forceIntent: true,
      });
      if (mustWebResult) return;
    }

    const dateTimeResult = await this._tryHandleDateTimeQuery({
      text: transcription,
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'voice',
      startedAt,
    });
    if (dateTimeResult) return;

    const weatherResult = await this._tryHandleWeatherQuery({
      text: transcription,
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'voice',
      startedAt,
    });
    if (weatherResult) return;

    const commentaryResult = await this._tryHandleCurrentPptCommentary({
      text: transcription,
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'voice',
      startedAt,
    });
    if (commentaryResult) return;

    const mediaResult = await this._tryHandleMediaCommand({
      text: transcription,
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'voice',
      startedAt,
    });
    if (mediaResult) return;

    if (preferWebSearch) {
      const priorityWebResult = await this._tryHandleWebSearch({
        text: transcription, clientInfo, seq, target: msg.target || 'all', source: 'voice', startedAt,
      });
      if (priorityWebResult) return;
    }

    const contextualFollowup = this._isContextDependentFollowup(seq);
    const qaResult = await this._tryHandleQaAnswer({
      text: transcription, clientInfo, seq, target: msg.target || 'all', source: 'voice', startedAt,
    });
    if (qaResult) return;

    const configuredFallbackResult = !contextualFollowup && await this._tryHandleConfiguredFallback({
      text: transcription, clientInfo, seq, target: msg.target || 'all', source: 'voice', startedAt,
    });
    if (configuredFallbackResult) return;

    const webSearchResult = !preferWebSearch && await this._tryHandleWebSearch({
      text: transcription,
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'voice',
      startedAt,
    });
    if (webSearchResult) return;

    const ragResult = await this._tryHandleMarkdownRag({
      text: transcription,
      retrievalText: this._retrievalTextFor(seq, transcription),
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'voice',
      startedAt,
    });
    if (ragResult) return;

    this._dispatchPendingQuestion({ text: transcription, target: msg.target || 'all', clientInfo, seq });
    const result = await this._chatDirectLlm({
      text: transcription,
      seq,
      deviceId: clientInfo.deviceId,
      deviceType: clientInfo.type,
      clientId: clientInfo.clientId,
      isVoice: true,
    });

    if (result.error) {
      this._recordFailure({ seq, source: 'voice', deviceId: clientInfo.deviceId, deviceType: clientInfo.type, target: msg.target || 'all', question: transcription, error: result.error, startedAt });
      const fallbackResult = this._buildHelpfulShowQaResult(transcription, msg.target || 'all', { modelError: result.error });
      await this._processModelResult(clientInfo, fallbackResult, seq, {
        fallbackQuestion: transcription,
        fallbackTarget: msg.target || 'all',
        source: 'voice',
        durationMs: Date.now() - startedAt,
      });
      return;
    }

    const llmWebFallback = await this._tryHandleLlmFreshnessFallback({
      text: transcription,
      rawReply: result.reply,
      clientInfo,
      seq,
      target: msg.target || 'all',
      source: 'voice',
      startedAt,
    });
    if (llmWebFallback) return;

    const normalized = this._limitAiReplyResult(this._ensureHelpfulModelResult(result, transcription));
    normalized.context = { ...(normalized.context || {}), answerSource: normalized.context?.answerSource || 'LLM' };
    normalized.context.attachRelatedMedia = true;
    const cachedQa = this._saveAutoAnswerToQa({
      question: transcription,
      answer: normalized.reply,
      sourceLabel: 'llm-cache',
      actions: normalized.actions,
    });
    if (cachedQa) normalized.context.cachedQaId = cachedQa.id;

    await this._processModelResult(clientInfo, normalized, seq, {
      fallbackQuestion: transcription,
      fallbackTarget: msg.target || 'all',
      source: 'voice',
      durationMs: Date.now() - startedAt,
    });
  }

  async _handleCommand(clientInfo, msg, seq) {
    if (![config.DEVICE_TYPES.MOBILE, config.DEVICE_TYPES.ADMIN].includes(clientInfo.type)) {
      wsServer.sendTo(clientInfo.clientId, { type: 'error', code: 'PERMISSION_DENIED', message: '无权发送控制指令', seq });
      return;
    }

    this._dispatchAction({
      type: msg.action,
      target: msg.target || 'all',
      params: msg.params || {},
    }, clientInfo, seq);

    wsServer.sendTo(clientInfo.clientId, {
      type: 'command_ack',
      action: msg.action,
      target: msg.target,
      seq,
      serverTime: Date.now(),
    });
  }

  _handleStatus(clientInfo, seq) {
    wsServer.sendTo(clientInfo.clientId, {
      type: 'status_resp',
      devices: deviceManager.getSummary(),
      serverTime: Date.now(),
      seq,
    });
  }

  _handleSync(clientInfo, seq) {
    wsServer.sendTo(clientInfo.clientId, {
      type: 'sync_resp',
      serverTime: Date.now(),
      seq,
    });
  }

  async _tryHandleModelSwitch({ text, clientInfo, seq, target, source, startedAt, echoToRequester = true }) {
    const switched = modelProvider.trySwitchByText(text);
    if (!switched) return null;

    const reply = `已切换到${switched.label}，后续语音会使用这个通道。`;
    const result = {
      reply,
      actions: [{
        type: 'show_qa',
        target,
        params: { question: text, answer: reply },
      }],
      context: { modelProvider: switched.provider },
    };

    const processed = await this._processModelResult(clientInfo, result, seq, {
      echoToRequester,
      fallbackQuestion: text,
      fallbackTarget: target,
      source,
      durationMs: Date.now() - startedAt,
    });

    return { seq, reply, actions: result.actions, target, provider: switched.provider };
  }

  async _tryHandleWakeGreeting({ text, clientInfo, seq, target, source, startedAt }) {
    const normalized = String(text || '')
      .replace(/[\s，。！？!?、,.;；:：]/g, '')
      .trim();
    if (normalized !== '你好思德') return null;

    const reply = '我在。';
    const result = {
      reply,
      actions: [{
        type: 'speak',
        target,
        params: { text: reply, preserveView: 'true', wakeGreeting: 'true' },
      }],
      context: {
        answerSource: '唤醒应答',
        wakeGreeting: true,
      },
    };

    await this._processModelResult(clientInfo, result, seq, {
      echoToRequester: false,
      fallbackQuestion: text,
      fallbackTarget: target,
      source,
      durationMs: Date.now() - startedAt,
      answerSource: '唤醒应答',
    });

    return { seq, reply, actions: result.actions, target, wakeGreeting: true };
  }

  _dispatchPendingQuestion({ text, target, clientInfo, seq }) {
    this._emitAnswerProgress(seq, 'SYNTHESIZING', {
      message: '正在组织回答…',
      clientInfo,
    });
    this._dispatchAction({
      type: 'pending_qa',
      target,
      params: {
        question: text,
        answer: '正在组织回答…',
        status: 'SYNTHESIZING',
        statusText: '正在组织回答…',
        streaming: true,
      },
    }, clientInfo, seq);
  }

  async _tryHandleDateTimeQuery({ text, clientInfo, seq, target, source, startedAt, echoToRequester = true }) {
    const classification = classifyCurrentDateTimeQuery(text);
    if (!classification.matched) {
      this._traceRoute(seq, {
        handler: 'current_datetime',
        matched: false,
        status: 'SKIPPED',
        reason: classification.reason,
      });
      return null;
    }
    const { asksWeekday, asksSolarDate, asksLunarDate, asksTime } = classification;
    this._traceRoute(seq, {
      handler: 'current_datetime',
      matched: true,
      status: 'HANDLED',
      reason: classification.reason,
    });

    const parts = new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      weekday: 'long',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).formatToParts(new Date()).reduce((acc, item) => {
      acc[item.type] = item.value;
      return acc;
    }, {});

    const dateText = `${parts.year}年${parts.month}月${parts.day}日`;
    const weekdayText = parts.weekday || '';
    const timeText = `${parts.hour}:${parts.minute}`;
    const lunarText = this._formatLunarDate(new Date());
    let reply = '';
    if (asksTime) {
      reply = `现在是北京时间${timeText}，${dateText}，${weekdayText}${asksLunarDate && lunarText ? `，${lunarText}` : ''}。`;
    } else if (asksLunarDate && asksSolarDate && asksWeekday) {
      reply = `今天是${dateText}，${weekdayText}${lunarText ? `，${lunarText}` : ''}。`;
    } else if (asksLunarDate && asksSolarDate) {
      reply = `今天是${dateText}${lunarText ? `，${lunarText}` : ''}。`;
    } else if (asksLunarDate && asksWeekday) {
      reply = `今天是${weekdayText}${lunarText ? `，${lunarText}` : ''}。`;
    } else if (asksLunarDate) {
      reply = lunarText ? `今天是${lunarText}。` : `今天是${dateText}，农历日期暂时无法获取。`;
    } else if (asksSolarDate && asksWeekday) {
      reply = `今天是${dateText}，${weekdayText}。`;
    } else if (asksSolarDate) {
      reply = `今天是${dateText}。`;
    } else {
      reply = `今天是${weekdayText}。`;
    }

    const result = {
      reply,
      actions: [{
        type: 'show_qa',
        target,
        params: {
          question: text,
          answer: reply,
        },
      }],
      context: { answerSource: '日期时间' },
    };

    await this._processModelResult(clientInfo, result, seq, {
      echoToRequester,
      fallbackQuestion: text,
      fallbackTarget: target,
      source,
      durationMs: Date.now() - startedAt,
      answerSource: result.context.answerSource,
    });

    return {
      seq,
      reply,
      actions: result.actions,
      target,
      answerSource: result.context.answerSource,
    };
  }

  _formatLunarDate(date = new Date()) {
    try {
      const parts = new Intl.DateTimeFormat('zh-CN-u-ca-chinese', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric',
        month: 'long',
        day: 'numeric',
      }).formatToParts(date).reduce((acc, item) => {
        acc[item.type] = item.value;
        return acc;
      }, {});
      const yearName = parts.yearName || '';
      const month = parts.month || '';
      const day = this._formatLunarDay(parts.day);
      if (!month || !day) return '';
      return `农历${yearName ? `${yearName}年` : ''}${month}${day}`;
    } catch {
      return '';
    }
  }

  _formatLunarDay(value) {
    const text = String(value || '').trim();
    if (!/^\d+$/.test(text)) return text;
    const day = Number.parseInt(text, 10);
    const names = [
      '', '初一', '初二', '初三', '初四', '初五', '初六', '初七', '初八', '初九', '初十',
      '十一', '十二', '十三', '十四', '十五', '十六', '十七', '十八', '十九', '二十',
      '廿一', '廿二', '廿三', '廿四', '廿五', '廿六', '廿七', '廿八', '廿九', '三十',
    ];
    return names[day] || text;
  }

  async _tryHandleWeatherQuery({ text, clientInfo, seq, target, source, startedAt, echoToRequester = true }) {
    const intent = weatherService.detect(text);
    if (!intent) return null;

    const weather = await weatherService.answer(intent);
    const reply = weather.reply || '天气信息暂时获取失败。';
    const result = {
      reply,
      actions: [{
        type: 'show_qa',
        target,
        params: {
          question: text,
          answer: reply,
        },
      }],
      context: {
        answerSource: weather.ok ? (weather.fromCache ? '天气缓存' : '天气服务') : '天气兜底',
        weatherCity: weather.city || intent.city,
        weatherCachedAt: weather.cachedAt,
        weatherFromCache: weather.fromCache === true,
      },
    };

    const processed = await this._processModelResult(clientInfo, result, seq, {
      echoToRequester,
      fallbackQuestion: text,
      fallbackTarget: target,
      source,
      durationMs: Date.now() - startedAt,
      answerSource: result.context.answerSource,
    });

    return {
      seq,
      reply,
      actions: result.actions,
      target,
      weather: {
        city: weather.city || intent.city,
        cachedAt: weather.cachedAt,
        fromCache: weather.fromCache === true,
      },
    };
  }

  async _tryHandleSpecialSwitch({ text, clientInfo, seq, source, startedAt, echoToRequester = true }) {
    const name = this._parseSpecialName(text);
    if (!name) return null;

    const result = this.applySpecial(name, { clientInfo, seq, source });
    const reply = result.applied.length
      ? `已切换到${result.special.name}，正在按专场配置展示。`
      : `已切换到${result.special.name}，但当前没有可用屏幕配置。`;

    if (echoToRequester && clientInfo.clientId) {
      wsServer.sendTo(clientInfo.clientId, {
        type: 'reply',
        text: reply,
        seq,
        serverTime: Date.now(),
      });
    }

    const conversationId = this._conversationTurnFor(seq)?.conversationId || '';
    usageStore.recordInteraction({
      seq,
      source,
      deviceId: clientInfo.deviceId,
      deviceType: clientInfo.type,
      target: 'special',
      question: text,
      answer: reply,
      actions: result.applied.map(item => item.action),
      conversationId,
      ok: true,
      durationMs: Date.now() - startedAt,
    });

    wsServer.pushToAdmin({
      type: 'conversation_log',
      deviceId: clientInfo.deviceId,
      input: text,
      reply,
      actions: result.applied.map(item => item.action),
      seq,
      answerSource: '控制',
      conversationId,
    });
    if (conversationId) {
      contextResolver.complete(conversationId, {
        question: text,
        answer: reply,
        answerSource: '控制',
        finalRoute: 'OTHER_ANSWERED',
      });
    }
    this._emitAnswerProgress(seq, 'COMPLETED', {
      message: '操作已完成',
      answer: reply,
      clientInfo,
    });
    this._conversationTurns.delete(seq);

    return { seq, reply, special: result.special, applied: result.applied };
  }

  applySpecial(idOrName, options = {}) {
    const special = specialConfigStore.get(idOrName) || specialConfigStore.findByName(idOrName);
    if (!special) throw new Error(`未找到专场: ${idOrName}`);

    specialConfigStore.setActive(special.id);
    const clientInfo = options.clientInfo || {
      deviceId: options.source || 'admin-api',
      type: config.DEVICE_TYPES.ADMIN,
    };
    const seq = options.seq || this._nextSeq();
    const applied = [];
    const skipped = [];

    if (special.mode === 'blackout') {
      const action = { type: 'blackout', target: 'all', params: {} };
      this._dispatchAction(action, clientInfo, seq);
      return { special, applied: [{ screenNumber: 'all', action }], skipped };
    }

    for (const assignment of special.assignments || []) {
      if (assignment.action) {
        const action = { type: assignment.action, target: assignment.screenNumber, params: this._commentaryParamsForTarget(assignment.screenNumber, special) };
        this._dispatchAction(action, clientInfo, seq);
        applied.push({ screenNumber: assignment.screenNumber, action });
        continue;
      }

      const item = this._findFirstMediaItem(assignment);
      if (!item) {
        skipped.push({ assignment, reason: 'media_not_found' });
        continue;
      }

      const action = this._mediaActionForItem(item, assignment.screenNumber, special);
      this._dispatchAction(action, clientInfo, seq);
      applied.push({ screenNumber: assignment.screenNumber, file: item.name, files: this._assignmentFileNames(assignment), action });
    }

    return { special, applied, skipped };
  }

  applyActiveSpecialForScreen(screenNumber, options = {}) {
    const activeId = specialConfigStore.getActiveId();
    if (!activeId) return { applied: [], skipped: [], reason: 'no_active_special' };

    const special = specialConfigStore.get(activeId);
    if (!special) return { applied: [], skipped: [], reason: 'active_special_not_found' };

    const targetScreen = Number.parseInt(screenNumber, 10);
    if (!Number.isFinite(targetScreen) || targetScreen <= 0) {
      return { special, applied: [], skipped: [], reason: 'invalid_screen_number' };
    }

    const clientInfo = options.clientInfo || {
      deviceId: options.source || 'auto-restore-special',
      type: config.DEVICE_TYPES.ADMIN,
    };
    const seq = options.seq || this._nextSeq();
    const applied = [];
    const skipped = [];

    if (special.mode === 'blackout') {
      const action = { type: 'blackout', target: targetScreen, params: {} };
      this._dispatchAction(action, clientInfo, seq);
      return { special, applied: [{ screenNumber: targetScreen, action }], skipped };
    }

    for (const assignment of special.assignments || []) {
      if (Number.parseInt(assignment.screenNumber, 10) !== targetScreen) continue;

      if (assignment.action) {
        const action = { type: assignment.action, target: targetScreen, params: this._commentaryParamsForTarget(targetScreen, special) };
        this._dispatchAction(action, clientInfo, seq);
        applied.push({ screenNumber: targetScreen, action });
        continue;
      }

      const item = this._findFirstMediaItem(assignment);
      if (!item) {
        skipped.push({ assignment, reason: 'media_not_found' });
        continue;
      }

      const action = this._mediaActionForItem(item, targetScreen, special);
      this._dispatchAction(action, clientInfo, seq);
      applied.push({ screenNumber: targetScreen, file: item.name, files: this._assignmentFileNames(assignment), action });
    }

    return { special, applied, skipped };
  }

  _showSpecialFilesForTarget({ target, clientInfo, seq, source, logToAdmin = true }) {
    const screenNumber = Number.parseInt(target, 10);
    if (!Number.isFinite(screenNumber) || screenNumber <= 0) {
      return { seq, error: '请先在手机端选择一个具体屏幕' };
    }

    const special = specialConfigStore.get(specialConfigStore.getActiveId());
    if (!special || special.mode === 'blackout') {
      return { seq, error: '当前没有可用的专场文件列表' };
    }

    const assignment = (special.assignments || [])
      .find(item => Number.parseInt(item.screenNumber, 10) === screenNumber);
    const items = this._mediaItemsForAssignment(assignment);
    if (!items.length) {
      return { seq, error: `${screenNumber}号屏没有配置专场文件` };
    }

      this._dispatchAction({
        type: 'show_special_file_strip',
        target: screenNumber,
        params: {
        specialId: special.id,
        specialName: special.name,
        commentaryLevel: this._normalizeCommentaryLevel(special.commentaryLevel),
        selectedIndex: 0,
        items: JSON.stringify(items),
      },
    }, clientInfo, seq);

    if (logToAdmin) {
      wsServer.pushToAdmin({
        type: 'conversation_log',
        deviceId: source,
        input: '打开专场文件列表',
        reply: `${screenNumber}号屏显示 ${items.length} 个专场文件缩略图`,
        actions: [{ type: 'show_special_file_strip', target: screenNumber }],
        seq,
        answerSource: '控制',
      });
    }

    return { seq, target: screenNumber, special: special.name, total: items.length };
  }

  _setScreenMode(target, mode) {
    for (const screenNumber of this._targetScreenNumbers(target)) {
      if (mode) this._screenModes.set(screenNumber, mode);
      else this._screenModes.delete(screenNumber);
    }
  }

  _getScreenMode(target) {
    const screenNumber = Number.parseInt(target, 10);
    if (!Number.isFinite(screenNumber) || screenNumber <= 0) return 'normal';
    const explicit = this._screenModes.get(screenNumber);
    if (explicit === 'file_picker') return explicit;
    const view = deviceManager.getByScreenNumber(screenNumber)?.status?.view || '';
    if (String(view).toLowerCase() === 'ppt') return 'ppt';
    if (explicit) return explicit;
    return String(view).toLowerCase() === 'ppt' ? 'ppt' : 'normal';
  }

  _targetScreenNumbers(target) {
    if (target === 'all') {
      return deviceManager.getOnlineScreens()
        .map(screen => Number.parseInt(screen.screenNumber, 10))
        .filter(num => Number.isFinite(num) && num > 0);
    }
    const screenNumber = Number.parseInt(target, 10);
    return Number.isFinite(screenNumber) && screenNumber > 0 ? [screenNumber] : [];
  }

  _setFilePickerState(target, items, selectedIndex = 0) {
    const normalizedItems = Array.isArray(items) ? items : [];
    for (const screenNumber of this._targetScreenNumbers(target)) {
      this._filePickerStates.set(screenNumber, {
        items: normalizedItems,
        selectedIndex: Math.max(0, Math.min(Number(selectedIndex) || 0, Math.max(0, normalizedItems.length - 1))),
      });
      this._screenModes.set(screenNumber, 'file_picker');
    }
  }

  _moveFilePickerSelection(target, delta) {
    for (const screenNumber of this._targetScreenNumbers(target)) {
      const state = this._filePickerStates.get(screenNumber);
      if (!state?.items?.length) continue;
      state.selectedIndex = (state.selectedIndex + delta + state.items.length) % state.items.length;
    }
  }

  _selectedFilePickerItem(target) {
    const screenNumber = Number.parseInt(target, 10);
    if (!Number.isFinite(screenNumber) || screenNumber <= 0) return null;
    const state = this._filePickerStates.get(screenNumber);
    if (!state?.items?.length) return null;
    return state.items[state.selectedIndex] || null;
  }

  _clearFilePickerState(target) {
    for (const screenNumber of this._targetScreenNumbers(target)) {
      this._filePickerStates.delete(screenNumber);
      if (this._screenModes.get(screenNumber) === 'file_picker') this._screenModes.delete(screenNumber);
    }
  }

  _parseSpecialName(text) {
    const value = String(text || '').trim();
    if (!value) return null;

    const normalized = this._normalizeSpecialText(value);

    const actionWords = '(?:切换到|切换|切到|进入|打开|启动|启用|换到|换成)';
    const suffixWords = '(?:专场|专题|场景)';
    const match = normalized.match(new RegExp(`${actionWords}(.+?${suffixWords})`));
    if (match?.[1]) {
      const candidate = match[1].trim();
      return this._resolveSpecialName(candidate, normalized) || candidate;
    }

    const specials = specialConfigStore.list();
    const exact = specials.find(special =>
      normalized.includes(`切换${special.name}`) ||
      normalized.includes(`切到${special.name}`) ||
      normalized.includes(`进入${special.name}`) ||
      normalized.includes(`打开${special.name}`) ||
      normalized.includes(`启动${special.name}`) ||
      normalized.includes(`启用${special.name}`) ||
      normalized.includes(`换到${special.name}`)
    )?.name;

    return exact || this._resolveBareSpecialName(normalized);
  }

  _normalizeSpecialText(text) {
    return String(text || '')
      .trim()
      .replace(/\s+/g, '')
      .replace(/[，。！？,.!?]/g, '')
      .replace(/专题|场景/g, '专场')
      .replace(/死的|死德|思的|斯德|斯的|四德|四的/g, '思德');
  }

  _resolveBareSpecialName(text) {
    const value = this._normalizeSpecialText(text)
      .replace(/^(请|帮我|给我|我要|我想要|我想看|我要看|看一下|看下)/, '')
      .replace(/(吧|一下|下|谢谢)$/g, '');
    if (!value || /(文件|资料|PPT|ppt|视频|图片|图像|照片|相关|哪些|什么|有没有|吗|呢|问答|介绍)/.test(value)) {
      return null;
    }

    const resolved = this._resolveSpecialName(value, '');
    if (!resolved) return null;

    const specialName = this._normalizeSpecialText(resolved);
    const shortName = specialName.replace(/专场$/, '');
    const allowedValues = new Set([
      specialName,
      shortName,
      `${shortName}专场`,
      `${shortName}专题`,
      `${shortName}场景`,
    ]);
    return allowedValues.has(value) ? resolved : null;
  }

  _resolveSpecialName(candidate, fullText = '') {
    const specials = specialConfigStore.list();
    if (!specials.length) return null;

    const candidateText = this._normalizeSpecialText(candidate);
    const full = this._normalizeSpecialText(fullText);
    const combined = `${candidateText}${full}`;

    const exact = specials.find(special => {
      const name = this._normalizeSpecialText(special.name);
      return candidateText === name || candidateText === name.replace(/专场$/, '');
    });
    if (exact) return exact.name;

    const contains = specials.find(special => {
      const name = this._normalizeSpecialText(special.name);
      const shortName = name.replace(/专场$/, '');
      return (candidateText && (candidateText.includes(shortName) || name.includes(candidateText))) ||
        (full && (full.includes(name) || full.includes(shortName)));
    });
    if (contains) return contains.name;

    const aliases = [
      { keywords: ['思德', 'sidex'], test: special => this._normalizeSpecialText(special.name).includes('思德') },
      { keywords: ['参观接待', '参观', '接待'], test: special => this._normalizeSpecialText(special.name).includes('参观接待') },
      { keywords: ['中国移动', '移动'], test: special => this._normalizeSpecialText(special.name).includes('中国移动') },
      { keywords: ['节能', '黑屏', '黑色'], test: special => this._normalizeSpecialText(special.name).includes('节能') || special.mode === 'blackout' },
    ];

    const alias = aliases.find(item => item.keywords.some(keyword => combined.includes(keyword)));
    return alias ? specials.find(alias.test)?.name || null : null;
  }

  _findMediaItem(assignment) {
    const items = mediaLibrary.getAll().filter(isClientMediaAvailable);
    return items.find(item =>
      (assignment.fileId && String(item.id) === String(assignment.fileId)) ||
      (assignment.fileName && (item.name === assignment.fileName || item.path === assignment.fileName || item.title === assignment.fileName))
    ) || null;
  }

  _findFirstMediaItem(assignment) {
    const fileNames = this._assignmentFileNames(assignment);
    if (!fileNames.length) return this._findMediaItem(assignment);

    for (const fileName of fileNames) {
      const item = this._findMediaItem({ ...assignment, fileName });
      if (item) return item;
    }
    return null;
  }

  _mediaItemsForAssignment(assignment) {
    if (!assignment) return [];
    return this._assignmentFileNames(assignment)
      .map((fileName, index) => this._findMediaItem({ ...assignment, fileName }))
      .filter(Boolean)
      .map((item, index) => ({
        index: index + 1,
        id: item.id,
        title: item.title,
        name: item.name,
        type: item.type,
        ext: item.ext,
        url: item.url,
        thumbnailUrl: item.thumbnailUrl,
        localPath: item.localPath,
        commentaryLevel: this._activeCommentaryLevelForTarget(assignment.screenNumber),
        hasCommentary: Boolean(item.hasCommentary || String(item.commentary || '').trim()),
      }));
  }

  _assignmentFileNames(assignment) {
    const values = [];
    if (Array.isArray(assignment.fileNames)) values.push(...assignment.fileNames);
    if (assignment.fileName) values.push(assignment.fileName);
    return [...new Set(values.map(value => String(value || '').trim()).filter(Boolean))];
  }

  _mediaActionForItem(item, screenNumber, special = null) {
    return {
      type: item.type === 'ppt' ? 'play_ppt' : item.type === 'image' ? 'show_doc' : 'play_video',
      target: screenNumber,
      params: {
        file: item.localPath || item.name,
        ...this._commentaryParamsForTarget(screenNumber, special),
      },
    };
  }

  _commentaryParamsForTarget(target, special = null) {
    const level = special
      ? this._normalizeCommentaryLevel(special.commentaryLevel)
      : this._activeCommentaryLevelForTarget(target);
    return { commentaryLevel: level };
  }

  _activeCommentaryLevelForTarget(target) {
    const screenNumber = Number.parseInt(target, 10);
    const active = specialConfigStore.get(specialConfigStore.getActiveId());
    if (!active || active.mode === 'blackout') return 'standard';
    if (Number.isFinite(screenNumber) && screenNumber > 0) {
      const hasAssignment = (active.assignments || [])
        .some(item => Number.parseInt(item.screenNumber, 10) === screenNumber);
      if (!hasAssignment) return 'standard';
    }
    return this._normalizeCommentaryLevel(active.commentaryLevel);
  }

  _normalizeCommentaryLevel(value) {
    const level = String(value || '').trim().toLowerCase();
    return ['concise', 'standard', 'detailed'].includes(level) ? level : 'standard';
  }

  async _tryHandleApprovedWebQaAnswer(options) {
    const match = findApprovedWebQaMatch(options.text);
    if (!match) return null;
    this._traceRoute(options.seq, {
      handler: 'approved_web_qa',
      matched: true,
      status: 'MATCHED',
      reason: 'MANUAL_PROMOTION_EXACT',
    });
    return this._tryHandleQaAnswer({ ...options, matchOverride: match });
  }

  async _tryHandleQaAnswer({
    text,
    clientInfo,
    seq,
    target,
    source,
    startedAt,
    echoToRequester = true,
    matchOverride = null,
  }) {
    const scope = this._currentKnowledgeScopeForTarget(target);
    const constrainToCurrentUnit = !matchOverride && Boolean(scope.unitId && (
      isCurrentContextQuestion(text)
      || knowledgeDb.hasQuestionAcrossKnowledgeUnits(text)
    ));
    const scopedLocalCandidates = constrainToCurrentUnit
      ? knowledgeDb.findAnswerCandidates(text, { limit: 20, minScore: 35, unitId: scope.unitId })
      : [];
    const scopedVectorCandidates = constrainToCurrentUnit
      ? await vectorKnowledgeService.searchFaq(text, { limit: 12, unitId: scope.unitId })
      : [];
    const scopedCandidates = this._filterQaCandidatesForQuestion(text, this._mergeFaqCandidates(
      scopedLocalCandidates,
      scopedVectorCandidates,
    ));
    const strongScopedMatch = scopedCandidates.find(item => item.score >= 75)
      || (scopedCandidates.length ? await this._rerankFaqWithModel(text, scopedCandidates) : null);

    const rawManualMatch = matchOverride || (constrainToCurrentUnit ? null : qaStore.findAnswer(text));
    const manualMatch = this._isAllowedQaCandidateForQuestion(text, rawManualMatch) ? rawManualMatch : null;
    const localCandidates = constrainToCurrentUnit || matchOverride
      ? []
      : knowledgeDb.findAnswerCandidates(text, { limit: 15, minScore: 35 });
    const strongLocalMatch = this._filterQaCandidatesForQuestion(text, localCandidates)
      .find(item => item.score >= 75) || null;
    const vectorCandidates = constrainToCurrentUnit || matchOverride
      ? []
      : await vectorKnowledgeService.searchFaq(text, { limit: 8 });
    const markdownCandidates = this._filterQaCandidatesForQuestion(text, this._mergeFaqCandidates(
      vectorCandidates,
      localCandidates,
    ));
    const strongMarkdownMatch = markdownCandidates.find(item => item.score >= 75) || null;
    const match = matchOverride
      || strongScopedMatch
      || manualMatch
      || strongLocalMatch
      || strongMarkdownMatch
      || await this._rerankFaqWithModel(text, markdownCandidates);
    if (!match) {
      return null;
    }

    const replySource = this._extractFaqPackageAnswer(match.item.answer, text) || match.item.answer;
    const reply = String(replySource || '').trim();
    const knowledgeSources = this._knowledgeSourcesFromItems([match.item]);
    const result = {
      reply,
      actions: [{
        type: 'show_qa',
        target,
        params: { question: text, answer: reply, knowledgeSources },
      }],
      context: {
        qaId: match.item.id,
        qaScore: match.score,
        qaSource: match.item.source || 'manual',
        qaReranked: match.reranked === true,
        answerSource: this._answerSourceForQaMatch(match, manualMatch),
        attachRelatedMedia: true,
        currentKnowledgeUnitId: scope.unitId,
        currentPptFileName: scope.fileName,
        currentPptTitle: scope.title,
        currentPptSlideNumber: scope.slideNumber,
        qaScopedToCurrentUnit: Boolean(strongScopedMatch && match === strongScopedMatch),
        qaCurrentUnitRequired: constrainToCurrentUnit,
        knowledgeSources,
      },
    };

    const processed = await this._processModelResult(clientInfo, result, seq, {
      echoToRequester,
      fallbackQuestion: text,
      fallbackTarget: target,
      source,
      durationMs: Date.now() - startedAt,
    });

    return {
      seq,
      reply,
      actions: processed?.actions || result.actions,
      target,
      qa: { id: match.item.id, score: match.score, reranked: match.reranked === true },
    };
  }

  async _tryHandleConfiguredFallback({ text, clientInfo, seq, target, source, startedAt, echoToRequester = true }) {
    const pptState = this._currentPptStateForTarget(target);
    const pptItem = this._currentPptMediaItemForTarget(target);
    const activeSpecial = specialConfigStore.get(specialConfigStore.getActiveId());
    const match = fallbackStore.findMatch(text, {
      target,
      currentPptFileName: pptState?.fileName || pptItem?.name || '',
      currentPptTitle: pptItem?.title || '',
      activeSpecialName: activeSpecial?.name || '',
    });
    if (!match) return null;

    const reply = String(match.item.answer || '').trim();
    if (!reply) return null;
    const result = {
      reply,
      actions: [{
        type: 'show_qa',
        target,
        params: { question: text, answer: reply },
      }],
      context: {
        answerSource: '兜底库',
        fallbackRuleId: match.item.id,
        fallbackRuleName: match.item.name,
        fallbackCategory: match.item.category,
        fallbackAction: match.item.actionType,
        fallbackScore: match.score,
        fallbackMatchedKeywords: match.matchedKeywords,
      },
    };

    const processed = await this._processModelResult(clientInfo, result, seq, {
      echoToRequester,
      fallbackQuestion: text,
      fallbackTarget: target,
      source,
      durationMs: Date.now() - startedAt,
      answerSource: '兜底库',
    });

    return {
      seq,
      reply,
      actions: processed?.actions || result.actions,
      target,
      fallback: {
        id: match.item.id,
        name: match.item.name,
        score: match.score,
        matchedKeywords: match.matchedKeywords,
      },
    };
  }

  async _tryHandleWebSearch({
    text,
    clientInfo,
    seq,
    target,
    source,
    startedAt,
    echoToRequester = true,
    forceIntent = false,
  }) {
    const route = webSearchService.classifyRoute(text, { target });
    if (!forceIntent && route.mode === 'NONE') {
      this._traceRoute(seq, {
        handler: 'web_search_intent',
        matched: false,
        status: 'NO_MATCH',
        reason: route.reason,
      });
      return null;
    }
    this._traceRoute(seq, {
      handler: 'web_search_intent',
      matched: true,
      status: forceIntent ? 'FORCED' : 'MATCHED',
      reason: route.reason,
      ruleId: route.intent?.item?.id || '',
      ruleName: route.intent?.item?.name || '',
    });
    try {
      const result = await webSearchService.answer({
        text,
        target,
        force: forceIntent,
        onProgress: progress => {
          this._emitAnswerProgress(seq, progress.stage, {
            ...progress,
            clientInfo,
            showOnScreen: true,
          });
        },
      });
      if (!result) throw new Error('联网意图已命中，但搜索服务没有返回结果');
      const webStatus = String(result.context?.webSearchStatus || '');
      const answerResult = webStatus === 'ANSWERED'
        ? result
        : await this._buildWebBestEffortResult({
            question: text,
            target,
            seq,
            clientInfo,
            source,
            webResult: result,
          });
      const candidate = this._recordWebQaCandidate(text, answerResult);
      if (candidate) {
        answerResult.context = {
          ...(answerResult.context || {}),
          webQaCandidateId: candidate.id,
          webQaCandidateUseCount: candidate.useCount,
          webQaCandidateEligible: candidate.eligibleForPromotion,
        };
      }
      this._traceRoute(seq, {
        handler: 'web_search_execute',
        matched: true,
        status: answerResult.context?.webSearchStatus || 'ANSWERED',
        reason: answerResult.context?.webSearchFallbackReason || '',
      });

      const processed = await this._processModelResult(clientInfo, answerResult, seq, {
        echoToRequester,
        fallbackQuestion: text,
        fallbackTarget: target,
        source,
        durationMs: Date.now() - startedAt,
        answerSource: answerResult.context?.answerSource || '联网搜索',
      });

      return {
        seq,
        reply: processed?.reply || answerResult.reply,
        actions: processed?.actions || answerResult.actions,
        target,
        webSearch: {
          query: answerResult.context?.webSearchQuery || text,
          ruleId: answerResult.context?.webSearchRuleId || '',
          ruleName: answerResult.context?.webSearchRuleName || '',
          sources: answerResult.context?.sources || [],
        },
      };
    } catch (err) {
      const error = String(err?.message || err || 'unknown error');
      console.warn(`[Router] Web search matched but failed: ${error}`);
      this._traceRoute(seq, {
        handler: 'web_search_execute',
        matched: true,
        status: 'FAILED',
        reason: 'WEB_EXECUTION_FAILED',
        error: error.slice(0, 300),
      });
      const reply = UNANSWERABLE_REPLY;
      const result = {
        reply,
        actions: [{
          type: 'show_qa',
          target,
          params: { question: text, answer: reply },
        }],
        context: {
          answerSource: '联网搜索异常',
          webSearchStatus: 'UNAVAILABLE',
          webSearchFallbackReason: error.slice(0, 300),
          webSearchQuery: route.intent?.query || text,
          webSearchRuleId: route.intent?.item?.id || '',
          webSearchRuleName: route.intent?.item?.name || '',
          sources: [],
        },
      };
      const processed = await this._processModelResult(clientInfo, result, seq, {
        echoToRequester,
        fallbackQuestion: text,
        fallbackTarget: target,
        source,
        durationMs: Date.now() - startedAt,
        answerSource: '联网搜索异常',
      });
      return {
        seq,
        reply: processed?.reply || reply,
        actions: processed?.actions || result.actions,
        target,
        webSearch: { query: route.intent?.query || text, error },
      };
    }
  }

  async _buildWebBestEffortResult({ question, target, seq, clientInfo, source, webResult }) {
    const reason = String(webResult?.context?.webSearchFallbackReason || '联网证据不足').slice(0, 300);
    const prompt = [
      `用户问题：${question}`,
      `联网检索状态：${reason}`,
      '请基于可靠的通用知识给出尽可能有帮助的直接回答。',
      '如果问题依赖最新事实且当前不能核实，明确区分“可确认信息”和“尚未核实信息”，但仍提供背景、判断依据或核实方向。',
      '禁止编造具体人名、时间、金额、比分、型号或结论；禁止只回复无法回答、资料不足、超出能力或联系工作人员。',
    ].join('\n');
    const generated = await this._chatDirectLlm({
      text: prompt,
      seq,
      deviceId: clientInfo.deviceId || source,
      deviceType: clientInfo.type,
      clientId: clientInfo.clientId,
      systemPrompt: buildQaSystemPrompt(),
    });
    if (generated?.error) return webResult;
    const classified = classifyAssistantReply(generated?.reply);
    if (!classified.useful) return webResult;
    const reply = this._shortDisplayReply(generated.reply, question);
    return {
      ...webResult,
      reply,
      actions: [{ type: 'show_qa', target, params: { question, answer: reply } }],
      context: {
        ...(webResult.context || {}),
        answerSource: '联网搜索（尽力回答）',
        webSearchOriginalStatus: webResult.context?.webSearchStatus || '',
        webSearchStatus: 'BEST_EFFORT_ANSWERED',
        bestEffortFallback: true,
        contextEligible: true,
        finalRoute: 'WEB_BEST_EFFORT_ANSWERED',
      },
    };
  }

  _shouldRetryWebAfterLlm(question, reply) {
    return shouldRetryWebAfterLlm(question, reply);
  }

  async _tryHandleLlmFreshnessFallback({
    text,
    rawReply,
    clientInfo,
    seq,
    target,
    source,
    startedAt,
    echoToRequester = true,
  }) {
    if (!this._shouldRetryWebAfterLlm(text, rawReply)) return null;
    this._traceRoute(seq, {
      handler: 'llm_freshness_fallback',
      matched: true,
      status: 'FORCE_WEB',
      reason: 'LLM_KNOWLEDGE_CUTOFF_OR_FUTURE_REFUSAL',
    });
    return this._tryHandleWebSearch({
      text,
      clientInfo,
      seq,
      target,
      source,
      startedAt,
      echoToRequester,
      forceIntent: true,
    });
  }

  _recordWebQaCandidate(question, result) {
    try {
      if (result?.context?.webSearchStatus !== 'ANSWERED') return null;
      const answer = String(result?.reply || '').trim();
      if (!answer || answer === UNANSWERABLE_REPLY) return null;
      const knowledgeCandidates = knowledgeDb.findAnswerCandidates(question, { limit: 5, minScore: 55 });
      const supporting = knowledgeCandidates.find(candidate =>
        candidate.score >= 75
        && this._answerAgreementScore(answer, candidate.item?.answer) >= 0.42);
      let candidate = webQaCandidateStore.record({
        question,
        answer,
        sources: result.context?.sources || [],
        knowledgeSupported: Boolean(supporting),
        knowledgeSource: supporting
          ? supporting.item?.title || supporting.item?.question || supporting.item?.source || 'knowledge-db'
          : '',
      });
      if (candidate?.autoPromotionReady) {
        const promoted = promoteWebQaCandidate(candidate, {
          promotionMode: 'AUTO_AFTER_MANUAL_REVIEW',
        });
        candidate = promoted.candidate || candidate;
        try {
          knowledgeDb.syncFromCurrentStores({ mediaLibrary, qaStore, specialConfigStore });
          vectorKnowledgeService.rebuildFromKnowledgeDb();
        } catch (err) {
          console.warn(`[Router] auto-promoted Web QA will be indexed later: ${err.message}`);
        }
      }
      return candidate;
    } catch (err) {
      console.warn(`[Router] Web QA candidate skipped: ${err.message}`);
      return null;
    }
  }

  _answerAgreementScore(left, right) {
    const normalize = value => String(value || '').normalize('NFKC').toLowerCase()
      .replace(/\s+/g, '')
      .replace(/[，。！？、,.!?;；:"'“”‘’（）()[\]{}<>《》【】]/g, '');
    const a = normalize(left);
    const b = normalize(right);
    if (!a || !b) return 0;
    if (a.includes(b) || b.includes(a)) return Math.min(a.length, b.length) / Math.max(a.length, b.length);
    const grams = value => Array.from({ length: Math.max(0, value.length - 1) }, (_, index) => value.slice(index, index + 2));
    const ag = grams(a);
    const bg = grams(b);
    const counts = new Map();
    for (const item of bg) counts.set(item, (counts.get(item) || 0) + 1);
    let overlap = 0;
    for (const item of ag) {
      const count = counts.get(item) || 0;
      if (count > 0) {
        overlap += 1;
        counts.set(item, count - 1);
      }
    }
    return ag.length + bg.length ? (2 * overlap) / (ag.length + bg.length) : 0;
  }

  async _tryHandleMarkdownRag({ text, retrievalText = text, clientInfo, seq, target, source, startedAt, echoToRequester = true }) {
    const effectiveRetrievalText = String(retrievalText || text || '').trim();
    const rawVectorSnippets = await vectorKnowledgeService.searchRag(effectiveRetrievalText, { limit: 5, excerptLength: 260 });
    const vectorSnippets = this._filterKnowledgeSnippetsForEntity(effectiveRetrievalText, rawVectorSnippets);
    const rawLocalSnippets = vectorSnippets.length
      ? []
      : knowledgeDb.findRagSnippets(effectiveRetrievalText, { limit: 5, minScore: 18, excerptLength: 260 });
    const localSnippets = this._filterKnowledgeSnippetsForEntity(effectiveRetrievalText, rawLocalSnippets);
    const snippets = vectorSnippets.length
      ? vectorSnippets.slice(0, 2)
      : localSnippets.slice(0, 2);
    if (!snippets.length) {
      const entityScope = assessKnowledgeEntityConsistency(effectiveRetrievalText, {});
      if (entityScope.applicable) {
        this._traceRoute(seq, {
          handler: 'local_knowledge_entity_gate',
          matched: true,
          status: 'RAG_MISS_CONTINUE',
          reason: `缺少实体证据，继续普通模型回答：${entityScope.queryEntities.join('、')}`,
        });
      }
      return null;
    }

    this._dispatchPendingQuestion({ text, target, clientInfo, seq });

    const result = await modelProvider.chat({
      text: buildRagAnswerUserPrompt(text, snippets, {
        conversationContext: this._modelContextFor(seq),
      }),
      systemPrompt: buildRagAnswerSystemPrompt(),
      responseSchema: RAG_ANSWER_SCHEMA,
      deviceId: clientInfo.deviceId,
      deviceType: clientInfo.type,
      clientId: clientInfo.clientId,
      isVoice: source === 'voice' || source === 'asr-apk',
      context: RAG_ANSWER_PROMPT_VERSION,
      temperature: 0.1,
      maxTokens: 320,
    });

    if (result.error) {
      console.warn(`[Router] Markdown RAG skipped: ${result.error}`);
      return null;
    }

    if (!classifyAssistantReply(result.reply).useful) {
      this._traceRoute(seq, {
        handler: 'local_knowledge_answer',
        matched: true,
        status: 'RAG_REFUSAL_CONTINUE',
        reason: '知识库模型未形成有效回答，继续普通模型回答',
      });
      return null;
    }

    const safeResult = this._ensureHelpfulModelResult(result, text);
    safeResult.context = {
      ...(safeResult.context || {}),
      answerSource: vectorSnippets.length ? 'ChromaDB RAG' : 'RAG',
      ragPromptVersion: RAG_ANSWER_PROMPT_VERSION,
    };
    const reply = String(safeResult.reply || '').trim();
    if (!reply) return null;

    const cachedQa = this._saveRagAnswerToQa({ question: text, answer: reply, snippets });
    const knowledgeSources = this._knowledgeSourcesFromItems(snippets);

    const finalResult = {
      reply,
      actions: [{
        type: 'show_qa',
        target,
        params: { question: text, answer: reply, knowledgeSources },
      }],
      context: {
        rag: true,
        attachRelatedMedia: true,
        ragCachedQaId: cachedQa?.id || '',
        knowledgeSources,
        ragSources: snippets.map(item => ({
          id: item.id,
          title: item.title,
          score: item.score,
          source: item.source,
        })),
      },
    };

    const processed = await this._processModelResult(clientInfo, finalResult, seq, {
      echoToRequester,
      fallbackQuestion: text,
      fallbackTarget: target,
      source,
      durationMs: Date.now() - startedAt,
    });

    return {
      seq,
      reply,
      actions: processed?.actions || finalResult.actions,
      target,
      qaCached: cachedQa ? { id: cachedQa.id } : null,
      rag: finalResult.context.ragSources,
    };
  }

  _isDefinitionLikeQuestion(text) {
    const value = String(text || '').replace(/[？?！!。,.，、\s]/g, '');
    return /(是什么|什么意思|介绍一下|介绍下|讲一下|说一下|有什么用|有什么帮助|有哪些帮助|有什么作用|有哪些作用|发挥.*作用|价值是什么|有什么价值|应用场景|能做什么|解决什么|解决了什么|主要解决)/.test(value);
  }

  _mergeFaqCandidates(...groups) {
    const rows = [];
    const seen = new Set();
    for (const group of groups) {
      for (const entry of group || []) {
        const key = entry?.item?.id || `${entry?.item?.question}:${entry?.item?.answer}`;
        if (!key || seen.has(key)) continue;
        seen.add(key);
        rows.push(entry);
      }
    }
    return rows.sort((a, b) => b.score - a.score).slice(0, 20);
  }

  _filterQaCandidatesForQuestion(question, candidates = []) {
    return (candidates || []).filter(candidate => {
      if (!this._isAllowedQaCandidateForQuestion(question, candidate)) return false;
      const entityConsistency = assessKnowledgeEntityConsistency(question, candidate?.item);
      candidate.entityConsistency = entityConsistency;
      if (entityConsistency.allowed) return true;
      console.info(
        `[Router] FAQ candidate blocked by entity consistency: `
        + `query=${entityConsistency.queryEntities.join('、')}, `
        + `missing=${entityConsistency.missingEntities.join('、')}, `
        + `candidate=${String(candidate?.item?.question || candidate?.item?.title || '').slice(0, 80)}`,
      );
      return false;
    });
  }

  _filterKnowledgeSnippetsForEntity(question, snippets = []) {
    return (snippets || []).filter(snippet => {
      const entityConsistency = assessKnowledgeEntityConsistency(question, snippet);
      snippet.entityConsistency = entityConsistency;
      if (entityConsistency.allowed) return true;
      console.info(
        `[Router] RAG snippet blocked by entity consistency: `
        + `query=${entityConsistency.queryEntities.join('、')}, `
        + `missing=${entityConsistency.missingEntities.join('、')}, `
        + `snippet=${String(snippet?.title || snippet?.file || '').slice(0, 80)}`,
      );
      return false;
    });
  }

  _isAllowedQaCandidateForQuestion(question, candidate) {
    if (!candidate) return false;
    if (!this._isAutoCachedQa(candidate)) return true;
    return this._isSameAutoCacheQuestionScope(question, candidate.item?.question || '');
  }

  _isAutoCachedQa(candidate) {
    const tags = [
      ...(candidate?.item?.tags || []),
      ...(candidate?.item?.keywords || []),
    ].map(item => String(item || '').trim().toLowerCase());
    return tags.some(tag => ['auto-cache', 'llm-cache', 'rag-cache', 'markdown-rag'].includes(tag));
  }

  _isSameAutoCacheQuestionScope(question, candidateQuestion) {
    const left = this._questionScopeTerms(question);
    const right = this._questionScopeTerms(candidateQuestion);
    if (!left.length || !right.length) return false;

    const leftText = left.join('');
    const rightText = right.join('');
    if (leftText === rightText) return true;
    if (leftText.includes(rightText) || rightText.includes(leftText)) {
      const shorter = Math.min(leftText.length, rightText.length);
      const longer = Math.max(leftText.length, rightText.length);
      return longer ? shorter / longer >= 0.72 : false;
    }

    return left.some(item => right.includes(item));
  }

  _questionScopeTerms(text) {
    const stopWords = new Set([
      '请问', '帮我', '给我', '一下', '一些', '哪些', '什么', '怎么', '如何', '是否', '是不是', '有没有',
      '非常', '好玩', '著名', '知名', '推荐', '重点', '介绍', '讲讲', '说说', '可逛', '可以逛',
      '景点', '景区', '旅游', '游玩', '地方', '内容', '资料', '文件', '相关', '问题', '方面',
      '是什么', '有什么', '有哪些', '主要', '核心', '作用', '帮助', '价值', '意义', '有',
    ]);

    let value = String(text || '')
      .replace(/\p{P}|\p{S}/gu, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    for (const word of [...stopWords].sort((a, b) => b.length - a.length)) {
      value = value.replaceAll(word, ' ');
    }
    return value
      .split(/\s+/)
      .map(item => item.trim())
      .filter(item => item.length >= 2)
      .filter(item => !stopWords.has(item))
      .slice(0, 6);
  }

  _saveRagAnswerToQa({ question, answer, snippets }) {
    const source = this._knowledgeSourcesFromItems(snippets)[0] || {};
    return this._saveAutoAnswerToQa({
      question,
      answer,
      sourceLabel: 'rag-cache',
      sourceFile: source.fileName || '',
      sourcePage: source.page || null,
      keywords: [
        'markdown-rag',
        ...snippets.flatMap(item => [item.title, ...(item.tags || [])]),
      ],
    });
  }

  _saveAutoAnswerToQa({ question, answer, sourceLabel = 'auto-cache', keywords = [], actions = [], sourceFile = '', sourcePage = null }) {
    try {
      if (!config.QA_AUTO_CACHE_ENABLED) return null;
      const normalizedQuestion = String(question || '').trim();
      const normalizedAnswer = String(answer || '').trim();
      if (!normalizedQuestion || !normalizedAnswer) return null;
      if (this._isUnhelpfulModelReply(normalizedAnswer)) return null;
      const hasNonQaAction = (actions || []).some(action => action?.type && action.type !== 'show_qa');
      if (hasNonQaAction) return null;

      const cached = qaStore.saveByQuestion({
        question: normalizedQuestion,
        answer: normalizedAnswer,
        keywords: [
          sourceLabel,
          'auto-cache',
          ...keywords,
        ],
        enabled: true,
        sourceFile,
        sourcePage,
      });
      try {
        knowledgeDb.syncFromCurrentStores({ mediaLibrary, qaStore, specialConfigStore });
        vectorKnowledgeService.rebuildFromKnowledgeDb();
      } catch (err) {
        console.warn(`[Router] QA auto-cache indexed later: ${err.message}`);
      }
      return cached;
    } catch (err) {
      console.warn(`[Router] QA auto-cache skipped: ${err.message}`);
      return null;
    }
  }

  async _rerankFaqWithModel(question, candidates = []) {
    const rows = candidates.slice(0, 15).filter(item => item?.item?.question && item?.item?.answer);
    if (!rows.length) return null;

    const controller = new AbortController();
    const timeoutMs = Number(config.FAQ_RERANK_TIMEOUT_MS || 8000);
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const response = await fetch(`${config.OLLAMA.baseUrl}/api/chat`, {
        method: 'POST',
        signal: controller.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: config.OLLAMA.model,
          stream: false,
          think: false,
          messages: [
            {
              role: 'system',
              content: [
                '你是FAQ语义匹配器，只判断用户问题是否等价或高度相关于候选FAQ。',
                '必须只输出JSON，不要解释。',
                '格式：{"match":true,"index":1,"confidence":0.0}',
                '规则：',
                '1. index 是候选编号，从1开始。',
                '2. 如果没有合适候选，输出 {"match":false,"index":0,"confidence":0}。',
                '3. 只有语义确实相近才 match=true。',
                '4. 不要生成答案，只负责选择FAQ。',
              ].join('\n'),
            },
            {
              role: 'user',
              content: [
                `用户问题：${question}`,
                '',
                '候选FAQ：',
                ...rows.map((entry, index) => [
                  `${index + 1}. 问：${entry.item.question}`,
                  `   答：${entry.item.answer}`,
                  `   程序分：${entry.score}`,
                ].join('\n')),
              ].join('\n'),
            },
          ],
          options: { temperature: 0, num_predict: 80 },
        }),
      });

      if (!response.ok) return null;
      const data = await response.json();
      const raw = data?.message?.content || data?.response || '';
      const parsed = this._extractJsonObject(raw);
      if (!parsed?.match) return null;
      const index = Number.parseInt(parsed.index, 10);
      const confidence = Number(parsed.confidence);
      if (!Number.isFinite(index) || index < 1 || index > rows.length) return null;
      if (Number.isFinite(confidence) && confidence < 0.42) return null;
      return {
        ...rows[index - 1],
        score: Math.max(rows[index - 1].score, Math.round((confidence || 0.7) * 100)),
        reranked: true,
      };
    } catch (err) {
      console.warn(`[Router] FAQ rerank skipped: ${err.message}`);
      return null;
    } finally {
      clearTimeout(timer);
    }
  }

  _extractJsonObject(raw) {
    const text = String(raw || '').trim();
    if (!text) return null;
    const codeBlock = text.match(/```(?:json)?\s*\n?([\s\S]*?)\n?\s*```/);
    const source = codeBlock?.[1]?.trim() || text;
    const start = source.indexOf('{');
    const end = source.lastIndexOf('}');
    if (start < 0 || end <= start) return null;
    try {
      return JSON.parse(source.slice(start, end + 1));
    } catch {
      return null;
    }
  }

  async _tryHandleDirectControlCommand({ text, clientInfo, seq, target, source, startedAt, echoToRequester = true }) {
    const directAction = this._parseDirectControlAction(text);
    if (!directAction) return null;

    const reply = directAction.reply;
    const result = {
      reply,
      actions: [{
        type: directAction.type,
        target,
        params: {},
      }],
    };

    await this._processModelResult(clientInfo, result, seq, {
      echoToRequester,
      fallbackQuestion: text,
      fallbackTarget: target,
      source,
      durationMs: Date.now() - startedAt,
    });

    return { seq, reply, actions: result.actions, target };
  }

  async _tryHandleCurrentPptCommentary({ text, clientInfo, seq, target, source, startedAt, echoToRequester = true }) {
    const realtimeIntent = this._parseRealtimeCurrentContentIntent(text);
    if (realtimeIntent) {
      const generated = await this._tryHandleRealtimePptCommentary({
        text,
        clientInfo,
        seq,
        target,
        source,
        startedAt,
        echoToRequester,
        industryHint: realtimeIntent.industryHint,
      });
      if (generated) return generated;
    }

    const level = this._parseCurrentPptCommentaryLevel(text);
    if (!level) return null;

    const state = this._currentPptStateForTarget(target);
    if (!state?.fileName) return null;

    const levelLabel = {
      concise: '精炼版',
      standard: '标准版',
      detailed: '详细版',
    }[level] || '标准版';
    const item = this._findMediaItem({ fileName: state.fileName });
    const commentaryText = item
      ? this._commentaryTextForLevel(item, level, state.slideNumber || 1)
      : '';
    const commentaryPreview = this._compactPreviewText(commentaryText, 48)
      || this._currentPptCommentaryPreview(target, level);
    const reply = commentaryPreview
      ? `按${levelLabel}播放当前PPT讲解词：${commentaryPreview}`
      : `按${levelLabel}播放当前PPT讲解词`;
    const action = commentaryText
      ? {
          type: 'speak',
          target,
          params: {
            text: commentaryText,
            preserveView: 'true',
            commentaryMode: 'stored',
            commentaryLevel: level,
            currentPptFileName: state.fileName,
            currentPptSlideNumber: String(state.slideNumber || 1),
          },
        }
      : {
          type: 'ppt_speak_commentary',
          target,
          params: { commentaryLevel: level, commentaryPreview },
        };
    const result = {
      reply,
      actions: [action],
      context: { answerSource: '讲解词规则' },
    };

    await this._processModelResult(clientInfo, result, seq, {
      echoToRequester,
      fallbackQuestion: text,
      fallbackTarget: target,
      source,
      durationMs: Date.now() - startedAt,
    });

    return { seq, reply, actions: result.actions, target, commentaryLevel: level };
  }

  async _tryHandleRealtimePptCommentary({ text, clientInfo, seq, target, source, startedAt, echoToRequester = true, industryHint = '' }) {
    const state = this._clientReportedPptStateForTarget(target);
    const item = state?.fileName ? this._findMediaItem({ fileName: state.fileName }) : null;
    const detailedText = item ? this._commentaryTextForLevel(item, 'detailed', state?.slideNumber || 1) : '';
    if (!state?.fileName || !item || !detailedText) return null;

    const profile = this._activeVisitorProfile(text, industryHint);
    const generated = await this._generateRealtimeCommentary({
      requestText: text,
      detailedText,
      fileTitle: item.title || item.name,
      pageNumber: state.slideNumber || 1,
      profile,
    });
    const speechText = this._cleanRealtimeCommentary(generated) || this._compactPreviewText(detailedText, 180);
    if (!speechText) return null;

    const reply = `实时生成讲解词：${speechText}`;
    const result = {
      reply,
      actions: [{
        type: 'speak',
        target,
        params: {
          text: speechText,
          preserveView: 'true',
          commentaryMode: 'realtime',
          currentPptFileName: state.fileName,
          currentPptSlideNumber: String(state.slideNumber || 1),
        },
      }],
      context: {
        answerSource: generated ? '实时生成讲解词' : '详细版讲解词兜底',
        realtimeCommentary: true,
        visitorProfile: profile,
      },
    };

    await this._processModelResult(clientInfo, result, seq, {
      echoToRequester,
      fallbackQuestion: text,
      fallbackTarget: target,
      source,
      durationMs: Date.now() - startedAt,
      answerSource: result.context.answerSource,
    });

    return {
      seq,
      reply,
      actions: result.actions,
      target,
      commentaryLevel: 'realtime',
      realtime: true,
    };
  }

  _parseRealtimeCurrentContentIntent(text) {
    const raw = String(text || '').trim();
    const value = raw.replace(/[？?！!。,.，、\s]/g, '');
    if (!value) return null;

    const hasCurrent = /(当前|这页|这一页|本页|这个内容|当前内容|当前页面|当前页|当前PPT|当前ppt|正在播放)/.test(value);
    const wantsIntro = /(介绍|讲解|讲一下|讲讲|说一下|说说|讲什么)/.test(value);
    if (!hasCurrent || !wantsIntro) return null;
    if (/(快速|简单|简短|简要|精炼|30秒|一句话|概括|详细|展开|讲深|多讲|深入|完整|讲细)/.test(value)) return null;
    if (/(是什么|为什么|怎么|如何|多少钱|多久|能不能|是否|有没有|吗)$/.test(value)) return null;

    const industryHint = this._extractIndustryHint(raw);
    const wantsPersonalizedCommentary = Boolean(industryHint)
      || /(结合|面向|针对|按照|根据).*(行业|企业|客户|来访|画像|需求|关注点)/.test(value)
      || /(行业|企业|客户|来访|画像|需求|关注点).*(介绍|讲解|讲一下|讲讲|说一下|说说)/.test(value);
    if (!wantsPersonalizedCommentary) return null;

    return { industryHint };
  }

  async _generateRealtimeCommentary({ requestText, detailedText, fileTitle, pageNumber, profile }) {
    const profileLines = [
      profile.companyName ? `来访企业：${profile.companyName}` : '',
      profile.industry ? `行业属性：${profile.industry}` : '',
      profile.visitorName ? `来访人：${profile.visitorName}` : '',
      profile.visitDuration ? `参观时间：${profile.visitDuration}` : '',
      profile.visitPurpose ? `来访目的：${profile.visitPurpose}` : '',
      profile.tags?.length ? `画像标签：${profile.tags.join('、')}` : '',
    ].filter(Boolean).join('\n') || '暂无企业画像，按行业和当前页内容生成。';

    const prompt = [
      '请为展厅现场生成一段可直接播报的当前PPT讲解词。',
      '要求：',
      '1. 只基于给定详细版讲解词和用户画像，不编造数据、案例、承诺。',
      '2. 语气自然，像讲解员现场讲解，不要出现“根据资料”“以下是”等字样。',
      '3. 结合行业/企业关注点，但不要生硬堆砌。',
      '4. 控制在90到140个中文字符，输出纯正文。',
      '',
      `用户原话：${requestText}`,
      `当前文件：${fileTitle}`,
      `当前页码：${pageNumber}`,
      '',
      '用户画像：',
      profileLines,
      '',
      '当前页详细版讲解词：',
      detailedText,
    ].join('\n');

    const result = await modelProvider.chat({
      text: prompt,
      context: 'realtime_ppt_commentary',
      deviceId: 'realtime-commentary',
      deviceType: config.DEVICE_TYPES.ADMIN,
      isVoice: false,
    });
    if (result?.error) {
      console.warn(`[Router] realtime commentary generation failed: ${result.error}`);
      return '';
    }
    return result?.reply || '';
  }

  _cleanRealtimeCommentary(text) {
    let value = this._cleanModelReplyText(String(text || ''))
      .replace(/^实时生成讲解词[:：]\s*/, '')
      .replace(/^讲解词[:：]\s*/, '')
      .replace(/^答[:：]\s*/, '')
      .trim();
    if (!value) return '';
    value = value.replace(/\s+/g, ' ');
    if (value.length > 180) value = `${value.slice(0, 180)}...`;
    return value;
  }

  _activeVisitorProfile(text = '', industryHint = '') {
    const active = specialConfigStore.get(specialConfigStore.getActiveId()) || {};
    const profile = active.visitorProfile || active.profile || {};
    const description = String(active.description || '').trim();
    const name = String(active.name || '').trim();
    const parsed = this._parseVisitorProfileText(`${description}\n${name}`);
    const industry = industryHint || profile.industry || profile.industryLabel || parsed.industry || this._extractIndustryHint(text);
    return {
      companyName: profile.companyName || profile.company || parsed.companyName,
      industry,
      visitorName: profile.visitorName || profile.visitor || parsed.visitorName,
      visitDuration: profile.visitDuration || profile.duration || parsed.visitDuration,
      visitPurpose: profile.visitPurpose || profile.purpose || parsed.visitPurpose,
      tags: [...new Set([
        ...(Array.isArray(profile.tags) ? profile.tags : []),
        ...(industry ? [industry] : []),
      ].map(item => String(item || '').trim()).filter(Boolean))],
    };
  }

  _parseVisitorProfileText(text) {
    const value = String(text || '');
    const companyName =
      value.match(/为\s*([^。\n]+?)\s*自动生成/)?.[1]?.trim() ||
      value.match(/^([^-。\n]+?)(?:-[^-。\n]+专场)/)?.[1]?.trim() ||
      '';
    return {
      companyName,
      industry: value.match(/识别行业[:：]\s*([^。，\n]+)/)?.[1]?.trim() || '',
      visitorName: value.match(/来访人[:：]\s*([^。，\n]+)/)?.[1]?.trim() || '',
      visitDuration: value.match(/参观时间[:：]\s*([^。，\n]+)/)?.[1]?.trim() || '',
      visitPurpose: value.match(/来访目的[:：]\s*([^。\n]+)/)?.[1]?.trim() || '',
    };
  }

  _extractIndustryHint(text) {
    const value = String(text || '').replace(/\s+/g, '');
    const known = [
      '教育局', '教育行业', '教育',
      '交通局', '交通行业', '交通',
      '住建局', '建筑行业', '建筑', '住建',
      '农业农村局', '农业行业', '农业',
      '水利局', '水利行业', '水利',
      '文旅局', '文旅行业', '文旅',
      '卫健委', '医疗行业', '医疗',
      '工信局', '工业行业', '工业',
      '政务', '应急', '公安', '园区', '能源',
    ];
    const hit = known.find(item => value.includes(item));
    if (hit) return hit;
    return value.match(/我是([^，。,.!?！？]{2,12}?)(?:行业|的|单位|部门)/)?.[1]?.trim() || '';
  }

  _currentPptCommentaryPreview(target, level, maxLength = 48) {
    const state = this._clientReportedPptStateForTarget(target);
    if (!state?.fileName) return '';

    const item = this._findMediaItem({ fileName: state.fileName });
    if (!item) return '';

    const text = this._commentaryTextForLevel(item, level, state?.slideNumber || 1);
    return this._compactPreviewText(text, maxLength);
  }

  _clientReportedPptStateForTarget(target) {
    const screenNumber = Number.parseInt(target, 10);
    if (!Number.isFinite(screenNumber) || screenNumber <= 0) return null;

    const deviceStatus = deviceManager.getByScreenNumber(screenNumber)?.status || {};
    const fileName = this._baseFileName(deviceStatus.currentPptFileName || deviceStatus.currentFileName || '');
    if (!fileName) return null;

    return {
      fileName,
      slideNumber: Math.max(1, Number.parseInt(deviceStatus.currentPptSlideNumber || deviceStatus.currentSlideNumber, 10) || 1),
    };
  }

  _currentPptStateForTarget(target) {
    const screenNumber = Number.parseInt(target, 10);
    if (Number.isFinite(screenNumber) && screenNumber > 0) {
      const deviceStatus = deviceManager.getByScreenNumber(screenNumber)?.status || {};
      const statusFile = this._baseFileName(deviceStatus.currentPptFileName || deviceStatus.currentFileName || '');
      if (statusFile) {
        const reported = {
          fileName: statusFile,
          slideNumber: Math.max(1, Number.parseInt(deviceStatus.currentPptSlideNumber || deviceStatus.currentSlideNumber, 10) || 1),
          updatedAt: Number(deviceStatus.updatedAt) || 0,
        };
        const tracked = this._currentPptStates.get(screenNumber);
        const sameFile = tracked && this._baseFileName(tracked.fileName).toLocaleLowerCase('zh-CN')
          === statusFile.toLocaleLowerCase('zh-CN');
        if (sameFile && Number(tracked.slideNumber) !== reported.slideNumber) {
          const commandIsRecent = Date.now() - Number(tracked.updatedAt || 0) <= 3000;
          const commandIsNewer = Number(tracked.updatedAt || 0) >= reported.updatedAt;
          if (commandIsRecent || commandIsNewer) return tracked;
        }
        this._currentPptStates.set(screenNumber, reported);
        return reported;
      }
      return this._currentPptStates.get(screenNumber) || null;
    }

    for (const screen of deviceManager.getOnlineScreens()) {
      const state = this._currentPptStateForTarget(screen.screenNumber);
      if (state?.fileName) return state;
    }
    return null;
  }

  _currentPptMediaItemForTarget(target) {
    const state = this._currentPptStateForTarget(target);
    if (state?.fileName) {
      const currentItem = this._findMediaItem({ fileName: state.fileName });
      if (currentItem?.type === 'ppt') return currentItem;
    }

    const active = specialConfigStore.get(specialConfigStore.getActiveId());
    if (!active || active.mode === 'blackout') return null;

    const targetScreen = Number.parseInt(target, 10);
    const assignments = Array.isArray(active.assignments) ? active.assignments : [];
    const candidates = Number.isFinite(targetScreen) && targetScreen > 0
      ? assignments.filter(item => Number.parseInt(item.screenNumber, 10) === targetScreen)
      : assignments;

    for (const assignment of candidates) {
      const selectedItem = this._selectedFilePickerItem(assignment.screenNumber);
      if (selectedItem) {
        const item = this._findMediaItem({
          fileId: selectedItem.id,
          fileName: selectedItem.name || selectedItem.path || selectedItem.title,
        });
        if (item?.type === 'ppt') return item;
      }

      const item = this._findFirstMediaItem(assignment);
      if (item?.type === 'ppt') return item;
    }

    return null;
  }

  _currentKnowledgeScopeForTarget(target) {
    const state = this._currentPptStateForTarget(target);
    const item = this._currentPptMediaItemForTarget(target);
    return {
      unitId: String(item?.unitId || '').trim(),
      fileName: state?.fileName || item?.name || '',
      slideNumber: state?.slideNumber || 1,
      title: item?.title || '',
    };
  }

  _commentaryTextForLevel(item, level, pageNumber = 1) {
    const pages = item?.commentaryPages || {};
    const pageKey = String(Math.max(1, Number.parseInt(pageNumber, 10) || 1));
    const hasPageCommentary = Object.values(pages).some(Boolean);
    const pageText = pages[pageKey];
    if (hasPageCommentary && !pageText) return '';
    const source = String(pageText || item?.commentary || '').trim();
    if (!source) return '';

    const levelNames = {
      concise: '精炼版',
      standard: '标准版',
      detailed: '详细版',
    };
    const label = levelNames[this._normalizeCommentaryLevel(level)] || levelNames.standard;
    const match = source.match(new RegExp(`【${label}】\\s*([\\s\\S]*?)(?=\\n\\s*【[^】]+】|$)`));
    return (match?.[1] || source).trim();
  }

  _compactPreviewText(text, maxLength = 48) {
    const value = String(text || '')
      .replace(/【[^】]+】/g, '')
      .replace(/&&/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    if (!value) return '';
    return value.length > maxLength ? `${value.slice(0, maxLength)}...` : value;
  }

  _parseCurrentPptCommentaryLevel(text) {
    const value = String(text || '')
      .replace(/[？?！!。,.，、\s]/g, '')
      .trim();
    if (!value) return '';

    const currentContentIntent =
      /(当前|这页|这一页|本页|这个内容|这个文件|这个PPT|这个ppt|当前内容|当前页面|当前页|当前PPT|当前ppt|正在播放)/.test(value) &&
      /(介绍|讲解|讲一下|讲讲|说一下|说说|播报|播放讲解词|讲什么)/.test(value);
    const shortIntroIntent =
      value.length <= 12 &&
      /^(快速介绍|快速介绍一下|简单说|简单介绍|简单介绍一下|简单讲一下|简短介绍|简要介绍|简要介绍一下|精炼介绍|精炼介绍一下|精炼的介绍一下|一句话介绍|概括一下|快速过一下|详细介绍|详细介绍一下|详细的介绍一下|详细讲|详细讲一下|展开讲|展开讲讲|讲深一点|多讲一点|深入介绍|完整介绍|讲细一点|介绍一下|讲解|讲解下|讲解一下|帮我讲解|帮我讲解一下|给我讲解|给我讲解一下|讲一下|讲讲)$/.test(value);

    if (!currentContentIntent && !shortIntroIntent) return '';

    const looksLikeSpecificQuestion =
      /(是什么|为什么|怎么|如何|多少钱|多久|能不能|是否|有没有|吗)$/.test(value) &&
      !/(这页|这一页|本页|当前|当前页|当前内容).*(讲什么|介绍|讲解|讲一下)/.test(value);
    if (looksLikeSpecificQuestion) return '';

    if (/(快速|简单|简短|简要|精炼|30秒|一句话|概括|快速过)/.test(value)) return 'concise';
    if (/(详细|展开|讲深|多讲|深入|完整|讲细)/.test(value)) return 'detailed';
    return 'standard';
  }

  async _tryHandleMediaCommand({ text, clientInfo, seq, target, source, startedAt, echoToRequester = true }) {
    if (this._parseDirectControlAction(text)) return null;

    const nth = this._parseOpenOrdinal(text);
    if (nth) {
      return this._handleOpenMediaByOrdinal({ nth, text, clientInfo, seq, target, source, startedAt, echoToRequester });
    }

    if (!this._isMediaSearchText(text)) return null;

    const requestedScreenTarget = this._parseScreenTargetFromText(text);
    const actionTarget = requestedScreenTarget || target;
    if (this._isExplicitMediaActionText(text)) {
      const directItem = this._findDirectMediaItem(text);
      if (directItem) {
        const reply = `正在打开：${directItem.title || directItem.name}`;
        const action = this._mediaActionForItem(directItem, actionTarget);
        const result = {
          reply,
          actions: [action],
          context: { answerSource: '文件打开', mediaActionIntent: true },
        };
        this._traceRoute(seq, {
          handler: 'media_action',
          matched: true,
          status: 'OPENED',
          reason: 'EXPLICIT_FILE_ACTION',
        });
        await this._processModelResult(clientInfo, result, seq, {
          echoToRequester,
          fallbackQuestion: text,
          fallbackTarget: actionTarget,
          source,
          durationMs: Date.now() - startedAt,
          answerSource: '文件打开',
        });
        return { seq, reply, actions: [action], target: actionTarget, item: directItem };
      }
    }

    const resolvedMediaText = this._resolveFollowupMediaText(text, clientInfo, source);
    const effectiveText = resolvedMediaText.text;
    if (resolvedMediaText.applied) {
      console.log(`[Router] [seq=${seq}] media follow-up resolved: "${text}" -> "${effectiveText}"`);
    }

    const resolvedScreenTarget = this._parseScreenTargetFromText(effectiveText);
    const resolvedActionTarget = resolvedScreenTarget || target;
    const searchText = resolvedScreenTarget ? this._removeScreenTargetFromText(effectiveText) : effectiveText;

    const screenFileList = this._getScreenFileListByText(effectiveText);
    if (screenFileList) {
      const { screenNumber, items } = screenFileList;
      const displayItems = items.map((item, index) => ({
        ...item,
        index: index + 1,
        hasCommentary: Boolean(item.hasCommentary),
        target: screenNumber,
      }));
      this._rememberMediaResults(screenNumber, source, displayItems);

      const reply = displayItems.length
        ? `${screenNumber}号屏有${displayItems.length}个文件`
        : `${screenNumber}号屏暂无文件`;
      const result = {
        reply,
        actions: [{
          type: 'show_media_results',
          target: screenNumber,
          params: {
            question: text,
            answer: reply,
            items: JSON.stringify(displayItems),
          },
        }],
        context: { answerSource: '专场文件' },
      };

      await this._processModelResult(clientInfo, result, seq, {
        echoToRequester,
        fallbackQuestion: text,
        fallbackTarget: screenNumber,
        source,
        durationMs: Date.now() - startedAt,
      });

      return { seq, reply, actions: result.actions, target: screenNumber, items: displayItems };
    }

    const mediaSearch = await this._searchMediaWithVector(searchText, 12);
    const items = mediaSearch.items;
    const displayItems = items.map((item, index) => ({
      index: index + 1,
      id: item.id,
      title: item.title,
      name: item.name,
      type: item.type,
      ext: item.ext,
      url: item.url,
      thumbnailUrl: item.thumbnailUrl,
      localPath: item.localPath,
      hasCommentary: Boolean(item.hasCommentary || String(item.commentary || '').trim()),
      ...(resolvedScreenTarget ? { target: resolvedActionTarget } : {}),
    }));

    this._rememberMediaResults(resolvedActionTarget, source, displayItems);

    const reply = displayItems.length
      ? `找到${displayItems.length}个文件，可说打开第几个`
      : '未找到相关文件';

    const result = {
      reply,
      actions: [{
        type: 'show_media_results',
        target: resolvedActionTarget,
        params: {
          question: text,
          answer: reply,
          items: JSON.stringify(displayItems),
        },
      }],
      context: { answerSource: mediaSearch.usedVector ? 'ChromaDB 文件' : '文件检索' },
    };

    await this._processModelResult(clientInfo, result, seq, {
      echoToRequester,
      fallbackQuestion: text,
        fallbackTarget: resolvedActionTarget,
        source,
        durationMs: Date.now() - startedAt,
      });

    return { seq, reply, actions: result.actions, target: resolvedActionTarget, items: displayItems };
  }

  _isExplicitMediaActionText(text) {
    const value = String(text || '').trim();
    if (!value || this._parseDirectControlAction(value)) return false;
    const hasAction = /(?:打开|播放|展示|调出|查看|放映|投屏|看一下|看下)/.test(value);
    if (!hasAction) return false;
    const hasMediaSignal = /(?:文件|资料|素材|PPT|ppt|视频|图片|图像|照片|\.pptx?|\.mp4|\.png|\.jpe?g)/i.test(value);
    if (hasMediaSignal) return true;
    const query = this._mediaActionQuery(value);
    return Boolean(query && this._findDirectMediaItem(value));
  }

  _findDirectMediaItem(text) {
    const query = this._mediaActionQuery(text);
    if (!query) return null;
    const normalizedQuery = this._normalizeMediaIdentity(query);
    if (!normalizedQuery) return null;
    const items = mediaLibrary.getAll().filter(isClientMediaAvailable);
    const exact = items.filter(item => [item.title, item.name, item.originalName, item.path]
      .some(value => this._normalizeMediaIdentity(value) === normalizedQuery));
    if (exact.length === 1) return exact[0];

    const contained = items.filter(item => [item.title, item.name, item.originalName]
      .some(value => this._normalizeMediaIdentity(value).includes(normalizedQuery)));
    if (contained.length === 1) return contained[0];

    const ranked = mediaLibrary.search(query, 3);
    return ranked.length === 1 ? ranked[0] : null;
  }

  _mediaActionQuery(text) {
    return this._removeScreenTargetFromText(String(text || ''))
      .replace(/(?:请|麻烦|帮我|给我|替我|我想|我要)/g, '')
      .replace(/(?:打开|播放|展示|调出|查看|放映|投屏|看一下|看下)/g, '')
      .replace(/(?:这个|那个|一下|文件|资料|素材)/g, ' ')
      .replace(/(?:的)?(?:PPT|ppt|视频|图片|图像|照片)\s*$/i, ' ')
      .replace(/[，。！？、,.!?;；:：]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  _normalizeMediaIdentity(value) {
    return String(value || '')
      .toLowerCase()
      .replace(/\.(pptx?|mp4|webm|avi|mov|mkv|png|jpe?g|gif|webp|bmp)$/i, '')
      .replace(/[\s_\-—（）()【】\[\]《》]/g, '');
  }

  async _searchMediaWithVector(searchText, limit = 12) {
    const lexicalItems = mediaLibrary.search(searchText, limit);
    const vectorRows = await vectorKnowledgeService.searchResources(searchText, { limit });
    if (!vectorRows.length) return { items: lexicalItems, usedVector: false };

    const allItems = mediaLibrary.getAll().filter(isClientMediaAvailable);
    const byKey = new Map();
    for (const item of allItems) {
      for (const key of [item.id, item.name, item.path, item.originalName, item.title]) {
        const value = String(key || '').trim().toLowerCase();
        if (value && !byKey.has(value)) byKey.set(value, item);
      }
    }

    const merged = [];
    const seen = new Set();
    const push = (item) => {
      if (!item) return;
      const key = String(item.id || item.name || item.path || item.title || '').toLowerCase();
      if (!key || seen.has(key)) return;
      seen.add(key);
      merged.push(item);
    };

    for (const row of vectorRows) {
      push(byKey.get(String(row.fileName || '').toLowerCase()));
      push(byKey.get(String(row.id || '').toLowerCase()));
      push(byKey.get(String(row.title || '').toLowerCase()));
    }
    lexicalItems.forEach(push);
    return { items: merged.slice(0, limit), usedVector: true };
  }

  async _handleOpenMediaByOrdinal({ nth, text, clientInfo, seq, target, source, startedAt, echoToRequester }) {
    const items = this._getRememberedMediaResults(target, source);
    const item = items[nth - 1];
    const reply = item
      ? `正在打开第 ${nth} 个：${item.title}`
      : '没有找到上一轮文件列表，请先问一下相关文件。';

    const actions = [];
    if (item) {
      actions.push({
        type: item.type === 'ppt' ? 'play_ppt' : item.type === 'image' ? 'show_doc' : 'play_video',
        target: item.target || target,
        params: { file: item.localPath || item.name },
      });
    } else {
      actions.push({
        type: 'show_qa',
        target,
        params: { question: text, answer: reply },
      });
    }

    const result = { reply, actions, context: { answerSource: item ? '文件打开' : '文件检索' } };
    await this._processModelResult(clientInfo, result, seq, {
      echoToRequester,
      fallbackQuestion: text,
      fallbackTarget: target,
      source,
      durationMs: Date.now() - startedAt,
    });

    return { seq, reply, actions, target };
  }

  _isMediaSearchText(text) {
    const value = String(text || '');
    if (!value.trim()) return false;
    if (this._parseDirectControlAction(value)) return false;
    return /(文件|资料|素材|PPT|ppt|视频|图片|图像|照片|可看|观看|播放|展示|打开|调出|查看|放映|投屏)/.test(value);
  }

  _getScreenFileListByText(text) {
    const value = String(text || '').trim();
    const normalized = value.replace(/\s+/g, '');
    if (!/(文件|资料|PPT|ppt|视频|图片|图像|照片|列表|有哪些|有什么|可以看|可看|观看|播放|展示)/.test(normalized)) return null;

    const match = normalized.match(/([一二两三四五六七八九十\d]+)号?屏/);
    if (!match) return null;

    const screenNumber = this._parseChineseNumber(match[1]);
    if (!Number.isFinite(screenNumber) || screenNumber <= 0) return null;

    const special = specialConfigStore.get(specialConfigStore.getActiveId());
    if (!special || special.mode === 'blackout') {
      return { screenNumber, items: [] };
    }

    const assignment = (special.assignments || [])
      .find(item => Number.parseInt(item.screenNumber, 10) === screenNumber);
    return { screenNumber, items: this._mediaItemsForAssignment(assignment) };
  }

  _parseScreenTargetFromText(text) {
    const match = String(text || '').replace(/\s+/g, '').match(/([一二两三四五六七八九十\d]+)号?屏/);
    if (!match) return null;
    const screenNumber = this._parseChineseNumber(match[1]);
    return Number.isFinite(screenNumber) && screenNumber > 0 ? screenNumber : null;
  }

  _removeScreenTargetFromText(text) {
    return String(text || '').replace(/[一二两三四五六七八九十\d]+号?屏/g, '').trim();
  }

  _parseDirectControlAction(text) {
    const value = String(text || '').trim();
    if (!value) return null;
    const normalized = value.replace(/\s+/g, '').replace(/[，。！？,.!?]/g, '');

    if (/(关闭|关掉|退出|结束|停止).*(ppt|PPT|演示|放映)/.test(value)) {
      return { type: 'close_ppt', reply: '已关闭PPT。' };
    }
    if (/^(返回|回去|回首页|返回首页|回到首页|返回主页|回到主页|返回桌面|回到桌面|回桌面|返回待机|回到待机)$/.test(normalized)) {
      return { type: 'home', reply: '已返回首页。' };
    }
    if (/^(返回屏保|回到屏保|回屏保|屏保|返回黑屏|回到黑屏|黑屏|节能|节能模式|返回节能|回到节能)$/.test(normalized)) {
      return { type: 'blackout', reply: '已返回屏保。' };
    }
    if (/(下一页|下页|下一张|下一个|翻页|往后翻|后翻)/.test(value)) {
      return { type: 'next_slide', reply: '已切到下一页。' };
    }
    if (/(上一页|上页|上一张|上一个|往前翻|前翻|返回上一页)/.test(value)) {
      return { type: 'prev_slide', reply: '已切到上一页。' };
    }

    return null;
  }

  _parseOpenOrdinal(text) {
    const value = String(text || '').trim();
    const match = value.match(/^(?:(?:打开|播放|展示|选择|选)\s*)?第?\s*([一二两三四五六七八九十\d]+)\s*(?:个|项|条|号)?[。.!！]?$/);
    if (!match) return null;
    return this._parseChineseNumber(match[1]);
  }

  _parseChineseNumber(value) {
    const text = String(value || '').trim();
    if (/^\d+$/.test(text)) return parseInt(text, 10);
    const digits = { 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };
    if (text === '十') return 10;
    if (text.startsWith('十')) return 10 + (digits[text[1]] || 0);
    if (text.includes('十')) {
      const [tens, ones] = text.split('十');
      return (digits[tens] || 1) * 10 + (digits[ones] || 0);
    }
    return digits[text] || null;
  }

  _rememberMediaResults(target, source, items) {
    const keys = this._mediaKeys(target, source);
    for (const key of keys) this._mediaResultSets.set(key, items);
  }

  _getRememberedMediaResults(target, source) {
    const keys = this._mediaKeys(target, source);
    for (const key of keys) {
      const items = this._mediaResultSets.get(key);
      if (items?.length) return items;
    }
    return [];
  }

  _mediaKeys(target, source) {
    return [
      `target:${target || 'all'}`,
      `source:${source || 'unknown'}`,
      'global',
    ];
  }

  _conversationKey(clientInfo, source) {
    return String(clientInfo?.deviceId || source || clientInfo?.clientId || 'global');
  }

  _rememberConversationTopic(text, clientInfo, source) {
    const value = String(text || '').trim();
    if (!value) return;
    if (this._parseDirectControlAction(value)) return;
    if (this._parseOpenOrdinal(value)) return;
    if (weatherService.detect(value)) return;
    if (this._isMediaSearchText(value)) return;
    if (/(切换|返回|回首页|回到首页|打开第|上一页|下一页|播放讲解词)/.test(value)) return;

    const topic = this._extractConversationTopic(value);
    if (!topic) return;

    const key = this._conversationKey(clientInfo, source);
    this._conversationTopics.set(key, {
      topic,
      updatedAt: Date.now(),
    });
  }

  _extractConversationTopic(text) {
    const value = String(text || '')
      .replace(/[？?！!。,.，、]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    if (!value) return '';

    const patterns = [
      /^(?:请问|帮我|给我)?(.{2,40}?)(?:是什么|什么意思|主要解决了什么问题|主要解决什么问题|解决了什么|有什么用|能做什么|有哪些特点|应用场景|介绍一下|介绍下|讲一下|说一下)/,
      /^(?:请问|帮我|给我)?(?:介绍一下|介绍下|讲一下|说一下)(.{2,40})$/,
    ];

    for (const pattern of patterns) {
      const match = value.match(pattern);
      const topic = this._cleanConversationTopic(match?.[1]);
      if (topic) return topic;
    }

    const standaloneTopic = this._cleanConversationTopic(value);
    if (this._looksLikeStandaloneTopic(standaloneTopic)) return standaloneTopic;

    return '';
  }

  _cleanConversationTopic(topic) {
    let value = String(topic || '')
      .replace(/^(请问|帮我|给我|关于|一下|这个|那个)+/, '')
      .replace(/(一下|这个|那个|相关|文件|资料|素材|PPT|ppt|视频|图片|图像|照片)+$/g, '')
      .replace(/\s+/g, '')
      .trim();
    if (value.length < 2 || value.length > 30) return '';
    if (/^(有哪些|有什么|多少|怎么|如何|为什么|可以|能不能)$/.test(value)) return '';
    return value;
  }

  _looksLikeStandaloneTopic(topic) {
    const value = String(topic || '').trim();
    if (value.length < 2 || value.length > 20) return false;
    if (/(是什么|什么意思|有哪些|有什么|多少|怎么|如何|为什么|可以|能不能|文件|资料|素材|PPT|ppt|视频|图片|照片)/.test(value)) return false;
    if (/^(你好|您好|返回|关闭|打开|切换|上一页|下一页|相关|这个|那个|第\d+个)$/.test(value)) return false;
    return /[\u4e00-\u9fa5A-Za-z0-9]/.test(value);
  }

  _resolveFollowupMediaText(text, clientInfo, source) {
    const value = String(text || '').trim();
    if (!value || this._parseScreenTargetFromText(value)) {
      return { text: value, applied: false, topic: '' };
    }
    if (!this._isGenericFollowupMediaSearch(value)) {
      return { text: value, applied: false, topic: '' };
    }

    const entry = this._conversationTopics.get(this._conversationKey(clientInfo, source));
    if (!entry?.topic || Date.now() - entry.updatedAt > conversationSessionStore.idleMs) {
      return { text: value, applied: false, topic: '' };
    }

    return {
      text: `${entry.topic} ${value}`,
      applied: true,
      topic: entry.topic,
    };
  }

  _isGenericFollowupMediaSearch(text) {
    const normalized = String(text || '')
      .replace(/[？?！!。,.，、]/g, '')
      .replace(/\s+/g, '');
    if (!normalized) return false;
    if (/^(相关)?(文件|资料|素材|PPT|ppt|视频|图片|图像|照片)$/.test(normalized)) return true;
    if (/^(有)?哪些(相关)?(文件|资料|素材|PPT|ppt|视频|图片|图像|照片)$/.test(normalized)) return true;
    if (/^(有)?哪些(相关)?(文件|资料|素材|PPT|ppt|视频|图片|图像|照片)(可以)?(看|看的|观看|播放|展示)?$/.test(normalized)) return true;
    if (/^(有)?什么(相关)?(文件|资料|素材|PPT|ppt|视频|图片|图像|照片)$/.test(normalized)) return true;
    if (/^(有)?什么(相关)?(文件|资料|素材|PPT|ppt|视频|图片|图像|照片)(可以)?(看|看的|观看|播放|展示)?$/.test(normalized)) return true;
    if (/^(有没有|是否有|找一下|找找|搜索)(相关)?(文件|资料|素材|PPT|ppt|视频|图片|图像|照片)$/.test(normalized)) return true;

    const remaining = normalized
      .replace(/(有没有|是否有|有哪些|有什么|哪些|什么|有啥|有|找一下|找找|搜索|相关|关于|的|可以看|可以看的|可以|看|看的|文件|资料|素材|PPT|ppt|视频|图片|图像|照片|可看|观看|播放|展示)/g, '');
    return remaining.length === 0;
  }

  _finalRouteFor(answerSource, context = {}) {
    const source = String(answerSource || '');
    if (source === '联网搜索异常') return 'WEB_FAILED';
    if (source === '联网搜索') {
      return context.webSearchStatus === 'ANSWERED' ? 'WEB_ANSWERED' : 'WEB_ABSTAINED';
    }
    if (context.localKnowledgeStatus === 'ABSTAINED') return 'LOCAL_KB_ABSTAINED';
    if (source === '日期时间') return 'DATETIME_ANSWERED';
    if (/QA|知识库|兜底库/.test(source)) return 'LOCAL_KB_ANSWERED';
    if (/RAG/.test(source)) return 'RAG_ANSWERED';
    if (/LLM|大模型/.test(source)) return 'LLM_DIRECT';
    return source ? 'OTHER_ANSWERED' : 'UNKNOWN';
  }

  async _processModelResult(clientInfo, result, seq, options = {}) {
    const {
      echoToRequester = true,
      fallbackQuestion = '',
      fallbackTarget = 'all',
      source = clientInfo.type || 'unknown',
      durationMs = null,
      answerSource = '',
    } = options;
    const context = result?.context || {};
    let reply = String(result?.reply || '').trim();
    const actionsFromResult = Array.isArray(result?.actions) ? result.actions : [];
    if (!reply && !actionsFromResult.length && fallbackQuestion) {
      reply = this._buildHelpfulFallbackReply(fallbackQuestion);
      context.fallback = true;
      context.emptyResultFallback = true;
    }

    if (reply && echoToRequester && clientInfo.clientId) {
      wsServer.sendTo(clientInfo.clientId, {
        type: 'reply',
        text: reply,
        seq,
        serverTime: Date.now(),
        isVoice: context.isVoice,
      });
    }

    const enrichedResult = context.attachRelatedMedia
      ? await this._attachRelatedMediaToAnswerResult(result, {
        question: fallbackQuestion,
        target: fallbackTarget,
        source,
      })
      : result;
    const enrichedContext = enrichedResult?.context || context;
    const enrichedActions = Array.isArray(enrichedResult?.actions) ? enrichedResult.actions : actionsFromResult;

    const rawActionsToDispatch = enrichedActions.length
      ? enrichedActions
      : reply
        ? [{
            type: 'show_qa',
            target: fallbackTarget,
            params: { question: fallbackQuestion, answer: reply },
          }]
        : [];
    const requestPolicy = this._requestPolicies.get(seq) || {};
    const actionsToDispatch = requestPolicy.suppressActions
      ? []
      : this._withStreamingPresentation(rawActionsToDispatch, seq);
    this._emitAnswerProgress(seq, 'STREAMING', {
      message: '正在输出回答…',
      answer: reply,
      clientInfo,
    });
    const resolvedAnswerSource = answerSource || enrichedContext?.answerSource || this._inferAnswerSource(actionsToDispatch, enrichedContext);
    const finalRoute = enrichedContext.finalRoute || this._finalRouteFor(resolvedAnswerSource, enrichedContext);
    const conversationTurn = this._conversationTurnFor(seq);
    const conversationId = conversationTurn?.conversationId || '';
    const routeTrace = [
      ...(this._routeTraces.get(seq) || []),
      ...(Array.isArray(enrichedContext.routeTrace) ? enrichedContext.routeTrace : []),
    ];
    if (!routeTrace.some(item => item.handler === 'final_route')) {
      routeTrace.push({
        handler: 'final_route',
        matched: true,
        status: finalRoute,
        reason: resolvedAnswerSource || '',
      });
    }
    enrichedContext.finalRoute = finalRoute;
    enrichedContext.routeTrace = routeTrace;
    enrichedContext.conversationId = conversationId;

    for (const action of actionsToDispatch) {
      this._dispatchAction(action, clientInfo, seq, reply);
    }

    const recordedEvent = usageStore.recordInteraction({
      seq,
      source,
      deviceId: clientInfo.deviceId,
      deviceType: clientInfo.type,
      target: fallbackTarget,
      question: fallbackQuestion,
      answer: reply,
      actions: actionsToDispatch,
      answerSource: resolvedAnswerSource,
      finalRoute,
      routeTrace,
      webSources: enrichedContext?.sources || [],
      knowledgeSources: enrichedContext?.knowledgeSources || [],
      webSearchQuery: enrichedContext?.webSearchQuery || '',
      webSearchQueries: enrichedContext?.webSearchQueries || [],
      webSearchStatus: enrichedContext?.webSearchStatus || '',
      webSearchClaims: enrichedContext?.webSearchClaims || [],
      webSearchUncertainties: enrichedContext?.webSearchUncertainties || [],
      webSearchVerification: enrichedContext?.webSearchVerification || null,
      webSearchRetrieval: enrichedContext?.webSearchRetrieval || null,
      webSearchFallbackReason: enrichedContext?.webSearchFallbackReason || '',
      webSearchModelRaw: enrichedContext?.webSearchModelRaw || '',
      webSearchRepairRaw: enrichedContext?.webSearchRepairRaw || '',
      webSearchVerifierRaw: enrichedContext?.webSearchVerifierRaw || '',
      knowledgeUnitId: enrichedContext?.currentKnowledgeUnitId || '',
      pptFileName: enrichedContext?.currentPptFileName || '',
      pptTitle: enrichedContext?.currentPptTitle || '',
      pptSlideNumber: enrichedContext?.currentPptSlideNumber || null,
      qaScopedToCurrentUnit: enrichedContext?.qaScopedToCurrentUnit === true,
      qaCurrentUnitRequired: enrichedContext?.qaCurrentUnitRequired === true,
      technicalFallback: enrichedContext?.technicalFallback === true,
      refusalFallback: enrichedContext?.refusalFallback === true,
      contextEligible: enrichedContext?.contextEligible !== false,
      failureCode: enrichedContext?.failureCode || '',
      conversationId,
      ok: true,
      durationMs,
    });

    wsServer.pushToAdmin({
      type: 'conversation_log',
      requestId: answerProgressTracker.get(seq)?.requestId || '',
      eventId: recordedEvent.id,
      time: recordedEvent.time,
      deviceId: clientInfo.deviceId,
      deviceType: clientInfo.type,
      source,
      target: fallbackTarget,
      input: fallbackQuestion,
      reply,
      actions: actionsToDispatch,
      seq,
      answerSource: resolvedAnswerSource,
      finalRoute,
      routeTrace,
      webSources: enrichedContext?.sources || [],
      knowledgeSources: enrichedContext?.knowledgeSources || [],
      webSearchQuery: enrichedContext?.webSearchQuery || '',
      webSearchStatus: enrichedContext?.webSearchStatus || '',
      webSearchVerificationDecision: enrichedContext?.webSearchVerification?.decision || '',
      knowledgeUnitId: enrichedContext?.currentKnowledgeUnitId || '',
      pptFileName: enrichedContext?.currentPptFileName || '',
      pptTitle: enrichedContext?.currentPptTitle || '',
      pptSlideNumber: enrichedContext?.currentPptSlideNumber || null,
      qaScopedToCurrentUnit: enrichedContext?.qaScopedToCurrentUnit === true,
      qaCurrentUnitRequired: enrichedContext?.qaCurrentUnitRequired === true,
      technicalFallback: enrichedContext?.technicalFallback === true,
      refusalFallback: enrichedContext?.refusalFallback === true,
      contextEligible: enrichedContext?.contextEligible !== false,
      failureCode: enrichedContext?.failureCode || '',
      conversationId,
    });

    if (conversationId) {
      contextResolver.complete(conversationId, {
        question: fallbackQuestion,
        answer: reply,
        answerSource: resolvedAnswerSource,
        finalRoute,
        ok: true,
        technicalFallback: enrichedContext?.technicalFallback === true,
        refusalFallback: enrichedContext?.refusalFallback === true,
        contextEligible: enrichedContext?.contextEligible !== false,
      });
    }

    if (requestPolicy.clientMode === 'ROBOT') {
      this._completedOutcomes.set(seq, {
        reply,
        answerSource: resolvedAnswerSource,
        finalRoute,
        context: enrichedContext,
        routeTrace,
      });
    }

    this._emitAnswerProgress(seq, 'COMPLETED', {
      message: '回答完成',
      answer: reply,
      clientInfo,
    });

    this._routeTraces.delete(seq);
    this._conversationTurns.delete(seq);
    this._requestPolicies.delete(seq);

    return {
      reply,
      actions: actionsToDispatch,
      context: enrichedContext,
      answerSource: resolvedAnswerSource,
    };
  }

  _answerSourceForQaMatch(match, manualMatch) {
    if (!match) return '';
    if (manualMatch && match.item?.id === manualMatch.item?.id) return 'QA';
    if (match.vector) return match.reranked ? 'ChromaDB QA+AI' : 'ChromaDB QA';
    if (match.reranked) return 'QA+AI';
    return 'QA';
  }

  async _attachRelatedMediaToAnswerResult(result, { question, target, source } = {}) {
    // A knowledge answer must remain an answer. Explicit file-search requests
    // already use their own route; do not replace QA with broad media matches.
    return result;
  }
  _inferAnswerSource(actions = [], context = {}) {
    if (context?.fallback) return '规则兜底';
    if (!actions?.length) return 'LLM';
    if (actions.some(action => action?.type === 'show_media_results')) return '文件检索';
    if (actions.some(action => action?.type === 'play_ppt' || action?.type === 'play_video' || action?.type === 'show_doc')) return '文件打开';
    if (actions.some(action => action?.type === 'show_qa')) return 'LLM';
    return '控制';
  }

  _knowledgeSourcesFromItems(items = []) {
    const seen = new Set();
    const result = [];
    for (const item of items || []) {
      const raw = item?.raw_json || item?.rawJson || {};
      const frontmatter = raw?.frontmatter || {};
      const fileName = String(
        item?.sourceFile
        || item?.file
        || item?.fileName
        || item?.resourceId
        || raw?.sourceFile
        || frontmatter?.resource
        || raw?.file
        || '',
      ).trim().replace(/\\/g, '/').split('/').pop();
      if (!fileName) continue;
      const pageValue = item?.sourcePage ?? item?.pageNumber ?? raw?.sourcePage ?? raw?.page_number ?? null;
      const page = Number(pageValue) > 0 ? Number(pageValue) : null;
      const key = `${fileName.toLowerCase()}#${page || ''}`;
      if (seen.has(key)) continue;
      seen.add(key);
      result.push({
        fileName,
        page,
        type: /\.pptx?$/i.test(fileName) ? 'PPT' : 'DOCUMENT',
      });
      if (result.length >= 8) break;
    }
    return result;
  }

  _dispatchAction(action, clientInfo, seq, replyText = '') {
    if (this._requestPolicies.get(seq)?.suppressActions) return;
    // Fence at the last dispatch boundary, including delayed LLM/web results.
    // Split broadcasts so an interrupted screen does not suppress other screens.
    const interruptedTargets = this._targetScreenNumbers(action.target || 'all')
      .filter(target => speechInterruption.isStale(target, seq));
    if (interruptedTargets.length) {
      if ((action.target || 'all') === 'all' && action.type !== 'switch_special') {
        for (const target of this._targetScreenNumbers('all')) {
          if (!speechInterruption.isStale(target, seq)) {
            this._dispatchAction({ ...action, target }, clientInfo, seq, replyText);
          }
        }
      }
      console.log(`[SpeechInterrupt] dropped stale action seq=${seq} action=${action.type} targets=${interruptedTargets.join(',')}`);
      return;
    }
    this._updateCurrentPptStateForAction(action);
    this._updateScreenModeForAction(action);

    if (action.type === 'switch_special') {
      const name = action.params?.name || action.params?.special || action.params?.specialName;
      if (!name) {
        console.warn(`[Router] [seq=${seq}] switch_special ignored: missing params.name`);
        return;
      }
      try {
        const result = this.applySpecial(name, { clientInfo, seq, source: clientInfo.type || 'model' });
        console.log(`[Router] [seq=${seq}] switched special by model action: ${result.special.name}`);
      } catch (err) {
        console.warn(`[Router] [seq=${seq}] switch_special failed: ${err.message}`);
      }
      return;
    }

    const params = {
      ...(['play_ppt', 'ppt_speak_commentary', 'speak_ppt_commentary'].includes(action.type)
        ? this._commentaryParamsForTarget(action.target || 'all')
        : {}),
      ...(action.params || {}),
    };

    const cmd = {
      type: 'command',
      action: action.type,
      params,
      text: params.text || action.text || '',
      replyText,
      from: clientInfo.deviceId,
      seq,
      serverTime: Date.now(),
    };

    const target = action.target || 'all';
    if (target === 'all') {
      const count = wsServer.pushToClient(cmd);
      console.log(`[Router] [seq=${seq}] action broadcast to ${count} clients: ${action.type}`);
      return;
    }

    if (typeof target === 'number' || /^\d+$/.test(String(target))) {
      const device = deviceManager.getByScreenNumber(parseInt(target, 10));
      if (device && device.online) {
        const sent = this._sendToDevice(device.id, cmd);
        console.log(`[Router] [seq=${seq}] action to ${target}号屏: ${action.type} (${sent ? 'ok' : 'failed'})`);
      } else if (clientInfo.clientId) {
        wsServer.sendTo(clientInfo.clientId, {
          type: 'reply',
          text: `${target}号屏不在线`,
          seq,
          serverTime: Date.now(),
        });
      }
      return;
    }

    wsServer.sendTo(target, cmd);
  }

  _updateCurrentPptStateForAction(action) {
    const type = action?.type;
    if (!type) return;

    const targets = this._targetScreenNumbers(action.target || 'all');
    if (!targets.length) return;

    if (type === 'play_ppt') {
      const fileName = this._baseFileName(action.params?.file || action.file || action.params?.name || '');
      if (!fileName) return;
      for (const screenNumber of targets) {
        this._currentPptStates.set(screenNumber, { fileName, slideNumber: 1, updatedAt: Date.now() });
      }
      return;
    }

    if (['next_slide', 'ppt_next', 'next_page', 'ppt_next_page'].includes(type)) {
      for (const screenNumber of targets) {
        const state = this._currentPptStates.get(screenNumber);
        if (state) this._currentPptStates.set(screenNumber, { ...state, slideNumber: Math.max(1, (state.slideNumber || 1) + 1), updatedAt: Date.now() });
      }
      return;
    }

    if (['prev_slide', 'ppt_prev', 'prev_page', 'ppt_prev_page'].includes(type)) {
      for (const screenNumber of targets) {
        const state = this._currentPptStates.get(screenNumber);
        if (state) this._currentPptStates.set(screenNumber, { ...state, slideNumber: Math.max(1, (state.slideNumber || 1) - 1), updatedAt: Date.now() });
      }
      return;
    }

    if (['goto_slide', 'ppt_goto', 'goto_page'].includes(type)) {
      const slideNumber = Number.parseInt(action.slide || action.params?.slide || action.params?.page, 10);
      if (!Number.isFinite(slideNumber) || slideNumber <= 0) return;
      for (const screenNumber of targets) {
        const state = this._currentPptStates.get(screenNumber);
        if (state) this._currentPptStates.set(screenNumber, { ...state, slideNumber, updatedAt: Date.now() });
      }
      return;
    }

    if (['close_ppt', 'ppt_close', 'home', 'blackout', 'black_screen', 'energy_saving'].includes(type)) {
      for (const screenNumber of targets) this._currentPptStates.delete(screenNumber);
    }
  }

  _baseFileName(value) {
    const text = String(value || '').trim();
    if (!text) return '';
    const normalized = decodeURIComponent(text).replace(/\\/g, '/');
    return normalized.split('/').pop() || normalized;
  }

  _updateScreenModeForAction(action) {
    const type = action?.type;
    const target = action?.target || 'all';
    if (!type) return;

    if (type === 'show_media_results' || type === 'show_special_file_strip') {
      let items = [];
      try {
        items = JSON.parse(action.params?.items || '[]');
      } catch {
        items = [];
      }
      this._setFilePickerState(target, items, action.params?.selectedIndex || 0);
      return;
    }

    if (type === 'special_file_next') {
      this._moveFilePickerSelection(target, 1);
      return;
    }
    if (type === 'special_file_prev') {
      this._moveFilePickerSelection(target, -1);
      return;
    }
    if (type === 'special_file_open_selected') {
      const item = this._selectedFilePickerItem(target);
      this._clearFilePickerState(target);
      this._setScreenMode(target, item?.type === 'ppt' ? 'ppt' : 'normal');
      return;
    }

    if (type === 'play_ppt') {
      this._clearFilePickerState(target);
      this._setScreenMode(target, 'ppt');
      return;
    }

    if (type === 'show_qa' || type === 'pending_qa') {
      this._clearFilePickerState(target);
      for (const screenNumber of this._targetScreenNumbers(target)) {
        const view = deviceManager.getByScreenNumber(screenNumber)?.status?.view || '';
        this._screenModes.set(screenNumber, String(view).toLowerCase() === 'ppt' ? 'ppt' : 'normal');
      }
      return;
    }

    if (['play_video', 'show_doc', 'home', 'blackout', 'black_screen', 'energy_saving', 'close_ppt'].includes(type)) {
      this._clearFilePickerState(target);
      this._setScreenMode(target, 'normal');
    }
  }

  _sendToDevice(deviceId, message) {
    for (const [ws, info] of wsServer.clients) {
      if (info.deviceId === deviceId && ws.readyState === ws.OPEN) {
        ws.send(JSON.stringify(message));
        return true;
      }
    }
    return false;
  }

  _buildContext() {
    const screens = deviceManager.getOnlineScreens();
    const files = fileServer._scanFiles ? fileServer._scanFiles(config.MEDIA_DIR) : [];

    let ctx = '';
    ctx += `\n【在线屏幕】共 ${screens.length} 个\n`;
    for (const screen of screens) {
      ctx += `- ${screen.screenNumber}号屏 (设备ID: ${screen.deviceId})\n`;
    }

    ctx += `\n【可用媒体文件】共 ${files.length} 个\n`;
    for (const file of files) {
      ctx += `- ${file.path} [${file.type}] 本地路径: ${file.localPath}\n`;
    }
    ctx += `\n图片/视频使用 HTTP URL: ${config.PUBLIC_FILE_BASE_URL}/文件名\n`;
    ctx += 'PPT/文档使用 localPath 字段。\n';
    const specials = specialConfigStore.list();
    ctx += `\n【可用专场】共 ${specials.length} 个\n`;
    for (const special of specials) {
      const screenCount = Array.isArray(special.assignments) ? special.assignments.length : 0;
      ctx += `- ${special.name} (${screenCount} 个屏幕配置)\n`;
    }
    ctx += usageStore.getContextSummary();
    return ctx;
  }

  _ensureRegistered(clientInfo, seq) {
    if (clientInfo.deviceId) return true;
    wsServer.sendTo(clientInfo.clientId, { type: 'error', code: 'NOT_REGISTERED', message: '请先注册设备', seq });
    return false;
  }

  _recordFailure({ seq, source, deviceId, deviceType, target, question, error, startedAt }) {
    const conversationId = this._conversationTurnFor(seq)?.conversationId || '';
    usageStore.recordInteraction({
      seq,
      source,
      deviceId,
      deviceType,
      target,
      question,
      conversationId,
      error,
      ok: false,
      durationMs: Date.now() - startedAt,
    });
  }
}

export const messageRouter = new MessageRouter();
export default messageRouter;
