using System;
using System.Drawing;
using System.Windows.Forms;
using ExhibitionClient.Services;

namespace ExhibitionClient.Views
{
    public class SettingsForm : Form
    {
        private readonly RuntimeSettings _settings;
        private TextBox _wsHost = null!;
        private NumericUpDown _wsPort = null!;
        private TextBox _fileHost = null!;
        private NumericUpDown _filePort = null!;
        private TextBox _mediaPath = null!;
        private NumericUpDown _screenNumber = null!;
        private TextBox _powerPointPath = null!;
        private NumericUpDown _displayWidth = null!;
        private NumericUpDown _displayHeight = null!;
        private NumericUpDown _segmentWidth = null!;
        private NumericUpDown _segmentHeight = null!;
        private Label _splitPreview = null!;

        public SettingsForm(RuntimeSettings settings)
        {
            _settings = settings;
            InitializeComponent();
            LoadValues();
            UpdateSplitPreview();
        }

        private void InitializeComponent()
        {
            Text = "客户端配置";
            Size = new Size(620, 620);
            StartPosition = FormStartPosition.CenterParent;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            BackColor = Color.FromArgb(28, 28, 32);

            var panel = new TableLayoutPanel
            {
                Dock = DockStyle.Fill,
                ColumnCount = 2,
                RowCount = 13,
                Padding = new Padding(20),
                BackColor = BackColor
            };
            panel.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 160));
            panel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            Controls.Add(panel);

            AddRow(panel, 0, "WebSocket IP", out _wsHost);
            AddNumericRow(panel, 1, "WebSocket 端口", out _wsPort, 0, 65535);
            AddRow(panel, 2, "文件服务 IP", out _fileHost);
            AddNumericRow(panel, 3, "文件服务端口", out _filePort, 0, 65535);
            AddRow(panel, 4, "媒体目录", out _mediaPath);
            AddNumericRow(panel, 5, "屏幕号", out _screenNumber, 0, 999);
            AddRow(panel, 6, "PowerPoint 路径", out _powerPointPath);
            AddNumericRow(panel, 7, "总宽度", out _displayWidth, 320, 16384);
            AddNumericRow(panel, 8, "总高度", out _displayHeight, 240, 4320);
            AddNumericRow(panel, 9, "分屏宽度", out _segmentWidth, 320, 16384);
            AddNumericRow(panel, 10, "分屏高度", out _segmentHeight, 240, 4320);

            _splitPreview = new Label
            {
                ForeColor = Color.FromArgb(190, 190, 190),
                AutoSize = true,
                Padding = new Padding(0, 10, 0, 10)
            };
            panel.Controls.Add(MakeLabel("分屏预览"), 0, 11);
            panel.Controls.Add(_splitPreview, 1, 11);

            _displayWidth.ValueChanged += (_, __) => UpdateSplitPreview();
            _displayHeight.ValueChanged += (_, __) => UpdateSplitPreview();
            _segmentWidth.ValueChanged += (_, __) => UpdateSplitPreview();
            _segmentHeight.ValueChanged += (_, __) => UpdateSplitPreview();

            var btnPanel = new FlowLayoutPanel
            {
                Dock = DockStyle.Fill,
                FlowDirection = FlowDirection.RightToLeft,
                AutoSize = true,
                BackColor = BackColor
            };

            var btnSave = MakeButton("保存", (_, __) => SaveAndClose());
            var btnCancel = MakeButton("取消", (_, __) => DialogResult = DialogResult.Cancel);
            var btnDefault = MakeButton("恢复默认", (_, __) => ResetDefault());
            var btnExit = MakeButton("退出程序", (_, __) => { DialogResult = DialogResult.Abort; Close(); });

            btnPanel.Controls.Add(btnSave);
            btnPanel.Controls.Add(btnCancel);
            btnPanel.Controls.Add(btnDefault);
            btnPanel.Controls.Add(btnExit);
            panel.Controls.Add(btnPanel, 0, 12);
            panel.SetColumnSpan(btnPanel, 2);
        }

        private void AddRow(TableLayoutPanel panel, int row, string label, out TextBox textBox)
        {
            var lbl = MakeLabel(label);
            textBox = new TextBox { Dock = DockStyle.Fill };
            panel.Controls.Add(lbl, 0, row);
            panel.Controls.Add(textBox, 1, row);
        }

        private void AddNumericRow(TableLayoutPanel panel, int row, string label, out NumericUpDown input, int min, int max)
        {
            var lbl = MakeLabel(label);
            input = new NumericUpDown
            {
                Dock = DockStyle.Left,
                Width = 140,
                Minimum = min,
                Maximum = max
            };
            panel.Controls.Add(lbl, 0, row);
            panel.Controls.Add(input, 1, row);
        }

        private Label MakeLabel(string text) => new Label
        {
            Text = text,
            ForeColor = Color.White,
            AutoSize = true,
            Anchor = AnchorStyles.Left,
            Margin = new Padding(0, 8, 0, 0)
        };

        private Button MakeButton(string text, EventHandler onClick)
        {
            var btn = new Button
            {
                Text = text,
                AutoSize = true,
                Margin = new Padding(8),
                ForeColor = Color.White,
                BackColor = Color.FromArgb(45, 45, 52),
                FlatStyle = FlatStyle.Flat
            };
            btn.FlatAppearance.BorderColor = Color.FromArgb(80, 80, 90);
            btn.Click += onClick;
            return btn;
        }

        private void LoadValues()
        {
            var ws = new Uri(_settings.WebSocketUrl);
            var fs = new Uri(_settings.FileServerUrl);
            _wsHost.Text = ws.Host;
            _wsPort.Value = ws.Port;
            _fileHost.Text = fs.Host;
            _filePort.Value = fs.Port;
            _mediaPath.Text = _settings.MediaPath;
            _screenNumber.Value = _settings.ScreenNumber ?? 0;
            _powerPointPath.Text = _settings.PowerPointPath ?? "";
            _displayWidth.Value = _settings.DisplayWidth;
            _displayHeight.Value = _settings.DisplayHeight;
            _segmentWidth.Value = _settings.SegmentWidth;
            _segmentHeight.Value = _settings.SegmentHeight;
        }

        private void ResetDefault()
        {
            _wsHost.Text = "192.168.16.61";
            _wsPort.Value = 3000;
            _fileHost.Text = "192.168.16.61";
            _filePort.Value = 3001;
            _mediaPath.Text = @"C:\media";
            _screenNumber.Value = 0;
            _powerPointPath.Text = @"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE";
            _displayWidth.Value = 1920;
            _displayHeight.Value = 1080;
            _segmentWidth.Value = 1920;
            _segmentHeight.Value = 1080;
            UpdateSplitPreview();
        }

        private void UpdateSplitPreview()
        {
            var totalWidth = (int)_displayWidth.Value;
            var totalHeight = (int)_displayHeight.Value;
            var segmentWidth = (int)_segmentWidth.Value;
            var segmentHeight = (int)_segmentHeight.Value;

            var horizontalCount = Math.Max(1, totalWidth / Math.Max(1, segmentWidth));
            var verticalCount = Math.Max(1, totalHeight / Math.Max(1, segmentHeight));
            var exactWidth = totalWidth % Math.Max(1, segmentWidth) == 0;
            var exactHeight = totalHeight % Math.Max(1, segmentHeight) == 0;
            var exact = exactWidth && exactHeight ? "整除" : "非整除";

            _splitPreview.Text =
                $"总分辨率 {totalWidth} x {totalHeight}，单块 {segmentWidth} x {segmentHeight}，可分为 {horizontalCount} x {verticalCount} 块，当前为{exact}";
        }

        private void SaveAndClose()
        {
            _settings.WebSocketUrl = $"ws://{_wsHost.Text}:{_wsPort.Value}";
            _settings.FileServerUrl = $"http://{_fileHost.Text}:{_filePort.Value}";
            _settings.MediaPath = _mediaPath.Text;
            _settings.ScreenNumber = _screenNumber.Value > 0 ? (int)_screenNumber.Value : null;
            _settings.PowerPointPath = _powerPointPath.Text.Trim();
            _settings.DisplayWidth = (int)_displayWidth.Value;
            _settings.DisplayHeight = (int)_displayHeight.Value;
            _settings.SegmentWidth = (int)_segmentWidth.Value;
            _settings.SegmentHeight = (int)_segmentHeight.Value;
            _settings.Save();
            DialogResult = DialogResult.OK;
            Close();
        }
    }
}
