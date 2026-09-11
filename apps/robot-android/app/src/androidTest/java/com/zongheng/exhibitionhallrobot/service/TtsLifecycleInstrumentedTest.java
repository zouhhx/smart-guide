package com.zongheng.exhibitionhallrobot.service;

import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import com.ainirobot.coreservice.client.listener.TextListener;
import com.zongheng.exhibitionhallrobot.MainActivity;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

/** Synthetic callbacks only: no microphone, playback, or robot movement. */
@RunWith(AndroidJUnit4.class)
public class TtsLifecycleInstrumentedTest {
    private static Field field(Object o, String name) throws Exception {
        Field f=o.getClass().getDeclaredField(name); f.setAccessible(true); return f;
    }
    @Test public void stopReportsFailureOnceAndHighPriorityKeepsItsTask() throws Exception {
        AtomicReference<TcpService> ref=new AtomicReference<>();
        try (ActivityScenario<MainActivity> scenario=ActivityScenario.launch(MainActivity.class)) {
            for (int i=0;i<50 && ref.get()==null;i++) {
                scenario.onActivity(a -> {
                    try { ref.set((TcpService)field(a,"tcpService").get(a)); }
                    catch (Exception e) { throw new AssertionError(e); }
                });
                if (ref.get()==null) Thread.sleep(100);
            }
            assertNotNull(ref.get());
            scenario.onActivity(a -> {
                try {
                    TcpService service=ref.get();
                    RobotCapabilities robot=(RobotCapabilities)field(service,"robotCapabilities").get(service);
                    assertFalse("Run only while idle",robot.isSpeaking());
                    AtomicInteger success=new AtomicInteger(),failed=new AtomicInteger();
                    RobotCapabilities.SpeakCallback receiver=new RobotCapabilities.SpeakCallback() {
                        public void onSuccess() { success.incrementAndGet(); }
                        public void onFailed(String reason) { failed.incrementAndGet(); }
                    };
                    Method factory=RobotCapabilities.class.getDeclaredMethod("createBuiltInTtsListener",
                            int.class,RobotCapabilities.SpeakCallback.class,long.class,AtomicBoolean.class);
                    factory.setAccessible(true);
                    TextListener listener=(TextListener)factory.invoke(robot,-991,receiver,0L,new AtomicBoolean());
                    listener.onStop(); listener.onComplete(); listener.onError(); listener.onStop();
                    assertEquals(1,failed.get()); assertEquals(0,success.get());
                    TextListener normal=(TextListener)factory.invoke(robot,-992,receiver,0L,new AtomicBoolean());
                    normal.onComplete(); normal.onStop();
                    assertEquals(1,failed.get()); assertEquals(1,success.get());

                    Field current=field(service,"currentTtsId"), capability=field(service,"robotCapabilities");
                    int previous=current.getInt(service);
                    assertEquals(-1,previous);
                    // Prevent SDK calls and invalidate the delayed request before restoring.
                    capability.set(service,null);
                    try {
                        AtomicInteger ids=(AtomicInteger)field(service,"ttsTaskId").get(service);
                        int expected=ids.get()+1;
                        Method play=TcpService.class.getDeclaredMethod("playTts",String.class,int.class,
                                String.class,String.class,String.class);
                        play.setAccessible(true);
                        play.invoke(service,"回归测试",-993,"interrupt","","");
                        assertEquals(expected,current.getInt(service));
                    } finally { current.setInt(service,previous); capability.set(service,robot); }
                } catch (Exception e) { throw new AssertionError(e); }
            });
        }
    }
}
