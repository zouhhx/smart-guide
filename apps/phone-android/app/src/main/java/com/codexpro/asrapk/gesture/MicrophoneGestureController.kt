package com.codexpro.asrapk.gesture

import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import kotlin.math.max

class MicrophoneGestureController(
    view: View,
    private val enabled: () -> Boolean,
    private val onStart: () -> Unit,
    private val onCancelPending: () -> Unit,
    private val onCancelCleared: () -> Unit,
    private val onSend: () -> Unit,
    private val onCancel: () -> Unit
) : View.OnTouchListener {
    private val host = view
    private val cancelThreshold = max(dp(80f), ViewConfiguration.get(view.context).scaledTouchSlop * 4f)
    private val clearThreshold = cancelThreshold * 0.6f
    private var downY = 0f
    private var active = false
    private var started = false
    private var cancelPending = false

    override fun onTouch(view: View, event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                if (!enabled()) return false
                active = true
                started = false
                cancelPending = false
                downY = event.y
                started = true
                onStart()
                return true
            }
            MotionEvent.ACTION_MOVE -> {
                if (!active) return false
                if (event.pointerCount > 1) {
                    cancelGesture()
                    return true
                }
                if (started) {
                    val upwardDistance = downY - event.y
                    if (!cancelPending && upwardDistance >= cancelThreshold) {
                        cancelPending = true
                        onCancelPending()
                    } else if (cancelPending && upwardDistance <= clearThreshold) {
                        cancelPending = false
                        onCancelCleared()
                    }
                }
                return true
            }
            MotionEvent.ACTION_POINTER_DOWN -> {
                cancelGesture()
                return true
            }
            MotionEvent.ACTION_UP -> {
                if (started) {
                    if (cancelPending) onCancel() else onSend()
                }
                view.performClick()
                reset()
                return true
            }
            MotionEvent.ACTION_CANCEL -> {
                cancelGesture()
                return true
            }
            else -> return active
        }
    }

    private fun cancelGesture() {
        if (active && started) onCancel()
        reset()
    }

    private fun reset() {
        active = false
        started = false
        cancelPending = false
    }

    private fun dp(value: Float): Float = value * host.resources.displayMetrics.density
}
