package com.zongheng.exhibitionhallrobot.speech;
import android.content.Context;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import java.io.InputStream;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;
@RunWith(AndroidJUnit4.class)
public class LocalVoiceCatalogueTest {
    @Test public void allSevenVoicesSynthesizeAndHaveMatchingWakeupCache()throws Exception {
        Context context=InstrumentationRegistry.getInstrumentation().getTargetContext();
        LocalTtsController controller=new LocalTtsController(context,s->android.util.Log.i("CatalogueTest",s),true);
        assertEquals(7,LocalVoiceSettings.labels().length);assertEquals("wanwan_xiaohe",LocalVoiceSettings.idAt(0));
        try{
            for(int i=0;i<7;i++){
                assertTrue(LocalVoiceSettings.select(context,i));assertEquals(i,LocalVoiceSettings.selectedIndex(context));
                try(InputStream in=context.getResources().openRawResource(LocalVoiceSettings.wakeupResource(i))){assertEquals('R',in.read());assertEquals('I',in.read());}
                CountDownLatch done=new CountDownLatch(1);AtomicReference<String> error=new AtomicReference<>();
                controller.start("您好。",new LocalTtsController.Listener(){
                    public void onStart(long ms){}
                    public void onComplete(){done.countDown();}
                    public void onError(String s){error.set(s);done.countDown();}
                });
                assertTrue(done.await(25,TimeUnit.SECONDS));assertNull(error.get());
            }
        }finally{controller.cancel();LocalVoiceSettings.select(context,0);}
    }
}
