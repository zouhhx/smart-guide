using System;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace VoiceGateway.AudioControl
{
    internal enum EDataFlow
    {
        Render = 0,
        Capture = 1,
        All = 2,
    }

    internal enum ERole
    {
        Console = 0,
        Multimedia = 1,
        Communications = 2,
    }

    [Flags]
    internal enum DeviceState : uint
    {
        Active = 0x1,
    }

    [Flags]
    internal enum ClsCtx : uint
    {
        InprocServer = 0x1,
        InprocHandler = 0x2,
        LocalServer = 0x4,
        All = InprocServer | InprocHandler | LocalServer,
    }

    internal enum StorageAccessMode
    {
        Read = 0,
    }

    internal enum AudioClientShareMode
    {
        Shared = 0,
        Exclusive = 1,
    }

    [Flags]
    internal enum AudioClientStreamFlags : uint
    {
        None = 0,
        NoPersist = 0x00080000,
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct PropertyKey
    {
        public Guid FormatId;
        public uint PropertyId;

        public PropertyKey(Guid formatId, uint propertyId)
        {
            FormatId = formatId;
            PropertyId = propertyId;
        }
    }

    [StructLayout(LayoutKind.Explicit)]
    internal struct PropVariant
    {
        [FieldOffset(0)]
        public ushort VariantType;

        [FieldOffset(8)]
        public IntPtr PointerValue;

        public string GetString()
        {
            // VT_LPWSTR
            return VariantType == 31 && PointerValue != IntPtr.Zero
                ? Marshal.PtrToStringUni(PointerValue)
                : string.Empty;
        }
    }

    [ComImport]
    [Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    internal class MMDeviceEnumeratorComObject
    {
    }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6")]
    internal interface IMMDeviceEnumerator
    {
        [PreserveSig]
        int EnumAudioEndpoints(
            EDataFlow dataFlow,
            DeviceState stateMask,
            out IMMDeviceCollection devices);

        [PreserveSig]
        int GetDefaultAudioEndpoint(
            EDataFlow dataFlow,
            ERole role,
            out IMMDevice endpoint);

        [PreserveSig]
        int GetDevice(
            [MarshalAs(UnmanagedType.LPWStr)] string id,
            out IMMDevice device);

        [PreserveSig]
        int RegisterEndpointNotificationCallback(IntPtr client);

        [PreserveSig]
        int UnregisterEndpointNotificationCallback(IntPtr client);
    }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E")]
    internal interface IMMDeviceCollection
    {
        [PreserveSig]
        int GetCount(out uint count);

        [PreserveSig]
        int Item(uint deviceIndex, out IMMDevice device);
    }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("D666063F-1587-4E43-81F1-B948E807363F")]
    internal interface IMMDevice
    {
        [PreserveSig]
        int Activate(
            ref Guid interfaceId,
            ClsCtx classContext,
            IntPtr activationParams,
            [MarshalAs(UnmanagedType.IUnknown)] out object interfacePointer);

        [PreserveSig]
        int OpenPropertyStore(
            StorageAccessMode accessMode,
            out IPropertyStore properties);

        [PreserveSig]
        int GetId(
            [MarshalAs(UnmanagedType.LPWStr)] out string id);

        [PreserveSig]
        int GetState(out DeviceState state);
    }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
    internal interface IPropertyStore
    {
        [PreserveSig]
        int GetCount(out uint propertyCount);

        [PreserveSig]
        int GetAt(uint propertyIndex, out PropertyKey key);

        [PreserveSig]
        int GetValue(ref PropertyKey key, out PropVariant value);

        [PreserveSig]
        int SetValue(ref PropertyKey key, ref PropVariant value);

        [PreserveSig]
        int Commit();
    }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("5CDF2C82-841E-4546-9722-0CF74078229A")]
    internal interface IAudioEndpointVolume
    {
        [PreserveSig]
        int RegisterControlChangeNotify(IntPtr notify);

        [PreserveSig]
        int UnregisterControlChangeNotify(IntPtr notify);

        [PreserveSig]
        int GetChannelCount(out uint channelCount);

        [PreserveSig]
        int SetMasterVolumeLevel(float levelDb, ref Guid eventContext);

        [PreserveSig]
        int SetMasterVolumeLevelScalar(float level, ref Guid eventContext);

        [PreserveSig]
        int GetMasterVolumeLevel(out float levelDb);

        [PreserveSig]
        int GetMasterVolumeLevelScalar(out float level);

        [PreserveSig]
        int SetChannelVolumeLevel(
            uint channelNumber,
            float levelDb,
            ref Guid eventContext);

        [PreserveSig]
        int SetChannelVolumeLevelScalar(
            uint channelNumber,
            float level,
            ref Guid eventContext);

        [PreserveSig]
        int GetChannelVolumeLevel(uint channelNumber, out float levelDb);

        [PreserveSig]
        int GetChannelVolumeLevelScalar(uint channelNumber, out float level);

        [PreserveSig]
        int SetMute(
            [MarshalAs(UnmanagedType.Bool)] bool muted,
            ref Guid eventContext);

        [PreserveSig]
        int GetMute([MarshalAs(UnmanagedType.Bool)] out bool muted);

        [PreserveSig]
        int GetVolumeStepInfo(out uint step, out uint stepCount);

        [PreserveSig]
        int VolumeStepUp(ref Guid eventContext);

        [PreserveSig]
        int VolumeStepDown(ref Guid eventContext);

        [PreserveSig]
        int QueryHardwareSupport(out uint hardwareSupportMask);

        [PreserveSig]
        int GetVolumeRange(
            out float minDb,
            out float maxDb,
            out float incrementDb);
    }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("C02216F6-8C67-4B5B-9D00-D008E73E0064")]
    internal interface IAudioMeterInformation
    {
        [PreserveSig]
        int GetPeakValue(out float peak);

        [PreserveSig]
        int GetMeteringChannelCount(out uint channelCount);

        [PreserveSig]
        int GetChannelsPeakValues(
            uint channelCount,
            [Out, MarshalAs(
                UnmanagedType.LPArray,
                SizeParamIndex = 0)] float[] peakValues);

        [PreserveSig]
        int QueryHardwareSupport(out uint hardwareSupportMask);
    }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2")]
    internal interface IAudioClient
    {
        [PreserveSig]
        int Initialize(
            AudioClientShareMode shareMode,
            AudioClientStreamFlags streamFlags,
            long bufferDuration,
            long periodicity,
            IntPtr format,
            ref Guid audioSessionGuid);

        [PreserveSig]
        int GetBufferSize(out uint bufferFrameCount);

        [PreserveSig]
        int GetStreamLatency(out long latency);

        [PreserveSig]
        int GetCurrentPadding(out uint currentPadding);

        [PreserveSig]
        int IsFormatSupported(
            AudioClientShareMode shareMode,
            IntPtr format,
            out IntPtr closestMatch);

        [PreserveSig]
        int GetMixFormat(out IntPtr deviceFormat);

        [PreserveSig]
        int GetDevicePeriod(
            out long defaultDevicePeriod,
            out long minimumDevicePeriod);

        [PreserveSig]
        int Start();

        [PreserveSig]
        int Stop();

        [PreserveSig]
        int Reset();

        [PreserveSig]
        int SetEventHandle(IntPtr eventHandle);

        [PreserveSig]
        int GetService(
            ref Guid interfaceId,
            [MarshalAs(UnmanagedType.IUnknown)] out object service);
    }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("C8ADBD64-E71E-48A0-A4DE-185C395CD317")]
    internal interface IAudioCaptureClient
    {
        [PreserveSig]
        int GetBuffer(
            out IntPtr data,
            out uint frameCount,
            out uint flags,
            out ulong devicePosition,
            out ulong qpcPosition);

        [PreserveSig]
        int ReleaseBuffer(uint frameCount);

        [PreserveSig]
        int GetNextPacketSize(out uint frameCount);
    }

    internal sealed class EndpointInfo
    {
        public string Id;
        public string Name;
        public float Volume;
        public bool Muted;
        public bool IsDefault;
    }

    internal static class Program
    {
        private static readonly Guid EndpointVolumeId =
            new Guid("5CDF2C82-841E-4546-9722-0CF74078229A");

        private static readonly Guid EndpointMeterId =
            new Guid("C02216F6-8C67-4B5B-9D00-D008E73E0064");

        private static readonly Guid AudioClientId =
            new Guid("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2");

        private static readonly Guid AudioCaptureClientId =
            new Guid("C8ADBD64-E71E-48A0-A4DE-185C395CD317");

        private static readonly PropertyKey FriendlyNameKey =
            new PropertyKey(
                new Guid("A45C254E-DF1C-4EFD-8020-67D146A850E0"),
                14);

        [DllImport("ole32.dll")]
        private static extern int PropVariantClear(ref PropVariant value);

        [STAThread]
        private static int Main(string[] args)
        {
            Console.OutputEncoding = new UTF8Encoding(false);
            try
            {
                if (args.Length == 0 || args[0] == "list")
                {
                    WriteEndpoints(ListCaptureEndpoints());
                    return 0;
                }

                if (args[0] == "set")
                {
                    string id = GetArgument(args, "--id");
                    string rawVolume = GetArgument(args, "--volume");
                    float volume;
                    if (!float.TryParse(
                        rawVolume,
                        NumberStyles.Float,
                        CultureInfo.InvariantCulture,
                        out volume) ||
                        volume < 0 ||
                        volume > 100)
                    {
                        throw new ArgumentException(
                            "volume must be between 0 and 100");
                    }
                    SetCaptureVolume(id, volume / 100.0f);
                    Console.WriteLine("{\"ok\":true}");
                    return 0;
                }

                if (args[0] == "peak")
                {
                    string id = GetArgument(args, "--id");
                    WritePeak(ReadCapturePeak(id));
                    return 0;
                }

                if (args[0] == "monitor")
                {
                    string id = GetArgument(args, "--id");
                    int interval = GetIntegerArgument(
                        args,
                        "--interval-ms",
                        100,
                        50,
                        1000);
                    MonitorCapturePeak(id, interval);
                    return 0;
                }

                throw new ArgumentException(
                    "supported commands: list, set, peak, monitor");
            }
            catch (Exception exception)
            {
                Console.Error.WriteLine(exception.Message);
                return 2;
            }
        }

        private static List<EndpointInfo> ListCaptureEndpoints()
        {
            var output = new List<EndpointInfo>();
            IMMDeviceEnumerator enumerator = CreateEnumerator();
            IMMDevice defaultDevice = null;
            string defaultId = string.Empty;
            try
            {
                Check(enumerator.GetDefaultAudioEndpoint(
                    EDataFlow.Capture,
                    ERole.Console,
                    out defaultDevice));
                if (defaultDevice != null)
                {
                    Check(defaultDevice.GetId(out defaultId));
                }

                IMMDeviceCollection collection;
                Check(enumerator.EnumAudioEndpoints(
                    EDataFlow.Capture,
                    DeviceState.Active,
                    out collection));
                try
                {
                    uint count;
                    Check(collection.GetCount(out count));
                    for (uint index = 0; index < count; index++)
                    {
                        IMMDevice device;
                        Check(collection.Item(index, out device));
                        try
                        {
                            string id;
                            Check(device.GetId(out id));
                            string name = ReadFriendlyName(device);
                            IAudioEndpointVolume volume = ActivateVolume(device);
                            try
                            {
                                float scalar;
                                bool muted;
                                Check(volume.GetMasterVolumeLevelScalar(out scalar));
                                Check(volume.GetMute(out muted));
                                output.Add(new EndpointInfo
                                {
                                    Id = id,
                                    Name = name,
                                    Volume = scalar * 100.0f,
                                    Muted = muted,
                                    IsDefault = string.Equals(
                                        id,
                                        defaultId,
                                        StringComparison.OrdinalIgnoreCase),
                                });
                            }
                            finally
                            {
                                Release(volume);
                            }
                        }
                        finally
                        {
                            Release(device);
                        }
                    }
                }
                finally
                {
                    Release(collection);
                }
            }
            finally
            {
                Release(defaultDevice);
                Release(enumerator);
            }
            return output;
        }

        private static void SetCaptureVolume(string id, float scalar)
        {
            IMMDeviceEnumerator enumerator = CreateEnumerator();
            try
            {
                IMMDevice device;
                Check(enumerator.GetDevice(id, out device));
                try
                {
                    IAudioEndpointVolume volume = ActivateVolume(device);
                    try
                    {
                        Guid context = Guid.NewGuid();
                        Check(volume.SetMasterVolumeLevelScalar(
                            scalar,
                            ref context));
                    }
                    finally
                    {
                        Release(volume);
                    }
                }
                finally
                {
                    Release(device);
                }
            }
            finally
            {
                Release(enumerator);
            }
        }

        private static float ReadCapturePeak(string id)
        {
            IMMDeviceEnumerator enumerator = CreateEnumerator();
            try
            {
                IMMDevice device;
                Check(enumerator.GetDevice(id, out device));
                try
                {
                    IAudioMeterInformation meter = ActivateMeter(device);
                    try
                    {
                        float peak;
                        Check(meter.GetPeakValue(out peak));
                        return peak;
                    }
                    finally
                    {
                        Release(meter);
                    }
                }
                finally
                {
                    Release(device);
                }
            }
            finally
            {
                Release(enumerator);
            }
        }

        private static void MonitorCapturePeak(string id, int intervalMs)
        {
            IMMDeviceEnumerator enumerator = CreateEnumerator();
            try
            {
                IMMDevice device;
                Check(enumerator.GetDevice(id, out device));
                try
                {
                    IAudioMeterInformation meter = ActivateMeter(device);
                    IAudioClient audioClient = ActivateAudioClient(device);
                    IAudioCaptureClient captureClient = null;
                    try
                    {
                        captureClient = StartSharedCapture(audioClient);
                        try
                        {
                            while (true)
                            {
                                DrainCapturePackets(captureClient);
                                float peak;
                                Check(meter.GetPeakValue(out peak));
                                WritePeak(peak);
                                Thread.Sleep(intervalMs);
                            }
                        }
                        finally
                        {
                            audioClient.Stop();
                        }
                    }
                    finally
                    {
                        Release(captureClient);
                        Release(audioClient);
                        Release(meter);
                    }
                }
                finally
                {
                    Release(device);
                }
            }
            finally
            {
                Release(enumerator);
            }
        }

        private static IAudioCaptureClient StartSharedCapture(
            IAudioClient audioClient)
        {
            IntPtr format = IntPtr.Zero;
            try
            {
                Check(audioClient.GetMixFormat(out format));
                Guid session = Guid.Empty;
                Check(audioClient.Initialize(
                    AudioClientShareMode.Shared,
                    AudioClientStreamFlags.NoPersist,
                    1000000,
                    0,
                    format,
                    ref session));
            }
            finally
            {
                if (format != IntPtr.Zero)
                {
                    Marshal.FreeCoTaskMem(format);
                }
            }

            Guid captureClientId = AudioCaptureClientId;
            object service;
            Check(audioClient.GetService(ref captureClientId, out service));
            IAudioCaptureClient captureClient =
                (IAudioCaptureClient)service;
            try
            {
                Check(audioClient.Start());
                return captureClient;
            }
            catch
            {
                Release(captureClient);
                throw;
            }
        }

        private static void DrainCapturePackets(
            IAudioCaptureClient captureClient)
        {
            uint packetFrames;
            Check(captureClient.GetNextPacketSize(out packetFrames));
            while (packetFrames > 0)
            {
                IntPtr data;
                uint frames;
                uint flags;
                ulong devicePosition;
                ulong qpcPosition;
                Check(captureClient.GetBuffer(
                    out data,
                    out frames,
                    out flags,
                    out devicePosition,
                    out qpcPosition));
                Check(captureClient.ReleaseBuffer(frames));
                Check(captureClient.GetNextPacketSize(out packetFrames));
            }
        }

        private static void WritePeak(float peak)
        {
            float percent = Math.Max(0.0f, Math.Min(1.0f, peak)) * 100.0f;
            Console.WriteLine(
                "{\"peak\":" +
                percent.ToString("0.###", CultureInfo.InvariantCulture) +
                "}");
        }

        private static IMMDeviceEnumerator CreateEnumerator()
        {
            return (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
        }

        private static IAudioEndpointVolume ActivateVolume(IMMDevice device)
        {
            Guid interfaceId = EndpointVolumeId;
            object endpoint;
            Check(device.Activate(
                ref interfaceId,
                ClsCtx.All,
                IntPtr.Zero,
                out endpoint));
            return (IAudioEndpointVolume)endpoint;
        }

        private static IAudioMeterInformation ActivateMeter(IMMDevice device)
        {
            Guid interfaceId = EndpointMeterId;
            object endpoint;
            Check(device.Activate(
                ref interfaceId,
                ClsCtx.All,
                IntPtr.Zero,
                out endpoint));
            return (IAudioMeterInformation)endpoint;
        }

        private static IAudioClient ActivateAudioClient(IMMDevice device)
        {
            Guid interfaceId = AudioClientId;
            object endpoint;
            Check(device.Activate(
                ref interfaceId,
                ClsCtx.All,
                IntPtr.Zero,
                out endpoint));
            return (IAudioClient)endpoint;
        }

        private static string ReadFriendlyName(IMMDevice device)
        {
            IPropertyStore properties;
            Check(device.OpenPropertyStore(
                StorageAccessMode.Read,
                out properties));
            try
            {
                PropertyKey key = FriendlyNameKey;
                PropVariant value;
                Check(properties.GetValue(ref key, out value));
                try
                {
                    return value.GetString();
                }
                finally
                {
                    PropVariantClear(ref value);
                }
            }
            finally
            {
                Release(properties);
            }
        }

        private static void WriteEndpoints(List<EndpointInfo> endpoints)
        {
            var output = new StringBuilder();
            output.Append("[");
            for (int index = 0; index < endpoints.Count; index++)
            {
                if (index > 0)
                {
                    output.Append(",");
                }
                EndpointInfo item = endpoints[index];
                output.Append("{\"id\":\"");
                output.Append(JsonEscape(item.Id));
                output.Append("\",\"name\":\"");
                output.Append(JsonEscape(item.Name));
                output.Append("\",\"volume\":");
                output.Append(item.Volume.ToString("0.##", CultureInfo.InvariantCulture));
                output.Append(",\"muted\":");
                output.Append(item.Muted ? "true" : "false");
                output.Append(",\"is_default\":");
                output.Append(item.IsDefault ? "true" : "false");
                output.Append("}");
            }
            output.Append("]");
            Console.WriteLine(output.ToString());
        }

        private static string JsonEscape(string value)
        {
            if (value == null)
            {
                return string.Empty;
            }
            var output = new StringBuilder();
            foreach (char character in value)
            {
                switch (character)
                {
                    case '"':
                        output.Append("\\\"");
                        break;
                    case '\\':
                        output.Append("\\\\");
                        break;
                    case '\b':
                        output.Append("\\b");
                        break;
                    case '\f':
                        output.Append("\\f");
                        break;
                    case '\n':
                        output.Append("\\n");
                        break;
                    case '\r':
                        output.Append("\\r");
                        break;
                    case '\t':
                        output.Append("\\t");
                        break;
                    default:
                        if (character < 32)
                        {
                            output.Append("\\u");
                            output.Append(((int)character).ToString("x4"));
                        }
                        else
                        {
                            output.Append(character);
                        }
                        break;
                }
            }
            return output.ToString();
        }

        private static string GetArgument(string[] args, string name)
        {
            for (int index = 0; index < args.Length - 1; index++)
            {
                if (string.Equals(
                    args[index],
                    name,
                    StringComparison.OrdinalIgnoreCase))
                {
                    return args[index + 1];
                }
            }
            throw new ArgumentException("missing argument: " + name);
        }

        private static int GetIntegerArgument(
            string[] args,
            string name,
            int defaultValue,
            int minimum,
            int maximum)
        {
            for (int index = 0; index < args.Length - 1; index++)
            {
                if (!string.Equals(
                    args[index],
                    name,
                    StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                int value;
                if (!int.TryParse(
                    args[index + 1],
                    NumberStyles.Integer,
                    CultureInfo.InvariantCulture,
                    out value) ||
                    value < minimum ||
                    value > maximum)
                {
                    throw new ArgumentException(
                        name + " must be between " +
                        minimum.ToString(CultureInfo.InvariantCulture) +
                        " and " +
                        maximum.ToString(CultureInfo.InvariantCulture));
                }
                return value;
            }
            return defaultValue;
        }

        private static void Check(int result)
        {
            if (result < 0)
            {
                Marshal.ThrowExceptionForHR(result);
            }
        }

        private static void Release(object value)
        {
            if (value != null && Marshal.IsComObject(value))
            {
                Marshal.ReleaseComObject(value);
            }
        }
    }
}
