using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace ExhibitionClient.Services;

// A server sequence is a request, not a single message: pending and answer share it.
internal sealed class CommandDeduplicator
{
    private sealed class Request
    {
        internal readonly HashSet<string> Messages = new();
        internal bool AnswerReceived;
    }
    private readonly Dictionary<long, Request> _requests = new();
    private readonly Queue<long> _order = new();
    internal void Clear() { _requests.Clear(); _order.Clear(); }

    internal bool Accept(long? sequence, JsonElement message)
    {
        if (!sequence.HasValue) return true;
        if (!_requests.TryGetValue(sequence.Value, out var request))
        {
            request = new Request();
            _requests.Add(sequence.Value, request);
            _order.Enqueue(sequence.Value);
            if (_order.Count > 1024) _requests.Remove(_order.Dequeue());
        }
        var action = message.TryGetProperty("action", out var a) ? a.GetString() : "";
        if (action == "pending_qa" && request.AnswerReceived) return false;
        // Ignore transport timestamps; preserve actual payload changes within one request.
        var payload = string.Join("\n", new[] { "action", "params", "text", "replyText" }
            .Select(key => message.TryGetProperty(key, out var value) ? value.GetRawText() : ""));
        var digest = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(payload)));
        if (!request.Messages.Add(digest)) return false;
        if (action is "show_qa" or "show_media_results") request.AnswerReceived = true;
        return true;
    }
}
