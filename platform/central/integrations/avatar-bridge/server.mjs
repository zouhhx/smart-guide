import http from 'node:http';
import fs from 'node:fs';
import { randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';

const platformPackage = process.env.EXHIBITION_PLATFORM_PACKAGE
  || 'D:/exhibition-hall-new/runtime/exhibition-hall-platform/package.json';
const require = createRequire(platformPackage);
const { WebSocket, WebSocketServer } = require('ws');

const host = process.env.AVATAR_SERVICE_HOST || '0.0.0.0';
const port = Number(process.env.AVATAR_SERVICE_PORT || 19200);
const maxTextLength = Number(process.env.MOFA_MAX_TEXT_LENGTH || 4000);
const configPath = process.env.MOFA_CONFIG_PATH
  || 'D:/exhibition-hall-new/runtime/mofa-avatar-service/mofa-config.json';
const adminPagePath = process.env.MOFA_ADMIN_PAGE_PATH
  || 'D:/exhibition-hall-new/runtime/mofa-avatar-service/index.html';

function loadMofaConfig() {
  try {
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    return {
      appId: String(config.appId || ''),
      appSecret: String(config.appSecret || ''),
      width: Number(config.width || 3840),
      height: Number(config.height || 2160),
      x: Number(config.x || 0),
      y: Number(config.y || 0),
    };
  } catch {
    return { appId: '', appSecret: '', width: 3840, height: 2160, x: 0, y: 0 };
  }
}

function saveMofaBounds(bounds) {
  const existing = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  const next = { ...existing, ...bounds };
  const tempPath = `${configPath}.tmp`;
  fs.writeFileSync(tempPath, `${JSON.stringify(next, null, 2)}\n`, 'utf8');
  fs.renameSync(tempPath, configPath);
  return next;
}

let current = null;
let lastConnectedAt = null;
let lastDisconnectedAt = null;
let lastError = null;
let lastEvent = null;
let lastCommand = null;
let speaking = false;
let desiredOpen = true;
let appStatus = 'unknown';
let appStatusChangedAt = null;
let operation = null;

const now = () => new Date().toISOString();
const log = (message, extra = '') => console.log(`[${now()}] ${message}${extra ? ` ${extra}` : ''}`);

function responsePacket() {
  return {
    message_type: 'event',
    event_type: 'response',
    error_code: 0,
    error_reason: '',
  };
}

function sendRaw(ws, message) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  ws.send(JSON.stringify(message));
  return true;
}

function sendCommand(channel, payload = {}) {
  if (!current || current.ws.readyState !== WebSocket.OPEN) return false;
  const message = {
    message_type: 'event',
    event_type: 'forward_to_device',
    data: {
      device_id: current.deviceId,
      channel,
      payload,
    },
  };
  if (!sendRaw(current.ws, message)) return false;
  const publicPayload = channel === 'switch-app'
    ? { app_id: payload.app_id, app_secret: payload.app_secret ? '[configured]' : '', type: payload.type }
    : payload;
  lastCommand = { at: now(), channel, payload: publicPayload };
  log('command sent', `${channel} ${JSON.stringify(publicPayload).slice(0, 300)}`);
  return true;
}

function bootstrapDigitalHuman(ws) {
  if (!desiredOpen) return;
  const config = loadMofaConfig();
  if (!config.appId || !config.appSecret) {
    lastError = `${now()} server-side MoFa credentials are missing`;
    log('bootstrap skipped', 'server-side MoFa credentials are missing');
    return;
  }
  const steps = [
    [2000, 'switch-app', { app_id: config.appId, app_secret: config.appSecret, type: 'connect' }],
    [3500, 'switch-app', { app_id: config.appId, app_secret: config.appSecret, type: 'enable' }],
    [5500, 'set-bounds', { width: config.width, height: config.height, x: config.x, y: config.y }],
    [6000, 'show', { duration: 300 }],
  ];
  for (const [delay, channel, payload] of steps) {
    setTimeout(() => {
      if (current?.ws === ws) sendCommand(channel, payload);
    }, delay).unref();
  }
}

function publicSession() {
  if (!current) return null;
  return {
    controlCode: current.controlCode,
    deviceId: current.deviceId,
    username: current.username,
    remoteAddress: current.remoteAddress,
    connectedAt: current.connectedAt,
  };
}

function status() {
  const config = loadMofaConfig();
  const transportOnline = Boolean(current && current.ws.readyState === WebSocket.OPEN);
  return {
    ok: true,
    online: transportOnline,
    transportOnline,
    speaking,
    desiredOpen,
    appStatus,
    appStatusChangedAt,
    ready: transportOnline && desiredOpen && appStatus === 'connected_normal',
    operation,
    session: publicSession(),
    lastConnectedAt,
    lastDisconnectedAt,
    lastError,
    lastEvent,
    lastCommand,
    bounds: { width: config.width, height: config.height, x: config.x, y: config.y },
    serverTime: now(),
  };
}

function json(res, statusCode, payload) {
  const data = Buffer.from(JSON.stringify(payload));
  res.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': data.length,
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
  });
  res.end(data);
}

function html(res, statusCode, content) {
  const data = Buffer.from(content);
  res.writeHead(statusCode, {
    'Content-Type': 'text/html; charset=utf-8',
    'Content-Length': data.length,
    'Cache-Control': 'no-store',
    'Content-Security-Policy': "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'",
  });
  res.end(data);
}

async function readJson(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 1024 * 1024) throw new Error('request body too large');
    chunks.push(chunk);
  }
  if (!chunks.length) return {};
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

const server = http.createServer(async (req, res) => {
  if (req.method === 'OPTIONS') return json(res, 204, {});
  try {
    const url = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);
    if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/admin')) {
      try {
        return html(res, 200, fs.readFileSync(adminPagePath, 'utf8'));
      } catch {
        return html(res, 500, '<h1>Digital human admin page is unavailable</h1>');
      }
    }
    if (req.method === 'GET' && (url.pathname === '/health' || url.pathname === '/api/avatar/online')) {
      return json(res, 200, status());
    }
    if (req.method !== 'POST') return json(res, 404, { ok: false, error: 'not found' });

    const body = await readJson(req);
    if (url.pathname === '/api/avatar/say') {
      const text = String(body.text || '').trim();
      if (!text) return json(res, 400, { ok: false, error: 'text is required' });
      if (text.length > maxTextLength) return json(res, 400, { ok: false, error: `text too long, max ${maxTextLength}` });
      if (!sendCommand('send-text', { text })) return json(res, 503, { ok: false, error: 'digital human is offline' });
      return json(res, 200, { ok: true, textLength: text.length });
    }
    if (url.pathname === '/api/avatar/say-stream') {
      const text = String(body.text || '');
      if (!sendCommand('send-stream-text', {
        text,
        is_start: body.is_start !== false,
        is_end: body.is_end !== false,
      })) return json(res, 503, { ok: false, error: 'digital human is offline' });
      return json(res, 200, { ok: true, textLength: text.length });
    }
    if (url.pathname === '/api/avatar/interrupt') {
      if (!sendCommand('interrupt', {})) return json(res, 503, { ok: false, error: 'digital human is offline' });
      speaking = false;
      return json(res, 200, { ok: true });
    }
    if (url.pathname === '/api/avatar/set-bounds' || url.pathname === '/api/avatar/reset') {
      const bounds = {
        width: Number(body.width ?? 3840),
        height: Number(body.height ?? 2160),
        x: Number(body.x ?? 0),
        y: Number(body.y ?? 0),
      };
      if (!Number.isFinite(bounds.width) || bounds.width <= 0
        || !Number.isFinite(bounds.height) || bounds.height <= 0
        || !Number.isFinite(bounds.x) || !Number.isFinite(bounds.y)) {
        return json(res, 400, { ok: false, error: 'invalid digital human bounds' });
      }
      try {
        saveMofaBounds(bounds);
      } catch (error) {
        return json(res, 500, { ok: false, error: `failed to save digital human bounds: ${error.message}` });
      }
      if (!sendCommand('set-bounds', bounds)) {
        return json(res, 200, { ok: true, saved: true, applied: false, bounds });
      }
      const ws = current.ws;
      for (const delay of [800, 2000]) {
        setTimeout(() => {
          if (current?.ws === ws) sendCommand('set-bounds', bounds);
        }, delay).unref();
      }
      return json(res, 200, { ok: true, saved: true, applied: true, bounds });
    }
    if (url.pathname === '/api/avatar/switch-app') {
      const config = loadMofaConfig();
      const type = String(body.type || 'enable');
      if (!config.appId || !config.appSecret) {
        return json(res, 500, { ok: false, error: 'server-side MoFa credentials are missing' });
      }
      if (!sendCommand('switch-app', { app_id: config.appId, app_secret: config.appSecret, type })) {
        return json(res, 503, { ok: false, error: 'digital human is offline' });
      }
      return json(res, 200, { ok: true, type });
    }
    if (url.pathname === '/api/avatar/open') {
      const config = loadMofaConfig();
      if (!config.appId || !config.appSecret) {
        return json(res, 500, { ok: false, error: 'server-side MoFa credentials are missing' });
      }
      if (!sendCommand('switch-app', { app_id: config.appId, app_secret: config.appSecret, type: 'connect' })) {
        return json(res, 503, { ok: false, error: 'digital human is offline' });
      }
      desiredOpen = true;
      operation = { id: randomUUID(), type: 'open', status: appStatus === 'connected_normal' ? 'completed' : 'accepted', startedAt: now() };
      if (operation.status === 'completed') operation.completedAt = operation.startedAt;
      const ws = current.ws;
      const steps = [
        [1500, 'switch-app', { app_id: config.appId, app_secret: config.appSecret, type: 'enable' }],
        [3500, 'set-bounds', { width: config.width, height: config.height, x: config.x, y: config.y }],
        [4000, 'show', { duration: 300 }],
      ];
      for (const [delay, channel, payload] of steps) {
        setTimeout(() => { if (current?.ws === ws) sendCommand(channel, payload); }, delay).unref();
      }
      return json(res, 202, { ok: true, accepted: true, action: 'open', operation });
    }
    if (url.pathname === '/api/avatar/exit') {
      const config = loadMofaConfig();
      if (!config.appId || !config.appSecret) {
        return json(res, 500, { ok: false, error: 'server-side MoFa credentials are missing' });
      }
      if (!sendCommand('hide', { duration: 300 })) {
        return json(res, 503, { ok: false, error: 'digital human is offline' });
      }
      desiredOpen = false;
      operation = { id: randomUUID(), type: 'exit', status: appStatus === 'disconnected' ? 'completed' : 'accepted', startedAt: now() };
      if (operation.status === 'completed') operation.completedAt = operation.startedAt;
      const ws = current.ws;
      const steps = [
        [400, 'switch-app', { app_id: config.appId, app_secret: config.appSecret, type: 'stop' }],
        [1000, 'switch-app', { app_id: config.appId, app_secret: config.appSecret, type: 'disconnect' }],
      ];
      for (const [delay, channel, payload] of steps) {
        setTimeout(() => { if (current?.ws === ws) sendCommand(channel, payload); }, delay).unref();
      }
      return json(res, 202, { ok: true, accepted: true, action: 'exit', operation });
    }
    if (url.pathname === '/api/avatar/show' || url.pathname === '/api/avatar/hide') {
      const channel = url.pathname.endsWith('/show') ? 'show' : 'hide';
      if (!sendCommand(channel, { duration: Number(body.duration || 500) })) {
        return json(res, 503, { ok: false, error: 'digital human is offline' });
      }
      return json(res, 200, { ok: true });
    }
    return json(res, 404, { ok: false, error: 'not found' });
  } catch (error) {
    lastError = `${now()} ${error.message}`;
    return json(res, 400, { ok: false, error: error.message });
  }
});

const wss = new WebSocketServer({ noServer: true });

server.on('upgrade', (req, socket, head) => {
  try {
    const url = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);
    const match = url.pathname.match(/^\/ws\/device\/([^/]+)\/([^/]+)\/([^/]+)$/);
    if (!match) {
      socket.write('HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n');
      socket.destroy();
      return;
    }
    const identity = {
      controlCode: decodeURIComponent(match[1]),
      deviceId: decodeURIComponent(match[2]),
      username: decodeURIComponent(match[3]),
      remoteAddress: socket.remoteAddress || '',
    };
    wss.handleUpgrade(req, socket, head, (ws) => wss.emit('connection', ws, identity));
  } catch (error) {
    lastError = `${now()} ${error.message}`;
    socket.destroy();
  }
});

wss.on('connection', (ws, identity) => {
  if (current?.ws && current.ws.readyState === WebSocket.OPEN) current.ws.close(4000, 'replaced');
  current = { ws, ...identity, connectedAt: now() };
  lastConnectedAt = current.connectedAt;
  lastError = null;
  speaking = false;
  log('digital human connected', JSON.stringify(identity));

  for (const delay of [0, 500, 1000]) {
    setTimeout(() => sendRaw(ws, responsePacket()), delay).unref();
  }
  bootstrapDigitalHuman(ws);

  ws.on('message', (raw) => {
    const text = raw.toString();
    try {
      const message = JSON.parse(text);
      lastEvent = { at: now(), message };
      const body = message?.body || {};
      const payload = body?.payload || {};
      if (message?.message_type === 'request' && message?.path === 'forward_to_controller') {
        if (body.channel === 'sdk-event' && payload?.type === 'voice_start') speaking = true;
        if (body.channel === 'sdk-event' && payload?.type === 'voice_end') speaking = false;
        if (body.channel === 'app-status-change') {
          const config = loadMofaConfig();
          const reportedStatus = payload?.[config.appId] || Object.values(payload || {})[0];
          if (reportedStatus) {
            appStatus = String(reportedStatus);
            appStatusChangedAt = now();
            if (operation?.status === 'accepted'
              && ((operation.type === 'open' && appStatus === 'connected_normal')
                || (operation.type === 'exit' && appStatus === 'disconnected'))) {
              operation = { ...operation, status: 'completed', completedAt: appStatusChangedAt };
            }
          }
          if (!desiredOpen) {
            log('app status while closed', String(reportedStatus || 'unknown'));
          } else if (reportedStatus === 'disconnected') {
            sendCommand('switch-app', { app_id: config.appId, app_secret: config.appSecret, type: 'connect' });
          } else if (reportedStatus === 'connected_invisible') {
            sendCommand('switch-app', { app_id: config.appId, app_secret: config.appSecret, type: 'enable' });
          } else if (reportedStatus === 'connected_normal') {
            sendCommand('set-bounds', { width: config.width, height: config.height, x: config.x, y: config.y });
            sendCommand('show', { duration: 300 });
          }
        }
        sendRaw(ws, responsePacket());
      }
      log('digital human event', JSON.stringify(message).slice(0, 500));
    } catch {
      lastEvent = { at: now(), raw: text.slice(0, 1000) };
      log('digital human text event', text.slice(0, 500));
    }
  });

  ws.on('close', (code, reason) => {
    if (current?.ws === ws) current = null;
    speaking = false;
    lastDisconnectedAt = now();
    log('digital human disconnected', `code=${code} reason=${reason.toString()}`);
  });
  ws.on('error', (error) => {
    lastError = `${now()} ${error.message}`;
    log('digital human socket error', error.message);
  });
});

server.listen(port, host, () => log('avatar HTTP/WebSocket service listening', `http://${host}:${port}`));

function shutdown(signal) {
  log('shutting down', signal);
  current?.ws?.close();
  wss.close();
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 3000).unref();
}

process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));
