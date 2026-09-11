import crypto from 'node:crypto';
import { config } from '../config/index.js';
import { operationsService } from './OperationsService.js';

function secureEqual(left, right) {
  const a = Buffer.from(String(left || ''), 'utf8');
  const b = Buffer.from(String(right || ''), 'utf8');
  return a.length === b.length && a.length > 0 && crypto.timingSafeEqual(a, b);
}

export class OperationsHttpApi {
  constructor(options = {}) {
    this.settings = options.settings || config.OPERATIONS_GATEWAY;
    this.service = options.service || operationsService;
  }

  async handle(req, res) {
    try {
      if (!this.settings.enabled || !this.settings.token) {
        return this._json(res, 503, { ok: false, code: 'OPERATIONS_GATEWAY_DISABLED' });
      }
      const auth = this._authorize(req);
      if (!auth.ok) return this._json(res, auth.status, { ok: false, code: auth.code });

      const parsed = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
      if (req.method === 'GET' && ['/api/operations/v1/health', '/api/operations/v1/capabilities'].includes(parsed.pathname)) {
        return this._json(res, 200, this.service.describe());
      }
      if (req.method !== 'POST' || parsed.pathname !== '/api/operations/v1/execute') {
        return this._json(res, 405, { ok: false, code: 'METHOD_NOT_ALLOWED' });
      }

      const payload = await this._readJson(req);
      const result = await this.service.execute(payload, auth.callerId);
      const statusCode = result.status === 'SUCCEEDED' ? 200
        : result.status === 'TIMED_OUT' ? 504
          : result.status === 'FAILED' ? 502
            : 400;
      return this._json(res, statusCode, { ok: result.status === 'SUCCEEDED', result });
    } catch (error) {
      return this._json(res, 400, { ok: false, code: 'INVALID_REQUEST', message: String(error?.message || error) });
    }
  }

  _authorize(req) {
    const header = String(req.headers.authorization || '');
    const token = header.startsWith('Bearer ') ? header.slice(7).trim() : '';
    if (!secureEqual(token, this.settings.token)) {
      return { ok: false, status: 401, code: 'UNAUTHORIZED' };
    }
    const callerId = String(req.headers['x-operations-caller'] || '').trim();
    if (!callerId || !this.settings.allowedCallers.includes(callerId)) {
      return { ok: false, status: 403, code: 'CALLER_NOT_ALLOWED' };
    }
    return { ok: true, callerId };
  }

  _readJson(req) {
    return new Promise((resolve, reject) => {
      let body = '';
      req.on('data', chunk => {
        body += chunk;
        if (Buffer.byteLength(body, 'utf8') > 64 * 1024) {
          reject(new Error('request body too large'));
          req.destroy();
        }
      });
      req.on('end', () => {
        try {
          resolve(body.trim() ? JSON.parse(body) : {});
        } catch {
          reject(new Error('request body must be JSON'));
        }
      });
      req.on('error', reject);
    });
  }

  _json(res, statusCode, payload) {
    res.writeHead(statusCode, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify(payload));
  }
}

export const operationsHttpApi = new OperationsHttpApi();
export default operationsHttpApi;
