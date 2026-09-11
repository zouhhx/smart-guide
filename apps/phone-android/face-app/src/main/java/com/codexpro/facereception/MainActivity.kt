package com.codexpro.facereception

import android.Manifest
import android.content.ContentValues
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Matrix
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.MediaStore
import android.provider.Settings
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import com.codexpro.facereception.databinding.ActivityMainBinding
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding
    private val analysisExecutor = Executors.newSingleThreadExecutor()
    private val uploadExecutor = Executors.newSingleThreadExecutor()
    private val requestInFlight = AtomicBoolean(false)
    private val captureInProgress = AtomicBoolean(false)
    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(12, TimeUnit.SECONDS)
        .writeTimeout(12, TimeUnit.SECONDS)
        .build()
    private var cameraProvider: ProcessCameraProvider? = null
    private var imageCapture: ImageCapture? = null
    private var lastUploadAt = 0L
    private var running = true

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) startCamera() else showStatus("需要摄像头权限才能识别人脸", true)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.serverUrlInput.setText(FaceAppConfig.serverUrl(this))
        binding.targetScreenInput.setText(FaceAppConfig.targetScreen(this))
        binding.saveButton.setOnClickListener { saveSettings() }
        binding.captureButton.setOnClickListener { capturePhoto() }
        binding.toggleButton.setOnClickListener {
            running = !running
            binding.toggleButton.text = if (running) "暂停识别" else "继续识别"
            showStatus(if (running) "正在等待人脸" else "识别已暂停")
        }

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            startCamera()
        } else {
            permissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    private fun saveSettings() {
        val serverUrl = binding.serverUrlInput.text?.toString()?.trim()?.trimEnd('/').orEmpty()
        if (!serverUrl.startsWith("http://") && !serverUrl.startsWith("https://")) {
            binding.serverUrlInput.error = "请输入 http:// 或 https:// 开头的地址"
            return
        }
        FaceAppConfig.save(this, serverUrl, binding.targetScreenInput.text?.toString().orEmpty())
        showStatus("设置已保存")
    }

    private fun startCamera() {
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            val provider = future.get()
            cameraProvider = provider
            val preview = Preview.Builder().build().also {
                it.surfaceProvider = binding.previewView.surfaceProvider
            }
            val analysis = ImageAnalysis.Builder()
                .setTargetResolution(android.util.Size(640, 480))
                .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build()
            analysis.setAnalyzer(analysisExecutor, ::analyzeFrame)
            val capture = ImageCapture.Builder()
                .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
                .setTargetRotation(binding.previewView.display.rotation)
                .build()
            imageCapture = capture

            val selector = if (provider.hasCamera(CameraSelector.DEFAULT_FRONT_CAMERA)) {
                CameraSelector.DEFAULT_FRONT_CAMERA
            } else {
                CameraSelector.DEFAULT_BACK_CAMERA
            }
            provider.unbindAll()
            provider.bindToLifecycle(this, selector, preview, analysis, capture)
            showStatus("摄像头已启动，正在等待人脸")
        }, ContextCompat.getMainExecutor(this))
    }

    private fun capturePhoto() {
        val capture = imageCapture ?: run {
            showStatus("摄像头尚未准备好", true)
            return
        }
        val fileName = "FaceReception_${SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(System.currentTimeMillis())}.jpg"
        val values = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, fileName)
            put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg")
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                put(MediaStore.Images.Media.RELATIVE_PATH, "Pictures/FaceReception")
            }
        }
        val options = ImageCapture.OutputFileOptions.Builder(
            contentResolver,
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
            values,
        ).build()

        binding.captureButton.isEnabled = false
        captureInProgress.set(true)
        showStatus("正在拍照取样…")
        capture.takePicture(
            options,
            ContextCompat.getMainExecutor(this),
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(result: ImageCapture.OutputFileResults) {
                    val savedUri = result.savedUri
                    if (savedUri == null) {
                        captureInProgress.set(false)
                        binding.captureButton.isEnabled = true
                        showStatus("照片已保存到相册，但无法上传到61图片库", true)
                        return
                    }
                    showStatus("照片已保存，正在上传到61图片库…")
                    uploadExecutor.execute { uploadCapture(savedUri, fileName) }
                }

                override fun onError(error: ImageCaptureException) {
                    captureInProgress.set(false)
                    binding.captureButton.isEnabled = true
                    showStatus("拍照保存失败：${error.message}", true)
                }
            },
        )
    }

    private fun uploadCapture(uri: Uri, fileName: String) {
        try {
            val jpeg = contentResolver.openInputStream(uri)?.use { it.readBytes() }
                ?: error("无法读取刚拍摄的照片")
            val deviceId = Settings.Secure.getString(contentResolver, Settings.Secure.ANDROID_ID)
                ?: "android-face-camera"
            val request = Request.Builder()
                .url("${FaceAppConfig.serverUrl(this)}/api/face/captures")
                .header("X-Device-Id", deviceId)
                .header("X-Camera-Id", "android-front-camera")
                .header("X-File-Name", fileName)
                .post(jpeg.toRequestBody("image/jpeg".toMediaType()))
                .build()
            httpClient.newCall(request).execute().use { response ->
                val text = response.body?.string().orEmpty()
                if (!response.isSuccessful) error("HTTP ${response.code}: $text")
                val capture = JSONObject(text).optJSONObject("capture")
                val faceCount = capture?.optInt("faceCount", 0) ?: 0
                runOnUiThread {
                    val resultText = if (faceCount > 0) "检测到${faceCount}张人脸" else "未检测到人脸"
                    showStatus("已上传到61图片库（$resultText）", faceCount == 0)
                }
            }
        } catch (error: Exception) {
            runOnUiThread { showStatus("照片已保存到相册，但上传61失败：${error.message}", true) }
        } finally {
            captureInProgress.set(false)
            runOnUiThread { binding.captureButton.isEnabled = true }
        }
    }

    private fun analyzeFrame(image: ImageProxy) {
        val now = System.currentTimeMillis()
        if (!running || captureInProgress.get() || now - lastUploadAt < UPLOAD_INTERVAL_MS || !requestInFlight.compareAndSet(false, true)) {
            image.close()
            return
        }
        lastUploadAt = now

        val jpeg = runCatching { imageToJpeg(image) }.getOrElse {
            image.close()
            requestInFlight.set(false)
            runOnUiThread { showStatus("图片处理失败：${it.message}", true) }
            return
        }
        image.close()
        uploadExecutor.execute { uploadImage(jpeg) }
    }

    private fun imageToJpeg(image: ImageProxy): ByteArray {
        val plane = image.planes[0]
        val buffer = plane.buffer
        buffer.rewind()
        val rowStride = plane.rowStride
        val pixelStride = plane.pixelStride
        val width = image.width
        val height = image.height
        val pixels = IntArray(width * height)
        for (y in 0 until height) {
            val rowStart = y * rowStride
            for (x in 0 until width) {
                val offset = rowStart + x * pixelStride
                val alpha = buffer.get(offset).toInt() and 0xff
                val red = buffer.get(offset + 1).toInt() and 0xff
                val green = buffer.get(offset + 2).toInt() and 0xff
                val blue = buffer.get(offset + 3).toInt() and 0xff
                pixels[y * width + x] = Color.argb(alpha, red, green, blue)
            }
        }

        var bitmap = Bitmap.createBitmap(pixels, width, height, Bitmap.Config.ARGB_8888)
        if (image.imageInfo.rotationDegrees != 0) {
            val matrix = Matrix().apply { postRotate(image.imageInfo.rotationDegrees.toFloat()) }
            val rotated = Bitmap.createBitmap(bitmap, 0, 0, width, height, matrix, true)
            if (rotated !== bitmap) bitmap.recycle()
            bitmap = rotated
        }
        val output = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, output)
        bitmap.recycle()
        return output.toByteArray()
    }

    private fun uploadImage(jpeg: ByteArray) {
        try {
            val serverUrl = FaceAppConfig.serverUrl(this)
            val deviceId = Settings.Secure.getString(contentResolver, Settings.Secure.ANDROID_ID)
                ?: "android-face-camera"
            val request = Request.Builder()
                .url("$serverUrl/api/face/recognize")
                .header("X-Device-Id", deviceId)
                .header("X-Camera-Id", "android-front-camera")
                .header("X-Target-Screen", FaceAppConfig.targetScreen(this))
                .post(jpeg.toRequestBody("image/jpeg".toMediaType()))
                .build()
            httpClient.newCall(request).execute().use { response ->
                val text = response.body?.string().orEmpty()
                if (!response.isSuccessful) error("HTTP ${response.code}: $text")
                val result = JSONObject(text)
                runOnUiThread { renderResult(result) }
            }
        } catch (error: Exception) {
            runOnUiThread { showStatus("识别服务连接失败：${error.message}", true) }
        } finally {
            requestInFlight.set(false)
        }
    }

    private fun renderResult(result: JSONObject) {
        val faces = result.optJSONArray("faces")
        val integration = result.optJSONObject("integration")
        val candidate = integration?.optJSONObject("candidate")
        when {
            candidate != null -> {
                val name = candidate.optString("name", "已识别人员")
                val score = candidate.optDouble("score", 0.0)
                val count = integration.optInt("confirmationCount", 0)
                val required = integration.optInt("requiredFrames", 3)
                val triggered = integration.optBoolean("triggered", false)
                val suffix = if (triggered) "，已触发展厅欢迎" else "，确认 $count/$required"
                showStatus("$name  相似度 ${"%.3f".format(score)}$suffix")
            }
            faces != null && faces.length() > 0 -> showStatus("检测到人脸，未匹配已录入人员")
            else -> showStatus("正在等待人脸")
        }
        binding.metricsText.text = "61识别耗时：${result.optDouble("durationMs", 0.0)} ms"
    }

    private fun showStatus(message: String, error: Boolean = false) {
        binding.statusText.text = message
        binding.statusText.setTextColor(
            ContextCompat.getColor(this, if (error) android.R.color.holo_red_light else android.R.color.white)
        )
    }

    override fun onDestroy() {
        cameraProvider?.unbindAll()
        analysisExecutor.shutdownNow()
        uploadExecutor.shutdownNow()
        httpClient.dispatcher.executorService.shutdown()
        httpClient.connectionPool.evictAll()
        super.onDestroy()
    }

    companion object {
        private const val UPLOAD_INTERVAL_MS = 500L
        private const val JPEG_QUALITY = 78
    }
}
