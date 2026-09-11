package com.zongheng.exhibitionhallrobot.service;

import com.zongheng.exhibitionhallrobot.monitor.BatteryMonitor;
import org.junit.Test;
import static org.junit.Assert.*;

public class TeachingChargingTest {
    @Test public void dockedRobotWithUnknownBatteryStatusIsStillProtected() {
        assertTrue(BatteryMonitor.isChargingState(0, 1));
        assertTrue(BatteryMonitor.isChargingState(2, 0));
        assertTrue(BatteryMonitor.isChargingState(5, 0));
        assertFalse(BatteryMonitor.isChargingState(3, 0));
    }
}
