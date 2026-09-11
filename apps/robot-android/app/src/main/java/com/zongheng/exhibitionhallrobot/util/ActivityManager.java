package com.zongheng.exhibitionhallrobot.util;

import android.app.Activity;
import android.util.Log;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * Activity管理器，用于统一管理所有Activity的生命周期
 */
public class ActivityManager {
    private static final String TAG = "ActivityManager";
    
    private static volatile ActivityManager instance;
    private final CopyOnWriteArrayList<Activity> activityList = new CopyOnWriteArrayList<>();
    
    private ActivityManager() {
    }
    
    public static ActivityManager getInstance() {
        if (instance == null) {
            synchronized (ActivityManager.class) {
                if (instance == null) {
                    instance = new ActivityManager();
                }
            }
        }
        return instance;
    }
    
    /**
     * 添加Activity到管理列表
     */
    public void addActivity(Activity activity) {
        if (activity != null && !activityList.contains(activity)) {
            activityList.add(activity);
            Log.d(TAG, "添加Activity: " + activity.getClass().getSimpleName() + ", 当前总数: " + activityList.size());
        }
    }
    
    /**
     * 从管理列表中移除Activity
     */
    public void removeActivity(Activity activity) {
        if (activity != null) {
            activityList.remove(activity);
            Log.d(TAG, "移除Activity: " + activity.getClass().getSimpleName() + ", 当前总数: " + activityList.size());
        }
    }
    
    /**
     * 关闭所有Activity
     */
    public void finishAllActivities() {
        Log.d(TAG, "开始关闭所有Activity, 总数: " + activityList.size());
        
        // 创建副本以避免并发修改异常
        List<Activity> activitiesToFinish = new ArrayList<>(activityList);
        
        for (Activity activity : activitiesToFinish) {
            if (activity != null && !activity.isFinishing()) {
                try {
                    Log.d(TAG, "关闭Activity: " + activity.getClass().getSimpleName());
                    activity.finish();
                } catch (Exception e) {
                    Log.e(TAG, "关闭Activity失败: " + activity.getClass().getSimpleName(), e);
                }
            }
        }
        
        // 清空列表
        activityList.clear();
        Log.d(TAG, "所有Activity已关闭");
    }
    
    /**
     * 关闭指定类型的Activity
     */
    public void finishActivity(Class<? extends Activity> activityClass) {
        List<Activity> activitiesToFinish = new ArrayList<>();
        
        for (Activity activity : activityList) {
            if (activity != null && activity.getClass() == activityClass && !activity.isFinishing()) {
                activitiesToFinish.add(activity);
            }
        }
        
        for (Activity activity : activitiesToFinish) {
            try {
                Log.d(TAG, "关闭指定Activity: " + activity.getClass().getSimpleName());
                activity.finish();
                activityList.remove(activity);
            } catch (Exception e) {
                Log.e(TAG, "关闭Activity失败: " + activity.getClass().getSimpleName(), e);
            }
        }
    }
    
    /**
     * 获取当前活动的Activity数量
     */
    public int getActivityCount() {
        return activityList.size();
    }
}
