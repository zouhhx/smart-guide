package com.zongheng.exhibitionhallrobot.application;

import android.os.RemoteException;

import com.ainirobot.coreservice.client.RobotApi;
import com.ainirobot.coreservice.client.Definition;
import com.ainirobot.coreservice.client.module.ModuleCallbackApi;
import com.zongheng.exhibitionhallrobot.service.TcpService;
import com.zongheng.exhibitionhallrobot.speech.IflytekAsrController;
import com.zongheng.exhibitionhallrobot.util.LogUtils;

public class MyModuleCallback extends ModuleCallbackApi {
    private static final String TAG = MyModuleCallback.class.getName();

    private TcpServiceCallback tcpServiceCallback;

    public interface TcpServiceCallback {
        void onNavigateArrived(String pointName);
        void onNavigateFailed(String pointName, String error);
    }

    public void setTcpServiceCallback(TcpServiceCallback callback) {
        this.tcpServiceCallback = callback;
    }

    /**
     * Receive speech request
     * 接收语音指令 底层发起request 请求
     *
     * @param reqId
     * @param reqType 语音指令类型, Speech type
     * @param reqText 语音识别内容, Speech text
     * @param reqParam 语音指令参数, Speech param
     * @throws RemoteException
     */
    @Override
    public boolean onSendRequest(int reqId, String reqType, String reqText, String reqParam) throws RemoteException {
        String text = "New speech request type=" + reqType + " chars=" + (reqText == null ? 0 : reqText.length());
        LogUtils.i(TAG, text);

        // This event arrives at KWS detection, before SkillCallback.onStart (speech/VAD).
        if (Definition.REQ_SPEECH_WAKEUP.equals(reqType)) {
            onKeywordWakeup();
            finishSpeechRequest(reqId);
            return true;
        }

        if (IflytekAsrController.isEnabledMode()) {
            LogUtils.d(TAG, "讯飞模式忽略RobotOS文本请求");
            finishSpeechRequest(reqId);
            return true;
        }

        String speechText = reqText == null ? "" : reqText.trim();
        if (!speechText.isEmpty()) {
            MyRobotApplication app = MyRobotApplication.getInstance();
            if (app != null) app.submitSpeechResult(speechText);
        }

        // This callback only hands the text to the existing asynchronous V5 pipeline.
        // Release RobotOS' parser immediately so it does not keep the application suspended.
        finishSpeechRequest(reqId);
        return true;
    }

    protected void onKeywordWakeup() {
        MyRobotApplication app = MyRobotApplication.getInstance();
        TcpService service = app == null ? null : app.getTcpService();
        if (service != null) service.onKeywordWakeup();
    }

    protected void finishSpeechRequest(int reqId) {
        RobotApi.getInstance().finishModuleParser(reqId, true);
    }

    /**
     * hardware error callback
     * 硬件出现异常回调
     * @param function
     * @param type
     * @param message
     * @throws RemoteException
     */
    @Override
    public void onHWReport(int function, String type, String message) throws RemoteException {
        LogUtils.i(TAG, "onHWReport function:" + function + " type:" + type + " message:" + message);
    }

    /**
     * Suspend system, after this message, RobotOS can not work with this APP
     * 控制权被系统剥夺，收到该事件后，所有Api调用无效
     * @throws RemoteException
     */
    @Override
    public void onSuspend() throws RemoteException {
        LogUtils.i(TAG, "onSuspend");
        MyRobotApplication app = MyRobotApplication.getInstance();
        if (app != null) app.onRobotOsSuspend();
    }

    /**
     * Recovery system, after this message, RobotOS can work with this APP again.
     * 控制权恢复，收到该事件后，重新恢复对机器人的控制
     * @throws RemoteException
     */
    @Override
    public void onRecovery() throws RemoteException {
        LogUtils.i(TAG, "onRecovery");
        MyRobotApplication app = MyRobotApplication.getInstance();
        if (app != null) app.recoverSpeechScene();
    }
}
