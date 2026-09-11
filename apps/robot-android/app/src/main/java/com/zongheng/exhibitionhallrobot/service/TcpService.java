package com.zongheng.exhibitionhallrobot.service;

import android.annotation.SuppressLint;
import android.app.Service;
import android.content.Intent;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.os.Looper;

import com.ainirobot.coreservice.client.listener.Person;
import com.zongheng.exhibitionhallrobot.CustomPhotoCaptureActivity;
import com.zongheng.exhibitionhallrobot.application.MyRobotApplication;
import com.zongheng.exhibitionhallrobot.monitor.BatteryMonitor;
import com.zongheng.exhibitionhallrobot.util.LogUtils;
import com.zongheng.exhibitionhallrobot.util.ReqIdUtils;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.Charset;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

public class TcpService extends Service {
    private static final String TAG = "TcpService";
    private static final long SPEECH_DUPLICATE_WINDOW_MS = 1500L;
    private static final long PENDING_SPEECH_TTL_MS = 15_000L;
    private static final int MAX_PENDING_SPEECH = 3;

    private final Object speechEventLock = new Object();
    private String lastSpeechText = "";
    private long lastSpeechAt = 0L;
    private final ArrayDeque<PendingSpeech> pendingSpeech = new ArrayDeque<>();

    private static final class PendingSpeech {
        final String text;
        final long createdAt;

        PendingSpeech(String text, long createdAt) {
            this.text = text;
            this.createdAt = createdAt;
        }
    }

    private static final int MAX_RECONNECT_DELAY_SEC = 30;
    private static final int MAX_RECONNECT_COUNT = 3;
    private static final int SOCKET_TIMEOUT_MS = 5000;
    private static final int TTS_PROTECTION_TIMEOUT_MS = 90000; // 优化：增加到90秒，支持更长文本
    private static final int HIGH_PRIORITY_TTS_DELAY_MS = 20; // 优化：进一步降低高优先级延迟
    private static final int NORMAL_PRIORITY_TTS_DELAY_MS = 50; // 优化：进一步降低普通优先级延迟
    // 中枢主动收音超时边界。实际值由 cmd_listen.timeout 下发；旧服务端缺省仍兼容 5 秒。
    private static final int DEFAULT_CMD_LISTEN_TIMEOUT_SEC = 5;
    private static final int MIN_CMD_LISTEN_TIMEOUT_SEC = 5;
    // Keep the microphone open for at most five seconds, even when an older
    // V5 server still requests the previous 30-second continuation window.
    private static final int MAX_CMD_LISTEN_TIMEOUT_SEC = 5;
    private static final int MAX_CONVERSATION_MESSAGES = 20;
    private static final String DEFAULT_PHOTO_POINT_NAME = "拍照点";

    private String serverIp = "192.168.18.227";
    private int serverPort = 19888;

    private Socket socket;
    private BufferedWriter writer;
    private BufferedReader reader;

    private final ExecutorService readExecutor = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "TcpReadThread");
        t.setDaemon(true);
        return t;
    });

    private final ScheduledExecutorService heartbeatExecutor = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "TcpHeartbeatThread");
        t.setDaemon(true);
        return t;
    });

    private ScheduledFuture<?> heartbeatTask;
    private final AtomicInteger seqGenerator = new AtomicInteger(0);
    private int heartbeatIntervalSec = 30;

    private final AtomicBoolean isConnected = new AtomicBoolean(false);
    private final AtomicBoolean isConnecting = new AtomicBoolean(false);
    private volatile boolean isManualStop = false;

    private int reConnectDelay = 1;
    private int reConnectCount = 0;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private OnLogListener logListener;

    private RobotCapabilities robotCapabilities;
    private BatteryMonitor batteryMonitor;

    private final String robotId = "robot_001";

    private final ExecutorService sendExecutor = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "TcpSendThread");
        t.setDaemon(true);
        return t;
    });

    private HandlerThread mWorkThread;
    private Handler mWorkHandler;

    private final AtomicInteger reconnectTaskId = new AtomicInteger(0);
    private volatile boolean isReconnectScheduled = false;

    private final AtomicInteger ttsTaskId = new AtomicInteger(0);
    private volatile int currentTtsId = -1;
    private Runnable ttsProtectionRunnable;

    private static final int STATE_IDLE = 0;
    private static final int STATE_CMD_LISTEN = 1;

    private volatile int listenState = STATE_IDLE;
    private HandlerThread listenThread;
    private Handler listenHandler;
    private final TeachingSessionController teachingController = new TeachingSessionController();
    private volatile boolean robotCharging;

    private int lastPersonCount = -1;

    public interface OnLogListener {
        void onLog(String log);
    }

    public interface OnConnectionListener {
        void onConnected();

        void onDisconnected(String reason);
    }

    private OnConnectionListener connectionListener;

    public static final class ConversationEntry {
        public static final String ROLE_USER = "user";
        public static final String ROLE_ASSISTANT = "assistant";
        public static final String ROLE_STAFF = "staff";
        public static final String ROLE_STATUS = "status";

        private final String role;
        private final String text;

        ConversationEntry(String role, String text) {
            this.role = role;
            this.text = text;
        }

        public String getRole() {
            return role;
        }

        public String getText() {
            return text;
        }
    }

    public interface OnConversationListener {
        void onConversationChanged(List<ConversationEntry> entries);
    }

    private final Object conversationLock = new Object();
    private final List<ConversationEntry> conversationHistory = new ArrayList<>();
    private volatile OnConversationListener conversationListener;

    public class LocalBinder extends android.os.Binder {
        public TcpService getService() {
            return TcpService.this;
        }
    }

    private final IBinder binder = new LocalBinder();
    private final Object lock = new Object();
    private volatile boolean isClosing = false;

    @Override
    public void onCreate() {
        super.onCreate();

        MyRobotApplication app = MyRobotApplication.getInstance();
        if (app != null) {
            app.setTcpService(this);
        }

        mWorkThread = new HandlerThread("TcpWorkThread");
        mWorkThread.start();
        mWorkHandler = new Handler(mWorkThread.getLooper());

        listenThread = new HandlerThread("ListenThread");
        listenThread.start();
        listenHandler = new Handler(listenThread.getLooper());

        initComponents();
    }

    private void initComponents() {
        robotCapabilities = new RobotCapabilities(this, this::log);
        robotCapabilities.setVoiceCommandCallback(this::submitSpeechResult);
        robotCapabilities.setSpeechPreviewCallback(this::updateSpeechPreview);

        // 设置视频关闭回调
        robotCapabilities.setVideoCloseCallback(() -> {
            log("视频被关闭，发送事件到中枢");
            JSONObject data = new JSONObject();
            try {
                data.put("reason", "user_closed");
                data.put("ts", System.currentTimeMillis());
            } catch (JSONException e) {
                LogUtils.e(TAG, "构建视频关闭数据失败", e);
            }
            sendEvent("evt_video_closed", data);
        });

        robotCapabilities.setFaceListener(new RobotCapabilities.FaceListener() {
            @Override
            public void onFaceRecognized(String robotId, String userId, String name, double confidence) {
                // 优化：只发送事件，不在这里打印日志（由 RobotCapabilities 控制）
                sendFaceRecognized(robotId, userId, name, confidence);
            }

            @Override
            public void updateFacePhoto(String robotId, String userId, String name, String base64) {
                // 优化：只发送事件，不打印日志
                sendFacePhoto(robotId, userId, name, base64);
            }

            @Override
            public void updatePersonCount(String robotId, int personCount) {
                if (personCount != lastPersonCount) {
                    lastPersonCount = personCount;
                    sendPersonCount(robotId, personCount);
                }
            }

            @Override
            public void onPersonDetail(String robotId, String personDataJson) {
                // [v7.0] Person详细信息转发到后端
                try {
                    JSONObject data = new JSONObject(personDataJson);
                    data.put("ts", System.currentTimeMillis());
                    sendEvent("evt_person_detail", data);
                } catch (Exception e) {
                    log("上报Person详情失败: " + e.getMessage());
                }
            }
        });

        this.batteryMonitor = new BatteryMonitor(this, robotCapabilities, (percent, charging) -> {
            robotCharging = charging;
            JSONObject batteryData = createData("battery", percent);
            try {
                batteryData.put("charging", charging);
                if (teachingController.isActive()) {
                    batteryData.put("teachingTurnId", teachingController.getTurnId());
                }
            } catch (JSONException e) {
                LogUtils.e(TAG, "构建电量事件失败", e);
            }
            sendEvent("evt_battery", batteryData);
            if (charging) {
                cancelTeachingCapture("charging", true, "状态：机器人充电，教学已取消");
            }
            if (percent <= 1) goOffline("电量不足");
        });
    }

    /**
     * Unified entry for final ASR text from both RobotOS ModuleCallback and SkillCallback.
     */
    public void submitSpeechResult(String command) {
        String text = command == null ? "" : command.trim();
        if (text.isEmpty()) return;
        if (!teachingController.isActive()
                && android.os.SystemClock.elapsedRealtime() < teachingSuppressUntil) return;

        // RobotOS 在教学窗口内偶尔仍可能从全局 ModuleCallback 回送文本。
        // 先交给教学通道，绝不能落入普通控制/问答链。
        if (teachingController.isActive()) {
            if (com.zongheng.exhibitionhallrobot.speech.IflytekAsrController.isEnabledMode()) {
                updateSpeechPreview(text);
                return;
            }
            handleTeachingSpeechResult(text, 3000L);
            return;
        }

        long now = System.currentTimeMillis();
        synchronized (speechEventLock) {
            if (text.equals(lastSpeechText) && now - lastSpeechAt < SPEECH_DUPLICATE_WINDOW_MS) {
                log("忽略重复语音识别结果: " + text);
                return;
            }
            lastSpeechText = text;
            lastSpeechAt = now;
        }

        recordConversation(ConversationEntry.ROLE_USER, text);
        log("语音指令: " + text + ", 当前TTS状态: currentTtsId=" + currentTtsId);

        robotCapabilities.pauseFaceProcessing(25_000L);

        if (!isConnected.get()) {
            synchronized (speechEventLock) {
                while (pendingSpeech.size() >= MAX_PENDING_SPEECH) {
                    pendingSpeech.removeFirst();
                }
                pendingSpeech.addLast(new PendingSpeech(text, now));
            }
            log("TCP尚未连接，已暂存语音识别结果");
            return;
        }

        dispatchSpeechResult(text);
    }

    private void dispatchSpeechResult(String text) {

        // 所有语音指令统一发给 Hub 中枢处理（唱歌/放歌等由中枢意图识别处理）
        interruptCurrentTts();

        JSONObject data = new JSONObject();
        try {
            data.put("text", text);
            data.put("confidence", 0.95);
            data.put("duration", 3000);
        } catch (JSONException e) {
            LogUtils.e(TAG, "构建语音数据失败", e);
            return;
        }
        sendEvent("evt_speech_result", data);
        log("已发送语音识别结果事件到中枢");
    }

    private void flushPendingSpeechResults() {
        List<String> ready = new ArrayList<>();
        long now = System.currentTimeMillis();
        synchronized (speechEventLock) {
            while (!pendingSpeech.isEmpty()) {
                PendingSpeech item = pendingSpeech.removeFirst();
                if (now - item.createdAt <= PENDING_SPEECH_TTL_MS) {
                    ready.add(item.text);
                }
            }
        }
        for (String text : ready) {
            log("连接恢复，发送暂存语音识别结果");
            dispatchSpeechResult(text);
        }
    }

    private void connect() {
        if (!isConnecting.compareAndSet(false, true)) {
            log("已在连接中或已连接，跳过");
            return;
        }

        if (isConnected.get()) {
            isConnecting.set(false);
            log("已经连接，跳过");
            return;
        }

        isManualStop = false;

        mWorkHandler.post(() -> {
            Socket tempSocket = null;
            try {
                log("正在连接 " + serverIp + ":" + serverPort);

                tempSocket = new Socket();
                tempSocket.connect(new InetSocketAddress(serverIp, serverPort), SOCKET_TIMEOUT_MS);

                BufferedWriter tempWriter = new BufferedWriter(
                        new OutputStreamWriter(tempSocket.getOutputStream(), Charset.forName("UTF-8")));
                BufferedReader tempReader = new BufferedReader(
                        new InputStreamReader(tempSocket.getInputStream(), Charset.forName("UTF-8")));

                synchronized (lock) {
                    socket = tempSocket;
                    writer = tempWriter;
                    reader = tempReader;
                }

                isConnected.set(true);
                reConnectDelay = 1;
                reConnectCount = 0;
                isReconnectScheduled = false;

                log("✅ 连接成功");
                startReading();

                robotCapabilities.setRobotId(robotId);

                JSONObject config = new JSONObject();
                config.put("robotId", robotId);
                config.put("version", "1.0.0");
                sendEvent("evt_get_config", config);
                flushPendingSpeechResults();

                if (batteryMonitor != null) {
                    batteryMonitor.setConnected(true);
                    batteryMonitor.startMonitoring();
                }

                OnConnectionListener connectedListener = connectionListener;
                if (connectedListener != null) {
                    mainHandler.post(connectedListener::onConnected);
                }

            } catch (Exception e) {
                log("❌️ 连接失败: " + e.getMessage());
                isConnected.set(false);

                if (tempSocket != null) {
                    try {
                        tempSocket.close();
                    } catch (IOException ex) {
                        LogUtils.e(TAG, "关闭socket失败", ex);
                    }
                }

                scheduleReconnect();
            } finally {
                isConnecting.set(false);
            }
        });
    }

    private void startReading() {
        if (!isConnected.get()) return;

        readExecutor.execute(() -> {
            BufferedReader localReader;
            synchronized (lock) {
                localReader = reader;
            }

            if (localReader == null) return;

            try {
                String line;
                while (isConnected.get() && (line = localReader.readLine()) != null) {
                    if (line.trim().isEmpty()) continue;
                    final String msg = line;
                    mWorkHandler.post(() -> {
                        synchronized (lock) {
                            if (reader != localReader) return;
                        }
                        handleMessage(msg);
                    });
                }
                log("❌️ 服务器主动断开连接");

            } catch (Exception e) {
                log("❌️ 读取异常: " + e.getMessage());
            } finally {
                synchronized (lock) {
                    if (reader == localReader && isConnected.get() && !isManualStop) {
                        handleDisconnect("连接断开");
                    }
                }
            }
        });
    }

    private void handleDisconnect(String reason) {
        cancelTeachingCapture("disconnect", false, "状态：连接断开，教学已取消");
        OnConnectionListener disconnectedListener = connectionListener;
        if (disconnectedListener != null) {
            mainHandler.post(() -> disconnectedListener.onDisconnected(reason));
        }

        if (isManualStop) {
            log("手动断开，无需重连");
            return;
        }

        isConnected.set(false);
        closeQuietly();
        scheduleReconnect();
    }



    private void handleMessage(String jsonStr) {
        try {
            JSONObject json = new JSONObject(jsonStr);
            String type = json.getString("type");
            int seq = json.optInt("seq", -1);
            JSONObject data = json.optJSONObject("data");
            if (data == null) data = new JSONObject();

            log("[中枢->机器人->" + type + "]" + data);

            switch (type) {
                case "ack_config":
                    handleAckConfig(data, seq);
                    sendRegister();
                    break;
                case "ack_register":
                    startHeartbeat();
                    break;
                case "cmd_navigate":
                    handleCmdNavigate(data, seq);
                    break;
                case "cmd_tts":
                    handleCmdTts(data, seq);
                    break;
                case "cmd_teaching_state":
                    handleTeachingState(data);
                    break;
                case "cmd_face_register_start":
                    handleCmdFaceRegister(data);
                    break;
                case "cmd_listen":
                    handleCmdListen(data, seq);
                    break;
                case "cmd_show":
                    handleShow(data, seq);
                    break;
                case "cmd_take_photo":
                    handleCmdTakePhoto(data, seq);
                    break;
                case "cmd_close_photo":
                    handleCmdClosePhoto(data, seq);
                    break;
                case "cmd_cancel":
                    handleCancel(seq, data.optString("reason", "用户主动中断"));
                    break;
                case "cmd_query_status":
                    handleQueryStatus(seq);
                    break;
                case "cmd_play_music":
                    handleCmdPlayMusic(data, seq);
                    break;
                case "cmd_stop_music":
                    log("收到停止音乐指令");
                    robotCapabilities.stopMusic();
                    if (seq > 0) {
                        JSONObject ack = new JSONObject();
                        try {
                            ack.put("success", true);
                        } catch (JSONException e) {
                            LogUtils.e(TAG, "构建停止音乐ack失败", e);
                        }
                        sendAck(seq, "evt_music_stopped", ack);
                    }
                    break;
                case "cmd_volume":
                    log("收到音量调节指令");
                    String direction = data.optString("direction", "up");
                    robotCapabilities.adjustVolume(direction);
                    if (seq > 0) {
                        try {
                            JSONObject ack = new JSONObject();
                            ack.put("success", true);
                            ack.put("direction", direction);
                            sendAck(seq, "evt_volume_adjusted", ack);
                        } catch (JSONException e) {
                            LogUtils.e(TAG, "构建音量ack失败", e);
                        }
                    }
                    break;
                default:
                    log("未知命令: " + type);
            }
        } catch (JSONException e) {
            log("解析失败: " + e.getMessage());
        }
    }

    private void handleCmdNavigate(JSONObject data, int seq) {
        String pointName = data.optString("pointName");
        int pointId = data.optInt("pointId");

        robotCapabilities.navigateTo(pointName, new RobotCapabilities.NavigateCallback() {
            @Override
            public void onSuccess(String name) {
                JSONObject result = new JSONObject();
                try {
                    result.put("pointId", pointId);
                    result.put("pointName", name);
                } catch (JSONException e) {
                    LogUtils.e(TAG, "构建导航成功结果失败", e);
                }
                sendAck(seq, "evt_arrived", result);
            }

            @Override
            public void onFailed(String error) {
                JSONObject result = new JSONObject();
                try {
                    result.put("reason", error);
                    result.put("currentLocation", robotCapabilities.getCurrentLocation());
                } catch (JSONException e) {
                    LogUtils.e(TAG, "构建导航失败结果失败", e);
                }
                sendAck(seq, "evt_nav_failed", result);
            }
        });
    }

    private void handleCmdTakePhoto(JSONObject data, int seq) {
        String photoPointName = data.optString("photoPointName",
                data.optString("pointName", DEFAULT_PHOTO_POINT_NAME));
        String qrContent = data.optString("photoUrl",
                data.optString("downloadUrl", data.optString("qrContent", "")));
        // uploadUrl 由服务端生成，使用 LAN IP 供 Android 上传照片。
        // 与 qrContent（公网 IP/域名，供扫码下载）分开，解决公网 IP 白名单拦截上传的问题。
        String uploadUrl = data.optString("uploadUrl", "");
        boolean skipNav = data.optBoolean("skipNav", false);

        // 先检查是否已在拍照点（需要在 stopFaceRecognize/stopFocusFollow 之前检查，
        // 因为 stopFocusFollow 会导致机器人物理旋转回导航到达时的方向）。
        boolean atPhotoPoint = false;
        if (!skipNav && robotCapabilities != null) {
            String cur = robotCapabilities.getCurrentLocation();
            atPhotoPoint = (cur != null && cur.equals(photoPointName));
        }

        // 拍照前关闭人脸跟随 + 焦点跟随，避免干扰。
        // 但若已在拍照点或重拍时跳过此步，以免机器人旋转回导航到达方向。
        if (robotCapabilities != null && !atPhotoPoint && !skipNav) {
            robotCapabilities.stopFaceRecognize();
            robotCapabilities.stopFocusFollow();
        }

        if (skipNav) {
            log("收到重拍指令，跳过导航。新 qrContent: " + qrContent);

            // 生成新 sessionId 并注册回调 (重拍后的照片保存时会通过此回调返回)
            String newSessionId = RobotCapabilities.registerPhotoCallback(
                    new RobotCapabilities.PhotoCallback() {
                @Override
                public void onSuccess(String photoPath, android.net.Uri photoUri) {
                    // 上传新照片到中枢 (使用服务端下发的 uploadUrl LAN IP 地址，绕过公网白名单)
                    PhotoUploadResult uploadResult = uploadPhotoToHub(photoPath, qrContent, uploadUrl);
                    if (!uploadResult.success) {
                        JSONObject failed = new JSONObject();
                        try {
                            failed.put("success", false);
                            failed.put("reason", uploadResult.message);
                            failed.put("photoPath", photoPath);
                            failed.put("downloadUrl", qrContent == null ? "" : qrContent);
                        } catch (JSONException e) {
                            LogUtils.e(TAG, "构建重拍上传失败结果失败", e);
                        }
                        sendAck(seq, "evt_photo_failed", failed);
                        return;
                    }
                    JSONObject result = new JSONObject();
                    try {
                        result.put("success", true);
                        result.put("pointName", "拍照点");
                        result.put("photoPath", photoPath);
                        result.put("photoUri", photoUri != null ? photoUri.toString() : "");
                        result.put("delivery", "qrcode_only");
                        result.put("qrPosition", "bottom_right");
                        result.put("downloadUrl", qrContent == null ? "" : qrContent);
                        result.put("uploadStatus", uploadResult.httpCode);
                        result.put("photoId", uploadResult.photoId);
                    } catch (JSONException e) {
                        LogUtils.e(TAG, "构建重拍成功结果失败", e);
                    }
                    sendAck(seq, "evt_photo_done", result);
                }

                @Override
                public void onFailed(String error) {
                    JSONObject result = new JSONObject();
                    try {
                        result.put("success", false);
                        result.put("reason", error);
                    } catch (JSONException e) {
                        LogUtils.e(TAG, "构建重拍失败结果失败", e);
                    }
                    sendAck(seq, "evt_photo_failed", result);
                }
            });

            // 通知 Activity 在当前页面重拍
            CustomPhotoCaptureActivity.retakePhoto(qrContent, newSessionId);
            return;
        }

        // [v4] 如果已在拍照点，跳过导航直接启动拍照
        if (atPhotoPoint) {
            log("已在拍照点(" + photoPointName + ")，跳过导航直接拍照");
            robotCapabilities.speak(seq, "已在拍照点，准备拍照。请大家站到拍照区域，面向屏幕。",
                    "interrupt", new RobotCapabilities.SpeakCallback() {
                @Override
                public void onSuccess() {
                    startPhotoCapture(seq, photoPointName, qrContent, uploadUrl);
                }
                @Override
                public void onFailed(String error) {
                    startPhotoCapture(seq, photoPointName, qrContent, uploadUrl);
                }
            });
            return;
        }

        // 需要导航 → 这时才关闭人脸/焦点跟随（上面已执行 stopFaceRecognize/stopFocusFollow）
        log("收到拍照指令，先导航到拍照点: " + photoPointName);
        robotCapabilities.speak(seq, "好的，我带您去拍照点。", "interrupt", new RobotCapabilities.SpeakCallback() {
            @Override
            public void onSuccess() {
                navigateToPhotoPoint(seq, photoPointName, qrContent, uploadUrl);
            }

            @Override
            public void onFailed(String error) {
                navigateToPhotoPoint(seq, photoPointName, qrContent, uploadUrl);
            }
        });
    }

    private void navigateToPhotoPoint(int seq, String photoPointName, String qrContent, String uploadUrl) {
        robotCapabilities.navigateTo(photoPointName, new RobotCapabilities.NavigateCallback() {
            @Override
            public void onSuccess(String pointName) {
                log("已到达拍照点，开始拍照前准备");
                robotCapabilities.speak(seq, "已经到达拍照点啦。请大家站到拍照区域，面向屏幕，小豹给大家准备一下。", "interrupt", new RobotCapabilities.SpeakCallback() {
                    @Override
                    public void onSuccess() {
                        startPhotoCapture(seq, pointName, qrContent, uploadUrl);
                    }

                    @Override
                    public void onFailed(String error) {
                        startPhotoCapture(seq, pointName, qrContent, uploadUrl);
                    }
                });
            }

            @Override
            public void onFailed(String error) {
                // 导航到拍照点失败(可能已在拍照点或附近)，直接进入拍照，不报错
                log("导航到拍照点失败(" + error + ")，直接进入拍照流程");
                robotCapabilities.speak(seq, "已在拍照点，准备拍照。请大家站到拍照区域，面向屏幕。",
                        "interrupt", new RobotCapabilities.SpeakCallback() {
                            @Override
                            public void onSuccess() {
                                startPhotoCapture(seq, photoPointName, qrContent, uploadUrl);
                            }
                            @Override
                            public void onFailed(String e) {
                                startPhotoCapture(seq, photoPointName, qrContent, uploadUrl);
                            }
                        });
            }
        });
    }

    private void startPhotoCapture(int seq, String pointName, String qrContent, String uploadUrl) {
        robotCapabilities.takePhoto(qrContent, new RobotCapabilities.PhotoCallback() {
                    @Override
                    public void onSuccess(String photoPath, android.net.Uri photoUri) {
                        // 上传照片到中枢服务器，供二维码下载。必须上传成功后才回 evt_photo_done，
                        // 否则中枢会提示用户扫码，但服务端实际没有文件，扫码会看到 Photo not found。
                        // uploadUrl 使用服务端下发的 LAN IP 地址，绕过公网 IP 白名单。
                        PhotoUploadResult uploadResult = uploadPhotoToHub(photoPath, qrContent, uploadUrl);
                        if (!uploadResult.success) {
                            JSONObject failed = new JSONObject();
                            try {
                                failed.put("success", false);
                                failed.put("reason", uploadResult.message);
                                failed.put("pointName", pointName);
                                failed.put("photoPath", photoPath);
                                failed.put("downloadUrl", qrContent == null ? "" : qrContent);
                            } catch (JSONException e) {
                                LogUtils.e(TAG, "构建照片上传失败结果失败", e);
                            }
                            sendAck(seq, "evt_photo_failed", failed);
                            return;
                        }

                        // 注意: v2 不在此恢复人脸识别 — 等到 cmd_close_photo 时再恢复
                        JSONObject result = new JSONObject();
                        try {
                            result.put("success", true);
                            result.put("pointName", pointName);
                            result.put("photoPath", photoPath);
                            result.put("photoUri", photoUri != null ? photoUri.toString() : "");
                            result.put("delivery", "qrcode_only");
                            result.put("qrPosition", "bottom_right");
                            result.put("downloadUrl", qrContent == null ? "" : qrContent);
                            result.put("uploadStatus", uploadResult.httpCode);
                            result.put("photoId", uploadResult.photoId);
                        } catch (JSONException e) {
                            LogUtils.e(TAG, "构建拍照成功结果失败", e);
                        }
                        sendAck(seq, "evt_photo_done", result);
                    }

                    @Override
                    public void onFailed(String error) {
                        LogUtils.d(TAG, "PhotoCallback.onFailed");
                        JSONObject result = new JSONObject();
                        try {
                            result.put("success", false);
                            result.put("reason", error);
                            result.put("pointName", pointName);
                        } catch (JSONException e) {
                            LogUtils.e(TAG, "构建拍照失败结果失败", e);
                        }
                        sendAck(seq, "evt_photo_failed", result);
                    }
                });
    }

    /**
     * 处理关闭拍照页面指令。
     * 收到 cmd_close_photo 后:
     * 1. 关闭 CustomPhotoCaptureActivity (释放相机)
     * 2. 恢复人脸跟随
     * 3. 回复 evt_photo_closed
     */
    private void handleCmdClosePhoto(JSONObject data, int seq) {
        log("收到关闭拍照页面指令");
        // 关闭 Activity (释放相机 & 资源)
        CustomPhotoCaptureActivity.closePhotoResult();
        // 恢复人脸识别 (拍照期间关闭了人脸跟随)
        if (robotCapabilities != null) {
            robotCapabilities.startFaceRecognize();
        }
        JSONObject result = new JSONObject();
        try {
            result.put("success", true);
        } catch (JSONException e) {
            LogUtils.e(TAG, "构建关闭拍照页结果失败", e);
        }
        sendAck(seq, "evt_photo_closed", result);
    }

    private static class PhotoUploadResult {
        boolean success;
        int httpCode;
        String photoId = "";
        String uploadUrl = "";
        String message = "";

        static PhotoUploadResult ok(int httpCode, String photoId, String uploadUrl) {
            PhotoUploadResult r = new PhotoUploadResult();
            r.success = true;
            r.httpCode = httpCode;
            r.photoId = photoId;
            r.uploadUrl = uploadUrl;
            r.message = "上传成功";
            return r;
        }

        static PhotoUploadResult fail(String message, int httpCode, String photoId, String uploadUrl) {
            PhotoUploadResult r = new PhotoUploadResult();
            r.success = false;
            r.httpCode = httpCode;
            r.photoId = photoId == null ? "" : photoId;
            r.uploadUrl = uploadUrl == null ? "" : uploadUrl;
            r.message = message == null ? "上传失败" : message;
            return r;
        }
    }

    /**
     * 上传照片文件到中枢服务器，以便二维码可下载。
     * 优先使用 serverUploadUrl（由服务端下发，使用 LAN IP 绕过公网白名单），
     * 仅当为空时从 qrContent 构造上传地址。
     * qrContent 标准格式: http://{ip}:{port}/api/photo/download/{photo_id}.jpg
     */
    private PhotoUploadResult uploadPhotoToHub(String photoPath, String qrContent, String serverUploadUrl) {
        String photoId = "";
        String uploadUrl = "";
        if (qrContent == null || qrContent.trim().isEmpty()) {
            String msg = "上传照片失败: 二维码下载地址为空";
            log(msg);
            return PhotoUploadResult.fail(msg, 0, photoId, uploadUrl);
        }

        try {
            photoId = extractPhotoId(qrContent);
            if (photoId.isEmpty()) {
                String msg = "上传照片失败: 无法从下载地址解析 photo_id: " + qrContent;
                log(msg);
                return PhotoUploadResult.fail(msg, 0, photoId, uploadUrl);
            }

            // 优先使用服务端下发的 uploadUrl（LAN IP 地址，绕过公网白名单）
            if (serverUploadUrl != null && !serverUploadUrl.trim().isEmpty()) {
                uploadUrl = serverUploadUrl.trim();
                log("使用服务端下发的 uploadUrl: " + uploadUrl);
            } else {
                // 兼容旧协议: 从 qrContent 构造上传地址
                if (!qrContent.contains("/api/")) {
                    String msg = "上传照片失败: 下载地址缺少 /api/，无法构造上传地址: " + qrContent;
                    log(msg);
                    return PhotoUploadResult.fail(msg, 0, photoId, uploadUrl);
                }
                String baseUrl = qrContent.substring(0, qrContent.indexOf("/api/"));
                uploadUrl = baseUrl + "/api/photo/upload/" + photoId;
                log("从下载地址构造上传地址: " + uploadUrl);
            }

            java.io.File file = new java.io.File(photoPath);
            if (!file.exists() || !file.isFile()) {
                String msg = "上传照片失败: 文件不存在 " + photoPath;
                log(msg);
                return PhotoUploadResult.fail(msg, 0, photoId, uploadUrl);
            }

            Exception lastError = null;
            int lastCode = 0;
            for (int attempt = 1; attempt <= 3; attempt++) {
                java.net.HttpURLConnection conn = null;
                try {
                    java.net.URL url = new java.net.URL(uploadUrl);
                    conn = (java.net.HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("POST");
                    conn.setDoOutput(true);
                    conn.setConnectTimeout(8000);
                    conn.setReadTimeout(30000);
                    conn.setRequestProperty("Accept", "application/json");
                    String boundary = "Boundary-" + System.currentTimeMillis();
                    conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);

                    java.io.OutputStream os = conn.getOutputStream();
                    String header = "--" + boundary + "\r\n"
                            + "Content-Disposition: form-data; name=\"file\"; filename=\"" + file.getName() + "\"\r\n"
                            + "Content-Type: image/jpeg\r\n\r\n";
                    os.write(header.getBytes("UTF-8"));
                    java.io.FileInputStream fis = new java.io.FileInputStream(file);
                    byte[] buf = new byte[8192];
                    int n;
                    while ((n = fis.read(buf)) != -1) os.write(buf, 0, n);
                    fis.close();
                    os.write(("\r\n--" + boundary + "--\r\n").getBytes("UTF-8"));
                    os.flush();
                    os.close();

                    lastCode = conn.getResponseCode();
                    log("照片上传第" + attempt + "次结果: HTTP " + lastCode
                            + " url=" + uploadUrl + " size=" + file.length());
                    if (lastCode >= 200 && lastCode < 300) {
                        return PhotoUploadResult.ok(lastCode, photoId, uploadUrl);
                    }
                    lastError = new IOException("HTTP " + lastCode);
                } catch (Exception e) {
                    lastError = e;
                    log("照片上传第" + attempt + "次异常: " + e.getMessage());
                } finally {
                    if (conn != null) conn.disconnect();
                }

                if (attempt < 3) {
                    try {
                        Thread.sleep(1000L * attempt);
                    } catch (InterruptedException ie) {
                        Thread.currentThread().interrupt();
                        break;
                    }
                }
            }

            String msg = "照片上传失败: " + (lastError != null ? lastError.getMessage() : "HTTP " + lastCode)
                    + " url=" + uploadUrl;
            log(msg);
            return PhotoUploadResult.fail(msg, lastCode, photoId, uploadUrl);
        } catch (Exception e) {
            String msg = "上传照片异常: " + e.getMessage();
            log(msg);
            return PhotoUploadResult.fail(msg, 0, photoId, uploadUrl);
        }
    }

    private String extractPhotoId(String qrContent) {
        try {
            int marker = qrContent.indexOf("/api/photo/download/");
            if (marker >= 0) {
                int idx = marker + "/api/photo/download/".length();
                int endIdx = qrContent.indexOf("?", idx);
                if (endIdx < 0) endIdx = qrContent.indexOf("#", idx);
                if (endIdx < 0) endIdx = qrContent.length();
                String part = qrContent.substring(idx, endIdx);
                int slash = part.indexOf('/');
                if (slash >= 0) part = part.substring(0, slash);
                if (part.endsWith(".jpg")) part = part.substring(0, part.length() - 4);
                if (part.endsWith(".jpeg")) part = part.substring(0, part.length() - 5);
                return part.trim();
            }
            // 兼容旧逻辑: 只要包含 download/ 就尝试解析。
            int idx = qrContent.indexOf("download/");
            if (idx >= 0) {
                idx += "download/".length();
                int endIdx = qrContent.indexOf("?", idx);
                if (endIdx < 0) endIdx = qrContent.indexOf("#", idx);
                if (endIdx < 0) endIdx = qrContent.length();
                String part = qrContent.substring(idx, endIdx);
                int slash = part.indexOf('/');
                if (slash >= 0) part = part.substring(0, slash);
                if (part.endsWith(".jpg")) part = part.substring(0, part.length() - 4);
                if (part.endsWith(".jpeg")) part = part.substring(0, part.length() - 5);
                return part.trim();
            }
        } catch (Exception e) {
            log("解析 photo_id 异常: " + e.getMessage());
        }
        return "";
    }

    private void handleCmdTts(JSONObject data, int seq) {
        String text = data.optString("text").trim();
        String priority = data.optString("priority", "normal");
        JSONObject teaching = data.optJSONObject("teaching");
        String teachingAction = teaching == null
                ? "" : teaching.optString("action", "").trim().toUpperCase();
        String teachingTurnId = teaching == null
                ? "" : teaching.optString("turnId", "").trim();
        if ("START".equals(teachingAction)) {
            if (teaching.optInt("protocolVersion", 0) != 2) return;
            if (robotCharging || (batteryMonitor != null && batteryMonitor.isChargingNow())) {
                JSONObject failed = createData("success", false);
                addTeachingAckFields(failed, teachingTurnId, "START");
                sendAck(seq, "evt_tts_done", failed);
                return;
            }
            if (teachingController.isMatchingTurn(teachingTurnId)) return;
            String question = teaching.optString("question", "").trim();
            int timeoutSeconds = teaching.optInt("timeoutSeconds", 120);
            cancelTeachingCapture("new_teaching_turn", false, null);
            teachingController.prepareStart(teachingTurnId, question, timeoutSeconds);
            teachingController.setSilenceVerified(getSharedPreferences("robot_settings", MODE_PRIVATE)
                    .getBoolean("teaching_speech_activity_verified", false));
            teachingDisplayTurn = teachingTurnId;
            teachingQuestion = question;
            teachingStateRevision = -1;
            teachingAcceptServerState = true;
            teachingStatus = "等待提示播报完成";
            notifyTeachingUi();
            recordConversation(ConversationEntry.ROLE_STATUS, "教学问题：" + question);
            recordConversation(ConversationEntry.ROLE_STATUS, "状态：等待提示播报完成");
        } else if ("STOP".equals(teachingAction)) {
            cancelTeachingCapture("server_stop", false, null);
            recordConversation(
                    ConversationEntry.ROLE_STATUS,
                    teachingOutcomeText(teaching.optString("outcome", ""))
            );
        } else if (teachingController.isActive()) {
            cancelTeachingCapture("tts_takeover", true, "状态：教学已取消");
        }
        if (!text.isEmpty()) {
            recordConversation(ConversationEntry.ROLE_ASSISTANT, text);
        }
        playTts(text, seq, priority, teachingTurnId, teachingAction);
    }

    private void handleCmdFaceRegister(JSONObject data) {
        String robotId = data.optString("robotId");
        robotCapabilities.setRobotId(robotId);
        String requestId = data.optString("requestId");
        String name = data.optString("name");

        Person person = new Person();
        person.setUserId(String.valueOf(ReqIdUtils.next()));
        person.setNewUser(true);
        person.setRemoteReqId(requestId);
        person.setName(name);
        robotCapabilities.handleFaceRegister(person, this);
    }

    private void handleCmdListen(JSONObject data, int seq) {
        int requestedTimeout = data.optInt("timeout", DEFAULT_CMD_LISTEN_TIMEOUT_SEC);
        int timeoutSec = normalizeCmdListenTimeout(requestedTimeout);
        if (timeoutSec != requestedTimeout) {
            log("收音超时已限制: requested=" + requestedTimeout + "s actual=" + timeoutSec + "s");
        }
        startCmdListen(seq, timeoutSec);
    }

    static int normalizeCmdListenTimeout(int requestedTimeout) {
        return Math.max(
                MIN_CMD_LISTEN_TIMEOUT_SEC,
                Math.min(MAX_CMD_LISTEN_TIMEOUT_SEC, requestedTimeout)
        );
    }

    public interface OnSpeechPreviewListener { void onChanged(String text, boolean staff); }
    private volatile OnSpeechPreviewListener speechPreviewListener;
    private volatile String speechPreview = "";
    public void setSpeechPreviewListener(OnSpeechPreviewListener listener) {
        speechPreviewListener = listener;
        if (listener != null) listener.onChanged(speechPreview, teachingController.isCollecting());
    }
    public void updateSpeechPreview(String rawText) {
        String text = rawText == null ? "" : rawText.trim();
        if (!text.isEmpty() && robotCapabilities != null && robotCapabilities.isSpeaking()) return;
        if (!text.isEmpty() && teachingController.isActive() && !teachingController.isCollecting()) return;
        speechPreview = text;
        OnSpeechPreviewListener listener = speechPreviewListener;
        if (listener != null) listener.onChanged(text, teachingController.isCollecting());
    }

    public void setConversationListener(OnConversationListener listener) {
        conversationListener = listener;
        if (listener != null) {
            listener.onConversationChanged(getConversationHistory());
        }
    }

    public List<ConversationEntry> getConversationHistory() {
        synchronized (conversationLock) {
            return new ArrayList<>(conversationHistory);
        }
    }

    private void recordConversation(String role, String rawText) {
        String text = rawText == null ? "" : rawText.trim();
        if (text.isEmpty()) return;
        if (ConversationEntry.ROLE_USER.equals(role) || ConversationEntry.ROLE_STAFF.equals(role)) updateSpeechPreview("");

        List<ConversationEntry> snapshot;
        synchronized (conversationLock) {
            conversationHistory.add(new ConversationEntry(role, text));
            while (conversationHistory.size() > MAX_CONVERSATION_MESSAGES) {
                conversationHistory.remove(0);
            }
            snapshot = new ArrayList<>(conversationHistory);
        }

        OnConversationListener listener = conversationListener;
        if (listener != null) {
            listener.onConversationChanged(snapshot);
        }
    }

    private volatile int currentListenSeq = -1;
    private volatile int remainingListenTime = 0;
    private Runnable listenTimeoutRunnable;

    private synchronized void startCmdListen(int seq, int timeoutSec) {
        log("中枢收⾳请求" + timeoutSec + "秒");

        stopCurrentTts();
        stopCurrentListening();
        cancelListenTimeout();

        int newTotalTime;
        if (listenState == STATE_CMD_LISTEN && remainingListenTime > 0) {
            newTotalTime = Math.min(
                    MAX_CMD_LISTEN_TIMEOUT_SEC,
                    remainingListenTime + timeoutSec
            );
            log("连续收音窗口保持在" + newTotalTime + "秒以内");
        } else {
            newTotalTime = timeoutSec;
        }

        listenState = STATE_CMD_LISTEN;
        currentListenSeq = seq;
        remainingListenTime = newTotalTime;

        if (robotCapabilities != null) {
            // 【关键修改】将 timeoutSec 传递给 startListening
            robotCapabilities.startListening(newTotalTime, "zh",
                    new RobotCapabilities.ListenCallback() {
                        @Override public void onSpeechStarted() {
                            // The follow-up window limits waiting for speech, not a sentence already underway.
                            if (listenHandler != null && listenTimeoutRunnable != null)
                                listenHandler.removeCallbacks(listenTimeoutRunnable);
                        }
                        @Override public void onTranscribing() {
                            // The 5-second window bounds capture, not the HTTP recognition request.
                            if (listenHandler != null && listenTimeoutRunnable != null)
                                listenHandler.removeCallbacks(listenTimeoutRunnable);
                        }
                        @Override
                        public void onResult(String text, long duration) {
                            if (listenState != STATE_CMD_LISTEN) return;
                            recordConversation(ConversationEntry.ROLE_USER, text);
                            JSONObject result = new JSONObject();
                            try {
                                result.put("text", text);
                                result.put("confidence", 0.95f);
                                result.put("duration", duration);
                            } catch (JSONException e) {
                                LogUtils.e(TAG, "构建语音结果失败", e);
                            }
                            sendAck(currentListenSeq, "evt_speech_result", result);
                            stopCmdListen("result");
                        }

                        @Override
                        public void onTimeout(String reason) {
                            if (listenState != STATE_CMD_LISTEN) return;
                            sendAck(currentListenSeq, "evt_listen_timeout",
                                    createData("reason", "timeout"));
                            stopCmdListen("timeout");
                        }

                        @Override
                        public void onError(String error) {
                            if (listenState != STATE_CMD_LISTEN) return;
                            sendAck(currentListenSeq, "evt_listen_timeout",
                                    createData("reason", error));
                            stopCmdListen("error");
                        }
                    });
        }

        listenTimeoutRunnable = () -> {
            if (listenState == STATE_CMD_LISTEN) {
                log("收音总超时: " + newTotalTime + "秒");
                stopCurrentListening();
                sendAck(currentListenSeq, "evt_listen_timeout", createData("reason", "total timeout"));
                stopCmdListen("total_timeout");
            }
        };

        if (listenHandler != null) {
            listenHandler.postDelayed(listenTimeoutRunnable, newTotalTime * 1000L);
        }
    }

    // 公开方法：用户关闭视频后调用，启动自动收音（超时0秒，即无总超时限制）
    public void startAutoListening() {
        if (robotCapabilities != null) {
            robotCapabilities.startAutoListening();
        } else {
            log("RobotCapabilities未初始化，无法启动收音");
        }
    }

    private void cancelListenTimeout() {
        if (listenHandler != null && listenTimeoutRunnable != null) {
            listenHandler.removeCallbacks(listenTimeoutRunnable);
            listenTimeoutRunnable = null;
        }
    }

    private synchronized void stopCmdListen(String reason) {
        if (listenState != STATE_CMD_LISTEN) return;
        log("停止收音: " + reason);
        listenState = STATE_IDLE;
        currentListenSeq = -1;
        remainingListenTime = 0;
        cancelListenTimeout();
        stopCurrentListening();
    }

    public interface OnTeachingListener { void onChanged(); }
    private volatile OnTeachingListener teachingListener;
    private String teachingDisplayTurn = "", teachingQuestion = "", teachingStatus = "";
    private long teachingStateRevision = -1;
    private volatile boolean teachingAcceptServerState = false;
    private long teachingSuppressUntil = 0;
    private String lastTeachingText = "";
    private Runnable teachingTick;
    private int teachingListenGeneration = 0;

    public void setTeachingListener(OnTeachingListener listener) {
        teachingListener = listener;
        notifyTeachingUi();
    }
    public boolean isTeachingCollecting() { return teachingController.isCollecting(); }
    public boolean isTeachingActive() { return teachingController.isActive(); }
    public boolean isRobotSpeaking() { return robotCapabilities != null && robotCapabilities.isSpeaking(); }
    public boolean isVoiceAudioPlaying() { return robotCapabilities != null && robotCapabilities.isVoiceAudioPlaying(); }

    /** UI binding must not replace an active socket or resume an explicit offline state. */
    public void ensureConnectedForPresentation() {
        if (!isManualStop && !isConnected.get()) connect();
    }
    public String getTeachingDisplay() {
        if (teachingDisplayTurn.isEmpty()) return "";
        int remaining = teachingController.remainingSeconds(android.os.SystemClock.elapsedRealtime());
        return "原问题：" + teachingQuestion + "\n" + teachingStatus
                + (teachingController.isCollecting() ? "（剩余" + remaining + "秒）" : "");
    }
    private void notifyTeachingUi() {
        OnTeachingListener listener = teachingListener;
        if (listener != null) listener.onChanged();
    }
    private void startTeachingClock(String turnId) {
        if (listenHandler == null) return;
        if (teachingTick != null) listenHandler.removeCallbacks(teachingTick);
        final long startupAt = android.os.SystemClock.elapsedRealtime();
        teachingTick = new Runnable() {
            @Override public void run() {
                if (!teachingController.isMatchingTurn(turnId)) return;
                long now = android.os.SystemClock.elapsedRealtime();
                String reason = teachingController.timedFinishReason(now);
                if (!reason.isEmpty()) { finishTeachingCapture(turnId, reason); return; }
                if (teachingController.getPhase() == TeachingSessionController.Phase.STARTING && now - startupAt > 8000) {
                    cancelTeachingCapture("start_timeout", true, "监听启动失败");
                    return;
                }
                notifyTeachingUi();
                listenHandler.postDelayed(this, 200);
            }
        };
        listenHandler.post(teachingTick);
    }

    private void startTeachingListen() {
        if (!teachingController.isActive() || robotCapabilities == null) return;
        final String turnId = teachingController.getTurnId();
        int remainingSeconds = teachingController.remainingSeconds(android.os.SystemClock.elapsedRealtime());
        if (remainingSeconds <= 0) { finishTeachingCapture(turnId, "deadline_120s"); return; }
        stopCmdListen("teaching_start");
        stopCurrentListening();
        final int generation = ++teachingListenGeneration;
        robotCapabilities.startTeachingListening(remainingSeconds, new RobotCapabilities.ListenCallback() {
            private boolean current() {
                return generation == teachingListenGeneration && teachingController.isMatchingTurn(turnId);
            }
            @Override public void onReady() {
                if (!current()) return;
                if (teachingController.onListenReady(turnId, android.os.SystemClock.elapsedRealtime())) {
                    teachingStatus = "正在记录现场补充";
                    JSONObject ready = createData("teachingTurnId", turnId);
                    try { ready.put("success", true); } catch (JSONException ignored) { }
                    sendEvent("evt_teaching_started", ready);
                    notifyTeachingUi();
                }
            }
            @Override public void onSpeechActivity(String partial) {
                if (current() && partial != null && !partial.trim().isEmpty()) {
                    teachingController.speechActivity(android.os.SystemClock.elapsedRealtime());
                    log("teaching partial activity turn=" + turnId + " chars=" + partial.length());
                }
            }
            @Override public void onResult(String text, long duration) {
                if (current()) handleTeachingSpeechResult(text, duration);
            }
            @Override public void onReplayRequested() {
                if(current()) finishTeachingCapture(turnId,"replay_query");
            }
            @Override public void onTimeout(String reason) {
                if (!current()) return;
                if (teachingController.isExpired(android.os.SystemClock.elapsedRealtime()))
                    finishTeachingCapture(turnId, "deadline_120s");
                else cancelTeachingCapture("listen_timeout", true, "监听异常，本次已取消");
            }
            @Override public void onError(String error) {
                if (current()) cancelTeachingCapture("listen_error", true, "监听失败，本次已取消");
            }
        });
    }

    private synchronized void handleTeachingSpeechResult(String text, long duration) {
        final String turnId = teachingController.getTurnId();
        TeachingSessionController.SpeechDecision decision = teachingController.acceptSpeech(text, android.os.SystemClock.elapsedRealtime());
        if (decision.action == TeachingSessionController.SpeechAction.IGNORE) return;
        if (!isConnected.get()) { cancelTeachingCapture("disconnect", false, "连接断开，已取消"); return; }
        if (decision.shouldSend()) {
            lastTeachingText = text == null ? "" : text.trim();
            JSONObject data = createData("text", decision.rawText);
            try {
                data.put("duration", duration);
                data.put("teachingTurnId", turnId);
                data.put("speechMode", "TEACHING");
                data.put("fragmentSeq", decision.fragmentSeq);
            } catch (JSONException e) {
                cancelTeachingCapture("event_build_error", true, "监听失败"); return;
            }
            recordConversation(ConversationEntry.ROLE_STAFF, decision.displayFragment);
            sendEvent("evt_speech_result", data);
        }
        if (decision.action == TeachingSessionController.SpeechAction.FINISH) {
            lastTeachingText = text == null ? "" : text.trim();
            finishTeachingCapture(turnId, "replay_query");
        }
    }

    public void finishTeachingCapture() {
        finishTeachingCapture(teachingController.getTurnId(), "button");
    }

    private synchronized void finishTeachingCapture(String turnId, String reason) {
        if (com.zongheng.exhibitionhallrobot.speech.IflytekAsrController.isEnabledMode()) {
            if (!teachingController.beginTranscribing(turnId)) return;
            if (listenHandler != null && teachingTick != null) listenHandler.removeCallbacks(teachingTick);
            final int generation = teachingListenGeneration;
            teachingStatus = "收音已结束，正在等待讯飞完整识别结果";
            notifyTeachingUi();
            JSONObject transcribing = createData("teachingTurnId", turnId);
            try { transcribing.put("completionReason", reason); } catch (JSONException ignored) { }
            sendEvent("evt_teaching_transcribing", transcribing);
            robotCapabilities.finishTeachingTranscription(new com.zongheng.exhibitionhallrobot.speech.IflytekAsrController.Listener() {
                private boolean current() {
                    return generation==teachingListenGeneration && teachingController.isMatchingTurn(turnId)
                            && teachingController.isTranscribing();
                }
                @Override public void onResult(String text,long duration) {
                    synchronized (TcpService.this) {
                        if(!current()) return;
                        if(text.length()>4000) { cancelTeachingCapture("answer_limit",true,"讲解文字超过4000字，本次未提交"); return; }
                        TeachingSessionController.SpeechDecision decision=teachingController.acceptTranscription(text);
                        if(decision.shouldSend()) {
                            lastTeachingText=decision.displayFragment;
                            JSONObject fragment=createData("text",decision.rawText);
                            try {
                                fragment.put("duration",duration); fragment.put("teachingTurnId",turnId);
                                fragment.put("speechMode","TEACHING"); fragment.put("fragmentSeq",decision.fragmentSeq);
                            } catch(JSONException error) { cancelTeachingCapture("event_build_error",true,"提交失败"); return; }
                            recordConversation(ConversationEntry.ROLE_STAFF,decision.displayFragment);
                            sendEvent("evt_speech_result",fragment);
                        }
                        completeTeachingCapture(turnId,decision.action==TeachingSessionController.SpeechAction.FINISH?"replay_query":reason);
                    }
                }
                @Override public void onError(String error) {
                    synchronized (TcpService.this) {
                        if(current()) cancelTeachingCapture("transcription_failed",true,"讯飞转写失败，本次未提交，请重新教学");
                    }
                }
            });
            return;
        }
        completeTeachingCapture(turnId, reason);
    }

    private synchronized void completeTeachingCapture(String turnId, String reason) {
        if (!teachingController.finish(turnId)) return;
        if (listenHandler != null && teachingTick != null) listenHandler.removeCallbacks(teachingTick);
        teachingListenGeneration++;
        teachingSuppressUntil = android.os.SystemClock.elapsedRealtime() + 1000;
        stopCurrentListening();
        if (robotCapabilities != null) robotCapabilities.switchToWakeupMode();
        teachingStatus = teachingController.getFragmentSeq() == 0 ? "尚未收到有效补充" : "正在整理 / 正在提交";
        notifyTeachingUi();
        JSONObject data = createData("teachingTurnId", turnId);
        try {
            data.put("completionReason", reason);
            data.put("lastFragmentSeq", teachingController.getFragmentSeq());
            data.put("replayRequested", "replay_query".equals(reason));
        } catch (JSONException ignored) { }
        if (isConnected.get()) sendEvent("evt_teaching_finished", data);
    }

    public void cancelTeachingCapture(String reason) {
        cancelTeachingCapture(reason, true, "已取消");
    }
    private synchronized void cancelTeachingCapture(String reason, boolean report, String displayText) {
        String turnId = teachingController.getTurnId();
        boolean active = teachingController.isActive();
        teachingController.stop();
        teachingListenGeneration++;
        if (listenHandler != null && teachingTick != null) listenHandler.removeCallbacks(teachingTick);
        if (active) {
            teachingSuppressUntil = android.os.SystemClock.elapsedRealtime() + 1000;
            stopCurrentListening();
        }
        if (displayText != null) teachingStatus = displayText;
        if (report && isConnected.get() && !teachingDisplayTurn.isEmpty()) {
            JSONObject data = createData("reason", reason);
            try { data.put("teachingTurnId", turnId.isEmpty() ? teachingDisplayTurn : turnId); } catch (JSONException ignored) { }
            sendEvent(active ? "evt_listen_timeout" : "evt_teaching_reset", data);
        }
        teachingAcceptServerState = false;
        if (!"cancel_button".equals(reason) && !"new_teaching_turn".equals(reason)) teachingDisplayTurn = "";
        notifyTeachingUi();
    }

    private synchronized void handleTeachingState(JSONObject data) {
        String id = data.optString("turnId", "");
        long revision = data.optLong("stateRevision", -1);
        if (!teachingAcceptServerState || !id.equals(teachingDisplayTurn) || revision <= teachingStateRevision) return;
        teachingStateRevision = revision;
        String capture = data.optString("captureState");
        if (!"PROMPTING".equals(capture) && !"COLLECTING".equals(capture) && !"TRANSCRIBING".equals(capture)) {
            boolean active = teachingController.isActive();
            teachingController.stop(); teachingListenGeneration++;
            if (listenHandler != null && teachingTick != null) listenHandler.removeCallbacks(teachingTick);
            if (active) { stopCurrentListening(); teachingSuppressUntil = android.os.SystemClock.elapsedRealtime() + 1000; }
        }
        String reason = data.optString("reason");
        if ("CANCELLED".equals(capture)) {
            teachingStatus = "answer_limit".equals(reason) ? "内容超出限制，本次未提交" : "已取消";
        } else if ("empty_answer".equals(reason)) {
            teachingStatus = "尚未收到有效补充，本次未提交";
        } else if ("TRANSCRIBING".equals(capture)) {
            teachingStatus = "收音已结束，正在等待讯飞完整识别结果";
        } else if ("COLLECTING".equals(capture)) {
            teachingStatus = "正在记录现场补充";
        } else {
            String draft = data.optString("draftState");
            String analysis = "READY".equals(data.optString("analysisState")) ? "整理完成" :
                    "FAILED".equals(data.optString("analysisState")) ? "整理暂不可用" : "正在整理";
            String submitted = "SUBMITTED".equals(draft) ? "已保存，待审核" :
                    "FAILED".equals(draft) ? ("submission_uncertain".equals(reason) ? "提交结果待确认" : "提交失败") : "正在提交";
            teachingStatus = analysis + " / " + submitted;
        }
        notifyTeachingUi();
    }

    private static String teachingOutcomeText(String outcome) { return "已取消"; }

    private final KeywordWakeupGate keywordWakeupGate = new KeywordWakeupGate();
    private volatile Runnable activeTtsWakeupCancel;

    public synchronized void onKeywordWakeup() {
        if (!keywordWakeupGate.accept(android.os.SystemClock.elapsedRealtime())) return;
        if (teachingController.isActive()) cancelTeachingCapture("keyword_wakeup",true,"已取消教学，开始新一轮提问");
        Runnable cancel = activeTtsWakeupCancel;
        if (cancel != null) cancel.run();
        if (robotCapabilities != null) robotCapabilities.onKeywordWakeup();
    }

    private synchronized void playTts(
            String text,
            int seq,
            String priority,
            String teachingTurnId,
            String teachingAction
    ) {
        int newTaskId = ttsTaskId.incrementAndGet();
        log("开始TTS taskId=" + newTaskId + " priority=" + priority + " text=" + text + " length=" + text.length());

        boolean isHigh = "high".equalsIgnoreCase(priority) || "interrupt".equalsIgnoreCase(priority);

        if (isHigh) {
            log("高优先级TTS，打断当前播报 taskId=" + newTaskId);
            interruptCurrentTts();
            currentTtsId = newTaskId;
            startSpeakWithDelay(text, priority, seq, newTaskId, HIGH_PRIORITY_TTS_DELAY_MS,
                    teachingTurnId, teachingAction);
        } else {
            if (currentTtsId == -1) {
                currentTtsId = newTaskId;
                cancelTtsProtection();
                startSpeakWithDelay(text, priority, seq, newTaskId, 0,
                        teachingTurnId, teachingAction);
            } else {
                log("普通TTS覆盖旧任务（不强制打断）oldTaskId=" + currentTtsId + " newTaskId=" + newTaskId);
                // 取消旧任务的保护，让其自然完成或被新任务替换
                cancelTtsProtection();
                currentTtsId = newTaskId;
                startSpeakWithDelay(text, priority, seq, newTaskId, NORMAL_PRIORITY_TTS_DELAY_MS,
                        teachingTurnId, teachingAction);
            }
        }
    }

    private void startSpeakWithDelay(
            String text,
            String priority,
            int seq,
            int newTaskId,
            int delayMs,
            String teachingTurnId,
            String teachingAction
    ) {
        final AtomicBoolean terminal = new AtomicBoolean(false);
        activeTtsWakeupCancel = () -> {
            if (newTaskId != currentTtsId || !terminal.compareAndSet(false, true)) return;
            currentTtsId = -1;
            activeTtsWakeupCancel = null;
            cancelTtsProtection();
            JSONObject result = createData("success", false);
            try { result.put("reason", "keyword_wakeup"); } catch (JSONException ignored) { }
            addTeachingAckFields(result, teachingTurnId, teachingAction);
            sendAck(seq, "evt_tts_done", result);
            if ("START".equals(teachingAction)) teachingController.onPromptTtsFailed(teachingTurnId);
            log("KWS中止TTS并回告 seq=" + seq + " taskId=" + newTaskId);
        };
        mWorkHandler.postDelayed(() -> {
            synchronized (TcpService.this) {
            if (newTaskId != currentTtsId) {
                log("TTS已被新任务替换，放弃 taskId=" + newTaskId + " currentTtsId=" + currentTtsId);
                return;
            }
            if (robotCapabilities == null) {
                log("robotCapabilities为null，无法播放TTS");
                return;
            }

            log("开始播放TTS taskId=" + newTaskId + " text长度=" + text.length());

            robotCapabilities.speak(seq, text, priority, new RobotCapabilities.SpeakCallback() {
                @Override
                public void onSuccess() {
                    if (newTaskId != currentTtsId) {
                        log("TTS成功但taskId已变化，忽略 taskId=" + newTaskId + " currentTtsId=" + currentTtsId);
                        return;
                    }
                    if (!terminal.compareAndSet(false, true)) return;
                    activeTtsWakeupCancel = null;
                    log("TTS完成 taskId=" + newTaskId);
                    currentTtsId = -1;
                    cancelTtsProtection();
                    JSONObject result = createData("success", true);
                    addTeachingAckFields(result, teachingTurnId, teachingAction);
                    sendAck(seq, "evt_tts_done", result);
                    if ("START".equals(teachingAction)
                            && teachingController.onPromptTtsSucceeded(
                            teachingTurnId,
                            android.os.SystemClock.elapsedRealtime())) {
                        recordConversation(ConversationEntry.ROLE_STATUS, "状态：等待工作人员讲解");
                        startTeachingClock(teachingTurnId);
                        startTeachingListen();
                    }
                }

                @Override
                public void onFailed(String error) {
                    if (newTaskId != currentTtsId) {
                        log("TTS失败但taskId已变化，忽略 taskId=" + newTaskId + " currentTtsId=" + currentTtsId);
                        return;
                    }
                    if (!terminal.compareAndSet(false, true)) return;
                    activeTtsWakeupCancel = null;
                    log("TTS失败 taskId=" + newTaskId + " err=" + error);
                    currentTtsId = -1;
                    cancelTtsProtection();
                    JSONObject result = new JSONObject();
                    try {
                        result.put("success", false);
                        result.put("reason", error);
                        addTeachingAckFields(result, teachingTurnId, teachingAction);
                    } catch (JSONException e) {
                        LogUtils.e(TAG, "构建TTS失败结果失败", e);
                    }
                    sendAck(seq, "evt_tts_done", result);
                    if ("START".equals(teachingAction)) {
                        teachingController.onPromptTtsFailed(teachingTurnId);
                        recordConversation(ConversationEntry.ROLE_STATUS, "状态：教学提示播放失败");
                    }
                }
            });

            ttsProtectionRunnable = () -> {
                if (newTaskId == currentTtsId) {
                    if (!terminal.compareAndSet(false, true)) return;
                    activeTtsWakeupCancel = null;
                    log("TTS超时强制释放 taskId=" + newTaskId + " text长度=" + text.length());
                    currentTtsId = -1;
                    JSONObject result = createData("success", false);
                    try { result.put("reason", "tts_timeout"); } catch (JSONException ignored) { }
                    addTeachingAckFields(result, teachingTurnId, teachingAction);
                    sendAck(seq, "evt_tts_done", result);
                    // 通知RobotCapabilities停止TTS
                    if (robotCapabilities != null) {
                        robotCapabilities.stopSpeak();
                    }
                    if ("START".equals(teachingAction)) {
                        teachingController.onPromptTtsFailed(teachingTurnId);
                        recordConversation(ConversationEntry.ROLE_STATUS, "状态：教学提示播放超时");
                    }
                } else {
                    log("TTS保护超时但taskId已变化，无需处理 taskId=" + newTaskId + " currentTtsId=" + currentTtsId);
                }
            };
            mWorkHandler.postDelayed(ttsProtectionRunnable, TTS_PROTECTION_TIMEOUT_MS);
            }

        }, delayMs);
    }

    private static void addTeachingAckFields(
            JSONObject result,
            String teachingTurnId,
            String teachingAction
    ) {
        if (teachingTurnId == null || teachingTurnId.trim().isEmpty()) return;
        try {
            result.put("teachingTurnId", teachingTurnId);
            result.put("teachingAction", teachingAction == null ? "" : teachingAction);
        } catch (JSONException ignored) {
            // JSONObject with string values should not fail.
        }
    }

    private void cancelTtsProtection() {
        if (ttsProtectionRunnable != null) {
            mWorkHandler.removeCallbacks(ttsProtectionRunnable);
            ttsProtectionRunnable = null;
        }
    }

    private void interruptCurrentTts() {
        int oldTaskId = currentTtsId;
        currentTtsId = ttsTaskId.incrementAndGet();
        log("中断当前TTS oldTaskId=" + oldTaskId + " newTaskId=" + currentTtsId);
        stopCurrentTts();
        cancelTtsProtection();
    }

    private void stopCurrentTts() {
        if (robotCapabilities != null) {
            robotCapabilities.stopSpeak();
        }
    }

    private void stopCurrentListening() {
        if (robotCapabilities != null) {
            robotCapabilities.stopListening();
        }
    }

    public void sendFaceRegisterSuccess(String robotId, int requestId, String name, String userId) {
        try {
            JSONObject data = new JSONObject();
            data.put("robotId", robotId);
            data.put("requestId", requestId);
            data.put("userId", userId);
            data.put("name", name);
            data.put("ts", System.currentTimeMillis());
            sendEvent("evt_face_register_success", data);
            log("上报人脸注册成功: " + name);
        } catch (Exception e) {
            log("上报失败: " + e.getMessage());
        }
    }

    public void sendFaceRegisterFailed(String robotId, int requestId, String name, String reason) {
        try {
            JSONObject data = new JSONObject();
            data.put("robotId", robotId);
            data.put("requestId", requestId);
            data.put("name", name);
            data.put("errorCode", "timeout");
            data.put("errorMessage", reason);
            data.put("ts", System.currentTimeMillis());
            sendEvent("evt_face_register_failed", data);
            log("上报人脸注册失败: " + reason);
        } catch (Exception e) {
            log("上报失败: " + e.getMessage());
        }
    }

    public void sendFaceRecognized(String robotId, String userId, String name, double confidence) {
        try {
            JSONObject data = new JSONObject();
            data.put("robotId", robotId);
            data.put("userId", userId);
            data.put("name", name != null ? name : "");
            data.put("confidence", confidence);
            data.put("location", robotCapabilities != null ? robotCapabilities.getCurrentLocation() : "");
            data.put("status", robotCapabilities != null ? robotCapabilities.getCurrentStatus() : "");
            data.put("currentTask", robotCapabilities != null && robotCapabilities.getCurrentTask() != null ?
                    robotCapabilities.getCurrentTask() : JSONObject.NULL);
            data.put("ts", System.currentTimeMillis());
            sendEvent("evt_face_recognized", data);
        } catch (Exception e) {
            log("上报已识别注册用户失败: " + e.getMessage());
        }
    }

    public void sendFacePhoto(String robotId, String userId, String name, String photoBase64) {
        try {
            JSONObject data = new JSONObject();
            data.put("robotId", robotId);
            data.put("userId", userId);
            data.put("name", name != null ? name : "");
            data.put("photoBase64", photoBase64);
            data.put("contentType", "image/jpeg");
            data.put("photoType", "register");
            data.put("ts", System.currentTimeMillis());
            sendEvent("evt_face_photo", data);
            // 优化：移除频繁的日志打印
        } catch (Exception e) {
            log("上报人脸图片失败: " + e.getMessage());
        }
    }

    public void sendPersonCount(String robotId, int personCount) {
        try {
            JSONObject data = new JSONObject();
            data.put("robotId", robotId);
            data.put("personCount", personCount);
            data.put("location", robotCapabilities != null ? robotCapabilities.getCurrentLocation() : "");
            data.put("status", robotCapabilities != null ? robotCapabilities.getCurrentStatus() : "");
            data.put("currentTask", robotCapabilities != null && robotCapabilities.getCurrentTask() != null ?
                    robotCapabilities.getCurrentTask() : JSONObject.NULL);
            data.put("ts", System.currentTimeMillis());
            sendEvent("evt_person_count", data);
            // 优化：移除频繁的日志打印
        } catch (Exception e) {
            log("上报当前视野内人员数量失败: " + e.getMessage());
        }
    }

    private void handleQueryStatus(int seq) {
        JSONObject result = new JSONObject();
        try {
            result.put("battery", robotCapabilities.getCurrentBattery());
            result.put("location", robotCapabilities.getCurrentLocation());
            result.put("pointId", getPointIdByName(robotCapabilities.getCurrentLocation()));
            result.put("status", robotCapabilities.getCurrentStatus());
            result.put("currentTask", robotCapabilities.getCurrentTask() != null ?
                    robotCapabilities.getCurrentTask() : JSONObject.NULL);
        } catch (JSONException e) {
            log("状态响应失败: " + e.getMessage());
        }
        sendAck(seq, "evt_status", result);
    }

    private int getPointIdByName(String pointName) {
        if (robotCapabilities != null) {
            RobotCapabilities.PointInfo point = robotCapabilities.getPointInfo(pointName);
            if (point != null && point.id != null) {
                try {
                    return Integer.parseInt(point.id);
                } catch (NumberFormatException e) {
                    return pointName.hashCode();
                }
            }
        }
        return -1;
    }

    private void handleCancel(int seq, String reason) {
        String canceledTask = null;

        interruptCurrentTts();

        if (robotCapabilities != null) {
            canceledTask = robotCapabilities.cancelCurrentTask();
            if (canceledTask != null) {
                log("已取消任务: " + canceledTask + ", 原因: " + reason);
            }
        }

        stopCurrentListening();

        JSONObject result = new JSONObject();
        try {
            result.put("canceledTask", canceledTask != null ? canceledTask : "none");
            result.put("success", true);
        } catch (JSONException e) {
            LogUtils.e(TAG, "构建取消结果失败", e);
        }
        sendAck(seq, "evt_cancelled", result);
    }

    private void handleShow(JSONObject data, int seq) {
        String action = data.optString("action");
        log("收到屏幕展示指令: action=" + action);

        robotCapabilities.showUI(action, data, new RobotCapabilities.ShowCallback() {
            @Override
            public void onSuccess() {
                JSONObject result = new JSONObject();
                try {
                    result.put("success", true);
                } catch (JSONException e) {
                    LogUtils.e(TAG, "构建展示成功结果失败", e);
                }
                sendAck(seq, "evt_show_done", result);
            }

            @Override
            public void onFailure(String reason) {
                JSONObject result = new JSONObject();
                try {
                    result.put("success", false);
                    result.put("reason", reason);
                } catch (JSONException e) {
                    LogUtils.e(TAG, "构建展示失败结果失败", e);
                }
                sendAck(seq, "evt_show_done", result);
            }
        });
    }

    private void handleAckConfig(JSONObject data, int seq) throws JSONException {
        heartbeatIntervalSec = data.optInt("heartbeatInterval", 30);
        String wakeup = data.optString("wakeWord", "云猴云猴");
        int listenTimeout = data.optInt("listenTimeout", 30);
        String listenLanguage = data.optString("listenLanguage", "zh");
        log("配置: 心跳=" + heartbeatIntervalSec + "s, 唤醒词=" + wakeup);

        robotCapabilities.setWakeWord(wakeup);
        robotCapabilities.setListenTimeout(listenTimeout);
        robotCapabilities.setListenLanguage(listenLanguage);

        robotCapabilities.loadPointList(new RobotCapabilities.PointListCallback() {
            @Override
            public void onSuccess(java.util.List<RobotCapabilities.PointInfo> points) {
                log("点位加载成功，共 " + points.size() + " 个点位");
                sendRegister();
            }

            @Override
            public void onFailed(String error) {
                log("点位加载失败: " + error + "，使用默认配置继续注册");
                sendRegister();
            }
        });
    }

    private void sendRegister() {
        try {
            JSONObject reg = new JSONObject();
            reg.put("robotId", robotId);
            reg.put("robotName", "展厅导览机器人");
            reg.put("version", "1.0.0");
            reg.put("teachingProtocolVersion", 2);
            reg.put("battery", robotCapabilities.getCurrentBattery());
            reg.put("location", robotCapabilities.getCurrentLocation());
            sendEvent("evt_register", reg);
        } catch (JSONException e) {
            log("注册失败: " + e.getMessage());
        }
    }

    private void startHeartbeat() {
        if (heartbeatTask != null) {
            heartbeatTask.cancel(true);
            heartbeatTask = null;
        }

        heartbeatTask = heartbeatExecutor.scheduleWithFixedDelay(() -> {
            if (isConnected.get()) {
                JSONObject data = new JSONObject();
                try {
                    data.put("timestamp", System.currentTimeMillis());
                    data.put("battery", robotCapabilities.getCurrentBattery());
                } catch (JSONException e) {
                    LogUtils.e(TAG, "构建心跳数据失败", e);
                }
                sendEvent("evt_heartbeat", data);
            }
        }, heartbeatIntervalSec, heartbeatIntervalSec, TimeUnit.SECONDS);
    }

    private void sendEvent(String type, JSONObject data) {
        boolean teaching = data != null && (data.has("teachingTurnId") || "TEACHING".equals(data.optString("speechMode")));
        log("[机器人->中枢->事件上报: " + type + "]" + (teaching ? "教学事件（正文省略）" : data.toString()));
        sendMessage(type, seqGenerator.incrementAndGet(), data);
    }

    private void sendAck(int seq, String type, JSONObject data) {
        log("[机器人->中枢->确认响应: " + type + "]" + data.toString());
        sendMessage(type, seq, data);
    }

    private void sendMessage(String type, int seq, JSONObject data) {
        if (socket == null || socket.isClosed()) {
            log("发送失败: 连接已断开");
            return;
        }

        BufferedWriter w;
        synchronized (lock) {
            w = writer;
            if (w == null) {
                log("发送失败: writer 已关闭");
                return;
            }
        }

        String line;
        JSONObject msg = new JSONObject();
        try {
            msg.put("type", type);
            msg.put("seq", seq);
            msg.put("data", data != null ? data : new JSONObject());
            line = msg + "\n";
        } catch (JSONException e) {
            log("构建消息失败: " + e.getMessage());
            return;
        }

        final String text = line;
        final BufferedWriter writerRef = w;

        sendExecutor.execute(() -> {
            try {
                writerRef.write(text);
                writerRef.flush();
            } catch (IOException e) {
                log("发送失败: " + e.getMessage());
                mainHandler.post(() -> {
                    if (writer == writerRef && isConnected.get() && !isManualStop) {
                        handleDisconnect("发送失败");
                    }
                });
            }
        });
    }

    public void goOffline(String reason) {
        log("主动下线: " + reason);
        cancelTeachingCapture(reason, true, "状态：教学已取消");
        isManualStop = true;

        // 【修复】立即停止当前TTS播放
        if (robotCapabilities != null) {
            robotCapabilities.stopSpeak();
            log("已主动停止TTS播放");
        }

        if (isConnected.get()) {
            sendEvent("evt_offline", createData("reason", reason));
        }

        new Thread(() -> {
            try {
                Thread.sleep(100);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            closeQuietly();
        }).start();

        isConnected.set(false);
        if (heartbeatTask != null) {
            heartbeatTask.cancel(false);
        }
    }

    private void scheduleReconnect() {
        if (isManualStop) {
            log("手动断开状态，不重连");
            return;
        }

        if (reConnectCount >= MAX_RECONNECT_COUNT) {
            log("已达到最大重连次数(" + MAX_RECONNECT_COUNT + "次)，停止重连，请手动重新连接");
            isReconnectScheduled = false;
            isManualStop = true;
            OnConnectionListener timeoutListener = connectionListener;
            if (timeoutListener != null) {
                mainHandler.post(() -> timeoutListener.onDisconnected("TCP连接超时"));
            }
            return;
        }

        if (isReconnectScheduled) {
            log("已有重连任务，跳过");
            return;
        }

        isReconnectScheduled = true;

        if (reConnectDelay > MAX_RECONNECT_DELAY_SEC) {
            reConnectDelay = MAX_RECONNECT_DELAY_SEC;
        }

        reConnectCount++;
        int currentDelay = reConnectDelay;

        log("第" + reConnectCount + "次重连，等待" + currentDelay + "秒后重连");

        int taskId = reconnectTaskId.incrementAndGet();
        mainHandler.postDelayed(() -> {
            isReconnectScheduled = false;
            if (taskId != reconnectTaskId.get()) return;
            if (!isConnected.get() && !isManualStop) {
                connect();
                reConnectDelay = Math.min(reConnectDelay * 2, MAX_RECONNECT_DELAY_SEC);
            }
        }, currentDelay * 1000L);
    }

    /**
     * 处理播放音乐命令
     */
    private void handleCmdPlayMusic(JSONObject data, int seq) {
        String audioUrl = data.optString("audioUrl");
        String songName = data.optString("songName");
        
        log("收到播放音乐指令: songName=" + songName + ", audioUrl=" + audioUrl);
        
        if (audioUrl.isEmpty()) {
            log("音频URL为空，无法播放");
            JSONObject result = new JSONObject();
            try {
                result.put("reason", "音频URL为空");
                result.put("success", false);
            } catch (JSONException e) {
                LogUtils.e(TAG, "构建播放失败结果失败", e);
            }
            sendAck(seq, "evt_music_play_failed", result);
            return;
        }
        
        robotCapabilities.playMusic(audioUrl, new RobotCapabilities.MusicCallback() {
            @Override
            public void onSuccess() {
                log("音乐播放成功: " + songName);
                JSONObject result = new JSONObject();
                try {
                    result.put("songName", songName);
                    result.put("success", true);
                } catch (JSONException e) {
                    LogUtils.e(TAG, "构建播放成功结果失败", e);
                }
                sendAck(seq, "evt_music_playing", result);
            }

            @Override
            public void onFailed(String error) {
                log("音乐播放失败: " + error);
                JSONObject result = new JSONObject();
                try {
                    result.put("reason", error);
                    result.put("success", false);
                } catch (JSONException e) {
                    LogUtils.e(TAG, "构建播放失败结果失败", e);
                }
                sendAck(seq, "evt_music_play_failed", result);
            }

            @Override
            public void onComplete() {
                log("音乐播放完成: " + songName);
                JSONObject result = new JSONObject();
                try {
                    result.put("songName", songName);
                    result.put("success", true);
                } catch (JSONException e) {
                    LogUtils.e(TAG, "构建播放完成结果失败", e);
                }
                sendAck(seq, "evt_music_play_complete", result);
            }
        });
    }

    private JSONObject createData(String key, Object value) {
        JSONObject obj = new JSONObject();
        try {
            obj.put(key, value);
        } catch (JSONException e) {
            LogUtils.e(TAG, "创建数据失败", e);
        }
        return obj;
    }

    private void closeQuietly() {
        BufferedWriter w = null;
        BufferedReader r = null;
        Socket s = null;

        synchronized (lock) {
            if (isClosing) {
                return;
            }
            isClosing = true;

            w = writer;
            r = reader;
            s = socket;

            writer = null;
            reader = null;
            socket = null;
            isConnected.set(false);
            isConnecting.set(false);
        }

        try {
            if (r != null) {
                r.close();
            }
        } catch (Exception e) {
            LogUtils.e(TAG, "关闭 reader 异常: " + e.getMessage());
        }

        try {
            if (w != null) {
                w.close();
            }
        } catch (Exception e) {
            LogUtils.e(TAG, "关闭 writer 异常: " + e.getMessage());
        }

        try {
            if (s != null && !s.isClosed()) {
                s.close();
            }
        } catch (Exception e) {
            LogUtils.e(TAG, "关闭 socket 异常: " + e.getMessage());
        }

        synchronized (lock) {
            isClosing = false;
        }

        if (batteryMonitor != null) {
            batteryMonitor.setConnected(false);
        }
    }

    private void log(String msg) {
        OnLogListener listener = logListener;
        if (listener != null) {
            mainHandler.post(() -> listener.onLog(msg));
        } else {
            LogUtils.d(TAG, msg);
        }
    }

    public void setLogListener(OnLogListener listener) {
        this.logListener = listener;
    }

    public void reconnectWithNewServer(String ip, int port) {
        cancelTeachingCapture("server_changed", false, "状态：连接切换，教学已取消");
        synchronized (lock) {
            this.serverIp = ip;
            this.serverPort = port;
            isManualStop = false;
            isConnected.set(false);
            isConnecting.set(false);
            closeQuietly();
        }
        connect();
    }

    public void uploadFace(String userId, String name) {
        try {
            JSONObject reg = new JSONObject();
            reg.put("robotId", robotId);
            reg.put("userId", userId);
            reg.put("name", name);
            sendEvent("evt_register_face", reg);
        } catch (JSONException e) {
            log("注册失败: " + e.getMessage());
        }
    }

    public BatteryMonitor getBatteryMonitor() {
        return this.batteryMonitor;
    }

    public int getRobotBattery() {
        return batteryMonitor != null ? batteryMonitor.getBattery() : 0;
    }

    public void testSpeak(String text) {
        if (robotCapabilities != null) {
            log("测试播报: " + text);
            int seq = ReqIdUtils.next();
            robotCapabilities.speak(seq, text, "normal", new RobotCapabilities.SpeakCallback() {
                @Override
                public void onSuccess() {
                    log("测试播报完成");
                }

                @Override
                public void onFailed(String error) {
                    log("测试播报失败: " + error);
                }
            });
        } else {
            log("RobotCapabilities 未初始化");
        }
    }

    @Override
    public IBinder onBind(Intent intent) {
        return binder;
    }

    public void setConnectionListener(OnConnectionListener listener) {
        this.connectionListener = listener;
    }

    public boolean isConnected() {
        return isConnected.get();
    }

    public void onRobotOsSuspend() {
        if (robotCapabilities != null) robotCapabilities.restoreSystemWakeupHint();
        cancelTeachingCapture("robotos_suspend", true, "状态：系统暂停，教学已取消");
    }

    @SuppressLint("NewApi")
    @Override
    public void onDestroy() {
        super.onDestroy();

        cancelTeachingCapture("service_destroy", false, null);

        // 【修复】先停止TTS，再清理其他资源
        if (robotCapabilities != null) {
            robotCapabilities.stopSpeak();  // 立即停止播报
        }

        isManualStop = true;
        isConnected.set(false);

        closeQuietly();

        if (heartbeatTask != null) {
            heartbeatTask.cancel(true);
        }
        heartbeatExecutor.shutdownNow();

        readExecutor.shutdownNow();
        sendExecutor.shutdownNow();

        if (mWorkHandler != null) {
            mWorkHandler.removeCallbacksAndMessages(null);
        }
        if (mWorkThread != null) {
            mWorkThread.quitSafely();
        }

        if (listenHandler != null) {
            listenHandler.removeCallbacksAndMessages(null);
        }
        if (listenThread != null) {
            listenThread.quitSafely();
        }

        cancelTtsProtection();

        if (robotCapabilities != null) {
            robotCapabilities.destroy();
        }

        // 清理所有资源
        MyRobotApplication app = MyRobotApplication.getInstance();
        if (app != null) {
            app.cleanupAllResources();
        }

        if (batteryMonitor != null) {
            batteryMonitor.stopMonitoring();
        }

        log("TcpService已销毁");
    }

    public void startFaceRecognize() {
        if (robotCapabilities != null) {
            robotCapabilities.startFaceRecognize();
        }
    }

    public void stopFaceRecognize() {
        if (robotCapabilities != null) {
            robotCapabilities.stopFaceRecognize();
        }
    }

    public void showDemoImage() {
        if (robotCapabilities != null) {
            robotCapabilities.showImage();
        }
    }
}
