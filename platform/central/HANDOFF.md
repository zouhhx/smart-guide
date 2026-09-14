# 61 主平台 · 2026-09-11 接收记录

2026-09-14已更新至现场HEAD c8d6e9e及未提交业务修改，包含修复22；并补入integrations/avatar-bridge。当前收取和测试说明见../../docs/RELEASE-20260914.md。以下9月11日收取记录保留供追溯。

已从 61 实际工作区收取，替换此前的待接收占位说明。原开发目录、Git refs、配置和现场服务未修改；只在 61 临时目录生成导出归档，下载后在本地恢复。

## 当前基线

- 工作区来源：`61:D:\exhibition-hall-new\runtime\exhibition-hall-platform`。
- 分支：`main`；HEAD：`70baf89763523547adf7401e30284b7da843a1b1`。
- 收取时间：2026-09-11 15:09 左右；导出前后 HEAD、Git 状态及文件哈希稳定。
- 175 个受 Git 管理的当前文件，加 9 个尚未跟踪的业务文件，共 184 文件，已逐项核验。
- main 可达历史已保存为 bundle，并在本目录恢复为 Git 工作区，`git fsck --full` 通过；其他分支只保留 refs 索引，不宣称全部分支历史都已导出。
- 当前代码是 **HEAD 加现场未提交修改**，不是仅 HEAD 的检出结果。未暂存任何内容，也未创建整合提交。

## 今天同事提交的内容

| 提交 | 时间 | 范围 |
| --- | --- | --- |
| `95dd6be` | 09:47 | FAQ 审核、客户端设置界面 |
| `b9e63c8` | 14:25 | 机器人教学草稿并入待审核 |
| `70baf89` | 14:46 | 机器人审核列表对齐、展开/收起 |

相较昨天 `8e4db3b`，提交涉及 12 文件、953 行新增、178 行删除，包含平台 JS/CSS、构建脚本、DeviceManager、FileServer、KnowledgeDb、QaStore 和对应测试。数量是 Git 差异统计，不代表功能验收结果。

## 保留的未提交修改

包括 `admin-ui/index.html`、Vue `admin/src/App.vue`、FileServer、MessageRouter 和 TTS 配置/服务文件。新增的 `SpeechInterruption.js`、`LocalTts.js`、音色16脚本、布局14 CSS、`scripts/voice-ui` 均已纳入。原机 `restart-admin-static.ps1` 另有仅格式相关状态，文件字节已同样收取；不能只凭本地 status 条数判断少了文件。

真实 `.env`、环境备份、数据、日志、node_modules、历史 stage/deployments 没有混入源工程。main 历史目前仅作本地保存，仍需在最终推送前审查，不把“已克隆”当成历史保密审查通过。

## 正式网页与构建边界

现场入口目前使用 `index-local-tts-v16.js?v=robot-review-toggle-19`，保留音色设置，并合入今天的审核列表修改。入口、16脚本、v22基础脚本、业务 CSS、布局 CSS 五项都与源码对应文件哈希一致。

第六项基础样式 `assets/index-Cfd4b_tz.css` 只存在于现场 `admin-ui/dist`，源码目录没有。已经从现场复制到本地 `admin-ui/assets`，作为单独的恢复依赖记录；没有回写 61。SHA256：`BA0BCFCA87DABB6EDE62933C227B668D81368D3382DC7DC25BD1B48E3CC2130A`。

正式界面仍采用对既有 bundle 的构建/合并脚本，不能称已经找回完整原始 React 工程。`npm run build:admin` 生成基础 v22 JS/CSS，音色组件另由 `scripts/voice-ui` 合并成16脚本。后续改基础 UI 必须核对合并产物，不能只更新基础文件或把入口退回没有音色的版本。

## 本轮验证

- 184 文件、6 个运行资产哈希核验通过；main bundle 恢复及 Git fsck 通过。
- 12 个关键 JS/构建脚本语法检查通过。
- 本地运行 `test/faq-association.test.js`、`test/admin-upload-pool-ui.test.js`：24 项，23 通过，1 失败。
- 失败项是 `admin entry selects the upload-pool UI assets`：测试仍要求 `index-real-analysis-v22.js?v=robot-review-toggle-3`，实际入口为保留音色的16脚本。已记录这一断言与正式入口不一致；此次仅接收检查，未擅改测试或业务实现。
- 未安装全量依赖、未运行后端全量测试/现场控制、未做页面点击或跨端验收。

## 工程入口

主服务 `src/index.js`，路由 `src/router/MessageRouter.js`，HTTP `src/files/FileServer.js`；正式管理页 `admin-ui`，Vue 开发者页 `admin`，测试 `test`，脚本 `scripts`。完整构建/配置字段参照原 README、package.json 和模板；环境值仍需由部署方安全提供。

本次接收范围是主平台，不包含 61:8769 模型服务原工程、独立配套服务、厂商数字人或 Unity 原项目。这些在总导航中继续标为待收集/待匹配。

外层证据：`received/61-20260911/verification.json`、`local-checks.json`、`local-tests.tap` 及 `export/evidence/`。未上传 GitLab。
