# 本次交付副本验证 · 2026-09-10

- 在本包 `source/android-robot` 实际运行 `gradlew.bat testDebugUnitTest assembleDebug --offline`：成功，41 个 Gradle 任务执行；JUnit XML 汇总 34 项，0 失败、0 错误。使用 JDK21、已有 Android SDK/依赖缓存，没有使用原工程 `local.properties`。
- 在本包 `source/v5-hub` 实际运行 `python -m pytest -q`：**190 passed in 10.64s**。使用本机 `.venv-teaching` Python3.12 环境，不代表已经验证全新 Linux 安装。
- V5 活动 Python 文件 AST 语法检查通过。
- 交付 APK 与此前安装基线 SHA256 一致。重新编译仅为源码可构建验证，不替换已经归档的现场 APK。
- 原 Android Git bundle 经 `git bundle verify` 验证可恢复，8 次提交保存在外层之外的本地备份。
- Android 源码与资源/SDK保留原字节；V5/样例配置中 4 个文件清除了历史默认凭据，明细见 `sanitized-files.json`。原工作目录未修改。
- 已知凭据比对和文本候选扫描用于本地交付审查，不能保证发现所有未知秘密；原历史另有助手记忆/配置，因此不自动上传原 bundle。最终整合应执行公司的仓库安全扫描。
- 未运行设备测试、未连接 V5 部署、未上传 GitLab、未验收最终多项目链路。本次没有生成生产依赖锁、正式签名 APK 或容器镜像。

原始构建日志 `android-build.log`、全量测试摘要 `v5-tests.log` 随交付保存。文件级来源见 `origin-manifest.json`；新增说明、示例和依赖版本记录属于本次整理生成文件，以 `SHA256SUMS.txt` 校验。
