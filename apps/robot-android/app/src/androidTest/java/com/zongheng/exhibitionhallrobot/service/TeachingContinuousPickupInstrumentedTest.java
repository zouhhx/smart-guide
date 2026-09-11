package com.zongheng.exhibitionhallrobot.service;

import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import com.zongheng.exhibitionhallrobot.MainActivity;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

/** Callback lifecycle only; does not open ASR, play TTS, or submit teaching data. */
@RunWith(AndroidJUnit4.class)
public class TeachingContinuousPickupInstrumentedTest {
    private static Field field(Object object, String name) throws Exception {
        Field f = object.getClass().getDeclaredField(name);
        f.setAccessible(true);
        return f;
    }

    private static RobotCapabilities capabilities(MainActivity activity) {
        try {
            Object service = field(activity, "tcpService").get(activity);
            assertNotNull(service);
            return (RobotCapabilities) field(service, "robotCapabilities").get(service);
        } catch (Exception e) { throw new AssertionError(e); }
    }

    private static class Receiver implements RobotCapabilities.ListenCallback {
        final List<String> results = new ArrayList<>();
        int ready, partial;
        public void onReady() { ready++; }
        public void onSpeechActivity(String text) { partial++; }
        public void onResult(String text, long duration) { results.add(text); }
        public void onTimeout(String reason) { fail(reason); }
        public void onError(String reason) { fail(reason); }
    }

    private void verify(boolean continuous) throws Exception {
        AtomicReference<RobotCapabilities> ref = new AtomicReference<>();
        try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
            // ActivityScenario is resumed before the local service binder necessarily arrives.
            for (int i = 0; i < 50 && ref.get() == null; i++) {
                scenario.onActivity(a -> {
                    try {
                        if (field(a, "tcpService").get(a) != null) ref.set(capabilities(a));
                    } catch (Exception e) { throw new AssertionError(e); }
                });
                if (ref.get() == null) Thread.sleep(100);
            }
            RobotCapabilities robot = ref.get();
            assertNotNull(robot);
            Field ownerField = field(robot, "listenCallback");
            Object previous = ownerField.get(robot);
            assertNull("Do not run this test during active listening", previous);
            Receiver receiver = new Receiver();
            Method factory = RobotCapabilities.class.getDeclaredMethod("createLongPickupCallback",
                    RobotCapabilities.ListenCallback.class, boolean.class);
            factory.setAccessible(true);
            RobotCapabilities.ListenCallback callback = (RobotCapabilities.ListenCallback)
                    factory.invoke(robot, receiver, continuous);
            try {
                ownerField.set(robot, receiver);
                callback.onReady();
                callback.onSpeechActivity("第一段");
                callback.onResult("第一段", 100);
                callback.onResult("第二段", 100);
                callback.onSpeechActivity("第三段");
                InstrumentationRegistry.getInstrumentation().waitForIdleSync();
                assertEquals(1, receiver.ready);
                assertEquals(continuous ? Arrays.asList("第一段", "第二段") : Arrays.asList("第一段"), receiver.results);
                assertEquals(continuous ? 2 : 1, receiver.partial);
                if (continuous) assertSame(receiver, ownerField.get(robot));
                else assertNull(ownerField.get(robot));
                // A callback from an ended/replaced owner cannot feed the next listener.
                Receiver replacement = new Receiver();
                ownerField.set(robot, replacement);
                callback.onResult("迟到的旧段", 100);
                InstrumentationRegistry.getInstrumentation().waitForIdleSync();
                assertTrue(replacement.results.isEmpty());
                assertEquals(continuous ? 2 : 1, receiver.results.size());
            } finally {
                ownerField.set(robot, previous);
            }
        }
    }

    @Test public void teachingKeepsOneOwnerAcrossUtterances() throws Exception { verify(true); }
    @Test public void ordinaryListeningStillEndsAfterOneResult() throws Exception { verify(false); }
}
