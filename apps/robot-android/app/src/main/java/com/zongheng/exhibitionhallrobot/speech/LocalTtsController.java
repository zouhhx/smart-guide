package com.zongheng.exhibitionhallrobot.speech;

import android.content.Context;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioTrack;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import com.google.gson.JsonObject;
import com.zongheng.exhibitionhallrobot.BuildConfig;
import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

/** Selected 本地/LAN voice to PCM16LE/24 kHz/mono, one player and terminal callback per utterance. */
public final class LocalTtsController {
    public interface Listener {
        void onStart(long firstAudioMs);
        void onComplete();
        void onError(String reason);
    }
    private static final int SAMPLE_RATE = 24000;
    private final Context context;
    private final Consumer<String> logger;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final boolean silent;
    private volatile Session active;

    public LocalTtsController(Context context, Consumer<String> logger) { this(context, logger, false); }
    LocalTtsController(Context context, Consumer<String> logger, boolean silent) {
        this.context = context.getApplicationContext(); this.logger = logger; this.silent = silent;
    }
    public static boolean isEnabledMode() { return "local".equalsIgnoreCase(BuildConfig.TTS_MODE); }

    public synchronized void start(String text, Listener listener) {
        cancel();
        Session session = new Session(listener, LocalVoiceSettings.selected(context));
        active = session;
        new Thread(() -> session.run(text), "local-tts-stream").start();
    }
    public synchronized void cancel() {
        Session session = active;
        active = null;
        if (session != null) session.finish("本地 TTS已停止");
    }

    public boolean isAudioPlaying() {
        Session session = active;
        return session != null && session.firstAudio && !session.terminal.get();
    }

    private final class Session {
        final Listener listener;
        final String voice;
        final AtomicBoolean terminal = new AtomicBoolean();
        final Object resourceLock = new Object();
        final long requestedAt = SystemClock.elapsedRealtime();
        volatile HttpURLConnection connection;
        volatile AudioTrack track;
        final AudioManager manager = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
        final AudioManager.OnAudioFocusChangeListener focus = change -> { };
        boolean hasFocus;
        long frames;
        long bytesReceived;
        volatile boolean firstAudio;
        final Runnable watchdog = () -> finish("本地 TTS超时");
        Session(Listener listener, String voice) { this.listener = listener; this.voice = voice; }

        void run(String text) {
            main.postDelayed(watchdog, 90000);
            try {
                if (text == null || text.trim().isEmpty()) throw new IOException("本地 empty text");
                for (String part : LocalPcmStream.split(text)) streamLocal(part);
                if (!firstAudio) throw new IOException("本地 empty audio");
                while (!terminal.get()) {
                    AudioTrack player = track;
                    if (player == null) return;
                    if ((player.getPlaybackHeadPosition() & 0xffffffffL) >= frames) break;
                    Thread.sleep(10);
                }
                finish(null);
            } catch (Exception error) {
                // Do not leak HTTP bodies, credentials, or request headers.
                String reason = error instanceof IOException && error.getMessage() != null
                        && (error.getMessage().startsWith("本地 ") || error.getMessage().startsWith("TTS "))
                        ? error.getMessage() : "TTS失败: " + error.getClass().getSimpleName();
                finish(reason);
            } finally {
                main.removeCallbacks(watchdog);
            }
        }

        void streamLocal(String text) throws Exception {
            JsonObject body = new JsonObject();
            body.addProperty("input", text); body.addProperty("voice", voice);
            body.addProperty("response_format", "pcm");
            byte[] payload = body.toString().getBytes(StandardCharsets.UTF_8);
            for (int attempt=0; attempt<3; attempt++) {
                if (terminal.get()) throw new IOException("TTS cancelled");
                HttpURLConnection request = (HttpURLConnection) new URL("http://192.168.16.61:8769/v1/audio/speech").openConnection();
                synchronized(resourceLock) {
                    if(terminal.get()) { request.disconnect(); throw new IOException("TTS cancelled"); }
                    connection=request;
                }
                try {
                    request.setConnectTimeout(3000); request.setReadTimeout(15000);
                    request.setInstanceFollowRedirects(false); request.setRequestMethod("POST");
                    request.setRequestProperty("Content-Type","application/json; charset=utf-8");
                    request.setRequestProperty("Accept","application/octet-stream");
                    request.setDoOutput(true); request.setFixedLengthStreamingMode(payload.length);
                    logger.accept("LAN TTS请求 chars="+text.length()+" voice="+voice);
                    try(java.io.OutputStream output=request.getOutputStream()) { output.write(payload); }
                    int status=request.getResponseCode();
                    if(status==429 && attempt<2) {
                        request.disconnect();
                        for(int i=0;i<10 && !terminal.get();i++) Thread.sleep(100);
                        continue;
                    }
                    if(status!=200) throw new IOException("TTS LAN HTTP "+status);
                    if(!"pcm_s16le".equals(request.getHeaderField("X-Audio-Format"))
                            || !"24000".equals(request.getHeaderField("X-Sample-Rate"))
                            || !"1".equals(request.getHeaderField("X-Audio-Channels"))
                            || request.getContentType()==null || !request.getContentType().startsWith("application/octet-stream"))
                        throw new IOException("TTS unexpected PCM format");
                    try(java.io.InputStream input=request.getInputStream()) { LocalPcmStream.read(input,this::writePcm); }
                    return;
                } finally { request.disconnect(); }
            }
        }

        void writePcm(byte[] pcm) throws IOException {
            if (terminal.get()) throw new IOException("TTS cancelled");
            if (pcm.length == 0) return;
            if (pcm.length % 2 != 0) throw new IOException("本地 unaligned PCM16");
            bytesReceived += pcm.length;
            if (bytesReceived > SAMPLE_RATE * 2L * 180) throw new IOException("本地 audio limit");
            if (!firstAudio) {
                firstAudio = true;
                long latency = SystemClock.elapsedRealtime() - requestedAt;
                logger.accept("TTS起播准备=" + latency + "ms voice=" + voice);
                synchronized (resourceLock) {
                    if (terminal.get()) throw new IOException("本地 cancelled");
                    int minimum = AudioTrack.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT);
                    if (minimum <= 0) throw new IOException("本地 audio unavailable");
                    track = new AudioTrack(AudioManager.STREAM_MUSIC, SAMPLE_RATE, AudioFormat.CHANNEL_OUT_MONO,
                            AudioFormat.ENCODING_PCM_16BIT, Math.max(minimum, 4096), AudioTrack.MODE_STREAM);
                    if (track.getState() != AudioTrack.STATE_INITIALIZED) throw new IOException("本地 player init failed");
                    if (silent) track.setVolume(0f);
                    if (!silent && manager != null) {
                        hasFocus = manager.requestAudioFocus(focus, AudioManager.STREAM_MUSIC, AudioManager.AUDIOFOCUS_GAIN_TRANSIENT) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED;
                        if (!hasFocus) throw new IOException("本地 audio focus unavailable");
                    }
                    track.play();
                }
                main.post(() -> { if (!terminal.get()) listener.onStart(latency); });
            }
            int offset = 0;
            while (offset < pcm.length) {
                if (terminal.get()) throw new IOException("本地 cancelled");
                AudioTrack player = track;
                if (player == null) throw new IOException("本地 cancelled");
                int written = player.write(pcm, offset, Math.min(2048, pcm.length - offset));
                if (written <= 0) throw new IOException("本地 audio write failed");
                offset += written;
                frames += written / 2;
            }
        }

        void finish(String reason) {
            if (!terminal.compareAndSet(false, true)) return;
            main.removeCallbacks(watchdog);
            // Pause promptly on cancellation, then disconnect/release off the UI thread.
            synchronized (resourceLock) {
                if (track != null) try { track.pause(); track.flush(); } catch (RuntimeException ignored) { }
            }
            new Thread(() -> {
                HttpURLConnection request = connection;
                if (request != null) request.disconnect();
                synchronized (resourceLock) {
                    if (track != null) { try { track.release(); } catch (RuntimeException ignored) { } track = null; }
                    if (hasFocus && manager != null) manager.abandonAudioFocus(focus);
                }
            }, "local-tts-close").start();
            logger.accept(reason == null ? "本地 TTS完成 frames=" + frames : reason);
            main.post(() -> { if (reason == null) listener.onComplete(); else listener.onError(reason); });
        }
    }
}
