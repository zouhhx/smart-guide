import { config } from '../config/index.js';
import { buildQaSystemPrompt } from './ModelPrompt.js';

class OpenClawBridge {
  constructor() {
    this.baseUrl = config.OPENCLAW.baseUrl;
    this.token = config.OPENCLAW.token;
    this.agentId = config.OPENCLAW.agentId;
  }

  async chat({
    text,
    context,
    conversationMessages = [],
    systemPrompt = '',
    responseSchema = null,
    preserveRaw = false,
    temperature = 0.3,
    maxTokens = 160,
    timeoutMs = 0,
  }) {
    const controller = new AbortController();
    const requestTimeoutMs = timeoutMs || config.OPENCLAW.timeoutMs || 15000;
    const timer = setTimeout(() => controller.abort(), requestTimeoutMs);
    try {
      const requestBody = {
        model: 'openclaw',
        messages: [
          { role: 'system', content: systemPrompt || buildQaSystemPrompt() },
          ...this._normalizeConversationMessages(conversationMessages),
          { role: 'user', content: systemPrompt ? String(text || '').trim() : this._buildUserPrompt(text, context) },
        ],
        temperature,
        max_tokens: maxTokens,
        ...(responseSchema ? {
          response_format: {
            type: 'json_schema',
            json_schema: { name: 'grounded_answer', strict: true, schema: responseSchema },
          },
        } : {}),
      };
      const request = () => fetch(`${this.baseUrl}/v1/chat/completions`, {
        method: 'POST',
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${this.token}`,
          'x-openclaw-agent-id': this.agentId,
        },
        body: JSON.stringify(requestBody),
      });
      let response = await request();
      if (!response.ok && responseSchema && [400, 422].includes(response.status)) {
        console.warn(`[OpenClawBridge] structured output unsupported (${response.status}), retrying with prompt-only JSON`);
        delete requestBody.response_format;
        response = await request();
      }

      if (!response.ok) {
        const errText = await response.text();
        console.error(`[OpenClawBridge] API error: ${response.status} - ${errText}`);
        return { error: `OpenClaw API error: ${response.status}` };
      }

      const data = await response.json();
      return this._normalize(data, { preserveRaw });
    } catch (err) {
      const message = err.name === 'AbortError'
        ? `OpenClaw request timeout after ${requestTimeoutMs}ms`
        : err.message;
      console.error('[OpenClawBridge] request failed:', message);
      return { error: message };
    } finally {
      clearTimeout(timer);
    }
  }

  _buildUserPrompt(text, context) {
    return [
      String(context || '').trim(),
      String(text || '').trim(),
      '',
      '50-100个字以内回答。',
    ].filter(Boolean).join('\n');
  }

  _normalizeConversationMessages(messages) {
    if (!Array.isArray(messages)) return [];
    return messages
      .map(message => ({
        role: message?.role === 'assistant' ? 'assistant' : 'user',
        content: String(message?.content || '').trim(),
      }))
      .filter(message => message.content);
  }

  _normalize(data, { preserveRaw = false } = {}) {
    if (typeof data === 'string') {
      if (!data.trim()) return { error: 'OpenClaw returned empty response' };
      return { reply: data, actions: [] };
    }

    const raw = data?.choices?.[0]?.message?.content || '';
    console.log(`[OpenClawBridge] raw response (${raw.length} chars):`, raw.substring(0, 500));
    if (!String(raw || '').trim()) {
      return { error: 'OpenClaw returned empty response' };
    }

    if (preserveRaw) {
      return { reply: raw, actions: [], context: { rawModelOutput: raw } };
    }

    const jsonStr = this._extractJson(raw);
    if (jsonStr) {
      try {
        const parsed = JSON.parse(jsonStr);
        return {
          reply: parsed.reply || parsed.text || raw,
          actions: this._normalizeActions(parsed.actions),
          context: parsed.context || {},
        };
      } catch (err) {
        console.error('[OpenClawBridge] JSON parse failed:', err.message);
      }
    }

    return { reply: raw, actions: [], context: {} };
  }

  _normalizeActions(actions) {
    if (!Array.isArray(actions)) return [];

    return actions
      .map(action => {
        const params = action.params || { file: action.file, text: action.text, slide: action.slide };
        const type = action.type || action.action || action.name || this._inferActionType(params.file);
        return {
          ...action,
          type,
          params,
        };
      })
      .filter(action => action.type);
  }

  _inferActionType(file) {
    const value = String(file || '').toLowerCase();
    if (value.endsWith('.ppt') || value.endsWith('.pptx')) return 'play_ppt';
    if (/\.(png|jpg|jpeg|gif|webp|bmp)$/.test(value)) return 'show_doc';
    if (/\.(mp4|webm|avi|mov|mkv)$/.test(value)) return 'play_video';
    return null;
  }

  _extractJson(text) {
    if (!text) return null;

    const trimmed = text.trim();
    if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
      return trimmed;
    }

    const codeBlockMatch = trimmed.match(/```(?:json)?\s*\n?([\s\S]*?)\n?\s*```/);
    if (codeBlockMatch) {
      const inner = codeBlockMatch[1].trim();
      if (inner.startsWith('{')) return inner;
    }

    const startIdx = trimmed.indexOf('{');
    if (startIdx === -1) return null;

    let depth = 0;
    let inString = false;
    let escape = false;

    for (let i = startIdx; i < trimmed.length; i++) {
      const ch = trimmed[i];
      if (escape) {
        escape = false;
        continue;
      }
      if (ch === '\\' && inString) {
        escape = true;
        continue;
      }
      if (ch === '"') {
        inString = !inString;
        continue;
      }
      if (inString) continue;
      if (ch === '{') depth++;
      else if (ch === '}') {
        depth--;
        if (depth === 0) return trimmed.substring(startIdx, i + 1);
      }
    }

    return null;
  }

  async ping() {
    try {
      const response = await fetch(`${this.baseUrl}/health`, {
        headers: { Authorization: `Bearer ${this.token}` },
      });
      return response.ok;
    } catch {
      return false;
    }
  }
}

export const openClawBridge = new OpenClawBridge();
export default openClawBridge;
