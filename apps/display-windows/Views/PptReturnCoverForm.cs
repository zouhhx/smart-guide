using System.ComponentModel;
using System.Runtime.InteropServices;

namespace ExhibitionClient.Views;

// Supply the complete frame to the window compositor before making it visible.
// A normal Form.BackgroundImage paints an erased background first, which is
// visible as white bands on the exhibition machine.
internal sealed class PptReturnCoverForm : Form
{
    public PptReturnCoverForm(Rectangle bounds, Bitmap frame)
    {
        Name = "PptReturnCover";
        FormBorderStyle = FormBorderStyle.None;
        ShowInTaskbar = false;
        StartPosition = FormStartPosition.Manual;
        AutoScaleMode = AutoScaleMode.None;
        Bounds = bounds;
        TopMost = true;
        IntPtr screen = GetDC(IntPtr.Zero), memory = IntPtr.Zero;
        IntPtr bitmap = IntPtr.Zero, previous = IntPtr.Zero;
        try
        {
            memory = CreateCompatibleDC(screen);
            bitmap = frame.GetHbitmap();
            if (screen == IntPtr.Zero || memory == IntPtr.Zero || bitmap == IntPtr.Zero)
                throw new Win32Exception();
            previous = SelectObject(memory, bitmap);
            var destination = new NativePoint(bounds.X, bounds.Y);
            var source = new NativePoint(0, 0);
            var size = new NativeSize(bounds.Width, bounds.Height);
            // ULW_OPAQUE ignores screenshot alpha and presents one complete image.
            if (!UpdateLayeredWindow(Handle, screen, ref destination, ref size,
                    memory, ref source, 0, IntPtr.Zero, 4))
                throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        catch { Dispose(); throw; }
        finally
        {
            if (previous != IntPtr.Zero) SelectObject(memory, previous);
            if (bitmap != IntPtr.Zero) DeleteObject(bitmap);
            if (memory != IntPtr.Zero) DeleteDC(memory);
            if (screen != IntPtr.Zero) ReleaseDC(IntPtr.Zero, screen);
        }
    }
    protected override bool ShowWithoutActivation => true;
    protected override CreateParams CreateParams
    {
        get { var cp = base.CreateParams; cp.ExStyle |= 0x80000 | 0x08000000 | 0x80; return cp; }
    }
    protected override void OnPaintBackground(PaintEventArgs e) { }
    protected override void OnPaint(PaintEventArgs e) { }
    [StructLayout(LayoutKind.Sequential)] private struct NativePoint
    { public int X, Y; public NativePoint(int x,int y) { X=x; Y=y; } }
    [StructLayout(LayoutKind.Sequential)] private struct NativeSize
    { public int Width, Height; public NativeSize(int width,int height) { Width=width; Height=height; } }
    [DllImport("user32.dll", SetLastError=true)] private static extern bool UpdateLayeredWindow(IntPtr window, IntPtr screen, ref NativePoint destination, ref NativeSize size, IntPtr sourceDc, ref NativePoint source, uint colorKey, IntPtr blend, uint flags);
    [DllImport("user32.dll")] private static extern IntPtr GetDC(IntPtr window);
    [DllImport("user32.dll")] private static extern int ReleaseDC(IntPtr window, IntPtr dc);
    [DllImport("gdi32.dll")] private static extern IntPtr CreateCompatibleDC(IntPtr dc);
    [DllImport("gdi32.dll")] private static extern IntPtr SelectObject(IntPtr dc, IntPtr value);
    [DllImport("gdi32.dll")] private static extern bool DeleteObject(IntPtr value);
    [DllImport("gdi32.dll")] private static extern bool DeleteDC(IntPtr dc);
}
