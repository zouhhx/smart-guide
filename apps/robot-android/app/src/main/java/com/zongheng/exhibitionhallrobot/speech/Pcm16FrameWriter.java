package com.zongheng.exhibitionhallrobot.speech;

import java.util.Arrays;

/**
 * Converts little-endian PCM16 input into the exact 1280-byte mono frames
 * expected by SparkChain ASR. RobotOS exposes stereo microphone samples, so
 * stereo mode keeps the left channel and discards the right channel.
 */
final class Pcm16FrameWriter {
    interface Sink {
        boolean write(byte[] frame);
    }

    static final int OUTPUT_FRAME_BYTES = 1280;

    private final boolean stereoInput;
    private final Sink sink;
    private final byte[] channelFrame = new byte[4];
    private final byte[] outputFrame = new byte[OUTPUT_FRAME_BYTES];
    private int channelFrameLength;
    private int outputLength;

    Pcm16FrameWriter(boolean stereoInput, Sink sink) {
        this.stereoInput = stereoInput;
        this.sink = sink;
    }

    boolean accept(byte[] data, int length) {
        if (data == null || length <= 0) return true;
        int safeLength = Math.min(length, data.length);
        if (!stereoInput) {
            return acceptMono(data, safeLength);
        }

        for (int i = 0; i < safeLength; i++) {
            channelFrame[channelFrameLength++] = data[i];
            if (channelFrameLength == 4) {
                if (!appendMonoByte(channelFrame[0]) || !appendMonoByte(channelFrame[1])) {
                    return false;
                }
                channelFrameLength = 0;
            }
        }
        return true;
    }

    private boolean acceptMono(byte[] data, int length) {
        int offset = 0;
        while (offset < length) {
            int copied = Math.min(length - offset, OUTPUT_FRAME_BYTES - outputLength);
            System.arraycopy(data, offset, outputFrame, outputLength, copied);
            outputLength += copied;
            offset += copied;
            if (outputLength == OUTPUT_FRAME_BYTES && !flush()) return false;
        }
        return true;
    }

    private boolean appendMonoByte(byte value) {
        outputFrame[outputLength++] = value;
        return outputLength != OUTPUT_FRAME_BYTES || flush();
    }

    private boolean flush() {
        byte[] frame = Arrays.copyOf(outputFrame, outputFrame.length);
        outputLength = 0;
        return sink.write(frame);
    }
}
