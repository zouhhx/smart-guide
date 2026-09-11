package com.codexpro.asrapk

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Build
import android.os.Bundle
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.text.InputType
import android.util.Log
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.SwitchCompat
import androidx.core.content.ContextCompat
import androidx.core.view.setPadding
import androidx.fragment.app.commit
import com.codexpro.asrapk.databinding.ActivityMainBinding
import com.codexpro.asrapk.input.DeploymentConfigProvider
import com.codexpro.asrapk.input.GatewayConfigurationValidator
import com.codexpro.asrapk.input.InputSubmissionCoordinator
import com.codexpro.asrapk.input.SubmissionUpdate
import com.codexpro.asrapk.input.DeploymentConfig
import com.codexpro.asrapk.input.SpeechInterruptClient
import com.codexpro.asrapk.input.SpeechInterruptOperation
import com.codexpro.asrapk.input.SpeechInterruptResult
import okhttp3.Call
import android.widget.Toast
import com.codexpro.asrapk.network.ControlReachability
import com.codexpro.asrapk.network.NetworkStateMonitor
import com.codexpro.asrapk.network.PhoneNetworkState
import com.codexpro.asrapk.target.TargetScreen
import com.codexpro.asrapk.target.TargetScreenRepository
import com.codexpro.asrapk.ui.GuideActions
import com.codexpro.asrapk.ui.GuideViewModel
import com.codexpro.asrapk.ui.ScreenSelectionFragment
import com.codexpro.asrapk.ui.VoiceExplanationFragment

class MainActivity : AppCompatActivity(), GuideActions {
    private lateinit var binding: ActivityMainBinding
    private val model: GuideViewModel by viewModels()
    private lateinit var deploymentConfigProvider: DeploymentConfigProvider
    private lateinit var targetRepository: TargetScreenRepository
    private lateinit var networkMonitor: NetworkStateMonitor
    private lateinit var submissionCoordinator: InputSubmissionCoordinator
    private lateinit var voiceInputController: VoiceInputController
    private var vibrator: Vibrator? = null
    private var pressStillActive = false
    private var recordingTargetId: String? = null
    private var recordingConfig: DeploymentConfig? = null
    private val speechInterruptClient = SpeechInterruptClient()
    private var interruptCall: Call? = null
    private var pressGeneration = 0L

    private val recordAudioPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted && pressStillActive) {
                voiceInputController.startListening()
            } else if (!granted) {
                pressStillActive = false
                recordingTargetId = null
                showStatus(getString(R.string.microphone_permission_required))
                errorFeedback()
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AppConfig.migrateToCurrentProtocol(applicationContext)
        supportActionBar?.hide()
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        vibrator = systemVibrator()
        deploymentConfigProvider = DeploymentConfigProvider(applicationContext)
        targetRepository = TargetScreenRepository(
            context = applicationContext,
            asrEndpointProvider = { deploymentConfigProvider.current().asrEndpoint },
            onScreensChanged = { screens, error ->
                model.targetCatalogReady.value = error == null && targetRepository.isCatalogReady()
                model.replaceScreens(screens, targetRepository.selectedScreen())
                if (error != null) showStatus(statusFor(error))
            }
        )
        networkMonitor = NetworkStateMonitor(
            context = applicationContext,
            asrEndpointProvider = { deploymentConfigProvider.current().asrEndpoint },
            onChanged = { model.networkState.value = it }
        )
        submissionCoordinator = InputSubmissionCoordinator(
            configProvider = deploymentConfigProvider::current,
            onUpdate = ::handleSubmissionUpdate
        )
        voiceInputController = VoiceInputController(
            context = applicationContext,
            onStatusChanged = ::showStatus,
            onPreviewChanged = { model.recognitionText.value = it },
            onFinalTextReady = ::handleAsrFinalText,
            onAudioLevelChanged = { level ->
                (supportFragmentManager.findFragmentById(R.id.fragmentHost) as? VoiceExplanationFragment)
                    ?.setAudioLevel(level)
            },
            onListeningChanged = { listening ->
                model.listening.value = listening
                if (!listening) model.cancelPending.value = false
            },
            sparkCredentialsProvider = SparkCredentialsProvider(applicationContext)::current
        )
        model.targetLocked.value = AppConfig.isTargetLocked(applicationContext)

        val promptForInitialConfiguration = savedInstanceState == null &&
            !isGatewayConfigurationComplete()
        if (savedInstanceState == null) {
            supportFragmentManager.commit {
                replace(R.id.fragmentHost, VoiceExplanationFragment())
            }
            if (promptForInitialConfiguration) {
                binding.root.post { openOperationsSettings() }
            }
        }
        if (!hasRecordAudioPermission() && !promptForInitialConfiguration) {
            binding.root.post {
                recordAudioPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
            }
        }
    }

    override fun onStart() {
        super.onStart()
        targetRepository.start()
        networkMonitor.start()
    }

    override fun onStop() {
        finishInterruptWait()
        pressStillActive = false
        recordingTargetId = null
        voiceInputController.cancelListening()
        targetRepository.stop()
        networkMonitor.stop()
        super.onStop()
    }

    override fun onDestroy() {
        speechInterruptClient.close()
        voiceInputController.release()
        submissionCoordinator.close()
        targetRepository.close()
        networkMonitor.close()
        super.onDestroy()
    }

    override fun startPushToTalk() {
        if (!isMicrophoneEnabled()) {
            showStatus(getString(R.string.asr_temporarily_unavailable))
            errorFeedback()
            return
        }
        pressStillActive = true
        recordingTargetId = model.selectedScreen.value?.targetScreenId
        recordingConfig = deploymentConfigProvider.current()
        // Release local HTTP wait; the dedicated server interrupt handles actual playback.
        submissionCoordinator.abandonPending()
        model.submitting.value = false
        interruptCurrentSpeech()
        vibrate(28L, 120)
        if (hasRecordAudioPermission()) {
            voiceInputController.startListening()
        } else {
            recordAudioPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    override fun stopAndSend() {
        pressStillActive = false
        model.cancelPending.value = false
        voiceInputController.stopAndSend()
        vibrate(36L, 150)
    }

    override fun cancelPushToTalk() {
        pressStillActive = false
        recordingTargetId = null
        model.cancelPending.value = false
        voiceInputController.cancelListening()
        vibrate(55L, 190)
        playTone(ToneGenerator.TONE_PROP_NACK)
    }

    override fun markCancelPending() {
        model.cancelPending.value = true
        voiceInputController.markCancelPending()
        vibrate(45L, 180)
    }

    override fun clearCancelPending() {
        model.cancelPending.value = false
        voiceInputController.clearCancelPending()
        vibrate(20L, 100)
    }

    override fun openScreenSelection() {
        if (model.targetLocked.value == true) {
            showStatus(getString(R.string.target_locked_message))
            errorFeedback()
            return
        }
        if (supportFragmentManager.findFragmentById(R.id.fragmentHost) is ScreenSelectionFragment) return
        model.candidateTargetId.value = model.selectedScreen.value?.targetScreenId
        supportFragmentManager.commit {
            replace(R.id.fragmentHost, ScreenSelectionFragment())
            addToBackStack(SCREEN_SELECTION_BACK_STACK)
        }
    }

    override fun closeScreenSelection() {
        supportFragmentManager.popBackStack()
    }

    override fun chooseScreenCandidate(screen: TargetScreen) {
        if (screen.authoritative && screen.online && model.targetLocked.value != true) {
            model.candidateTargetId.value = screen.targetScreenId
            vibrate(20L, 100)
        }
    }

    override fun confirmScreenCandidate() {
        if (model.targetLocked.value == true) {
            showStatus(getString(R.string.target_locked_message))
            return
        }
        val candidate = model.screens.value.orEmpty()
            .firstOrNull { it.targetScreenId == model.candidateTargetId.value }
        if (candidate == null || !targetRepository.select(candidate)) {
            showStatus(getString(R.string.target_unavailable_for_selection))
            errorFeedback()
            return
        }
        model.selectedScreen.value = candidate
        showStatus(getString(R.string.target_selected, candidate.displayName))
        vibrate(42L, 170)
        closeScreenSelection()
    }

    override fun refreshScreens() {
        showStatus(getString(R.string.refreshing_screens))
        targetRepository.refresh()
    }

    override fun explainCurrent() {
        if (!isCoreSubmissionReady()) {
            showStatus(blockedInputReason())
            errorFeedback()
            return
        }
        vibrate(34L, 140)
        submissionCoordinator.submitDoubleTap(model.selectedScreen.value!!.targetScreenId)
    }

    override fun previousPage() = submitNavigation(previous = true)

    override fun nextPage() = submitNavigation(previous = false)

    private fun submitNavigation(previous: Boolean) {
        if (!isCoreSubmissionReady()) {
            showStatus(blockedInputReason())
            errorFeedback()
            return
        }
        val target = model.selectedScreen.value!!.targetScreenId
        vibrate(34L, 140)
        if (previous) submissionCoordinator.submitSwipeLeft(target)
        else submissionCoordinator.submitSwipeRight(target)
    }

    override fun openOperationsSettings() {
        val context = this
        val padding = (20 * resources.displayMetrics.density).toInt()
        val serverInput = EditText(context).apply {
            hint = getString(R.string.config_url_hint)
            setText(AppConfig.getServerUrl(context).orEmpty())
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        }
        fun label(textResource: Int) = TextView(context).apply {
            text = getString(textResource)
            setTextColor(ContextCompat.getColor(context, R.color.v27_text_primary))
            textSize = 13f
        }
        val runtimeSection = TextView(context).apply {
            text = getString(R.string.runtime_status_section)
            setTextColor(ContextCompat.getColor(context, R.color.v27_text_primary))
            textSize = 15f
        }
        val inputStatus = TextView(context).apply {
            text = getString(R.string.input_status_format, model.statusText.value.orEmpty())
            setTextColor(ContextCompat.getColor(context, R.color.v27_text_muted))
            textSize = 13f
        }
        val phoneStatus = TextView(context).apply {
            val value = when (model.networkState.value?.phone) {
                PhoneNetworkState.AVAILABLE -> getString(R.string.phone_network_available)
                else -> getString(R.string.phone_offline)
            }
            text = getString(R.string.phone_status_format, value)
            setTextColor(ContextCompat.getColor(context, R.color.v27_text_muted))
            textSize = 13f
        }
        val coreStatus = TextView(context).apply {
            val value = when (model.networkState.value?.control) {
                ControlReachability.REACHABLE -> getString(R.string.core_reachable)
                ControlReachability.UNREACHABLE -> getString(R.string.core_unreachable)
                else -> getString(R.string.core_unknown)
            }
            text = getString(R.string.core_status_format, value)
            setTextColor(ContextCompat.getColor(context, R.color.v27_text_muted))
            textSize = 13f
        }
        val selected = model.selectedScreen.value
        val lockSwitch = SwitchCompat(context).apply {
            text = getString(R.string.lock_current_target)
            isChecked = AppConfig.isTargetLocked(context)
            isEnabled = isChecked || selected?.authoritative == true
        }
        val soundSwitch = SwitchCompat(context).apply {
            text = getString(R.string.sound_feedback)
            isChecked = AppConfig.isSoundFeedbackEnabled(context)
        }
        val note = TextView(context).apply {
            text = getString(R.string.gateway_security_note)
            setTextColor(ContextCompat.getColor(context, R.color.v27_text_muted))
            textSize = 13f
        }
        val container = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(padding)
            addView(label(R.string.gateway_url_label))
            addView(serverInput)
            addView(runtimeSection)
            addView(inputStatus)
            addView(phoneStatus)
            addView(coreStatus)
            addView(lockSwitch)
            addView(soundSwitch)
            addView(note)
        }
        val scrollContainer = ScrollView(context).apply { addView(container) }
        val dialog = AlertDialog.Builder(context)
            .setTitle(R.string.operations_settings)
            .setView(scrollContainer)
            .setNegativeButton(android.R.string.cancel, null)
            .setPositiveButton(R.string.config_save, null)
            .show()
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            val previous = deploymentConfigProvider.current()
            val normalizedEndpoint: String
            try {
                normalizedEndpoint = GatewayConfigurationValidator.normalizeAsrEndpoint(
                    serverInput.text?.toString().orEmpty()
                )
            } catch (error: IllegalArgumentException) {
                showStatus(statusFor(error.message.orEmpty()))
                errorFeedback()
                return@setOnClickListener
            }

            val identityChanged = previous.asrEndpoint != normalizedEndpoint
            AppConfig.saveServerUrl(context, normalizedEndpoint)
            if (identityChanged) targetRepository.invalidate(clearSelection = true)

            val lockEnabled = !identityChanged && lockSwitch.isChecked &&
                model.selectedScreen.value?.authoritative == true
            AppConfig.setTargetLocked(context, lockEnabled)
            AppConfig.setSoundFeedbackEnabled(context, soundSwitch.isChecked)
            model.targetLocked.value = lockEnabled
            targetRepository.start()
            networkMonitor.start()
            showStatus(getString(R.string.settings_saved))
            dialog.dismiss()
            if (!hasRecordAudioPermission()) {
                binding.root.post {
                    recordAudioPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
                }
            }
        }
    }

    override fun isMicrophoneEnabled(): Boolean {
        return !pressStillActive && !voiceInputController.isBusy()
    }

    override fun isPageGestureEnabled(): Boolean =
        !pressStillActive && !voiceInputController.isBusy() && model.submitting.value != true

    private fun interruptCurrentSpeech() {
        finishInterruptWait()
        val target = recordingTargetId
        if (target == null) {
            showStatus(getString(R.string.target_select_required))
            return
        }
        val operation = SpeechInterruptOperation(recordingConfig!!.asrEndpoint, target)
        val currentPress = pressGeneration
        Log.i("SpeechInterrupt", "dispatch requestId=${operation.requestId} target=${operation.target}")
        interruptCall = speechInterruptClient.interrupt(operation) { result ->
            Log.i("SpeechInterrupt", "requestId=${operation.requestId} target=${operation.target} result=$result")
            binding.root.post {
                if (currentPress == pressGeneration && !isDestroyed) {
                    if (result is SpeechInterruptResult.Failed) {
                        val reason = when (result.code) {
                            "CLIENT_UPGRADE_REQUIRED" -> getString(R.string.interrupt_client_upgrade)
                            "TTS_PROVIDER_UNSUPPORTED" -> getString(R.string.interrupt_provider_unsupported)
                            "TARGET_OFFLINE" -> getString(R.string.interrupt_target_offline)
                            "SCREEN_NOT_FOUND", "TARGET_REQUIRED" -> getString(R.string.target_select_required)
                            else -> getString(R.string.interrupt_unconfirmed)
                        }
                        showStatus(reason)
                        Toast.makeText(this, reason, Toast.LENGTH_SHORT).show()
                    }
                    // A 202 confirms dispatch, not speaker silence. Keep the recording UI.
                }
            }
        }
    }

    private fun finishInterruptWait() {
        pressGeneration++
        interruptCall?.cancel()
        interruptCall = null
    }

    private fun isGatewayConfigurationComplete(): Boolean {
        val config = deploymentConfigProvider.current()
        return config.asrEndpoint.isNotBlank()
    }

    private fun handleSubmissionUpdate(update: SubmissionUpdate) {
        when (update) {
            SubmissionUpdate.Sending -> {
                model.submitting.value = true
                showStatus(getString(R.string.submitting))
            }
            is SubmissionUpdate.Succeeded -> {
                model.submitting.value = false
                val message = when {
                    update.result.duplicate -> getString(R.string.gesture_duplicate)
                    update.result.ignored -> getString(R.string.gesture_ignored)
                    !update.result.reply.isNullOrBlank() -> update.result.reply
                    else -> getString(R.string.request_accepted_by_control)
                }
                showStatus(message)
                if (update.result.ignored || update.result.duplicate) errorFeedback()
                else vibrate(45L, 170)
            }
            is SubmissionUpdate.Failed -> {
                model.submitting.value = false
                showStatus(statusFor(update.code))
                errorFeedback()
            }
        }
    }

    private fun handleAsrFinalText(text: String) {
        val targetId = recordingTargetId
        val config = recordingConfig
        recordingTargetId = null
        recordingConfig = null
        // Never leave an old interrupt queued/retrying once the new question is submitted.
        finishInterruptWait()
        if (config != null && config == deploymentConfigProvider.current() && isCoreSubmissionReady(targetId)) {
            submissionCoordinator.submitVoice(text, targetId!!, config)
        } else {
            showStatus(getString(R.string.asr_completed_not_sent))
            vibrate(36L, 140)
        }
    }

    private fun isCoreSubmissionReady(targetId: String? = model.selectedScreen.value?.targetScreenId): Boolean {
        val selected = model.screens.value.orEmpty().firstOrNull { it.targetScreenId == targetId }
        val network = model.networkState.value
        return selected?.authoritative == true &&
            selected.online &&
            model.targetCatalogReady.value == true &&
            network?.phone == PhoneNetworkState.AVAILABLE &&
            network.control == ControlReachability.REACHABLE
    }

    private fun blockedInputReason(): String {
        val selected = model.selectedScreen.value
        val network = model.networkState.value
        return when {
            selected == null -> getString(R.string.target_select_required)
            !selected.online -> getString(R.string.target_offline_blocked)
            model.targetCatalogReady.value != true -> getString(R.string.screen_catalog_unreachable)
            network?.phone != PhoneNetworkState.AVAILABLE -> getString(R.string.phone_offline)
            network.control != ControlReachability.REACHABLE -> getString(R.string.core_unreachable)
            else -> getString(R.string.input_temporarily_unavailable)
        }
    }

    private fun statusFor(code: String): String = when (code) {
        "HALL_API_URL_INVALID" -> getString(R.string.input_gateway_base_url_missing)
        "HALL_API_HTTP_HOST_NOT_PRIVATE" -> getString(R.string.http_gateway_private_ip_required)
        "SCREEN_CATALOG_UNREACHABLE" ->
            getString(R.string.screen_catalog_unreachable)
        "TARGET_NOT_SELECTED" -> getString(R.string.target_select_required)
        "TARGET_OFFLINE" -> getString(R.string.target_offline_blocked)
        "HALL_API_RESULT_UNKNOWN" -> getString(R.string.send_unconfirmed)
        "HALL_API_RESPONSE_INVALID" -> getString(R.string.hall_api_response_invalid)
        "ASR_TEXT_EMPTY" -> getString(R.string.asr_text_empty)
        "INPUT_ALREADY_SENDING" -> getString(R.string.already_sending)
        else -> getString(R.string.send_failed_with_code, code)
    }

    private fun showUnsupportedControl(message: Int) {
        showStatus(getString(message))
        vibrate(70L, 210)
        playTone(ToneGenerator.TONE_PROP_NACK)
    }

    private fun showStatus(text: String) {
        model.statusText.value = text
    }

    private fun hasRecordAudioPermission(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED

    private fun systemVibrator(): Vibrator? = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        (getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager)?.defaultVibrator
    } else {
        @Suppress("DEPRECATION")
        getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
    }

    private fun vibrate(durationMs: Long, amplitude: Int) {
        val device = vibrator ?: return
        if (!device.hasVibrator()) return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            device.vibrate(VibrationEffect.createOneShot(durationMs, amplitude))
        } else {
            @Suppress("DEPRECATION")
            device.vibrate(durationMs)
        }
    }

    private fun errorFeedback() {
        vibrate(80L, 220)
        playTone(ToneGenerator.TONE_PROP_NACK)
    }

    private fun playTone(tone: Int) {
        if (!AppConfig.isSoundFeedbackEnabled(applicationContext)) return
        ToneGenerator(AudioManager.STREAM_NOTIFICATION, 45).apply {
            startTone(tone, 120)
            binding.root.postDelayed({ release() }, 180L)
        }
    }

    companion object {
        private const val SCREEN_SELECTION_BACK_STACK = "screen-selection"
    }
}
