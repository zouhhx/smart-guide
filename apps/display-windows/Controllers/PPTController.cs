using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using ExhibitionClient.Services;
namespace ExhibitionClient.Controllers;

/// <summary>All Office objects belong exclusively to the worker STA.</summary>
public sealed class PPTController : IDisposable
{
    private readonly StaOperationQueue _queue = new();
    private readonly string _mediaPath;
    private readonly System.Threading.Timer _maintenance;
    private dynamic _application, _presentation, _show;
    private IntPtr _showWindow;
    private string _stamp = "";
    private long _idleSince = Stopwatch.GetTimestamp(), _lowMemorySince;
    private int _maintenancePending, _slide = 1, _count;
    private volatile bool _opened;
    private volatile string _file;
    private int _navigationPending;
    public bool IsNavigating => Volatile.Read(ref _navigationPending) != 0;
    public event Action OnOpened;
    public event Action OnClosed;
    public event Action<string> OnFaulted;
    public bool IsOpened => _opened;
    public bool HasActivePowerPoint => _opened;
    public int CurrentSlide => Volatile.Read(ref _slide);
    public string CurrentFile => _file;
    public bool IsFaulted => _queue.IsFaulted;
    public PPTController(string mediaPath = @"C:\media")
    {
        _mediaPath = mediaPath;
        _maintenance = new System.Threading.Timer(_ => Maintain(), null, 1000, 1000);
    }
    private Task<int> Execute(string action, Func<CancellationToken, int> operation, int timeoutSeconds = 5, long receivedAt = 0, string correlation = "", Action recover = null)
    {
        var received = receivedAt == 0 ? Stopwatch.GetTimestamp() : receivedAt;
        Logger.Info($"[PPT-Control] received action={action} id={correlation} tick={received}");
        if (_queue.IsFaulted && recover == null)
        {
            Logger.Warn($"[PPT-Control] rejected-faulted action={action} id={correlation}");
            return Task.FromException<int>(new InvalidOperationException("PPT 控制已停止，请等待 PowerPoint 恢复后重新打开文件"));
        }
        return ObserveAsync(_queue.Enqueue(token =>
        {
            var started = Stopwatch.GetTimestamp();
            Logger.Info($"[PPT-Control] execute action={action} id={correlation} queueMs={Stopwatch.GetElapsedTime(received).TotalMilliseconds:F1}");
            var before = CurrentSlide;
            var result = operation(token);
            token.ThrowIfCancellationRequested();
            Volatile.Write(ref _slide, result);
            Logger.Info($"[PPT-Control] confirmed action={action} id={correlation} before={before} slide={result} changed={before != result} executionMs={Stopwatch.GetElapsedTime(started).TotalMilliseconds:F1} totalMs={Stopwatch.GetElapsedTime(received).TotalMilliseconds:F1}");
            return result;
        }, TimeSpan.FromSeconds(timeoutSeconds), recover), action, correlation);
    }
    private static async Task<int> ObserveAsync(Task<int> task, string action, string correlation)
    {
        try { return await task.ConfigureAwait(false); }
        catch (Exception ex)
        {
            Logger.Error($"[PPT-Control] {(ex is TimeoutException ? "timeout" : "failed")} action={action} id={correlation} error={ex.Message}");
            throw;
        }
    }
    public Task<int> OpenAsync(string filePath, CancellationToken cancellationToken = default) => Execute("open", token =>
    {
        cancellationToken.ThrowIfCancellationRequested();
        var path = Path.IsPathRooted(filePath) ? filePath : Path.Combine(_mediaPath, Path.GetFileName(filePath));
        var info = new FileInfo(path);
        if (!info.Exists) throw new FileNotFoundException("PPT 文件不存在", path);
        var stamp = $"{info.FullName}|{info.Length}|{info.LastWriteTimeUtc.Ticks}";
        if (_opened && string.Equals(_stamp, stamp, StringComparison.OrdinalIgnoreCase))
        {
            _show.Activate();
            return ReadSlide();
        }
        var officeStarted = Stopwatch.GetTimestamp();
        if (_application == null)
        {
            var type = Type.GetTypeFromProgID("PowerPoint.Application") ?? throw new InvalidOperationException("未安装或注册 Microsoft PowerPoint");
            _application = Activator.CreateInstance(type);
        }
        Logger.Info($"[PPT-Open] officeReadyMs={Stopwatch.GetElapsedTime(officeStarted).TotalMilliseconds:F1}");
        token.ThrowIfCancellationRequested();
        cancellationToken.ThrowIfCancellationRequested();
        dynamic presentations = _application.Presentations;
        dynamic next = null;
        try
        {
            next = presentations.Open(info.FullName, -1, 0, 0);
            token.ThrowIfCancellationRequested();
            cancellationToken.ThrowIfCancellationRequested();
            CloseDocument();
            _presentation = next;
            next = null;
            dynamic slides = _presentation.Slides;
            try { _count = slides.Count; } finally { Release(slides); }
            dynamic settings = _presentation.SlideShowSettings;
            var previousWindows = FindSlideShowWindows();
            try { _show = settings.Run(); } finally { Release(settings); }
            token.ThrowIfCancellationRequested();
            cancellationToken.ThrowIfCancellationRequested();
            _show.Activate();
            var newWindows = FindSlideShowWindows().Except(previousWindows).ToArray();
            _showWindow = newWindows.Length == 1 ? newWindows[0] : IntPtr.Zero;
            Logger.Info($"[PPT] native-window tracked={_showWindow != IntPtr.Zero} handle={_showWindow}");
            _file = info.Name;
            _stamp = stamp;
            var actual = ReadSlide();
            _opened = true;
            OnOpened?.Invoke();
            return actual;
        }
        catch
        {
            if (next != null) { try { next.Close(); } finally { Release(next); } }
            if (token.IsCancellationRequested || cancellationToken.IsCancellationRequested) CloseDocument();
            throw;
        }
        finally { Release(presentations); }
    }, 30, recover: RecoverForOpen);
    private void RecoverForOpen()
    {
        Logger.Info("[PPT-Control] recovery-start: fresh open after previous call returned; old commands discarded");
        CloseDocument();
        ReleaseApplication();
        Logger.Info("[PPT-Control] recovery-ready: owned presentation closed and Office references released");
    }
    public Task<int> NextAsync(long received = 0, string id = "") => MoveAsync(1, null, received, id);
    public Task<int> PrevAsync(long received = 0, string id = "") => MoveAsync(-1, null, received, id);
    public Task<int> GotoAsync(int page, long received = 0, string id = "") => MoveAsync(0, page, received, id);
    private async Task<int> MoveAsync(int delta, int? absolute, long received, string id)
    {
        if (Interlocked.CompareExchange(ref _navigationPending, 1, 0) != 0)
            throw new InvalidOperationException("PowerPoint 正在翻页，请等当前页显示后再操作；本次重复操作未排队。");
        try { return await Execute(absolute.HasValue ? "goto" : delta > 0 ? "next" : "prev", token =>
    {
        if (!_opened || _show == null) throw new InvalidOperationException("没有就绪的 PPT 放映窗口");
        var before = ReadSlide();
        var target = Math.Clamp(absolute ?? before + delta, 1, _count);
        if (target == before) return before;
        dynamic view = _show.View;
        try
        {
            token.ThrowIfCancellationRequested();
            Logger.Info($"[PPT-Control] goto-request before={before} target={target} tick={Stopwatch.GetTimestamp()}");
            view.GotoSlide(target, -1);
            Logger.Info($"[PPT-Control] goto-returned target={target} tick={Stopwatch.GetTimestamp()}");
            token.ThrowIfCancellationRequested();
            var actual = ReadSlide();
            if (actual != target) throw new InvalidOperationException($"PPT 页码未确认：目标 {target}，实际 {actual}");
            return actual;
        }
        finally { Release(view); }
    // Media-heavy pages on the exhibition PC take 7-8 seconds. Keep the hard
    // watchdog, but do not classify a normal render as a permanent COM failure.
    }, timeoutSeconds: 30, receivedAt: received, correlation: id).ConfigureAwait(false); }
        finally { Volatile.Write(ref _navigationPending, 0); }
    }
    private int ReadSlide()
    {
        dynamic view = _show.View;
        dynamic slide = null;
        try { slide = view.Slide; return (int)slide.SlideIndex; }
        finally { Release(slide); Release(view); }
    }
    // Explicit close is also a recovery request, but never races an executing COM call.
    public Task<int> CloseAsync() => Execute("close", _ => { CloseDocument(); return 1; }, 10,
        recover: () => Logger.Info("[PPT-Control] recovery-close: previous call returned; closing owned document"));
    public async void Close()
    {
        try { await CloseAsync(); }
        catch (Exception ex) { Logger.Error($"[PPT] 关闭未完成: {ex.Message}"); }
    }
    private void CloseDocument()
    {
        if (_presentation != null && (_showWindow == IntPtr.Zero || IsWindow(_showWindow))) _presentation.Close();
        ForgetDocument();
    }
    private void ForgetDocument()
    {
        Release(_show); _show = null;
        Release(_presentation); _presentation = null;
        _opened = false; _file = null; _stamp = "";
        _showWindow = IntPtr.Zero;
        _idleSince = Stopwatch.GetTimestamp();
    }
    private async void Maintain()
    {
        var recoveringClosedWindow = _queue.IsFaulted;
        if (recoveringClosedWindow && (!_opened || _showWindow == IntPtr.Zero || IsWindow(_showWindow))) return;
        if (Interlocked.Exchange(ref _maintenancePending, 1) != 0) return;
        try
        {
            await _queue.Enqueue(_ =>
            {
                if (_opened && _show != null)
                {
                    // Never poll Office COM while it is rendering media. A slow status read
                    // used to fault the command queue and cover an otherwise live slideshow.
                    if (_showWindow != IntPtr.Zero && !IsWindow(_showWindow))
                    {
                        Logger.Info("[PPT] owned slideshow window was destroyed; restoring home");
                        // Office can destroy the hidden presentation together with its show.
                        // Do not call Close again on that now-invalid COM object.
                        ForgetDocument();
                        OnClosed?.Invoke();
                    }
                }
                if (!_opened && _application != null)
                {
                    var memory = new MemoryStatus { Length = (uint)Marshal.SizeOf<MemoryStatus>() };
                    bool low = GlobalMemoryStatusEx(ref memory) && memory.AvailablePhysical < 1024UL * 1024 * 1024;
                    if (!low) _lowMemorySince = 0;
                    else if (_lowMemorySince == 0) _lowMemorySince = Stopwatch.GetTimestamp();
                    if (Stopwatch.GetElapsedTime(_idleSince).TotalMinutes >= 5 ||
                        (_lowMemorySince != 0 && Stopwatch.GetElapsedTime(_lowMemorySince).TotalSeconds >= 15)) ReleaseApplication();
                }
                return 0;
            }, TimeSpan.FromSeconds(5), recover: recoveringClosedWindow ? () => { } : null);
        }
        catch (Exception ex)
        {
            if (recoveringClosedWindow)
            {
                Logger.Warn($"[PPT] exited window cleanup waiting for previous call: {ex.Message}");
                return;
            }
            Logger.Error($"[PPT] 状态检查失败，停止控制: {ex.Message}");
            OnFaulted?.Invoke("PowerPoint 状态检查失败，已停止翻页。待 PowerPoint 恢复后请重新打开文件。");
        }
        finally { Interlocked.Exchange(ref _maintenancePending, 0); }
    }
    private void ReleaseApplication()
    {
        if (_application == null) return;
        dynamic documents = _application.Presentations;
        try { if ((int)documents.Count == 0) _application.Quit(); }
        finally { Release(documents); Release(_application); _application = null; }
        Logger.Info("[PPT] 已释放空闲 Office 引用");
    }
    public void PresentWindow()
    {
        var window = _showWindow;
        if (!_opened || window == IntPtr.Zero || !IsWindow(window))
            throw new InvalidOperationException("PPT 放映窗口尚未就绪");
        // Native ordering only: no Office COM call on the UI thread.
        var avatars = new HashSet<IntPtr>();
        using var current = Process.GetCurrentProcess();
        foreach (var avatar in Process.GetProcessesByName("有灵屏型智能体"))
        {
            using (avatar) { try { if (avatar.SessionId == current.SessionId && avatar.MainWindowHandle != IntPtr.Zero) avatars.Add(avatar.MainWindowHandle); } catch (InvalidOperationException) { } }
        }
        ShowWindow(window, 5);
        var success = SetForegroundWindow(window);
        SetWindowPos(window, new IntPtr(-1), 0, 0, 0, 0, 0x13);
        if (avatars.Count == 1) SetWindowPos(window, avatars.Single(), 0, 0, 0, 0, 0x13);
        Logger.Info($"[PPT-Open] native handoff window={window}, foreground={success}");
    }
    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] private static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int width, int height, uint flags);
    private static void Release(object value)
    {
        if (value != null && Marshal.IsComObject(value)) Marshal.ReleaseComObject(value);
    }
    public void Dispose()
    {
        _maintenance.Dispose();
        _queue.DisposeWithCleanup(() => { CloseDocument(); ReleaseApplication(); });
    }
    [DllImport("user32.dll")] private static extern bool IsWindow(IntPtr hwnd);
    private static HashSet<IntPtr> FindSlideShowWindows()
    {
        var result = new HashSet<IntPtr>();
        EnumWindows((window, _) =>
        {
            var name = new StringBuilder(256);
            GetClassName(window, name, name.Capacity);
            if (!string.Equals(name.ToString(), "screenClass", StringComparison.OrdinalIgnoreCase)) return true;
            GetWindowThreadProcessId(window, out var processId);
            try
            {
                using var process = Process.GetProcessById((int)processId);
                if (string.Equals(process.ProcessName, "POWERPNT", StringComparison.OrdinalIgnoreCase)) result.Add(window);
            }
            catch (ArgumentException) { }
            return true;
        }, IntPtr.Zero);
        return result;
    }
    private delegate bool WindowCallback(IntPtr window, IntPtr state);
    [DllImport("user32.dll")] private static extern bool EnumWindows(WindowCallback callback, IntPtr state);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetClassName(IntPtr window, StringBuilder name, int capacity);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("kernel32.dll")] private static extern bool GlobalMemoryStatusEx(ref MemoryStatus status);
    [StructLayout(LayoutKind.Sequential)] private struct MemoryStatus
    {
        public uint Length, Load;
        public ulong TotalPhysical, AvailablePhysical, TotalPageFile, AvailablePageFile, TotalVirtual, AvailableVirtual, AvailableExtendedVirtual;
    }
}
