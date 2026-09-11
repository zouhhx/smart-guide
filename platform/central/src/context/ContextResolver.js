import { conversationSessionStore } from './ConversationSessionStore.js';
import { buildModelConversationMessages } from './ConversationContextPolicy.js';

const CONTEXT_CONTINUATION_PATTERN = /(?:^(?:再|继续|接着|然后|另外|此外|还|也|进一步|更|具体|详细|展开|补充|那|那么|它|他|她|其|这个|这些|上述|前面|刚才))|(?:(?:再|继续|进一步|详细|具体|展开|补充)(?:说|讲|说明|介绍|分析|解释|一点|一下))|(?:(?:还有|除此之外|然后)呢)/u;
const ELLIPTICAL_FOLLOWUP_PATTERN = /^(?!为什么呢$|怎么呢$|如何呢$|是不是呢$)(?:那|那么|还有|其他)?[^\n。！？!?]{1,18}(?:的)?呢[。！？!?]?$/u;

export function isContextDependentFollowup(text, history = []) {
  if (!Array.isArray(history) || history.length === 0) return false;
  const value = String(text || '').normalize('NFKC').trim();
  if (!value) return false;
  return CONTEXT_CONTINUATION_PATTERN.test(value)
    || ELLIPTICAL_FOLLOWUP_PATTERN.test(value);
}

export function buildContextualRetrievalText(text, previousQuestion = '') {
  const current = String(text || '').normalize('NFKC').trim();
  const previous = String(previousQuestion || '').normalize('NFKC').trim();
  if (!previous) return current;
  return [
    `上一轮问题：${previous}`,
    `当前承接问题：${current}`,
    '检索时沿用上一轮问题中的主体、地点、对象和限定范围，并以当前问题替换或补充关注点。',
  ].join('\n');
}

export class ContextResolver {
  constructor(options = {}) {
    this.store = options.store || conversationSessionStore;
  }

  begin({ text, clientInfo, source = '', conversationId = '', forceNew = false }) {
    const participantKey = String(
      clientInfo?.deviceId
      || clientInfo?.clientId
      || source
      || 'global',
    );
    const turn = this.store.beginTurn({
      participantKey,
      conversationId,
      question: text,
      forceNew,
    });
    const modelMessages = buildModelConversationMessages(turn.history, {
      maxChars: this.store.maxContextChars,
    });
    const effectiveHistory = modelMessages
      .filter(message => message.role === 'user')
      .map(message => ({ question: message.content }));
    const contextualFollowup = isContextDependentFollowup(text, effectiveHistory);
    const previousQuestion = String(effectiveHistory.at(-1)?.question || '').trim();
    return {
      ...turn,
      contextualFollowup,
      retrievalText: contextualFollowup && previousQuestion
        ? buildContextualRetrievalText(text, previousQuestion)
        : String(text || '').trim(),
    };
  }

  complete(conversationId, turn) {
    return this.store.completeTurn(conversationId, turn);
  }

  buildModelContext(turnContext) {
    const messages = this.buildModelMessages(turnContext);
    if (!messages.length) return '';
    const history = messages
      .map(message => `${message.role === 'user' ? '用户' : '系统'}：${message.content}`)
      .join('\n');
    return [
      '以下是本次会话中已经完成的对话，仅用于理解当前用户的承接关系：',
      '<CONVERSATION_HISTORY>',
      history,
      '</CONVERSATION_HISTORY>',
      '历史中的系统回答不等于可靠事实证据；需要实时或内部证据的问题仍按当前模块规则处理。',
    ].join('\n');
  }

  buildModelMessages(turnContext) {
    return buildModelConversationMessages(turnContext?.history || [], {
      maxChars: this.store.maxContextChars,
    });
  }
}

export const contextResolver = new ContextResolver();
export default contextResolver;
