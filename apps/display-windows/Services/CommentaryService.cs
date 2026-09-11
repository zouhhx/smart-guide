using System;
using System.Collections.Generic;
using System.IO;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using LibVLCSharp.Shared;

namespace ExhibitionClient.Services
{
    public class CommentaryService : IDisposable
    {
        private readonly string _fileServerUrl;
        private readonly string _ttsServerUrl;
        private readonly int? _screenNumber;
        private string _ttsVoice;
        private readonly HttpClient _http;
        private readonly Dictionary<string, string> _commentaryMap = new();
        private readonly object _audioLock = new();
        private LibVLC _audioLibVLC;
        private MediaPlayer _audioPlayer;
        private Media _currentAudioMedia;
        private TaskCompletionSource<bool> _playbackCompletion;
        private bool _isSpeaking;
        private bool _isMuted;
        private int _speechVersion;
        private CancellationTokenSource _speechCts;
        private readonly SemaphoreSlim _pcmGate = new(1, 1);

        public event Action OnSpeechStarted;
        public event Action OnSpeechFinished;

        public bool IsSpeaking => _isSpeaking;
        public bool IsMuted
        {
            get => _isMuted;
            set => _isMuted = value;
        }

        public CommentaryService(string fileServerUrl, int? screenNumber = null)
        {
            _fileServerUrl = fileServerUrl.TrimEnd('/');
            var settings = RuntimeSettingsStore.Current ?? new RuntimeSettings();
            _ttsServerUrl = string.IsNullOrWhiteSpace(settings.TtsServerUrl)
                ? _fileServerUrl
                : settings.TtsServerUrl.TrimEnd('/');
            _ttsVoice = string.IsNullOrWhiteSpace(settings.TtsVoice) ? "xiaoxiao" : settings.TtsVoice.Trim();
            _screenNumber = screenNumber;
            _http = new HttpClient { Timeout = TimeSpan.FromSeconds(90) };
            InitializeAudioPlayer();
        }

        private void InitializeAudioPlayer()
        {
            Core.Initialize();
            _audioLibVLC = new LibVLC(
                "--no-video",
                "--no-video-title-show",
                "--no-osd"
            );
            _audioPlayer = new MediaPlayer(_audioLibVLC);
            _audioPlayer.EndReached += (s, e) => CompletePlayback();
            _audioPlayer.EncounteredError += (s, e) =>
            {
                Logger.Error("[Commentary] LibVLC audio playback error");
                CompletePlayback();
            };
        }

        public async Task LoadCommentaryAsync()
        {
            try
            {
                var url = $"{_fileServerUrl}/commentary.json";
                Logger.Info($"[Commentary] Loading commentary: {url}");
                var json = await _http.GetStringAsync(url);
                var commentaries = JsonSerializer.Deserialize<List<CommentaryItem>>(
                    json,
                    new JsonSerializerOptions { PropertyNameCaseInsensitive = true });

                _commentaryMap.Clear();
                if (commentaries != null)
                {
                    foreach (var item in commentaries)
                    {
                        if (!string.IsNullOrEmpty(item.File) &&
                            !string.IsNullOrEmpty(item.Commentary) &&
                            string.Equals(item.Status, "ok", StringComparison.OrdinalIgnoreCase))
                        {
                            _commentaryMap[CleanFileName(item.File)] = item.Commentary;
                        }
                    }
                }

                Logger.Info($"[Commentary] Loaded {_commentaryMap.Count} commentary entries");
            }
            catch (Exception ex)
            {
                Logger.Error($"[Commentary] Failed to load commentary: server={_fileServerUrl}, type={ex.GetType().Name}, message={ex.Message}");
            }
        }

        public void SpeakCommentary(string fileName, int? pageNumber = null, string level = "concise")
        {
            var key = CleanFileName(fileName);

            if (!_commentaryMap.TryGetValue(key, out var raw))
            {
                Logger.Warn($"[Commentary] Commentary not found: file={fileName}, key={key}");
                return;
            }

            var text = ParseCommentary(raw, pageNumber, level);
            if (string.IsNullOrEmpty(text))
                return;

            Speak(text);
        }

        public void Speak(string text)
        {
            if (_isMuted)
            {
                Logger.Info("[Commentary] Muted, skip speech");
                return;
            }

            Stop();

            var version = Interlocked.Increment(ref _speechVersion);
            var request = new CancellationTokenSource();
            lock (_audioLock) _speechCts = request;
            _isSpeaking = true;
            OnSpeechStarted?.Invoke();

            var preview = text.Substring(0, Math.Min(50, text.Length));
            Logger.Info($"[Commentary] Start TTS, version={version}, length={text.Length}, voice={_ttsVoice}, preview={preview}");

            Task.Run(async () =>
            {
                try
                {
                    await SpeakWithEdgeTtsAsync(text, version, request.Token);
                }
                catch (OperationCanceledException) { Logger.Info($"[Commentary] Speech canceled version={version}"); }
                catch (Exception ex)
                {
                    Logger.Error($"[Commentary] TTS failed, version={version}, type={ex.GetType().Name}, message={ex.Message}");
                }
                finally
                {
                    lock (_audioLock) { if (_speechCts == request) _speechCts = null; }
                    request.Dispose();
                    if (version == _speechVersion)
                    {
                        _isSpeaking = false;
                        OnSpeechFinished?.Invoke();
                    }
                }
            });
        }

        private async Task SpeakWithEdgeTtsAsync(string text, int version, CancellationToken token)
        {
            var settings = await RefreshTtsVoiceAsync(token);
            token.ThrowIfCancellationRequested();
            var voice = settings?.Voice ?? _ttsVoice;
            if (settings?.Provider == "qwen3")
            {
                await _pcmGate.WaitAsync(token);
                try
                {
                    using var streamRequest = new HttpRequestMessage(HttpMethod.Post, $"{_ttsServerUrl}/api/tts-stream") {
                        Content = new StringContent(JsonSerializer.Serialize(new { text, voice, screenNumber = _screenNumber }), Encoding.UTF8, "application/json")
                    };
                    using var streamResponse = await _http.SendAsync(streamRequest, HttpCompletionOption.ResponseHeadersRead, token);
                    streamResponse.EnsureSuccessStatusCode();
                    if (!streamResponse.Headers.TryGetValues("X-Sample-Rate", out var rates) || rates.Single() != "24000"
                        || !streamResponse.Headers.TryGetValues("X-Audio-Channels", out var channels) || channels.Single() != "1"
                        || !streamResponse.Headers.TryGetValues("X-Audio-Format", out var formats) || formats.Single() != "pcm_s16le")
                        throw new IOException("本地语音流格式错误");
                    using var stream = await streamResponse.Content.ReadAsStreamAsync(token);
                    Logger.Info($"[Commentary] Local PCM playback voice={voice}, version={version}");
                    await PcmSpeechPlayer.PlayAsync(stream, token);
                    Logger.Info($"[Commentary] Local PCM completed voice={voice}, version={version}");
                }
                finally { _pcmGate.Release(); }
                return;
            }
            var payload = JsonSerializer.Serialize(new { text, voice, provider = settings?.Provider, screenNumber = _screenNumber });
            using var content = new StringContent(payload, Encoding.UTF8, "application/json");
            Logger.Info($"[Commentary] Request TTS, version={version}, server={_ttsServerUrl}, voice={_ttsVoice}");
            using var response = await _http.PostAsync($"{_ttsServerUrl}/api/tts", content, token);
            response.EnsureSuccessStatusCode();

            var json = await response.Content.ReadAsStringAsync();
            var result = JsonSerializer.Deserialize<TtsResponse>(json, new JsonSerializerOptions { PropertyNameCaseInsensitive = true });
            if (result?.Ok != true || result.Audio == null || string.IsNullOrWhiteSpace(result.Audio.Url))
                throw new InvalidOperationException(result?.Error ?? "invalid tts response");

            if (version != _speechVersion)
            {
                Logger.Warn($"[Commentary] TTS response ignored, version={version}, current={_speechVersion}");
                return;
            }

            var audioUrl = BuildAbsoluteUrl(result.Audio.Url);
            var audioPath = await DownloadTtsAudioAsync(audioUrl, result.Audio.FileName);

            if (version != _speechVersion)
            {
                Logger.Warn($"[Commentary] TTS audio ignored after download, version={version}, current={_speechVersion}");
                return;
            }

            await PlayAudioWithLibVlcAsync(audioPath, version);
        }

        private async Task<TtsSettingsResponse> RefreshTtsVoiceAsync(CancellationToken token)
        {
            try
            {
                using var response = await _http.GetAsync($"{_ttsServerUrl}/api/tts-settings", token);
                response.EnsureSuccessStatusCode();

                var json = await response.Content.ReadAsStringAsync();
                var result = JsonSerializer.Deserialize<TtsSettingsResponse>(json, new JsonSerializerOptions { PropertyNameCaseInsensitive = true });
                if (result?.Ok == true && !string.IsNullOrWhiteSpace(result.Voice))
                    _ttsVoice = result.Voice.Trim();
                return result;
            }
            catch (Exception ex)
            {
                Logger.Warn($"[Commentary] Failed to refresh TTS settings: {ex.GetType().Name} - {ex.Message}");
                throw; // Do not silently speak a different provider/voice when settings are unavailable.
            }
        }

        private string BuildAbsoluteUrl(string url)
        {
            if (Uri.TryCreate(url, UriKind.Absolute, out _))
                return url;

            return $"{_ttsServerUrl}/{url.TrimStart('/')}";
        }

        private async Task<string> DownloadTtsAudioAsync(string audioUrl, string fileName)
        {
            var cacheDir = Path.Combine(Path.GetTempPath(), "exhibition-edge-tts");
            Directory.CreateDirectory(cacheDir);

            var safeName = string.IsNullOrWhiteSpace(fileName)
                ? $"{HashText(audioUrl)}.mp3"
                : Path.GetFileName(fileName);
            if (!safeName.EndsWith(".mp3", StringComparison.OrdinalIgnoreCase))
                safeName += ".mp3";

            var localPath = Path.Combine(cacheDir, safeName);
            if (File.Exists(localPath) && new FileInfo(localPath).Length > 0)
            {
                Logger.Info($"[Commentary] Use cached TTS audio: {localPath}");
                return localPath;
            }

            var bytes = await _http.GetByteArrayAsync(audioUrl);
            await File.WriteAllBytesAsync(localPath, bytes);
            Logger.Info($"[Commentary] Downloaded TTS audio: {localPath}, bytes={bytes.Length}");
            return localPath;
        }

        private async Task PlayAudioWithLibVlcAsync(string audioPath, int version)
        {
            TaskCompletionSource<bool> completion;
            lock (_audioLock)
            {
                if (version != _speechVersion)
                    return;

                _audioPlayer.Stop();
                _currentAudioMedia?.Dispose();
                _currentAudioMedia = new Media(_audioLibVLC, new Uri(audioPath));
                _audioPlayer.Media = _currentAudioMedia;
                _audioPlayer.Mute = _isMuted;
                _audioPlayer.Volume = _isMuted ? 0 : 100;
                completion = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                _playbackCompletion = completion;

                Logger.Info($"[Commentary] Play TTS with LibVLC, version={version}: {audioPath}");
                if (!_audioPlayer.Play())
                    throw new InvalidOperationException("LibVLC failed to start audio playback");
            }

            await completion.Task;
            Logger.Info($"[Commentary] TTS playback completed, version={version}");
        }

        private void CompletePlayback()
        {
            lock (_audioLock)
            {
                _playbackCompletion?.TrySetResult(true);
                _playbackCompletion = null;
            }
        }

        private static string HashText(string text)
        {
            using var sha = SHA256.Create();
            var bytes = sha.ComputeHash(Encoding.UTF8.GetBytes(text));
            var sb = new StringBuilder(bytes.Length * 2);
            foreach (var b in bytes)
                sb.Append(b.ToString("x2"));
            return sb.ToString();
        }

        public void Stop()
        {
            var version = Interlocked.Increment(ref _speechVersion);
            _isSpeaking = false;
            lock (_audioLock)
            {
                try
                {
                    _speechCts?.Cancel();
                    _audioPlayer?.Stop();
                    _currentAudioMedia?.Dispose();
                    _currentAudioMedia = null;
                    _playbackCompletion?.TrySetResult(true);
                    _playbackCompletion = null;
                    Logger.Info($"[Commentary] Stop speech, version={version}");
                }
                catch
                {
                }
            }
        }

        private string ParseCommentary(string raw, int? pageNumber = null, string level = "concise")
        {
            if (string.IsNullOrEmpty(raw))
                return string.Empty;

            var text = Regex.Replace(raw, "<[^>]+>", string.Empty);
            text = text.Replace("&nbsp;", " ");
            var pageBlocks = Regex.Split(text, @"\s*&&\s*")
                .Where(item => !string.IsNullOrWhiteSpace(item))
                .ToList();

            if (pageNumber.HasValue && pageBlocks.Count > 0)
            {
                var index = Math.Max(1, pageNumber.Value) - 1;
                if (index >= pageBlocks.Count)
                    index = pageBlocks.Count - 1;

                return ExtractCommentaryLevel(pageBlocks[index], level);
            }

            var pages = pageBlocks
                .Select(item => ExtractCommentaryLevel(item, level))
                .Where(item => !string.IsNullOrWhiteSpace(item))
                .ToList();

            if (pages.Count > 0)
                text = string.Join(" ", pages);
            else
                text = Regex.Replace(text, @"\s*&&\s*", " ");

            return text.Trim();
        }

        private static string ExtractCommentaryLevel(string text, string level)
        {
            if (string.IsNullOrWhiteSpace(text))
                return string.Empty;

            var cleaned = text.Trim();
            foreach (var label in PreferredLabels(level))
            {
                var pattern = $"【{label}】";
                var start = cleaned.IndexOf(pattern, StringComparison.Ordinal);
                if (start < 0)
                    continue;

                start += pattern.Length;
                var next = Regex.Match(cleaned[start..], @"【(?:精炼版|标准版|详细版)】");
                var end = next.Success ? start + next.Index : cleaned.Length;
                var value = cleaned[start..end].Trim();
                if (!string.IsNullOrWhiteSpace(value))
                    return value;
            }

            return Regex.Replace(cleaned, @"【讲解单元】[^\r\n]*", string.Empty).Trim();
        }

        private static string[] PreferredLabels(string level)
        {
            return (level ?? string.Empty).Trim().ToLowerInvariant() switch
            {
                "standard" => new[] { "标准版", "精炼版", "详细版" },
                "detailed" => new[] { "详细版", "标准版", "精炼版" },
                _ => new[] { "精炼版", "标准版", "详细版" },
            };
        }

        private string CleanFileName(string fileName)
        {
            if (string.IsNullOrEmpty(fileName))
                return string.Empty;

            var name = Path.GetFileName(fileName);
            name = name.Replace(" ", "").Replace("\u3000", "");

            return name;
        }

        public void Dispose()
        {
            Stop();
            _audioPlayer?.Dispose();
            _audioLibVLC?.Dispose();
            _http?.Dispose();
        }

        private class TtsResponse
        {
            public bool Ok { get; set; }
            public string Error { get; set; }
            public TtsAudio Audio { get; set; }
        }

        private class TtsSettingsResponse
        {
            public string Provider { get; set; }
            public bool Ok { get; set; }
            public string Voice { get; set; }
        }

        private class TtsAudio
        {
            public string Url { get; set; }
            public string FileName { get; set; }
        }
    }

    internal class CommentaryItem
    {
        public string File { get; set; }
        public string Commentary { get; set; }
        public string Status { get; set; }
    }
}
