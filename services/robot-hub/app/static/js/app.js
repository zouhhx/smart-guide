// 展厅流程中枢系统 v5.0 前端逻辑
// 核心目标：零ID化、全下拉、傻瓜化操作

const state = {
  refs: {
    halls: [],
    areas: [],
    exhibits: [],
    specials: [],
    equipments: [],
    group_commands: [],
    positions: [],
  },
  workflows: [],
  bindings: [],
  intents: [],
  schedules: [],
  logs: [],
  editingWorkflowId: null,
  unitree: { motions: [], dances: [], maps: [], points: {} },
};

// ============================================================
// 基础工具
// ============================================================
async function api(url, options = {}) {
  const resp = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  const data = await resp.json();
  if (!resp.ok) {
    throw new Error(data.error || `请求失败: ${resp.status}`);
  }
  return data;
}

function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

function setResult(id, message, ok = true) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = message || '';
  el.className = `result-box ${ok ? 'ok' : 'error'}`;
}

function fillSelect(selectId, items, options = {}) {
  const el = document.getElementById(selectId);
  if (!el) return;
  const { valueKey = 'id', labelKey = 'name', placeholder = '请选择', allowEmpty = false, emptyLabel = '全部' } = options;

  let html = '';
  if (allowEmpty) {
    html += `<option value="">${emptyLabel}</option>`;
  } else {
    html += `<option value="">${placeholder}</option>`;
  }

  for (const item of items || []) {
    const value = item[valueKey] ?? '';
    const label = item[labelKey] ?? value;
    html += `<option value="${escapeHtml(String(value))}">${escapeHtml(String(label))}</option>`;
  }
  el.innerHTML = html;
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function currentSpecialName() {
  const s = state.refs.specials.find(x => Number(x.is_current) === 1);
  return s ? s.name : '未设置';
}

// ============================================================
// 页面初始化
// ============================================================
window.addEventListener('DOMContentLoaded', async () => {
  // 第一时间从 URL hash / localStorage 恢复 tab —— 不等任何接口。
  // 之前放在所有 loader 完成之后, 任意 loader 失败就会停留在 dashboard,
  // 导致 "切换 tab 后刷新经常回首页" 的体感 bug。
  try { restoreTabFromURL(); } catch (e) { console.warn('restoreTab fail', e); }

  // bootstrap 数据放在前面 (引用数据/配置), 仍然 await。
  try {
    await loadBootstrapData();
    bindDefaults();
  } catch (e) {
    console.error('bootstrap fail', e);
    showToast(`基础数据加载失败: ${e.message}`, 'error');
  }

  // 各 tab 数据并行加载, 单点失败不连累其它 — Promise.allSettled。
  const loaders = [
    ['stats', loadStats], ['workflows', loadWorkflows],
    ['bindings', loadBindings], ['tourpoints', loadTourPoints],
    ['intents', loadIntentMappings], ['schedules', loadSchedules],
    ['logs', loadLogs], ['robotctrl', loadRobotConfig],
  ];
  const results = await Promise.allSettled(loaders.map(([_n, fn]) => fn()));
  const failed = results
    .map((r, i) => r.status === 'rejected' ? loaders[i][0] : null)
    .filter(Boolean);
  if (failed.length) {
    console.warn('部分模块加载失败:', failed);
    showToast(`部分模块加载失败: ${failed.join(', ')} (其它正常)`, 'warning');
  } else {
    showToast('系统初始化完成', 'ok');
  }
});

async function loadBootstrapData() {
  const [refResp, cfgResp, mResp, dResp, mapResp] = await Promise.all([
    api('/api/ref/all'),
    api('/api/system/config'),
    api('/api/unitree/motions').catch(() => ({ data: [] })),
    api('/api/unitree/dances').catch(() => ({ data: [] })),
    api('/api/unitree/maps').catch(() => ({ data: [] })),
  ]);

  state.refs = refResp.data || state.refs;
  state.unitree = {
    motions: mResp.data || [],
    dances: dResp.data || [],
    maps: mapResp.data || [],
    points: {}, // map_id → [{point_id, point_name}]
  };

  document.getElementById('current-special-badge').textContent = `当前专场: ${currentSpecialName()}`;
  document.getElementById('sync-time').textContent = `同步: ${cfgResp.data.last_sync_time || '--'}`;
}

async function loadUnitreeMapPoints(mapId) {
  if (!mapId) return [];
  if (state.unitree && state.unitree.points && state.unitree.points[mapId]) {
    return state.unitree.points[mapId];
  }
  try {
    const r = await api(`/api/unitree/map-points?map_id=${encodeURIComponent(mapId)}`);
    const pts = r.data || [];
    if (!state.unitree) state.unitree = { motions: [], dances: [], maps: [], points: {} };
    if (!state.unitree.points) state.unitree.points = {};
    state.unitree.points[mapId] = pts;
    return pts;
  } catch (e) {
    return [];
  }
}

function bindDefaults() {
  // 控制面板
  fillSelect('ctrl-special', state.refs.specials);
  fillSelect('ctrl-res-special', state.refs.specials);
  fillSelect('ctrl-group', state.refs.group_commands);
  fillSelect('ctrl-eq-area', state.refs.areas);
  fillSelect('wf-special', state.refs.specials, { allowEmpty: true, emptyLabel: '全部专场' });

  // 绑定页
  fillSelect('bind-position', state.refs.positions, { valueKey: 'position_id', labelKey: 'name' });
  updateBindTargetOptions();

  // 参观配置页
  fillSelect('tp-position', state.bindings.map(b => ({ position_id: b.position_id, name: b.position_name + ' → ' + b.target_name })), { valueKey: 'position_id', labelKey: 'name' });
  fillSelect('tp-return-pos', state.refs.positions, { valueKey: 'position_id', labelKey: 'name' }, true);

  // 意图页
  updateIntentTargetOptions();

  // 初始化联动
  loadEquipmentsByArea();
  loadExhibitsForResource();
}

// ============================================================
// Tab 切换
// ============================================================
const TAB_NAMES = ['dashboard', 'control', 'robotctrl', 'workflows', 'bindings', 'tourpoints', 'music', 'intents', 'schedules', 'visitors', 'settings', 'logs'];

function switchTab(name) {
  if (!TAB_NAMES.includes(name)) name = 'dashboard';

  document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));

  const tabs = Array.from(document.querySelectorAll('.tab'));
  const idx = TAB_NAMES.indexOf(name);
  if (tabs[idx]) tabs[idx].classList.add('active');
  const content = document.getElementById(`tab-${name}`);
  if (content) content.classList.add('active');

  // 持久化到 URL hash + localStorage，刷新后保持在同一 tab
  try {
    if (window.location.hash !== '#' + name) {
      history.replaceState(null, '', '#' + name);
    }
    localStorage.setItem('hub.activeTab', name);
  } catch (e) {}

  if (name === 'settings') loadSettings();
  if (name === 'visitors') initVisitorTab();
  if (name === 'robotctrl') initRobotCtrlTab();
  if (name === 'music') loadMusic();
  // logs tab 自动启停实时刷新, 切走时停 timer 省请求
  _onLogsTabActive(name === 'logs');
}

function restoreTabFromURL() {
  // 优先级: URL hash > localStorage > dashboard
  let target = '';
  const hash = (window.location.hash || '').replace('#', '');
  if (hash && TAB_NAMES.includes(hash)) {
    target = hash;
  } else {
    try {
      const saved = localStorage.getItem('hub.activeTab');
      if (saved && TAB_NAMES.includes(saved)) target = saved;
    } catch (e) {}
  }
  if (target && target !== 'dashboard') {
    switchTab(target);
  }
}

// 监听浏览器前进后退
window.addEventListener('hashchange', () => {
  const hash = (window.location.hash || '').replace('#', '');
  if (hash && TAB_NAMES.includes(hash)) {
    switchTab(hash);
  }
});

// ============================================================
// 统计
// ============================================================
async function loadStats() {
  const resp = await api('/api/system/stats');
  const s = resp.data;
  for (const key of Object.keys(s)) {
    const el = document.getElementById(`stat-${key}`);
    if (el) el.textContent = s[key];
  }
  document.getElementById('robot-count-badge').textContent = `在线机器人: ${s.online_robots ?? 0}`;
}

async function triggerSync() {
  // source=all 同时拉中控 (waterdrop) + 机器人点位 (orionstar)
  // 同步后强制重拉 bootstrap 数据 + 各模块表格, 否则前端还是旧数据
  try {
    showToast('同步中…', 'ok');
    const resp = await api('/api/system/sync?source=all', { method: 'POST' });
    const wd = (resp.data && resp.data.waterdrop) || {};
    const os = (resp.data && resp.data.orionstar) || {};
    const wd_err = (wd.errors && wd.errors.length) ? ` 错误${wd.errors.length}` : '';
    const os_err = (os.errors && os.errors.length) ? ` 错误${os.errors.length}` : '';
    const summary = (
      `中控: 展项${wd.exhibits || 0} 专场${wd.specials || 0} ` +
      `资源${wd.resources || 0} 设备${wd.equipments || 0}${wd_err} | ` +
      `点位${os.positions || 0}${os_err}`
    );
    // 强制重拉所有受影响的数据 (引用数据 + 各模块表格), 让前端立刻看到新内容
    try {
      await loadBootstrapData();  // 引用数据 / 配置 / 宇树缓存等基础
    } catch (_) {}
    const reloads = [
      typeof loadStats === 'function' ? loadStats() : null,
      typeof loadWorkflows === 'function' ? loadWorkflows() : null,
      typeof loadBindings === 'function' ? loadBindings() : null,
      typeof loadTourPoints === 'function' ? loadTourPoints() : null,
      typeof loadIntentMappings === 'function' ? loadIntentMappings() : null,
    ].filter(Boolean);
    await Promise.allSettled(reloads);
    showToast('✓ 同步完成 — ' + summary, 'ok');
  } catch (e) {
    showToast('同步失败: ' + (e && e.message ? e.message : e), 'error');
  }
}

// 手动同步 OrionStar 地图点位 (含自动迁移旧地图绑定 + 清理旧点位)
async function syncOrionStarMap() {
  const resultEl = document.getElementById('orion-sync-result');
  if (resultEl) resultEl.innerHTML = '<span style="color:#888">同步中...</span>';
  try {
    const resp = await api('/api/system/sync?source=orionstar', { method: 'POST' });
    const data = (resp.data && resp.data.orionstar) || {};
    if (data.error) {
      if (resultEl) resultEl.innerHTML = `<span style="color:#e53e3e">同步失败: ${escapeHtml(data.error)}</span>`;
      showToast('同步失败: ' + data.error, 'error');
      return;
    }
    const parts = [
      `<b>${data.positions || 0}</b> 个点位`,
    ];
    if (data.migrated_bindings) parts.push(`迁移 <b>${data.migrated_bindings}</b> 条绑定`);
    if (data.migrated_scripts) parts.push(`迁移 <b>${data.migrated_scripts}</b> 条流程步骤`);
    if (data.purged_obsolete) parts.push(`清理 <b>${data.purged_obsolete}</b> 个旧点位`);
    if (resultEl) resultEl.innerHTML = `<span style="color:#38a169">✓ 同步完成: ${parts.join(' · ')}</span>`;
    showToast('地图点位同步完成', 'ok');
    // 刷新页面绑定下拉
    if (typeof loadBindPositions === 'function') loadBindPositions();
    if (typeof loadBindings === 'function') loadBindings();
    loadActiveMapBadge();
  } catch (e) {
    if (resultEl) resultEl.innerHTML = `<span style="color:#e53e3e">同步异常: ${escapeHtml(e.message)}</span>`;
    showToast(e.message, 'error');
  }
}

// 拉 system_config 显示活地图名
async function loadActiveMapBadge() {
  const badge = document.getElementById('orion-active-map');
  if (!badge) return;
  try {
    const resp = await api('/api/system/config');
    const cfg = (resp.data || resp || {});
    const name = cfg.active_map_name || cfg.active_map_id || '未知 (尚未同步)';
    badge.textContent = '活地图: ' + name;
  } catch (e) {
    badge.textContent = '活地图: 加载失败';
  }
}

// ============================================================
// 控制面板
// ============================================================
async function doSwitchSpecial() {
  const specialId = Number(document.getElementById('ctrl-special').value || 0);
  if (!specialId) return setResult('ctrl-special-result', '请先选择专场', false);
  try {
    const resp = await api('/api/control/switch_special', {
      method: 'POST',
      body: JSON.stringify({ special_id: specialId }),
    });
    setResult('ctrl-special-result', resp.message, true);
    await refreshReferences();
  } catch (e) {
    setResult('ctrl-special-result', e.message, false);
  }
}

function loadEquipmentsByArea() {
  const areaId = Number(document.getElementById('ctrl-eq-area').value || 0);
  let items = state.refs.equipments || [];
  if (areaId) items = items.filter(x => Number(x.area_id) === areaId);
  fillSelect('ctrl-equipment', items);
}

async function doEquipment(action) {
  const equipmentId = Number(document.getElementById('ctrl-equipment').value || 0);
  if (!equipmentId) return setResult('ctrl-eq-result', '请先选择设备', false);
  try {
    const resp = await api('/api/control/equipment', {
      method: 'POST',
      body: JSON.stringify({ equipment_id: equipmentId, action }),
    });
    setResult('ctrl-eq-result', `${resp.message} | 指令: ${resp.command}`, resp.status === 'ok');
    await loadLogs();
  } catch (e) {
    setResult('ctrl-eq-result', e.message, false);
  }
}

async function doGroupCommand() {
  const groupId = Number(document.getElementById('ctrl-group').value || 0);
  if (!groupId) return setResult('ctrl-group-result', '请先选择组策略', false);
  try {
    const resp = await api('/api/control/group_command', {
      method: 'POST',
      body: JSON.stringify({ group_id: groupId }),
    });
    setResult('ctrl-group-result', `${resp.message} | 指令: ${resp.command}`, resp.status === 'ok');
    await loadLogs();
  } catch (e) {
    setResult('ctrl-group-result', e.message, false);
  }
}

function loadExhibitsForResource() {
  fillSelect('ctrl-res-exhibit', state.refs.exhibits || []);
  loadResourcesByExhibitSpecial();
}

async function loadResourcesByExhibitSpecial() {
  const specialId = Number(document.getElementById('ctrl-res-special').value || 0);
  const exhibitId = Number(document.getElementById('ctrl-res-exhibit').value || 0);
  if (!specialId || !exhibitId) {
    fillSelect('ctrl-res-resource', []);
    return;
  }
  try {
    const resp = await api(`/api/ref/resources?special_id=${specialId}&exhibit_id=${exhibitId}`);
    fillSelect('ctrl-res-resource', resp.data || []);
  } catch (e) {
    showToast(`加载资源失败: ${e.message}`, 'error');
  }
}

async function doPlayResource() {
  const specialId = Number(document.getElementById('ctrl-res-special').value || 0);
  const exhibitId = Number(document.getElementById('ctrl-res-exhibit').value || 0);
  const resourceId = Number(document.getElementById('ctrl-res-resource').value || 0);
  if (!specialId || !exhibitId || !resourceId) return setResult('ctrl-res-result', '请选择专场、展项、资源', false);

  try {
    const resp = await api('/api/control/resource_play', {
      method: 'POST',
      body: JSON.stringify({ special_id: specialId, exhibit_id: exhibitId, resource_id: resourceId }),
    });
    const commands = (resp.commands || []).join(' → ');
    setResult('ctrl-res-result', `${resp.message} | 指令序列: ${commands}`, resp.status === 'ok');
    await loadLogs();
  } catch (e) {
    setResult('ctrl-res-result', e.message, false);
  }
}

// ============================================================
// 流程管理
// ============================================================
async function loadWorkflows() {
  const [resp, activeResp] = await Promise.all([
    api('/api/workflows'),
    api('/api/active-workflows').catch(() => ({ data: [] })),
  ]);
  state.workflows = resp.data || [];
  // script_id → [active flow, ...]
  const activeByScript = {};
  for (const a of (activeResp.data || [])) {
    (activeByScript[a.script_id] = activeByScript[a.script_id] || []).push(a);
  }
  const el = document.getElementById('workflow-list');
  el.innerHTML = state.workflows.map(w => {
    const actives = activeByScript[w.id] || [];
    const running = actives.length > 0;
    const runningBadge = running
      ? `<span class="badge" style="background:#16a34a;color:#fff">🔴 运行中 × ${actives.length}</span>`
      : '';
    const stopBtns = actives.map(a =>
      `<button class="btn btn-sm btn-danger" onclick="stopWorkflow('${escapeHtml(a.robot_id)}', ${w.id})" title="停止在 ${escapeHtml(a.robot_id)} 上的流程 (步骤 ${a.current_step}/${a.total_steps})">⏹ 停止 ${escapeHtml(a.robot_id)}</button>`
    ).join('');
    return `
    <div class="card-item">
      <div class="card-item-info">
        <div class="card-item-title">${escapeHtml(w.name)} ${w.enabled ? '' : '<span class="badge badge-muted">已禁用</span>'} ${runningBadge}</div>
        <div class="card-item-subtitle">触发词：${escapeHtml(w.trigger_word)} | 专场：${escapeHtml(w.special_name || '全部')} | 步骤：${w.step_count}</div>
      </div>
      <div class="card-item-actions">
        ${stopBtns}
        <button class="btn btn-sm btn-success" onclick="runWorkflow(${w.id})" title="手动执行">▶ 执行</button>
        <button class="btn btn-sm" onclick="editWorkflow(${w.id})">编辑</button>
        <button class="btn btn-sm ${w.enabled ? '' : 'btn-success'}" onclick="toggleWorkflow(${w.id}, ${w.enabled ? 0 : 1})">${w.enabled ? '禁用' : '启用'}</button>
        <button class="btn btn-sm btn-danger" onclick="deleteWorkflow(${w.id})">删除</button>
      </div>
    </div>
  `;
  }).join('') || '<div class="panel">暂无流程</div>';
}

async function stopWorkflow(robotId, scriptId) {
  if (!confirm(`确认停止机器人 [${robotId}] 正在执行的流程？`)) return;
  try {
    const result = await api('/api/active-workflows/stop', {
      method: 'POST',
      body: JSON.stringify({ robot_id: robotId }),
    });
    if (result.status === 'ok') {
      showToast(result.message || '流程已停止', 'ok');
    } else {
      showToast(result.message || '停止失败', 'error');
    }
    await loadWorkflows();
  } catch (e) {
    showToast('停止失败: ' + e.message, 'error');
  }
}

// ============================================================
// 图形化拖拽流程编辑器
// ============================================================

// hasCallback=true 的步骤: 后端"不补齐步骤时长", 步骤真正等 webhook/WS 回调再走下一步
//   与 app/engine/flow_executor.py._NO_PAD_TYPES 严格一致, 改动时两边同步
const STEP_TYPES = {
  // 豹小秘 mini (WebSocket)
  navigate:       { emoji: '🗺️', label: '导航到点位', hasCallback: true  },
  tts:            { emoji: '🎤', label: '语音播报',   hasCallback: false },  // fire-and-forget
  show:           { emoji: '📺', label: '屏幕展示',   hasCallback: false },
  listen:         { emoji: '👂', label: '开始收音',   hasCallback: true  },
  // 中控 / 展项
  resource_play:  { emoji: '▶️', label: '播放资源',   hasCallback: false },
  switch_special: { emoji: '🎛️', label: '切换专场',   hasCallback: false },
  group_command:  { emoji: '⚡', label: '组策略',     hasCallback: false },
  equipment:      { emoji: '🔌', label: '设备控制',   hasCallback: false },
  custom_request: { emoji: '🌐', label: '自定义请求', hasCallback: false },
  digital_human:  { emoji: '🤖', label: '数字人控制', hasCallback: false },
  pad_active:     { emoji: '📱', label: '激活展项',   hasCallback: false },
  // 通用
  delay:          { emoji: '⏱️', label: '延时等待',   hasCallback: false },
  end:            { emoji: '🏁', label: '结束',       hasCallback: false },
  // 宇树机器人 (HTTP 网关) — 4 个长动作等 webhook, 其余同步返回
  unitree_tts:         { emoji: '🎙️', label: '宇树·语音播报',  hasCallback: true  },
  unitree_tts_stop:    { emoji: '🔇', label: '宇树·打断播报',  hasCallback: false },
  unitree_volume:      { emoji: '🔊', label: '宇树·设置音量',  hasCallback: false },
  unitree_motion:      { emoji: '🤸', label: '宇树·执行动作',  hasCallback: true  },
  unitree_dance:       { emoji: '💃', label: '宇树·跳舞',      hasCallback: true  },
  unitree_dance_stop:  { emoji: '⏹️', label: '宇树·停舞',      hasCallback: false },
  unitree_map_apply:   { emoji: '🗺️', label: '宇树·切换地图',  hasCallback: false },
  unitree_nav:         { emoji: '📍', label: '宇树·导航到点位', hasCallback: true  },
  unitree_nav_control: { emoji: '⏯️', label: '宇树·导航控制',  hasCallback: false },
  unitree_asr:         { emoji: '🎧', label: '宇树·语音识别',  hasCallback: false },
  unitree_face:        { emoji: '🙂', label: '宇树·人脸识别',  hasCallback: false },
};

let dragState = { dragType: null, dragIdx: null };

function showCreateWorkflow() {
  state.editingWorkflowId = null;
  document.getElementById('workflow-editor').style.display = 'block';
  document.getElementById('wf-editor-title').textContent = '✏️ 新建流程';
  document.getElementById('wf-name').value = '';
  document.getElementById('wf-trigger').value = '';
  document.getElementById('wf-desc').value = '';
  fillSelect('wf-special', state.refs.specials, { allowEmpty: true, emptyLabel: '全部专场通用' });
  document.getElementById('wf-special').value = '';
  document.getElementById('wf-steps').innerHTML = '';
  initDragAndDrop();
}

function cancelWorkflowEdit() {
  document.getElementById('workflow-editor').style.display = 'none';
}

function initDragAndDrop() {
  // 积木面板拖拽
  document.querySelectorAll('.wf-block-palette .block-item').forEach(block => {
    block.ondragstart = (e) => {
      dragState.dragType = block.dataset.type;
      dragState.dragIdx = null;
      e.dataTransfer.effectAllowed = 'copy';
      e.dataTransfer.setData('text/plain', block.dataset.type);
    };
  });

  // 画布 drop zone
  const dropZone = document.getElementById('wf-drop-zone');
  const canvas = document.getElementById('wf-canvas');

  canvas.ondragover = (e) => { e.preventDefault(); e.dataTransfer.dropEffect = dragState.dragType ? 'copy' : 'move'; };
  dropZone.ondragover = (e) => { e.preventDefault(); dropZone.classList.add('drag-active'); };
  dropZone.ondragleave = () => dropZone.classList.remove('drag-active');
  dropZone.ondrop = (e) => {
    e.preventDefault();
    dropZone.classList.remove('drag-active');
    if (dragState.dragType) {
      addCanvasStep(dragState.dragType);
      dragState.dragType = null;
    }
  };
}

function addCanvasStep(type, config = null, waitType = 'callback', waitTimeout = 30, description = '') {
  const container = document.getElementById('wf-steps');
  const info = STEP_TYPES[type] || { emoji: '❓', label: type };
  const idx = container.querySelectorAll('.canvas-step').length;

  // 连线（非首个步骤前加）
  if (idx > 0) {
    const conn = document.createElement('div');
    conn.className = 'step-connector';
    container.appendChild(conn);
  }

  const div = document.createElement('div');
  div.className = `canvas-step block-${type}`;
  div.draggable = true;
  div.dataset.type = type;
  div.dataset.stepId = `step-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;

  const cbBadge = info.hasCallback
    ? '<span class="step-cb-badge" title="等待机器人回调后才进入下一步">⏳ 等待回调</span>'
    : '';
  div.innerHTML = `
    <div class="step-label">
      <span class="step-number">${idx + 1}</span>
      <span class="step-title">${info.emoji} ${escapeHtml(info.label)}</span>
      ${cbBadge}
    </div>
    <button class="step-delete" onclick="removeCanvasStep(this)" title="删除">✕</button>
    <div class="step-config-panel" style="display:none"></div>
  `;

  // 点击展开/收起配置
  div.querySelector('.step-label').onclick = () => toggleStepConfig(div, type);

  // 画布内拖拽排序
  div.ondragstart = (e) => {
    dragState.dragType = null;
    dragState.dragIdx = getStepIndex(div);
    div.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
  };
  div.ondragend = () => { div.classList.remove('dragging'); dragState.dragIdx = null; };
  div.ondragover = (e) => {
    e.preventDefault();
    if (dragState.dragIdx !== null) {
      div.classList.add('drag-over');
    }
  };
  div.ondragleave = () => div.classList.remove('drag-over');
  div.ondrop = (e) => {
    e.preventDefault();
    div.classList.remove('drag-over');
    if (dragState.dragIdx !== null) {
      const targetIdx = getStepIndex(div);
      if (dragState.dragIdx !== targetIdx) {
        reorderSteps(dragState.dragIdx, targetIdx);
      }
      dragState.dragIdx = null;
    } else if (dragState.dragType) {
      // 从积木面板拖到某个步骤上 → 插入到其前面
      const targetIdx = getStepIndex(div);
      addCanvasStep(dragState.dragType);
      const allSteps = getCanvasSteps();
      reorderSteps(allSteps.length - 1, targetIdx);
      dragState.dragType = null;
    }
  };

  container.appendChild(div);

  // 填充配置
  buildStepConfigPanel(div, config, waitType, waitTimeout, description);
}

function getStepIndex(stepEl) {
  return Array.from(document.querySelectorAll('#wf-steps .canvas-step')).indexOf(stepEl);
}

function getCanvasSteps() {
  return Array.from(document.querySelectorAll('#wf-steps .canvas-step'));
}

function removeCanvasStep(btn) {
  const step = btn.closest('.canvas-step');
  // 也删除前面的连线
  if (step.previousElementSibling && step.previousElementSibling.classList.contains('step-connector')) {
    step.previousElementSibling.remove();
  } else if (step.nextElementSibling && step.nextElementSibling.classList.contains('step-connector')) {
    step.nextElementSibling.remove();
  }
  step.remove();
  renumberSteps();
}

function renumberSteps() {
  document.querySelectorAll('#wf-steps .canvas-step').forEach((el, idx) => {
    const num = el.querySelector('.step-number');
    if (num) num.textContent = idx + 1;
  });
}

function reorderSteps(fromIdx, toIdx) {
  const container = document.getElementById('wf-steps');
  // 收集所有步骤数据
  const stepsData = collectAllStepsData();
  const moved = stepsData.splice(fromIdx, 1)[0];
  stepsData.splice(toIdx, 0, moved);
  // 重建画布
  container.innerHTML = '';
  stepsData.forEach(s => addCanvasStep(s.step_type, s.config, s.wait_type, s.wait_timeout, s.description));
}

function toggleStepConfig(stepEl, type) {
  const panel = stepEl.querySelector('.step-config-panel');
  if (!panel) return;
  const wasHidden = panel.style.display === 'none';
  panel.style.display = wasHidden ? 'block' : 'none';

  // 展开时检查资源级联是否需要加载
  if (wasHidden && (type === 'resource_play' || stepEl.dataset.type === 'resource_play')) {
    const spEl = panel.querySelector('.cfg-special');
    const exEl = panel.querySelector('.cfg-exhibit');
    const rsEl = panel.querySelector('.cfg-resource');
    // 有专场+展项但资源列表为空 → 重新加载
    if (spEl && spEl.value && exEl && exEl.value && rsEl && rsEl.options.length <= 1) {
      loadResourceList(exEl);
    }
  }
}

function buildStepConfigPanel(stepEl, config, waitType, waitTimeout, description) {
  const type = stepEl.dataset.type;
  const panel = stepEl.querySelector('.step-config-panel');
  if (!panel) return;

  const specialOpts = optionsHtml(state.refs.specials);
  const exhibitOpts = optionsHtml(state.refs.exhibits);
  const equipmentOpts = optionsHtml(state.refs.equipments);
  const groupOpts = optionsHtml(state.refs.group_commands);
  // 宇树机器人下拉选项
  const u = state.unitree || { motions: [], dances: [], maps: [] };
  const unitreeMotionOpts = optionsHtml(
    (u.motions || []).map(m => ({ id: m.motion_id, name: `${m.motion_id} · ${m.name}` }))
  );
  const unitreeDanceOpts = optionsHtml(
    (u.dances || []).map(d => ({
      id: d.dance_id,
      name: `${d.dance_id} · ${d.name}${d.duration_seconds ? ` (${d.duration_seconds}s)` : ''}`,
    }))
  );
  const unitreeMapOpts = optionsHtml(
    (u.maps || []).map(m => ({
      id: m.map_id,
      name: `${m.display_name || m.map_name}${Number(m.is_current) === 1 ? ' · 当前' : ''}`,
    }))
  );
  // 导航下拉: 有绑定的显示"展项名 (点位名)"，无绑定的显示纯点位名，确保所有导航点位都可选
  const bindMap = {};
  (state.bindings || []).forEach(b => { bindMap[b.position_id] = b; });
  const navItems = (state.refs.positions || []).map(p => {
    const bind = bindMap[p.position_id];
    const label = bind && bind.target_name ? (bind.target_name + " (" + p.name + ")") : p.name;
    return { position_id: p.position_id, name: label };
  });
  const navigateOpts = optionsHtml(navItems, 'position_id', 'name');
  const positionOpts = optionsHtml(state.refs.positions, 'position_id', 'name');

  const configHtmlMap = {
    navigate: `<div class="form-group"><label>展项点位</label><select class="cfg-pointId select-full" onchange="updatePointName(this)">${navigateOpts}</select></div>`,
    tts: `<div class="form-group"><label>播报内容</label><input class="cfg-text" type="text" placeholder="请输入播报文本"></div>`,
    show: `<div class="form-group"><label>展示类型</label>
        <select class="cfg-action" onchange="toggleShowFields(this)">
          <option value="text">纯文字</option>
          <option value="list">列表</option>
          <option value="card">信息卡片</option>
          <option value="clear">清空屏幕</option>
        </select></div>
        <div class="form-group show-list-fields" style="display:none"><label>列表类型</label>
          <select class="cfg-listType">
            <option value="terminal">终端/展项</option>
            <option value="special">专场</option>
            <option value="resource">资源</option>
          </select></div>
        <div class="form-group show-title-fields"><label>标题</label><input class="cfg-title" type="text" placeholder="展示标题"></div>
        <div class="form-group show-text-fields"><label>文字内容</label><input class="cfg-text" type="text" placeholder="展示内容"></div>`,
    resource_play: `
      <div class="form-group"><label>专场</label><select class="cfg-special select-full" onchange="loadResourceExhibits(this)">${specialOpts}</select></div>
      <div class="form-group"><label>展项</label><select class="cfg-exhibit select-full" onchange="loadResourceList(this)">${exhibitOpts}</select></div>
      <div class="form-group"><label>资源</label><select class="cfg-resource select-full" onchange="loadNarrationForStep(this)">${optionsHtml([])}</select></div>
      <div class="form-group"><label>讲解执行端</label>
        <select class="cfg-narration_target select-full">
          <option value="screen">仅中控屏 (默认, 不发讲解词到机器人)</option>
          <option value="baoxiaomi">中控屏 + 豹小秘 TTS</option>
          <option value="digital_human">中控屏 + 数字人 TTS</option>
          <option value="unitree">中控屏 + 宇树 TTS</option>
        </select>
        <div style="font-size:11px;color:#94a3b8;margin-top:2px">控制资源播放时讲解词由谁朗读; 中控屏 (展项屏幕) 始终会播</div>
      </div>
      <div class="narration-area" style="margin-top:8px;display:none">
        <label style="font-size:12px;color:#94a3b8">🎙️ 讲解词 <span class="narr-status"></span></label>
        <textarea class="cfg-narration" rows="3" style="width:100%;font-size:12px;border:1px solid #334155;border-radius:4px;background:#1e293b;color:#e2e8f0;padding:6px;margin-top:4px" placeholder="选择资源后自动加载讲解词，可手动编辑"></textarea>
        <div style="display:flex;gap:6px;margin-top:4px">
          <button type="button" class="btn btn-sm btn-primary" onclick="saveStepNarration(this)">💾 保存讲解词</button>
          <button type="button" class="btn btn-sm" onclick="generateStepNarration(this)">🤖 AI生成</button>
        </div>
      </div>`,
    switch_special: `<div class="form-group"><label>目标专场</label><select class="cfg-special select-full">${specialOpts}</select></div>`,
    group_command: `
      <div class="form-group"><label>组策略</label><select class="cfg-group select-full">${groupOpts}</select></div>
      <div class="form-group"><label>动作</label><select class="cfg-action select-full"><option value="on">开启</option><option value="off">关闭</option></select></div>`,
    equipment: `
      <div class="form-group"><label>设备</label><select class="cfg-equipment select-full">${equipmentOpts}</select></div>
      <div class="form-group"><label>动作</label><select class="cfg-action select-full"><option value="on">开启</option><option value="off">关闭</option></select></div>`,
    delay: `<div class="hint">纯延时步骤, 时长由下方"步骤时长"字段控制.</div>`,
    listen: `<div class="hint">收音时长由下方"步骤时长"字段控制.</div>`,
    end: `<div class="hint">流程结束，无需配置。</div>`,
    custom_request: `
      <div class="form-group"><label>协议</label>
        <select class="cfg-protocol select-full" onchange="toggleRequestFields(this)">
          <option value="tcp">TCP</option>
          <option value="udp">UDP</option>
          <option value="http_get">HTTP GET</option>
          <option value="http_post">HTTP POST</option>
        </select>
      </div>
      <div class="form-group"><label>目标地址</label>
        <input class="cfg-host" type="text" placeholder="IP或域名，如 192.168.16.18">
      </div>
      <div class="form-group cfg-port-group"><label>端口</label>
        <input class="cfg-port" type="number" placeholder="如 8989">
      </div>
      <div class="form-group cfg-url-group" style="display:none"><label>URL路径</label>
        <input class="cfg-url" type="text" placeholder="/api/xxx">
      </div>
      <div class="form-group"><label>发送内容</label>
        <input class="cfg-body" type="text" placeholder="TCP/UDP: 纯文本指令; HTTP POST: JSON body">
      </div>
      <div class="form-group"><label>超时（秒）</label>
        <input class="cfg-timeout" type="number" value="10">
      </div>`,
    digital_human: `
      <div class="form-group"><label>动作</label>
        <select class="cfg-action select-full" onchange="toggleDhFields(this)">
          <option value="Enter">进入 (Enter)</option>
          <option value="Leave">离开 (Leave)</option>
          <option value="Show">显示 (Show)</option>
          <option value="Hide">隐藏 (Hide)</option>
          <option value="AllShow">全部显示 (AllShow)</option>
          <option value="AllHide">全部隐藏 (AllHide)</option>
          <option value="Speak">讲解 (Speak)</option>
        </select>
      </div>
      <div class="form-group"><label>展项</label>
        <select class="cfg-exhibitionID select-full">${exhibitOpts}</select>
        <p class="hint">数字人与展项绑定，同一展项的数字人共享控制。动作=讲解时可不选。</p>
      </div>
      <div class="form-group cfg-speak-fields" style="display:none"><label>讲解文本</label>
        <textarea class="cfg-text" rows="3" style="width:100%;font-size:12px;border:1px solid #334155;border-radius:4px;background:#1e293b;color:#e2e8f0;padding:6px" placeholder="数字人要播报的文本内容"></textarea>
      </div>
      <div class="form-group cfg-speak-fields" style="display:none"><label>前置延时（秒）</label>
        <input class="cfg-resetDelay type="number" type="number" value="10" placeholder="复位后等待时间，默认10秒">
      </div>`,
    pad_active: `
      <div class="form-group"><label>展项</label>
        <select class="cfg-exhibitionID select-full">${exhibitOpts}</select>
        <p class="hint">通知Pad系统激活该展项的数字人，在Enter前调用。</p>
      </div>`,
    // ============ 宇树机器人 (HTTP 网关) ============
    unitree_tts: `
      <div class="form-group"><label>播报文本</label>
        <input class="cfg-text" type="text" maxlength="200" placeholder="1~200字, 超长请拆成多段">
        <p class="hint">单次最大 200 字; 长文本请拆多个步骤串联, 每步步骤时长覆盖播放时间。</p>
      </div>`,
    unitree_tts_stop: `<div class="hint">立即打断宇树当前的TTS播报。</div>`,
    unitree_volume: `
      <div class="form-group"><label>音量 (0~100)</label>
        <input class="cfg-audio_volume" type="number" min="0" max="100" value="60">
      </div>`,
    unitree_motion: `
      <div class="form-group"><label>动作</label>
        <select class="cfg-motion_id select-full">${unitreeMotionOpts}</select>
        <p class="hint">动作库由定时任务从网关拉取; 如为空请点"系统设置 → 立即同步"。</p>
      </div>`,
    unitree_dance: `
      <div class="form-group"><label>舞蹈</label>
        <select class="cfg-dance_id select-full">${unitreeDanceOpts}</select>
        <p class="hint">舞蹈不可打断, 步骤时长建议≥舞蹈 duration。</p>
      </div>`,
    unitree_dance_stop: `<div class="hint">让舞蹈回到默认姿态 (下发 F1F2 实现, 非硬中断)。</div>`,
    unitree_map_apply: `
      <div class="form-group"><label>工作地图</label>
        <select class="cfg-map_id select-full" onchange="onUnitreeMapChange(this)">${unitreeMapOpts}</select>
        <p class="hint">切换后该地图成为导航基准; 副作用: 开启避障导航。</p>
      </div>`,
    unitree_nav: `
      <div class="form-group"><label>工作地图</label>
        <select class="cfg-map_id select-full" onchange="onUnitreeNavMapChange(this)">${unitreeMapOpts}</select>
      </div>
      <div class="form-group"><label>目标点位</label>
        <select class="cfg-point_id select-full"><option value="">请先选地图</option></select>
        <p class="hint">若未传 map_id 会兜底用当前工作地图。</p>
      </div>`,
    unitree_nav_control: `
      <div class="form-group"><label>动作</label>
        <select class="cfg-action select-full">
          <option value="cancel">取消当前导航</option>
          <option value="pause">暂停</option>
          <option value="resume">恢复</option>
        </select>
      </div>
      <div class="form-group"><label>任务ID (可选)</label>
        <input class="cfg-task_id" type="text" placeholder="留空用最近一次 unitree_nav 的 task_id">
      </div>`,
    unitree_asr: `
      <div class="form-group"><label>动作</label>
        <select class="cfg-action select-full">
          <option value="start">启动识别 (持续监听)</option>
          <option value="stop">停止</option>
          <option value="listen_once">等一条识别 (需先启动)</option>
        </select>
        <p class="hint">首次启动加载 funASR 模型需 5~10 秒; 结果通过 webhook 推回。<br>
        「等一条识别」订阅下一条 asr 文本 (或超时), 期间常规语音应答挂起 — 要求 ASR 已启动。
        识别到的文本存入 execution._unitree_last_asr_text 供后续步骤引用。</p>
      </div>
      <div class="form-group"><label>收音时长 (仅 start 生效, 秒, 0=持续)</label>
        <input class="cfg-listen_duration" type="number" min="0" max="600" value="0">
        <p class="hint">&gt;0 时 V5 侧计时到点自动停, 让 APK 回到唤醒词模式。</p>
      </div>
      <div class="form-group"><label>等待时长 (仅 listen_once 生效, 秒)</label>
        <input class="cfg-duration" type="number" min="3" max="60" value="10">
      </div>`,
    unitree_face: `
      <div class="form-group"><label>动作</label>
        <select class="cfg-action select-full">
          <option value="start">启动人脸识别</option>
          <option value="stop">停止</option>
        </select>
      </div>
      <div class="form-group"><label>group_id (可选)</label>
        <input class="cfg-group_id" type="number" placeholder="留空用网关默认 100092">
        <p class="hint">累计 3 次识别后网关会自动停; 启动会占用摄像头 RTMP 推流。</p>
      </div>`,
  };

  // 步骤时长语义分两类:
  //   hasCallback=true (等回调): 字段是"最长等待", 回调提前到就立刻进入下一步, 超时仍推进
  //   hasCallback=false (补齐): 字段是"步骤时长", 动作立即发出, 整步用满该时长
  const typeInfo = STEP_TYPES[type] || {};
  const isCb = !!typeInfo.hasCallback;
  const waitLabel = isCb ? '最长等待（秒）' : '步骤时长（秒）';
  const waitDefault = isCb ? (type === 'unitree_dance' ? 120 : 60) : 30;
  const waitHint = isCb
    ? '⏳ 此步骤等待机器人完成回调; 回调到达立即进入下一步, 超时则按已完成推进下一步'
    : '从步骤开始计时; 动作立即发出, 整步用满该时长后进入下一步';

  const waitHtml = `
    <hr style="border-color:rgba(255,255,255,0.2);margin:10px 0">
    <div class="form-group"><label>${waitLabel}</label>
      <input class="cfg-wait_timeout" type="number" value="${waitDefault}">
      <p class="hint">${waitHint}</p>
    </div>
    <div class="form-group"><label>步骤说明</label><input class="cfg-description" type="text" placeholder="给管理员看的说明"></div>
  `;

  panel.innerHTML = (configHtmlMap[type] || '') + waitHtml;

  // 填充已有配置 — config key → CSS class 映射
  if (config) {
    // 特殊映射: config字段名 → 前端 cfg-xxx class
    const keyMap = {
      pointId: 'position', pointName: 'position',
      commandName: 'group',
      exhibitId: 'exhibit',
      resourceId: 'resource', resourceName: 'resource',
      text: 'text',
      resetDelay: 'resetDelay',
    };
    for (const [key, value] of Object.entries(config)) {
      const cssKey = keyMap[key] || key;
      const el = panel.querySelector(`.cfg-${cssKey}`);
      if (el && value) el.value = value;
    }
  }
  // 数字人: 如果动作是 Speak，显示讲解文本框
  if (type === 'digital_human' && config && config.action === 'Speak') {
    const actEl = panel.querySelector('.cfg-action');
    if (actEl) toggleDhFields(actEl);
  }

  const wtoEl = panel.querySelector('.cfg-wait_timeout');
  // 旧流程可能用 delay 步骤的 cfg-seconds (config.seconds) 作为时长, 做回填兼容
  if (wtoEl) {
    wtoEl.value = waitTimeout || (config && config.seconds) || 30;
  }
  const descEl = panel.querySelector('.cfg-description');
  if (descEl) descEl.value = description || '';

  // 导航: pointId 回填到 cfg-pointId 下拉框
  if (type === 'navigate' && config) {
    const navEl = panel.querySelector('.cfg-pointId');
    const pid = config.pointId || config.position || '';
    if (navEl && pid) {
      navEl.value = pid;
      updatePointName(navEl);
    }
  }

  // 组策略: commandName → 匹配名称
  if (type === 'group_command' && config) {
    const gEl = panel.querySelector('.cfg-group');
    const name = config.commandName || config.group || '';
    if (gEl && name) {
      // 按名称匹配 option
      const opt = Array.from(gEl.options).find(o => o.text === name || o.value === name);
      if (opt) gEl.value = opt.value;
    }
  }

  // 播放资源级联: 专场 → 展项 → 资源 → 讲解词 (编辑已有流程点时也要链式加载, 否则讲解词框不显示)
  if (type === 'resource_play') {
    const sp = config ? (config.special || '') : '';
    const ex = config ? (config.exhibit || config.exhibitId || '') : '';
    const rs = config ? (config.resource || config.resourceId || '') : '';
    const spEl = panel.querySelector('.cfg-special');
    if (spEl && sp) {
      spEl.value = sp;
      loadResourceExhibits(spEl, ex, rs).then(() => {
        const rsEl = panel.querySelector('.cfg-resource');
        if (rsEl && rsEl.value) loadNarrationForStep(rsEl);
      });
    }
  }

  // 宇树·导航: map_id → 点位级联回填
  if (type === 'unitree_nav' && config && config.map_id) {
    const mapEl = panel.querySelector('.cfg-map_id');
    if (mapEl) {
      mapEl.value = config.map_id;
      onUnitreeNavMapChange(mapEl, config.point_id || config.pointId);
    }
  }
}

// 宇树·切换地图下拉变化 — 目前只是选中, 无需联动
function onUnitreeMapChange(selectEl) {
  /* reserved for future */
}

// 宇树·导航下拉变化 — 按 map_id 加载点位
async function onUnitreeNavMapChange(selectEl, presetPointId) {
  const panel = selectEl.closest('.step-config-panel');
  const pointSelect = panel.querySelector('.cfg-point_id');
  if (!pointSelect) return;
  const mapId = selectEl.value;
  if (!mapId) {
    pointSelect.innerHTML = '<option value="">请先选地图</option>';
    return;
  }
  const points = await loadUnitreeMapPoints(mapId);
  pointSelect.innerHTML = optionsHtml(
    points.map(p => ({ id: p.point_id, name: `${p.point_id} · ${p.point_name || '(未命名)'}` }))
  );
  if (presetPointId !== undefined && presetPointId !== null && String(presetPointId) !== '') {
    pointSelect.value = String(presetPointId);
  }
}

// 播放资源级联：专场 → 展项
async function loadResourceExhibits(selectEl, presetExhibit, presetResource) {
  const panel = selectEl.closest('.step-config-panel');
  const exhibitSelect = panel.querySelector('.cfg-exhibit');
  const resourceSelect = panel.querySelector('.cfg-resource');
  if (exhibitSelect) exhibitSelect.innerHTML = optionsHtml(state.refs.exhibits);
  if (resourceSelect) resourceSelect.innerHTML = optionsHtml([]);
  if (presetExhibit && exhibitSelect) {
    exhibitSelect.value = presetExhibit;
    await loadResourceList(exhibitSelect, presetResource);
  }
}

// 播放资源级联：展项 → 资源列表
async function loadResourceList(selectEl, presetResource) {
  const panel = selectEl.closest('.step-config-panel');
  const specialId = panel.querySelector('.cfg-special')?.value;
  const exhibitId = selectEl.value;
  const resourceSelect = panel.querySelector('.cfg-resource');
  if (!specialId || !exhibitId || !resourceSelect) {
    if (resourceSelect) resourceSelect.innerHTML = optionsHtml([]);
    return;
  }
  try {
    const resp = await api(`/api/ref/resources?special_id=${specialId}&exhibit_id=${exhibitId}`);
    const items = resp.data || [];
    resourceSelect.innerHTML = optionsHtml(items);
    if (presetResource) {
      resourceSelect.value = presetResource;
    } else if (items.length > 0) {
      resourceSelect.value = items[0].id;
    }
  } catch (e) {
    showToast(`加载资源失败: ${e.message}`, 'error');
  }
}

function toggleShowFields(selectEl) {
  const panel = selectEl.closest('.step-config-panel');
  const action = selectEl.value;
  const listFields = panel.querySelector('.show-list-fields');
  const titleFields = panel.querySelector('.show-title-fields');
  const textFields = panel.querySelector('.show-text-fields');
  if (listFields) listFields.style.display = (action === 'list') ? '' : 'none';
  if (titleFields) titleFields.style.display = (action === 'clear') ? 'none' : '';
  if (textFields) textFields.style.display = (action === 'text') ? '' : 'none';
}

function toggleRequestFields(selectEl) {
  const panel = selectEl.closest('.step-config-panel');
  const protocol = selectEl.value;
  const isHttp = protocol.startsWith('http');
  const portGroup = panel.querySelector('.cfg-port-group');
  const urlGroup = panel.querySelector('.cfg-url-group');
  if (portGroup) portGroup.style.display = isHttp ? 'none' : '';
  if (urlGroup) urlGroup.style.display = isHttp ? '' : 'none';
  const hostInput = panel.querySelector('.cfg-host');
  if (hostInput) hostInput.placeholder = isHttp ? '如 http://192.168.18.65:8866' : 'IP或域名，如 192.168.16.18';
}

function toggleDhFields(selectEl) {
  const panel = selectEl.closest('.step-config-panel');
  const isSpeak = selectEl.value === 'Speak';
  panel.querySelectorAll('.cfg-speak-fields').forEach(el => el.style.display = isSpeak ? '' : 'none');
}

function collectStepConfig(stepEl) {
  const config = {};
  stepEl.querySelectorAll('.step-config-panel [class*="cfg-"]').forEach(el => {
    const cls = Array.from(el.classList).find(x => x.startsWith('cfg-'));
    if (!cls) return;
    const key = cls.replace('cfg-', '');
    if (['wait_type', 'wait_timeout', 'description'].includes(key)) return;
    config[key] = el.value;
  });
  return config;
}

function collectAllStepsData() {
  return getCanvasSteps().map(stepEl => ({
    step_type: stepEl.dataset.type,
    config: collectStepConfig(stepEl),
    // wait_type 已废弃, 后端仅用 wait_timeout 作为统一步骤时长. 保留空值以兼容旧列.
    wait_type: 'delay',
    wait_timeout: Number(stepEl.querySelector('.cfg-wait_timeout')?.value || 30),
    description: stepEl.querySelector('.cfg-description')?.value || '',
  }));
}

function updatePointName(selectEl) {
  const stepEl = selectEl.closest(".canvas-step");
  if (!stepEl) return;
  let hidden = stepEl.querySelector(".cfg-pointName");
  if (!hidden) {
    hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.className = "cfg-pointName";
    selectEl.parentElement.appendChild(hidden);
  }
  // pointName 必须存机器人地图上的纯点位名(如"岛台1")，不是下拉显示的拼接名(如"大岛台正面 (岛台1)")
  const pid = selectEl.value;
  const bind = (state.bindings || []).find(b => b.position_id === pid);
  // 从 bindings 取 position_name，或从 positions 兜底
  let realName = "";
  if (bind) {
    realName = bind.position_name || "";
  } else {
    const pos = (state.refs.positions || []).find(p => p.position_id === pid);
    if (pos) realName = pos.name || "";
  }
  hidden.value = realName;
}

function optionsHtml(items, valueKey = 'id', labelKey = 'name') {
  let html = '<option value="">请选择</option>';
  for (const item of items || []) {
    html += `<option value="${escapeHtml(item[valueKey] ?? '')}">${escapeHtml(item[labelKey] ?? '')}</option>`;
  }
  return html;
}

function safeParse(v) {
  try { return typeof v === 'string' ? JSON.parse(v || '{}') : (v || {}); } catch { return {}; }
}

async function saveWorkflow() {
  const name = document.getElementById('wf-name').value.trim();
  const trigger = document.getElementById('wf-trigger').value.trim();
  const desc = document.getElementById('wf-desc').value.trim();
  const specialId = document.getElementById('wf-special').value || null;
  if (!name || !trigger) return showToast('流程名称和触发词不能为空', 'error');

  try {
    let workflowId = state.editingWorkflowId;
    if (!workflowId) {
      const createResp = await api('/api/workflows', {
        method: 'POST',
        body: JSON.stringify({ name, trigger_word: trigger, description: desc, special_id: specialId ? Number(specialId) : null }),
      });
      workflowId = createResp.data.id;
    } else {
      await api(`/api/workflows/${workflowId}`, {
        method: 'PUT',
        body: JSON.stringify({ name, trigger_word: trigger, description: desc, special_id: specialId ? Number(specialId) : null }),
      });
    }

    const steps = collectAllStepsData().map(s => ({
      step_type: s.step_type,
      config: s.config,
      wait_type: s.wait_type,
      wait_timeout: s.wait_timeout,
      description: s.description,
    }));

    await api(`/api/workflows/${workflowId}/steps`, {
      method: 'PUT',
      body: JSON.stringify(steps),
    });

    showToast('流程已保存', 'ok');
    cancelWorkflowEdit();
    await loadWorkflows();
  } catch (e) {
    showToast(`保存失败: ${e.message}`, 'error');
  }
}

async function editWorkflow(id) {
  try {
    const resp = await api(`/api/workflows/${id}`);
    const w = resp.data;
    state.editingWorkflowId = id;
    document.getElementById('workflow-editor').style.display = 'block';
    document.getElementById('wf-editor-title').textContent = `✏️ 编辑流程：${w.name}`;
    document.getElementById('wf-name').value = w.name || '';
    document.getElementById('wf-trigger').value = w.trigger_word || '';
    document.getElementById('wf-desc').value = w.description || '';
    fillSelect('wf-special', state.refs.specials, { allowEmpty: true, emptyLabel: '全部专场通用' });
    document.getElementById('wf-special').value = w.special_id || '';
    document.getElementById('wf-steps').innerHTML = '';
    (w.steps || []).forEach(step => {
      const cfg = safeParse(step.config_json);
      addCanvasStep(step.step_type, cfg, step.wait_type, step.wait_timeout, step.description);
    });
    initDragAndDrop();
    // 资源级联 (special/exhibit/resource + 讲解词) 已在 buildStepConfigPanel 内链式加载
  } catch (e) {
    showToast(`加载流程失败: ${e.message}`, 'error');
  }
}

async function deleteWorkflow(id) {
  if (!confirm('确定删除该流程吗？')) return;
  try {
    await api(`/api/workflows/${id}`, { method: 'DELETE' });
    showToast('流程已删除', 'ok');
    await loadWorkflows();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function runWorkflow(id) {
  // 不再让用户挑机器人 — 后端按步骤类型自动识别 (unitree_* → 宇树, 否则豹小秘)
  // 跟语音触发流程的路径保持一致
  if (!confirm('确认执行该流程？')) return;
  try {
    const result = await api(`/api/workflows/${id}/run`, {
      method: 'POST',
      body: JSON.stringify({ robot_id: '' }),  // 空 → 后端自动推断
    });
    if (result.status === 'ok') {
      const rid = result.robot_id ? ` (机器人: ${result.robot_id})` : '';
      showToast((result.message || '流程已启动') + rid, 'ok');
    } else {
      showToast(result.message || '执行失败', 'error');
    }
  } catch (e) {
    showToast('执行失败: ' + e.message, 'error');
  }
}

async function toggleWorkflow(id, enabled) {
  try {
    await api(`/api/workflows/${id}`, { method: 'PUT', body: JSON.stringify({ enabled }) });
    showToast(enabled ? '流程已启用' : '流程已禁用', 'ok');
    await loadWorkflows();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

// ============================================================
// 点位绑定
// ============================================================
function updateBindTargetOptions() {
  const type = document.getElementById('bind-type').value;
  const items = type === 'area' ? state.refs.areas : state.refs.exhibits;
  fillSelect('bind-target', items);
}

function switchBindRobot() {
  const kind = document.getElementById('bind-robot').value;
  document.getElementById('bind-baoxiaomi-section').style.display = kind === 'unitree' ? 'none' : '';
  document.getElementById('bind-unitree-section').style.display    = kind === 'unitree' ? ''     : 'none';
  if (kind === 'unitree') initBindUnitreeSelects();
}
window.switchBindRobot = switchBindRobot;

function initBindUnitreeSelects() {
  const mapEl = document.getElementById('bind-unitree-map');
  if (!mapEl) return;
  const u = state.unitree || { maps: [] };
  mapEl.innerHTML = '<option value="">选择地图</option>' + (u.maps || []).map(x =>
    `<option value="${x.map_id}" ${Number(x.is_current) === 1 ? 'selected' : ''}>${escapeHtml(x.display_name || x.map_name || x.map_id)}${Number(x.is_current) === 1 ? ' · 当前' : ''}</option>`
  ).join('');
  // 如果有当前地图, 自动拉点位
  if (mapEl.value) onBindUnitreeMapChange(mapEl);
}

async function onBindUnitreeMapChange(selectEl) {
  const ptEl = document.getElementById('bind-unitree-point');
  if (!ptEl) return;
  const mapId = selectEl.value;
  if (!mapId) { ptEl.innerHTML = '<option value="">先选地图</option>'; return; }
  ptEl.innerHTML = '<option value="">加载中...</option>';
  const pts = await loadUnitreeMapPoints(mapId);
  ptEl.innerHTML = '<option value="">选择点位</option>' + (pts || []).map(p =>
    `<option value="${p.point_id}">${p.point_id} · ${escapeHtml(p.point_name || '(未命名)')}</option>`
  ).join('');
}
window.onBindUnitreeMapChange = onBindUnitreeMapChange;

async function createBinding() {
  const kind = document.getElementById('bind-robot').value;
  const bindType = document.getElementById('bind-type').value;
  const bindTargetId = Number(document.getElementById('bind-target').value || 0);
  if (!bindTargetId) return showToast('请选择绑定目标', 'error');

  try {
    if (kind === 'unitree') {
      const mapId = document.getElementById('bind-unitree-map').value;
      const pointId = Number(document.getElementById('bind-unitree-point').value || 0);
      if (!mapId || !pointId) return showToast('请选择地图和点位', 'error');
      const resp = await api('/api/system/unitree_point_bindings', {
        method: 'POST',
        body: JSON.stringify({ map_id: mapId, point_id: pointId, bind_type: bindType, bind_target_id: bindTargetId }),
      });
      if (resp && resp.ok === false) return showToast(resp.error || '保存失败', 'error');
    } else {
      const positionId = document.getElementById('bind-position').value;
      if (!positionId) return showToast('请选择点位', 'error');
      await api('/api/system/position_bindings', {
        method: 'POST',
        body: JSON.stringify({ position_id: positionId, bind_type: bindType, bind_target_id: bindTargetId }),
      });
    }
    showToast('绑定已保存', 'ok');
    await loadBindings();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function loadBindings() {
  // 顺手刷新活地图徽章 (无依赖, 失败不阻塞)
  if (typeof loadActiveMapBadge === 'function') loadActiveMapBadge();
  // 豹小秘
  const resp = await api('/api/ref/position_bindings');
  state.bindings = resp.data || [];
  const el = document.getElementById('bindings-list');
  el.innerHTML = state.bindings.map(b => `
    <div class="card-item">
      <div class="card-item-info">
        <div class="card-item-title">${escapeHtml(b.position_name || b.position_id)} → ${escapeHtml(b.target_name || '')}</div>
        <div class="card-item-subtitle">类型：${b.bind_type === 'area' ? '展区' : '展项'}</div>
      </div>
      <div class="card-item-actions">
        <button class="btn btn-sm btn-danger" onclick="deleteBinding(${b.id})">删除</button>
      </div>
    </div>
  `).join('') || '<div class="panel">暂无绑定</div>';

  // 宇树
  try {
    const ur = await api('/api/ref/unitree_point_bindings');
    const uList = ur.data || [];
    const uEl = document.getElementById('unitree-bindings-list');
    if (uEl) uEl.innerHTML = uList.map(b => `
      <div class="card-item">
        <div class="card-item-info">
          <div class="card-item-title">[${escapeHtml(b.map_name || b.map_id)} · 航点${b.point_id}${b.point_name ? ' ' + escapeHtml(b.point_name) : ''}] → ${escapeHtml(b.target_name || '')}</div>
          <div class="card-item-subtitle">类型：${b.bind_type === 'area' ? '展区' : '展项'}</div>
        </div>
        <div class="card-item-actions">
          <button class="btn btn-sm btn-danger" onclick="deleteUnitreeBinding(${b.id})">删除</button>
        </div>
      </div>
    `).join('') || '<div class="panel">暂无绑定</div>';
  } catch (_) {}
}

async function deleteBinding(id) {
  if (!confirm('确定删除这个绑定吗？')) return;
  await api(`/api/system/position_bindings/${id}`, { method: 'DELETE' });
  showToast('绑定已删除', 'ok');
  await loadBindings();
}

async function deleteUnitreeBinding(id) {
  if (!confirm('确定删除这个宇树绑定吗？')) return;
  await api(`/api/system/unitree_point_bindings/${id}`, { method: 'DELETE' });
  showToast('绑定已删除', 'ok');
  await loadBindings();
}
window.deleteUnitreeBinding = deleteUnitreeBinding;

// ============================================================
// 参观配置
// ============================================================
async function loadTourPoints() {
  const resp = await api('/api/ref/tour_points');
  state.tourPoints = resp.data || [];
  const el = document.getElementById('tourpoints-list');
  if (!state.tourPoints.length) {
    el.innerHTML = '<div class="panel">暂无参观点位，请在上方添加</div>';
    return;
  }
  el.innerHTML = state.tourPoints.map(t => `
    <div class="card-item">
      <div class="card-item-info">
        <div class="card-item-title">${t.enabled ? '✅' : '❌'} ${escapeHtml(t.position_name || t.position_id)} ${t.is_photo_point ? '<span class="badge badge-info">拍照点</span>' : ''} ${t.photo_suggestion_enabled ? '<span class="badge badge-success">主动拍照建议</span>' : ''}</div>
        <div class="card-item-subtitle">结束语：${escapeHtml(t.end_phrase || '默认')} | 返回：${escapeHtml(t.return_position_name || '接待点')} | 二维码：${escapeHtml(t.photo_download_base_url || '自动生成')}</div>
      </div>
      <div class="card-item-actions">
        <button class="btn btn-sm btn-warning" onclick="editTourPoint(${t.id})">编辑</button>
        <button class="btn btn-sm btn-danger" onclick="deleteTourPoint(${t.id})">删除</button>
      </div>
    </div>
  `).join('');
}

async function saveTourPoint() {
  const position_id = document.getElementById('tp-position').value;
  const enabled = document.getElementById('tp-enabled').value === '1';
  const end_phrase = document.getElementById('tp-end-phrase').value.trim();
  const return_position_id = document.getElementById('tp-return-pos').value;
  const is_photo_point = document.getElementById('tp-photo-point').value === '1';
  const photo_suggestion_enabled = document.getElementById('tp-photo-suggest').value === '1';
  const photo_suggestion_text = document.getElementById('tp-photo-suggest-text').value.trim();
  const photo_download_base_url = document.getElementById('tp-photo-url').value.trim();
  if (!position_id) return showToast('请选择点位', 'error');
  try {
    await api('/api/system/tour_points', {
      method: 'POST',
      body: JSON.stringify({
        position_id, enabled, end_phrase, return_position_id,
        is_photo_point, photo_suggestion_enabled,
        photo_suggestion_text, photo_download_base_url,
      }),
    });
    showToast('参观点位已保存', 'ok');
    await loadTourPoints();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

function editTourPoint(id) {
  const t = state.tourPoints.find(p => p.id === id);
  if (!t) return;
  document.getElementById('tp-position').value = t.position_id;
  document.getElementById('tp-enabled').value = t.enabled ? '1' : '0';
  document.getElementById('tp-end-phrase').value = t.end_phrase || '';
  document.getElementById('tp-return-pos').value = t.return_position_id || '';
  document.getElementById('tp-photo-point').value = t.is_photo_point ? '1' : '0';
  document.getElementById('tp-photo-suggest').value = t.photo_suggestion_enabled ? '1' : '0';
  document.getElementById('tp-photo-suggest-text').value = t.photo_suggestion_text || '';
  document.getElementById('tp-photo-url').value = t.photo_download_base_url || '';
}

async function deleteTourPoint(id) {
  if (!confirm('确定删除该参观点位配置吗？')) return;
  await api(`/api/system/tour_points/${id}`, { method: 'DELETE' });
  showToast('已删除', 'ok');
  await loadTourPoints();
}

// ============================================================
// 意图映射
// ============================================================
function updateIntentTargetOptions() {
  const type = document.getElementById('intent-cmd-type').value;
  let items = [];
  if (type === 'group_command') items = state.refs.group_commands;
  else if (type === 'equipment') items = state.refs.equipments;
  else if (type === 'special_switch') items = state.refs.specials;
  fillSelect('intent-target', items);
}

async function createIntentMapping() {
  const payload = {
    intent_name: document.getElementById('intent-name').value.trim(),
    display_name: document.getElementById('intent-display').value.trim(),
    command_type: document.getElementById('intent-cmd-type').value,
    command_target: document.getElementById('intent-target').value,
    command_action: document.getElementById('intent-action').value,
    reply_text: document.getElementById('intent-reply').value.trim(),
  };
  if (!payload.intent_name || !payload.display_name || !payload.command_target) {
    return showToast('请把意图映射填写完整', 'error');
  }
  await api('/api/system/intent_mappings', {
    method: 'POST', body: JSON.stringify(payload),
  });
  showToast('意图映射已保存', 'ok');
  await loadIntentMappings();
}

async function loadIntentMappings() {
  const resp = await api('/api/ref/intent_mappings');
  state.intents = resp.data || [];
  const el = document.getElementById('intents-list');
  el.innerHTML = state.intents.map(i => `
    <div class="card-item">
      <div class="card-item-info">
        <div class="card-item-title">${escapeHtml(i.display_name)} → ${escapeHtml(i.command_type)}:${escapeHtml(i.command_target)}</div>
        <div class="card-item-subtitle">意图：${escapeHtml(i.intent_name)} | 动作：${escapeHtml(i.command_action)} | 回复：${escapeHtml(i.reply_text || '-')}</div>
      </div>
      <div class="card-item-actions">
        <button class="btn btn-sm btn-danger" onclick="deleteIntentMapping(${i.id})">删除</button>
      </div>
    </div>
  `).join('') || '<div class="panel">暂无意图映射</div>';
}

async function deleteIntentMapping(id) {
  if (!confirm('确定删除该映射吗？')) return;
  await api(`/api/system/intent_mappings/${id}`, { method: 'DELETE' });
  showToast('意图映射已删除', 'ok');
  await loadIntentMappings();
}

// ============================================================
// 定时任务（先展示列表，创建可后续增强）
// ============================================================
async function loadSchedules() {
  const resp = await api('/api/schedules');
  state.schedules = resp.data || [];
  const el = document.getElementById('schedule-list');
  el.innerHTML = state.schedules.map(s => `
    <div class="card-item">
      <div class="card-item-info">
        <div class="card-item-title">${escapeHtml(s.name)} ${s.enabled ? '' : '<span class="badge badge-muted">已禁用</span>'}</div>
        <div class="card-item-subtitle">类型：${escapeHtml(s.schedule_type)} | 表达式：${escapeHtml(s.schedule_expr)} | 动作：${escapeHtml(s.action_type)}</div>
      </div>
      <div class="card-item-actions">
        <button class="btn btn-sm" onclick="toggleSchedule(${s.id}, ${s.enabled ? 0 : 1})">${s.enabled ? '禁用' : '启用'}</button>
        <button class="btn btn-sm btn-danger" onclick="deleteSchedule(${s.id})">删除</button>
      </div>
    </div>
  `).join('') || '<div class="panel">暂无定时任务</div>';
}

function showCreateSchedule() {
  document.getElementById('schedule-editor').style.display = 'block';
  document.getElementById('sched-editor-title').textContent = '➕ 新建定时任务';
  document.getElementById('sched-name').value = '';
  document.getElementById('sched-type').value = 'daily';
  document.getElementById('sched-time').value = '08:30';
  document.getElementById('sched-desc').value = '';
  toggleScheduleExpr();
  toggleScheduleAction();
}

function cancelScheduleEdit() {
  document.getElementById('schedule-editor').style.display = 'none';
}

function toggleScheduleExpr() {
  const type = document.getElementById('sched-type').value;
  document.getElementById('sched-daily-group').style.display = type === 'daily' ? '' : 'none';
  document.getElementById('sched-interval-group').style.display = type === 'interval' ? '' : 'none';
  document.getElementById('sched-cron-group').style.display = type === 'cron' ? '' : 'none';
}

function toggleScheduleAction() {
  const type = document.getElementById('sched-action-type').value;
  const container = document.getElementById('sched-action-config');
  const groupOpts = optionsHtml(state.refs.group_commands);
  const equipOpts = optionsHtml(state.refs.equipments);
  const specialOpts = optionsHtml(state.refs.specials);

  const htmlMap = {
    group_command: `
      <div class="form-group"><label>组策略</label><select id="sched-cfg-target" class="select-full">${groupOpts}</select></div>
      <div class="form-group"><label>动作</label><select id="sched-cfg-action" class="select-full">
        <option value="on">开启</option><option value="off">关闭</option></select></div>`,
    equipment: `
      <div class="form-group"><label>设备</label><select id="sched-cfg-target" class="select-full">${equipOpts}</select></div>
      <div class="form-group"><label>动作</label><select id="sched-cfg-action" class="select-full">
        <option value="on">开启</option><option value="off">关闭</option></select></div>`,
    special_switch: `
      <div class="form-group"><label>目标专场</label><select id="sched-cfg-target" class="select-full">${specialOpts}</select></div>`,
    custom_request: `
      <div class="form-group"><label>协议</label><select id="sched-cfg-protocol" class="select-full">
        <option value="tcp">TCP</option><option value="udp">UDP</option>
        <option value="http_get">HTTP GET</option><option value="http_post">HTTP POST</option></select></div>
      <div class="form-group"><label>目标地址</label><input id="sched-cfg-host" type="text" placeholder="IP或URL"></div>
      <div class="form-group"><label>端口</label><input id="sched-cfg-port" type="number" placeholder="端口号"></div>
      <div class="form-group"><label>发送内容</label><input id="sched-cfg-body" type="text" placeholder="指令或JSON"></div>`,
  };
  container.innerHTML = htmlMap[type] || '';
}

function buildScheduleExpr() {
  const type = document.getElementById('sched-type').value;
  if (type === 'daily') {
    const time = document.getElementById('sched-time').value || '08:30';
    const [h, m] = time.split(':');
    return `${parseInt(m)} ${parseInt(h)} * * *`;
  } else if (type === 'interval') {
    return document.getElementById('sched-interval').value || '60';
  } else if (type === 'cron') {
    return document.getElementById('sched-cron').value || '0 8 * * *';
  } else {
    return 'once';
  }
}

function collectScheduleActionConfig() {
  const type = document.getElementById('sched-action-type').value;
  if (type === 'custom_request') {
    return {
      protocol: document.getElementById('sched-cfg-protocol')?.value || 'tcp',
      host: document.getElementById('sched-cfg-host')?.value || '',
      port: document.getElementById('sched-cfg-port')?.value || '',
      body: document.getElementById('sched-cfg-body')?.value || '',
    };
  }
  return {
    target: document.getElementById('sched-cfg-target')?.value || '',
    action: document.getElementById('sched-cfg-action')?.value || 'on',
  };
}

async function saveSchedule() {
  const name = document.getElementById('sched-name').value.trim();
  if (!name) return showToast('请输入任务名称', 'error');

  try {
    await api('/api/schedules', {
      method: 'POST',
      body: JSON.stringify({
        name,
        schedule_type: document.getElementById('sched-type').value,
        schedule_expr: buildScheduleExpr(),
        action_type: document.getElementById('sched-action-type').value,
        action_config: {
          ...collectScheduleActionConfig(),
          weekday_filter: document.getElementById('sched-weekday').value,
        },
        description: document.getElementById('sched-desc').value.trim(),
      }),
    });
    showToast('定时任务已创建', 'ok');
    cancelScheduleEdit();
    await loadSchedules();
  } catch (e) {
    showToast(`创建失败: ${e.message}`, 'error');
  }
}

async function toggleSchedule(id, enabled) {
  await api(`/api/schedules/${id}`, { method: 'PUT', body: JSON.stringify({ enabled }) });
  showToast(enabled ? '任务已启用' : '任务已禁用', 'ok');
  await loadSchedules();
}

async function deleteSchedule(id) {
  if (!confirm('确定删除该任务吗？')) return;
  await api(`/api/schedules/${id}`, { method: 'DELETE' });
  showToast('任务已删除', 'ok');
  await loadSchedules();
}

// ============================================================
// 日志
// ============================================================
// 日志分类: 4 个 bucket → 多个 log_type 联动
const LOG_CATEGORIES = {
  all:      { label: '全部', types: null },
  interact: { label: '现场交互', types: ['robot_event','visitor','flow_step','llm_call','face_register','unitree_voice'] },
  control:  { label: '设备控制', types: ['robot_command','robot_conn','tcp_command','config_push','unitree_event'] },
  ops:      { label: '同步运维', types: ['sync','flow_start','flow_end'] },
  error:    { label: '异常',     types: null, badStatusOnly: true },
};

// 机器人筛选: 仅保留属于该机器人体系的 log_type. 与 LOG_CATEGORIES 正交相乘。
// flow_step 同时归两类: 由 _detectLogBot 看 detail 里的 step_type 判断 unitree_*.
const LOG_BOT_TYPES = {
  all: null,
  baoxiaomi: ['robot_event', 'robot_command', 'robot_conn', 'error', 'flow_step', 'flow_start', 'flow_end'],
  unitree:   ['unitree_event', 'unitree_voice', 'flow_step', 'flow_start', 'flow_end'],
};

state.logCat = state.logCat || 'all';
state.logBot = state.logBot || 'all';
state.logSinceMin = state.logSinceMin ?? 60;   // 默认近 1h
state.logLiveTimer = state.logLiveTimer || null;

function setLogCategory(cat) {
  if (!LOG_CATEGORIES[cat]) cat = 'all';
  state.logCat = cat;
  document.querySelectorAll('.log-cat').forEach(b => {
    b.classList.toggle('active', b.dataset.cat === cat);
  });
  loadLogs();
}

function setLogBot(bot) {
  if (!LOG_BOT_TYPES[bot]) bot = 'all';
  state.logBot = bot;
  document.querySelectorAll('.log-bot').forEach(b => {
    b.classList.toggle('active', b.dataset.bot === bot);
  });
  loadLogs();
}

// 单条日志归属哪个机器人
// 返回 'baoxiaomi' / 'unitree' / 'shared' (后面用作过滤 + 行内 badge)
function _detectLogBot(log) {
  const t = log.log_type || '';
  if (t === 'unitree_event' || t === 'unitree_voice') return 'unitree';
  if (t === 'robot_event' || t === 'robot_command' || t === 'robot_conn') return 'baoxiaomi';
  // error 来源是 robot_001 才算豹小秘
  if (t === 'error' && (log.source || '').startsWith('robot_')) return 'baoxiaomi';
  // flow_* 看 detail 里的 step_type
  if (t === 'flow_step' || t === 'flow_start' || t === 'flow_end') {
    const detail = log.detail || '';
    if (/unitree_/i.test(detail) || /宇树/.test(detail)) return 'unitree';
    return 'baoxiaomi';
  }
  return 'shared';
}

function setLogTime(min) {
  state.logSinceMin = Number(min) || 0;
  document.querySelectorAll('.log-time-btn').forEach(b => {
    b.classList.toggle('active', Number(b.dataset.min) === state.logSinceMin);
  });
  loadLogs();
  loadLogSummary();
}

function _startLogLive() {
  if (state.logLiveTimer) return;  // 防重复启动
  // 3s 一次刷一遍 (含摘要); 现场体感够实时, 还不至于把后端打爆
  state.logLiveTimer = setInterval(() => {
    loadLogs();
    loadLogSummary();
  }, 3000);
}

function _stopLogLive() {
  if (state.logLiveTimer) {
    clearInterval(state.logLiveTimer);
    state.logLiveTimer = null;
  }
}

function toggleLogLive() {
  const on = !!document.getElementById('log-live')?.checked;
  if (on) _startLogLive();
  else _stopLogLive();
}

// logs tab 自动启停: 进入 logs 自动开实时, 切走自动停 (省请求)
function _onLogsTabActive(active) {
  const cb = document.getElementById('log-live');
  if (!cb) return;
  if (active && cb.checked) {
    _startLogLive();
  } else {
    _stopLogLive();
  }
}

async function loadLogSummary() {
  try {
    const min = state.logSinceMin || 60;
    const r = await api(`/api/system/logs/summary?since_minutes=${min || 60}`);
    const el = document.getElementById('log-summary');
    if (!el) return;
    const bs = r.by_status || {};
    el.innerHTML = `
      <span class="log-stat ok"><b>${bs.ok || 0}</b> 正常</span>
      <span class="log-stat warning"><b>${bs.warning || 0}</b> 警告</span>
      <span class="log-stat error"><b>${bs.error || 0}</b> 错误</span>
      <span class="log-stat timeout"><b>${bs.timeout || 0}</b> 超时</span>
      <span class="log-stat total"><b>${r.total || 0}</b> 总条数 (近 ${min || 60} 分钟)</span>
    `;
  } catch (e) { /* 静默 */ }
}

async function loadLogs() {
  const cat = LOG_CATEGORIES[state.logCat] || LOG_CATEGORIES.all;
  const botTypes = LOG_BOT_TYPES[state.logBot] || null;
  const onlyBad = !!document.getElementById('log-only-bad')?.checked;
  const kw = (document.getElementById('log-search')?.value || '').trim();
  const params = new URLSearchParams();
  params.set('limit', '200');
  // 类别 ∩ 机器人 — 取交集; 任一为 null 就用另一边
  let types = cat.types;
  if (botTypes) {
    types = types ? types.filter(t => botTypes.includes(t)) : botTypes.slice();
    if (!types.length) {
      // 选了"宇树" + 类别"同步运维" 这种交集为空的组合, 直接渲染空表
      state.logs = []; state.logTotal = 0;
      state.logExpanded = state.logExpanded || new Set();
      state.logExpanded.clear();
      renderLogs();
      loadLogSummary();
      return;
    }
  }
  if (types) params.set('types', types.join(','));
  // 异常分类: 只看 status 为非 ok
  if (state.logCat === 'error' || onlyBad) {
    params.set('status', 'error,warning,timeout');
  }
  if (state.logSinceMin) params.set('since_minutes', String(state.logSinceMin));
  if (kw) params.set('q', kw);
  const resp = await api(`/api/system/logs?${params.toString()}`);
  let logs = resp.data || [];
  // flow_step 等共享类型由 _detectLogBot 看 detail 二次过滤
  if (state.logBot !== 'all') {
    logs = logs.filter(l => _detectLogBot(l) === state.logBot || _detectLogBot(l) === 'shared');
  }
  state.logs = logs;
  state.logTotal = resp.total || 0;
  state.logExpanded = state.logExpanded || new Set();
  state.logExpanded.clear();
  renderLogs();
  loadLogSummary();
}

function _logSummary(log) {
  // 一行摘要(给折叠状态展示). 各类型按可读性裁剪一下.
  const raw = log.detail || '';
  if (log.log_type === 'robot_command') {
    try {
      const p = JSON.parse(raw);
      const dataStr = p.data ? JSON.stringify(p.data) : '';
      return dataStr ? `${p.type || ''} ${dataStr}` : (p.type || raw);
    } catch(_) { return raw; }
  }
  try {
    const p = JSON.parse(raw);
    if (p.text !== undefined) {
      let s = `🗣️ 「${p.text}」`;
      if (p.confidence) s += ` (置信度:${(p.confidence * 100).toFixed(0)}%)`;
      return s;
    }
    return Object.entries(p).map(([k,v]) => `${k}:${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' | ');
  } catch(_) { return raw; }
}

function _logDetailPretty(log) {
  const raw = log.detail || '';
  if (!raw) return '(空)';
  try {
    const p = JSON.parse(raw);
    return JSON.stringify(p, null, 2);
  } catch(_) { return raw; }
}

function _logShortTime(t) {
  // "2026-04-21 15:08:29" → "04-21 15:08:29"
  if (!t) return '';
  const m = String(t).match(/^\d{4}-(\d{2}-\d{2} \d{2}:\d{2}:\d{2})/);
  return m ? m[1] : t;
}

function renderLogs() {
  const tbody = document.getElementById('log-table-body');
  if (!tbody) return;
  const collapseDup = document.getElementById('log-collapse-dup')?.checked;

  // 跨页搜索由后端过滤了, 前端不再二次过滤; 这里只做"折叠重复"
  let logs = state.logs || [];
  if (collapseDup) logs = _collapseConsecutiveDuplicates(logs);

  if (!logs.length) {
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:24px;">该窗口内暂无日志</td></tr>';
    return;
  }
  tbody.innerHTML = logs.map((log, idx) => {
    const expanded = state.logExpanded.has(log.id ?? idx);
    const summary = _logSummary(log);
    const sourceTarget = log.target ? `${log.source || ''} → ${log.target}` : (log.source || '');
    const status = log.status || 'ok';
    const dur = log.duration_ms ? `${log.duration_ms}ms` : '';
    const dupBadge = log._dupCount > 1 ? `<span class="log-dup">×${log._dupCount}</span>` : '';
    // 机器人 badge: 豹小秘/宇树/共享, 让现场排查一眼分清来源
    const bot = _detectLogBot(log);
    const botLabelMap = {baoxiaomi: '豹', unitree: '宇', shared: '共'};
    const botBadge = `<span class="log-bot-badge b-${bot}" title="${bot}">${botLabelMap[bot] || ''}</span>`;
    const summaryRow = `
      <tr class="log-row${expanded ? ' expanded' : ''} log-row-status-${escapeHtml(status)}" data-log-key="${log.id ?? idx}" onclick="toggleLogRow(this)">
        <td><span class="log-caret">▸</span></td>
        <td class="log-time">${escapeHtml(_logShortTime(log.created_at))}</td>
        <td>${botBadge}<span class="log-badge t-${escapeHtml(log.log_type || '')}">${escapeHtml(log.log_type || '')}</span>${dupBadge}</td>
        <td>${escapeHtml(sourceTarget)}</td>
        <td>${escapeHtml(log.action || '')}</td>
        <td class="log-detail-cell" title="${escapeHtml(log.detail || '')}">${escapeHtml(summary)}</td>
        <td><span class="log-status s-${escapeHtml(status)}">${escapeHtml(status)}</span></td>
        <td style="color:var(--text-muted);">${escapeHtml(dur)}</td>
      </tr>`;
    const detailRow = expanded ? `
      <tr class="log-detail-row"><td colspan="8"><pre>${escapeHtml(_logDetailPretty(log))}</pre></td></tr>` : '';
    return summaryRow + detailRow;
  }).join('');
}

// 把连续相同 (log_type+source+action+status) 的相邻日志折叠成一条 + 计数
function _collapseConsecutiveDuplicates(logs) {
  if (!Array.isArray(logs) || logs.length <= 1) return logs;
  const out = [];
  for (const log of logs) {
    const last = out[out.length - 1];
    const sameKey = last
      && last.log_type === log.log_type
      && last.source === log.source
      && last.action === log.action
      && last.status === log.status;
    if (sameKey) {
      last._dupCount = (last._dupCount || 1) + 1;
      // 保留最新一条的 detail/created_at, 早的丢弃
    } else {
      out.push({ ...log, _dupCount: 1 });
    }
  }
  return out;
}

function toggleLogRow(tr) {
  const key = tr.getAttribute('data-log-key');
  state.logExpanded = state.logExpanded || new Set();
  // key 是 string, state.logs[].id 是数字 — 统一比较时用宽松判断
  const numKey = Number(key);
  const lookup = Number.isFinite(numKey) ? numKey : key;
  if (state.logExpanded.has(lookup)) state.logExpanded.delete(lookup);
  else state.logExpanded.add(lookup);
  renderLogs();
}

// ============================================================
// 其他
// ============================================================
// ============================================================
// 机器人配置
// ============================================================
// ============================================================
// 机器人直接控制
// ============================================================
async function robotCmd(command, params) {
  const robotId = document.getElementById('ctrl-robot-id')?.value;
  if (!robotId) return showToast('请选择机器人', 'error');
  const resultEl = document.getElementById('robot-cmd-result');
  if (resultEl) resultEl.textContent = `发送中: cmd_${command}...`;
  try {
    const resp = await api('/api/control/robot', {
      method: 'POST',
      body: JSON.stringify({ robot_id: robotId, command, params }),
    });
    if (resultEl) {
      if (resp.error) {
        resultEl.textContent = `❌ ${resp.error}`;
      } else {
        resultEl.textContent = `✅ ${resp.message || 'OK'}` +
          (resp.result ? '\n' + JSON.stringify(resp.result.data || resp.result, null, 2) : '');
      }
    }
  } catch (e) {
    if (resultEl) resultEl.textContent = `❌ ${e.message}`;
  }
}

async function robotStartListen() {
  const v = parseInt(document.getElementById('ctrl-listen-timeout')?.value || 30);
  const timeout = Math.max(5, Math.min(120, v));
  await robotCmd('listen', { timeout, language: 'zh' });
}
window.robotStartListen = robotStartListen;

async function robotTts() {
  const text = document.getElementById('ctrl-tts-text')?.value?.trim();
  if (!text) return showToast('请输入播报内容', 'error');
  await robotCmd('tts', { text, priority: 'normal' });
}

async function robotNavigate() {
  const sel = document.getElementById('ctrl-nav-point');
  if (!sel?.value) return showToast('请选择点位', 'error');
  const pointName = sel.options[sel.selectedIndex]?.dataset?.name || '';
  await robotCmd('navigate', { pointId: parseInt(sel.value), pointName, command: `go_${sel.value}` });
}

async function robotShow(action) {
  if (action === 'clear') {
    await robotCmd('show', { action: 'clear' });
  } else {
    const text = document.getElementById('ctrl-show-text')?.value?.trim();
    if (!text) return showToast('请输入展示内容', 'error');
    await robotCmd('show', { action: 'text', text });
  }
}

// ============================================================
// 宇树机器人手动控制 (透传到网关 /api/common)
// ============================================================
let _unitreeLastNavTask = '';

async function unitreeCmd(action, params) {
  const out = document.getElementById('unitree-ctrl-result');
  if (out) out.textContent = `→ ${action}...`;
  try {
    const resp = await api('/api/unitree/command', {
      method: 'POST',
      body: JSON.stringify({ action, params: params || {} }),
    });
    if (resp.status === 'ok') {
      if (out) out.textContent = `✅ ${action} OK` + (resp.data ? '\n' + JSON.stringify(resp.data, null, 2) : '');
      return resp.data;
    } else {
      const msg = `[${resp.code ?? '-'}] ${resp.message || '失败'}`;
      if (out) out.textContent = `❌ ${action}: ${msg}`;
      showToast(`${action} 失败: ${msg}`, 'error');
    }
  } catch (e) {
    if (out) out.textContent = `❌ ${e.message}`;
    showToast(e.message, 'error');
  }
  return null;
}

async function unitreeCtrlTts() {
  const text = document.getElementById('unitree-ctrl-tts-text')?.value?.trim();
  if (!text) return showToast('请输入播报内容', 'error');
  await unitreeCmd('tts.play', { text: text.slice(0, 200) });
}
async function unitreeCtrlTtsStop()   { await unitreeCmd('tts.stop', {}); }
async function unitreeCtrlMotion() {
  const id = parseInt(document.getElementById('unitree-ctrl-motion')?.value);
  if (!id) return showToast('请选择动作', 'error');
  await unitreeCmd('motions.playback.create', { motions: [id] });
}
async function unitreeCtrlDance() {
  const did = document.getElementById('unitree-ctrl-dance')?.value;
  if (!did) return showToast('请选择舞蹈', 'error');
  await unitreeCmd('dance.select', { dance_id: did });
}
async function unitreeCtrlDanceStop() { await unitreeCmd('dance.stop', {}); }
async function unitreeCtrlVolume() {
  const v = parseInt(document.getElementById('unitree-ctrl-volume')?.value || 60);
  await unitreeCmd('tts.volume.set', { audio_volume: Math.max(0, Math.min(100, v)) });
}
async function unitreeCtrlAsr(action)  { await unitreeCmd(`asr.${action}`, {}); }

async function unitreeCtrlAsrStart() {
  const out = document.getElementById('unitree-ctrl-result');
  const d = parseInt(document.getElementById('unitree-ctrl-asr-listen')?.value || 0);
  const dur = Math.max(0, Math.min(600, d));
  if (out) out.textContent = dur > 0 ? `→ asr.start (收音时长 ${dur}s)` : '→ asr.start';
  try {
    const resp = await api('/api/unitree/asr_start', {
      method: 'POST',
      body: JSON.stringify({ listen_duration: dur }),
    });
    const r = (resp && resp.data) || {};
    if (r.ok) {
      if (out) out.textContent = dur > 0
        ? `✅ ASR 已启动, ${dur}s 后自动回唤醒词模式`
        : '✅ ASR 已启动 (持续, 手动停)';
    } else {
      if (out) out.textContent = `❌ ${r.error || '启动失败'}`;
      showToast(r.error || 'ASR 启动失败', 'error');
    }
  } catch (e) {
    if (out) out.textContent = `❌ ${e.message}`;
    showToast(e.message, 'error');
  }
}
window.unitreeCtrlAsrStart = unitreeCtrlAsrStart;

async function unitreeCtrlAsrStop() {
  const out = document.getElementById('unitree-ctrl-result');
  if (out) out.textContent = '→ asr.stop';
  try {
    const resp = await api('/api/unitree/asr_stop', { method: 'POST' });
    const r = (resp && resp.data) || {};
    if (r.ok) {
      if (out) out.textContent = '✅ ASR 已停 (已取消自动停计时器)';
    } else {
      if (out) out.textContent = `❌ ${r.error || '停止失败'}`;
      showToast(r.error || 'ASR 停止失败', 'error');
    }
  } catch (e) {
    if (out) out.textContent = `❌ ${e.message}`;
    showToast(e.message, 'error');
  }
}
window.unitreeCtrlAsrStop = unitreeCtrlAsrStop;

async function unitreeCtrlAsrListenOnce() {
  const out = document.getElementById('unitree-ctrl-result');
  const d = parseInt(document.getElementById('unitree-ctrl-asr-duration')?.value || 10);
  const dur = Math.max(3, Math.min(60, d));
  if (out) out.textContent = `🎙️ 监听中… 最长 ${dur}s`;
  try {
    const resp = await api('/api/unitree/listen_once', {
      method: 'POST',
      body: JSON.stringify({ duration: dur }),
    });
    const r = (resp && resp.data) || {};
    if (r.text) {
      if (out) out.textContent = `✅ 识别 (${r.elapsed_ms}ms): ${r.text}`;
      showToast(`识别到: ${r.text}`, 'ok');
    } else if (r.error) {
      if (out) out.textContent = `❌ ${r.error}`;
      showToast(r.error, 'error');
    } else {
      if (out) out.textContent = `⌛ ${dur}s 内无识别结果 (${r.elapsed_ms}ms)`;
      showToast(`超时 ${dur}s 内无识别结果`, 'warn');
    }
  } catch (e) {
    if (out) out.textContent = `❌ ${e.message}`;
    showToast(e.message, 'error');
  }
}
window.unitreeCtrlAsrListenOnce = unitreeCtrlAsrListenOnce;
async function unitreeCtrlFace(action) { await unitreeCmd(`face_recognition.${action}`, {}); }

async function unitreeCtrlNav() {
  const mapId = document.getElementById('unitree-ctrl-map')?.value;
  const pointId = document.getElementById('unitree-ctrl-point')?.value;
  if (!mapId || !pointId) return showToast('请选择地图和点位', 'error');
  const data = await unitreeCmd('nav.planning_to_goal', {
    current_working_map_id: String(mapId),
    point_id: parseInt(pointId),
  });
  if (data && data.task_id) _unitreeLastNavTask = data.task_id;
}

async function unitreeCtrlNavCtl(action) {
  if (!_unitreeLastNavTask) return showToast('没有最近的导航任务', 'error');
  await unitreeCmd('nav.task_control', { task_id: _unitreeLastNavTask, action });
}

// 切换地图下拉 → 拉点位
async function onUnitreeCtrlMapChange(selectEl) {
  const pointSel = document.getElementById('unitree-ctrl-point');
  if (!pointSel) return;
  pointSel.innerHTML = '<option value="">加载中...</option>';
  const mapId = selectEl.value;
  if (!mapId) { pointSel.innerHTML = '<option value="">点位</option>'; return; }
  const pts = await loadUnitreeMapPoints(mapId);
  pointSel.innerHTML = '<option value="">点位</option>' + pts.map(p =>
    `<option value="${p.point_id}">${p.point_id} · ${escapeHtml(p.point_name || '(未命名)')}</option>`
  ).join('');
}

// 初始化宇树控制面板下拉
// ============================================================
// 🕹️ 机器人控制独立 Tab — 初始化 + 定时状态刷新
// ============================================================
let _robotCtrlTimer = null;

function initRobotCtrlTab() {
  // 下拉数据 (动作 / 舞蹈 / 地图 / 豹小秘点位 / 豹小秘机器人列表)
  initUnitreeCtrlPanel();
  fillBaoxiaomiRobotSelect();
  // 立刻刷一次状态
  refreshRobotCtrlTab();
  // 独立 tab 在查看时每 3s 刷新; 切走后停
  if (_robotCtrlTimer) clearInterval(_robotCtrlTimer);
  _robotCtrlTimer = setInterval(() => {
    const visible = document.getElementById('tab-robotctrl')?.classList.contains('active');
    if (!visible) { clearInterval(_robotCtrlTimer); _robotCtrlTimer = null; return; }
    refreshRobotCtrlTab();
  }, 3000);
}

function fillBaoxiaomiRobotSelect() {
  // 填豹小秘机器人下拉 (复用 /api/robots)
  const sel = document.getElementById('ctrl-robot-id');
  if (!sel) return;
  api('/api/robots').then(r => {
    const robots = (r.data || []).filter(x => x.status !== 'offline');
    sel.innerHTML = robots.length === 0
      ? '<option value="">无在线机器人</option>'
      : robots.map(x => `<option value="${x.id}">${escapeHtml(x.name || x.id)}</option>`).join('');
  }).catch(() => { sel.innerHTML = '<option value="">加载失败</option>'; });
  // 豹小秘点位 (nav_positions)
  const navSel = document.getElementById('ctrl-nav-point');
  if (navSel && state.refs && state.refs.positions) {
    navSel.innerHTML = '<option value="">选择点位</option>' + state.refs.positions.map(p =>
      `<option value="${p.position_id}" data-name="${escapeHtml(p.name)}">${escapeHtml(p.name)}</option>`
    ).join('');
  }
}

async function refreshRobotCtrlTab() {
  // 豹小秘状态
  try {
    const r = await api('/api/robots');
    const robots = (r.data || []).filter(x => x.status !== 'offline');
    const pill = document.getElementById('bxm-status-pill');
    const overlay = document.getElementById('robot-control-empty');
    const batteryEl = document.getElementById('bxm-battery');
    const locationEl = document.getElementById('bxm-location');
    const selectedId = document.getElementById('ctrl-robot-id')?.value;
    const current = robots.find(x => x.id === selectedId) || robots[0];
    if (pill) {
      if (current) {
        pill.innerHTML = `<span class="rc-dot rc-dot-green"></span><span class="rc-status-text">${escapeHtml(current.status || '在线')}</span>`;
      } else {
        pill.innerHTML = `<span class="rc-dot rc-dot-red"></span><span class="rc-status-text">离线</span>`;
      }
    }
    if (overlay) overlay.style.display = robots.length === 0 ? '' : 'none';
    if (batteryEl) batteryEl.textContent = current ? `${current.battery || '--'}%` : '—';
    if (locationEl) locationEl.textContent = current?.location || '—';
  } catch (e) { /* silent */ }

  // 宇树状态
  try {
    const r = await api('/api/unitree/status');
    const d = r.data || {};
    const pill = document.getElementById('unitree-status-pill');
    const subtitle = document.getElementById('unitree-card-subtitle');
    if (pill) {
      if (!d.enabled) {
        pill.innerHTML = `<span class="rc-dot rc-dot-gray"></span><span class="rc-status-text">禁用</span>`;
      } else if (d.online) {
        pill.innerHTML = `<span class="rc-dot rc-dot-green"></span><span class="rc-status-text">在线</span>`;
      } else {
        pill.innerHTML = `<span class="rc-dot rc-dot-red"></span><span class="rc-status-text">离线</span>`;
      }
    }
    if (subtitle) subtitle.textContent = `HTTP Gateway · ${d.base_url || '—'}`;
    const ros2El = document.getElementById('unitree-ros2');
    if (ros2El) ros2El.textContent = d.gateway?.ros2_ok ? '✓ OK' : (d.online ? '✗ FAIL' : '—');
    const mapEl = document.getElementById('unitree-cur-map');
    if (mapEl) mapEl.textContent = d.current_map?.display_name || d.current_map?.map_name || d.current_map?.map_id || '—';
    const libEl = document.getElementById('unitree-lib');
    const lib = d.library || {};
    if (libEl) libEl.textContent = `🤸${lib.motions||0} 💃${lib.dances||0} 🗺️${lib.maps||0}`;
  } catch (e) { /* silent */ }
}

function initUnitreeCtrlPanel() {
  const u = state.unitree || { motions: [], dances: [], maps: [] };
  const mEl = document.getElementById('unitree-ctrl-motion');
  if (mEl) {
    mEl.innerHTML = '<option value="">选择动作</option>' + (u.motions || []).map(m =>
      `<option value="${m.motion_id}">${m.motion_id} · ${escapeHtml(m.name)}</option>`
    ).join('');
  }
  const dEl = document.getElementById('unitree-ctrl-dance');
  if (dEl) {
    dEl.innerHTML = '<option value="">选择舞蹈</option>' + (u.dances || []).map(d =>
      `<option value="${d.dance_id}">${d.dance_id} · ${escapeHtml(d.name)}${d.duration_seconds ? ` (${d.duration_seconds}s)` : ''}</option>`
    ).join('');
  }
  const mapEl = document.getElementById('unitree-ctrl-map');
  if (mapEl) {
    const cur = (u.maps || []).find(x => Number(x.is_current) === 1);
    mapEl.innerHTML = '<option value="">地图</option>' + (u.maps || []).map(x =>
      `<option value="${x.map_id}" ${Number(x.is_current) === 1 ? 'selected' : ''}>${escapeHtml(x.display_name || x.map_name || x.map_id)}${Number(x.is_current) === 1 ? ' · 当前' : ''}</option>`
    ).join('');
    if (cur) {
      mapEl.value = cur.map_id;
      onUnitreeCtrlMapChange(mapEl);
    }
  }
}

async function unitreeMapRename() {
  const mapEl = document.getElementById('unitree-ctrl-map');
  if (!mapEl || !mapEl.value) return showToast('请先选中一张地图', 'error');
  const mapId = mapEl.value;
  const u = state.unitree || { maps: [] };
  const current = (u.maps || []).find(x => String(x.map_id) === String(mapId));
  const currentAlias = current ? (current.display_name || current.map_name || '') : '';
  const next = prompt(`给地图 ${mapId} 起个中文别名 (留空则恢复原名):`, currentAlias);
  if (next === null) return; // 取消
  try {
    await api(`/api/system/unitree_maps/${encodeURIComponent(mapId)}/display_name`, {
      method: 'PUT',
      body: JSON.stringify({ display_name: next.trim() }),
    });
    // 刷新地图缓存
    const r = await api('/api/unitree/maps');
    state.unitree = state.unitree || {};
    state.unitree.maps = r.data || [];
    initUnitreeCtrlPanel();
    showToast('别名已更新', 'ok');
  } catch (e) {
    showToast(e.message, 'error');
  }
}
window.unitreeMapRename = unitreeMapRename;

async function loadRobotConfig() {
  try {
    const resp = await api('/api/system/config');
    const cfg = resp.data || {};
    // wake_word 已固定为"小豹小豹"，不再走后台配置
    const rNameEl = document.getElementById('cfg-robot-display-name');
    const dNameEl = document.getElementById('cfg-digital-human-display-name');
    const hbEl = document.getElementById('cfg-heartbeat-interval');
    const ltEl = document.getElementById('cfg-listen-timeout');
    const llEl = document.getElementById('cfg-listen-language');
    if (rNameEl) rNameEl.value = cfg.robot_display_name || '小豹';
    if (dNameEl) dNameEl.value = cfg.digital_human_display_name || '艾琳';
    if (hbEl) hbEl.value = cfg.heartbeat_interval || '30';
    if (ltEl) ltEl.value = cfg.listen_timeout || '30';
    if (llEl) llEl.value = cfg.listen_language || 'zh';
  } catch (e) {
    console.warn('加载机器人配置失败:', e);
  }

  // 加载在线机器人
  try {
    const robotResp = await api('/api/robots');
    const robots = robotResp.data || [];
    const onlineEl = document.getElementById('cfg-robot-online');
    if (onlineEl) {
      const online = robots.filter(r => r.status !== 'offline');
      const controlArea = document.getElementById('robot-control-area');
      if (online.length === 0) {
        onlineEl.innerHTML = '<span style="color:#94a3b8">无在线机器人</span>';
        if (controlArea) controlArea.style.display = 'none';
      } else {
        onlineEl.innerHTML = online.map(r => {
          const pc = typeof r.person_count === 'number' ? r.person_count : 0;
          const pcClass = pc > 0 ? 'person-live' : 'person-idle';
          const hb = r.last_heartbeat ? String(r.last_heartbeat).slice(11, 19) : '--';
          return `
          <div class="robot-card">
            <span class="rc-icon">🤖</span>
            <b>${r.name || r.id}</b>
            <span class="rc-tag status-${r.status}">${r.status}</span>
            <span class="rc-tag ${pcClass}">👥 当前 ${pc} 人</span>
            <span class="rc-tag">🔋 ${r.battery || '--'}%</span>
            <span class="rc-tag rc-dim">⏱ ${hb}</span>
            ${r.location ? `<span class="rc-tag rc-dim">📍 ${r.location}</span>` : ''}
          </div>`;
        }).join('');
        // 填充控制区机器人下拉框
        const sel = document.getElementById('ctrl-robot-id');
        if (sel) {
          sel.innerHTML = online.map(r =>
            `<option value="${r.id}">${r.name || r.id}</option>`
          ).join('');
        }
        if (controlArea) controlArea.style.display = '';
        // 填充点位下拉
        const navSel = document.getElementById('ctrl-nav-point');
        if (navSel && state.refs.positions && state.refs.positions.length) {
          navSel.innerHTML = state.refs.positions.map(p =>
            `<option value="${p.position_id}" data-name="${p.name}">${p.name}</option>`
          ).join('');
        }
      }
    }
  } catch (e) { console.warn('加载机器人状态失败:', e); }

  // 宇树机器人首屏状态 (独立于豹小秘)
  try { await refreshUnitreeStatus(); } catch (_) {}
  // 宇树手动控制面板下拉初始化
  try { initUnitreeCtrlPanel(); } catch (_) {}
}

async function saveRobotConfig() {
  // 唤醒词已固定为"小豹小豹"，不参与保存
  const robotName = (document.getElementById('cfg-robot-display-name').value || '').trim();
  const dhName = (document.getElementById('cfg-digital-human-display-name').value || '').trim();
  if (!robotName) return setResult('cfg-robot-result', '机器人昵称不能为空', false);
  if (!dhName)    return setResult('cfg-robot-result', '数字人昵称不能为空', false);
  try {
    const resp = await api('/api/system/config', {
      method: 'PUT',
      body: JSON.stringify({
        robot_display_name: robotName,
        digital_human_display_name: dhName,
        heartbeat_interval: document.getElementById('cfg-heartbeat-interval').value,
        listen_timeout: document.getElementById('cfg-listen-timeout').value,
        listen_language: document.getElementById('cfg-listen-language').value,
      }),
    });
    const pushed = resp.pushed_to_robots;
    const msg = pushed != null
      ? `配置已保存，已下发到 ${pushed} 台机器人`
      : '配置已保存';
    setResult('cfg-robot-result', msg, true);
  } catch (e) {
    setResult('cfg-robot-result', `保存失败: ${e.message}`, false);
  }
}

async function refreshReferences() {
  const refResp = await api('/api/ref/all');
  state.refs = refResp.data || state.refs;
  bindDefaults();
  document.getElementById('current-special-badge').textContent = `当前专场: ${currentSpecialName()}`;
  await loadStats();
}

// ============================================================
// 音乐曲库
// ============================================================
async function loadMusic() {
  try {
    const r = await api('/api/music');
    const baseInput = document.getElementById('music-url-base');
    if (baseInput) baseInput.value = r.configured_url_base || '';
    const autoEl = document.getElementById('music-auto-base');
    if (autoEl) autoEl.textContent = r.auto_url_base || '(未探测到)';
    const list = document.getElementById('music-list');
    const tracks = r.data || [];
    if (!tracks.length) {
      list.innerHTML = '<p class="hint">还没有上传任何音乐。请先用上面的"⬆️ 上传"按钮添加。</p>';
      return;
    }
    list.innerHTML = tracks.map(t => {
      const sizeMB = (t.size_bytes / 1024 / 1024).toFixed(2);
      const status = t.enabled ? '✅ 已启用' : '⏸️ 已禁用';
      return `
        <div class="card">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
            <div style="flex:1;min-width:200px">
              <strong>${escapeHtml(t.name)}</strong>
              <div class="hint" style="margin-top:4px">${status} · ${sizeMB} MB · ${escapeHtml(t.uploaded_at || '')}</div>
              <div class="hint" style="margin-top:4px;font-family:monospace;word-break:break-all">${escapeHtml(t.url)}</div>
            </div>
            <div style="display:flex;gap:6px;flex-wrap:wrap">
              <audio src="${escapeHtml(t.url)}" controls preload="none" style="height:32px"></audio>
              <button class="btn" onclick="toggleMusicTrack(${t.id}, ${t.enabled ? 0 : 1})">${t.enabled ? '⏸️ 禁用' : '✅ 启用'}</button>
              <button class="btn btn-danger" data-music-id="${t.id}" data-music-name="${escapeHtml(t.name)}" onclick="deleteMusicTrack(this)">🗑️ 删除</button>
            </div>
          </div>
        </div>`;
    }).join('');
  } catch (e) {
    showToast(`音乐列表加载失败: ${e.message}`, 'error');
  }
}

async function uploadMusic() {
  const fileInput = document.getElementById('music-file');
  const nameInput = document.getElementById('music-name');
  if (!fileInput.files || !fileInput.files.length) {
    showToast('请先选择文件', 'warning');
    return;
  }
  const f = fileInput.files[0];
  if (f.size > 50 * 1024 * 1024) {
    showToast('文件超出 50MB 上限', 'error');
    return;
  }
  const fd = new FormData();
  fd.append('file', f);
  fd.append('name', nameInput.value.trim());
  try {
    const resp = await fetch('/api/music/upload', { method: 'POST', body: fd });
    if (!resp.ok) {
      const txt = await resp.text();
      throw new Error(txt || resp.statusText);
    }
    showToast('上传成功', 'ok');
    fileInput.value = '';
    nameInput.value = '';
    await loadMusic();
  } catch (e) {
    showToast(`上传失败: ${e.message}`, 'error');
  }
}

async function toggleMusicTrack(id, enabled) {
  try {
    await api(`/api/music/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled }),
    });
    await loadMusic();
  } catch (e) {
    showToast(`操作失败: ${e.message}`, 'error');
  }
}

async function deleteMusicTrack(btn) {
  const id = parseInt(btn.dataset.musicId);
  const name = btn.dataset.musicName;
  if (!confirm(`确认删除 "${name}" ?`)) return;
  try {
    await api(`/api/music/${id}`, { method: 'DELETE' });
    showToast('已删除', 'ok');
    await loadMusic();
  } catch (e) {
    showToast(`删除失败: ${e.message}`, 'error');
  }
}

async function saveMusicUrlBase() {
  const value = document.getElementById('music-url-base').value.trim();
  try {
    await api('/api/system/config', {
      method: 'PUT',
      body: JSON.stringify({ music_url_base: value }),
    });
    showToast('已保存', 'ok');
  } catch (e) {
    showToast(`保存失败: ${e.message}`, 'error');
  }
}

// 暴露给 HTML onclick
window.switchTab = switchTab;
window.triggerSync = triggerSync;
window.doSwitchSpecial = doSwitchSpecial;
window.loadEquipmentsByArea = loadEquipmentsByArea;
window.doEquipment = doEquipment;
window.doGroupCommand = doGroupCommand;
window.loadExhibitsForResource = loadExhibitsForResource;
window.loadResourcesByExhibitSpecial = loadResourcesByExhibitSpecial;
window.doPlayResource = doPlayResource;
window.showCreateWorkflow = showCreateWorkflow;
window.cancelWorkflowEdit = cancelWorkflowEdit;
window.addCanvasStep = addCanvasStep;
window.removeCanvasStep = removeCanvasStep;
window.toggleStepConfig = toggleStepConfig;
window.loadResourceExhibits = loadResourceExhibits;
window.loadResourceList = loadResourceList;
window.saveWorkflow = saveWorkflow;
window.editWorkflow = editWorkflow;
window.deleteWorkflow = deleteWorkflow;
window.toggleWorkflow = toggleWorkflow;
window.updateBindTargetOptions = updateBindTargetOptions;
window.createBinding = createBinding;
window.deleteBinding = deleteBinding;
window.updateIntentTargetOptions = updateIntentTargetOptions;
window.createIntentMapping = createIntentMapping;
window.deleteIntentMapping = deleteIntentMapping;
window.showCreateSchedule = showCreateSchedule;
window.toggleSchedule = toggleSchedule;
window.deleteSchedule = deleteSchedule;
window.cancelScheduleEdit = cancelScheduleEdit;
window.toggleScheduleExpr = toggleScheduleExpr;
window.toggleScheduleAction = toggleScheduleAction;
window.saveSchedule = saveSchedule;
window.loadLogs = loadLogs;
window.setLogCategory = setLogCategory;
window.setLogBot = setLogBot;
window.setLogTime = setLogTime;
window.toggleLogLive = toggleLogLive;
window.loadLogSummary = loadLogSummary;
window.toggleLogRow = typeof toggleLogRow === 'function' ? toggleLogRow : window.toggleLogRow;
window.loadWorkflows = loadWorkflows;
window.loadBindings = loadBindings;
window.loadTourPoints = loadTourPoints;
window.saveTourPoint = saveTourPoint;
window.editTourPoint = editTourPoint;
window.deleteTourPoint = deleteTourPoint;
window.loadMusic = loadMusic;
window.uploadMusic = uploadMusic;
window.deleteMusicTrack = deleteMusicTrack;
window.toggleMusicTrack = toggleMusicTrack;
window.saveMusicUrlBase = saveMusicUrlBase;
window.loadIntentMappings = loadIntentMappings;
window.loadSchedules = loadSchedules;
window.renumberSteps = renumberSteps;
window.reorderSteps = reorderSteps;
window.saveRobotConfig = saveRobotConfig;
window.loadRobotConfig = loadRobotConfig;
window.toggleAccordion = function(id) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle('open');
};

// 每 3s 实时刷新在线机器人列表(含 person_count), 仅当面板可见时拉
async function refreshOnlineRobots() {
  const el = document.getElementById('cfg-robot-online');
  if (!el || !el.offsetParent) return;
  // 宇树状态并发刷新 (独立于豹小秘, 单独失败不影响另一方)
  refreshUnitreeStatus();
  try {
    const r = await api('/api/robots');
    const robots = r.data || [];
    const online = robots.filter(x => x.status !== 'offline');
    const countEl = document.getElementById('cfg-online-count');
    if (countEl) countEl.textContent = `(${online.length})`;
    if (online.length === 0) {
      el.innerHTML = '<span style="color:#94a3b8;font-size:12px">无在线机器人</span>';
      const empty = document.getElementById('robot-control-empty');
      const ctrl = document.getElementById('robot-control-area');
      if (empty) empty.style.display = '';
      if (ctrl) ctrl.style.display = 'none';
      return;
    }
    el.innerHTML = online.map(x => {
      const pc = typeof x.person_count === 'number' ? x.person_count : 0;
      const pcClass = pc > 0 ? 'person-live' : 'person-idle';
      const hb = x.last_heartbeat ? String(x.last_heartbeat).slice(11, 19) : '';
      return `
        <div class="robot-row">
          <b>🤖 ${x.name || x.id}</b>
          <span class="rc-tag status-${x.status}">${x.status}</span>
          <span class="rc-tag ${pcClass}">👥 ${pc}</span>
          <span class="rc-tag">🔋 ${x.battery || '--'}%</span>
          ${hb ? `<span class="rc-dim">${hb}</span>` : ''}
        </div>`;
    }).join('');
    // 同步填充控制下拉
    const sel = document.getElementById('ctrl-robot-id');
    if (sel && sel.options.length !== online.length) {
      sel.innerHTML = online.map(x => `<option value="${x.id}">${x.name || x.id}</option>`).join('');
    }
    const empty = document.getElementById('robot-control-empty');
    const ctrl = document.getElementById('robot-control-area');
    if (empty) empty.style.display = 'none';
    if (ctrl) ctrl.style.display = '';
  } catch (e) { /* 静默 */ }
}
setInterval(refreshOnlineRobots, 3000);

// 宇树机器人状态渲染 — 独立函数, 在线检查走 /api/unitree/status (后端 2s TTL 缓存)
async function refreshUnitreeStatus() {
  const el = document.getElementById('cfg-unitree-online');
  if (!el) return;
  try {
    const r = await api('/api/unitree/status');
    const d = r.data || {};
    if (!d.enabled) {
      el.innerHTML = '<span style="color:#94a3b8;font-size:12px">⚠️ 已禁用 (系统设置里启用)</span>';
      return;
    }
    const dot = d.online ? '🟢' : '🔴';
    const label = d.online ? '在线' : (d.error ? '离线' : '无响应');
    const gw = d.gateway || {};
    const rosTag = d.online
      ? `<span class="rc-tag ${gw.ros2_ok ? 'status-idle' : 'status-error'}">ROS2 ${gw.ros2_ok ? 'OK' : 'FAIL'}</span>`
      : '';
    const lib = d.library || {};
    const curMap = d.current_map
      ? `<span class="rc-dim">📍 ${escapeHtml(d.current_map.display_name || d.current_map.map_name || d.current_map.map_id)}</span>`
      : '';
    const errTag = d.error
      ? `<span class="rc-dim" title="${escapeHtml(d.error)}">· ${escapeHtml(String(d.error).slice(0, 40))}</span>`
      : '';
    el.innerHTML = `
      <div class="robot-row">
        <b>${dot} G1 @ ${escapeHtml(d.base_url || '-')}</b>
        <span class="rc-tag status-${d.online ? 'idle' : 'error'}">${label}</span>
        ${rosTag}
        <span class="rc-tag">🤸 ${lib.motions || 0}</span>
        <span class="rc-tag">💃 ${lib.dances || 0}</span>
        <span class="rc-tag">🗺️ ${lib.maps || 0}</span>
        ${curMap}
        ${errTag}
      </div>`;
  } catch (e) {
    el.innerHTML = `<span style="color:#ef4444;font-size:12px">❌ 状态接口异常: ${escapeHtml(e.message)}</span>`;
  }
}
window.refreshUnitreeStatus = refreshUnitreeStatus;
window.unitreeCmd = unitreeCmd;
window.unitreeCtrlTts = unitreeCtrlTts;
window.unitreeCtrlTtsStop = unitreeCtrlTtsStop;
window.unitreeCtrlMotion = unitreeCtrlMotion;
window.unitreeCtrlDance = unitreeCtrlDance;
window.unitreeCtrlDanceStop = unitreeCtrlDanceStop;
window.unitreeCtrlVolume = unitreeCtrlVolume;
window.unitreeCtrlAsr = unitreeCtrlAsr;
window.unitreeCtrlFace = unitreeCtrlFace;
window.unitreeCtrlNav = unitreeCtrlNav;
window.unitreeCtrlNavCtl = unitreeCtrlNavCtl;
window.onUnitreeCtrlMapChange = onUnitreeCtrlMapChange;
window.initUnitreeCtrlPanel = initUnitreeCtrlPanel;
window.initRobotCtrlTab = initRobotCtrlTab;
window.refreshRobotCtrlTab = refreshRobotCtrlTab;
window.toggleRequestFields = toggleRequestFields;
window.toggleShowFields = toggleShowFields;
window.toggleDhFields = toggleDhFields;
window.robotCmd = robotCmd;
window.robotTts = robotTts;
window.robotNavigate = robotNavigate;
window.robotShow = robotShow;
window.showTemplates = showTemplates;
window.applyTemplate = applyTemplate;
window.loadNarrationForStep = loadNarrationForStep;
window.saveStepNarration = saveStepNarration;
window.generateStepNarration = generateStepNarration;

window.loadSettings = loadSettings;
window.saveSettings = saveSettings;
window.testWebhook = testWebhook;
window.testUnitreeHealth = testUnitreeHealth;
window.triggerUnitreeSync = triggerUnitreeSync;
window.onUnitreeMapChange = onUnitreeMapChange;
window.onUnitreeNavMapChange = onUnitreeNavMapChange;

// ============================================================
// 流程模板
// ============================================================
async function showTemplates() {
  try {
    const resp = await api('/api/templates');
    const tpls = resp.data || [];
    const html = tpls.map(t => `
      <div class="card" style="margin-bottom:8px;padding:10px;cursor:pointer" onclick="applyTemplate('${t.id}')">
        <b>${t.name}</b>
        <p style="color:#94a3b8;font-size:13px;margin:4px 0 0">${t.description}</p>
        <span style="color:#60a5fa;font-size:12px">${t.steps.length} 个步骤 • 点击应用</span>
      </div>
    `).join('');
    const el = document.getElementById('workflow-list');
    if (el) {
      el.innerHTML = `<div style="margin-bottom:12px">
        <h4>📋 选择模板</h4>${html}
        <button class="btn" onclick="loadWorkflows()" style="margin-top:8px">← 返回流程列表</button>
      </div>`;
    }
  } catch (e) {
    showToast('加载模板失败: ' + e.message, 'error');
  }
}

async function applyTemplate(tplId) {
  const name = prompt('请输入流程名称 (留空用模板名)');
  if (name === null) return; // 取消
  try {
    const resp = await api(`/api/templates/${tplId}/apply`, {
      method: 'POST',
      body: JSON.stringify({ name: name || '' }),
    });
    if (resp.error) {
      showToast(resp.error, 'error');
    } else {
      showToast(`流程 "${resp.name}" 已创建`, 'ok');
      loadWorkflows();
    }
  } catch (e) {
    showToast('应用模板失败: ' + e.message, 'error');
  }
}

// ============================================================
// 讲解词 (嵌入流程编辑器)
// ============================================================
async function loadNarrationForStep(selectEl) {
  const resId = selectEl.value;
  const step = selectEl.closest('.canvas-step');
  const area = step?.querySelector('.narration-area');
  const ta = step?.querySelector('.cfg-narration');
  const statusEl = step?.querySelector('.narr-status');
  if (!area || !ta) return;

  if (!resId) { area.style.display = 'none'; return; }
  area.style.display = 'block';
  ta.value = '加载中...';
  ta.dataset.resourceId = resId;

  try {
    const resp = await api(`/api/resources/narrations?resource_id=${resId}`);
    const items = resp.data || [];
    const r = items.find(i => String(i.id) === String(resId));
    if (r && r.narration_text) {
      ta.value = r.narration_text;
      if (statusEl) statusEl.textContent = '✅ 已有';
    } else {
      ta.value = '';
      if (statusEl) statusEl.textContent = '⚠️ 无讲解词';
    }
  } catch (e) {
    ta.value = '';
    if (statusEl) statusEl.textContent = '❌ 加载失败';
  }
}

async function saveStepNarration(btn) {
  const step = btn.closest('.canvas-step');
  const ta = step?.querySelector('.cfg-narration');
  const resId = ta?.dataset?.resourceId;
  if (!resId || !ta) return showToast('请先选择资源', 'error');

  try {
    await api(`/api/resources/${resId}/narration`, {
      method: 'PUT',
      body: JSON.stringify({ narration_text: ta.value }),
    });
    const statusEl = step?.querySelector('.narr-status');
    if (statusEl) statusEl.textContent = '✅ 已保存';
    showToast('讲解词已保存', 'ok');
  } catch (e) {
    showToast('保存失败: ' + e.message, 'error');
  }
}

async function generateStepNarration(btn) {
  const step = btn.closest('.canvas-step');
  const ta = step?.querySelector('.cfg-narration');
  const resId = ta?.dataset?.resourceId;
  if (!resId || !ta) return showToast('请先选择资源', 'error');

  ta.value = '🤖 AI 生成中...';
  try {
    const resp = await api(`/api/resources/${resId}/narration/generate`, { method: 'POST' }).catch(e => ({ error: e.message }));
    if (resp.error) {
      const hint = resp.error.includes('LLM') || resp.error.includes('配置')
        ? '请先在「系统设置」中开启 AI 生成并配置 API'
        : resp.error;
      showToast(hint, 'error');
      ta.value = '';
      return;
    }
    ta.value = resp.narration || '';
    const statusEl = step?.querySelector('.narr-status');
    if (statusEl) statusEl.textContent = '✅ AI已生成';
    showToast('AI 讲解词已生成并保存', 'ok');
  } catch (e) {
    showToast('AI 生成失败: ' + e.message, 'error');
    ta.value = '';
  }
}

// ============================================================
// 系统设置
// ============================================================
const SETTING_KEYS = [
  'llm_enabled', 'llm_api_url', 'llm_api_key', 'llm_model',
  'webhook_enabled', 'webhook_url', 'webhook_events',
  'openclaw_enabled', 'auto_plan_enabled',
  'unitree_enabled', 'unitree_base_url', 'unitree_api_key', 'unitree_sync_interval_min',
];
const SETTING_MAP = {
  llm_enabled: 'set-llm-enabled',
  llm_api_url: 'set-llm-url',
  llm_api_key: 'set-llm-key',
  llm_model: 'set-llm-model',
  webhook_enabled: 'set-webhook-enabled',
  webhook_url: 'set-webhook-url',
  webhook_events: 'set-webhook-events',
  openclaw_enabled: 'set-openclaw-enabled',
  auto_plan_enabled: 'set-auto-plan-enabled',
  unitree_enabled: 'set-unitree-enabled',
  unitree_base_url: 'set-unitree-base-url',
  unitree_api_key: 'set-unitree-api-key',
  unitree_sync_interval_min: 'set-unitree-sync-interval',
};

async function loadSettings() {
  try {
    const resp = await api('/api/system/config');
    const map = resp.data || {};
    for (const [key, elId] of Object.entries(SETTING_MAP)) {
      const el = document.getElementById(elId);
      if (el && map[key] !== undefined) el.value = map[key];
    }
  } catch (e) {
    console.error('loadSettings error', e);
  }
}

async function saveSettings() {
  try {
    const configs = {};
    for (const [key, elId] of Object.entries(SETTING_MAP)) {
      const el = document.getElementById(elId);
      if (!el) continue;
      configs[key] = el.value;
    }
    await api('/api/system/config', {
      method: 'PUT',
      body: JSON.stringify(configs),
    });
    showToast('设置已保存', 'ok');
  } catch (e) {
    showToast('保存失败: ' + e.message, 'error');
  }
}

async function testWebhook() {
  try {
    const resp = await api('/api/webhook/test', { method: 'POST' });
    if (resp.error) {
      showToast(resp.error, 'error');
    } else {
      showToast('测试消息已发送', 'ok');
    }
  } catch (e) {
    showToast('发送失败: ' + e.message, 'error');
  }
}

async function testUnitreeHealth() {
  const out = document.getElementById('unitree-sync-result');
  if (out) out.textContent = '检查中…';
  try {
    const resp = await api('/api/unitree/health');
    if (resp.status === 'ok') {
      showToast('宇树网关在线', 'ok');
      if (out) out.textContent = '✅ ' + JSON.stringify(resp.gateway || {});
    } else if (resp.status === 'disabled') {
      showToast('宇树网关已禁用', 'error');
      if (out) out.textContent = '⚠️ 当前状态: disabled';
    } else {
      showToast('网关无响应: ' + (resp.message || ''), 'error');
      if (out) out.textContent = '❌ ' + (resp.message || '无响应');
    }
  } catch (e) {
    showToast('健康检查失败: ' + e.message, 'error');
    if (out) out.textContent = '❌ ' + e.message;
  }
}

async function triggerUnitreeSync() {
  const out = document.getElementById('unitree-sync-result');
  if (out) out.textContent = '同步中…';
  try {
    const resp = await api('/api/unitree/sync', { method: 'POST' });
    if (resp.status === 'ok') {
      const s = resp.stats || {};
      showToast(`同步完成: 动作${s.motions}/舞蹈${s.dances}/地图${s.maps}/点位${s.points}`, 'ok');
      if (out) out.textContent = `✅ 动作 ${s.motions} / 舞蹈 ${s.dances} / 地图 ${s.maps} / 点位 ${s.points}`;
      // 刷新前端缓存的下拉数据
      try {
        const [mResp, dResp, mapResp] = await Promise.all([
          api('/api/unitree/motions'),
          api('/api/unitree/dances'),
          api('/api/unitree/maps'),
        ]);
        state.unitree = {
          motions: mResp.data || [],
          dances: dResp.data || [],
          maps: mapResp.data || [],
          points: {},
        };
        try { initUnitreeCtrlPanel(); } catch (_) {}
      } catch (_) {}
    } else {
      showToast('同步失败: ' + (resp.message || ''), 'error');
      if (out) out.textContent = '❌ ' + (resp.message || '');
    }
  } catch (e) {
    showToast('同步失败: ' + e.message, 'error');
    if (out) out.textContent = '❌ ' + e.message;
  }
}


// ============================================================
// 访客记忆中心
// ============================================================
let _visitorInited = false;
let _visitorCategories = [];
let _visitorTags = [];
let _currentVisitorId = null;

async function initVisitorTab() {
  if (_visitorInited) return;
  _visitorInited = true;
  await Promise.allSettled([loadVisitorMeta(), loadVisitors(), loadRegisterTasks()]);
}

async function loadVisitorMeta() {
  try {
    const [catResp, tagResp] = await Promise.all([
      api('/api/visitors/meta/categories'),
      api('/api/visitors/meta/tags'),
    ]);
    _visitorCategories = catResp.data || [];
    _visitorTags = tagResp.data || [];
    // fill filters
    const catSel = document.getElementById('visitor-category-filter');
    if (catSel) {
      catSel.innerHTML = '<option value="">全部分类</option>' +
        _visitorCategories.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
    }
    const tagSel = document.getElementById('visitor-tag-filter');
    if (tagSel) {
      tagSel.innerHTML = '<option value="">全部标签</option>' +
        _visitorTags.map(t => `<option value="${t.id}">${escapeHtml(t.name)}</option>`).join('');
    }
    // fill robot select for registration
    try {
      const robotResp = await api('/api/robots');
      const robots = (robotResp.data || []).filter(r => r.status !== 'offline');
      fillSelect('reg-robot', robots, { valueKey: 'id', labelKey: 'name', placeholder: '选择机器人' });
    } catch(e) {}
    // render tags/categories management
    renderCategoriesList();
    renderTagsList();
  } catch (e) { console.error('loadVisitorMeta', e); }
}

function showVisitorSub(name) {
  ['list', 'detail', 'register', 'welcome', 'tags'].forEach(s => {
    const el = document.getElementById('visitor-sub-' + s);
    if (el) el.style.display = (s === name) ? '' : 'none';
  });
  // 切换子标签按钮的激活态。detail 复用 list 按钮的高亮。
  const activeSub = (name === 'detail') ? 'list' : name;
  document.querySelectorAll('.visitor-subtabs [data-sub]').forEach(btn => {
    if (btn.dataset.sub === activeSub) {
      btn.classList.add('btn-primary');
    } else {
      btn.classList.remove('btn-primary');
    }
  });
  // 离开 register 子页时, 停止轮询
  if (name !== 'register' && window._regTasksAutoRefresh) {
    clearTimeout(window._regTasksAutoRefresh);
    window._regTasksAutoRefresh = null;
  }
  if (name === 'welcome') loadWelcomeConfig();
  if (name === 'list') loadVisitors();
  if (name === 'register') loadRegisterTasks();
}

// ---- Visitor List ----
async function loadVisitors() {
  try {
    const search = document.getElementById('visitor-search')?.value || '';
    const catId = document.getElementById('visitor-category-filter')?.value || '';
    const tagId = document.getElementById('visitor-tag-filter')?.value || '';
    let url = `/api/visitors?limit=50&offset=0`;
    if (search) url += `&search=${encodeURIComponent(search)}`;
    if (catId) url += `&category_id=${catId}`;
    if (tagId) url += `&tag_id=${tagId}`;
    const resp = await api(url);
    const tbody = document.getElementById('visitor-table-body');
    if (!tbody) return;
    if (!resp.data || resp.data.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-muted);">暂无访客数据</td></tr>';
      return;
    }
    tbody.innerHTML = resp.data.map(v => {
      const avatar = v.avatar_url
        ? `<img src="${escapeHtml(v.avatar_url)}" style="width:36px;height:36px;border-radius:50%;object-fit:cover;">`
        : '<span style="display:inline-block;width:36px;height:36px;border-radius:50%;background:var(--border);text-align:center;line-height:36px;">👤</span>';
      const catBadge = v.category_name
        ? `<span style="padding:2px 8px;border-radius:10px;font-size:0.75rem;background:${v.category_color||'#64748b'};color:white;">${escapeHtml(v.category_name)}</span>`
        : '<span style="color:var(--text-muted);">-</span>';
      const tags = (v.tags || []).map(t =>
        `<span style="padding:1px 6px;border-radius:8px;font-size:0.7rem;background:${t.color||'#94a3b8'};color:white;margin:1px;">${escapeHtml(t.name)}</span>`
      ).join('');
      return `<tr>
        <td>${avatar}</td>
        <td><a href="#" onclick="openVisitorDetail(${v.id});return false;" style="color:var(--primary);text-decoration:none;font-weight:600;">${escapeHtml(v.display_name || v.name || '-')}</a></td>
        <td>${catBadge}</td>
        <td>${tags || '-'}</td>
        <td>${v.visit_count || 0}</td>
        <td>${v.last_seen_at || '-'}</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(v.profile_summary || '-')}</td>
        <td><button class="btn btn-sm" onclick="openVisitorDetail(${v.id})">详情</button></td>
      </tr>`;
    }).join('');
  } catch (e) { console.error('loadVisitors', e); }
}

// ---- Visitor Detail ----
async function openVisitorDetail(id) {
  _currentVisitorId = id;
  showVisitorSub('detail');
  try {
    const resp = await api(`/api/visitors/${id}`);
    const v = resp.data;
    if (!v) { showToast('访客不存在', 'error'); return; }
    // Avatar
    const avatarEl = document.getElementById('visitor-detail-avatar');
    if (v.avatar_url) {
      avatarEl.innerHTML = `<img src="${escapeHtml(v.avatar_url)}" style="width:80px;height:80px;border-radius:50%;object-fit:cover;">`;
    } else {
      avatarEl.innerHTML = '<span style="font-size:2rem;color:var(--text-muted);">👤</span>';
    }
    // Fields
    document.getElementById('vd-display-name').value = v.display_name || v.name || '';
    document.getElementById('vd-notes').value = v.notes || '';
    document.getElementById('vd-status').value = v.status || 'active';
    // Category select
    const catSel = document.getElementById('vd-category');
    catSel.innerHTML = '<option value="">无分类</option>' +
      _visitorCategories.map(c => `<option value="${c.id}" ${c.id==v.category_id?'selected':''}>${escapeHtml(c.name)}</option>`).join('');
    // Tags
    const tagsContainer = document.getElementById('vd-tags-container');
    const existingTagIds = new Set((v.tags||[]).map(t => t.id));
    tagsContainer.innerHTML = _visitorTags.map(t => {
      const checked = existingTagIds.has(t.id) ? 'checked' : '';
      return `<label style="display:inline-flex;align-items:center;gap:2px;padding:2px 8px;border-radius:12px;background:${checked?t.color:'var(--bg)'};color:${checked?'white':'var(--text)'};font-size:0.8rem;cursor:pointer;border:1px solid ${t.color};">
        <input type="checkbox" value="${t.id}" ${checked} style="display:none;" onchange="this.parentElement.style.background=this.checked?'${t.color}':'var(--bg)';this.parentElement.style.color=this.checked?'white':'var(--text)';">
        ${escapeHtml(t.name)}
      </label>`;
    }).join('');
    // Stats
    document.getElementById('vd-visit-count').textContent = v.visit_count || 0;
    document.getElementById('vd-memory-count').textContent = (v.recent_memories || []).length;
    document.getElementById('vd-first-seen').textContent = v.first_seen_at || '-';
    document.getElementById('vd-last-seen').textContent = v.last_seen_at || '-';
    document.getElementById('vd-last-robot').textContent = v.last_robot_id || '-';
    document.getElementById('vd-source').textContent = v.source || '-';
    document.getElementById('vd-profile-summary').textContent = v.profile_summary || '暂无画像数据';
    // Memories table
    const memBody = document.getElementById('vd-memories-body');
    const memories = v.recent_memories || [];
    if (memories.length === 0) {
      memBody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted);">暂无问答记录</td></tr>';
    } else {
      memBody.innerHTML = memories.map(m => `<tr>
        <td style="white-space:nowrap;">${(m.created_at||'').slice(5,16)}</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(m.question||'')}</td>
        <td style="max-width:250px;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(m.answer||'')}</td>
        <td>${escapeHtml(m.intent||'-')}</td>
        <td>${escapeHtml(m.topic||'-')}</td>
        <td><span style="padding:1px 6px;border-radius:8px;font-size:0.7rem;background:${m.memory_level==='long'?'var(--primary)':'var(--text-muted)'};color:white;">${m.memory_level||'short'}</span></td>
      </tr>`).join('');
    }
  } catch (e) { showToast('加载失败: ' + e.message, 'error'); }
}

async function saveVisitorDetail() {
  if (!_currentVisitorId) return;
  try {
    const tagCheckboxes = document.querySelectorAll('#vd-tags-container input[type=checkbox]:checked');
    const tagIds = Array.from(tagCheckboxes).map(cb => parseInt(cb.value));
    await api(`/api/visitors/${_currentVisitorId}`, {
      method: 'PUT',
      body: JSON.stringify({
        display_name: document.getElementById('vd-display-name').value,
        category_id: document.getElementById('vd-category').value ? parseInt(document.getElementById('vd-category').value) : null,
        status: document.getElementById('vd-status').value,
        notes: document.getElementById('vd-notes').value,
      }),
    });
    await api(`/api/visitors/${_currentVisitorId}/tags`, {
      method: 'PUT',
      body: JSON.stringify(tagIds),
    });
    setResult('vd-save-result', '保存成功', true);
    showToast('访客信息已保存', 'ok');
  } catch (e) {
    setResult('vd-save-result', '保存失败: ' + e.message, false);
  }
}

// ---- Face Registration ----
async function doFaceRegister() {
  const name = document.getElementById('reg-name')?.value?.trim();
  const robotId = document.getElementById('reg-robot')?.value;
  if (!name) { setResult('reg-result', '请输入姓名', false); showToast('请输入姓名', 'error'); return; }
  if (!robotId) { setResult('reg-result', '请选择机器人', false); showToast('请选择机器人', 'error'); return; }

  // 前端先做一次姓名去重预检: 访客列表里如果已有同名, 直接拦截
  try {
    const checkResp = await api(`/api/visitors?search=${encodeURIComponent(name)}&limit=20`);
    const hit = (checkResp.data || []).find(v =>
      (v.name || '').trim() === name || (v.display_name || '').trim() === name
    );
    if (hit) {
      const msg = `姓名「${name}」已存在 (访客 #${hit.id})，请换一个名字。`;
      setResult('reg-result', msg, false);
      showToast(msg, 'error');
      return;
    }
  } catch (e) { /* 预检失败不阻断, 交给后端兜底 */ }

  try {
    const resp = await api('/api/visitors/register-face', {
      method: 'POST',
      body: JSON.stringify({ robotId, name }),
    });
    if (resp.status === 'ok') {
      setResult('reg-result', '注册命令已发送，等待机器人执行...', true);
      showToast('注册命令已发送', 'ok');
      document.getElementById('reg-name').value = '';
      setTimeout(loadRegisterTasks, 2000);
    } else {
      const msg = resp.message || '注册失败';
      setResult('reg-result', msg, false);
      showToast(msg, 'error');
    }
  } catch (e) {
    const msg = '发送失败: ' + e.message;
    setResult('reg-result', msg, false);
    showToast(msg, 'error');
  }
}

async function loadRegisterTasks() {
  // 清除前一次调度, 防止重复计时器
  if (window._regTasksAutoRefresh) {
    clearTimeout(window._regTasksAutoRefresh);
    window._regTasksAutoRefresh = null;
  }
  try {
    const resp = await api('/api/visitors/register-tasks?limit=20');
    const tbody = document.getElementById('reg-tasks-body');
    if (!tbody) return;
    const tasks = resp.data || [];
    if (tasks.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);">暂无注册任务</td></tr>';
      return;
    }
    const statusColors = { pending:'var(--text-muted)', sent:'var(--warning)', started:'var(--warning)', success:'var(--success)', failed:'var(--danger)', timeout:'var(--danger)' };
    const statusLabels = { pending:'等待中', sent:'已发送', started:'执行中', success:'成功', failed:'失败', timeout:'超时' };
    tbody.innerHTML = tasks.map(t => `<tr>
      <td>${escapeHtml(t.visitor_name||'-')}</td>
      <td>${escapeHtml(t.robot_id||'-')}</td>
      <td><span style="color:${statusColors[t.status]||'inherit'};font-weight:600;">${statusLabels[t.status]||t.status}</span></td>
      <td style="white-space:nowrap;">${(t.created_at||'').slice(5,16)}</td>
      <td style="font-size:0.8rem;color:var(--text-muted);">${t.error_message ? escapeHtml(t.error_message) : (t.robot_user_id || '-')}</td>
    </tr>`).join('');

    // 只有当前还在 register 子页时, 才持续轮询
    const hasPending = tasks.some(t => ['pending','sent','started'].includes(t.status));
    const onRegSub = document.getElementById('visitor-sub-register') &&
                      document.getElementById('visitor-sub-register').style.display !== 'none';
    if (hasPending && onRegSub) {
      window._regTasksAutoRefresh = setTimeout(() => {
        window._regTasksAutoRefresh = null;
        const stillOn = document.getElementById('visitor-sub-register') &&
                         document.getElementById('visitor-sub-register').style.display !== 'none';
        if (stillOn) loadRegisterTasks();
      }, 10000);  // 10s 更积极, 超时是 30s/120s, 多轮能看到状态变化
    }
  } catch (e) { console.error('loadRegisterTasks', e); }
}

// ---- Welcome Config ----
async function loadWelcomeConfig() {
  try {
    const resp = await api('/api/visitors/config/welcome');
    const c = resp.data || {};
    document.getElementById('wc-enabled').value = c.visitor_welcome_enabled || '1';
    document.getElementById('wc-mode').value = c.visitor_welcome_mode || 'recognized';
    document.getElementById('wc-cooldown').value = c.visitor_welcome_cooldown || '300';
    document.getElementById('wc-binding-ttl').value = c.visitor_binding_ttl || '300';
    document.getElementById('wc-lock-duration').value = c.visitor_lock_duration || '60';
    document.getElementById('wc-say-name').value = c.visitor_welcome_say_name || '1';
    document.getElementById('wc-generic').value = c.visitor_welcome_generic || '';
    document.getElementById('wc-returning').value = c.visitor_welcome_returning || '';
    document.getElementById('wc-vip').value = c.visitor_welcome_vip || '';
  } catch (e) { console.error('loadWelcomeConfig', e); }
}

async function saveWelcomeConfig() {
  try {
    await api('/api/visitors/config/welcome', {
      method: 'PUT',
      body: JSON.stringify({
        visitor_welcome_enabled: document.getElementById('wc-enabled').value,
        visitor_welcome_mode: document.getElementById('wc-mode').value,
        visitor_welcome_cooldown: document.getElementById('wc-cooldown').value,
        visitor_binding_ttl: document.getElementById('wc-binding-ttl').value,
        visitor_lock_duration: document.getElementById('wc-lock-duration').value,
        visitor_welcome_say_name: document.getElementById('wc-say-name').value,
        visitor_welcome_generic: document.getElementById('wc-generic').value,
        visitor_welcome_returning: document.getElementById('wc-returning').value,
        visitor_welcome_vip: document.getElementById('wc-vip').value,
      }),
    });
    setResult('wc-result', '配置已保存', true);
    showToast('欢迎配置已保存', 'ok');
  } catch (e) {
    setResult('wc-result', '保存失败: ' + e.message, false);
  }
}

// ---- Categories & Tags Management ----
function renderCategoriesList() {
  const el = document.getElementById('categories-list');
  if (!el) return;
  el.innerHTML = _visitorCategories.map(c =>
    `<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);">
      <span style="width:16px;height:16px;border-radius:50%;background:${c.color};flex-shrink:0;"></span>
      <span style="flex:1;">${escapeHtml(c.name)}</span>
      <span style="color:var(--text-muted);font-size:0.8rem;">${escapeHtml(c.description||'')}</span>
    </div>`
  ).join('') || '<div style="color:var(--text-muted);">暂无分类</div>';
}

function renderTagsList() {
  const el = document.getElementById('tags-list');
  if (!el) return;
  el.innerHTML = _visitorTags.map(t =>
    `<span style="display:inline-block;padding:3px 10px;border-radius:12px;background:${t.color};color:white;font-size:0.8rem;margin:2px;">${escapeHtml(t.name)}</span>`
  ).join('') || '<div style="color:var(--text-muted);">暂无标签</div>';
}

async function addCategory() {
  const name = document.getElementById('new-cat-name')?.value?.trim();
  const color = document.getElementById('new-cat-color')?.value || '#64748b';
  if (!name) return;
  try {
    await api('/api/visitors/meta/categories', {
      method: 'POST', body: JSON.stringify({ name, color }),
    });
    document.getElementById('new-cat-name').value = '';
    await loadVisitorMeta();
    showToast('分类已添加', 'ok');
  } catch (e) { showToast('添加失败: ' + e.message, 'error'); }
}

async function addTag() {
  const name = document.getElementById('new-tag-name')?.value?.trim();
  const color = document.getElementById('new-tag-color')?.value || '#94a3b8';
  if (!name) return;
  try {
    await api('/api/visitors/meta/tags', {
      method: 'POST', body: JSON.stringify({ name, color, type: 'custom' }),
    });
    document.getElementById('new-tag-name').value = '';
    await loadVisitorMeta();
    showToast('标签已添加', 'ok');
  } catch (e) { showToast('添加失败: ' + e.message, 'error'); }
}
