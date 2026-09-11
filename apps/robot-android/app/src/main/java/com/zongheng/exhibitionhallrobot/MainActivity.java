package com.zongheng.exhibitionhallrobot;

import android.Manifest;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.text.method.ScrollingMovementMethod;
import android.view.MotionEvent;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.appcompat.app.AlertDialog;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import com.ainirobot.coreservice.client.Definition;
import com.ainirobot.coreservice.client.RobotApi;
import com.ainirobot.coreservice.client.listener.ActionListener;
import com.ainirobot.coreservice.client.listener.Person;
import com.ainirobot.coreservice.client.person.PersonApi;
import com.ainirobot.coreservice.client.person.PersonUtils;
import com.zongheng.exhibitionhallrobot.application.MyRobotApplication;
import com.zongheng.exhibitionhallrobot.service.TcpService;
import com.zongheng.exhibitionhallrobot.util.ActivityManager;
import com.zongheng.exhibitionhallrobot.util.LogUtils;
import com.zongheng.exhibitionhallrobot.util.ReqIdUtils;
import com.zongheng.exhibitionhallrobot.ui.RobotPresentation;
import com.zongheng.exhibitionhallrobot.speech.LocalVoiceSettings;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * 机器人展示页；开发工具保留在同一Activity的隐藏面板中。
 */
public class MainActivity extends AppCompatActivity {

    private static final String TAG = "MainActivity";
    private TextView logTextView;
    private TextView conversationTextView;
    private ScrollView conversationScrollView;
    private TextView teachingTextView;
    private View teachingCard;
    private Button finishTeachingButton, cancelTeachingButton;
    private EditText etServerIp, etServerPort, etTtsText;
    private Button btnConnect, btnPreview, btnOffline, btnGetBattery, btnTtsTest, btnFaceRegister, btnCameraPreview;
    private EditText etFaceName;
    private TcpService tcpService;
    private boolean isBound = false;
    private boolean bindingRequested = false;
    private RobotPresentation presentation;
    private com.zongheng.exhibitionhallrobot.ui.RobotSpeechAnimation speechAnimation;
    private boolean developerVisible;
    private View developerAccessZone;
    private final Handler presentationHandler = new Handler(Looper.getMainLooper());
    private final Runnable developerHold = () -> {
        if (!developerVisible && hasWindowFocus() && developerAccessZone.isPressed())
            developerAccessZone.performLongClick();
    };
    private final Runnable presentationTick = new Runnable() {
        @Override public void run() {
            if (presentation != null) {
                presentation.connection(tcpService != null && tcpService.isConnected(),
                        tcpService != null && tcpService.isRobotSpeaking());
            }
            if (speechAnimation != null) speechAnimation.update(
                    tcpService != null && tcpService.isVoiceAudioPlaying(), !developerVisible);
            presentationHandler.postDelayed(this, 100);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        
        // 注册到ActivityManager
        ActivityManager.getInstance().addActivity(this);
        
        setContentView(R.layout.activity_main);
        speechAnimation = new com.zongheng.exhibitionhallrobot.ui.RobotSpeechAnimation(findViewById(R.id.robotSpeakingVideo));

        // 设置全局日志监听
        LogUtils.setLogListener(log -> runOnUiThread(() -> appendLog(log)));

        initViews();
        presentation = new RobotPresentation(this,
                () -> { if (tcpService != null) tcpService.finishTeachingCapture(); },
                () -> { if (tcpService != null) tcpService.cancelTeachingCapture("cancel_button"); });
        initDeveloperAccess();
        findViewById(R.id.returnToRobotButton).setOnClickListener(v -> showDeveloper(false));
        showDeveloper(false);
        checkPermissions();
        startTcpService();
    }

    private final ServiceConnection serviceConnection = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder service) {
            TcpService.LocalBinder binder = (TcpService.LocalBinder) service;
            tcpService = binder.getService();
            isBound = true;
            tcpService.setConversationListener(entries ->
                    runOnUiThread(() -> renderConversation(entries)));
            tcpService.setTeachingListener(() -> runOnUiThread(MainActivity.this::renderTeaching));
            tcpService.setSpeechPreviewListener((text, staff) -> runOnUiThread(() -> {
                if (presentation != null) presentation.preview(text, staff);
            }));
            // tcpService服务日志输出
            tcpService.setLogListener(log -> {
                // [v7.3] 隐藏人脸/人数相关日志，避免调试页被识别上报刷屏
                if (isFaceRelatedLog(log)) return;
                runOnUiThread(() -> appendLog(log));
            });
            // 连接状态监听
            tcpService.setConnectionListener(new TcpService.OnConnectionListener() {
                @Override
                public void onConnected() {
                    appendLog("✅ 已连接TCP服务器");
                    runOnUiThread(() -> {
                        btnOffline.setEnabled(true);
                        btnPreview.setEnabled(true);
                        btnGetBattery.setEnabled(true);
                        btnTtsTest.setEnabled(true);
                        btnFaceRegister.setEnabled(true);
                        btnConnect.setEnabled(false);
                        btnConnect.setText("已连接");
                        //连接成功后再注册人脸服务
                        tcpService.startFaceRecognize();
                    });
                }

                @Override
                public void onDisconnected(String reason) {
                    appendLog("❌ TCP服务器已断开: " + reason);
                    runOnUiThread(() -> {
                        btnPreview.setEnabled(false);
                        btnTtsTest.setEnabled(false);
                        btnGetBattery.setEnabled(false);
                        btnFaceRegister.setEnabled(false);
                        btnConnect.setEnabled(true);
                        btnConnect.setText("重新连接");
                        //连接断开取消人脸服务
                        tcpService.stopFaceRecognize();
                    });
                }
            });
            // The presentation page connects through the service's existing guarded path.
            // Binding/recreating the view must not reset a live TCP/teaching session.
            tcpService.ensureConnectedForPresentation();
            if (tcpService.isConnected()) {
                btnConnect.setEnabled(false);
                btnConnect.setText("已连接");
                btnOffline.setEnabled(true);
                btnPreview.setEnabled(true);
                btnGetBattery.setEnabled(true);
                btnTtsTest.setEnabled(true);
                btnFaceRegister.setEnabled(true);
            }
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            isBound = false;
            tcpService = null;
            appendLog("TCP服务已断开");
        }
    };
    private String ip;
    private int port;

    private void startTcpService() {
        try {
            if (bindingRequested || isBound || tcpService != null) {
                appendLog("服务已启动");
                return;
            }
            Intent serviceIntent = new Intent(this, TcpService.class);
            startService(serviceIntent);
            bindingRequested = bindService(serviceIntent, serviceConnection, Context.BIND_AUTO_CREATE);
            appendLog("TCP服务已启动");
        } catch (Exception e) {
            appendLog("启动TCP服务失败: " + e.getMessage());
        }
    }

    private void initViews() {
        logTextView = findViewById(R.id.logTextView);
        logTextView.setMovementMethod(new ScrollingMovementMethod());
        conversationTextView = findViewById(R.id.conversationTextView);
        conversationScrollView = findViewById(R.id.conversationScrollView);
        teachingCard = findViewById(R.id.teachingCard);
        teachingTextView = findViewById(R.id.teachingTextView);
        finishTeachingButton = findViewById(R.id.finishTeachingButton);
        cancelTeachingButton = findViewById(R.id.cancelTeachingButton);
        finishTeachingButton.setOnClickListener(v -> { if (tcpService != null) tcpService.finishTeachingCapture(); });
        cancelTeachingButton.setOnClickListener(v -> { if (tcpService != null) tcpService.cancelTeachingCapture("cancel_button"); });

        etServerIp = findViewById(R.id.etServerIp);
        etServerPort = findViewById(R.id.etServerPort);
        etTtsText = findViewById(R.id.etTtsText);
        initVoiceSettings();

        // 连接按钮
        btnConnect = findViewById(R.id.btnConnect);
        btnConnect.setOnClickListener(v -> {
            try {
                ip = etServerIp.getText().toString().trim();
                String portStr = etServerPort.getText().toString().trim();

                if (ip.isEmpty() || portStr.isEmpty()) {
                    Toast.makeText(this, "请输入IP和端口", Toast.LENGTH_SHORT).show();
                    return;
                }

                try {
                    port = Integer.parseInt(portStr);
                } catch (Exception e) {
                    Toast.makeText(this, "端口格式错误", Toast.LENGTH_SHORT).show();
                    return;
                }
                btnConnect.setEnabled(false);
                btnConnect.setText("正在连接...");
                appendLog("准备连接 " + ip + ":" + port);

                // 第一次点击 → 启动服务
                if (!isBound) {
                    startTcpService();

                    // 等服务绑定完成再连接
                    btnConnect.postDelayed(() -> {
                        if (tcpService != null) {
                            tcpService.reconnectWithNewServer(ip, port);
                        }
                    }, 3500);

                } else {
                    // 已启动 → 直接重连
                    if (tcpService != null) {
                        tcpService.reconnectWithNewServer(ip, port);
                    }
                }
            } catch (Exception e) {
                appendLog("点击异常: " + e.getMessage());
                LogUtils.e(TAG, "点击异常", e);
            }
        });

        // 清除日志
        findViewById(R.id.btnClearLog).setOnClickListener(v -> {
            logTextView.setText("");
            // 清除后滚动到顶部
            logTextView.scrollTo(0, 0);
        });

        // 主动下线
        btnOffline = findViewById(R.id.btnOffline);
        btnOffline.setEnabled(false);
        btnOffline.setOnClickListener(v -> {
            if (tcpService != null) {
                btnConnect.setEnabled(true);
                btnConnect.setText("测试连接");
                btnOffline.setEnabled(false);
                btnPreview.setEnabled(false);
                btnTtsTest.setEnabled(false);
                btnGetBattery.setEnabled(false);
                btnFaceRegister.setEnabled(false);
                tcpService.goOffline("shutdown");
            }
            else appendLog("TCP服务未连接");
        });

        // 获取电量
        btnGetBattery = findViewById(R.id.btnGetBattery);
        btnGetBattery.setEnabled(false);
        btnGetBattery.setOnClickListener(v -> {
            int battery = tcpService.getBatteryMonitor().getBattery();
            appendLog("当前电量: " + battery + "%");
            Toast.makeText(this, "电量: " + battery + "%", Toast.LENGTH_SHORT).show();
        });

        // 播报测试
        btnTtsTest = findViewById(R.id.btnTtsTest);
        btnTtsTest.setEnabled(false);
        btnTtsTest.setOnClickListener(v -> {
            String text = etTtsText.getText().toString().trim();
            if (text.isEmpty()) {
                Toast.makeText(this, "请输入播报文字", Toast.LENGTH_SHORT).show();
                return;
            }
            appendLog(">>> 测试播报: " + text);
            if (tcpService != null) {
                tcpService.testSpeak(text);
            } else {
                appendLog("服务未连接，无法播报");
            }
        });
        btnPreview = findViewById(R.id.btnPreview);
        btnPreview.setEnabled(false);
        btnPreview.setOnClickListener(v -> {
            tcpService.showDemoImage();
        });

        findViewById(R.id.btnExit).setOnClickListener(v -> {
            appendLog("正在退出应用...");

            // 断开连接
            if (tcpService != null && isBound) {
                tcpService.goOffline("app_exit");
            }

            // 延迟一下确保消息发送
            new Handler(Looper.getMainLooper()).postDelayed(() -> {
                if (isBound) {
                    unbindService(serviceConnection);
                    isBound = false;
                    bindingRequested = false;
                }

                // 清理所有资源（包括关闭所有Activity）
                MyRobotApplication app = MyRobotApplication.getInstance();
                if (app != null) {
                    app.cleanupAllResources();
                }

                // 停止TCP服务
                Intent serviceIntent = new Intent(MainActivity.this, TcpService.class);
                stopService(serviceIntent);

                appendLog("应用已退出");
                
                // 使用finishAffinity关闭所有Activity
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.JELLY_BEAN) {
                    finishAffinity();
                } else {
                    finish();
                }
                
                // 最后调用System.exit确保进程结束
                System.exit(0);
            }, 300); // 增加延迟时间，确保清理完成
        });

        etFaceName = findViewById(R.id.etFaceName);
        btnFaceRegister = findViewById(R.id.btnFaceRegister);
        btnFaceRegister.setEnabled(false);
        btnFaceRegister.setOnClickListener(v -> {
            String personName = etFaceName.getText().toString().trim();

            if (personName.isEmpty()) {
                appendLog("请输入人名");
                return;
            }
            appendLog("开始录入人脸: " + personName);
            int reqId = ReqIdUtils.next();

            RobotApi.getInstance().startRegister(reqId, personName,
                    AppConstants.REGISTER_FACE_TIMEOUT,
                    AppConstants.REGISTER_FACE_TRY_COUNT,
                    AppConstants.REGISTER_FACE_SECOND_DELAY, new ActionListener() {
                @Override
                public void onResult(int status, String response, String extraData) {
                    if (Definition.RESULT_OK != status) {
                        appendLog("注册失败: " + status + ", " + response + " " + extraData);
                        return;
                    }
                    try {
                        JSONObject json = new JSONObject(response);

                        String remoteType = json.optString(Definition.REGISTER_REMOTE_TYPE);
                        String remoteName = json.optString(Definition.REGISTER_REMOTE_NAME);

                        if (Definition.REGISTER_REMOTE_SERVER_EXIST.equals(remoteType)) {
                            appendLog("用户已存在: " + remoteName);
                        } else if (Definition.REGISTER_REMOTE_SERVER_NEW.equals(remoteType)) {
                            final List<Person> allFaceList = PersonApi.getInstance().getAllPersons(1);
                            //get best person face to register
                            //找到人脸最佳的进行识别
                            final Person person = PersonUtils.getBestFace(allFaceList);
                            if(person == null){
                                appendLog("没有找到符合要求的人脸图片");
                                return;
                            }
                            appendLog("注册成功, UserID：" + person.getUserId() + ", 姓名: " + person.getName());
                            // 上报TCP
                            if (tcpService != null) {
                                tcpService.uploadFace(person.getUserId(), person.getName());
                            }
                        }
                    } catch (JSONException e) {
                        appendLog("识别人脸失败: " + e.getMessage());
                    }
                }
            });
        });

        // 拍照预览按钮：生产拍照流程由 Hub 下发 cmd_take_photo，调试页不再承载拍照逻辑。
        btnCameraPreview = findViewById(R.id.btnCameraPreview);
        btnCameraPreview.setOnClickListener(v ->
                Toast.makeText(this, "请通过中枢拍照指令触发拍照", Toast.LENGTH_SHORT).show());
    }

    private void checkPermissions() {
        List<String> needed = new ArrayList<>();
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.INTERNET) != PackageManager.PERMISSION_GRANTED)
            needed.add(Manifest.permission.INTERNET);
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED)
            needed.add(Manifest.permission.RECORD_AUDIO);
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED)
            needed.add(Manifest.permission.CAMERA);
        if (!needed.isEmpty()) {
            ActivityCompat.requestPermissions(this, needed.toArray(new String[0]), 100);
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions, @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        for (int i = 0; i < permissions.length; i++) {
            if (grantResults[i] != PackageManager.PERMISSION_GRANTED) {
                appendLog("权限拒绝: " + permissions[i]);
            }
        }
    }

    private boolean isFaceRelatedLog(String log) {
        if (log == null) return false;
        return log.contains("evt_face")
                || log.contains("evt_person")
                || log.contains("人脸")
                || log.contains("人员")
                || log.contains("人数")
                || log.contains("Person")
                || log.contains("face")
                || log.contains("Face")
                || log.contains("personCount")
                || log.contains("迟到回调");
    }

    private void appendLog(String log) {
        // Logcat/file logging remains; do not repaint a hidden developer panel.
        if (!developerVisible) return;
        logTextView.post(() -> {
            String current = logTextView.getText().toString();
            if (current.length() > 5000) {
                logTextView.setText("");
            }
            logTextView.append(log + "\n");
            if (logTextView.getLayout() != null) {
                int scrollAmount = logTextView.getLayout().getLineTop(logTextView.getLineCount()) - logTextView.getHeight();
                logTextView.scrollTo(0, Math.max(scrollAmount, 0));
            }
        });
    }

    private void renderConversation(List<TcpService.ConversationEntry> entries) {
        if (presentation != null) presentation.conversation(entries);
        if (conversationTextView == null || conversationScrollView == null) return;

        if (entries == null || entries.isEmpty()) {
            conversationTextView.setText("请先说“小豹小豹”，然后开始提问");
            conversationScrollView.scrollTo(0, 0);
            return;
        }

        StringBuilder display = new StringBuilder();
        for (TcpService.ConversationEntry entry : entries) {
            if (display.length() > 0) display.append("\n\n");
            String role = entry.getRole();
            if (TcpService.ConversationEntry.ROLE_USER.equals(role)) {
                display.append("我：");
            } else if (TcpService.ConversationEntry.ROLE_STAFF.equals(role)) {
                display.append("工作人员：");
            } else if (TcpService.ConversationEntry.ROLE_STATUS.equals(role)) {
                display.append("【").append(entry.getText()).append("】");
                continue;
            } else {
                display.append("小豹：");
            }
            display.append(entry.getText());
        }
        conversationTextView.setText(display.toString());
        conversationScrollView.post(() -> conversationScrollView.fullScroll(View.FOCUS_DOWN));
    }

    private void initVoiceSettings() {
        Button choose = findViewById(R.id.chooseLocalVoice);
        choose.setText(LocalVoiceSettings.selectedLabel(this));
        choose.setOnClickListener(v -> new AlertDialog.Builder(this)
                .setTitle("选择机器人音色")
                .setSingleChoiceItems(LocalVoiceSettings.labels(), LocalVoiceSettings.selectedIndex(this), (dialog, index) -> {
                    if (LocalVoiceSettings.select(this, index)) {
                        choose.setText(LocalVoiceSettings.selectedLabel(this));
                        Toast.makeText(this, "音色已保存，下一次播报生效", Toast.LENGTH_LONG).show();
                        dialog.dismiss();
                    } else Toast.makeText(this, "保存失败，请检查音色样本是否已安装", Toast.LENGTH_SHORT).show();
                }).setNegativeButton("取消", null).show());
        findViewById(R.id.previewLocalVoice).setOnClickListener(v -> {
            if (tcpService == null) {
                Toast.makeText(this, "语音服务正在启动，请稍候", Toast.LENGTH_SHORT).show();
            } else if (tcpService.isTeachingActive() || tcpService.isRobotSpeaking()) {
                Toast.makeText(this, "请等当前讲解或教学结束后再试听", Toast.LENGTH_SHORT).show();
            } else tcpService.testSpeak("您好，我是小豹，欢迎来到展厅。");
        });
    }

    private void renderTeaching() {
        if (teachingCard == null || tcpService == null) return;
        String text = tcpService.getTeachingDisplay();
        if (presentation != null) presentation.teaching(text,
                tcpService.isTeachingActive(), tcpService.isTeachingCollecting());
        teachingCard.setVisibility(text.isEmpty() ? View.GONE : View.VISIBLE);
        teachingTextView.setText(text);
        finishTeachingButton.setEnabled(tcpService.isTeachingCollecting());
        cancelTeachingButton.setEnabled(tcpService.isTeachingCollecting());
    }

    @Override
    protected void onResume() {
        super.onResume();
        presentationHandler.removeCallbacks(presentationTick);
        presentationHandler.post(presentationTick);
        MyRobotApplication app = MyRobotApplication.getInstance();
        if (app != null && app.isSdkReady()) {
            app.recoverSpeechScene();
        }
    }

    private void initDeveloperAccess() {
        developerAccessZone = findViewById(R.id.developerAccessZone);
        developerAccessZone.setOnLongClickListener(v -> { showDeveloper(true); return true; });
        developerAccessZone.setOnTouchListener((view, event) -> {
            switch (event.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    cancelDeveloperHold();
                    view.setPressed(true);
                    presentationHandler.postDelayed(developerHold, 2000);
                    break;
                case MotionEvent.ACTION_MOVE:
                    if (event.getX() < 0 || event.getX() >= view.getWidth()
                            || event.getY() < 0 || event.getY() >= view.getHeight()) cancelDeveloperHold();
                    break;
                case MotionEvent.ACTION_UP:
                    if (view.isPressed()) view.performClick();
                    cancelDeveloperHold();
                    break;
                case MotionEvent.ACTION_CANCEL:
                case MotionEvent.ACTION_POINTER_DOWN:
                    cancelDeveloperHold();
                    break;
            }
            return true;
        });
    }

    private void cancelDeveloperHold() {
        presentationHandler.removeCallbacks(developerHold);
        if (developerAccessZone != null) developerAccessZone.setPressed(false);
    }

    private void showDeveloper(boolean visible) {
        cancelDeveloperHold();
        developerVisible = visible;
        if (speechAnimation != null) speechAnimation.update(tcpService != null && tcpService.isVoiceAudioPlaying(), !visible);
        findViewById(R.id.developerPanel).setVisibility(visible ? View.VISIBLE : View.GONE);
        findViewById(R.id.robotPresentation).setVisibility(visible ? View.GONE : View.VISIBLE);
        getWindow().getDecorView().setSystemUiVisibility(visible ? View.SYSTEM_UI_FLAG_VISIBLE
                : View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
    }

    @Override public void onBackPressed() {
        if (developerVisible) showDeveloper(false);
        else super.onBackPressed();
    }

    @Override protected void onPause() {
        if (speechAnimation != null) speechAnimation.update(false, false);
        cancelDeveloperHold();
        presentationHandler.removeCallbacks(presentationTick);
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        if (speechAnimation != null) speechAnimation.release();
        // 从 ActivityManager 中移除
        ActivityManager.getInstance().removeActivity(this);
        
        if (tcpService != null) tcpService.setConversationListener(null);
        if (tcpService != null) tcpService.setSpeechPreviewListener(null);
        if (tcpService != null) tcpService.setTeachingListener(null);
        presentationHandler.removeCallbacksAndMessages(null);
        if (tcpService != null) tcpService.setLogListener(null);
        if (tcpService != null) tcpService.setConnectionListener(null);
        if (bindingRequested) unbindService(serviceConnection);
        super.onDestroy();
    }
}
