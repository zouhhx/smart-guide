const DEFAULT_TIME_ZONE = 'Asia/Shanghai';

function zonedDateParts(value, timeZone) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) throw new Error('invalid temporal reference date');
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(date);
  return Object.fromEntries(parts.filter(item => item.type !== 'literal').map(item => [item.type, item.value]));
}

function calendarShift(parts, days) {
  const shifted = new Date(Date.UTC(
    Number(parts.year),
    Number(parts.month) - 1,
    Number(parts.day) + days,
  ));
  return {
    year: String(shifted.getUTCFullYear()),
    month: String(shifted.getUTCMonth() + 1).padStart(2, '0'),
    day: String(shifted.getUTCDate()).padStart(2, '0'),
  };
}

function isoDate(parts) {
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function chineseDate(parts) {
  return `${Number(parts.year)}年${Number(parts.month)}月${Number(parts.day)}日`;
}

function chineseMonth(parts) {
  return `${Number(parts.year)}年${Number(parts.month)}月`;
}

function weekRange(parts) {
  const utc = new Date(Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day)));
  const day = utc.getUTCDay() || 7;
  const start = calendarShift(parts, 1 - day);
  const end = calendarShift(parts, 7 - day);
  return `${chineseDate(start)}至${chineseDate(end)}`;
}

function uniqueNumbers(values) {
  return [...new Set(values.filter(Number.isFinite))];
}

export function resolveTemporalContext(question, options = {}) {
  const originalQuestion = String(question || '').trim();
  const timeZone = String(options.timeZone || DEFAULT_TIME_ZONE);
  const current = zonedDateParts(options.now || new Date(), timeZone);
  const currentYear = Number(current.year);
  const previous = calendarShift(current, -1);
  const next = calendarShift(current, 1);
  const explicitYears = [...originalQuestion.matchAll(/(?:19|20)\d{2}/g)].map(match => Number(match[0]));
  const targetYears = [...explicitYears];
  const relativeTerms = {};
  let resolvedQuestion = originalQuestion;

  const replacements = [
    [/截至(?:今天|本日)/g, `截至${chineseDate(current)}`, '截至今天', currentYear],
    [/截止到(?:今天|本日)/g, `截止到${chineseDate(current)}`, '截止到今天', currentYear],
    [/今天/g, chineseDate(current), '今天', currentYear],
    [/昨天/g, chineseDate(previous), '昨天', Number(previous.year)],
    [/明天/g, chineseDate(next), '明天', Number(next.year)],
    [/本月/g, chineseMonth(current), '本月', currentYear],
    [/本周/g, weekRange(current), '本周', currentYear],
    [/今年/g, `${currentYear}年`, '今年', currentYear],
    [/明年/g, `${currentYear + 1}年`, '明年', currentYear + 1],
    [/去年/g, `${currentYear - 1}年`, '去年', currentYear - 1],
  ];

  for (const [pattern, replacement, label, year] of replacements) {
    if (!pattern.test(resolvedQuestion)) continue;
    pattern.lastIndex = 0;
    resolvedQuestion = resolvedQuestion.replace(pattern, replacement);
    relativeTerms[label] = replacement;
    targetYears.push(year);
  }

  const hasCurrentAnchor = /(?:截至目前|截止到目前|截至现在|截止到现在|当前|目前|现在|近期|最近)/.test(originalQuestion);
  if (hasCurrentAnchor && !resolvedQuestion.includes(`基准日期：${isoDate(current)}`)) {
    resolvedQuestion = `${resolvedQuestion}（基准日期：${isoDate(current)}）`;
  }

  return {
    timeZone,
    currentDate: isoDate(current),
    currentYear,
    originalQuestion,
    resolvedQuestion,
    relativeTerms,
    targetYears: uniqueNumbers(targetYears),
    hasRelativeTemporal: Object.keys(relativeTerms).length > 0 || hasCurrentAnchor,
  };
}

export function classifyTemporalEvidence(text, temporalContext = {}) {
  const targetYears = uniqueNumbers((temporalContext.targetYears || []).map(Number));
  if (!targetYears.length) return 'UNKNOWN';
  const evidenceYears = uniqueNumbers(
    [...String(text || '').matchAll(/(?:19|20)\d{2}/g)].map(match => Number(match[0])),
  );
  if (!evidenceYears.length) return 'UNKNOWN';
  return evidenceYears.some(year => targetYears.includes(year)) ? 'MATCH' : 'OTHER_YEAR';
}

export default { resolveTemporalContext, classifyTemporalEvidence };
