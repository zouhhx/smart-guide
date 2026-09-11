import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { PptThumbnailClient, rewriteLocalThumbnailUrls } from '../src/media/PptThumbnailClient.js';

function startServer(handler) {
  const server = http.createServer(handler);
  return new Promise(resolve => server.listen(0, '127.0.0.1', () => {
    const address = server.address();
    resolve({ server, baseUrl: `http://127.0.0.1:${address.port}` });
  }));
}

function makeMediaDir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ppt-thumbnails-'));
  fs.mkdirSync(path.join(dir, '中文目录'));
  fs.writeFileSync(path.join(dir, '中文目录', '智慧 展厅.pptx'), 'fixture');
  return dir;
}

test('client preserves 202 to READY and percent-encodes Chinese media-relative names', async t => {
  const requests = [];
  const { server, baseUrl } = await startServer((req, res) => {
    requests.push(req.url);
    const ready = requests.length > 1;
    res.writeHead(ready ? 200 : 202, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(ready
      ? { status: 'READY', slideCount: 1, pages: [{ page: 1, url: `${baseUrl}/page-001.png` }] }
      : { status: 'GENERATING', slideCount: 0, pages: [] }));
  });
  t.after(() => server.close());
  const mediaDir = makeMediaDir();
  t.after(() => fs.rmSync(mediaDir, { recursive: true, force: true }));
  const client = new PptThumbnailClient({ baseUrl, mediaDir, timeoutMs: 2000 });

  assert.equal((await client.request('中文目录/智慧 展厅.pptx')).statusCode, 202);
  assert.equal((await client.request('中文目录\\智慧 展厅.pptx')).statusCode, 200);
  assert.match(requests[0], /name=%E4%B8%AD%E6%96%87%E7%9B%AE%E5%BD%95%2F%E6%99%BA%E6%85%A7%20%E5%B1%95%E5%8E%85\.pptx/);
});

test('client forwards page and POST retry semantics', async t => {
  const seen = [];
  const { server, baseUrl } = await startServer((req, res) => {
    seen.push({ method: req.method, url: req.url });
    const failed = seen.length === 1;
    res.writeHead(failed ? 200 : req.method === 'POST' ? 202 : 200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(failed ? { status: 'FAILED', error: 'conversion failed' } : { status: req.method === 'POST' ? 'GENERATING' : 'READY' }));
  });
  t.after(() => server.close());
  const mediaDir = makeMediaDir();
  t.after(() => fs.rmSync(mediaDir, { recursive: true, force: true }));
  const client = new PptThumbnailClient({ baseUrl, mediaDir, timeoutMs: 2000 });

  const failed = await client.request('中文目录/智慧 展厅.pptx', { page: 3, quality: 'full' });
  assert.equal(JSON.parse(failed.body).status, 'FAILED');
  assert.match(seen[0].url, /&page=3&quality=full$/);
  assert.equal((await client.request('中文目录/智慧 展厅.pptx', { method: 'POST' })).statusCode, 202);
  assert.equal(seen[1].method, 'POST');
  assert.equal((await client.request('中文目录/智慧 展厅.pptx')).statusCode, 200);
});

test('client rejects absolute, traversal, non-PPT and missing paths before upstream', async t => {
  const mediaDir = makeMediaDir();
  t.after(() => fs.rmSync(mediaDir, { recursive: true, force: true }));
  const client = new PptThumbnailClient({ baseUrl: 'http://127.0.0.1:1', mediaDir, timeoutMs: 1000 });

  for (const name of ['C:\\secret.pptx', '../secret.pptx', '/secret.pptx', 'notes.pdf']) {
    assert.throws(() => client.resolveMediaName(name), error => error.statusCode === 400);
  }
  await assert.rejects(client.request('missing.pptx'), error => error.statusCode === 404);
});

test('proxy rewrites localhost image URLs to the requesting LAN host', () => {
  const body = Buffer.from(JSON.stringify({
    status: 'READY',
    pages: [{ page: 1, url: 'http://127.0.0.1:3025/thumbnails/a/page-001.png' }],
    page: { page: 1, url: 'http://localhost:3025/thumbnails/a/page-001.png' },
  }));
  const payload = JSON.parse(rewriteLocalThumbnailUrls(body, '192.168.16.61:3001'));
  assert.equal(payload.pages[0].url, 'http://192.168.16.61:3025/thumbnails/a/page-001.png');
  assert.equal(payload.page.url, 'http://192.168.16.61:3025/thumbnails/a/page-001.png');
});
