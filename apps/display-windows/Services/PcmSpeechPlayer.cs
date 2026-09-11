using System.Runtime.InteropServices;

namespace ExhibitionClient.Services;

/// <summary>Bounded 24kHz mono S16LE streaming output. All native ownership stays on one task.</summary>
internal static class PcmSpeechPlayer
{
    public static async Task PlayAsync(Stream source, CancellationToken token)
    {
        var format = new WaveFormat { Tag = 1, Channels = 1, Rate = 24000, BytesPerSecond = 48000, BlockAlign = 2, Bits = 16 };
        Check(waveOutOpen(out var device, uint.MaxValue, ref format, IntPtr.Zero, IntPtr.Zero, 0));
        var pending = new Queue<BufferOwner>();
        try
        {
            // ReadExactly is not assumed: HTTP chunks can split even one PCM sample.
            var bytes = new byte[4800];
            int filled = 0;
            for (;;)
            {
                token.ThrowIfCancellationRequested();
                int count = await source.ReadAsync(bytes.AsMemory(filled, bytes.Length - filled), token);
                filled += count;
                if (filled == bytes.Length || count == 0)
                {
                    if (filled % 2 != 0) throw new IOException("PCM 音频结尾不完整");
                    if (filled > 0)
                    {
                        while (pending.Count >= 8)
                        {
                            if (pending.Peek().Done) pending.Dequeue().Dispose();
                            else await Task.Delay(10, token);
                        }
                        var buffer = new BufferOwner(device, bytes, filled);
                        pending.Enqueue(buffer);
                        buffer.Write();
                        filled = 0;
                    }
                    if (count == 0) break;
                }
                while (pending.Count > 0 && pending.Peek().Done) pending.Dequeue().Dispose();
            }
            while (pending.Count > 0)
            {
                if (pending.Peek().Done) pending.Dequeue().Dispose();
                else await Task.Delay(10, token);
            }
        }
        finally
        {
            waveOutReset(device);
            while (pending.Count > 0) pending.Dequeue().Dispose();
            waveOutClose(device);
        }
    }

    private sealed class BufferOwner : IDisposable
    {
        private readonly IntPtr device;
        private IntPtr data, header;
        private bool prepared;
        public BufferOwner(IntPtr device, byte[] bytes, int size)
        {
            this.device = device;
            try
            {
                data = Marshal.AllocHGlobal(size);
                Marshal.Copy(bytes, 0, data, size);
                header = Marshal.AllocHGlobal(Marshal.SizeOf<WaveHeader>());
                Marshal.StructureToPtr(new WaveHeader { Data = data, Length = (uint)size }, header, false);
                Check(waveOutPrepareHeader(device, header, (uint)Marshal.SizeOf<WaveHeader>()));
                prepared = true;
            }
            catch { Dispose(); throw; }
        }
        public bool Done => (Marshal.PtrToStructure<WaveHeader>(header).Flags & 1) != 0;
        public void Write() => Check(waveOutWrite(device, header, (uint)Marshal.SizeOf<WaveHeader>()));
        public void Dispose()
        {
            if (prepared) { Check(waveOutUnprepareHeader(device, header, (uint)Marshal.SizeOf<WaveHeader>())); prepared = false; }
            if (header != IntPtr.Zero) { Marshal.FreeHGlobal(header); header = IntPtr.Zero; }
            if (data != IntPtr.Zero) { Marshal.FreeHGlobal(data); data = IntPtr.Zero; }
        }
    }
    private static void Check(uint result) { if (result != 0) throw new IOException($"音频输出失败 ({result})"); }
    [StructLayout(LayoutKind.Sequential, Pack = 2)] private struct WaveFormat
    { public ushort Tag, Channels; public uint Rate, BytesPerSecond; public ushort BlockAlign, Bits, Extra; }
    [StructLayout(LayoutKind.Sequential)] private struct WaveHeader
    { public IntPtr Data; public uint Length, Recorded; public IntPtr User; public uint Flags, Loops; public IntPtr Next, Reserved; }
    [DllImport("winmm.dll")] private static extern uint waveOutOpen(out IntPtr device, uint id, ref WaveFormat format, IntPtr callback, IntPtr instance, uint flags);
    [DllImport("winmm.dll")] private static extern uint waveOutPrepareHeader(IntPtr device, IntPtr header, uint size);
    [DllImport("winmm.dll")] private static extern uint waveOutUnprepareHeader(IntPtr device, IntPtr header, uint size);
    [DllImport("winmm.dll")] private static extern uint waveOutWrite(IntPtr device, IntPtr header, uint size);
    [DllImport("winmm.dll")] private static extern uint waveOutReset(IntPtr device);
    [DllImport("winmm.dll")] private static extern uint waveOutClose(IntPtr device);
}
