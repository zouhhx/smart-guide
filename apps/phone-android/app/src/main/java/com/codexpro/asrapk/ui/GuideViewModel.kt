package com.codexpro.asrapk.ui

import androidx.lifecycle.MutableLiveData
import androidx.lifecycle.ViewModel
import com.codexpro.asrapk.network.ControlReachability
import com.codexpro.asrapk.network.NetworkState
import com.codexpro.asrapk.network.PhoneNetworkState
import com.codexpro.asrapk.target.TargetScreen

class GuideViewModel : ViewModel() {
    val screens = MutableLiveData<List<TargetScreen>>(emptyList())
    val selectedScreen = MutableLiveData<TargetScreen?>(null)
    val candidateTargetId = MutableLiveData<String?>(null)
    val statusText = MutableLiveData("准备就绪")
    val recognitionText = MutableLiveData("")
    val networkState = MutableLiveData(
        NetworkState(PhoneNetworkState.OFFLINE, ControlReachability.UNKNOWN)
    )
    val targetLocked = MutableLiveData(false)
    val targetCatalogReady = MutableLiveData(false)
    val listening = MutableLiveData(false)
    val cancelPending = MutableLiveData(false)
    val submitting = MutableLiveData(false)

    fun replaceScreens(value: List<TargetScreen>, selected: TargetScreen?) {
        screens.value = value
        selectedScreen.value = selected
        if (candidateTargetId.value == null) candidateTargetId.value = selected?.targetScreenId
    }
}
