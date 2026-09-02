package org.joanna.phase5;

import android.Manifest;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.media.AudioDeviceInfo;
import android.media.AudioManager;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;

import java.io.File;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

public class MainActivity extends Activity {
    private static final String LAN_SERVER_URL = "http://YOUR_MAC_LAN_IP:18787/api/phase5/segments";
    private static final String PUBLIC_SERVER_URL = "https://YOUR_PUBLIC_DOMAIN/api/phase5/segments";
    private static final String DEFAULT_SERVER_URL = LAN_SERVER_URL;
    private static final String PREFS = "joanna_phase5_capture";
    private static final String KEY_SERVER_URL = "server_url";
    private static final String KEY_UPLOAD_TOKEN = "upload_token";
    private static final String KEY_DEVICE_ID = "device_id";
    private static final String KEY_DEVICE_NAME = "device_name";
    private static final String KEY_DEVICE_TYPE = "device_type";
    private static final String KEY_SOURCE_INDEX = "source_index";

    private EditText serverUrlInput;
    private EditText uploadTokenInput;
    private Spinner deviceSpinner;
    private Spinner sourceSpinner;
    private TextView statusText;
    private TextView routeText;
    private TextView levelText;
    private AudioLevelView audioLevelView;
    private TextView uploadText;
    private TextView cacheText;
    private TextView logText;
    private Button startButton;
    private final List<AudioDeviceInfo> inputDevices = new ArrayList<>();
    private SharedPreferences prefs;

    private final String[] sourceLabels = {
        "MIC",
        "DEFAULT",
        "UNPROCESSED",
        "CAMCORDER",
        "VOICE_RECOGNITION",
        "VOICE_COMMUNICATION"
    };
    private final String[] sourceValues = {
        "mic",
        "default",
        "unprocessed",
        "camcorder",
        "voice_recognition",
        "voice_communication"
    };

    private final CaptureStatusBus.Listener statusListener = intent -> runOnUiThread(() -> handleStatus(intent));
    private final BroadcastReceiver statusReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            handleStatus(intent);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        buildUi();
        requestRuntimePermissions();
        refreshDevices();
    }

    @Override
    protected void onResume() {
        super.onResume();
        IntentFilter filter = new IntentFilter(CaptureService.ACTION_STATUS);
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(statusReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(statusReceiver, filter);
        }
        CaptureStatusBus.register(statusListener);
        setCacheCount(countCachedUploads());
    }

    @Override
    protected void onPause() {
        try {
            unregisterReceiver(statusReceiver);
        } catch (Exception ignored) {
        }
        CaptureStatusBus.unregister(statusListener);
        super.onPause();
    }

    private void handleStatus(Intent intent) {
        if (!CaptureService.ACTION_STATUS.equals(intent.getAction())) return;
        String status = intent.getStringExtra("status");
        String message = intent.getStringExtra("message");
        statusText.setText("状态：" + valueOr(status, "unknown") + " / " + valueOr(message, ""));
        if (intent.hasExtra("cache_count")) {
            setCacheCount(intent.getIntExtra("cache_count", 0));
        }
        if ("level".equals(status)) {
            int segment = intent.getIntExtra("segment_index", 0);
            long bytes = intent.getLongExtra("audio_bytes_written", 0);
            int ok = intent.getIntExtra("read_success_count", 0);
            int zero = intent.getIntExtra("zero_read_count", 0);
            int err = intent.getIntExtra("read_error_count", 0);
            int peak = intent.getIntExtra("byte_peak", 0);
            int currentPeak = intent.getIntExtra("current_byte_peak", peak);
            int nonZero = intent.getIntExtra("non_zero_samples", 0);
            int maxAmp = intent.getIntExtra("max_amplitude", 0);
            int amp = intent.getIntExtra("current_amplitude", maxAmp);
            audioLevelView.updateLevel(amp);
            levelText.setText(String.format(
                Locale.US,
                "片段=%d bytes=%d ok=%d zero=%d err=%d amp=%d max=%d bytePeak=%d/%d nonZero=%d",
                segment,
                bytes,
                ok,
                zero,
                err,
                amp,
                maxAmp,
                currentPeak,
                peak,
                nonZero
            ));
            routeText.setText("实际 route：" + valueOr(intent.getStringExtra("actual_route"), "unknown")
                + "\nwarning：" + valueOr(intent.getStringExtra("route_warning"), ""));
            return;
        }
        if (isUploadStatus(status)) {
            uploadText.setText("最近上传：" + valueOr(status, "unknown") + " / " + valueOr(message, ""));
        }
        appendLog(valueOr(status, "unknown") + " " + valueOr(message, ""));
        String metadata = intent.getStringExtra("metadata");
        if (metadata != null && !metadata.isEmpty()) {
            appendLog(metadata);
        }
    }

    private void buildUi() {
        int pad = dp(14);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(pad, pad, pad, pad);

        TextView title = new TextView(this);
        title.setText("乔纳五期原生采集器");
        title.setTextSize(22);
        title.setPadding(0, 0, 0, dp(8));
        root.addView(title);

        serverUrlInput = new EditText(this);
        serverUrlInput.setSingleLine(true);
        serverUrlInput.setText(prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL));
        serverUrlInput.setHint("http://YOUR_MAC_LAN_IP:18787/api/phase5/segments");
        root.addView(label("接收端 URL"));
        root.addView(serverUrlInput);
        LinearLayout urlButtons = new LinearLayout(this);
        urlButtons.setOrientation(LinearLayout.HORIZONTAL);
        Button lanUrlButton = new Button(this);
        lanUrlButton.setText("局域网");
        lanUrlButton.setOnClickListener(v -> setServerUrl(LAN_SERVER_URL));
        Button publicUrlButton = new Button(this);
        publicUrlButton.setText("公网转发");
        publicUrlButton.setOnClickListener(v -> setServerUrl(PUBLIC_SERVER_URL));
        urlButtons.addView(lanUrlButton, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        urlButtons.addView(publicUrlButton, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        root.addView(urlButtons);

        uploadTokenInput = new EditText(this);
        uploadTokenInput.setSingleLine(true);
        uploadTokenInput.setText(prefs.getString(KEY_UPLOAD_TOKEN, ""));
        uploadTokenInput.setHint("公网转发时填写，局域网可留空");
        root.addView(label("上传 token"));
        root.addView(uploadTokenInput);

        deviceSpinner = new Spinner(this);
        sourceSpinner = new Spinner(this);
        sourceSpinner.setAdapter(new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, sourceLabels));
        sourceSpinner.setSelection(Math.min(sourceLabels.length - 1, Math.max(0, prefs.getInt(KEY_SOURCE_INDEX, 0))));
        root.addView(label("音频输入设备"));
        root.addView(deviceSpinner);
        root.addView(label("录音音源"));
        root.addView(sourceSpinner);

        LinearLayout buttons = new LinearLayout(this);
        buttons.setOrientation(LinearLayout.VERTICAL);
        LinearLayout firstButtonRow = new LinearLayout(this);
        firstButtonRow.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout secondButtonRow = new LinearLayout(this);
        secondButtonRow.setOrientation(LinearLayout.HORIZONTAL);
        Button permissionButton = new Button(this);
        permissionButton.setText("授权");
        permissionButton.setOnClickListener(v -> requestRuntimePermissions());
        Button refreshButton = new Button(this);
        refreshButton.setText("刷新设备");
        refreshButton.setOnClickListener(v -> refreshDevices());
        Button retryButton = new Button(this);
        retryButton.setText("重传缓存");
        retryButton.setOnClickListener(v -> retryCachedUploads());
        startButton = new Button(this);
        startButton.setText("开始采集");
        startButton.setOnClickListener(v -> startCapture());
        Button stopButton = new Button(this);
        stopButton.setText("停止");
        stopButton.setOnClickListener(v -> stopCapture());
        firstButtonRow.addView(permissionButton, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        firstButtonRow.addView(refreshButton, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        firstButtonRow.addView(retryButton, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        secondButtonRow.addView(startButton, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        secondButtonRow.addView(stopButton, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        buttons.addView(firstButtonRow);
        buttons.addView(secondButtonRow);
        root.addView(buttons);

        statusText = label("状态：idle");
        routeText = label("实际 route：unknown");
        audioLevelView = new AudioLevelView(this);
        levelText = label("片段=0 bytes=0 ok=0 zero=0 err=0 amp=0 bytePeak=0 nonZero=0");
        uploadText = label("最近上传：none");
        cacheText = label("缓存待重传：0");
        root.addView(statusText);
        root.addView(routeText);
        root.addView(audioLevelView, new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            dp(112)
        ));
        root.addView(levelText);
        root.addView(uploadText);
        root.addView(cacheText);

        ScrollView scroll = new ScrollView(this);
        logText = new TextView(this);
        logText.setTextSize(12);
        logText.setText("最近事件\n");
        scroll.addView(logText);
        root.addView(scroll, new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            0,
            1
        ));

        setContentView(root);
    }

    private void requestRuntimePermissions() {
        List<String> permissions = new ArrayList<>();
        addMissing(permissions, Manifest.permission.RECORD_AUDIO);
        addMissing(permissions, Manifest.permission.ACCESS_FINE_LOCATION);
        addMissing(permissions, Manifest.permission.ACCESS_COARSE_LOCATION);
        addMissing(permissions, Manifest.permission.MODIFY_AUDIO_SETTINGS);
        if (Build.VERSION.SDK_INT >= 31) {
            addMissing(permissions, Manifest.permission.BLUETOOTH_CONNECT);
        }
        if (Build.VERSION.SDK_INT >= 33) {
            addMissing(permissions, Manifest.permission.POST_NOTIFICATIONS);
        }
        if (!permissions.isEmpty()) {
            requestPermissions(permissions.toArray(new String[0]), 5505);
        }
    }

    private void addMissing(List<String> permissions, String permission) {
        if (checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) {
            permissions.add(permission);
        }
    }

    private void refreshDevices() {
        inputDevices.clear();
        List<String> labels = new ArrayList<>();
        AudioManager audioManager = (AudioManager) getSystemService(Context.AUDIO_SERVICE);
        if (audioManager != null) {
            for (AudioDeviceInfo device : audioManager.getDevices(AudioManager.GET_DEVICES_INPUTS)) {
                inputDevices.add(device);
                labels.add(AudioNames.deviceName(device) + " / " + AudioNames.deviceTypeName(device.getType()) + " / id=" + device.getId());
            }
        }
        if (labels.isEmpty()) {
            labels.add("未发现输入设备");
        }
        ArrayAdapter<String> adapter = new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, labels);
        deviceSpinner.setAdapter(adapter);
        int savedIndex = savedDeviceIndex();
        int bluetoothIndex = firstBluetoothIndex();
        if (savedIndex >= 0) {
            deviceSpinner.setSelection(savedIndex);
        } else if (bluetoothIndex >= 0) {
            deviceSpinner.setSelection(bluetoothIndex);
        }
        appendLog("发现输入设备：" + labels.size());
    }

    private int savedDeviceIndex() {
        int savedId = prefs.getInt(KEY_DEVICE_ID, -1);
        int savedType = prefs.getInt(KEY_DEVICE_TYPE, AudioDeviceInfo.TYPE_UNKNOWN);
        String savedName = prefs.getString(KEY_DEVICE_NAME, "");
        for (int i = 0; i < inputDevices.size(); i += 1) {
            AudioDeviceInfo device = inputDevices.get(i);
            if (savedId >= 0 && device.getId() == savedId) return i;
        }
        for (int i = 0; i < inputDevices.size(); i += 1) {
            AudioDeviceInfo device = inputDevices.get(i);
            if (device.getType() == savedType && AudioNames.deviceName(device).equals(savedName)) return i;
        }
        return -1;
    }

    private int firstBluetoothIndex() {
        for (int i = 0; i < inputDevices.size(); i += 1) {
            if (AudioNames.isBluetoothInput(inputDevices.get(i).getType())) return i;
        }
        return -1;
    }

    private void startCapture() {
        requestRuntimePermissions();
        int selected = deviceSpinner.getSelectedItemPosition();
        if (selected < 0 || selected >= inputDevices.size()) {
            appendLog("必须先选择一个音频输入设备");
            return;
        }
        AudioDeviceInfo device = inputDevices.get(selected);
        int sourceIndex = Math.max(0, sourceSpinner.getSelectedItemPosition());
        saveSettings(device, sourceIndex);
        Intent intent = new Intent(this, CaptureService.class);
        intent.setAction(CaptureService.ACTION_START);
        intent.putExtra(CaptureService.EXTRA_SERVER_URL, serverUrlInput.getText().toString().trim());
        intent.putExtra(CaptureService.EXTRA_UPLOAD_TOKEN, uploadTokenInput.getText().toString().trim());
        intent.putExtra(CaptureService.EXTRA_DEVICE_ID, device.getId());
        intent.putExtra(CaptureService.EXTRA_DEVICE_NAME, AudioNames.deviceName(device));
        intent.putExtra(CaptureService.EXTRA_DEVICE_TYPE, device.getType());
        intent.putExtra(CaptureService.EXTRA_AUDIO_SOURCE_MODE, sourceValues[sourceIndex]);
        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(intent);
        } else {
            startService(intent);
        }
        startButton.setEnabled(false);
        appendLog("开始采集：" + AudioNames.deviceName(device) + " / " + AudioNames.deviceTypeName(device.getType()));
    }

    private void retryCachedUploads() {
        prefs.edit()
            .putString(KEY_SERVER_URL, serverUrlInput.getText().toString().trim())
            .putString(KEY_UPLOAD_TOKEN, uploadTokenInput.getText().toString().trim())
            .apply();
        Intent intent = new Intent(this, CaptureService.class);
        intent.setAction(CaptureService.ACTION_RETRY_CACHE);
        intent.putExtra(CaptureService.EXTRA_SERVER_URL, serverUrlInput.getText().toString().trim());
        intent.putExtra(CaptureService.EXTRA_UPLOAD_TOKEN, uploadTokenInput.getText().toString().trim());
        startService(intent);
        appendLog("重传缓存请求已发送");
    }

    private void stopCapture() {
        Intent intent = new Intent(this, CaptureService.class);
        intent.setAction(CaptureService.ACTION_STOP);
        startService(intent);
        startButton.setEnabled(true);
        appendLog("停止请求已发送");
    }

    private void setServerUrl(String value) {
        serverUrlInput.setText(value);
        serverUrlInput.setSelection(serverUrlInput.getText().length());
        prefs.edit().putString(KEY_SERVER_URL, value).apply();
    }

    private TextView label(String value) {
        TextView text = new TextView(this);
        text.setText(value);
        text.setTextSize(14);
        text.setPadding(0, dp(8), 0, dp(4));
        return text;
    }

    private void appendLog(String value) {
        logText.append("\n" + value);
    }

    private void saveSettings(AudioDeviceInfo device, int sourceIndex) {
        prefs.edit()
            .putString(KEY_SERVER_URL, serverUrlInput.getText().toString().trim())
            .putString(KEY_UPLOAD_TOKEN, uploadTokenInput.getText().toString().trim())
            .putInt(KEY_DEVICE_ID, device.getId())
            .putString(KEY_DEVICE_NAME, AudioNames.deviceName(device))
            .putInt(KEY_DEVICE_TYPE, device.getType())
            .putInt(KEY_SOURCE_INDEX, sourceIndex)
            .apply();
    }

    private boolean isUploadStatus(String status) {
        return "uploading".equals(status)
            || "uploaded".equals(status)
            || "upload_failed".equals(status)
            || "cache_uploaded".equals(status)
            || "cache_retry_failed".equals(status)
            || "cache_retry_done".equals(status)
            || "cache_saved".equals(status);
    }

    private void setCacheCount(int count) {
        cacheText.setText("缓存待重传：" + count);
    }

    private int countCachedUploads() {
        File dir = new File(getExternalFilesDir(null), "failed_uploads");
        File[] files = dir.listFiles((file, name) -> name.endsWith(".json"));
        return files == null ? 0 : files.length;
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    private static String valueOr(String value, String fallback) {
        return value == null || value.isEmpty() ? fallback : value;
    }
}
