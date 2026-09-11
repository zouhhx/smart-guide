# 开发与维护说明

## 当前架构

系统由两个 Windows 进程组成：

```text
05-web-manager.cmd
  │ 前台等待；Web异常退出时按2/5/10/30/60秒退避重启
  ▼
VoiceGateway-WebUI.exe
  │ 自动启动、健康监督、停止、读取 stdout
  ▼
VoiceGateway-G10C-Test.exe
  │
  └─ 麦克风 → KWS → VAD → 本地 ASR → JSONL/最新结果

浏览器 ── HTTP :8765 ──> VoiceGateway-WebUI.exe
```

Web 管理端不是另一套语音实现。它负责配置和管理语音核心子进程，语音算法仍位于 `src/voice_gateway`。

浏览器不是运行链路的一部分，关闭页面不影响语音监听。部署时需要保持`05-web-manager.cmd`控制台开启；当前方案没有注册Windows Service或开机任务。

## 修改位置

| 需求 | 主要位置 |
| --- | --- |
| 麦克风采集、重采样、电平统计 | `src/voice_gateway/audio.py` |
| KWS/VAD/ASR 模型初始化 | `src/voice_gateway/engines.py` |
| 唤醒到识别的状态机 | `src/voice_gateway/pipeline.py` |
| 配置结构和校验 | `src/voice_gateway/config.py` |
| 日志和识别结果格式 | `src/voice_gateway/events.py` |
| Web API、配置应用、子进程管理 | `webui/server.py` |
| 网页布局与交互 | `webui/static/` |
| Windows 输入音量和峰值 | `webui/audio_helper/Program.cs` |
| 双击启动和防火墙脚本 | `deploy/` |

## 配置生效规则

- Windows 系统输入音量通过 Core Audio 立即生效，不重启语音进程。
- 录音设备、唤醒词、KWS 和 VAD 参数保存后，Web 管理端会重启语音子进程。
- 保存前先校验并备份配置。
- 新配置启动失败时恢复旧配置并尝试恢复旧服务。
- 不要在一次识别进行中频繁保存配置，该次识别可能被中断。

## 运行时恢复规则

- Web管理端启动后默认自动启动语音子进程；`--no-auto-start`仅用于开发和诊断。
- 用户点击“停止语音”会清除期望运行状态，健康监督不能擅自重启。
- 麦克风短暂断开或PortAudio异常由语音子进程在内部按退避策略重连。
- 子进程异常退出、启动超时、音频心跳超时或ASR长时间无响应，由Web管理端终止当前进程句柄并按有上限的退避策略恢复。
- Web管理进程异常退出由`05-web-manager.cmd`恢复。退出码0视为人工正常停止，退出码3视为端口或启动配置问题，其余异常退出按2、5、10、30、60秒退避，之后保持60秒。
- Web管理端通过专用stdin管道向语音子进程提供父进程存活信号；Web硬退出导致管道关闭时，旧语音子进程会立即退出，避免启动器恢复Web后出现两个麦克风占用者。
- 启动器只在第一次运行时传入`--open-browser`，防止故障恢复时反复打开标签页。
- 不允许在启动器中按进程名批量结束程序；任何终止都必须限定为当前管理器创建并持有的子进程。
- 核心和Web日志按日期或大小轮转并设置容量边界；音频心跳不进入持久日志，避免固定周期磁盘写入。

这套机制覆盖进程和设备的运行时故障，不覆盖Windows关机、系统重启或整机断电。需要无人值守开机时，应在目标主机另行验证BIOS来电开机、专用账户登录和任务计划，不能在文档中把当前前台启动器描述为开机自启。

## 测试

核心测试：

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Web 测试：

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s webui\tests -v
```

设备与模型诊断：

```powershell
.\scripts\doctor.ps1
.\scripts\probe-mic.ps1 -Device 1 -Seconds 8
.\scripts\run-verified-demo.ps1
```

## 开发运行

只调试语音核心时使用 `.venv` 中的 `voice-gateway.exe` 或 `scripts/run-mic.ps1`。

调试 Web 源码时使用：

```powershell
.\scripts\run-web-manager-dev.ps1 -OpenBrowser
```

该脚本加载根目录 Web 源码，但复用当前成品中的语音 EXE、配置和 Windows 音频辅助程序。修改静态页面后刷新浏览器即可；修改 `server.py` 后重新启动脚本。

只调试页面或HTTP接口时使用`-NoAutoStart`，避免开发过程意外占用麦克风：

```powershell
.\scripts\run-web-manager-dev.ps1 -OpenBrowser -NoAutoStart
```

## 安全构建

```powershell
# 完整运行目录
.\scripts\build-test-package.ps1

# 完整运行目录 + ZIP + manifest
.\scripts\build-release.ps1
```

构建始终先写入 `build/` 临时目录。测试、模型诊断和 EXE 冒烟验证全部通过后，才替换 `artifacts/VoiceGateway-G10C-Test`。

不得把源码重新放入 `artifacts`，也不要把带具体声卡编号的当前机器配置当作干净部署配置。

## 发布检查

每次发布至少确认：

1. 核心与 Web 单元测试全部通过。
2. `doctor` 能验证三个模型及摘要。
3. 离线演示能产生 ASR 结果。
4. Web EXE 能启动，首页和 `/api/bootstrap` 正常。
5. ZIP 中不包含日志、识别结果、配置备份和构建缓存。
6. `pyproject.toml`、语音核心和Web管理端三处版本号完全一致。
7. ZIP、内外manifest和程序报告的版本号一致。
8. `05-web-manager.cmd`正常退出不重启、异常退出有退避、恢复时不重复打开浏览器。
9. 记录 ZIP 的 SHA-256。

## 接口扩展边界

中控接口建议消费 `results/asr-results-YYYYMMDD.jsonl` 或监听后续新增的独立输出适配器，不要把网络请求直接塞进音频回调线程。

推荐将中控通信实现为独立模块，并通过有界队列接收 `asr_result`：

```text
Pipeline → EventWriter → ControllerAdapter → 中控主机
```

网络超时、断线和重试不能阻塞 KWS、VAD 或 ASR。

## 当前已知限制

- 没有 Web 登录验证和 HTTPS。
- 没有 Windows Service。
- 没有中控接口。
- 没有云端 ASR。
- 没有自动创建Windows开机任务；异常断电后来电恢复还依赖BIOS和Windows部署设置。
- Core Audio 设备名称与 PortAudio 设备映射在少数特殊驱动上可能不唯一。
- Web 实时状态采用 50 ms 增量轮询，音量峰值采用较低频率独立轮询。
