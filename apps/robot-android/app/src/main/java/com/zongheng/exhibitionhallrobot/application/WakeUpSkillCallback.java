package com.zongheng.exhibitionhallrobot.application;

import com.ainirobot.coreservice.client.speech.SkillCallback;
import com.zongheng.exhibitionhallrobot.service.RobotCapabilities;
import com.zongheng.exhibitionhallrobot.util.LogUtils;

public class WakeUpSkillCallback extends SkillCallback {
    private static final String TAG = "MySkillCallback";

    private SpeechResultListener listener;
    public interface SpeechResultListener {
        void onAsrResult(String asrResult);
        default void onPartial(String text) { }
        default void onQueryEnded(int status) { }

        void onStart();

        void onStop();
    }

    public void setListener(SpeechResultListener listener) {
        this.listener = listener;
    }

    @Override
    public void onSpeechParResult(String s) {
        if (listener != null) listener.onPartial(s);
    }

    @Override
    public void onStart() {
        if (listener != null) {
            // ASR/VAD start can be seconds after KWS. Actual wake events arrive
            // in MyModuleCallback as Definition.REQ_SPEECH_WAKEUP.
            listener.onStart();
        }
    }

    @Override
    public void onStop() {
        if (listener != null)
            listener.onStop();
    }

    @Override
    public void onVolumeChange(int volume) {
//        LogUtils.d(TAG, "音量变化: " + volume);
    }

    @Override
    public void onQueryEnded(int status) {
        if (listener != null) listener.onQueryEnded(status);
        // status: 0正常返回, 1other返回, 2噪音, 3超时, 4取消
        switch (status) {
            case 2:
                LogUtils.d(TAG, "机器人回调：识别到噪音");
                break;
            case 3:
                LogUtils.d(TAG, "机器人回调：识别超时");
                break;
            case 4:
                LogUtils.d(TAG, "机器人回调：识别取消");
                break;
        }
    }

    @Override
    public void onQueryAsrResult(String asrResult) {
        // 优化：只记录最终的语音识别结果
        LogUtils.d(TAG, "ASR最终结果: " + asrResult);
        if (listener != null) {
            listener.onAsrResult(asrResult);
        }
    }
}
