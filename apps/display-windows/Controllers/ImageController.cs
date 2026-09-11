using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace ExhibitionClient.Controllers
{
    /// <summary>
    /// 图片/文档展示控制器
    /// </summary>
    public class ImageController : IDisposable
    {
        private readonly PictureBox _pictureBox;
        private readonly Panel _container;
        private readonly string _mediaPath;
        private Image? _currentImage;
        private MemoryStream? _currentStream;
        private int _currentIndex;
        private string[]? _imageFiles;

        public event Action? OnImageChanged;

        public PictureBox PictureBox => _pictureBox;
        public Panel Container => _container;
        public int CurrentIndex => _currentIndex;
        public int TotalImages => _imageFiles?.Length ?? 0;

        public ImageController(string mediaPath = @"C:\media")
        {
            _mediaPath = mediaPath;

            // 创建容器
            _container = new Panel
            {
                Dock = DockStyle.Fill,
                BackColor = Color.FromArgb(26, 26, 26),
                Visible = false
            };

            // 创建图片框
            _pictureBox = new PictureBox
            {
                Dock = DockStyle.Fill,
                SizeMode = PictureBoxSizeMode.Zoom,
                BackColor = Color.Black
            };

            _container.Controls.Add(_pictureBox);
        }

        /// <summary>
        /// 显示单张图片（异步加载，不阻塞 UI）
        /// </summary>
        public void ShowImage(string fileName)
        {
            var localPath = GetLocalPath(fileName);
            Services.Logger.Info($"[Image] ShowImage: {fileName} -> {localPath ?? "NOT FOUND"}");
            
            if (string.IsNullOrEmpty(localPath) || !File.Exists(localPath))
            {
                Services.Logger.Warn($"[Image] 文件不存在: {fileName}");
                return;
            }

            _currentIndex = 1;
            _imageFiles = new[] { localPath };

            LoadImageAsync(localPath);
        }

        /// <summary>
        /// 显示文件夹中的图片（可翻页）
        /// </summary>
        public void ShowFolder(string folderPath, string pattern = "*.jpg|*.png|*.jpeg|*.gif|*.bmp")
        {
            try
            {
                if (!Directory.Exists(folderPath))
                {
                    Console.WriteLine($"[Image] 文件夹不存在: {folderPath}");
                    return;
                }

                var extensions = pattern.Split('|');
                var files = new System.Collections.Generic.List<string>();
                
                foreach (var ext in extensions)
                {
                    files.AddRange(Directory.GetFiles(folderPath, ext));
                }

                if (files.Count == 0)
                {
                    Console.WriteLine($"[Image] 文件夹为空: {folderPath}");
                    return;
                }

                _imageFiles = files.ToArray();
                _currentIndex = 0;
                
                ShowCurrentImage();
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[Image] 加载文件夹失败: {ex.Message}");
            }
        }

        /// <summary>
        /// 下一张
        /// </summary>
        public void Next()
        {
            if (_imageFiles == null || _imageFiles.Length == 0)
                return;

            _currentIndex = (_currentIndex + 1) % _imageFiles.Length;
            ShowCurrentImage();
        }

        /// <summary>
        /// 上一张
        /// </summary>
        public void Prev()
        {
            if (_imageFiles == null || _imageFiles.Length == 0)
                return;

            _currentIndex = (_currentIndex - 1 + _imageFiles.Length) % _imageFiles.Length;
            ShowCurrentImage();
        }

        /// <summary>
        /// 跳转到指定图片
        /// </summary>
        public void GoTo(int index)
        {
            if (_imageFiles == null || index < 0 || index >= _imageFiles.Length)
                return;

            _currentIndex = index;
            ShowCurrentImage();
        }

        private void ShowCurrentImage()
        {
            if (_imageFiles == null || _imageFiles.Length == 0)
                return;

            var file = _imageFiles[_currentIndex];
            Console.WriteLine($"[Image] {_currentIndex + 1}/{_imageFiles.Length}: {Path.GetFileName(file)}");
            LoadImageAsync(file);
        }

        /// <summary>
        /// 后台加载图片，用 FromStream 避免锁文件，加载完再切回 UI 线程
        /// </summary>
        private void LoadImageAsync(string filePath)
        {
            // 直接在调用线程（已是 UI 线程，由 BeginInvoke 保证）同步加载
            // 避免二次跨线程调度导致句柄未就绪问题
            try
            {
                if (!File.Exists(filePath))
                {
                    Services.Logger.Warn($"[Image] 文件不存在: {filePath}");
                    return;
                }

                Services.Logger.Info($"[Image] 开始加载: {System.IO.Path.GetFileName(filePath)}");

                // 读入内存，不持有文件锁；MemoryStream 不能释放，GDI+ 渲染时仍需访问
                byte[] bytes = File.ReadAllBytes(filePath);
                var ms = new MemoryStream(bytes);
                var img = Image.FromStream(ms, false, true);

                var old = _currentImage;
                var oldStream = _currentStream;
                _currentImage = img;
                _currentStream = ms;
                _container.Visible = true;
                _pictureBox.Image = _currentImage;
                old?.Dispose();
                oldStream?.Dispose();
                OnImageChanged?.Invoke();

                Services.Logger.Info($"[Image] 显示成功: {System.IO.Path.GetFileName(filePath)}");
            }
            catch (Exception ex)
            {
                Services.Logger.Error($"[Image] 加载失败: {ex.GetType().Name} - {ex.Message}");
            }
        }

        /// <summary>
        /// 隐藏图片
        /// </summary>
        public void Hide()
        {
            _container.Visible = false;
        }

        /// <summary>
        /// 放大
        /// </summary>
        public void ZoomIn()
        {
            _pictureBox.SizeMode = PictureBoxSizeMode.Zoom;
        }

        /// <summary>
        /// 缩小
        /// </summary>
        public void ZoomOut()
        {
            _pictureBox.SizeMode = PictureBoxSizeMode.Zoom;
        }

        /// <summary>
        /// 适应窗口
        /// </summary>
        public void FitToWindow()
        {
            _pictureBox.SizeMode = PictureBoxSizeMode.Zoom;
        }

        private string? GetLocalPath(string fileName)
        {
            if (File.Exists(fileName))
                return fileName;
            
            var path = Path.Combine(_mediaPath, Path.GetFileName(fileName));
            return File.Exists(path) ? path : null;
        }

        public void Dispose()
        {
            _currentImage?.Dispose();
            _currentStream?.Dispose();
            _pictureBox?.Dispose();
            _container?.Dispose();
        }
    }
}
