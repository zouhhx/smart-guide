import { classifyCurrentDateTimeQuery } from '../router/CurrentDateTimeIntent.js';

const CURRENT_ANCHOR = /(?:现在|当前|目前|现阶段|眼下|截至(?:今天|现在|目前|当前|本日)|截止到(?:今天|现在|目前|当前))/i;
const QUESTION_SIGNAL = /[?？]|(?:谁|哪(?:个|些|家|位|款|部|一)|什么|多少|几(?:点|个|家|位|款|部)?|怎么样|怎样|如何|是否|有没有|有吗|了吗|何时|什么时候|哪里|哪儿|情况|状态|进展)(?:呢|吗|了)?$/i;
const CURRENT_FACT_SHAPE = [
  /(?:现在|当前|目前|现阶段|眼下)(?:的)?[^，。！？?]{1,80}(?:是|为|有|由|谁|哪|什么|多少|几|如何|怎样|怎么样|是否|有没有|情况|状态|进展|吗|呢|了)/i,
  /[^，。！？?]{2,80}(?:现在|当前|目前|现阶段|眼下)(?:是|为|有|由|谁|哪|什么|多少|几|如何|怎样|怎么样|是否|有没有|还|已经|能否|可以|情况|状态|进展)/i,
  /(?:截至|截止到)(?:今天|现在|目前|当前|本日)[^，。！？?]{1,100}/i,
];
const NON_FACT_REQUEST = /^(?:现在|当前|目前)?(?:请|麻烦)?(?:帮我|给我|替我)?(?:写|改写|润色|翻译|总结|生成|创作|设计|分析|解释|算一下|计算)/i;
const DETERMINISTIC_WEATHER = /(?:天气|气温|温度|降雨|下雨|空气质量|天气预报)/i;
const LOCAL_CONTEXT = /(?:当前|现在|目前)(?:的)?(?:ppt|PPT|页面|这一页|这页|屏幕|专场|展项|方案|设备|视频|图片)/;
const CONTROL_REQUEST = /(?:打开|播放|暂停|关闭|切换|跳转|翻页|下一页|上一页|黑屏|唤醒|启动|停止)(?:一下|当前|现在|目前)?/;

export function detectFreshnessIntent(value) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  if (!text || !CURRENT_ANCHOR.test(text)) return null;
  if (NON_FACT_REQUEST.test(text) || classifyCurrentDateTimeQuery(text).matched || DETERMINISTIC_WEATHER.test(text)
    || LOCAL_CONTEXT.test(text) || CONTROL_REQUEST.test(text)) return null;

  const hasFactShape = CURRENT_FACT_SHAPE.some(pattern => pattern.test(text));
  if (!hasFactShape && !QUESTION_SIGNAL.test(text)) return null;

  return {
    mode: 'MUST_WEB',
    reason: 'EXPLICIT_CURRENT_FACT',
    confidence: hasFactShape && QUESTION_SIGNAL.test(text) ? 0.98 : 0.9,
    recencyDays: 30,
  };
}

export function isTimeSensitiveQuestion(value) {
  return Boolean(detectFreshnessIntent(value))
    || /(?:最新|最近|近期|今天|昨天|本周|本月|实时|刚刚|截至|截止到|现任|即将)/i.test(String(value || ''));
}

export default { detectFreshnessIntent, isTimeSensitiveQuestion };
