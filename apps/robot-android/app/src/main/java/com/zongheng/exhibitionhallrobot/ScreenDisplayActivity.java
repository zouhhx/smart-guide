package com.zongheng.exhibitionhallrobot;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Handler;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TableLayout;
import android.widget.TableRow;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

public class ScreenDisplayActivity extends Activity {

    private LinearLayout contentContainer;
    private final Handler autoCloseHandler = new Handler();
    private boolean isVoiceIntroductionCompleted = false;
    private static ScreenDisplayActivity currentInstance = null;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        currentInstance = this;

        String action = getIntent().getStringExtra("action");
        String dataJson = getIntent().getStringExtra("data");

        setContentView(createMainLayout());

        if (dataJson != null) {
            try {
                JSONObject data = new JSONObject(dataJson);
                displayContent(action, data);
            } catch (JSONException e) {
                displayError("解析数据失败: " + e.getMessage());
            }
        }

        // 10秒后自动关闭，但可以通过markVoiceIntroductionCompleted方法取消
        startAutoCloseTimer();
    }

    private View createMainLayout() {
        LinearLayout mainLayout = new LinearLayout(this);
        mainLayout.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        mainLayout.setOrientation(LinearLayout.VERTICAL);
        mainLayout.setBackgroundColor(Color.parseColor("#F5F5F5"));
        mainLayout.setPadding(20, 20, 20, 20);

        // 标题栏
        LinearLayout titleBar = new LinearLayout(this);
        titleBar.setOrientation(LinearLayout.HORIZONTAL);
        titleBar.setBackgroundColor(Color.parseColor("#2196F3"));
        titleBar.setPadding(20, 30, 20, 30);

        TextView titleText = new TextView(this);
        titleText.setText("机器人屏幕");
        titleText.setTextColor(Color.WHITE);
        titleText.setTextSize(24);
        titleText.setTypeface(null, Typeface.BOLD);

        Button closeButton = new Button(this);
        closeButton.setText("关闭");
        closeButton.setTextColor(Color.WHITE);
        closeButton.setBackgroundColor(Color.TRANSPARENT);
        closeButton.setOnClickListener(v -> finish());

        titleBar.addView(titleText, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        titleBar.addView(closeButton, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        // 内容区域
        ScrollView scrollView = new ScrollView(this);
        scrollView.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));

        contentContainer = new LinearLayout(this);
        contentContainer.setOrientation(LinearLayout.VERTICAL);
        contentContainer.setPadding(10, 10, 10, 10);

        scrollView.addView(contentContainer);

        mainLayout.addView(titleBar);
        mainLayout.addView(scrollView);

        return mainLayout;
    }

    private void displayContent(String action, JSONObject data) {
        contentContainer.removeAllViews();

        if (action == null) {
            displayError("缺少 action 参数");
            return;
        }

        switch (action) {
            case "list":
                displayList(data);
                break;
            case "card":
                displayCard(data);
                break;
            case "text":
                displayText(data);
                break;
            case "clear":
                clearScreen();
                break;
            default:
                displayError("不支持的展示类型: " + action);
        }
    }

    private void displayList(JSONObject data) {
        String listType = data.optString("listType", "terminal");
        String title = data.optString("title", "");
        JSONArray items = data.optJSONArray("items");

        if (!title.isEmpty()) {
            TextView titleView = new TextView(this);
            titleView.setText(title);
            titleView.setTextSize(22);
            titleView.setTextColor(Color.parseColor("#2196F3"));
            titleView.setTypeface(null, Typeface.BOLD);
            titleView.setPadding(10, 20, 10, 15);
            titleView.setBackgroundColor(Color.parseColor("#E0E0E0"));
            contentContainer.addView(titleView);
        }

        if (items == null || items.length() == 0) {
            TextView emptyView = new TextView(this);
            emptyView.setText("暂无数据");
            emptyView.setTextSize(18);
            emptyView.setTextColor(Color.GRAY);
            emptyView.setPadding(20, 50, 20, 50);
            emptyView.setGravity(android.view.Gravity.CENTER);
            contentContainer.addView(emptyView);
            return;
        }

        // 表格样式
        TableLayout tableLayout = new TableLayout(this);
        tableLayout.setStretchAllColumns(true);
        tableLayout.setBackgroundColor(Color.WHITE);

        // 表头
        TableRow headerRow = new TableRow(this);
        headerRow.setBackgroundColor(Color.parseColor("#E0E0E0"));
        headerRow.setPadding(10, 15, 10, 15);

        String[] headers = {"名称", "状态", "详情"};
        for (String header : headers) {
            TextView headerView = new TextView(this);
            headerView.setText(header);
            headerView.setTextColor(Color.BLACK);
            headerView.setTextSize(16);
            headerView.setTypeface(null, Typeface.BOLD);
            headerView.setPadding(10, 5, 10, 5);
            headerRow.addView(headerView);
        }
        tableLayout.addView(headerRow);

        for (int i = 0; i < items.length(); i++) {
            try {
                JSONObject item = items.getJSONObject(i);
                TableRow dataRow = new TableRow(this);
                dataRow.setPadding(10, 15, 10, 15);
                if (i % 2 == 0) {
                    dataRow.setBackgroundColor(Color.parseColor("#FAFAFA"));
                }

                String name = item.optString("name", "-");
                String status = item.optString("status", "-");
                String detail = item.optString("detail", "-");

                TextView nameView = createCellTextView(name);
                TextView statusView = createCellTextView(status);
                TextView detailView = createCellTextView(detail);

                if ("播放中".equals(status)) {
                    statusView.setTextColor(Color.parseColor("#4CAF50"));
                } else if ("待命".equals(status)) {
                    statusView.setTextColor(Color.parseColor("#FF9800"));
                }

                dataRow.addView(nameView);
                dataRow.addView(statusView);
                dataRow.addView(detailView);
                tableLayout.addView(dataRow);

                View divider = new View(this);
                divider.setLayoutParams(new TableLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, 1));
                divider.setBackgroundColor(Color.parseColor("#DDDDDD"));
                tableLayout.addView(divider);

            } catch (JSONException e) {
                e.printStackTrace();
            }
        }

        contentContainer.addView(tableLayout);
    }

    private void displayCard(JSONObject data) {
        String title = data.optString("title", "信息卡片");
        String content = data.optString("content", "");

        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackgroundColor(Color.WHITE);
        card.setPadding(25, 30, 25, 30);

        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, 10, 0, 10);
        card.setLayoutParams(params);

        TextView titleView = new TextView(this);
        titleView.setText(title);
        titleView.setTextSize(24);
        titleView.setTextColor(Color.parseColor("#2196F3"));
        titleView.setTypeface(null, Typeface.BOLD);
        titleView.setPadding(0, 0, 0, 20);

        TextView contentView = new TextView(this);
        contentView.setText(content.isEmpty() ? "暂无内容" : content);
        contentView.setTextSize(18);
        contentView.setTextColor(Color.parseColor("#333333"));

        card.addView(titleView);
        card.addView(contentView);

        contentContainer.addView(card);
    }

    private void displayText(JSONObject data) {
        String text = data.optString("text", "");

        LinearLayout container = new LinearLayout(this);
        container.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        container.setGravity(android.view.Gravity.CENTER);
        container.setPadding(30, 30, 30, 30);

        TextView textView = new TextView(this);
        textView.setText(text.isEmpty() ? "暂无文字内容" : text);
        textView.setTextSize(28);
        textView.setTextColor(Color.parseColor("#333333"));
        textView.setGravity(android.view.Gravity.CENTER);
        textView.setTypeface(null, Typeface.BOLD);

        container.addView(textView);
        contentContainer.addView(container);
    }

    private void clearScreen() {
        contentContainer.removeAllViews();
        TextView clearedView = new TextView(this);
        clearedView.setText("屏幕已清空");
        clearedView.setTextSize(20);
        clearedView.setTextColor(Color.GRAY);
        clearedView.setGravity(android.view.Gravity.CENTER);
        clearedView.setPadding(20, 100, 20, 100);
        contentContainer.addView(clearedView);
    }

    private void displayError(String error) {
        contentContainer.removeAllViews();
        TextView errorView = new TextView(this);
        errorView.setText("显示错误: " + error);
        errorView.setTextSize(18);
        errorView.setTextColor(Color.RED);
        errorView.setPadding(20, 50, 20, 50);
        errorView.setGravity(android.view.Gravity.CENTER);
        contentContainer.addView(errorView);
    }

    private TextView createCellTextView(String text) {
        TextView textView = new TextView(this);
        textView.setText(text);
        textView.setTextSize(15);
        textView.setTextColor(Color.parseColor("#333333"));
        textView.setPadding(10, 8, 10, 8);
        return textView;
    }

    /**
     * 启动自动关闭定时器
     */
    private void startAutoCloseTimer() {
        // 先移除之前的回调
        autoCloseHandler.removeCallbacksAndMessages(null);
        // 重新设置10秒后关闭
        autoCloseHandler.postDelayed(() -> {
            if (!isVoiceIntroductionCompleted) {
                finish();
            }
        }, 10000);
    }

    /**
     * 标记语音介绍已完成，防止页面过早关闭
     */
    public static void markVoiceIntroductionCompleted() {
        if (currentInstance != null) {
            currentInstance.isVoiceIntroductionCompleted = true;
            // 取消自动关闭定时器
            currentInstance.autoCloseHandler.removeCallbacksAndMessages(null);
        }
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        // 清理静态引用
        if (currentInstance == this) {
            currentInstance = null;
        }
        // 移除所有回调
        autoCloseHandler.removeCallbacksAndMessages(null);
    }
}
