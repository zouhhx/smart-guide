function text(value) {
  return String(value ?? '').trim();
}

export function normalizeUnitId(value) {
  return text(value).toUpperCase();
}

export function extractKnowledgeUnitId(item = {}) {
  const raw = item.raw_json || item.rawJson || {};
  const frontmatter = raw.frontmatter || raw.metadata?.frontmatter || {};
  const vectorMetadata = raw.vector?.metadata || {};
  const direct = [
    item.unitId,
    item.unit_id,
    raw.unitId,
    raw.unit_id,
    frontmatter.unitId,
    frontmatter.unit_id,
    vectorMetadata.unitId,
    vectorMetadata.unit_id,
  ].find(value => text(value));
  if (direct) return normalizeUnitId(direct);

  const values = [
    item.id,
    item.resource_id,
    ...(item.tags || []),
    ...(item.keywords || []),
  ].map(text).filter(Boolean);
  for (const value of values) {
    const match = value.match(/UNIT-[A-Z0-9]+(?:-[A-Z0-9]+)*?-\d{3}/i);
    if (match) return normalizeUnitId(match[0]);
  }
  return '';
}

export function matchesKnowledgeUnit(item, unitId) {
  const expected = normalizeUnitId(unitId);
  return !expected || extractKnowledgeUnitId(item) === expected;
}

export function isCurrentContextQuestion(value) {
  const question = text(value).replace(/[？?！!。,.，、\s]/g, '');
  if (!question) return false;

  if (/(这个|这页|本页|当前|这里|该页|该方案|该内容|本内容|眼前|屏幕上|它)/.test(question)) {
    return true;
  }

  return /^(?:请|请问|帮我|给我)?(?:简单|快速|详细)?(?:介绍|讲解|讲讲|说说)?(?:一下|下)?(?:主要)?(?:有什么价值|有什么作用|能做什么|解决什么问题|解决了什么问题|有哪些能力|有哪些功能|有哪些案例|有没有案例|需要接哪些系统|如何落地|怎么落地|如何实施|怎么实施|适合哪些客户|适合什么行业)$/.test(question);
}
