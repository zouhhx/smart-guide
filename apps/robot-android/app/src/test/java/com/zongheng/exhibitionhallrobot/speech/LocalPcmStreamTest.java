package com.zongheng.exhibitionhallrobot.speech;

import java.io.*;
import java.util.*;
import org.junit.Test;
import static org.junit.Assert.*;

public class LocalPcmStreamTest {
    @Test public void oddNetworkChunksAndPrebufferPreserveEveryByte() throws Exception {
        byte[] data=new byte[19000];new Random(1).nextBytes(data);
        ByteArrayInputStream input=new ByteArrayInputStream(data) {
            @Override public synchronized int read(byte[] b,int off,int len) { return super.read(b,off,Math.min(len,301)); }
        };
        ByteArrayOutputStream output=new ByteArrayOutputStream();List<Integer> chunks=new ArrayList<>();
        LocalPcmStream.read(input,b -> { assertEquals(0,b.length%2); chunks.add(b.length); output.write(b); });
        assertTrue(chunks.get(0)>=4800);assertArrayEquals(data,output.toByteArray());
    }
    @Test public void shortAudioFlushesButEmptyOrTruncatedFails() throws Exception {
        ByteArrayOutputStream output=new ByteArrayOutputStream();
        LocalPcmStream.read(new ByteArrayInputStream(new byte[42]),output::write);assertEquals(42,output.size());
        for(int size:new int[]{0,43}) {
            try { LocalPcmStream.read(new ByteArrayInputStream(new byte[size]),b -> {});fail(); }
            catch(IOException expected) { }
        }
    }
    @Test public void brokenConnectionAndCancellationDoNotReportSuccess() {
        try { LocalPcmStream.read(new InputStream(){public int read()throws IOException{throw new IOException("broken");}},b -> {}); fail(); }
        catch(IOException expected){assertEquals("broken",expected.getMessage());}
        try { LocalPcmStream.read(new ByteArrayInputStream(new byte[5000]),b -> {throw new IOException("cancelled");});fail(); }
        catch(IOException expected){assertEquals("cancelled",expected.getMessage());}
    }
    @Test public void longTextSplitsWithoutLosingContentOrSurrogatePairs() {
        StringBuilder text=new StringBuilder();for(int i=0;i<80;i++)text.append("这是完整内容😀。下一句继续。");
        List<String> parts=LocalPcmStream.split(text.toString());assertTrue(parts.size()>1);
        assertEquals(text.toString(),String.join("",parts));
        for(String part:parts){assertTrue(part.codePointCount(0,part.length())<=300);assertFalse(Character.isHighSurrogate(part.charAt(part.length()-1)));}
    }
}
