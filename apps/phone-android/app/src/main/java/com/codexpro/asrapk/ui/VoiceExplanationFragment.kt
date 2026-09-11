package com.codexpro.asrapk.ui

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.fragment.app.activityViewModels
import com.codexpro.asrapk.R
import com.codexpro.asrapk.databinding.FragmentVoiceExplanationBinding
import com.codexpro.asrapk.gesture.MicrophoneGestureController
import com.codexpro.asrapk.gesture.PageGestureController
import com.codexpro.asrapk.network.ControlReachability
import com.codexpro.asrapk.network.PhoneNetworkState

class VoiceExplanationFragment : Fragment() {
    private var _binding: FragmentVoiceExplanationBinding? = null
    private val binding get() = _binding!!
    private val model: GuideViewModel by activityViewModels()
    private val actions get() = requireActivity() as GuideActions

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, state: Bundle?): View {
        _binding = FragmentVoiceExplanationBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, state: Bundle?) {
        binding.settingsButton.setOnClickListener { actions.openOperationsSettings() }
        binding.changeTargetButton.setOnClickListener { actions.openScreenSelection() }

        binding.talkArea.setOnTouchListener(
            MicrophoneGestureController(
                binding.talkArea,
                enabled = actions::isMicrophoneEnabled,
                onStart = actions::startPushToTalk,
                onCancelPending = actions::markCancelPending,
                onCancelCleared = actions::clearCancelPending,
                onSend = actions::stopAndSend,
                onCancel = actions::cancelPushToTalk
            )
        )
        binding.gestureSurface.gestureListener =
            PageGestureController(
                binding.gestureSurface,
                excludedViews = listOf(binding.targetCard),
                enabled = actions::isPageGestureEnabled,
                onPrevious = actions::previousPage,
                onNext = actions::nextPage,
                onExplainCurrent = actions::explainCurrent
            )

        model.selectedScreen.observe(viewLifecycleOwner) { screen ->
            binding.targetName.text = screen?.displayName ?: getString(R.string.target_not_selected)
            binding.targetTypeIcon.setImageResource(
                when (screen?.screenType?.uppercase()) {
                    "TOUCH", "TOUCH_SCREEN" -> R.drawable.ic_touch_blue
                    "IMMERSIVE", "IMMERSIVE_SCREEN" -> R.drawable.ic_immersive_blue
                    "DIGITAL_HUMAN" -> R.drawable.ic_person_blue
                    else -> R.drawable.ic_screen_blue
                }
            )
            binding.targetState.text = when {
                screen == null -> getString(R.string.target_select_required)
                !screen.authoritative -> getString(R.string.target_context_unverified)
                screen.online -> getString(R.string.target_online)
                else -> getString(R.string.target_offline)
            }
            val color = when {
                screen?.online == true && screen.authoritative -> R.color.v27_success
                screen == null -> R.color.v27_text_muted
                else -> R.color.v27_error
            }
            binding.targetState.setTextColor(ContextCompat.getColor(requireContext(), color))
            binding.targetStatusDot.setBackgroundResource(
                if (screen?.online == true && screen.authoritative) {
                    R.drawable.bg_status_dot_green
                } else {
                    R.drawable.bg_status_dot_gray
                }
            )
        }
        model.recognitionText.observe(viewLifecycleOwner) {
            binding.recognitionText.text = it
            renderRecognitionContent()
        }
        model.statusText.observe(viewLifecycleOwner) { binding.operationStatus.text = it }
        model.networkState.observe(viewLifecycleOwner) { network ->
            binding.phoneNetworkStatus.text = when (network.phone) {
                PhoneNetworkState.OFFLINE -> getString(R.string.phone_offline)
                PhoneNetworkState.AVAILABLE -> getString(R.string.phone_network_available)
            }
            binding.coreStatus.text = when (network.control) {
                ControlReachability.UNKNOWN -> getString(R.string.core_unknown)
                ControlReachability.UNREACHABLE -> getString(R.string.core_unreachable)
                ControlReachability.REACHABLE -> getString(R.string.core_reachable)
            }
            binding.phoneNetworkDot.setBackgroundResource(
                if (network.phone == PhoneNetworkState.AVAILABLE) {
                    R.drawable.bg_status_dot_green
                } else {
                    R.drawable.bg_status_dot_gray
                }
            )
            binding.coreStatusDot.setBackgroundResource(
                when (network.control) {
                    ControlReachability.UNKNOWN -> R.drawable.bg_status_dot_blue
                    ControlReachability.UNREACHABLE -> R.drawable.bg_status_dot_gray
                    ControlReachability.REACHABLE -> R.drawable.bg_status_dot_green
                }
            )
        }
        model.targetLocked.observe(viewLifecycleOwner) { locked ->
            binding.lockState.visibility = if (locked) View.VISIBLE else View.GONE
            binding.changeTargetButton.isEnabled = !locked
        }
        model.listening.observe(viewLifecycleOwner) {
            renderMicrophone()
            renderRecognitionContent()
        }
        model.cancelPending.observe(viewLifecycleOwner) { renderMicrophone() }
    }

    private fun renderMicrophone() {
        val listening = model.listening.value == true
        val cancel = model.cancelPending.value == true
        binding.waveView.visibility = if (listening) View.VISIBLE else View.INVISIBLE
        if (!listening) binding.waveView.reset()
        binding.microphoneLabel.visibility = View.GONE
        binding.dynamicCancelHint.visibility = View.GONE
        binding.talkArea.setBackgroundResource(
            when {
                cancel -> R.drawable.bg_v27_mic_cancel
                listening -> R.drawable.bg_v27_mic_active
                else -> R.drawable.bg_v27_mic_idle
            }
        )
    }

    private fun renderRecognitionContent() {
        val showText = !model.recognitionText.value.isNullOrBlank()
        binding.recognitionText.visibility = if (showText) View.VISIBLE else View.GONE
        binding.recognitionWaveIcon.visibility = if (showText) View.GONE else View.VISIBLE
    }

    fun setAudioLevel(level: Float) {
        _binding?.waveView?.setLevel(level)
    }

    override fun onDestroyView() {
        binding.gestureSurface.gestureListener = null
        _binding = null
        super.onDestroyView()
    }
}
