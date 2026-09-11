using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Windows.Forms;
using ExhibitionClient.Controllers;
using ExhibitionClient.Models;
using ExhibitionClient.Services;
using LibVLCSharp.Shared;

namespace ExhibitionClient.Views
{
    /// <summary>
    /// 灞曞巺涓荤獥鍙ｃ€?    /// </summary>
    public class MainForm : Form
    {
        private const int CaptionBackdropAlpha = 77;
        private const int MaxVisibleMediaResults = 9;
        private const int UiDesignWidth = 1920;
        private const int UiDesignHeight = 1080;
        private readonly WebSocketService _ws;
        private readonly FileSyncService _sync;
        private readonly CommentaryService _commentary;
        private readonly PptThumbnailService _pptThumbnails;
        private readonly AvatarClientService _avatar;
        private readonly RuntimeSettings _settings;
        private readonly PPTController _ppt;
        private readonly List<DisplayZone> _zones = new();
        private readonly Size _configuredResolution;

        private Panel _topPanel = null!;
        private Panel _idlePanel = null!;
        private Panel _blackoutPanel = null!;
        private Panel _speechPanel = null!;
        private Panel _qaPanel = null!;
        private TableLayoutPanel _zoneLayout = null!;
        private Panel _statusBar = null!;
        private Panel _statusBarFill = null!;
        private Label _toast = null!;
        private Label _popupCloseButton = null!;
        private Panel _adminPanel = null!;
        private Label _idleLogo = null!;
        private Label _idleTitle = null!;
        private Label _idleSubtitle = null!;
        private Label _idleHint = null!;
        private Label _speechAvatar = null!;
        private Label _speechText = null!;
        private Label _qaQuestion = null!;
        private Label _qaAnswer = null!;
        private Panel _mediaDialogPanel = null!;
        private Panel _mediaPromptPanel = null!;
        private Label _mediaDialogTitle = null!;
        private FlowLayoutPanel _mediaResultsPanel = null!;
        private Panel _qaCaptionPanel = null!;
        private Label _qaCaptionQuestion = null!;
        private Label _qaCaptionAnswer = null!;
        private SpriteAnimationBox _aiOrb = null!;
        private Panel _specialFileStripPanel = null!;
        private Label _specialFileStripTitle = null!;
        private FlowLayoutPanel _specialFileStripFlow = null!;
        private Image? _backgroundImage;
        private Bitmap? _backgroundRenderCache;
        private Size _backgroundRenderSize;
        private string _backgroundRenderPath = string.Empty;
        private string _backgroundRenderFit = string.Empty;
        private Image? _qaCaptionBackground;
        private string _backgroundImagePath = string.Empty;

        private System.Threading.Timer _toastTimer = null!;
        private System.Threading.Timer _statusUpdateTimer = null!;
        private System.Threading.Timer _snapshotTimer = null!;
        private System.Windows.Forms.Timer _thinkingTimer = null!;
        private System.Windows.Forms.Timer _qaAutoCloseTimer = null!;
        private int _thinkingDots;

        private string _currentView = "idle";
        private int? _screenNumber;
        private bool _isAdminVisible;
        private bool _isFullscreen;
        private bool _switchingToPpt;
        private readonly Queue<Command> _pendingCommands = new();
        private int _speechInterruptVersion;
        private bool _drainingCommands;
        private CancellationTokenSource _pptOpenCts;
        private bool _hidingToTray;
        private bool _snapshotInProgress;
        private bool _statusSendInProgress;
        private string _currentPptFileName = string.Empty;
        private int _currentPptSlideNumber = 1;
        private string _currentCommentaryLevel = "concise";
        private DateTime _startedAt = DateTime.Now;
        private ulong _lastIdleTime;
        private ulong _lastKernelTime;
        private ulong _lastUserTime;
        private bool _hasCpuSample;
        private readonly List<MediaResultCard> _mediaResultItems = new();
        private readonly List<Panel> _mediaResultCards = new();
        private int _mediaResultSelectedIndex = -1;
        private CancellationTokenSource? _mediaThumbnailCts;
        private readonly ToolTip _mediaTooltips = new();
        private readonly SemaphoreSlim _thumbnailSlots = new(2, 2);
        private readonly System.Net.Http.HttpClient _thumbnailHttp = new() { Timeout = TimeSpan.FromSeconds(15) };
        private readonly List<MediaResultCard> _specialFileItems = new();
        private readonly List<Panel> _specialFileCards = new();
        private int _specialFileSelectedIndex = -1;

        public MainForm()
        {
            Core.Initialize();

            _settings = RuntimeSettings.Load();
            RuntimeSettingsStore.Current = _settings;
            _configuredResolution = new Size(_settings.DisplayWidth, _settings.DisplayHeight);

            _ws = new WebSocketService(_settings.WebSocketUrl, _settings.ScreenNumber);
            _ws.OnCommand += HandleCommand;
            _ws.OnRegistered += OnDeviceRegistered;
            _ws.OnScreenSettings += OnScreenSettings;
            _ws.OnConnected += OnWSConnected;
            _ws.OnDisconnected += OnWSDisconnected;

            _sync = new FileSyncService(_settings.FileServerUrl, _settings.MediaPath);
            _commentary = new CommentaryService(_settings.FileServerUrl, _settings.ScreenNumber);
            _commentary.OnSpeechFinished += OnSpeechFinished;
            _pptThumbnails = new PptThumbnailService(_settings.PptThumbnailServiceUrl);
            _avatar = new AvatarClientService(_settings);

            _ppt = new PPTController(_settings.MediaPath);
            _ppt.OnClosed += OnPPTClosed;
            _ppt.OnFaulted += message =>
            {
                if (!IsDisposed && IsHandleCreated)
                    BeginInvoke(new Action(() => ShowControlError(message)));
            };

            InitializeZones();
            InitializeComponent();
            ApplyConfiguredResolution();

            StartPosition = FormStartPosition.CenterScreen;
            KeyPreview = true;
            KeyDown += OnKeyDown;
            Load += OnLoaded;

            ConnectAndStart();
            _statusUpdateTimer = new System.Threading.Timer(UpdateStatus, null, 0, ToTimerPeriod(_settings.StatusIntervalSeconds, 1));
            _snapshotTimer = new System.Threading.Timer(SendScreenSnapshot, null, ToTimerPeriod(_settings.SnapshotIntervalSeconds, 5), ToTimerPeriod(_settings.SnapshotIntervalSeconds, 5));
        }

        private void InitializeZones()
        {
            var segmentWidth = Math.Max(1, _settings.SegmentWidth);
            var segmentHeight = Math.Max(1, _settings.SegmentHeight);
            var totalWidth = Math.Max(segmentWidth, _settings.DisplayWidth);
            var totalHeight = Math.Max(segmentHeight, _settings.DisplayHeight);
            var columns = Math.Max(1, totalWidth / segmentWidth);
            var rows = Math.Max(1, totalHeight / segmentHeight);
            var totalZones = Math.Max(1, columns * rows);

            for (int i = 0; i < totalZones; i++)
            {
                var zone = new DisplayZone(i, _settings.MediaPath);
                zone.Video.OnEnded += () => OnZoneVideoEnded(zone.Index);
                zone.Video.OnError += () => OnZoneVideoError(zone.Index);
                _zones.Add(zone);
            }
        }

        private void InitializeComponent()
        {
            Text = "AI \u667a\u80fd\u5c55\u5385";
            Size = _configuredResolution;
            WindowState = FormWindowState.Normal;
            BackColor = GetShellBackColor();
            FormBorderStyle = FormBorderStyle.None;
            DoubleBuffered = true;
            TransparencyKey = _settings.TransparentBackground ? TransparentShellColor : Color.Empty;
            ShowInTaskbar = !_settings.NonIntrusiveMode;
            if (_settings.NonIntrusiveMode)
                Opacity = 0;

            _topPanel = new BufferedPanel { Dock = DockStyle.Fill, BackColor = GetShellBackColor() };
            _topPanel.MouseDown += StartPopupDrag;
            Controls.Add(_topPanel);

            _idlePanel = CreateIdlePanel();
            _topPanel.Controls.Add(_idlePanel);

            _blackoutPanel = new Panel
            {
                Dock = DockStyle.Fill,
                BackColor = Color.Black,
                Visible = false
            };
            _topPanel.Controls.Add(_blackoutPanel);

            _zoneLayout = CreateZoneLayout();
            _zoneLayout.Visible = false;
            _topPanel.Controls.Add(_zoneLayout);

            _speechPanel = CreateSpeechPanel();
            _speechPanel.Visible = false;
            _topPanel.Controls.Add(_speechPanel);

            _qaPanel = CreateQAPanel();
            _qaPanel.Visible = false;
            _topPanel.Controls.Add(_qaPanel);

            _qaCaptionPanel = CreateQACaptionPanel();
            _topPanel.Controls.Add(_qaCaptionPanel);
            _qaCaptionPanel.BringToFront();

            _specialFileStripPanel = CreateSpecialFileStripPanel();
            _topPanel.Controls.Add(_specialFileStripPanel);
            _specialFileStripPanel.BringToFront();

            _aiOrb = new SpriteAnimationBox
            {
                BackColor = Color.Transparent,
                Visible = false,
                PaintBehind = PaintOrbBackground
            };
            _topPanel.Controls.Add(_aiOrb);

            _statusBar = new Panel
            {
                Dock = DockStyle.Top,
                Height = 3,
                BackColor = Color.Black,
                Visible = false
            };
            _statusBarFill = new Panel
            {
                Dock = DockStyle.Left,
                Width = 0,
                Height = 3,
                BackColor = Color.FromArgb(0, 212, 255)
            };
            _statusBar.Controls.Add(_statusBarFill);
            _topPanel.Controls.Add(_statusBar);

            _toast = new Label
            {
                AutoSize = false,
                TextAlign = ContentAlignment.MiddleCenter,
                Font = new Font("Microsoft YaHei UI", 14F),
                BackColor = Color.FromArgb(200, 0, 0, 0),
                ForeColor = Color.White,
                Visible = false,
                Size = new Size(420, 50)
            };
            _topPanel.Controls.Add(_toast);

            _popupCloseButton = new Label
            {
                AutoSize = false,
                Text = "×",
                Font = new Font("Segoe UI", 16F, FontStyle.Regular),
                TextAlign = ContentAlignment.MiddleCenter,
                ForeColor = Color.FromArgb(190, 240, 248),
                BackColor = Color.FromArgb(48, 255, 255, 255),
                Size = new Size(34, 34),
                Cursor = Cursors.Hand,
                Visible = false
            };
            _popupCloseButton.Click += (s, e) => ShowIdle();
            _topPanel.Controls.Add(_popupCloseButton);

            _adminPanel = CreateAdminPanel();
            _adminPanel.Visible = false;
            Controls.Add(_adminPanel);
        }

        private TableLayoutPanel CreateZoneLayout()
        {
            var segmentWidth = Math.Max(1, _settings.SegmentWidth);
            var segmentHeight = Math.Max(1, _settings.SegmentHeight);
            var columns = Math.Max(1, _settings.DisplayWidth / segmentWidth);
            var rows = Math.Max(1, _settings.DisplayHeight / segmentHeight);

            var layout = new TableLayoutPanel
            {
                Dock = DockStyle.Fill,
                BackColor = Color.Black,
                ColumnCount = columns,
                RowCount = rows,
                Margin = Padding.Empty,
                Padding = Padding.Empty
            };

            for (int column = 0; column < columns; column++)
                layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F / columns));

            for (int row = 0; row < rows; row++)
                layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100F / rows));

            for (int i = 0; i < _zones.Count; i++)
            {
                var zone = _zones[i];
                zone.Root.Controls.Add(zone.Image.Container);
                zone.Root.Controls.Add(zone.Video.Container!);
                zone.Root.Controls.Add(zone.Placeholder);

                var row = i / columns;
                var column = i % columns;
                layout.Controls.Add(zone.Root, column, row);
            }

            return layout;
        }

        private Panel CreateIdlePanel()
        {
            var panel = new BufferedPanel { Dock = DockStyle.Fill, BackColor = GetShellBackColor() };
            panel.Paint += (s, e) =>
            {
                PaintTechIdleBackground(panel, e.Graphics);
            };

            _idleLogo = new Label
            {
                Text = string.Empty,
                Font = new Font("Segoe UI", 20F, FontStyle.Bold),
                ForeColor = Color.FromArgb(88, 239, 255),
                TextAlign = ContentAlignment.MiddleCenter,
                AutoSize = true,
                BackColor = Color.Transparent,
                Visible = false
            };
            panel.Controls.Add(_idleLogo);

            _idleTitle = new Label
            {
                Text = "AI \u667a\u80fd\u5c55\u5385",
                Font = new Font("Microsoft YaHei UI", 54F, FontStyle.Bold),
                ForeColor = Color.White,
                TextAlign = ContentAlignment.MiddleCenter,
                AutoSize = true,
                BackColor = Color.Transparent,
                Visible = false
            };
            panel.Controls.Add(_idleTitle);

            _idleSubtitle = new Label
            {
                Text = "INTELLIGENT EXHIBITION COMMAND CENTER",
                Font = new Font("Segoe UI", 16F, FontStyle.Regular),
                ForeColor = Color.FromArgb(130, 230, 255),
                TextAlign = ContentAlignment.MiddleCenter,
                AutoSize = true,
                BackColor = Color.Transparent,
                Visible = false
            };
            panel.Controls.Add(_idleSubtitle);

            _idleHint = new Label
            {
                Text = "\u8bf7\u4f7f\u7528\u79fb\u52a8\u7aef\u8bed\u97f3\u63a7\u5236",
                Font = new Font("Microsoft YaHei UI", 15F),
                ForeColor = Color.FromArgb(150, 186, 210),
                TextAlign = ContentAlignment.MiddleCenter,
                AutoSize = true,
                BackColor = Color.Transparent,
                Visible = false
            };
            panel.Controls.Add(_idleHint);

            panel.Resize += (s, e) =>
            {
                CenterIdleLabels();
            };

            return panel;
        }

        private void CenterIdleLabels()
        {
            if (_idlePanel == null) return;

            int centerX = _idlePanel.Width / 2;
            int centerY = _idlePanel.Height / 2;

            _idleLogo.Location = new Point(centerX - _idleLogo.Width / 2, centerY - 118);
            _idleTitle.Location = new Point(centerX - _idleTitle.Width / 2, centerY - 48);
            _idleSubtitle.Location = new Point(centerX - _idleSubtitle.Width / 2, centerY + 44);
            _idleHint.Location = new Point(centerX - _idleHint.Width / 2, centerY + 132);
        }

        private void PaintTechIdleBackground(Control panel, Graphics g)
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            var rect = panel.ClientRectangle;
            if (rect.Width <= 0 || rect.Height <= 0) return;

            if (PaintConfiguredBackground(panel, g))
                return;

            using (var brush = new LinearGradientBrush(
                rect,
                Color.FromArgb(5, 12, 28),
                Color.FromArgb(8, 34, 48),
                LinearGradientMode.ForwardDiagonal))
            {
                g.FillRectangle(brush, rect);
            }

            using (var glow = new GraphicsPath())
            {
                glow.AddEllipse(rect.Width / 2 - 520, rect.Height / 2 - 360, 1040, 720);
                using var pathBrush = new PathGradientBrush(glow)
                {
                    CenterColor = Color.FromArgb(54, 0, 190, 255),
                    SurroundColors = new[] { Color.FromArgb(0, 0, 190, 255) }
                };
                g.FillPath(pathBrush, glow);
            }

            using var gridPen = new Pen(Color.FromArgb(26, 88, 205, 230), 1F);
            const int grid = 64;
            for (int x = rect.Left % grid; x < rect.Width; x += grid)
                g.DrawLine(gridPen, x, 0, x, rect.Height);
            for (int y = rect.Top % grid; y < rect.Height; y += grid)
                g.DrawLine(gridPen, 0, y, rect.Width, y);

            using var scanPen = new Pen(Color.FromArgb(34, 78, 255, 228), 2F);
            g.DrawLine(scanPen, rect.Width * 0.10F, rect.Height * 0.22F, rect.Width * 0.90F, rect.Height * 0.22F);
            g.DrawLine(scanPen, rect.Width * 0.15F, rect.Height * 0.78F, rect.Width * 0.85F, rect.Height * 0.78F);

            var centerPanel = new Rectangle(rect.Width / 2 - 520, rect.Height / 2 - 215, 1040, 430);
            using var panelBrush = new SolidBrush(Color.FromArgb(42, 5, 20, 38));
            using var borderPen = new Pen(Color.FromArgb(128, 46, 232, 255), 2F);
            using var accentPen = new Pen(Color.FromArgb(210, 40, 255, 178), 4F);
            using (var centerPath = CreateRoundedRectanglePath(centerPanel, 28))
            {
                g.FillPath(panelBrush, centerPath);
                g.DrawPath(borderPen, centerPath);
            }
            g.DrawLine(accentPen, centerPanel.Left + 36, centerPanel.Top, centerPanel.Left + 260, centerPanel.Top);
            g.DrawLine(accentPen, centerPanel.Right - 260, centerPanel.Bottom, centerPanel.Right - 36, centerPanel.Bottom);

            DrawCorner(g, rect.Left + 52, rect.Top + 48, 92, 1);
            DrawCorner(g, rect.Right - 144, rect.Top + 48, 92, 2);
            DrawCorner(g, rect.Left + 52, rect.Bottom - 140, 92, 3);
            DrawCorner(g, rect.Right - 144, rect.Bottom - 140, 92, 4);
        }

        private static void DrawCorner(Graphics g, int x, int y, int size, int corner)
        {
            using var pen = new Pen(Color.FromArgb(160, 52, 235, 255), 3F);
            if (corner == 1 || corner == 3)
                g.DrawLine(pen, x, y, x + size, y);
            else
                g.DrawLine(pen, x + size, y, x, y);

            if (corner == 1 || corner == 2)
                g.DrawLine(pen, corner == 1 ? x : x + size, y, corner == 1 ? x : x + size, y + size);
            else
                g.DrawLine(pen, corner == 3 ? x : x + size, y + size, corner == 3 ? x : x + size, y);
        }

        private bool PaintConfiguredBackground(Control panel, Graphics g)
        {
            var rect = panel.ClientRectangle;
            if (rect.Width <= 0 || rect.Height <= 0)
                return true;

            if (_settings.TransparentBackground)
            {
                g.Clear(TransparentShellColor);
                return true;
            }

            var renderedBackground = GetRenderedBackground(rect.Size);
            if (renderedBackground != null)
            {
                g.DrawImageUnscaled(renderedBackground, rect.Location);
                return true;
            }

            using var brush = new SolidBrush(ParseHtmlColor(_settings.BackgroundColor, Color.FromArgb(5, 12, 28)));
            g.FillRectangle(brush, rect);
            return false;
        }

        private Image? GetConfiguredBackgroundImage()
        {
            var configuredPath = ResolveAppPath(_settings.BackgroundImagePath);
            if (_backgroundImage != null && string.Equals(_backgroundImagePath, configuredPath, StringComparison.OrdinalIgnoreCase))
                return _backgroundImage;

            if (string.IsNullOrWhiteSpace(configuredPath) || !File.Exists(configuredPath)) return null;
            try
            {
                using var source = Image.FromFile(configuredPath);
                var replacement = new Bitmap(source);
                _backgroundImage?.Dispose();
                _backgroundImage = replacement;
            }
            catch (Exception ex)
            {
                Logger.Warn($"[Display] 背景读取失败: {ex.Message}");
                return _backgroundImage;
            }
            _backgroundImagePath = configuredPath;
            ClearBackgroundRenderCache();
            return _backgroundImage;
        }

        private Bitmap? GetRenderedBackground(Size size)
        {
            if (size.Width <= 0 || size.Height <= 0)
                return null;

            var image = GetConfiguredBackgroundImage();
            if (image == null)
                return null;

            var backgroundFit = NormalizeBackgroundFit(_settings.BackgroundFit);
            if (_backgroundRenderCache != null &&
                _backgroundRenderSize == size &&
                string.Equals(_backgroundRenderPath, _backgroundImagePath, StringComparison.OrdinalIgnoreCase) &&
                string.Equals(_backgroundRenderFit, backgroundFit, StringComparison.Ordinal))
            {
                return _backgroundRenderCache;
            }

            ClearBackgroundRenderCache();
            var rendered = new Bitmap(size.Width, size.Height, PixelFormat.Format32bppPArgb);
            using (var graphics = Graphics.FromImage(rendered))
            {
                graphics.CompositingMode = CompositingMode.SourceCopy;
                graphics.CompositingQuality = CompositingQuality.HighQuality;
                graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
                graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
                var bounds = new Rectangle(Point.Empty, size);
                if (backgroundFit == "contain")
                {
                    graphics.Clear(ParseHtmlColor(_settings.BackgroundColor, Color.FromArgb(5, 12, 28)));
                    DrawImageContain(graphics, image, bounds);
                }
                else
                {
                    DrawImageCover(graphics, image, bounds);
                }
            }

            _backgroundRenderCache = rendered;
            _backgroundRenderSize = size;
            _backgroundRenderPath = _backgroundImagePath;
            _backgroundRenderFit = backgroundFit;
            return rendered;
        }

        private void ClearBackgroundRenderCache()
        {
            _backgroundRenderCache?.Dispose();
            _backgroundRenderCache = null;
            _backgroundRenderSize = Size.Empty;
            _backgroundRenderPath = string.Empty;
            _backgroundRenderFit = string.Empty;
        }

        private static void DrawImageCover(Graphics g, Image image, Rectangle bounds)
        {
            g.InterpolationMode = InterpolationMode.HighQualityBicubic;
            var scale = Math.Max(bounds.Width / (float)image.Width, bounds.Height / (float)image.Height);
            var width = image.Width * scale;
            var height = image.Height * scale;
            var x = bounds.Left + (bounds.Width - width) / 2F;
            var y = bounds.Top + (bounds.Height - height) / 2F;
            g.DrawImage(image, x, y, width, height);
        }

        private static void DrawImageContain(Graphics g, Image image, Rectangle bounds)
        {
            g.InterpolationMode = InterpolationMode.HighQualityBicubic;
            var scale = Math.Min(bounds.Width / (float)image.Width, bounds.Height / (float)image.Height);
            var width = image.Width * scale;
            var height = image.Height * scale;
            var x = bounds.Left + (bounds.Width - width) / 2F;
            var y = bounds.Top + (bounds.Height - height) / 2F;
            g.DrawImage(image, x, y, width, height);
        }

        private static string NormalizeBackgroundFit(string? value)
        {
            return string.Equals(value?.Trim(), "contain", StringComparison.OrdinalIgnoreCase)
                ? "contain"
                : "cover";
        }

        private static void DrawImageWithOpacity(Graphics g, Image image, Rectangle bounds, float opacity)
        {
            using var attributes = new ImageAttributes();
            var matrix = new ColorMatrix { Matrix33 = Math.Clamp(opacity, 0F, 1F) };
            attributes.SetColorMatrix(matrix, ColorMatrixFlag.Default, ColorAdjustType.Bitmap);
            g.DrawImage(
                image,
                bounds,
                0,
                0,
                image.Width,
                image.Height,
                GraphicsUnit.Pixel,
                attributes);
        }

        private static Color ParseHtmlColor(string? value, Color fallback)
        {
            if (string.IsNullOrWhiteSpace(value))
                return fallback;

            try
            {
                return ColorTranslator.FromHtml(value.Trim());
            }
            catch
            {
                return fallback;
            }
        }

        private Color GetShellBackColor()
        {
            return _settings.TransparentBackground
                ? TransparentShellColor
                : ParseHtmlColor(_settings.BackgroundColor, Color.Black);
        }

        private static string ResolveAppPath(string? path)
        {
            if (string.IsNullOrWhiteSpace(path))
                return string.Empty;

            return Path.IsPathRooted(path)
                ? path
                : Path.Combine(AppContext.BaseDirectory, path);
        }

        private static GraphicsPath CreateRoundedRectanglePath(Rectangle rect, int radius)
        {
            var path = new GraphicsPath();
            int diameter = radius * 2;
            path.AddArc(rect.Left, rect.Top, diameter, diameter, 180, 90);
            path.AddArc(rect.Right - diameter, rect.Top, diameter, diameter, 270, 90);
            path.AddArc(rect.Right - diameter, rect.Bottom - diameter, diameter, diameter, 0, 90);
            path.AddArc(rect.Left, rect.Bottom - diameter, diameter, diameter, 90, 90);
            path.CloseFigure();
            return path;
        }

        private Panel CreateSpeechPanel()
        {
            var panel = new BufferedPanel { Dock = DockStyle.Fill, BackColor = GetShellBackColor() };
            panel.Paint += (s, e) =>
            {
                PaintConfiguredBackground(panel, e.Graphics);
            };

            _speechAvatar = new Label
            {
                Text = string.Empty,
                Visible = false,
                Font = new Font("Microsoft YaHei UI", 60F, FontStyle.Bold),
                ForeColor = Color.White,
                TextAlign = ContentAlignment.MiddleCenter,
                AutoSize = true
            };
            panel.Controls.Add(_speechAvatar);

            _speechText = new Label
            {
                Font = new Font("Microsoft YaHei UI", 28F),
                ForeColor = Color.White,
                TextAlign = ContentAlignment.MiddleCenter,
                AutoSize = false
            };
            panel.Controls.Add(_speechText);

            panel.Resize += (s, e) =>
            {
                _speechAvatar.Location = new Point(panel.Width / 2 - _speechAvatar.Width / 2, 150);
                _speechText.Location = new Point(100, panel.Height / 2);
                _speechText.Size = new Size(panel.Width - 200, 300);
            };

            return panel;
        }

        private Panel CreateQAPanel()
        {
            var panel = new BufferedPanel { Dock = DockStyle.Fill, BackColor = GetShellBackColor() };
            panel.Paint += (s, e) =>
            {
                e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
                PaintConfiguredBackground(panel, e.Graphics);
            };

            _mediaPromptPanel = new BufferedPanel
            {
                Height = 110,
                Dock = DockStyle.Bottom,
                BackColor = Color.Transparent,
                Visible = false
            };
            _mediaPromptPanel.Paint += (s, e) => PaintCaptionBackdrop(_mediaPromptPanel, e.Graphics);
            panel.Controls.Add(_mediaPromptPanel);

            _qaQuestion = new Label
            {
                Font = new Font("Microsoft YaHei UI", 22F),
                ForeColor = Color.FromArgb(0, 212, 255),
                TextAlign = ContentAlignment.MiddleLeft,
                AutoSize = false,
                Padding = new Padding(20),
                BackColor = Color.FromArgb(30, 0, 212, 255)
            };
            panel.Controls.Add(_qaQuestion);

            _qaAnswer = new Label
            {
                Font = new Font("Microsoft YaHei UI", 38F, FontStyle.Bold),
                ForeColor = Color.White,
                TextAlign = ContentAlignment.MiddleLeft,
                AutoSize = false,
                BackColor = Color.Transparent
            };
            panel.Controls.Add(_qaAnswer);

            _mediaDialogPanel = new BufferedPanel
            {
                BackColor = Color.Transparent,
                Visible = false,
                Padding = new Padding(44, 72, 44, 34)
            };
            _mediaDialogPanel.Paint += (s, e) =>
            {
                e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
                var rect = new Rectangle(1, 1, _mediaDialogPanel.Width - 3, _mediaDialogPanel.Height - 3);
                if (rect.Width <= 0 || rect.Height <= 0)
                    return;

                using var path = CreateRoundedRectanglePath(rect, 24);
                using var fill = new SolidBrush(Color.FromArgb(218, 82, 88, 101));
                using var border = new Pen(Color.FromArgb(125, 255, 255, 255), 2);
                e.Graphics.FillPath(fill, path);
                e.Graphics.DrawPath(border, path);
            };
            panel.Controls.Add(_mediaDialogPanel);

            _mediaDialogTitle = new Label
            {
                Text = "请选择要展示的文件",
                Location = new Point(28, 14),
                Height = 38,
                Font = new Font("Microsoft YaHei UI", 20F, FontStyle.Bold),
                ForeColor = Color.White,
                TextAlign = ContentAlignment.MiddleLeft,
                BackColor = Color.Transparent
            };
            _mediaDialogPanel.Controls.Add(_mediaDialogTitle);

            _mediaResultsPanel = new BufferedFlowLayoutPanel
            {
                AutoScroll = false,
                WrapContents = true,
                FlowDirection = FlowDirection.LeftToRight,
                BackColor = Color.Transparent,
                Padding = new Padding(0),
                Visible = false
            };
            _mediaDialogPanel.Controls.Add(_mediaResultsPanel);

            panel.Resize += (s, e) => LayoutQAPanel(panel);
            LayoutQAPanel(panel);

            return panel;
        }

        private void LayoutQAPanel(Panel panel)
        {
            var scale = GetUiScale(panel);
            var promptHeight = ScalePx(84, scale);
            if (_mediaPromptPanel.Height != promptHeight)
                _mediaPromptPanel.Height = promptHeight;

            if (_mediaDialogPanel?.Visible == true)
            {
                var avatarReserve = GetAvatarReserve(panel.Width, scale);
                var usableWidth = Math.Max(ScalePx(720, scale), panel.Width - avatarReserve);
                var dialogWidth = Math.Min(ScalePx(860, scale), Math.Max(ScalePx(620, scale), usableWidth - ScalePx(100, scale)));
                var rowCount = Math.Clamp((_mediaResultItems.Count + 2) / 3, 1, 3);
                var dialogHeight = Math.Min(ScalePx(rowCount * 240 + 52, scale), panel.Height - _mediaPromptPanel.Height - ScalePx(64, scale));
                var promptTop = panel.Height - _mediaPromptPanel.Height;
                var centeredTop = (panel.Height - dialogHeight) / 2 + ScalePx(240, scale);
                var dialogTop = Math.Clamp(
                    centeredTop,
                    ScalePx(40, scale),
                    Math.Max(ScalePx(40, scale), promptTop - dialogHeight - ScalePx(24, scale)));
                var minLeft = ScalePx(32, scale);
                var maxLeft = Math.Max(minLeft, usableWidth - dialogWidth - ScalePx(24, scale));
                var dialogLeft = Math.Clamp((panel.Width - dialogWidth) / 2, minLeft, maxLeft);
                _mediaDialogPanel.Bounds = new Rectangle(
                    dialogLeft,
                    dialogTop,
                    dialogWidth,
                    dialogHeight);
                var dialogPadding = ScalePx(18, scale);
                var titleHeight = ScalePx(28, scale);
                _mediaDialogTitle.Bounds = new Rectangle(dialogPadding, ScalePx(8, scale), dialogWidth - dialogPadding * 2, titleHeight);
                SetScaledFont(_mediaDialogTitle, 14F, FontStyle.Bold, scale);
                _mediaResultsPanel.Bounds = new Rectangle(
                    dialogPadding,
                    ScalePx(39, scale),
                    dialogWidth - dialogPadding * 2,
                    dialogHeight - ScalePx(52, scale));

                var orbSpace = ScalePx(125, scale);
                var promptRight = GetAvatarReserve(_mediaPromptPanel.Width, scale);
                _qaAnswer.Bounds = new Rectangle(
                    orbSpace,
                    0,
                    Math.Max(ScalePx(260, scale), _mediaPromptPanel.Width - orbSpace - promptRight),
                    _mediaPromptPanel.Height);
                SetScaledFont(_qaAnswer, 14F, FontStyle.Bold, scale);
                LayoutMediaResultCards(scale);
                return;
            }

            _qaQuestion.Location = new Point(48, 52);
            _qaQuestion.Size = new Size(panel.Width - 112, 94);
            _qaAnswer.Location = new Point(48, 182);
            _qaAnswer.Size = new Size(panel.Width - 96, 112);
        }

        private float GetUiScale(Control? basis = null)
        {
            var size = basis?.ClientSize ?? _topPanel?.ClientSize ?? ClientSize;
            if (size.Width <= 0 || size.Height <= 0)
                return 1F;

            return Math.Clamp(Math.Min(size.Width / (float)UiDesignWidth, size.Height / (float)UiDesignHeight), 0.65F, 2.5F);
        }

        private static int ScalePx(int value, float scale)
        {
            return Math.Max(1, (int)Math.Round(value * scale));
        }

        private static int GetAvatarReserve(int width, float scale)
        {
            return Math.Clamp((int)(width * 0.24F), ScalePx(360, scale), ScalePx(860, scale));
        }

        private static void SetScaledFont(Control control, float baseSize, FontStyle style, float scale)
        {
            var size = Math.Max(9F, baseSize * scale);
            if (Math.Abs(control.Font.Size - size) < 0.25F && control.Font.Style == style)
                return;

            var oldFont = control.Font;
            control.Font = new Font("Microsoft YaHei UI", size, style);
            oldFont.Dispose();
        }

        private void SetMediaResultsMode(bool active)
        {
            if (active && _qaAnswer.Parent != _mediaPromptPanel)
                _mediaPromptPanel.Controls.Add(_qaAnswer);
            else if (!active && _qaAnswer.Parent != _qaPanel)
                _qaPanel.Controls.Add(_qaAnswer);

            _mediaDialogPanel.Visible = active;
            _mediaPromptPanel.Visible = active;
            _mediaResultsPanel.Visible = active && _mediaResultItems.Count > 0;
            _qaQuestion.Visible = !active;
            _qaAnswer.Visible = true;
            LayoutQAPanel(_qaPanel);
            if (active)
            {
                _mediaPromptPanel.BringToFront();
                _mediaDialogPanel.BringToFront();
                _qaAnswer.BringToFront();
                ConfigureOrb(_settings.InteractionOrbSpritePath, _settings.InteractionOrbFrameCount, true);
                _aiOrb.BringToFront();
            }
        }

        private Panel CreateQACaptionPanel()
        {
            var panel = new BufferedPanel
            {
                Height = 230,
                Dock = DockStyle.Bottom,
                BackColor = Color.Transparent,
                Padding = new Padding(42, 22, 42, 22),
                Visible = false
            };
            panel.Paint += (s, e) => PaintCaptionBackdrop(panel, e.Graphics);

            _qaCaptionQuestion = new Label
            {
                AutoSize = false,
                Font = new Font("Microsoft YaHei UI", 22F, FontStyle.Bold),
                ForeColor = Color.FromArgb(0, 212, 255),
                TextAlign = ContentAlignment.MiddleLeft,
                AutoEllipsis = true,
                BackColor = Color.Transparent
            };
            panel.Controls.Add(_qaCaptionQuestion);

            _qaCaptionAnswer = new Label
            {
                AutoSize = false,
                Font = new Font("Microsoft YaHei UI", 23F, FontStyle.Regular),
                ForeColor = Color.White,
                TextAlign = ContentAlignment.MiddleLeft,
                AutoEllipsis = true,
                BackColor = Color.Transparent
            };
            panel.Controls.Add(_qaCaptionAnswer);
            panel.Resize += (s, e) => LayoutQACaptionPanel(panel);
            LayoutQACaptionPanel(panel);

            return panel;
        }

        private Image? GetQACaptionBackgroundImage()
        {
            if (_qaCaptionBackground != null)
                return _qaCaptionBackground;

            var path = ResolveAppPath(@"Assets\qa-caption-bg.png");
            if (!File.Exists(path))
                return null;

            _qaCaptionBackground = Image.FromFile(path);
            return _qaCaptionBackground;
        }

        private void PaintConfiguredBackgroundSlice(Control overlay, Graphics graphics)
        {
            if (_topPanel == null)
                return;

            var state = graphics.Save();
            try
            {
                graphics.TranslateTransform(-overlay.Left, -overlay.Top);
                PaintConfiguredBackground(_topPanel, graphics);
            }
            finally
            {
                graphics.Restore(state);
            }
        }

        private void PaintCaptionBackdrop(Control overlay, Graphics graphics)
        {
            PaintConfiguredBackgroundSlice(overlay, graphics);
            using var shade = new SolidBrush(Color.FromArgb(CaptionBackdropAlpha, Color.Black));
            graphics.FillRectangle(shade, overlay.ClientRectangle);
        }

        private void LayoutQACaptionPanel(Panel panel)
        {
            if (_qaCaptionQuestion == null || _qaCaptionAnswer == null)
                return;

            var scale = GetUiScale(_topPanel);
            var panelHeight = ScalePx(230, scale);
            if (panel.Height != panelHeight)
                panel.Height = panelHeight;

            var orbReserve = ScalePx(220, scale);
            var left = ScalePx(42, scale) + orbReserve;
            var right = GetAvatarReserve(panel.Width, scale);
            var width = Math.Max(120, panel.Width - left - right);

            _qaCaptionQuestion.Location = new Point(left, ScalePx(20, scale));
            _qaCaptionQuestion.Size = new Size(width, ScalePx(52, scale));
            _qaCaptionAnswer.Location = new Point(left, ScalePx(78, scale));
            _qaCaptionAnswer.Size = new Size(width, Math.Max(ScalePx(72, scale), panel.Height - ScalePx(94, scale)));
            SetScaledFont(_qaCaptionQuestion, 22F, FontStyle.Bold, scale);
            SetScaledFont(_qaCaptionAnswer, 23F, FontStyle.Regular, scale);
        }

        private Panel CreateSpecialFileStripPanel()
        {
            var panel = new Panel
            {
                Height = 260,
                Dock = DockStyle.Bottom,
                BackColor = Color.FromArgb(232, 4, 18, 28),
                Padding = new Padding(24, 14, 24, 18),
                Visible = false
            };
            panel.Paint += (s, e) =>
            {
                using var topPen = new Pen(Color.FromArgb(150, 0, 212, 255), 1);
                e.Graphics.DrawLine(topPen, 0, 0, panel.Width, 0);
            };

            _specialFileStripTitle = new Label
            {
                Dock = DockStyle.Top,
                Height = 36,
                Font = new Font("Microsoft YaHei UI", 15F, FontStyle.Bold),
                ForeColor = Color.FromArgb(120, 246, 255),
                TextAlign = ContentAlignment.MiddleLeft,
                AutoEllipsis = true
            };
            panel.Controls.Add(_specialFileStripTitle);

            _specialFileStripFlow = new FlowLayoutPanel
            {
                Dock = DockStyle.Fill,
                AutoScroll = true,
                WrapContents = false,
                FlowDirection = FlowDirection.LeftToRight,
                BackColor = Color.Transparent,
                Padding = new Padding(0, 8, 0, 0)
            };
            panel.Controls.Add(_specialFileStripFlow);
            _specialFileStripFlow.BringToFront();

            return panel;
        }

        private Panel CreateAdminPanel()
        {
            var panel = new Panel
            {
                Dock = DockStyle.Right,
                Width = 320,
                BackColor = Color.FromArgb(230, 0, 0, 0),
                Padding = new Padding(20)
            };

            var title = new Label
            {
                Text = "\u5ba2\u6237\u7aef\u63a7\u5236",
                Font = new Font("Microsoft YaHei UI", 16F, FontStyle.Bold),
                ForeColor = Color.White,
                Dock = DockStyle.Top,
                Height = 40
            };
            panel.Controls.Add(title);

            var btnPanel = new FlowLayoutPanel
            {
                Dock = DockStyle.Fill,
                FlowDirection = FlowDirection.TopDown,
                WrapContents = false,
                AutoScroll = true
            };
            panel.Controls.Add(btnPanel);

            AddAdminButton(btnPanel, "寰呮満鐢婚潰", (s, e) => ShowIdle());
            AddAdminButton(btnPanel, "\u5168\u5c4f/\u9000\u51fa", (s, e) => ToggleFullscreen());
            AddAdminButton(btnPanel, "娴嬭瘯鍗曞睆瑙嗛", (s, e) => TestVideo());
            AddAdminButton(btnPanel, "娴嬭瘯鍒嗗睆瑙嗛", (s, e) => TestAllZonesVideo());
            AddAdminButton(btnPanel, "娴嬭瘯鍒嗗睆鍥剧墖", (s, e) => TestAllZonesImage());
            AddAdminButton(btnPanel, "娴嬭瘯鎾姤", (s, e) => TestSpeech());
            AddAdminButton(btnPanel, "娴嬭瘯闂瓟", (s, e) => TestQA());
            AddAdminButton(btnPanel, "娴嬭瘯 PPT", (s, e) => TestPPT());
            AddAdminButton(btnPanel, "\u6e05\u7a7a\u6240\u6709\u5206\u5c4f", (s, e) => ShowIdle());
            AddAdminButton(btnPanel, "闈欓煶/鍙栨秷", (s, e) => ToggleMute());

            var info = new Label
            {
                Text = "\u672a\u8fde\u63a5",
                Font = new Font("Microsoft YaHei UI", 10F),
                ForeColor = Color.FromArgb(85, 85, 85),
                Dock = DockStyle.Bottom,
                Height = 30
            };
            panel.Controls.Add(info);
            panel.Tag = info;

            return panel;
        }

        private void AddAdminButton(Control container, string text, EventHandler handler)
        {
            var btn = new Button
            {
                Text = text,
                Font = new Font("Microsoft YaHei UI", 12F),
                Size = new Size(260, 45),
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(34, 34, 34),
                ForeColor = Color.FromArgb(204, 204, 204),
                Margin = new Padding(0, 5, 0, 5),
                Cursor = Cursors.Hand
            };
            btn.FlatAppearance.BorderColor = Color.FromArgb(68, 68, 68);
            btn.Click += handler;
            btn.MouseEnter += (s, e) => btn.BackColor = Color.FromArgb(51, 51, 51);
            btn.MouseLeave += (s, e) => btn.BackColor = Color.FromArgb(34, 34, 34);
            container.Controls.Add(btn);
        }

        private void OnLoaded(object? sender, EventArgs e)
        {
            foreach (var zone in _zones)
                zone.Video.RebindView();
            ShowView("idle");
        }

        private void HandleCommand(Command cmd)
        {
            if (IsDisposed || !IsHandleCreated) return;
            if (cmd.Action == "stop_speech")
            {
                // Audio cancellation must not wait behind an asynchronous PPT command.
                Interlocked.Increment(ref _speechInterruptVersion);
                _commentary.Stop();
                BeginInvoke(new Action(() =>
                {
                    var retained = _pendingCommands.Where(c => !CanStartSpeech(c.Action)).ToArray();
                    var removed = _pendingCommands.Count - retained.Length;
                    _pendingCommands.Clear();
                    foreach (var pending in retained) _pendingCommands.Enqueue(pending);
                    _commentary.Stop();
                    Logger.Info($"[SpeechInterrupt] applied id={cmd.CorrelationId}, cleared={removed}, request={GetParam(cmd, "requestId")}");
                }));
                return;
            }
            BeginInvoke(new Action(() =>
            {
                if (_ppt.IsNavigating && IsPptNavigation(cmd.Action))
                {
                    Logger.Info($"[Control] rejected navigation-busy action={cmd.Action} id={cmd.CorrelationId}; not queued");
                    ShowControlError("PowerPoint 正在翻页，请等当前页显示后再操作；本次重复操作未排队。");
                    return;
                }
                if (_pendingCommands.Count >= 32)
                {
                    Logger.Warn($"[Control] rejected queue-full action={cmd.Action} id={cmd.CorrelationId}");
                    ShowToast("操作过快，队列已满，请等待当前操作完成");
                    return;
                }
                _pendingCommands.Enqueue(cmd);
                DrainCommands();
            }));
        }

        private static bool IsPptNavigation(string action) => action is
            "next_slide" or "ppt_next" or "next_page" or "ppt_next_page" or
            "prev_slide" or "ppt_prev" or "prev_page" or "ppt_prev_page" or
            "goto_slide" or "ppt_goto" or "goto_page";

        private static bool CanStartSpeech(string action) => action is
            "speak" or "show_qa" or "pending_qa" or "show_media_results" or
            "ppt_speak_commentary" or "speak_ppt_commentary" or "play_video" or "show_doc";

        private async void DrainCommands()
        {
            if (_drainingCommands) return;
            _drainingCommands = true;
            try
            {
                while (_pendingCommands.TryDequeue(out var cmd))
                {
                    try { await ProcessCommandAsync(cmd); }
                    catch (Exception ex)
                    {
                        Logger.Error($"[Control] failed action={cmd.Action} id={cmd.CorrelationId}: {ex.Message}");
                        ShowControlError(ex.Message);
                    }
                }
            }
            finally { _drainingCommands = false; }
        }

        private async Task ProcessCommandAsync(Command cmd)
        {
                if (IsSpecialCommand(cmd))
                {
                    Logger.Info($"[WS] 已忽略专场命令: action={cmd.Action}, from={cmd.From}");
                    HideSpecialFileStrip();
                    return;
                }

                Logger.Info($"[WS] 澶勭悊鍛戒护 action={cmd.Action}, file={cmd.File}, slide={cmd.Slide}");

                switch (cmd.Action)
                {
                    case "play_video":
                        PlayVideo(cmd.File, ResolveTargetZone(cmd));
                        break;
                    case "show_doc":
                        ShowDoc(cmd.File, ResolveTargetZone(cmd));
                        break;
                    case "play_ppt":
                        await PlayPPT(cmd.File, GetParam(cmd, "commentaryLevel"));
                        break;
                    case "ppt_speak_commentary":
                    case "speak_ppt_commentary":
                        SpeakCurrentPptCommentary(GetParam(cmd, "commentaryLevel"));
                        break;
                    case "next_slide":
                    case "ppt_next":
                    case "next_page":
                    case "ppt_next_page":
                        if (!_ppt.IsOpened)
                        {
                            throw new InvalidOperationException("PPT 尚未就绪，无法翻页");
                        }
                        HideQaOverlayForPptCommand();
                        _currentPptSlideNumber = await _ppt.NextAsync(cmd.ReceivedTimestamp, cmd.CorrelationId);
                        HidePptNotice();
                        break;
                    case "prev_slide":
                    case "ppt_prev":
                    case "prev_page":
                    case "ppt_prev_page":
                        if (!_ppt.IsOpened)
                        {
                            throw new InvalidOperationException("PPT 尚未就绪，无法翻页");
                        }
                        HideQaOverlayForPptCommand();
                        _currentPptSlideNumber = await _ppt.PrevAsync(cmd.ReceivedTimestamp, cmd.CorrelationId);
                        HidePptNotice();
                        break;
                    case "goto_slide":
                    case "ppt_goto":
                    case "goto_page":
                        if (!_ppt.IsOpened)
                        {
                            throw new InvalidOperationException("PPT 尚未就绪，无法翻页");
                        }
                        HideQaOverlayForPptCommand();
                        if (cmd.Slide.HasValue)
                        {
                            _currentPptSlideNumber = await _ppt.GotoAsync(cmd.Slide.Value, cmd.ReceivedTimestamp, cmd.CorrelationId);
                            HidePptNotice();
                        }
                        break;
                    case "close_ppt":
                    case "ppt_close":
                        await ReturnFromPptAsync(false);
                        break;
                    case "pause":
                        foreach (var zone in _zones) zone.Video.Pause();
                        _commentary.Stop();
                        break;
                    case "resume":
                        foreach (var zone in _zones) zone.Video.Resume();
                        break;
                    case "mute":
                        var muteVal = cmd.Mute ?? (cmd.Params?.ContainsKey("mute") == true &&
                            bool.TryParse(cmd.Params["mute"], out var parsedMute) && parsedMute);
                        foreach (var zone in _zones) zone.Video.IsMuted = muteVal;
                        _commentary.IsMuted = muteVal;
                        Logger.Info($"[WS] 闈欓煶鐘舵€佸凡鏇存柊: mute={muteVal}");
                        break;
                    case "stop_speech":
                        _commentary.Stop();
                        break;
                    case "speak":
                        if (!string.IsNullOrEmpty(cmd.Text))
                        {
                            var preserveView = IsTruthy(GetParam(cmd, "preserveView")) ||
                                               IsTruthy(GetParam(cmd, "testSpeak"));
                            if (preserveView)
                                SpeakWithoutViewChange(cmd.Text);
                            else
                                Speak(cmd.Text);
                        }
                        break;
                    case "avatar_activate":
                        _avatar.Activate();
                        break;
                    case "avatar_deactivate":
                        _commentary.Stop();
                        _avatar.Deactivate();
                        break;
                    case "show_qa":
                        ShowQA(cmd.Question ?? string.Empty, cmd.Answer ?? string.Empty);
                        break;
                    case "show_media_results":
                        ShowMediaResults(
                            cmd.Question ?? GetParam(cmd, "question") ?? string.Empty,
                            cmd.Answer ?? GetParam(cmd, "answer") ?? string.Empty,
                            GetParam(cmd, "items") ?? "[]");
                        break;
                    case "show_special_file_strip":
                        ShowSpecialFileStrip(
                            GetParam(cmd, "specialName") ?? string.Empty,
                            GetParam(cmd, "items") ?? "[]",
                            GetParam(cmd, "selectedIndex"));
                        break;
                    case "special_file_next":
                        MoveSpecialFileSelection(1);
                        break;
                    case "special_file_prev":
                        MoveSpecialFileSelection(-1);
                        break;
                    case "special_file_open_selected":
                        OpenSelectedSpecialFile();
                        break;
                    case "pending_qa":
                        ShowQAPending(cmd.Question ?? GetParam(cmd, "question") ?? string.Empty);
                        break;
                    case "home":
                        await ReturnFromPptAsync(true);
                        break;
                    case "blackout":
                    case "black_screen":
                    case "energy_saving":
                        ShowBlackout();
                        break;
                }
        }

        private static bool IsSpecialCommand(Command cmd)
        {
            if (string.Equals(cmd.From, "auto-restore-special", StringComparison.OrdinalIgnoreCase))
                return true;

            return cmd.Action is "show_special_file_strip"
                or "special_file_next"
                or "special_file_prev"
                or "special_file_open_selected";
        }

        private void ConnectAndStart()
        {
            Logger.Info($"[Startup] 瀹㈡埛绔惎鍔紝鍑嗗杩炴帴 WebSocket={_settings.WebSocketUrl}锛屾枃浠舵湇鍔?{_settings.FileServerUrl}锛屽獟浣撶洰褰?{_settings.MediaPath}");
            Logger.Info($"[Display] 鎬诲垎杈ㄧ巼={_settings.DisplayWidth}x{_settings.DisplayHeight}锛屽垎灞忓垎杈ㄧ巼={_settings.SegmentWidth}x{_settings.SegmentHeight}锛屽尯鍩熸暟={_zones.Count}");
            ShowToast("姝ｅ湪杩炴帴鏈嶅姟鍣?..");
            _ = ConnectAndStartAsync();
        }

        private async System.Threading.Tasks.Task ConnectAndStartAsync()
        {
            try
            {
                await _ws.ConnectAsync();
            }
            catch
            {
                Logger.Warn($"[Startup] 棣栨杩炴帴娴佺▼鎶涘嚭寮傚父锛屽鎴风灏嗗湪 3 绉掑悗閲嶈瘯: ws={_settings.WebSocketUrl}");
                ShowToast("杩炴帴澶辫触锛?绉掑悗閲嶈瘯...");
                await System.Threading.Tasks.Task.Delay(3000);
                ConnectAndStart();
            }
        }

        private void OnDeviceRegistered(DeviceInfo device)
        {
            BeginInvoke(new Action(() =>
            {
                _screenNumber = device.ScreenNumber;
                UpdateIdleHint();
                ShowToast($"\u5df2\u8fde\u63a5 {_screenNumber}\u53f7\u5c4f");
                _ = _commentary.LoadCommentaryAsync();
                _ = _sync.SyncAllAsync();
            }));
        }

        private void OnScreenSettings(ScreenSettings settings)
        {
            BeginInvoke(new Action(() =>
            {
                _settings.ScreenName = settings.Name ?? "";
                _settings.SnapshotIntervalSeconds = NormalizeInterval(settings.SnapshotIntervalSeconds, 5);
                _settings.StatusIntervalSeconds = NormalizeInterval(settings.StatusIntervalSeconds, 1);

                if (settings.TransparentBackground.HasValue)
                    _settings.TransparentBackground = settings.TransparentBackground.Value;

                if (string.Equals(settings.BackgroundFit, "cover", StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(settings.BackgroundFit, "contain", StringComparison.OrdinalIgnoreCase))
                {
                    _settings.BackgroundFit = NormalizeBackgroundFit(settings.BackgroundFit);
                }

                _settings.Save();
                ClearBackgroundRenderCache();
                ApplyShellChrome();
                RefreshBackgroundSurfaces();

                Text = string.IsNullOrWhiteSpace(_settings.ScreenName)
                    ? "\u601d\u5fb7\u79d1\u6280\u5c55\u5385"
                    : _settings.ScreenName;

                _statusUpdateTimer?.Change(0, ToTimerPeriod(_settings.StatusIntervalSeconds, 1));
                _snapshotTimer?.Change(ToTimerPeriod(_settings.SnapshotIntervalSeconds, 5), ToTimerPeriod(_settings.SnapshotIntervalSeconds, 5));
                ShowToast("\u5c4f\u5e55\u8bbe\u7f6e\u5df2\u540c\u6b65");
                Logger.Info($"[Settings] 已同步屏幕设置: name={_settings.ScreenName}, snapshot={_settings.SnapshotIntervalSeconds}s, status={_settings.StatusIntervalSeconds}s");
                Logger.Info($"[Settings] 已同步背景设置: transparent={_settings.TransparentBackground.ToString().ToLowerInvariant()}, fit={NormalizeBackgroundFit(_settings.BackgroundFit)}");
            }));
        }

        private void RefreshBackgroundSurfaces()
        {
            var backColor = GetShellBackColor();
            foreach (var panel in new[] { _topPanel, _idlePanel, _qaPanel, _speechPanel })
            {
                if (panel == null || panel.IsDisposed)
                    continue;

                panel.BackColor = backColor;
                panel.Invalidate(true);
                if (panel.Visible)
                    panel.Update();
            }

            if (_aiOrb?.Visible == true)
            {
                _aiOrb.UseBinaryAlpha = _settings.TransparentBackground;
                _aiOrb.RefreshVisualCache();
                _aiOrb.Invalidate();
            }
        }

        private void OnWSConnected()
        {
            BeginInvoke(new Action(() => UpdateAdminInfo("\u5df2\u8fde\u63a5")));
        }

        private void OnWSDisconnected()
        {
            BeginInvoke(new Action(() =>
            {
                Logger.Warn($"[Startup] WebSocket 宸叉柇寮€: {_settings.WebSocketUrl}");
                UpdateAdminInfo("鏂紑杩炴帴");
            }));
        }

        private void OnSpeechFinished()
        {
            BeginInvoke(new Action(() =>
            {
                if (_currentView == "speech")
                    ShowIdle();
            }));
        }

        private void OnPPTClosed()
        {
            BeginInvoke(new Action(() =>
            {
                _currentPptFileName = string.Empty;
                _currentPptSlideNumber = 1;
                if (_switchingToPpt)
                    return;

                ShowIdle();
            }));
        }

        private void OnZoneVideoEnded(int zoneIndex)
        {
            BeginInvoke(new Action(() =>
            {
                Logger.Info($"[Video] 鍒嗗睆 {zoneIndex + 1} 鎾斁缁撴潫");
                _zones[zoneIndex].Reset();
                if (_zones.All(z => !z.HasContent))
                    ShowIdle();
            }));
        }

        private void OnZoneVideoError(int zoneIndex)
        {
            BeginInvoke(new Action(() =>
            {
                Logger.Warn($"[Video] 鍒嗗睆 {zoneIndex + 1} 鎾斁澶辫触");
                _zones[zoneIndex].Reset();
                ShowToast($"\u7b2c {zoneIndex + 1} \u5757\u5c4f\u5e55\u89c6\u9891\u64ad\u653e\u5931\u8d25");
                if (_zones.All(z => !z.HasContent))
                    ShowIdle();
            }));
        }

        private Form? _pptNotice;

        private void HidePptNotice()
        {
            _pptNotice?.Dispose();
            _pptNotice = null;
        }

        private void ShowControlError(string message)
        {
            if (_currentView != "ppt") { ShowToast(message); return; }
            HidePptNotice();
            var bounds = Screen.FromControl(this).Bounds;
            var notice = new PptNoticeForm
            {
                FormBorderStyle = FormBorderStyle.None, ShowInTaskbar = false, TopMost = true,
                StartPosition = FormStartPosition.Manual,
                Bounds = new Rectangle(bounds.Left + 20, bounds.Top + 20, Math.Min(760, bounds.Width - 40), 76),
                BackColor = Color.FromArgb(255, 239, 185)
            };
            notice.Controls.Add(new Label
            {
                Dock = DockStyle.Fill, Padding = new Padding(12), Text = message,
                Font = new Font("Microsoft YaHei UI", 12F), ForeColor = Color.FromArgb(45, 35, 10),
                TextAlign = ContentAlignment.MiddleLeft
            });
            _pptNotice = notice;
            notice.Show();
            Logger.Info("[Display] PPT error notice shown; slideshow remains visible");
        }

        private sealed class PptNoticeForm : Form
        {
            protected override bool ShowWithoutActivation => true;
            protected override CreateParams CreateParams
            {
                get { var value = base.CreateParams; value.ExStyle |= 0x08000000 | 0x00000080; return value; }
            }
        }

        private void ShowView(string view)
        {
            HidePptNotice();
            var started = System.Diagnostics.Stopwatch.GetTimestamp();
            var previous = _currentView;
            _aiOrb?.Stop();
            _topPanel.SuspendLayout();
            try
            {
                Control next = view switch
                {
                    "idle" => _idlePanel, "blackout" => _blackoutPanel,
                    "media" => _zoneLayout, "speech" => _speechPanel, "qa" => _qaPanel,
                    _ => throw new ArgumentOutOfRangeException(nameof(view))
                };
                _currentView = view;
                next.Visible = true;
                next.BringToFront();
                foreach (var surface in new Control[] { _idlePanel, _blackoutPanel, _zoneLayout, _speechPanel, _qaPanel })
                    if (surface != next) surface.Visible = false;
                _statusBar.Visible = false;
                ApplyWindowModeForView(view);
                ApplyOrbForView(view);
            }
            finally { _topPanel.ResumeLayout(true); }
            _topPanel.Invalidate(true);
            _topPanel.Update();
            Logger.Info($"[Display] painted from={previous} to={view} elapsedMs={System.Diagnostics.Stopwatch.GetElapsedTime(started).TotalMilliseconds:F1}");
        }

        private void ApplyWindowModeForView(string view)
        {
            if (!_settings.NonIntrusiveMode)
            {
                EnterPresentationMode();
                return;
            }

            switch (view)
            {
                case "idle":
                case "blackout":
                    EnterHiddenMode();
                    break;
                case "speech":
                case "qa":
                    EnterPopupMode(980, 620, false);
                    break;
                case "media":
                    EnterPresentationMode();
                    break;
            }
        }

        private void EnterHiddenMode()
        {
            if (_isAdminVisible)
                return;

            ApplyPopupChrome(false);
            TopMost = false;
            _isFullscreen = false;
            WindowState = FormWindowState.Normal;
            FormBorderStyle = FormBorderStyle.None;
            Opacity = 0;

            if (Visible)
                Hide();
        }

        private void EnterPopupMode(int width, int height, bool alignBottom)
        {
            var screen = Screen.FromControl(this);
            var area = screen.WorkingArea;
            var actualWidth = Math.Min(width, Math.Max(360, area.Width - 80));
            var actualHeight = Math.Min(height, Math.Max(260, area.Height - 80));

            _isFullscreen = false;
            FormBorderStyle = FormBorderStyle.None;
            MaximizeBox = false;
            MinimizeBox = true;
            WindowState = FormWindowState.Normal;
            Size = new Size(actualWidth, actualHeight);

            var x = area.Left + (area.Width - actualWidth) / 2;
            var y = alignBottom
                ? area.Bottom - actualHeight - 36
                : area.Top + (area.Height - actualHeight) / 2;
            Location = new Point(Math.Max(area.Left, x), Math.Max(area.Top, y));

            Opacity = 1;
            ShowInTaskbar = false;
            if (!Visible)
                Show();
            ApplyPopupChrome(true);
            TopMost = false;
        }

        private void EnterPresentationMode()
        {
            var screen = Screen.FromControl(this);
            ApplyPopupChrome(false);
            ApplyShellChrome();
            _isFullscreen = true;
            // Borderless full-screen bounds avoid Normal/Maximized churn on every command.
            if (FormBorderStyle != FormBorderStyle.None) FormBorderStyle = FormBorderStyle.None;
            if (WindowState != FormWindowState.Normal) WindowState = FormWindowState.Normal;
            if (Bounds != screen.Bounds) Bounds = screen.Bounds;
            ShowInTaskbar = !_settings.NonIntrusiveMode;
            if (Opacity != 1) Opacity = 1;
            if (!Visible) Show();
            TopMost = false;
        }

        private void ApplyShellChrome()
        {
            var backColor = GetShellBackColor();
            BackColor = backColor;
            TransparencyKey = _settings.TransparentBackground ? TransparentShellColor : Color.Empty;
            if (_topPanel != null)
                _topPanel.BackColor = backColor;
            if (_idlePanel != null)
                _idlePanel.BackColor = backColor;
            if (_qaPanel != null)
                _qaPanel.BackColor = backColor;
            if (_speechPanel != null)
                _speechPanel.BackColor = backColor;
        }

        private void EnterPptMode()
        {
            HidePptNotice();
            // Called only after Office confirms the slideshow is ready.
            _currentView = "ppt";
            _aiOrb?.Stop();
            ApplyPopupChrome(false);
            TopMost = false;
            if (Visible) Hide();
            Logger.Info("[Display] PPT ready; obscured orb animation paused");
        }

        private void ApplyPopupChrome(bool visible)
        {
            if (_popupCloseButton == null)
                return;

            _popupCloseButton.Visible = visible;
            if (visible)
            {
                using var path = CreateRoundedRectanglePath(ClientRectangle, 16);
                Region = new Region(path);
                PositionPopupCloseButton();
                _popupCloseButton.BringToFront();
            }
            else
            {
                Region = null;
            }
        }

        private void PositionPopupCloseButton()
        {
            if (_popupCloseButton == null)
                return;

            _popupCloseButton.Location = new Point(Math.Max(0, ClientSize.Width - _popupCloseButton.Width - 14), 12);
        }

        private void StartPopupDrag(object? sender, MouseEventArgs e)
        {
            if (!_settings.NonIntrusiveMode || _currentView == "media" || e.Button != MouseButtons.Left)
                return;

            ReleaseCapture();
            SendMessage(Handle, WM_NCLBUTTONDOWN, HTCAPTION, 0);
        }

        private void ApplyOrbForView(string view)
        {
            if (_aiOrb == null)
                return;

            switch (view)
            {
                case "idle":
                    ConfigureOrb(_settings.IdleOrbSpritePath, _settings.IdleOrbFrameCount, true);
                    break;
                case "speech":
                case "qa":
                    ConfigureOrb(_settings.InteractionOrbSpritePath, _settings.InteractionOrbFrameCount, true);
                    break;
                default:
                    _aiOrb.Stop();
                    _aiOrb.Visible = false;
                    break;
            }
        }

        private void ConfigureOrb(string spritePath, int frameCount, bool visible)
        {
            if (!visible)
            {
                _aiOrb.Stop();
                _aiOrb.Visible = false;
                return;
            }

            var resolved = ResolveAppPath(spritePath);
            _aiOrb.Configure(resolved, Math.Max(1, _settings.OrbFps), frameCount);
            _aiOrb.Visible = true;
            PositionOrb();
            _aiOrb.BringToFront();
            _aiOrb.Start();
        }

        private void PositionOrb()
        {
            if (_aiOrb == null || _topPanel == null)
                return;

            var baseSize = Math.Max(120, _settings.OrbSize);
            var scaleSize = Math.Clamp(Math.Min(_topPanel.Width, _topPanel.Height) / 7, 180, 360);
            var displayScale = Math.Min(_topPanel.Width / 3840F, _topPanel.Height / 2160F);
            var size = _settings.OrbSize > 0
                ? Math.Clamp((int)Math.Round(baseSize * displayScale), 96, 520)
                : scaleSize;
            var margin = Math.Max(32, size / 6);
            _aiOrb.UseBinaryAlpha = _settings.TransparentBackground;
            if (_qaCaptionPanel?.Visible == true)
            {
                var scale = GetUiScale(_topPanel);
                var panelTop = _topPanel.Height - _qaCaptionPanel.Height;
                var qaSize = Math.Min(ScalePx(180, scale), _qaCaptionPanel.Height - ScalePx(34, scale));
                _aiOrb.Size = new Size(qaSize, qaSize);
                _aiOrb.Location = new Point(ScalePx(42, scale), panelTop + Math.Max(ScalePx(12, scale), (_qaCaptionPanel.Height - qaSize) / 2));
                _aiOrb.RefreshVisualCache();
                return;
            }

            if (_mediaPromptPanel?.Visible == true)
            {
                var scale = GetUiScale(_topPanel);
                var panelTop = _topPanel.Height - _mediaPromptPanel.Height;
                var promptSize = Math.Min(ScalePx(90, scale), _mediaPromptPanel.Height - ScalePx(16, scale));
                _aiOrb.Size = new Size(promptSize, promptSize);
                _aiOrb.Location = new Point(ScalePx(22, scale), panelTop + Math.Max(ScalePx(5, scale), (_mediaPromptPanel.Height - promptSize) / 2));
                _aiOrb.RefreshVisualCache();
                return;
            }

            _aiOrb.Location = new Point(margin, Math.Max(margin, _topPanel.Height - size - margin));
            _aiOrb.Size = new Size(size, size);
            _aiOrb.RefreshVisualCache();
        }

        private void PaintOrbBackground(Graphics g, Rectangle bounds)
        {
            if (_topPanel == null)
                return;

            var state = g.Save();
            try
            {
                g.TranslateTransform(-_aiOrb.Left, -_aiOrb.Top);
                var bottomPanel = _mediaPromptPanel?.Visible == true
                    ? _mediaPromptPanel
                    : _qaCaptionPanel?.Visible == true
                        ? _qaCaptionPanel
                        : null;
                if (bottomPanel != null)
                {
                    PaintConfiguredBackground(_topPanel, g);
                    var destination = new Rectangle(0, _topPanel.Height - bottomPanel.Height, _topPanel.Width, bottomPanel.Height);
                    using var shade = new SolidBrush(Color.FromArgb(CaptionBackdropAlpha, Color.Black));
                    g.FillRectangle(shade, destination);
                    return;
                }

                if (_currentView == "idle")
                    PaintTechIdleBackground(_topPanel, g);
                else
                    PaintConfiguredBackground(_topPanel, g);
            }
            finally
            {
                g.Restore(state);
            }
        }

        private void ShowIdle()
        {
            StopQAAutoCloseTimer();
            StopThinkingTimer();
            HideQACaption();
            HideSpecialFileStrip();
            ClearMediaResults();
            foreach (var zone in _zones)
                zone.Reset();

            _commentary.Stop();
            ClosePptWithoutIdle();
            ShowView("idle");
        }

        private void ShowBlackout()
        {
            StopQAAutoCloseTimer();
            StopThinkingTimer();
            HideQACaption();
            HideSpecialFileStrip();
            ClearMediaResults();
            foreach (var zone in _zones)
                zone.Reset();

            _commentary.Stop();
            ClosePptWithoutIdle();
            _toast.Visible = false;
            ShowView("blackout");
        }

        private async void PlayVideo(string? fileName, int zoneIndex)
        {
            if (string.IsNullOrEmpty(fileName)) return;
            var speechVersion = Volatile.Read(ref _speechInterruptVersion);

            StopQAAutoCloseTimer();
            StopThinkingTimer();
            HideQACaption();
            var zone = GetZone(zoneIndex);
            if (zone == null) return;

            fileName = await EnsureMediaFileAsync(fileName);
            zone.ShowVideo(fileName);
            ShowView("media");
            ClosePptWithoutIdle();

            var cleanName = Path.GetFileName(fileName).Replace(" ", string.Empty);
            if (speechVersion == Volatile.Read(ref _speechInterruptVersion)) _commentary.SpeakCommentary(cleanName);
        }

        private async void ShowDoc(string? fileName, int zoneIndex)
        {
            if (string.IsNullOrEmpty(fileName)) return;
            var speechVersion = Volatile.Read(ref _speechInterruptVersion);

            StopQAAutoCloseTimer();
            StopThinkingTimer();
            HideQACaption();
            var zone = GetZone(zoneIndex);
            if (zone == null) return;

            fileName = await EnsureMediaFileAsync(fileName);
            zone.ShowImage(fileName);
            ShowView("media");
            ClosePptWithoutIdle();

            var cleanName = Path.GetFileName(fileName).Replace(" ", string.Empty);
            if (speechVersion == Volatile.Read(ref _speechInterruptVersion)) _commentary.SpeakCommentary(cleanName);
        }

        private void Speak(string text)
        {
            StopQAAutoCloseTimer();
            StopThinkingTimer();
            HideQACaption();
            foreach (var zone in _zones)
                zone.HideMedia();

            ClosePptWithoutIdle();
            ShowView("speech");
            _speechText.Text = text;
            _commentary.Speak(text);
        }

        private void SpeakWithoutViewChange(string text)
        {
            StopQAAutoCloseTimer();
            StopThinkingTimer();
            HideQACaption();
            _commentary.Speak(text);
        }

        private void ShowQA(string question, string answer)
        {
            _commentary.Stop();
            StopThinkingTimer();
            HideSpecialFileStrip();
            var cleanQuestion = CleanDisplayText(question);
            var cleanAnswer = CleanDisplayText(answer);
            Logger.Info($"[QA] ShowQA questionLength={cleanQuestion.Length}, answerLength={cleanAnswer.Length}, muted={_commentary.IsMuted}");
            if (_settings.NonIntrusiveMode)
            {
                HideQACaption();
                SetMediaResultsMode(false);
                foreach (var zone in _zones)
                    zone.HideMedia();
                ClosePptWithoutIdle();
                ShowView("qa");
                _qaQuestion.Text = "\u95ee\uff1a" + cleanQuestion;
                _qaAnswer.Text = cleanAnswer;
            }
            else
            {
                ShowQACaption(cleanQuestion, cleanAnswer);
            }
            ClearMediaResults();
            if (!string.IsNullOrWhiteSpace(cleanAnswer))
            {
                _commentary.IsMuted = false;
                _commentary.Speak(cleanAnswer);
            }
            else
            {
                Logger.Warn("[QA] Answer is empty, skip speech");
            }
            StartQAAutoCloseTimer();
        }

        private void ShowMediaResults(string question, string answer, string itemsJson)
        {
            var started = System.Diagnostics.Stopwatch.GetTimestamp();
            StopQAAutoCloseTimer();
            HideQACaption();
            HideSpecialFileStrip();
            foreach (var zone in _zones)
                zone.HideMedia();

            ClosePptWithoutIdle();
            _commentary.Stop();
            StopThinkingTimer();

            _qaPanel.SuspendLayout();
            _mediaDialogPanel.SuspendLayout();
            _mediaResultsPanel.SuspendLayout();

            string selectionPrompt;
            var cleanAnswer = CleanDisplayText(answer);
            try
            {
                _qaQuestion.Text = "\u95ee\uff1a" + CleanDisplayText(question);
                RenderMediaResults(itemsJson);
                selectionPrompt = _mediaResultItems.Count > 0
                    ? $"我找到了 {_mediaResultItems.Count} 个相关文件，请说出序号选择"
                    : cleanAnswer;
                _qaAnswer.Text = selectionPrompt;
                SetMediaResultsMode(_mediaResultItems.Count > 0);
            }
            finally
            {
                _mediaResultsPanel.ResumeLayout(true);
                _mediaDialogPanel.ResumeLayout(true);
                _qaPanel.ResumeLayout(true);
                LayoutQAPanel(_qaPanel);
            }
            ShowView("qa");
            Logger.Info($"[MediaList] ready count={_mediaResultItems.Count} elapsedMs={System.Diagnostics.Stopwatch.GetElapsedTime(started).TotalMilliseconds:F1}");

            Logger.Info($"[QA] ShowMediaResults questionLength={CleanDisplayText(question).Length}, answerLength={cleanAnswer.Length}, muted={_commentary.IsMuted}");
            if (!string.IsNullOrWhiteSpace(selectionPrompt))
            {
                _commentary.IsMuted = false;
                _commentary.Speak(selectionPrompt);
            }
            else
            {
                Logger.Warn("[QA] Media result answer is empty, skip speech");
            }
        }

        private void ShowQAPending(string question)
        {
            StopQAAutoCloseTimer();
            _commentary.Stop();
            HideSpecialFileStrip();

            var cleanQuestion = CleanDisplayText(question);
            if (_settings.NonIntrusiveMode)
            {
                HideQACaption();
                SetMediaResultsMode(false);
                foreach (var zone in _zones)
                    zone.HideMedia();
                ClosePptWithoutIdle();
                ShowView("qa");
                _qaQuestion.Text = "\u95ee\uff1a" + cleanQuestion;
                _qaAnswer.Text = "\u601d\u8003\u4e2d";
            }
            else
            {
                ShowQACaption(cleanQuestion, "\u601d\u8003\u4e2d");
            }
            _thinkingDots = 0;
            ClearMediaResults();

            if (_thinkingTimer == null)
            {
                _thinkingTimer = new System.Windows.Forms.Timer { Interval = 420 };
                _thinkingTimer.Tick += (s, e) =>
                {
                    _thinkingDots = (_thinkingDots + 1) % 4;
                    SetThinkingText("\u601d\u8003\u4e2d" + new string('.', _thinkingDots));
                };
            }
            _thinkingTimer.Start();
        }

        private void RenderMediaResults(string itemsJson)
        {
            ClearMediaResults();

            List<MediaResultCard>? items = null;
            try
            {
                items = JsonSerializer.Deserialize<List<MediaResultCard>>(itemsJson,
                    new JsonSerializerOptions { PropertyNameCaseInsensitive = true });
            }
            catch (Exception ex)
            {
                Logger.Warn($"[QA] 缩略图列表解析失败: {ex.Message}");
            }

            if (items == null || items.Count == 0)
                return;

            _mediaResultItems.AddRange(items.Take(MaxVisibleMediaResults));
            _mediaResultSelectedIndex = 0;
            _mediaResultsPanel.SuspendLayout();
            try
            {
                foreach (var item in _mediaResultItems)
                {
                    var card = CreateMediaCard(item);
                    _mediaResultCards.Add((Panel)card);
                    _mediaResultsPanel.Controls.Add(card);
                }
            }
            finally
            {
                _mediaResultsPanel.ResumeLayout(true);
            }
            LayoutMediaResultCards(GetUiScale(_qaPanel));
            _mediaResultsPanel.PerformLayout();
            UpdateMediaResultSelection();
            BeginLoadMediaThumbnails();
        }

        private static void SetControlRedraw(Control control, bool enabled)
        {
            if (!control.IsHandleCreated)
                return;

            SendMessage(control.Handle, WM_SETREDRAW, enabled ? 1 : 0, 0);
        }

        private void ShowSpecialFileStrip(string specialName, string itemsJson, string? selectedIndexText)
        {
            List<MediaResultCard>? items = null;
            try
            {
                items = JsonSerializer.Deserialize<List<MediaResultCard>>(itemsJson,
                    new JsonSerializerOptions { PropertyNameCaseInsensitive = true });
            }
            catch (Exception ex)
            {
                Logger.Warn($"[SpecialFiles] 列表解析失败: {ex.Message}");
            }

            ClearSpecialFileStrip();
            ClearMediaResults();
            if (items == null || items.Count == 0)
                return;

            _specialFileItems.AddRange(items);
            _specialFileSelectedIndex = 0;
            if (int.TryParse(selectedIndexText, out var parsed))
                _specialFileSelectedIndex = Math.Max(0, Math.Min(parsed, _specialFileItems.Count - 1));

            _specialFileStripTitle.Text = string.IsNullOrWhiteSpace(specialName)
                ? "专场文件"
                : $"{specialName} · 专场文件";

            foreach (var item in _specialFileItems)
            {
                var card = CreateSpecialFileCard(item);
                _specialFileCards.Add(card);
                _specialFileStripFlow.Controls.Add(card);
            }

            _specialFileStripPanel.Visible = true;
            _specialFileStripPanel.BringToFront();
            if (_settings.NonIntrusiveMode)
            {
                _currentView = "qa";
                _idlePanel.Visible = false;
                _blackoutPanel.Visible = false;
                _zoneLayout.Visible = false;
                _speechPanel.Visible = false;
                _qaPanel.Visible = false;
                _statusBar.Visible = false;
                EnterPopupMode(1120, 340, true);
            }
            else
            {
                _currentView = "qa";
                EnterPresentationMode();
                ConfigureOrb(_settings.InteractionOrbSpritePath, _settings.InteractionOrbFrameCount, true);
            }
            UpdateSpecialFileSelection();
            ShowToast("左右滑动选择，双击打开");
        }

        private Panel CreateSpecialFileCard(MediaResultCard item)
        {
            var card = new Panel
            {
                Width = 250,
                Height = 182,
                Margin = new Padding(0, 0, 16, 0),
                BackColor = Color.FromArgb(42, 0, 212, 255),
                Padding = new Padding(8),
                Tag = item
            };
            card.Paint += (s, e) =>
            {
                var selected = _specialFileCards.IndexOf(card) == _specialFileSelectedIndex;
                using var pen = new Pen(selected ? Color.FromArgb(44, 255, 221) : Color.FromArgb(120, 0, 212, 255), selected ? 4 : 1);
                e.Graphics.DrawRectangle(pen, 1, 1, card.Width - 3, card.Height - 3);
            };

            var thumb = new PictureBox
            {
                Location = new Point(8, 8),
                Size = new Size(234, 112),
                BackColor = Color.FromArgb(8, 26, 34),
                SizeMode = PictureBoxSizeMode.Zoom
            };
            card.Controls.Add(thumb);


            var badge = new Label
            {
                Text = item.Index.ToString(),
                Location = new Point(16, 16),
                Size = BadgeSize(item.Index, 34, 18),
                Font = BadgeFont(item.Index, 13F),
                ForeColor = Color.Black,
                BackColor = Color.FromArgb(44, 255, 221),
                TextAlign = ContentAlignment.MiddleCenter
            };
            card.Controls.Add(badge);
            badge.BringToFront();

            if (item.HasCommentary)
            {
                var commentaryBadge = new Label
                {
                    Text = "讲解",
                    Location = new Point(card.Width - 62, 150),
                    Size = new Size(46, 26),
                    Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Bold),
                    ForeColor = Color.FromArgb(8, 26, 34),
                    BackColor = Color.FromArgb(255, 221, 92),
                    TextAlign = ContentAlignment.MiddleCenter
                };
                card.Controls.Add(commentaryBadge);
                commentaryBadge.BringToFront();
            }

            var title = new Label
            {
                Text = CleanDisplayText(item.Title ?? item.Name ?? string.Empty),
                Location = new Point(8, 126),
                Size = new Size(234, 30),
                Font = new Font("Microsoft YaHei UI", 12F, FontStyle.Bold),
                ForeColor = Color.White,
                TextAlign = ContentAlignment.MiddleLeft,
                AutoEllipsis = true
            };
            card.Controls.Add(title);

            var type = new Label
            {
                Text = TypeLabel(item.Type),
                Location = new Point(8, 156),
                Size = new Size(234, 20),
                Font = new Font("Microsoft YaHei UI", 9F),
                ForeColor = Color.FromArgb(148, 238, 255),
                TextAlign = ContentAlignment.MiddleCenter
            };
            card.Controls.Add(type);

            return card;
        }

        private void MoveSpecialFileSelection(int delta)
        {
            if (_specialFileStripPanel.Visible && _specialFileItems.Count > 0)
            {
                _specialFileSelectedIndex = (_specialFileSelectedIndex + delta + _specialFileItems.Count) % _specialFileItems.Count;
                UpdateSpecialFileSelection();
                return;
            }

            if (_mediaResultsPanel.Visible && _mediaResultItems.Count > 0)
            {
                _mediaResultSelectedIndex = (_mediaResultSelectedIndex + delta + _mediaResultItems.Count) % _mediaResultItems.Count;
                UpdateMediaResultSelection();
            }
        }

        private void UpdateSpecialFileSelection()
        {
            for (int i = 0; i < _specialFileCards.Count; i++)
                _specialFileCards[i].Invalidate();

            if (_specialFileSelectedIndex >= 0 && _specialFileSelectedIndex < _specialFileCards.Count)
                _specialFileStripFlow.ScrollControlIntoView(_specialFileCards[_specialFileSelectedIndex]);
        }

        private void UpdateMediaResultSelection()
        {
            for (int i = 0; i < _mediaResultCards.Count; i++)
                _mediaResultCards[i].Invalidate();

            if (_mediaResultSelectedIndex >= 0 && _mediaResultSelectedIndex < _mediaResultCards.Count)
                _mediaResultsPanel.ScrollControlIntoView(_mediaResultCards[_mediaResultSelectedIndex]);
        }

        private void OpenSelectedSpecialFile()
        {
            if (_specialFileStripPanel.Visible && _specialFileSelectedIndex >= 0 && _specialFileSelectedIndex < _specialFileItems.Count)
            {
                OpenMediaResultItem(_specialFileItems[_specialFileSelectedIndex]);
                HideSpecialFileStrip();
                return;
            }

            if (_mediaResultsPanel.Visible && _mediaResultSelectedIndex >= 0 && _mediaResultSelectedIndex < _mediaResultItems.Count)
                OpenMediaResultItem(_mediaResultItems[_mediaResultSelectedIndex]);
        }

        private void OpenMediaResultItem(MediaResultCard item)
        {
            var file = item.LocalPath ?? item.Name ?? string.Empty;

            if (string.Equals(item.Type, "ppt", StringComparison.OrdinalIgnoreCase))
                _ = PlayPPT(file, item.CommentaryLevel);
            else if (string.Equals(item.Type, "image", StringComparison.OrdinalIgnoreCase))
                ShowDoc(file, 0);
            else
                PlayVideo(file, 0);
        }

        private void ClearSpecialFileStrip()
        {
            if (_specialFileStripFlow == null)
                return;

            foreach (Control control in _specialFileStripFlow.Controls)
                control.Dispose();
            _specialFileStripFlow.Controls.Clear();
            _specialFileItems.Clear();
            _specialFileCards.Clear();
            _specialFileSelectedIndex = -1;
        }

        private void HideSpecialFileStrip()
        {
            if (_specialFileStripPanel != null)
                _specialFileStripPanel.Visible = false;
        }

        private static string TypeLabel(string? type)
        {
            return string.Equals(type, "ppt", StringComparison.OrdinalIgnoreCase)
                ? "PPT"
                : string.Equals(type, "image", StringComparison.OrdinalIgnoreCase)
                    ? "\u56fe\u7247"
                    : "\u89c6\u9891";
        }

        private void ShowQACaption(string question, string answer)
        {
            _qaCaptionQuestion.Text = "\u95ee\uff1a" + question;
            _qaCaptionAnswer.Text = answer;
            if (_currentView == "ppt" && !Visible)
                EnterPopupMode(980, 240, true);
            _qaCaptionPanel.Visible = true;
            LayoutQACaptionPanel(_qaCaptionPanel);
            _qaCaptionPanel.BringToFront();
            ConfigureOrb(_settings.InteractionOrbSpritePath, _settings.InteractionOrbFrameCount, true);
            _aiOrb.BringToFront();
            if (_currentView != "ppt") RestorePresentationForeground();
        }

        private void RestorePresentationForeground()
        {
            if (IsDisposed || !IsHandleCreated || !Visible) return;
            PlaceResponseWindow(this, true);
        }

        private static void PlaceResponseWindow(Form window, bool activate)
        {
            // Keep only the visible client response above Unity and below the avatar.
            // EnterPptMode/EnterPopupMode clear TopMost before yielding to PowerPoint.
            using var current = System.Diagnostics.Process.GetCurrentProcess();
            var avatarWindows = new HashSet<IntPtr>();
            foreach (var avatar in System.Diagnostics.Process.GetProcessesByName("有灵屏型智能体"))
            {
                using (avatar)
                {
                    try
                    {
                        if (avatar.SessionId == current.SessionId && avatar.MainWindowHandle != IntPtr.Zero)
                            avatarWindows.Add(avatar.MainWindowHandle);
                    }
                    catch (InvalidOperationException) { }
                }
            }
            if (activate) NativeShowWindow(window.Handle, 9);
            if (avatarWindows.Count == 1)
            {
                window.TopMost = true;
                NativeSetWindowPos(window.Handle, avatarWindows.Single(), 0, 0, 0, 0, 0x213);
            }
            else
                NativeSetWindowPos(window.Handle, IntPtr.Zero, 0, 0, 0, 0, 0x213);
            if (!activate) return;
            var restored = NativeSetForegroundWindow(window.Handle);
            Logger.Info($"[Display] requested foreground for explicit response success={restored}");
        }

        private async Task ReturnFromPptAsync(bool home)
        {
            Form cover = null;
            Bitmap frame = null;
            try
            {
                if (_currentView == "ppt" && _ppt.HasActivePowerPoint)
                {
                    // Keep the currently displayed frame until the real destination is
                    // painted. Closing Office first would briefly expose Unity underneath.
                    try
                    {
                        var bounds = Screen.FromControl(this).Bounds;
                        frame = new Bitmap(bounds.Width, bounds.Height);
                        using (var graphics = Graphics.FromImage(frame))
                            graphics.CopyFromScreen(bounds.Location, Point.Empty, bounds.Size);
                        cover = new PptReturnCoverForm(bounds, frame);
                        cover.Show();
                        // The captured frame already includes the avatar. Cover the live
                        // frame briefly too, otherwise its transparent moving silhouette
                        // would be composited over the captured silhouette a second time.
                        NativeSetWindowPos(cover.Handle, new IntPtr(-1), 0, 0, 0, 0, 0x213);
                        cover.Update();
                        Logger.Info("[Display] return-cover painted before PPT close");
                    }
                    catch (Exception ex)
                    {
                        cover?.Dispose(); cover = null;
                        Logger.Warn($"[Display] return-cover unavailable: {ex.Message}");
                    }
                }
                await _ppt.CloseAsync();
                if (home) ShowIdle(); else ClosePptAndRestoreContext();
                RestorePresentationForeground();
                Update();
                Logger.Info("[Display] return destination painted before cover removal");
            }
            finally
            {
                cover?.Dispose();
                frame?.Dispose();
                Logger.Info("[Display] return-cover removed");
            }
        }

        [DllImport("user32.dll", EntryPoint = "ShowWindow")]
        private static extern bool NativeShowWindow(IntPtr window, int command);
        [DllImport("user32.dll", EntryPoint = "SetForegroundWindow")]
        private static extern bool NativeSetForegroundWindow(IntPtr window);
        [DllImport("user32.dll", EntryPoint = "SetWindowPos")]
        private static extern bool NativeSetWindowPos(IntPtr window, IntPtr after, int x, int y, int width, int height, uint flags);

        private void SetThinkingText(string text)
        {
            if (_qaCaptionPanel?.Visible == true)
                _qaCaptionAnswer.Text = text;
            else
                _qaAnswer.Text = text;
        }

        private Control CreateMediaCard(MediaResultCard item)
        {
            var card = new BufferedPanel
            {
                Width = 350,
                Height = 260,
                Margin = new Padding(8, 4, 8, 8),
                BackColor = Color.FromArgb(238, 243, 249),
                Padding = new Padding(8)
            };
            card.Paint += (s, e) =>
            {
                var selected = _mediaResultCards.IndexOf(card) == _mediaResultSelectedIndex;
                var scale = GetUiScale(_qaPanel);
                var stroke = selected ? ScalePx(4, scale) : ScalePx(2, scale);
                var inset = Math.Max(1, stroke / 2);
                using var pen = new Pen(selected ? Color.FromArgb(53, 111, 163) : Color.FromArgb(170, 188, 205), stroke);
                e.Graphics.DrawRectangle(pen, inset, inset, card.Width - stroke, card.Height - stroke);
            };

            var thumb = new PictureBox
            {
                Name = "Thumbnail",
                Location = new Point(12, 12),
                Size = new Size(566, 178),
                BackColor = card.BackColor,
                BorderStyle = BorderStyle.None,
                SizeMode = PictureBoxSizeMode.Zoom
            };
            card.Controls.Add(thumb);
            var placeholder = new Label
            {
                Name = "ThumbnailPlaceholder", Text = "PPT 预览", BackColor = card.BackColor,
                ForeColor = Color.FromArgb(110, 125, 140), TextAlign = ContentAlignment.MiddleCenter
            };
            card.Controls.Add(placeholder);
            placeholder.BringToFront();


            var badge = new Label
            {
                Name = "IndexBadge",
                Text = item.Index.ToString("00"),
                Location = new Point(24, 25),
                Size = new Size(104, 54),
                Font = BadgeFont(item.Index, 23F),
                ForeColor = Color.FromArgb(61, 94, 128),
                BackColor = Color.Transparent,
                TextAlign = ContentAlignment.MiddleLeft
            };
            card.Controls.Add(badge);
            badge.BringToFront();

            if (item.HasCommentary)
            {
                var commentaryBadge = new Label
                {
                    Name = "CommentaryBadge",
                    Text = "讲解",
                    Location = new Point(card.Width - 92, 322),
                    Size = new Size(68, 34),
                    Font = new Font("Microsoft YaHei UI", 12F, FontStyle.Bold),
                    ForeColor = Color.FromArgb(8, 26, 34),
                    BackColor = Color.FromArgb(255, 221, 92),
                    TextAlign = ContentAlignment.MiddleCenter
                };
                card.Controls.Add(commentaryBadge);
                commentaryBadge.BringToFront();
            }

            var title = new Label
            {
                Name = "TitleLabel",
                Text = CleanDisplayText(item.Title ?? item.Name ?? string.Empty),
                Location = new Point(24, 202),
                Size = new Size(542, 62),
                Font = new Font("Microsoft YaHei UI", 20F, FontStyle.Bold),
                ForeColor = Color.FromArgb(25, 35, 49),
                TextAlign = ContentAlignment.MiddleLeft,
                AutoEllipsis = true,
                UseCompatibleTextRendering = false
            };
            card.Controls.Add(title);
            _mediaTooltips.SetToolTip(title, title.Text);
            _mediaTooltips.SetToolTip(card, title.Text);

            var typeText = string.Equals(item.Type, "ppt", StringComparison.OrdinalIgnoreCase)
                ? "PPT"
                : string.Equals(item.Type, "image", StringComparison.OrdinalIgnoreCase)
                    ? "\u56fe\u7247"
                    : "\u89c6\u9891";

            var type = new Label
            {
                Name = "SubtitleLabel",
                Text = BuildMediaCardSubtitle(item, typeText),
                Location = new Point(24, 270),
                Size = new Size(454, 54),
                Font = new Font("Microsoft YaHei UI", 14F),
                ForeColor = Color.FromArgb(91, 103, 119),
                TextAlign = ContentAlignment.MiddleLeft
            };
            card.Controls.Add(type);
            LayoutMediaResultCard(card, GetUiScale(_qaPanel));

            return card;
        }

        private void LayoutMediaResultCards(float scale)
        {
            if (_mediaResultsPanel == null || _mediaResultCards.Count == 0)
                return;

            var margin = ScalePx(5, scale);
            var availableWidth = Math.Max(ScalePx(480, scale), _mediaResultsPanel.ClientSize.Width);
            var cardWidth = Math.Max(ScalePx(240, scale), (availableWidth - margin * 6) / 3);
            var rowCount = Math.Clamp((_mediaResultCards.Count + 2) / 3, 1, 3);
            var cardHeight = Math.Max(
                ScalePx(140, scale),
                (_mediaResultsPanel.ClientSize.Height - ScalePx(6, scale) * rowCount) / rowCount);

            foreach (var card in _mediaResultCards)
            {
                card.Margin = new Padding(margin, ScalePx(2, scale), margin, ScalePx(4, scale));
                card.Size = new Size(cardWidth, cardHeight);
                LayoutMediaResultCard(card, scale);
            }
        }

        private static void LayoutMediaResultCard(Panel card, float scale)
        {
            var padding = ScalePx(8, scale);
            var gap = ScalePx(4, scale);
            var footerHeight = ScalePx(24, scale);
            var titleHeight = ScalePx(36, scale);
            var footerTop = card.Height - padding - footerHeight;
            var numberHeight = ScalePx(20, scale);
            var thumbTop = padding + numberHeight + gap;
            var thumbWidth = Math.Max(40, card.Width - padding * 2);
            var thumbHeight = Math.Max(20, Math.Min((int)(thumbWidth * 9d / 16d), footerTop - thumbTop - titleHeight - gap * 2));
            var titleTop = thumbTop + thumbHeight + gap;
            if (card.Controls["Thumbnail"] is PictureBox thumb)
                thumb.Bounds = new Rectangle(padding, thumbTop, thumbWidth, thumbHeight);
            if (card.Controls["ThumbnailPlaceholder"] is Control placeholder)
                placeholder.Bounds = new Rectangle(padding, thumbTop, thumbWidth, thumbHeight);
            if (card.Controls["IndexBadge"] is Label badge)
            {
                // Keep the number in its own top-left row, clear of the image and title.
                badge.Bounds = new Rectangle(padding, padding, thumbWidth, numberHeight);
                badge.TextAlign = ContentAlignment.TopLeft;
                SetScaledFont(badge, 10F, FontStyle.Bold, scale);
            }
            if (card.Controls["TitleLabel"] is Label title)
            {
                title.Bounds = new Rectangle(padding, titleTop, thumbWidth, titleHeight);
                title.TextAlign = ContentAlignment.TopLeft;
                SetScaledFont(title, 9.5F, FontStyle.Bold, scale);
            }
            if (card.Controls["SubtitleLabel"] is Label subtitle)
            {
                var commentaryWidth = card.Controls["CommentaryBadge"] != null ? ScalePx(52, scale) : 0;
                subtitle.Bounds = new Rectangle(padding, footerTop,
                    Math.Max(40, card.Width - padding * 2 - commentaryWidth), footerHeight);
                SetScaledFont(subtitle, 8F, FontStyle.Regular, scale);
            }
            if (card.Controls["CommentaryBadge"] is Label commentary)
            {
                var width = ScalePx(44, scale);
                commentary.Bounds = new Rectangle(card.Width - width - padding, footerTop, width, footerHeight);
                SetScaledFont(commentary, 8F, FontStyle.Bold, scale);
            }
        }

        private static string BuildMediaCardSubtitle(MediaResultCard item, string typeText)
        {
            return typeText + " 文件";
        }

        private static Size BadgeSize(int index, int height, int horizontalPadding)
        {
            var digits = Math.Max(1, Math.Abs(index).ToString().Length);
            return new Size(Math.Max(height, digits * 15 + horizontalPadding), height);
        }

        private static Font BadgeFont(int index, float baseSize)
        {
            var digits = Math.Max(1, Math.Abs(index).ToString().Length);
            var size = digits >= 3 ? baseSize - 2F : digits >= 2 ? baseSize - 1F : baseSize;
            return new Font("Microsoft YaHei UI", Math.Max(10F, size), FontStyle.Bold);
        }

        private void ClearMediaResults()
        {
            _mediaThumbnailCts?.Cancel();
            _mediaThumbnailCts?.Dispose();
            _mediaThumbnailCts = null;
            if (_mediaResultsPanel == null)
                return;

            foreach (Control control in _mediaResultsPanel.Controls.Cast<Control>().ToArray())
            {
                if (control.Controls["Thumbnail"] is PictureBox thumbnail)
                {
                    var old = thumbnail.Image;
                    thumbnail.Image = null;
                    old?.Dispose();
                }
                control.Dispose();
            }
            _mediaResultsPanel.Controls.Clear();
            _mediaResultItems.Clear();
            _mediaResultCards.Clear();
            _mediaResultSelectedIndex = -1;
            SetMediaResultsMode(false);
        }

        private void BeginLoadMediaThumbnails()
        {
            _mediaThumbnailCts?.Cancel();
            _mediaThumbnailCts?.Dispose();
            _mediaThumbnailCts = new CancellationTokenSource();
            var cancellationToken = _mediaThumbnailCts.Token;

            for (var index = 0; index < _mediaResultItems.Count && index < _mediaResultCards.Count; index++)
            {
                var item = _mediaResultItems[index];
                var card = _mediaResultCards[index];
                if (!string.Equals(item.Type, "ppt", StringComparison.OrdinalIgnoreCase) &&
                    string.IsNullOrWhiteSpace(item.ThumbnailUrl)) continue;

                _ = LoadMediaThumbnailAsync(item, card, cancellationToken);
            }
        }

        private async Task LoadMediaThumbnailAsync(MediaResultCard item, Panel card, CancellationToken cancellationToken)
        {
            Bitmap bitmap = null;
            try
            {
                await _thumbnailSlots.WaitAsync(cancellationToken);
                try
                {
                    var bytes = string.IsNullOrWhiteSpace(item.ThumbnailUrl)
                        ? await _pptThumbnails.GetFirstPageAsync(item.Path ?? item.Name ?? string.Empty, cancellationToken)
                        : await _thumbnailHttp.GetByteArrayAsync(new Uri(new Uri(_settings.FileServerUrl.TrimEnd('/') + "/"), item.ThumbnailUrl), cancellationToken);
                    if (bytes == null || bytes.Length == 0) return;
                    bitmap = await Task.Run(() =>
                    {
                        using var stream = new MemoryStream(bytes);
                        using var image = Image.FromStream(stream);
                        // Bound retained image memory to card resolution instead of full PPT resolution.
                        var ratio = Math.Min(1d, 800d / Math.Max(image.Width, image.Height));
                        return new Bitmap(image, new Size(Math.Max(1, (int)(image.Width * ratio)), Math.Max(1, (int)(image.Height * ratio))));
                    }, cancellationToken);
                }
                finally { _thumbnailSlots.Release(); }
                if (cancellationToken.IsCancellationRequested || IsDisposed || card.IsDisposed) return;
                if (card.Controls["Thumbnail"] is PictureBox thumbnail)
                {
                    var old = thumbnail.Image;
                    thumbnail.Image = bitmap;
                    bitmap = null;
                    old?.Dispose();
                    if (card.Controls["ThumbnailPlaceholder"] is Control placeholder) placeholder.Visible = false;
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception ex) { Logger.Warn($"[Thumbnail] failed file={item.Name}: {ex.Message}"); }
            finally { bitmap?.Dispose(); }
        }

        private void HideQACaption()
        {
            if (_qaCaptionPanel != null)
                _qaCaptionPanel.Visible = false;
        }

        private void StopThinkingTimer()
        {
            _thinkingTimer?.Stop();
        }

        private void HideQaOverlayForPptCommand()
        {
            StopQAAutoCloseTimer();
            StopThinkingTimer();
            ClearMediaResults();
            if (_currentView == "qa" || _currentView == "ppt")
            {
                _qaPanel.Visible = false;
                EnterPptMode();
            }
        }

        private void ClosePptWithoutIdle()
        {
            _pptOpenCts?.Cancel();
            if (_ppt.HasActivePowerPoint || _switchingToPpt) _ppt.Close();
            _currentPptFileName = string.Empty;
            _currentPptSlideNumber = 1;
        }

        private void ClosePptAndRestoreContext()
        {
            StopQAAutoCloseTimer();
            StopThinkingTimer();
            _commentary.Stop();

            ClosePptWithoutIdle();

            if (_mediaResultsPanel?.Controls.Count > 0)
            {
                ShowView("qa");
                return;
            }

            ShowIdle();
        }

        private void StartQAAutoCloseTimer()
        {
            if (_qaAutoCloseTimer == null)
            {
                _qaAutoCloseTimer = new System.Windows.Forms.Timer { Interval = 30000 };
                _qaAutoCloseTimer.Tick += (s, e) =>
                {
                    StopQAAutoCloseTimer();
                    if (_qaCaptionPanel?.Visible == true)
                        HideQACaption();
                    else if (_currentView == "qa")
                        ShowIdle();
                };
            }

            _qaAutoCloseTimer.Stop();
            _qaAutoCloseTimer.Start();
        }

        private void StopQAAutoCloseTimer()
        {
            _qaAutoCloseTimer?.Stop();
        }

        private static string CleanDisplayText(string text)
        {
            if (string.IsNullOrWhiteSpace(text))
                return string.Empty;

            return text
                .Replace("**", string.Empty)
                .Replace("*", string.Empty)
                .Replace("__", string.Empty)
                .Replace("`", string.Empty)
                .Replace("###", string.Empty)
                .Replace("##", string.Empty)
                .Replace("#", string.Empty)
                .Trim();
        }

        private async Task PlayPPT(string? fileName, string? commentaryLevel = null)
        {
            if (string.IsNullOrEmpty(fileName)) return;
            if (_switchingToPpt) { ShowToast("PPT 正在打开，请稍候"); return; }
            _switchingToPpt = true;
            _pptOpenCts?.Dispose();
            var request = _pptOpenCts = new CancellationTokenSource();
            var watch = System.Diagnostics.Stopwatch.StartNew();
            try
            {
                fileName = await EnsureMediaFileAsync(fileName);
                request.Token.ThrowIfCancellationRequested();
                Logger.Info($"[PPT-Open] prepared file={fileName} prepareMs={watch.ElapsedMilliseconds}");
                var page = await _ppt.OpenAsync(fileName, request.Token);
                request.Token.ThrowIfCancellationRequested();
                StopQAAutoCloseTimer();
                StopThinkingTimer();
                HideQACaption();
                foreach (var zone in _zones) zone.HideMedia();
                _commentary.Stop();
                _ppt.PresentWindow();
                EnterPptMode();
                _ppt.PresentWindow();
                _currentPptFileName = fileName;
                _currentPptSlideNumber = page;
                _currentCommentaryLevel = NormalizeCommentaryLevel(commentaryLevel ?? _currentCommentaryLevel);
                _currentView = "ppt";
                Logger.Info($"[PPT-Open] ready file={fileName} page={page} totalMs={watch.ElapsedMilliseconds}");
            }
            catch (OperationCanceledException) { Logger.Info("[PPT-Open] canceled by view change"); }
            catch (Exception ex)
            {
                Logger.Error($"[PPT-Open] failed file={fileName}: {ex.Message}");
                ShowIdle();
                ShowToast($"PPT 打开失败：{ex.Message}");
            }
            finally { _switchingToPpt = false; }
        }

        private async System.Threading.Tasks.Task<string> EnsureMediaFileAsync(string input)
        {
            var resolved = ResolveFileName(input);
            var fileName = Path.GetFileName(resolved);
            if (string.IsNullOrWhiteSpace(fileName))
                return resolved;

            var localPath = _sync.GetLocalPath(fileName);
            if (!string.IsNullOrWhiteSpace(localPath))
            {
                Logger.Info($"[Sync] 优先使用本地缓存: {localPath}");
                return localPath;
            }

            ShowToast("正在检查文件位置，请稍候…");
            var probeStarted = System.Diagnostics.Stopwatch.GetTimestamp();
            var exists = await FileAvailabilityProbe.ExistsAsync(resolved);
            Logger.Info($"[Sync] file-probe exists={exists} elapsedMs={System.Diagnostics.Stopwatch.GetElapsedTime(probeStarted).TotalMilliseconds:F1} file={fileName}");
            if (IsDisposed || Disposing)
                throw new OperationCanceledException("客户端已关闭，取消打开文件");
            if (exists)
                return resolved;

            try
            {
                Logger.Info($"[Sync] 本地缺少媒体文件，按需同步: {fileName}");
                ShowToast($"\u6b63\u5728\u540c\u6b65\u6587\u4ef6: {fileName}");
                var fileUrl = input.StartsWith("http://", StringComparison.OrdinalIgnoreCase) ||
                              input.StartsWith("https://", StringComparison.OrdinalIgnoreCase)
                    ? input
                    : null;
                var downloaded = await _sync.DownloadFileAsync(fileName, fileUrl);
                if (!string.IsNullOrWhiteSpace(downloaded) && File.Exists(downloaded))
                {
                    Logger.Info($"[Sync] 按需同步完成: {downloaded}");
                    return downloaded;
                }

                Logger.Warn($"[Sync] 按需同步后仍未找到文件: {fileName}");
            }
            catch (Exception ex)
            {
                Logger.Error($"[Sync] 按需同步异常: file={fileName}, type={ex.GetType().Name}, message={ex.Message}");
            }

            return resolved;
        }

        private string ResolveFileName(string input)
        {
            if (input.StartsWith("http://", StringComparison.OrdinalIgnoreCase) ||
                input.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
                return Uri.UnescapeDataString(input.Split('/').Last().Split('?')[0]);

            if (input.Contains('%'))
                return Uri.UnescapeDataString(input);

            return input;
        }

        private int ResolveTargetZone(Command cmd)
        {
            if (TryResolveZoneFromString(GetParam(cmd, "screenNumber"), out var zoneByScreen)) return zoneByScreen;
            if (TryResolveZoneFromString(GetParam(cmd, "screen"), out var zoneByScreenAlias)) return zoneByScreenAlias;
            if (TryResolveZoneFromString(GetParam(cmd, "segment"), out var zoneBySegment)) return zoneBySegment;
            if (TryResolveZoneFromString(GetParam(cmd, "zone"), out var zoneByZone)) return zoneByZone;
            return 0;
        }

        private static string? GetParam(Command cmd, string key)
        {
            return cmd.Params != null && cmd.Params.TryGetValue(key, out var value) ? value : null;
        }

        private void SpeakCurrentPptCommentary(string? commentaryLevel)
        {
            if (!_ppt.IsOpened || string.IsNullOrWhiteSpace(_currentPptFileName))
                return;

            _currentCommentaryLevel = NormalizeCommentaryLevel(commentaryLevel ?? _currentCommentaryLevel);
            _currentPptSlideNumber = _ppt.CurrentSlide;
            var cleanName = Path.GetFileName(_currentPptFileName).Replace(" ", string.Empty);
            _commentary.SpeakCommentary(cleanName, _currentPptSlideNumber, _currentCommentaryLevel);
        }

        private static string NormalizeCommentaryLevel(string? value)
        {
            var level = (value ?? string.Empty).Trim().ToLowerInvariant();
            return level is "standard" or "detailed" ? level : "concise";
        }

        private static bool IsTruthy(string? value)
        {
            if (string.IsNullOrWhiteSpace(value))
                return false;

            return value.Trim().ToLowerInvariant() is "true" or "1" or "yes" or "y" or "on";
        }

        private bool TryResolveZoneFromString(string? value, out int zoneIndex)
        {
            zoneIndex = 0;
            if (string.IsNullOrWhiteSpace(value))
                return false;

            var normalized = value.Trim().ToLowerInvariant();
            if (normalized is "left" or "\u5de6")
            {
                zoneIndex = 0;
                return zoneIndex < _zones.Count;
            }

            if (normalized is "center" or "middle" or "\u4e2d")
            {
                zoneIndex = Math.Min(1, _zones.Count - 1);
                return true;
            }

            if (normalized is "right" or "\u53f3")
            {
                zoneIndex = Math.Min(2, _zones.Count - 1);
                return true;
            }

            if (int.TryParse(normalized, out var parsed))
            {
                zoneIndex = parsed > 0 ? parsed - 1 : parsed;
                if (zoneIndex < 0) zoneIndex = 0;
                if (zoneIndex >= _zones.Count) zoneIndex = _zones.Count - 1;
                return true;
            }

            return false;
        }

        private DisplayZone? GetZone(int zoneIndex)
        {
            if (zoneIndex < 0 || zoneIndex >= _zones.Count)
            {
                Logger.Warn($"[Display] 鍒嗗睆绱㈠紩瓒呭嚭鑼冨洿: zoneIndex={zoneIndex}, totalZones={_zones.Count}");
                return _zones.FirstOrDefault();
            }

            return _zones[zoneIndex];
        }

        private void ToggleFullscreen()
        {
            _isFullscreen = !_isFullscreen;
            FormBorderStyle = FormBorderStyle.None;
            WindowState = _isFullscreen ? FormWindowState.Maximized : FormWindowState.Normal;
            if (!_isFullscreen)
                ApplyConfiguredResolution();
            TopMost = false;
            ShowToast(_isFullscreen ? "\u5df2\u5207\u6362\u5230\u5168\u5c4f" : "\u5df2\u9000\u51fa\u5168\u5c4f");
        }

        private void TestVideo()
        {
            var testFile = Directory.GetFiles(_settings.MediaPath, "*.mp4").FirstOrDefault();
            if (testFile != null)
                PlayVideo(testFile, 0);
            else
                ShowToast($"鏈湪 {_settings.MediaPath} 鎵惧埌 mp4 鏂囦欢");
        }

        private void TestAllZonesVideo()
        {
            var files = GetMediaFiles(new[] { "*.mp4", "*.mov", "*.mkv" });
            if (files.Count == 0)
            {
                ShowToast($"鏈湪 {_settings.MediaPath} 鎵惧埌鍙敤浜庡垎灞忕殑瑙嗛鏂囦欢");
                return;
            }

            for (int i = 0; i < _zones.Count; i++)
            {
                var file = files[i % files.Count];
                PlayVideo(file, i);
            }

            ShowToast($"\u5df2\u5411 {_zones.Count} \u4e2a\u5206\u5c4f\u533a\u57df\u6295\u653e\u6d4b\u8bd5\u89c6\u9891");
        }

        private void TestAllZonesImage()
        {
            var files = GetMediaFiles(new[] { "*.jpg", "*.jpeg", "*.png", "*.bmp", "*.gif" });
            if (files.Count == 0)
            {
                ShowToast($"鏈湪 {_settings.MediaPath} 鎵惧埌鍙敤浜庡垎灞忕殑鍥剧墖鏂囦欢");
                return;
            }

            for (int i = 0; i < _zones.Count; i++)
            {
                var file = files[i % files.Count];
                ShowDoc(file, i);
            }

            ShowToast($"\u5df2\u5411 {_zones.Count} \u4e2a\u5206\u5c4f\u533a\u57df\u6295\u653e\u6d4b\u8bd5\u56fe\u7247");
        }

        private List<string> GetMediaFiles(IEnumerable<string> patterns)
        {
            var files = new List<string>();
            foreach (var pattern in patterns)
            {
                files.AddRange(Directory.GetFiles(_settings.MediaPath, pattern));
            }
            return files.Distinct(StringComparer.OrdinalIgnoreCase).ToList();
        }

        private void TestSpeech()
        {
            Speak("\u60a8\u597d\uff0c\u6b22\u8fce\u6765\u5230\u601d\u5fb7\u79d1\u6280\u5c55\u5385\uff0c\u8fd9\u91cc\u5c55\u793a\u4e86\u6211\u4eec\u7684\u6838\u5fc3\u4ea7\u54c1\u4e0e\u89e3\u51b3\u65b9\u6848\u3002");
        }

        private void TestQA()
        {
            ShowQA("\u601d\u5fb7\u79d1\u6280\u7684\u4e3b\u8425\u4e1a\u52a1\u662f\u4ec0\u4e48\uff1f", "\u601d\u5fb7\u79d1\u6280\u4e13\u6ce8\u4e8e\u4eba\u5de5\u667a\u80fd\u8425\u9500\u89e3\u51b3\u65b9\u6848\uff0c\u4e3a\u4f01\u4e1a\u6253\u9020\u667a\u80fd\u5316\u5c55\u5385\u4e0e\u8425\u9500\u7cfb\u7edf\u3002");
        }

        private void TestPPT()
        {
            var testFile = Directory.GetFiles(_settings.MediaPath, "*.ppt*").FirstOrDefault();
            if (testFile != null)
                _ = PlayPPT(testFile);
            else
                ShowToast($"鏈湪 {_settings.MediaPath} 鎵惧埌 PPT 鏂囦欢");
        }

        private void ToggleMute()
        {
            _commentary.IsMuted = !_commentary.IsMuted;
            foreach (var zone in _zones)
                zone.Video.IsMuted = _commentary.IsMuted;

            ShowToast(_commentary.IsMuted ? "\u5df2\u9759\u97f3" : "\u5df2\u53d6\u6d88\u9759\u97f3");
        }

        private void SendScreenSnapshot(object? state)
        {
            if (_snapshotInProgress || !_ws.IsConnected || IsDisposed)
                return;

            _snapshotInProgress = true;
            System.Threading.Tasks.Task.Run(async () =>
            {
                try
                {
                    using var image = CapturePreviewImage();
                    if (image == null) return;

                    using var ms = new MemoryStream();
                    var encoder = ImageCodecInfo.GetImageEncoders().FirstOrDefault(item => item.MimeType == "image/jpeg");
                    if (encoder != null)
                    {
                        using var parameters = new EncoderParameters(1);
                        parameters.Param[0] = new EncoderParameter(Encoder.Quality, 65L);
                        image.Save(ms, encoder, parameters);
                    }
                    else
                    {
                        image.Save(ms, ImageFormat.Jpeg);
                    }

                    await _ws.SendAsync(new
                    {
                        type = "screen_snapshot",
                        screenNumber = _screenNumber ?? _settings.ScreenNumber,
                        width = image.Width,
                        height = image.Height,
                        imageBase64 = Convert.ToBase64String(ms.ToArray())
                    });
                    Logger.Info($"[Snapshot] Uploaded screen preview: {image.Width}x{image.Height}, size={ms.Length}");
                }
                catch (Exception ex)
                {
                    Logger.Warn($"[Snapshot] Upload failed: {ex.GetType().Name} - {ex.Message}");
                }
                finally
                {
                    _snapshotInProgress = false;
                }
            });
        }

        private Bitmap? CapturePreviewImage()
        {
            Rectangle bounds;
            try
            {
                bounds = Screen.FromControl(this)?.Bounds ?? Screen.PrimaryScreen?.Bounds ?? Bounds;
            }
            catch
            {
                bounds = Screen.PrimaryScreen?.Bounds ?? Bounds;
            }

            if (bounds.Width <= 0 || bounds.Height <= 0)
                return null;

            var targetWidth = Math.Min(960, bounds.Width);
            var targetHeight = Math.Max(1, (int)Math.Round(bounds.Height * (targetWidth / (double)bounds.Width)));
            var preview = new Bitmap(targetWidth, targetHeight);
            using (var graphics = Graphics.FromImage(preview))
            {
                graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
                graphics.SmoothingMode = SmoothingMode.HighQuality;
                graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;

                var targetDc = graphics.GetHdc();
                var sourceDc = GetDC(IntPtr.Zero);
                try
                {
                    SetStretchBltMode(targetDc, StretchBltModeHalftone);
                    StretchBlt(
                        targetDc,
                        0,
                        0,
                        targetWidth,
                        targetHeight,
                        sourceDc,
                        bounds.Left,
                        bounds.Top,
                        bounds.Width,
                        bounds.Height,
                        SrcCopy);
                }
                finally
                {
                    if (sourceDc != IntPtr.Zero)
                        ReleaseDC(IntPtr.Zero, sourceDc);
                    graphics.ReleaseHdc(targetDc);
                }
            }

            return preview;
        }

        private void ShowToast(string message)
        {
            _toast.Text = message;
            _toast.Visible = true;
            PositionToast();

            _toastTimer?.Dispose();
            _toastTimer = new System.Threading.Timer(_ =>
            {
                try
                {
                    if (IsHandleCreated && !IsDisposed)
                    {
                        BeginInvoke(new Action(() =>
                        {
                            try { _toast.Visible = false; } catch { }
                        }));
                    }
                }
                catch
                {
                }
            }, null, 2500, System.Threading.Timeout.Infinite);
        }

        private void PositionToast()
        {
            _toast.Location = new Point((Width - _toast.Width) / 2, 50);
        }

        private void OnKeyDown(object? sender, KeyEventArgs e)
        {
            switch (e.KeyCode)
            {
                case Keys.F10:
                    LogForm.Instance.ToggleVisible();
                    break;
                case Keys.F12:
                    _isAdminVisible = !_isAdminVisible;
                    _adminPanel.Visible = _isAdminVisible;
                    break;
                case Keys.Escape:
                    OpenSettingsDialog();
                    break;
                case Keys.F11:
                case Keys.Enter when e.Control:
                    ToggleFullscreen();
                    break;
            }
        }

        private void UpdateStatus(object? state)
        {
            if (!IsHandleCreated) return;
            SendClientStatus();

            BeginInvoke(new Action(() =>
            {
                if (_currentView == "speech" && _commentary.IsSpeaking)
                {
                    _statusBarFill.Width = (_statusBarFill.Width + 5) % Math.Max(1, _statusBar.Width);
                }
                else
                {
                    _statusBarFill.Width = _ws.IsConnected ? _statusBar.Width : 0;
                }
            }));
        }

        private void SendClientStatus()
        {
            if (_statusSendInProgress || !_ws.IsConnected || IsDisposed)
                return;

            _statusSendInProgress = true;
            System.Threading.Tasks.Task.Run(async () =>
            {
                try
                {
                    var memory = GetMemoryStatus();
                    await _ws.SendAsync(new
                    {
                        type = "client_status",
                        screenNumber = _screenNumber ?? _settings.ScreenNumber,
                        cpuPercent = GetCpuUsagePercent(),
                        memoryPercent = memory.Percent,
                        memoryUsedMb = memory.UsedMb,
                        memoryTotalMb = memory.TotalMb,
                        processMemoryMb = Math.Round(Environment.WorkingSet / 1024d / 1024d, 1),
                        uptimeSeconds = Math.Max(0, (int)(DateTime.Now - _startedAt).TotalSeconds),
                        view = _currentView,
                        currentPptFileName = Path.GetFileName(_currentPptFileName ?? string.Empty),
                        currentPptSlideNumber = _ppt.IsOpened ? _ppt.CurrentSlide : _currentPptSlideNumber,
                        currentCommentaryLevel = _currentCommentaryLevel
                    });
                }
                catch (Exception ex)
                {
                    Logger.Warn($"[Status] Upload failed: {ex.GetType().Name} - {ex.Message}");
                }
                finally
                {
                    _statusSendInProgress = false;
                }
            });
        }

        private double? GetCpuUsagePercent()
        {
            if (!GetSystemTimes(out var idleFileTime, out var kernelFileTime, out var userFileTime))
                return null;

            var idle = ToUInt64(idleFileTime);
            var kernel = ToUInt64(kernelFileTime);
            var user = ToUInt64(userFileTime);
            if (!_hasCpuSample)
            {
                _lastIdleTime = idle;
                _lastKernelTime = kernel;
                _lastUserTime = user;
                _hasCpuSample = true;
                return null;
            }

            var idleDiff = idle - _lastIdleTime;
            var kernelDiff = kernel - _lastKernelTime;
            var userDiff = user - _lastUserTime;
            var total = kernelDiff + userDiff;
            _lastIdleTime = idle;
            _lastKernelTime = kernel;
            _lastUserTime = user;
            if (total == 0) return null;

            var used = Math.Max(0, total - idleDiff);
            return Math.Round(Math.Min(100, used * 100d / total), 1);
        }

        private static (double? Percent, double? UsedMb, double? TotalMb) GetMemoryStatus()
        {
            var status = new MemoryStatusEx();
            status.dwLength = (uint)Marshal.SizeOf<MemoryStatusEx>();
            if (!GlobalMemoryStatusEx(ref status))
                return (null, null, null);

            var totalMb = Math.Round(status.ullTotalPhys / 1024d / 1024d, 1);
            var availableMb = Math.Round(status.ullAvailPhys / 1024d / 1024d, 1);
            var usedMb = Math.Round(totalMb - availableMb, 1);
            return (Math.Round((double)status.dwMemoryLoad, 1), usedMb, totalMb);
        }

        private static ulong ToUInt64(FileTime fileTime)
        {
            return ((ulong)fileTime.dwHighDateTime << 32) | fileTime.dwLowDateTime;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct FileTime
        {
            public uint dwLowDateTime;
            public uint dwHighDateTime;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct MemoryStatusEx
        {
            public uint dwLength;
            public uint dwMemoryLoad;
            public ulong ullTotalPhys;
            public ulong ullAvailPhys;
            public ulong ullTotalPageFile;
            public ulong ullAvailPageFile;
            public ulong ullTotalVirtual;
            public ulong ullAvailVirtual;
            public ulong ullAvailExtendedVirtual;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetSystemTimes(out FileTime idleTime, out FileTime kernelTime, out FileTime userTime);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GlobalMemoryStatusEx(ref MemoryStatusEx lpBuffer);

        private void UpdateIdleHint()
        {
            var hint = _screenNumber.HasValue
                ? $"\u5c4f\u5e55\u53f7: {_screenNumber} | \u5206\u5c4f\u6570: {_zones.Count} | \u8bf7\u4f7f\u7528\u79fb\u52a8\u7aef\u8bed\u97f3\u63a7\u5236"
                : $"\u5206\u5c4f\u6570: {_zones.Count} | \u8bf7\u4f7f\u7528\u79fb\u52a8\u7aef\u8bed\u97f3\u63a7\u5236";
            _idleHint.Text = hint;
            _idleHint.PerformLayout();
            CenterIdleLabels();
        }

        private void UpdateAdminInfo(string status)
        {
            if (_adminPanel.Tag is Label label)
                label.Text = _screenNumber.HasValue ? $"{_screenNumber}鍙峰睆 | {status}" : status;
        }

        private void OpenSettingsDialog()
        {
            using var form = new SettingsForm(_settings);
            var result = form.ShowDialog(this);
            if (result == DialogResult.OK)
            {
                ShowToast("\u914d\u7f6e\u5df2\u4fdd\u5b58\uff0c\u91cd\u542f\u7a0b\u5e8f\u540e\u751f\u6548");
            }
            else if (result == DialogResult.Abort)
            {
                Application.Exit();
            }
        }

        private void ApplyConfiguredResolution()
        {
            var bounds = Screen.PrimaryScreen?.Bounds ?? new Rectangle(0, 0, _configuredResolution.Width, _configuredResolution.Height);
            Size = _configuredResolution;
            Location = new Point(
                Math.Max(0, (bounds.Width - _configuredResolution.Width) / 2),
                Math.Max(0, (bounds.Height - _configuredResolution.Height) / 2));
            PositionToast();

            Logger.Info($"[Display] 宸插簲鐢ㄧ獥鍙ｅ垎杈ㄧ巼: {_configuredResolution.Width}x{_configuredResolution.Height}");
        }

        private static int NormalizeInterval(int value, int fallback)
        {
            return value > 0 ? Math.Min(3600, value) : fallback;
        }

        private static int ToTimerPeriod(int seconds, int fallback)
        {
            return NormalizeInterval(seconds, fallback) * 1000;
        }

        private const int StretchBltModeHalftone = 4;
        private const int SrcCopy = 0x00CC0020;
        private const int WM_SETREDRAW = 0x000B;
        private const int WM_NCLBUTTONDOWN = 0xA1;
        private const int HTCAPTION = 0x2;
        private static readonly Color TransparentShellColor = Color.FromArgb(1, 2, 3);

        [DllImport("user32.dll")]
        private static extern IntPtr GetDC(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern int ReleaseDC(IntPtr hWnd, IntPtr hDc);

        [DllImport("user32.dll")]
        private static extern bool ReleaseCapture();

        [DllImport("user32.dll")]
        private static extern IntPtr SendMessage(IntPtr hWnd, int msg, int wParam, int lParam);

        [DllImport("gdi32.dll")]
        private static extern int SetStretchBltMode(IntPtr hdc, int mode);

        [DllImport("gdi32.dll")]
        private static extern bool StretchBlt(
            IntPtr hdcDest,
            int xDest,
            int yDest,
            int widthDest,
            int heightDest,
            IntPtr hdcSrc,
            int xSrc,
            int ySrc,
            int widthSrc,
            int heightSrc,
            int rop);

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (_settings.NonIntrusiveMode && e.CloseReason == CloseReason.UserClosing && !_hidingToTray)
            {
                e.Cancel = true;
                _hidingToTray = true;
                try
                {
                    ShowIdle();
                }
                finally
                {
                    _hidingToTray = false;
                }
                return;
            }

            _ws.Dispose();
            HidePptNotice();
            _sync.Dispose();
            _commentary.Dispose();
            _mediaThumbnailCts?.Cancel();
            _mediaThumbnailCts?.Dispose();
            _pptThumbnails.Dispose();
            _thumbnailHttp.Dispose();
            _mediaTooltips.Dispose();
            _avatar.Dispose();
            _backgroundRenderCache?.Dispose();
            _backgroundImage?.Dispose();
            _qaCaptionBackground?.Dispose();
            _ppt.Dispose();
            foreach (var zone in _zones)
                zone.Dispose();
            _statusUpdateTimer?.Dispose();
            _snapshotTimer?.Dispose();
            _thinkingTimer?.Dispose();
            _qaAutoCloseTimer?.Dispose();
            base.OnFormClosing(e);
        }

        protected override void OnResize(EventArgs e)
        {
            base.OnResize(e);
            if (_toast != null)
                PositionToast();
            if (_aiOrb != null)
                PositionOrb();
            if (_settings.NonIntrusiveMode && _popupCloseButton?.Visible == true)
                ApplyPopupChrome(true);
        }

        private sealed class BufferedPanel : Panel
        {
            public BufferedPanel()
            {
                DoubleBuffered = true;
                ResizeRedraw = true;
                SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.SupportsTransparentBackColor, true);
            }
        }

        private sealed class BufferedFlowLayoutPanel : FlowLayoutPanel
        {
            public BufferedFlowLayoutPanel()
            {
                DoubleBuffered = true;
                ResizeRedraw = true;
                SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer, true);
            }
        }

        private sealed class SpriteAnimationBox : Control
        {
            private readonly System.Windows.Forms.Timer _timer;
            private Image? _sprite;
            private Bitmap? _backgroundCache;
            private readonly List<Bitmap> _renderedFrames = new();
            private readonly List<Region> _renderedFrameRegions = new();
            private Size _renderedFrameSize;
            private List<int> _frameSequence = new();
            private string _spritePath = string.Empty;
            private int _frameIndex;
            private int _sequenceIndex;
            private int _frameCount;
            private int _columns = 9;
            private int _rows = 9;
            private int _frameWidth;
            private int _frameHeight;
            private bool _useBinaryAlpha;
            public Action<Graphics, Rectangle>? PaintBehind { get; set; }

            public bool UseBinaryAlpha
            {
                get => _useBinaryAlpha;
                set
                {
                    if (_useBinaryAlpha == value)
                        return;

                    _useBinaryAlpha = value;
                    DisposeRenderedFrames();
                    if (!value)
                        ApplyEllipseRegion();
                }
            }

            public SpriteAnimationBox()
            {
                DoubleBuffered = true;
                SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.SupportsTransparentBackColor, true);
                _timer = new System.Windows.Forms.Timer();
                _timer.Tick += (s, e) =>
                {
                    if (_frameSequence.Count <= 0)
                        return;
                    _sequenceIndex = (_sequenceIndex + 1) % _frameSequence.Count;
                    _frameIndex = _frameSequence[_sequenceIndex];
                    ApplyCurrentFrameRegion();
                    Invalidate(false);
                };
            }

            public void Configure(string spritePath, int fps, int configuredFrameCount)
            {
                _timer.Interval = Math.Max(16, 1000 / Math.Max(1, fps));
                if (string.Equals(_spritePath, spritePath, StringComparison.OrdinalIgnoreCase) && _sprite != null)
                    return;

                _timer.Stop();
                _sprite?.Dispose();
                _sprite = null;
                DisposeRenderedFrames();
                _spritePath = spritePath;
                _frameIndex = 0;
                _sequenceIndex = 0;
                _frameCount = 0;
                _frameSequence.Clear();

                if (string.IsNullOrWhiteSpace(spritePath) || !File.Exists(spritePath))
                    return;

                _sprite = Image.FromFile(spritePath);
                _frameWidth = Math.Max(1, _sprite.Width / _columns);
                _frameHeight = Math.Max(1, _sprite.Height / _rows);
                var maxFrames = _columns * _rows;
                _frameCount = configuredFrameCount > 0
                    ? Math.Clamp(configuredFrameCount, 1, maxFrames)
                    : DetectFrameCount(_sprite, _columns, _rows, _frameWidth, _frameHeight);
                _frameSequence = BuildFrameSequence(_frameCount);
                _frameIndex = _frameSequence.Count > 0 ? _frameSequence[0] : 0;
            }

            public void RefreshVisualCache()
            {
                if (Width <= 0 || Height <= 0)
                    return;

                _backgroundCache?.Dispose();
                _backgroundCache = new Bitmap(Width, Height, PixelFormat.Format32bppPArgb);
                using (var graphics = Graphics.FromImage(_backgroundCache))
                {
                    graphics.Clear(BackColor);
                    PaintBehind?.Invoke(graphics, ClientRectangle);
                }

                if (_sprite == null || _frameCount <= 0 || _renderedFrameSize == ClientSize)
                    return;

                DisposeRenderedFrames();
                _renderedFrameSize = ClientSize;
                for (var frame = 0; frame < _frameCount; frame++)
                {
                    _renderedFrames.Add(null);
                    _renderedFrameRegions.Add(null);
                }
                ApplyCurrentFrameRegion();
            }

            private void EnsureRenderedFrame(int frame)
            {
                if (_sprite == null || frame < 0 || frame >= _renderedFrames.Count || _renderedFrames[frame] != null) return;
                var rendered = new Bitmap(Width, Height, PixelFormat.Format32bppPArgb);
                using (var graphics = Graphics.FromImage(rendered))
                {
                    graphics.CompositingMode = CompositingMode.SourceCopy;
                    graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
                    graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
                    var source = new Rectangle((frame % _columns) * _frameWidth + 3,
                        (frame / _columns) * _frameHeight + 3, Math.Max(1, _frameWidth - 6), Math.Max(1, _frameHeight - 6));
                    graphics.DrawImage(_sprite, ClientRectangle, source, GraphicsUnit.Pixel);
                }
                if (_useBinaryAlpha) _renderedFrameRegions[frame] = ApplyBinaryAlpha(rendered);
                _renderedFrames[frame] = rendered;
            }

            private static Region ApplyBinaryAlpha(Bitmap bitmap)
            {
                const byte alphaThreshold = 96;
                var bounds = new Rectangle(Point.Empty, bitmap.Size);
                var data = bitmap.LockBits(bounds, ImageLockMode.ReadWrite, PixelFormat.Format32bppPArgb);
                using var path = new GraphicsPath();
                try
                {
                    var bytes = new byte[Math.Abs(data.Stride) * data.Height];
                    Marshal.Copy(data.Scan0, bytes, 0, bytes.Length);
                    for (var y = 0; y < data.Height; y++)
                    {
                        var row = y * Math.Abs(data.Stride);
                        for (var x = 0; x < data.Width; x++)
                        {
                            var offset = row + x * 4;
                            var alpha = bytes[offset + 3];
                            if (alpha < alphaThreshold)
                            {
                                bytes[offset] = 0;
                                bytes[offset + 1] = 0;
                                bytes[offset + 2] = 0;
                                bytes[offset + 3] = 0;
                                continue;
                            }

                            bytes[offset] = Unpremultiply(bytes[offset], alpha);
                            bytes[offset + 1] = Unpremultiply(bytes[offset + 1], alpha);
                            bytes[offset + 2] = Unpremultiply(bytes[offset + 2], alpha);
                            bytes[offset + 3] = 255;
                        }

                        var runStart = -1;
                        for (var x = 0; x <= data.Width; x++)
                        {
                            var opaque = x < data.Width && bytes[row + x * 4 + 3] > 0;
                            if (opaque && runStart < 0)
                            {
                                runStart = x;
                            }
                            else if (!opaque && runStart >= 0)
                            {
                                path.AddRectangle(new Rectangle(runStart, y, x - runStart, 1));
                                runStart = -1;
                            }
                        }
                    }
                    Marshal.Copy(bytes, 0, data.Scan0, bytes.Length);
                }
                finally
                {
                    bitmap.UnlockBits(data);
                }

                if (path.PointCount > 0)
                    return new Region(path);

                var empty = new Region();
                empty.MakeEmpty();
                return empty;
            }

            private static byte Unpremultiply(byte color, byte alpha)
            {
                return (byte)Math.Min(255, (color * 255 + alpha / 2) / alpha);
            }

            public void Start()
            {
                if (_sprite != null && _frameSequence.Count > 0)
                    _timer.Start();
            }

            public void Stop()
            {
                _timer.Stop();
            }

            protected override void Dispose(bool disposing)
            {
                if (disposing)
                {
                    _timer.Dispose();
                    _sprite?.Dispose();
                    _backgroundCache?.Dispose();
                    DisposeRenderedFrames();
                }
                base.Dispose(disposing);
            }

            protected override void OnPaintBackground(PaintEventArgs pevent)
            {
                if (_backgroundCache != null)
                    pevent.Graphics.DrawImageUnscaled(_backgroundCache, Point.Empty);
                else
                    PaintBehind?.Invoke(pevent.Graphics, ClientRectangle);
            }

            protected override void OnResize(EventArgs e)
            {
                base.OnResize(e);
                if (Width <= 0 || Height <= 0)
                    return;

                ApplyEllipseRegion();
                _renderedFrameSize = Size.Empty;
            }

            protected override void OnPaint(PaintEventArgs e)
            {
                base.OnPaint(e);
                if (_sprite == null || _frameSequence.Count <= 0 || Width <= 0 || Height <= 0)
                    return;

                if (_renderedFrames.Count != _frameCount || _renderedFrameSize != ClientSize)
                    RefreshVisualCache();
                EnsureRenderedFrame(_frameIndex);
                if (_frameIndex >= 0 && _frameIndex < _renderedFrames.Count)
                    e.Graphics.DrawImageUnscaled(_renderedFrames[_frameIndex], Point.Empty);
            }

            private void DisposeRenderedFrames()
            {
                foreach (var frame in _renderedFrames)
                    frame?.Dispose();
                _renderedFrames.Clear();
                foreach (var region in _renderedFrameRegions)
                    region?.Dispose();
                _renderedFrameRegions.Clear();
                _renderedFrameSize = Size.Empty;
            }

            private void ApplyCurrentFrameRegion()
            {
                EnsureRenderedFrame(_frameIndex);
                if (!_useBinaryAlpha || _frameIndex < 0 || _frameIndex >= _renderedFrameRegions.Count)
                    return;

                var oldRegion = Region;
                Region = _renderedFrameRegions[_frameIndex].Clone();
                oldRegion?.Dispose();
            }

            private void ApplyEllipseRegion()
            {
                if (Width <= 0 || Height <= 0)
                    return;

                using var path = new GraphicsPath();
                path.AddEllipse(ClientRectangle);
                var oldRegion = Region;
                Region = new Region(path);
                oldRegion?.Dispose();
            }

            private static int DetectFrameCount(Image sprite, int columns, int rows, int frameWidth, int frameHeight)
            {
                using var bitmap = new Bitmap(sprite);
                var total = columns * rows;
                var detected = total;
                for (var i = total - 1; i >= 0; i--)
                {
                    var column = i % columns;
                    var row = i / columns;
                    if (HasVisibleFrame(bitmap, column * frameWidth, row * frameHeight, frameWidth, frameHeight))
                    {
                        detected = i + 1;
                        break;
                    }
                }

                return Math.Max(1, detected);
            }

            private static List<int> BuildFrameSequence(int frameCount)
            {
                var frames = Enumerable.Range(0, Math.Max(1, frameCount)).ToList();
                if (frames.Count <= 2)
                    return frames;

                for (var i = frames.Count - 2; i > 0; i--)
                    frames.Add(i);

                return frames;
            }

            private static bool HasVisibleFrame(Bitmap bitmap, int x, int y, int width, int height)
            {
                var samples = 0;
                var nonWhite = 0;
                var stepX = Math.Max(1, width / 8);
                var stepY = Math.Max(1, height / 8);
                for (var py = y + stepY; py < y + height - stepY && py < bitmap.Height; py += stepY)
                {
                    for (var px = x + stepX; px < x + width - stepX && px < bitmap.Width; px += stepX)
                    {
                        var color = bitmap.GetPixel(px, py);
                        samples++;
                        if (color.R < 245 || color.G < 245 || color.B < 245)
                            nonWhite++;
                    }
                }

                return samples == 0 || nonWhite > samples / 3;
            }
        }

        private sealed class MediaResultCard
        {
            public int Index { get; set; }
            public string? Title { get; set; }
            public string? Name { get; set; }
            public string? Path { get; set; }
            public string? Type { get; set; }
            public string? ThumbnailUrl { get; set; }
            public string? LocalPath { get; set; }
            public string? CommentaryLevel { get; set; }
            [JsonPropertyName("ai_explain")]
            public string? AiExplain { get; set; }
            [JsonPropertyName("commentary")]
            public string? Commentary { get; set; }
            [JsonPropertyName("all_commentary")]
            public string? AllCommentary { get; set; }
            [JsonPropertyName("ppt_commentary")]
            public string? PptCommentary { get; set; }
            [JsonPropertyName("audio_url")]
            public string? AudioUrl { get; set; }
            [JsonPropertyName("hasCommentary")]
            public bool HasCommentaryFlag { get; set; }

            [JsonIgnore]
            public bool HasCommentary =>
                HasCommentaryFlag ||
                string.Equals(AiExplain?.Trim(), "1", StringComparison.OrdinalIgnoreCase) ||
                !string.IsNullOrWhiteSpace(Commentary) ||
                !string.IsNullOrWhiteSpace(AllCommentary) ||
                !string.IsNullOrWhiteSpace(PptCommentary) ||
                !string.IsNullOrWhiteSpace(AudioUrl);
        }

        private sealed class DisplayZone : IDisposable
        {
            public int Index { get; }
            public Panel Root { get; }
            public Label Placeholder { get; }
            public VideoController Video { get; }
            public ImageController Image { get; }
            public bool HasContent { get; private set; }

            public DisplayZone(int index, string mediaPath)
            {
                Index = index;
                Video = new VideoController(mediaPath);
                Image = new ImageController(mediaPath);

                Root = new Panel
                {
                    Dock = DockStyle.Fill,
                    Margin = Padding.Empty,
                    Padding = Padding.Empty,
                    BackColor = Color.Black
                };

                Placeholder = new Label
                {
                    Dock = DockStyle.Fill,
                    Text = $"\u7b2c {index + 1} \u5c4f\u5f85\u673a",
                    Font = new Font("Microsoft YaHei UI", 18F),
                    ForeColor = Color.FromArgb(120, 120, 120),
                    BackColor = Color.Black,
                    TextAlign = ContentAlignment.MiddleCenter
                };
            }

            public void ShowVideo(string fileName)
            {
                Placeholder.Visible = false;
                Image.Hide();
                Video.Play(fileName);
                Video.Show();
                HasContent = true;
            }

            public void ShowImage(string fileName)
            {
                Placeholder.Visible = false;
                Video.Hide();
                Image.ShowImage(fileName);
                HasContent = true;
            }

            public void HideMedia()
            {
                Video.Hide();
                Image.Hide();
                if (HasContent)
                    Placeholder.Visible = true;
            }

            public void Reset()
            {
                Video.Hide();
                Image.Hide();
                Placeholder.Visible = true;
                HasContent = false;
            }

            public void Dispose()
            {
                Video.Dispose();
                Image.Dispose();
                Root.Dispose();
            }
        }
    }
}
