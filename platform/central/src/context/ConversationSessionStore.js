import fs from 'fs';
import path from 'path';
import { randomUUID } from 'crypto';
import { config } from '../config/index.js';
import { buildModelConversationMessages } from './ConversationContextPolicy.js';

export const DEFAULT_CONVERSATION_IDLE_MS = 3 * 60 * 1000;

export class ConversationSessionStore {
  constructor(options = {}) {
    this.idleMs = Number(options.idleMs) || DEFAULT_CONVERSATION_IDLE_MS;
    this.maxTurns = Number(options.maxTurns) || 20;
    this.maxContextChars = Number(options.maxContextChars) || 8000;
    this.archivePath = options.archivePath || path.join(config.DATA_DIR, 'conversation-sessions.jsonl');
    this.historyLoader = typeof options.historyLoader === 'function' ? options.historyLoader : null;
    this.sessions = new Map();
    this.activeByParticipant = new Map();
    this.archiveListeners = new Set();
  }

  onArchive(listener) {
    if (typeof listener === 'function') this.archiveListeners.add(listener);
    return () => this.archiveListeners.delete(listener);
  }

  setHistoryLoader(loader) {
    this.historyLoader = typeof loader === 'function' ? loader : null;
  }

  beginTurn({ participantKey, conversationId = '', question = '', forceNew = false, now = Date.now() }) {
    const participant = String(participantKey || 'global');
    const requestedId = String(conversationId || '').trim();
    const activeId = requestedId || this.activeByParticipant.get(participant) || '';
    let session = activeId ? this.sessions.get(activeId) : null;

    if (forceNew && session) {
      this.archive(session.id, 'USER_STARTED_NEW', now);
      session = null;
    }
    if (!requestedId && session && now - session.lastActiveAt >= this.idleMs) {
      this.archive(session.id, 'IDLE_TIMEOUT', now);
      session = null;
    }
    if (requestedId && session && !forceNew) {
      if (session.timer) clearTimeout(session.timer);
      session.participantKey = participant;
      session.archivedAt = null;
      session.archiveReason = '';
    }
    if (!session) {
      const restoredTurns = requestedId && !forceNew ? this._loadHistory(requestedId) : [];
      session = this._createSession(participant, forceNew ? '' : requestedId, now, restoredTurns);
    }

    session.lastActiveAt = now;
    session.pendingQuestion = String(question || '').trim();
    this.activeByParticipant.set(participant, session.id);
    this._scheduleArchive(session);

    return {
      conversationId: session.id,
      isNew: session.turns.length === 0,
      turnCount: session.turns.length,
      history: session.turns.map(turn => ({ ...turn })),
      contextText: this.buildContext(session.id),
      idleTimeoutMs: this.idleMs,
    };
  }

  completeTurn(conversationId, turn = {}, now = Date.now()) {
    const session = this.sessions.get(String(conversationId || ''));
    if (!session || session.archivedAt) return null;
    const question = String(turn.question || session.pendingQuestion || '').trim();
    const answer = String(turn.answer || '').trim();
    session.turns.push({
      question,
      answer,
      answerSource: String(turn.answerSource || ''),
      finalRoute: String(turn.finalRoute || ''),
      ok: turn.ok !== false,
      technicalFallback: turn.technicalFallback === true,
      refusalFallback: turn.refusalFallback === true,
      contextEligible: turn.contextEligible !== false,
      time: new Date(now).toISOString(),
    });
    if (session.turns.length > this.maxTurns) {
      session.turns.splice(0, session.turns.length - this.maxTurns);
    }
    session.pendingQuestion = '';
    session.lastActiveAt = now;
    this._scheduleArchive(session);
    return this.snapshot(session.id);
  }

  buildContext(conversationId) {
    const session = this.sessions.get(String(conversationId || ''));
    if (!session?.turns?.length) return '';
    const rows = [];
    for (const turn of session.turns) {
      if (turn.question) rows.push(`用户：${turn.question}`);
      if (turn.answer) rows.push(`系统：${turn.answer}`);
    }
    let text = rows.join('\n');
    if (text.length > this.maxContextChars) text = text.slice(-this.maxContextChars);
    return text;
  }

  buildModelMessages(conversationId) {
    const session = this.sessions.get(String(conversationId || ''));
    return buildModelConversationMessages(session?.turns || [], {
      maxChars: this.maxContextChars,
    });
  }

  archive(conversationId, reason = 'IDLE_TIMEOUT', now = Date.now()) {
    const session = this.sessions.get(String(conversationId || ''));
    if (!session || session.archivedAt) return null;
    if (session.timer) clearTimeout(session.timer);
    session.timer = null;
    session.archivedAt = now;
    session.archiveReason = reason;
    if (this.activeByParticipant.get(session.participantKey) === session.id) {
      this.activeByParticipant.delete(session.participantKey);
    }
    const snapshot = this.snapshot(session.id);
    this._persistArchive(snapshot);
    for (const listener of this.archiveListeners) {
      try { listener(snapshot); } catch {}
    }
    return snapshot;
  }

  delete(conversationId) {
    const id = String(conversationId || '').trim();
    const session = this.sessions.get(id);
    if (!session) return false;
    if (session.timer) clearTimeout(session.timer);
    if (this.activeByParticipant.get(session.participantKey) === id) {
      this.activeByParticipant.delete(session.participantKey);
    }
    this.sessions.delete(id);
    return true;
  }

  snapshot(conversationId) {
    const session = this.sessions.get(String(conversationId || ''));
    if (!session) return null;
    return {
      id: session.id,
      participantKey: session.participantKey,
      startedAt: new Date(session.startedAt).toISOString(),
      lastActiveAt: new Date(session.lastActiveAt).toISOString(),
      archivedAt: session.archivedAt ? new Date(session.archivedAt).toISOString() : '',
      archiveReason: session.archiveReason || '',
      turnCount: session.turns.length,
      title: session.turns[0]?.question || session.pendingQuestion || '新会话',
    };
  }

  _createSession(participantKey, requestedId, now, restoredTurns = []) {
    const id = requestedId && !this.sessions.has(requestedId) ? requestedId : randomUUID();
    const turns = (Array.isArray(restoredTurns) ? restoredTurns : [])
      .slice(-this.maxTurns)
      .map(turn => ({
        question: String(turn?.question || '').trim(),
        answer: String(turn?.answer || '').trim(),
        answerSource: String(turn?.answerSource || ''),
        finalRoute: String(turn?.finalRoute || ''),
        ok: turn?.ok !== false,
        technicalFallback: turn?.technicalFallback === true,
        refusalFallback: turn?.refusalFallback === true,
        contextEligible: turn?.contextEligible !== false,
        time: String(turn?.time || ''),
      }))
      .filter(turn => turn.question || turn.answer);
    const restoredStartedAt = Date.parse(turns[0]?.time || '');
    const session = {
      id,
      participantKey,
      startedAt: Number.isFinite(restoredStartedAt) ? restoredStartedAt : now,
      lastActiveAt: now,
      archivedAt: null,
      archiveReason: '',
      pendingQuestion: '',
      turns,
      timer: null,
    };
    this.sessions.set(id, session);
    this.activeByParticipant.set(participantKey, id);
    return session;
  }

  _loadHistory(conversationId) {
    if (!this.historyLoader) return [];
    try {
      return this.historyLoader(conversationId, this.maxTurns) || [];
    } catch (err) {
      console.warn(`[ConversationSessionStore] history restore skipped: ${err.message}`);
      return [];
    }
  }

  _scheduleArchive(session) {
    if (session.timer) clearTimeout(session.timer);
    session.timer = setTimeout(() => this.archive(session.id, 'IDLE_TIMEOUT'), this.idleMs);
    session.timer.unref?.();
  }

  _persistArchive(snapshot) {
    if (!snapshot) return;
    try {
      fs.mkdirSync(path.dirname(this.archivePath), { recursive: true });
      fs.appendFileSync(this.archivePath, `${JSON.stringify(snapshot)}\n`, 'utf8');
    } catch (err) {
      console.warn(`[ConversationSessionStore] archive persistence skipped: ${err.message}`);
    }
  }
}

export const conversationSessionStore = new ConversationSessionStore();
export default conversationSessionStore;
