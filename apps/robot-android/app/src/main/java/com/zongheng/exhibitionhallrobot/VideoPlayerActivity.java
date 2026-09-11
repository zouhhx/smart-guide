package com.zongheng.exhibitionhallrobot;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.graphics.Color;
import android.graphics.SurfaceTexture;
import android.graphics.Typeface;
import android.media.MediaPlayer;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.util.TypedValue;
import android.view.MotionEvent;
import android.view.Surface;
import android.view.TextureView;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.widget.FrameLayout;
import android.widget.ImageButton;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TableRow;
import android.widget.TextView;

import com.zongheng.exhibitionhallrobot.application.MyRobotApplication;
import com.zongheng.exhibitionhallrobot.service.TcpService;
import com.zongheng.exhibitionhallrobot.util.ActivityManager;
import com.zongheng.exhibitionhallrobot.util.LogUtils;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.util.concurrent.atomic.AtomicBoolean;

public class VideoPlayerActivity extends Activity implements TextureView.SurfaceTextureListener {

    private static final String TAG = "VideoPlayerActivity";
    private static final String EXTRA_VIDEO_RES_ID = "video_res_id";
    private static final String EXTRA_STATIC_IMAGE_RES_ID = "static_image_res_id";
    private static final String EXTRA_LOOP_MODE = "loop_mode";
    private static final int DEFAULT_STATIC_IMAGE = R.drawable.bg_welcome;

    private static VideoPlayerActivity currentInstance = null;
    private static OnVideoStopListener stopListener = null;

    private TextureView textureView;
    private MediaPlayer mediaPlayer;
    private ImageView staticImageView;
    private ImageButton closeButton;
    private FrameLayout container;
    private ScrollView contentContainer;
    private LinearLayout contentLayout;
    private Handler handler = new Handler();
    private boolean isLoopMode = false;
    private boolean isUserClosed = false;
    private boolean contentAdded = false;

    private final AtomicBoolean isClosingHandled = new AtomicBoolean(false);
    private boolean isVideoPrepared = false;
    private SurfaceTexture pendingSurfaceTexture;
    private Surface pendingSurface;
    private int pendingVideoResId = -1;
    private boolean firstFrameRendered = false;   // 首帧渲染标志

    // 五击返回 Debug 页面
    private int clickCount = 0;
    private Handler clickHandler = new Handler();
    private Runnable clickResetRunnable = () -> clickCount = 0;

    private Runnable contentBringToFrontRunnable = new Runnable() {
        @Override
        public void run() {
            if (contentContainer != null && contentContainer.getVisibility() == View.VISIBLE) {
                contentContainer.bringToFront();
            }
            handler.postDelayed(this, 500);
        }
    };

    public interface OnVideoStopListener {
        void onVideoStopped(boolean userClosed);
    }

    public static void startLoop(Context context, int videoResId, OnVideoStopListener listener) {
        // 【关键修复】如果视频已在播放且未结束，直接返回
        if (currentInstance != null && !currentInstance.isFinishing()) {
            // 检查MediaPlayer是否正在播放
            boolean isPlaying = currentInstance.mediaPlayer != null && currentInstance.mediaPlayer.isPlaying();
            
            if (isPlaying) {
                LogUtils.d(TAG, "视频已在播放中，忽略重复启动请求");
                if (listener != null) {
                    listener.onVideoStopped(false);
                }
                return;
            } else {
                // MediaPlayer未播放，可能是已停止但未销毁，需要重新启动
                LogUtils.d(TAG, "视频Activity存在但未播放，重新启动");
                // 不return，继续执行下面的启动逻辑
            }
        }
        
        stopListener = listener;
        start(context, videoResId, true);
    }

    public static void start(Context context, int videoResId, boolean loopMode) {
        Intent intent = new Intent(context, VideoPlayerActivity.class);
        intent.putExtra(EXTRA_VIDEO_RES_ID, videoResId);
        intent.putExtra(EXTRA_LOOP_MODE, loopMode);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        context.startActivity(intent);
    }

    public static void stopCurrent() {
        stopCurrent(false);
    }

    public static void stopCurrent(boolean byUser) {
        if (currentInstance != null && !currentInstance.isFinishing()) {
            currentInstance.runOnUiThread(() -> {
                currentInstance.isUserClosed = byUser;
                
                // 【关键修复】TTS结束时，暂停视频而不是完全释放
                // 这样下次TTS启动时可以快速恢复
                if (currentInstance.mediaPlayer != null) {
                    try {
                        if (currentInstance.mediaPlayer.isPlaying()) {
                            currentInstance.mediaPlayer.pause();
                            LogUtils.d(TAG, "视频已暂停（未释放）");
                        }
                    } catch (Exception e) {
                        LogUtils.e(TAG, "暂停视频失败: " + e.getMessage());
                    }
                }
                
                // 显示静态图片
                currentInstance.showStaticImage();
                
                if (stopListener != null) {
                    stopListener.onVideoStopped(byUser);
                }
                if (byUser) {
                    TcpService tcpService = MyRobotApplication.getInstance().getTcpService();
                    if (tcpService != null) {
                        LogUtils.d(TAG, "用户关闭视频，立即启动自动收音（超时0秒）");
                        tcpService.startAutoListening();
                    } else {
                        LogUtils.w(TAG, "TcpService 未初始化，无法启动收音");
                    }
                }
            });
        } else {
            if (stopListener != null) {
                stopListener.onVideoStopped(byUser);
            }
        }
    }

    public static void forceFinish() {
        if (currentInstance != null && !currentInstance.isFinishing()) {
            currentInstance.finishVideo();
            currentInstance = null;
        }
        stopListener = null;
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        currentInstance = this;
        ActivityManager.getInstance().addActivity(this);

        setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE);
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        getWindow().setFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN,
                WindowManager.LayoutParams.FLAG_FULLSCREEN);

        setContentView(R.layout.activity_video_player);

        textureView = findViewById(R.id.textureView);
        staticImageView = findViewById(R.id.staticImageView);
        closeButton = findViewById(R.id.closeButton);
        container = findViewById(R.id.container);
        contentContainer = findViewById(R.id.contentContainer);
        contentLayout = findViewById(R.id.contentLayout);

        textureView.setSurfaceTextureListener(this);

        setupCloseButton();
        setupClickListener();

        int videoResId = getIntent().getIntExtra(EXTRA_VIDEO_RES_ID, -1);
        int staticImageResId = getIntent().getIntExtra(EXTRA_STATIC_IMAGE_RES_ID, DEFAULT_STATIC_IMAGE);
        isLoopMode = getIntent().getBooleanExtra(EXTRA_LOOP_MODE, false);

        staticImageView.setImageResource(staticImageResId);
        showStaticImage();

        if (videoResId != -1 && isLoopMode) {
            playVideo(videoResId);
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        int videoResId = intent.getIntExtra(EXTRA_VIDEO_RES_ID, -1);
        boolean loopMode = intent.getBooleanExtra(EXTRA_LOOP_MODE, false);
        if (videoResId != -1 && loopMode) {
            this.isLoopMode = loopMode;
            playVideo(videoResId);
        }
    }

    private void finishVideo() {
        releaseMediaPlayer();
        finish();
    }

    @SuppressLint("ClickableViewAccessibility")
    private void setupClickListener() {
        container.setOnTouchListener((v, event) -> {
            if (closeButton != null && closeButton.getVisibility() == View.VISIBLE && closeButton.isEnabled()) {
                int[] location = new int[2];
                closeButton.getLocationOnScreen(location);
                int x = (int) event.getRawX();
                int y = (int) event.getRawY();
                if (x >= location[0] && x <= location[0] + closeButton.getWidth() &&
                        y >= location[1] && y <= location[1] + closeButton.getHeight()) {
                    return false;
                }
            }
            if (event.getAction() == MotionEvent.ACTION_UP) {
                clickCount++;
                clickHandler.removeCallbacks(clickResetRunnable);
                if (clickCount >= 5) {
                    clickCount = 0;
                    goToDebugPage();
                    return true;
                }
                clickHandler.postDelayed(clickResetRunnable, 1500);
            }
            return true;
        });
    }

    private void setupCloseButton() {
        closeButton.setOnClickListener(v -> {
            if (!isClosingHandled.compareAndSet(false, true)) {
                LogUtils.d(TAG, "关闭事件已在处理中");
                return;
            }
            LogUtils.d(TAG, "用户点击关闭按钮");
            stopCurrent(true);
            closeButton.setEnabled(false);

            if (contentLayout != null) {
                contentLayout.removeAllViews();
            }
            if (contentContainer != null) {
                contentContainer.setVisibility(View.GONE);
            }
            handler.removeCallbacks(contentBringToFrontRunnable);
            contentAdded = false;
            showStaticImage();

            handler.postDelayed(() -> {
                if (closeButton != null) {
                    closeButton.setEnabled(true);
                }
                isClosingHandled.set(false);
            }, 500);
        });
    }

    private void goToDebugPage() {
        releaseMediaPlayer();
        handler.removeCallbacksAndMessages(null);
        clickHandler.removeCallbacksAndMessages(null);
        Intent intent = new Intent(this, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT);
        startActivity(intent);
        finish();
    }

    private void playVideo(int videoResId) {
        isUserClosed = false;
        isVideoPrepared = false;
        firstFrameRendered = false;

        // 【关键修复】如果MediaPlayer已存在且已暂停，直接恢复播放
        if (mediaPlayer != null) {
            try {
                mediaPlayer.start();
                LogUtils.d(TAG, "视频已恢复播放（从暂停状态）");
                
                // 隐藏静态图片，显示视频
                staticImageView.setVisibility(View.GONE);
                textureView.setVisibility(View.VISIBLE);
                closeButton.setVisibility(View.GONE);
                contentContainer.setVisibility(View.GONE);
                contentLayout.setVisibility(View.GONE);
                
                isVideoPrepared = true;
                firstFrameRendered = true;
                return; // 直接返回，不需要重新创建
            } catch (Exception e) {
                LogUtils.w(TAG, "恢复播放失败，将重新创建: " + e.getMessage());
                // 如果恢复失败，释放并重新创建
                releaseMediaPlayer();
            }
        }

        // 确保静态图片可见，遮盖 TextureView
        staticImageView.setVisibility(View.VISIBLE);
        textureView.setVisibility(View.VISIBLE);
        staticImageView.bringToFront();

        closeButton.setVisibility(View.GONE);
        contentContainer.setVisibility(View.GONE);
        contentLayout.setVisibility(View.GONE);

        // 如果 SurfaceTexture 尚未准备好，保存视频资源 ID，等待 onSurfaceTextureAvailable
        if (pendingSurfaceTexture == null) {
            pendingVideoResId = videoResId;
            LogUtils.d(TAG, "等待 SurfaceTexture 准备，视频ID=" + videoResId);
        } else {
            startMediaPlayer(videoResId, pendingSurfaceTexture);
        }
    }

    // ================= TextureView 回调 =================
    @Override
    public void onSurfaceTextureAvailable(SurfaceTexture surfaceTexture, int width, int height) {
        LogUtils.d(TAG, "onSurfaceTextureAvailable, width=" + width + ", height=" + height);
        pendingSurfaceTexture = surfaceTexture;
        if (pendingVideoResId != -1) {
            startMediaPlayer(pendingVideoResId, surfaceTexture);
            pendingVideoResId = -1;
        }
    }

    @Override
    public void onSurfaceTextureSizeChanged(SurfaceTexture surfaceTexture, int width, int height) {
        LogUtils.d(TAG, "onSurfaceTextureSizeChanged");
    }

    @Override
    public boolean onSurfaceTextureDestroyed(SurfaceTexture surfaceTexture) {
        LogUtils.d(TAG, "onSurfaceTextureDestroyed");
        if (pendingSurface != null) {
            pendingSurface.release();
            pendingSurface = null;
        }
        pendingSurfaceTexture = null;
        return true;
    }

    @Override
    public void onSurfaceTextureUpdated(SurfaceTexture surfaceTexture) {
        // 首帧渲染时隐藏静态图片，避免黑屏
        if (!firstFrameRendered && mediaPlayer != null && mediaPlayer.isPlaying()) {
            firstFrameRendered = true;
            LogUtils.d(TAG, "首帧渲染完成，隐藏静态图片");
            staticImageView.setVisibility(View.GONE);
            if (contentContainer != null && contentContainer.getVisibility() == View.VISIBLE) {
                contentContainer.bringToFront();
            }
        }
    }

    // ================= MediaPlayer 控制 =================
    private void startMediaPlayer(int videoResId, SurfaceTexture surfaceTexture) {
        try {
            releaseMediaPlayer(); // 确保旧的已释放
            
            // 【关键修复】先创建新的 MediaPlayer 实例
            mediaPlayer = new MediaPlayer();
            
            // 【关键】将视频音量设为0，实现静音播放，不干扰音乐
            mediaPlayer.setVolume(0.0f, 0.0f);

            Uri uri = Uri.parse("android.resource://" + getPackageName() + "/" + videoResId);
            mediaPlayer.setDataSource(this, uri);

            Surface surface = new Surface(surfaceTexture);
            pendingSurface = surface;
            mediaPlayer.setSurface(surface);

            mediaPlayer.setLooping(isLoopMode);
            mediaPlayer.setScreenOnWhilePlaying(true);

            // 监听准备完成
            mediaPlayer.setOnPreparedListener(mp -> {
                LogUtils.d(TAG, "MediaPlayer 准备完成，开始播放");
                isVideoPrepared = true;
                mp.start();
                // 注意：首帧渲染由 onSurfaceTextureUpdated 触发，此处不隐藏静态图片
            });

            // 监听播放完成
            mediaPlayer.setOnCompletionListener(mp -> {
                LogUtils.d(TAG, "视频播放完成");
                if (!isLoopMode) {
                    showStaticImage();
                } else {
                    if (contentContainer != null && contentContainer.getVisibility() == View.VISIBLE) {
                        contentContainer.bringToFront();
                    }
                }
            });

            // 监听视频渲染开始（备用方案，某些设备可能不触发 onSurfaceTextureUpdated）
            mediaPlayer.setOnInfoListener((mp, what, extra) -> {
                if (what == MediaPlayer.MEDIA_INFO_VIDEO_RENDERING_START) {
                    LogUtils.d(TAG, "MEDIA_INFO_VIDEO_RENDERING_START 触发");
                    if (!firstFrameRendered) {
                        firstFrameRendered = true;
                        staticImageView.setVisibility(View.GONE);
                    }
                    return true;
                }
                return false;
            });

            // 错误处理
            mediaPlayer.setOnErrorListener((mp, what, extra) -> {
                LogUtils.e(TAG, "视频播放错误: what=" + what + ", extra=" + extra);
                showStaticImage();
                return true;
            });

            mediaPlayer.prepareAsync();

        } catch (IOException e) {
            LogUtils.e(TAG, "startMediaPlayer 异常: " + e.getMessage());
            showStaticImage();
        }
    }

    private void releaseMediaPlayer() {
        if (mediaPlayer != null) {
            try {
                if (mediaPlayer.isPlaying()) {
                    mediaPlayer.stop();
                }
                mediaPlayer.release();
            } catch (Exception e) {
                LogUtils.e(TAG, "释放 MediaPlayer 异常: " + e.getMessage());
            }
            mediaPlayer = null;
        }
        if (pendingSurface != null) {
            pendingSurface.release();
            pendingSurface = null;
        }
        isVideoPrepared = false;
        firstFrameRendered = false;
    }

    private void showStaticImage() {
        LogUtils.d(TAG, "showStaticImage, contentAdded=" + contentAdded);
        releaseMediaPlayer();
        if (textureView != null) {
            textureView.setVisibility(View.GONE);
        }
        if (closeButton != null) {
            closeButton.setVisibility(View.VISIBLE);
        }
        staticImageView.setVisibility(View.VISIBLE);
        contentContainer.setVisibility(View.VISIBLE);
        contentLayout.setVisibility(View.VISIBLE);
    }

    // ================= 外部内容显示接口 =================
    public static void showContentOnVideo(String action, String dataJson) {
        if (currentInstance != null && !currentInstance.isFinishing()) {
            currentInstance.runOnUiThread(() -> {
                LogUtils.d(TAG, "showContentOnVideo: action=" + action);
                currentInstance.displayContent(action, dataJson);
            });
        } else {
            LogUtils.w(TAG, "showContentOnVideo: currentInstance is null");
        }
    }

    public static void hideContent() {
        if (currentInstance != null && !currentInstance.isFinishing()) {
            currentInstance.runOnUiThread(() -> {
                if (currentInstance.contentLayout != null) {
                    currentInstance.contentLayout.removeAllViews();
                }
                if (currentInstance.contentContainer != null) {
                    currentInstance.contentContainer.setVisibility(View.GONE);
                }
                currentInstance.handler.removeCallbacks(currentInstance.contentBringToFrontRunnable);
                currentInstance.contentAdded = false;
            });
        }
    }

    private void displayContent(String action, String dataJson) {
        if (dataJson == null || dataJson.isEmpty()) {
            LogUtils.w(TAG, "displayContent: dataJson is null or empty");
            return;
        }
        try {
            JSONObject data = new JSONObject(dataJson);
            contentLayout.removeAllViews();
            if (action == null) {
                displayError("缺少 action 参数");
                return;
            }
            switch (action) {
                case "list":
                    displayList(data);
                    break;
                case "card":
                    displayCard(data);
                    break;
                case "text":
                    displayText(data);
                    break;
                case "clear":
                    clearScreen();
                    break;
                default:
                    displayError("不支持的展示类型: " + action);
            }
            contentAdded = true;
            contentContainer.setVisibility(View.VISIBLE);
            contentContainer.bringToFront();
            handler.removeCallbacks(contentBringToFrontRunnable);
            handler.postDelayed(contentBringToFrontRunnable, 500);
            LogUtils.d(TAG, "displayContent: success");
        } catch (JSONException e) {
            LogUtils.e(TAG, "displayContent: JSON error: " + e.getMessage());
            displayError("解析数据失败: " + e.getMessage());
            contentContainer.setVisibility(View.VISIBLE);
            contentContainer.bringToFront();
        }
    }

    private void displayList(JSONObject data) {
        String title = data.optString("title", "");
        JSONArray items = data.optJSONArray("items");
        if (!title.isEmpty()) {
            TextView titleView = new TextView(this);
            titleView.setText("资源列表");
            titleView.setTextSize(12);
            titleView.setTextColor(Color.parseColor("#6A1B9A"));
            titleView.setTypeface(null, Typeface.BOLD);
            titleView.setPadding(52, 40, 8, 0);
            contentLayout.addView(titleView);
        }
        if (items == null || items.length() == 0) {
            TextView emptyView = new TextView(this);
            emptyView.setText("暂无数据");
            emptyView.setTextSize(12);
            emptyView.setTextColor(Color.parseColor("#6A1B9A"));
            emptyView.setPadding(10, 15, 10, 15);
            emptyView.setGravity(android.view.Gravity.CENTER);
            contentLayout.addView(emptyView);
            return;
        }
        LinearLayout tableLayout = new LinearLayout(this);
        tableLayout.setOrientation(LinearLayout.VERTICAL);
        tableLayout.setBackgroundColor(Color.TRANSPARENT);
        tableLayout.setPadding(4, 4, 4, 4);
        TableRow headerRow = new TableRow(this);
        headerRow.setBackgroundColor(Color.TRANSPARENT);
        headerRow.setPadding(6, 6, 6, 6);
        /*String[] headers = {"名称", "状态", "详情"};
        for (String header : headers) {
            TextView headerView = new TextView(this);
            headerView.setText(header);
            headerView.setTextColor(Color.parseColor("#6A1B9A"));
            headerView.setTextSize(10);
            headerView.setTypeface(null, Typeface.BOLD);
            headerView.setPadding(6, 4, 6, 4);
            headerView.setGravity(android.view.Gravity.START | android.view.Gravity.CENTER_VERTICAL);
            TableRow.LayoutParams headerParams = new TableRow.LayoutParams();
            headerParams.width = 0;
            headerParams.weight = 1;
            headerView.setLayoutParams(headerParams);
            headerRow.addView(headerView);
        }*/
        tableLayout.addView(headerRow);

        // 状态固定宽度
        int statusWidthPx = (int) TypedValue.applyDimension(
                TypedValue.COMPLEX_UNIT_DIP, 13, getResources().getDisplayMetrics());

        for (int i = 0; i < items.length(); i++) {
            try {
                JSONObject item = items.getJSONObject(i);
                TableRow dataRow = new TableRow(this);
                dataRow.setPadding(2, 6, 6, 6);
                dataRow.setBackgroundColor(Color.TRANSPARENT);

                String name = item.optString("name", "-");
                String status = item.optString("status", "-");

                // 创建状态 TextView，固定宽度
                TextView statusView = createCellTextView(status);
                TableRow.LayoutParams statusParams = new TableRow.LayoutParams(
                        statusWidthPx,
                        TableRow.LayoutParams.WRAP_CONTENT);
                statusView.setLayoutParams(statusParams);
                if ("播放中".equals(status)) {
                    statusView.setTextColor(Color.parseColor("#7B1FA2"));
                    statusView.setTypeface(null, Typeface.BOLD);
                } else if ("待命".equals(status)) {
                    statusView.setTextColor(Color.parseColor("#9C27B0"));
                }

                // 创建名称 TextView，权重1，宽度0，占据剩余宽度
                TextView nameView = createCellTextView(name);
                TableRow.LayoutParams nameParams = new TableRow.LayoutParams(
                        TableRow.LayoutParams.WRAP_CONTENT,
                        TableRow.LayoutParams.WRAP_CONTENT, 1.0f);
                nameView.setLayoutParams(nameParams);

                // 顺序：状态在左，名称在右
                dataRow.addView(statusView);
                dataRow.addView(nameView);

                tableLayout.addView(dataRow);
            } catch (JSONException e) {
                e.printStackTrace();
            }
        }
        contentLayout.addView(tableLayout);
    }

    private void displayCard(JSONObject data) {
        String title = data.optString("title", "信息卡片");
        String content = data.optString("content", "");
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackgroundColor(Color.TRANSPARENT);
        card.setPadding(12, 15, 12, 15);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, 4, 0, 4);
        card.setLayoutParams(params);
        TextView titleView = new TextView(this);
        titleView.setText(title);
        titleView.setTextSize(12);
        titleView.setTextColor(Color.parseColor("#6A1B9A"));
        titleView.setTypeface(null, Typeface.BOLD);
        titleView.setPadding(0, 0, 0, 8);
        TextView contentView = new TextView(this);
        contentView.setText(content.isEmpty() ? "暂无内容" : content);
        contentView.setTextSize(10);
        contentView.setTextColor(Color.parseColor("#4A148C"));
        contentView.setLineSpacing(1.2f, 1.0f);
        card.addView(titleView);
        card.addView(contentView);
        contentLayout.addView(card);
    }

    private void displayText(JSONObject data) {
        String text = data.optString("text", "");
        TextView textView = new TextView(this);
        textView.setText(text.isEmpty() ? "暂无文字内容" : text);
        textView.setTextSize(12);
        textView.setTextColor(Color.parseColor("#6A1B9A"));
        textView.setTypeface(null, Typeface.BOLD);
        textView.setPadding(10, 15, 10, 15);
        textView.setLineSpacing(1.2f, 1.0f);
        contentLayout.addView(textView);
    }

    private void clearScreen() {
        contentLayout.removeAllViews();
        TextView clearedView = new TextView(this);
        clearedView.setText("屏幕已清空");
        clearedView.setTextSize(12);
        clearedView.setTextColor(Color.parseColor("#6A1B9A"));
        clearedView.setGravity(android.view.Gravity.CENTER);
        clearedView.setPadding(10, 30, 10, 30);
        contentLayout.addView(clearedView);
    }

    private void displayError(String error) {
        contentLayout.removeAllViews();
        TextView errorView = new TextView(this);
        errorView.setText("显示错误: " + error);
        errorView.setTextSize(12);
        errorView.setTextColor(Color.parseColor("#D32F2F"));
        errorView.setPadding(10, 15, 10, 15);
        errorView.setGravity(android.view.Gravity.CENTER);
        contentLayout.addView(errorView);
    }

    private TextView createCellTextView(String text) {
        TextView textView = new TextView(this);
        textView.setText(text);
        textView.setTextSize(10);
        textView.setTextColor(Color.parseColor("#6A1B9A"));
        textView.setPadding(6, 4, 6, 4);
        textView.setGravity(android.view.Gravity.START | android.view.Gravity.CENTER_VERTICAL);
        return textView;
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (currentInstance == this) {
            currentInstance = null;
        }
        handler.removeCallbacksAndMessages(null);
        clickHandler.removeCallbacksAndMessages(null);
        releaseMediaPlayer();
        if (contentLayout != null) {
            contentLayout.removeAllViews();
            contentLayout = null;
        }
        contentContainer = null;
        ActivityManager.getInstance().removeActivity(this);
    }
}