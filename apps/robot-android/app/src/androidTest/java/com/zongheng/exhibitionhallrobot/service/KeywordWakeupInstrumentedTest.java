package com.zongheng.exhibitionhallrobot.service;

import android.os.Handler;
import android.os.HandlerThread;
import com.ainirobot.coreservice.client.Definition;
import com.zongheng.exhibitionhallrobot.application.MyModuleCallback;
import com.zongheng.exhibitionhallrobot.application.WakeUpSkillCallback;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.io.BufferedWriter;
import java.io.StringWriter;
import java.net.Socket;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

/** Synthetic KWS route and pending-TTS cancellation; no speaker, mic, motion, or network. */
@RunWith(AndroidJUnit4.class)
public class KeywordWakeupInstrumentedTest {
    private static Field field(Object object, String name) throws Exception {
        Field value = object.getClass().getDeclaredField(name); value.setAccessible(true); return value;
    }

    @Test public void keywordIsHandledBeforeAnySpeechAndAsrStartDoesNotRepeatIt() throws Exception {
        AtomicInteger wakes = new AtomicInteger(), finished = new AtomicInteger(), starts = new AtomicInteger();
        MyModuleCallback module = new MyModuleCallback() {
            @Override protected void onKeywordWakeup() { wakes.incrementAndGet(); }
            @Override protected void finishSpeechRequest(int id) { finished.incrementAndGet(); }
        };
        assertTrue(module.onSendRequest(42, Definition.REQ_SPEECH_WAKEUP, "", "0"));
        assertEquals(1, wakes.get()); assertEquals(1, finished.get());
        WakeUpSkillCallback asr = new WakeUpSkillCallback();
        asr.setListener(new WakeUpSkillCallback.SpeechResultListener() {
            public void onAsrResult(String text) { fail("not expected"); }
            public void onStart() { starts.incrementAndGet(); }
            public void onStop() { }
        });
        asr.onStart();
        assertEquals(1, starts.get()); assertEquals(1, wakes.get());
    }

    @Test public void keywordInvalidatesPendingTtsWithoutWaitingForSdkCallback() throws Exception {
        TcpService service = new TcpService(); // Unattached: no real service lifecycle/network.
        HandlerThread worker = new HandlerThread("kws-test-worker"); worker.start();
        StringWriter receipts = new StringWriter();
        try {
            field(service, "mWorkHandler").set(service, new Handler(worker.getLooper()));
            field(service, "socket").set(service, new Socket()); // Never connected; writer stays in memory.
            field(service, "writer").set(service, new BufferedWriter(receipts));
            field(service, "currentTtsId").setInt(service, 99);
            Method schedule = TcpService.class.getDeclaredMethod("startSpeakWithDelay", String.class, String.class,
                    int.class, int.class, int.class, String.class, String.class);
            schedule.setAccessible(true);
            schedule.invoke(service, "测试", "normal", 123, 99, 60000, "", "");
            Runnable lateCancel = (Runnable) field(service, "activeTtsWakeupCancel").get(service);
            service.onKeywordWakeup();
            assertEquals(-1, field(service, "currentTtsId").getInt(service));
            assertNull(field(service, "activeTtsWakeupCancel").get(service));
            assertNull(field(service, "ttsProtectionRunnable").get(service));
            service.onKeywordWakeup();
            lateCancel.run();
            assertEquals(-1, field(service, "currentTtsId").getInt(service));
            for (int i=0; i<50 && receipts.toString().isEmpty(); i++) Thread.sleep(10);
            String[] messages = receipts.toString().trim().split("\n");
            assertEquals(1, messages.length);
            org.json.JSONObject receipt = new org.json.JSONObject(messages[0]);
            assertEquals("evt_tts_done", receipt.getString("type"));
            assertEquals(123, receipt.getInt("seq"));
            assertFalse(receipt.getJSONObject("data").getBoolean("success"));
            assertEquals("keyword_wakeup", receipt.getJSONObject("data").getString("reason"));
        } finally {
            worker.quitSafely();
            ((ExecutorService)field(service,"sendExecutor").get(service)).shutdownNow();
        }
    }
}
