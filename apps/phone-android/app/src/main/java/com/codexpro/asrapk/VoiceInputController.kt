package com.codexpro.asrapk

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Handler
import android.os.Looper
import androidx.core.content.ContextCompat
import com.iflytek.sparkchain.core.LogLvl
import com.iflytek.sparkchain.core.SparkChain
import com.iflytek.sparkchain.core.SparkChainConfig
import com.iflytek.sparkchain.core.asr.ASR
import com.iflytek.sparkchain.core.asr.AsrCallbacks
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread
import kotlin.math.abs
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.sqrt

class VoiceInputController(
    context: Context,
    private val onStatusChanged: (String) -> Unit,
    private val onPreviewChanged: (String) -> Unit,
    private val onFinalTextReady: (String) -> Unit,
    private val onAudioLevelChanged: (Float) -> Unit,
    private val onListeningChanged: (Boolean) -> Unit = {},
    private val sparkCredentialsProvider: () -> SparkCredentials?
) {

    private val appContext = context.applicationContext
    private val mainHandler = Handler(Looper.getMainLooper())
    private val sessionCounter = AtomicInteger(1)
    private val lifecycleGeneration = AtomicInteger(0)
    private val resultBuilder = StringBuilder()
    private var audioRecord: AudioRecord? = null
    private var asr: ASR? = null
    private var currentSessionId = ""

    @Volatile
    private var isRecording = false

    @Volatile
    private var isStarting = false

    @Volatile
    private var isCanceled = false

    @Volatile
    private var finishWhenStarted = false

    @Volatile
    private var sdkReady = false

    @Volatile
    private var awaitingFinalResult = false

    init {
        initSparkChain()
    }

    fun startListening() {
        startListeningInternal()
    }

    fun isBusy(): Boolean = isRecording || isStarting || awaitingFinalResult

    private fun startListeningInternal() {
        if (awaitingFinalResult) {
            pushStatus("正在完成上一次识别…")
            return
        }
        if (!sdkReady) {
            initSparkChain()
            if (!sdkReady) {
                pushStatus(appContext.getString(R.string.sdk_init_failed))
                return
            }
        }
        if (isRecording || isStarting) {
            return
        }

        val instance = ensureAsr()
        if (instance == null) {
            pushStatus("语音识别初始化失败")
            return
        }

        resultBuilder.clear()
        pushPreview("")
        isCanceled = false
        finishWhenStarted = false

        currentSessionId = sessionCounter.getAndIncrement().toString()
        instance.language("zh_cn")
        instance.domain("iat")
        instance.accent("mandarin")
        instance.dwa("wpgs")

        isStarting = true
        val generation = lifecycleGeneration.incrementAndGet()
        pushStatus("正在启动识别…")
        thread(start = true, name = "sparkchain-asr-start") {
            val ret = instance.start(currentSessionId)
            if (ret != 0) {
                isStarting = false
                pushStatus("开启识别失败: $ret")
                return@thread
            }
            if (generation != lifecycleGeneration.get() || isCanceled) {
                isStarting = false
                runCatching { instance.stop(true) }
                return@thread
            }
            startRecording(generation)
            if (generation == lifecycleGeneration.get() && finishWhenStarted && isRecording) {
                finishWhenStarted = false
                stopRecordingAndNotify(cancelImmediately = false)
            }
        }
    }

    fun markCancelPending() {
        if (!isRecording) {
            return
        }
        isCanceled = true
        pushStatus(appContext.getString(R.string.status_slide_cancel))
    }

    fun clearCancelPending() {
        if (!isRecording) {
            return
        }
        isCanceled = false
        pushStatus(appContext.getString(R.string.status_press_talk))
    }

    fun stopAndSend() {
        if (isStarting && !isRecording) {
            finishWhenStarted = true
            pushStatus("正在结束识别…")
            return
        }
        if (!isRecording) {
            return
        }
        isCanceled = false
        pushStatus("正在结束识别…")
        stopRecordingAndNotify(cancelImmediately = false)
    }

    fun cancelListening() {
        if (!isRecording && !isStarting) {
            return
        }
        lifecycleGeneration.incrementAndGet()
        isCanceled = true
        awaitingFinalResult = false
        finishWhenStarted = false
        resultBuilder.clear()
        pushPreview("")
        pushStatus("已取消")
        stopRecordingAndNotify(cancelImmediately = true)
    }

    fun release() {
        lifecycleGeneration.incrementAndGet()
        awaitingFinalResult = false
        stopRecording()
        runCatching { asr?.stop(true) }
        asr = null
        if (sdkReady) {
            runCatching { SparkChain.getInst().unInit() }
        }
        postToMain { onAudioLevelChanged(0f) }
    }

    private fun initSparkChain() {
        val credentials = sparkCredentialsProvider()
        if (credentials == null) {
            sdkReady = false
            pushStatus(appContext.getString(R.string.spark_credentials_missing))
            return
        }
        val config = SparkChainConfig.builder()
            .appID(credentials.appId)
            .apiKey(credentials.apiKey)
            .apiSecret(credentials.apiSecret)
            .logLevel(LogLvl.VERBOSE.value)

        val ret = SparkChain.getInst().init(appContext, config)
        sdkReady = ret == 0
        pushStatus(if (sdkReady) appContext.getString(R.string.status_idle) else "SDK 初始化失败: $ret")
    }

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

    private val asrCallbacks = object : AsrCallbacks {
        override fun onResult(asrResult: ASR.ASRResult?, userTag: Any?) {
            if (asrResult == null || isCanceled || userTag != currentSessionId) {
                return
            }
            val status = asrResult.status
            val text = asrResult.bestMatchText.orEmpty()

            if (text.isNotBlank()) {
                synchronized(resultBuilder) {
                    if (status == 0) {
                        resultBuilder.clear()
                    }
                    resultBuilder.setLength(0)
                    resultBuilder.append(text)
                    pushPreview(resultBuilder.toString())
                }
            }

            if (status == 2) {
                awaitingFinalResult = false
                stopRecording()
                sendRecognizedText()
            }
        }

        override fun onError(asrError: ASR.ASRError?, userTag: Any?) {
            if (userTag != currentSessionId) return
            awaitingFinalResult = false
            stopRecording()
            if (isCanceled) {
                pushStatus("已取消")
                return
            }
            val code = asrError?.code ?: -1
            val msg = asrError?.errMsg ?: "unknown error"
            pushStatus("识别失败: $code $msg")
        }

        override fun onBeginOfSpeech() {
            pushStatus(appContext.getString(R.string.status_press_talk))
        }

        override fun onEndOfSpeech() {
            pushStatus("说话结束")
        }
    }

    private fun startRecording(generation: Int) {
        if (ContextCompat.checkSelfPermission(appContext, Manifest.permission.RECORD_AUDIO) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            failRecordingStart(appContext.getString(R.string.microphone_permission_required))
            return
        }
        val minBufferSize = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT
        )
        if (minBufferSize <= 0) {
            failRecordingStart("录音器初始化失败")
            return
        }

        val recorder = runCatching {
            AudioRecord(
                MediaRecorder.AudioSource.MIC,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                minBufferSize * 2
            )
        }.getOrElse {
            failRecordingStart("录音器初始化失败")
            return
        }
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            recorder.release()
            failRecordingStart("录音器初始化失败")
            return
        }
        if (generation != lifecycleGeneration.get() || isCanceled) {
            recorder.release()
            isStarting = false
            return
        }
        audioRecord = recorder
        if (runCatching { recorder.startRecording() }.isFailure) {
            audioRecord = null
            recorder.release()
            failRecordingStart("无法开始录音")
            return
        }
        if (recorder.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
            audioRecord = null
            recorder.release()
            failRecordingStart("无法开始录音")
            return
        }
        isRecording = true
        isStarting = false
        postToMain { onListeningChanged(true) }
        pushStatus(appContext.getString(R.string.status_press_talk))

        thread(start = true, name = "sparkchain-asr-recorder") {
            val instance = asr ?: return@thread
            val buffer = ByteArray(CHUNK_SIZE)
            while (isRecording && generation == lifecycleGeneration.get()) {
                val read = recorder.read(buffer, 0, buffer.size)
                if (read > 0) {
                    pushAudioLevel(calculateAudioLevel(buffer, read))
                    val writeResult = instance.write(buffer.copyOf(read))
                    if (writeResult != 0 && isRecording) {
                        stopRecordingAndNotify(cancelImmediately = true)
                        pushStatus("音频流写入失败: $writeResult")
                        break
                    }
                } else if (read < 0 && isRecording) {
                    stopRecordingAndNotify(cancelImmediately = true)
                    pushStatus("录音读取失败: $read")
                    break
                }
            }
        }
    }

    private fun failRecordingStart(message: String) {
        isStarting = false
        finishWhenStarted = false
        awaitingFinalResult = false
        runCatching { asr?.stop(true) }
        pushStatus(message)
    }

    private fun stopRecordingAndNotify(cancelImmediately: Boolean) {
        awaitingFinalResult = !cancelImmediately
        stopRecording()
        val stopResult = runCatching { asr?.stop(cancelImmediately) ?: -1 }.getOrDefault(-1)
        if (stopResult != 0) {
            awaitingFinalResult = false
            if (!cancelImmediately) pushStatus("结束识别失败: $stopResult")
        }
    }

    private fun stopRecording() {
        val wasActive = isRecording || isStarting
        isRecording = false
        isStarting = false
        postToMain { onAudioLevelChanged(0f) }
        audioRecord?.let { recorder ->
            audioRecord = null
            runCatching { recorder.stop() }
            recorder.release()
        }
        if (wasActive) postToMain { onListeningChanged(false) }
    }

    private fun calculateAudioLevel(buffer: ByteArray, length: Int): Float {
        var sum = 0.0
        var peak = 0
        var count = 0
        var index = 0
        while (index + 1 < length) {
            val sample = ((buffer[index + 1].toInt() shl 8) or (buffer[index].toInt() and 0xFF)).toShort()
            sum += sample * sample
            peak = max(peak, abs(sample.toInt()))
            count++
            index += 2
        }
        if (count == 0) {
            return 0f
        }
        val rms = sqrt(sum / count) / Short.MAX_VALUE
        val decibels = 20.0 * log10(max(rms, 0.0001))
        val rmsLevel = ((decibels + 45.0) / 45.0).coerceIn(0.0, 1.0)
        val peakLevel = (peak / Short.MAX_VALUE.toDouble()).coerceIn(0.0, 1.0)
        return max(rmsLevel, peakLevel * 0.75).toFloat().coerceIn(0f, 1f)
    }

    private fun sendRecognizedText() {
        if (isCanceled) {
            pushStatus("已取消")
            return
        }

        val text = synchronized(resultBuilder) { resultBuilder.toString().trim() }
        if (text.isBlank()) {
            pushStatus("没有识别到内容")
            return
        }

        pushStatus("识别完成，正在提交…")
        postToMain { onFinalTextReady(text) }
    }

    private fun pushStatus(value: String) {
        postToMain { onStatusChanged(value) }
    }

    private fun pushPreview(value: String) {
        postToMain { onPreviewChanged(value) }
    }

    private fun pushAudioLevel(value: Float) {
        postToMain { onAudioLevelChanged(value) }
    }

    private fun postToMain(block: () -> Unit) {
        mainHandler.post(block)
    }

    companion object {
        private const val SAMPLE_RATE = 16_000
        private const val CHUNK_SIZE = 1280
    }
}
