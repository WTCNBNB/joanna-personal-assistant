package org.joanna.phase5;

import android.location.Location;

final class GpsPoint {
    final String time;
    final double lat;
    final double lng;
    final Float accuracy;
    final Float speed;
    final String provider;
    final boolean lastKnown;
    final Long locationTimeMs;

    GpsPoint(String time, Location location) {
        this.time = time;
        this.lat = location.getLatitude();
        this.lng = location.getLongitude();
        this.accuracy = location.hasAccuracy() ? location.getAccuracy() : null;
        this.speed = location.hasSpeed() ? location.getSpeed() : null;
        this.provider = location.getProvider() == null ? "" : location.getProvider();
        this.lastKnown = false;
        this.locationTimeMs = location.getTime() > 0 ? location.getTime() : null;
    }

    GpsPoint(String time, double lat, double lng, Float accuracy, Float speed) {
        this(time, lat, lng, accuracy, speed, "", false, null);
    }

    GpsPoint(
        String time,
        double lat,
        double lng,
        Float accuracy,
        Float speed,
        String provider,
        boolean lastKnown,
        Long locationTimeMs
    ) {
        this.time = time;
        this.lat = lat;
        this.lng = lng;
        this.accuracy = accuracy;
        this.speed = speed;
        this.provider = provider == null ? "" : provider;
        this.lastKnown = lastKnown;
        this.locationTimeMs = locationTimeMs;
    }
}
