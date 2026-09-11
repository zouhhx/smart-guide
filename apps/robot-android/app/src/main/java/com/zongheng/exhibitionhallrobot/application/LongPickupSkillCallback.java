package com.zongheng.exhibitionhallrobot.application;

import com.ainirobot.coreservice.client.speech.SkillCallback;
import com.zongheng.exhibitionhallrobot.service.RobotCapabilities;

/**
 * 语音识别-长拾音模式
 */
public class LongPickupSkillCallback extends SkillCallback {

    private RobotCapabilities.ListenCallback callback;

    private Long start, end;

    public LongPickupSkillCallback(RobotCapabilities.ListenCallback callback) {
        this.callback = callback;
    }

    @Override
    public void onSpeechParResult(String text) {
        if (callback != null) callback.onSpeechActivity(text);
    }

    @Override
    public void onStart() {
        start = android.os.SystemClock.elapsedRealtime();
        if (callback != null) callback.onReady();
    }

    @Override
    public void onStop() {
        end = android.os.SystemClock.elapsedRealtime();
    }

    @Override
    public void onVolumeChange(int volume) {

    }

    @Override
    public void onQueryEnded(int queryEndStatus) {
    }

    @Override
    public void onQueryAsrResult(String asrResult) {
        if (callback != null) {
            long startedAt = start != null ? start : android.os.SystemClock.elapsedRealtime();
            long endedAt = end != null ? end : android.os.SystemClock.elapsedRealtime();
            long duration = Math.max(0L, endedAt - startedAt);
            callback.onResult(asrResult, duration);
        }
    }
}
