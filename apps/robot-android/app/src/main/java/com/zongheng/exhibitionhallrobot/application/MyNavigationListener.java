package com.zongheng.exhibitionhallrobot.application;

import android.os.RemoteException;
import android.util.Log;

import com.ainirobot.coreservice.client.Definition;
import com.ainirobot.coreservice.client.listener.ActionListener;

public class MyNavigationListener extends ActionListener {
    private static final String TAG = "MyNavigationListener";

    private NavigationCallback callback;

    public interface NavigationCallback {
        void onSuccess();
        void onFailed(String error);
        void onStatusUpdate(int status, String data);
    }

    public MyNavigationListener(NavigationCallback callback) {
        this.callback = callback;
    }

    @Override
    public void onResult(int status, String response, String extraData) throws RemoteException {
        switch (status) {
            case Definition.RESULT_OK:
                if ("true".equals(response)) {
                    Log.d(TAG, "导航成功");
                    if (callback != null) callback.onSuccess();
                } else {
                    Log.d(TAG, "导航失败: " + response);
                    if (callback != null) callback.onFailed(response);
                }
                break;
            case Definition.ACTION_RESPONSE_STOP_SUCCESS:
                Log.d(TAG, "导航已停止");
                break;
        }
    }

    @Override
    public void onError(int errorCode, String errorString, String extraData) throws RemoteException {
        String errorMsg;
        switch (errorCode) {
            case Definition.ERROR_NOT_ESTIMATE:
                errorMsg = "当前未定位";
                break;
            case Definition.ERROR_IN_DESTINATION:
                errorMsg = "已在目的地范围内";
                break;
            case Definition.ERROR_DESTINATION_NOT_EXIST:
                errorMsg = "目的地不存在";
                break;
            case Definition.ERROR_DESTINATION_CAN_NOT_ARRAIVE:
                errorMsg = "目的地不可达";
                break;
            case Definition.ACTION_RESPONSE_ALREADY_RUN:
                errorMsg = "导航任务已在执行";
                break;
            case Definition.ACTION_RESPONSE_REQUEST_RES_ERROR:
                errorMsg = "底盘资源被占用";
                break;
            default:
                errorMsg = "导航失败: code=" + errorCode;
        }
        Log.e(TAG, errorMsg);
        if (callback != null) callback.onFailed(errorMsg);
    }

    @Override
    public void onStatusUpdate(int status, String data, String extraData) throws RemoteException {
        String statusMsg;
        switch (status) {
            case Definition.STATUS_START_NAVIGATION:
                statusMsg = "开始导航";
                break;
            case Definition.STATUS_NAVI_AVOID:
                statusMsg = "路线被障碍物堵死";
                break;
            case Definition.STATUS_NAVI_AVOID_END:
                statusMsg = "障碍物已移除";
                break;
            case Definition.STATUS_NAVI_OUT_MAP:
                statusMsg = "走出地图";
                break;
            case Definition.STATUS_NAVI_GLOBAL_PATH_FAILED:
                statusMsg = "路径规划失败";
                break;
            default:
                statusMsg = "状态更新: " + status;
        }
        Log.d(TAG, statusMsg);
        if (callback != null) callback.onStatusUpdate(status, data);
    }
}
