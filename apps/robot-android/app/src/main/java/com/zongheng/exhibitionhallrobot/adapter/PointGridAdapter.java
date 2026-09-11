package com.zongheng.exhibitionhallrobot.adapter;

import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.BaseAdapter;
import android.widget.TextView;

import com.zongheng.exhibitionhallrobot.R;

import java.util.List;

public class PointGridAdapter extends BaseAdapter {
    private List<String> pointNames;
    private OnPointClickListener listener;

    public interface OnPointClickListener {
        void onPointClick(String pointName);
    }

    public PointGridAdapter(List<String> pointNames, OnPointClickListener listener) {
        this.pointNames = pointNames;
        this.listener = listener;
    }

    public void updateData(List<String> newPoints) {
        this.pointNames = newPoints;
        notifyDataSetChanged();
    }

    @Override
    public int getCount() {
        return pointNames == null ? 0 : pointNames.size();
    }

    @Override
    public Object getItem(int position) {
        return pointNames == null ? null : pointNames.get(position);
    }

    @Override
    public long getItemId(int position) {
        return position;
    }

    @Override
    public View getView(int position, View convertView, ViewGroup parent) {
        ViewHolder holder;
        if (convertView == null) {
            convertView = LayoutInflater.from(parent.getContext())
                    .inflate(R.layout.item_point_grid, parent, false);
            holder = new ViewHolder();
            holder.tvPointName = convertView.findViewById(R.id.tvPointName);
            convertView.setTag(holder);
        } else {
            holder = (ViewHolder) convertView.getTag();
        }

        String pointName = pointNames.get(position);
        holder.tvPointName.setText(pointName);

        convertView.setOnClickListener(v -> {
            if (listener != null) {
                listener.onPointClick(pointName);
            }
        });

        return convertView;
    }

    static class ViewHolder {
        TextView tvPointName;
    }
}
