package com.zongheng.exhibitionhallrobot.util;

import java.util.concurrent.atomic.AtomicInteger;

public class ReqIdUtils {

    private static final AtomicInteger generator = new AtomicInteger(10000000);

    public static int next() {
        int id = generator.incrementAndGet();

        if (id > 99999999) {
            generator.set(10000000);
            id = generator.incrementAndGet();
        }

        return id;
    }
}
