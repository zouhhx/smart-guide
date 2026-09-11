# 给整合助手的交接手册：Android 小手机源码与成品

## 1. 本次交付是什么

这是一个独立的 Android Gradle 工程，原目录 D:/work/exhibition-hall_AsrApk。
当前主产品包名 com.codexpro.digitalhumanasr，versionName 1.7，versionCode 8。
app 是小手机播控端；face-app 是原工程中的独立人脸接待模块，完整保留，但不属于本次主产品的验收范围。

本次用户授权：建立本地仓库、整理源码和现有成品、编写手册，交给后续助手统一整合多个项目并上传公司 GitLab。本阶段没有配置 remote，没有创建 GitLab 项目，没有推送，没有改动其他项目。

仓库默认分支 main；首个基线标签 asr-apk-v1.7-baseline。本项目过去没有有效 Git 仓库，因此本次只有从当前文件快照开始的真实历史，不能恢复之前 v1.5/v1.6 的逐次修改。
提交人使用本机现有 Git user.name/user.email；后续助手需使用自己的公司身份提交新增变更，不应重写当前基线作者。

## 2. 给你的文件

交付目录：D:/work/delivery/asr-android-v1.7-handoff

| 内容 | 用途 |
| --- | --- |
| 00-给整合助手的交接手册.md | 本手册副本，先读 |
| asr-apk-v1.7-baseline.bundle | 可离线 clone 的 Git 仓库，含 main 提交历史和基线 tag |
| asr-apk-v1.7-source.zip | git archive 导出的纯源码快照，没有 .git，没有历史 |
| artifacts/ 下的 APK | 现有 v1.7 现场测试成品 |
| artifacts/artifacts.json、v1.7-release-notes.md | 成品哈希、签名和已知限制 |
| DELIVERY_INDEX.json | 本次提交 ID、文件清单和文件哈希 |

需要保留历史时使用 bundle，不要仅复制源码 ZIP 后重新 git init。
本机原仓库的 .git 是以后持续开发和查询历史的实际位置；bundle 是导出时的快照。

## 3. 整合前先阅读

1. PROJECT_STATUS.md：现状和下一步。
2. docs/KNOWN_ISSUES.md：用户已报告偶发打断失败，仍未修复。
3. docs/ANDROID_RUNTIME_CONFIGURATION.md：配置与协议边界。
4. docs/api/phone-api-v1.md 和 speech-interrupt-v15.md：当前接口快照。
5. docs/THIRD_PARTY_DEPENDENCIES.md：AAR 和 Wrapper 的完整性信息。

禁止因历史文档而恢复旧 /ready、/v1/screens、/v1/input-events、Token、KB 或 InputEvent。
手机只配置一个完整 ASR 文字地址，默认 http://192.168.16.61:3001/api/asr-text。
当前四个接口为 /api/screens、/api/asr-text、/api/remote-control、/api/speech-interrupt。
不要为上传或整合擅自向现场发送 POST，不要批量执行 Postman 集合。

## 4. 克隆并核验

以下命令在 PowerShell 执行，路径替换为实际的交付目录和一个尚不存在的目标目录：

```powershell
git clone 'D:/work/delivery/asr-android-v1.7-handoff/asr-apk-v1.7-baseline.bundle' 'D:/integration/phone-android'
Set-Location 'D:/integration/phone-android'
git status --short
git log --oneline --decorate -5
git show-ref --tags
git fsck --full
```

预期：main 和 asr-apk-v1.7-baseline 指向交付基线，工作区无改动。
对照 DELIVERY_INDEX.json 的 commit 值核验 git rev-parse HEAD。clone bundle 产生的 origin 是本地 bundle 路径，上传前需要替换。
源码 ZIP 可用于人工审阅或无历史导入；ZIP 不包含 APK、本地凭据、签名密钥和 Gradle 缓存。

## 5. 凭据如何提供

真实讯飞 App ID/API Key/API Secret 已从 strings.xml 移除。构建时注入同名资源，原 SDK 和页面运行方式不变。

- 环境变量名称：SPARK_APP_ID、SPARK_API_KEY、SPARK_API_SECRET。
- 本机备用文件：项目根目录 local.secrets.properties（.gitignore 排除）。
- 模板：local.secrets.properties.example，只含空值。
- 环境变量非空时优先于本机配置文件。
- 缺少任一项时 :app:verifySparkCredentials 和 app 构建会明确失败，避免生成无法识别语音的安装包。
- 仅构建不依赖 ASR 的 face-app 时不需要这些值。

本机已经保留可用的 local.secrets.properties，但未包含在 bundle/ZIP/交付包里。到另一台机器必须向讯飞账号负责人或公司凭据系统获取，不能要求通过聊天明文粘贴。
GitLab CI 中由负责人配置变量，按公司策略设置 masked/protected（尤其 API Key/Secret）；确认运行流水线的分支有权访问。
不要把真实配置文件、调试 keystore、签名密码、.env 或 CI 凭据提交进 Git。

构建注入解决的是 Git 历史不包含明文授权值。Android SDK 运行仍需要凭据，生成的 APK 包含相应资源，可被反编译提取；现有 APK 也如此。因此成品仅放公司访问受控的存储，不作为公开下载。
本次没有轮换原账号授权；是否轮换由账号负责人决定。

## 6. 构建环境和命令

保持项目当前版本：JDK 17、Android SDK Platform 34、Gradle Wrapper 9.0-milestone-1，依赖版本以 build.gradle.kts 为准。
不要直接升级 Gradle、AGP、Kotlin、AndroidX 或供应商 AAR 来“统一多个项目”。
仓库保留 app/libs/Codec.aar、SparkChain.aar、gradle-wrapper.jar；不需要复制开发机的 .gradle-user。

本机已验证环境（其他电脑需替换路径）：

```powershell
$env:JAVA_HOME = 'D:/work/.jdk17/jdk-17.0.20+8'
$env:ANDROID_HOME = 'C:/Users/ZOUHU/AppData/Local/Android/Sdk'
$env:Path = "$env:JAVA_HOME/bin;$env:Path"
# 原机器可复用已有缓存；其他机器可不设置这一项。
$env:GRADLE_USER_HOME = 'D:/work/exhibition-hall_AsrApk/.gradle-user'
.\gradlew.bat :app:testDebugUnitTest :app:lintDebug :app:assembleRelease --no-daemon
```

Linux runner 使用 bash ./gradlew 执行相同任务，事先安装 SDK/JDK 并配置三项环境变量。
本机验证使用 --offline 是因为已有完整缓存；全新 CI 不要使用 --offline，并需能访问 Gradle、Google Maven 和 Maven Central（或公司对应镜像）。
本次没有提供绑定具体 runner 的 .gitlab-ci.yml，因为公司 runner 类型、SDK 镜像、变量权限和签名流程尚未确定。整合助手应根据实际 runner 配置。

app/build/outputs/apk/release/app-release-unsigned.apk 是构建输出，不能直接作为可安装成品。需要受控签名和 apksigner verify。
Debug 可用 :app:assembleDebug 生成，签名是该机器的调试证书，不保证能覆盖现场旧包。
整合时使用正式签名须确认覆盖升级与讯飞授权的包名/签名绑定，不要为方便修改 applicationId。

## 7. 多项目如何整合

默认建议：公司 GitLab 同一个 Group 下，为手机、61 中控、展示端等保留独立仓库，通过统一的交接索引说明版本组合。这样不需要改变现有各工程构建根目录和提交历史。

如果总负责人已决定使用一个 monorepo：

- 将本工程导入独立目录，例如 apps/phone-android，保留其 settings.gradle.kts 和 Wrapper。
- 不要复制原目录的 .git 到总仓库，也不要误把嵌套仓库提交成 gitlink/submodule。
- 如需保留本次历史，可在已准备好的总仓库使用 git subtree（先确认该命令可用），且不使用 --squash：

```powershell
# 在总仓库根目录执行；要求目标前缀不存在，已有工作均已妥善保存。
git remote add phone-android-local 'D:/integration/phone-android'
git fetch phone-android-local main
git subtree add --prefix=apps/phone-android phone-android-local main
```

- 具体前缀和总仓库由整合任务决定，不在本次本地准备阶段预设。
- 导入后按新的子目录位置配置工作目录、本机凭据/CI 注入路径，再重新构建验证。
- 其他仓库的许可证、凭据、构建结果和 Git 历史需单独检查，本手册不代表它们已经核验。

## 8. 上传公司 GitLab

先明确公司 GitLab URL、项目路径和当前助手的授权。优先使用空的私有项目；如果已有提交，先 fetch/检查，不强推覆盖。
不要把 Token 写进 remote URL、脚本、手册或 shell 历史。使用公司配置好的 SSH 或凭据管理器。

独立仓库上传流程：

```powershell
# 在克隆后的 phone-android 仓库里执行，先查看现有 remote。
git remote -v
# 从 bundle 克隆时已有 origin，替换为负责人提供的实际 GitLab 地址：
git remote set-url origin '<公司GitLab仓库SSH或HTTPS地址>'
git ls-remote origin
git push -u origin main
git push origin asr-apk-v1.7-baseline
```

在原本机仓库（没有 origin）操作时用 git remote add origin，而不是 set-url。
仅推送 main 和明确的基线 tag，不使用 --mirror 或 --force；如远程非空或保护分支阻止直接推送，按公司分支/MR流程处理。
上传后核对远端提交 ID 和 tag，试克隆并构建，再向用户报告链接。

## 9. 成品怎样上传

源码进入 Git。APK 不进入 Git 提交，也不应把整个开发目录压缩上传。
将交付包 artifacts/ 中现有 APK 和 artifacts.json 上传公司 GitLab 的私有 Package Registry、CI artifacts，或公司指定的私有文件存储；再从 Release 页面关联相应下载链接，按实际平台配置操作。
不要直接把 Git Release 页面视为 APK 二进制存储，也不要将本机 D 盘路径当成用户可访问的下载链接。

现有 APK SHA-256：
68D604C7B77C5FBD2918FBBA37302C023E89CA6DFEAACC6570D5D97BEB1724ED

现有 APK 在首次 Git 基线之前生成，使用 Android Debug 证书，存在待定位的偶发打断问题。
本次基线的唯一运行构建相关调整是凭据注入方式，因此不能声称这个旧 APK 就是从新 commit 精确重建得到的。
发布说明应标识“v1.7 现场测试包 / 已知偶发打断失败”，保留原 SHA-256；公司正式发布应在 CI 重新构建、受控签名、验收并记录 commit + APK hash + 证书摘要。

## 10. 交付完成检查

- 本地 main 和基线 tag 可从 bundle 恢复，文件与索引一致。
- 凭据只在 Git 忽略的本机文件/CI 变量，不在 Git 对象或源码 ZIP。
- APK 和源码分开交付，构建依赖 AAR 仍在仓库中。
- 18 项现有 JVM 测试、lintDebug 和 Release 构建通过，具体验证记录见 docs/REPOSITORY_BASELINE_VALIDATION.md。
- 已知缺陷没有被误标为修复，未宣称手机端已完成真实打断验收。
- 本手册是后续整合输入，最终多个项目的组合版本、GitLab 项目地址和上传结果需要整合助手补充。
