# 第三方组件与模型说明（P1）

本文件用于第一期技术验证和后续合规归档，不代替公司法务或开源合规审查。正式MSI交付前应把适用的完整许可证文本随安装包一并提供。

| 项目 | 当前版本/模型 | 用途 | 许可证/条件 |
|---|---|---|---|
| sherpa-onnx | 1.13.2 | Windows x64离线推理框架 | Apache-2.0；以官方仓库文件为准 |
| NumPy | 2.3.1 | 音频数组计算 | BSD-3-Clause；以发行包为准 |
| python-sounddevice | 0.5.2 | PortAudio麦克风采集 | MIT；以发行包为准 |
| pypinyin | 0.55.0 | 中文唤醒词转拼音Token | MIT；以发行包为准 |
| 中文KWS | sherpa-onnx WenetSpeech Zipformer 3.3M mobile | 开放词表中文唤醒 | 模型README标记Apache License 2.0；保留模型名称、来源和NOTICE |
| Silero VAD | sherpa-onnx发布的`silero_vad.onnx` | 语音活动检测 | Silero VAD官方仓库标记MIT；同时保留sherpa-onnx来源 |
| SenseVoice | `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17` | 中文离线ASR | FunASR Model Open Source License Agreement 1.1；需要标注来源并保留模型名称 |

## 来源

- sherpa-onnx：https://github.com/k2-fsa/sherpa-onnx
- 中文KWS：https://github.com/k2-fsa/sherpa-onnx/releases/tag/kws-models
- Silero VAD：https://github.com/snakers4/silero-vad
- SenseVoice：https://github.com/FunAudioLLM/SenseVoice
- FunASR及模型协议：https://github.com/modelscope/FunASR

2026年5月，SenseVoice项目成员在公开Issue中说明：SenseVoice等FunASR自有模型允许商业使用，但应遵守FunASR模型协议、注明来源并保留模型名称。正式交付仍应由公司按当时有效的模型协议完成书面合规确认。

