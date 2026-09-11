package com.zongheng.exhibitionhallrobot;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.hardware.Camera;
import android.media.AudioManager;
import android.media.MediaPlayer;
import android.net.Uri;
import android.os.Bundle;
import android.os.CountDownTimer;
import android.os.Environment;
import android.os.Handler;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.widget.ImageView;
import android.widget.TextView;

import androidx.annotation.NonNull;

import com.google.zxing.BarcodeFormat;
import com.google.zxing.WriterException;
import com.google.zxing.common.BitMatrix;
import com.google.zxing.qrcode.QRCodeWriter;
import com.zongheng.exhibitionhallrobot.service.RobotCapabilities;
import com.zongheng.exhibitionhallrobot.util.LogUtils;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * 自定义拍照 Activity
 *
 * 全功能拍照流程 (v2 — 优化版):
 * 1. 摄像头预览
 * 2. 准备画面（留出时间给用户站好位置）
 * 3. 3-2-1 倒计时动画
 * 4. Android 原生快门音 (MediaActionSound) + 自动拍照
 * 5. 照片保存 + 二维码合成（右下角）
 * 6. 结果预览 → 保持展示（不自动关闭），同时回调 notifyPhotoSuccess
 * 7. 中枢端 TTS "需要重拍吗？"
 * 8. 用户说 "重拍" → 中枢发送 cmd_take_photo?skipNav=true → TcpService 调 retakePhoto()
 * 9. 用户说 "不用了" → 中枢发送 cmd_close_photo → TcpService 调 closePhotoResult()
 *
 * 替代 OrionStar SDK 中无法正常工作的 PhotoCaptureActivity。
 */
public class CustomPhotoCaptureActivity extends Activity
        implements SurfaceHolder.Callback, Camera.PictureCallback {

    private static final String TAG = "CustomPhotoCapture";

    // Intent 参数键名（与 SDK PhotoCaptureActivity 保持一致）
    public static final String EXTRA_SESSION_ID = "session_id";
    public static final String EXTRA_PREPARE_SECONDS = "prepare_seconds";
    public static final String EXTRA_RESULT_HOLD_SECONDS = "result_hold_seconds";
    public static final String EXTRA_QR_CONTENT = "qr_content";

    // 默认参数
    private static final int DEFAULT_PREPARE_SEC = 3;
    private static final int CAMERA_PREVIEW_WIDTH = 1280;
    private static final int CAMERA_PREVIEW_HEIGHT = 720;

    // UI 组件
    private SurfaceView surfaceView;
    private SurfaceHolder surfaceHolder;
    private TextView tvCountdown;
    private TextView tvHint;
    private ImageView ivResult;

    // 相机
    private Camera camera;
    private int cameraId = 0;

    // 状态
    private boolean isPreviewing = false;
    private boolean isCapturing = false;
    private boolean isCountdownRunning = false;
    private byte[] capturedPhotoData;
    private String currentPhotoPath;
    private String currentOriginalPhotoPath;  // 原始照片路径（不含二维码，用于上传）

    // 配置
    private int prepareSeconds = DEFAULT_PREPARE_SEC;
    private String qrContent = "";
    private String sessionId = "";

    // 音频
    private MediaPlayer shutterPlayer;      // 快门音 (v3: MediaPlayer on STREAM_MUSIC, 更逼真)
    private MediaPlayer beepMediaPlayer;    // 倒计时哔哔声

    // Handler
    private Handler mainHandler = new Handler();

    // ── 静态引用: 供 TcpService 从外部控制 (重拍/关闭) ──
    private static CustomPhotoCaptureActivity currentInstance;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 全屏 + 无 ActionBar + 横屏
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        getWindow().setFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN,
                WindowManager.LayoutParams.FLAG_FULLSCREEN);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        setContentView(R.layout.activity_custom_photo_capture);

        // 读取传入参数
        Intent intent = getIntent();
        if (intent != null) {
            prepareSeconds = intent.getIntExtra(EXTRA_PREPARE_SECONDS, DEFAULT_PREPARE_SEC);
            qrContent = intent.getStringExtra(EXTRA_QR_CONTENT);
            sessionId = intent.getStringExtra(EXTRA_SESSION_ID);
            if (qrContent == null) qrContent = "";
            if (sessionId == null) sessionId = "";
        }
        if (prepareSeconds < 1) prepareSeconds = DEFAULT_PREPARE_SEC;

        LogUtils.d(TAG, "启动自定义拍照 (v2): session=" + sessionId +
                ", prepare=" + prepareSeconds + "s, qr=" + qrContent);

        // 保存静态实例
        currentInstance = this;

        initViews();
        initSounds();

        // 初始化 SurfaceView
        surfaceView = findViewById(R.id.camera_preview);
        surfaceHolder = surfaceView.getHolder();
        surfaceHolder.addCallback(this);
        surfaceHolder.setType(SurfaceHolder.SURFACE_TYPE_PUSH_BUFFERS);
    }

    private void initViews() {
        tvCountdown = findViewById(R.id.tv_countdown);
        tvHint = findViewById(R.id.tv_hint);
        ivResult = findViewById(R.id.iv_result_photo);
    }

    // ============================================================
    // 音效初始化 (v3: MediaPlayer + 合成 WAV, 更逼真的机械快门)
    // ============================================================
    private void initSounds() {
        try {
            // ── 快门音 (v3: MediaPlayer STREAM_MUSIC 最大音量) ──
            // 之前用 MediaActionSound 播放系统音效, 但在 OrionStar 机器人上
            // STREAM_SYSTEM 可能被静音导致无声。改用 MediaPlayer 播放合成 WAV
            // 可保证音量 + 更真实的机械快门效果（两段式: 点击 + 机械回弹）。
            File sf = new File(getCacheDir(), "shutter_realistic.wav");
            if (!sf.exists() || sf.length() < 100) {
                byte[] wavData = generateWav(44100, _shutterPcm());
                FileOutputStream fos = new FileOutputStream(sf);
                fos.write(wavData);
                fos.close();
            }
            shutterPlayer = new MediaPlayer();
            shutterPlayer.setAudioStreamType(AudioManager.STREAM_MUSIC);
            shutterPlayer.setDataSource(sf.getAbsolutePath());
            shutterPlayer.setVolume(1.0f, 1.0f);
            shutterPlayer.prepare();
            LogUtils.d(TAG, "快门音已加载 (MediaPlayer STREAM_MUSIC)");

            // 倒计时哔声 — 保留原有合成 WAV (简单短促)
            File bf = new File(getCacheDir(), "count_beep.wav");
            if (!bf.exists() || bf.length() < 100) {
                byte[] wavData = generateWav(44100, _beepPcm());
                FileOutputStream fos = new FileOutputStream(bf);
                fos.write(wavData);
                fos.close();
            }
            beepMediaPlayer = new MediaPlayer();
            beepMediaPlayer.setAudioStreamType(AudioManager.STREAM_MUSIC);
            beepMediaPlayer.setDataSource(bf.getAbsolutePath());
            beepMediaPlayer.prepare();

            LogUtils.d(TAG, "音效已准备 (shutter=MediaPlayer WAV, beep=WAV)");
        } catch (Exception e) {
            LogUtils.w(TAG, "音效准备失败: " + e.getMessage());
        }
    }

    /**
     * 生成更逼真的机械快门 PCM 数据。
     *
     * 两段式设计:
     *   Phase 1 (0-30ms):  高频冲击 — "咔嚓" 的起始脉冲
     *   Phase 2 (30-80ms):  中频机械回弹 — 镜箱/反光板回弹
     *   Phase 3 (80-200ms): 低频衰减 — 快门帘关闭的余韵
     * 采样率 44100Hz, 16-bit 有符号 PCM, mono.
     */
    private short[] _shutterPcm() {
        int sr = 44100;
        int len = (int) (sr * 0.20);   // 200ms
        short[] pcm = new short[len];
        for (int i = 0; i < len; i++) {
            double t = (double) i / sr;
            double sample = 0.0;

            if (t < 0.030) {
                // Phase 1: 高频冲击 1200-1800Hz, 快速衰减
                double env = Math.exp(-t * 80);
                sample += env * Math.sin(t * 1500 * Math.PI * 2) * 0.7;
                // 叠加一次快速咔嗒
                sample += env * Math.sin(t * 3200 * Math.PI * 2) * 0.3;
            } else if (t < 0.080) {
                // Phase 2: 机械回弹, 600-900Hz, 慢衰减
                double dt = t - 0.030;
                double env = Math.exp(-dt * 25);
                sample += env * Math.sin(t * 750 * Math.PI * 2) * 0.5;
                // 叠加一些噪感
                sample += env * (Math.random() - 0.5) * 0.15;
            } else {
                // Phase 3: 低频衰减余韵
                double dt = t - 0.080;
                double env = Math.exp(-dt * 10);
                sample += env * Math.sin(t * 200 * Math.PI * 2) * 0.3;
                sample += env * (Math.random() - 0.5) * 0.05;
            }

            // 归一化到 short 范围
            sample = Math.max(-1.0, Math.min(1.0, sample));
            pcm[i] = (short) (sample * Short.MAX_VALUE * 0.9);
        }
        return pcm;
    }

    // 倒计时短哔声 (保留原有)
    private short[] _beepPcm() {
        int sr = 44100;
        int len = (int) (sr * 0.08);  // 80ms
        short[] pcm = new short[len];
        for (int i = 0; i < len; i++) {
            double t = (double) i / sr;
            double env = Math.exp(-t * 30);
            pcm[i] = (short) (env * Math.sin(t * 800 * Math.PI * 2) * Short.MAX_VALUE * 0.5);
        }
        return pcm;
    }

    // WAV 编码器
    private byte[] generateWav(int sr, short[] pcm) {
        int ns = pcm.length;
        int ds = ns * 2;
        byte[] w = new byte[ds + 44];
        System.arraycopy("RIFF".getBytes(), 0, w, 0, 4);
        writeInt32(w, 4, ds + 36);
        System.arraycopy("WAVE".getBytes(), 0, w, 8, 4);
        System.arraycopy("fmt ".getBytes(), 0, w, 12, 4);
        writeInt32(w, 16, 16); writeInt16(w, 20, 1); writeInt16(w, 22, 1);
        writeInt32(w, 24, sr); writeInt32(w, 28, sr * 2);
        writeInt16(w, 32, 2); writeInt16(w, 34, 16);
        System.arraycopy("data".getBytes(), 0, w, 36, 4);
        writeInt32(w, 40, ds);
        for (int i = 0; i < ns; i++) {
            w[44 + i * 2] = (byte) (pcm[i] & 0xFF);
            w[44 + i * 2 + 1] = (byte) ((pcm[i] >> 8) & 0xFF);
        }
        return w;
    }

    private void writeInt32(byte[] arr, int offset, int value) {
        arr[offset] = (byte) (value & 0xFF);
        arr[offset + 1] = (byte) ((value >> 8) & 0xFF);
        arr[offset + 2] = (byte) ((value >> 16) & 0xFF);
        arr[offset + 3] = (byte) ((value >> 24) & 0xFF);
    }

    private void writeInt16(byte[] arr, int offset, int value) {
        arr[offset] = (byte) (value & 0xFF);
        arr[offset + 1] = (byte) ((value >> 8) & 0xFF);
    }

    private void playShutterSound() {
        if (shutterPlayer != null) {
            try {
                shutterPlayer.seekTo(0);
                shutterPlayer.start();
                LogUtils.d(TAG, "快门音已播放 (MediaPlayer WAV)");
            } catch (Exception e) {
                LogUtils.w(TAG, "快门音播放失败: " + e.getMessage());
            }
        }
    }

    // ============================================================
    // SurfaceHolder.Callback
    // ============================================================

    @Override
    public void surfaceCreated(@NonNull SurfaceHolder holder) {
        LogUtils.d(TAG, "Surface 创建");
        openCamera();
    }

    @Override
    public void surfaceChanged(@NonNull SurfaceHolder holder, int format, int width, int height) {
        LogUtils.d(TAG, "Surface 变化: " + width + "x" + height);
        if (surfaceHolder.getSurface() == null) return;
        stopPreview();
        startPreview();
    }

    @Override
    public void surfaceDestroyed(@NonNull SurfaceHolder holder) {
        LogUtils.d(TAG, "Surface 销毁");
        stopPreview();
        releaseCamera();
    }

    // ============================================================
    // 相机操作
    // ============================================================

    private void openCamera() {
        try {
            Camera.CameraInfo info = new Camera.CameraInfo();
            for (int i = 0; i < Camera.getNumberOfCameras(); i++) {
                Camera.getCameraInfo(i, info);
                if (info.facing == Camera.CameraInfo.CAMERA_FACING_BACK) {
                    cameraId = i;
                    break;
                }
            }
            camera = Camera.open(cameraId);
            LogUtils.d(TAG, "摄像头已打开: cameraId=" + cameraId);

            Camera.Parameters params = camera.getParameters();

            // 设置预览尺寸
            Camera.Size previewSize = findBestPreviewSize(params, CAMERA_PREVIEW_WIDTH, CAMERA_PREVIEW_HEIGHT);
            if (previewSize != null) {
                params.setPreviewSize(previewSize.width, previewSize.height);
                LogUtils.d(TAG, "预览尺寸: " + previewSize.width + "x" + previewSize.height);
            }
            // 设置照片尺寸
            Camera.Size pictureSize = findBestPictureSize(params, 1920, 1080);
            if (pictureSize != null) {
                params.setPictureSize(pictureSize.width, pictureSize.height);
                LogUtils.d(TAG, "照片尺寸: " + pictureSize.width + "x" + pictureSize.height);
            }
            params.setFocusMode(Camera.Parameters.FOCUS_MODE_AUTO);
            params.setJpegQuality(90);
            camera.setParameters(params);

            // 重拍时: 重置相机朝向 (回正)
            camera.setDisplayOrientation(0);

            startPreview();

        } catch (Exception e) {
            LogUtils.e(TAG, "打开摄像头失败", e);
            setResult(RESULT_CANCELED);
            finish();
        }
    }

    private void startPreview() {
        if (camera == null || surfaceHolder.getSurface() == null) return;
        try {
            camera.setPreviewDisplay(surfaceHolder);
            camera.startPreview();
            isPreviewing = true;
            LogUtils.d(TAG, "预览已启动");

            // 预览启动后，开始倒计时流程
            if (!isCountdownRunning && !isCapturing) {
                startPhotoSequence();
            }
        } catch (IOException e) {
            LogUtils.e(TAG, "启动预览失败", e);
        }
    }

    private void stopPreview() {
        if (camera != null && isPreviewing) {
            camera.stopPreview();
            isPreviewing = false;
            LogUtils.d(TAG, "预览已停止");
        }
    }

    private void releaseCamera() {
        if (camera != null) {
            stopPreview();
            camera.release();
            camera = null;
            LogUtils.d(TAG, "摄像头已释放");
        }
    }

    private Camera.Size findBestPreviewSize(Camera.Parameters params, int targetW, int targetH) {
        Camera.Size best = null;
        int bestScore = Integer.MAX_VALUE;
        for (Camera.Size size : params.getSupportedPreviewSizes()) {
            int diff = Math.abs(size.width - targetW) + Math.abs(size.height - targetH);
            if (diff < bestScore) {
                bestScore = diff;
                best = size;
            }
        }
        return best;
    }

    private Camera.Size findBestPictureSize(Camera.Parameters params, int targetW, int targetH) {
        Camera.Size best = null;
        int bestScore = Integer.MAX_VALUE;
        for (Camera.Size size : params.getSupportedPictureSizes()) {
            int diff = Math.abs(size.width - targetW) + Math.abs(size.height - targetH);
            if (diff < bestScore) {
                bestScore = diff;
                best = size;
            }
        }
        return best;
    }

    // ============================================================
    // 拍照流程
    // ============================================================

    private void startPhotoSequence() {
        isCountdownRunning = true;
        isCapturing = false;

        // 切换回相机预览界面 (如果当前显示的是结果图)
        runOnUiThread(() -> {
            surfaceView.setVisibility(View.VISIBLE);
            ivResult.setVisibility(View.GONE);
            tvCountdown.setVisibility(View.GONE);
        });

        // 阶段1: 准备提示
        showHint("请站到拍照区域，面向屏幕");

        mainHandler.postDelayed(() -> {
            // 阶段2: 开始倒计时
            showHint("准备拍照");
            startCountdown();
        }, prepareSeconds * 1000L);
    }

    private void startCountdown() {
        runOnUiThread(() -> {
            tvHint.setVisibility(View.GONE);
            tvCountdown.setVisibility(View.VISIBLE);
        });

        new CountDownTimer(4000, 1000) {
            @Override
            public void onTick(long millisUntilFinished) {
                int secondsLeft = (int) (millisUntilFinished / 1000);
                if (secondsLeft >= 1 && secondsLeft <= 3) {
                    tvCountdown.setText(String.valueOf(secondsLeft));
                    tvCountdown.setAlpha(1.0f);
                    tvCountdown.animate().alpha(0.3f).setDuration(800).start();
                    playBeep();
                }
            }

            @Override
            public void onFinish() {
                tvCountdown.setVisibility(View.GONE);
                playShutterSound();
                takePhoto();
            }
        }.start();
    }

    private void playBeep() {
        if (beepMediaPlayer != null) {
            try { beepMediaPlayer.seekTo(0); beepMediaPlayer.start(); } catch (Exception ignored) {}
        }
    }

    private void takePhoto() {
        isCountdownRunning = false;
        isCapturing = true;

        if (camera == null) {
            LogUtils.e(TAG, "摄像头为空，无法拍照");
            handlePhotoFailed("摄像头未就绪");
            return;
        }

        try {
            camera.takePicture(null, null, this);
        } catch (Exception e) {
            LogUtils.e(TAG, "拍照失败", e);
            handlePhotoFailed("拍照失败: " + e.getMessage());
        }
    }

    @Override
    public void onPictureTaken(byte[] data, Camera cam) {
        LogUtils.d(TAG, "照片已拍摄，数据大小: " + (data != null ? data.length : 0) + " bytes");

        if (data == null || data.length == 0) {
            handlePhotoFailed("照片数据为空");
            return;
        }

        isCapturing = false;
        isPreviewing = false;
        capturedPhotoData = data;

        // [v3] 后台线程处理照片（解码、合成、保存），避免阻塞主线程导致倒计时后卡顿
        new Thread(() -> processPhotoOnBg(data), "photo-process").start();
    }

    /**
     * [v3] 后台线程处理照片 — 解码 → 合成二维码 → 保存到文件 → 切回主线程通知UI和TcpService。
     * 避免 decodeBitmap / generateQRCode / compress 等耗时操作阻塞主线程带来的卡顿。
     */
    private void processPhotoOnBg(byte[] photoData) {
        try {
            Bitmap originalBitmap = BitmapFactory.decodeByteArray(photoData, 0, photoData.length);
            if (originalBitmap == null) {
                mainHandler.post(() -> handlePhotoFailed("照片解码失败"));
                return;
            }

            // [v5] 先保存原始照片（不含二维码），用于上传到后端
            saveOriginalPhotoOnBg(originalBitmap);

            // 复制一份用于合成二维码（屏幕展示用）
            Bitmap photoWithQr = originalBitmap.copy(Bitmap.Config.ARGB_8888, true);
            originalBitmap.recycle();

            // 合成二维码到右下角（后台线程）
            if (qrContent != null && !qrContent.isEmpty()) {
                Bitmap qrBitmap = generateQRCode(qrContent, 300, 300);
                if (qrBitmap != null) {
                    Canvas canvas = new Canvas(photoWithQr);
                    int margin = 20;
                    int qrX = photoWithQr.getWidth() - qrBitmap.getWidth() - margin;
                    int qrY = photoWithQr.getHeight() - qrBitmap.getHeight() - margin;
                    canvas.drawBitmap(qrBitmap, qrX, qrY, null);
                    qrBitmap.recycle();
                    LogUtils.d(TAG, "二维码已合成到照片右下角");
                }
            }

            // 保存含二维码的展示版照片（屏幕预览用）
            savePhotoOnBg(photoWithQr);

        } catch (Exception e) {
            LogUtils.e(TAG, "照片处理失败", e);
            mainHandler.post(() -> handlePhotoFailed("照片处理失败: " + e.getMessage()));
        }
    }

    /**
     * [v5] 保存原始照片（不含二维码），用于上传到后端。
     * 在 processPhotoOnBg 中最先调用，确保上传的是干净的原图。
     */
    private String saveOriginalPhotoOnBg(Bitmap bitmap) {
        try {
            File photoDir = new File(getExternalFilesDir(Environment.DIRECTORY_PICTURES), "ExhibitionPhotos");
            if (!photoDir.exists()) {
                photoDir.mkdirs();
            }

            String timestamp = new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.getDefault()).format(new Date());
            String fileName = "photo_original_" + timestamp + ".jpg";
            File photoFile = new File(photoDir, fileName);

            FileOutputStream fos = new FileOutputStream(photoFile);
            bitmap.compress(Bitmap.CompressFormat.JPEG, 95, fos);
            fos.flush();
            fos.close();

            currentOriginalPhotoPath = photoFile.getAbsolutePath();
            LogUtils.d(TAG, "原始照片已保存（不含二维码）: " + currentOriginalPhotoPath);
            return currentOriginalPhotoPath;
        } catch (Exception e) {
            LogUtils.e(TAG, "保存原始照片失败", e);
            currentOriginalPhotoPath = null;
            return null;
        }
    }

    /**
     * [v3] 在后台线程保存照片 + 通知 TcpService，最后切回主线程显示预览。
     */
    private void savePhotoOnBg(Bitmap bitmap) {
        try {
            File photoDir = new File(getExternalFilesDir(Environment.DIRECTORY_PICTURES), "ExhibitionPhotos");
            if (!photoDir.exists()) {
                photoDir.mkdirs();
            }

            String timestamp = new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.getDefault()).format(new Date());
            String fileName = "photo_" + timestamp + ".jpg";
            File photoFile = new File(photoDir, fileName);

            FileOutputStream fos = new FileOutputStream(photoFile);
            bitmap.compress(Bitmap.CompressFormat.JPEG, 90, fos);
            fos.flush();
            fos.close();

            currentPhotoPath = photoFile.getAbsolutePath();
            LogUtils.d(TAG, "照片已保存: " + currentPhotoPath);

            bitmap.recycle();

            // [v4] 切回主线程显示预览，但将上传放到后台线程:
        //   showResultPreview() 需要主线程更新 UI，必须立即执行以免卡顿。
        //   notifyTcpServiceSuccess() 触发 HTTP 上传（含重试），
        //   如果放在 mainHandler 里会阻塞主线程，导致倒计时结束后画面卡住。
        //   使用局部变量捕获 sessionId/photoPath，避免重拍时 restartForRetake 的竞态条件。
            final String localSessionId = this.sessionId;
            final String localPhotoPath = this.currentPhotoPath;
            final String localOriginalPath = this.currentOriginalPhotoPath;
            mainHandler.post(() -> showResultPreview());
            new Thread(() -> {
                if (localSessionId != null && !localSessionId.isEmpty()) {
                    // [v5] 上传原始照片（不含二维码），而非展示版
                    String uploadPath = (localOriginalPath != null) ? localOriginalPath : localPhotoPath;
                    Uri photoUri = Uri.fromFile(new File(uploadPath));
                    RobotCapabilities.notifyPhotoSuccess(localSessionId, uploadPath, photoUri);
                }
            }, "photo-upload").start();

        } catch (Exception e) {
            LogUtils.e(TAG, "保存照片失败", e);
            mainHandler.post(() -> handlePhotoFailed("保存照片失败: " + e.getMessage()));
        }
    }

    private Bitmap generateQRCode(String content, int width, int height) {
        try {
            QRCodeWriter writer = new QRCodeWriter();
            BitMatrix bitMatrix = writer.encode(content, BarcodeFormat.QR_CODE, width, height);

            Bitmap qrBitmap = Bitmap.createBitmap(width, height, Bitmap.Config.RGB_565);
            for (int x = 0; x < width; x++) {
                for (int y = 0; y < height; y++) {
                    qrBitmap.setPixel(x, y, bitMatrix.get(x, y) ? Color.BLACK : Color.WHITE);
                }
            }
            return qrBitmap;
        } catch (WriterException e) {
            LogUtils.e(TAG, "生成二维码失败", e);
            return null;
        }
    }

    // savePhoto 已迁移到 savePhotoOnBg（后台线程执行）

    /**
     * [v2] 保存完照片后立即通过 sessionId 通知 TcpService, 不等待页面关闭。
     * 这样中枢端可以提前收到 evt_photo_done 并发 TTS 询问是否重拍。
     * 重拍时 TcpService 会注册新 sessionId 的回调, 回正确 seq。
     */
    private void notifyTcpServiceSuccess() {
        if (sessionId != null && !sessionId.isEmpty()) {
            Uri photoUri = Uri.fromFile(new File(currentPhotoPath));
            RobotCapabilities.notifyPhotoSuccess(sessionId, currentPhotoPath, photoUri);
            LogUtils.d(TAG, "已通过 RobotCapabilities 通知 TcpService: sessionId=" + sessionId);
        }
    }

    /**
     * [v2] 显示照片结果 — 保持展示, 不自动关闭。
     * 由中枢端 cmd_close_photo 决定何时关闭。
     */
    private void showResultPreview() {
        runOnUiThread(() -> {
            surfaceView.setVisibility(View.GONE);
            tvCountdown.setVisibility(View.GONE);
            tvHint.setVisibility(View.GONE);

            Bitmap resultBitmap = BitmapFactory.decodeFile(currentPhotoPath);
            if (resultBitmap != null) {
                ivResult.setImageBitmap(resultBitmap);
                ivResult.setVisibility(View.VISIBLE);
                LogUtils.d(TAG, "照片结果预览已展示 (等待关闭指令)");
            }
        });
    }

    private void handlePhotoFailed(String reason) {
        LogUtils.e(TAG, "拍照失败: " + reason);
        if (sessionId != null && !sessionId.isEmpty()) {
            RobotCapabilities.notifyPhotoFailed(sessionId, reason);
        }
        showHint("拍照失败: " + reason);
        mainHandler.postDelayed(() -> {
            finishWithResult();
        }, 2000);
    }

    /**
     * [v2] 正常关闭: 清理资源并 finish Activity。
     * 由外部 (TcpService) 通过 closePhotoResult() 触发。
     */
    private void finishWithResult() {
        releaseCamera();
        if (shutterPlayer != null) {
            shutterPlayer.release();
            shutterPlayer = null;
        }
        if (beepMediaPlayer != null) {
            beepMediaPlayer.release();
            beepMediaPlayer = null;
        }
        currentInstance = null;
        finish();
    }

    private void showHint(String text) {
        runOnUiThread(() -> {
            tvHint.setText(text);
            tvHint.setVisibility(View.VISIBLE);
        });
    }

    // ============================================================
    // 外部控制接口 (静态, 供 TcpService 调用)
    // ============================================================

    /**
     * 关闭照片结果页面。
     * 中枢端收到用户说"不用了"后发送 cmd_close_photo → TcpService 调用此方法。
     */
    public static void closePhotoResult() {
        LogUtils.d(TAG, "静态 closePhotoResult() 被调用");
        if (currentInstance != null) {
            currentInstance.runOnUiThread(() -> currentInstance.finishWithResult());
        }
    }

    /**
     * 重拍 — 无需关闭 Activity, 在当前页面重新打开相机并启动拍照流程。
     * 中枢端收到用户说"重拍"后发送 cmd_take_photo?skipNav=true → TcpService 调用此方法。
     *
     * @param newQrContent 新二维码内容 (含新 photo_id)
     * @param newSessionId 新会话 ID
     */
    public static void retakePhoto(final String newQrContent, final String newSessionId) {
        LogUtils.d(TAG, "静态 retakePhoto() 被调用, newQrContent=" + newQrContent
                + " newSessionId=" + newSessionId);
        if (currentInstance != null) {
            currentInstance.runOnUiThread(() ->
                    currentInstance.restartForRetake(newQrContent, newSessionId));
        }
    }

    /**
     * [v2] 在同一个 Activity 内重新开始拍照:
     * 1. 释放当前相机
     * 2. 更新 QR content + sessionId
     * 3. 重新打开相机 → 触发 startPhotoSequence
     */
    private void restartForRetake(String newQrContent, String newSessionId) {
        // 更新二维码内容 (含新 photo_id)
        if (newQrContent != null && !newQrContent.isEmpty()) {
            this.qrContent = newQrContent;
        }

        // 更新 sessionId (TcpService 已为新 sessionId 注册了回调)
        if (newSessionId != null && !newSessionId.isEmpty()) {
            this.sessionId = newSessionId;
        }

        // 清理 UI 状态
        ivResult.setVisibility(View.GONE);
        surfaceView.setVisibility(View.VISIBLE);
        isCountdownRunning = false;
        isCapturing = false;
        capturedPhotoData = null;
        currentPhotoPath = null;
        currentOriginalPhotoPath = null;

        // 释放旧相机
        releaseCamera();

        // 重新打开相机 (openCamera → startPreview → startPhotoSequence)
        openCamera();
    }

    /**
     * 停止当前流程 (兼容旧接口).
     * 在 v2 中相当于强制关闭 Activity.
     */
    public static void stopCurrent() {
        LogUtils.d(TAG, "static stopCurrent()");
        closePhotoResult();
    }

    /**
     * 强制关闭 (兼容旧接口).
     */
    public static void forceFinish() {
        LogUtils.d(TAG, "static forceFinish()");
        closePhotoResult();
    }

    @Override
    protected void onPause() {
        super.onPause();
        releaseCamera();
        mainHandler.removeCallbacksAndMessages(null);
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        releaseCamera();
        if (shutterPlayer != null) {
            shutterPlayer.release();
            shutterPlayer = null;
        }
        if (beepMediaPlayer != null) {
            beepMediaPlayer.release();
            beepMediaPlayer = null;
        }
        mainHandler.removeCallbacksAndMessages(null);
        if (currentInstance == this) {
            currentInstance = null;
        }
    }
}
