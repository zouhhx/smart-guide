# Android V2.7 本地 Fake / 契约自动测试报告（历史归档）

> 本报告记录 2026-08-18 的旧 `/v1/*` 方案，已于 2026-09-09 失效，不得作为当前实现依据。

- 日期：2026-08-18
- Android 版本：1.3（versionCode 4）
- 契约基线：`integration/r1@a36c47d`（已确认与 `549b5dbf` 的 contracts/input-sdk 兼容）
- 范围：`app` 模块；未修改公司 GitLab 仓库

## 测试计划

1. InputEvent 构造：VOICE/TEXT QUERY、PREVIOUS/NEXT COMMAND、sessionId 省略与身份关联。
2. 屏幕目录：正式字段、在线/离线、目录时效、动作权限、演示文稿页码和未知字段拒绝。
3. Fake HTTP：无 Core Bearer、FinalResult 身份与语义、NAVIGATE_PRESENTATION 目标页和版本关联。
4. 传输策略：相同事件有界重试、确定性错误不重试、401/503 分类。
5. 构建与静态检查：Debug Lint、Release lintVital、Debug/Release APK。
6. 产物审计：正式接口存在，旧接口和 `core_bearer_token` 不进入 Release，Release 禁止明文流量。

## 自动测试结果

- 单元测试：21/21 通过，0 failure，0 error，0 skipped。
- `InputEventFactoryTest`：7/7。
- `TargetScreenCatalogParserTest`：7/7。
- `ControlPlaneInputClientTest`：4/4。
- `RetryingInputSenderTest`：2/2。
- `LegacyProtocolGuardTest`：1/1。
- Debug Lint：成功，0 Error、127 Warning。
- Debug APK：构建成功。
- Release APK：构建成功，lintVital 成功，未签名。

## Release 审计

- 包名：`com.codexpro.digitalhumanasr`
- 版本：`1.3` / `4`
- 存在：`/v1/screens`、`/v1/input-events`、`COMMAND_COMPLETED`、`NAVIGATE_PRESENTATION`、`navigate_relative`。
- 不存在：`/api/screens`、`/api/asr-text`、`/api/remote-control`、`core_bearer_token`。
- Release Manifest：`usesCleartextTraffic=false`。

## Lint 分析

127 条均为 Warning，不阻断构建：82 条旧未使用资源、19 条运维配置标题硬编码、10 条依赖版本提示，另外包含 3 条触控可访问性、3 条可能过度绘制、2 条波形绘制分配、2 条 10sp 小字号、2 条兼容 drawable、1 条 Debug 明文配置、1 条列表全量刷新、1 条旧 SDK 判断和 1 条复杂矢量路径。Debug 明文配置是本地联调用途，Release 已确认关闭；触控可访问性、小字号和波形绘制性能建议在真机 UI 回归阶段处理。

## 尚未覆盖

- 无连接设备，未验证麦克风权限、真实 Spark ASR、AudioRecord、震动、声音和真机手势阈值。
- 无手机可访问的 Input Gateway/代理，未验证真实 `/v1/screens` 和 `/v1/input-events` 网络链路。
- 无正式组合环境，未验证 Android -> Gateway -> Core -> Fake/真实 Output -> Terminal。
- 当前 MockWebServer 证明客户端契约收发正确，不能替代真实 Core、Screen State Provider 或 ExecutionReceipt 闭环。

## 结论

本地客户端契约和 Fake HTTP 测试未发现阻断缺陷，可以等待官方组合启动脚本和代理入口。当前不能标记真机或端到端联调通过。
