# VoiceGateway G10-C 本地语音网关

当前基线版本为 **v0.3.8**。程序运行在 Windows 主机上，使用本地模型完成：

```text
麦克风输入 → KWS 唤醒 → VAD 断句 → SenseVoice ASR → 结构化结果
```

项目同时提供局域网 Web 管理端，可自动启动并监督语音服务、选择录音设备、调节 Windows 系统输入音量、观察实时输入电平、修改触发模式、唤醒词与 KWS/VAD 参数，并查看状态、识别结果和日志。v0.3.4 在本地 ASR 完成后异步调用局域网问答接口并展示回答；v0.3.5 增加醒目的唤醒/聆听指示；v0.3.7 在KWS唤醒成功后，通过现有局域网接口异步发送命中的唤醒词；v0.3.8 增加可与唤醒词模式切换的分贝触发模式，并支持运行中即时调整触发阈值。

当前不包含科大讯飞 ASR 和 Windows Service 部署。现阶段仅接入固定的局域网问答接口，完整中控协议仍由后续联调确定。

KWS唤醒通知格式请参阅`docs/KWS-WAKE-NOTIFY-API.md`。
通知使用与ASR相同的固定POST地址、`target`和`source`。

## 目录原则

```text
src/voice_gateway/                  语音核心源码
webui/                              Web 后端、前端、测试和音频控制源码
config/default.json                 干净的默认配置
models/                             本地 KWS、VAD、ASR 模型
tests/                              语音核心单元测试
scripts/                            开发、测试和构建入口
deploy/                             部署包中的双击 CMD 入口源文件
docs/                               使用、开发和阶段文档
artifacts/VoiceGateway-G10C-Test/   当前完整可运行成品
artifacts/releases/                 正式 ZIP 和对应 manifest
build/                              临时构建目录，可随时删除
logs/ results/ runtime/             开发运行数据，可重新生成
```

请遵守以下规则：

- 日常开发只修改 `src/`、`webui/`、`config/`、`scripts/`、`deploy/` 和 `docs/`。
- 不要直接修改 `artifacts/VoiceGateway-G10C-Test`；它由构建脚本重新生成。
- `build/` 是临时目录，任何时候都可以删除。
- 正式交付只认 `artifacts/releases` 中同版本的 ZIP 和 manifest。

## 开发环境

要求 Windows 10/11、Python 3.12 x64 和 .NET Framework 4.x。

首次准备：

```powershell
cd D:\work\voice_gateway_p1
.\scripts\setup-dev.ps1
.\scripts\download-models.ps1
```

运行全部源码测试：

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -B -m unittest discover -s webui\tests -v
```

运行语音核心：

```powershell
.\scripts\doctor.ps1
.\scripts\probe-mic.ps1 -Device 1 -Seconds 8
.\scripts\run-mic.ps1 -Device 1
```

使用源码启动 Web 管理端，同时复用当前成品中的语音 EXE 和 Windows 音频辅助程序：

```powershell
.\scripts\run-web-manager-dev.ps1 -OpenBrowser
```

只调试页面或HTTP接口、不希望自动启动语音和占用麦克风时，加上`-NoAutoStart`。

## 构建

重新生成完整可运行目录：

```powershell
.\scripts\build-test-package.ps1
```

生成完整可运行目录、版本化 ZIP 和 manifest：

```powershell
.\scripts\build-release.ps1
```

构建过程会：

1. 校验项目、语音核心和Web管理端版本号一致。
2. 运行核心单元测试。
3. 在 `build/` 下构建并验证语音核心。
4. 构建 Windows 音频控制组件和 Web 管理 EXE。
5. 运行 Web 单元测试和 EXE 冒烟测试。
6. 全部成功后才替换 `artifacts/VoiceGateway-G10C-Test`。
7. 发布时先生成临时 ZIP 和manifest，完成后再替换正式文件并计算 SHA-256。

构建失败不会主动删除上一版正式 ZIP。

## 部署和使用

目标 Windows 主机不需要安装 Python。

1. 解压 `artifacts/releases` 中最新 ZIP。
2. 首次运行 `06-enable-lan-access.cmd`，允许局域网访问 TCP 8765。
3. 运行 `05-web-manager.cmd`，并保持该控制台窗口开启。Web 管理端会自动启动语音服务。
4. 本机访问 `http://127.0.0.1:8765/`。
5. 局域网其他电脑访问 `http://远程主机IPv4:8765/`。

Web 管理接口没有登录验证，只能部署在可信局域网，不要映射到公网。

## 配置和运行数据

- 源码默认配置：`config/default.json`
- 成品当前配置：`artifacts/VoiceGateway-G10C-Test/config/default.json`
- Web 配置备份：运行目录中的 `webui-runtime/config-backups`
- 人类可读日志：`logs/*.log`
- 结构化事件：`logs/*.jsonl`
- ASR 结果流：`results/*.jsonl`
- 最近识别快照：`results/latest-asr-result.json`

日志按日期或大小轮转，并受保留天数和容量限制；配置备份也会自动清理旧版本，避免长期运行持续占用磁盘。

Web 页面保存配置时会先校验和备份。如果语音服务处于自动运行状态，只重启 `VoiceGateway-G10C-Test.exe` 子进程，不会重启 Web 管理端或 Windows 主机。用户明确点击“停止语音”后，保存配置不会自行恢复监听。

## 自动恢复边界

- `VoiceGateway-WebUI.exe` 启动后会自动启动语音子进程。
- 麦克风短暂断开或驱动异常时，语音子进程会按退避策略重新连接设备。
- 语音子进程异常退出、启动超时、音频心跳超时或识别长时间无响应时，Web 管理端会按有上限的退避策略重新启动它。
- `05-web-manager.cmd` 会在 Web 管理进程异常退出后延迟重启；正常退出或端口绑定失败不会循环重启，恢复过程也不会反复打开浏览器标签页。
- 关闭浏览器页面不会停止语音服务；关闭 `05-web-manager.cmd` 所在的控制台窗口会结束这一套前台运行机制。

这些机制用于处理进程和音频设备的运行时故障，但不会注册 Windows 开机任务，也不能让已断电或已关机的电脑自行开机。异常断电后来电自动恢复仍需要 BIOS“来电开机”和 Windows 任务计划程序，当前发布包没有自动修改这些系统设置。

## 模块说明

- `audio.py`：WAV 和 Windows 实时麦克风输入、电平统计。
- `keyword.py`：中文唤醒词到 sherpa-onnx token 的转换。
- `engines.py`：KWS、VAD、SenseVoice ASR 引擎封装。
- `pipeline.py`：唤醒、录音、识别和冷却状态机。
- `events.py`：日志、JSONL 事件和最近识别快照。
- `webui/server.py`：HTTP API、配置、子进程和实时状态管理。
- `webui/static/`：浏览器页面。
- `webui/audio_helper/Program.cs`：Windows Core Audio 音量和峰值读取。

更多开发说明见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)，Web 操作说明见 [docs/WEB-MANAGER.md](docs/WEB-MANAGER.md)。

## 当前边界

- 固定内部音频格式为 16 kHz、16-bit、单声道。
- 本地 ASR 使用 SenseVoice INT8 ONNX。
- 一次只处理一个语音会话。
- Web 管理端不是 Windows Service；生产试运行时需要保持 `05-web-manager.cmd` 控制台开启。
- Windows 系统输入音量依赖 Core Audio 端点映射，少数特殊驱动可能只支持录音而不能调节音量。
- 完整中控协议尚未接入，当前仅使用固定的局域网 `/api/asr-text` 临时联调接口；版本化 JSONL 识别结果仍作为本机诊断边界。

## 第三方组件

- sherpa-onnx
- Silero VAD
- SenseVoice
- PortAudio / sounddevice

正式商用前应复核 `THIRD_PARTY_NOTICES.md`、模型来源和 `models/models.lock.json`。
