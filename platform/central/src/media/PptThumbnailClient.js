import fs from 'fs';
import path from 'path';
import { config } from '../config/index.js';

const PPT_EXTENSIONS = new Set(['.ppt', '.pptx']);

export function rewriteLocalThumbnailUrls(body, requestHost) {
  let payload;
  try {
    payload = JSON.parse(Buffer.isBuffer(body) ? body.toString('utf8') : String(body));
  } catch {
    return body;
  }
  const host = String(requestHost || config.PUBLIC_HOST || '127.0.0.1').replace(/^\[|\]$/g, '').split(':')[0];
  let servicePort = '3025';
  try {
    servicePort = new URL(config.PPT_THUMBNAIL_SERVICE_URL).port || servicePort;
  } catch {}
  const rewrite = item => {
    if (!item || typeof item.url !== 'string') return;
    try {
      const url = new URL(item.url);
      if (url.hostname === '127.0.0.1' || url.hostname === 'localhost') {
        url.hostname = host;
        url.port = servicePort;
        item.url = url.toString();
      }
    } catch {}
  };
  if (Array.isArray(payload?.pages)) payload.pages.forEach(rewrite);
  rewrite(payload?.page);
  return Buffer.from(JSON.stringify(payload));
}

export class PptThumbnailClient {
  constructor(options = {}) {
    this.baseUrl = String(options.baseUrl || config.PPT_THUMBNAIL_SERVICE_URL).replace(/\/+$/, '');
    this.mediaDir = path.resolve(options.mediaDir || config.MEDIA_DIR);
    this.timeoutMs = Math.max(1000, Number(options.timeoutMs || config.PPT_THUMBNAIL_SERVICE_TIMEOUT_MS || 15000));
  }

  resolveMediaName(value, options = {}) {
    const name = String(value || '').trim().replace(/\\/g, '/');
    if (!name || name.includes('\0') || path.isAbsolute(name) || /^[a-zA-Z]:/.test(name) || name.startsWith('/')) {
      throw Object.assign(new Error('name must be a media-relative PPT path'), { statusCode: 400 });
    }
    const segments = name.split('/');
    if (segments.some(segment => !segment || segment === '.' || segment === '..')) {
      throw Object.assign(new Error('name must not contain empty or parent path segments'), { statusCode: 400 });
    }
    if (!PPT_EXTENSIONS.has(path.extname(name).toLowerCase())) {
      throw Object.assign(new Error('only PPT and PPTX thumbnails are supported'), { statusCode: 400 });
    }
    const fullPath = path.resolve(this.mediaDir, ...segments);
    if (fullPath === this.mediaDir || !fullPath.startsWith(`${this.mediaDir}${path.sep}`)) {
      throw Object.assign(new Error('name resolves outside the media directory'), { statusCode: 400 });
    }
    if (options.requireExisting !== false && (!fs.existsSync(fullPath) || !fs.statSync(fullPath).isFile())) {
      throw Object.assign(new Error('PPT file not found'), { statusCode: 404 });
    }
    return { name: segments.join('/'), fullPath };
  }

  async request(value, options = {}) {
    const method = String(options.method || 'GET').toUpperCase();
    if (!['GET', 'POST'].includes(method)) {
      throw Object.assign(new Error('method not allowed'), { statusCode: 405 });
    }
    const { name } = this.resolveMediaName(value);
    const page = Number(options.page);
    const pageQuery = Number.isInteger(page) && page > 0 ? `&page=${page}` : '';
    const quality = options.quality === 'full' ? '&quality=full' : '';
    const url = `${this.baseUrl}/api/ppt-thumbnails?name=${encodeURIComponent(name)}${pageQuery}${quality}`;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    const forwardAbort = () => controller.abort();
    options.signal?.addEventListener('abort', forwardAbort, { once: true });
    if (options.signal?.aborted) controller.abort();
    try {
      const response = await fetch(url, { method, cache: 'no-store', signal: controller.signal });
      const body = Buffer.from(await response.arrayBuffer());
      return {
        statusCode: response.status,
        contentType: response.headers.get('content-type') || 'application/json; charset=utf-8',
        body,
      };
    } catch (error) {
      if (error?.name === 'AbortError') {
        throw Object.assign(new Error('PPT thumbnail service timeout'), { statusCode: 504 });
      }
      throw Object.assign(new Error(`PPT thumbnail service unavailable: ${error.message}`), { statusCode: 502 });
    } finally {
      clearTimeout(timeout);
      options.signal?.removeEventListener('abort', forwardAbort);
    }
  }

  async warm(value) {
    return this.request(value, { method: 'GET' });
  }
}

export const pptThumbnailClient = new PptThumbnailClient();
