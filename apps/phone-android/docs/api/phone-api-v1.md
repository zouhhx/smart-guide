# 展厅小手机 App 接口文档

版本：1.0 · 核对日期：2026-09-09 · 适用：接入现有展厅中控的新 Android 手机端。

本文依据 61 当前部署目录的代码，以及在线屏幕查询结果编写。描述的是现有接口，不需要修改 61 或升级展示客户端。附带 Postman Collection，可导入联调。所有响应示例均为说明性示例，`seq`、时间、设备和文件信息以实际返回为准。

## 1. 接入速览

**HTTP 基地址：`http://192.168.16.61:3001`**

`5180` 是管理后台页面端口，不是手机控制 API 端口。手机向 61 发请求，61 再向对应展示电脑下发控制命令。

| 手机功能 | 方法与路径 | 关键参数 |
| --- | --- | --- |
| 获取可选屏幕 | `GET /api/screens` | 无 |
| 选择屏幕 | 手机本地保存选择；没有独立的“选择目标屏幕”POST 接口 | 保存 `screenNumber`，后续每次请求携带 `target` |
| 左滑 | `POST /api/remote-control` | `action: "gesture_swipe_left"` |
| 右滑 | `POST /api/remote-control` | `action: "gesture_swipe_right"` |
| 双击 | `POST /api/remote-control` | `action: "gesture_double_tap"` |
| 按住说话后提交识别结果 | `POST /api/asr-text` | `text`、`target`、`source` |
| 上滑返回主页（补充） | `POST /api/remote-control` | `action: "gesture_swipe_up"` |
| 三击显示当前专场文件（补充） | `POST /api/remote-control` | `action: "gesture_triple_tap"` |

新 App 使用 HTTP 即可完成以上功能，不需要先注册手机 WebSocket，也不需要直连 28。本文不定义手机端的语音识别供应商协议。

### 通用约定

- 请求正文使用 UTF-8 JSON，设置 `Content-Type: application/json; charset=utf-8`。
- `target` 统一发送屏幕编号字符串，例如 `"2"`。不要发送 `"192.168.16.28"`、设备 UUID、`"2号屏"` 或 `"all"`。
- `source` 建议使用安装后持久保存的唯一标识，例如 `hall-phone-<安装UUID>`。同一手机所有接口使用同一值，不要每次请求变化，也不要所有手机共用一个值。
- 当前这三个业务端点没有要求登录、Token 或签名；在现有展厅网络中访问即可。不要填写 SSH 密码或管理端账号。
- `serverTime` 为 Unix 毫秒；`seq` 为中控生成的序号，中控重启后可能重新计数。
- 按住说话不传 `clientMode: "ROBOT"`，也不传 `suppressTts: true`：这两种配置会抑制展示动作，可能出现“有回答但屏幕没反应”。

## 2. 屏幕列表与选择

### 2.1 获取列表

```http
GET /api/screens HTTP/1.1
Host: 192.168.16.61:3001
```

响应 HTTP 200，结构示例（省略非必需字段）：

```json
{
  "screens": [
    {
      "deviceId": "2bc06324-f92a-4cff-85fb-fe1d5d268a6d",
      "screenNumber": 2,
      "name": "WIN-20241001ETD (2号屏)",
      "online": true,
      "ipAddress": "192.168.16.28",
      "status": {
        "view": "ppt",
        "currentPptFileName": "九天人工智能.pptx",
        "currentPptSlideNumber": 3,
        "updatedAt": 1788945808379
      }
    }
  ],
  "total": 1,
  "serverTime": 1788945809000
}
```

| 字段 | 类型 | 用法 |
| --- | --- | --- |
| `screens` | array | 已注册屏幕，包含离线设备；可能为空 |
| `screenNumber` | number | 用户选择和 `target` 的依据 |
| `deviceId` | string | 设备身份信息，不用于本文控制接口的目标值 |
| `name` | string | 可显示的设备名称 |
| `online` | boolean | 连接状态，离线项置灰；它不是实时送达保证 |
| `ipAddress` | string | 辅助辨认电脑，不用于控制请求路由 |
| `status` | object / null | 展示客户端最后上报状态，兼容字段缺失和空值 |
| `status.view` | string | 客户端界面状态，例如 `idle`、`ppt`；不是手势接口 `mode` 的同一枚举 |
| `status.currentPptSlideNumber` | number | 客户端上报的页码，可用于辅助确认；不是某一请求的执行回执 |
| `status.updatedAt` | number | 状态更新时间，用于判断信息是否陈旧 |

列表还可能返回 `snapshot`、`settings`、资源占用等字段，新 App 可忽略未使用字段。

### 2.2 选择流程

1. App 启动获取列表，按 `screenNumber` 显示；选屏页建议每 3～5 秒刷新，失败允许用户手动刷新。
2. 用户点击“2 号屏”后，在 App 本地保存 `selectedTarget = "2"`，主操作页持续显示当前目标。
3. 所有手势、文字请求都使用这个目标。未选择有效在线屏幕时不发送控制。
4. 用户切屏时，清除旧屏尚未发送的操作；录音或排队操作应绑定产生时的屏号，不能在发送时悄悄改成新屏号。

本次现场对应关系：**2 号屏 = 192.168.16.28；4 号屏 = 192.168.16.50；5 号屏 = 192.168.16.29**。这是核对时的对应关系，开发不能硬编码“第二项就是 2 号屏”或固定列表数量。

`/api/screens/settings` 是修改展示设备设置；`/api/avatar-target` 是改变数字人运行目标。两者都不是手机的控制选屏接口，选择屏幕时不要调用。

## 3. 左滑、右滑、双击

### 3.1 请求

```http
POST /api/remote-control HTTP/1.1
Host: 192.168.16.61:3001
Content-Type: application/json; charset=utf-8
```

左滑正文：

```json
{
  "action": "gesture_swipe_left",
  "target": "2",
  "source": "hall-phone-demo-01"
}
```

右滑将 `action` 改为 `gesture_swipe_right`；双击改为 `gesture_double_tap`。

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `action` | string | 必填，使用本文的精确动作名，区分大小写 |
| `target` | string | 新 App 必填，具体正整数屏号；服务端遗漏时默认 `all`，手势会报错，不可依赖默认值 |
| `source` | string | 新 App 应始终填写；服务端默认 `asr-apk`，翻页防重复会用到此值 |

此端点不读取手机传入的 `seq`、`requestId` 或自定义 `params`，不能通过添加这些字段实现幂等、指定页码或设置讲解级别。

### 3.2 同一个手势随屏幕状态变化

**手机只发送手势名，由中控决定当前动作。文件选择中的左右方向与 PPT 翻页方向不一致，下面是现行实现。**

| 手势 | `mode: file_picker` 文件选择 | `mode: ppt` PPT 播放 | `mode: normal` 普通展示 |
| --- | --- | --- | --- |
| 左滑 `gesture_swipe_left` | 上一个文件 `special_file_prev` | 下一页 `next_slide` | 忽略 |
| 右滑 `gesture_swipe_right` | 下一个文件 `special_file_next` | 上一页 `prev_slide` | 忽略 |
| 双击 `gesture_double_tap` | 打开选中文件 `special_file_open_selected` | 讲解当前 PPT 页 `ppt_speak_commentary` | 忽略 |
| 下滑 `gesture_swipe_down` | 下一个文件 `special_file_next` | 忽略 | 忽略 |
| 上滑 `gesture_swipe_up` | 返回主页 `home` | 返回主页 `home` | 返回主页 `home` |
| 三击 `gesture_triple_tap` | 请求当前专场文件列表 | 请求当前专场文件列表 | 请求当前专场文件列表 |

上滑响应 `mode` 为 `return`。三击的文件必须已配置在当前专场内，它不是“查找所有 PPT”的快捷搜索。

双击讲解由展示客户端取当前文件和当前页发起；手机不用传文件名或页码。讲解级别由中控当前配置决定。本次核对的 28 客户端按真实页码翻页，手机一次手势只发一个请求，不要额外发送方向键或第二个翻页请求。

### 3.3 响应及判断

PPT 左滑被路由：

```json
{
  "ok": true,
  "seq": 1234,
  "action": "next_slide",
  "sourceAction": "gesture_swipe_left",
  "target": 2,
  "mode": "ppt"
}
```

普通展示下双击无功能，仍返回 HTTP 200：

```json
{
  "ok": true,
  "seq": 1235,
  "action": "gesture_double_tap",
  "target": 2,
  "ignored": true,
  "mode": "normal"
}
```

重复翻页被过滤，仍返回 HTTP 200：

```json
{
  "ok": true,
  "seq": 1236,
  "action": "next_slide",
  "sourceAction": "gesture_swipe_left",
  "target": 2,
  "mode": "ppt",
  "ignored": true,
  "duplicate": true
}
```

无有效目标返回 HTTP 400：

```json
{"ok": false, "seq": 1237, "error": "请先选择一个具体屏幕"}
```

判断顺序：HTTP 错误 / `ok:false` → 请求失败；`duplicate:true` → 重复过滤；`ignored:true` → 当前状态无对应动作；其余 → 中控已处理请求。

**HTTP 200 和 `ok:true` 不等于 PPT 已经翻页、文件已经打开或数字人已经开始讲话。** 当前 HTTP 控制响应没有等待展示客户端执行完成；目标离线时也可能没有返回 HTTP 错误。App 不应立即把本地页码加一当作事实，可以结合屏幕状态刷新与现场画面确认。

### 3.4 防重复与顺序

当前中控仅对 `next_slide`、`prev_slide` 做时间过滤：以 **`source + target + 映射后的动作`** 为键，同一键距离上次请求 **小于 800 毫秒**就忽略。被忽略的请求也会刷新时间，所以不停快速发送可能一直被过滤；前进、后退分别计时。

- 手机触摸识别一次只提交一次：不要在滑动回调与抬手回调各发一遍。
- 同一目标的手势串行发送，避免并发到达次序变化。不要用长队列囤积翻页。
- 若允许连续同向翻页，应在手机端限制发送间隔至少 800 毫秒，建议留余量到 900 毫秒；被限流时给轻量反馈，不在后台攒请求补发。这是兼容现有服务端的建议，不是手机识别手势必须持续的时间。
- 超时、断网、`duplicate` 都不要自动重发翻页：上一请求可能已经执行。
- 双击不受上述 800 毫秒规则保护，手机必须避免同一次双击产生两个请求。
- 如果同时实现三击，识别器须区分双击/三击，不能先发送双击再发送三击。长按录音也不能同时触发滑动、双击。

## 4. 按住说话

### 4.1 实际职责与流程

**`/api/asr-text` 接收已经识别完成的文本，不是录音上传接口。** 本次核对的中控链路没有为手机实现“开始录音/结束录音/音频转文字”的 HTTP 接口；WebSocket 的 `voice` 路由同样读取 `msg.text`，不能把音频包直接交给它识别。

新手机端需要接入 Android 语音识别能力或选定的 ASR SDK/服务。原 APK 使用哪家 ASR、账号和音频格式，本次未取得可核实的配置，不能据此文档假定已有可调用的录音识别服务。

推荐状态流程：

```text
空闲 → 按下：取得麦克风权限、开始采集并固定目标屏
     → 持续按住：显示录音状态；中间识别结果只用于预览
     → 松开：结束采集，等待最终识别结果
     → 最终非空文本：只提交一次 POST /api/asr-text
     → 收到响应：显示 reply / 错误，回到空闲
取消、权限拒绝、识别失败、空白文本：不提交中控
```

ASR 的临时结果、重复最终回调都不能逐条提交，否则可能重复打开文件、翻页或讲话。麦克风权限、最大录音时长和音频编码按所选 ASR 的要求实现；这些不是中控规定的参数。

### 4.2 提交识别文字

```http
POST /api/asr-text HTTP/1.1
Host: 192.168.16.61:3001
Content-Type: application/json; charset=utf-8
```

```json
{
  "text": "查找九天人工智能的PPT",
  "target": "2",
  "source": "hall-phone-demo-01",
  "requestId": "7f3d95c5-7e8d-4ad4-81f3-c5cdb409bda9"
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `text` | string | 必填，去除首尾空白后不能空；传识别原文 |
| `target` | string | 新 App 必填；服务端未填时默认 `"2"`，不能依赖它代替选屏 |
| `source` | string | 与手势接口保持同一个安装标识 |
| `requestId` | string | 可选，建议每次有效语音提交生成 UUID，用于追踪；当前不是幂等键，不保证响应回显 |

普通展厅手机不发送 `clientMode`、`suppressTts`、`sessionId`。特别注意 `suppressTts: true` 在当前实现中抑制的是展示动作，不仅是声音。普通手机的 `sessionId` 也不会作为此 HTTP 入口的会话标识使用。

示意响应：

```json
{
  "ok": true,
  "seq": 2001,
  "reply": "请选择要展示的文件",
  "actions": [
    {
      "type": "show_media_results",
      "target": "2",
      "params": {}
    }
  ],
  "target": "2"
}
```

这里 `params` 仅为占位，实际可能包含文件列表等信息。不同路由返回字段会不同，`answerSource`、`context` 等为可选扩展。App 读取 `reply` 展示结果即可；**不要把返回的 `actions` 再逐条提交控制接口**，服务端已经负责下发，否则会重复执行。

### 4.3 常用识别文本示例

| 意图 | `text` 示例 |
| --- | --- |
| 查找文件 | `查找九天人工智能的PPT` |
| 打开搜索结果 | `打开第一个` |
| 指定文件 | `打开九天人工智能PPT` |
| 自然语言问答 | `介绍一下九天人工智能平台` |

这些是供联调的自然语言示例，不是固定命令枚举；最终处理取决于当前文件库、上下文和路由结果。“打开第一个”必须在同一目标屏已有结果的前提下使用。

### 4.4 兼容旧入口

旧入口支持 `GET /api/voiceInput?text=...&target=2&source=...`；中文和特殊字符必须 URL 编码。新 App 统一使用 `POST /api/asr-text` JSON，避免混用两条链路重复提交。

## 5. 固定按钮接口（可选）

如果新 App 增加明确标注“上一页/下一页”的按钮，可以继续调用 `POST /api/remote-control`，把 `action` 换成下表值。手势区域仍使用 `gesture_*`，不要把固定按钮动作与状态相关手势混为一谈。

| `action` | 作用 | 前提 |
| --- | --- | --- |
| `ppt_next` | PPT 下一页 | PPT 已打开 |
| `ppt_prev` | PPT 上一页 | PPT 已打开 |
| `ppt_speak_commentary` | 当前 PPT 页讲解 | PPT 已打开 |
| `special_files_show` | 显示当前专场分配给该屏的文件 | 当前专场及该屏文件已配置 |
| `special_files_prev` | 选择上一个文件 | 文件列表已显示 |
| `special_files_next` | 选择下一个文件 | 文件列表已显示 |
| `special_files_open` | 打开当前选中文件 | 文件列表已显示 |

固定动作响应可能没有 `mode`、`sourceAction`。未知动作 HTTP 400，错误形如 `unknown remote action: xxx`。`special_files_show` 成功返回 `seq`、`target`、`special`、`total`；没有配置时返回明确错误。不要将展示端内部动作名 `next_slide` 直接作为此 HTTP 接口的 `action`；这里对应的公开值是 `ppt_next`。

## 6. 错误、超时与 Android 接入

| 场景 | 当前接口表现 | App 行为 |
| --- | --- | --- |
| 控制缺少 `action` | HTTP 400，`action is required` | 修正参数 |
| 文字为空 | HTTP 400，`text is required` | 阻止提交空结果 |
| 手势没有具体目标 | HTTP 400，`请先选择一个具体屏幕` | 引导选屏 |
| 手势在当前状态无功能 | HTTP 200，`ignored:true` | 提示当前状态不可用，不重试 |
| 同向翻页过快 | HTTP 200，`duplicate:true` | 不重试，修正手机限流 |
| 专场没有文件 | HTTP 400，`error` 说明原因 | 提示配置问题 |
| 文字处理返回错误 | HTTP 502，`ok:false` | 显示错误；部分路由也可能以 200 返回文字兜底 |
| 未捕获服务端异常 | HTTP 500，`ok:false` | 记录错误、请求时间 |
| 网络超时或连接断开 | 没有可靠业务结果 | 显示“结果未知”，不自动重发控制 |

超时建议（手机策略，不是服务器 SLA）：连接 5 秒、屏幕查询 10 秒、手势响应 10 秒、语音文字处理 90 秒。文字路由可能调用模型，耗时与翻页不同。界面全程异步，等待时显示状态，避免卡住录音按钮。取消手机 HTTP 等待不保证服务器已停止执行。

Android 需要 `INTERNET` 权限，录音需要 `RECORD_AUDIO` 及运行时授权。现场使用明文 HTTP，新 App 需要按目标 Android 版本配置允许访问该地址的明文网络策略；通过公司 Wi-Fi 或其他有路由的网络连接到 `192.168.16.61:3001`。这些是集成要求，本文不预设所用网络/ASR SDK 版本。

建议日志保留：本地操作 ID、`requestId`、`source`、`target`、动作名、HTTP 状态、返回 `seq`、`mode`、`ignored`、发送与响应时间。耗时用手机单调时钟测量；服务器 `serverTime` 不应在未校时情况下直接相减计算延迟。

## 7. 联调验收清单

1. 首次启动展示真实屏幕列表，未选屏不能录音提交或发手势；切换到 2 号屏后所有请求均为 `target:"2"`。
2. 首页左滑/右滑/双击：正确处理 `ignored`，不显示“翻页成功”。
3. 已打开 PPT：左滑下一页、右滑上一页；单次触摸只产生一条请求。第一页后退和末页前进结合真实画面核对。
4. 双击 PPT：只触发一次当前页讲解；列表中双击则打开选中文件。
5. 文件列表：左滑上一个、右滑下一个，编号和画面选中项一致。
6. 快速同向滑动：正确处理 800 毫秒服务端过滤；停止操作后不补发一串翻页。
7. 按住说话：中间结果不提交，松手后的最终结果只提交一次；取消、空白、识别失败不发请求。
8. 查找→选择→打开全程使用同一屏；不得再次执行响应里的 `actions`。
9. 断网、超时、离线屏幕有清楚提示；不因自动重试出现重复打开或连跳。
10. App 前后台切换、权限拒绝和多手机同时使用时，录音资源释放正常，`source` 标识稳定且不同安装之间不冲突。

## 8. 核对范围与已知限制

- 已只读核对 61 的 `src/files/FileServer.js`、`src/router/MessageRouter.js`、`src/devices/DeviceManager.js`，并实际执行 `GET /api/screens`。没有为写文档向现场发送手势或语音控制，也没有修改、重启任何服务。
- 已对照 28 源码的当前页讲解与页码状态实现。其他屏幕客户端不保证与 28 完全同版。
- 接口没有逐指令完成回执、通用请求幂等、独立选屏会话或手机音频 ASR 服务。`requestId` 不能解决自动重试重复执行问题。
- 手势 `mode` 由中控状态和展示客户端上报共同判断，不能仅靠手机自己的画面猜测；客户端状态上报也存在时间差。
- 本文提供当前兼容接法。未来若要支持高速连续翻页、严格执行回执或服务端录音识别，需要另行扩展协议，不能让新 App 假定这些能力已存在。

附件：`小手机接口.postman_collection.json`。默认目标为 2 号屏，手动执行控制请求会影响现场屏幕；Collection 不应直接整组自动运行。
