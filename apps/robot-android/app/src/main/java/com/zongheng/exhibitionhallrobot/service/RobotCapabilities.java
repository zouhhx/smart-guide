
package com.zongheng.exhibitionhallrobot.service;

import android.content.Context;
import android.content.Intent;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioManager;
import android.media.MediaPlayer;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.os.Message;
import android.os.SystemClock;
import android.speech.tts.TextToSpeech;
import android.text.TextUtils;
import android.util.Log;

import androidx.annotation.NonNull;
import androidx.annotation.RequiresApi;

import com.ainirobot.coreservice.client.Definition;
import com.ainirobot.coreservice.client.RobotApi;
import com.ainirobot.coreservice.client.listener.ActionListener;
import com.ainirobot.coreservice.client.listener.CommandListener;
import com.ainirobot.coreservice.client.listener.Person;
import com.ainirobot.coreservice.client.listener.TextListener;
import com.ainirobot.coreservice.client.person.PersonApi;
import com.ainirobot.coreservice.client.person.PersonListener;
import com.ainirobot.coreservice.client.person.PersonUtils;
import com.ainirobot.coreservice.client.robotsetting.RobotSettingApi;
import com.ainirobot.coreservice.client.speech.SkillApi;
import com.ainirobot.coreservice.client.speech.entity.TTSEntity;
import com.zongheng.exhibitionhallrobot.AppConstants;
import com.zongheng.exhibitionhallrobot.CustomPhotoCaptureActivity;
import com.zongheng.exhibitionhallrobot.R;
import com.zongheng.exhibitionhallrobot.VideoPlayerActivity;
import com.zongheng.exhibitionhallrobot.application.LongPickupSkillCallback;
import com.zongheng.exhibitionhallrobot.application.MyRobotApplication;
import com.zongheng.exhibitionhallrobot.application.WakeUpSkillCallback;
import com.zongheng.exhibitionhallrobot.config.RobotConfig;
import com.zongheng.exhibitionhallrobot.enums.LogLevel;
import com.zongheng.exhibitionhallrobot.speech.IflytekAsrController;
import com.zongheng.exhibitionhallrobot.speech.IflytekTtsController;
import com.zongheng.exhibitionhallrobot.speech.LocalTtsController;
import com.zongheng.exhibitionhallrobot.speech.WakeupResponsePlayer;
import java.util.concurrent.atomic.AtomicInteger;
import com.zongheng.exhibitionhallrobot.util.ReqIdUtils;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.lang.ref.WeakReference;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Random;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.locks.ReadWriteLock;
import java.util.concurrent.locks.ReentrantLock;
import java.util.concurrent.locks.ReentrantReadWriteLock;
import java.util.function.Consumer;

public class RobotCapabilities {
    private static final String TAG = "RobotCapabilities";

    private static final String TASK_NAVIGATE = "navigate";
    private static final String TASK_SPEAK = "speak";
    private static final String TASK_LISTEN = "listen";
    private static final String TASK_SHOW = "show";
    private static final String TASK_FOCUS_FOLLOW = "focus_follow";
    private static final String TASK_PLAY_MUSIC = "play_music";
    private static final String TASK_PHOTO = "photo";

    private static final String PRIORITY_INTERRUPT = "interrupt";
    private static final String NAV_RESPONSE_SUCCESS = "true";

    private final WeakReference<Context> contextRef;
    private final Context applicationContext;
    private final Consumer<String> logCallback;

    private ListenCallback listenCallback;

    private volatile boolean isOnRobot;
    private SkillApi skillApi;
    private WakeUpSkillCallback wakeUpSkillCallback;
    private LongPickupSkillCallback longPickupSkillCallback;
    private boolean wakeUpCallbackRegistered = false;
    private boolean longPickupCallbackRegistered = false;
    private final Object speechCallbackLock = new Object();
    private final IflytekAsrController iflytekAsrController;
    private final IflytekTtsController iflytekTtsController;
    private final LocalTtsController localTtsController;
    private final WakeupResponsePlayer wakeupResponsePlayer;
    private final AtomicInteger localTtsGeneration = new AtomicInteger();

    private TextToSpeech mockTts;
    private volatile boolean mockTtsReady = false;

    private final AtomicBoolean isSpeaking = new AtomicBoolean(false);
    private final AtomicBoolean isVideoPlaying = new AtomicBoolean(false);
    private final AtomicBoolean isListening = new AtomicBoolean(false);
    private final AtomicBoolean isMusicPlaying = new AtomicBoolean(false);

    private final LinkedBlockingQueue<TtsRequest> ttsQueue = new LinkedBlockingQueue<>();
    private final ReentrantLock ttsLock = new ReentrantLock();
    private final ReadWriteLock stateLock = new ReentrantReadWriteLock();
    private final AtomicLong lastCommandTimestamp = new AtomicLong(0);

    private static final long COMMAND_DEBOUNCE_MS = 300;
    private static final int MAX_TTS_QUEUE_SIZE = 10;
    private static final int AUTO_LISTEN_TIMEOUT_SEC = 5;

    private static final int MSG_PROCESS_TTS = 1;
    private static final int MSG_LISTEN_TIMEOUT = 2;
    private static final int MSG_TTS_TIMEOUT = 3;
    private static final int MSG_RE_REGISTER_FACE = 4;
    private static final int MSG_FOLLOW_MONITOR = 5;
    private static final int MSG_MOCK_NAVIGATE = 6;
    private static final int MSG_MOCK_POINT_LIST = 7;
    private static final int MSG_MOCK_SPEAK = 8;

    private static final long MOCK_NAVIGATE_DELAY_MS = 2000;
    private static final long MOCK_POINT_LIST_DELAY_MS = 500;
    private static final long MOCK_SPEAK_DELAY_PER_CHAR_MS = 100;

    private final Handler mainHandler;
    private HandlerThread faceHandlerThread;
    private Handler faceHandler;

    private final Map<String, PointInfo> pointMap = Collections.synchronizedMap(new HashMap<>());
    private volatile boolean isPointListLoaded = false;
    private volatile String currentLocation = "充电位";
    private volatile int currentBattery = 100;

    private int listenTimeout;
    private String listenLanguage;

    private volatile String currentTask = null;
    private volatile String currentStatus = STATUS_IDLE;
    private volatile long lastPhotoTakenAt = 0;  // 拍照后15s内禁止 startVideoLoop

    public static final String STATUS_IDLE = "idle";
    public static final String STATUS_NAVIGATING = "navigating";
    public static final String STATUS_SPEAKING = "speaking";
    public static final String STATUS_LISTENING = "listening";
    public static final String STATUS_SHOWING = "showing";
    public static final String STATUS_CHARGING = "charging";
    public static final String STATUS_ERROR = "error";

    private String robotId;

    private volatile String lastReportedUserId = null;
    private volatile String lastReportedUserName = null;
    private volatile int lastReportedPersonCount = -1;
    private volatile long lastReportTime = 0;

    // 优化：添加人员数量上报的时间戳，防止频繁上报
    private volatile long lastPersonCountReportTime = 0;


    // [v6.7] 人员数量上报间隔（毫秒）- 降低以支持接待点主动问询
    private static final long PERSON_COUNT_REPORT_INTERVAL_MS = 5000;

    // [v7.1] 已知/陌生人脸识别事件上报间隔（毫秒）- 降到1秒,实时响应
    private static final long FACE_RECOGNIZE_REPORT_INTERVAL_MS = 1000;

    // [v8.2] 主动问候节奏（毫秒）：产品要求每10秒可再次触发主动打招呼
    private static final long SAME_FACE_REPORT_INTERVAL_MS = 10000;

    // [v7.1] 未知用户上报间隔（毫秒）- 降到1秒, 让接待问询即时响应
    private static final long UNKNOWN_FACE_REPORT_INTERVAL_MS = 15000;

    // 优化：记录最后一次上报的未知用户时间，用于未知用户的特殊防抖
    private volatile long lastUnknownFaceReportTime = 0;

    // 优化：记录最后一次上报的已知用户时间，用于已知用户的防抖
    private volatile long lastKnownFaceReportTime = 0;
    private final AtomicLong faceProcessingSuppressedUntil = new AtomicLong(0L);

    public static class PointInfo {
        public String id;
        public String name;
        public double x;
        public double y;
        public double theta;
        public int status;

        public PointInfo(String id, String name, double x, double y, double theta, int status) {
            this.id = id;
            this.name = name;
            this.x = x;
            this.y = y;
            this.theta = theta;
            this.status = status;
        }
    }

    public interface NavigateCallback {
        void onSuccess(String pointName);

        void onFailed(String error);
    }

    public interface SpeakCallback {
        void onSuccess();

        void onFailed(String error);
    }

    public interface MusicCallback {
        void onSuccess();

        void onFailed(String error);

        void onComplete();
    }

    public interface ListenCallback {
        default void onReady() { }
        default void onSpeechActivity(String partial) { }
        default void onTranscribing() { }
        default void onSpeechStarted() { }
        default void onReplayRequested() { }
        void onResult(String text, long duration);

        void onTimeout(String reason);

        void onError(String error);
    }

    public interface VoiceCommandCallback {
        void onVoiceCommand(String command);
    }

    private volatile VoiceCommandCallback voiceCommandCallback;
    private volatile Consumer<String> speechPreviewCallback;
    public void setSpeechPreviewCallback(Consumer<String> callback) { speechPreviewCallback = callback; }
    private void previewSpeech(String text) {
        Consumer<String> callback = speechPreviewCallback;
        if (callback != null) callback.accept(text == null ? "" : text);
    }

    public interface VideoCloseCallback {
        void onVideoClosed();
    }

    private volatile VideoCloseCallback videoCloseCallback;

    public interface PointListCallback {
        void onSuccess(List<PointInfo> points);

        void onFailed(String error);
    }

    public interface ShowCallback {
        void onSuccess();

        void onFailure(String reason);
    }

    public interface PhotoCallback {
        void onSuccess(String photoPath, Uri photoUri);

        void onFailed(String error);
    }

    private volatile MusicCallback musicCallback;

    private static final ConcurrentHashMap<String, PhotoCallback> PHOTO_CALLBACKS = new ConcurrentHashMap<>();

    // 保持MediaPlayer引用，防止被垃圾回收
    private MediaPlayer currentMusicPlayer;
    
    // 音频焦点管理
    private AudioManager audioManager;
    private AudioFocusRequest audioFocusRequest;
    private boolean hasAudioFocus = false;

    public interface FaceListener {
        void onFaceRecognized(String robotId, String userId, String name, double confidence);

        void updateFacePhoto(String robotId, String userId, String name, String base64);

        void updatePersonCount(String robotId, int personCount);
        
        // [v7.0] 上报完整Person信息(含personId/remoteUserId/isRegistered等)
        void onPersonDetail(String robotId, String personDataJson);
    }

    private volatile FaceListener faceListener;

    public void setFaceListener(FaceListener listener) {
        this.faceListener = listener;
    }

    private static class TtsRequest {
        int seq;
        String text;
        String priority;
        SpeakCallback callback;
        long timestamp;
        int requestId;

        TtsRequest(int seq, String text, String priority, SpeakCallback callback) {
            this.seq = seq;
            this.text = text;
            this.priority = priority;
            this.callback = callback;
            this.timestamp = System.currentTimeMillis();
            this.requestId = generateRequestId(text, priority);
        }

        private int generateRequestId(String text, String priority) {
            return (text != null ? text.hashCode() : 0) ^
                    (priority != null ? priority.hashCode() : 0) ^
                    (int) (System.currentTimeMillis() & 0xFFFF);
        }

        boolean isDuplicateOf(TtsRequest other) {
            if (other == null) return false;
            return this.requestId == other.requestId &&
                    Math.abs(this.timestamp - other.timestamp) < COMMAND_DEBOUNCE_MS;
        }
    }

    private static class MockNavigateData {
        String pointName;
        NavigateCallback callback;

        MockNavigateData(String pointName, NavigateCallback callback) {
            this.pointName = pointName;
            this.callback = callback;
        }
    }

    private volatile long listenStartTime = 0;
    private volatile int totalListenTime = 0;

    private final PersonListener personListener = new PersonListener() {
        @Override
        public void personChanged() {
            if (faceHandler != null) {
                faceHandler.post(() -> processPersonChange());
            }
        }
    };

    private volatile boolean isPersonListenerRegistered = false;
    private volatile long lastFaceProcessTime = 0;

    private volatile int currentFaceId = -1;
    private volatile int currentReqId = -1;
    private volatile boolean isFocusFollow = false;

    private volatile SpeakCallback pendingSpeakCallback;
    private volatile PointListCallback pendingPointListCallback;
    private volatile int currentTtsTextLength = 0;

    public RobotCapabilities(Context context, Consumer<String> logCallback) {
        this.contextRef = new WeakReference<>(context);
        this.applicationContext = context.getApplicationContext();
        this.logCallback = logCallback;
        this.mainHandler = new MainHandler(this);
        this.iflytekAsrController = new IflytekAsrController(
                applicationContext,
                message -> log(LogLevel.INFO, message)
        );
        this.iflytekTtsController = new IflytekTtsController(
                applicationContext,
                message -> log(LogLevel.INFO, message)
        );
        this.iflytekAsrController.warmUp();
        this.localTtsController = new LocalTtsController(applicationContext,
                message -> log(LogLevel.INFO, message));

        this.wakeupResponsePlayer = new WakeupResponsePlayer(applicationContext,
                message -> log(LogLevel.INFO, message));

        // 使用独立线程处理人脸识别，避免阻塞主线程
        faceHandlerThread = new HandlerThread("FaceHandler");
        faceHandlerThread.start();
        faceHandler = new Handler(faceHandlerThread.getLooper());

        MyRobotApplication app = MyRobotApplication.getInstance();
        if (app != null) {
            isOnRobot = app.isSdkReady();
            log(LogLevel.INFO, isOnRobot ? "运行环境: 机器人" : "等待机器人语音SDK连接");
            initRobotSdk();
        } else {
            isOnRobot = false;
            log(LogLevel.INFO, "运行环境: 手机模拟");
            initMockMode();
        }
    }

    private static class MainHandler extends Handler {
        private final WeakReference<RobotCapabilities> capabilitiesRef;

        MainHandler(RobotCapabilities capabilities) {
            super(Looper.getMainLooper());
            this.capabilitiesRef = new WeakReference<>(capabilities);
        }

        @Override
        public void handleMessage(@NonNull Message msg) {
            RobotCapabilities capabilities = capabilitiesRef.get();
            if (capabilities != null) {
                switch (msg.what) {
                    case MSG_PROCESS_TTS:
                        capabilities.processNextTtsInternal();
                        break;
                    case MSG_LISTEN_TIMEOUT:
                        capabilities.handleListenTimeout();
                        break;
                    case MSG_TTS_TIMEOUT:
                        capabilities.handleTtsTimeout();
                        break;
                    case MSG_MOCK_NAVIGATE:
                        if (msg.obj instanceof MockNavigateData) {
                            capabilities.handleMockNavigate((MockNavigateData) msg.obj);
                        }
                        break;
                    case MSG_MOCK_POINT_LIST:
                        capabilities.handleMockPointList();
                        break;
                    case MSG_MOCK_SPEAK:
                        capabilities.handleMockSpeakComplete();
                        break;
                }
            }
        }
    }

    // ======================= 语音识别（优化唤醒响应） =======================
    private void unregisterWakeupCallbackLocked() {
        if (skillApi == null || wakeUpSkillCallback == null || !wakeUpCallbackRegistered) return;
        try {
            skillApi.unregisterCallBack(wakeUpSkillCallback);
            wakeUpCallbackRegistered = false;
        } catch (Exception e) {
            log(LogLevel.WARN, "注销唤醒回调失败: " + e.getMessage());
        }
    }

    private void registerWakeupCallbackLocked() {
        if (skillApi == null || wakeUpSkillCallback == null || wakeUpCallbackRegistered) return;
        skillApi.registerCallBack(wakeUpSkillCallback);
        wakeUpCallbackRegistered = true;
    }

    private void unregisterLongPickupCallbackLocked() {
        if (skillApi == null || longPickupSkillCallback == null || !longPickupCallbackRegistered) return;
        try {
            skillApi.unregisterCallBack(longPickupSkillCallback);
            longPickupCallbackRegistered = false;
        } catch (Exception e) {
            log(LogLevel.WARN, "注销长拾音回调失败: " + e.getMessage());
        }
    }

    public void startListening(int totalTimeSec, String language, ListenCallback callback) {
        startListeningInternal(totalTimeSec, language, callback, false);
    }

    public void startTeachingListening(int totalTimeSec, ListenCallback callback) {
        startListeningInternal(totalTimeSec, "zh", callback, true);
    }

    public void finishTeachingTranscription(IflytekAsrController.Listener listener) {
        iflytekAsrController.finish(listener);
        listenCallback=null;
        cancelListenTimeout();
        isListening.set(false);
        clearTask();
    }

    private void startListeningInternal(int totalTimeSec, String language, ListenCallback callback, boolean teaching) {
        // 立即停止当前TTS（同步）
        stopTTSImmediately();
        cancelCurrentTask();
        setCurrentStatus(STATUS_LISTENING, TASK_LISTEN);
        this.listenCallback = callback;
        this.totalListenTime = totalTimeSec;
        this.listenStartTime = System.currentTimeMillis();

        log(LogLevel.INFO, "开始收音: 超时时间=" + totalTimeSec + "秒, 语言=" + language);
        if (!isOnRobot || skillApi == null) {
            log(LogLevel.ERROR, "语音服务未就绪，无法启动收音");
            if (callback != null) callback.onError("语音服务未就绪");
            cleanupListenState();
            return;
        }

        cancelListenTimeout();

        startIflytekListening(teaching, callback);
    }

    private void startIflytekListening(boolean teaching, final ListenCallback owner) {
        if (!iflytekAsrController.hasCredentials()) {
            owner.onError("讯飞ASR凭据未配置"); cleanupListenState(); return;
        }
        try {
            synchronized(speechCallbackLock) { unregisterLongPickupCallbackLocked(); unregisterWakeupCallbackLocked(); }
            skillApi.setRecognizeMode(false); skillApi.setRecognizable(false); skillApi.setASREnabled(false);
        } catch(Exception error) { log(LogLevel.WARN,"暂停原机识别失败"); }
        isListening.set(true);
        if(!teaching) scheduleListenTimeout(totalListenTime);
        IflytekAsrController.Listener listener = new IflytekAsrController.Listener() {
            boolean speechSeen;
            private boolean current(){return listenCallback==owner;}
            public void onReady(){if(current())owner.onReady();}
            public void onPartial(String text){
                if(!current())return;
                previewSpeech(text);owner.onSpeechActivity(text);
                if(!speechSeen && text!=null&&!text.trim().isEmpty()){
                    speechSeen=true;owner.onSpeechStarted();
                    if(!teaching){cancelListenTimeout();scheduleListenTimeout(30);}
                }
                if(teaching && TeachingSessionController.hasReplayEnding(text))owner.onReplayRequested();
            }
            public void onResult(String text,long duration){
                if(!current())return;
                if(!teaching)cleanupListenState();
                owner.onResult(text,duration);
            }
            public void onNoSpeech(){if(current()){cleanupListenState();owner.onTimeout("未识别到有效语音");}}
            public void onError(String error){if(current()){cleanupListenState();owner.onError(error);}}
        };
        if(teaching)iflytekAsrController.startTeaching(listener);else iflytekAsrController.startListening(listener);
    }

    private void cleanupListenState() {
        listenCallback = null;
        cancelListenTimeout();
        iflytekAsrController.cancel();
        switchToWakeupMode(); // 自动恢复唤醒词模式
        clearTask();
        isListening.set(false);
    }

    public void switchToWakeupMode() {
        if (skillApi == null) return;
        try {
            iflytekAsrController.cancel();
                synchronized (speechCallbackLock) {
                unregisterLongPickupCallbackLocked();
                skillApi.setASREnabled(true);
                skillApi.setRecognizable(true);
                skillApi.setRecognizeMode(false);
                registerWakeupCallbackLocked();
            }
            isListening.set(false);
            log(LogLevel.INFO, "已切换回唤醒词模式");
        } catch (Exception e) {
            log(LogLevel.ERROR, "切换回唤醒模式失败: " + e.getMessage());
        }
    }

    public void startAutoListening() {
        log(LogLevel.INFO, "自动收音模式启动（超时" + AUTO_LISTEN_TIMEOUT_SEC + "秒，长拾音）");
        if (!isOnRobot || skillApi == null) {
            log(LogLevel.ERROR, "语音服务未就绪");
            return;
        }

        // 立即停止当前TTS
        stopTTSImmediately();

        ListenCallback autoListenCallback = new ListenCallback() {
            @Override
            public void onResult(String text, long duration) {
                log(LogLevel.INFO, "自动收音识别结果: " + text);
                VoiceCommandCallback cb = voiceCommandCallback;
                if (cb != null) cb.onVoiceCommand(text);
            }

            @Override
            public void onTimeout(String reason) {
                log(LogLevel.DEBUG, "自动收音超时: " + reason);
            }

            @Override
            public void onError(String error) {
                log(LogLevel.WARN, "自动收音错误: " + error);
            }
        };

        // 首次唤醒后最多等待 30 秒；用户说出一句后立即交给 V5，避免持续开麦收进本机 TTS。
        startListening(AUTO_LISTEN_TIMEOUT_SEC, "zh", autoListenCallback);
        log(LogLevel.INFO, "自动收音已启动（超时" + AUTO_LISTEN_TIMEOUT_SEC + "秒）");
    }

    // ======================= TTS 播报（优化队列与超时） =======================
    public void speak(int seq, String text, String priority, SpeakCallback callback) {
        wakeupResponsePlayer.stop();
        if (text == null || text.isEmpty()) {
            log(LogLevel.WARN, "TTS文本为空");
            if (callback != null) callback.onFailed("文本为空");
            return;
        }

        pauseFaceProcessing(Math.max(25_000L, text.length() * 350L));

        // Never leave the cloud microphone open while the robot is speaking.
        if (iflytekAsrController.isListening()) {
            log(LogLevel.INFO, "TTS开始前停止讯飞ASR收音");
            stopListening();
        }

        ttsLock.lock();
        try {
            TtsRequest newRequest = new TtsRequest(seq, text, priority, callback);

            // 去重
            if (isDuplicateCommand(newRequest)) {
                log(LogLevel.INFO, "重复指令忽略: " + text);
                if (callback != null) callback.onSuccess();
                return;
            }

            if (PRIORITY_INTERRUPT.equalsIgnoreCase(priority)) {
                log(LogLevel.INFO, "高优先级中断，清空队列并停止当前播放");
                ttsQueue.clear();
                stopTTSImmediately();      // 立即停止SDK播放
                isSpeaking.set(false);
                currentTtsTextLength = 0;
                pendingSpeakCallback = null;
                cancelTtsTimeout();
                // 清除当前任务状态
                if (TASK_SPEAK.equals(currentTask)) clearTask();
            } else {
                if (ttsQueue.size() >= MAX_TTS_QUEUE_SIZE) {
                    log(LogLevel.WARN, "队列已满，丢弃最旧任务");
                    ttsQueue.poll();
                }
            }

            if (!ttsQueue.offer(newRequest)) {
                log(LogLevel.ERROR, "加入队列失败");
                if (callback != null) callback.onFailed("队列失败");
                return;
            }

            log(LogLevel.INFO, "TTS入队 [seq=" + seq + "] 优先级=" + priority + " 队列大小=" + ttsQueue.size() + " isSpeaking=" + isSpeaking.get());
            if (!isSpeaking.get()) {
                processNextTts();
            }
        } finally {
            ttsLock.unlock();
        }
    }

    private boolean isDuplicateCommand(TtsRequest newRequest) {
        long now = System.currentTimeMillis();
        if (now - lastCommandTimestamp.get() < COMMAND_DEBOUNCE_MS) {
            for (TtsRequest existing : ttsQueue) {
                if (newRequest.isDuplicateOf(existing)) return true;
            }
        }
        return false;
    }

    private void processNextTts() {
        mainHandler.sendEmptyMessage(MSG_PROCESS_TTS);
    }

    private void processNextTtsInternal() {
        ttsLock.lock();
        try {
            if (ttsQueue.isEmpty()) {
                isSpeaking.set(false);
                currentTtsTextLength = 0;
                return;
            }
            if (isSpeaking.get()) {
                log(LogLevel.WARN, "当前正在播放，等待完成，队列大小=" + ttsQueue.size());
                return;
            }
            TtsRequest req = ttsQueue.poll();
            if (req == null) return;

            lastCommandTimestamp.set(req.timestamp);
            isSpeaking.set(true);
            currentTtsTextLength = req.text.length();
            log(LogLevel.INFO, "开始播放TTS [seq=" + req.seq + "] 文本长度=" + currentTtsTextLength);

            if (isOnRobot) {
                speakOnRobot(req.seq, req.text, req.callback);
            } else {
                speakOnMock(req.text, req.callback);
            }
        } finally {
            ttsLock.unlock();
        }
    }

    private void speakOnRobot(int seq, String text, SpeakCallback callback) {
        if (LocalTtsController.isEnabledMode()) {
            speakOnLocal(seq, text, callback);
            return;
        }
        if (!IflytekTtsController.isEnabledMode()) {
            speakOnRobotBuiltIn(seq, text, callback);
            return;
        }

        setCurrentStatus(STATUS_SPEAKING, TASK_SPEAK);
        startTtsTimeout(text.length());
        boolean accepted = iflytekTtsController.start(text, new IflytekTtsController.Listener() {
            @Override
            public void onStart(long firstAudioLatencyMs) {
                mainHandler.post(() -> {
                    log(LogLevel.INFO, "讯飞TTS开始 [seq=" + seq + "] 首包=" + firstAudioLatencyMs + "ms");
                    isSpeaking.set(true);
                });
            }

            @Override
            public void onComplete() {
                mainHandler.post(() -> {
                    log(LogLevel.INFO, "讯飞TTS完成 [seq=" + seq + "]");
                    isSpeaking.set(false);
                    currentTtsTextLength = 0;
                    cancelTtsTimeout();
                    clearTask();
                    notifyVoiceIntroductionCompleted();
                    if (callback != null) callback.onSuccess();
                    mainHandler.postDelayed(RobotCapabilities.this::processNextTts, 100);
                });
            }

            @Override
            public void onError(String error, boolean audioStarted) {
                mainHandler.post(() -> {
                    if (!audioStarted) {
                        log(LogLevel.WARN, error + "，回退RobotOS TTS");
                        speakOnRobotBuiltIn(seq, text, callback);
                        return;
                    }
                    log(LogLevel.ERROR, error);
                    isSpeaking.set(false);
                    currentTtsTextLength = 0;
                    cancelTtsTimeout();
                    clearTask();
                    if (callback != null) callback.onFailed(error);
                    processNextTts();
                });
            }
        });
        if (!accepted) {
            log(LogLevel.WARN, "讯飞TTS当前不可用，使用RobotOS TTS");
            speakOnRobotBuiltIn(seq, text, callback);
        }
    }

    public boolean isVoiceAudioPlaying() {
        return LocalTtsController.isEnabledMode() ? localTtsController.isAudioPlaying() : isSpeaking();
    }

    private void speakOnLocal(int seq, String text, SpeakCallback callback) {
        final int generation = localTtsGeneration.incrementAndGet();
        // Custom AudioTrack playback does not close RobotOS' follow-up ASR window.
        // Return to wake-word mode before playback so the robot does not transcribe itself.
        stopListening();
        setCurrentStatus(STATUS_SPEAKING, TASK_SPEAK);
        startTtsTimeout(text.length());
        localTtsController.start(text, new LocalTtsController.Listener() {
            @Override public void onStart(long latencyMs) {
                if (generation != localTtsGeneration.get()) return;
                log(LogLevel.INFO, "本地TTS开始 [seq=" + seq + "] 首段音频=" + latencyMs + "ms");
            }
            @Override public void onComplete() { terminal(null); }
            @Override public void onError(String reason) { terminal(reason); }
            private void terminal(String reason) {
                if (generation != localTtsGeneration.get()) {
                    if (callback != null) callback.onFailed("本地TTS已停止");
                    return;
                }
                isSpeaking.set(false);
                currentTtsTextLength = 0;
                cancelTtsTimeout();
                clearTask();
                if (reason == null) {
                    notifyVoiceIntroductionCompleted();
                    if (callback != null) callback.onSuccess();
                } else if (callback != null) callback.onFailed(reason);
                processNextTts();
            }
        });
    }

    private void speakOnRobotBuiltIn(int seq, String text, SpeakCallback callback) {
        if (skillApi == null) {
            log(LogLevel.ERROR, "skillApi为null");
            isSpeaking.set(false);
            clearTask();
            if (callback != null) callback.onFailed("语音未就绪");
            processNextTts();
            return;
        }

        setCurrentStatus(STATUS_SPEAKING, TASK_SPEAK);
        startTtsTimeout(text.length());
        final long requestedAt = SystemClock.elapsedRealtime();
        final AtomicBoolean completed = new AtomicBoolean(false);

        try {
            skillApi.playText(new TTSEntity(text), createBuiltInTtsListener(seq, callback, requestedAt, completed));
        } catch (Exception e) {
            log(LogLevel.ERROR, "TTS异常: " + e.getMessage());
            if (!completed.compareAndSet(false, true)) return;
            isSpeaking.set(false);
            cancelTtsTimeout();
            clearTask();
            if (callback != null) callback.onFailed(e.getMessage());
            processNextTts();
        }
    }

    private TextListener createBuiltInTtsListener(int seq, SpeakCallback callback,
            long requestedAt, AtomicBoolean completed) {
        return new TextListener() {
                @Override
                public void onStart() {
                    if (completed.get()) return;
                    long firstAudioMs = SystemClock.elapsedRealtime() - requestedAt;
                    log(LogLevel.INFO, "TTS开始 [seq=" + seq + "] 首包=" + firstAudioMs + "ms");
                    isSpeaking.set(true);
                    // Keep MainActivity in the foreground so the visitor can
                    // read the ASR question and answer while TTS is playing.
                    // Explicit video/music commands still own VideoPlayerActivity.
                }

                @Override
                public void onStop() {
                    if (!completed.compareAndSet(false, true)) return;
                    log(LogLevel.INFO, "TTS停止 [seq=" + seq + "]");
                    isSpeaking.set(false);
                    currentTtsTextLength = 0;
                    cancelTtsTimeout();
                    clearTask();
                    if (callback != null) callback.onFailed("TTS已停止");
                    processNextTts();
                }

                @Override
                public void onError() {
                    if (!completed.compareAndSet(false, true)) return;
                    log(LogLevel.ERROR, "TTS错误 [seq=" + seq + "]");
                    isSpeaking.set(false);
                    currentTtsTextLength = 0;
                    cancelTtsTimeout();
                    clearTask();
                    if (callback != null) callback.onFailed("TTS错误");
                    processNextTts();
                }

                @Override
                public void onComplete() {
                    if (!completed.compareAndSet(false, true)) return;
                    log(LogLevel.INFO, "TTS完成 [seq=" + seq + "]");
                    isSpeaking.set(false);
                    currentTtsTextLength = 0;
                    cancelTtsTimeout();
                    clearTask();
                    notifyVoiceIntroductionCompleted();
                    if (callback != null) callback.onSuccess();
                    mainHandler.postDelayed(RobotCapabilities.this::processNextTts, 100);
                }
        };
    }

    private void startTtsTimeout(int textLength) {
        cancelTtsTimeout();
        long timeout = Math.min(RobotConfig.TTS_TIMEOUT_BASE_MS + textLength * RobotConfig.TTS_TIMEOUT_PER_CHAR_MS, RobotConfig.TTS_TIMEOUT_MAX_MS);
        mainHandler.sendEmptyMessageDelayed(MSG_TTS_TIMEOUT, timeout);
    }

    private void handleTtsTimeout() {
        if (isSpeaking.get()) {
            log(LogLevel.WARN, "TTS超时强制停止");
            stopTTS();
        }
    }

    private void cancelTtsTimeout() {
        mainHandler.removeMessages(MSG_TTS_TIMEOUT);
    }

    public void stopTTS() {
        log(LogLevel.INFO, "停止TTS");
        wakeupResponsePlayer.stop();
        localTtsGeneration.incrementAndGet();
        localTtsController.cancel();
        iflytekTtsController.cancel();
        if (skillApi != null) {
            try {
                skillApi.stopTTS();
            } catch (Exception e) {
                log(LogLevel.WARN, "stopTTS异常: " + e.getMessage());
            }
        }
        isSpeaking.set(false);
        currentTtsTextLength = 0;
        pendingSpeakCallback = null;
        clearTask();
        processNextTts();
    }

    private void stopTTSImmediately() {
        wakeupResponsePlayer.stop();
        localTtsGeneration.incrementAndGet();
        localTtsController.cancel();
        if (LocalTtsController.isEnabledMode() && TASK_SPEAK.equals(currentTask)) {
            isSpeaking.set(false);
            currentTtsTextLength = 0;
            cancelTtsTimeout();
            clearTask();
        }
        iflytekTtsController.cancel();
        if (skillApi != null) {
            try {
                skillApi.stopTTS();
            } catch (Exception e) {
                log(LogLevel.ERROR, "停止TTS失败: " + e.getMessage());
            }
        } else if (mockTts != null) {
            mockTts.stop();
        }
    }

    // ======================= 人脸识别（优化频率） =======================
    public void pauseFaceProcessing(long durationMs) {
        if (durationMs <= 0L) return;
        long until = System.currentTimeMillis() + durationMs;
        faceProcessingSuppressedUntil.accumulateAndGet(until, Math::max);
    }

    public void startFaceRecognize() {
        log(LogLevel.INFO, "启动人脸检测");
        lastReportedUserId = null;
        lastReportedPersonCount = -1;
        lastReportTime = 0;
        lastPersonCountReportTime = 0;
        lastUnknownFaceReportTime = 0;
        lastKnownFaceReportTime = 0;

        if (!isPersonListenerRegistered) {
            PersonApi.getInstance().registerPersonListener(personListener);
            isPersonListenerRegistered = true;
            log(LogLevel.INFO, "人脸监听已注册");
        }
    }

    public void stopFaceRecognize() {
        if (isPersonListenerRegistered) {
            PersonApi.getInstance().unregisterPersonListener(personListener);
            isPersonListenerRegistered = false;
            log(LogLevel.INFO, "人脸监听已停止");
        }
    }

    private void processPersonChange() {

        long now = System.currentTimeMillis();
        if (now < faceProcessingSuppressedUntil.get()) return;

        // [v8.0] 基础防抖 - 1秒内不重复处理
        if (now - lastFaceProcessTime < RobotConfig.FACE_DEBOUNCE_MS) return;
        lastFaceProcessTime = now;

        // 实时人脸检测(用于人数统计)
        List<Person> liveFaces = PersonApi.getInstance().getAllFaceList(1);
        int currentCount = (liveFaces != null) ? liveFaces.size() : 0;

        // [v8.0] 获取已注册人员信息(含姓名), 有则用含姓名的Person
        final Person person;
        List<Person> regPersons = PersonApi.getInstance().getAllPersons(1);
        if (regPersons != null && !regPersons.isEmpty()) {
            person = PersonUtils.getBestFace(regPersons);
        } else if (liveFaces != null && !liveFaces.isEmpty()) {
            // 兜底: 无注册人员时使用实时检测(无姓名)
            person = PersonUtils.getBestFace(liveFaces);
        } else {
            return;
        }
        if (person == null) return;

        // 优化2: 人员数量上报的时间间隔限制
        if (currentCount != lastReportedPersonCount &&
                (now - lastPersonCountReportTime) >= PERSON_COUNT_REPORT_INTERVAL_MS) {
            lastReportedPersonCount = currentCount;
            lastPersonCountReportTime = now;
            FaceListener listener = faceListener;
            if (listener != null) {
                listener.updatePersonCount(robotId, currentCount);
            }
        }

        // 优化3: 判断是否为已知用户（已注册用户）
        String userId = person.getUserId();
        String userName = person.getName();
        boolean isKnownUser = !TextUtils.isEmpty(userName) || !TextUtils.isEmpty(userId);
        String identityKey = isKnownUser
                ? (!TextUtils.isEmpty(userId) ? userId : userName)
                : "__unknown__";

        // 优化4: 全局人脸识别事件上报频率限制（1秒），用于 SDK 高频回调防抖。
        // 不能因为同一张脸 1 秒内重复出现就阻断陌生人第一次上报；陌生人也需要触发中枢通用问候。
        if (now - lastReportTime < FACE_RECOGNIZE_REPORT_INTERVAL_MS) {
            // 注意：即使不上报事件，也要启动焦点跟随
            // 焦点跟随独立于事件上报
            startFocusFollow(ReqIdUtils.next(), person.getId());
            return;
        }

        // 优化5: 根据用户类型使用不同的上报间隔
        if (isKnownUser) {
            // 已知用户：同一用户 10 秒内不重复上报，和中枢主动问候节奏保持一致；不同用户仍可立即上报。
            if (identityKey.equals(lastReportedUserId) &&
                    (now - lastKnownFaceReportTime) < SAME_FACE_REPORT_INTERVAL_MS) {
                // 注意：即使不上报事件，也要启动焦点跟随
                startFocusFollow(ReqIdUtils.next(), person.getId());
                return;
            }
            lastKnownFaceReportTime = now;
        } else {
            // 未知用户：15秒内只上报一次，避免刷屏
            if ((now - lastUnknownFaceReportTime) < UNKNOWN_FACE_REPORT_INTERVAL_MS) {
                // 注意：即使不上报事件，也要启动焦点跟随
                startFocusFollow(ReqIdUtils.next(), person.getId());
                return;
            }
            lastUnknownFaceReportTime = now;
        }

        // 更新上报记录
        lastReportedUserId = identityKey;
        lastReportedUserName = userName;
        lastReportTime = now;

        // [v7.0] 上报所有Person字段用于后台识别身份
        FaceListener listener = faceListener;
        if (listener != null) {
            listener.onFaceRecognized(robotId, userId, userName, 1);
            if (!TextUtils.isEmpty(userId) || !TextUtils.isEmpty(userName)) {
                log(LogLevel.INFO, "上报已识别人脸: userId=" + userId + ", name=" + userName);
            } else {
                //log(LogLevel.INFO, "上报陌生人脸: personId=" + person.getId());
                // SDK 有时 getAllPersons/getAllFaceList 返回的 Person 不带 name/userId，
                // 但云端可能能按图片查到已录入姓名；这里异步补查，查到后再上报一次带姓名事件。
                processFaceRecognition(person);
            }
            // 额外发送完整Person信息
            try {
                org.json.JSONObject extra = new org.json.JSONObject();
                extra.put("robotId", robotId);
                extra.put("userId", userId != null ? userId : "");
                extra.put("name", userName != null ? userName : "");
                try { extra.put("personId", person.getId()); } catch (Exception e) {}
                try { extra.put("age", person.getAge()); } catch (Exception e) {}
                try { extra.put("gender", person.getGender()); } catch (Exception e) {}
                try { extra.put("distance", person.getDistance()); } catch (Exception e) {}
                listener.onPersonDetail(robotId, extra.toString());
            } catch (Exception ignored) {}
        }

        // 启动焦点跟随（每次检测到人脸都尝试启动）
        startFocusFollow(ReqIdUtils.next(), person.getId());

    }

    private void processFaceRecognition(final Person person) {
        int reqID = ReqIdUtils.next();
        try {
            RobotApi.getInstance().getPictureById(reqID, person.getId(), 1, new CommandListener() {
                @Override
                public void onResult(int result, String message, String extra) {
                    try {
                        JSONObject json = new JSONObject(message);
                        if (Definition.RESPONSE_OK.equals(json.optString("status"))) {
                            JSONArray pictures = json.optJSONArray("pictures");
                            if (pictures != null && pictures.length() > 0 && !TextUtils.isEmpty(pictures.optString(0))) {
                                String picturePath = pictures.optString(0);
                                List<String> facePics = new ArrayList<>();
                                facePics.add(picturePath);
                                fetchPersonInfo(reqID, person, picturePath, facePics);
                            }
                        }
                    } catch (JSONException e) {
                        log(LogLevel.ERROR, "人脸图片解析失败");
                    }
                }
            });
        } catch (Exception e) {
            log(LogLevel.ERROR, "获取人脸图片异常");
        }
    }

    private void fetchPersonInfo(int reqID, Person person, String picturePath, List<String> facePics) {
        try {
            RobotApi.getInstance().getPersonInfoFromNet(reqID, person.getUserId(), facePics, new CommandListener() {
                @Override
                public void onResult(int result, String message, String extraData) {
                    try {
                        JSONObject json = new JSONObject(message);
                        JSONObject data = json.optJSONObject("data");
                        if (data == null) return;
                        JSONObject info = data.optJSONObject("people");
                        if (info == null) return;
                        /* ===== FIX: get cloud name and report it ===== */
                        String cloudUserId = info.optString("user_id", "");
                        String cloudName = info.optString("name", "");
                        if (!TextUtils.isEmpty(cloudName) && faceListener != null) {
                            android.util.Log.d(TAG, "Cloud has name: " + cloudName);
                            lastReportedUserName = cloudName;
                            if (!TextUtils.isEmpty(cloudUserId)) {
                                lastReportedUserId = cloudUserId;
                            }
                            faceListener.onFaceRecognized(robotId, cloudUserId, cloudName, 1);
                            try {
                                org.json.JSONObject extra = new org.json.JSONObject();
                                extra.put("robotId", robotId);
                                extra.put("userId", cloudUserId);
                                extra.put("name", cloudName);
                                extra.put("source", "cloud_lookup");
                                faceListener.onPersonDetail(robotId, extra.toString());
                            } catch (Exception ignored) {}
                        }
                        /* ===== END FIX ===== */
                    } catch (JSONException e) {
                        log(LogLevel.ERROR, "人脸信息解析失败");
                    }
                }
                @Override
                public void onStatusUpdate(int status, String data, String extraData) {
                }
            });
        } catch (Exception e) {
            log(LogLevel.ERROR, "获取人员信息异常");
        }
    }


    // 焦点跟随方法（简化，防止阻塞）
    public void startFocusFollow(int reqId, int faceId) {
        if (isFocusFollow) return;
        isFocusFollow = true;
        currentReqId = reqId;
        currentFaceId = faceId;
        try {
            RobotApi.getInstance().startSmartFocusFollow(reqId, faceId,
                    RobotConfig.SMART_FOCUS_FOLLOW_TIMEOUT,
                    RobotConfig.SMART_FOCUS_FOLLOW_DISTANCE,
                    new ActionListener() {
                        @Override
                        public void onStatusUpdate(int status, String data, String extraData) {
                            if (status == Definition.STATUS_GUEST_LOST || status == Definition.STATUS_GUEST_FARAWAY) {
                                isFocusFollow = false;
                                stopFocusFollow();
                            }
                        }

                        @Override
                        public void onResult(int status, String responseString, String extraData) {
                            if (status == Definition.ACTION_RESPONSE_STOP_SUCCESS)
                                isFocusFollow = false;
                        }

                        @Override
                        public void onError(int errorCode, String errorString, String extraData) {
                            isFocusFollow = false;
                        }
                    });
        } catch (Exception e) {
            log(LogLevel.ERROR, "启动焦点跟随失败");
        }
    }

    public void stopFocusFollow() {
        if (currentReqId != -1) {
            RobotApi.getInstance().stopSmartFocusFollow(currentReqId);
            isFocusFollow = false;
        }
    }

    // ======================= 导航、点位、UI等（保持原逻辑，优化状态） =======================
    public void loadPointList(PointListCallback callback) {
        if (!isOnRobot) {
            log(LogLevel.INFO, "模拟模式，使用模拟点位");
            loadMockPointList(callback);
            return;
        }
        log(LogLevel.INFO, "从机器人获取点位列表...");
        try {
            int reqId = new Random().nextInt(10000);
            RobotApi.getInstance().getPlaceList(reqId, new CommandListener() {
                @Override
                public void onResult(int result, String message, String extraData) {
                    try {
                        JSONArray jsonArray = new JSONArray(message);
                        int length = jsonArray.length();
                        pointMap.clear();

                        List<PointInfo> points = new ArrayList<>();

                        for (int i = 0; i < length; i++) {
                            JSONObject json = jsonArray.getJSONObject(i);

                            String name = json.getString("name");
                            String id = json.getString("id");
                            double x = json.getDouble("x");
                            double y = json.getDouble("y");
                            double theta = json.optDouble("theta", 0);
                            int status = json.optInt("status", 0);

                            PointInfo point = new PointInfo(id, name, x, y, theta, status);
                            pointMap.put(name, point);
                            points.add(point);

                            log(LogLevel.DEBUG, "点位: " + name + " (x=" + x + ", y=" + y + ", status=" + status + ")");
                        }

                        isPointListLoaded = true;
                        log(LogLevel.INFO, "成功加载 " + pointMap.size() + " 个点位");

                        if (callback != null) {
                            callback.onSuccess(points);
                        }

                    } catch (JSONException e) {
                        log(LogLevel.ERROR, "解析点位列表失败: " + e.getMessage());
                        if (callback != null) {
                            callback.onFailed("解析点位列表失败: " + e.getMessage());
                        }
                    } catch (Exception e) {
                        log(LogLevel.ERROR, "加载点位列表异常: " + e.getMessage());
                        if (callback != null) {
                            callback.onFailed("加载点位列表异常: " + e.getMessage());
                        }
                    }
                }

                @Override
                public void onError(int errorCode, String errorString, String extraData) {
                    log(LogLevel.ERROR, "获取点位列表失败: code=" + errorCode + ", msg=" + errorString);
                    if (callback != null) {
                        callback.onFailed("获取点位列表失败: " + errorString);
                    }
                }
            });
        } catch (Exception e) {
            log(LogLevel.ERROR, "获取点位列表异常: " + e.getMessage());
            if (callback != null) {
                callback.onFailed(e.getMessage());
            }
        }
    }

    private void loadMockPointList(PointListCallback callback) {
        pendingPointListCallback = callback;
        mainHandler.sendEmptyMessageDelayed(MSG_MOCK_POINT_LIST, MOCK_POINT_LIST_DELAY_MS);
    }

    private void handleMockPointList() {
        pointMap.clear();
        List<PointInfo> points = new ArrayList<>();

        points.add(new PointInfo("1", "充电位", 0, 0, 0, 0));
        points.add(new PointInfo("2", "序厅", 1.5, 0.5, 0, 0));
        points.add(new PointInfo("3", "序厅入口", 1.2, 0.3, 0, 0));
        points.add(new PointInfo("4", "新能源展区", 2.5, 1.2, 0, 0));
        points.add(new PointInfo("5", "数字沙盘", 3.0, 2.0, 0, 0));

        for (PointInfo p : points) {
            pointMap.put(p.name, p);
        }

        isPointListLoaded = true;
        log(LogLevel.INFO, "模拟模式，加载 " + pointMap.size() + " 个点位");

        PointListCallback cb = pendingPointListCallback;
        if (cb != null) {
            cb.onSuccess(points);
            pendingPointListCallback = null;
        }
    }

    public List<String> getPointNames() {
        return new ArrayList<>(pointMap.keySet());
    }

    public boolean isPointExists(String pointName) {
        return pointMap.containsKey(pointName);
    }

    public PointInfo getPointInfo(String pointName) {
        return pointMap.get(pointName);
    }

    public boolean isPointListLoaded() {
        return isPointListLoaded;
    }

    public void navigateTo(String pointName, NavigateCallback callback) {
        if (isFocusFollow) stopFocusFollow();
        cancelCurrentTask();
        setCurrentStatus(STATUS_NAVIGATING, TASK_NAVIGATE);
        if (!pointMap.containsKey(pointName)) {
            if (callback != null) callback.onFailed("点位不存在");
            return;
        }
        if (isOnRobot) navigateOnRobot(pointName, callback);
        else navigateOnMock(pointName, callback);
    }

    public void takePhoto(String qrContent, PhotoCallback callback) {
        setCurrentStatus(STATUS_SHOWING, TASK_PHOTO);
        lastPhotoTakenAt = System.currentTimeMillis();
        String sessionId = UUID.randomUUID().toString();
        PhotoCallback safeCallback = new PhotoCallback() {
            @Override
            public void onSuccess(String photoPath, Uri photoUri) {
                clearTask();
                if (callback != null) callback.onSuccess(photoPath, photoUri);
            }

            @Override
            public void onFailed(String error) {
                clearTask();
                if (callback != null) callback.onFailed(error);
            }
        };
        PHOTO_CALLBACKS.put(sessionId, safeCallback);
        try {
            Intent intent = new Intent(applicationContext, CustomPhotoCaptureActivity.class);
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            intent.putExtra(CustomPhotoCaptureActivity.EXTRA_SESSION_ID, sessionId);
            intent.putExtra(CustomPhotoCaptureActivity.EXTRA_QR_CONTENT, qrContent == null ? "" : qrContent);
            intent.putExtra(CustomPhotoCaptureActivity.EXTRA_PREPARE_SECONDS, 3);
            intent.putExtra(CustomPhotoCaptureActivity.EXTRA_RESULT_HOLD_SECONDS, 25);
            applicationContext.startActivity(intent);
            log(LogLevel.INFO, "已进入拍照流程页面，等待用户摆姿势并拍照");
        } catch (Exception e) {
            PHOTO_CALLBACKS.remove(sessionId);
            clearTask();
            log(LogLevel.ERROR, "启动拍照流程失败: " + e.getMessage());
            if (callback != null) callback.onFailed(e.getMessage());
        }
    }

    public static void notifyPhotoSuccess(String sessionId, String photoPath, Uri photoUri) {
        PhotoCallback callback = PHOTO_CALLBACKS.remove(sessionId);
        if (callback != null) callback.onSuccess(photoPath, photoUri);
    }

    public static void notifyPhotoFailed(String sessionId, String error) {
        PhotoCallback callback = PHOTO_CALLBACKS.remove(sessionId);
        if (callback != null) callback.onFailed(error);
    }

    /**
     * 注册拍照回调并返回 sessionId (用于重拍场景: Activity 已存在但需要新回调).
     * 调用方需将返回的 sessionId 传给 CustomPhotoCaptureActivity.retakePhoto().
     */
    public static String registerPhotoCallback(PhotoCallback callback) {
        String sessionId = java.util.UUID.randomUUID().toString();
        PHOTO_CALLBACKS.put(sessionId, callback);
        return sessionId;
    }

    private void navigateOnRobot(String pointName, NavigateCallback callback) {
        try {
            int reqId = new Random().nextInt(10000);
            RobotApi.getInstance().startNavigation(reqId, pointName,
                    RobotConfig.NAVIGATION_COORDINATE_DEVIATION,
                    RobotConfig.NAVIGATION_TIMEOUT_MS,
                    new ActionListener() {
                        @Override
                        public void onResult(int status, String response, String extraData) {
                            try {
                                if (status == Definition.RESULT_OK && "true".equals(response)) {
                                    log(LogLevel.INFO, "导航成功: " + pointName);
                                    currentLocation = pointName;
                                    if (callback != null) callback.onSuccess(pointName);
                                } else {
                                    log(LogLevel.ERROR, "导航失败: " + response);
                                    if (callback != null) callback.onFailed(response);
                                }
                            } catch (Exception e) {
                                log(LogLevel.ERROR, "导航结果处理异常: " + e.getMessage());
                                if (callback != null) callback.onFailed(e.getMessage());
                            } finally {
                                clearTask();
                            }
                        }

                        @Override
                        public void onError(int errorCode, String errorString, String extraData) {
                            try {
                                String errorMsg = getNavErrorMsg(errorCode);
                                log(LogLevel.ERROR, "导航错误: " + errorMsg);
                                if (callback != null) callback.onFailed(errorMsg);
                            } catch (Exception e) {
                                log(LogLevel.ERROR, "导航错误处理异常: " + e.getMessage());
                                if (callback != null) callback.onFailed(e.getMessage());
                            } finally {
                                clearTask();
                            }
                        }

                        @Override
                        public void onStatusUpdate(int status, String data, String extraData) {
                            log(LogLevel.DEBUG, "导航状态更新: " + status);
                            switch (status) {
                                case Definition.STATUS_NAVI_AVOID:
                                    log(LogLevel.WARN, "当前路线已经被障碍物堵死");
                                    break;
                                case Definition.STATUS_NAVI_AVOID_END:
                                    log(LogLevel.INFO, "障碍物已移除");
                                    break;
                            }
                        }
                    });
        } catch (Exception e) {
            log(LogLevel.ERROR, "导航异常: " + e.getMessage());
            if (callback != null) callback.onFailed(e.getMessage());
            clearTask();
        }
    }

    private void navigateOnMock(String pointName, NavigateCallback callback) {
        log(LogLevel.INFO, "模拟导航: " + pointName);
        Message msg = mainHandler.obtainMessage(MSG_MOCK_NAVIGATE);
        msg.obj = new MockNavigateData(pointName, callback);
        mainHandler.sendMessageDelayed(msg, MOCK_NAVIGATE_DELAY_MS);
    }

    private void handleMockNavigate(MockNavigateData data) {
        if (data != null) {
            boolean success = new Random().nextInt(100) < 90;
            if (success) {
                currentLocation = data.pointName;
                if (data.callback != null) data.callback.onSuccess(data.pointName);
            } else {
                if (data.callback != null) data.callback.onFailed("模拟导航失败");
            }
            clearTask();
        }
    }

    private String getNavErrorMsg(int code) {
        switch (code) {
            case Definition.ERROR_NOT_ESTIMATE:
                return "当前未定位";
            case Definition.ERROR_IN_DESTINATION:
                return "当前机器人已经在目的地范围内";
            case Definition.ERROR_DESTINATION_NOT_EXIST:
                return "导航目的地不存在";
            case Definition.ERROR_DESTINATION_CAN_NOT_ARRAIVE:
                return "避障超时，目的地不能到达";
            case Definition.ACTION_RESPONSE_ALREADY_RUN:
                return "当前接口已经调用，请先停止";
            case Definition.ACTION_RESPONSE_REQUEST_RES_ERROR:
                return "已有控制底盘的接口调用，请先停止";
            default:
                return "导航失败: code=" + code;
        }
    }

    public void cancelNavigation() {
        if (isOnRobot) {
            try {
                RobotApi.getInstance().stopNavigation(new Random().nextInt(10000));
            } catch (Exception e) {
                log(LogLevel.ERROR, "取消导航失败");
            }
        }
        currentTask = null;
    }

    public void showUI(String action, JSONObject data, ShowCallback callback) {
        setCurrentStatus(STATUS_SHOWING, TASK_SHOW);
        if (isOnRobot) showUIOnRobot(action, data, callback);
        else showUIOnMock(action, data, callback);
    }

    private void showUIOnRobot(String action, JSONObject data, ShowCallback callback) {
        try {
            VideoPlayerActivity.showContentOnVideo(action, data.toString());
            if (callback != null) callback.onSuccess();
        } catch (Exception e) {
            log(LogLevel.ERROR, "启动屏幕展示失败: " + e.getMessage());
            if (callback != null) callback.onFailure(e.getMessage());
        }
    }

    private void showUIOnMock(String action, JSONObject data, ShowCallback callback) {
        log(LogLevel.INFO, "模拟展示: action=" + action);
        log(LogLevel.INFO, "展示数据: " + data.toString());

        if ("list".equals(action)) {
            String listType = data.optString("listType", "");
            String title = data.optString("title", "");
            JSONArray items = data.optJSONArray("items");
            log(LogLevel.INFO, "列表展示 - 类型:" + listType + ", 标题:" + title + ", 条目数:" + (items != null ? items.length() : 0));
        } else if ("card".equals(action)) {
            String title = data.optString("title", "");
            String content = data.optString("content", "");
            log(LogLevel.INFO, "卡片展示 - 标题:" + title + ", 内容:" + content);
        } else if ("text".equals(action)) {
            String text = data.optString("text", "");
            log(LogLevel.INFO, "文字展示: " + text);
        } else if ("clear".equals(action)) {
            log(LogLevel.INFO, "清空屏幕");
        }

        if (callback != null) callback.onSuccess();
    }

    public void startVideoLoop() {
        if (isVideoPlaying.get()) {
            log(LogLevel.WARN, "视频已在播放中");
            return;
        }
        log(LogLevel.INFO, "启动视频循环播放");
        isVideoPlaying.set(true);
        VideoPlayerActivity.startLoop(applicationContext, getDefaultVideoResId(), (userClosed) -> {
            log(LogLevel.INFO, "视频已停止，用户关闭=" + userClosed);
            isVideoPlaying.set(false);
            if (userClosed && videoCloseCallback != null) {
                log(LogLevel.INFO, "用户主动关闭视频，发送关闭事件");
                videoCloseCallback.onVideoClosed();
            }
        });
    }

    private int getDefaultVideoResId() {
        return R.raw.tts_video;
    }

    public void showImage() {
        if (isVideoPlaying.get()) {
            stopVideoLoop();
        }
        isVideoPlaying.set(true);
        VideoPlayerActivity.startLoop(applicationContext, -1, (userClosed) -> {
            isVideoPlaying.set(false);
            if (userClosed && videoCloseCallback != null) {
                log(LogLevel.INFO, "用户主动关闭图片，发送关闭事件");
                videoCloseCallback.onVideoClosed();
            }
        });
    }

    private void stopVideoLoop() {
        if (!isVideoPlaying.get()) {
            return;
        }
        log(LogLevel.INFO, "停止视频播放");
        isVideoPlaying.set(false);
        VideoPlayerActivity.stopCurrent();
    }

    private void notifyVoiceIntroductionCompleted() {
        try {
            Class<?> activityClass = Class.forName("com.zongheng.exhibitionhallrobot.ScreenDisplayActivity");
            java.lang.reflect.Method isValidMethod = activityClass.getMethod("isActivityValid");
            Boolean isValid = (Boolean) isValidMethod.invoke(null);

            if (isValid != null && isValid) {
                java.lang.reflect.Method method = activityClass.getMethod("markVoiceIntroductionCompleted");
                method.invoke(null);
                log(LogLevel.INFO, "已通知ScreenDisplayActivity语音介绍完成");
            } else {
                log(LogLevel.INFO, "ScreenDisplayActivity已不存在，无需通知");
            }
        } catch (Exception e) {
            //log(LogLevel.WARN, "通知ScreenDisplayActivity失败: " + e.getMessage());
        }
    }

    public void stopListening() {
        iflytekAsrController.cancel();
        previewSpeech("");
        if (isOnRobot && skillApi != null) switchToWakeupMode();
        isListening.set(false);
        clearTask();
    }

    public void stopSpeak() {
        stopTTS();
    }

    /**
     * 播放音乐
     * @param audioUrl 音频URL地址
     * @param callback 播放回调
     */
    public void playMusic(String audioUrl, MusicCallback callback) {
        if (audioUrl == null || audioUrl.isEmpty()) {
            log(LogLevel.ERROR, "音频URL为空");
            if (callback != null) callback.onFailed("音频URL为空");
            return;
        }

        cancelCurrentTask();
        setCurrentStatus(STATUS_SPEAKING, TASK_PLAY_MUSIC);
        this.musicCallback = callback;

        log(LogLevel.INFO, "开始播放音乐: " + audioUrl);

        if (isOnRobot) {
            playMusicOnRobot(audioUrl, callback);
        } else {
            playMusicOnMock(audioUrl, callback);
        }
    }

    /**
     * 在机器人上播放音乐
     */
    private void playMusicOnRobot(String audioUrl, MusicCallback callback) {
        if (skillApi == null) {
            log(LogLevel.ERROR, "skillApi为null，无法播放音乐");
            isMusicPlaying.set(false);
            clearTask();
            if (callback != null) callback.onFailed("语音服务未就绪");
            return;
        }

        try {
            // 先停止之前的播放
            if (currentMusicPlayer != null) {
                try {
                    currentMusicPlayer.stop();
                    currentMusicPlayer.release();
                } catch (Exception e) {
                    log(LogLevel.WARN, "释放旧播放器失败: " + e.getMessage());
                }
                currentMusicPlayer = null;
            }

            isMusicPlaying.set(true);
            
            // 【关键修复】启动视频循环播放
            startVideoLoop();
            
            // 初始化AudioManager
            if (audioManager == null) {
                audioManager = (AudioManager) applicationContext.getSystemService(Context.AUDIO_SERVICE);
            }
            
            // 请求音频焦点
            if (requestAudioFocus()) {
                log(LogLevel.INFO, "成功获取音频焦点");
            } else {
                log(LogLevel.WARN, "未能获取音频焦点，但仍尝试播放");
            }

            // 创建MediaPlayer并保存引用
            currentMusicPlayer = new MediaPlayer();

            // 设置音频属性（使用新的API，替代setAudioStreamType）
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                AudioAttributes audioAttributes = new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                    .build();
                currentMusicPlayer.setAudioAttributes(audioAttributes);
            } else {
                // 兼容旧版本
                currentMusicPlayer.setAudioStreamType(android.media.AudioManager.STREAM_MUSIC);
            }

            // 设置音量（最大音量）
            currentMusicPlayer.setVolume(1.0f, 1.0f);

            // 设置循环播放（可选，设为false只播放一次）
            currentMusicPlayer.setLooping(false);

            currentMusicPlayer.setOnPreparedListener(mp -> {
                log(LogLevel.INFO, "音乐准备完成，开始播放");
                try {
                    mp.start();
                    log(LogLevel.INFO, "MediaPlayer已启动播放");
                } catch (Exception e) {
                    log(LogLevel.ERROR, "启动播放失败: " + e.getMessage());
                }
            });

            currentMusicPlayer.setOnCompletionListener(mp -> {
                log(LogLevel.INFO, "音乐播放完成");
                isMusicPlaying.set(false);
                stopVideoLoop();
                clearTask();

                // 释放资源
                if (currentMusicPlayer != null) {
                    currentMusicPlayer.release();
                    currentMusicPlayer = null;
                }

                if (musicCallback != null) {
                    musicCallback.onSuccess();
                    musicCallback.onComplete();
                    musicCallback = null;
                }
            });

            currentMusicPlayer.setOnErrorListener((mp, what, extra) -> {
                String errorMsg = "音乐播放错误: what=" + what + ", extra=" + extra;
                log(LogLevel.ERROR, errorMsg);

                // 详细错误码说明
                switch (what) {
                    case MediaPlayer.MEDIA_ERROR_UNKNOWN:
                        log(LogLevel.ERROR, "未知错误 (MEDIA_ERROR_UNKNOWN)");
                        break;
                    case MediaPlayer.MEDIA_ERROR_SERVER_DIED:
                        log(LogLevel.ERROR, "媒体服务器死亡 (MEDIA_ERROR_SERVER_DIED)");
                        break;
                }
                
                switch (extra) {
                    case MediaPlayer.MEDIA_ERROR_IO:
                        log(LogLevel.ERROR, "IO错误 (MEDIA_ERROR_IO) - 可能是网络问题或URL无效");
                        break;
                    case MediaPlayer.MEDIA_ERROR_MALFORMED:
                        log(LogLevel.ERROR, "格式错误 (MEDIA_ERROR_MALFORMED)");
                        break;
                    case MediaPlayer.MEDIA_ERROR_UNSUPPORTED:
                        log(LogLevel.ERROR, "不支持的格式 (MEDIA_ERROR_UNSUPPORTED)");
                        break;
                    case MediaPlayer.MEDIA_ERROR_TIMED_OUT:
                        log(LogLevel.ERROR, "超时错误 (MEDIA_ERROR_TIMED_OUT)");
                        break;
                    default:
                        log(LogLevel.ERROR, "额外错误码: " + extra);
                }

                isMusicPlaying.set(false);
                stopVideoLoop();
                clearTask();

                // 释放资源
                if (currentMusicPlayer != null) {
                    currentMusicPlayer.release();
                    currentMusicPlayer = null;
                }

                if (musicCallback != null) {
                    musicCallback.onFailed(errorMsg);
                    musicCallback = null;
                }
                return true;
            });

            currentMusicPlayer.setOnBufferingUpdateListener((mp, percent) -> {
                log(LogLevel.DEBUG, "缓冲进度: " + percent + "%");
            });

            // 设置数据源
            if (audioUrl.startsWith("http://") || audioUrl.startsWith("https://")) {
                log(LogLevel.INFO, "设置网络音频源: " + audioUrl);
                
                // 【关键】使用带Headers的方式设置数据源，避免某些服务器拒绝请求
                try {
                    java.util.Map<String, String> headers = new java.util.HashMap<>();
                    headers.put("User-Agent", "Mozilla/5.0");
                    currentMusicPlayer.setDataSource(applicationContext, Uri.parse(audioUrl), headers);
                    log(LogLevel.DEBUG, "使用带Headers的数据源设置");
                } catch (Exception e) {
                    // 如果失败，使用默认方式
                    log(LogLevel.WARN, "带Headers设置失败，使用默认方式: " + e.getMessage());
                    currentMusicPlayer.setDataSource(applicationContext, Uri.parse(audioUrl));
                }
            } else {
                log(LogLevel.INFO, "设置本地音频源: " + audioUrl);
                currentMusicPlayer.setDataSource(audioUrl);
            }

            // 异步准备
            log(LogLevel.INFO, "开始异步准备MediaPlayer...");
            currentMusicPlayer.prepareAsync();

        } catch (Exception e) {
            log(LogLevel.ERROR, "音乐播放异常: " + e.getMessage());
            e.printStackTrace();
            isMusicPlaying.set(false);
            stopVideoLoop();
            clearTask();

            // 释放资源
            if (currentMusicPlayer != null) {
                try {
                    currentMusicPlayer.release();
                } catch (Exception ex) {
                    // 忽略
                }
                currentMusicPlayer = null;
            }

            if (callback != null) callback.onFailed(e.getMessage());
        }
    }

    /**
     * 模拟模式播放音乐
     */
    private void playMusicOnMock(String audioUrl, MusicCallback callback) {
        log(LogLevel.INFO, "模拟模式播放音乐: " + audioUrl);
        
        mainHandler.postDelayed(() -> {
            if (callback != null) {
                callback.onSuccess();
                callback.onComplete();
            }
            isMusicPlaying.set(false);
            clearTask();
        }, 5000);
    }

    /**
     * 停止音乐播放
     */
    public void stopMusic() {
        log(LogLevel.INFO, "停止音乐播放");
        
        // 放弃音频焦点
        abandonAudioFocus();
        
        // 停止并释放MediaPlayer
        if (currentMusicPlayer != null) {
            try {
                if (currentMusicPlayer.isPlaying()) {
                    currentMusicPlayer.stop();
                }
                currentMusicPlayer.release();
                log(LogLevel.INFO, "MediaPlayer已释放");
            } catch (Exception e) {
                log(LogLevel.ERROR, "停止音乐失败: " + e.getMessage());
            }
            currentMusicPlayer = null;
        }
        
        isMusicPlaying.set(false);
        stopVideoLoop();
        clearTask();
        
        if (musicCallback != null) {
            musicCallback.onComplete();
            musicCallback = null;
        }
    }

    /**
     * 调节音乐音量
     * @param direction "up" 调大 / "down" 调小
     */
    public void adjustVolume(String direction) {
        if (currentMusicPlayer == null) {
            log(LogLevel.WARN, "当前没有播放音乐，无法调节音量");
            return;
        }
        try {
            float currentVolume = 0.5f;  // 默认
            try {
                // Android MediaPlayer 没有 getVolume, 用 AudioManager 替代
                // 这里用 MediaPlayer 的 setVolume 设置左右声道音量
                if ("up".equals(direction)) {
                    currentMusicPlayer.setVolume(1.0f, 1.0f);
                    log(LogLevel.INFO, "音量已调大");
                } else if ("down".equals(direction)) {
                    currentMusicPlayer.setVolume(0.3f, 0.3f);
                    log(LogLevel.INFO, "音量已调小");
                }
            } catch (Exception e) {
                log(LogLevel.ERROR, "调节音量失败: " + e.getMessage());
            }
        } catch (Exception e) {
            log(LogLevel.ERROR, "调节音量异常: " + e.getMessage());
        }
    }

    public String cancelCurrentTask() {
        String canceled = currentTask;
        if (TASK_NAVIGATE.equals(currentTask)) cancelNavigation();
        else if (TASK_SPEAK.equals(currentTask)) stopSpeak();
        else if (TASK_LISTEN.equals(currentTask)) stopListening();
        else if (TASK_FOCUS_FOLLOW.equals(currentTask)) stopFocusFollow();
        else if (TASK_PLAY_MUSIC.equals(currentTask)) stopMusic();
        clearTask();
        return canceled;
    }

    public void onSpeechResultReceived(String text) {
        ListenCallback cb = listenCallback;
        if (cb != null && STATUS_LISTENING.equals(currentStatus)) {
            cb.onResult(text, 3000);
        } else {
            VoiceCommandCallback vcb = voiceCommandCallback;
            if (vcb != null) vcb.onVoiceCommand(text);
        }
    }

    private void scheduleListenTimeout(int seconds) {
        mainHandler.sendEmptyMessageDelayed(MSG_LISTEN_TIMEOUT, seconds * 1000L);
    }

    private void handleListenTimeout() {
        if (listenCallback != null) listenCallback.onTimeout("总时间超时");
        cleanupListenState();
    }

    private void cancelListenTimeout() {
        mainHandler.removeMessages(MSG_LISTEN_TIMEOUT);
    }

    public void setVoiceCommandCallback(VoiceCommandCallback callback) {
        this.voiceCommandCallback = callback;
    }

    public void setVideoCloseCallback(VideoCloseCallback callback) {
        this.videoCloseCallback = callback;
    }

    public void setMusicCallback(MusicCallback callback) {
        this.musicCallback = callback;
    }

    public void setWakeWord(String word) {
        //log(LogLevel.WARN,"设置唤醒词: " + word);
        //            String pinyin = skillApi.queryPinYinFromChinese(word);
        //            skillApi.setCustomizeWakeUpWord(word, pinyin, "");
    }

    public void setListenTimeout(int timeout) {
        this.listenTimeout = timeout;
    }

    public void setListenLanguage(String language) {
        this.listenLanguage = language;
    }

    public void setRobotId(String id) {
        this.robotId = id;
    }

    public int getCurrentBattery() {
        return currentBattery;
    }

    public void updateBattery(int battery) {
        this.currentBattery = battery;
    }

    public String getCurrentLocation() {
        return currentLocation;
    }

    public String getCurrentStatus() {
        stateLock.readLock().lock();
        try {
            return currentStatus;
        } finally {
            stateLock.readLock().unlock();
        }
    }

    public String getCurrentTask() {
        stateLock.readLock().lock();
        try {
            return currentTask;
        } finally {
            stateLock.readLock().unlock();
        }
    }

    public boolean isSpeaking() {
        return isSpeaking.get();
    }

    /**
     * 检查是否正在播放音乐
     */
    public boolean isMusicPlaying() {
        return isMusicPlaying.get();
    }

    public int getRobotBattery() {
        return RobotSettingApi.getInstance().getRobotInt(Definition.ROBOT_SETTINGS_BATTERY_INFO);
    }

    private void setCurrentStatus(String status, String task) {
        stateLock.writeLock().lock();
        try {
            this.currentStatus = status;
            this.currentTask = task;
        } finally {
            stateLock.writeLock().unlock();
        }
    }

    private void clearTask() {
        stateLock.writeLock().lock();
        try {
            this.currentStatus = STATUS_IDLE;
            this.currentTask = null;
        } finally {
            stateLock.writeLock().unlock();
        }
    }

    public void onKeywordWakeup() {
        log(LogLevel.INFO, "KWS唤醒：立即中止播报，不等待ASR文本");
        pauseFaceProcessing(30_000L);
        ttsQueue.clear();
        stopTTSImmediately();
        previewSpeech("");
        // Do not re-register ASR or use the normal TTS queue for this short reply.
        // Both would disturb the recognition window RobotOS just opened.
        startAutoListening();
        if (LocalTtsController.isEnabledMode()) wakeupResponsePlayer.play();
    }

    public void restoreSystemWakeupHint() {
        iflytekAsrController.cancel();
        previewSpeech("");
        if (skillApi != null && LocalTtsController.isEnabledMode()) {
            try { skillApi.setWakeupHintClosed(false); }
            catch (Exception error) { log(LogLevel.WARN, "恢复系统唤醒提示失败"); }
        }
        wakeupResponsePlayer.stop();
    }

    private void initRobotSdk() {
        MyRobotApplication app = MyRobotApplication.getInstance();
        if (app != null) {
            app.setSdkReadyListener(skillApi -> {
                isOnRobot = true;
                this.skillApi = skillApi;
                initRobotSpeechCallback();
            });
        }
    }

    private void initRobotSpeechCallback() {
        if (skillApi == null) return;
        skillApi.setWakeupHintClosed(LocalTtsController.isEnabledMode());
        synchronized (speechCallbackLock) {
            unregisterWakeupCallbackLocked();
            unregisterLongPickupCallbackLocked();
            wakeUpSkillCallback = new WakeUpSkillCallback();
            wakeUpSkillCallback.setListener(new WakeUpSkillCallback.SpeechResultListener() {
                @Override
                public void onAsrResult(String asrResult) { /* Native KWS only; SparkChain owns text. */ }
                @Override public void onStart() { }
                @Override public void onPartial(String text) { }
                @Override public void onQueryEnded(int status) { }

                @Override
                public void onStop() {
                }
            });
            try {
                registerWakeupCallbackLocked();
                skillApi.setASREnabled(true);
                skillApi.setRecognizable(true);
                skillApi.setRecognizeMode(false);
                log(LogLevel.INFO, "唤醒词模式注册成功，可接收识别结果=" + skillApi.isRecognizable());
            } catch (Exception e) {
                log(LogLevel.ERROR, "唤醒词注册失败: " + e.getMessage());
            }
        }
    }

    private void initMockMode() {
        mockTts = new TextToSpeech(applicationContext, status -> {
            if (status == TextToSpeech.SUCCESS) {
                int result = mockTts.setLanguage(Locale.CHINESE);
                mockTtsReady = result != TextToSpeech.LANG_MISSING_DATA && result != TextToSpeech.LANG_NOT_SUPPORTED;
            }
        });
    }

    private void speakOnMock(String text, SpeakCallback callback) {
        if (!mockTtsReady || mockTts == null) {
            isSpeaking.set(false);
            currentTtsTextLength = 0;
            if (callback != null) callback.onSuccess();
            processNextTts();
            return;
        }

        startTtsTimeout(text.length());

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            if (mockTts.speak(text, TextToSpeech.QUEUE_FLUSH, null, null) == TextToSpeech.SUCCESS) {
                pendingSpeakCallback = callback;
                long delay = Math.max(text.length() * MOCK_SPEAK_DELAY_PER_CHAR_MS, 100);
                mainHandler.sendEmptyMessageDelayed(MSG_MOCK_SPEAK, delay);
            } else {
                isSpeaking.set(false);
                currentTtsTextLength = 0;
                cancelTtsTimeout();
                if (callback != null) callback.onFailed("TTS失败");
                processNextTts();
            }
        }
    }

    private void handleMockSpeakComplete() {
        isSpeaking.set(false);
        currentTtsTextLength = 0;
        cancelTtsTimeout();
        SpeakCallback cb = pendingSpeakCallback;
        if (cb != null) {
            cb.onSuccess();
            pendingSpeakCallback = null;
        }

        notifyVoiceIntroductionCompleted();

        processNextTts();
    }

    public void handleFaceRegister(Person person, TcpService tcpService) {
        int requestId = Integer.parseInt(person.getRemoteReqId());
        log(LogLevel.INFO, "发起人脸注册请求: " + person.getName() + " requestId=" + requestId);

        RobotApi.getInstance().startRegister(
                requestId,
                person.getName(),
                AppConstants.REGISTER_FACE_TIMEOUT,
                AppConstants.REGISTER_FACE_TRY_COUNT,
                AppConstants.REGISTER_FACE_SECOND_DELAY,
                new ActionListener() {
                    @Override
                    public void onResult(int status, String response, String extraData) {
                        if (Definition.RESULT_OK != status) {
                            log(LogLevel.ERROR, "注册失败: " + status);
                            tcpService.sendFaceRegisterFailed(robotId, requestId, person.getName(), "reg_failed");
                            return;
                        }
                        try {
                            JSONObject json = new JSONObject(response);
                            log(LogLevel.INFO, "机器人返回:" + response);
                            String remoteType = json.optString(Definition.REGISTER_REMOTE_TYPE);
                            String remoteName = json.optString(Definition.REGISTER_REMOTE_NAME);

                            if (Definition.REGISTER_REMOTE_SERVER_EXIST.equals(remoteType)) {
                                log(LogLevel.WARN, "用户已存在: " + remoteName);
                                tcpService.sendFaceRegisterFailed(robotId, requestId, person.getName(), "exist_user");
                            } else if (Definition.REGISTER_REMOTE_SERVER_NEW.equals(remoteType)) {
                                log(LogLevel.INFO, "新用户注册成功: " + remoteName);
                                String userId = json.optString("userId");
                                tcpService.sendFaceRegisterSuccess(robotId, requestId, person.getName(), userId);
                            }
                        } catch (JSONException e) {
                            log(LogLevel.ERROR, "解析失败: " + e.getMessage());
                            tcpService.sendFaceRegisterFailed(robotId, requestId, person.getName(), "json_error");
                        } catch (Exception e) {
                            log(LogLevel.ERROR, "人脸注册异常: " + e.getMessage());
                            tcpService.sendFaceRegisterFailed(robotId, requestId, person.getName(), "exception");
                        }
                    }
                }
        );
    }

    @RequiresApi(api = Build.VERSION_CODES.JELLY_BEAN_MR2)
    public void destroy() {
        restoreSystemWakeupHint();
        cancelCurrentTask();
        mainHandler.removeCallbacksAndMessages(null);
        if (faceHandler != null) {
            faceHandler.removeCallbacksAndMessages(null);
            faceHandlerThread.quitSafely();
        }
        stopFaceRecognize();
        cancelListenTimeout();
        cancelTtsTimeout();
        stopTTS();
        wakeupResponsePlayer.release();
        stopListening();
        iflytekAsrController.release();
        stopVideoLoop();
        ttsQueue.clear();
        
        // 停止音乐并释放音频焦点
        stopMusic();
        abandonAudioFocus();
        
        if (mockTts != null) {
            mockTts.stop();
            mockTts.shutdown();
            mockTts = null;
        }
        if (skillApi != null) {
            try {
                synchronized (speechCallbackLock) {
                    unregisterWakeupCallbackLocked();
                    unregisterLongPickupCallbackLocked();
                }
            } catch (Exception e) {
                log(LogLevel.ERROR, "注销语音回调失败: " + e.getMessage());
            }
        }
        voiceCommandCallback = null;
        faceListener = null;
        wakeUpSkillCallback = null;
        longPickupSkillCallback = null;
        skillApi = null;
        listenCallback = null;
        audioManager = null;
        audioFocusRequest = null;
        MyRobotApplication app = MyRobotApplication.getInstance();
        if (app != null) app.setSdkReadyListener(null);
        log(LogLevel.INFO, "RobotCapabilities 已销毁");
    }

    private void log(LogLevel level, String msg) {
        if (logCallback != null && level != LogLevel.VERBOSE)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                logCallback.accept("[" + level + "] " + msg);
            }
        switch (level) {
            case DEBUG:
                Log.d(TAG, msg);
                break;
            case INFO:
                Log.i(TAG, msg);
                break;
            case WARN:
                Log.w(TAG, msg);
                break;
            case ERROR:
                Log.e(TAG, msg);
                break;
            default:
                Log.v(TAG, msg);
        }
    }
    
    /**
     * 请求音频焦点
     */
    private boolean requestAudioFocus() {
        if (audioManager == null) {
            return false;
        }
        
        if (hasAudioFocus) {
            return true;
        }
        
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                // Android 8.0+ 使用 AudioFocusRequest
                audioFocusRequest = new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
                    .setAudioAttributes(new AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                        .build())
                    .setOnAudioFocusChangeListener(focusChange -> {
                        handleAudioFocusChange(focusChange);
                    })
                    .build();
                
                int result = audioManager.requestAudioFocus(audioFocusRequest);
                hasAudioFocus = (result == AudioManager.AUDIOFOCUS_REQUEST_GRANTED);
            } else {
                // Android 8.0 以下使用旧API
                int result = audioManager.requestAudioFocus(
                    focusChange -> handleAudioFocusChange(focusChange),
                    AudioManager.STREAM_MUSIC,
                    AudioManager.AUDIOFOCUS_GAIN
                );
                hasAudioFocus = (result == AudioManager.AUDIOFOCUS_REQUEST_GRANTED);
            }
            
            return hasAudioFocus;
        } catch (Exception e) {
            log(LogLevel.ERROR, "请求音频焦点失败: " + e.getMessage());
            return false;
        }
    }
    
    /**
     * 放弃音频焦点
     */
    private void abandonAudioFocus() {
        if (!hasAudioFocus || audioManager == null) {
            return;
        }
        
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                if (audioFocusRequest != null) {
                    audioManager.abandonAudioFocusRequest(audioFocusRequest);
                    audioFocusRequest = null;
                }
            } else {
                audioManager.abandonAudioFocus(null);
            }
            hasAudioFocus = false;
            log(LogLevel.INFO, "已放弃音频焦点");
        } catch (Exception e) {
            log(LogLevel.ERROR, "放弃音频焦点失败: " + e.getMessage());
        }
    }
    
    /**
     * 处理音频焦点变化
     */
    private void handleAudioFocusChange(int focusChange) {
        switch (focusChange) {
            case AudioManager.AUDIOFOCUS_LOSS:
                log(LogLevel.WARN, "失去音频焦点，停止播放");
                if (currentMusicPlayer != null && currentMusicPlayer.isPlaying()) {
                    currentMusicPlayer.pause();
                }
                break;
            case AudioManager.AUDIOFOCUS_LOSS_TRANSIENT:
                log(LogLevel.WARN, "暂时失去音频焦点，暂停播放");
                if (currentMusicPlayer != null && currentMusicPlayer.isPlaying()) {
                    currentMusicPlayer.pause();
                }
                break;
            case AudioManager.AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK:
                log(LogLevel.INFO, "可以降低音量");
                if (currentMusicPlayer != null) {
                    currentMusicPlayer.setVolume(0.3f, 0.3f);
                }
                break;
            case AudioManager.AUDIOFOCUS_GAIN:
                log(LogLevel.INFO, "重新获得音频焦点");
                if (currentMusicPlayer != null) {
                    currentMusicPlayer.setVolume(1.0f, 1.0f);
                    if (!currentMusicPlayer.isPlaying()) {
                        currentMusicPlayer.start();
                    }
                }
                break;
        }
    }
}
