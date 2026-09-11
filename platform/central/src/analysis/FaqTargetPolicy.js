export const FAQ_TARGET_COUNT = 100;

function cleanText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function questionKey(value) {
  return cleanText(value)
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[？?！!。,.，、；;：:\s“”"'‘’（）()[\]{}]/g, '');
}

const FAQ_INTENTS = new Set([
  'positioning', 'technical_term', 'painpoint', 'value', 'audience',
  'provider_capability', 'capability', 'case_detail', 'metric', 'landing',
  'comparison', 'boundary', 'commercial_boundary', 'security_boundary',
  'handoff', 'online_search',
]);

function characterDice(left, right) {
  const counts = new Map();
  for (const char of left) counts.set(char, (counts.get(char) || 0) + 1);
  let overlap = 0;
  for (const char of right) {
    const count = counts.get(char) || 0;
    if (!count) continue;
    overlap += 1;
    counts.set(char, count - 1);
  }
  return (2 * overlap) / Math.max(1, left.length + right.length);
}

function bigramJaccard(left, right) {
  const make = value => new Set(Array.from(
    { length: Math.max(0, value.length - 1) },
    (_, index) => value.slice(index, index + 2),
  ));
  const a = make(left);
  const b = make(right);
  if (!a.size || !b.size) return 0;
  const overlap = [...a].filter(value => b.has(value)).length;
  return overlap / (a.size + b.size - overlap);
}

export function isNearDuplicateQuestion(left, right) {
  const a = questionKey(left);
  const b = questionKey(right);
  if (!a || !b) return false;
  if (a === b) return true;
  if (Math.abs(a.length - b.length) > Math.max(a.length, b.length) * 0.2) return false;
  return characterDice(a, b) >= 0.94 && bigramJaccard(a, b) >= 0.55;
}

export function normalizeFaqItems(items = [], validPages = []) {
  const allowedPages = new Set((Array.isArray(validPages) ? validPages : []).map(Number));
  const seen = new Set();
  const seenQuestions = [];
  const normalized = [];
  for (const item of Array.isArray(items) ? items : []) {
    const question = cleanText(item?.question);
    const answer = cleanText(item?.answer);
    const sourcePage = Number(item?.sourcePage ?? item?.page);
    const key = questionKey(question);
    if (!question || !answer || !key || seen.has(key)) continue;
    if (seenQuestions.some(existing => isNearDuplicateQuestion(existing, question))) continue;
    if (allowedPages.size && !allowedPages.has(sourcePage)) continue;
    seen.add(key);
    seenQuestions.push(question);
    const rawIntent = cleanText(item?.intent).toLowerCase();
    const intent = FAQ_INTENTS.has(rawIntent) ? rawIntent : '';

    normalized.push({
      question,
      answer,
      keywords: Array.isArray(item?.keywords)
        ? [...new Set(item.keywords.map(cleanText).filter(Boolean))].slice(0, 8)
        : [],
      sourcePage,
      intent,
      handoffRequired: item?.handoffRequired === true || ['boundary', 'commercial_boundary', 'security_boundary', 'handoff'].includes(intent),
      sourceId: cleanText(item?.sourceId),
    });
  }
  return normalized;
}

export function mergeFaqItems(existing = [], incoming = [], validPages = [], target = FAQ_TARGET_COUNT) {
  return normalizeFaqItems([...existing, ...incoming], validPages).slice(0, target);
}

export function assertFaqTarget(items = [], target = FAQ_TARGET_COUNT) {
  if (items.length !== target) {
    throw new Error(`FAQ生成数量未达到硬性要求：需要 ${target} 条，实际 ${items.length} 条`);
  }
  return items;
}

export function recoverFaqItemsFromMalformedJson(value = '') {
  const source = String(value || '').replace(/```(?:json)?/gi, '').replace(/```/g, '');
  const faqKey = source.search(/"faqs"\s*:/i);
  const arrayStart = source.indexOf('[', faqKey >= 0 ? faqKey : 0);
  if (arrayStart < 0) return [];
  const recovered = [];
  let objectStart = -1;
  let depth = 0;
  let quoted = false;
  let escaped = false;
  for (let index = arrayStart + 1; index < source.length; index += 1) {
    const char = source[index];
    if (escaped) { escaped = false; continue; }
    if (char === '\\' && quoted) { escaped = true; continue; }
    if (char === '"') { quoted = !quoted; continue; }
    if (quoted) continue;
    if (char === '{') {
      if (depth === 0) objectStart = index;
      depth += 1;
      continue;
    }
    if (char === '}' && depth > 0) {
      depth -= 1;
      if (depth === 0 && objectStart >= 0) {
        try { recovered.push(JSON.parse(source.slice(objectStart, index + 1))); } catch {}
        objectStart = -1;
      }
    }
  }
  return recovered;
}
