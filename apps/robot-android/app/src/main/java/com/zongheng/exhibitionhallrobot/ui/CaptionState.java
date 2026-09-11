package com.zongheng.exhibitionhallrobot.ui;

/** Display-only reduction; never routes speech or dispatches commands. */
public final class CaptionState {
    public String question = "", answer = "", answerRole = "小豹";
    public void accept(String role, String text) {
        if (text == null || text.trim().isEmpty()) return;
        if ("user".equals(role)) {
            question = text;
            answer = "";
            answerRole = "小豹";
        } else if ("assistant".equals(role)) {
            answer = text;
            answerRole = "小豹";
        } else if ("staff".equals(role)) {
            answer = "工作人员 · 已识别".equals(answerRole) && !answer.isEmpty() ? answer + "\n" + text : text;
            answerRole = "工作人员 · 已识别";
        }
    }
    public boolean hasContent() { return !question.isEmpty() || !answer.isEmpty(); }
    public long displayDurationMs() { return Math.min(180000L, Math.max(45000L, answer.length() * 200L)); }
    public static boolean visible(long now, long deadline, boolean active, boolean speaking) {
        return active || speaking || now < deadline;
    }
}
