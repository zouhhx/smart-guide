// ============================================================
// 展厅智能中控 - 本地中转服务配置文件
// ============================================================

import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function loadDotEnv(filePath) {
  try {
    if (!fs.existsSync(filePath)) return;
    const content = fs.readFileSync(filePath, 'utf8').replace(/^\uFEFF/, '');
    for (const line of content.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const index = trimmed.indexOf('=');
      if (index <= 0) continue;
      const key = trimmed.slice(0, index).trim();
      let value = trimmed.slice(index + 1).trim();
      value = value.replace(/^['"]|['"]$/g, '');
      if (key && process.env[key] === undefined) process.env[key] = value;
    }
  } catch (err) {
    console.warn(`[Config] Failed to load .env: ${err.message}`);
  }
}

loadDotEnv(path.resolve(__dirname, '../../.env'));

const env = process.env;

function readIntEnv(name, fallback) {
  const value = Number.parseInt(env[name] || '', 10);
  return Number.isFinite(value) ? value : fallback;
}

function stripTrailingSlash(value) {
  return value.replace(/\/+$/, '');
}

function readJsonEnv(name, fallback) {
  try {
    return env[name] ? JSON.parse(env[name]) : fallback;
  } catch (err) {
    console.warn(`[Config] Invalid JSON in ${name}: ${err.message}`);
    return fallback;
  }
}

function readCsvEnv(name, fallback = []) {
  const value = env[name];
  if (!value) return [...fallback];
  return [...new Set(String(value).split(',').map(item => item.trim()).filter(Boolean))];
}

// 设备类型常量
export const DEVICE_TYPES = {
  MOBILE: 'mobile',
  CLIENT: 'client',
  ADMIN: 'admin',
};

// 心跳配置（毫秒）
export const HEARTBEAT = {
  interval: 15000,        // 客户端每 15s 发一次心跳
  timeout: 45000,         // 45s 没收到心跳视为离线
  cleanupInterval: 10000, // 每 10s 清理一次超时设备
};

const PORT = readIntEnv('PORT', 3000);
const FILE_PORT = readIntEnv('FILE_PORT', 3001);
const PUBLIC_HOST = env.PUBLIC_HOST || env.HOST_IP || '192.168.16.61';
const PUBLIC_FILE_BASE_URL = stripTrailingSlash(
  env.PUBLIC_FILE_BASE_URL || `http://${PUBLIC_HOST}:${FILE_PORT}`
);
const PUBLIC_WS_URL = env.PUBLIC_WS_URL || `ws://${PUBLIC_HOST}:${PORT}`;
const PPT_THUMBNAIL_SERVICE_URL = stripTrailingSlash(
  env.PPT_THUMBNAIL_SERVICE_URL || 'http://127.0.0.1:3025'
);

export const config = {
  // 中转服务端口
  PORT,

  // 静态文件服务端口（媒体资源）
  FILE_PORT,

  // 手机、展示端可访问到的局域网地址
  PUBLIC_HOST,
  PUBLIC_FILE_BASE_URL,
  PUBLIC_WS_URL,
  PPT_THUMBNAIL_SERVICE_URL,
  PPT_THUMBNAIL_SERVICE_TIMEOUT_MS: readIntEnv('PPT_THUMBNAIL_SERVICE_TIMEOUT_MS', 15000),

  // 媒体文件存储目录（WSL 路径）
  MEDIA_DIR: env.MEDIA_DIR || path.resolve(__dirname, '../../media'),

  // 服务端运行数据目录：使用情况、统计摘要等
  DATA_DIR: env.DATA_DIR || path.resolve(__dirname, '../../data'),

  // Obsidian 内容母仓目录。中控同步后构建 Markdown 内存索引，不再使用 SQLite 运行库。
  OBSIDIAN_VAULT_PATH: env.OBSIDIAN_VAULT_PATH || path.resolve(env.DATA_DIR || path.resolve(__dirname, '../../data'), 'obsidian-vault'),

  // Windows 本地路径前缀（展示端 Office 服务用）
  MEDIA_WIN_PATH: env.MEDIA_WIN_PATH || '\\\\wsl.localhost\\Ubuntu\\home\\user\\.openclaw\\workspace\\exhibition-hall\\media',

  // OpenClaw Gateway 地址
  OPENCLAW: {
    baseUrl: env.OPENCLAW_BASE_URL || 'http://127.0.0.1:18789',
    token: env.OPENCLAW_TOKEN || '',
    agentId: env.OPENCLAW_AGENT_ID || 'main',
    timeoutMs: readIntEnv('OPENCLAW_TIMEOUT_MS', 15000),
  },

  MODEL_PROVIDER: env.MODEL_PROVIDER || 'openclaw',

  OLLAMA: {
    baseUrl: env.OLLAMA_BASE_URL || 'http://127.0.0.1:11434',
    model: env.OLLAMA_MODEL || 'gemma4:e4b',
    timeoutMs: readIntEnv('OLLAMA_TIMEOUT_MS', 15000),
  },

  VECTOR_SEARCH: {
    enabled: env.VECTOR_SEARCH_ENABLED !== 'false',
    serviceUrl: env.VECTOR_SEARCH_URL || 'http://127.0.0.1:3021',
    timeoutMs: readIntEnv('VECTOR_SEARCH_TIMEOUT_MS', 2500),
    faqMinSimilarity: Number(env.VECTOR_FAQ_MIN_SIMILARITY || 0.18),
    ragMinSimilarity: Number(env.VECTOR_RAG_MIN_SIMILARITY || 0.24),
    mediaMinSimilarity: Number(env.VECTOR_MEDIA_MIN_SIMILARITY || 0.16),
  },

  WEATHER: {
    enabled: env.WEATHER_ENABLED !== 'false',
    provider: 'amap',
    keyName: env.AMAP_WEATHER_KEY_NAME || 'fhhl',
    key: env.AMAP_WEATHER_KEY || '',
    defaultCity: env.WEATHER_DEFAULT_CITY || '南京',
    cacheFile: env.WEATHER_CACHE_FILE || path.resolve(env.DATA_DIR || path.resolve(__dirname, '../../data'), 'weather-cache.json'),
    refreshIntervalHours: readIntEnv('WEATHER_REFRESH_INTERVAL_HOURS', 24),
    timeoutMs: readIntEnv('WEATHER_TIMEOUT_MS', 6000),
  },

  TTS: {
    provider: 'iflytek',
    defaultVoice: env.TTS_VOICE || 'xiaoyan',
    cacheDir: env.TTS_CACHE_DIR || path.resolve(__dirname, '../../data/tts-cache'),
    maxTextLength: readIntEnv('TTS_MAX_TEXT_LENGTH', 2000),
    serviceUrl: env.TTS_SERVICE_URL || 'http://127.0.0.1:3011/tts',
    timeoutMs: readIntEnv('TTS_TIMEOUT_MS', 20000),
    retries: readIntEnv('TTS_RETRIES', 2),
    avatarServiceUrl: stripTrailingSlash(env.AVATAR_SERVICE_URL || 'http://127.0.0.1:19200'),
    avatarTimeoutMs: readIntEnv('AVATAR_SERVICE_TIMEOUT_MS', 8000),
    avatarTaskControlEnabled: env.AVATAR_TASK_CONTROL_ENABLED !== 'false',
  },

  OPERATIONS_GATEWAY: {
    enabled: env.OPERATIONS_GATEWAY_ENABLED === 'true',
    token: env.OPERATIONS_GATEWAY_TOKEN || '',
    allowedCapabilities: readCsvEnv('OPERATIONS_GATEWAY_ALLOWED_CAPABILITIES', [
      'avatar.speak',
      'tts.speak',
      'screen.command',
      'media.command',
      'monitor.status',
    ]),
    allowedCallers: readCsvEnv('OPERATIONS_GATEWAY_ALLOWED_CALLERS', ['new-rag']),
    timeoutMs: readIntEnv('OPERATIONS_GATEWAY_TIMEOUT_MS', 15000),
    idempotencyFile: env.OPERATIONS_GATEWAY_IDEMPOTENCY_FILE
      || path.resolve(env.DATA_DIR || path.resolve(__dirname, '../../data'), 'operations-idempotency.json'),
  },

  // Whether model/RAG answers may be promoted into the authoritative QA store.
  QA_AUTO_CACHE_ENABLED: env.QA_AUTO_CACHE_ENABLED !== 'false',

  SCREEN_ASSIGNMENTS: readJsonEnv('SCREEN_ASSIGNMENTS', {
    '2bc06324-f92a-4cff-85fb-fe1d5d268a6d': 2,
  }),

  // 火山引擎 ASR/TTS（待配置）
  VOLCENGINE: {
    enabled: false,
  },

  // 心跳配置（毫秒）
  HEARTBEAT,

  // 设备类型
  DEVICE_TYPES,

  LOG: {
    level: 'info',
    file: './logs/relay.log',
  },
};
