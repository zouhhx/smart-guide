import http from 'http';
import https from 'https';

const FACE_SERVICE_URL = String(process.env.FACE_SERVICE_URL || 'http://127.0.0.1:3030').replace(/\/$/, '');
const FACE_SERVICE_TIMEOUT_MS = Number.parseInt(process.env.FACE_SERVICE_TIMEOUT_MS || '10000', 10);
const FACE_MAX_IMAGE_BYTES = Number.parseInt(process.env.FACE_MAX_IMAGE_BYTES || String(5 * 1024 * 1024), 10);
const FACE_CONFIRM_FRAMES = Number.parseInt(process.env.FACE_CONFIRM_FRAMES || '3', 10);
const FACE_CONFIRM_WINDOW_MS = Number.parseInt(process.env.FACE_CONFIRM_WINDOW_MS || '2500', 10);
const FACE_COOLDOWN_MS = Number.parseInt(process.env.FACE_COOLDOWN_MS || '90000', 10);

class FaceIntegrationService {
  constructor() {
    this.confirmations = new Map();
    this.cooldowns = new Map();
  }

  async handle(req, res) {
    try {
      const incoming = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
      const route = this._targetRoute(incoming.pathname);
      if (!route) {
        this._json(res, 404, { ok: false, error: 'face endpoint not found' });
        return;
      }

      const body = ['POST', 'PUT', 'PATCH'].includes(req.method)
        ? await this._readBody(req)
        : Buffer.alloc(0);
      const response = await this._requestFaceService({
        method: req.method,
        path: `${route}${incoming.search}`,
        body,
        headers: req.headers,
      });

      if (response.raw) {
        res.writeHead(response.statusCode, {
          'Content-Type': response.contentType || 'application/octet-stream',
          'Content-Length': String(response.raw.length),
          'Cache-Control': 'no-store',
        });
        res.end(response.raw);
        return;
      }

      if (incoming.pathname !== '/api/face/recognize' || response.statusCode >= 400) {
        this._json(res, response.statusCode, response.data);
        return;
      }

      const integration = await this._processRecognition(response.data, req.headers);
      this._json(res, response.statusCode, { ...response.data, integration });
    } catch (err) {
      const statusCode = err.statusCode || 502;
      this._json(res, statusCode, { ok: false, error: err.message, serverTime: Date.now() });
    }
  }

  _targetRoute(pathname) {
    if (pathname === '/api/face/health') return '/health';
    if (pathname === '/api/face/recognize') return '/api/recognize';
    if (pathname === '/api/face/persons') return '/api/persons';
    if (pathname === '/api/face/captures') return '/api/captures';
    const captureMatch = pathname.match(/^\/api\/face\/captures\/([^/]+)(\/image)?$/);
    if (captureMatch) {
      return `/api/captures/${encodeURIComponent(decodeURIComponent(captureMatch[1]))}${captureMatch[2] || ''}`;
    }
    const personMatch = pathname.match(/^\/api\/face\/persons\/([^/]+)(\/faces)?$/);
    if (personMatch) {
      return `/api/persons/${encodeURIComponent(decodeURIComponent(personMatch[1]))}${personMatch[2] || ''}`;
    }
    return '';
  }

  async _processRecognition(data, headers) {
    const deviceId = String(headers['x-device-id'] || data.deviceId || 'unknown-device');
    const cameraId = String(headers['x-camera-id'] || data.cameraId || 'default-camera');
    const key = `${deviceId}:${cameraId}`;
    const matchedFaces = (data.faces || [])
      .filter(face => face?.matched && face.personId)
      .sort((a, b) => this._faceArea(b) - this._faceArea(a));
    const candidate = matchedFaces[0] || null;
    const now = Date.now();

    if (!candidate) {
      this.confirmations.delete(key);
      return { candidate: null, confirmationCount: 0, confirmed: false, triggered: false };
    }

    const previous = this.confirmations.get(key);
    const sameSequence = previous
      && previous.personId === candidate.personId
      && now - previous.lastAt <= FACE_CONFIRM_WINDOW_MS;
    const confirmationCount = sameSequence ? previous.count + 1 : 1;
    this.confirmations.set(key, { personId: candidate.personId, count: confirmationCount, lastAt: now });

    const confirmed = confirmationCount >= FACE_CONFIRM_FRAMES;
    const cooldownKey = `${key}:${candidate.personId}`;
    const cooldownUntil = this.cooldowns.get(cooldownKey) || 0;
    let triggered = false;
    let triggerResult = null;

    if (confirmed && now >= cooldownUntil) {
      this.cooldowns.set(cooldownKey, now + FACE_COOLDOWN_MS);
      const { messageRouter } = await import('../router/MessageRouter.js');
      triggerResult = messageRouter.handleFaceWelcome({
        person: candidate,
        source: deviceId,
        cameraId,
        target: headers['x-target-screen'] || candidate.targetScreen || 'all',
      });
      triggered = true;
    }

    this._cleanup(now);
    return {
      candidate,
      confirmationCount,
      requiredFrames: FACE_CONFIRM_FRAMES,
      confirmed,
      triggered,
      cooldownRemainingMs: triggered ? FACE_COOLDOWN_MS : Math.max(0, cooldownUntil - now),
      triggerResult,
    };
  }

  _cleanup(now) {
    for (const [key, value] of this.confirmations) {
      if (now - value.lastAt > FACE_CONFIRM_WINDOW_MS * 2) this.confirmations.delete(key);
    }
    for (const [key, until] of this.cooldowns) {
      if (now > until) this.cooldowns.delete(key);
    }
  }

  _faceArea(face) {
    const box = face?.box || [];
    return Number(box[2] || 0) * Number(box[3] || 0);
  }

  _readBody(req) {
    return new Promise((resolve, reject) => {
      const chunks = [];
      let total = 0;
      req.on('data', chunk => {
        total += chunk.length;
        if (total > FACE_MAX_IMAGE_BYTES) {
          const error = new Error(`image exceeds ${FACE_MAX_IMAGE_BYTES} bytes`);
          error.statusCode = 413;
          reject(error);
          req.destroy();
          return;
        }
        chunks.push(chunk);
      });
      req.on('end', () => resolve(Buffer.concat(chunks)));
      req.on('error', reject);
    });
  }

  _requestFaceService({ method, path, body, headers }) {
    return new Promise((resolve, reject) => {
      const target = new URL(path, FACE_SERVICE_URL);
      const client = target.protocol === 'https:' ? https : http;
      const outgoingHeaders = {
        accept: 'application/json',
        'content-type': headers['content-type'] || 'application/octet-stream',
        'x-device-id': headers['x-device-id'] || '',
        'x-camera-id': headers['x-camera-id'] || '',
        'x-file-name': headers['x-file-name'] || '',
      };
      if (body.length) outgoingHeaders['content-length'] = String(body.length);

      const request = client.request({
        protocol: target.protocol,
        hostname: target.hostname,
        port: target.port,
        method,
        path: `${target.pathname}${target.search}`,
        headers: outgoingHeaders,
        timeout: FACE_SERVICE_TIMEOUT_MS,
      }, response => {
        const chunks = [];
        response.on('data', chunk => chunks.push(chunk));
        response.on('end', () => {
          const raw = Buffer.concat(chunks);
          const contentType = String(response.headers['content-type'] || '');
          if (!contentType.includes('json')) {
            resolve({ statusCode: response.statusCode || 502, raw, contentType });
            return;
          }
          const text = raw.toString('utf8');
          let data;
          try {
            data = text ? JSON.parse(text) : {};
          } catch {
            data = { ok: false, error: text || `face service returned ${response.statusCode}` };
          }
          resolve({ statusCode: response.statusCode || 502, data });
        });
      });
      request.on('timeout', () => request.destroy(new Error('face service timeout')));
      request.on('error', reject);
      if (body.length) request.write(body);
      request.end();
    });
  }

  _json(res, statusCode, payload) {
    res.writeHead(statusCode, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify(payload));
  }
}

export const faceIntegrationService = new FaceIntegrationService();
export default faceIntegrationService;
