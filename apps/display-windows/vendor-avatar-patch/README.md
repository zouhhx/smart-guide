# 原数字人打断与嘴型恢复补丁 21

本目录保存我们对原厂数字人客户端的补丁构建与应用方法，不包含原厂完整程序或其原工程。C#展示客户端的PPT修复在上一层，两个组件须分别升级。

原问题：只调用底层interrupt会设置面部中断标记，而SDK未开启enableClientInterrupt时，下一轮voice_start不会清除该标记，导致有声音但嘴型不动。补丁开启SDK配套客户端中断路径，使用原interactiveidle，并拦截停止后迟到的页面发送回调。

## 构建

准备合法的原厂云播客户端2026Ai_1.1安装包及未打补丁的app.asar备份。在本目录执行：

```powershell
npm ci
node build.cjs "D:\vendor-backup\app.asar" "D:\staging\candidate.js"
```

也可以输入从原app.asar提取的out/renderer/assets/index-CPRpiHM5.js。构建脚本严格核对原文件哈希，厂商改版或已经打补丁的输入会被拒绝。依赖esbuild锁定0.21.5。

原renderer SHA256：1331D61D1C66DE76155CCEB572863008B97090F533FE188D4EDBA97FCC8F2F34

补丁renderer SHA256：04540B0BF96A8498927D75CCD80606FBE23D41299943ECD31298420F705C6054

这两个哈希属于内部JS，不是整个app.asar。2026-09-14已使用本仓库脚本重建，结果与28部署的补丁JS一致。

## 应用与恢复

在28维护时段，用PowerShell运行apply.ps1，显式提供Archive、Candidate、Backup、AvatarExe的绝对路径，StartTask默认ExhibitionAvatarStartOnce。脚本只停止该AvatarExe路径对应进程，备份原归档，核对长度和完整性布局后写入。启动任务必须已配置在实际展示桌面会话；脚本不创建任务、不开放调试端口、不更改账号。

示例路径须替换为实际现场路径：

```powershell
.\apply.ps1 -Archive 'D:\MofaAvatar\云播客户端2026Ai_1.1\win-unpacked\resources\app.asar' -Candidate 'D:\staging\candidate.js' -Backup 'D:\staging\app.asar.before-v21' -AvatarExe 'D:\MofaAvatar\云播客户端2026Ai_1.1\win-unpacked\有灵屏型智能体.exe'
```

应用后测试“原数字人说话并有嘴型 → 手机按下停止 → 新回答嘴型恢复”。当前现场三轮SDK运行时验证通过，但手机网络偶发打断未确认仍需现场复测。Qwen3本地音频没有口型同步，不在此补丁中模拟嘴部动作。

回退时先停止该原数字人进程，恢复匹配的完整app.asar备份，再由原正常任务启动。不要回退C#的PPT修复，也不要删除长期维护账号。
