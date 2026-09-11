package com.codexpro.asrapk.gesture

import android.content.Context
import android.util.AttributeSet
import android.widget.FrameLayout

class PressToTalkLayout @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : FrameLayout(context, attrs) {
    override fun performClick(): Boolean {
        super.performClick()
        return true
    }
}
