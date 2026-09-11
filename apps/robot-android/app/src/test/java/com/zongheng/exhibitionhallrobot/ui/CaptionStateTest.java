package com.zongheng.exhibitionhallrobot.ui;

import org.junit.Test;
import static org.junit.Assert.*;

public class CaptionStateTest {
    @Test public void newestQuestionNeverShowsPreviousAnswer() {
        CaptionState state = new CaptionState();
        state.accept("user", "旧问题");
        state.accept("assistant", "旧答案");
        state.accept("user", "新问题");
        assertEquals("新问题", state.question);
        assertEquals("", state.answer);
    }
    @Test public void latestRobotAnswerIsNotEntireHistory() {
        CaptionState state = new CaptionState();
        state.accept("assistant", "欢迎");
        state.accept("user", "云猴是什么");
        state.accept("assistant", "请稍等");
        state.accept("assistant", "最新答案");
        assertEquals("云猴是什么", state.question);
        assertEquals("最新答案", state.answer);
        assertEquals("小豹", state.answerRole);
    }
    @Test public void staffFragmentsAccumulateAndDoNotBecomeQuestions() {
        CaptionState state = new CaptionState();
        state.accept("user", "原问题");
        state.accept("assistant", "邀请补充");
        state.accept("staff", "开灯之后");
        state.accept("staff", "可以查看设备状态");
        state.accept("status", "已提交审核");
        assertEquals("原问题", state.question);
        assertEquals("开灯之后\n可以查看设备状态", state.answer);
        assertEquals("工作人员 · 已识别", state.answerRole);
    }
    @Test public void emptyAndStatusEventsDoNotFabricateAnswers() {
        CaptionState state = new CaptionState();
        state.accept("user", " ");
        state.accept("assistant", null);
        state.accept("status", "服务状态");
        assertFalse(state.hasContent());
    }
    @Test public void readingTimeIsBounded() {
        CaptionState state = new CaptionState();
        assertEquals(45000, state.displayDurationMs());
        state.answer = new String(new char[4000]);
        assertEquals(180000, state.displayDurationMs());
    }
    @Test public void idleHidesButSpeakingAndTeachingKeepVisible() {
        assertFalse(CaptionState.visible(50000, 45000, false, false));
        assertTrue(CaptionState.visible(50000, 45000, true, false));
        assertTrue(CaptionState.visible(50000, 45000, false, true));
        assertTrue(CaptionState.visible(44000, 45000, false, false));
    }
}
