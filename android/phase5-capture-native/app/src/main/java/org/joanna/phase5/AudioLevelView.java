package org.joanna.phase5;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.view.View;

import java.util.Arrays;
import java.util.Locale;

final class AudioLevelView extends View {
    private static final int HISTORY_SIZE = 96;

    private final Paint backgroundPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint levelPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint peakPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint wavePaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final float[] history = new float[HISTORY_SIZE];
    private int historyIndex = 0;
    private float level = 0f;
    private float peak = 0f;
    private int amplitude = 0;

    AudioLevelView(Context context) {
        super(context);
        backgroundPaint.setColor(Color.rgb(28, 32, 36));
        levelPaint.setColor(Color.rgb(31, 173, 116));
        peakPaint.setColor(Color.rgb(237, 137, 54));
        wavePaint.setColor(Color.rgb(94, 203, 245));
        wavePaint.setStrokeWidth(3f);
        textPaint.setColor(Color.WHITE);
        textPaint.setTextSize(32f);
        Arrays.fill(history, 0f);
    }

    void updateLevel(int maxAmplitude) {
        amplitude = Math.max(0, maxAmplitude);
        level = Math.min(1f, amplitude / 32768f);
        peak = Math.max(level, peak * 0.96f);
        history[historyIndex] = level;
        historyIndex = (historyIndex + 1) % HISTORY_SIZE;
        invalidate();
    }

    @Override
    protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
        int width = MeasureSpec.getSize(widthMeasureSpec);
        int height = Math.max(dp(96), MeasureSpec.getSize(heightMeasureSpec));
        setMeasuredDimension(width, height);
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float width = getWidth();
        float height = getHeight();
        float pad = dp(10);
        canvas.drawRoundRect(0, 0, width, height, dp(8), dp(8), backgroundPaint);

        float barTop = pad;
        float barHeight = dp(22);
        float usable = width - pad * 2;
        canvas.drawRoundRect(pad, barTop, pad + usable * level, barTop + barHeight, dp(4), dp(4), levelPaint);
        float peakX = pad + usable * peak;
        canvas.drawRect(Math.max(pad, peakX - 2), barTop, Math.min(width - pad, peakX + 2), barTop + barHeight, peakPaint);

        float mid = height - dp(32);
        float waveTop = barTop + barHeight + dp(18);
        float waveHeight = Math.max(dp(26), mid - waveTop);
        float step = usable / Math.max(1, HISTORY_SIZE - 1);
        for (int i = 1; i < HISTORY_SIZE; i += 1) {
            int prevIndex = (historyIndex + i - 1) % HISTORY_SIZE;
            int currentIndex = (historyIndex + i) % HISTORY_SIZE;
            float x1 = pad + (i - 1) * step;
            float x2 = pad + i * step;
            float y1 = waveTop + waveHeight * (1f - history[prevIndex]);
            float y2 = waveTop + waveHeight * (1f - history[currentIndex]);
            canvas.drawLine(x1, y1, x2, y2, wavePaint);
        }

        String label = String.format(Locale.US, "amp=%d  level=%d%%", amplitude, Math.round(level * 100f));
        canvas.drawText(label, pad, height - dp(10), textPaint);
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }
}
