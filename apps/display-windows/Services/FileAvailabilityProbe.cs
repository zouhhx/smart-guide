namespace ExhibitionClient.Services;

// File.Exists can wait for Windows network timeouts. Never run it on the UI thread.
// Keep the gate until the physical call returns: no timed-out orphan probes.
internal static class FileAvailabilityProbe
{
    private static readonly SemaphoreSlim Gate = new(1, 1);

    internal static async Task<bool> ExistsAsync(string path, Func<string, bool> exists = null)
    {
        await Gate.WaitAsync().ConfigureAwait(false);
        try
        {
            return await Task.Run(() => (exists ?? File.Exists)(path)).ConfigureAwait(false);
        }
        finally { Gate.Release(); }
    }
}
