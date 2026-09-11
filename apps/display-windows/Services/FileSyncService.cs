using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace ExhibitionClient.Services
{
    /// <summary>
    /// 文件同步服务，从服务端下载媒体文件到本地。
    /// </summary>
    public class FileSyncService : IDisposable
    {
        private readonly string _fileServerUrl;
        private readonly string _localPath;
        private readonly HttpClient _http;
        private readonly HashSet<string> _syncedFiles = new();

        public FileSyncService(string fileServerUrl, string localPath)
        {
            _fileServerUrl = fileServerUrl.TrimEnd('/');
            _localPath = localPath;
            _http = new HttpClient { Timeout = TimeSpan.FromMinutes(10) };

            if (!Directory.Exists(_localPath))
                Directory.CreateDirectory(_localPath);
        }

        /// <summary>
        /// 获取文件列表并执行全量同步。
        /// </summary>
        public async Task SyncAllAsync()
        {
            try
            {
                Logger.Info($"[Sync] 开始同步文件，服务地址: {_fileServerUrl}, 本地目录: {_localPath}");

                var response = await _http.GetStringAsync($"{_fileServerUrl}/list");
                var data = JsonSerializer.Deserialize<FileListResponse>(response,
                    new JsonSerializerOptions { PropertyNameCaseInsensitive = true });

                if (data?.Files == null)
                {
                    Logger.Warn($"[Sync] 文件列表为空或接口格式异常: {_fileServerUrl}/list");
                    return;
                }

                using var semaphore = new SemaphoreSlim(3);
                var tasks = data.Files.Select(async f =>
                {
                    await semaphore.WaitAsync();
                    try
                    {
                        string fileName = f.Name;
                        if (string.IsNullOrWhiteSpace(fileName) && !string.IsNullOrEmpty(f.Url))
                        {
                            try
                            {
                                var decoded = Uri.UnescapeDataString(f.Url.Split('/').Last());
                                if (!string.IsNullOrEmpty(decoded))
                                    fileName = decoded;
                            }
                            catch
                            {
                            }
                        }

                        return await DownloadFileAsync(fileName, f.Url);
                    }
                    finally
                    {
                        semaphore.Release();
                    }
                });

                await Task.WhenAll(tasks);
                Logger.Info($"[Sync] 同步完成，共 {data.Files.Count} 个文件");
            }
            catch (Exception ex)
            {
                Logger.Error($"[Sync] 同步失败: server={_fileServerUrl}, local={_localPath}, type={ex.GetType().Name}, message={ex.Message}");
            }
        }

        /// <summary>
        /// 下载单个文件。
        /// </summary>
        public async Task<string?> DownloadFileAsync(string fileName, string? fileUrl)
        {
            var localPath = Path.Combine(_localPath, fileName);

            try
            {
                if ((_syncedFiles.Contains(fileName) || File.Exists(localPath)) && new FileInfo(localPath).Length > 0)
                {
                    _syncedFiles.Add(fileName);
                    return localPath;
                }

                Logger.Info($"[Sync] 下载文件: {fileName}");

                var url = fileUrl?.StartsWith("http") == true ? fileUrl : $"{_fileServerUrl}/{Uri.EscapeDataString(fileName)}";
                using var response = await _http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead);

                if (response.IsSuccessStatusCode)
                {
                    await using var source = await response.Content.ReadAsStreamAsync();
                    await using var destination = File.Create(localPath);
                    await source.CopyToAsync(destination);
                    _syncedFiles.Add(fileName);
                    Logger.Info($"[Sync] 下载完成: {fileName}");
                }
                else
                {
                    Logger.Warn($"[Sync] 下载失败: file={fileName}, url={url}, status={(int)response.StatusCode} {response.StatusCode}");
                }

                return localPath;
            }
            catch (Exception ex)
            {
                Logger.Error($"[Sync] 下载异常: file={fileName}, target={localPath}, type={ex.GetType().Name}, message={ex.Message}");
                return null;
            }
        }

        public string? GetLocalPath(string fileName)
        {
            var localPath = Path.Combine(_localPath, fileName);
            return File.Exists(localPath) ? localPath : null;
        }

        public bool FileExists(string fileName)
        {
            return File.Exists(Path.Combine(_localPath, fileName));
        }

        private class FileListResponse
        {
            public List<FileItem> Files { get; set; } = new();
        }

        private class FileItem
        {
            public string Name { get; set; } = string.Empty;
            public string? Url { get; set; }
        }

        public void Dispose()
        {
            _http?.Dispose();
        }
    }
}
