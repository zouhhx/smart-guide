package com.zongheng.exhibitionhallrobot.speech;

import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/** Raw PCM framing and lossless 300-code-point request splitting for the LAN TTS service. */
final class LocalPcmStream {
    interface Sink { void accept(byte[] pcm) throws IOException; }
    static void read(InputStream input, Sink sink) throws IOException {
        byte[] buffer = new byte[8192];
        int pending=0; boolean started=false, received=false;
        int n;
        while ((n=input.read(buffer,pending,buffer.length-pending))!=-1) {
            if (n==0) continue;
            received=true; pending+=n;
            if (!started && pending<4800) continue;
            int aligned=pending & ~1;
            if (aligned>0) { sink.accept(Arrays.copyOf(buffer,aligned)); started=true; }
            if (aligned<pending) buffer[0]=buffer[aligned];
            pending-=aligned;
        }
        if (pending%2!=0) throw new IOException("TTS incomplete PCM sample");
        if (pending>0) sink.accept(Arrays.copyOf(buffer,pending));
        if (!received) throw new IOException("TTS empty audio");
    }

    static List<String> split(String text) {
        List<String> parts=new ArrayList<>();
        int start=0;
        while(start<text.length()) {
            int end=text.offsetByCodePoints(start, Math.min(300,text.codePointCount(start,text.length())));
            if(end<text.length()) {
                for(int i=end-1;i>start;i--) {
                    if("。！？!?；;\n".indexOf(text.charAt(i))>=0) { end=i+1; break; }
                }
            }
            String part=text.substring(start,end);
            if(!part.trim().isEmpty()) parts.add(part);
            start=end;
        }
        return parts;
    }
}
