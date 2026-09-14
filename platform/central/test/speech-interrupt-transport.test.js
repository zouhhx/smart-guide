import fs from 'node:fs';
import vm from 'node:vm';
import http from 'node:http';
import assert from 'node:assert/strict';
import test from 'node:test';
const source = fs.readFileSync(new URL('../src/files/FileServer.js', import.meta.url), 'utf8');
const method = source.slice(source.indexOf('  async _handleSpeechInterrupt(req, res) {'), source.indexOf('  async _handleLocalTtsLive(req, res) {'))
  .replaceAll("await import('../router/MessageRouter.js')", '({messageRouter:globalThis.messageRouter})')
  .replaceAll("await import('../tts/TtsConfigStore.js')", '({ttsConfigStore:globalThis.ttsConfigStore})')
  .replaceAll("await import('../avatar/AvatarTargetCoordinator.js')", '({avatarTargetCoordinator:globalThis.avatarTargetCoordinator})');
async function fixture(fn, { provider = 'iflytek', fail = false } = {}) {
  const logs = [], stops = [], fences = [];
  const context = {
    console: { log: x => logs.push(x), warn: x => logs.push(x) },
    speechInterruption: { runOnce: (t, id, f) => f() },
    deviceManager: { getByScreenNumber: () => ({version:'speech-interrupt-v1'}) },
    messageRouter: { interruptSpeech: (t, id) => {fences.push([t,id]); return {delivered:true};} },
    ttsConfigStore: { get: () => ({provider,avatarEnabled:true}) },
    avatarTargetCoordinator: { interrupt: async t => {stops.push(t); if(fail) throw Error('bridge unavailable'); return {ok:true};} },
  };
  vm.createContext(context);
  vm.runInContext('this.H = class {' + method + '}', context);
  const handler = new context.H();
  handler._readJsonOrQuery = async req => {let body='';for await (const b of req)body+=b;return JSON.parse(body)};
  let connections = 0;
  const server = http.createServer((req,res) => handler._handleSpeechInterrupt(req,res));
  server.on('connection',()=>connections++);
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const agent = new http.Agent({keepAlive:true,maxSockets:1});
  async function press(id, target='2') {
    return new Promise((resolve,reject)=>{
      const req=http.request({host:'127.0.0.1',port:server.address().port,path:'/api/speech-interrupt',method:'POST',agent,headers:{'Content-Type':'application/json'}},res=>{
        let b='';res.on('data',d=>b+=d);res.on('end',()=>resolve({status:res.statusCode,headers:res.headers,body:JSON.parse(b)}));
      });req.on('error',reject);req.end(JSON.stringify({target,requestId:id}));
    });
  }
  try {await fn({press,logs,stops,fences,connections:()=>connections})}
  finally {agent.destroy();await new Promise(r=>server.close(r))}
}
test('phone keep-alive agent gets separate connections for consecutive presses',()=>fixture(async f=>{
  for(const id of ['first','second']){
    const r=await f.press(id);assert.equal(r.status,202);assert.equal(r.headers.connection,'close');
    assert.equal(r.body.requestId,id);assert.equal(r.body.target,'2');
    assert.equal(r.body.playbackConfirmed,false);assert.equal(r.body.queueClearRequested,true);
  }
  assert.equal(f.connections(),2);assert.equal(f.stops.length,2);assert.equal(f.fences.length,2);
}));
test('bridge failure remains explicit and request ID and cause are logged',()=>fixture(async f=>{
  const r=await f.press('failed-stop');assert.equal(r.status,503);assert.equal(r.body.status,'AVATAR_STOP_UNCONFIRMED');
  assert.equal(r.body.staleResultsBlocked,true);assert.equal(f.fences.length,1);
  assert.ok(f.logs.some(l=>l.includes('failed-stop')&&l.includes('bridge unavailable')));
},{fail:true}));
test('Qwen retains local-only cancellation',()=>fixture(async f=>{
  assert.equal((await f.press('qwen')).status,202);assert.equal(f.stops.length,0);assert.equal(f.fences.length,1);
},{provider:'qwen3'}));
test('invalid target does not stop any playback',()=>fixture(async f=>{
  assert.equal((await f.press('invalid','all')).status,400);assert.equal(f.stops.length,0);assert.equal(f.fences.length,0);
}));
