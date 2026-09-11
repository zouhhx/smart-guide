package com.zongheng.exhibitionhallrobot.service;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.os.SystemClock;
import android.view.MotionEvent;
import android.view.View;
import android.widget.TextView;
import android.widget.ScrollView;
import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import com.zongheng.exhibitionhallrobot.MainActivity;
import com.zongheng.exhibitionhallrobot.R;
import com.zongheng.exhibitionhallrobot.ui.RobotPresentation;
import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.Field;
import java.util.Arrays;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

/** Only injects view data: no ASR, TTS, draft submission, navigation or teaching start. */
@RunWith(AndroidJUnit4.class)
public class RobotOverlayInstrumentedTest {
    @Test public void appendedTeachingTextShowsLatestLine() {
        try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
            scenario.onActivity(a -> {
                RobotPresentation p = new RobotPresentation(a, () -> {}, () -> {});
                try {
                    Field f = MainActivity.class.getDeclaredField("presentation");
                    f.setAccessible(true);
                    f.set(a, p);
                } catch (Exception e) { throw new AssertionError(e); }
                p.teaching("正在记录现场补充", true, true);
                p.conversation(Arrays.asList(
                        new TcpService.ConversationEntry("user", "测试用途？"),
                        new TcpService.ConversationEntry("staff", "第一句\n第二句\n第三句\n第四句\n第五句\n最后一句")));
            });
            SystemClock.sleep(500);
            scenario.onActivity(a -> {
                ScrollView scroll = a.findViewById(R.id.robotAnswerScroll);
                assertTrue("Later teaching lines must scroll into view", scroll.getScrollY() > 0);
                assertFalse("The final line must be visible", scroll.canScrollVertically(1));
                capture(a, "teaching-scroll-latest");
                RobotPresentation p = (RobotPresentation) field(a, "presentation");
                p.teaching("", false, false);
                p.conversation(Arrays.asList(
                        new TcpService.ConversationEntry("user", "普通问题？"),
                        new TcpService.ConversationEntry("assistant", "回答开头\n第二句\n第三句\n第四句\n第五句\n最后一句")));
            });
            SystemClock.sleep(500);
            scenario.onActivity(a -> assertEquals(0,
                    ((ScrollView) a.findViewById(R.id.robotAnswerScroll)).getScrollY()));
        }
    }

    private static Object field(Object target, String name) {
        try {
            Field f = target.getClass().getDeclaredField(name);
            f.setAccessible(true);
            return f.get(target);
        } catch (Exception e) { throw new AssertionError(e); }
    }

    private static void capture(MainActivity activity, String name) {
        try {
            View view = activity.findViewById(android.R.id.content);
            Bitmap bitmap = Bitmap.createBitmap(view.getWidth(), view.getHeight(), Bitmap.Config.ARGB_8888);
            view.draw(new Canvas(bitmap));
            try (FileOutputStream output = new FileOutputStream(
                    new File(activity.getExternalFilesDir(null), name + ".png"))) {
                bitmap.compress(Bitmap.CompressFormat.PNG, 100, output);
            }
            bitmap.recycle();
        } catch (Exception e) { throw new AssertionError(e); }
    }

    private static void touch(View view, int action) {
        long now = SystemClock.uptimeMillis();
        MotionEvent event = MotionEvent.obtain(now, now, action, view.getWidth() / 2f, view.getHeight() / 2f, 0);
        try { view.dispatchTouchEvent(event); }
        finally { event.recycle(); }
    }

    private static void checkHiddenSettingsGesture(ActivityScenario<MainActivity> scenario) {
        AtomicReference<Object> service = new AtomicReference<>();
        scenario.onActivity(a -> {
            service.set(field(a, "tcpService"));
            View zone = a.findViewById(R.id.developerAccessZone);
            touch(zone, MotionEvent.ACTION_DOWN);
            touch(zone, MotionEvent.ACTION_UP);
            assertEquals(View.GONE, a.findViewById(R.id.developerPanel).getVisibility());
        });
        // Releasing a short touch must also cancel its delayed action.
        SystemClock.sleep(2200);
        scenario.onActivity(a -> {
            assertEquals(View.GONE, a.findViewById(R.id.developerPanel).getVisibility());
            touch(a.findViewById(R.id.developerAccessZone), MotionEvent.ACTION_DOWN);
        });
        SystemClock.sleep(2200);
        scenario.onActivity(a -> {
            touch(a.findViewById(R.id.developerAccessZone), MotionEvent.ACTION_UP);
            assertEquals(View.VISIBLE, a.findViewById(R.id.developerPanel).getVisibility());
            a.findViewById(R.id.returnToRobotButton).performClick();
            assertEquals(View.GONE, a.findViewById(R.id.developerPanel).getVisibility());
            assertSame(service.get(), field(a, "tcpService"));
        });
    }

    @Test public void overlayAndDeveloperNavigationOnlyChangeViews() {
        AtomicInteger finishes = new AtomicInteger(), cancels = new AtomicInteger();
        try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
            scenario.onActivity(a -> {
                assertEquals(View.GONE, a.findViewById(R.id.developerPanel).getVisibility());
                RobotPresentation preview = new RobotPresentation(a, finishes::incrementAndGet, cancels::incrementAndGet);
                // Swap only the view presenter so UI timers cannot reset the visual fixture.
                try {
                    Field f = MainActivity.class.getDeclaredField("presentation");
                    f.setAccessible(true);
                    f.set(a, preview);
                } catch (Exception e) { throw new AssertionError(e); }
                preview.conversation(Arrays.asList(
                        new TcpService.ConversationEntry("user", "这个展项能解决什么问题？"),
                        new TcpService.ConversationEntry("assistant", "它能把分散的设备统一管理，让运行状态一目了然，也让日常运维更高效。")));
                preview.connection(true, false);
                assertEquals(View.VISIBLE, a.findViewById(R.id.robotCaptionPanel).getVisibility());
                assertEquals(View.GONE, a.findViewById(R.id.robotTeachingPanel).getVisibility());
            });
            scenario.onActivity(a -> capture(a, "overlay-question-answer"));
            scenario.onActivity(a -> {
                RobotPresentation p = (RobotPresentation) field(a, "presentation");
                p.conversation(Arrays.asList(
                        new TcpService.ConversationEntry("user", "这个展项能解决什么问题？"),
                        new TcpService.ConversationEntry("staff", "这里展示的是统一设备管理。"),
                        new TcpService.ConversationEntry("staff", "工作人员可以直接查看设备状态。")));
                p.teaching("原问题：这个展项能解决什么问题？\n正在听您讲解 · 剩余96秒", true, true);
                assertTrue(a.findViewById(R.id.robotFinishTeaching).isEnabled());
                assertTrue(((TextView) a.findViewById(R.id.robotAnswerText)).getText().toString().contains("工作人员可以"));
            });
            scenario.onActivity(a -> capture(a, "overlay-teaching"));
            scenario.onActivity(a -> {
                a.findViewById(R.id.robotFinishTeaching).performClick();
                a.findViewById(R.id.robotCancelTeaching).performClick();
                assertEquals(1, finishes.get());
                assertEquals(1, cancels.get());
                RobotPresentation p = (RobotPresentation) field(a, "presentation");
                p.teaching("已记录，等待审核", false, false);
                assertEquals(View.GONE, a.findViewById(R.id.robotFinishTeaching).getVisibility());
            });
            checkHiddenSettingsGesture(scenario);
            scenario.onActivity(a -> {
                RobotPresentation p = (RobotPresentation) field(a, "presentation");
                // Change only the displayed state; leave the real TCP connection intact.
                p.connection(false, false);
            });
            checkHiddenSettingsGesture(scenario);
        }
    }
}
