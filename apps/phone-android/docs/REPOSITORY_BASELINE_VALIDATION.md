# 首次 Git 基线整理验证

日期：2026-09-10；基线标签：asr-apk-v1.7-baseline；主分支：main。

## 实际验证

| 检查 | 结果 |
| --- | --- |
| 原项目凭据改为本地文件注入后运行 app 单测、Lint、Release | BUILD SUCCESSFUL |
| 从 Git index 导出纯源码（没有 .git、local.secrets.properties、原 build 输出） | 成功 |
| 导出源码没有三项凭据时执行 :app:verifySparkCredentials | 按预期失败，明确列出缺少的变量名称，未显示值 |
| 导出源码仅通过环境变量注入，再执行 app 单测、Lint、Release + face-app Debug | BUILD SUCCESSFUL，130 个 task 实际执行 |
| 导出源码 app JVM 测试 | 18 tests，0 failures，0 errors，0 skipped |
| 导出源码 app lintDebug | 0 errors，102 warnings（历史资源/依赖等警告仍保留；以独立源码目录的全新报告为准） |
| 现有 v1.7 交付 APK 哈希 | 68D604C7B77C5FBD2918FBBA37302C023E89CA6DFEAACC6570D5D97BEB1724ED，与整理前一致 |
| 已暂存文件包含现有三项 ASR 凭据值检查 | 无匹配；扫描未打印凭据内容 |
| 已暂存文件范围检查 | 无本机配置、签名密钥、APK、build、Gradle 缓存或 .prd-review |
| 必要输入 | Codec.aar、SparkChain.aar、Gradle Wrapper、app/face-app 源码和布局均保留 |
| gradlew Git 文件模式 | 100755，Linux 可执行 |
| git diff --cached --check | 通过 |

测试环境：Windows、JDK 17.0.20、Android SDK Platform 34、项目原 Gradle Wrapper。离线复用本机依赖缓存，不代表全新机器无需下载依赖或 SDK。
验证目录使用独立临时路径，凭据只注入该进程环境，没有写入导出源码。

本次没有测试公司 GitLab 网络、权限、CI runner、正式签名或多项目总集成。
没有重新签署或覆盖现有现场 APK。新基线注入凭据的构建方式已验证，但已交付 APK 的生成时间早于本次 Git 提交；详见 releases/artifacts.json。
用户报告的偶发打断失败仍未修复，见 docs/KNOWN_ISSUES.md。

提交 ID、bundle/ZIP/成品哈希和最终仓库导出验证记录在交付目录的 DELIVERY_INDEX.json；避免在提交内自引用其自身 hash。
