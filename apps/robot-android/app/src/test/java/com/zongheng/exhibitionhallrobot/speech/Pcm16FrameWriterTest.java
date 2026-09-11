package com.zongheng.exhibitionhallrobot.speech;

import org.junit.Test;

import java.util.ArrayList;
import java.util.List;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

public class Pcm16FrameWriterTest {
    @Test
    public void stereoInputKeepsLeftChannelAndProducesExactCloudFrame() {
        List<byte[]> frames = new ArrayList<>();
        Pcm16FrameWriter writer = new Pcm16FrameWriter(true, frame -> {
            frames.add(frame);
            return true;
        });
        byte[] stereo = new byte[2560];
        for (int i = 0; i < 640; i++) {
            stereo[i * 4] = (byte) (i & 0xff);
            stereo[i * 4 + 1] = (byte) ((i >>> 8) & 0xff);
            stereo[i * 4 + 2] = 99;
            stereo[i * 4 + 3] = 88;
        }

        assertTrue(writer.accept(stereo, 777));
        assertTrue(writer.accept(slice(stereo, 777), stereo.length - 777));

        assertEquals(1, frames.size());
        byte[] expected = new byte[1280];
        for (int i = 0; i < 640; i++) {
            expected[i * 2] = (byte) (i & 0xff);
            expected[i * 2 + 1] = (byte) ((i >>> 8) & 0xff);
        }
        assertArrayEquals(expected, frames.get(0));
    }

    @Test
    public void monoInputReframesPartialReads() {
        List<byte[]> frames = new ArrayList<>();
        Pcm16FrameWriter writer = new Pcm16FrameWriter(false, frame -> {
            frames.add(frame);
            return true;
        });
        byte[] mono = new byte[2560];
        for (int i = 0; i < mono.length; i++) mono[i] = (byte) i;

        assertTrue(writer.accept(mono, 100));
        assertTrue(writer.accept(slice(mono, 100), 2460));

        assertEquals(2, frames.size());
        assertArrayEquals(slice(mono, 0, 1280), frames.get(0));
        assertArrayEquals(slice(mono, 1280, 1280), frames.get(1));
    }

    private static byte[] slice(byte[] source, int offset) {
        return slice(source, offset, source.length - offset);
    }

    private static byte[] slice(byte[] source, int offset, int length) {
        byte[] result = new byte[length];
        System.arraycopy(source, offset, result, 0, length);
        return result;
    }
}
