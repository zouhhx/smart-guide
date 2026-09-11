# APK 科大讯飞 ASR 调用说明

本文整理 `exhibition-hall_AsrApk` 中 Android APK 调用科大讯飞 SparkChain ASR 的方式。

核心代码文件：

- `app/src/main/java/com/codexpro/asrapk/VoiceInputController.kt`
- `app/src/main/java/com/codexpro/asrapk/MainActivity.kt`
- `app/src/main/res/values/strings.xml`

整体链路：

```text
按住说话
  -> MainActivity 检查录音权限
  -> VoiceInputController.startListening()
  -> 初始化/启动讯飞 ASR
  -> Android AudioRecord 采集麦克风 PCM
  -> asr.write(pcmBytes) 写入讯飞
  -> AsrCallbacks.onResult() 接收识别结果
  -> sendRecognizedText() 发给中控 /api/asr-text
```

## 1. SDK 初始化

`VoiceInputController` 创建时会执行：

```kotlin
init {
    initSparkChain()
}
```

初始化方法：

```kotlin
private fun initSparkChain() {
    val config = SparkChainConfig.builder()
        .appID(appContext.getString(R.string.appid))
        .apiKey(appContext.getString(R.string.apikey))
        .apiSecret(appContext.getString(R.string.apiSecret))
        .logLevel(LogLvl.VERBOSE.value)

    val ret = SparkChain.getInst().init(appContext, config)
    sdkReady = ret == 0
}
```

讯飞配置项来自 `strings.xml`：

```xml
<string name="appid">...</string>
<string name="apiSecret">...</string>
<string name="apikey">...</string>
```

这里的作用是把 APK 绑定到讯飞 SparkChain 服务。`ret == 0` 表示初始化成功。

## 2. 创建 ASR 实例

开始识别前会确保 ASR 实例存在：

```kotlin
private fun ensureAsr(): ASR? {
    if (asr != null) {
        return asr
    }
    return runCatching {
        ASR().also { it.registerCallbacks(asrCallbacks) }
    }.getOrNull().also {
        asr = it
    }
}
```

关键点：

- `ASR()` 是讯飞 ASR 对象。
- `registerCallbacks(asrCallbacks)` 注册识别结果、错误、语音开始和结束回调。
- APK 复用同一个 `asr` 实例。

## 3. 开始识别

按住说话后，`MainActivity` 调用：

```kotlin
voiceInputController.startListening()
```

`VoiceInputController` 内部进入：

```kotlin
private fun startListeningInternal()
```

这里会配置讯飞识别参数：

```kotlin
instance.language("zh_cn")
instance.domain("iat")
instance.accent("mandarin")
instance.vinfo(true)
instance.dwa("wpgs")
```

参数含义：

- `language("zh_cn")`：中文识别。
- `domain("iat")`：听写场景。
- `accent("mandarin")`：普通话。
- `vinfo(true)`：启用语音端点相关信息。
- `dwa("wpgs")`：动态修正/流式结果相关配置。

然后启动讯飞识别会话：

```kotlin
currentSessionId = sessionCounter.getAndIncrement().toString()
val ret = instance.start(currentSessionId)
```

`ret != 0` 表示启动失败；成功后 APK 才会开始本地录音。

## 4. 本地录音

APK 使用 Android 原生 `AudioRecord` 采集麦克风，而不是让讯飞 SDK 自己录音。

录音参数：

```kotlin
private const val SAMPLE_RATE = 16_000
private const val CHUNK_SIZE = 1280
private const val CHUNK_INTERVAL_MS = 40L
```

`AudioRecord` 创建方式：

```kotlin
val recorder = AudioRecord(
    MediaRecorder.AudioSource.MIC,
    SAMPLE_RATE,
    AudioFormat.CHANNEL_IN_MONO,
    AudioFormat.ENCODING_PCM_16BIT,
    minBufferSize * 2
)
```

也就是说，APK 采集的是：

- 采样率：`16000 Hz`
- 声道：单声道
- 编码：16-bit PCM

## 5. 把音频写给讯飞

录音线程中循环读取 PCM 数据：

```kotlin
val read = recorder.read(buffer, 0, buffer.size)
```

读到有效音频后：

```kotlin
pushAudioLevel(calculateAudioLevel(buffer, read))
instance.write(buffer.copyOf(read))
Thread.sleep(CHUNK_INTERVAL_MS)
```

这里有两件事：

1. `pushAudioLevel(...)`：计算音量，驱动 APK 音波 UI。
2. `instance.write(...)`：把 PCM 音频块写入讯飞 ASR。

真正调用讯飞识别流输入的是：

```kotlin
instance.write(buffer.copyOf(read))
```

## 6. 接收讯飞识别结果

识别回调定义在：

```kotlin
private val asrCallbacks = object : AsrCallbacks { ... }
```

主要回调：

```kotlin
override fun onResult(asrResult: ASR.ASRResult?, userTag: Any?)
override fun onError(asrError: ASR.ASRError?, userTag: Any?)
override fun onBeginOfSpeech()
override fun onEndOfSpeech()
```

`onResult` 中读取识别文本：

```kotlin
val status = asrResult.status
val text = asrResult.bestMatchText.orEmpty()
```

如果识别到了文字，会更新预览：

```kotlin
resultBuilder.setLength(0)
resultBuilder.append(text)
pushPreview(resultBuilder.toString())
```

当 `status == 2` 时，表示讯飞认为本轮识别结束：

```kotlin
if (status == 2) {
    stopRecording()
    sendRecognizedText()
}
```

## 7. 松手结束或取消

正常松手发送：

```kotlin
fun stopAndSend() {
    isCanceled = false
    stopRecordingAndNotify(cancelImmediately = false)
}
```

取消：

```kotlin
fun cancelListening() {
    isCanceled = true
    resultBuilder.clear()
    pushPreview("")
    stopRecordingAndNotify(cancelImmediately = true)
}
```

最终都会调用：

```kotlin
private fun stopRecordingAndNotify(cancelImmediately: Boolean) {
    stopRecording()
    runCatching { asr?.stop(cancelImmediately) }
}
```

关键区别：

- `asr.stop(false)`：正常结束识别。
- `asr.stop(true)`：取消/立即停止。

## 8. 释放 SDK

Activity 销毁时，`MainActivity.onDestroy()` 会调用：

```kotlin
voiceInputController.release()
```

释放逻辑：

```kotlin
fun release() {
    stopRecording()
    runCatching { asr?.stop(true) }
    asr = null
    httpClient.dispatcher.executorService.shutdown()
    httpClient.connectionPool.evictAll()
    if (sdkReady) {
        runCatching { SparkChain.getInst().unInit() }
    }
}
```

这里会：

- 停止本地录音。
- 停止讯飞 ASR。
- 清空 ASR 实例。
- 关闭 HTTP 客户端。
- 调用 `SparkChain.getInst().unInit()` 释放讯飞 SDK。

## 9. 识别文本发送给中控

讯飞只负责把语音转成文本。识别完成后，APK 把文本发送给中控：

```kotlin
private fun sendRecognizedText()
```

目标接口：

```text
POST /api/asr-text
```

请求体：

```json
{
  "text": "识别出来的文本",
  "target": "当前选中的屏幕号",
  "source": "asr-apk"
}
```

代码：

```kotlin
val url = baseUrl.toHttpUrl().newBuilder()
    .addPathSegments("api/asr-text")
    .build()

val json = JSONObject()
    .put("text", text)
    .put("target", targetProvider())
    .put("source", "asr-apk")
    .toString()
```

也就是说：

- APK 调讯飞完成语音识别。
- APK 不做业务理解。
- APK 把识别文本交给中控。
- 中控决定怎么路由、怎么控制屏幕。

## 10. 关键 API 总结

APK 用到的讯飞相关 API：

```kotlin
SparkChainConfig.builder()
SparkChain.getInst().init(context, config)
SparkChain.getInst().unInit()

ASR()
asr.registerCallbacks(callbacks)
asr.language("zh_cn")
asr.domain("iat")
asr.accent("mandarin")
asr.vinfo(true)
asr.dwa("wpgs")
asr.start(sessionId)
asr.write(pcmBytes)
asr.stop(cancelImmediately)
```

识别结果相关：

```kotlin
asrResult.status
asrResult.bestMatchText
asrError.code
asrError.errMsg
```

## 11. 当前实现特点

- APK 侧手动采集 PCM，再写给讯飞。
- 录音和网络发送都放在后台线程。
- 识别中实时更新预览文字。
- 识别结束后统一发给中控 `/api/asr-text`。
- 当前 APK 的手势遥控逻辑和 ASR 文本发送逻辑是分开的。
