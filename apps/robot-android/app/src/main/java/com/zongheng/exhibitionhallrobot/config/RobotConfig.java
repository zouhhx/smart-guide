package com.zongheng.exhibitionhallrobot.config;

public class RobotConfig {
    // TTS配置优化：增加基础超时，适应长文本
    public static final long TTS_TIMEOUT_BASE_MS = 15000;      // 基础超时15秒（原10秒）
    public static final long TTS_TIMEOUT_PER_CHAR_MS = 300;    // 每字符350ms（原300ms）
    public static final long TTS_TIMEOUT_MAX_MS = 180000;       // 最大超时90秒（原60秒）

    public static final long LISTEN_TIMEOUT_DEFAULT_MS = 30000;
    public static final long FACE_DEBOUNCE_MS = 1000;
    public static final long REPORT_DEBOUNCE_MS = 3000;
    public static final long FOLLOW_CHECK_INTERVAL_MS = 5000;
    public static final long RE_REGISTER_DELAY_MS = 3000;

    public static final float NAVIGATION_COORDINATE_DEVIATION = 0.2f;
    public static final int NAVIGATION_TIMEOUT_MS = 30000;

    public static final int REGISTER_FACE_TIMEOUT = 30;
    public static final int REGISTER_FACE_TRY_COUNT = 3;
    public static final int REGISTER_FACE_SECOND_DELAY = 2;

    public static final int SMART_FOCUS_FOLLOW_TIMEOUT = 10;
    public static final int SMART_FOCUS_FOLLOW_DISTANCE = 5;

    public static final int CALLBACK_SWITCH_RETRY_COUNT = 3;
    public static final long CALLBACK_SWITCH_RETRY_DELAY_MS = 100;
}
