import { randomUUID } from 'crypto';
import { modelProvider } from '../api/ModelProvider.js';
import { knowledgeDb } from '../knowledge/KnowledgeDb.js';

const COMPLETION_REASONS = new Set(['button', 'silence_10s', 'deadline_120s', 'replay_query']);
const RESPONSE_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['relevance', 'completeness', 'canReplay', 'replayAnswer', 'warnings'],
  properties: {
    relevance: { type: 'string', enum: ['RELATED', 'PARTIAL', 'UNRELATED'] },
    completeness: { type: 'string', enum: ['SUFFICIENT', 'INSUFFICIENT'] },
    canReplay: { type: 'boolean' },
    replayAnswer: { type: 'string' },
    warnings: { type: 'array', items: { type: 'string' }, maxItems: 8 },
  },
};

function parseJson(value) {
  const text = String(value || '').trim().replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/i, '');
  try { return JSON.parse(text); } catch {}
  const match = text.match(/\{[\s\S]*\}/);
  if (!match) return null;
  try { return JSON.parse(match[0]); } catch { return null; }
}

function hasDirectInternalSupport(transcript, evidence = []) {
  const normalized = String(transcript || '').replace(/[\s，。！？、,.!?;；:"'“”‘’（）()]/g, '');
  if (normalized.length < 12) return false;
  const fragments = [];
  for (let index = 0; index <= normalized.length - 12; index += 6) {
    fragments.push(normalized.slice(index, index + 12));
  }
  return evidence.some(item => {
    const source = String(item.excerpt || item.summary || '').replace(/[\s，。！？、,.!?;；:"'“”‘’（）()]/g, '');
    return fragments.some(fragment => source.includes(fragment));
  });
}

export class TeachingAnalysisService {
  constructor(options = {}) {
    this.model = options.model || modelProvider;
    this.knowledge = options.knowledge || knowledgeDb;
    this.activeByRobot = new Map();
    this.lastStartedByRobot = new Map();
  }

  async analyze(input = {}) {
    const request = this._validate(input);
    const now = Date.now();
    if (!request.isFinal) {
      if (this.activeByRobot.has(request.robotId)) {
        throw Object.assign(new Error('a newer or active process analysis already exists'), { statusCode: 425 });
      }
      const previous = this.lastStartedByRobot.get(request.robotId) || 0;
      if (now - previous < 3000) {
        throw Object.assign(new Error('process analysis is rate limited'), { statusCode: 429 });
      }
      this.activeByRobot.set(request.robotId, request.analysisId);
      this.lastStartedByRobot.set(request.robotId, now);
    }

    try {
      const evidence = this.knowledge.findRagSnippets(request.question, { limit: 3, minScore: 30, excerptLength: 240 });
      const modelResult = await this.model.chat({
        text: [
          `原问题：${request.question}`,
          `工作人员累计讲解：${request.accumulatedTranscript}`,
          '判断讲解是否回应原问题，并整理成可直接播报的答案。只可整理原话，不得新增事实、数字、判断或承诺。',
          '若不相关或不充分，canReplay=false，replayAnswer 置空。只输出 JSON。',
        ].join('\n'),
        systemPrompt: '你是展厅机器人的现场转述助手。仅根据工作人员提供的内容回答原问题；可修正断句、删除口头语和重复，但不得补充任何原文没有的信息。',
        responseSchema: RESPONSE_SCHEMA,
        preserveRaw: true,
        temperature: 0.1,
        maxTokens: 520,
        timeoutMs: request.isFinal ? 5000 : 3000,
        context: 'robot-teaching-analysis-v1',
      });
      if (modelResult?.error) {
        return this._failed(request, modelResult.error);
      }
      const parsed = parseJson(modelResult.reply);
      if (!parsed || !['RELATED', 'PARTIAL', 'UNRELATED'].includes(parsed.relevance)
        || !['SUFFICIENT', 'INSUFFICIENT'].includes(parsed.completeness)) {
        return this._failed(request, 'invalid structured model output');
      }
      const canReplay = parsed.canReplay === true
        && parsed.relevance === 'RELATED'
        && parsed.completeness === 'SUFFICIENT'
        && String(parsed.replayAnswer || '').trim().length > 0;
      const supported = hasDirectInternalSupport(request.accumulatedTranscript, evidence);
      return {
        schemaVersion: '1.0.0',
        analysisId: request.analysisId,
        teachingTurnId: request.teachingTurnId,
        revision: request.revision,
        status: 'OK',
        relevance: parsed.relevance,
        completeness: parsed.completeness,
        factuality: supported ? 'SUPPORTED' : 'UNVERIFIED',
        canReplay,
        replayAnswer: canReplay ? String(parsed.replayAnswer).trim().slice(0, 800) : '',
        warnings: Array.isArray(parsed.warnings) ? parsed.warnings.map(String).slice(0, 8) : [],
        evidenceRefs: supported ? evidence.map(item => ({
          id: item.id,
          title: item.title,
          sourceFile: item.sourceFile || item.file || '',
          sourcePage: item.sourcePage || null,
        })) : [],
        traceId: randomUUID(),
      };
    } finally {
      if (!request.isFinal && this.activeByRobot.get(request.robotId) === request.analysisId) {
        this.activeByRobot.delete(request.robotId);
      }
    }
  }

  _validate(input) {
    const result = {
      schemaVersion: String(input.schemaVersion || '1.0.0'),
      analysisId: String(input.analysisId || '').trim(),
      teachingTurnId: String(input.teachingTurnId || '').trim(),
      revision: Number(input.revision),
      robotId: String(input.robotId || '').trim(),
      sessionId: String(input.sessionId || '').trim(),
      question: String(input.question || '').trim(),
      accumulatedTranscript: String(input.accumulatedTranscript || '').trim(),
      fragmentCount: Number(input.fragmentCount || 0),
      isFinal: input.isFinal === true,
      completionReason: input.completionReason == null ? null : String(input.completionReason),
    };
    if (!result.analysisId || !result.teachingTurnId || !result.robotId || !result.sessionId
      || !result.question || !result.accumulatedTranscript || !Number.isInteger(result.revision) || result.revision < 0) {
      throw Object.assign(new Error('invalid teaching analysis request'), { statusCode: 422 });
    }
    if (result.question.length > 1000 || result.accumulatedTranscript.length > 16000) {
      throw Object.assign(new Error('teaching analysis request is too large'), { statusCode: 413 });
    }
    if (result.isFinal && !COMPLETION_REASONS.has(result.completionReason)) {
      throw Object.assign(new Error('invalid completionReason'), { statusCode: 422 });
    }
    return result;
  }

  _failed(request, error) {
    return {
      schemaVersion: '1.0.0',
      analysisId: request.analysisId,
      teachingTurnId: request.teachingTurnId,
      revision: request.revision,
      status: 'FAILED',
      relevance: 'PARTIAL',
      completeness: 'INSUFFICIENT',
      factuality: 'UNVERIFIED',
      canReplay: false,
      replayAnswer: '',
      warnings: [String(error || 'analysis failed').slice(0, 300)],
      evidenceRefs: [],
      traceId: randomUUID(),
    };
  }
}

export const teachingAnalysisService = new TeachingAnalysisService();
export default teachingAnalysisService;
