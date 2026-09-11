using System;
using System.Collections.Generic;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using ExhibitionClient.Models;

namespace ExhibitionClient.Services
{
    /// <summary>
    /// WebSocket 通信服务
    /// </summary>
    public class WebSocketService : IDisposable
    {
        private ClientWebSocket _ws;
        private readonly string _url;
        private readonly int? _fixedScreenNumber;
        private readonly string _deviceId;
        private CancellationTokenSource _cts;
        private bool _isConnected;
        private bool _reconnectScheduled;
        private string _connectionSession = Guid.NewGuid().ToString("N");
        private readonly CommandDeduplicator _deduplicator = new();

        public event Action<Command> OnCommand;
        public event Action<DeviceInfo> OnRegistered;
        public event Action<ScreenSettings> OnScreenSettings;
        public event Action OnConnected;
        public event Action OnDisconnected;

        public bool IsConnected => _isConnected;
        public DeviceInfo Device { get; private set; }

        public WebSocketService(string url, int? fixedScreenNumber = null)
        {
            _url = url;
            _fixedScreenNumber = fixedScreenNumber;
            _deviceId = LoadOrCreateDeviceId();
            _ws = new ClientWebSocket();
            _cts = new CancellationTokenSource();
        }

        /// <summary>
        /// 从本地文件读取持久化 deviceId，不存在则生成并保存。
        /// </summary>
        private static string LoadOrCreateDeviceId()
        {
            var appDataDir = System.IO.Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "ExhibitionClient");
            System.IO.Directory.CreateDirectory(appDataDir);

            var path = System.IO.Path.Combine(appDataDir, "device-id.txt");
            var legacyPath = System.IO.Path.Combine(
                System.IO.Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location) ?? "",
                "device-id.txt");

            if (System.IO.File.Exists(path))
            {
                var id = System.IO.File.ReadAllText(path).Trim();
                if (!string.IsNullOrEmpty(id))
                {
                    Logger.Log($"[WS] 加载本地 deviceId: {id}");
                    return id;
                }
            }

            if (System.IO.File.Exists(legacyPath))
            {
                try
                {
                    System.IO.File.Delete(legacyPath);
                    Logger.Log($"[WS] 已删除旧版程序目录中的 device-id: {legacyPath}");
                }
                catch (Exception ex)
                {
                    Logger.Log($"[WS] 删除旧版程序目录中的 device-id 失败: {ex.Message}");
                }
            }

            var newId = Guid.NewGuid().ToString();
            System.IO.File.WriteAllText(path, newId);
            Logger.Log($"[WS] 生成新的 deviceId: {newId}");
            return newId;
        }

        public async Task ConnectAsync()
        {
            try
            {
                if (_ws.State == WebSocketState.Open)
                    return;

                _ws = new ClientWebSocket();
                _cts = new CancellationTokenSource();

                Logger.Info($"[WS] 开始连接服务端: {_url}");
                await _ws.ConnectAsync(new Uri(_url), _cts.Token);
                _isConnected = true;
                _connectionSession = Guid.NewGuid().ToString("N");
                _deduplicator.Clear();
                Logger.Info($"[WS] 已连接到服务端: {_url}");
                OnConnected?.Invoke();

                await SendAsync(new
                {
                    type = "register",
                    deviceType = "client",
                    deviceId = _deviceId,
                    name = Environment.MachineName,
                    os = Environment.OSVersion.ToString(),
                    version = "2.0.0+speech-interrupt-v1",
                    screenNumber = _fixedScreenNumber.HasValue && _fixedScreenNumber.Value > 0
                        ? (object)_fixedScreenNumber.Value
                        : null
                });
                Logger.Info($"[WS] 已发送注册请求: deviceId={_deviceId}, screenNumber={_fixedScreenNumber}");

                _ = ReceiveLoop();
                _ = HeartbeatLoop();
            }
            catch (Exception ex)
            {
                Logger.Error($"[WS] 连接失败: url={_url}, type={ex.GetType().Name}, message={ex.Message}");
                _isConnected = false;
                OnDisconnected?.Invoke();
                ScheduleReconnect();
            }
        }

        private async Task ReceiveLoop()
        {
            var buffer = new byte[8192];
            using var messageBuffer = new MemoryStream();

            while (_ws.State == WebSocketState.Open && !_cts.Token.IsCancellationRequested)
            {
                try
                {
                    var result = await _ws.ReceiveAsync(buffer, _cts.Token);

                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        await CloseAsync();
                        break;
                    }

                    messageBuffer.Write(buffer, 0, result.Count);
                    if (!result.EndOfMessage)
                        continue;

                    var json = Encoding.UTF8.GetString(messageBuffer.ToArray());
                    messageBuffer.SetLength(0);
                    Logger.Log($"[WS] 收到: {json}");

                    ProcessMessage(json);
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (Exception ex)
                {
                    Logger.Warn($"[WS] 接收异常: {ex.GetType().Name} - {ex.Message}");
                    break;
                }
            }

            _isConnected = false;
            OnDisconnected?.Invoke();

            Logger.Warn("[WS] 连接已断开，3 秒后尝试重连");
            ScheduleReconnect();
        }

        private void ScheduleReconnect()
        {
            if (_reconnectScheduled || _cts.IsCancellationRequested)
                return;

            _reconnectScheduled = true;
            _ = Task.Run(async () =>
            {
                try
                {
                    while (!_cts.IsCancellationRequested && _ws.State != WebSocketState.Open)
                    {
                        await Task.Delay(3000, _cts.Token);
                        Logger.Info($"[WS] 灏濊瘯閲嶈繛鏈嶅姟绔? {_url}");
                        await ConnectAsync();
                    }
                }
                catch (OperationCanceledException)
                {
                }
                finally
                {
                    _reconnectScheduled = false;
                }
            });
        }

        private void ProcessMessage(string json)
        {
            try
            {
                using var doc = JsonDocument.Parse(json);
                var root = doc.RootElement;
                var type = root.GetProperty("type").GetString();

                switch (type)
                {
                    case "registered":
                        if (root.TryGetProperty("device", out var deviceProp))
                        {
                            Device = new DeviceInfo
                            {
                                Id = deviceProp.GetProperty("id").GetString(),
                                ScreenNumber = deviceProp.TryGetProperty("screenNumber", out var sn) ? sn.GetInt32() : null,
                                Name = deviceProp.TryGetProperty("name", out var name) ? name.GetString() : null
                            };
                            OnRegistered?.Invoke(Device);
                            Logger.Log($"[WS] 注册成功: deviceId={Device.Id}, screenNumber={Device.ScreenNumber}, name={Device.Name}");
                        }
                        else
                        {
                            Logger.Warn("[WS] 收到 registered 消息，但缺少 device 字段");
                        }
                        break;

                    case "command":
                        long? sequence = root.TryGetProperty("seq", out var seqValue) && long.TryParse(seqValue.ToString(), out var seq) ? seq : null;
                        if (!_deduplicator.Accept(sequence, root))
                        {
                            Logger.Info($"[Control] duplicate session={_connectionSession} seq={sequence}");
                            break;
                        }
                        var receivedAt = System.Diagnostics.Stopwatch.GetTimestamp();
                        var parsedParams = new Dictionary<string, string>();
                        if (root.TryGetProperty("params", out var paramsElement) &&
                            paramsElement.ValueKind == JsonValueKind.Object)
                        {
                            foreach (var property in paramsElement.EnumerateObject())
                            {
                                parsedParams[property.Name] = property.Value.ToString();
                            }
                        }

                        var cmd = new Command
                        {
                            Sequence = sequence,
                            ConnectionSession = _connectionSession,
                            ReceivedTimestamp = receivedAt,
                            Type = type,
                            Params = parsedParams,
                            Action = root.TryGetProperty("action", out var action) ? action.GetString() : null,
                            From = root.TryGetProperty("from", out var from) ? from.GetString() : null,
                            ReplyText = root.TryGetProperty("replyText", out var rt) ? rt.GetString() : null,
                            File = root.TryGetProperty("params", out var p1) && p1.TryGetProperty("file", out var f) ? f.GetString() : null,
                            Text = root.TryGetProperty("params", out var p2) && p2.TryGetProperty("text", out var t) ? t.GetString() : null,
                            Question = root.TryGetProperty("params", out var p3) && p3.TryGetProperty("question", out var q) ? q.GetString() : null,
                            Answer = root.TryGetProperty("params", out var p4) && p4.TryGetProperty("answer", out var a) ? a.GetString() : null,
                            Slide = root.TryGetProperty("params", out var p5) && p5.TryGetProperty("slide", out var s) ? int.TryParse(s.ToString(), out var slideValue) ? slideValue : null : null,
                            Mute = root.TryGetProperty("params", out var p6) && p6.TryGetProperty("mute", out var m) ? bool.TryParse(m.ToString(), out var muteValue) ? muteValue : null : null
                        };
                        OnCommand?.Invoke(cmd);
                        break;

                    case "screen_settings":
                        if (root.TryGetProperty("settings", out var settingsProp))
                        {
                            bool? transparentBackground = null;
                            if (settingsProp.TryGetProperty("transparentBackground", out var transparentProp) &&
                                transparentProp.ValueKind is JsonValueKind.True or JsonValueKind.False)
                            {
                                transparentBackground = transparentProp.GetBoolean();
                            }

                            var backgroundFit = "";
                            if (settingsProp.TryGetProperty("backgroundFit", out var fitProp))
                            {
                                var fit = fitProp.ValueKind == JsonValueKind.String
                                    ? (fitProp.GetString() ?? "").Trim().ToLowerInvariant()
                                    : "";
                                backgroundFit = fit == "contain" ? "contain" : "cover";
                            }

                            var settings = new ScreenSettings
                            {
                                Name = settingsProp.TryGetProperty("name", out var nameProp) ? nameProp.GetString() ?? "" : "",
                                SnapshotIntervalSeconds = settingsProp.TryGetProperty("snapshotIntervalSeconds", out var snapshotProp) ? snapshotProp.GetInt32() : 5,
                                StatusIntervalSeconds = settingsProp.TryGetProperty("statusIntervalSeconds", out var statusProp) ? statusProp.GetInt32() : 1,
                                TransparentBackground = transparentBackground,
                                BackgroundFit = backgroundFit
                            };
                            OnScreenSettings?.Invoke(settings);
                            Logger.Log($"[WS] 已接收屏幕设置: name={settings.Name}, snapshot={settings.SnapshotIntervalSeconds}s, status={settings.StatusIntervalSeconds}s, transparent={settings.TransparentBackground?.ToString().ToLowerInvariant() ?? "unchanged"}, fit={settings.BackgroundFit}");
                        }
                        break;

                    default:
                        Logger.Warn($"[WS] 收到未处理的消息类型: {type}");
                        break;
                }
            }
            catch (Exception ex)
            {
                Logger.Error($"[WS] 解析消息失败: {ex.GetType().Name} - {ex.Message}; raw={json}");
            }
        }

        private async Task HeartbeatLoop()
        {
            while (_ws.State == WebSocketState.Open && !_cts.Token.IsCancellationRequested)
            {
                await Task.Delay(15000, _cts.Token);
                if (_ws.State == WebSocketState.Open)
                {
                    await SendAsync(new { type = "heartbeat" });
                }
            }
        }

        public async Task SendAsync(object data)
        {
            if (_ws.State != WebSocketState.Open)
                return;

            try
            {
                var json = JsonSerializer.Serialize(data);
                var bytes = Encoding.UTF8.GetBytes(json);
                await _ws.SendAsync(bytes, WebSocketMessageType.Text, true, _cts.Token);
            }
            catch (Exception ex)
            {
                Logger.Warn($"[WS] 发送失败: {ex.GetType().Name} - {ex.Message}");
            }
        }

        public async Task CloseAsync()
        {
            if (_ws.State == WebSocketState.Open)
            {
                try
                {
                    _cts.Cancel();
                    await _ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "Closing", CancellationToken.None);
                }
                catch
                {
                }
            }
            _isConnected = false;
        }

        public void Dispose()
        {
            _cts?.Cancel();
            _cts?.Dispose();
            _ws?.Dispose();
        }
    }

    public class ScreenSettings
    {
        public string Name { get; set; } = "";
        public int SnapshotIntervalSeconds { get; set; } = 5;
        public int StatusIntervalSeconds { get; set; } = 1;
        public bool? TransparentBackground { get; set; }
        public string BackgroundFit { get; set; } = "";
    }

    /// <summary>
    /// 日志服务
    /// </summary>
    public static class Logger
    {
        private static readonly string LogPath = @"C:\media\logs\client.log";
        private static readonly object FileLock = new();

        static Logger()
        {
            var dir = System.IO.Path.GetDirectoryName(LogPath);
            if (!System.IO.Directory.Exists(dir))
                System.IO.Directory.CreateDirectory(dir);
        }

        public static void Log(string msg, string level = "INFO")
        {
            try
            {
                var log = $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {msg}";
                lock (FileLock) System.IO.File.AppendAllText(LogPath, log + Environment.NewLine);
                System.Diagnostics.Debug.WriteLine(log);

                try { Views.LogForm.AppendIfCreated(level, msg); } catch { }
            }
            catch
            {
            }
        }

        public static void Info(string msg) => Log(msg, "INFO");
        public static void Warn(string msg) => Log(msg, "WARN");
        public static void Error(string msg) => Log(msg, "ERROR");
    }
}
