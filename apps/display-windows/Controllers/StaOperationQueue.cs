using System.Windows.Forms;
namespace ExhibitionClient.Controllers;

// A blocked COM call cannot block this queue's independent timeout timer.
internal sealed class StaOperationQueue : IDisposable
{
    private sealed record Work(Action<CancellationToken> Run, Action<Exception> Fail, CancellationTokenSource Stop);
    private readonly object _gate = new();
    private readonly Queue<Work> _pending = new();
    private Exception _fault;
    private bool _disposed;
    private bool _executing;
    private Action _cleanup;
    private readonly Thread _thread;
    public bool IsFaulted { get { lock (_gate) return _fault != null; } }
    public StaOperationQueue()
    {
        var thread = _thread = new Thread(Loop) { IsBackground = true, Name = "PowerPoint STA" };
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
    }
    public Task<T> Enqueue<T>(Func<CancellationToken, T> action, TimeSpan timeout, Action recover = null)
    {
        var result = new TaskCompletionSource<T>(TaskCreationOptions.RunContinuationsAsynchronously);
        var stop = new CancellationTokenSource();
        lock (_gate)
        {
            var recovering = false;
            if (!_disposed && _fault != null && recover != null)
            {
                if (_executing)
                {
                    stop.Dispose();
                    return Task.FromException<T>(new InvalidOperationException("PowerPoint 上一条调用仍未结束，请稍后重新打开文件；不会补发旧操作。"));
                }
                // FaultLocked discarded the old backlog. Only a fresh explicit open may recover,
                // after the previous COM call has physically returned on this same STA.
                _fault = null;
                recovering = true;
            }
            if (_disposed || _fault != null || _pending.Count >= 32)
            {
                stop.Dispose();
                return Task.FromException<T>(_fault ?? new InvalidOperationException("PPT 控制已停止或队列已满"));
            }
            _pending.Enqueue(new Work(token =>
            {
                using var timer = new System.Threading.Timer(_ =>
                {
                    lock (_gate)
                    {
                        if (result.Task.IsCompleted) return;
                        var error = new TimeoutException("PowerPoint 执行超时，已取消排队操作；待 PowerPoint 恢复后请重新打开文件。不会自动补发翻页。");
                        stop.Cancel();
                        result.TrySetException(error);
                        FaultLocked(error);
                    }
                }, null, timeout, Timeout.InfiniteTimeSpan);
                try
                {
                    if (recovering) recover();
                    token.ThrowIfCancellationRequested();
                    var value = action(token);
                    lock (_gate) result.TrySetResult(value);
                }
                catch (Exception ex)
                {
                    lock (_gate)
                    {
                        result.TrySetException(ex);
                        if (ex is not FileNotFoundException && ex is not OperationCanceledException) FaultLocked(ex);
                    }
                }
            }, ex => result.TrySetException(ex), stop));
        }
        return result.Task;
    }
    private void FaultLocked(Exception error)
    {
        _fault ??= error;
        while (_pending.TryDequeue(out var item))
        {
            item.Fail(new InvalidOperationException("PPT 前一操作失败，取消排队操作，防止恢复后连跳", error));
            item.Stop.Dispose();
        }
    }
    private void Loop()
    {
        while (true)
        {
            Work work = null;
            lock (_gate)
            {
                if (_disposed) break;
                if (_pending.Count > 0)
                {
                    work = _pending.Dequeue();
                    _executing = true;
                }
            }
            if (work != null)
            {
                try { work.Run(work.Stop.Token); }
                finally
                {
                    work.Stop.Dispose();
                    lock (_gate) _executing = false;
                }
            }
            Application.DoEvents();
            Thread.Sleep(10);
        }
        try { _cleanup?.Invoke(); } catch { }
    }
    public void DisposeWithCleanup(Action cleanup)
    {
        lock (_gate)
        {
            _cleanup = cleanup;
            _disposed = true;
            FaultLocked(new ObjectDisposedException(nameof(StaOperationQueue)));
        }
        if (Thread.CurrentThread != _thread) _thread.Join(1500);
    }
    public void Dispose() => DisposeWithCleanup(null);
}
