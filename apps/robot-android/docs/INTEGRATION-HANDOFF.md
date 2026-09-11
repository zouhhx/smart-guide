# 豹小秘机器人项目：源码整合与 GitLab 上传手册

整理日期：2026-09-10。适用对象：负责多个项目最终整合、建仓、上传的助手或工程师。

## 1. 交接目标与边界

这份交付包含当前 Android 机器人源码、V5 流程中枢源码、最新可安装 APK、必要 SDK、测试和交接资料。已把未提交的近期开发内容纳入源码副本。没有上传 GitLab，没有改原仓库分支、提交或远程地址，没有部署现场服务。

整合时以 `source/` 为本项目的源码输入。不要直接把整个 `D:\robot` 上传，它包含虚拟环境、旧包、历史备份和本地敏感配置。不要将旧快照覆盖到本次源码上。

**建议：源码进 Git；APK 放 GitLab 对应版本的发布附件或公司制品库。** 不要让每次编译的 APK、构建目录和运行数据进入 Git 历史。最终由总整合助手根据公司既有项目布局决定合并为总仓库或分仓；本交付保留独立目录，方便两种方案。

## 2. 包内目录

以下路径均相对本交付包根目录；若只接收到源码压缩包，则直接从其根目录进入 `android-robot`、`v5-hub`、`docs`。

| 路径 | 用途 | 上传位置 |
| --- | --- | --- |
| `source/android-robot/` | Android Java 工程、Gradle Wrapper、资源、测试、RobotOS 与 SparkChain SDK | Git 源码 |
| `source/v5-hub/` | Python/FastAPI 中枢、管理页面、TCP、教学与问答逻辑、测试 | Git 源码 |
| `source/docs/` | 当前状态、历史说明和验证记录 | Git 文档 |
| `release-assets/robot-localtts-sparkasr-debug.apk` | 最新历史现场安装基线，调试签名 | 版本发布附件/公司制品库 |
| `audit/` | 来源清单、脱敏清单、构建与测试结果、历史审计 | 内部交接附件；按需保留报告 |
| `SHA256SUMS.txt` | 包内文件校验清单，不包含自身和外层压缩包 | 随交付保存 |

`source/` 刻意不含 `.git`，方便总助手整合，避免出现嵌套仓库或误把子目录提交为 gitlink。原有 Git 历史另存，见第 7 节。

## 3. 当前架构和哪些项目尚需补齐

```text
机器人 Android（RobotOS 唤醒；讯飞 SparkChain ASR）
             ↓ 现有 TCP/教学协议
V5 中枢（8000 HTTP / 19888 TCP）
             ↓ 问答与待审核教学草稿
61 业务平台（3001；后台 5180）
             ↓ 回答文本经 V5 回机器人
机器人 → 61 TTS（8769，流式 PCM）→ 播放/字幕/说话动画
```

- TTS：当前七种音色全部来自 `http://192.168.16.61:8769`，湾湾小何 `wanwan_xiaohe` 第一且默认；其余为 `ma_junfei`、`tw_sweet`、`child_cute`、`huayan`、`baker`、`cn_66`。机器人右上角长按两秒进入管理页切换。
- ASR：普通问答、实时字幕和教学统一 SparkChain。RobotOS 保留硬件唤醒。不要根据旧文档切回 MiMo ASR/TTS。
- 意图分类：V5 仍用 MiMo LLM；这是意图判断，不是 ASR/TTS，也不负责改写工作人员讲述内容。
- 现场教学只提交待审核草稿，审核发布在 61 平台完成，不把机器人识别文字直接改接数字人 2 号屏。
- 正式 UI 的说话动画已恢复，跟随音频播放状态，是循环视频，不是逐音素口型。

**本包没有以下服务的源码或部署资料，不能声称整个展厅系统已齐套：**

1. 61 主机的问答、FAQ、草稿审核和后台平台源码，以及它的数据库迁移、依赖锁文件和部署配置。
2. 61:8769 TTS 服务源码、模型权重、音色参考资产和服务启动配置。机器人内的短句缓存不等于完整克隆模型或参考素材。
3. 其他同事维护的数字人、屏幕、中控、宇树网关等项目。

总整合助手应从这些项目各自的交付中补齐，并保留它们原有 Git 历史/许可证/部署说明；不要从 IP 地址推断它们的源码目录，也不要拿同事示例 APK 替换本项目协议。

## 4. Android 构建与运行

- 包名：`com.zongheng.exhibitionhallrobot`；主入口 `.MainActivity`。
- 当前工程：Java 8 源码；构建 JDK 21、Gradle 8.13、AGP 8.13.2、SDK 36（minor 1）、Build Tools 36.0.0。以工程实际配置为准。
- `app/libs` 的 `robotservice_11.6.1.jar`、`SparkChain.aar`、`Codec.aar` 为实际编译依赖，已经保留，不要当作构建垃圾删除。内部仓库保留，后续按公司 SDK 制品策略管理。
- 新电脑在 `local.properties` 配置自己的 `sdk.dir`，或使用 `ANDROID_HOME`。参考 `local.properties.example`；真实 `local.properties` 不提交。

Windows PowerShell，在 `source/android-robot` 执行：

```powershell
$env:JAVA_HOME = '本机JDK21目录'
$env:ANDROID_HOME = '本机AndroidSDK目录'
.\gradlew.bat testDebugUnitTest assembleDebug
```

依赖已缓存时可加 `--offline`。首次构建需能访问工程配置的依赖仓库。输出 `app/build/outputs/apk/debug/app-debug.apk`。本包只提供调试签名版本，不包含正式签名私钥，不能将它称为正式签名发布版。

ASR 凭据由应用私有文件 `no_backup/iflytek-asr.json` 提供，JSON 字段为 `appId`、`apiKey`、`apiSecret`。模板见 `iflytek-asr.example.json`。三项凭据不在源码/APK中；由有权限的人员通过安全渠道配置到设备，文件权限 600。这里的 `no_backup` 是应用私有目录，不是 SD 卡目录。现有机器人已配置，不要覆盖清空。

升级现有机器人使用覆盖安装，例如 `adb -s 当前设备地址 install --no-streaming -r APK路径`。不要卸载主应用或清除数据；调试签名不同会导致升级失败，应取得原签名配置，而不是通过卸载来解决。ADB 地址是现场变量，之前为 `192.168.23.228:5555`，不代表始终可达。

## 5. V5 构建、配置和部署注意事项

- 本次源目录来自 `D:\robot\v5-legacy-staging-20260901`；已排除嵌套 `app/app` 旧备份、缓存、损坏/备份页面，保留活动 `app/`。
- Python/FastAPI；现场历史版本 Python 3.11.4，本次本地测试 Python 3.12。
- `requirements.txt` 从 2026-08-18 完整交接包恢复，原本是最低版本范围。`requirements-local-tested.txt` 记录这次本地主要依赖版本，不是完整生产锁文件。最终整合应在目标 Linux 环境解析并锁定所有依赖，不能声称已有生产级可重复构建。

新建隔离环境，在 `source/v5-hub` 执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-local-tested.txt
.\.venv\Scripts\python.exe -m pytest -q
```

运行入口为 `python -m app.main config.yaml`。**在启动服务之前**先从 `config.example.yaml` 生成本机忽略的 `config.yaml`，核对业务地址、机器人标识、数据库路径和开关。样例源自旧本地配置，含关闭的教学/契约开关，不是生产配置，也不是即复制即用的现场配置；不要直接覆盖 `/opt/exhibition-hub/config.yaml`。

交付副本移除了 `app/database.py`、`app/engine/llm.py`、`app/engine/unitree_client.py` 中历史凭据默认值，以及样例配置中的凭据。相关第三方功能要由部署人员在受限配置或现有管理配置中补齐，空默认值不会自动读取任意环境变量。现有数据库配置仍由原代码读取；本包不含生产数据库。

生产现场历史位置：`192.168.18.227:/opt/exhibition-hub`，systemd `exhibition-hub.service`，数据库 `data/exhibition_hub.db`。本次没有连接现场确认实时状态。真实配置、数据库、服务环境、签名和密钥需独立安全备份，不进普通 Git 仓库。

V5 独立意图密钥文件 `/etc/exhibition-hub/mimo-intent.key`；教学 Token 通过原 systemd 环境配置传入，例如 `V5_LEGACY_QA_DRAFT_TOKEN`。不要照抄旧部署脚本，也不要把演示 Token 写入实际配置。迁移前备份数据库和配置，验证 HTTP 健康、TCP 重连、问答、取消、教学草稿与审核完整链路，保留回滚路径。

## 6. 成品如何归档

最新已安装 APK：`robot-localtts-sparkasr-debug.apk`。

SHA256：`fce24d01e47eeaed777f5f03147ff334c4440c45b60306c891b3ccafc0b8398a`。

这是 2026-09-09 历史部署基线，保留原文件，没有拿本次重编译输出冒充已部署成品。当前 `versionName=1.0`、`versionCode=1` 不足以区分所有历史包，必须按 SHA256 区分。

建议最终整合提交后创建公司约定的标签，并在对应发布条目附上 APK、校验值和验证记录。后续的新构建记录其实际 commit、签名方式、构建环境和哈希。本 APK 对应“交付来源基线”，V5 副本有凭据清理，不要把 APK 标成整个整合仓库全部内容的构建结果。

V5 为 Python 源码服务，目前没有经过验证的独立二进制、容器镜像或一键部署包。不捏造这些成品；发布时可从最终 Git 标签生成源码归档。

## 7. Git 历史怎么处理

- 原 Android 仓库仍在 `D:\robot\exhibitionhallrobot\.git`，原工作区未提交改动未动。
- 已保存完整可达历史备份：`D:\robot\backups\git-history-20260910\android-original-history.bundle`，执行 `git bundle verify` 已通过；当前 `master` 8 次提交。
- V5 当前工作目录不是 Git 仓库，无可追溯 Git 提交；不要把文件时间当提交时间，也不要声称保留了 V5 Git 历史。
- 原 Android 历史包含助手记忆/工具配置等与产品无关文件。已知凭据比对没有命中可达历史对象，但该结果不等于全量保密审查通过，因此原 bundle 留在本地备份，不混入上传包。

**默认整合方式：** 将本包的干净源码快照加入公司总仓库，产生一次真实的“导入机器人与 V5 当前源码”提交，原历史以本地 bundle 留档。这样不伪造旧历史，也不上传助手私有上下文。用户无需先手工整理提交。

**如公司要求导入旧 Android 提交：** 在新的临时目录用 `git clone 原bundle路径 临时目录` 恢复，先审查所有分支/标签历史中的凭据、个人资料与大文件；只在副本中清理历史，再按总仓库约定导入子目录历史，最后覆盖本次 `source/android-robot` 并提交近期开发。不要删除或改写原仓库，不要用 `push --mirror` 或强推覆盖公司已有分支。原始 bundle 不包含未提交改动；这些改动在本交付源码里。

## 8. 给总整合助手的执行清单

1. 阅读本手册、`source/docs/CURRENT_STATUS.md`、`audit/VERIFICATION.md`，核验 `SHA256SUMS.txt`；历史文档只作背景，不能恢复已退役 MiMo 语音链路。
2. 收集其他项目交付和公司目标 GitLab 地址、命名/分支约定。先检查目标仓库是否已有代码，保留其结构与历史；没有地址前不猜测上传位置。
3. 将 `source/android-robot`、`source/v5-hub` 以及相关文档映射到总仓库明确的项目目录。例如 `robot/android`、`robot/v5-hub`，避免覆盖已有同名项目。
4. 把本包 `.gitignore` 规则合并到总仓库现有规则，不覆盖其他项目规则。不要使用全盘 `git add D:\robot`，不要加入 `release-assets`、原 bundle、真实配置或其他备份。
5. 审查 `git diff --cached --stat` 和待提交文件，检查没有嵌套 `.git`/意外 gitlink，没有密钥、APK、数据库、日志、虚拟环境和缓存。SDK/必要图片视频音频已经在源码中，若公司有单文件限额/LFS策略按其执行，并同步 CI 拉取方式。
6. 在整合目录重新构建 Android、运行 V5 测试。本次 Android 构建和 34 项单元测试通过，V5 全量 190 项测试通过；整合后的验证仍需重新执行。建立 Linux 依赖锁和公司 CI 是后续整合工作。
7. 用真实身份产生可审查提交，按公司流程推开发分支或提交合并请求，不覆盖现有默认分支。凭据通过系统凭据管理/SSH提供，不写入命令文件或 URL。
8. 合并和标签确定后上传 APK 为发布附件，附哈希和验证说明；源码与成品建立对应关系。补齐其他项目的制品与服务依赖清单。
9. 交付最终仓库 URL、分支、commit、标签/成品链接、项目目录表、测试结果，以及仍需现场配置/联调的事项。只有远端核验成功才称“上传完成”。

## 9. 本次整理没有改变什么

原 Android/V5 开发目录、原 Git 历史、机器人安装、V5 服务和其他同事项目保持原样。本次只生成交付副本、历史备份、验证记录和说明。历史记录里的部署成功是以前的证据，不代表今天重新部署或现场复测。
