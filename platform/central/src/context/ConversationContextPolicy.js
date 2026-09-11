const TECHNICAL_FALLBACK_PATTERNS = [
  /^当前模型暂时没有返回有效答案，请稍后再问一次[。！!]?$/u,
  /^这个问题我还不会呢，请问一下旁边的专家吧[。！!]?$/u,
  /^模型服务暂时不可用/u,
  /^系统暂时无法处理/u,
  /^请求(?:超时|失败)/u,
];

const PURE_REFUSAL_PATTERN = /(?:抱歉|对不起|无法回答|不能回答|无法确定|不知道|不清楚|还不会|没有找到|没找到|未找到|资料不足|资料中?没有(?:明确)?(?:说明|信息)|知识库中?(?:没有|未(?:明确)?(?:提及|说明|包含|提供))|当前问题超出能力范畴|旁边的专家)/u;
const USEFUL_CONTINUATION_PATTERN = /(?:但|不过|然而|同时|其中|目前|已经|已于|主要|包括|采用|提供|支持|可用于|意味着|显示|表明|确认|结论是)/u;
const CONTROL_SOURCE_PATTERN = /(?:控制|媒体|PPT|文件操作|专场切换|设备)/iu;

function normalize(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

export function isTechnicalFallbackReply(reply) {
  const value = normalize(reply);
  return !value || TECHNICAL_FALLBACK_PATTERNS.some(pattern => pattern.test(value));
}

export function classifyAssistantReply(reply) {
  const value = normalize(reply);
  if (!value) return { kind: 'EMPTY', useful: false, technicalFallback: true };
  if (isTechnicalFallbackReply(value)) {
    return { kind: 'TECHNICAL_FALLBACK', useful: false, technicalFallback: true };
  }
  if (!PURE_REFUSAL_PATTERN.test(value)) {
    return { kind: 'ANSWER', useful: true, technicalFallback: false };
  }

  const clauses = value.split(/[。！？!?；;]+/u).map(item => item.trim()).filter(Boolean);
  const nonRefusalClauses = clauses.filter(clause => !PURE_REFUSAL_PATTERN.test(clause));
  const contrastTail = value.match(/(?:但|不过|然而|同时|其中)(.{8,})/u)?.[1] || '';
  const hasUsefulContinuation = (contrastTail && /\d|是|为|在|由|将|已|确认|提供|支持/u.test(contrastTail))
    || nonRefusalClauses.some(clause =>
      clause.length >= 8 && (USEFUL_CONTINUATION_PATTERN.test(clause) || /\d|是|为|在|由|将|已/u.test(clause)));
  if (hasUsefulContinuation) {
    return { kind: 'PARTIAL_ANSWER', useful: true, technicalFallback: false };
  }
  return { kind: 'PURE_REFUSAL', useful: false, technicalFallback: false };
}

export function isTurnEligibleForModelContext(turn = {}) {
  if (turn.contextEligible === false || turn.ok === false || turn.technicalFallback === true || turn.refusalFallback === true) return false;
  if (CONTROL_SOURCE_PATTERN.test(String(turn.answerSource || ''))) return false;
  return classifyAssistantReply(turn.answer).useful;
}

export function buildModelConversationMessages(history = [], options = {}) {
  const maxChars = Number(options.maxChars) || 8000;
  const eligibleTurns = (Array.isArray(history) ? history : [])
    .filter(turn => isTurnEligibleForModelContext(turn));
  const selected = [];
  let usedChars = 0;
  for (let index = eligibleTurns.length - 1; index >= 0; index -= 1) {
    const turn = eligibleTurns[index];
    const question = normalize(turn.question);
    const answer = normalize(turn.answer);
    const size = question.length + answer.length;
    if (!question || !answer) continue;
    if (selected.length && usedChars + size > maxChars) break;
    selected.unshift(
      { role: 'user', content: question },
      { role: 'assistant', content: answer },
    );
    usedChars += size;
  }
  return selected;
}

export const conversationContextPolicy = {
  classifyAssistantReply,
  isTechnicalFallbackReply,
  isTurnEligibleForModelContext,
  buildModelConversationMessages,
};

export default conversationContextPolicy;
