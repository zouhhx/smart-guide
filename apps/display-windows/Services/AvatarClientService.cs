using System;
using System.Diagnostics;
using System.IO;
using System.Linq;

namespace ExhibitionClient.Services
{
    /// <summary>
    /// Controls the MoFa client that belongs to this display computer.
    /// The central controller guarantees that only the selected screen is active.
    /// </summary>
    public sealed class AvatarClientService : IDisposable
    {
        private readonly string _installRoot;
        private readonly string _configuredExecutable;
        private readonly string _userDataPath;
        private readonly object _sync = new();

        public AvatarClientService(RuntimeSettings settings)
        {
            _installRoot = string.IsNullOrWhiteSpace(settings?.AvatarInstallRoot)
                ? @"D:\MofaAvatar\云播客户端2026Ai_1.1"
                : settings.AvatarInstallRoot.Trim();
            _configuredExecutable = settings?.AvatarExecutablePath?.Trim() ?? string.Empty;
            _userDataPath = settings?.AvatarUserDataPath?.Trim() ?? string.Empty;
        }

        public bool Activate()
        {
            lock (_sync)
            {
                var executable = ResolveExecutable();
                if (string.IsNullOrWhiteSpace(executable))
                {
                    Logger.Error($"[Avatar] MoFa executable not found: root={_installRoot}");
                    return false;
                }

                if (IsExecutableRunning(executable))
                {
                    Logger.Info($"[Avatar] MoFa client already running: {executable}");
                    return true;
                }

                var unityRuntime = Directory.Exists(_installRoot)
                    ? Directory.EnumerateFiles(_installRoot, "ExhibitionShow.exe", SearchOption.AllDirectories).FirstOrDefault()
                    : null;
                if (string.IsNullOrWhiteSpace(unityRuntime))
                    Logger.Warn($"[Avatar] Unity runtime not found under {_installRoot}; MoFa may not render correctly");

                if (!string.IsNullOrWhiteSpace(_userDataPath))
                    Directory.CreateDirectory(_userDataPath);

                Process.Start(new ProcessStartInfo
                {
                    FileName = executable,
                    WorkingDirectory = Path.GetDirectoryName(executable) ?? _installRoot,
                    UseShellExecute = true,
                    Arguments = string.IsNullOrWhiteSpace(_userDataPath)
                        ? string.Empty
                        : $"--user-data-dir=\"{_userDataPath}\"",
                });
                Logger.Info($"[Avatar] Started MoFa client: {executable}");
                return true;
            }
        }

        public void Deactivate()
        {
            lock (_sync)
            {
                var stopped = 0;
                foreach (var process in Process.GetProcesses())
                {
                    try
                    {
                        var path = process.MainModule?.FileName;
                        if (string.IsNullOrWhiteSpace(path) || !IsUnderInstallRoot(path))
                            continue;

                        process.Kill(true);
                        stopped++;
                    }
                    catch
                    {
                        // System processes and already exited child processes are expected here.
                    }
                    finally
                    {
                        process.Dispose();
                    }
                }
                Logger.Info($"[Avatar] Stopped MoFa process tree: count={stopped}, root={_installRoot}");
            }
        }

        private string ResolveExecutable()
        {
            if (!string.IsNullOrWhiteSpace(_configuredExecutable) && File.Exists(_configuredExecutable))
                return Path.GetFullPath(_configuredExecutable);

            var clientDir = Path.Combine(_installRoot, "win-unpacked");
            if (!Directory.Exists(clientDir))
                return string.Empty;

            return Directory.EnumerateFiles(clientDir, "*.exe", SearchOption.TopDirectoryOnly)
                .Select(path => new FileInfo(path))
                .OrderByDescending(file => file.Length)
                .Select(file => file.FullName)
                .FirstOrDefault() ?? string.Empty;
        }

        private bool IsExecutableRunning(string executable)
        {
            foreach (var process in Process.GetProcesses())
            {
                try
                {
                    if (string.Equals(process.MainModule?.FileName, executable, StringComparison.OrdinalIgnoreCase))
                        return true;
                }
                catch
                {
                }
                finally
                {
                    process.Dispose();
                }
            }
            return false;
        }

        private bool IsUnderInstallRoot(string path)
        {
            var root = Path.GetFullPath(_installRoot).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            var candidate = Path.GetFullPath(path);
            return candidate.StartsWith(root, StringComparison.OrdinalIgnoreCase);
        }

        public void Dispose()
        {
            // Client restarts must preserve the independently running avatar.
        }
    }
}
