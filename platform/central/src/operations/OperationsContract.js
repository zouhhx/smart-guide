export const OPERATIONS_CONTRACT_VERSION = 'operations.v1';

export const OPERATIONS_CAPABILITIES = Object.freeze([
  'avatar.speak',
  'tts.speak',
  'screen.command',
  'media.command',
  'monitor.status',
]);

const CAPABILITY_SET = new Set(OPERATIONS_CAPABILITIES);
const REQUEST_ID_PATTERN = /^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,99}$/;

function cleanObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? { ...value } : {};
}

export function normalizeOperationsRequest(input = {}, defaults = {}) {
  return {
    version: String(input.version || OPERATIONS_CONTRACT_VERSION),
    requestId: String(input.requestId || defaults.requestId || '').trim(),
    eventId: String(input.eventId || defaults.eventId || input.requestId || '').trim(),
    capability: String(input.capability || '').trim(),
    target: String(input.target || 'all').trim() || 'all',
    params: cleanObject(input.params),
    actor: {
      id: String(input.actor?.id || defaults.actorId || 'anonymous').trim(),
      type: String(input.actor?.type || defaults.actorType || 'system').trim(),
    },
    timeoutMs: Number(input.timeoutMs || defaults.timeoutMs || 5000),
    metadata: cleanObject(input.metadata),
  };
}

export function validateOperationsRequest(request) {
  const errors = [];
  if (request.version !== OPERATIONS_CONTRACT_VERSION) errors.push('UNSUPPORTED_VERSION');
  if (!REQUEST_ID_PATTERN.test(request.requestId)) errors.push('INVALID_REQUEST_ID');
  if (!REQUEST_ID_PATTERN.test(request.eventId)) errors.push('INVALID_EVENT_ID');
  if (!CAPABILITY_SET.has(request.capability)) errors.push('UNKNOWN_CAPABILITY');
  if (!request.target || request.target.length > 100) errors.push('INVALID_TARGET');
  if (!Number.isFinite(request.timeoutMs) || request.timeoutMs < 100 || request.timeoutMs > 30000) {
    errors.push('INVALID_TIMEOUT');
  }
  if (['avatar.speak', 'tts.speak'].includes(request.capability)
    && !String(request.params.text || '').trim()) {
    errors.push('TEXT_REQUIRED');
  }
  if (['screen.command', 'media.command'].includes(request.capability)
    && !String(request.params.command || '').trim()) {
    errors.push('COMMAND_REQUIRED');
  }
  return { ok: errors.length === 0, errors };
}

export function buildOperationsResult(request, input = {}) {
  return {
    version: OPERATIONS_CONTRACT_VERSION,
    requestId: request.requestId,
    eventId: request.eventId,
    capability: request.capability,
    target: request.target,
    status: String(input.status || 'SUCCEEDED'),
    code: String(input.code || 'OK'),
    message: String(input.message || ''),
    data: cleanObject(input.data),
    retryable: input.retryable === true,
    startedAt: String(input.startedAt || new Date().toISOString()),
    completedAt: String(input.completedAt || new Date().toISOString()),
  };
}

export function isOperationsCapability(value) {
  return CAPABILITY_SET.has(String(value || ''));
}
