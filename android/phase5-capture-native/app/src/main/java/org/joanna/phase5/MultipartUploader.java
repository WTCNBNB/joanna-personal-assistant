package org.joanna.phase5;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;

final class MultipartUploader {
    private MultipartUploader() {
    }

    static String upload(
        String targetUrl,
        String uploadToken,
        File audioFile,
        Map<String, Object> metadata,
        List<GpsPoint> gpsPoints
    ) throws IOException, JSONException {
        String boundary = "joanna-native-" + System.currentTimeMillis();
        HttpURLConnection connection = (HttpURLConnection) new URL(targetUrl).openConnection();
        connection.setConnectTimeout(15000);
        connection.setReadTimeout(60000);
        connection.setDoOutput(true);
        connection.setRequestMethod("POST");
        connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
        if (uploadToken != null && !uploadToken.trim().isEmpty()) {
            connection.setRequestProperty("X-Joanna-Phase5-Token", uploadToken.trim());
        }

        try (DataOutputStream out = new DataOutputStream(connection.getOutputStream())) {
            writeTextPart(out, boundary, "metadata", new JSONObject(metadata).toString());
            writeTextPart(out, boundary, "gps", gpsJson(gpsPoints).toString());
            writeFilePart(out, boundary, "audio", audioFile.getName(), "audio/wav", audioFile);
            out.writeBytes("--" + boundary + "--\r\n");
            out.flush();
        }

        int status = connection.getResponseCode();
        String body;
        try (BufferedInputStream in = new BufferedInputStream(
            status >= 200 && status < 300 ? connection.getInputStream() : connection.getErrorStream()
        )) {
            body = readAll(in);
        }
        if (status < 200 || status >= 300) {
            throw new IOException("HTTP " + status + ": " + body);
        }
        return body;
    }

    static JSONObject gpsJson(List<GpsPoint> points) throws JSONException {
        JSONArray array = new JSONArray();
        for (GpsPoint point : points) {
            JSONObject item = new JSONObject();
            item.put("time", point.time);
            item.put("lat", point.lat);
            item.put("lng", point.lng);
            item.put("accuracy_m", point.accuracy == null ? JSONObject.NULL : point.accuracy);
            item.put("speed", point.speed == null ? JSONObject.NULL : point.speed);
            item.put("provider", point.provider);
            item.put("source", point.lastKnown ? "last_known" : "live");
            item.put("location_time_ms", point.locationTimeMs == null ? JSONObject.NULL : point.locationTimeMs);
            array.put(item);
        }
        JSONObject root = new JSONObject();
        root.put("points", array);
        return root;
    }

    private static void writeTextPart(
        DataOutputStream out,
        String boundary,
        String name,
        String value
    ) throws IOException {
        out.writeBytes("--" + boundary + "\r\n");
        out.writeBytes("Content-Disposition: form-data; name=\"" + name + "\"\r\n");
        out.writeBytes("Content-Type: application/json; charset=utf-8\r\n\r\n");
        out.write(value.getBytes(StandardCharsets.UTF_8));
        out.writeBytes("\r\n");
    }

    private static void writeFilePart(
        DataOutputStream out,
        String boundary,
        String name,
        String filename,
        String contentType,
        File file
    ) throws IOException {
        out.writeBytes("--" + boundary + "\r\n");
        out.writeBytes("Content-Disposition: form-data; name=\"" + name + "\"; filename=\"" + filename + "\"\r\n");
        out.writeBytes("Content-Type: " + contentType + "\r\n\r\n");
        byte[] buffer = new byte[64 * 1024];
        try (FileInputStream input = new FileInputStream(file)) {
            int read;
            while ((read = input.read(buffer)) != -1) {
                out.write(buffer, 0, read);
            }
        }
        out.writeBytes("\r\n");
    }

    private static String readAll(BufferedInputStream input) throws IOException {
        if (input == null) return "";
        byte[] buffer = new byte[8192];
        StringBuilder builder = new StringBuilder();
        int read;
        while ((read = input.read(buffer)) != -1) {
            builder.append(new String(buffer, 0, read, StandardCharsets.UTF_8));
        }
        return builder.toString();
    }
}
