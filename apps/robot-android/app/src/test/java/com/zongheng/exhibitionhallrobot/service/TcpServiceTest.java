package com.zongheng.exhibitionhallrobot.service;

import org.junit.Test;

import static org.junit.Assert.assertEquals;

public class TcpServiceTest {
    @Test
    public void listenTimeoutCapsLegacyServerValueAtFiveSeconds() {
        assertEquals(5, TcpService.normalizeCmdListenTimeout(30));
    }

    @Test
    public void listenTimeoutIsClampedToSafeBounds() {
        assertEquals(5, TcpService.normalizeCmdListenTimeout(0));
        assertEquals(5, TcpService.normalizeCmdListenTimeout(300));
    }
}
