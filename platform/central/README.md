# 展厅智能中控系统

基于 OpenClaw 的智能展厅控制系统，支持 ASR APK 拾音输入 + C# 展示端联动 + 管理后台。

## 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    OpenClaw (小龙虾)                     │
│            意图理解 · 知识问答 · 指令生成                 │
└──────────────────────────┬──────────────────────────────┘
                           │ /v1/chat/completions
┌──────────────────────────▼──────────────────────────────┐
│                   本地中转服务 (Node.js)                  │
│   WebSocket Hub · ASR文本入口 · 文件服务 · 设备管理 · 指令路由 │
└──────┬──────────────────┬──────────────────┬───────────┘
       │                  │                  │
  ┌────▼────┐        ┌────▼─────┐      ┌─────▼─────┐
  │📱ASR APK│        │🖥️展示屏   │      │⚙️ 管理端  │
  │(拾音转文)│        │(C# 客户端)│      │(后台Web)  │
  │Android  │        │WinForms  │      │Vue3/ElementPlus│
  └─────────┘        └──────────┘      └───────────┘
```

## 快速启动

```bash
cd exhibition-hall
bash start.sh
```

## 服务地址

| 服务 | 地址 | 说明 |
|------|------|------|
| 管理端 Web | http://localhost:5180 | 后台管理 |
| 中转服务 WS | ws://localhost:3000 | 内部通信 |
| 文件服务 HTTP | http://localhost:3001 | 媒体文件列表与下载 |
| 使用统计 API | http://localhost:3001/api/usage-stats | 提问次数、高频关注点、最近问题 |
| ChromaDB 向量服务 | http://localhost:3021 | FAQ、文件和 Markdown 片段语义检索 |
| OpenClaw | http://localhost:18789 | 智能中枢 |
| C# 展示端 | 独立 WinForms 客户端 | 展示屏播放与响应 |
| ASR APK | 独立 Android 客户端 | 只负责拾音、转文字、发送到中控 |

## 项目结构

```
exhibition-hall/
├── src/                        # 中转服务（Phase 1）
│   ├── index.js                # 入口
│   ├── config/index.js         # 配置
│   ├── ws/WsServer.js          # WebSocket 服务器
│   ├── devices/DeviceManager.js # 设备管理
│   ├── router/MessageRouter.js  # 消息路由
│   └── api/OpenClawBridge.js    # OpenClaw 桥接
│
├── admin/                      # 管理端（Phase 5）
│   └── src/
│       └── App.vue            # 管理界面（总览/设备/知识库/讲解词/音色/设置）
│
└── media/                      # 演示素材
```

## Phase 开发进度

| Phase | 内容 | 状态 |
|-------|------|------|
| Phase 1 | 本地中转服务骨架 | ✅ 完成 |
| Phase 2 | 小龙虾展厅控制 Skill | ✅ 完成 |
| Phase 3 | ASR APK 拾音输入 | 🚧 规划接入中 |
| Phase 4 | 展示端展示响应（已迁移到 C# 客户端） | ✅ 完成 |
| Phase 5 | 管理端后台 | ✅ 完成 |

## 功能清单

### ASR APK
- 🎤 拾音并进行语音转文字
- 📤 将识别文本发送到本地中控服务
- 🎯 可携带目标屏幕信息
- 🚫 不负责回答显示、TTS 朗读和复杂控制逻辑

### 展示端
- 🎬 视频播放（支持本地文件）
- 📄 PPT/PDF/图片展示
- 🔊 TTS 语音播报
- ❓ 问答展示
- ⛶ 全屏支持
- 📡 WebSocket 指令响应
- 🪟 当前使用独立 C# WinForms 客户端

### 管理端
- 📊 总览（设备状态 + 对话记录）
- 🖥️ 设备管理（在线状态 + 控制测试）
- 📚 知识库管理（上传/分类/删除）
- 📝 讲解词管理（场景配置）
- 🎵 音色配置（TTS 试听）
- ⚙️ 系统设置（服务地址配置）

## 展厅使用统计

Node 中控会自动记录每次来自 ASR APK、WebSocket 文字和 WebSocket 语音入口的提问，用于后续回答“用户对什么提问比较感兴趣”“提问最多的是什么”等问题。

- 原始事件：`data/usage-events.jsonl`
- 聚合摘要：`data/usage-summary.json`
- 统计接口：`GET /api/usage-stats`
- 最近事件：`GET /api/usage-events?limit=50`

统计摘要会自动注入 OpenClaw 上下文，后续用户询问“大家关心什么”“提问最多的是什么”时，中控可以依据真实展厅使用记录回答。

## ChromaDB 向量知识库

中控支持 ChromaDB 本地向量库。Markdown/Obsidian、QA 和媒体文件仍是内容来源；启动向量服务后，中控会把 FAQ、文件标题标签概括、Markdown 片段同步到 ChromaDB，并在 FAQ 命中、文件搜索和 RAG 问答中优先使用语义召回。

默认使用 `VECTOR_EMBEDDING_PROVIDER=hash`，无需外网下载模型，适合展厅服务器稳定启动。后续如需切换 BGE，可改为 `VECTOR_EMBEDDING_PROVIDER=bge` 并提前缓存 `VECTOR_EMBEDDING_MODEL`。

```powershell
cd exhibition-hall-platform
python -m pip install -r requirements-vector.txt
powershell -ExecutionPolicy Bypass -File scripts\start-vector-service.ps1
```

常用接口：

- `POST /api/knowledge/vector-sync`：重建向量索引
- `GET /api/knowledge/status`：查看向量服务状态
- `GET /api/knowledge/vector-search?q=低空经济&types=resource,chunk`：调试语义检索

## 硬件准备

- 展示屏（电脑/一体机，运行 C# 展示端客户端）
- 手机（安装 ASR APK，作为拾音输入端）
- 局域网连接

## 文件部署

将展示文件（视频/PPT/PDF）放到服务器目录（如 `/var/www/files/`），展示端通过 `/files/文件名` 访问。
