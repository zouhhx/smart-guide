# 展厅展示端 - C# WinForms 版本

当前展示端使用 **C# WinForms 客户端**，用于替代原来的 Electron 展示端。

## 当前定位

这是展厅项目中的 **展示屏执行端**，负责：

- 连接中控平台 WebSocket
- 注册为展示设备
- 接收控制命令
- 播放视频 / 图片 / PPT
- 显示讲解、问答、待机界面
- 执行静音、暂停、翻页、返回主页等动作

## 项目结构

```text
exhibition-hall-cs/
├── Program.cs                    # 程序入口
├── App.config                    # 配置文件
├── ExhibitionClient.csproj       # 项目文件
├── Models/
│   └── Command.cs                # 命令模型 / 设备模型
├── Services/
│   ├── WebSocketService.cs       # WebSocket 通信
│   ├── FileSyncService.cs        # 文件同步
│   ├── CommentaryService.cs      # 讲解词 / TTS
│   ├── RuntimeSettings.cs        # 运行配置
│   └── RuntimeSettingsStore.cs   # 运行配置状态
├── Controllers/
│   ├── PPTController.cs          # PPT 控制
│   ├── VideoController.cs        # 视频播放
│   └── ImageController.cs        # 图片展示
├── Views/
│   ├── MainForm.cs               # 主窗口 / 主调度
│   ├── SettingsForm.cs           # 设置界面
│   └── LogForm.cs                # 日志界面
└── README.md
```

## 依赖

- .NET 8
- Microsoft Office PowerPoint（用于 PPT 控制）
- LibVLC / VLC 运行时（用于视频播放）

## 配置

编辑 `App.config`：

```xml
<appSettings>
    <add key="WebSocketUrl" value="ws://192.168.23.83:3000" />
    <add key="FileServerUrl" value="http://192.168.23.83:3001" />
    <add key="MediaPath" value="C:\media" />
</appSettings>
```

## 运行方式

### 本地开发
```bash
cd exhibition-hall-cs
dotnet restore
dotnet build
```

### 发布
```bash
dotnet publish -c Release -r win-x64 --self-contained true -o ./publish
```

## 支持的命令

- `play_video`
- `play_ppt`
- `show_doc`
- `next_slide`
- `prev_slide`
- `goto_slide`
- `close_ppt`
- `pause`
- `resume`
- `mute`
- `speak`
- `show_qa`
- `home`

## 关键入口

### `Services/WebSocketService.cs`
负责与平台通信：
- 连接服务端
- 注册设备
- 接收命令
- 自动重连

### `Views/MainForm.cs`
负责命令分发与界面编排：
- 待机画面
- 分屏布局
- 媒体展示
- QA 展示
- 播报展示

### `Controllers/`
负责不同类型资源控制：
- 视频
- 图片
- PPT

## 说明

- 原 Electron 展示端已移除
- 当前维护重点应放在 C# 展示端与 platform 的指令链路上
- 构建产物 `bin/`、`obj/`、`publish/` 已在交接前清理
