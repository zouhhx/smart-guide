package com.zongheng.exhibitionhallrobot.service;

import org.junit.Test;
import static org.junit.Assert.*;

public class KeywordWakeupGateTest {
    @Test public void deduplicatesOnlyImmediateRepeatedKeywordEvents() {
        KeywordWakeupGate gate = new KeywordWakeupGate();
        assertTrue(gate.accept(1000));
        assertFalse(gate.accept(1000));
        assertFalse(gate.accept(1100));
        assertTrue(gate.accept(1400));
        assertTrue(gate.accept(3000));
    }
}
