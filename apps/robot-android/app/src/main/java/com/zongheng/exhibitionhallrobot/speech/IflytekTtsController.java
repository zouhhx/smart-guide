package com.zongheng.exhibitionhallrobot.speech;

import android.content.Context;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioTrack;
import android.os.Process;

import com.iflytek.sparkchain.core.SparkChain;
import com.iflytek.sparkchain.core.tts.OnlineTTS;
import com.iflytek.sparkchain.core.tts.TTS;
import com.iflytek.sparkchain.core.tts.TTSCallbacks;
import com.zongheng.exhibitionhallrobot.BuildConfig;

import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Consumer;

/**
 * Low-latency streaming TTS backed by SparkChain OnlineTTS.
 *
 * Audio is returned as 16 kHz mono PCM and written to AudioTrack as soon as
 * the first chunk arrives. RobotCapabilities retains the existing RobotOS TTS
 * as a fallback when this capability is unavailable or fails before playback.
 */
public final class IflytekTtsController {
    private static final int SAMPLE_RATE = 16_000;
    private static final int PCM_ENCODING = AudioFormat.ENCODING_PCM_16BIT;
    private static final int CHANNEL_CONFIG = AudioFormat.CHANNEL_OUT_MONO;
    private static final long DRAIN_GRACE_MS = 1_500L;

    public interface Listener {
        void onStart(long firstAudioLatencyMs);

        void onComplete();

        void onError(String error, boolean audioStarted);
    }

    private final Context appContext;
    private final Consumer<String> logger;
    private final Object stateLock = new Object();
    private final AtomicInteger generation = new AtomicInteger();
    private final AtomicBoolean audioStarted = new AtomicBoolean(false);
    private final AtomicBoolean finishing = new AtomicBoolean(false);
    private final AtomicLong writtenFrames = new AtomicLong();

    private volatile Listener listener;
    private volatile long requestedAt;
    private OnlineTTS onlineTts;
    private AudioTrack audioTrack;
    private AudioManager audioManager;

    public IflytekTtsController(Context context, Consumer<String> logger) {
        appContext = context.getApplicationContext();
        this.logger = logger;
    }

    public static boolean isEnabledMode() {
        return "iflytek".equalsIgnoreCase(BuildConfig.TTS_MODE);
    }

    public boolean start(String text, Listener callback) {
        if (!isEnabledMode() || text == null || text.trim().isEmpty()) return false;
        if (SparkChain.getInst().getInitCode() != 0) {
            log("讯飞TTS不可用：SDK尚未初始化");
            return false;
        }

        cancel();
        int currentGeneration = generation.incrementAndGet();
        listener = callback;
        requestedAt = System.currentTimeMillis();
        audioStarted.set(false);
        finishing.set(false);
        writtenFrames.set(0L);

        AudioTrack track = createAudioTrack();
        if (track == null) {
            listener = null;
            return false;
        }

        try {
            OnlineTTS tts = new OnlineTTS("xiaoyan");
            tts.aue("raw");
            tts.auf("audio/L16;rate=16000");
            tts.speed(50);
            tts.pitch(50);
            tts.volume(80);
            tts.bgs(0);
            tts.registerCallbacks(createCallbacks(currentGeneration));

            synchronized (stateLock) {
                if (currentGeneration != generation.get()) {
                    releaseTrack(track);
                    return false;
                }
                audioTrack = track;
                onlineTts = tts;
            }

            requestAudioFocus();
            track.play();
            int result = tts.aRun(text, currentGeneration);
            if (result != 0) {
                fail(currentGeneration, "讯飞TTS启动失败: " + result);
                return true;
            }
            log("讯飞流式TTS请求已提交，文本长度=" + text.length());
            return true;
        } catch (Throwable error) {
            fail(currentGeneration, "讯飞TTS启动异常: " + safeMessage(error));
            return true;
        }
    }

    public void cancel() {
        generation.incrementAndGet();
        listener = null;
        finishing.set(true);
        OnlineTTS tts;
        AudioTrack track;
        synchronized (stateLock) {
            tts = onlineTts;
            onlineTts = null;
            track = audioTrack;
            audioTrack = null;
        }
        if (tts != null) {
            try {
                tts.stop();
            } catch (Throwable ignored) {
            }
        }
        releaseTrack(track);
        abandonAudioFocus();
    }

    public boolean isSpeaking() {
        return listener != null;
    }

    private TTSCallbacks createCallbacks(final int callbackGeneration) {
        return new TTSCallbacks() {
            @Override
            public void onResult(TTS.TTSResult result, Object userTag) {
                if (result == null || callbackGeneration != generation.get()) return;
                byte[] data = result.getData();
                int length = Math.min(result.getLen(), data == null ? 0 : data.length);
                if (length > 0) {
                    notifyAudioStarted();
                    AudioTrack track;
                    synchronized (stateLock) {
                        track = audioTrack;
                    }
                    if (track == null) return;
                    int offset = 0;
                    while (offset < length && callbackGeneration == generation.get()) {
                        int written = track.write(data, offset, length - offset);
                        if (written <= 0) {
                            fail(callbackGeneration, "讯飞TTS音频播放失败: " + written);
                            return;
                        }
                        offset += written;
                        writtenFrames.addAndGet(written / 2L);
                    }
                }
                if (result.getStatus() == 2) {
                    finishAfterDrain(callbackGeneration);
                }
            }

            @Override
            public void onError(TTS.TTSError error, Object userTag) {
                String detail = error == null
                        ? "未知错误"
                        : error.getCode() + " " + String.valueOf(error.getErrMsg());
                fail(callbackGeneration, "讯飞TTS合成失败: " + detail);
            }
        };
    }

    private void notifyAudioStarted() {
        if (!audioStarted.compareAndSet(false, true)) return;
        long latency = Math.max(0L, System.currentTimeMillis() - requestedAt);
        log("讯飞TTS首包延迟=" + latency + "ms");
        Listener callback = listener;
        if (callback != null) callback.onStart(latency);
    }

    private void finishAfterDrain(final int callbackGeneration) {
        if (!finishing.compareAndSet(false, true)) return;
        new Thread(() -> {
            Process.setThreadPriority(Process.THREAD_PRIORITY_AUDIO);
            long targetFrames = writtenFrames.get();
            long expectedMs = targetFrames * 1000L / SAMPLE_RATE;
            long deadline = System.currentTimeMillis() + expectedMs + DRAIN_GRACE_MS;
            while (callbackGeneration == generation.get() && System.currentTimeMillis() < deadline) {
                AudioTrack track;
                synchronized (stateLock) {
                    track = audioTrack;
                }
                if (track == null || track.getPlaybackHeadPosition() >= targetFrames) break;
                try {
                    Thread.sleep(20L);
                } catch (InterruptedException ignored) {
                    Thread.currentThread().interrupt();
                    break;
                }
            }
            complete(callbackGeneration);
        }, "iflytek-tts-drain").start();
    }

    private void complete(int callbackGeneration) {
        if (callbackGeneration != generation.get()) return;
        Listener callback = listener;
        listener = null;
        releaseCurrentResources();
        if (callback != null) callback.onComplete();
    }

    private void fail(int callbackGeneration, String message) {
        if (callbackGeneration != generation.get()) return;
        boolean hadAudio = audioStarted.get();
        Listener callback = listener;
        listener = null;
        log(message);
        releaseCurrentResources();
        if (callback != null) callback.onError(message, hadAudio);
    }

    private void releaseCurrentResources() {
        OnlineTTS tts;
        AudioTrack track;
        synchronized (stateLock) {
            tts = onlineTts;
            onlineTts = null;
            track = audioTrack;
            audioTrack = null;
        }
        if (tts != null) {
            try {
                tts.stop();
            } catch (Throwable ignored) {
            }
        }
        releaseTrack(track);
        abandonAudioFocus();
    }

    private AudioTrack createAudioTrack() {
        try {
            int minBuffer = AudioTrack.getMinBufferSize(SAMPLE_RATE, CHANNEL_CONFIG, PCM_ENCODING);
            if (minBuffer <= 0) return null;
            AudioTrack track = new AudioTrack(
                    AudioManager.STREAM_MUSIC,
                    SAMPLE_RATE,
                    CHANNEL_CONFIG,
                    PCM_ENCODING,
                    Math.max(minBuffer, 4096),
                    AudioTrack.MODE_STREAM
            );
            if (track.getState() != AudioTrack.STATE_INITIALIZED) {
                track.release();
                return null;
            }
            return track;
        } catch (Throwable error) {
            log("讯飞TTS播放器初始化失败: " + safeMessage(error));
            return null;
        }
    }

    private void requestAudioFocus() {
        audioManager = (AudioManager) appContext.getSystemService(Context.AUDIO_SERVICE);
        if (audioManager != null) {
            audioManager.requestAudioFocus(null, AudioManager.STREAM_MUSIC, AudioManager.AUDIOFOCUS_GAIN_TRANSIENT);
        }
    }

    private void abandonAudioFocus() {
        AudioManager manager = audioManager;
        audioManager = null;
        if (manager != null) manager.abandonAudioFocus(null);
    }

    private void releaseTrack(AudioTrack track) {
        if (track == null) return;
        try {
            track.pause();
            track.flush();
            track.stop();
        } catch (Throwable ignored) {
        }
        try {
            track.release();
        } catch (Throwable ignored) {
        }
    }

    private void log(String message) {
        if (logger != null) logger.accept(message);
    }

    private static String safeMessage(Throwable error) {
        String message = error == null ? null : error.getMessage();
        return message == null || message.trim().isEmpty()
                ? (error == null ? "未知错误" : error.getClass().getSimpleName())
                : message;
    }
}
