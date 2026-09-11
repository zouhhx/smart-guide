using System;
using System.IO;
using System.Windows.Forms;
using LibVLCSharp.Shared;
using LibVLCSharp.WinForms;
using ExhibitionClient.Services;

namespace ExhibitionClient.Controllers
{
    /// <summary>
    /// 视频播放控制器 - 使用 LibVLC
    /// </summary>
    public class VideoController : IDisposable
    {
        private LibVLC? _libVLC;
        private MediaPlayer? _mediaPlayer;
        private VideoView? _videoView;
        private Panel? _container;
        private readonly string _mediaPath;
        private bool _isPlaying;
        private bool _isMuted;
        private bool _isStopping;
        private int _playGeneration;
        private Media? _currentMedia;

        public event Action? OnEnded;
        public event Action? OnError;
        public event Action? OnPlaying;

        public bool IsPlaying => _isPlaying;
        public bool IsMuted
        {
            get => _isMuted;
            set
            {
                _isMuted = value;
                if (_mediaPlayer != null)
                    _mediaPlayer.Mute = value;
            }
        }

        public VideoView? VideoView => _videoView;
        public Panel? Container => _container;

        public VideoController(string mediaPath = @"C:\media")
        {
            _mediaPath = mediaPath;
            InitializeVLC();
        }

        private void InitializeVLC()
        {
            try
            {
                Core.Initialize();
                
                _libVLC = new LibVLC(
                    "--no-video-title-show",
                    "--no-osd",
                    "--mouse-hide-timeout=0"
                );

                _container = new Panel
                {
                    Dock = DockStyle.Fill,
                    BackColor = System.Drawing.Color.Black,
                    Visible = false
                };

                _videoView = new VideoView
                {
                    Dock = DockStyle.Fill,
                    MediaPlayer = null
                };

                _container.Controls.Add(_videoView);

                _mediaPlayer = new MediaPlayer(_libVLC);
                _videoView.MediaPlayer = _mediaPlayer;

                _mediaPlayer.Playing += (s, e) =>
                {
                    _isPlaying = true;
                    OnPlaying?.Invoke();
                    Logger.Info("[Video] 开始播放");
                };

                _mediaPlayer.EndReached += (s, e) =>
                {
                    BeginHandleEnd("EndReached");
                };



                Logger.Info("[Video] LibVLC 初始化完成");
            }
            catch (Exception ex)
            {
                Logger.Error($"[Video] LibVLC 初始化失败: {ex.Message}");
            }
        }

        private void BeginHandleEnd(string source)
        {
            var gen = _playGeneration;
            Task.Run(async () =>
            {
                await Task.Delay(300);
                if (gen == _playGeneration && !_isStopping)
                {
                    _isPlaying = false;
                    Logger.Info($"[Video] {source} 确认结束 gen={gen}");
                    OnEnded?.Invoke();
                }
                else
                {
                    Logger.Info($"[Video] {source} 忽略 gen={gen}, current={_playGeneration}, isStopping={_isStopping}");
                }
            });
        }

        /// <summary>
        /// 窗口 Load 后重新绑定 MediaPlayer，确保 HWND 已存在
        /// </summary>
        public void RebindView()
        {
            Logger.Info($"[Video] RebindView 调用: videoView={_videoView != null}, mediaPlayer={_mediaPlayer != null}");
            if (_videoView != null && _mediaPlayer != null)
            {
                _videoView.MediaPlayer = _mediaPlayer;
                Logger.Info($"[Video] VideoView 重新绑定完成, Handle={_videoView.IsHandleCreated}");
            }
        }

        /// <summary>
        /// 播放视频文件（支持URL或本地路径）
        /// </summary>
        public void Play(string fileName)
        {
            try
            {
                var localPath = GetLocalPath(fileName);
                
                if (string.IsNullOrEmpty(localPath) || !File.Exists(localPath))
                {
                    Logger.Error($"[Video] 文件不存在: {fileName}");
                    OnError?.Invoke();
                    return;
                }

                Logger.Info($"[Video] 播放: {localPath}");

                Logger.Info($"[Video] container.Visible={_container!.Visible}, videoView.Visible={_videoView!.Visible}");
                Logger.Info($"[Video] videoView.Handle={_videoView.IsHandleCreated}, mediaPlayer绑定={_videoView.MediaPlayer != null}");

                _container!.Visible = true;
                _videoView!.Visible = true;

                if (_isPlaying || (_mediaPlayer != null && _mediaPlayer.Media != null))
                {
                    Logger.Info($"[Video] Play 前 Stop，当前 generation={_playGeneration}");
                    Stop();
                }
                else
                {
                    Logger.Info("[Video] 当前无旧播放，跳过 Stop");
                }

                _playGeneration++; // 新一轮播放，旧的结束事件将被忽略
                Logger.Info($"[Video] 新播放 generation={_playGeneration}");

                var media = new Media(_libVLC!, localPath, FromType.FromPath);
                _currentMedia = media;

                _mediaPlayer!.Media = media;
                _mediaPlayer.Volume = _isMuted ? 0 : 100;
                _mediaPlayer.Play();
                Logger.Info($"[Video] Play() 已调用, state={_mediaPlayer.State}");
            }
            catch (Exception ex)
            {
                Logger.Error($"[Video] 播放失败: {ex.Message}");
                OnError?.Invoke();
            }
        }

        /// <summary>
        /// 暂停
        /// </summary>
        public void Pause()
        {
            if (_mediaPlayer?.CanPause == true)
            {
                _mediaPlayer.Pause();
                Logger.Info("[Video] 已暂停");
            }
        }

        /// <summary>
        /// 继续播放
        /// </summary>
        public void Resume()
        {
            if (_mediaPlayer?.State == VLCState.Paused)
            {
                _mediaPlayer.Play();
                Logger.Info("[Video] 继续播放");
            }
        }

        /// <summary>
        /// 停止
        /// </summary>
        public void Stop(bool hideContainer = false)
        {
            try
            {
                _isStopping = true;
                if (_mediaPlayer != null)
                {
                    _mediaPlayer.Stop();
                }
                _currentMedia?.Dispose();
                _currentMedia = null;
                _isPlaying = false;
                if (hideContainer)
                {
                    _container!.Visible = false;
                    _videoView!.Visible = false;
                }
                Logger.Info($"[Video] 已停止 hideContainer={hideContainer}");
            }
            catch (Exception ex)
            {
                Logger.Error($"[Video] 停止失败: {ex.Message}");
            }
            finally
            {
                _isStopping = false;
            }
        }

        /// <summary>
        /// 跳转到指定位置（秒）
        /// </summary>
        public void Seek(long timeMs)
        {
            if (_mediaPlayer != null)
            {
                _mediaPlayer.Time = timeMs;
            }
        }

        /// <summary>
        /// 隐藏播放器
        /// </summary>
        public void Hide()
        {
            Stop(true);
        }

        /// <summary>
        /// 显示播放器
        /// </summary>
        public void Show()
        {
            _container!.Visible = true;
            _videoView!.Visible = true;
        }

        private string? GetLocalPath(string fileName)
        {
            // 支持URL: 从URL取文件名并解码
            if (fileName.StartsWith("http://") || fileName.StartsWith("https://"))
            {
                var name = Uri.UnescapeDataString(fileName.Split('/').Last().Split('?')[0]);
                var path = Path.Combine(_mediaPath, name);
                return File.Exists(path) ? path : null;
            }
            if (File.Exists(fileName))
                return fileName;
            var p = Path.Combine(_mediaPath, Path.GetFileName(fileName));
            return File.Exists(p) ? p : null;
        }

        public void Dispose()
        {
            Stop();
            _mediaPlayer?.Dispose();
            _mediaPlayer = null;
            _libVLC?.Dispose();
            _videoView?.Dispose();
            _container?.Dispose();
        }
    }
}
