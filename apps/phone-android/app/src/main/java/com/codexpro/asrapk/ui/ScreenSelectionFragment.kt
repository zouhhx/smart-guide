package com.codexpro.asrapk.ui

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.fragment.app.Fragment
import androidx.fragment.app.activityViewModels
import androidx.recyclerview.widget.LinearLayoutManager
import com.codexpro.asrapk.databinding.FragmentScreenSelectionBinding

class ScreenSelectionFragment : Fragment() {
    private var _binding: FragmentScreenSelectionBinding? = null
    private val binding get() = _binding!!
    private val model: GuideViewModel by activityViewModels()
    private val actions get() = requireActivity() as GuideActions
    private lateinit var adapter: ScreenListAdapter

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, state: Bundle?): View {
        _binding = FragmentScreenSelectionBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, state: Bundle?) {
        adapter = ScreenListAdapter(actions::chooseScreenCandidate)
        binding.screenList.layoutManager = LinearLayoutManager(requireContext())
        binding.screenList.adapter = adapter
        binding.backButton.setOnClickListener { actions.closeScreenSelection() }
        binding.refreshButton.setOnClickListener { actions.refreshScreens() }
        binding.confirmTargetButton.setOnClickListener { actions.confirmScreenCandidate() }

        val render = {
            adapter.submit(
                model.screens.value.orEmpty(),
                model.candidateTargetId.value,
                model.selectedScreen.value?.targetScreenId
            )
            val candidate = model.screens.value.orEmpty()
                .firstOrNull { it.targetScreenId == model.candidateTargetId.value }
            binding.confirmTargetButton.isEnabled = candidate?.authoritative == true &&
                candidate.online && model.targetLocked.value != true &&
                model.targetCatalogReady.value == true
            binding.emptyState.visibility = if (model.screens.value.isNullOrEmpty()) View.VISIBLE else View.GONE
        }
        model.screens.observe(viewLifecycleOwner) { render() }
        model.candidateTargetId.observe(viewLifecycleOwner) { render() }
        model.selectedScreen.observe(viewLifecycleOwner) { render() }
        model.targetLocked.observe(viewLifecycleOwner) { render() }
        model.targetCatalogReady.observe(viewLifecycleOwner) { render() }
    }

    override fun onDestroyView() {
        _binding = null
        super.onDestroyView()
    }
}
