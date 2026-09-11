# Android 运行配置与接口边界

本文件记录 2026-09-09 起的小手机实际运行配置。接口细节以 `D:\讲解修复\phone-api-docs\小手机APP接口文档.md` 为准。

## 唯一配置项

设置页只填写完整的中控 ASR 文字接口地址：

`http://192.168.16.61:3001/api/asr-text`

该地址已作为默认值。地址变化会清除旧屏幕目录、旧选屏和目标锁定。HTTP 仅允许字面量回环或私有 IP；HTTPS 可使用域名。地址不能包含账号、密码、query 或 fragment，并且路径必须是 `/api/asr-text`。

## 调用链

- `GET /api/screens`：取得包含 `screenNumber`、名称、在线状态和可选状态信息的屏幕列表。
- `POST /api/asr-text`：只发送最终识别文字，字段为 `text/target/source/requestId`。
- `POST /api/remote-control`：字段为 `action/target/source`。
- `POST /api/speech-interrupt`：按下麦克风时发出 `target/requestId`，独立异步通道；从同一个设置地址派生，无需新增配置项。协议见 `D:\讲解修复\phone-api-docs\手机按麦克风打断接口-v15.md`。

接口匿名访问，不配置或发送 Bearer Token、Knowledge Base ID、Tenant/Site、Core 凭据或 InputEvent。

## 行为边界

- 选屏保存 `screenNumber` 的字符串形式，离线屏幕不可选择。
- 左滑固定发送 `gesture_swipe_left`，右滑固定发送 `gesture_swipe_right`，双击固定发送 `gesture_double_tap`。
- 手机不根据本地页码改写手势，不重复执行响应 `actions`。
- `ignored:true` 表示当前模式无动作；`duplicate:true` 表示翻页过快被过滤；二者都不重试。
- 网络失败或超时表示执行结果未知，控制请求不自动重试。
- ASR 临时文字只预览，最终非空结果只发送一次；上滑取消不提交问题，但按下时的打断仍有效。
- 打断与新问题使用按下时固定的屏号和配置；不等待打断响应才开始 ASR，也不等待打断成功才提交问题。
- 打断只发送一次，不自动重试，不在松手提交新问题后补发旧请求。总超时 3 秒；新问题提交前取消旧等待。
- 202/DISPATCHED 仅说明中控下发命令，没有扬声器 ACK。失败轻提示并继续原语音流程；2 号屏支持本地 Qwen3 打断，4 号屏按 v15 文档仍需升级。

## 已弃用

`/ready`、`/v1/screens`、`/v1/input-events`、Gateway Bearer、Knowledge Base ID 和冻结 InputEvent 方案已整体弃用，禁止与当前接口双发。
