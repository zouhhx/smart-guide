package com.zongheng.exhibitionhallrobot.config;

import android.content.Context;

import com.zongheng.exhibitionhallrobot.util.LogUtils;

import java.io.InputStream;
import java.util.Properties;

public class ServerConfig {
    private static final String TAG = "ServerConfig";
    private static ServerConfig instance;
    private String serverIp;
    private int serverPort;
    private String robotId;

    private ServerConfig() {}

    public static ServerConfig getInstance() {
        if (instance == null) {
            instance = new ServerConfig();
        }
        return instance;
    }

    public void loadFromAssets(Context context) {
        try {
            InputStream inputStream = context.getAssets().open("server_config.properties");
            Properties properties = new Properties();
            properties.load(inputStream);
            serverIp = properties.getProperty("server.ip", "192.168.18.227");
            serverPort = Integer.parseInt(properties.getProperty("server.port", "19888"));
            robotId =  properties.getProperty("robot.id", "robot_001");
            inputStream.close();
            LogUtils.d(TAG, "配置加载成功: " + serverIp + ":" + serverPort);
        } catch (Exception e) {
            LogUtils.e(TAG, "加载配置文件失败，使用默认值", e);
        }
    }

    public String getServerIp() {
        return serverIp;
    }

    public int getServerPort() {
        return serverPort;
    }

    public String getRobotId() {
        return robotId;
    }
}
