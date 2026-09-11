# 运营能力网关 V1

运营能力网关是现有中控能力的兼容 Facade。它不参与问答、RAG、联网搜索或自然语言路由，只负责执行经过鉴权和白名单校验的运营动作。

当前采用旁路接入：运行中的展厅 RAG 继续使用原接口，新系统使用版本化网关接口。两条路径最终复用同一套数字人、TTS、WebSocket 屏幕、媒体库和设备状态实现。

首批能力固定为：

- `avatar.speak`
- `tts.speak`
- `screen.command`
- `media.command`
- `monitor.status`

调用方必须提交 `requestId`、`eventId`、`capability`、`target` 和 `params`。网关只有在能力同时满足“契约已声明、运行白名单已开启、处理器已注册”时才执行。相同 `eventId` 只执行一次。

## 当前 RAG 的兼容关系

现有 RAG 不需要改造，以下调用保持不变：

| 现有能力 | 当前入口 | 本批处理 |
| --- | --- | --- |
| 问答后的屏幕动作 | `MessageRouter` → WebSocket command | 保留 |
| 普通 TTS | `POST /api/tts` | 保留 |
| TTS 配置 | `GET/POST /api/tts-settings` | 保留 |
| 数字人目标 | `GET/POST /api/avatar-target` | 保留且继续作为唯一人工目标来源 |
| 数字人播报 | `AvatarTargetCoordinator.say()` | 保留 |
| 屏幕状态 | `GET /api/screens` | 保留 |

不得让现有 RAG 和新网关对同一个回答同时执行播报。迁移某个调用方时，应在调用方侧选择旧路径或网关路径其中之一，并使用唯一 `eventId`。

## 新系统接口

基础地址：`http://<中控主机>:3001/api/operations/v1`

- `GET /health`：网关契约、启用状态和白名单。
- `GET /capabilities`：同上，供接入方能力发现。
- `POST /execute`：执行一次运营能力。

每次请求必须包含：

```http
Authorization: Bearer <OPERATIONS_GATEWAY_TOKEN>
X-Operations-Caller: new-rag
Content-Type: application/json
```

调用示例：

```json
{
  "version": "operations.v1",
  "requestId": "rag-20260827-0001",
  "eventId": "answer-20260827-0001-speech",
  "capability": "avatar.speak",
  "target": "selected",
  "params": {
    "text": "欢迎参观思德科技展厅"
  },
  "timeoutMs": 15000
}
```

`eventId` 表示一次业务动作，网络重试必须沿用同一个值；新的业务动作必须使用新值。幂等结果会落入 `data/operations-idempotency.json`，服务重启后仍能避免重复执行。

## 能力参数

### `avatar.speak`

- `params.text` 必填。
- `target` 使用 `selected`，或与运营端当前人工选择的目标一致。
- 网关禁止改变数字人目标屏。

### `tts.speak`

- `params.text` 必填。
- `params.delivery` 为 `screen`（默认）或 `url`。
- `screen` 会沿用现有展示端 `speak` command；`url` 仅返回音频地址，由新系统自行播放。

### `screen.command`

仅允许：`home`、`blackout`、`next_slide`、`prev_slide`、`goto_slide`、`close_ppt`。`goto_slide` 还需提供正整数 `params.slide`。

### `media.command`

仅允许：`play_ppt`、`play_video`、`show_doc`。文件必须通过 `fileId`、`file` 或 `name` 命中现有媒体库，且命令必须与文件类型一致。

### `monitor.status`

只读返回设备、数字人目标和 TTS 配置，不产生屏幕或播报动作。

## 生产配置

```dotenv
OPERATIONS_GATEWAY_ENABLED=true
OPERATIONS_GATEWAY_TOKEN=<独立随机密钥>
OPERATIONS_GATEWAY_ALLOWED_CALLERS=new-rag
OPERATIONS_GATEWAY_ALLOWED_CAPABILITIES=avatar.speak,tts.speak,screen.command,media.command,monitor.status
OPERATIONS_GATEWAY_TIMEOUT_MS=15000
```

Token 只写入运行环境，不提交 Git。新增调用方时只扩展 `OPERATIONS_GATEWAY_ALLOWED_CALLERS`，不要共用或伪造其他系统的 caller id。

独立 staging 必须设置 `AVATAR_TASK_CONTROL_ENABLED=false`，防止仅加载测试进程就操作生产数字人计划任务；生产默认值仍为 `true`。
