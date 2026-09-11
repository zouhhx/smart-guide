# KWS唤醒通知

语音网关识别到唤醒词后发送：

```http
POST http://192.168.16.61:3001/api/asr-text
Content-Type: application/json

{
  "text": "你好思德",
  "target": "2",
  "source": "microphone"
}
```

`target`固定为`"2"`，`source`固定为`"microphone"`。

中控收到后，自行决定数字人的应答内容、动作或画面反馈。
