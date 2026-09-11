package com.codexpro.asrapk.ui

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView
import com.codexpro.asrapk.R
import com.codexpro.asrapk.databinding.ItemTargetScreenBinding
import com.codexpro.asrapk.target.TargetScreen

class ScreenListAdapter(
    private val onClick: (TargetScreen) -> Unit
) : RecyclerView.Adapter<ScreenListAdapter.Holder>() {
    private var items: List<TargetScreen> = emptyList()
    private var candidateId: String? = null
    private var selectedId: String? = null

    fun submit(value: List<TargetScreen>, candidate: String?, selected: String?) {
        items = value
        candidateId = candidate
        selectedId = selected
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder {
        return Holder(ItemTargetScreenBinding.inflate(LayoutInflater.from(parent.context), parent, false))
    }

    override fun getItemCount(): Int = items.size

    override fun onBindViewHolder(holder: Holder, position: Int) = holder.bind(items[position])

    inner class Holder(private val binding: ItemTargetScreenBinding) : RecyclerView.ViewHolder(binding.root) {
        fun bind(screen: TargetScreen) {
            val context = binding.root.context
            val id = screen.targetScreenId
            val isCandidate = id == candidateId
            binding.screenName.text = screen.displayName
            binding.screenTypeIcon.setImageResource(
                when (screen.screenType.uppercase()) {
                    "TOUCH", "TOUCH_SCREEN" -> R.drawable.ic_touch_blue
                    "IMMERSIVE", "IMMERSIVE_SCREEN" -> R.drawable.ic_immersive_blue
                    "DIGITAL_HUMAN" -> R.drawable.ic_person_blue
                    else -> R.drawable.ic_screen_blue
                }
            )
            binding.screenStatus.text = when {
                !screen.authoritative -> context.getString(R.string.target_context_unverified)
                screen.online -> context.getString(R.string.target_online)
                else -> context.getString(R.string.target_offline)
            }
            binding.screenStatus.setTextColor(
                ContextCompat.getColor(
                    context,
                    if (screen.online && screen.authoritative) R.color.v27_success else R.color.v27_error
                )
            )
            binding.screenStatusDot.setBackgroundResource(
                if (screen.online && screen.authoritative) {
                    R.drawable.bg_status_dot_green
                } else {
                    R.drawable.bg_status_dot_gray
                }
            )
            binding.screenRadio.isChecked = isCandidate
            binding.root.strokeWidth = if (isCandidate) dp(2) else dp(1)
            binding.root.strokeColor = ContextCompat.getColor(
                context,
                if (isCandidate) R.color.v27_blue else R.color.v27_card_stroke
            )
            binding.root.setCardBackgroundColor(
                ContextCompat.getColor(
                    context,
                    if (isCandidate) R.color.v27_selected_background else R.color.white
                )
            )
            binding.currentBadge.text = if (id == selectedId) {
                context.getString(R.string.current_target)
            } else ""
            binding.root.isEnabled = screen.authoritative && screen.online
            binding.root.alpha = if (binding.root.isEnabled) 1f else 0.68f
            binding.root.setOnClickListener { if (binding.root.isEnabled) onClick(screen) }
        }

        private fun dp(value: Int): Int =
            (value * binding.root.resources.displayMetrics.density).toInt()
    }
}
