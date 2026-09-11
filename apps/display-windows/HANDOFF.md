# 28 展示客户端 · 当前整合说明

本说明优先于原 `README.md` 和 `REPAIR_NOTES.md` 中的旧版本状态。那些文件保留作历史来源，不代表今天现场仍为 07。

运行设备是 `192.168.16.28`，业务对应 **2号屏**。源码来自 `D:\讲解修复\exhibition-hall-cs`，本次按白名单复制 37 个源文件、项目文件、配置和界面资源，排除了 bin、obj、publish-*、成品 ZIP、日志和 download.json。

## 职责与入口

- `Views/MainForm.cs`：城市主页、文件列表、字幕、窗口和播报状态。
- `Controllers/PPTController.cs`、`StaOperationQueue.cs`：PowerPoint 控制与串行 STA 操作。
- `Services/WebSocketService.cs`：接收 61 指令、注册屏号。
- `Services/PcmSpeechPlayer.cs`：本地 PCM 播放；打断同时涉及 MainForm 队列状态。
- `Services/RuntimeSettings.cs`、`RuntimeSettingsStore.cs`：运行配置。
- `ExhibitionClient.csproj`：真实构建目标 `net8.0-windows` 和 NuGet 依赖。

它不包含 Unity `ExhibitionShow.exe`、厂商数字人客户端、数字人代理或 PowerPoint 本身的源码。

## 构建

在有 .NET8 SDK 的 Windows 运行：

```powershell
dotnet restore ExhibitionClient.csproj
dotnet build ExhibitionClient.csproj -c Release
```

原机可用 `D:\讲解修复\.tools\dotnet\dotnet.exe`，不能把只装了 runtime 的 dotnet 当作 SDK。原 `App.config` 带历史 .NETFramework 文本，构建以 csproj 为准，整合时不擅自迁移运行配置。

## 版本与验收

本地已有 15 版打断修复的构建、部署和专项验证记录；整合快照未重新编译或与 28 当前运行程序匹配。最终上传前需收取当天新增修改、测试工程和发布说明，并记录实际源码/产物哈希。

保留此前单次翻页、PPT 故障返回、旧播报取消等修复；大 PPT 性能仍有边界。不得用整理源码顺手覆盖现场、重启数字人或运行操作大屏的 UI 测试。

本次保持原业务文件字节不变，仅新增此交接说明。完整构建/专项测试资料仍在原 `D:\讲解修复` 工作区，最终冻结时需同步纳入本工程的 tests/docs，而不是把旧历史测试当作最终版本验收。
