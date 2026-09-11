package com.zongheng.exhibitionhallrobot.speech;

import android.content.Context;
import android.media.AudioAttributes;
import android.media.SoundPool;
import java.util.function.Consumer;
import java.util.HashSet;
import java.util.Set;

/** Locally cached 本地 “来了。” for each preset; never owns ASR or a V5 TTS receipt. */
public final class WakeupResponsePlayer {
    private final SoundPool pool;
    private final Consumer<String> logger;
    private final Context context;
    private final Set<Integer> readySamples = new HashSet<>();
    private boolean released;
    private final int[] samples = new int[LocalVoiceSettings.labels().length];
    private int stream;

    public WakeupResponsePlayer(Context context, Consumer<String> logger) {
        this.logger = logger;
        this.context = context.getApplicationContext();
        pool = new SoundPool.Builder().setMaxStreams(1).setAudioAttributes(new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build()).build();
        pool.setOnLoadCompleteListener((soundPool, sampleId, status) -> {
            synchronized (WakeupResponsePlayer.this) {
                if (!released && status == 0) readySamples.add(sampleId);
            }
            logger.accept("本地唤醒应答缓存 sample=" + sampleId + (status == 0 ? " ready" : " load_failed"));
        });
        for (int i = 0; i < samples.length; i++) {
            int resource = LocalVoiceSettings.wakeupResource(i);
            if (resource != 0) samples[i] = pool.load(context, resource, 1);
        }
    }
    public synchronized void play() {
        int sample = samples[LocalVoiceSettings.selectedIndex(context)];
        if (released || !readySamples.contains(sample)) { logger.accept("本地唤醒应答缓存尚未就绪"); return; }
        stop();
        stream = pool.play(sample, 1f, 1f, 1, 0, 1f);
        logger.accept("本地唤醒应答 来了 stream=" + stream);
    }
    public synchronized void stop() {
        if (!released && stream != 0) pool.stop(stream);
        stream = 0;
    }
    public synchronized void release() {
        if (released) return;
        stop(); released = true; pool.release();
    }
}
