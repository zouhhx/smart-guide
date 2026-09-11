package com.zongheng.exhibitionhallrobot.speech;
import android.content.Context;
import android.os.SystemClock;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import java.io.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class SparkAsrInstrumentedTest {
    @Test public void longTeachingCrossesFortyFiveSecondSdkSessionBoundary()throws Exception {
        Context context=InstrumentationRegistry.getInstrumentation().getTargetContext();
        IflytekAsrController c=new IflytekAsrController(context,s->android.util.Log.i("SparkAsrTest",s));
        byte[] shortClip=pcm(),audio=new byte[52*32000];
        for(int i=0;i<13;i++)System.arraycopy(shortClip,0,audio,i*4*32000,shortClip.length);
        CountDownLatch done=new CountDownLatch(1);AtomicReference<String> text=new AtomicReference<>(),error=new AtomicReference<>();
        try {
            c.startPcm(audio,true,45000,new IflytekAsrController.Listener(){
                public void onResult(String s,long ms){text.set(s);done.countDown();}
                public void onError(String s){error.set(s);done.countDown();}
            });
            assertTrue(done.await(80,TimeUnit.SECONDS));assertNull(error.get());assertNotNull(text.get());
            assertEquals(text.get(),13,text.get().split("来",-1).length-1);
            android.util.Log.i("SparkAsrTest","LONG_52_SECONDS_PASS text="+text.get());
        }finally{c.release();}
    }
    private byte[] pcm()throws Exception {
        ByteArrayOutputStream out=new ByteArrayOutputStream();
        try(InputStream in=InstrumentationRegistry.getInstrumentation().getContext().getAssets().open("asr_benchmark.pcm")){
            byte[] b=new byte[4096];int n;while((n=in.read(b))!=-1)out.write(b,0,n);
        }return out.toByteArray();
    }
    @Test public void finalRecognitionTeachingSegmentsAndCancellation()throws Exception {
        Context context=InstrumentationRegistry.getInstrumentation().getTargetContext();
        IflytekAsrController c=new IflytekAsrController(context,s->android.util.Log.i("SparkAsrTest",s));
        byte[] audio=pcm();
        try {
            for(boolean teaching:new boolean[]{false,true}) {
                CountDownLatch done=new CountDownLatch(1);AtomicReference<String> text=new AtomicReference<>(),error=new AtomicReference<>();
                AtomicInteger partials=new AtomicInteger();
                byte[] input=audio;
                if(teaching){input=new byte[3*64000];for(int i=0;i<3;i++)System.arraycopy(audio,0,input,i*64000,audio.length);}
                c.startPcm(input,teaching,2000,new IflytekAsrController.Listener(){
                    public void onPartial(String s){partials.incrementAndGet();android.util.Log.i("SparkAsrTest","PARTIAL chars="+s.length());}
                    public void onResult(String s,long ms){text.set(s);done.countDown();}
                    public void onError(String s){error.set(s);done.countDown();}
                });
                assertTrue("SDK result timeout",done.await(30,TimeUnit.SECONDS));assertNull(error.get());
                assertNotNull(text.get());assertTrue(text.get(),text.get().contains("来"));assertTrue(partials.get()>0);
                if(teaching)assertEquals(text.get(),3,text.get().split("来",-1).length-1);
                android.util.Log.i("SparkAsrTest","FIXTURE teaching="+teaching+" text="+text.get());
            }
            AtomicInteger obsolete=new AtomicInteger();
            c.startPcm(audio,false,45000,new IflytekAsrController.Listener(){
                public void onResult(String s,long ms){obsolete.incrementAndGet();}
                public void onError(String s){obsolete.incrementAndGet();}
            });
            SystemClock.sleep(200);c.cancel();SystemClock.sleep(1500);assertEquals(0,obsolete.get());
        } finally {c.release();}
    }
}
