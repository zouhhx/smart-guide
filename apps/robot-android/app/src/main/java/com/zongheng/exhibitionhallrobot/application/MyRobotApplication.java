package com.zongheng.exhibitionhallrobot.application;

import android.app.Application;

import com.ainirobot.coreservice.client.ApiListener;
import com.ainirobot.coreservice.client.RobotApi;
import com.ainirobot.coreservice.client.speech.SkillApi;
import com.zongheng.exhibitionhallrobot.service.TcpService;
import com.zongheng.exhibitionhallrobot.util.LogUtils;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;

public class MyRobotApplication extends Application {
    private static final String TAG = "MyRobotApplication";
    private static final int MAX_PENDING_SPEECH = 3;
    private static final long PENDING_SPEECH_TTL_MS = 15_000L;
    private static MyRobotApplication instance;

    private SkillApi skillApi;
    private MyModuleCallback mModuleCallback;//NLP回调
    private boolean isSdkReady = false;
    private SdkReadyListener sdkReadyListener;

    // 新增：TcpService 引用
    private TcpService tcpService;
    private final Object pendingSpeechLock = new Object();
    private final ArrayDeque<PendingSpeech> pendingSpeech = new ArrayDeque<>();

    private static final class PendingSpeech {
        final String text;
        final long createdAt;

        PendingSpeech(String text, long createdAt) {
            this.text = text;
            this.createdAt = createdAt;
        }
    }

    public interface SdkReadyListener {
        void onSdkReady(SkillApi skillApi);
    }

    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        LogUtils.d(TAG, "展厅机器人应用启动...");
        if (isRobotEnvironment()) { // 判断是否是机器人
            init();
            try {
                initRobotApi();
            }
            catch (Exception ex) {
                LogUtils.d(TAG, "未检测到猎户星空SDK");
            }
        }
    }

    private void init() {
        this.mModuleCallback = new MyModuleCallback();
    }

    private boolean isRobotEnvironment() {
        // 检查特定类或系统属性
        try {
            Class.forName("com.ainirobot.coreservice.client.RobotApi");
            return true;
        } catch (ClassNotFoundException e) {
            return false;
        }
    }

    private void initRobotApi() {
        RobotApi.getInstance().connectServer(this, new ApiListener() {
            @Override
            public void handleApiDisabled() {
                LogUtils.d(TAG, "猎户星空SDK连接失败");
                isSdkReady = false;
            }

            @Override
            public void handleApiConnected() {
                LogUtils.d(TAG, "猎户星空SDK连接成功");
                addApiCallBack();
                initSkillApi();
            }

            @Override
            public void handleApiDisconnected() {
                LogUtils.d(TAG, "猎户星空SDK连接断开");
                isSdkReady = false;
                onRobotOsSuspend();
            }
        });
    }

    private void addApiCallBack() {
        RobotApi.getInstance().setCallback(mModuleCallback);
        LogUtils.d(TAG, "机器人语音请求回调注册成功");
    }

    private void initSkillApi() {
        try {
            skillApi = new SkillApi();
            skillApi.connectApi(this, new ApiListener() {
                @Override
                public void handleApiDisabled() {
                    LogUtils.d(TAG, "语音服务失效");
                }

                @Override
                public void handleApiConnected() {
                    LogUtils.d(TAG, "语音服务连接成功");
                    activateSpeechScene();
                    isSdkReady = true;
                    if (sdkReadyListener != null) {
                        sdkReadyListener.onSdkReady(skillApi);
                    }
                }

                @Override
                public void handleApiDisconnected() {
                    LogUtils.d(TAG, "语音服务断开");
                    isSdkReady = false;
                    onRobotOsSuspend();
                }
            });
        } catch (Throwable t) {
            LogUtils.e(TAG, "语音服务启动失败", t);
        }
    }

    private void activateSpeechScene() {
        if (skillApi == null) return;
        String appId = getPackageName();
        try {
            // Remove the temporary side-test scene left by earlier on-device diagnosis.
            // RobotOS keeps speech scenes independently from Android package lifecycle.
            skillApi.onDestroy(appId + ".speechtest");
            skillApi.onCreate(appId);
            skillApi.setVersion(appId, "1.0.0");
            skillApi.setPath(appId, "main");
            skillApi.onForeground(appId);
            skillApi.setASREnabled(true);
            skillApi.setRecognizable(true);
            LogUtils.d(TAG, "机器人语音场景已激活: " + appId + "/main");
        } catch (Throwable t) {
            LogUtils.e(TAG, "机器人语音场景激活失败", t);
        }
    }

    /** Reclaim the app speech scene after RobotOS temporarily suspends it. */
    public void recoverSpeechScene() {
        if (skillApi == null) return;
        try {
            activateSpeechScene();
            isSdkReady = true;
            if (sdkReadyListener != null) {
                sdkReadyListener.onSdkReady(skillApi);
            }
            LogUtils.d(TAG, "机器人语音场景恢复完成");
        } catch (Throwable t) {
            LogUtils.e(TAG, "机器人语音场景恢复失败", t);
        }
    }

    /** RobotOS/SystemActivity took control; teaching must never resume implicitly. */
    public void onRobotOsSuspend() {
        TcpService service = tcpService;
        if (service != null) service.onRobotOsSuspend();
    }

    public static MyRobotApplication getInstance() {
        return instance;
    }

    public SkillApi getSkillApi() {
        return skillApi;
    }

    public boolean isSdkReady() {
        return isSdkReady;
    }

    public void setSdkReadyListener(SdkReadyListener listener) {
        this.sdkReadyListener = listener;
        if (isSdkReady && listener != null) {
            listener.onSdkReady(this.skillApi);
        }
    }

    // ========== 新增：TcpService 管理 ==========
    public void setTcpService(TcpService service) {
        List<String> toFlush = new ArrayList<>();
        synchronized (pendingSpeechLock) {
            this.tcpService = service;
            if (service != null) {
                long now = System.currentTimeMillis();
                while (!pendingSpeech.isEmpty()) {
                    PendingSpeech item = pendingSpeech.removeFirst();
                    if (now - item.createdAt <= PENDING_SPEECH_TTL_MS) {
                        toFlush.add(item.text);
                    }
                }
            }
        }
        for (String text : toFlush) {
            service.submitSpeechResult(text);
        }
    }

    public TcpService getTcpService() {
        return tcpService;
    }

    public void submitSpeechResult(String text) {
        String normalized = text == null ? "" : text.trim();
        if (normalized.isEmpty()) return;

        TcpService service;
        synchronized (pendingSpeechLock) {
            service = tcpService;
            if (service == null) {
                while (pendingSpeech.size() >= MAX_PENDING_SPEECH) {
                    pendingSpeech.removeFirst();
                }
                pendingSpeech.addLast(new PendingSpeech(normalized, System.currentTimeMillis()));
                LogUtils.w(TAG, "TCP服务尚未启动，暂存语音结果");
                return;
            }
        }
        service.submitSpeechResult(normalized);
    }
    // ========================================

    /**
     * 清理所有资源，用于应用退出时调用
     */
    public void cleanupAllResources() {
        LogUtils.d(TAG, "清理所有资源...");

        // 首先关闭所有Activity
        com.zongheng.exhibitionhallrobot.util.ActivityManager activityManager =
                com.zongheng.exhibitionhallrobot.util.ActivityManager.getInstance();
        LogUtils.d(TAG, "当前活动Activity数量: " + activityManager.getActivityCount());
        activityManager.finishAllActivities();

        // 停止视频播放
        com.zongheng.exhibitionhallrobot.VideoPlayerActivity.stopCurrent();

        // 强制关闭VideoPlayerActivity（双重保险）
        com.zongheng.exhibitionhallrobot.VideoPlayerActivity.forceFinish();

        // 强制关闭ScreenDisplayActivity（双重保险）
        //com.zongheng.exhibitionhallrobot.ScreenDisplayActivity.forceFinishAll();

        // 清理 TcpService 引用
        synchronized (pendingSpeechLock) {
            tcpService = null;
            pendingSpeech.clear();
        }

        isSdkReady = false;
        sdkReadyListener = null;
        skillApi = null;
        mModuleCallback = null;

        LogUtils.d(TAG, "所有资源清理完成");
    }
}
