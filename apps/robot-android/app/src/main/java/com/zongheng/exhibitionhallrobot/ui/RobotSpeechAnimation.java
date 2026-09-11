package com.zongheng.exhibitionhallrobot.ui;

import android.content.res.AssetFileDescriptor;
import android.graphics.Matrix;
import android.graphics.SurfaceTexture;
import android.media.MediaPlayer;
import android.view.Surface;
import android.view.TextureView;
import com.zongheng.exhibitionhallrobot.R;

/** Silent, preloaded speaking loop underneath captions; never controls robot audio. */
public final class RobotSpeechAnimation implements TextureView.SurfaceTextureListener {
    private final TextureView view;
    private MediaPlayer player;
    private Surface surface;
    private boolean prepared, wanted, foreground;

    public RobotSpeechAnimation(TextureView view) {
        this.view = view;
        view.setOpaque(false);
        view.setAlpha(0f);
        view.setSurfaceTextureListener(this);
        if (view.isAvailable()) onSurfaceTextureAvailable(view.getSurfaceTexture(), view.getWidth(), view.getHeight());
    }

    public void update(boolean speaking, boolean visible) {
        wanted = speaking; foreground = visible;
        if (player == null || !prepared) return;
        try {
            if (wanted && foreground) {
                if (!player.isPlaying()) player.start();
                view.setAlpha(1f);
            } else {
                view.setAlpha(0f);
                if (player.isPlaying()) player.pause();
            }
        } catch (IllegalStateException ignored) { release(); }
    }

    @Override public void onSurfaceTextureAvailable(SurfaceTexture texture, int width, int height) {
        release();
        MediaPlayer next = new MediaPlayer(); player = next;
        surface = new Surface(texture);
        try (AssetFileDescriptor asset = view.getResources().openRawResourceFd(R.raw.tts_video)) {
            next.setSurface(surface);
            next.setVolume(0f, 0f);
            next.setLooping(true);
            next.setDataSource(asset.getFileDescriptor(), asset.getStartOffset(), asset.getLength());
            next.setOnPreparedListener(p -> {
                if (player != p) return;
                prepared = true; resize(); update(wanted, foreground);
            });
            next.setOnErrorListener((p, what, extra) -> { if (player == p) release(); return true; });
            next.prepareAsync();
        } catch (Exception ignored) { release(); }
    }

    private void resize() {
        if (!prepared || view.getWidth() == 0 || view.getHeight() == 0) return;
        float videoRatio = (float) player.getVideoWidth() / Math.max(1, player.getVideoHeight());
        float viewRatio = (float) view.getWidth() / view.getHeight();
        Matrix transform = new Matrix();
        transform.setScale(Math.max(1f, videoRatio / viewRatio), Math.max(1f, viewRatio / videoRatio),
                view.getWidth() / 2f, view.getHeight() / 2f);
        view.setTransform(transform);
    }
    @Override public void onSurfaceTextureSizeChanged(SurfaceTexture t, int w, int h) { resize(); }
    @Override public boolean onSurfaceTextureDestroyed(SurfaceTexture t) { release(); return true; }
    @Override public void onSurfaceTextureUpdated(SurfaceTexture t) { }
    public void release() {
        prepared = false; view.setAlpha(0f);
        if (player != null) { player.release(); player = null; }
        if (surface != null) { surface.release(); surface = null; }
    }
}
