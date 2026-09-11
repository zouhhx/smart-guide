using System;
using System.Drawing;
using System.Windows.Forms;

namespace ExhibitionClient.Views
{
    /// <summary>
    /// 日志悬浮窗，按 F10 显示或隐藏。
    /// </summary>
    public class LogForm : Form
    {
        private RichTextBox _logBox = null!;
        private Button _btnClear = null!;
        private Button _btnClose = null!;
        private static LogForm? _instance;

        public static LogForm Instance => _instance ??= new LogForm();
        public static void AppendIfCreated(string level, string message) => _instance?.AppendLog(level, message);

        private LogForm()
        {
            InitializeComponent();
        }

        private void InitializeComponent()
        {
            Text = "运行日志";
            Size = new Size(900, 500);
            StartPosition = FormStartPosition.Manual;
            Location = new Point(0, Screen.PrimaryScreen!.WorkingArea.Height - 500);
            BackColor = Color.FromArgb(18, 18, 18);
            ForeColor = Color.FromArgb(200, 200, 200);
            FormBorderStyle = FormBorderStyle.SizableToolWindow;
            TopMost = true;
            ShowInTaskbar = false;

            FormClosing += (s, e) =>
            {
                e.Cancel = true;
                Hide();
            };

            var toolbar = new Panel
            {
                Dock = DockStyle.Top,
                Height = 36,
                BackColor = Color.FromArgb(30, 30, 30),
                Padding = new Padding(6, 4, 6, 4)
            };

            _btnClear = new Button
            {
                Text = "清空",
                Size = new Size(60, 26),
                Location = new Point(6, 5),
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(50, 50, 50),
                ForeColor = Color.White,
                Font = new Font("Microsoft YaHei UI", 9F),
                Cursor = Cursors.Hand
            };
            _btnClear.FlatAppearance.BorderColor = Color.FromArgb(80, 80, 80);
            _btnClear.Click += (s, e) => _logBox.Clear();
            toolbar.Controls.Add(_btnClear);

            _btnClose = new Button
            {
                Text = "隐藏 (F10)",
                Size = new Size(90, 26),
                Location = new Point(74, 5),
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(50, 50, 50),
                ForeColor = Color.White,
                Font = new Font("Microsoft YaHei UI", 9F),
                Cursor = Cursors.Hand
            };
            _btnClose.FlatAppearance.BorderColor = Color.FromArgb(80, 80, 80);
            _btnClose.Click += (s, e) => Hide();
            toolbar.Controls.Add(_btnClose);

            _logBox = new RichTextBox
            {
                Dock = DockStyle.Fill,
                BackColor = Color.FromArgb(18, 18, 18),
                ForeColor = Color.FromArgb(200, 200, 200),
                Font = new Font("Consolas", 10F),
                ReadOnly = true,
                ScrollBars = RichTextBoxScrollBars.Vertical,
                BorderStyle = BorderStyle.None,
                WordWrap = false
            };

            Controls.Add(_logBox);
            Controls.Add(toolbar);
        }

        /// <summary>
        /// 追加一条日志，线程安全。
        /// </summary>
        public void AppendLog(string level, string message)
        {
            if (IsDisposed || Disposing || !Visible)
                return;

            if (InvokeRequired)
            {
                if (IsHandleCreated)
                    BeginInvoke(new Action(() => AppendLog(level, message)));
                return;
            }

            if (IsDisposed || Disposing || !Visible || _logBox.IsDisposed)
                return;

            var ts = DateTime.Now.ToString("HH:mm:ss");
            var line = $"[{ts}] [{level}] {message}\n";

            _logBox.AppendText(line);

            if (_logBox.Lines.Length > 1000)
            {
                var cutIndex = _logBox.GetFirstCharIndexFromLine(200);
                if (cutIndex > 0)
                    _logBox.Text = _logBox.Text.Substring(cutIndex);
            }
        }

        public void ToggleVisible()
        {
            if (Visible) Hide();
            else Show();
        }
    }
}
