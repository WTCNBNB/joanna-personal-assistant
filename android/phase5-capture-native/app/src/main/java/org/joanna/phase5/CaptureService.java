package org.joanna.phase5;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.media.AudioDeviceInfo;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.FileWriter;
import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

public class CaptureService extends Service {
    static final String ACTION_START = "org.joanna.phase5.START";
    static final String ACTION_STOP = "org.joanna.phase5.STOP";
    static final String ACTION_RETRY_CACHE = "org.joanna.phase5.RETRY_CACHE";
    static final String ACTION_STATUS = "org.joanna.phase5.STATUS";
    static final String EXTRA_SERVER_URL = "server_url";
    static final String EXTRA_UPLOAD_TOKEN = "upload_token";
    static final String EXTRA_DEVICE_ID = "device_id";
    static final String EXTRA_DEVICE_NAME = "device_name";
    static final String EXTRA_DEVICE_TYPE = "device_type";
    static final String EXTRA_AUDIO_SOURCE_MODE = "audio_source_mode";
    static final int SEGMENT_MS = 60 * 1000;
    static final int GPS_INTERVAL_MS = 5000;

    private static final String CHANNEL_ID = "joanna_phase5_capture";
    private static final int NOTIFICATION_ID = 5505;

    private volatile boolean running = false;
    private Thread captureThread;
    private AudioManager audioManager;
    private Integer previousAudioMode;
    private LocationManager locationManager;
    private final List<GpsPoint> gpsPoints = new ArrayList<>();
    private final List<String> activeLocationProviders = new ArrayList<>();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private int sequence = 0;
    private PowerManager.WakeLock wakeLock;
    private long lastLevelStatusAt = 0;
    private volatile String locationProviderStatus = "";

    private final LocationListener locationListener = new LocationListener() {
        @Override
        public void onLocationChanged(Location location) {
            synchronized (gpsPoints) {
                gpsPoints.add(new GpsPoint(nowIso(), location));
            }
        }

        @Override
        public void onProviderEnabled(String provider) {
        }

        @Override
        public void onProviderDisabled(String provider) {
            sendStatus("gps_provider_disabled", provider, null);
        }

        @Override
        public void onStatusChanged(String provider, int status, Bundle extras) {
        }
    };

    @Override
    public void onCreate() {
        super.onCreate();
        audioManager = (AudioManager) getSystemService(Context.AUDIO_SERVICE);
        locationManager = (LocationManager) getSystemService(Context.LOCATION_SERVICE);
        ensureNotificationChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) return START_NOT_STICKY;
        String action = intent.getAction();
        if (ACTION_STOP.equals(action)) {
            stopCapture();
            stopSelf();
            return START_NOT_STICKY;
        }
        if (ACTION_RETRY_CACHE.equals(action)) {
            retryCachedUploads(
                intent.getStringExtra(EXTRA_SERVER_URL),
                intent.getStringExtra(EXTRA_UPLOAD_TOKEN)
            );
            return START_NOT_STICKY;
        }
        if (ACTION_START.equals(action)) {
            startForeground(NOTIFICATION_ID, notification("采集中"));
            startCapture(intent);
            return START_STICKY;
        }
        return START_NOT_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        stopCapture();
        super.onDestroy();
    }

    private void startCapture(Intent intent) {
        if (running) return;
        final String serverUrl = intent.getStringExtra(EXTRA_SERVER_URL);
        final String uploadToken = valueOr(intent.getStringExtra(EXTRA_UPLOAD_TOKEN), "");
        final int selectedDeviceId = intent.getIntExtra(EXTRA_DEVICE_ID, -1);
        final String selectedDeviceName = valueOr(intent.getStringExtra(EXTRA_DEVICE_NAME), "unknown");
        final int selectedDeviceType = intent.getIntExtra(EXTRA_DEVICE_TYPE, AudioDeviceInfo.TYPE_UNKNOWN);
        final String audioSourceMode = valueOr(intent.getStringExtra(EXTRA_AUDIO_SOURCE_MODE), "mic");
        running = true;
        sequence = 0;
        acquireWakeLock();
        startLocationUpdates();
        retryCachedUploads(serverUrl, uploadToken);
        captureThread = new Thread(() -> runCaptureLoop(
            serverUrl,
            uploadToken,
            selectedDeviceId,
            selectedDeviceName,
            selectedDeviceType,
            audioSourceMode
        ), "JoannaPhase5NativeCapture");
        captureThread.start();
    }

    private void stopCapture() {
        running = false;
        stopLocationUpdates();
        restoreAudioRoute();
        releaseWakeLock();
        if (captureThread != null) {
            captureThread.interrupt();
            try {
                captureThread.join(1000);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
            captureThread = null;
        }
        sendStatus("stopped", "capture stopped", null);
    }

    private void runCaptureLoop(
        String serverUrl,
        String uploadToken,
        int selectedDeviceId,
        String selectedDeviceName,
        int selectedDeviceType,
        String audioSourceMode
    ) {
        sendStatus("started", "native capture started", null);
        while (running) {
            sequence += 1;
            synchronized (gpsPoints) {
                gpsPoints.clear();
            }
            seedSegmentWithLastKnownLocation();
            try {
                captureOneSegment(serverUrl, uploadToken, selectedDeviceId, selectedDeviceName, selectedDeviceType, audioSourceMode, sequence);
            } catch (Exception error) {
                sendStatus("segment_error", error.toString(), null);
                sleep(1000);
            }
        }
    }

    private void captureOneSegment(
        String serverUrl,
        String uploadToken,
        int selectedDeviceId,
        String selectedDeviceName,
        int selectedDeviceType,
        String audioSourceMode,
        int segmentIndex
    ) throws Exception {
        String startedAt = nowIso();
        AudioDeviceInfo selectedDevice = findInputDevice(selectedDeviceId);
        RouteState route = prepareAudioRoute(selectedDevice, selectedDeviceType);
        int sampleRate = AudioNames.sampleRateForType(selectedDeviceType);
        int channelConfig = AudioFormat.CHANNEL_IN_MONO;
        int audioFormatValue = AudioFormat.ENCODING_PCM_16BIT;
        int minBuffer = AudioRecord.getMinBufferSize(sampleRate, channelConfig, audioFormatValue);
        if (minBuffer <= 0) {
            throw new IllegalStateException("AudioRecord unsupported config: " + sampleRate + "Hz mono PCM16");
        }
        int bufferSize = Math.max(minBuffer * 2, sampleRate / 10);
        AudioFormat format = new AudioFormat.Builder()
            .setSampleRate(sampleRate)
            .setChannelMask(channelConfig)
            .setEncoding(audioFormatValue)
            .build();
        AudioRecord recorder = new AudioRecord.Builder()
            .setAudioSource(AudioNames.audioSourceValue(audioSourceMode))
            .setAudioFormat(format)
            .setBufferSizeInBytes(bufferSize)
            .build();
        if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
            recorder.release();
            throw new IllegalStateException("AudioRecord init failed: state=" + recorder.getState());
        }
        boolean preferredApplied = false;
        if (selectedDevice != null && Build.VERSION.SDK_INT >= 23) {
            preferredApplied = recorder.setPreferredDevice(selectedDevice);
        }

        File audioFile = segmentFile(startedAt, segmentIndex);
        SegmentStats stats = new SegmentStats();
        long audioBytesWritten = 0;
        AudioDeviceInfo actualDevice = null;
        String routeWarning = route.warning;

        try (WavWriter writer = new WavWriter(audioFile, sampleRate, 1, 16)) {
            byte[] buffer = new byte[bufferSize];
            recorder.startRecording();
            long endAt = System.currentTimeMillis() + SEGMENT_MS;
            while (running && System.currentTimeMillis() < endAt) {
                int read = recorder.read(buffer, 0, buffer.length, AudioRecord.READ_BLOCKING);
                if (read > 0) {
                    writer.write(buffer, read);
                    audioBytesWritten += read;
                    stats.readSuccessCount += 1;
                    stats.update(buffer, read);
                } else if (read == 0) {
                    stats.zeroReadCount += 1;
                } else {
                    stats.readErrorCount += 1;
                }
                AudioDeviceInfo routed = recorder.getRoutedDevice();
                if (routed != null) {
                    actualDevice = routed;
                }
                maybeSendLevel(segmentIndex, stats, audioBytesWritten, actualDevice, routeWarning);
            }
        } finally {
            try {
                recorder.stop();
            } catch (Exception ignored) {
            }
            recorder.release();
            restoreAudioRoute();
        }

        String endedAt = nowIso();
        List<GpsPoint> segmentGps = copyGpsPoints();
        if (actualDevice == null) {
            actualDevice = selectedDevice;
            routeWarning = appendWarning(routeWarning, "AudioRecord.getRoutedDevice returned null");
        }
        if (actualDevice != null && AudioNames.isBluetoothInput(selectedDeviceType) && !AudioNames.isBluetoothInput(actualDevice.getType())) {
            routeWarning = appendWarning(routeWarning, "recording route is not bluetooth/DJI");
        }
        Map<String, Object> metadata = buildMetadata(
            startedAt,
            endedAt,
            segmentIndex,
            selectedDeviceId,
            selectedDeviceName,
            selectedDeviceType,
            actualDevice,
            sampleRate,
            audioSourceMode,
            route,
            preferredApplied,
            routeWarning,
            audioBytesWritten,
            stats,
            segmentGps
        );
        sendStatus("uploading", "segment " + segmentIndex, metadata);
        uploadInBackground(serverUrl, uploadToken, audioFile, metadata, segmentGps);
    }

    private void uploadInBackground(
        String serverUrl,
        String uploadToken,
        File audioFile,
        Map<String, Object> metadata,
        List<GpsPoint> segmentGps
    ) {
        new Thread(() -> {
            try {
                String response = MultipartUploader.upload(serverUrl, uploadToken, audioFile, metadata, segmentGps);
                sendStatus("uploaded", response, metadata);
            } catch (Exception error) {
                cacheFailedUpload(audioFile, metadata, segmentGps, error.toString());
                sendStatus("upload_failed", error.toString(), metadata);
            }
        }, "JoannaPhase5Upload").start();
    }

    private RouteState prepareAudioRoute(AudioDeviceInfo selectedDevice, int selectedType) {
        RouteState route = new RouteState();
        if (audioManager == null || !AudioNames.isBluetoothInput(selectedType)) return route;
        try {
            previousAudioMode = audioManager.getMode();
            audioManager.setMode(AudioManager.MODE_IN_COMMUNICATION);
            if (selectedDevice != null && Build.VERSION.SDK_INT >= 31) {
                route.communicationDeviceApplied = audioManager.setCommunicationDevice(selectedDevice);
            }
            audioManager.startBluetoothSco();
            audioManager.setBluetoothScoOn(true);
            long until = System.currentTimeMillis() + 5000;
            while (System.currentTimeMillis() < until && !audioManager.isBluetoothScoOn() && running) {
                sleep(200);
            }
            route.bluetoothScoReady = audioManager.isBluetoothScoOn();
            if (!route.bluetoothScoReady && !route.communicationDeviceApplied) {
                route.warning = appendWarning(route.warning, "Bluetooth SCO was not ready before recording");
            }
        } catch (Exception error) {
            route.warning = appendWarning(route.warning, "failed to enable bluetooth route: " + error);
        }
        return route;
    }

    private void restoreAudioRoute() {
        if (audioManager == null) return;
        try {
            if (Build.VERSION.SDK_INT >= 31) {
                audioManager.clearCommunicationDevice();
            }
            audioManager.setBluetoothScoOn(false);
            audioManager.stopBluetoothSco();
            if (previousAudioMode != null) {
                audioManager.setMode(previousAudioMode);
            }
        } catch (Exception ignored) {
        }
        previousAudioMode = null;
    }

    private Map<String, Object> buildMetadata(
        String startedAt,
        String endedAt,
        int segmentIndex,
        int selectedDeviceId,
        String selectedDeviceName,
        int selectedDeviceType,
        AudioDeviceInfo actualDevice,
        int sampleRate,
        String audioSourceMode,
        RouteState route,
        boolean preferredApplied,
        String routeWarning,
        long audioBytesWritten,
        SegmentStats stats,
        List<GpsPoint> segmentGps
    ) {
        String actualName = actualDevice == null ? "" : AudioNames.deviceName(actualDevice);
        int actualType = actualDevice == null ? AudioDeviceInfo.TYPE_UNKNOWN : actualDevice.getType();
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("device_id", Build.MODEL == null ? "android" : Build.MODEL);
        metadata.put("mic_label", selectedDeviceName);
        metadata.put("segment_index", segmentIndex);
        metadata.put("started_at", startedAt);
        metadata.put("ended_at", endedAt);
        metadata.put("duration_seconds", 60);
        metadata.put("selected_audio_device_id", String.valueOf(selectedDeviceId));
        metadata.put("selected_audio_device_name", selectedDeviceName);
        metadata.put("route_type", AudioNames.deviceTypeName(selectedDeviceType));
        metadata.put("actual_audio_device_id", actualDevice == null ? "" : String.valueOf(actualDevice.getId()));
        metadata.put("actual_audio_device_name", actualName);
        metadata.put("actual_route_type", AudioNames.deviceTypeName(actualType));
        metadata.put("route_warning", routeWarning == null ? "" : routeWarning);
        metadata.put("sample_rate", sampleRate);
        metadata.put("channels", 1);
        metadata.put("codec", "wav/pcm_s16le");
        metadata.put("capture_app", "native_android");
        metadata.put("capture_client_version", "0.1.0-native");
        metadata.put("capture_audio_engine", "audio_record");
        metadata.put("audio_source_mode", audioSourceMode);
        metadata.put("audio_read_mode", "thread_blocking");
        metadata.put("network_mode", "wlan");
        metadata.put("upload_attempt", 1);
        metadata.put("cached_upload", false);
        metadata.put("client_cached_at", "");
        metadata.put("route_binding_strategy", AudioNames.isBluetoothInput(selectedDeviceType)
            ? "native_audio_record_communication_device_sco_preferred_device"
            : "native_audio_record_preferred_device");
        metadata.put("communication_device_applied", route.communicationDeviceApplied);
        metadata.put("preferred_device_applied", preferredApplied);
        metadata.put("bluetooth_sco_ready", route.bluetoothScoReady);
        metadata.put("audio_bytes_written", audioBytesWritten);
        metadata.put("read_success_count", stats.readSuccessCount);
        metadata.put("zero_read_count", stats.zeroReadCount);
        metadata.put("read_error_count", stats.readErrorCount);
        metadata.put("byte_peak", stats.bytePeak);
        metadata.put("non_zero_samples", stats.nonZeroSamples);
        metadata.put("max_amplitude", stats.maxAmplitude);
        metadata.put("gps_point_count", segmentGps.size());
        metadata.put("gps_provider_status", locationProviderStatus);
        metadata.put("gps_active_providers", joinActiveLocationProviders());
        metadata.put("gps_live_point_count", countGpsPoints(segmentGps, false));
        metadata.put("gps_last_known_point_count", countGpsPoints(segmentGps, true));
        metadata.put("no_health_metrics", true);
        return metadata;
    }

    private AudioDeviceInfo findInputDevice(int deviceId) {
        if (audioManager == null) return null;
        AudioDeviceInfo[] devices = audioManager.getDevices(AudioManager.GET_DEVICES_INPUTS);
        for (AudioDeviceInfo device : devices) {
            if (device.getId() == deviceId) return device;
        }
        return null;
    }

    private File segmentFile(String startedAt, int segmentIndex) {
        File dir = new File(getExternalFilesDir(null), "phase5_segments");
        if (!dir.exists()) {
            //noinspection ResultOfMethodCallIgnored
            dir.mkdirs();
        }
        String safeTime = startedAt.replace(":", "").replace("+", "p").replace("-", "").replace(".", "");
        return new File(dir, String.format(Locale.US, "joanna_phase5_%s_%04d.wav", safeTime, segmentIndex));
    }

    private void startLocationUpdates() {
        if (locationManager == null) return;
        boolean hasFine = checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED;
        boolean hasCoarse = checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED;
        if (!hasFine && !hasCoarse) {
            sendStatus("gps_permission_missing", "location permission missing", null);
            return;
        }
        mainHandler.post(() -> {
            List<String> requested = new ArrayList<>();
            List<String> failed = new ArrayList<>();
            Set<String> providers = availableLocationProviders();
            for (String provider : preferredLocationProviders()) {
                if (!providers.contains(provider)) continue;
                if (LocationManager.GPS_PROVIDER.equals(provider) && !hasFine) continue;
                try {
                    locationManager.requestLocationUpdates(
                        provider,
                        GPS_INTERVAL_MS,
                        0,
                        locationListener,
                        Looper.getMainLooper()
                    );
                    requested.add(provider);
                } catch (Exception error) {
                    failed.add(provider + "=" + error.getClass().getSimpleName());
                }
            }
            synchronized (activeLocationProviders) {
                activeLocationProviders.clear();
                activeLocationProviders.addAll(requested);
            }
            locationProviderStatus = requested.isEmpty()
                ? "requested=none failed=" + joinStrings(failed)
                : "requested=" + joinStrings(requested) + (failed.isEmpty() ? "" : " failed=" + joinStrings(failed));
            sendStatus(requested.isEmpty() ? "gps_error" : "gps_started", locationProviderStatus, null);
            seedSegmentWithLastKnownLocation();
        });
    }

    private List<String> preferredLocationProviders() {
        List<String> providers = new ArrayList<>();
        providers.add(LocationManager.GPS_PROVIDER);
        providers.add(LocationManager.NETWORK_PROVIDER);
        providers.add("fused");
        providers.add(LocationManager.PASSIVE_PROVIDER);
        return providers;
    }

    private Set<String> availableLocationProviders() {
        Set<String> providers = new LinkedHashSet<>();
        try {
            providers.addAll(locationManager.getAllProviders());
        } catch (Exception ignored) {
        }
        return providers;
    }

    private void seedSegmentWithLastKnownLocation() {
        if (locationManager == null) return;
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED
            && checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        GpsPoint point = freshestLastKnownPoint();
        if (point == null) return;
        synchronized (gpsPoints) {
            if (!gpsPoints.isEmpty()) return;
            gpsPoints.add(point);
        }
        sendStatus("gps_last_known", point.provider + " accuracy=" + (point.accuracy == null ? "" : point.accuracy), null);
    }

    private GpsPoint freshestLastKnownPoint() {
        Location best = null;
        for (String provider : availableLocationProviders()) {
            try {
                Location location = locationManager.getLastKnownLocation(provider);
                if (location == null) continue;
                if (best == null || location.getTime() > best.getTime()) {
                    best = location;
                }
            } catch (Exception ignored) {
            }
        }
        if (best == null) return null;
        long now = System.currentTimeMillis();
        long ageMs = best.getTime() > 0 ? now - best.getTime() : Long.MAX_VALUE;
        if (ageMs > 15 * 60 * 1000) {
            sendStatus("gps_last_known_stale", best.getProvider() + " age_ms=" + ageMs, null);
            return null;
        }
        return new GpsPoint(
            nowIso(),
            best.getLatitude(),
            best.getLongitude(),
            best.hasAccuracy() ? best.getAccuracy() : null,
            best.hasSpeed() ? best.getSpeed() : null,
            best.getProvider(),
            true,
            best.getTime() > 0 ? best.getTime() : null
        );
    }

    private void stopLocationUpdates() {
        if (locationManager == null) return;
        mainHandler.post(() -> {
            try {
                locationManager.removeUpdates(locationListener);
            } catch (Exception ignored) {
            }
            synchronized (activeLocationProviders) {
                activeLocationProviders.clear();
            }
            locationProviderStatus = "";
        });
    }

    private List<GpsPoint> copyGpsPoints() {
        synchronized (gpsPoints) {
            return new ArrayList<>(gpsPoints);
        }
    }

    private void cacheFailedUpload(File audioFile, Map<String, Object> metadata, List<GpsPoint> gps, String error) {
        try {
            File dir = new File(getExternalFilesDir(null), "failed_uploads");
            if (!dir.exists()) {
                //noinspection ResultOfMethodCallIgnored
                dir.mkdirs();
            }
            Map<String, Object> cachedMetadata = new LinkedHashMap<>(metadata);
            cachedMetadata.put("client_cached_at", nowIso());
            JSONObject payload = new JSONObject();
            payload.put("audio_path", audioFile.getAbsolutePath());
            payload.put("metadata", new JSONObject(cachedMetadata));
            payload.put("gps", MultipartUploader.gpsJson(gps));
            payload.put("error", error);
            File out = new File(dir, "failed_" + System.currentTimeMillis() + ".json");
            try (FileWriter writer = new FileWriter(out)) {
                writer.write(payload.toString(2));
            }
            sendCacheStatus("cache_saved", "cached failed upload");
        } catch (Exception ignored) {
        }
    }

    private void retryCachedUploads(String serverUrl, String uploadToken) {
        if (serverUrl == null || serverUrl.trim().isEmpty()) {
            sendCacheStatus("cache_retry_skipped", "server url missing");
            return;
        }
        String token = valueOr(uploadToken, "");
        new Thread(() -> {
            File[] files = failedUploadFiles();
            if (files.length == 0) {
                sendCacheStatus("cache_empty", "no cached uploads");
                return;
            }
            int success = 0;
            int failed = 0;
            for (File cacheFile : files) {
                try {
                    CachedUpload cached = readCachedUpload(cacheFile);
                    Map<String, Object> retryMetadata = new LinkedHashMap<>(cached.metadata);
                    retryMetadata.put("upload_attempt", intValue(retryMetadata.get("upload_attempt"), 1) + 1);
                    retryMetadata.put("cached_upload", true);
                    if (valueOr(String.valueOf(retryMetadata.get("client_cached_at")), "").isEmpty()) {
                        retryMetadata.put("client_cached_at", nowIso());
                    }
                    String response = MultipartUploader.upload(serverUrl.trim(), token, cached.audioFile, retryMetadata, cached.gpsPoints);
                    if (!cacheFile.delete()) {
                        sendStatus("cache_delete_failed", cacheFile.getAbsolutePath(), retryMetadata);
                    }
                    success += 1;
                    sendStatus("cache_uploaded", response, retryMetadata);
                } catch (Exception error) {
                    failed += 1;
                    sendStatus("cache_retry_failed", cacheFile.getName() + ": " + error, null);
                }
            }
            sendCacheStatus("cache_retry_done", "success=" + success + " failed=" + failed);
        }, "JoannaPhase5CacheRetry").start();
    }

    private File[] failedUploadFiles() {
        File dir = new File(getExternalFilesDir(null), "failed_uploads");
        File[] files = dir.listFiles((file, name) -> name.endsWith(".json"));
        return files == null ? new File[0] : files;
    }

    private CachedUpload readCachedUpload(File cacheFile) throws Exception {
        JSONObject payload = new JSONObject(readText(cacheFile));
        File audioFile = new File(payload.getString("audio_path"));
        if (!audioFile.exists()) {
            throw new IllegalStateException("cached audio file missing: " + audioFile.getAbsolutePath());
        }
        JSONObject metadataJson = payload.getJSONObject("metadata");
        Map<String, Object> metadata = new LinkedHashMap<>();
        JSONArray names = metadataJson.names();
        if (names != null) {
            for (int i = 0; i < names.length(); i += 1) {
                String name = names.getString(i);
                metadata.put(name, metadataJson.get(name));
            }
        }
        List<GpsPoint> gps = gpsPointsFromJson(payload.getJSONObject("gps"));
        return new CachedUpload(audioFile, metadata, gps);
    }

    private List<GpsPoint> gpsPointsFromJson(JSONObject root) throws Exception {
        List<GpsPoint> points = new ArrayList<>();
        JSONArray array = root.optJSONArray("points");
        if (array == null) return points;
        for (int i = 0; i < array.length(); i += 1) {
            JSONObject item = array.getJSONObject(i);
            points.add(new GpsPoint(
                item.optString("time", ""),
                item.getDouble("lat"),
                item.getDouble("lng"),
                item.isNull("accuracy_m") ? null : (float) item.getDouble("accuracy_m"),
                item.isNull("speed") ? null : (float) item.getDouble("speed"),
                item.optString("provider", ""),
                "last_known".equals(item.optString("source", "")),
                item.isNull("location_time_ms") ? null : item.getLong("location_time_ms")
            ));
        }
        return points;
    }

    private String readText(File file) throws Exception {
        StringBuilder builder = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new FileReader(file))) {
            String line;
            while ((line = reader.readLine()) != null) {
                builder.append(line).append('\n');
            }
        }
        return builder.toString();
    }

    private void sendCacheStatus(String status, String message) {
        Intent intent = new Intent(ACTION_STATUS).setPackage(getPackageName());
        intent.putExtra("status", status);
        intent.putExtra("message", message);
        intent.putExtra("cache_count", failedUploadFiles().length);
        sendBroadcast(intent);
        CaptureStatusBus.publish(intent);
    }

    private void maybeSendLevel(
        int segmentIndex,
        SegmentStats stats,
        long audioBytesWritten,
        AudioDeviceInfo actualDevice,
        String routeWarning
    ) {
        long now = System.currentTimeMillis();
        if (now - lastLevelStatusAt < 250) return;
        lastLevelStatusAt = now;
        Intent intent = new Intent(ACTION_STATUS).setPackage(getPackageName());
        intent.putExtra("status", "level");
        intent.putExtra("message", "segment " + segmentIndex);
        intent.putExtra("segment_index", segmentIndex);
        intent.putExtra("max_amplitude", stats.maxAmplitude);
        intent.putExtra("current_amplitude", stats.currentAmplitude);
        intent.putExtra("byte_peak", stats.bytePeak);
        intent.putExtra("current_byte_peak", stats.currentBytePeak);
        intent.putExtra("non_zero_samples", stats.nonZeroSamples);
        intent.putExtra("audio_bytes_written", audioBytesWritten);
        intent.putExtra("read_success_count", stats.readSuccessCount);
        intent.putExtra("zero_read_count", stats.zeroReadCount);
        intent.putExtra("read_error_count", stats.readErrorCount);
        intent.putExtra("route_warning", routeWarning == null ? "" : routeWarning);
        if (actualDevice != null) {
            intent.putExtra("actual_route", AudioNames.deviceName(actualDevice) + "/" + AudioNames.deviceTypeName(actualDevice.getType()) + "/id=" + actualDevice.getId());
        }
        sendBroadcast(intent);
        CaptureStatusBus.publish(intent);
    }

    private void sendStatus(String status, String message, Map<String, Object> metadata) {
        Intent intent = new Intent(ACTION_STATUS).setPackage(getPackageName());
        intent.putExtra("status", status);
        intent.putExtra("message", message);
        intent.putExtra("cache_count", failedUploadFiles().length);
        if (metadata != null) {
            intent.putExtra("metadata", new JSONObject(metadata).toString());
        }
        sendBroadcast(intent);
        CaptureStatusBus.publish(intent);
    }

    private void acquireWakeLock() {
        if (wakeLock != null && wakeLock.isHeld()) return;
        PowerManager manager = (PowerManager) getSystemService(Context.POWER_SERVICE);
        if (manager == null) return;
        wakeLock = manager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "JoannaPhase5:Capture");
        wakeLock.setReferenceCounted(false);
        wakeLock.acquire();
    }

    private void releaseWakeLock() {
        if (wakeLock == null) return;
        try {
            if (wakeLock.isHeld()) wakeLock.release();
        } catch (Exception ignored) {
        }
        wakeLock = null;
    }

    private void ensureNotificationChannel() {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationChannel channel = new NotificationChannel(
            CHANNEL_ID,
            "Joanna Phase5 Capture",
            NotificationManager.IMPORTANCE_LOW
        );
        NotificationManager manager = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager != null) manager.createNotificationChannel(channel);
    }

    private Notification notification(String text) {
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
            ? new Notification.Builder(this, CHANNEL_ID)
            : new Notification.Builder(this);
        return builder
            .setContentTitle("Joanna Phase5 Capture")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true)
            .build();
    }

    private static String nowIso() {
        return OffsetDateTime.now().format(DateTimeFormatter.ISO_OFFSET_DATE_TIME);
    }

    private static String valueOr(String value, String fallback) {
        return value == null || value.isEmpty() ? fallback : value;
    }

    private String joinActiveLocationProviders() {
        synchronized (activeLocationProviders) {
            return joinStrings(activeLocationProviders);
        }
    }

    private static int countGpsPoints(List<GpsPoint> points, boolean lastKnown) {
        int count = 0;
        for (GpsPoint point : points) {
            if (point.lastKnown == lastKnown) count += 1;
        }
        return count;
    }

    private static String joinStrings(List<String> values) {
        if (values == null || values.isEmpty()) return "";
        StringBuilder builder = new StringBuilder();
        for (String value : values) {
            if (value == null || value.isEmpty()) continue;
            if (builder.length() > 0) builder.append(",");
            builder.append(value);
        }
        return builder.toString();
    }

    private static int intValue(Object value, int fallback) {
        if (value instanceof Number) return ((Number) value).intValue();
        if (value == null) return fallback;
        try {
            return Integer.parseInt(String.valueOf(value));
        } catch (Exception ignored) {
            return fallback;
        }
    }

    private static String appendWarning(String left, String right) {
        if (right == null || right.isEmpty()) return left == null ? "" : left;
        if (left == null || left.isEmpty()) return right;
        if (left.contains(right)) return left;
        return left + "; " + right;
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        }
    }

    private static final class RouteState {
        boolean communicationDeviceApplied = false;
        boolean bluetoothScoReady = false;
        String warning = "";
    }

    private static final class CachedUpload {
        final File audioFile;
        final Map<String, Object> metadata;
        final List<GpsPoint> gpsPoints;

        CachedUpload(File audioFile, Map<String, Object> metadata, List<GpsPoint> gpsPoints) {
            this.audioFile = audioFile;
            this.metadata = metadata;
            this.gpsPoints = gpsPoints;
        }
    }
}
