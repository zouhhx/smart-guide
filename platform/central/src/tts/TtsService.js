import fs from 'fs/promises';
import path from 'path';
import crypto from 'crypto';
import { config } from '../config/index.js';

const TTS_PROVIDERS = {
  iflytek: '原数字人 / 讯飞语音',
  qwen3: '本地 Qwen3 语音',
};

const IFLYTEK_VOICES = {
  xiaoyan: 'xiaoyan',
  aisjiuxu: 'aisjiuxu',
  aisxping: 'aisxping',
  aisjinger: 'aisjinger',
  aisbabyxu: 'aisbabyxu',
};

const VOICE_ALIASES = IFLYTEK_VOICES;
const PROVIDER_VOICES = {
  iflytek: IFLYTEK_VOICES,
};

class TtsService {
  constructor() {
    this.cacheDir = config.TTS.cacheDir;
    this.serviceUrl = config.TTS.serviceUrl;
    this.timeoutMs = config.TTS.timeoutMs || 20000;
    if (this.getProvider() === 'iflytek' && !this.serviceUrl) {
      throw new Error('TTS_SERVICE_URL is required for iFlytek TTS');
    }
  }

  getProvider(provider = config.TTS.provider) {
    const value = String(provider || 'iflytek').trim().toLowerCase();
    return TTS_PROVIDERS[value] ? value : 'iflytek';
  }

  getVoices(provider = config.TTS.provider) {
    return PROVIDER_VOICES[this.getProvider(provider)] || IFLYTEK_VOICES;
  }

  getDefaultVoice(provider = config.TTS.provider) {
    return config.TTS.defaultVoice;
  }

  normalizeVoice(voice, provider = config.TTS.provider) {
    const currentProvider = this.getProvider(provider);
    const voices = this.getVoices(currentProvider);
    const fallback = 'xiaoyan';
    const value = String(voice || this.getDefaultVoice(currentProvider) || fallback).trim();
    if (!value) return voices[fallback];
    const lower = value.toLowerCase();
    if (voices[lower]) return voices[lower];

    const entry = Object.entries(voices).find(([, fullName]) => String(fullName).toLowerCase() === lower);
    if (entry) return entry[1];
    return value;
  }

  async synthesize(text, voice, options = {}) {
    const cleanText = this.cleanText(text);
    if (!cleanText) {
      throw new Error('text is required');
    }
    if (cleanText.length > config.TTS.maxTextLength) {
      throw new Error(`text is too long, max ${config.TTS.maxTextLength} characters`);
    }

    await fs.mkdir(this.cacheDir, { recursive: true });

    const provider = this.getProvider(options.provider);
    const normalizedVoice = this.normalizeVoice(voice, provider);
    const extension = 'mp3';
    const hash = crypto
      .createHash('sha1')
      .update(`${provider}-tts\n${normalizedVoice}\n${cleanText}`)
      .digest('hex');
    const fileName = `${hash}.${extension}`;
    const filePath = path.join(this.cacheDir, fileName);

    const cached = await this._getCachedResult(fileName, filePath, normalizedVoice, provider);
    if (cached) return cached;

    const tmpTextPath = path.join(this.cacheDir, `${hash}.txt`);
    const tmpAudioPath = path.join(this.cacheDir, `${hash}.tmp.${extension}`);

    try {
      await fs.writeFile(tmpTextPath, cleanText, 'utf8');
      await this._callTtsService({ text: cleanText, outputPath: tmpAudioPath, voice: normalizedVoice });

      const stat = await fs.stat(tmpAudioPath);
      if (!stat.isFile() || stat.size <= 0) {
        throw new Error(`empty tts audio generated for voice ${normalizedVoice}`);
      }

      await fs.rename(tmpAudioPath, filePath);
      return this._result(fileName, filePath, normalizedVoice, stat.size, false, provider);
    } finally {
      await fs.rm(tmpTextPath, { force: true }).catch(() => {});
      await fs.rm(tmpAudioPath, { force: true }).catch(() => {});
    }
  }

  async _getCachedResult(fileName, filePath, voice, provider) {
    try {
      const stat = await fs.stat(filePath);
      if (stat.isFile() && stat.size > 0) {
        return this._result(fileName, filePath, voice, stat.size, true, provider);
      }
    } catch {
      return null;
    }
    return null;
  }

  async _callTtsService({ text, outputPath, voice }) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const response = await fetch(this.serviceUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
        body: JSON.stringify({
          text,
          voice,
          output: outputPath,
          retries: config.TTS.retries ?? 2,
        }),
        signal: controller.signal,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.ok !== true) {
        throw new Error(payload.error || `HTTP ${response.status}`);
      }
    } finally {
      clearTimeout(timer);
    }
  }

  async createAvatarSilence(text) {
    const cleanText = this.cleanText(text);
    const durationMs = Math.max(1200, Math.min(60000, Math.ceil(Array.from(cleanText).length / 5 * 1000 + 500)));
    const hash = crypto.createHash('sha1').update(`avatar-silence\n${durationMs}`).digest('hex');
    const fileName = `avatar-${hash}.wav`;
    const filePath = path.join(this.cacheDir, fileName);
    await fs.mkdir(this.cacheDir, { recursive: true });
    try {
      const stat = await fs.stat(filePath);
      if (stat.isFile() && stat.size > 44) return { ...this._result(fileName, filePath, 'digital-human', stat.size, true, 'avatar'), durationMs, avatar: true };
    } catch {}

    const sampleRate = 8000;
    const sampleCount = Math.ceil(sampleRate * durationMs / 1000);
    const dataSize = sampleCount * 2;
    const wav = Buffer.alloc(44 + dataSize);
    wav.write('RIFF', 0); wav.writeUInt32LE(36 + dataSize, 4); wav.write('WAVE', 8);
    wav.write('fmt ', 12); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20);
    wav.writeUInt16LE(1, 22); wav.writeUInt32LE(sampleRate, 24); wav.writeUInt32LE(sampleRate * 2, 28);
    wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(dataSize, 40);
    await fs.writeFile(filePath, wav);
    return { ...this._result(fileName, filePath, 'digital-human', wav.length, false, 'avatar'), durationMs, avatar: true };
  }

  _mimeFor(fileName) {
    const ext = path.extname(fileName).toLowerCase();
    if (ext === '.wav') return 'audio/wav';
    if (ext === '.pcm') return 'audio/L16';
    return 'audio/mpeg';
  }

  _result(fileName, filePath, voice, size, cached, provider) {
    return {
      fileName,
      filePath,
      provider,
      voice,
      size,
      cached,
      mime: this._mimeFor(fileName),
      url: `${config.PUBLIC_FILE_BASE_URL}/tts-cache/${fileName}`,
    };
  }

  cleanText(text) {
    let value = String(text || '')
      .replace(/[*_`#>~|[\]{}<>]/g, '')
      .replace(/https?:\/\/\S+/gi, '')
      .replace(/[\u0000-\u001f\u007f-\u009f]/g, ' ')
      .replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g, '')
      .replace(/\s+/g, ' ')
      .trim();

    for (const marker of ['•', '●', '◆', '■', '▶', '▷', '→', '←']) {
      value = value.split(marker).join(' ');
    }

    return value.replace(/\s+/g, ' ').trim();
  }
}

export const ttsService = new TtsService();
export { VOICE_ALIASES, TTS_PROVIDERS, PROVIDER_VOICES };
