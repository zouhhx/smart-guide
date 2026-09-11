package com.zongheng.exhibitionhallrobot.speech;

import android.content.Context;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import com.iflytek.sparkchain.core.SparkChain;
import com.iflytek.sparkchain.core.SparkChainConfig;
import com.iflytek.sparkchain.core.asr.ASR;
import com.iflytek.sparkchain.core.asr.AsrCallbacks;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.zongheng.exhibitionhallrobot.BuildConfig;
import java.io.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

/** One microphone producer; sequential SparkChain sessions preserve long teaching audio. */
public final class IflytekAsrController {
    public interface Listener {
        void onResult(String text,long durationMs);
        void onError(String reason);
        default void onNoSpeech(){onResult("",0);}
        default void onReady(){}
        default void onPartial(String text){}
    }
    private static final Object SDK_LOCK=new Object();
    private final Context context;
    private final Consumer<String> logger;
    private final Handler main=new Handler(Looper.getMainLooper());
    private volatile Session current;
    private volatile Session previous;
    public IflytekAsrController(Context context,Consumer<String> logger){this.context=context.getApplicationContext();this.logger=logger;}
    public static boolean isEnabledMode(){return "iflytek".equalsIgnoreCase(BuildConfig.ASR_MODE);}
    public boolean hasCredentials(){return new File(context.getNoBackupFilesDir(),"iflytek-asr.json").isFile();}
    public void warmUp(){new Thread(()->{try{ensureSdk();}catch(Exception e){logger.accept("讯飞初始化失败: "+e.getClass().getSimpleName());}},"spark-warmup").start();}
    private void ensureSdk() throws Exception {
        synchronized(SDK_LOCK){
            if(SparkChain.getInst().getInitCode()==0)return;
            File file=new File(context.getNoBackupFilesDir(),"iflytek-asr.json");
            byte[] bytes=new byte[2048];int n;
            try(InputStream in=new FileInputStream(file)){n=in.read(bytes);if(n<=0||in.read()!=-1)throw new IOException("ASR凭据无效");}
            JsonObject keys=JsonParser.parseString(new String(bytes,0,n,java.nio.charset.StandardCharsets.UTF_8)).getAsJsonObject();
            File work=new File(context.getNoBackupFilesDir(),"spark-work");work.mkdirs();
            SparkChainConfig config=SparkChainConfig.builder().appID(keys.get("appId").getAsString())
                .apiKey(keys.get("apiKey").getAsString()).apiSecret(keys.get("apiSecret").getAsString())
                .workDir(work.getAbsolutePath()).logLevel(100);
            int code=SparkChain.getInst().init(context,config);if(code!=0)throw new IOException("ASR初始化错误码 "+code);
        }
    }
    public void startListening(Listener listener){start(false,listener,null,45000);}
    public void startTeaching(Listener listener){start(true,listener,null,45000);}
    void startPcm(byte[] pcm,boolean teaching,int segmentMs,Listener listener){start(teaching,listener,pcm,segmentMs);}
    private synchronized void start(boolean teaching,Listener listener,byte[] fixture,int segmentMs){
        cancel();Session before=previous;Session session=new Session(teaching,listener,fixture,segmentMs);current=session;
        new Thread(()->session.run(before),"spark-session").start();
    }
    public synchronized void cancel(){
        Session session=current;current=null;
        if(session!=null){previous=session;session.cancelled=true;session.stopping=true;new Thread(session::stopRecorder,"spark-cancel").start();}
    }
    public boolean isListening(){Session s=current;return s!=null&&!s.cancelled&&!s.finished.get();}
    public void finish(Listener listener){
        Session s=current;if(s==null){main.post(()->listener.onError("讯飞ASR尚未开始"));return;}
        s.finalListener=listener;s.stopping=true;new Thread(s::stopRecorder,"spark-finish").start();
    }
    public void release(){cancel();}
    private final class Session {
        final boolean teaching;final Listener listener;final byte[] fixture;final int segmentMs;
        final LinkedBlockingDeque<byte[]> queue=new LinkedBlockingDeque<>(250);
        final CountDownLatch closed=new CountDownLatch(1),producerClosed=new CountDownLatch(1);final AtomicBoolean finished=new AtomicBoolean();
        boolean producerStarted;
        final StringBuilder complete=new StringBuilder();final long requested=SystemClock.elapsedRealtime();
        volatile boolean cancelled,stopping,producerDone;
        volatile String error;volatile Listener finalListener;volatile AudioRecord recorder;
        volatile Segment segment;long audioBytes;
        Session(boolean t,Listener l,byte[] f,int ms){teaching=t;listener=l;fixture=f;segmentMs=ms;}
        boolean valid(){return current==this&&!cancelled;}
        void post(Runnable action){main.post(()->{if(valid())action.run();});}
        void stopRecorder(){AudioRecord r=recorder;if(r!=null)try{r.stop();}catch(RuntimeException ignored){}}
        void record(){
            try{
                if(fixture!=null){
                    post(listener::onReady);
                    for(int offset=0;offset<fixture.length&&!stopping&&!cancelled;offset+=1280){
                        byte[] frame=Arrays.copyOfRange(fixture,offset,Math.min(offset+1280,fixture.length));
                        if(frame.length<1280)frame=Arrays.copyOf(frame,1280);
                        offer(frame);SystemClock.sleep(40);
                    }
                }else{
                    AudioRecord r=createRecorder(AudioFormat.CHANNEL_IN_STEREO);boolean stereo=r!=null;
                    if(r==null)r=createRecorder(AudioFormat.CHANNEL_IN_MONO);
                    if(r==null)throw new IOException("讯飞麦克风初始化失败");recorder=r;
                    if(stopping||cancelled)return;r.startRecording();
                    if(r.getRecordingState()!=AudioRecord.RECORDSTATE_RECORDING)throw new IOException("讯飞麦克风启动失败");
                    post(listener::onReady);byte[] input=new byte[stereo?2560:1280];
                    Pcm16FrameWriter frames=new Pcm16FrameWriter(stereo,b->{offer(b);return !cancelled&&error==null;});
                    while(!stopping&&!cancelled){
                        int n=r.read(input,0,input.length);
                        if(n>0){if(!frames.accept(input,n))break;}
                        else if(n<0&&!stopping)throw new IOException("讯飞麦克风读取失败");
                        if(audioBytes/32> (teaching?121000:31000)){stopping=true;break;}
                    }
                }
            }catch(Exception e){if(!cancelled&&!stopping)error="讯飞录音失败: "+e.getClass().getSimpleName();}
            finally{stopRecorder();AudioRecord r=recorder;recorder=null;if(r!=null)r.release();producerDone=true;producerClosed.countDown();}
        }
        void offer(byte[] frame){
            if(cancelled)return;
            if(!queue.offer(frame)){error="讯飞音频缓存已满，本次识别取消";stopping=true;}
            else audioBytes+=frame.length;
        }
        Segment open() throws Exception {
            Segment s=new Segment();segment=s;s.asr=new ASR();
            s.asr.language("zh_cn");s.asr.domain("iat");s.asr.accent("mandarin");s.asr.vinfo(true);s.asr.dwa("wpgs");
            s.asr.vadEos(teaching ? 10000 : 800);
            s.asr.registerCallbacks(new AsrCallbacks(){
                public void onResult(ASR.ASRResult result,Object tag){
                    if(!valid()||segment!=s||result==null)return;
                    s.text=result.getBestMatchText()==null?"":result.getBestMatchText();
                    String preview=complete.toString()+s.text;post(()->listener.onPartial(preview));
                    if(result.getStatus()==2)s.done.countDown();
                }
                public void onError(ASR.ASRError failure,Object tag){
                    if(!valid()||segment!=s)return;
                    s.error="讯飞识别错误码 "+(failure==null?-1:failure.getCode());logger.accept(s.error);s.done.countDown();
                }
                public void onBeginOfSpeech(){}
                public void onEndOfSpeech(){}
            });
            int code=s.asr.start(s.id);if(code!=0)throw new IOException("讯飞启动错误码 "+code);return s;
        }
        void closeSegment(Segment s) throws Exception {
            if(s.done.getCount()!=0){int code=s.asr.stop(false);if(code!=0)throw new IOException("讯飞结束错误码 "+code);}
            long deadline=SystemClock.elapsedRealtime()+10000;
            while(!s.done.await(40,TimeUnit.MILLISECONDS)){if(cancelled)return;if(SystemClock.elapsedRealtime()>deadline)throw new IOException("讯飞最终结果超时");}
            if(s.error!=null)throw new IOException(s.error);
            complete.append(s.text);if(complete.length()>5000)throw new IOException("讯飞文字超过上限");
        }
        void run(Session before){
            try{
                if(before!=null&&!before.closed.await(3,TimeUnit.SECONDS))throw new IOException("讯飞上一轮尚未关闭");
                if(!valid())return;
                // Capture starts before authentication; bounded queue preserves the first syllable.
                producerStarted=true;new Thread(this::record,"spark-recorder").start();ensureSdk();
                Segment s=null;int sent=0;long nextWrite=0;
                while(valid()){
                    if(error!=null)throw new IOException(error);
                    if(s!=null && s.done.getCount()==0){
                        closeSegment(s);if(!teaching)break;
                        if(producerDone&&queue.isEmpty())break;s=null;sent=0;
                    }
                    byte[] frame=queue.poll(40,TimeUnit.MILLISECONDS);
                    if(frame!=null){
                        if(s==null)s=open();
                        long wait=nextWrite-SystemClock.elapsedRealtime();if(wait>0)Thread.sleep(wait);
                        if(!valid())return;
                        int code=s.asr.write(frame);
                        if(code!=0){
                            s.done.await(200,TimeUnit.MILLISECONDS);
                            if(s.error!=null)throw new IOException(s.error);
                            if(s.done.getCount()==0){
                                closeSegment(s);if(!teaching)break;
                                if(!queue.offerFirst(frame))throw new IOException("讯飞音频缓存已满");
                                s=null;sent=0;continue;
                            }
                            throw new IOException("讯飞写入错误码 "+code);
                        }
                        nextWrite=SystemClock.elapsedRealtime()+40;sent+=40;
                        if(teaching&&sent>=segmentMs){closeSegment(s);if(producerDone&&queue.isEmpty())break;s=null;sent=0;}
                    }else if(producerDone){if(s!=null)closeSegment(s);break;}
                }
                if(valid())deliver(null);
            }catch(Exception e){if(valid())deliver(e instanceof IOException?e.getMessage():"讯飞ASR失败: "+e.getClass().getSimpleName());}
            finally{stopping=true;stopRecorder();Segment s=segment;if(s!=null)try{s.asr.stop(true);}catch(Exception ignored){}
                if(producerStarted)try{producerClosed.await(1500,TimeUnit.MILLISECONDS);}catch(InterruptedException ignored){}
                closed.countDown();}
        }
        void deliver(String failure){
            if(!finished.compareAndSet(false,true))return;
            String text=complete.toString().trim();long duration=audioBytes/32;
            logger.accept("讯飞ASR完成 chars="+text.length()+" duration_ms="+duration+" failure="+(failure==null?"none":failure));
            post(()->{Listener target=finalListener==null?listener:finalListener;if(failure!=null)target.onError(failure);else if(text.isEmpty())target.onNoSpeech();else target.onResult(text,duration);});
        }
    }
    private static final class Segment{final String id=UUID.randomUUID().toString();final CountDownLatch done=new CountDownLatch(1);volatile String text="",error;ASR asr;}
    private AudioRecord createRecorder(int channels){
        try{int min=AudioRecord.getMinBufferSize(16000,channels,AudioFormat.ENCODING_PCM_16BIT);if(min<=0)return null;
            AudioRecord r=new AudioRecord(MediaRecorder.AudioSource.MIC,16000,channels,AudioFormat.ENCODING_PCM_16BIT,Math.max(min*2,5120));
            if(r.getState()==AudioRecord.STATE_INITIALIZED)return r;r.release();
        }catch(RuntimeException ignored){}return null;
    }
}
