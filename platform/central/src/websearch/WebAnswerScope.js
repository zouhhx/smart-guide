export const WEB_ANSWER_SCOPE_VERSION = 'web-answer-scope-v1';

function text(value) {
  return String(value || '').trim();
}

function inferAnswerType(question, profile = {}) {
  const value = text(question);
  if (profile.type === 'RESULT_OUTCOME') return 'RESULT_ENTITY';
  if (profile.type === 'AWARD_RESULT') return 'AWARD_ENTITY';
  if (profile.type === 'LIVE_RESULT') return 'ENTITY_PAIR';
  if (profile.type === 'SCHEDULE_DATETIME') return 'DATETIME';
  if (profile.type === 'SCHEDULE_FACT') return 'DATE';
  if (profile.type === 'QUANTITATIVE_BREAKDOWN') return 'NUMBER_BREAKDOWN';
  if (/(谁|哪位|哪个人|哪家公司|哪个机构|哪个国家|哪国|哪支球队)/i.test(value)) return 'ENTITY';
  if (/(什么时候|何时|哪天|日期|几月几日|几点)/i.test(value)) return 'DATETIME';
  if (/(哪里|哪儿|何处|在哪(?:里)?|什么地点)/i.test(value)) return 'LOCATION';
  if (/(多少|几项|几个|总额|金额|比例|百分比)/i.test(value)) return 'NUMBER';
  if (/(是否|有没有|了吗|是不是|能否)/i.test(value)) return 'BOOLEAN_STATUS';
  if (/(哪些|有什么|包括什么|提供什么|主要功能)/i.test(value)) return 'LIST';
  return 'GENERAL';
}

const CLOSED_WHEN_COMPLETE = new Set([
  'RESULT_ENTITY',
  'AWARD_ENTITY',
  'ENTITY_PAIR',
  'ENTITY',
  'DATETIME',
  'DATE',
  'LOCATION',
  'NUMBER',
  'NUMBER_BREAKDOWN',
  'BOOLEAN_STATUS',
]);

export function buildWebAnswerScope(question, profile = {}, assessment = {}) {
  const required = (assessment.requiredAspects || []).map(item => ({
    id: text(item.id),
    name: text(item.name),
  })).filter(item => item.id || item.name);
  const missingIds = new Set((assessment.missingAspects || []).map(item => text(item.id)).filter(Boolean));
  const missingNames = new Set((assessment.missingAspects || []).map(item => text(item.name)).filter(Boolean));
  const answerType = inferAnswerType(question, profile);
  const complete = required.length > 0
    && required.every(item => !missingIds.has(item.id) && !missingNames.has(item.name));
  return {
    version: WEB_ANSWER_SCOPE_VERSION,
    answerType,
    requiredFields: required,
    missingFields: required.filter(item => missingIds.has(item.id) || missingNames.has(item.name)),
    complete,
    closeUncertaintiesWhenComplete: CLOSED_WHEN_COMPLETE.has(answerType),
  };
}

export function constrainUncertainties(uncertainties, scope) {
  const values = (Array.isArray(uncertainties) ? uncertainties : [])
    .map(item => text(item))
    .filter(Boolean);
  if (!scope) return values;
  if (scope.complete && scope.closeUncertaintiesWhenComplete) return [];
  return values;
}

export function answerScopePromptPayload(scope) {
  return {
    version: scope?.version || WEB_ANSWER_SCOPE_VERSION,
    answer_type: scope?.answerType || 'GENERAL',
    required_fields: (scope?.requiredFields || []).map(item => ({
      id: item.id,
      description: item.name,
    })),
    missing_fields: (scope?.missingFields || []).map(item => item.id || item.name),
    complete_core_fields_must_have_empty_uncertainties:
      scope?.closeUncertaintiesWhenComplete === true,
  };
}
