package com.zongheng.exhibitionhallrobot.monitor;

import static com.ainirobot.base.ApplicationWrapper.getApplicationContext;

import android.content.Context;
import android.content.BroadcastReceiver;
import android.content.Intent;
import android.content.IntentFilter;
import android.os.BatteryManager;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;

import com.zongheng.exhibitionhallrobot.service.RobotCapabilities;
import com.zongheng.exhibitionhallrobot.util.LogUtils;

public class BatteryMonitor {
    private static final String TAG = "BatteryMonitor";
    private Context context;
    private RobotCapabilities robotCapabilities;
    public interface Listener {
        void onBatteryChanged(int percent, boolean charging);
    }

    private Listener onBatteryChange;
    private int lastPercent = -1;
    private boolean lastCharging = false;
    private boolean batteryReceiverRegistered = false;
    private Handler handler = new Handler(Looper.getMainLooper());
    private boolean monitoring = false;
    private boolean isConnected = false;  // 添加连接状态标志

    public BatteryMonitor(Context context, RobotCapabilities robotCapabilities, Listener onBatteryChange) {
        this.context = context;
        this.robotCapabilities = robotCapabilities;
        this.onBatteryChange = onBatteryChange;
        LogUtils.d(TAG, "电量监控初始化成功");
    }

    /**
     * 设置连接状态（由 TcpService 调用）
     */
    public void setConnected(boolean connected) {
        this.isConnected = connected;
//        LogUtils.d(TAG, "连接状态设置: " + connected);
    }

    public void startMonitoring() {
        if (monitoring) {
            isConnected = true;
            return;
        }
        monitoring = true;
        LogUtils.d(TAG, "开始电量监控");
        if (!batteryReceiverRegistered) {
            IntentFilter filter = new IntentFilter(Intent.ACTION_BATTERY_CHANGED);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                context.registerReceiver(batteryReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
            } else {
                context.registerReceiver(batteryReceiver, filter);
            }
            batteryReceiverRegistered = true;
        }
        handler.post(periodicTask);
    }

    private final BroadcastReceiver batteryReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context ignored, Intent intent) {
            updateSnapshot(intent);
        }
    };

    private final Runnable periodicTask = new Runnable() {
        @Override
        public void run() {
            if (monitoring) {
                Intent sticky = context.registerReceiver(
                        null,
                        new IntentFilter(Intent.ACTION_BATTERY_CHANGED)
                );
                updateSnapshot(sticky);
                handler.postDelayed(this, 180000);
            }
        }
    };

    private void updateSnapshot(Intent batteryStatus) {
        int current = getBattery();
        boolean charging = isCharging(batteryStatus);
        if (current == lastPercent && charging == lastCharging) return;
        lastPercent = current;
        lastCharging = charging;
        if (robotCapabilities != null) robotCapabilities.updateBattery(current);
        if (isConnected && onBatteryChange != null) {
            onBatteryChange.onBatteryChanged(current, charging);
        }
    }

    private boolean isCharging(Intent batteryStatus) {
        if (batteryStatus == null) return false;
        int status = batteryStatus.getIntExtra(BatteryManager.EXTRA_STATUS, -1);
        return isChargingState(status, batteryStatus.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0));
    }

    public static boolean isChargingState(int status, int plugged) {
        return plugged != 0 || status == BatteryManager.BATTERY_STATUS_CHARGING
                || status == BatteryManager.BATTERY_STATUS_FULL;
    }

    public boolean isChargingNow() {
        return isCharging(context.registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED)));
    }

    public int getBattery() {
        return robotCapabilities.getRobotBattery() == 0 ? getSystemBattery() : robotCapabilities.getRobotBattery();
    }

    /**
     * 获取手机系统电量
     */
    private int getSystemBattery() {
        if (Build.VERSION.SDK_INT >= 21) {
            BatteryManager batteryManager = (BatteryManager) context.getSystemService(Context.BATTERY_SERVICE);
            return batteryManager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY);
        } else {
            IntentFilter ifilter = new IntentFilter(Intent.ACTION_BATTERY_CHANGED);
            Intent batteryStatus = getApplicationContext().registerReceiver(null, ifilter);
            if (batteryStatus != null) {
                int level = batteryStatus.getIntExtra(BatteryManager.EXTRA_LEVEL, -1);
                int scale = batteryStatus.getIntExtra(BatteryManager.EXTRA_SCALE, -1);
                return (int) ((level / (float) scale) * 100);
            }
        }
        return 50;
    }

    public void stopMonitoring() {
        monitoring = false;
        LogUtils.d(TAG, "停止电量监控");
        handler.removeCallbacks(periodicTask);
        if (batteryReceiverRegistered) {
            try {
                context.unregisterReceiver(batteryReceiver);
            } catch (IllegalArgumentException ignored) {
                // Receiver was already removed by the platform.
            }
            batteryReceiverRegistered = false;
        }
    }
}
