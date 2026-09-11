package com.zongheng.exhibitionhallrobot.service;

import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import com.zongheng.exhibitionhallrobot.MainActivity;
import com.zongheng.exhibitionhallrobot.R;
import android.widget.TextView;
import java.lang.reflect.Field;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

/** Display partial hypotheses without adding a question/history entry or contacting V5. */
@RunWith(AndroidJUnit4.class)
public class SpeechPreviewInstrumentedTest {
    @Test public void incrementalTextIsVisibleButNeverCommittedToConversation() throws Exception {
        AtomicReference<TcpService> ref=new AtomicReference<>();
        try(ActivityScenario<MainActivity> scenario=ActivityScenario.launch(MainActivity.class)) {
            for(int i=0;i<50 && ref.get()==null;i++) {
                scenario.onActivity(a->{
                    try { Field f=MainActivity.class.getDeclaredField("tcpService"); f.setAccessible(true); ref.set((TcpService)f.get(a)); }
                    catch(Exception error) { throw new AssertionError(error); }
                });
                if(ref.get()==null) Thread.sleep(100);
            }
            assertNotNull(ref.get());
            scenario.onActivity(a->{
                TcpService service=ref.get(); int before=service.getConversationHistory().size();
                service.updateSpeechPreview("传统水利");
                assertEquals("传统水利",((TextView)a.findViewById(R.id.robotAnswerText)).getText().toString());
                service.updateSpeechPreview("传统水利监测的痛点是什么");
                assertEquals("传统水利监测的痛点是什么",((TextView)a.findViewById(R.id.robotAnswerText)).getText().toString());
                assertEquals(before,service.getConversationHistory().size());
                service.updateSpeechPreview("");
            });
        }
    }
}
