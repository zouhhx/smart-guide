package com.zongheng.exhibitionhallrobot;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.text.method.ScrollingMovementMethod;
import android.widget.GridView;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;

import com.zongheng.exhibitionhallrobot.adapter.PointGridAdapter;
import com.zongheng.exhibitionhallrobot.service.TcpService;
import com.zongheng.exhibitionhallrobot.util.ActivityManager;
import com.zongheng.exhibitionhallrobot.util.LogUtils;

import org.json.JSONException;
import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;

public class WelcomeActivity extends AppCompatActivity {
    private static final String TAG = "WelcomeActivity";

    // UI组件
    private TextView tvBattery, tvTime, tvLocation, tvStatus,  tvAsrLog;
    private GridView gvPoints;
    private LinearLayout llBatteryArea;

    private TcpService tcpService;
    private boolean isBound = false;
    private Handler uiHandler = new Handler(Looper.getMainLooper());
    private PointGridAdapter pointAdapter;
    private List<String> pointList = new ArrayList<>();

    // ASR对话记录
    private StringBuilder asrLogBuilder = new StringBuilder();

    // 电量更新定时器
    private Runnable batteryUpdateRunnable;

    private final ServiceConnection serviceConnection = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder service) {
            TcpService.LocalBinder binder = (TcpService.LocalBinder) service;
            tcpService = binder.getService();
            isBound = true;

            // 只设置日志监听（用于ASR显示），不重复设置connectionListener
            tcpService.setLogListener(log -> runOnUiThread(() -> {
                if (log.contains("语音指令:") || log.contains("识别结果:")) {
                    String text = log.replace("语音指令: ", "").replace("识别结果: ", "");
                    updateAsrDisplay("用户", text);
                }
                if (log.contains("TTS播报:") || log.contains("测试播报:")) {
                    String text = log.replace("TTS播报: ", "").replace("测试播报: ", "");
                    updateAsrDisplay("机器人", text);
                }
            }));

            // 获取当前连接状态并更新UI
            updateConnectionStatus();
            updateBatteryFromCapabilities();

            LogUtils.d(TAG, "主页绑定TCP服务成功");
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            isBound = false;
            tcpService = null;
            runOnUiThread(() -> tvStatus.setText("状态: 离线"));
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        
        // 注册到ActivityManager
        ActivityManager.getInstance().addActivity(this);
        
        setContentView(R.layout.activity_welcome);

        initViews();
        setupPointGrid();
        startTimeUpdate();
        startBatteryUpdate();
        bindTcpService();
        setupBatteryClickToDebug();
    }

    /**
     * 更新连接状态（从TcpService获取）
     */
    private void updateConnectionStatus() {
        if (tcpService != null && isBound) {
            boolean isConnected = tcpService.isConnected();
            runOnUiThread(() -> {
                if (isConnected) {
                    tvStatus.setText("状态: 在线");
                } else {
                    tvStatus.setText("状态: 离线");
                }
            });
        }
    }

    /**
     * 点击电池图标返回调试页面
     */
    private void setupBatteryClickToDebug() {
        if (llBatteryArea != null) {
            llBatteryArea.setOnClickListener(v -> {
                Toast.makeText(this, "返回调试页面", Toast.LENGTH_SHORT).show();
                goToDebugPage();
            });
        }
    }

    /**
     * 跳转到调试页面
     */
    private void goToDebugPage() {
        Intent intent = new Intent(this, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT);
        startActivity(intent);
    }

    private void initViews() {
        tvBattery = findViewById(R.id.tvBattery);
        tvTime = findViewById(R.id.tvTime);
        tvLocation = findViewById(R.id.tvLocation);
        tvStatus = findViewById(R.id.tvStatus);
        gvPoints = findViewById(R.id.gvPoints);
        tvAsrLog = findViewById(R.id.tvAsrLog);
        llBatteryArea = findViewById(R.id.llBatteryArea);
        tvAsrLog.setMovementMethod(new ScrollingMovementMethod());
    }

    private void setupPointGrid() {
        pointList.add("充电位");
        pointList.add("序厅");
        pointList.add("序厅入口");
        pointList.add("新能源展区");
        pointList.add("数字沙盘");
        pointList.add("会议室");

        pointAdapter = new PointGridAdapter(pointList, pointName -> {
            if (tcpService != null && isBound) {
                updateAsrDisplay("用户", "去" + pointName);
                JSONObject data = new JSONObject();
                try {
                    data.put("pointName", pointName);
                } catch (JSONException e) {}
//                tcpService.sendEvent("cmd_navigate", data);
            } else {
                Toast.makeText(this, "服务未连接", Toast.LENGTH_SHORT).show();
            }
        });
        gvPoints.setAdapter(pointAdapter);
    }

    private void bindTcpService() {
        try {
            Intent serviceIntent = new Intent(this, TcpService.class);
            bindService(serviceIntent, serviceConnection, Context.BIND_AUTO_CREATE);
        } catch (Exception e) {
            LogUtils.e(TAG, "绑定TCP服务失败: " + e.getMessage());
        }
    }

    private void updateBatteryFromCapabilities() {
        if (tcpService != null && isBound) {
            String batteryInfo = String.valueOf(tcpService.getRobotBattery());
            tvBattery.setText(batteryInfo);
//            LogUtils.d(TAG, "更新电量: " + batteryInfo);
        }
    }

    private void startBatteryUpdate() {
        batteryUpdateRunnable = new Runnable() {
            @Override
            public void run() {
                updateBatteryFromCapabilities();
                if (uiHandler != null) {
                    uiHandler.postDelayed(this, 30000);
                }
            }
        };
        uiHandler.post(batteryUpdateRunnable);
    }

    private void startTimeUpdate() {
        uiHandler.post(new Runnable() {
            @Override
            public void run() {
                SimpleDateFormat sdf = new SimpleDateFormat("HH:mm", Locale.getDefault());
                tvTime.setText(sdf.format(new Date()));
                uiHandler.postDelayed(this, 1000);
            }
        });
    }

    private void updateAsrDisplay(String speaker, String text) {
        runOnUiThread(() -> {
            asrLogBuilder.append(speaker).append(": ").append(text).append("\n");
            if (asrLogBuilder.length() > 5000) {
                String content = asrLogBuilder.toString();
                asrLogBuilder = new StringBuilder(content.substring(content.length() - 4000));
            }
            tvAsrLog.setText(asrLogBuilder.toString());
            tvAsrLog.post(() -> {
                int scrollAmount = tvAsrLog.getLayout() != null ?
                        tvAsrLog.getLayout().getLineTop(tvAsrLog.getLineCount()) - tvAsrLog.getHeight() : 0;
                if (scrollAmount > 0) tvAsrLog.scrollTo(0, scrollAmount);
            });
        });
    }

    public void updatePointList(List<String> points) {
        runOnUiThread(() -> {
            pointList.clear();
            pointList.addAll(points);
            pointAdapter.updateData(pointList);
        });
    }

    public void updateLocation(String location) {
        runOnUiThread(() -> tvLocation.setText("当前位置: " + location));
    }

    @Override
    protected void onDestroy() {
        // 从 ActivityManager 中移除
        ActivityManager.getInstance().removeActivity(this);
        
        if (batteryUpdateRunnable != null && uiHandler != null) {
            uiHandler.removeCallbacks(batteryUpdateRunnable);
        }
        if (isBound) {
            unbindService(serviceConnection);
            isBound = false;
        }
        super.onDestroy();
    }
}