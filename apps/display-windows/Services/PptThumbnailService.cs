using System;
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace ExhibitionClient.Services
{
    public sealed class PptThumbnailService : IDisposable
    {
        private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(15) };
        private readonly SemaphoreSlim _generationSlots = new(2, 2);
        private readonly string _baseUrl;

        public PptThumbnailService(string baseUrl)
        {
            _baseUrl = (baseUrl ?? string.Empty).TrimEnd('/');
        }

        public async Task<byte[]?> GetFirstPageAsync(string relativePath, CancellationToken cancellationToken)
        {
            if (string.IsNullOrWhiteSpace(_baseUrl) || string.IsNullOrWhiteSpace(relativePath))
                return null;

            var normalizedPath = relativePath.Replace('\\', '/').TrimStart('/');
            await _generationSlots.WaitAsync(cancellationToken);
            try
            {
                for (var attempt = 0; attempt < 90; attempt++)
                {
                    using var response = await _http.GetAsync(
                        $"{_baseUrl}/api/ppt-thumbnails?name={Uri.EscapeDataString(normalizedPath)}",
                        cancellationToken);
                    var json = await response.Content.ReadAsStringAsync(cancellationToken);
                    using var document = JsonDocument.Parse(json);
                    var root = document.RootElement;
                    var status = root.TryGetProperty("status", out var statusNode)
                        ? statusNode.GetString() ?? string.Empty
                        : string.Empty;

                    if (string.Equals(status, "READY", StringComparison.OrdinalIgnoreCase))
                    {
                        if (!root.TryGetProperty("pages", out var pages) || pages.ValueKind != JsonValueKind.Array)
                            return null;

                        var firstPage = pages.EnumerateArray().FirstOrDefault();
                        if (firstPage.ValueKind != JsonValueKind.Object ||
                            !firstPage.TryGetProperty("url", out var urlNode))
                            return null;

                        var url = urlNode.GetString();
                        if (string.IsNullOrWhiteSpace(url))
                            return null;

                        return await _http.GetByteArrayAsync(new Uri(new Uri(_baseUrl + "/"), url), cancellationToken);
                    }

                    if (string.Equals(status, "FAILED", StringComparison.OrdinalIgnoreCase))
                    {
                        var error = root.TryGetProperty("error", out var errorNode) ? errorNode.GetString() : "unknown";
                        Logger.Warn($"[Thumbnail] PPT 缩略图生成失败: file={normalizedPath}, error={error}");
                        return null;
                    }

                    await Task.Delay(1000, cancellationToken);
                }

                Logger.Warn($"[Thumbnail] PPT 缩略图生成超时: file={normalizedPath}");
                return null;
            }
            catch (OperationCanceledException)
            {
                return null;
            }
            catch (Exception ex)
            {
                Logger.Warn($"[Thumbnail] PPT 缩略图读取失败: file={normalizedPath}, type={ex.GetType().Name}, message={ex.Message}");
                return null;
            }
            finally
            {
                _generationSlots.Release();
            }
        }

        public void Dispose()
        {
            _http.Dispose();
            // Pending canceled requests still release their slot in finally.
        }
    }
}
