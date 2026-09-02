package org.joanna.phase5;

import android.media.AudioDeviceInfo;
import android.media.MediaRecorder;

final class AudioNames {
    private AudioNames() {
    }

    static String deviceTypeName(int type) {
        switch (type) {
            case AudioDeviceInfo.TYPE_BUILTIN_EARPIECE:
                return "builtin_earpiece";
            case AudioDeviceInfo.TYPE_BUILTIN_SPEAKER:
                return "builtin_speaker";
            case AudioDeviceInfo.TYPE_WIRED_HEADSET:
                return "wired_headset";
            case AudioDeviceInfo.TYPE_WIRED_HEADPHONES:
                return "wired_headphones";
            case AudioDeviceInfo.TYPE_LINE_ANALOG:
                return "line_analog";
            case AudioDeviceInfo.TYPE_LINE_DIGITAL:
                return "line_digital";
            case AudioDeviceInfo.TYPE_BLUETOOTH_SCO:
                return "bluetooth_sco";
            case AudioDeviceInfo.TYPE_BLUETOOTH_A2DP:
                return "bluetooth_a2dp";
            case AudioDeviceInfo.TYPE_HDMI:
                return "hdmi";
            case AudioDeviceInfo.TYPE_BUILTIN_MIC:
                return "builtin_mic";
            case AudioDeviceInfo.TYPE_TELEPHONY:
                return "telephony";
            case AudioDeviceInfo.TYPE_USB_DEVICE:
                return "usb_device";
            case AudioDeviceInfo.TYPE_USB_ACCESSORY:
                return "usb_accessory";
            case AudioDeviceInfo.TYPE_BLE_HEADSET:
                return "ble_headset";
            default:
                return "type_" + type;
        }
    }

    static boolean isBluetoothInput(int type) {
        return type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO
            || type == AudioDeviceInfo.TYPE_BLE_HEADSET;
    }

    static String deviceName(AudioDeviceInfo device) {
        CharSequence product = device.getProductName();
        if (product != null && product.length() > 0) {
            return product.toString();
        }
        String address = device.getAddress();
        if (address != null && !address.isEmpty()) {
            return address;
        }
        return "input-" + device.getId();
    }

    static int audioSourceValue(String mode) {
        if ("default".equals(mode)) return MediaRecorder.AudioSource.DEFAULT;
        if ("unprocessed".equals(mode)) return MediaRecorder.AudioSource.UNPROCESSED;
        if ("camcorder".equals(mode)) return MediaRecorder.AudioSource.CAMCORDER;
        if ("voice_recognition".equals(mode)) return MediaRecorder.AudioSource.VOICE_RECOGNITION;
        if ("voice_communication".equals(mode)) return MediaRecorder.AudioSource.VOICE_COMMUNICATION;
        return MediaRecorder.AudioSource.MIC;
    }

    static int sampleRateForType(int type) {
        return isBluetoothInput(type) ? 16000 : 48000;
    }
}
