package com.zongheng.exhibitionhallrobot.util;

import android.util.Log;

import java.io.PrintWriter;
import java.io.StringWriter;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

public class LogUtils {
    private static OnLogListener logListener;

    private static boolean isListenerEnabled = true;

    // 优化：添加需要显示到界面的日志标签白名单
    private static final Set<String> ALLOWED_TAGS = new HashSet<>(Arrays.asList(
            "TcpService",      // TCP服务相关（包含TTS和语音指令）
            "RobotCapabilities", // 机器人能力（包含TTS播放状态）
            "MySkillCallback"   // 语音识别回调
    ));

    // 优化：添加关键词过滤，只允许特定类型的日志
    private static final Set<String> ALLOWED_KEYWORDS = new HashSet<>(Arrays.asList(
            "TTS", "tts", "播报", "语音", "ASR", "识别结果", "唤醒",
            "error", "Error", "ERROR", "失败", "异常", "❌"
    ));

    public interface OnLogListener {
        void onLog(String log);
    }

    public static void setLogListener(OnLogListener listener) {
        logListener = listener;
    }

    public static void enableListener(boolean enable) {
        isListenerEnabled = enable;
    }

    public static void v(String tag, String message) {
        Log.v(tag, message);
    }

    public static void d(String tag, String message) {
        Log.d(tag, message);

        // 优化：只有白名单标签或包含关键词的日志才输出到界面
        if (isListenerEnabled && logListener != null && shouldDisplayToUI(tag, message)) {
            logListener.onLog("[DEBUG] " + message);
        }
    }

    public static void e(String tag, String message) {
        Log.e(tag, message);

        // 优化：错误日志始终显示
        if (isListenerEnabled && logListener != null) {
            logListener.onLog("[ERROR] " + message);
        }
    }

    public static void e(String tag, String message, Throwable throwable) {
        Log.e(tag, message, throwable);

        // 优化：错误日志始终显示
        if (isListenerEnabled && logListener != null) {
            StringWriter sw = new StringWriter();
            PrintWriter pw = new PrintWriter(sw);
            throwable.printStackTrace(pw);
            logListener.onLog("[ERROR] " + message + "\n" + sw.toString());
        }
    }

    public static void i(String tag, String message) {
        Log.i(tag, message);

        // 优化：只有白名单标签或包含关键词的日志才输出到界面
        if (isListenerEnabled && logListener != null && shouldDisplayToUI(tag, message)) {
            logListener.onLog("[INFO] " + message);
        }
    }

    public static void w(String tag, String message) {
        Log.w(tag, message);

        // 优化：警告日志始终显示
        if (isListenerEnabled && logListener != null) {
            logListener.onLog("[WARN] " + message);
        }
    }

    /**
     * 优化：判断日志是否应该显示到UI界面
     * 规则：
     * 1. 标签在白名单中
     * 2. 或者消息包含允许的关键词（TTS、语音、错误等）
     */
    private static boolean shouldDisplayToUI(String tag, String message) {
        // 检查标签是否在白名单中
        if (ALLOWED_TAGS.contains(tag)) {
            return true;
        }

        // 检查消息是否包含允许的关键词
        for (String keyword : ALLOWED_KEYWORDS) {
            if (message.contains(keyword)) {
                return true;
            }
        }

        return false;
    }
}
