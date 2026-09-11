# 展厅智能讲解 Android 播控端

当前维护范围是 `app` 模块；`face-app` 为独立模块。

新开发者请从以下文档开始：

- [给整合助手的 GitLab 上传手册](docs/GITLAB_INTEGRATION_HANDOFF.md)
- [当前已知问题](docs/KNOWN_ISSUES.md)
- [开发者项目交接指南](docs/DEVELOPER_PROJECT_HANDOFF_GUIDE.md)
- [Android 运行配置](docs/ANDROID_RUNTIME_CONFIGURATION.md)

当前状态：v1.7 已接入按下麦克风打断目标屏播报，沿用 61 中控现有 HTTP 接口，等待真机与真实大屏端到端验收。

本地 Git 已从当前快照建立，分支 main，基线标签 asr-apk-v1.7-baseline；此前没有可恢复的提交历史。用户已报告偶发打断失败，尚待定位。

构建前请从 local.secrets.properties.example 创建被 Git 忽略的 local.secrets.properties，填入负责人提供的讯飞授权；CI 可改用 SPARK_APP_ID / SPARK_API_KEY / SPARK_API_SECRET 环境变量。实际凭据和 APK 均不进入 Git。

`/ready`、`/v1/screens`、`/v1/input-events` 及 Gateway Token/KB 方案已废弃，不得恢复或与当前 `/api/*` 双发。
