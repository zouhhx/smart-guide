# 机器人 Android 客户端

运行在豹小秘机器人上，包名 `com.zongheng.exhibitionhallrobot`。负责设备界面、字幕、语音播放/动画、SparkChain 识别、RobotOS 唤醒与机器人交互。

## 开发入口

主入口 `app/src/main/java/com/zongheng/exhibitionhallrobot/MainActivity.java`；语音在同包 `speech/`，教学会话在 `service/TeachingSessionController.java`，TCP 在 `service/TcpService.java`，说话动画在 `ui/RobotSpeechAnimation.java`。

机器人通过 V5 中枢问答/教学，语音使用 61:8769 流式 TTS。七音色选择保存在设备，默认湾湾小何；右上角长按两秒进入管理。V5 的 MiMo 意图模型不是机器人 ASR/TTS。RobotOS 保留硬件唤醒，普通和教学 ASR 使用讯飞 SparkChain。

## 构建

使用 JDK21、工程自带 Gradle8.13/AGP8.13.2、Android SDK36 minor1 和 Build Tools36.0.0；以项目配置为准。

```powershell
.\gradlew.bat testDebugUnitTest assembleDebug
```

配置本机 `JAVA_HOME`、`ANDROID_HOME`，或参考 `local.properties.example`。保留 `app/libs` 内 RobotOS JAR、SparkChain/Codec AAR。不要将 `.gradle`、build、真实 local.properties 或签名私钥入库。

ASR 配置通过设备应用私有 `no_backup/iflytek-asr.json` 提供，字段模板在项目根目录，不包含真实凭据。现有安装覆盖升级需兼容签名，整理时不卸载或清数据。

## 来源与状态

从机器人 2026-09-10 干净交付快照复制 117 文件，逐项 SHA256 一致。原历史未导入；新增这份 README 是整合说明，不是业务修改。交付方记录 34 单元测试和 Debug 构建通过；本轮没有重跑构建或设备测试。

历史安装 APK 哈希：`fce24d01e47eeaed777f5f03147ff334c4440c45b60306c891b3ccafc0b8398a`。它是此前调试签名现场基线，不是今天新构建。源码与 V5、61 的组合版本仍待最终核对。
