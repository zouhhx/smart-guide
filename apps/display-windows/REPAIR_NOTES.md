## 当前线上版本（2026-09-09 17:10，07-stable-display）

已部署列表显示和 PPT 误退回主页修复，线上 DLL SHA256 为 9FF1B79442B1D0C42D79A986A8FEAA5CBAE1F487731FE7D48315C8EBAAF613C6。先将 06 回退至原单次翻页基线，再完成本轮隔离验证后部署 07。对应备份：28 的 D:\exhibition-client-backups\before-07-stable-display-20260909；脚本 D:\exhibition-client-repair\07-stable-display\rollback.ps1。居中和左上编号保留，61 未修改。

最新实机：列表本地显示约 987/739/909 ms；消防监管停留 22 秒不误退；九天人工智能前后翻页确认成功约 2569/1447 ms；正常结束放映恢复主页；不抢焦点的错误提示条实际显示且 PPT 仍可见。媒体翻页仍未达到 1 秒目标，这一轮不是全部文件和长时稳定性的最终验收。

根因、实现、测试限制与证据详见 releases\07-stable-display\README.md。下文 06 及更早版本均属历史。
## 打开 PPT 持续失败修复（2026-09-09 16:27）

根因：16:00:49 的 GotoSlide 在 16:00:54 达到 5 秒超时，16:00:55 实际返回，但队列故障状态一直保持；16:01、16:17 的新打开请求被 rejected-faulted 拒绝。本地文件存在且已命中缓存，因此这次打不开不是路径不存在或目标屏号错误，而是候选的超时恢复设计缺陷。

修复：队列在锁内跟踪实际执行状态。只有新的显式打开请求能请求恢复；旧调用仍在执行时明确拒绝，实际返回后在同一 STA 先清理本客户端持有的文档和 Office 引用，再执行新的打开。旧积压操作不恢复、不重放，不自动重试相对翻页；恢复失败重新进入故障状态。保持 5 秒翻页保护，不改原始 PPT，不结束所有 Office 进程。

验证：本地测试通过 STA/顺序、超时清积压、调用仍忙时拒绝恢复、旧调用结束后显式恢复、恢复失败保持故障、文件缺失不污染队列。28 隔离实机测试模拟一次超时，再打开用户报错的两份实际文件：5G 专网 2925 ms，2号屏 AI+数字住建 9064 ms；两份均确认第 1 页，截图核对内容及数字人正常。此结果验证可重新打开，仍不代表大型媒体文件速度达标。

现场先回退 05 到原单次翻页基线，再完成实机测试，随后部署 06-open-recovery。DLL SHA256：158BB6B225FBBE64D80AED46F780DF44CB40BAC57AFA372A471AF6283B8360A7。部署回退点为 28 的 D:\exhibition-client-backups\before-06-open-recovery-20260909，脚本 D:\exhibition-client-repair\06-open-recovery\rollback.ps1。居中及左上角编号保留，61 未修改。

证据：screen2-inspection\open-failure-client.log、open-failure-events.json、open-recovery-build-tests.log、open-recovery-results.json、open-recovery-5g.png、open-recovery-housing.png。
## 最新部署（2026-09-09 15:50）

用户要求调整居中后推送现场测试，已部署 releases\05-centered-cards 候选到 28，并通过 ExhibitionClientOnLogon 启动在交互会话 1。线上 DLL SHA256：A451DB5337601EF02B4C551CDCA57042674B2B30D91722A8620C564FBE358ADC。此前所有“线上仍为基线/未部署”描述均属历史。

列表优先按整个屏幕水平居中，右边界约束在数字人预留区左侧；1920×1080 下 x 从约 300 调整为 530。编号在每张卡片左上角独立行，不遮图，标题占整行。候选同时包含此前的 PPT、显示恢复等源码修复；媒体 PPT 超时仍为已知未解决问题，没有提高 5 秒保护阈值。

构建 0 错误、68 个原有警告。28 隔离目录进行 1 轮普通 PPT 翻页、列表几何检查和返回主页，通过；居中和编号截图已核对（使用模拟列表及占位内容，并非真实缩略图接口验收）。部署后进程响应、数字人和主页截图正常，真实手机操作待用户测试。61 未修改。

证据：screen2-inspection\centered-cards-ui-results.json、centered-cards.png、centered-cards-deployed-home.jpg、centered-cards-build.log。

回退点：28 的 D:\exhibition-client-backups\before-05-centered-cards-20260909。回退脚本：D:\exhibition-client-repair\05-centered-cards\rollback.ps1，只在对应线上哈希一致时恢复 DLL/PDB，通过原交互任务启动，并恢复数字人；不覆盖配置。完整源码在当前源码目录和本次 source.zip。
# 28 客户端修复进展与交接（2026-09-09）

## 单桌面复测更新（2026-09-09 15:40）

用户关闭第二个虚拟桌面后，已通过 SSH 确认 sshd 服务正常，数字人“有灵屏型智能体”已在交互会话 1 恢复运行。普通 CMD 关闭没有导致 SSH 失联。原客户端启动时存在自动关闭数字人的代码；候选源码已移除此启动/退出副作用，不能将数字人关闭直接归因于虚拟桌面。

最新候选在隔离测试目录完成 30/30 轮列表→普通 PPT→主页，状态断言、编号/缩略图和标题/按钮边界断言通过。截图确认真实 PPT 可见，结束后完整城市背景与数字人恢复。首轮普通 PPT 打开 2713 ms，记录的 8 次交替翻页 51–188 ms。该测试使用本地生成的普通 PPT 和模拟文件列表，并非 APK 端到端验收；列表缩略图使用测试图片。

随后同一单桌面、数字人运行条件下，《九天人工智能.pptx》打开约 6323 ms，第 1→2 页再次超过 5 秒控制器超时。说明多桌面不是全部卡顿的解释。超时调用随后完成，遗留测试放映通过交互会话中的 COM 正常关闭；没有补发翻页或结束所有 Office 进程。尚未取得挂起转储，也未证明具体硬件瓶颈。

复测产物：`screen2-inspection\single-desktop-ui-results.json`、`single-desktop-media-results.json`、`single-desktop-home-after.png`、`single-desktop-ppt.png`、`single-desktop-cards.png`。测试任务已结束，线上仍保留原单次翻页修复版本，候选未正式发布。61 未修改。

下文保留此前测试历史；其中“15 轮中止”和虚拟桌面干扰属于此前一轮，已由上述单桌面 30 轮复测补充。媒体性能、现场真实手势、断线恢复及外部背景降载接口仍待验收。

## 当前状态

**源码修复已落地并可构建；候选版本尚未完成现场验收。28 已回退到本轮开始前、已修复双翻页的版本。**

当前线上 DLL 的 SHA256：`16AD5992791785E6C28CCA6DFEBB0C228B732CC87327B41640D0CC994DB597CF`。

本轮曾依次试部署 PPT 控制、显示恢复两批。完整测试发现数字人与媒体 PPT 同时运行时仍可能超时，且截图出现 Windows 任务视图及另一个虚拟桌面的 WELCOME 画面，无法确认完整现场画面验收。自动循环已停止；没有把最后一批候选文件留在线上。61 未修改代码、配置或重启服务。

## 源码与构建

用户提供的 `D:\讲解修复\exhibition-hall-cs` 中 16 个非生成源码文件，全部与 28 原始运行程序的 PDB SHA256 一致。缺失源码问题已解决。改动前源码存档：`screen2-inspection\matching-source-original.zip`；逐文件证据：`screen2-inspection\matching-source-verification.json`。

本地 SDK：`.tools\dotnet\dotnet.exe`，版本 8.0.425。目标仍为 .NET 8 Windows，没有新增第三方包，没有改原始 PPT 内容或 Office/显卡设置。

在本工作目录执行：

```powershell
.\build-repair.ps1
```

脚本仅构建并运行 STA 队列测试，不部署、不操纵展厅画面。最新构建 0 错误，68 个原工程 nullable 注解警告。完整日志：`screen2-inspection\final-build.log`。

候选文件：`releases\03-cards\ExhibitionClient.dll`、`ExhibitionClient.pdb`。该目录的 `source.zip` 包含源码、项目文件、资源；`changes.patch` 是相对原始匹配源码的差异，`hashes.json` 记录候选文件哈希。

## 已实现的改动

- `Controllers/PPTController.cs`：改为 Office 放映对象的真实页码读取与 `GotoSlide`，取消全局方向键、双按键和定时抢焦点；第一页/最后一页边界保持不变。只有读回页码与目标一致才记为 confirmed。
- `Controllers/StaOperationQueue.cs`：专用 STA、顺序执行、32 项队列上限。独立超时计时器不会随 Office 阻塞；超时后取消待执行项并停止接收后续 PPT 操作，禁止补发相对翻页。
- 复用客户端管理的 Office 引用；同文件路径、长度、修改时间一致时复用放映。只关闭自己打开的文档；退出放映后保留空闲实例 5 分钟，可用内存连续 15 秒低于 1 GB 且无文档时释放。没有“结束所有 PowerPoint”逻辑。
- `Models/Command.cs`、`Services/WebSocketService.cs`：保留现有协议，记录连接会话、服务端序号、同机 Stopwatch 时间；连接内按序号去重，兼容无序号旧消息；界面命令按接收顺序执行。
- `Views/MainForm.cs`：文件准备与 Office 就绪后才切换显示；取消旧窗口的 Normal/Maximized 反复切换和列表的 WM_SETREDRAW 调用；绘制面使用双缓冲。最新候选会在放映就绪后隐藏客户端主窗口并停止交互图标动画，返回主页时恢复完整画面。
- 缩略图下载/解码异步，限制并发 2，保留尺寸限制 800 像素，过期结果不应用，清列表时释放图片；失败保持占位和卡片几何结构。
- 文件列表继续三列、预留数字人区域。编号放到标题区域；图片保持比例、取消独立灰底/装饰框；标题最多两行并提供完整名称提示；黄色讲解标记固定在底部。
- `Services/AvatarClientService.cs`：候选版本不再因客户端启动/退出自动结束数字人；保留服务端明确的 activate/deactivate 动作。
- `Views/LogForm.cs`、日志服务：后台日志不隐式创建窗口，文件写入串行，减少诊断记录丢失。日志区分 received、execute、goto-request、goto-returned、confirmed、timeout、rejected-faulted。

## 已得到的测试结果

| 测试 | 结果与范围 |
|---|---|
| 本地队列回归 | STA 线程、交替方向顺序、超时取消积压、恢复后不补发、文件缺失后仍能执行均通过。`client-tests` 链接实际控制器/队列源码。 |
| 28 纯文字 Office 测试 | 24 次前后交替，全部真实页码正确；约 27–267 ms。首次打开约 3.13 s，重复打开同一放映约 37 ms，关闭后重新打开约 357 ms。边界和直接跳页通过。 |
| 28 完整客户端、数字人未运行 | 普通 PPT 8 次翻页约 10–57 ms；《九天人工智能》8 次翻页均到正确页，部分约 1.19 s。3 轮列表→PPT→主页，状态和背景恢复检查通过。不能据此声称全部画面场景已验收。 |
| 数字人与 PPT 同时运行 | 普通文件测试曾通过；《九天人工智能》第 1→2 页出现超过 5 s 的 Office 调用超时。超时后没有继续执行积压翻页。 |
| 最新 30 轮测试 | 记录到 15 轮进度后主动中止。截图出现任务视图/虚拟桌面切换，无法判定是谁或哪个程序引起，未将其记为 30 轮验收通过。 |
| 资源采样 | 数字人运行后曾观察到约 91% 内存使用。另一段采样 CPU 52–97%，最低可用内存 958 MB，部分时刻发生页面读入；GPU 上 ExhibitionShow 和数字人相关进程均有负载。采样未与超时瞬间严格对齐，不能单凭这些数值断定根因。 |

数据文件：

- `screen2-inspection\office-plain-results.json`
- `screen2-inspection\stage2-ui-results.json`
- `screen2-inspection\stage3-avatar-failure.json`
- `screen2-inspection\full-load-samples.json`
- `screen2-inspection\home-after.png`：3 轮测试后的完整城市背景
- `screen2-inspection\stage3-plain.png`：受任务视图干扰的截图，**不能当作画面验收图**
- `screen2-inspection\stage3-cards.png`：受虚拟桌面干扰的 WELCOME 截图，**不是新卡片排版的预览**

普通 PPT 的性能测试显示控制链有改善，但样本量有限；未取得与原控制链同样条件的严格前后配对数据。媒体 PPT 与数字人同时运行仍未满足目标，不能宣布卡顿已全部解决。

## 尚未完成的验收和诊断

1. 确认 28 的测试窗口和虚拟桌面，在没有其他人/程序切换桌面的情况下重跑最新候选；逐次核对真实页码与屏幕实际画面。最新窗口隐藏/放映激活修正需要现场复测。
2. 完成 30 轮完整切换，实际检查卡片样式、占位、长标题、数字人显示和整页渲染；目前只有部分几何断言运行过，最终视觉截图尚未取得。
3. 在数字人稳定运行与暂停数字人两种状态下，对同一媒体 PPT 做受控对照，恢复原状态；卡住时采集 Office 挂起转储和对应 CPU/GPU/内存/磁盘数据。当前尚未取得挂起转储。
4. 外部 `ExhibitionShow.exe` 已作只读接口检查，尚未找到能确认只暂停背景绘制、同时保留数字人的安全接口。没有调用不确定的暂停命令，也没有强制挂起进程。
5. 真实 APK/61→28 的端到端交替滑动、断线重连仍需联测；当前验证的是 28 控制器及客户端接收后的行为。服务端原有过滤仍可能在指令到达 28 之前丢弃快速手势。
6. 新版当前策略在 Office 超时后要求检查 PowerPoint 并重启客户端，不会自动清理挂起 Office 或自动重放命令。需现场验证提示与恢复流程。

## 部署与回退记录

28 的完整回退点：`D:\exhibition-client-backups\repair-baseline-20260909`，其中 app 为完整客户端，包含已生效的单次翻页修复，另有任务 XML 与文件哈希。

两批发布前的小备份：

- `D:\exhibition-client-backups\before-01-ppt-20260909`
- `D:\exhibition-client-backups\before-02-display-20260909`

已执行回退：只恢复第一份备份中的 DLL/PDB，保留线上配置，通过 `ExhibitionClientOnLogon` 任务恢复交互会话中的客户端，并核对基线 SHA256。

`releases\01-ppt\deploy.ps1`、`02-display\deploy.ps1` 和 `releases\rollback-to-baseline.ps1` 是本轮历史操作记录，包含当时的前置哈希，不应跳过检查直接重跑。下一次部署应从当前线上基线重新校验和备份，完成现场测试后再发布候选。

现场测试工具保留在 28 的 `D:\exhibition-client-tests`，仅人工启动的测试任务没有周期触发器。本轮自动循环已停止。UI 测试使用隔离目录中的 settings.json，服务器地址为本机无服务端口，不更改正式客户端配置。


## 左上角编号调整

按用户最新要求，编号移至卡片左上角独立顶行，透明底且不遮图；标题占整行。新的本地候选为 releases\04-number-layout，构建通过，尚未部署或进行本次排版的现场验收。此前编号放到标题区域的描述由本次更新替代。

## 文件选择没有显示的现场定位

部署后用户反馈语音答应查找/打开但 28 没有画面。只读核对 28 的 client.log 和 61 的 /api/usage-events：15:53:40、15:54:35、15:54:48 的查找指令，以及 15:54:40 的打开第 1 个，均来自 asr-apk，target=4，动作 show_media_results/play_ppt 的 target 也为 4。/api/screens 确认 28=2 号屏，而 4 号屏=192.168.16.50。28 同期仅收到心跳/配置，没有对应列表/打开指令。

FileServer.js 第 1724 行读取 payload.target，未提供时默认 2。因此本次记录中的目标 4 是请求目标，并非 28 客户端改了自身屏号。需要将手机控制目标改回 2 号屏，重新查找后选择文件；这次未回退客户端，也未修改 61。证据保存于 screen2-inspection\missing-file-ui-usage-events.json。
