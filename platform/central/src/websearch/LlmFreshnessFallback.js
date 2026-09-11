import { isDynamicEventQuestion } from './DynamicEventIntentDetector.js';

export function shouldRetryWebAfterLlm(question, reply) {
  const value = String(reply || '').trim();
  if (!value) return false;
  const genericStaleness = /(知识(?:截止|更新|库)|无法预测(?:未来)?|不能预测(?:未来)?|未来的信息|尚未公布|尚未揭晓|需要等到|无法提供.{0,20}(?:最新|当前|实时|20\d{2})|没有实时信息|建议(?:您)?查询.{0,16}(?:最新|官方)|查询更准确的信息)/i.test(value);
  const dynamicEventUncertainty = isDynamicEventQuestion(question)
    && /(?:没有|暂无|尚无|未有).{0,16}(?:公开信息|官方消息|明确消息)|需要关注.{0,24}(?:官方公告|权威财经|最新进展)|无法确认.{0,16}(?:状态|进展|时间)/i.test(value);
  if (!genericStaleness && !dynamicEventUncertainty) {
    return false;
  }
  return !/(写一段|改写|翻译|润色|总结|生成文案|创作)/.test(String(question || ''));
}

export default { shouldRetryWebAfterLlm };
