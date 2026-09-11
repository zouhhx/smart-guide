const EVENT_RELATION = /(?:上市|发布|上线|推出|开通|通车|投产|交付|获批|批准|生效|实施|完成|发射|回收|试飞|启用|建成|部署|投入使用|启动|落地|并购|收购|融资|发售|开售|上映|开业|停运|复航|签约)/i;
const STATUS_OR_TIME_QUESTION = /(?:是否|有没有|有无|了吗|了没|何时|什么时候|哪天|几月几日|具体时间|进展|状态|时间表|计划|预计)/i;
const EVENT_ATTRIBUTE_QUESTION = /(?:哪(?:一)?型|什么型号|哪(?:一)?款|使用(?:了)?(?:哪|什么)|采用(?:了)?(?:哪|什么)|由谁|谁(?:负责|实施|完成|发起|执行)|在哪(?:里|儿)|何地|涉及哪些|包含哪些|分别是(?:谁|什么)|名称(?:是)?什么|结果(?:是)?什么)/i;
const NON_FACT_REQUEST = /(?:是什么意思|什么含义|如何理解|怎么操作|怎么办理|流程|条件|要求|教程|方法|方案|帮我|给我|替我).{0,20}(?:写|发布|上线|设计|生成|制作|介绍|解释)/i;
const CONTROL_OR_LOCAL_CONTEXT = /(?:打开|播放|暂停|关闭|切换|跳转|翻页|下一页|上一页|当前PPT|这页|专场)/i;

function compact(value) {
  return String(value || '').replace(/\s+/g, '').trim();
}

function extractSubject(text) {
  const relation = text.match(EVENT_RELATION);
  if (!relation || relation.index == null) return '';
  return text.slice(0, relation.index)
    .replace(/^(?:请问|想问|帮我查一下|查询一下|查一下|截至[^，。！？?]{0,18})/i, '')
    .replace(/(?:现在|当前|目前|近期|最近|最新|今年|本年|20\d{2}年?)/gi, '')
    .replace(/[，。！？、,.!?;；:"'“”‘’（）()[\]{}<>《》【】]/g, '')
    .trim();
}

function hasRecentTimeAnchor(text, now = new Date()) {
  if (/(?:今年|本年|本月|近期|最近|最新|今天|当前|目前|截至|截止到)/i.test(text)) return true;
  const currentYear = now.getFullYear();
  return [...text.matchAll(/20\d{2}/g)]
    .map(match => Number(match[0]))
    .some(year => year >= currentYear - 1);
}

export function detectDynamicEventIntent(value) {
  const text = compact(value);
  if (!text || !EVENT_RELATION.test(text)) return null;
  if (NON_FACT_REQUEST.test(text) || CONTROL_OR_LOCAL_CONTEXT.test(text)) return null;

  const asksStateOrTime = STATUS_OR_TIME_QUESTION.test(text);
  const asksRecentEventAttribute = hasRecentTimeAnchor(text) && EVENT_ATTRIBUTE_QUESTION.test(text);
  if (!asksStateOrTime && !asksRecentEventAttribute) return null;

  const subject = extractSubject(text);
  if (subject.length < 2) return null;

  return {
    mode: 'MUST_WEB',
    reason: asksStateOrTime ? 'DYNAMIC_EVENT_STATE' : 'TIME_BOUND_EVENT_FACT',
    confidence: 0.96,
    recencyDays: 0,
    subject,
    relation: text.match(EVENT_RELATION)?.[0] || '',
    requestedAttribute: asksRecentEventAttribute
      ? text.match(EVENT_ATTRIBUTE_QUESTION)?.[0] || ''
      : '',
  };
}

export function isDynamicEventQuestion(value) {
  return Boolean(detectDynamicEventIntent(value));
}

export default { detectDynamicEventIntent, isDynamicEventQuestion };
