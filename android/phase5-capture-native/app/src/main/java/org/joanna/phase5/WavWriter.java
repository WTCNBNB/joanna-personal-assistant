package org.joanna.phase5;

import java.io.Closeable;
import java.io.File;
import java.io.IOException;
import java.io.RandomAccessFile;

final class WavWriter implements Closeable {
    private final RandomAccessFile file;
    private final int sampleRate;
    private final int channels;
    private final int bitsPerSample;
    private long dataBytes = 0;

    WavWriter(File path, int sampleRate, int channels, int bitsPerSample) throws IOException {
        this.file = new RandomAccessFile(path, "rw");
        this.sampleRate = sampleRate;
        this.channels = channels;
        this.bitsPerSample = bitsPerSample;
        file.setLength(0);
        writeHeader(0);
    }

    void write(byte[] buffer, int count) throws IOException {
        file.write(buffer, 0, count);
        dataBytes += count;
    }

    long dataBytes() {
        return dataBytes;
    }

    @Override
    public void close() throws IOException {
        writeHeader(dataBytes);
        file.close();
    }

    private void writeHeader(long bytes) throws IOException {
        int byteRate = sampleRate * channels * bitsPerSample / 8;
        int blockAlign = channels * bitsPerSample / 8;
        file.seek(0);
        writeAscii("RIFF");
        writeLE32(36 + bytes);
        writeAscii("WAVE");
        writeAscii("fmt ");
        writeLE32(16);
        writeLE16(1);
        writeLE16(channels);
        writeLE32(sampleRate);
        writeLE32(byteRate);
        writeLE16(blockAlign);
        writeLE16(bitsPerSample);
        writeAscii("data");
        writeLE32(bytes);
        file.seek(44 + bytes);
    }

    private void writeAscii(String value) throws IOException {
        for (int i = 0; i < value.length(); i += 1) {
            file.write(value.charAt(i) & 0xff);
        }
    }

    private void writeLE16(int value) throws IOException {
        file.write(value & 0xff);
        file.write((value >> 8) & 0xff);
    }

    private void writeLE32(long value) throws IOException {
        file.write((int) value & 0xff);
        file.write((int) (value >> 8) & 0xff);
        file.write((int) (value >> 16) & 0xff);
        file.write((int) (value >> 24) & 0xff);
    }
}
