package com.zongheng.exhibitionhallrobot.service;
import org.junit.Test;
import static org.junit.Assert.*;

public class TeachingSessionControllerTest {
    @Test public void streamingTranscriptDetectsOnlyStandaloneReplayEnding() {
        assertTrue(TeachingSessionController.hasReplayEnding("第一步检查电源。现在会了吗？"));
        assertFalse(TeachingSessionController.hasReplayEnding("他问我现在会了吗是什么意思？"));
        assertFalse(TeachingSessionController.hasReplayEnding("现在会了吗？继续介绍设备。"));
    }
    @Test public void finalMimoTranscriptCanArriveAfterCaptureDeadlineExactlyOnce() {
        TeachingSessionController c=active();
        assertTrue(c.beginTranscribing("t"));
        assertFalse(c.isCollecting()); assertTrue(c.isActive());
        assertEquals("",c.timedFinishReason(200000));
        assertEquals(TeachingSessionController.SpeechAction.IGNORE,c.acceptSpeech("原机错误文字",200000).action);
        TeachingSessionController.SpeechDecision finalText=c.acceptTranscription("先保存现场画面，再通知调度人员，最后登记处理结果。");
        assertTrue(finalText.shouldSend()); assertEquals(1,finalText.fragmentSeq);
        assertEquals(TeachingSessionController.SpeechAction.IGNORE,c.acceptTranscription("重复回调").action);
        assertTrue(c.finish("t"));
    }
    @Test public void cancelledFinalTranscriptCannotReviveTeaching() {
        TeachingSessionController c=active(); c.beginTranscribing("t"); c.stop();
        assertEquals(TeachingSessionController.SpeechAction.IGNORE,c.acceptTranscription("晚到结果").action);
        assertFalse(c.finish("t"));
    }
    private TeachingSessionController active() {
        TeachingSessionController c = new TeachingSessionController();
        c.prepareStart("t", "原问题", 120);
        assertTrue(c.onPromptTtsSucceeded("t", 500));
        assertTrue(c.onListenReady("t", 1000));
        return c;
    }
    @Test public void ttsSuccessIsNotListeningReady() {
        TeachingSessionController c = new TeachingSessionController();
        c.prepareStart("t", "原问题", 120);
        assertFalse(c.onPromptTtsSucceeded("old", 10));
        assertTrue(c.onPromptTtsSucceeded("t", 100));
        assertFalse(c.isCollecting());
        assertFalse(c.onPromptTtsSucceeded("t", 200));
        assertTrue(c.onListenReady("t", 500));
        assertEquals(120, c.remainingSeconds(500));
        assertFalse(c.onListenReady("t", 800));
        assertEquals(119, c.remainingSeconds(1500));
    }
    @Test public void failedReplacedAndLifecycleNeverRestart() {
        TeachingSessionController c = new TeachingSessionController();
        c.prepareStart("t", "问题", 120);
        c.onPromptTtsFailed("t");
        assertFalse(c.onPromptTtsSucceeded("t", 10));
        c.prepareStart("new", "新问题", 120);
        assertFalse(c.onPromptTtsSucceeded("t", 10));
        c.stop();
        assertFalse(c.onListenReady("new", 100));
        assertFalse(c.isActive());
    }
    @Test public void controlsAndQuotedReplayRemainFragments() {
        TeachingSessionController c = active();
        assertEquals(TeachingSessionController.SpeechAction.FRAGMENT, c.acceptSpeech("开灯，停下，播放", 2000).action);
        assertEquals(TeachingSessionController.SpeechAction.FRAGMENT, c.acceptSpeech("他问我现在会了吗", 3000).action);
        assertEquals(TeachingSessionController.SpeechAction.FRAGMENT, c.acceptSpeech("讲解完毕", 4000).action);
        assertEquals(TeachingSessionController.SpeechAction.FRAGMENT, c.acceptSpeech("取消录入", 5000).action);
    }
    @Test public void replayQuestionIsExcludedAndSuffixPreserved() {
        TeachingSessionController c = active();
        TeachingSessionController.SpeechDecision r = c.acceptSpeech("统一管理。现在会了吗？", 2000);
        assertEquals(TeachingSessionController.SpeechAction.FINISH, r.action);
        assertEquals("统一管理", r.rawText);
        assertEquals(1, r.fragmentSeq);
        assertTrue(c.finish("t"));
        assertFalse(c.finish("t"));
        c = active();
        r = c.acceptSpeech("学会了吗？", 2000);
        assertEquals(TeachingSessionController.SpeechAction.FINISH, r.action);
        assertFalse(r.shouldSend());
        assertEquals(0, r.fragmentSeq);
    }
    @Test public void duplicatesWithinTurnButLaterEmphasisAllowed() {
        TeachingSessionController c = active();
        assertEquals(1, c.acceptSpeech("答案", 2000).fragmentSeq);
        assertEquals(TeachingSessionController.SpeechAction.DUPLICATE, c.acceptSpeech("答案", 2300).action);
        assertEquals(2, c.acceptSpeech("答案", 3600).fragmentSeq);
        c.stop(); c.prepareStart("other", "另一个问题", 120);
        c.onPromptTtsSucceeded("other", 4000); c.onListenReady("other", 4100);
        assertEquals(1, c.acceptSpeech("答案", 4200).fragmentSeq);
    }
    @Test public void silenceUsesPartialActivityAndRequiresVerification() {
        TeachingSessionController c = active();
        assertEquals("", c.timedFinishReason(11000));
        c.setSilenceVerified(true);
        c.speechActivity(10000);
        assertEquals("", c.timedFinishReason(19000));
        assertEquals("silence_10s", c.timedFinishReason(20000));
        c.speechActivity(20500); // More than 10s continuous speech without final ASR.
        assertEquals("", c.timedFinishReason(21000));
        assertEquals(0, c.getFragmentSeq());
    }
    @Test public void deadlineNeverResetsAndFinishIsExactlyOnce() {
        TeachingSessionController c = active();
        c.acceptSpeech("第一段", 2000);
        c.acceptSpeech("第二段", 120000);
        assertFalse(c.isExpired(120999));
        assertEquals("deadline_120s", c.timedFinishReason(121000));
        assertEquals(0, c.remainingSeconds(121000));
        assertTrue(c.finish("t"));
        assertFalse(c.finish("t"));
        assertFalse(c.isActive());
        assertEquals(2, c.getFragmentSeq());
        assertEquals(TeachingSessionController.SpeechAction.IGNORE, c.acceptSpeech("迟到", 122000).action);
    }
    @Test public void cancellationAndWrongTurnDoNotFinishNewCapture() {
        TeachingSessionController c = active();
        assertFalse(c.finish("old"));
        assertTrue(c.isCollecting());
        c.stop();
        assertFalse(c.finish("t"));
        c.prepareStart("next", "下一问", 120);
        assertFalse(c.onListenReady("t", 2000));
    }
}
