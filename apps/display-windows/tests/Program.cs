using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using ExhibitionClient.Controllers;
class Program
{
    static void Check(bool ok, string message) { if (!ok) throw new Exception(message); }
    [STAThread] static int Main(string[] args)
    {
        try
        {
            if (args.Length > 0 && args[0] == "office") Office(args.Length > 1 ? args[1] : null).GetAwaiter().GetResult();
            else QueueTests().GetAwaiter().GetResult();
            Console.WriteLine("PASS"); return 0;
        }
        catch (Exception ex) { Console.WriteLine(ex); return 1; }
    }
    static async Task QueueTests()
    {
        using (var controller = new PPTController())
        {
            var flags = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic;
            void Set(string name, object value) => typeof(PPTController).GetField(name, flags).SetValue(controller, value);
            object Get(string name) => typeof(PPTController).GetField(name, flags).GetValue(controller);
            Set("_application", new DeadOffice());
            Set("_presentation", new DeadOffice());
            Set("_opened", true);
            await controller.CloseAsync();
            Check(Get("_application") == null && Get("_presentation") == null && !controller.IsOpened,
                "dead Office close must discard references and restore usable state");
            Set("_application", new DeadOffice());
            typeof(PPTController).GetMethod("ReleaseApplication", flags).Invoke(controller, null);
            Check(Get("_application") == null, "dead Presentations getter must not retain application");
        }
        using (var controller = new PPTController())
        {
            void Set(string name, object value) => typeof(PPTController).GetField(name,
                System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic).SetValue(controller,value);
            var show = new SlowTestShow();
            Set("_show",show); Set("_opened",true); Set("_count",5);
            var first = controller.NextAsync();
            Check(controller.IsNavigating,"navigation busy state missing");
            try { await controller.NextAsync(); throw new Exception("duplicate move queued"); }
            catch(InvalidOperationException) { }
            Check(await first == 2 && !controller.IsFaulted,"6-second render incorrectly faulted");
            show.View.DelayMs=0;
            Check(await controller.NextAsync()==3,"fresh next after slow render failed");
            Check(await controller.PrevAsync()==2,"fresh previous after slow render failed");
            Check(show.View.Moves==3,"busy request replayed");
        }
        var dedup = new ExhibitionClient.Services.CommandDeduplicator();
        bool Accept(long? seq, string action, string text = "")
        {
            using var doc = JsonDocument.Parse(JsonSerializer.Serialize(new { action, @params = new { answer = text } }));
            return dedup.Accept(seq, doc.RootElement);
        }
        Check(Accept(19,"pending_qa","waiting"),"pending dropped");
        Check(Accept(19,"show_qa","智慧家庭"),"answer sharing pending seq dropped");
        Check(!Accept(19,"show_qa","智慧家庭"),"duplicate answer replayed");
        Check(!Accept(19,"pending_qa","late waiting"),"late pending replaced answer");
        Check(Accept(20,"next_slide") && !Accept(20,"next_slide"),"duplicate relative move replayed");
        Check(Accept(21,"pending_qa","phase1") && Accept(21,"pending_qa","phase2"),"progress update dropped");
        Check(Accept(null,"next_slide") && Accept(null,"next_slide"),"legacy commands dropped");
        dedup.Clear(); Check(Accept(20,"next_slide"),"reconnect did not reset dedup");
        using (var controller = new PPTController())
        {
            var field = typeof(PPTController).GetField("_queue",System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic);
            var q = (StaOperationQueue)field.GetValue(controller);
            using var entered = new ManualResetEventSlim(); using var unblock = new ManualResetEventSlim();
            var blocked = q.Enqueue(_ => {entered.Set(); unblock.Wait(); return 0;},TimeSpan.FromMilliseconds(100));
            entered.Wait();
            try { await blocked; throw new Exception("timeout missing"); } catch(TimeoutException) { }
            try
            {
                try { await controller.CloseAsync(); throw new Exception("close raced blocked call"); } catch(InvalidOperationException) { }
            }
            finally { unblock.Set(); }
            await Task.Delay(150);
            await controller.CloseAsync();
            Check(!controller.IsFaulted && !controller.IsOpened,"explicit close did not recover");
        }
        using (var q = new StaOperationQueue())
        {
            var visited = new List<int>(); int page = 3;
            var tasks = new List<Task<int>>();
            foreach (var delta in new[] { 1,1,-1,-1,1,-1 }) tasks.Add(q.Enqueue(_ => { Check(Thread.CurrentThread.GetApartmentState() == ApartmentState.STA,"not STA"); page += delta; visited.Add(page); return page; }, TimeSpan.FromSeconds(1)));
            await Task.WhenAll(tasks);
            Check(visited.SequenceEqual(new[]{4,5,4,3,4,3}), "direction or ordering lost");
        }
        using (var q = new StaOperationQueue())
        {
            using var started = new ManualResetEventSlim(); using var unblock = new ManualResetEventSlim();
            var hanging = q.Enqueue(_ => { started.Set(); unblock.Wait(); return 1; }, TimeSpan.FromMilliseconds(100));
            started.Wait();
            int replays = 0;
            var pending = q.Enqueue(_ => ++replays, TimeSpan.FromSeconds(1));
            try { await hanging; throw new Exception("missing timeout"); } catch (TimeoutException) { }
            try { await pending; throw new Exception("backlog not canceled"); } catch (InvalidOperationException) { }
            int recoveries = 0;
            try
            {
                await q.Enqueue(_ => 9, TimeSpan.FromSeconds(1), () => recoveries++);
                throw new Exception("recovered while the old call was still running");
            }
            catch (InvalidOperationException) { }
            Check(q.IsFaulted && recoveries == 0,"unsafe concurrent recovery");
            unblock.Set(); await Task.Delay(150);
            Check(replays == 0 && q.IsFaulted,"late replay after timeout");
            Check(await q.Enqueue(_ => 9, TimeSpan.FromSeconds(1), () =>
            {
                Check(Thread.CurrentThread.GetApartmentState() == ApartmentState.STA,"recovery not on STA");
                recoveries++;
            }) == 9,"fresh open did not recover");
            Check(recoveries == 1 && replays == 0 && !q.IsFaulted,"recovery replayed old commands");
            Check(await q.Enqueue(_ => 10, TimeSpan.FromSeconds(1)) == 10,"queue unusable after recovery");
        }
        using (var q = new StaOperationQueue())
        {
            try { await q.Enqueue<int>(_ => throw new FileNotFoundException(),TimeSpan.FromSeconds(1)); } catch (FileNotFoundException) { }
            Check(await q.Enqueue(_=>7,TimeSpan.FromSeconds(1)) == 7,"missing file poisons queue");
        }
        using (var q = new StaOperationQueue())
        {
            try { await q.Enqueue<int>(_ => throw new IOException("simulated Office failure"),TimeSpan.FromSeconds(1)); } catch (IOException) { }
            await Task.Delay(100);
            try { await q.Enqueue(_ => 1,TimeSpan.FromSeconds(1),()=>throw new IOException("recovery failed")); } catch (IOException) { }
            Check(q.IsFaulted,"failed recovery re-enabled control");
        }
        Console.WriteLine("Queue: STA, ordered directions, timeout/backlog cancellation, busy recovery rejection, explicit recovery, recovery failure, missing-file recovery passed");
    }
    static async Task Office(string file)
    {
        file ??= @"C:\media\codex-control-test.pptx";
        if (!File.Exists(file))
        {
            dynamic app = Activator.CreateInstance(Type.GetTypeFromProgID("PowerPoint.Application"));
            dynamic presentations = app.Presentations;
            dynamic doc = presentations.Add(0);
            dynamic slides = doc.Slides;
            for (int i=1;i<=5;i++)
            {
                dynamic slide=slides.Add(i,12); dynamic shapes=slide.Shapes;
                dynamic box=shapes.AddTextbox(1,80f,80f,600f,100f); dynamic frame=box.TextFrame; dynamic text=frame.TextRange;
                text.Text=$"Control test - slide {i}";
                foreach(object obj in new object[]{text,frame,box,shapes,slide}) Marshal.ReleaseComObject(obj);
            }
            doc.SaveAs(file,24); doc.Close(); app.Quit();
            foreach(object obj in new object[]{slides,doc,presentations,app}) Marshal.ReleaseComObject(obj);
        }
        using var controller = new PPTController(Path.GetDirectoryName(file));
        var results = new List<object>(); var watch=Stopwatch.StartNew();
        int page = await controller.OpenAsync(file); results.Add(new { action="open",page,ms=watch.Elapsed.TotalMilliseconds });
        Check(page==1,"initial slide");
        watch.Restart(); Check(await controller.PrevAsync()==1,"first boundary");
        var steps = new[]{1,1,-1,1,-1,-1,1,-1};
        foreach (var delta in Enumerable.Range(0,3).SelectMany(_=>steps))
        {
            watch.Restart(); var expected=Math.Clamp(page+delta,1,5);
            page=delta>0?await controller.NextAsync():await controller.PrevAsync();
            results.Add(new{action=delta>0?"next":"prev",page,ms=watch.Elapsed.TotalMilliseconds});
            Check(page==expected,"wrong slide");
        }
        Check(await controller.GotoAsync(5)==5,"goto"); Check(await controller.NextAsync()==5,"last boundary");
        watch.Restart(); Check(await controller.OpenAsync(file)==5,"same file not reused"); results.Add(new{action="reuse",page=5,ms=watch.Elapsed.TotalMilliseconds});
        await controller.CloseAsync();
        watch.Restart(); Check(await controller.OpenAsync(file)==1,"reopen"); results.Add(new{action="reopen",page=1,ms=watch.Elapsed.TotalMilliseconds});
        await controller.CloseAsync();
        File.WriteAllText(Path.Combine(AppContext.BaseDirectory,"office-results.json"),JsonSerializer.Serialize(results,new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine(JsonSerializer.Serialize(results));
    }
}
namespace ExhibitionClient.Services { static class Logger { public static void Info(string s)=>Console.WriteLine(s); public static void Error(string s)=>Console.WriteLine(s); public static void Warn(string s)=>Console.WriteLine(s); } }

public sealed class SlowTestShow { public SlowTestView View { get; } = new(); }
public sealed class SlowTestView {
    public SlowTestSlide Slide { get; } = new();
    public int DelayMs=6000, Moves;
    public void GotoSlide(int target,int reset) { Thread.Sleep(DelayMs); Slide.SlideIndex=target; Moves++; }
}
public sealed class SlowTestSlide { public int SlideIndex { get; set; } = 1; }

public sealed class DeadOffice
{
    public object Presentations => throw new COMException("RPC server unavailable", unchecked((int)0x800706BA));
    public void Close() => throw new COMException("RPC server unavailable", unchecked((int)0x800706BA));
}
