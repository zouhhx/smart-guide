# PROJECT_STATUS

> 快照日期：2026-09-14
>
> 项目目录：`D:\work\exhibition-hall_AsrApk`
>
> Android 版本：`versionName=1.8`、`versionCode=9`、包名 `com.codexpro.digitalhumanasr`

## 2026-09-14：屏幕名称简化

- 首页、选屏列表和选择成功提示统一显示 `${screenNumber}号屏`，例如“2号屏”“4号屏”，不再显示接口中的电脑名称或型号。
- 仍使用接口权威 `screenNumber`，不是列表序号；实际 target、排序、在线状态、已选屏幕和所有控制接口保持不变。
- 新包路径：`releases/app-release-simple-screen-names-v1.8-20260914.apk`；验证与安装说明见 `releases/v1.8-release-notes.md`。
- 2026-09-10 交付 ZIP/bundle 仍是 v1.7 历史基线，不包含本次修改；后续整合应使用本工作区最新源码和 v1.8 成品。
- 偶发打断失败仍是已知问题，本次仅简化名称。下方 v1.7 验证记录保留作为历史记录。

## 当前目标

本地仓库已建立，main 分支、asr-apk-v1.7-baseline 标签。此前无 Git 提交历史；本次为首次经过凭据清理的源码基线，未配置远程、未上传 GitLab。整合说明见 docs/GITLAB_INTEGRATION_HANDOFF.md。
真实讯飞授权已迁移到被忽略的 local.secrets.properties，构建通过 resValue 注入资源，环境变量优先；缺失配置时构建明确失败。现有 APK 保留原始文件与哈希，生成时间早于首次 Git 基线。

展厅讲解员使用 Android 小手机选择目标屏幕，通过讯飞流式 ASR 将讲话转成最终文字并提交给现有 61 中控；首页保留按住说话、上滑取消、左滑、右滑和双击交互，UI 结构不变。

## 当前权威接口

权威外部资料：`D:\讲解修复\phone-api-docs\小手机APP接口文档.md`（1.0，2026-09-09）。

可携带版本：docs/api/phone-api-v1.md、docs/api/speech-interrupt-v15.md，原 Postman 集合也已保存。

打断补充协议：`D:\讲解修复\phone-api-docs\手机按麦克风打断接口-v15.md`（2026-09-10）。

- 默认配置：`http://192.168.16.61:3001/api/asr-text`
- `GET /api/screens`：匿名获取屏幕列表。
- `POST /api/asr-text`：提交最终 ASR 文字，正文为 `text`、`target`、`source`、`requestId`。
- `POST /api/remote-control`：提交 `action`、`target`、`source`。
- `POST /api/speech-interrupt`：每次按下麦克风异步发送 `target/requestId`，requestId 为新的 `mic-press-<UUID>`，与语音问题 ID 不同。
- 左滑：`gesture_swipe_left`；PPT 模式下由中控映射为下一页。
- 右滑：`gesture_swipe_right`；PPT 模式下由中控映射为上一页。
- 双击：`gesture_double_tap`；PPT 模式下讲解当前页，文件选择模式下打开选中文件。
- `target` 必须是屏幕编号字符串；`source` 为安装后持久不变的 `hall-phone-<UUID>`。
- 四个接口均不使用登录、Bearer Token、Knowledge Base ID 或冻结 InputEvent。

旧 `/ready`、`/v1/screens`、`/v1/input-events` 方案已于 2026-09-09 被产品方明确整体弃用，禁止双发或回退。

## 已完成改造

- 设置页仅填写中控 ASR 接口地址，默认值已预置；保留目标锁定、声音反馈和运行状态。
- 地址变化时清除旧屏幕目录、选择和锁定，避免跨中控误控。
- `TargetScreenRepository` 每 5 秒匿名刷新 `/api/screens`，按 `screenNumber` 排序并保存选屏。
- 离线屏幕显示但不可选择；未选在线屏幕时阻止提交问题和手势。保留原来的本地 ASR 行为。
- 语音仍使用 SparkChain + AudioRecord 流式 ASR；临时结果仅预览，最终非空文字只提交一次。
- 语音请求生成 UUID `requestId`，固定本次 `target`，不发送 `clientMode`、`suppressTts` 或 `sessionId`。
- 左滑、右滑和双击只发送一次精确手势名，不发送固定 PPT 动作或客户端页码。
- 普通问题/手势单线程、单飞发送；打断使用独立 OkHttp 通道，避免被 90 秒问题请求阻塞。
- 按下立即固定中控配置和屏号、生成打断请求并继续 ASR。允许在等待上一轮 HTTP 回答时按下，关闭旧 HTTP 等待并丢弃旧回调；真正的播放与旧轮拦截由中控负责。
- 打断请求只发送一次、不持久化、不自动重试（含 HTTP 自动重试）；最终文字提交前取消旧打断等待，下一次按下或 Activity 停止也清理旧等待。取消本地等待无法撤回中控已收到的命令。
- 打断失败显示简短提示，但不阻塞录音/松手提交；上滑取消不提交新问题，按下产生的打断仍有效。
- HTTP 202 只有在目标/请求 ID 一致、DISPATCHED、delivered、staleResultsBlocked 和 queueClearRequested 为 true 且 playbackConfirmed 为 false 时视作已下发，不宣称扬声器停止。
- 日志标签 `SpeechInterrupt` 记录本次 requestId、target 和结果，不记录识别内容。
- 响应区分 HTTP/`ok:false`、`ignored:true`、`duplicate:true` 和普通接收成功；不把 HTTP 200 当作物理大屏已执行。
- 连接超时 5 秒、屏幕查询 10 秒、手势 10 秒、语音文字处理 90 秒。
- 独立打断连接超时 2 秒、总超时 3 秒。
- 明文 HTTP 仍限字面量回环或私有 IP；HTTPS 可使用域名。配置必须是完整 `/api/asr-text` 地址。

## 不变范围

- 原生 Android Kotlin + XML/ViewBinding，minSdk 24、targetSdk 34。
- 讯飞 SparkChain 流式动态修正、实时文字、松手保留、下一次讲话覆盖。
- 按下立即收音、上滑取消、回滑恢复。
- 首页和选屏 UI 布局、手势区域、目标锁定、声音/触觉反馈。
- 不引入数据库。

## 验证状态

- 2026-09-10：Debug 编译与 JVM 单元测试通过，18/18，0 failure、0 error、0 skipped；`lintDebug` 0 error、111 warning；Release 构建成功。
- 新增 7 个测试覆盖打断 202 与身份校验、错误/旧客户端/离线、503 不自动重试、非法目标、本地取消、慢请求期间独立打断、旧响应隔离及新问题正常提交。
- MockWebServer 已验证匿名请求、三个路径、语音 JSON、精确手势名、`ignored`/`duplicate` 和 `ok:false`。
- 2026-09-09 曾只读访问现场 `GET /api/screens`：返回 9 个屏幕；2、4 在线，5～11 离线。此为历史快照，不表示当前在线状态。
- 当前安装包：`releases/app-release-speech-interrupt-v1.7-20260910.apk`；非 debuggable Release 行为包，沿用 Android Debug 证书（SHA-256：`18fcfbb0c08687a5f5995d56c5a97dfa67d8e2d58005d1d142cb50c3029f31bd`），APK Signature v2/v3 验证通过。使用 releases 目录保留交付包，避免后续 Gradle 打包清理。
- APK SHA-256：`68D604C7B77C5FBD2918FBBA37302C023E89CA6DFEAACC6570D5D97BEB1724ED`。
- 2026-09-10 ADB 未连接设备；本轮没有向现场屏幕发送打断请求，未执行手机到扬声器的真机测试。
- 仍需真机连接 61 中控和真实大屏完成现场 E2E。

## 下一步 TODO

0. 优先定位用户现场反馈的“偶发打断失败”；详情见 docs/KNOWN_ISSUES.md。本次建仓未修复该问题，不能据单元测试通过声明现场稳定。
1. 真机覆盖安装 v1.7，确认设置和已选屏幕保留；让 2 号屏播报时按下，检查立即停止、松手后新问题正常播报。测试空闲、快速按下松手、取消录音、旧回答尚未返回时再次按下和切屏场景。
2. 验证语音查找→选择→打开，全程目标屏号不变且最终结果只提交一次。
3. 在文件选择、PPT、normal 三种状态验证左滑、右滑、双击及 `ignored`。
4. 验证 800ms 快速同向滑动过滤、第一页/末页、离线、断网与 90 秒超时。
5. 保留录像和 `requestId/source/target/action/http/seq/mode/ignored` 日志。

## 避坑

- `5180` 是管理后台页面端口，不是手机 API。
- `target` 只发屏幕号字符串，不能发 IP、deviceId、名称或 `all`。
- 不要重放响应里的 `actions`，中控已经负责下发。
- 不要自动重试手势或超时请求；超时代表结果未知。
- 左滑/右滑在文件列表和 PPT 中由中控映射，手机不得自行猜测或交换动作名。
- HTTP 200/`ok:true` 只是中控已处理，不是展示端完成回执。
- 按 v15 文档，完整打断能力适用于本地 Qwen3；2 号屏已部署，4 号屏仍需升级。409 CLIENT_UPGRADE_REQUIRED / TTS_PROVIDER_UNSUPPORTED、503 TARGET_OFFLINE 不得当作成功。
- 不调用 remote-control 的 stop_speech 或旧 tts-live stop。手机本轮无现场声音确认，不将服务端文档的测试结果算作 APK 真机验收。
