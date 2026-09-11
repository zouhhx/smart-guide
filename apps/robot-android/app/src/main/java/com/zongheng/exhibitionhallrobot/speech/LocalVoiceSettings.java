package com.zongheng.exhibitionhallrobot.speech;
import android.content.Context;
import com.zongheng.exhibitionhallrobot.R;
/** The seven voices currently exposed by the LAN TTS service. */
public final class LocalVoiceSettings {
    public static final String LOCAL_VOICE="wanwan_xiaohe";
    private static final String[] IDS={LOCAL_VOICE,"ma_junfei","tw_sweet","child_cute","huayan","baker","cn_66"};
    private static final String[] LABELS={"湾湾小何","马俊飞","台湾腔 · 甜美女声","童声风格 · 活泼可爱","华严","贝克","中文66号"};
    private static final int[] WAKEUPS={R.raw.wanwan_wakeup,R.raw.local_wakeup_ma_junfei,R.raw.local_wakeup_tw_sweet,R.raw.local_wakeup_child_cute,R.raw.local_wakeup_huayan,R.raw.local_wakeup_baker,R.raw.local_wakeup_cn_66};
    private LocalVoiceSettings() {}
    public static String[] labels(){return LABELS.clone();}
    public static String idAt(int index){return IDS[index];}
    public static int selectedIndex(Context context){
        String saved=context.getSharedPreferences("local_voice",Context.MODE_PRIVATE).getString("voice",LOCAL_VOICE);
        for(int i=0;i<IDS.length;i++)if(IDS[i].equals(saved))return i;
        return 0;
    }
    public static String selected(Context context){return IDS[selectedIndex(context)];}
    public static String selectedLabel(Context context){return LABELS[selectedIndex(context)];}
    public static boolean select(Context context,int index){
        return index>=0 && index<IDS.length && context.getSharedPreferences("local_voice",Context.MODE_PRIVATE).edit().putString("voice",IDS[index]).commit();
    }
    static int wakeupResource(int index){return WAKEUPS[index];}
}
