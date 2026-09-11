package com.codexpro.asrapk.ui

import com.codexpro.asrapk.target.TargetScreen

interface GuideActions {
    fun startPushToTalk()
    fun stopAndSend()
    fun cancelPushToTalk()
    fun markCancelPending()
    fun clearCancelPending()
    fun openScreenSelection()
    fun closeScreenSelection()
    fun chooseScreenCandidate(screen: TargetScreen)
    fun confirmScreenCandidate()
    fun refreshScreens()
    fun explainCurrent()
    fun previousPage()
    fun nextPage()
    fun openOperationsSettings()
    fun isMicrophoneEnabled(): Boolean
    fun isPageGestureEnabled(): Boolean
}
