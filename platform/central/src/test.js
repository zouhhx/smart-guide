// ============================================================
// Phase 1 冒烟测试 - 模拟三端连接
// 运行: node src/test.js
// ============================================================

import WebSocket from 'ws';

const SERVER = 'ws://127.0.0.1:3000';

async function wait(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function run() {
  console.log('\n========== Phase 1 冒烟测试 ==========\n');

  // 测试1: 连接 + 注册移动端
  console.log('【测试1】连接 + 注册移动端...');
  const ws1 = new WebSocket(SERVER);
  await new Promise((res, rej) => {
    ws1.on('open', () => console.log('  ✅ WS 连接成功'));
    ws1.on('message', (data) => {
      const msg = JSON.parse(data.toString());
      if (msg.type === 'connected') res();
    });
    ws1.on('error', rej);
  });

  ws1.send(JSON.stringify({ type: 'register', deviceType: 'mobile', name: '测试手机', os: 'iOS', version: '1.0.0' }));
  await new Promise((res) => {
    ws1.on('message', (data) => {
      const msg = JSON.parse(data.toString());
      if (msg.type === 'registered') {
        console.log(`  ✅ 移动端注册成功: ${msg.device.id}`);
        res();
      }
    });
  });

  // 测试2: 心跳
  console.log('\n【测试2】心跳...');
  ws1.send(JSON.stringify({ type: 'heartbeat' }));
  await new Promise((res) => {
    ws1.on('message', (data) => {
      const msg = JSON.parse(data.toString());
      if (msg.type === 'heartbeat_ack') {
        console.log('  ✅ 心跳响应成功');
        res();
      }
    });
  });

  // 测试3: 注册客户端
  console.log('\n【测试3】注册客户端（展示屏）...');
  const ws2 = new WebSocket(SERVER);
  await new Promise((res) => {
    ws2.on('open', () => {
      ws2.send(JSON.stringify({ type: 'register', deviceType: 'client', name: '展厅大屏', os: 'Windows', version: '1.0.0' }));
    });
    ws2.on('message', (data) => {
      const msg = JSON.parse(data.toString());
      if (msg.type === 'registered') {
        console.log(`  ✅ 客户端注册成功: ${msg.device.id}`);
        res();
      }
    });
    ws2.on('error', console.error);
  });

  // 测试4: 发送聊天消息
  console.log('\n【测试4】发送聊天消息...');
  ws1.send(JSON.stringify({ type: 'chat', text: '帮我播放公司介绍视频' }));
  await new Promise((res) => {
    ws1.on('message', (data) => {
      const msg = JSON.parse(data.toString());
      console.log(`  📩 收到回复: type=${msg.type}, text=${msg.text || ''}`);
      res();
    });
    setTimeout(() => { console.log('  ⏳ OpenClaw 未配置，继续...'); res(); }, 3000);
  });

  // 测试5: 广播指令
  console.log('\n【测试5】广播控制指令 -> all 客户端...');
  ws1.send(JSON.stringify({ type: 'command', action: 'play_video', target: 'all', params: { file: 'intro.mp4' } }));
  await new Promise((res) => {
    let done = false;
    ws2.on('message', (data) => {
      const msg = JSON.parse(data.toString());
      if (msg.type === 'command') {
        console.log(`  ✅ 客户端收到指令: ${msg.action} params=${JSON.stringify(msg.params)}`);
        done = true;
        setTimeout(res, 100);
      }
    });
    setTimeout(() => { if (!done) { console.log('  ⏳ 等待指令...'); res(); } }, 2000);
  });

  // 清理
  ws1.close();
  ws2.close();
  await wait(300);

  console.log('\n========== ✅ Phase 1 测试完成 ==========');
  console.log('服务运行中: ws://0.0.0.0:3000');
  console.log('下一步: Phase 2 - 开发小龙虾展厅控制 Skill\n');
  process.exit(0);
}

run().catch(err => {
  console.error('\n❌ 测试失败:', err.message);
  console.error('请先启动服务: npm run dev\n');
  process.exit(1);
});
