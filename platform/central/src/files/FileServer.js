import { localTts, wavHeader } from '../tts/LocalTts.js';
import { once } from 'node:events';
import { speechInterruption } from '../tts/SpeechInterruption.js';
// ============================================================
// 静态文件服务 - 提供媒体资源给客户端
// 视频/图片/PPT 等文件统一存储在 media/ 目录
// 客户端通过 http://<服务器IP>:3001/文件名 访问
// ============================================================
import http from 'http';
import fs from 'fs';
import path from 'path';
import { createHash, timingSafeEqual } from 'crypto';
import { config } from '../config/index.js';
import { deviceManager } from '../devices/DeviceManager.js';
import { usageStore } from '../usage/UsageStore.js';
import { conversationSessionStore } from '../context/ConversationSessionStore.js';
import { conversationMetadataStore } from '../context/ConversationMetadataStore.js';
import { isClientMediaAvailable, mediaLibrary } from '../media/MediaLibrary.js';
import { pptThumbnailClient, rewriteLocalThumbnailUrls } from '../media/PptThumbnailClient.js';
import { qaStore } from '../qa/QaStore.js';
import { webQaCandidateStore } from '../qa/WebQaCandidateStore.js';
import { promoteWebQaCandidate } from '../qa/WebQaPromotionService.js';
import { fallbackStore } from '../fallback/FallbackStore.js';
import { wsServer } from '../ws/WsServer.js';
import { knowledgeDb } from '../knowledge/KnowledgeDb.js';
import { obsidianAnalyzer } from '../knowledge/ObsidianAnalyzer.js';
import { vectorKnowledgeService } from '../knowledge/VectorKnowledgeService.js';
import { uploadAnalysisService } from '../analysis/UploadAnalysisService.js';
import { webSearchService } from '../websearch/WebSearchService.js';
import { operationsHttpApi } from '../operations/OperationsHttpApi.js';
import { avatarServiceController } from '../avatar/AvatarServiceController.js';
import { adminAccountStore } from '../auth/AdminAccountStore.js';
import { teachingAnalysisService } from '../teaching/TeachingAnalysisService.js';
import { qaDraftStore } from '../teaching/QaDraftStore.js';

const SMART_EXHIBITION_INTERNAL_BASE_URL = process.env.SMART_EXHIBITION_INTERNAL_BASE_URL || 'http://127.0.0.1:3090';
const FILE_UPLOAD_MAX_BYTES = Math.max(1, Number(process.env.FILE_UPLOAD_MAX_BYTES || 1024 * 1024 * 1024));
const SCREEN_BACKGROUND_MAX_BYTES = 20 * 1024 * 1024;

const MIME_TYPES = {
  '.mp4': 'video/mp4',
  '.webm': 'video/webm',
  '.mp3': 'audio/mpeg',
  '.wav': 'audio/wav',
  '.pcm': 'audio/L16',
  '.avi': 'video/x-msvideo',
  '.mov': 'video/quicktime',
  '.mkv': 'video/x-matroska',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.svg': 'image/svg+xml',
  '.pdf': 'application/pdf',
  '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  '.ppt': 'application/vnd.ms-powerpoint',
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.json': 'application/json',
  '.txt': 'text/plain',
};

class FileServer {
  constructor() {
    this.server = null;
    this.mediaDir = config.MEDIA_DIR;
  }

  start() {
    // 确保 media 目录存在
    if (!fs.existsSync(this.mediaDir)) {
      fs.mkdirSync(this.mediaDir, { recursive: true });
      console.log(`[FileServer] 创建媒体目录: ${this.mediaDir}`);
    }

    this.server = http.createServer((req, res) => {
      // CORS
      res.setHeader('Access-Control-Allow-Origin', '*');
      res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PATCH, DELETE, OPTIONS');
      res.setHeader('Access-Control-Allow-Headers', '*');

      if (req.method === 'OPTIONS') {
        res.writeHead(204);
        res.end();
        return;
      }

      // POST /v1/dataset/.../chat/messages — 外包 AI 后端协议兼容层
      if (req.url?.startsWith('/v1/dataset/')) {
        return this._handleAiBackendCompat(req, res);
      }

      // Versioned operations facade for other RAG systems. Existing internal
      // MessageRouter/TTS/avatar call paths remain unchanged.
      if (req.url?.startsWith('/api/operations/v1/')) {
        return operationsHttpApi.handle(req, res);
      }

      if (req.url?.startsWith('/api/admin/auth')) {
        return this._handleAdminAuth(req, res);
      }

      if (req.url?.startsWith('/api/admin/accounts')) {
        return this._handleAdminAccounts(req, res);
      }

      // GET /api/screens — 列出注册过的展示屏
      if (req.url?.startsWith('/api/screens')) {
        return this._handleScreens(req, res);
      }

      // GET/POST /api/avatar-target - manually select the only screen allowed
      // to run the digital human. Question targets never change this setting.
      if (req.url?.startsWith('/api/avatar-target')) {
        return this._handleAvatarTarget(req, res);
      }

      // GET/POST /api/avatar-service - inspect or control the independent
      // digital-human bridge task without coupling it to the main platform.
      if (req.url?.startsWith('/api/avatar-service')) {
        return this._handleAvatarService(req, res);
      }

      // POST /api/asr-text — ASR APK 发送识别文本
      // GET /api/voiceInput?text=... 保留兼容旧 APK 配置
      if (req.url?.startsWith('/api/asr-text') || req.url?.startsWith('/api/voiceInput')) {
        return this._handleAsrText(req, res);
      }

      // POST /api/remote-control — ASR APK gesture controls
      if (req.url?.split('?')[0] === '/api/speech-interrupt') {
        return this._handleSpeechInterrupt(req, res);
      }
      if (req.url?.startsWith('/api/remote-control')) {
        return this._handleRemoteControl(req, res);
      }

      // GET /api/usage-stats — 展厅使用情况统计
      if (req.url?.startsWith('/api/usage-stats')) {
        return this._handleUsageStats(req, res);
      }

      // GET /api/usage-analytics — 从原始事件只读聚合运营指标
      if (req.url?.startsWith('/api/usage-analytics')) {
        return this._handleUsageAnalytics(req, res);
      }

      // GET /api/usage-events?limit=50 — 最近提问原始记录
      if (req.url?.startsWith('/api/usage-events')) {
        return this._handleUsageEvents(req, res);
      }

      // PATCH/DELETE /api/conversation-sessions — 重命名或删除后台会话
      if (req.url?.startsWith('/api/conversation-sessions')) {
        return this._handleConversationSessions(req, res);
      }

      // GET /api/usage-logs — 最近调用记录网页
      if (req.url?.startsWith('/api/usage-logs')) {
        return this._handleUsageLogsPage(req, res);
      }

      if (req.url?.startsWith('/api/model-provider')) {
        return this._handleModelProvider(req, res);
      }

      if (req.url?.startsWith('/api/face/')) {
        return this._handleFaceApi(req, res);
      }

      if (req.url === '/face-admin' || req.url?.startsWith('/face-admin?')) {
        return this._handleFaceAdmin(req, res);
      }

      if (req.url?.startsWith('/api/smart-exhibition/')) {
        return this._proxySmartExhibition(req, res, '/api/smart-exhibition');
      }

      if (req.url === '/smart-exhibition' || req.url?.startsWith('/smart-exhibition/')) {
        return this._proxySmartExhibition(req, res, '/smart-exhibition');
      }

      if (req.url?.startsWith('/api/specials')) {
        return this._handleSpecials(req, res);
      }

      if (req.url === '/commentary.json' || req.url?.startsWith('/api/commentaries')) {
        return this._handleCommentaries(req, res);
      }

      if (req.url?.startsWith('/api/files/commentary')) {
        return this._handleFileCommentary(req, res);
      }

      if (req.url?.startsWith('/api/files/review')) {
        return this._handleFileReview(req, res);
      }

      if (req.url?.startsWith('/api/files/analysis')) {
        return this._handleFileAnalysis(req, res);
      }

      if (req.url?.startsWith('/api/files/duplicate')) {
        return this._handleFileDuplicate(req, res);
      }

      if (req.url?.startsWith('/api/files/upload')) {
        return this._handleFileUpload(req, res);
      }

      if (req.url?.startsWith('/api/ppt-thumbnails')) {
        return this._handlePptThumbnails(req, res);
      }

      if (req.url?.startsWith('/api/fallback-rules')) {
        return this._handleFallbackRules(req, res);
      }

      if (req.url?.startsWith('/api/web-qa-candidates')) {
        return this._handleWebQaCandidates(req, res);
      }

      if (req.url?.startsWith('/api/web-search')) {
        return this._handleWebSearch(req, res);
      }

      if (req.url?.startsWith('/api/qa')) {
        return this._handleQa(req, res);
      }

      if (req.url?.startsWith('/api/knowledge/teaching/analyze')) {
        return this._handleTeachingAnalyze(req, res);
      }

      if (req.url?.startsWith('/api/knowledge/qa-drafts')) {
        return this._handleQaDrafts(req, res);
      }

      if (req.url?.startsWith('/api/knowledge')) {
        return this._handleKnowledge(req, res);
      }

      if (req.url?.split('?')[0] === '/api/tts-stream') return this._handleLocalTtsStream(req, res);
      if (req.url?.split('?')[0] === '/api/tts-live') return this._handleLocalTtsLive(req, res);
      if (req.url?.startsWith('/api/tts')) {
        if (req.url?.startsWith('/api/tts-settings')) {
          return this._handleTtsSettings(req, res);
        }
        return this._handleTts(req, res);
      }

      if (req.url?.startsWith('/tts-cache/')) {
        return this._handleTtsCache(req, res);
      }

      if (req.url?.startsWith('/screen-snapshots/')) {
        return this._handleScreenSnapshotFile(req, res);
      }

      if (req.url?.startsWith('/screen-backgrounds/')) {
        return this._handleScreenBackgroundFile(req, res);
      }

      // GET /list or /api/files — list media; DELETE /api/files — remove one file and its indexes.
      if (req.url === '/list' || req.url?.startsWith('/api/files')) {
        if (req.method === 'DELETE') return this._handleFileDelete(req, res);
        return this._handleList(req, res);
      }

      // GET /文件名 — 下载/播放文件
      this._handleFile(req, res);
    });

    this.server.listen(config.FILE_PORT, '0.0.0.0', () => {
      console.log(`[${new Date().toISOString()}] [INFO] [FileServer] 静态文件服务启动: http://0.0.0.0:${config.FILE_PORT}`);
      console.log(`[${new Date().toISOString()}] [INFO] [FileServer] 媒体目录: ${this.mediaDir}`);
      this._logMediaFiles();
    });
  }

  async _handlePptThumbnails(req, res) {
    try {
      if (!['GET', 'POST'].includes(req.method || '')) {
        res.writeHead(405, { 'Content-Type': 'application/json; charset=utf-8', Allow: 'GET, POST' });
        res.end(JSON.stringify({ ok: false, error: 'method not allowed' }));
        return;
      }
      const parsed = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
      const name = parsed.searchParams.get('name') || '';
      const page = parsed.searchParams.get('page');
      const quality = parsed.searchParams.get('quality');
      if (quality !== null && !['thumbnail', 'full'].includes(quality)) {
        throw Object.assign(new Error('invalid thumbnail quality'), { statusCode: 400 });
      }
      const upstream = await pptThumbnailClient.request(name, {
        method: req.method,
        page: page === null ? undefined : Number(page),
        quality: quality || 'thumbnail',
      });
      const body = upstream.contentType.includes('application/json')
        ? rewriteLocalThumbnailUrls(upstream.body, req.headers.host)
        : upstream.body;
      res.writeHead(upstream.statusCode, {
        'Content-Type': upstream.contentType,
        'Cache-Control': 'no-store',
      });
      res.end(body);
    } catch (error) {
      const statusCode = Number(error?.statusCode) || 500;
      res.writeHead(statusCode, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: error.message || 'thumbnail request failed' }));
    }
  }

  // 列出媒体文件
  _handleList(req, res) {
    try {
      const files = this._scanFiles(this.mediaDir)
        .filter(file => req.url !== '/list' || isClientMediaAvailable(file));
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ files, total: files.length }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: err.message }));
    }
  }

  async _handleFileDelete(req, res) {
    try {
      const parsed = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
      const requestedName = String(parsed.searchParams.get('name') || '').trim();
      if (!requestedName) {
        res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: 'file name is required' }));
        return;
      }

      const file = mediaLibrary.getAll().find(item => (
        item.name === requestedName || item.path === requestedName || item.originalName === requestedName
      ));
      if (!file) {
        res.writeHead(404, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: 'file not found' }));
        return;
      }

      uploadAnalysisService.assertFileRemovable(file.name);
      const mediaRoot = path.resolve(this.mediaDir);
      const diskPath = path.resolve(this.mediaDir, String(file.path || file.name));
      if (diskPath === mediaRoot || !diskPath.startsWith(`${mediaRoot}${path.sep}`)) {
        res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: 'unsafe media path cannot be deleted' }));
        return;
      }

      const artifacts = uploadAnalysisService.removeFileArtifacts(file.name, file.analysisJobId);
      const removedLibraryEntry = mediaLibrary.remove(file.name);
      const removedPhysicalFile = fs.existsSync(diskPath);
      if (removedPhysicalFile) fs.rmSync(diskPath, { force: true });

      const { specialConfigStore } = await import('../specials/SpecialConfigStore.js');
      const removedSpecialAssignments = specialConfigStore.removeFileReferences(file.name);
      knowledgeDb.syncFromCurrentStores({ mediaLibrary, qaStore, specialConfigStore });
      knowledgeDb.syncObsidian();
      const vector = await vectorKnowledgeService.rebuildFromKnowledgeDb();

      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({
        ok: true,
        deleted: {
          name: file.name,
          physicalFile: removedPhysicalFile,
          libraryEntry: Boolean(removedLibraryEntry),
          analysisJobs: artifacts.removedJobs,
          generatedDirectories: artifacts.removedDirectories.length,
          specialAssignments: removedSpecialAssignments,
          knowledgeIndex: true,
          vectorIndex: Boolean(vector?.ok || vector?.skipped),
        },
        vector,
        serverTime: Date.now(),
      }));
    } catch (err) {
      const status = err?.code === 'FILE_ANALYSIS_RUNNING' ? 409 : 500;
      res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: err.message, code: err?.code || 'FILE_DELETE_FAILED' }));
    }
  }

  _proxySmartExhibition(req, res, prefix) {
    const incoming = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
    const targetPath = prefix === '/api/smart-exhibition'
      ? `${incoming.pathname.slice(prefix.length)}${incoming.search}`
      : `/${incoming.pathname.slice(prefix.length).replace(/^\/+/, '')}${incoming.search}`;
    const target = new URL(targetPath || '/', SMART_EXHIBITION_INTERNAL_BASE_URL);
    const bodyChunks = [];

    req.on('data', chunk => bodyChunks.push(chunk));
    req.on('end', () => {
      const body = Buffer.concat(bodyChunks);
      const headers = { ...req.headers, host: target.host };
      delete headers['content-length'];
      if (body.length) headers['content-length'] = String(body.length);

      const proxyReq = http.request({
        protocol: target.protocol,
        hostname: target.hostname,
        port: target.port,
        path: `${target.pathname}${target.search}`,
        method: req.method,
        headers,
      }, proxyRes => {
        const responseHeaders = { ...proxyRes.headers };
        res.writeHead(proxyRes.statusCode || 502, responseHeaders);
        proxyRes.pipe(res);
      });

      proxyReq.on('error', err => {
        res.writeHead(502, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: `smart exhibition proxy failed: ${err.message}` }));
      });

      if (body.length) proxyReq.write(body);
      proxyReq.end();
    });
  }

  async _handleScreens(req, res) {
    try {
      const parsed = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
      if (parsed.pathname === '/api/screens/background') {
        return this._handleScreenBackgroundUpload(req, res, parsed);
      }
      if (parsed.pathname === '/api/screens/settings') {
        if (req.method !== 'POST') {
          res.writeHead(405, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, error: 'method not allowed' }));
          return;
        }

        const payload = await this._readJsonOrQuery(req);
        const screen = deviceManager.updateScreenSettings(payload.deviceId || payload.id, payload);
        const synced = this._syncScreenSettings(screen.deviceId);
        this._broadcastScreenEvent('settings', screen);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, screen, synced, serverTime: Date.now() }));
        return;
      }

      const screens = deviceManager.getScreens();
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ screens, total: screens.length, serverTime: Date.now() }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  async _handleScreenBackgroundUpload(req, res, parsed) {
    if (req.method !== 'POST') {
      res.writeHead(405, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: 'method not allowed' }));
      return;
    }

    const deviceId = String(parsed.searchParams.get('deviceId') || '').trim();
    const currentSettings = deviceManager.getScreenSettings(deviceId);
    if (!currentSettings) {
      req.resume();
      res.writeHead(404, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: 'screen not found' }));
      return;
    }

    const originalName = this._safeUploadName(parsed.searchParams.get('name') || 'background.jpg');
    const ext = path.extname(originalName).toLowerCase();
    if (!['.png', '.jpg', '.jpeg', '.webp'].includes(ext)) {
      req.resume();
      res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: '仅支持 PNG、JPG、JPEG、WEBP 图片' }));
      return;
    }

    const contentLength = Number(req.headers['content-length'] || 0);
    if (Number.isFinite(contentLength) && contentLength > SCREEN_BACKGROUND_MAX_BYTES) {
      req.resume();
      res.writeHead(413, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: '背景图片不能超过 20MB' }));
      return;
    }

    const root = path.join(config.DATA_DIR, 'screen-backgrounds');
    fs.mkdirSync(root, { recursive: true });
    const fileName = `${deviceId.replace(/[^a-zA-Z0-9_-]/g, '_')}-${Date.now()}${ext}`;
    const targetPath = path.join(root, fileName);
    const stream = fs.createWriteStream(targetPath, { flags: 'wx' });
    let receivedBytes = 0;
    let responded = false;
    const removePartial = () => fs.rmSync(targetPath, { force: true });

    req.on('data', chunk => {
      receivedBytes += chunk.length;
      if (receivedBytes <= SCREEN_BACKGROUND_MAX_BYTES || responded) return;
      responded = true;
      req.unpipe(stream);
      stream.destroy();
      req.resume();
      removePartial();
      res.writeHead(413, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: '背景图片不能超过 20MB' }));
    });
    req.on('aborted', () => {
      if (responded) return;
      responded = true;
      stream.destroy();
      removePartial();
    });
    req.pipe(stream);
    stream.on('finish', () => {
      if (responded) return;
      responded = true;
      try {
        const backgroundImageUrl = `${config.PUBLIC_FILE_BASE_URL}/screen-backgrounds/${encodeURIComponent(fileName)}`;
        const updated = deviceManager.updateScreenSettings(deviceId, {
          ...currentSettings,
          transparentBackground: false,
          backgroundImageUrl,
          backgroundImageName: originalName,
        });
        const synced = this._syncScreenSettings(deviceId);
        this._broadcastScreenEvent('settings', updated);
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, screen: updated, synced, serverTime: Date.now() }));
      } catch (err) {
        removePartial();
        res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: err.message }));
      }
    });
    stream.on('error', err => {
      removePartial();
      if (responded) return;
      responded = true;
      res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    });
  }

  async _handleAvatarTarget(req, res) {
    try {
      const { avatarTargetCoordinator } = await import('../avatar/AvatarTargetCoordinator.js');
      let status;

      if (req.method === 'GET') {
        status = avatarTargetCoordinator.getTargetStatus();
      } else if (req.method === 'POST') {
        const payload = await this._readJsonOrQuery(req);
        status = avatarTargetCoordinator.selectTarget(payload.target);
        if (!status.ok) {
          res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify(status));
          return;
        }
      } else {
        res.writeHead(405, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: 'method not allowed' }));
        return;
      }

      const screens = deviceManager.getScreens()
        .filter(screen => Number.isFinite(Number(screen.screenNumber)))
        .sort((a, b) => Number(a.screenNumber) - Number(b.screenNumber))
        .map(screen => ({
          target: String(screen.screenNumber),
          screenNumber: Number(screen.screenNumber),
          label: `${screen.screenNumber}号屏`,
          online: screen.online === true,
          name: screen.name || '',
          ipAddress: screen.ipAddress || screen.ip || '',
        }));

      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ...status, targets: [
        { target: '61', label: '61主机', online: true },
        ...screens,
      ], serverTime: Date.now() }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  async _handleAvatarService(req, res) {
    try {
      let status;
      if (req.method === 'GET') {
        status = await avatarServiceController.status();
      } else if (req.method === 'POST') {
        const payload = await this._readJsonOrQuery(req);
        status = await avatarServiceController.control(String(payload.action || '').trim().toLowerCase());
      } else {
        res.writeHead(405, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: 'method not allowed' }));
        return;
      }

      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ...status, serverTime: Date.now() }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  async _handleAdminAuth(req, res) {
    try {
      const requestUrl = new URL(req.url, 'http://localhost');
      const action = requestUrl.pathname.split('/').filter(Boolean).at(-1);
      const token = this._getBearerToken(req);
      let result;
      if (req.method === 'POST' && action === 'login') {
        const payload = await this._readJsonOrQuery(req);
        result = adminAccountStore.login(payload.username, payload.password);
      } else if (req.method === 'GET' && action === 'me') {
        const account = adminAccountStore.authenticate(token);
        if (!account) throw Object.assign(new Error('登录已失效，请重新登录'), { statusCode: 401 });
        result = { ok: true, account };
      } else if (req.method === 'POST' && action === 'logout') {
        adminAccountStore.logout(token);
        result = { ok: true };
      } else {
        throw Object.assign(new Error('method not allowed'), { statusCode: 405 });
      }
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify(result));
    } catch (err) {
      res.writeHead(err.statusCode || 400, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  async _handleAdminAccounts(req, res) {
    try {
      const requestUrl = new URL(req.url, 'http://localhost');
      const segments = requestUrl.pathname.split('/').filter(Boolean);
      const id = segments.length > 3 ? decodeURIComponent(segments[3]) : '';
      const token = this._getBearerToken(req);
      let result;
      if (req.method === 'GET' && !id) {
        result = {
          ok: true,
          accounts: adminAccountStore.list(token),
          permissions: adminAccountStore.permissionCatalog,
          roleDefaults: adminAccountStore.roleDefaults,
        };
      } else if (req.method === 'POST' && !id) {
        result = { ok: true, account: adminAccountStore.create(token, await this._readJsonOrQuery(req)) };
      } else if (req.method === 'PATCH' && id) {
        result = { ok: true, account: adminAccountStore.update(token, id, await this._readJsonOrQuery(req)) };
      } else if (req.method === 'DELETE' && id) {
        result = adminAccountStore.delete(token, id);
      } else {
        throw Object.assign(new Error('method not allowed'), { statusCode: 405 });
      }
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify(result));
    } catch (err) {
      res.writeHead(err.statusCode || 400, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  _getBearerToken(req) {
    const value = String(req.headers.authorization || '');
    return value.toLowerCase().startsWith('bearer ') ? value.slice(7).trim() : '';
  }

  async _handleAiBackendCompat(req, res) {
    try {
      if (!config.AI_BACKEND_COMPAT.enabled) {
        res.writeHead(404, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ code: 404, message: 'ai backend compat disabled', data: {} }));
        return;
      }
      if (req.method !== 'POST') {
        res.writeHead(405, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ code: 405, message: 'method not allowed', data: {} }));
        return;
      }

      const parsed = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
      if (!['/v1/dataset/szr/chat/messages', '/v1/dataset/chat/messages'].includes(parsed.pathname)) {
        res.writeHead(404, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ code: 404, message: 'ai backend compat endpoint not found', data: {} }));
        return;
      }
      const variant = parsed.pathname.includes('/szr/') ? 'digital-human' : 'web';
      const { aiBackendCompatService } = await import('../compat/AiBackendCompatService.js');
      return aiBackendCompatService.handleChatRequest(req, res, { variant });
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ code: 500, message: err.message, data: {} }));
    }
  }

  _syncScreenSettings(deviceId) {
    const settings = deviceManager.getScreenSettings(deviceId);
    if (!settings) return false;
    return wsServer.sendToDevice(deviceId, {
      type: 'screen_settings',
      settings,
      serverTime: Date.now(),
    });
  }

  _broadcastScreenEvent(event, screen) {
    wsServer.pushToAdmin({
      type: 'device_event',
      event,
      deviceId: screen.deviceId,
      screen,
    });
  }

  async _syncKnowledgeQuietly() {
    try {
      const { specialConfigStore } = await import('../specials/SpecialConfigStore.js');
      knowledgeDb.syncFromCurrentStores({ mediaLibrary, qaStore, specialConfigStore });
      vectorKnowledgeService.rebuildFromKnowledgeDb();
    } catch (err) {
      console.warn(`[KnowledgeDb] sync skipped: ${err.message}`);
    }
  }

  _handleCommentaries(req, res) {
    try {
      const commentaries = mediaLibrary.getCommentaries();
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(commentaries));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  async _handleFileCommentary(req, res) {
    try {
      if (req.method !== 'POST') {
        res.writeHead(405, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: 'method not allowed' }));
        return;
      }

      const payload = await this._readJsonOrQuery(req);
      const file = mediaLibrary.updateCommentary(payload.name || payload.file || payload.path, payload.commentary || '', {
        commentaryPages: payload.commentaryPages,
        slideCount: payload.slideCount,
      });
      await this._syncKnowledgeQuietly();
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, file, serverTime: Date.now() }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  async _handleFileAnalysis(req, res) {
    try {
      const parsed = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
      const jobId = parsed.searchParams.get('jobId') || '';
      if (req.method === 'GET') {
        const job = jobId ? uploadAnalysisService.get(jobId) : null;
        if (jobId && !job) {
          res.writeHead(404, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, error: 'analysis job not found' }));
          return;
        }
        const jobs = jobId ? undefined : uploadAnalysisService.list(Number(parsed.searchParams.get('limit') || 50));
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, job, jobs, serverTime: Date.now() }));
        return;
      }
      if (req.method === 'POST' && parsed.pathname.endsWith('/retry')) {
        const job = uploadAnalysisService.retry(jobId);
        if (!job) {
          res.writeHead(404, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, error: 'analysis job not found' }));
          return;
        }
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, job, serverTime: Date.now() }));
        return;
      }
      res.writeHead(405, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'method not allowed' }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  async _handleFileReview(req, res) {
    try {
      const parsed = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
      const operationMatch = parsed.pathname.match(/\/operations\/([^/]+)$/);
      if (req.method === 'GET' && operationMatch) {
        const operation = uploadAnalysisService.getBatchReviewOperation(decodeURIComponent(operationMatch[1]));
        if (!operation) {
          res.writeHead(404, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ ok: false, error: 'review operation not found' }));
          return;
        }
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, operation, serverTime: Date.now() }));
        return;
      }
      if (req.method === 'POST' && parsed.pathname.endsWith('/batch')) {
        const payload = await this._readJsonOrQuery(req);
        const operation = uploadAnalysisService.startBatchReview(payload.fileNames, payload.action || 'approve');
        res.writeHead(202, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, operation, serverTime: Date.now() }));
        return;
      }
      if (parsed.pathname.endsWith('/settings')) {
        if (req.method === 'GET') {
          res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ ok: true, settings: uploadAnalysisService.getPublishSettings(), serverTime: Date.now() }));
          return;
        }
        if (req.method === 'POST') {
          const payload = await this._readJsonOrQuery(req);
          const settings = uploadAnalysisService.updatePublishSettings(payload.mode);
          res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ ok: true, settings, serverTime: Date.now() }));
          return;
        }
      }
      if (req.method === 'GET') {
        const reviewFaqs = knowledgeDb.listFaqs().filter(item => item.status === 'reviewing');
        const items = uploadAnalysisService.listReviews().map(file => {
          const faqs = reviewFaqs.filter(item => item.sourceFile === file.name);
          return {
            ...file,
            faqs,
            generatedFaqCount: Math.max(Number(file.generatedFaqCount || 0), faqs.length),
          };
        });
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, items, total: items.length, serverTime: Date.now() }));
        return;
      }
      if (req.method === 'POST') {
        const payload = await this._readJsonOrQuery(req);
        const action = String(payload.action || parsed.searchParams.get('action') || '').toLowerCase();
        const file = await uploadAnalysisService.review(payload.fileName || payload.name || parsed.searchParams.get('name'), action);
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, file, serverTime: Date.now() }));
        return;
      }
      res.writeHead(405, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: 'method not allowed' }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  async _handleFileUpload(req, res) {
    try {
      if (req.method !== 'POST') {
        res.writeHead(405, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: 'method not allowed' }));
        return;
      }

      const parsed = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
      const rawName = parsed.searchParams.get('name') || req.headers['x-file-name'] || '';
      const safeName = this._safeUploadName(rawName);
      if (!safeName) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: 'file name is required' }));
        return;
      }

      const ext = path.extname(safeName).toLowerCase();
      if (!['.pptx', '.pdf', '.docx'].includes(ext)) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: 'unsupported file type' }));
        return;
      }

      const contentLength = Number(req.headers['content-length'] || 0);
      if (Number.isFinite(contentLength) && contentLength > FILE_UPLOAD_MAX_BYTES) {
        res.writeHead(413, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: `file exceeds ${Math.floor(FILE_UPLOAD_MAX_BYTES / 1024 / 1024)}MB limit` }));
        return;
      }

      if (!fs.existsSync(this.mediaDir)) {
        fs.mkdirSync(this.mediaDir, { recursive: true });
      }

      // Treat a PPT name as one logical document. A re-upload replaces that
      // document's revision instead of creating name_1/name_2 copies.
      const finalName = safeName;
      const targetPath = path.join(this.mediaDir, finalName);
      const replacingExisting = fs.existsSync(targetPath);
      const existingRecord = mediaLibrary.getAll().find(item => item.name === finalName || item.path === finalName);
      const logicalFileExists = Boolean(replacingExisting || existingRecord);
      const allowReplace = /^(?:1|true|yes)$/i.test(String(parsed.searchParams.get('replace') || ''));
      if (logicalFileExists && !allowReplace) {
        req.resume();
        res.writeHead(409, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({
          ok: false,
          code: 'FILE_ALREADY_EXISTS',
          error: '文件已存在，请确认是否作为新版本重新上传',
          existing: this._duplicateFileSummary(existingRecord, finalName, replacingExisting ? fs.statSync(targetPath).size : 0),
        }));
        return;
      }
      if (replacingExisting) uploadAnalysisService.assertFileRemovable(finalName);
      const uploadPath = replacingExisting
        ? path.join(this.mediaDir, `.${finalName}.${process.pid}.${Date.now()}.upload`)
        : targetPath;
      const stream = fs.createWriteStream(uploadPath, { flags: 'wx' });
      const contentHash = createHash('sha256');
      let receivedBytes = 0;
      let responded = false;
      const removePartialFile = () => fs.rmSync(uploadPath, { force: true });

      req.on('data', chunk => {
        receivedBytes += chunk.length;
        contentHash.update(chunk);
        if (receivedBytes <= FILE_UPLOAD_MAX_BYTES || responded) return;
        responded = true;
        req.unpipe(stream);
        stream.destroy();
        req.resume();
        removePartialFile();
        res.writeHead(413, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: `file exceeds ${Math.floor(FILE_UPLOAD_MAX_BYTES / 1024 / 1024)}MB limit` }));
      });
      req.on('aborted', () => {
        if (responded) return;
        responded = true;
        stream.destroy();
        removePartialFile();
      });
      req.pipe(stream);
      stream.on('finish', async () => {
        if (responded) return;
        responded = true;
        try {
          const contentSha256 = contentHash.digest('hex');
          const duplicateByContent = mediaLibrary.getAll().find(item => (
            item.name !== finalName
            && item.contentSha256
            && item.contentSha256 === contentSha256
          ));
          if (duplicateByContent) {
            removePartialFile();
            res.writeHead(409, { 'Content-Type': 'application/json; charset=utf-8' });
            res.end(JSON.stringify({
              ok: false,
              code: 'FILE_CONTENT_DUPLICATE',
              error: `文件内容与“${duplicateByContent.name}”完全相同，无需重复上传`,
              existing: this._duplicateFileSummary(duplicateByContent, duplicateByContent.name, duplicateByContent.size),
            }));
            return;
          }
          if (replacingExisting) {
            const existingSha256 = await this._sha256File(targetPath);
            if (existingSha256 === contentSha256) {
              removePartialFile();
              const existing = mediaLibrary.getAll().find(item => item.name === finalName || item.path === finalName);
              res.writeHead(409, { 'Content-Type': 'application/json; charset=utf-8' });
              res.end(JSON.stringify({
                ok: false,
                code: 'FILE_CONTENT_DUPLICATE',
                error: '文件内容与系统中已有文件完全相同，无需重复上传',
                existing: this._duplicateFileSummary(existing, finalName, fs.statSync(targetPath).size),
              }));
              return;
            }
            const current = mediaLibrary.getAll().find(item => item.name === finalName);
            uploadAnalysisService.removeFileArtifacts(finalName, current?.analysisJobId || '');
            fs.copyFileSync(uploadPath, targetPath);
            fs.rmSync(uploadPath, { force: true });
          }
          const stat = fs.statSync(targetPath);
          const faqOnly = ['.pdf', '.docx'].includes(ext);
          let file = {
            name: finalName,
            originalName: safeName,
            path: finalName,
            ext,
            size: stat.size,
            type: this._fileCategory(ext),
            url: `${config.PUBLIC_FILE_BASE_URL}/${encodeURIComponent(finalName)}`,
            localPath: `${config.MEDIA_WIN_PATH}\\${finalName}`,
            faqOnly,
            clientDisplay: !faqOnly,
            contentSha256,
          };
          file = mediaLibrary.registerUploadedFile(file) || file;
          const analysisJob = uploadAnalysisService.enqueue(file);
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: true, file, analysisJob, serverTime: Date.now() }));
          if (ext === '.pptx' || ext === '.ppt') {
            pptThumbnailClient.warm(file.path || file.name).catch(error => {
              console.warn(`[PptThumbnail] upload prewarm skipped: ${error.message}`);
            });
          }
        } catch (err) {
          if (uploadPath !== targetPath) removePartialFile();
          res.writeHead(500, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, error: err.message }));
        }
      });
      stream.on('error', err => {
        removePartialFile();
        if (responded) return;
        responded = true;
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: err.message }));
      });
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  async _handleFileDuplicate(req, res) {
    try {
      if (req.method !== 'GET') {
        res.writeHead(405, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: 'method not allowed' }));
        return;
      }
      const parsed = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
      const name = this._safeUploadName(parsed.searchParams.get('name') || '');
      if (!name) {
        res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: 'file name is required' }));
        return;
      }
      const filePath = path.join(this.mediaDir, name);
      const existing = mediaLibrary.getAll().find(item => item.name === name || item.path === name);
      const exists = Boolean(existing || fs.existsSync(filePath));
      const actualSize = fs.existsSync(filePath) ? fs.statSync(filePath).size : Number(existing?.size || 0);
      const incomingSize = Number(parsed.searchParams.get('size') || 0);
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({
        ok: true,
        duplicate: exists,
        sameSize: Boolean(exists && incomingSize > 0 && actualSize === incomingSize),
        existing: exists ? this._duplicateFileSummary(existing, name, actualSize) : null,
        serverTime: Date.now(),
      }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  _duplicateFileSummary(item, fallbackName, size = 0) {
    return {
      id: item?.id || '',
      name: item?.name || fallbackName,
      size: Number(item?.size || size || 0),
      status: item?.publicationStatus || item?.contentStatus || item?.status || 'EXISTS',
      uploadedAt: item?.uploadedAt || '',
    };
  }

  _sha256File(filePath) {
    return new Promise((resolve, reject) => {
      const hash = createHash('sha256');
      const input = fs.createReadStream(filePath);
      input.on('data', chunk => hash.update(chunk));
      input.on('error', reject);
      input.on('end', () => resolve(hash.digest('hex')));
    });
  }

  async _handleFallbackRules(req, res) {
    try {
      const parsed = new URL(req.url, `http://${req.headers.host || 'localhost'}`);

      if (req.method === 'GET') {
        const items = fallbackStore.list();
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, items, total: items.length, serverTime: Date.now() }));
        return;
      }

      if (req.method === 'POST' && parsed.pathname.endsWith('/test')) {
        const payload = await this._readJsonOrQuery(req);
        const match = fallbackStore.findMatch(payload.text, {
          target: payload.target,
          currentPptFileName: payload.currentPptFileName,
          currentPptTitle: payload.currentPptTitle,
          activeSpecialName: payload.activeSpecialName,
        });
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, match, serverTime: Date.now() }));
        return;
      }

      if (req.method === 'POST') {
        const payload = await this._readJsonOrQuery(req);
        const item = fallbackStore.save(payload);
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, item, serverTime: Date.now() }));
        return;
      }

      if (req.method === 'DELETE') {
        const removed = fallbackStore.remove(parsed.searchParams.get('id'));
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, removed, serverTime: Date.now() }));
        return;
      }

      res.writeHead(405, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: 'method not allowed' }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  async _handleWebSearch(req, res) {
    try {
      const parsed = new URL(req.url, `http://${req.headers.host || 'localhost'}`);

      if (req.method === 'GET' && (parsed.pathname === '/api/web-search' || parsed.pathname === '/api/web-search/rules')) {
        const items = webSearchService.listRules();
        const ruleStats = usageStore.getWebSearchRuleStats(items.map(item => item.id));
        const enrichedItems = items.map(item => ({
          ...item,
          stats: ruleStats.byRule[item.id] || {
            matched: 0,
            answered: 0,
            notAnswered: 0,
            failed: 0,
            successRate: null,
          },
        }));
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({
          ok: true,
          items: enrichedItems,
          total: enrichedItems.length,
          statsWindow: {
            scannedEvents: ruleStats.scannedEvents,
            limit: ruleStats.limit,
          },
          serverTime: Date.now(),
        }));
        return;
      }

      if (req.method === 'POST' && parsed.pathname.endsWith('/test')) {
        const payload = await this._readJsonOrQuery(req);
        const text = String(payload.text || '').trim();
        if (!text) {
          res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ ok: false, error: 'text is required' }));
          return;
        }

        const route = webSearchService.classifyRoute(text, { target: payload.target || 'all' });
        const intent = route.intent || webSearchService.testIntent(text, { target: payload.target || 'all' });
        const force = payload.force === true || route.mode === 'MUST_WEB';
        if (!intent && !force) {
          res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ ok: true, matched: false, route, result: null, serverTime: Date.now() }));
          return;
        }

        const result = await webSearchService.answer({
          text,
          target: payload.target || 'all',
          force,
        });
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({
          ok: true,
          matched: Boolean(intent || force),
          route,
          intent,
          result,
          serverTime: Date.now(),
        }));
        return;
      }

      if (req.method === 'POST') {
        const payload = await this._readJsonOrQuery(req);
        const item = webSearchService.saveRule(payload);
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, item, serverTime: Date.now() }));
        return;
      }

      if (req.method === 'DELETE') {
        const removed = webSearchService.removeRule(parsed.searchParams.get('id'));
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, removed, serverTime: Date.now() }));
        return;
      }

      res.writeHead(405, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: 'method not allowed' }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  async _handleWebQaCandidates(req, res) {
    try {
      const parsed = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
      if (req.method === 'GET') {
        const status = String(parsed.searchParams.get('status') || '').toUpperCase();
        const allItems = webQaCandidateStore.list();
        const items = status ? allItems.filter(item => item.status === status) : allItems;
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, items, total: items.length, serverTime: Date.now() }));
        return;
      }

      if (req.method === 'POST' && parsed.pathname.endsWith('/promote')) {
        const payload = await this._readJsonOrQuery(req);
        const candidate = webQaCandidateStore.get(payload.id);
        if (!candidate) {
          res.writeHead(404, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ ok: false, error: 'candidate not found' }));
          return;
        }
        if (!candidate.eligibleForPromotion || candidate.status !== 'CANDIDATE'
          || (candidate.expiresAt && Date.parse(candidate.expiresAt) <= Date.now())) {
          res.writeHead(409, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ ok: false, error: 'candidate is not eligible for promotion' }));
          return;
        }
        const promotedQuestion = String(payload.question || candidate.question || '').trim();
        const promotedAnswer = String(payload.answer || candidate.answer || '').trim();
        if (!promotedQuestion || !promotedAnswer) {
          res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ ok: false, error: 'question and answer are required' }));
          return;
        }
        const { qaItem: item } = promoteWebQaCandidate(candidate, {
          question: promotedQuestion,
          answer: promotedAnswer,
          promotionMode: 'MANUAL',
        });
        await this._syncKnowledgeQuietly();
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, item, serverTime: Date.now() }));
        return;
      }

      if (req.method === 'POST' && parsed.pathname.endsWith('/verify-source')) {
        const payload = await this._readJsonOrQuery(req);
        const item = webQaCandidateStore.setSourceVerified(
          payload.id,
          payload.url,
          payload.verified !== false,
        );
        res.writeHead(item ? 200 : 404, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify(item
          ? { ok: true, item, serverTime: Date.now() }
          : { ok: false, error: 'candidate or source not found' }));
        return;
      }

      if (req.method === 'POST' && parsed.pathname.endsWith('/reject')) {
        const payload = await this._readJsonOrQuery(req);
        const item = webQaCandidateStore.reject(payload.id);
        res.writeHead(item ? 200 : 404, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify(item
          ? { ok: true, item, serverTime: Date.now() }
          : { ok: false, error: 'candidate not found' }));
        return;
      }

      res.writeHead(405, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: 'method not allowed' }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  async _handleQa(req, res) {
    try {
      const parsed = new URL(req.url, `http://${req.headers.host || 'localhost'}`);

      if (req.method === 'GET') {
        const items = knowledgeDb.listFaqs();
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, items, total: items.length, serverTime: Date.now() }));
        return;
      }

      if (req.method === 'POST' && parsed.pathname.endsWith('/batch')) {
        const payload = await this._readJsonOrQuery(req);
        const ids = Array.isArray(payload.ids) ? payload.ids.map(String).filter(Boolean) : [];
        let changed = 0;
        if (!ids.length) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, error: 'ids are required' }));
          return;
        }
        if (ids.some(id => knowledgeDb.isReadOnlyFaq(id))) {
          res.writeHead(409, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ ok: false, error: 'OBSIDIAN_FAQ_READ_ONLY' }));
          return;
        }
        if (payload.action === 'delete') {
          changed = qaStore.removeMany(ids);
        } else if (payload.action === 'enable') {
          changed = qaStore.updateMany(ids, { enabled: true });
        } else if (payload.action === 'disable') {
          changed = qaStore.updateMany(ids, { enabled: false });
        } else {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, error: 'unsupported batch action' }));
          return;
        }
        if (changed > 0) await this._syncKnowledgeQuietly();
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, changed, total: qaStore.list().length, serverTime: Date.now() }));
        return;
      }

      if (req.method === 'POST') {
        const payload = await this._readJsonOrQuery(req);
        const sourceItem = payload.id
          ? knowledgeDb.listFaqs().find(item => String(item.id) === String(payload.id))
          : null;
        // Editing an imported FAQ creates a manual override with the same id.
        // KnowledgeDb suppresses the original imported row, so both the FAQ
        // library and page maintenance view immediately share one editable item.
        const item = qaStore.save({
          ...payload,
          sourcePage: payload.sourcePage ?? sourceItem?.sourcePage ?? sourceItem?.raw_json?.sourcePage,
          sourceFile: payload.sourceFile || sourceItem?.sourceFile || sourceItem?.raw_json?.frontmatter?.resource,
          source: payload.source || sourceItem?.source || 'manual',
          keywords: payload.keywords?.length ? payload.keywords : sourceItem?.keywords,
        });
        await this._syncKnowledgeQuietly();
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, item, serverTime: Date.now() }));
        return;
      }

      if (req.method === 'DELETE') {
        const id = parsed.searchParams.get('id');
        if (knowledgeDb.isReadOnlyFaq(id)) {
          res.writeHead(409, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ ok: false, error: 'OBSIDIAN_FAQ_READ_ONLY' }));
          return;
        }
        const removed = qaStore.remove(id);
        await this._syncKnowledgeQuietly();
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, removed, serverTime: Date.now() }));
        return;
      }

      res.writeHead(405, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'method not allowed' }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  async _handleKnowledge(req, res) {
    try {
      const parsed = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
      const { specialConfigStore } = await import('../specials/SpecialConfigStore.js');

      if (req.method === 'GET' && parsed.pathname.endsWith('/status')) {
        const status = knowledgeDb.getStatus();
        status.vector = await vectorKnowledgeService.status();
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, status, serverTime: Date.now() }));
        return;
      }

      if (req.method === 'GET' && parsed.pathname.endsWith('/search')) {
        const items = knowledgeDb.search(parsed.searchParams.get('q') || '', {
          limit: Number.parseInt(parsed.searchParams.get('limit') || '12', 10),
          status: parsed.searchParams.get('status') || 'published',
        });
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, items, total: items.length, serverTime: Date.now() }));
        return;
      }

      if (req.method === 'GET' && parsed.pathname.endsWith('/obsidian-analyses')) {
        const result = knowledgeDb.listObsidianAnalyses({
          q: parsed.searchParams.get('q') || '',
          limit: Number.parseInt(parsed.searchParams.get('limit') || '80', 10),
          offset: Number.parseInt(parsed.searchParams.get('offset') || '0', 10),
        });
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, ...result, serverTime: Date.now() }));
        return;
      }

      if (req.method === 'POST' && parsed.pathname.endsWith('/sync')) {
        const status = knowledgeDb.syncFromCurrentStores({ mediaLibrary, qaStore, specialConfigStore });
        status.vector = await vectorKnowledgeService.rebuildFromKnowledgeDb();
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, status, serverTime: Date.now() }));
        return;
      }

      if (req.method === 'POST' && parsed.pathname.endsWith('/obsidian-sync')) {
        const payload = await this._readJsonOrQuery(req);
        const status = knowledgeDb.syncObsidian({ vaultPath: payload.vaultPath });
        status.vector = await vectorKnowledgeService.rebuildFromKnowledgeDb();
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, status, serverTime: Date.now() }));
        return;
      }

      if (req.method === 'POST' && parsed.pathname.endsWith('/vector-sync')) {
        const status = await vectorKnowledgeService.rebuildFromKnowledgeDb();
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: status.ok, status, serverTime: Date.now() }));
        return;
      }

      if (req.method === 'GET' && parsed.pathname.endsWith('/vector-search')) {
        const items = await vectorKnowledgeService.search(parsed.searchParams.get('q') || '', {
          limit: Number.parseInt(parsed.searchParams.get('limit') || '10', 10),
          objectTypes: (parsed.searchParams.get('types') || '').split(',').map(item => item.trim()).filter(Boolean),
          minSimilarity: Number(parsed.searchParams.get('min') || 0),
        });
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, items, total: items.length, serverTime: Date.now() }));
        return;
      }

      if (req.method === 'POST' && parsed.pathname.endsWith('/obsidian-analyze')) {
        const payload = await this._readJsonOrQuery(req);
        const status = await obsidianAnalyzer.analyze({
          vaultPath: payload.vaultPath,
          limit: payload.limit,
          force: payload.force,
          missingAnalysisOnly: payload.missingAnalysisOnly,
        });
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, status, serverTime: Date.now() }));
        return;
      }

      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'knowledge endpoint not found' }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  _handleUsageStats(req, res) {
    try {
      const summary = usageStore.getSummary();
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, summary, serverTime: Date.now() }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  _handleUsageAnalytics(req, res) {
    if (req.method !== 'GET') {
      res.writeHead(405, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: 'method not allowed' }));
      return;
    }
    try {
      const parsed = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
      const analytics = usageStore.getAnalytics({
        from: parsed.searchParams.get('from'),
        to: parsed.searchParams.get('to'),
        source: parsed.searchParams.get('source'),
        target: parsed.searchParams.get('target'),
      });
      const screenLabels = new Map(deviceManager.getScreens().map(screen => {
        const number = String(screen.screenNumber);
        const clientName = String(screen.name || '')
          .replace(/\s*\(\d+\s*号屏\)\s*$/u, '')
          .trim() || screen.ipAddress || '展示客户端';
        return [number, `${number}号屏（${clientName}）`];
      }));
      const targetLabel = target => {
        const value = String(target || '').trim();
        if (value === 'all') return '整馆展示';
        return screenLabels.get(value) || (/^\d+$/.test(value) ? `${value}号屏` : value || '未指定');
      };
      analytics.targetLabels = Object.fromEntries([
        ['all', '整馆展示'],
        ...[...new Set([
          ...(analytics.filters?.targets || []),
          ...(analytics.targetDistribution || []).map(item => item.name),
        ])].map(target => [String(target), targetLabel(target)]),
      ]);
      analytics.targetDistribution = (analytics.targetDistribution || []).map(item => ({
        ...item,
        label: targetLabel(item.name),
      }));
      res.writeHead(200, {
        'Content-Type': 'application/json; charset=utf-8',
        'Cache-Control': 'no-store',
      });
      res.end(JSON.stringify({ ok: true, ...analytics, serverTime: Date.now() }));
    } catch (err) {
      res.writeHead(Number(err.statusCode) || 500, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  _handleUsageEvents(req, res) {
    try {
      const parsed = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
      const limit = Number.parseInt(parsed.searchParams.get('limit') || '50', 10);
      const events = usageStore.getRecentEvents(Number.isFinite(limit) ? Math.min(limit, 200) : 50)
        .map(event => ({
          ...event,
          conversationTitle: conversationMetadataStore.getTitle(
            event.conversationId || `legacy-${event.id}`,
          ),
        }));
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, events, total: events.length, serverTime: Date.now() }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  async _handleConversationSessions(req, res) {
    try {
      if (req.method === 'PATCH') {
        const payload = await this._readJsonOrQuery(req);
        const renamed = conversationMetadataStore.rename(payload.id, payload.title);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, ...renamed }));
        return;
      }
      if (req.method !== 'DELETE') {
        res.writeHead(405, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: 'method not allowed' }));
        return;
      }
      const parsed = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
      const conversationId = String(parsed.searchParams.get('id') || '').trim();
      if (!conversationId) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: 'conversation id is required' }));
        return;
      }
      const result = usageStore.deleteConversation(conversationId);
      conversationSessionStore.delete(conversationId);
      conversationMetadataStore.remove(conversationId);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, removed: result.removed, conversationId }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  _handleUsageLogsPage(req, res) {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(`<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>新智慧大脑调用日志</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; font-family: "Microsoft YaHei", Arial, sans-serif; background: #f4f7f9; color: #172b36; }
    header { height: 64px; display: flex; align-items: center; justify-content: space-between; padding: 0 24px; background: #0d2b3a; color: #fff; }
    h1 { margin: 0; font-size: 20px; font-weight: 650; }
    main { padding: 20px 24px; max-width: 1280px; margin: 0 auto; }
    .toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
    button { border: 0; background: #1677ff; color: #fff; height: 34px; padding: 0 14px; border-radius: 6px; cursor: pointer; }
    button:active { transform: translateY(1px); }
    .meta { color: #607482; font-size: 13px; }
    table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d8e2e8; }
    th, td { padding: 10px 12px; border-bottom: 1px solid #e8eef2; text-align: left; vertical-align: top; font-size: 13px; }
    th { background: #eef4f7; color: #36505d; font-weight: 650; white-space: nowrap; }
    tr:hover td { background: #f8fbfc; }
    .question { max-width: 260px; }
    .answer { max-width: 420px; color: #294653; }
    .tag { display: inline-block; padding: 2px 8px; border-radius: 999px; background: #e7f2ff; color: #1363b3; white-space: nowrap; }
    .error { color: #c03434; }
    .empty { padding: 40px; text-align: center; color: #789; background: #fff; border: 1px solid #d8e2e8; }
  </style>
</head>
<body>
  <header>
    <h1>新智慧大脑调用日志</h1>
    <span id="status" class="meta">加载中...</span>
  </header>
  <main>
    <div class="toolbar">
      <button onclick="loadLogs()">刷新</button>
      <span class="meta">自动每 5 秒刷新一次，最多显示最近 80 条。</span>
    </div>
    <div id="content" class="empty">加载中...</div>
  </main>
  <script>
    function esc(value) {
      return String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
    }
    function short(value, len) {
      const text = String(value || '');
      return text.length > len ? text.slice(0, len) + '...' : text;
    }
    async function loadLogs() {
      const status = document.getElementById('status');
      const content = document.getElementById('content');
      try {
        const res = await fetch('/api/usage-events?limit=80', { cache: 'no-store' });
        const data = await res.json();
        const rows = data.events || [];
        status.textContent = '最近 ' + rows.length + ' 条 | ' + new Date().toLocaleTimeString();
        if (!rows.length) {
          content.className = 'empty';
          content.textContent = '暂无调用记录';
          return;
        }
        content.className = '';
        content.innerHTML = '<table><thead><tr><th>时间</th><th>来源</th><th>展厅/目标</th><th>问题</th><th>答案</th><th>来源标签</th><th>耗时</th><th>状态</th></tr></thead><tbody>' +
          rows.map(item => '<tr>' +
            '<td>' + esc(new Date(item.time).toLocaleString()) + '</td>' +
            '<td>' + esc(item.source || '-') + '</td>' +
            '<td>' + esc(item.target || '-') + '</td>' +
            '<td class="question">' + esc(short(item.question, 80)) + '</td>' +
            '<td class="answer">' + esc(short(item.answer || item.error, 140)) + '</td>' +
            '<td><span class="tag">' + esc(item.answerSource || '-') + '</span></td>' +
            '<td>' + esc(item.durationMs ? item.durationMs + 'ms' : '-') + '</td>' +
            '<td class="' + (item.ok ? '' : 'error') + '">' + esc(item.ok ? '成功' : '失败') + '</td>' +
          '</tr>').join('') +
          '</tbody></table>';
      } catch (err) {
        status.textContent = '加载失败';
        content.className = 'empty error';
        content.textContent = err.message || String(err);
      }
    }
    loadLogs();
    setInterval(loadLogs, 5000);
  </script>
</body>
</html>`);
  }

  async _handleModelProvider(req, res) {
    try {
      const { modelProvider } = await import('../api/ModelProvider.js');
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, ...modelProvider.getCurrent(), serverTime: Date.now() }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  async _handleFaceApi(req, res) {
    const { faceIntegrationService } = await import('../face/FaceIntegrationService.js');
    return faceIntegrationService.handle(req, res);
  }

  _handleFaceAdmin(req, res) {
    if (req.method !== 'GET') {
      res.writeHead(405, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: 'method not allowed' }));
      return;
    }
    const pageUrl = new URL('../face/face-admin.html', import.meta.url);
    const html = fs.readFileSync(pageUrl, 'utf8');
    res.writeHead(200, {
      'Content-Type': 'text/html; charset=utf-8',
      'Cache-Control': 'no-store',
    });
    res.end(html);
  }

  async _handleSpecials(req, res) {
    try {
      const parsed = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
      const { specialConfigStore } = await import('../specials/SpecialConfigStore.js');

      if (req.method === 'GET') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
          ok: true,
          specials: specialConfigStore.list(),
          activeId: specialConfigStore.getActiveId(),
          screens: deviceManager.getOnlineScreens(),
          serverTime: Date.now(),
        }));
        return;
      }

      if (req.method === 'POST' && parsed.pathname.endsWith('/activate')) {
        const payload = await this._readJsonOrQuery(req);
        const { messageRouter } = await import('../router/MessageRouter.js');
        const result = await messageRouter.applySpecial(payload.id || payload.name, { source: 'admin-api' });
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, ...result, serverTime: Date.now() }));
        return;
      }

      if (req.method === 'POST') {
        const payload = await this._readJsonOrQuery(req);
        const special = specialConfigStore.save(payload);
        await this._syncKnowledgeQuietly();
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, special, serverTime: Date.now() }));
        return;
      }

      if (req.method === 'DELETE') {
        const removed = specialConfigStore.remove(parsed.searchParams.get('id'));
        await this._syncKnowledgeQuietly();
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, removed, serverTime: Date.now() }));
        return;
      }

      res.writeHead(405, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'method not allowed' }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  async _handleSpeechInterrupt(req, res) {
    const respond = (status, body) => {
      res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
      res.end(JSON.stringify(body));
    };
    try {
      if (req.method !== 'POST') return respond(405, { ok: false, error: 'METHOD_NOT_ALLOWED' });
      const payload = await this._readJsonOrQuery(req);
      const rawTarget = payload.target;
      if (!['string', 'number'].includes(typeof rawTarget) || !/^[1-9]\d*$/.test(String(rawTarget))
        || !Number.isSafeInteger(Number(rawTarget))) {
        return respond(400, { ok: false, error: 'TARGET_REQUIRED', message: 'target 必须是具体的正整数屏号，不支持 all 或 IP' });
      }
      const target = String(Number(rawTarget));
      const requestId = typeof payload.requestId === 'string' ? payload.requestId.trim() : '';
      if (!requestId || requestId.length > 128) return respond(400, { ok: false, error: 'REQUEST_ID_REQUIRED' });
      if (!deviceManager.getByScreenNumber(Number(target))) return respond(404, { ok: false, error: 'SCREEN_NOT_FOUND', target });
      const { messageRouter } = await import('../router/MessageRouter.js');
      const { ttsConfigStore } = await import('../tts/TtsConfigStore.js');
      const result = await speechInterruption.runOnce(target, requestId, () => {
        const device = deviceManager.getByScreenNumber(Number(target));
        if (!String(device?.version || '').includes('speech-interrupt-v1')) {
          return { ok: false, status: 'CLIENT_UPGRADE_REQUIRED', target, requestId, httpStatus: 409 };
        }
        if (ttsConfigStore.get().provider !== 'qwen3') {
          return { ok: false, status: 'TTS_PROVIDER_UNSUPPORTED', target, requestId, httpStatus: 409 };
        }
        const stopped = messageRouter.interruptSpeech(target, requestId);
        return {
          ok: stopped.delivered, status: stopped.delivered ? 'DISPATCHED' : 'TARGET_OFFLINE',
          target, requestId, ...stopped, staleResultsBlocked: true,
          playbackConfirmed: false, queueClearRequested: stopped.delivered, serverTime: Date.now(),
        };
      });
      const { httpStatus, ...body } = result;
      return respond(httpStatus || (result.ok ? 202 : 503), body);
    } catch (err) {
      return respond(err.statusCode || 500, { ok: false, error: err.message });
    }
  }

  async _handleLocalTtsLive(req, res) {
    try {
      if (req.method !== 'POST') throw new Error('仅支持 POST');
      const payload = await this._readJsonOrQuery(req);
      const { avatarTargetCoordinator } = await import('../avatar/AvatarTargetCoordinator.js');
      const target = avatarTargetCoordinator.getTargetStatus();
      const screen = deviceManager.getByScreenNumber(target.screenNumber);
      if (!screen?.online) throw new Error('请先选择已升级的展示屏作为数字人屏幕');
      const text = String(payload.text || '').trim();
      if (payload.action !== 'stop' && (!text || Array.from(text).length > 12000)) throw new Error('播报内容为空或过长');
      const sent = wsServer.sendToDevice(screen.id || screen.deviceId, {
        type: 'command', action: payload.action === 'stop' ? 'stop_speech' : 'speak',
        text, params: { text, preserveView: 'true' }, serverTime: Date.now(),
      });
      if (!sent) throw new Error('展示屏已离线');
      res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ ok: true }));
    } catch (err) { res.writeHead(400, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ ok: false, error: err.message })); }
  }

  async _handleLocalTtsStream(req, res) {
    const abort = new AbortController();
    let release = () => {};
    res.on('close', () => { if (!res.writableEnded) abort.abort(); });
    try {
      if (req.method !== 'POST') throw new Error('仅支持 POST');
      const payload = await this._readJsonOrQuery(req);
      release = speechInterruption.track(payload.screenNumber || payload.target, abort);
      const { ttsConfigStore } = await import('../tts/TtsConfigStore.js');
      const state = ttsConfigStore.get();
      const voice = payload.voice || state.voice;
      for await (const chunk of localTts.pcm(payload.text, voice, abort.signal)) {
        abort.signal.throwIfAborted();
        if (!res.headersSent) res.writeHead(200, {
          'Content-Type': 'application/octet-stream', 'Cache-Control': 'no-store',
          'X-Sample-Rate': '24000', 'X-Audio-Channels': '1', 'X-Audio-Format': 'pcm_s16le',
        });
        if (!res.write(chunk)) await once(res, 'drain', { signal: abort.signal });
      }
      res.end();
    } catch (err) {
      if (res.destroyed) return;
      if (res.headersSent) { res.destroy(); return; }
      res.writeHead(err.status === 429 ? 429 : 503, { 'Content-Type': 'application/json', 'Retry-After': '1' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    } finally {
      release();
    }
  }

  async _handleTts(req, res) {
    const abort = new AbortController();
    let release = () => {};
    res.on('close', () => { if (!res.writableEnded) abort.abort(); });
    try {
      const payload = await this._readJsonOrQuery(req);
      if (payload.preview !== true) release = speechInterruption.track(payload.screenNumber || payload.target, abort);
      const text = String(payload.text || '').trim();
      const { ttsConfigStore } = await import('../tts/TtsConfigStore.js');
      const ttsState = ttsConfigStore.get();
      const provider = payload.provider || ttsState.provider;
      const voice = payload.voice || payload.speaker || ttsState.voice;

      if (!text) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: 'text is required' }));
        return;
      }

      const { ttsService, PROVIDER_VOICES } = await import('../tts/TtsService.js');
      if (provider === 'qwen3') {
        const chunks = []; let size = 0;
        for await (const chunk of localTts.pcm(text, voice, abort.signal)) {
          abort.signal.throwIfAborted();
          size += chunk.length; if (size > 32 * 1024 * 1024) throw new Error('音频过长，请缩短文本');
          chunks.push(Buffer.from(chunk));
        }
        abort.signal.throwIfAborted();
        const name = `qwen3-${createHash('sha256').update(voice + '\0' + text).digest('hex')}.wav`;
        const dir = path.resolve(config.TTS.cacheDir);
        await fs.promises.mkdir(dir, { recursive: true });
        await fs.promises.writeFile(path.join(dir, name), Buffer.concat([wavHeader(size), ...chunks]));
        abort.signal.throwIfAborted();
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, avatar: false, audio: { url: `/tts-cache/${name}`, fileName: name, provider, voice } }));
        return;
      }
      if (ttsState.avatarEnabled && payload.preview !== true) {
        const { avatarTargetCoordinator } = await import('../avatar/AvatarTargetCoordinator.js');
        const screenNumber = payload.screenNumber || payload.target;
        const avatarResult = await avatarTargetCoordinator.say(text, screenNumber);

        const audio = await ttsService.createAvatarSilence(text);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
          ok: true,
          avatar: true,
          avatarScreenNumber: avatarResult.screenNumber,
          audio,
          voices: {},
          serverTime: Date.now(),
        }));
        return;
      }

      const audio = await ttsService.synthesize(text, voice, { provider });
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, avatar: false, audio, voices: PROVIDER_VOICES[audio.provider] || {}, serverTime: Date.now() }));
    } catch (err) {
      if (res.destroyed) return;
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    } finally {
      release();
    }
  }

  async _handleTtsSettings(req, res) {
    try {
      const { ttsConfigStore } = await import('../tts/TtsConfigStore.js');
      await localTts.refresh(req.method === 'POST');

      if (req.method === 'GET') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, ...ttsConfigStore.get(), serverTime: Date.now() }));
        return;
      }

      if (req.method === 'POST') {
        const payload = await this._readJsonOrQuery(req);
        const avatarEnabled = Object.prototype.hasOwnProperty.call(payload, 'avatarEnabled')
          ? payload.avatarEnabled === true
          : ttsConfigStore.get().avatarEnabled;
        const state = ttsConfigStore.setVoice(payload.voice || payload.speaker, payload.provider, avatarEnabled);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, ...state, serverTime: Date.now() }));
        return;
      }

      res.writeHead(405, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'method not allowed' }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  _handleTtsCache(req, res) {
    const urlPath = decodeURIComponent(req.url.split('?')[0]);
    const fileName = path.basename(urlPath);
    const cacheDir = path.resolve(config.TTS.cacheDir);
    const filePath = path.resolve(cacheDir, fileName);

    if (!filePath.startsWith(`${cacheDir}${path.sep}`)) {
      res.writeHead(403);
      res.end('Forbidden');
      return;
    }

    this._serveFile(filePath, res);
  }

  async _handleScreenSnapshotFile(req, res) {
    const urlPath = decodeURIComponent(req.url.split('?')[0]);
    const fileName = path.basename(urlPath);
    const { snapshotStore } = await import('../snapshots/SnapshotStore.js');
    const filePath = snapshotStore.getFilePath(fileName);

    if (!filePath) {
      res.writeHead(403);
      res.end('Forbidden');
      return;
    }

    this._serveFile(filePath, res);
  }

  _handleScreenBackgroundFile(req, res) {
    const urlPath = decodeURIComponent(req.url.split('?')[0]);
    const fileName = path.basename(urlPath);
    const root = path.resolve(config.DATA_DIR, 'screen-backgrounds');
    const filePath = path.resolve(root, fileName);
    if (!filePath.startsWith(`${root}${path.sep}`)) {
      res.writeHead(403);
      res.end('Forbidden');
      return;
    }
    this._serveFile(filePath, res);
  }

  async _handleAsrText(req, res) {
    try {
      const payload = await this._readJsonOrQuery(req);
      const text = String(payload.text || '').trim();
      const target = payload.target || '2';
      const clientMode = String(payload.clientMode || '').trim().toUpperCase();
      const robotMode = clientMode === 'ROBOT';

      if (!text) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: 'text is required' }));
        return;
      }

      const { messageRouter } = await import('../router/MessageRouter.js');
      const result = await messageRouter.handleExternalText({
        text,
        target,
        source: payload.source || 'asr-apk',
        requestId: payload.requestId || '',
        conversationId: robotMode ? String(payload.sessionId || '') : '',
        // Robot requests are data-only on 61. V5/RobotOS owns robot TTS and
        // all robot-side presentation, so a missing client flag must never
        // cause host audio, digital-human speech or screen actions.
        suppressActions: robotMode || payload.suppressTts === true,
        clientMode,
      });

      if (robotMode) {
        const outcome = messageRouter.consumeCompletedOutcome(result.seq) || {
          reply: result.reply || '',
          answerSource: result.answerSource || '',
          context: result.context || {},
        };
        const structured = this._robotAnswerEnvelope({ result, outcome, text });
        res.writeHead(structured.answerStatus === 'FAILED' ? 503 : 200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: structured.answerStatus !== 'FAILED', ...result, ...structured }));
        return;
      }

      res.writeHead(result.error ? 502 : 200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: !result.error, ...result }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  _robotAnswerEnvelope({ result, outcome, text }) {
    const context = outcome?.context || {};
    const reply = String(outcome?.reply || result?.reply || '').trim();
    const traceId = String(result?.seq || '');
    const webStatus = String(context.webSearchStatus || '').toUpperCase();
    if (result?.error || context.technicalFallback === true || context.modelError
      || ['UNAVAILABLE', 'FAILED', 'TIMED_OUT'].includes(webStatus)) {
      return { answerStatus: 'FAILED', reason: 'SERVICE_ERROR', teachable: false, answerSource: outcome?.answerSource || '', reply: '', traceId };
    }
    const sensitive = /(?:价格|报价|费用|多少钱|采购|合同|承诺|保密|机密|敏感)/u.test(String(text || ''));
    if (sensitive || outcome?.answerSource === '兜底库') {
      return { answerStatus: 'ABSTAINED', reason: 'STAFF_ONLY', teachable: false, answerSource: outcome?.answerSource || '', reply: '', traceId };
    }
    const noKnowledge = context.robotAnswerStatus === 'NO_KNOWLEDGE'
      || context.refusalFallback === true
      || ['INSUFFICIENT_EVIDENCE', 'CONFLICTING_EVIDENCE', 'ABSTAINED'].includes(webStatus);
    if (noKnowledge || !reply) {
      return { answerStatus: 'ABSTAINED', reason: 'NO_KNOWLEDGE', teachable: true, answerSource: outcome?.answerSource || '', reply: '', traceId };
    }
    return { answerStatus: 'ANSWERED', reason: null, teachable: false, answerSource: outcome?.answerSource || result?.answerSource || 'LLM', reply, traceId };
  }

  async _handleTeachingAnalyze(req, res) {
    try {
      this._assertTeachingAuth(req);
      if (req.method !== 'POST') throw Object.assign(new Error('method not allowed'), { statusCode: 405 });
      const result = await teachingAnalysisService.analyze(await this._readJsonOrQuery(req));
      res.writeHead(result.status === 'FAILED' ? 503 : 200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify(result));
    } catch (err) {
      res.writeHead(err.statusCode || 500, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ status: 'FAILED', error: err.message }));
    }
  }

  async _handleQaDrafts(req, res) {
    try {
      const parsed = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
      const basePath = '/api/knowledge/qa-drafts';
      const segments = parsed.pathname.slice(basePath.length).split('/').filter(Boolean).map(decodeURIComponent);

      // V5 only creates pending drafts. The robot credential cannot read,
      // edit, approve or publish drafts.
      if (req.method === 'POST' && segments.length === 0) {
        this._assertTeachingAuth(req);
        const saved = qaDraftStore.save(await this._readJsonOrQuery(req));
        res.writeHead(saved.replayed ? 200 : 201, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({
          submissionId: saved.item.submissionId,
          draftId: saved.item.draftId,
          status: saved.item.status,
          replayed: saved.replayed,
        }));
        return;
      }

      if (req.method === 'GET' && segments.length === 0) {
        adminAccountStore.require(this._getBearerToken(req), 'knowledge.view');
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({
          ok: true,
          items: qaDraftStore.list().map(item => this._publicQaDraft(item)),
          serverTime: Date.now(),
        }));
        return;
      }

      const draftId = segments[0] || '';
      if (req.method === 'PATCH' && draftId && segments.length === 1) {
        const actor = adminAccountStore.require(this._getBearerToken(req), 'knowledge.manage');
        const item = qaDraftStore.updateReview(draftId, await this._readJsonOrQuery(req), actor);
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, item: this._publicQaDraft(item), serverTime: Date.now() }));
        return;
      }

      if (req.method === 'POST' && draftId && segments[1] === 'publish' && segments.length === 2) {
        const actor = adminAccountStore.require(this._getBearerToken(req), 'publish.manage');
        const result = await this._publishQaDraft(draftId, await this._readJsonOrQuery(req), actor);
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, ...result, serverTime: Date.now() }));
        return;
      }

      if (req.method === 'POST' && draftId && segments[1] === 'reject' && segments.length === 2) {
        const actor = adminAccountStore.require(this._getBearerToken(req), 'publish.manage');
        const current = qaDraftStore.get(draftId);
        if (!current) throw Object.assign(new Error('teaching draft not found'), { statusCode: 404 });
        if (current.status === 'PUBLISHED') {
          throw Object.assign(new Error('published teaching draft cannot be rejected'), { statusCode: 409 });
        }
        const item = qaDraftStore.setStatus(draftId, 'REJECTED', {
          reviewedBy: actor.id,
          reviewedByName: actor.displayName || actor.username,
          rejectedAt: new Date().toISOString(),
        });
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, item: this._publicQaDraft(item), serverTime: Date.now() }));
        return;
      }

      throw Object.assign(new Error('method not allowed'), { statusCode: 405 });
    } catch (err) {
      res.writeHead(err.statusCode || 500, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ error: err.message }));
    }
  }

  _publicQaDraft(item = {}) {
    const { fingerprint, ...visible } = item;
    return visible;
  }

  async _publishQaDraft(draftId, input = {}, actor = {}) {
    let draft = qaDraftStore.get(draftId);
    if (!draft) throw Object.assign(new Error('teaching draft not found'), { statusCode: 404 });
    if (draft.status === 'PUBLISHED') {
      return { item: this._publicQaDraft(draft), qaId: draft.qaId, replayed: true };
    }
    if (['REJECTED', 'INDEXING'].includes(draft.status)) {
      throw Object.assign(new Error(`teaching draft cannot be published from ${draft.status}`), { statusCode: 409 });
    }

    draft = qaDraftStore.updateReview(draftId, input, actor);
    draft = qaDraftStore.setStatus(draftId, 'INDEXING', {
      approvedAt: new Date().toISOString(),
      reviewedBy: actor.id,
      reviewedByName: actor.displayName || actor.username,
      indexError: '',
    });

    const qaItem = {
      id: `robot-draft:${draft.draftId}`,
      question: draft.reviewQuestion,
      answer: draft.reviewAnswer,
      keywords: ['机器人现场补充', '人工审核', draft.robotId].filter(Boolean),
      enabled: true,
      source: 'robot-teaching',
      sourceFile: '机器人现场补充',
      reviewStatus: 'APPROVED',
      createdAt: draft.createdAt,
    };

    const vector = await vectorKnowledgeService.upsertFaq(qaItem);
    if (!vector?.ok) {
      const failed = qaDraftStore.setStatus(draftId, 'INDEX_FAILED', {
        indexError: String(vector?.error || vector?.reason || 'vector indexing failed').slice(0, 500),
      });
      throw Object.assign(new Error(failed.indexError), { statusCode: 503 });
    }

    try {
      const savedQa = qaStore.save(qaItem);
      const { specialConfigStore } = await import('../specials/SpecialConfigStore.js');
      knowledgeDb.syncFromCurrentStores({ mediaLibrary, qaStore, specialConfigStore });
      const published = qaDraftStore.setStatus(draftId, 'PUBLISHED', {
        qaId: savedQa.id,
        publishedAt: new Date().toISOString(),
        indexError: '',
      });
      return { item: this._publicQaDraft(published), qaId: savedQa.id, replayed: false };
    } catch (err) {
      // Do not leave a formal FAQ behind when the local knowledge snapshot
      // cannot be updated. Rebuild removes the already-upserted candidate
      // from the vector index as well.
      try { qaStore.remove(qaItem.id); } catch {}
      try {
        const { specialConfigStore } = await import('../specials/SpecialConfigStore.js');
        knowledgeDb.syncFromCurrentStores({ mediaLibrary, qaStore, specialConfigStore });
      } catch {}
      try { await vectorKnowledgeService.rebuildFromKnowledgeDb(); } catch {}
      qaDraftStore.setStatus(draftId, 'INDEX_FAILED', {
        indexError: String(err.message || err).slice(0, 500),
      });
      throw Object.assign(new Error('teaching draft publication failed'), { statusCode: 503 });
    }
  }

  _assertTeachingAuth(req) {
    const expected = String(process.env.ROBOT_TEACHING_TOKEN || '');
    if (!expected) throw Object.assign(new Error('robot teaching service is not configured'), { statusCode: 503 });
    const authorization = String(req.headers.authorization || '');
    const supplied = authorization.startsWith('Bearer ') ? authorization.slice(7) : '';
    const left = Buffer.from(expected);
    const right = Buffer.from(supplied);
    if (left.length !== right.length || !timingSafeEqual(left, right)) {
      throw Object.assign(new Error('unauthorized'), { statusCode: 401 });
    }
  }

  async _handleRemoteControl(req, res) {
    try {
      const payload = await this._readJsonOrQuery(req);
      const action = String(payload.action || '').trim();
      const target = payload.target || 'all';

      if (!action) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: 'action is required' }));
        return;
      }

      const { messageRouter } = await import('../router/MessageRouter.js');
      const result = await messageRouter.handleRemoteControl({
        action,
        target,
        source: payload.source || 'asr-apk',
      });

      res.writeHead(result.error ? 400 : 200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: !result.error, ...result }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
  }

  _readJsonOrQuery(req) {
    return new Promise((resolve, reject) => {
      const parsed = new URL(req.url, `http://${req.headers.host || 'localhost'}`);

      if (req.method === 'GET') {
        resolve(Object.fromEntries(parsed.searchParams.entries()));
        return;
      }

      let body = '';
      req.on('data', chunk => {
        body += chunk;
        if (body.length > 1024 * 1024) {
          req.destroy();
          reject(new Error('request body too large'));
        }
      });
      req.on('end', () => {
        if (!body.trim()) {
          resolve({});
          return;
        }
        try {
          resolve(JSON.parse(body));
        } catch {
          resolve(Object.fromEntries(new URLSearchParams(body).entries()));
        }
      });
      req.on('error', reject);
    });
  }

  // 提供文件
  _handleFile(req, res) {
    const urlPath = decodeURIComponent(req.url.split('?')[0]);
    const filePath = path.join(this.mediaDir, urlPath);

    // 安全检查：防止目录遍历
    if (!filePath.startsWith(this.mediaDir)) {
      res.writeHead(403);
      res.end('Forbidden');
      return;
    }

    if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: '文件不存在', path: urlPath }));
      return;
    }

    return this._serveFile(filePath, res, req.headers.range);
  }

  _serveFile(filePath, res, range = null) {
    if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'file not found' }));
      return;
    }

    const ext = path.extname(filePath).toLowerCase();
    const mime = MIME_TYPES[ext] || 'application/octet-stream';
    const stat = fs.statSync(filePath);

    // 支持 Range 请求（视频拖拽播放）
    if (range && mime.startsWith('video/')) {
      const parts = range.replace(/bytes=/, '').split('-');
      const start = parseInt(parts[0], 10);
      const end = parts[1] ? parseInt(parts[1], 10) : stat.size - 1;
      const chunkSize = end - start + 1;

      res.writeHead(206, {
        'Content-Range': `bytes ${start}-${end}/${stat.size}`,
        'Accept-Ranges': 'bytes',
        'Content-Length': chunkSize,
        'Content-Type': mime,
      });
      fs.createReadStream(filePath, { start, end }).pipe(res);
    } else {
      res.writeHead(200, {
        'Content-Length': stat.size,
        'Content-Type': mime,
      });
      fs.createReadStream(filePath).pipe(res);
    }
  }

  // 递归扫描文件
  _scanFiles(dir, prefix = '') {
    if (!prefix && mediaLibrary.hasLibrary()) {
      return mediaLibrary.getAll();
    }

    const files = [];
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const relPath = prefix ? `${prefix}/${entry.name}` : entry.name;
      if (entry.isDirectory()) {
        files.push(...this._scanFiles(path.join(dir, entry.name), relPath));
      } else {
        const ext = path.extname(entry.name).toLowerCase();
        const stat = fs.statSync(path.join(dir, entry.name));
        files.push({
          name: entry.name,
          path: relPath,
          ext,
          size: stat.size,
          type: this._fileCategory(ext),
          url: `${config.PUBLIC_FILE_BASE_URL}/${encodeURIComponent(relPath)}`,
          localPath: `${config.MEDIA_WIN_PATH}\\${relPath.replace(/\//g, '\\')}`,
        });
      }
    }
    return files;
  }

  _fileCategory(ext) {
    if (['.mp4', '.webm', '.avi', '.mov', '.mkv'].includes(ext)) return 'video';
    if (['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'].includes(ext)) return 'image';
    if (['.pptx', '.ppt'].includes(ext)) return 'ppt';
    if (['.pdf', '.docx'].includes(ext)) return 'document';
    return 'other';
  }

  _safeUploadName(fileName) {
    const base = path.basename(String(fileName || '').replace(/\\/g, '/')).trim();
    return base.replace(/[<>:"/\\|?*\x00-\x1F]/g, '_');
  }

  _isAllowedUploadExt(ext) {
    return [
      '.ppt', '.pptx',
      '.pdf', '.docx',
      '.mp4', '.webm', '.avi', '.mov', '.mkv',
      '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
    ].includes(ext);
  }

  _uniqueFileName(dir, fileName) {
    const ext = path.extname(fileName);
    const base = path.basename(fileName, ext);
    let candidate = fileName;
    let index = 1;
    while (fs.existsSync(path.join(dir, candidate))) {
      candidate = `${base}_${index}${ext}`;
      index += 1;
    }
    return candidate;
  }

  _logMediaFiles() {
    const files = this._scanFiles(this.mediaDir);
    if (files.length === 0) {
      console.log(`[FileServer] ⚠️ 媒体目录为空，请将视频/图片/PPT放入: ${this.mediaDir}`);
    } else {
      console.log(`[FileServer] 📂 媒体文件 (${files.length}个):`);
      files.forEach(f => console.log(`  - ${f.path} [${f.type}] ${(f.size / 1024 / 1024).toFixed(1)}MB`));
    }
  }

  stop() {
    if (this.server) this.server.close();
  }
}

export const fileServer = new FileServer();
export default fileServer;
