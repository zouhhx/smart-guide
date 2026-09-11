import fs from 'fs';
import path from 'path';
import { config } from '../config/index.js';
import { openClawBridge } from './OpenClawBridge.js';
import { ollamaBridge } from './OllamaBridge.js';

const PROVIDERS = {
  openclaw: {
    id: 'openclaw',
    label: '小龙虾 OpenClaw',
    bridge: openClawBridge,
  },
  ollama: {
    id: 'ollama',
    label: `大模型 Ollama (${config.OLLAMA.model})`,
    bridge: ollamaBridge,
  },
};

class ModelProvider {
  constructor() {
    this.statePath = path.join(config.DATA_DIR, 'model-provider.json');
    this.current = this._loadProvider();
  }

  getCurrent() {
    return {
      provider: this.current,
      label: PROVIDERS[this.current].label,
      ollamaModel: config.OLLAMA.model,
    };
  }

  async chat(args) {
    const provider = PROVIDERS[this.current] || PROVIDERS.openclaw;
    console.log(`[ModelProvider] using ${provider.id}`);
    const result = await provider.bridge.chat(args);
    if (result?.error && provider.id !== 'ollama') {
      console.warn(`[ModelProvider] ${provider.id} failed, fallback to ollama: ${result.error}`);
      const fallback = await PROVIDERS.ollama.bridge.chat(args);
      return {
        ...fallback,
        context: {
          ...(fallback.context || {}),
          modelProvider: fallback.error ? provider.id : 'ollama',
          fallbackFromProvider: provider.id,
          fallbackReason: result.error,
        },
      };
    }
    return {
      ...result,
      context: {
        ...(result.context || {}),
        modelProvider: provider.id,
      },
    };
  }

  trySwitchByText(text) {
    const normalized = String(text || '').replace(/\s+/g, '').toLowerCase();

    if (normalized.includes('切换大模型') || normalized.includes('切到大模型') || normalized.includes('打开大模型') || normalized.includes('切换ollama')) {
      return this.switchTo('ollama');
    }

    if (normalized.includes('切换小龙虾') || normalized.includes('切到小龙虾') || normalized.includes('打开小龙虾') || normalized.includes('切换openclaw')) {
      return this.switchTo('openclaw');
    }

    if (normalized.includes('切换大模型') || normalized.includes('切到大模型') || normalized.includes('切换ollama')) {
      return this.switchTo('ollama');
    }

    if (normalized.includes('切换小龙虾') || normalized.includes('切到小龙虾') || normalized.includes('切换openclaw')) {
      return this.switchTo('openclaw');
    }

    return null;
  }

  switchTo(provider) {
    if (!PROVIDERS[provider]) {
      throw new Error(`Unknown model provider: ${provider}`);
    }

    this.current = provider;
    this._saveProvider();
    const current = this.getCurrent();
    console.log(`[ModelProvider] switched to ${current.provider}`);
    return current;
  }

  _loadProvider() {
    const fallback = PROVIDERS[config.MODEL_PROVIDER] ? config.MODEL_PROVIDER : 'openclaw';

    try {
      if (!fs.existsSync(this.statePath)) return fallback;
      const saved = JSON.parse(fs.readFileSync(this.statePath, 'utf8'));
      return PROVIDERS[saved.provider] ? saved.provider : fallback;
    } catch (err) {
      console.warn('[ModelProvider] failed to load provider state:', err.message);
      return fallback;
    }
  }

  _saveProvider() {
    try {
      fs.mkdirSync(path.dirname(this.statePath), { recursive: true });
      fs.writeFileSync(this.statePath, JSON.stringify({
        provider: this.current,
        updatedAt: new Date().toISOString(),
      }, null, 2));
    } catch (err) {
      console.warn('[ModelProvider] failed to save provider state:', err.message);
    }
  }
}

export const modelProvider = new ModelProvider();
export default modelProvider;
