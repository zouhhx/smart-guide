import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const adminRoot = path.resolve(process.env.ADMIN_DIST || path.join(__dirname, 'admin-ui', 'dist'));
const developerRoot = path.resolve(process.env.DEVELOPER_DIST || path.join(__dirname, 'admin', 'dist'));
const port = Number(process.env.ADMIN_PORT || 5180);
const host = process.env.ADMIN_HOST || '0.0.0.0';

const types = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.svg': 'image/svg+xml; charset=utf-8',
  '.ico': 'image/x-icon',
  '.webp': 'image/webp',
  '.woff2': 'font/woff2',
  '.map': 'application/json; charset=utf-8',
};

for (const [label, root] of [['admin', adminRoot], ['developer', developerRoot]]) {
  const indexPath = path.join(root, 'index.html');
  if (!fs.existsSync(indexPath)) {
    throw new Error(`[admin-static] missing ${label} UI entry: ${indexPath}`);
  }
}

process.on('uncaughtException', err => {
  console.error('[admin-static] uncaughtException', err?.stack || err);
});

process.on('unhandledRejection', err => {
  console.error('[admin-static] unhandledRejection', err?.stack || err);
});

function safePath(root, clean) {
  const resolved = path.resolve(root, clean || 'index.html');
  const relative = path.relative(root, resolved);
  if (relative.startsWith('..') || path.isAbsolute(relative)) return null;
  return resolved;
}

function writeRedirect(res, location) {
  res.writeHead(308, {
    Location: location,
    'Cache-Control': 'no-store',
    'Content-Type': 'text/plain; charset=utf-8',
  });
  res.end(`Redirecting to ${location}`);
}

const server = http.createServer((req, res) => {
  try {
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.writeHead(405, { Allow: 'GET, HEAD' });
      res.end('Method not allowed');
      return;
    }

    const pathname = decodeURIComponent((req.url || '/').split('?')[0]);
    if (pathname === '/developer') {
      writeRedirect(res, '/developer/');
      return;
    }
    if (pathname === '/admin' || pathname === '/admin/') {
      writeRedirect(res, '/');
      return;
    }
    if (pathname === '/healthz') {
      const body = JSON.stringify({ ok: true, service: 'dual-admin-ui', admin: '/', developer: '/developer/' });
      res.writeHead(200, {
        'Content-Type': 'application/json; charset=utf-8',
        'Cache-Control': 'no-store',
      });
      res.end(req.method === 'HEAD' ? undefined : body);
      return;
    }
    if (pathname.startsWith('/api/')) {
      res.writeHead(404, {
        'Content-Type': 'application/json; charset=utf-8',
        'Cache-Control': 'no-store',
      });
      res.end(JSON.stringify({ ok: false, error: 'API endpoint not found' }));
      return;
    }

    const isDeveloper = pathname.startsWith('/developer/');
    const root = isDeveloper ? developerRoot : adminRoot;
    const clean = (isDeveloper ? pathname.slice('/developer/'.length) : pathname).replace(/^\/+/, '');
    let target = safePath(root, clean);
    console.log(`[admin-static] ${req.method} ${req.url} -> ${isDeveloper ? 'developer' : 'admin'}:${target}`);
    if (!target) {
      res.writeHead(403);
      res.end('Forbidden');
      return;
    }
    if (!fs.existsSync(target) || fs.statSync(target).isDirectory()) {
      if (path.extname(clean) || clean.startsWith('assets/') || clean.startsWith('prototype/')) {
        res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
        res.end('Not found');
        return;
      }
      target = path.join(root, 'index.html');
    }
    fs.readFile(target, (err, data) => {
      if (err) {
        console.error(`[admin-static] read failed ${target}: ${err.message}`);
        res.writeHead(404);
        res.end('Not found');
        return;
      }
      res.writeHead(200, {
        'Content-Type': types[path.extname(target).toLowerCase()] || 'application/octet-stream',
        'Cache-Control': target.endsWith('index.html') ? 'no-store' : 'public, max-age=31536000, immutable',
      });
      res.end(req.method === 'HEAD' ? undefined : data);
    });
  } catch (err) {
    console.error('[admin-static] request failed', err?.stack || err);
    if (!res.headersSent) {
      res.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('Internal server error');
    } else {
      res.end();
    }
  }
});

server.on('clientError', (err, socket) => {
  console.error('[admin-static] clientError', err?.message || err);
  if (socket.writable) {
    socket.end('HTTP/1.1 400 Bad Request\r\n\r\n');
  }
});

server.listen(port, host, () => {
  console.log(`dual admin static server listening on http://${host}:${port}`);
  console.log(`admin UI root=${adminRoot}`);
  console.log(`developer UI root=${developerRoot}`);
});
