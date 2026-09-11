# 展厅人脸迎宾 Android APK

独立于原有 ASR APK。该应用只使用 CameraX 抓取前置摄像头画面，将限流后的 JPEG 上传到中控 `POST /api/face/recognize`，不在安卓本地保存人员库或运行识别模型。

## 构建

```powershell
cd exhibition-hall_AsrApk
.\gradlew.bat :face-app:assembleDebug
```

产物：`face-app/build/outputs/apk/debug/face-app-debug.apk`。

安装后输入61电脑中控地址，例如 `http://192.168.16.61:3001`，以及欢迎语目标屏幕号。应用默认每500毫秒最多上传一张图；上一请求未完成时自动丢帧。
