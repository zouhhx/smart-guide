import { setTimeout as delay } from 'node:timers/promises';

export function splitSpeech(text, limit = 280) {
  const chars = Array.from(String(text || '').trim());
  if (!chars.length || chars.length > 12000) throw new Error('播报文本须为 1～12000 字');
  const parts = [];
  while (chars.length) {
    let n = Math.min(limit, chars.length);
    if (chars.length > limit) {
      for (let i = n - 1; i >= Math.floor(limit / 2); i--) {
        if (/[。！？；.!?;\n]/u.test(chars[i])) { n = i + 1; break; }
      }
    }
    const part = chars.splice(0, n).join('').trim();
    if (part) parts.push(part);
  }
  return parts;
}

export function wavHeader(length) {
  const b = Buffer.alloc(44);
  b.write('RIFF'); b.writeUInt32LE(length + 36, 4); b.write('WAVEfmt ', 8);
  b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22);
  b.writeUInt32LE(24000, 24); b.writeUInt32LE(48000, 28);
  b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34); b.write('data', 36); b.writeUInt32LE(length, 40);
  return b;
}

export class LocalTts {
  constructor(base = process.env.LOCAL_TTS_URL || 'http://127.0.0.1:8769') {
    this.base = base.replace(/\/+$/, ''); this.voices = {}; this.checked = 0;
    this.status = { ready: false, busy: false }; this.active = false;
  }
  async refresh(force = false) {
    if (!force && Date.now() - this.checked < 10000) return this.status;
    if (this.refreshing) return this.refreshing;
    this.refreshing = (async () => {
      try {
        const [h, v] = await Promise.all([
          fetch(`${this.base}/healthz`, { signal: AbortSignal.timeout(2500) }),
          fetch(`${this.base}/v1/voices`, { signal: AbortSignal.timeout(2500) }),
        ]);
        if (!h.ok || !v.ok) throw new Error('本地语音服务未就绪');
        const health = await h.json(), catalog = await v.json();
        this.voices = Object.fromEntries((catalog.voices || []).map(v => [v.id, v.name || v.id]));
        this.status = { ...health, ready: health.ready === true || health.status === 'ready', online: true };
      } catch { this.status = { ready: false, busy: false, online: false }; }
      this.checked = Date.now(); return this.status;
    })();
    try { return await this.refreshing; } finally { this.refreshing = null; }
  }
  async *pcm(text, voice, signal) {
    const parts = splitSpeech(text);
    await this.refresh();
    if (!Object.hasOwn(this.voices, voice)) throw new Error('音色不存在，请刷新音色列表后重新选择');
    // Bound contention; never build a queue of stale exhibition utterances.
    const deadline = Date.now() + 5000;
    while (this.active && Date.now() < deadline) await delay(100, undefined, { signal });
    signal?.throwIfAborted();
    if (this.active) throw Object.assign(new Error('本地语音正在使用，请稍后重试'), { status: 429 });
    this.active = true;
    try {
      for (const input of parts) {
        const timeout = AbortSignal.timeout(90000);
        const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
        let response;
        const busyDeadline = Date.now() + 5000;
        for (;;) {
          response = await fetch(`${this.base}/v1/audio/speech`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ input, voice, response_format: 'pcm' }), signal: combined,
          });
          if (response.status !== 429 || Date.now() >= busyDeadline) break;
          await response.body?.cancel();
          await delay(1000, undefined, { signal: combined });
        }
        if (!response.ok) {
          await response.body?.cancel();
          throw Object.assign(new Error(`本地语音请求失败 (${response.status})`), { status: response.status });
        }
        if (response.headers.get('x-sample-rate') !== '24000' || response.headers.get('x-audio-channels') !== '1'
            || response.headers.get('x-audio-format') !== 'pcm_s16le') {
          await response.body?.cancel(); throw new Error('本地语音 PCM 格式不符合 24kHz 单声道 S16LE');
        }
        let bytes = 0;
        // Async iteration cancels the upstream on downstream failure/interrupt.
        for await (const chunk of response.body) { bytes += chunk.length; yield chunk; }
        if (!bytes || bytes % 2) throw new Error('本地语音数据不完整');
      }
    } finally { this.active = false; }
  }
}
export const localTts = new LocalTts();
