// ============================================================
// WebSocket 服务 - 所有端通过 WS 连接中转服务
// ============================================================
import { WebSocketServer } from 'ws';
import { v4 as uuidv4 } from 'uuid';
import { config } from '../config/index.js';
import { deviceManager } from '../devices/DeviceManager.js';
// messageRouter 延迟加载，避免循环依赖

export class WsServer {
  constructor(port) {
    this.port = port;
    this.wss = null;
    // ws -> { deviceId, type }
    this.clients = new Map();
  }

  start() {
    this.wss = new WebSocketServer({ port: this.port });

    this.wss.on('listening', () => {
      console.log(`[${new Date().toISOString()}] [INFO] [WsServer] WebSocket 服务启动: ws://0.0.0.0:${this.port}`);
    });

    this.wss.on('connection', (ws, req) => {
      const clientId = uuidv4();
      ws._clientId = clientId;
      this.clients.set(ws, { clientId, deviceId: null, type: null });

      console.log(`[${new Date().toISOString()}] [INFO] [WsServer] 新连接: ${clientId}`);

      // 发送连接成功 + clientId
      this._send(ws, {
        type: 'connected',
        clientId,
        serverTime: Date.now(),
      });

      ws.on('message', (raw) => this._onMessage(ws, raw));
      ws.on('close', () => this._onClose(ws));
      ws.on('error', (err) => this._onError(ws, err));
      ws.on('pong', () => {
        // 心跳响应，心跳正常
      });
    });

    // 定时心跳广播（服务器主动 ping 所有客户端）
    this._heartbeatInterval = setInterval(() => {
      for (const [ws] of this.clients) {
        if (ws.readyState === ws.OPEN) {
          ws.ping();
        }
      }
    }, config.HEARTBEAT.interval);

    return this;
  }

  stop() {
    if (this._heartbeatInterval) {
      clearInterval(this._heartbeatInterval);
    }
    if (this.wss) {
      this.wss.close();
    }
    console.log('[WsServer] 服务已停止');
  }

  // 发送消息给指定 ws
  sendTo(clientId, message) {
    for (const [ws, info] of this.clients) {
      if (info.clientId === clientId && ws.readyState === ws.OPEN) {
        this._send(ws, message);
        return true;
      }
    }
    return false;
  }

  sendToDevice(deviceId, message) {
    for (const [ws, info] of this.clients) {
      if (info.deviceId === deviceId && ws.readyState === ws.OPEN) {
        this._send(ws, message);
        return true;
      }
    }
    return false;
  }

  // 发送给指定类型的所有在线设备
  broadcastToType(type, message) {
    let count = 0;
    for (const [ws, info] of this.clients) {
      if (info.type === type && ws.readyState === ws.OPEN) {
        this._send(ws, message);
        count++;
      }
    }
    return count;
  }

  // 广播给所有客户端
  broadcast(message) {
    let count = 0;
    for (const [ws] of this.clients) {
      if (ws.readyState === ws.OPEN) {
        this._send(ws, message);
        count++;
      }
    }
    return count;
  }

  // 推送给移动端（手机遥控器）
  pushToMobile(message) {
    return this.broadcastToType(config.DEVICE_TYPES.MOBILE, message);
  }

  // 推送给客户端（展示屏）
  pushToClient(message) {
    return this.broadcastToType(config.DEVICE_TYPES.CLIENT, message);
  }

  // 推送给管理端
  pushToAdmin(message) {
    return this.broadcastToType(config.DEVICE_TYPES.ADMIN, message);
  }

  // 获取在线客户端数
  getOnlineCount() {
    let count = 0;
    for (const [ws] of this.clients) {
      if (ws.readyState === ws.OPEN) {
        count++;
      }
    }
    return count;
  }

  // ---------- 内部方法 ----------

  _onMessage(ws, raw) {
    console.log(`[${new Date().toISOString()}] [DEBUG] 收到消息: ${raw.toString().substring(0, 200)}`);
    let msg;
    try {
      msg = JSON.parse(raw.toString());
    } catch (e) {
      this._send(ws, { type: 'error', code: 'INVALID_JSON', message: '消息格式错误' });
      return;
    }

    const clientInfo = this.clients.get(ws);

    switch (msg.type) {
      case 'register':
        // 设备注册
        this._handleRegister(ws, clientInfo, msg);
        break;

      case 'heartbeat':
        // 心跳
        this._handleHeartbeat(ws, clientInfo, msg);
        break;

      case 'screen_snapshot':
        this._handleScreenSnapshot(clientInfo, msg);
        break;

      case 'client_status':
        this._handleClientStatus(clientInfo, msg);
        break;

      case 'chat':
      case 'voice':
      case 'command':
      case 'status':
      case 'sync':
        // 业务消息 -> 路由处理（延迟加载避免循环依赖）
        this._routeMessage(clientInfo, msg).catch(console.error);
        break;

      default:
        this._send(ws, { type: 'error', code: 'UNKNOWN_TYPE', message: `未知消息类型: ${msg.type}` });
    }
  }

  _handleRegister(ws, clientInfo, msg) {
    try {
      const device = deviceManager.register({
        type: msg.deviceType,
        name: msg.name,
        os: msg.os,
        version: msg.version,
        deviceId: msg.deviceId,
        screenNumber: msg.screenNumber,
        ipAddress: this._clientIp(ws),
      });

      // 关联 ws 和 device
      clientInfo.deviceId = device.id;
      clientInfo.type = device.type;

      this._send(ws, {
        type: 'registered',
        device,
        serverTime: Date.now(),
      });

      if (device.type === config.DEVICE_TYPES.CLIENT) {
        this._send(ws, {
          type: 'screen_settings',
          settings: deviceManager.getScreenSettings(device.id),
          serverTime: Date.now(),
        });
      }

      // 通知管理端有新设备注册
      this.pushToAdmin({
        type: 'device_event',
        event: 'registered',
        device,
      });

      console.log(`[${new Date().toISOString()}] [INFO] [WsServer] 设备注册成功: ${device.name}`);
      this._restoreActiveSpecialForDevice(device);
    } catch (e) {
      this._send(ws, { type: 'error', code: 'REGISTER_FAILED', message: e.message });
    }
  }

  _restoreActiveSpecialForDevice(device) {
    if (!device || device.type !== config.DEVICE_TYPES.CLIENT || !device.screenNumber) {
      return;
    }

    setTimeout(async () => {
      try {
        const current = deviceManager.get(device.id);
        if (!current?.online) return;

        const { messageRouter } = await import('../router/MessageRouter.js');
        const result = messageRouter.applyActiveSpecialForScreen(current.screenNumber, {
          source: 'auto-restore-special',
          clientInfo: {
            deviceId: 'auto-restore-special',
            type: config.DEVICE_TYPES.ADMIN,
          },
        });

        if (result?.applied?.length) {
          console.log(`[${new Date().toISOString()}] [INFO] [WsServer] restored active special for screen ${current.screenNumber}: ${result.special?.name || ''}`);
        } else if (result?.reason && result.reason !== 'no_active_special') {
          console.log(`[${new Date().toISOString()}] [WARN] [WsServer] skipped active special restore for screen ${current.screenNumber}: ${result.reason}`);
        }
      } catch (err) {
        console.warn(`[WsServer] restore active special failed: ${err.message}`);
      }
    }, 5000);
  }

  _handleHeartbeat(ws, clientInfo, msg) {
    if (!clientInfo.deviceId) {
      this._send(ws, { type: 'error', code: 'NOT_REGISTERED', message: '请先注册设备' });
      return;
    }

    const device = deviceManager.heartbeat(clientInfo.deviceId);
    this._send(ws, {
      type: 'heartbeat_ack',
      serverTime: Date.now(),
      deviceId: clientInfo.deviceId,
    });
  }

  _handleClientStatus(clientInfo, msg) {
    if (!clientInfo?.deviceId || clientInfo.type !== config.DEVICE_TYPES.CLIENT) {
      return;
    }

    const status = {
      cpuPercent: this._numberOrNull(msg.cpuPercent),
      memoryPercent: this._numberOrNull(msg.memoryPercent),
      memoryUsedMb: this._numberOrNull(msg.memoryUsedMb),
      memoryTotalMb: this._numberOrNull(msg.memoryTotalMb),
      processMemoryMb: this._numberOrNull(msg.processMemoryMb),
      uptimeSeconds: this._numberOrNull(msg.uptimeSeconds),
      view: typeof msg.view === 'string' ? msg.view : undefined,
      currentPptFileName: typeof msg.currentPptFileName === 'string' ? msg.currentPptFileName : undefined,
      currentPptSlideNumber: this._numberOrNull(msg.currentPptSlideNumber),
      currentCommentaryLevel: typeof msg.currentCommentaryLevel === 'string' ? msg.currentCommentaryLevel : undefined,
    };
    const device = deviceManager.updateStatus(clientInfo.deviceId, status);
    if (device) {
      this.pushToAdmin({
        type: 'device_event',
        event: 'status',
        deviceId: clientInfo.deviceId,
        status: device.status,
      });
    }
  }

  async _handleScreenSnapshot(clientInfo, msg) {
    if (!clientInfo?.deviceId || clientInfo.type !== config.DEVICE_TYPES.CLIENT) {
      return;
    }

    try {
      const { snapshotStore } = await import('../snapshots/SnapshotStore.js');
      const device = deviceManager.get(clientInfo.deviceId);
      const snapshot = snapshotStore.save({
        deviceId: clientInfo.deviceId,
        screenNumber: device?.screenNumber || msg.screenNumber,
        imageBase64: msg.imageBase64,
        width: msg.width,
        height: msg.height,
      });
      deviceManager.updateSnapshot(clientInfo.deviceId, snapshot);
      this.pushToAdmin({
        type: 'device_event',
        event: 'snapshot',
        deviceId: clientInfo.deviceId,
        snapshot,
      });
    } catch (err) {
      console.warn(`[WsServer] screen snapshot failed: ${err.message}`);
    }
  }

  async _routeMessage(clientInfo, msg) {
    const { messageRouter } = await import('../router/MessageRouter.js');
    messageRouter.route(clientInfo, msg);
  }

  _onClose(ws) {
    const clientInfo = this.clients.get(ws);
    if (clientInfo?.deviceId) {
      deviceManager.offline(clientInfo.deviceId);
      // 通知管理端
      this.pushToAdmin({
        type: 'device_event',
        event: 'offline',
        deviceId: clientInfo.deviceId,
      });
    }
    this.clients.delete(ws);
    console.log(`[${new Date().toISOString()}] [INFO] [WsServer] 连接断开: ${clientInfo?.clientId}`);
  }

  _onError(ws, err) {
    console.error(`[${new Date().toISOString()}] [ERROR] [WsServer] WS 错误:`, err.message);
  }

  _send(ws, data) {
    if (ws.readyState === ws.OPEN) {
      ws.send(JSON.stringify(data));
    }
  }

  _clientIp(ws) {
    const raw = ws?._socket?.remoteAddress || '';
    return String(raw).replace(/^::ffff:/, '');
  }

  _numberOrNull(value) {
    const num = Number(value);
    return Number.isFinite(num) ? num : null;
  }
}

export const wsServer = new WsServer(config.PORT);
export default wsServer;
