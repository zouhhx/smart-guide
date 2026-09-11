package com.zongheng.exhibitionhallrobot.speech;
import org.junit.Test;
import static org.junit.Assert.*;
public class SpeechEndGuardTest {
    @Test public void persistentLowLevelActivityAvoidsWastefulEarlyRequests() {
        SpeechEndGuard guard=new SpeechEndGuard();
        guard.activity(1000);guard.nativeFinal(1100);guard.audioActivity(120,1400);
        assertFalse(guard.canSpeculate(1700));
        assertTrue(guard.canSpeculate(1800));
    }
    @Test public void quietTailInvalidatesSnapshotEvenBelowEndpointThreshold() {
        SpeechEndGuard guard=new SpeechEndGuard();
        guard.activity(1000); guard.nativeFinal(1100);
        long snapshot=guard.revision();
        guard.audioActivity(120,1700);
        assertNotEquals(snapshot,guard.revision());
        assertTrue(guard.canFinish(2500)); // Capture duration is unchanged; use the complete WAV instead.
    }
    @Test public void earlyTranscriptionDoesNotShortenCaptureProtection() {
        SpeechEndGuard guard=new SpeechEndGuard();
        guard.activity(1000); guard.nativeFinal(1100);
        assertFalse(guard.canSpeculate(1400));
        assertTrue(guard.canSpeculate(1450));
        assertFalse(guard.canFinish(1450));
        assertFalse(guard.canFinish(2499));
        assertTrue(guard.canFinish(2500));
    }
    @Test public void resumedSpeechInvalidatesEarlySnapshotAndKeepsListening() {
        SpeechEndGuard guard=new SpeechEndGuard();
        guard.activity(1000); guard.nativeFinal(1100);
        long snapshot=guard.revision();
        guard.activity(1700);
        assertNotEquals(snapshot,guard.revision());
        assertFalse(guard.canFinish(2500));
        assertTrue(guard.canFinish(3200));
    }
    @Test public void nativeFinalDoesNotCutOffContinuingPcmSpeech() {
        SpeechEndGuard guard=new SpeechEndGuard();
        guard.activity(1000); guard.nativeFinal(1200);
        assertFalse(guard.canFinish(1600));
        guard.activity(2100);
        assertFalse(guard.canFinish(2500));
        assertFalse(guard.canFinish(3599));
        assertTrue(guard.canFinish(3600));
    }
    @Test public void finalNeedsCandidateAndSmallTailWindow() {
        SpeechEndGuard guard=new SpeechEndGuard();
        assertFalse(guard.canFinish(5000));
        guard.nativeFinal(5000);
        assertFalse(guard.canFinish(5300));
        assertTrue(guard.canFinish(5350));
    }
}
