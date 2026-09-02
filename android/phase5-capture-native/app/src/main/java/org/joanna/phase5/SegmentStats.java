package org.joanna.phase5;

final class SegmentStats {
    int readSuccessCount = 0;
    int zeroReadCount = 0;
    int readErrorCount = 0;
    int bytePeak = 0;
    int currentBytePeak = 0;
    int nonZeroSamples = 0;
    int maxAmplitude = 0;
    int currentAmplitude = 0;

    void update(byte[] pcm, int count) {
        int limit = count - (count % 2);
        int stride = Math.max(2, (limit / 256) * 2);
        int chunkAmplitude = 0;
        int chunkBytePeak = 0;
        for (int i = 0; i < limit; i += stride) {
            int low = pcm[i] & 0xff;
            int high = pcm[i + 1];
            short sample = (short) (low | (high << 8));
            int abs = Math.abs((int) sample);
            if (abs > chunkAmplitude) chunkAmplitude = abs;
            if (abs > maxAmplitude) maxAmplitude = abs;
            if (sample != 0) nonZeroSamples += 1;
            int b0 = Math.abs((int) pcm[i]);
            int b1 = Math.abs((int) pcm[i + 1]);
            if (b0 > chunkBytePeak) chunkBytePeak = b0;
            if (b1 > chunkBytePeak) chunkBytePeak = b1;
            if (b0 > bytePeak) bytePeak = b0;
            if (b1 > bytePeak) bytePeak = b1;
        }
        currentAmplitude = chunkAmplitude;
        currentBytePeak = chunkBytePeak;
    }
}
