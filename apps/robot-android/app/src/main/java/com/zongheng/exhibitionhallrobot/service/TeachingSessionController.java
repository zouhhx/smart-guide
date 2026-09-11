package com.zongheng.exhibitionhallrobot.service;

/** Pure, monotonic-clock state for one capture. No network or RobotOS calls. */
final class TeachingSessionController {
    enum Phase { IDLE, PENDING_TTS, STARTING, COLLECTING, TRANSCRIBING }
    enum SpeechAction { IGNORE, DUPLICATE, FRAGMENT, FINISH }
    static final class SpeechDecision {
        final SpeechAction action;
        final String rawText;
        final String displayFragment;
        final int fragmentSeq;
        SpeechDecision(SpeechAction action, String text, int seq) {
            this.action = action; this.rawText = text; this.displayFragment = text; this.fragmentSeq = seq;
        }
        boolean shouldSend() { return !rawText.isEmpty() && (action == SpeechAction.FRAGMENT || action == SpeechAction.FINISH); }
        boolean shouldContinueListening() { return action == SpeechAction.FRAGMENT || action == SpeechAction.DUPLICATE; }
    }
    private static final String PUNCT = " 。.！!？?，,；;：:\t\r\n";
    private static final String[] REPLAY = {"现在会了吗", "学会了吗", "记住了吗", "现在知道了吗", "能回答刚才的问题了吗"};
    static boolean isReplayQuery(String text) {
        String normalized=strip(text);
        for(String phrase:REPLAY) if(normalized.equals(phrase)) return true;
        return false;
    }
    static boolean hasReplayEnding(String text) {
        String normalized=strip(text);
        int boundary=-1;
        for(int i=0;i<normalized.length();i++)if("。！？!?；;\n".indexOf(normalized.charAt(i))>=0)boundary=i;
        return isReplayQuery(normalized.substring(boundary+1));
    }
    private Phase phase = Phase.IDLE;
    private String turnId = "", question = "", lastText = "";
    private long deadlineMs, lastActivityMs, lastTextMs;
    private int fragmentSeq;
    private boolean silenceVerified;

    synchronized void prepareStart(String id, String q, int timeoutSeconds) {
        stop();
        turnId = clean(id); question = clean(q);
        if (!turnId.isEmpty() && !question.isEmpty()) phase = Phase.PENDING_TTS;
    }
    synchronized boolean onPromptTtsSucceeded(String id, long now) {
        if (phase != Phase.PENDING_TTS || !turnId.equals(id)) return false;
        phase = Phase.STARTING;
        return true;
    }
    synchronized void onPromptTtsFailed(String id) {
        if (turnId.equals(id) && (phase == Phase.PENDING_TTS || phase == Phase.STARTING)) stop();
    }
    synchronized boolean onListenReady(String id, long now) {
        if (phase != Phase.STARTING || !turnId.equals(id)) return false;
        phase = Phase.COLLECTING; deadlineMs = now + 120000; lastActivityMs = now;
        return true;
    }
    synchronized void setSilenceVerified(boolean value) { silenceVerified = value; }
    synchronized void speechActivity(long now) {
        if (phase == Phase.COLLECTING) lastActivityMs = now;
    }
    synchronized String timedFinishReason(long now) {
        if (phase != Phase.COLLECTING) return "";
        if (now >= deadlineMs) return "deadline_120s";
        if (silenceVerified && now - lastActivityMs >= 10000) return "silence_10s";
        return "";
    }
    synchronized boolean finish(String id) {
        if ((phase != Phase.COLLECTING && phase != Phase.TRANSCRIBING) || !turnId.equals(id)) return false;
        phase = Phase.IDLE;
        return true;
    }
    synchronized boolean beginTranscribing(String id) {
        if(phase!=Phase.COLLECTING || !turnId.equals(id)) return false;
        phase=Phase.TRANSCRIBING; return true;
    }
    synchronized boolean isTranscribing() { return phase==Phase.TRANSCRIBING; }
    synchronized SpeechDecision acceptTranscription(String raw) {
        if(phase!=Phase.TRANSCRIBING || fragmentSeq!=0) return new SpeechDecision(SpeechAction.IGNORE,"",fragmentSeq);
        return acceptText(raw,0);
    }
    synchronized SpeechDecision acceptSpeech(String raw, long now) {
        String text = clean(raw);
        if (phase != Phase.COLLECTING || text.isEmpty() || now >= deadlineMs)
            return new SpeechDecision(SpeechAction.IGNORE, "", fragmentSeq);
        speechActivity(now);
        return acceptText(text,now);
    }
    private SpeechDecision acceptText(String raw,long now) {
        String text=clean(raw);
        if(text.isEmpty()) return new SpeechDecision(SpeechAction.IGNORE,"",fragmentSeq);
        if (text.equals(lastText) && now - lastTextMs < 1500)
            return new SpeechDecision(SpeechAction.DUPLICATE, "", fragmentSeq);
        lastText = text; lastTextMs = now;
        String normalized = strip(text);
        for (String phrase : REPLAY) {
            if (normalized.equals(phrase))
                return new SpeechDecision(SpeechAction.FINISH, "", fragmentSeq);
            if (normalized.endsWith(phrase)) {
                String prefix = normalized.substring(0, normalized.length() - phrase.length());
                if (!prefix.isEmpty() && "。！？!?；;\n".indexOf(prefix.charAt(prefix.length() - 1)) >= 0) {
                    prefix = strip(prefix);
                    if (!prefix.isEmpty()) fragmentSeq++;
                    return new SpeechDecision(SpeechAction.FINISH, prefix, fragmentSeq);
                }
            }
        }
        return new SpeechDecision(SpeechAction.FRAGMENT, text, ++fragmentSeq);
    }
    synchronized int remainingSeconds(long now) {
        if (phase == Phase.STARTING) return 120;
        return phase != Phase.COLLECTING ? 0 : (int)Math.max(0, (deadlineMs - now + 999) / 1000);
    }
    synchronized boolean isExpired(long now) { return phase == Phase.COLLECTING && now >= deadlineMs; }
    synchronized boolean isActive() { return phase != Phase.IDLE; }
    synchronized boolean isCollecting() { return phase == Phase.COLLECTING; }
    synchronized boolean isMatchingTurn(String id) { return isActive() && turnId.equals(id); }
    synchronized Phase getPhase() { return phase; }
    synchronized String getTurnId() { return turnId; }
    synchronized String getQuestion() { return question; }
    synchronized int getFragmentSeq() { return fragmentSeq; }
    synchronized void stop() {
        phase = Phase.IDLE; turnId = ""; question = ""; lastText = "";
        deadlineMs = lastActivityMs = lastTextMs = 0; fragmentSeq = 0;
    }
    private static String clean(String s) { return s == null ? "" : s.trim(); }
    private static String strip(String s) {
        s = clean(s);
        while (!s.isEmpty() && PUNCT.indexOf(s.charAt(s.length() - 1)) >= 0) s = s.substring(0, s.length() - 1);
        return s;
    }
}
