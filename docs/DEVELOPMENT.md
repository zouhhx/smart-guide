# 架构与开发指南

命令在对应工程目录执行。原现场地址只是参考，配置样例不能直接覆盖新客户生产配置。各工程的 README、HANDOFF 和 docs 包含进一步细节。

## 61 中控与 Web 管理：platform/central

入口 src/index.js，HTTP/API 3001，WebSocket 3000；正式 Web 页面端口 5180，与 3001 管理/API 入口属于同一业务系统。中控负责问答、文件检索、按 target 路由、播报及中断协调。

使用 Node 和 package-lock.json：`npm ci`，按 .env.example 准备环境后 `npm start`。样例地址、媒体路径、外部服务和凭据需重配。实际数据不包含在仓库。

核心位置按文件名查找 MessageRouter.js、FileServer.js，以及 src/tts、admin-ui、admin、scripts。当前正式 UI 使用 index-local-tts-v16.js，合并了机器人审核和本地音色。完整原 React 工程未确认取得，现有静态资源与补丁构建脚本已保留。读 scripts/voice-ui/README.md，勿只跑基础 UI 构建就覆盖入口而丢失后续合并。

针对性检查：`node --test test/faq-association.test.js test/admin-upload-pool-ui.test.js`。其他联调脚本可能连接现场，执行前核对目标。

## Windows 展示：apps/display-windows

.NET 8 SDK，在 Windows 执行 `dotnet restore ExhibitionClient.csproj` 和 `dotnet build ExhibitionClient.csproj -c Release`。以 csproj 为准，旧说明可能保留早期配置。

入口 Program/MainForm，重点文件 PPTController、StaOperationQueue、WebSocketService、PcmSpeechPlayer、RuntimeSettings。控制 PPT 依赖 PowerPoint，媒体使用 LibVLC；Unity 城市景观、第三方数字人及桥接软件需另行安装。主机 28 的现场 target 为 2。

## 小手机：apps/phone-android

JDK 17、Android SDK 34，使用自带 Gradle Wrapper。按 local.secrets.properties.example 和 docs 的运行配置提供讯飞凭据，不提交真实密钥。

`gradlew.bat :app:testDebugUnitTest :app:lintDebug :app:assembleRelease --no-daemon`

app 是 v1.7 主应用，face-app 是保留的独立模块。正式发布需公司签名，仓库没有签名私钥。

手机用自身语音 SDK：按下调用 /api/speech-interrupt 请求打断目标屏，同时继续录音；松手将识别文本提交 /api/asr-text。接口字段见 docs/api。202/DISPATCHED 只代表下发，不能当作播放器已停止。手持网关的离线 ASR 不服务手机。

## 手持话筒：services/voice-gateway

只收录 v0.3.8。Python 3.11～3.12，建议 3.12，依赖使用 requirements.lock / requirements-build.lock。构建和启动见 README、deploy、docs；models 及锁定文件说明外部模型，源码不含全部权重。Windows 现成包见根 release-assets。

采音、唤醒、VAD、SenseVoice 离线识别后向 61:3001/api/asr-text 提交文本及 target，来源为 microphone。网关 Web 端口 8765。

## 机器人安卓：apps/robot-android

构建 JDK 21、SDK 36（细节以 Gradle 配置为准），代码兼容 Java 8，Wrapper 和必要厂商 SDK 随源码保留。

`gradlew.bat testDebugUnitTest assembleDebug`

设备私有语音配置参考 iflytek-asr.example.json，与手机构建注入方式不同。机器人负责语音识别、播放和机身交互；音色在机器人屏幕管理。硬件联调需真实机器人。

## V5 中枢：services/robot-hub

Python/FastAPI，HTTP 8000、TCP 19888；历史部署为 192.168.18.227:/opt/exhibition-hub，本次未重新核查该主机。

独立 Python 3.12 环境安装 requirements-local-tested.txt，执行 `python -m pytest -q`。按 config.example.yaml 配置后 `python -m app.main config.yaml`。该依赖文件不是完整 Linux 生产锁，样例开关也需核对。

机器人通过 V5 对接 61 问答、教学分析与草稿。教学原文进入待审核流程，61 审核后发布；机器人回答由机器人播放。MiMo 用于意图判断，不替代 ASR/TTS。数据库需按目标环境初始化，未带入客户数据。

## 本地 TTS 与发布

现场模型 Qwen3 TTS 0.6B，现有服务 61:8769。机器人直接请求，中控也保留本地语音接入。数字人音色通过 61 业务管理入口调整；TTS 自带网页只是调试工具，不另列主要产品端。播放成功不等于支持数字人口型同步。

在分支开发，跑所改工程相关测试；跨端变更同步更新字段、target 和兼容说明。部署前备份现场二进制、配置与数据。没有统一 CI/CD 或已验证的一键全系统安装器。
