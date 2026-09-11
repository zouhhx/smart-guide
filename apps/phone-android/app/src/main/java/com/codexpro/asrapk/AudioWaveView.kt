package com.codexpro.asrapk

import android.content.Context
import android.graphics.Canvas
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Shader
import android.util.AttributeSet
import android.view.View
import androidx.core.content.ContextCompat
import kotlin.math.max
import kotlin.math.sin

class AudioWaveView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : View(context, attrs) {

    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        strokeCap = Paint.Cap.ROUND
        strokeWidth = dp(5f)
    }

    private val ghostPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ContextCompat.getColor(context, R.color.wave_blue_dim)
        strokeCap = Paint.Cap.ROUND
        strokeWidth = dp(5f)
    }

    private val levels = FloatArray(BAR_COUNT) { MIN_LEVEL }
    private var targetLevel = 0f
    private var visualLevel = 0f
    private var phase = 0f
    private var lastFrameAt = 0L
    private var isAnimating = false
    private var onIdle: (() -> Unit)? = null

    private val frameRunnable = object : Runnable {
        override fun run() {
            if (!isAnimating) {
                return
            }
            val now = System.nanoTime()
            val elapsedSeconds = if (lastFrameAt == 0L) {
                1f / 60f
            } else {
                ((now - lastFrameAt) / 1_000_000_000f).coerceIn(0f, 0.05f)
            }
            lastFrameAt = now
            phase = (phase + elapsedSeconds * 8.5f) % TWO_PI
            val easing = if (targetLevel > visualLevel) ATTACK_EASING else RELEASE_EASING
            visualLevel += (targetLevel - visualLevel) * easing
            advanceLevels()
            invalidate()

            if (targetLevel > 0.001f || visualLevel > 0.001f) {
                postOnAnimation(this)
            } else {
                isAnimating = false
                lastFrameAt = 0L
                levels.fill(MIN_LEVEL)
                invalidate()
                onIdle?.invoke()
                onIdle = null
            }
        }
    }

    fun setLevel(level: Float) {
        onIdle = null
        targetLevel = level.coerceIn(0f, 1f)
        ensureAnimating()
    }

    fun finish(onFinished: (() -> Unit)? = null) {
        targetLevel = 0f
        onIdle = onFinished
        ensureAnimating()
    }

    fun cancelFinish() {
        onIdle = null
    }

    private fun advanceLevels() {
        val clamped = (visualLevel * DISPLAY_GAIN).coerceIn(MIN_LEVEL, 1f)
        for (i in 0 until BAR_COUNT - 1) {
            levels[i] = levels[i + 1]
        }
        val wave = sin(phase * 1.2f).let { 0.82f + it * 0.18f }
        levels[BAR_COUNT - 1] = (clamped * wave).coerceIn(MIN_LEVEL, 1f)
    }

    fun reset() {
        onIdle = null
        targetLevel = 0f
        visualLevel = 0f
        phase = 0f
        lastFrameAt = 0L
        isAnimating = false
        removeCallbacks(frameRunnable)
        levels.fill(MIN_LEVEL)
        invalidate()
    }

    private fun ensureAnimating() {
        if (!isAnimating) {
            isAnimating = true
            lastFrameAt = 0L
            postOnAnimation(frameRunnable)
        }
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val centerY = height / 2f
        val spacing = width / (BAR_COUNT + 1f)
        val maxBarHeight = height * 0.82f
        barPaint.shader = LinearGradient(
            0f,
            0f,
            0f,
            height.toFloat(),
            intArrayOf(
                ContextCompat.getColor(context, R.color.wave_blue_top),
                ContextCompat.getColor(context, R.color.wave_blue),
                ContextCompat.getColor(context, R.color.wave_blue_deep)
            ),
            floatArrayOf(0f, 0.45f, 1f),
            Shader.TileMode.CLAMP
        )
        for (i in 0 until BAR_COUNT) {
            val x = spacing * (i + 1)
            val distance = kotlin.math.abs(i - BAR_COUNT / 2f) / (BAR_COUNT / 2f)
            val focus = 1f - distance * 0.45f
            val factor = 0.52f + focus * 0.82f
            val breathing = 0.88f + sin(phase + i * 0.72f) * 0.14f
            val barHeight = max(maxBarHeight * levels[i] * factor * breathing, dp(14f))
            val top = centerY - barHeight / 2f
            val bottom = centerY + barHeight / 2f
            canvas.drawLine(x, top, x, bottom, ghostPaint)
            canvas.drawLine(x, top, x, bottom, barPaint)
        }
    }

    private fun dp(value: Float): Float = value * resources.displayMetrics.density

    companion object {
        private const val BAR_COUNT = 18
        private const val MIN_LEVEL = 0.08f
        private const val TWO_PI = (Math.PI * 2).toFloat()
        private const val ATTACK_EASING = 0.34f
        private const val RELEASE_EASING = 0.11f
        private const val DISPLAY_GAIN = 1.45f
    }
}
