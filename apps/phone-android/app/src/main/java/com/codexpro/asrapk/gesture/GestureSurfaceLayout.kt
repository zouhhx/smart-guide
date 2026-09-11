package com.codexpro.asrapk.gesture

import android.content.Context
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import android.widget.FrameLayout

class GestureSurfaceLayout @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : FrameLayout(context, attrs) {
    var gestureListener: View.OnTouchListener? = null

    override fun dispatchTouchEvent(event: MotionEvent): Boolean {
        gestureListener?.onTouch(this, event)
        return super.dispatchTouchEvent(event)
    }
}
