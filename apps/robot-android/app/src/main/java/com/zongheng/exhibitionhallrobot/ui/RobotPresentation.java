package com.zongheng.exhibitionhallrobot.ui;

import android.app.Activity;
import android.os.SystemClock;
import android.view.View;
import android.widget.Button;
import android.widget.ScrollView;
import android.widget.TextView;
import com.zongheng.exhibitionhallrobot.R;
import com.zongheng.exhibitionhallrobot.service.TcpService;
import java.util.List;

/** A view of the existing service state, not a second conversation/teaching owner. */
public final class RobotPresentation {
    private final View captions, teaching, idle;
    private final TextView question, answer, label, teachingStatus, hint;
    private final ScrollView answerScroll;
    private final Button finish, cancel;
    private CaptionState state = new CaptionState();
    private long deadline;
    private String lastTeaching = "";
    private String dismissedTeaching = "";
    private boolean teachingActive, speaking;
    private String lastAnswer = "";
    private String previewText = "";
    private boolean previewStaff;
    private List<TcpService.ConversationEntry> lastEntries;

    public RobotPresentation(Activity activity, Runnable onFinish, Runnable onCancel) {
        captions = activity.findViewById(R.id.robotCaptionPanel);
        teaching = activity.findViewById(R.id.robotTeachingPanel);
        idle = activity.findViewById(R.id.robotIdleHint);
        hint = activity.findViewById(R.id.robotIdleHint);
        question = activity.findViewById(R.id.robotQuestionText);
        answer = activity.findViewById(R.id.robotAnswerText);
        label = activity.findViewById(R.id.robotAnswerLabel);
        teachingStatus = activity.findViewById(R.id.robotTeachingStatus);
        answerScroll = activity.findViewById(R.id.robotAnswerScroll);
        finish = activity.findViewById(R.id.robotFinishTeaching);
        cancel = activity.findViewById(R.id.robotCancelTeaching);
        finish.setOnClickListener(v -> onFinish.run());
        cancel.setOnClickListener(v -> onCancel.run());
        // Touching/scrolling text extends reading time, never changes robot listening.
        answerScroll.setOnTouchListener((v, event) -> {
            deadline = SystemClock.elapsedRealtime() + state.displayDurationMs();
            return false;
        });
    }

    public void conversation(List<TcpService.ConversationEntry> entries) {
        lastEntries = entries;
        if (!teachingActive && entries != null && !entries.isEmpty()
                && "user".equals(entries.get(entries.size() - 1).getRole())) {
            dismissedTeaching = lastTeaching;
            lastTeaching = "";
        }
        state = new CaptionState();
        if (entries != null) for (TcpService.ConversationEntry entry : entries)
            state.accept(entry.getRole(), entry.getText());
        question.setText(state.question.isEmpty() ? "" : "您说  " + state.question);
        question.setVisibility(state.question.isEmpty() ? View.GONE : View.VISIBLE);
        label.setText(state.answerRole);
        answer.setText(state.answer.isEmpty() ? "正在为您思考…" : state.answer);
        if (!lastAnswer.equals(state.answer)) {
            final boolean showLatest = "工作人员 · 已识别".equals(state.answerRole);
            answerScroll.post(() -> {
                if (showLatest) answerScroll.fullScroll(View.FOCUS_DOWN);
                else answerScroll.scrollTo(0, 0);
            });
            lastAnswer = state.answer;
        }
        deadline = SystemClock.elapsedRealtime() + state.displayDurationMs();
        if (!previewText.isEmpty()) renderPreview();
        refresh();
    }

    public void preview(String text, boolean staff) {
        previewText = text == null ? "" : text;
        previewStaff = staff;
        if (previewText.isEmpty()) { conversation(lastEntries); return; }
        renderPreview();
        deadline = SystemClock.elapsedRealtime() + 45000L;
        refresh();
    }

    private void renderPreview() {
        question.setVisibility(View.GONE);
        label.setText(previewStaff ? "工作人员 · 实时识别" : "您说 · 实时识别");
        answer.setText(previewText);
        answerScroll.post(() -> answerScroll.fullScroll(View.FOCUS_DOWN));
    }

    public void teaching(String display, boolean active, boolean collecting) {
        display = display == null ? "" : display;
        if (active) dismissedTeaching = "";
        if (!active && !display.isEmpty() && display.equals(dismissedTeaching)) display = "";
        if (!display.equals(lastTeaching)) {
            deadline = SystemClock.elapsedRealtime() + 45000L;
            lastTeaching = display;
            teachingStatus.setText(display);
        }
        teachingActive = active;
        finish.setVisibility(collecting ? View.VISIBLE : View.GONE);
        cancel.setVisibility(active ? View.VISIBLE : View.GONE);
        finish.setEnabled(collecting);
        cancel.setEnabled(active);
        refresh();
    }

    public void connection(boolean connected, boolean isSpeaking) {
        speaking = isSpeaking;
        if (speaking) deadline = SystemClock.elapsedRealtime() + state.displayDurationMs();
        String idleText = connected ? "说「小豹小豹」，我在听" : "服务暂未连接，请稍候";
        if (!idleText.contentEquals(hint.getText())) hint.setText(idleText);
        refresh();
    }

    public void refresh() {
        boolean show = CaptionState.visible(SystemClock.elapsedRealtime(), deadline, teachingActive, speaking);
        captions.setVisibility(show && (state.hasContent() || !previewText.isEmpty()) ? View.VISIBLE : View.GONE);
        teaching.setVisibility(show && !lastTeaching.isEmpty() ? View.VISIBLE : View.GONE);
        idle.setVisibility(captions.getVisibility() == View.VISIBLE || teaching.getVisibility() == View.VISIBLE
                ? View.GONE : View.VISIBLE);
    }
}
