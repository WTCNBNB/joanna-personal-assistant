package org.joanna.phase5;

import android.content.Intent;

import java.util.ArrayList;
import java.util.List;

final class CaptureStatusBus {
    interface Listener {
        void onStatus(Intent intent);
    }

    private static final List<Listener> listeners = new ArrayList<>();

    private CaptureStatusBus() {
    }

    static synchronized void register(Listener listener) {
        if (!listeners.contains(listener)) listeners.add(listener);
    }

    static synchronized void unregister(Listener listener) {
        listeners.remove(listener);
    }

    static void publish(Intent intent) {
        List<Listener> snapshot;
        synchronized (CaptureStatusBus.class) {
            snapshot = new ArrayList<>(listeners);
        }
        for (Listener listener : snapshot) {
            listener.onStatus(new Intent(intent));
        }
    }
}
