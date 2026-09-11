// ============================================================
// 展厅智能中控 - 本地中转服务
// Phase 1: WebSocket + 设备管理 + 消息路由
// Phase 2: 静态文件服务 + 屏幕编号分发
// ============================================================

import { config } from './config/index.js';
import { wsServer } from './ws/WsServer.js';
import { deviceManager } from './devices/DeviceManager.js';
import { openClawBridge } from './api/OpenClawBridge.js';
import { fileServer } from './files/FileServer.js';
import { mediaLibrary } from './media/MediaLibrary.js';
import { qaStore } from './qa/QaStore.js';
import { specialConfigStore } from './specials/SpecialConfigStore.js';
import { knowledgeDb } from './knowledge/KnowledgeDb.js';
import { vectorKnowledgeService } from './knowledge/VectorKnowledgeService.js';
import { weatherService } from './weather/WeatherService.js';

console.log('');
console.log('╔══════════════════════════════════════════════╗');
console.log('║     展厅智能中控 - 本地中转服务             ║');
console.log('╚══════════════════════════════════════════════╝');
console.log('');

// 启动 WebSocket 服务
wsServer.start();

// 启动静态文件服务
fileServer.start();

// 启动天气缓存服务：默认城市每日刷新一次，问到天气时优先读本地缓存。
weatherService.start();

// 初始化 Markdown 运行索引。Obsidian/Markdown 是知识源，中控只构建内存/缓存索引。
try {
  const status = knowledgeDb.syncFromCurrentStores({ mediaLibrary, qaStore, specialConfigStore });
  console.log(`[${new Date().toISOString()}] [INFO] Markdown知识索引: ${status.indexPath}`);
  vectorKnowledgeService.rebuildFromKnowledgeDb().then(result => {
    if (result.ok) {
      console.log(`[${new Date().toISOString()}] [INFO] ChromaDB向量索引: ${result.documents} 条`);
    } else {
      console.warn(`[${new Date().toISOString()}] [WARN] ChromaDB向量索引未启用/不可用: ${result.reason || result.error}`);
    }
  });
} catch (err) {
  console.warn(`[${new Date().toISOString()}] [WARN] Markdown知识索引初始化失败: ${err.message}`);
}

// 测试 OpenClaw 连接
setTimeout(async () => {
  const ok = await openClawBridge.ping();
  console.log(`[${new Date().toISOString()}] [INFO] OpenClaw 连接: ${ok ? '✅ 在线' : '❌ 离线'}`);
}, 2000);

// 定期打印设备状态（每 30 秒）
setInterval(() => {
  const summary = deviceManager.getSummary();
  const screenInfo = summary.screens.length > 0
    ? ` | 屏幕: ${summary.screens.map(s => `${s.screenNumber}号`).join(',')}`
    : '';
  console.log(`[${new Date().toISOString()}] [INFO] 设备状态: 在线 ${summary.online}/${summary.total} | 移动端 ${summary.byType.mobile?.online || 0} | 客户端 ${summary.byType.client?.online || 0} | 管理端 ${summary.byType.admin?.online || 0}${screenInfo}`);
}, 30000);

// 优雅退出
process.on('SIGINT', () => {
  console.log('\n[Server] 收到退出信号，正在关闭...');
  wsServer.stop();
  weatherService.stop();
  fileServer.stop();
  knowledgeDb.close();
  process.exit(0);
});

process.on('SIGTERM', () => {
  console.log('\n[Server] 收到终止信号，正在关闭...');
  wsServer.stop();
  weatherService.stop();
  fileServer.stop();
  knowledgeDb.close();
  process.exit(0);
});

console.log(`[${new Date().toISOString()}] [INFO] WebSocket: ws://0.0.0.0:${config.PORT}`);
console.log(`[${new Date().toISOString()}] [INFO] 文件服务: http://0.0.0.0:${config.FILE_PORT}`);
console.log(`[${new Date().toISOString()}] [INFO] 媒体目录: ${config.MEDIA_DIR}`);
console.log(`[${new Date().toISOString()}] [INFO] OpenClaw: ${config.OPENCLAW.baseUrl}`);
console.log('');
