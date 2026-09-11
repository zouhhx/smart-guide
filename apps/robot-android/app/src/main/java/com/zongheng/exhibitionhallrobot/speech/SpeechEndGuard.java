package com.zongheng.exhibitionhallrobot.speech;

/** A native final is only a candidate boundary; recent PCM/partial activity holds capture open. */
final class SpeechEndGuard {
    private long lastActivity, lastContentActivity, finalAt = -1;
    private long revision;
    synchronized void activity(long now) { lastActivity = lastContentActivity = now; revision++; }
    synchronized void audioActivity(double rms,long now) {
        if(rms>250) activity(now);
        // Reusing an earlier transcript requires stronger silence evidence than ending capture.
        // Quiet speech/noise can invalidate the snapshot without changing the existing end guard.
        else if(rms>80) { lastContentActivity=now; revision++; }
    }
    synchronized void nativeFinal(long now) { finalAt = now; }
    synchronized long revision() { return revision; }
    synchronized boolean canSpeculate(long now) {
        return finalAt >= 0 && now-finalAt >= 350 && now-lastContentActivity >= 400;
    }
    synchronized boolean canFinish(long now) {
        return finalAt >= 0 && now-finalAt >= 350 && now-lastActivity >= 1500;
    }
}
