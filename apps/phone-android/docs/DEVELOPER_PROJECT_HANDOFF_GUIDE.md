# Android 小手机开发者交接指南

> 当前版本：1.7；协议切换日期：2026-09-09；麦克风打断接入：2026-09-10。

## 阅读顺序

1. 根目录 `PROJECT_STATUS.md`
2. `docs/ANDROID_RUNTIME_CONFIGURATION.md`
3. 外部权威接口文档 `D:\讲解修复\phone-api-docs\小手机APP接口文档.md`
4. 打断接口 `D:\讲解修复\phone-api-docs\手机按麦克风打断接口-v15.md`

接口的仓库内副本位于 docs/api/；上传整合步骤见 docs/GITLAB_INTEGRATION_HANDOFF.md。注意 docs/KNOWN_ISSUES.md 已记录真实现场的偶发打断失败。

## 模块职责

- `MainActivity.kt`：页面编排、设置、状态与动作入口。
- `VoiceInputController.kt`：SparkChain 流式 ASR，不包含中控协议。
- `gesture/*`：按住/取消及左滑、右滑、双击识别，不拼接网络请求。
- `target/TargetScreenRepository.kt`：`GET /api/screens`、解析和本地选屏。
- `input/GatewayConfiguration.kt`：配置地址校验及四个固定端点派生。
- `input/SpeechInterruptClient.kt`：独立异步打断通道、一次性请求体、202 下发响应校验。
- `input/ControlPlaneInputClient.kt`：当前 JSON 请求和响应解析。
- `input/InputSubmissionCoordinator.kt`：单线程、单飞发送和目标绑定。
- `network/NetworkStateMonitor.kt`：以 `GET /api/screens` 探测中控可达性。

## 必须保持的共识

- UI、讯飞 ASR、按住说话和上滑取消行为保持现状。
- 设置页只配置完整 `/api/asr-text` 地址。
- `target` 来自屏幕目录的 `screenNumber`；`source` 是稳定安装 ID。
- 手势发送 `gesture_swipe_left/right/double_tap`，动作含义由中控当前 mode 决定。
- 不自动重试，不把 HTTP 200 当作大屏完成，不重放响应 `actions`。
- 打断在按下时发出，录音/松手不等待其成功；新问题前清理旧打断等待，旧问题回调由 generation 隔离；本地 HTTP 取消不等于停止远端计算/声音。
- 不恢复 2026-09-09 前的 `/v1/*`、Token、KB 或 InputEvent 代码。

## 发布前验证

讯飞授权从 local.secrets.properties（Git 忽略）或 SPARK_APP_ID/SPARK_API_KEY/SPARK_API_SECRET 环境变量提供，禁止恢复 strings.xml 中的明文凭据。

运行 `testDebugUnitTest`、`lintDebug` 和 Release 构建，然后按 `PROJECT_STATUS.md` 的现场 TODO 在 61 中控及真实大屏验收。
