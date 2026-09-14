# 61 原数字人桥接服务

从61实际运行的mofa-avatar-service目录收取server.mjs和index.html，采集日期2026-09-14。该服务监听HTTP/WebSocket 19200，将中控播报、停止、窗口控制转发给原数字人客户端，是原数字人链路的独立服务。

原运行目录：D:\exhibition-hall-new\runtime\mofa-avatar-service

原计划任务：ExhibitionMofaAvatarService

## 配置与启动

先在platform/central执行npm ci。桥接通过EXHIBITION_PLATFORM_PACKAGE指向中控package.json并复用其ws依赖。将mofa-config.example.json复制为受控运行目录中的mofa-config.json，使用厂商提供的真实appId/appSecret；真实配置不得提交。

在PowerShell设置以下环境变量并运行，示例中的绝对路径按实际部署调整：

```powershell
$env:EXHIBITION_PLATFORM_PACKAGE='D:\smart-guide\platform\central\package.json'
$env:MOFA_CONFIG_PATH='D:\runtime\mofa-config.json'
$env:MOFA_ADMIN_PAGE_PATH='D:\smart-guide\platform\central\integrations\avatar-bridge\index.html'
$env:AVATAR_SERVICE_HOST='0.0.0.0'
$env:AVATAR_SERVICE_PORT='19200'
node .\server.mjs
```

监听地址按公司内网要求设置。厂商客户端连接配置需与桥接服务匹配；仅启动服务不代表数字人已连接或打开。沿用原现场任务时先核对脚本目录和运行账号，不另起重复监听进程。

GET /health检查online、ready及session；POST /api/avatar/say提交{text}；POST /api/avatar/interrupt提交空对象。该桥接只有一个当前数字人会话，按屏号路由应通过中控AvatarTargetCoordinator，手机应使用3001/api/speech-interrupt，不直接调用这个无target的接口。

服务对interrupt的成功响应表示指令已写入WebSocket，不是扬声器停声确认。speaking字段也可能在下发时乐观置为false，应结合实体播放与客户端状态判断。

本目录不包含mofa-config.json、service.log、凭据导出工具、诊断缓存或原厂客户端归档。28客户端补丁见apps/display-windows/vendor-avatar-patch。
