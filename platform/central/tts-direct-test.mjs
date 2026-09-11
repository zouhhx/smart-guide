const fs = await import('node:fs/promises');
const path = 'D:/exhibition-hall-new/runtime/exhibition-hall-platform/data/tts-cache/direct-' + Date.now() + '.mp3';
const endpoint = 'http://127.0.0.1:3011/tts';
const seed = '欢迎来到思德科技展厅，这里展示数字化展陈、智能交互、多媒体控制和人工智能问答能力。现在进行一百字语音合成速度测试，请保持自然语速和清晰发音。测试编号' + Date.now();
let text = seed;
while (Array.from(text).length < 100) text += '展';
text = Array.from(text).slice(0, 100).join('');
const started = performance.now();
const res = await fetch(endpoint, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ text, voice: 'xiaoxiao', output: path, retries: 1 })
});
const elapsed = performance.now() - started;
const body = await res.text();
let parsed;
try { parsed = JSON.parse(body); } catch { parsed = { raw: body }; }
let size = 0;
try { size = (await fs.stat(path)).size; } catch {}
console.log(JSON.stringify({ status: res.status, elapsedMs: Math.round(elapsed), textLength: Array.from(text).length, ok: parsed.ok, size, output: path, error: parsed.error || null }, null, 2));
