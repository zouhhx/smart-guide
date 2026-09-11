package com.zongheng.exhibitionhallrobot.service;

/** Deduplicates a burst of the same KWS event without treating ASR onStart as a wake word. */
final class KeywordWakeupGate {
    private long last = -1;
    synchronized boolean accept(long now) {
        if (last >= 0 && now >= last && now - last < 400) return false;
        last = now;
        return true;
    }
}
