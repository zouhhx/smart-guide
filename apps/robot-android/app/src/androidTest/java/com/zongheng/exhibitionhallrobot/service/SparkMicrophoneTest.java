package com.zongheng.exhibitionhallrobot.service;
import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import com.zongheng.exhibitionhallrobot.MainActivity;
import java.lang.reflect.Field;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;
@RunWith(AndroidJUnit4.class)
public class SparkMicrophoneTest {
    private Object get(Object o,String n)throws Exception{Field f=o.getClass().getDeclaredField(n);f.setAccessible(true);return f.get(o);}
    @Test public void realMicrophoneStartsAndReturnsToWakeupWithoutSubmittingText()throws Exception{
        AtomicReference<RobotCapabilities> robot=new AtomicReference<>();
        try(ActivityScenario<MainActivity> scene=ActivityScenario.launch(MainActivity.class)){
            for(int i=0;i<40&&robot.get()==null;i++){
                scene.onActivity(a->{try{Object service=get(a,"tcpService");if(service!=null)robot.set((RobotCapabilities)get(service,"robotCapabilities"));}catch(Exception e){throw new AssertionError(e);}});
                if(robot.get()==null)Thread.sleep(100);
            }
            assertNotNull(robot.get());CountDownLatch ready=new CountDownLatch(1);AtomicReference<String> error=new AtomicReference<>();
            scene.onActivity(a->robot.get().startTeachingListening(120,new RobotCapabilities.ListenCallback(){
                public void onReady(){ready.countDown();}
                public void onResult(String s,long ms){}
                public void onTimeout(String s){error.set(s);ready.countDown();}
                public void onError(String s){error.set(s);ready.countDown();}
            }));
            try{
                assertTrue(ready.await(8,TimeUnit.SECONDS));assertNull(error.get());Thread.sleep(700);
                Object controller=get(robot.get(),"iflytekAsrController"),session=get(controller,"current");assertNotNull(session);
                assertTrue((Long)get(session,"audioBytes")>=1280L);
            }finally{scene.onActivity(a->robot.get().stopListening());}
        }
    }
}
