const path = 'D:/exhibition-hall-new/runtime/exhibition-hall-platform/data/tts-cache/direct-short-' + Date.now() + '.mp3';
const endpoint = 'http://127.0.0.1:3011/tts';
const text = '欢迎来到思德科技展厅，正在测试语音合成速度。';
const started = performance.now();
const res = await fetch(endpoint, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ text, voice: 'xiaoxiao', output: path, retries: 0 })
});
const elapsed = performance.now() - started;
const body = await res.text();
let parsed;
try { parsed = JSON.parse(body); } catch { parsed = { raw: body }; }
console.log(JSON.stringify({ status: res.status, elapsedMs: Math.round(elapsed), textLength: Array.from(text).length, ok: parsed.ok, error: parsed.error || null }, null, 2));
