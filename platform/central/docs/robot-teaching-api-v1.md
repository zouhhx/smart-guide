# V5 机器人问答与现场补充接口 V1

本接口是现有问答链路的兼容扩展。旧客户端不传新增字段时行为不变；61 不执行机器人导航、屏幕、播放或 TTS 命令。

## 1. 机器人问答

`POST /api/asr-text`

新增可选字段：

```json
{
  "text": "用户问题",
  "target": "2",
  "source": "microphone",
  "suppressTts": true,
  "clientMode": "ROBOT",
  "sessionId": "接待会话ID",
  "robotId": "机器人编号"
}
```

机器人请求返回 `answerStatus`：`ANSWERED`、`ABSTAINED` 或 `FAILED`。只有 `ABSTAINED + NO_KNOWLEDGE` 返回 `teachable=true`；价格、采购、承诺等场景返回 `STAFF_ONLY + teachable=false`；系统和模型故障返回 `FAILED + SERVICE_ERROR`。

当 `clientMode="ROBOT"` 时，61 会强制把该请求作为纯数据请求：不触发主机 TTS、数字人播报或展示屏动作，即使调用方遗漏 `suppressTts` 也不会产生动作。该策略按请求隔离，不会改变手机、主机麦克风或其他客户端的全局设置。

## 2. 教学内容分析（兼容保留，本轮不调用）

`POST /api/knowledge/teaching/analyze`

当前 V5 由本地记录识别文字、简单清理并直接复述，不再调用本接口，也不让 61 判断相关性或完整性。接口仅为旧联调兼容保留，不属于当前生产教学主链路。

请求必须携带 `Authorization: Bearer <ROBOT_TEACHING_TOKEN>`。接口接受 `analysisId`、`teachingTurnId`、`revision`、`robotId`、`sessionId`、`question`、`accumulatedTranscript`、`fragmentCount`、`isFinal` 和 `completionReason`。

过程分析按机器人限制为最多一个并发，且至少间隔 3 秒；超限返回 425/429。过程分析模型超时 3 秒，最终整理超时 5 秒。返回值原样回显三个关联字段，并包含 `relevance`、`completeness`、`factuality`、`canReplay`、`replayAnswer`、`warnings` 和 `evidenceRefs`。

模型只负责整理工作人员已经说出的内容。没有直接内部依据时，`factuality` 必须为 `UNVERIFIED`。

## 3. 待审核草稿

`POST /api/knowledge/qa-drafts`

请求必须携带同一 Bearer 凭据。`submissionId` 是持久化幂等键：相同 ID 和相同内容重放会返回原 `draftId`；相同 ID 但内容不同返回 409。

草稿只能以 `PENDING_REVIEW` 创建；`rawAnswer` 与 `suggestedAnswer` 分开保存，任何内容都不会自动进入正式 QA、向量库、缓存或模型上下文。

审核后台使用管理端登录凭据调用：

- `GET /api/knowledge/qa-drafts`：查看草稿；
- `PATCH /api/knowledge/qa-drafts/:draftId`：编辑正式问题和答案；
- `POST /api/knowledge/qa-drafts/:draftId/publish`：审核发布；
- `POST /api/knowledge/qa-drafts/:draftId/reject`：拒绝草稿。

发布会先写入向量索引，再进入与现有后台相同的正式 FAQ；任一步失败都保留为 `INDEX_FAILED`，不能显示为已发布。机器人 Bearer 凭据没有查看、编辑或发布权限。

## 4. 配置与联调

在 61 的 `.env` 中配置独立强随机 `ROBOT_TEACHING_TOKEN`。V5 与 61 使用 3001 验证：旧客户端兼容、机器人请求静音且无屏幕动作、三种问答状态、知识不足仍形成问答记录、草稿幂等重放以及系统故障不进入教学。生产 Token 不得写入 Git、响应或普通日志。
