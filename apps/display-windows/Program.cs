using System;
using System.Windows.Forms;
using ExhibitionClient.Views;
using ExhibitionClient.Services;

namespace ExhibitionClient
{
    internal static class Program
    {
        [STAThread]
        static void Main()
        {
            // 捕获所有未处理异常，防止进程崩溃
            Application.ThreadException += (s, e) =>
            {
                Logger.Error($"[未处理异常] {e.Exception.GetType().Name}: {e.Exception.Message}\n{e.Exception.StackTrace}");
            };
            AppDomain.CurrentDomain.UnhandledException += (s, e) =>
            {
                if (e.ExceptionObject is Exception ex)
                    Logger.Error($"[致命异常] {ex.GetType().Name}: {ex.Message}\n{ex.StackTrace}");
            };
            Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new MainForm());
        }
    }
}
