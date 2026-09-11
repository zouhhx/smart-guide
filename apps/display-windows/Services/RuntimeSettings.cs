using System;
using System.IO;
using System.Text.Json;

namespace ExhibitionClient.Services
{
    public class RuntimeSettings
    {
        public string WebSocketUrl { get; set; } = "ws://192.168.16.61:3000";
        public string FileServerUrl { get; set; } = "http://192.168.16.61:3001";
        public string PptThumbnailServiceUrl { get; set; } = "http://192.168.16.61:3025";
        public string TtsServerUrl { get; set; } = "http://192.168.16.61:3001";
        public string TtsVoice { get; set; } = "xiaoxiao";
        public string MediaPath { get; set; } = @"C:\media";
        public int? ScreenNumber { get; set; } = null;
        public string PowerPointPath { get; set; } = "";
        public int DisplayWidth { get; set; } = 1920;
        public int DisplayHeight { get; set; } = 1080;
        public int SegmentWidth { get; set; } = 1920;
        public int SegmentHeight { get; set; } = 1080;
        public string ScreenName { get; set; } = "";
        public int SnapshotIntervalSeconds { get; set; } = 5;
        public int StatusIntervalSeconds { get; set; } = 1;
        public bool NonIntrusiveMode { get; set; } = false;
        public bool TransparentBackground { get; set; } = false;
        public string BackgroundFit { get; set; } = "cover";
        public string BackgroundColor { get; set; } = "#050C1C";
        public string BackgroundImagePath { get; set; } = @"Assets\display-background.png";
        public string IdleOrbSpritePath { get; set; } = @"Assets\ai-idle.png";
        public string InteractionOrbSpritePath { get; set; } = @"Assets\ai-interaction.png";
        public int OrbSize { get; set; } = 260;
        public int OrbFps { get; set; } = 18;
        public int IdleOrbFrameCount { get; set; } = 76;
        public int InteractionOrbFrameCount { get; set; } = 75;
        public string AvatarInstallRoot { get; set; } = @"D:\MofaAvatar\云播客户端2026Ai_1.1";
        public string AvatarExecutablePath { get; set; } = "";
        public string AvatarUserDataPath { get; set; } = "";

        public static string SettingsPath => Path.Combine(
            Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location) ?? "",
            "settings.json");

        public static RuntimeSettings Load()
        {
            try
            {
                if (File.Exists(SettingsPath))
                {
                    var json = File.ReadAllText(SettingsPath);
                    var settings = JsonSerializer.Deserialize<RuntimeSettings>(json);
                    if (settings != null)
                    {
                        Logger.Info($"[Settings] 已加载 settings.json: {SettingsPath}");
                        return settings;
                    }

                    Logger.Warn($"[Settings] settings.json 解析结果为空，将回退到 App.config: {SettingsPath}");
                }
            }
            catch (Exception ex)
            {
                Logger.Error($"[Settings] 读取 settings.json 失败: path={SettingsPath}, type={ex.GetType().Name}, message={ex.Message}");
            }

            var fallback = new RuntimeSettings
            {
                WebSocketUrl = System.Configuration.ConfigurationManager.AppSettings["WebSocketUrl"] ?? "ws://192.168.16.61:3000",
                FileServerUrl = System.Configuration.ConfigurationManager.AppSettings["FileServerUrl"] ?? "http://192.168.16.61:3001",
                TtsServerUrl = System.Configuration.ConfigurationManager.AppSettings["TtsServerUrl"] ?? "http://192.168.16.61:3001",
                TtsVoice = System.Configuration.ConfigurationManager.AppSettings["TtsVoice"] ?? "xiaoxiao",
                MediaPath = System.Configuration.ConfigurationManager.AppSettings["MediaPath"] ?? @"C:\media"
            };

            if (int.TryParse(System.Configuration.ConfigurationManager.AppSettings["ScreenNumber"], out var sn) && sn > 0)
                fallback.ScreenNumber = sn;

            Logger.Info("[Settings] 未找到可用的 settings.json，已回退到 App.config");
            return fallback;
        }

        public void Save()
        {
            var json = JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true });
            File.WriteAllText(SettingsPath, json);
            Logger.Info($"[Settings] 已保存 settings.json: {SettingsPath}");
        }
    }
}
