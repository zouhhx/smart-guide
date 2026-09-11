@file:Suppress("DEPRECATION")

package com.codexpro.asrapk.gesture

import android.graphics.Rect
import android.view.GestureDetector
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import androidx.core.view.GestureDetectorCompat
import kotlin.math.abs
import kotlin.math.max

class PageGestureController(
    view: View,
    private val excludedViews: List<View> = emptyList(),
    private val enabled: () -> Boolean,
    private val onPrevious: () -> Unit,
    private val onNext: () -> Unit,
    private val onExplainCurrent: () -> Unit
) : View.OnTouchListener {
    private var ignoreSequence = false
    private val minimumDistance = max(
        56f * view.resources.displayMetrics.density,
        ViewConfiguration.get(view.context).scaledTouchSlop * 3f
    )
    private val minimumVelocity = ViewConfiguration.get(view.context).scaledMinimumFlingVelocity.toFloat()
    private val detector = GestureDetectorCompat(view.context, object : GestureDetector.SimpleOnGestureListener() {
        override fun onDown(event: MotionEvent): Boolean = enabled()

        override fun onFling(
            first: MotionEvent?,
            second: MotionEvent,
            velocityX: Float,
            velocityY: Float
        ): Boolean {
            if (!enabled() || first == null) return false
            val dx = second.x - first.x
            val dy = second.y - first.y
            if (abs(dx) < minimumDistance || abs(dx) <= abs(dy) || abs(velocityX) < minimumVelocity) {
                return false
            }
            if (dx < 0) onPrevious() else onNext()
            return true
        }

        override fun onDoubleTap(event: MotionEvent): Boolean {
            if (!enabled()) return false
            onExplainCurrent()
            return true
        }
    })

    override fun onTouch(view: View, event: MotionEvent): Boolean {
        if (event.actionMasked == MotionEvent.ACTION_DOWN) {
            ignoreSequence = excludedViews.any { excluded ->
                excluded.isShown && Rect().also(excluded::getGlobalVisibleRect)
                    .contains(event.rawX.toInt(), event.rawY.toInt())
            }
        }
        val handled = !ignoreSequence && enabled() && detector.onTouchEvent(event)
        if (event.actionMasked == MotionEvent.ACTION_UP && handled) view.performClick()
        if (event.actionMasked == MotionEvent.ACTION_UP ||
            event.actionMasked == MotionEvent.ACTION_CANCEL
        ) {
            ignoreSequence = false
        }
        return handled
    }
}
