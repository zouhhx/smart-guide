package com.zongheng.exhibitionhallrobot.speech;

import android.graphics.Bitmap;
import android.os.SystemClock;
import android.view.TextureView;
import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import com.zongheng.exhibitionhallrobot.MainActivity;
import com.zongheng.exhibitionhallrobot.R;
import com.zongheng.exhibitionhallrobot.ui.RobotSpeechAnimation;
import java.lang.reflect.Field;
import java.io.File;
import java.io.FileOutputStream;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class RobotSpeechAnimationTest {
    @Test public void speakingVideoMovesAndStopsWithoutReplacingCaptions() throws Exception {
        try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
            final RobotSpeechAnimation[] animation = new RobotSpeechAnimation[1];
            final TextureView[] view = new TextureView[1];
            // Hold service polling while testing the presentation's actual decoder independently.
            scenario.onActivity(a -> {
                try {
                    Field field = MainActivity.class.getDeclaredField("speechAnimation"); field.setAccessible(true);
                    animation[0] = (RobotSpeechAnimation) field.get(a); field.set(a, null);
                    view[0] = a.findViewById(R.id.robotSpeakingVideo);
                    assertEquals(0f, view[0].getAlpha(), 0f);
                    animation[0].update(true, true);
                } catch (Exception e) { throw new AssertionError(e); }
            });
            Bitmap[] frames = new Bitmap[2];
            try {
                for (int i=0; i<2; i++) {
                    SystemClock.sleep(i==0 ? 1500 : 650);
                    final int index=i;
                    scenario.onActivity(a -> {
                        assertEquals(1f, view[0].getAlpha(), 0f);
                        frames[index] = view[0].getBitmap(); assertNotNull(frames[index]);
                        try (FileOutputStream output = new FileOutputStream(new File(a.getExternalFilesDir(null), "speaking-frame-"+index+".png"))) {
                            frames[index].compress(Bitmap.CompressFormat.PNG,100,output);
                        } catch (Exception e) { throw new AssertionError(e); }
                        assertNotNull(a.findViewById(R.id.robotCaptionPanel));
                    });
                }
                assertFalse("Speaking frames should change", frames[0].sameAs(frames[1]));
                scenario.onActivity(a -> {
                    animation[0].update(false,true); assertEquals(0f,view[0].getAlpha(),0f);
                    animation[0].update(true,false); assertEquals(0f,view[0].getAlpha(),0f);
                });
            } finally {
                scenario.onActivity(a -> animation[0].release());
                for(Bitmap frame:frames) if(frame!=null) frame.recycle();
            }
        }
    }
}
