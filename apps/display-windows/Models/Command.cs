using System.Collections.Generic;

namespace ExhibitionClient.Models
{
    /// <summary>
    /// WebSocket 命令模型
    /// </summary>
    public class Command
    {
        public long? Sequence { get; set; }
        public string ConnectionSession { get; set; }
        public long ReceivedTimestamp { get; set; }
        public string CorrelationId => $"{ConnectionSession}:{Sequence?.ToString() ?? "legacy"}";
        public string Type { get; set; }
        public string Action { get; set; }
        public string From { get; set; }
        public Dictionary<string, string> Params { get; set; } = new();
        public string ReplyText { get; set; }
        public string File { get; set; }
        public string Question { get; set; }
        public string Answer { get; set; }
        public int? Slide { get; set; }
        public bool? Mute { get; set; }
        public string Text { get; set; }
    }

    /// <summary>
    /// 设备注册信息
    /// </summary>
    public class DeviceInfo
    {
        public string Id { get; set; }
        public int? ScreenNumber { get; set; }
        public string Name { get; set; }
        public string Os { get; set; }
        public string Version { get; set; }
    }

    /// <summary>
    /// 媒体文件模型
    /// </summary>
    public class MediaItem
    {
        public string Name { get; set; }
        public string Url { get; set; }
        public string Commentary { get; set; }
        public string Status { get; set; }
    }

    /// <summary>
    /// 讲解词数据
    /// </summary>
    public class CommentaryData
    {
        public string File { get; set; }
        public string Commentary { get; set; }
        public string Status { get; set; }
    }
}
