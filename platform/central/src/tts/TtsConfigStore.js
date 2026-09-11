import { localTts } from './LocalTts.js';
import fs from 'fs';
import path from 'path';
import { config } from '../config/index.js';
import { PROVIDER_VOICES, TTS_PROVIDERS } from './TtsService.js';

class TtsConfigStore {
  constructor() {
    this.filePath = path.resolve(config.DATA_DIR || path.resolve(process.cwd(), 'data'), 'tts-config.json');
    this.state = {
      provider: 'iflytek',
      voice: config.TTS.defaultVoice || 'xiaoyan',
      avatarEnabled: false,
      updatedAt: null,
    };
    this._load();
  }

  _load() {
    try {
      if (!fs.existsSync(this.filePath)) return;
      const raw = JSON.parse(fs.readFileSync(this.filePath, 'utf8'));
      this.state = {
        ...this.state,
        ...raw,
        provider: this.normalizeProvider(raw.provider || this.state.provider),
        voice: this.normalizeVoiceKey(raw.voice || this.state.voice, raw.provider || this.state.provider),
        avatarEnabled: raw.avatarEnabled === true,
      };
    } catch (err) {
      console.warn(`[TtsConfigStore] Failed to load config: ${err.message}`);
    }
  }

  _save() {
    fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
    fs.writeFileSync(this.filePath + '.tmp', JSON.stringify(this.state, null, 2), 'utf8');
    fs.renameSync(this.filePath + '.tmp', this.filePath);
  }

  normalizeProvider(value) {
    const key = String(value || '').trim().toLowerCase();
    return TTS_PROVIDERS[key] ? key : 'iflytek';
  }

  normalizeVoiceKey(value, provider = this.state.provider) {
    const currentProvider = this.normalizeProvider(provider);
    if (currentProvider === 'qwen3') return String(value || 'ma_junfei').trim();
    const voices = PROVIDER_VOICES[currentProvider] || PROVIDER_VOICES.iflytek;
    const defaultVoice = config.TTS.defaultVoice;
    const key = String(value || defaultVoice || '').trim().toLowerCase();
    if (voices[key]) return key;

    const entry = Object.entries(voices).find(([, fullName]) => String(fullName).toLowerCase() === key);
    return entry?.[0] || 'xiaoyan';
  }

  get() {
    const provider = this.normalizeProvider(this.state.provider);
    const voice = this.normalizeVoiceKey(this.state.voice, provider);
    const voices = provider === 'qwen3' ? localTts.voices : PROVIDER_VOICES[provider] || PROVIDER_VOICES.iflytek;
    return {
      provider,
      providerName: TTS_PROVIDERS[provider],
      providers: TTS_PROVIDERS,
      voice,
      voiceName: voices[voice],
      voices,
      allVoices: { ...PROVIDER_VOICES, qwen3: localTts.voices },
      localStatus: localTts.status,
      avatarEnabled: this.state.avatarEnabled === true,
      updatedAt: this.state.updatedAt,
    };
  }

  setVoice(voice, provider = this.state.provider, avatarEnabled = this.state.avatarEnabled) {
    if (!TTS_PROVIDERS[provider]) throw new Error('不支持的语音服务');
    if (provider === 'qwen3' && !Object.hasOwn(localTts.voices, voice)) throw new Error('音色不存在，请刷新后重新选择');
    this.state.provider = this.normalizeProvider(provider);
    this.state.voice = this.normalizeVoiceKey(voice, this.state.provider);
    this.state.avatarEnabled = avatarEnabled === true;
    this.state.updatedAt = new Date().toISOString();
    this._save();
    return this.get();
  }
}

export const ttsConfigStore = new TtsConfigStore();
