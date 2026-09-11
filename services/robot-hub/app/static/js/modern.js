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
  try {
    await loadBootstrapData();
    bindDefaults();
    await Promise.all([
      loadStats(),
      loadWorkflows(),
      loadBindings(),
      loadTourPoints(),
      loadIntentMappings(),
      loadSchedules(),
      loadLogs(),
      loadRobotConfig(),
    ]);
    showToast('系统初始化完成', 'ok');
  } catch (e) {
    console.error(e);
    showToast(`初始化失败: ${e.message}`, 'error');
  }
});

async function loadBootstrapData() {
  const [refResp, cfgResp] = await Promise.all([
    api('/api/ref/all'),
    api('/api/system/config'),
  ]);

  state.refs = refResp.data || state.refs;

  document.getElementById('current-special-badge').textContent = `当前专场: ${currentSpecialName()}`;
  document.getElementById('sync-time').textContent = `同步: ${cfgResp.data.last_sync_time || '--'}`;
}

function bindDefaults() {
  // 控制面板
  fillSelect('ctrl-special', state.refs.specials);
  fillSelect('ctrl-res-special', state.refs.specials);
  fillSelect('ctrl-group', state.refs.group_commands);
  fillSelect('ctrl-eq-area', state.refs.areas);
  fillSelect('ctrl-nav-point', state.refs.positions, { valueKey: 'position_id', labelKey: 'name' });
  fillSelect('wf-special', state.refs.specials, { allowEmpty: true, emptyLabel: '全部专场' });

  // 绑定页
  fillSelect('bind-position', state.refs.positions, { valueKey: 'position_id', labelKey: 'name' });
  updateBindTargetOptions();

  // 参观配置页
  fillSelect('tp-position', state.refs.positions, { valueKey: 'position_id', labelKey: 'name' });
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
function switchTab(name) {
  document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));

  const tabs = Array.from(document.querySelectorAll('.tab'));
  const tabNames = ['dashboard', 'control', 'workflows', 'bindings', 'tourpoints', 'intents', 'schedules', 'settings', 'logs'];
  const idx = tabNames.indexOf(name);
  if (tabs[idx]) tabs[idx].classList.add('active');
  document.getElementById(`tab-${name}`).classList.add('active');

  if (name === 'settings') loadSettings();
}

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
  try {
    const resp = await api('/api/system/sync', { method: 'POST' });
    showToast(resp.message || '同步任务已触发', 'ok');
  } catch (e) {
    showToast(e.message, 'error');
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
    const resp = await api('/api/control/play_resource', {
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
  const resp = await api('/api/workflows');
  state.workflows = resp.data || [];
  const el = document.getElementById('workflow-list');
  el.innerHTML = state.workflows.map(w => `
    <div class="card-item">
      <div class="card-item-info">
        <div class="card-item-title">${escapeHtml(w.name)} ${w.enabled ? '' : '<span class="badge badge-muted">已禁用</span>'}</div>
        <div class="card-item-subtitle">触发词：${escapeHtml(w.trigger_word)} | 专场：${escapeHtml(w.special_name || '全部')} | 步骤：${w.step_count}</div>
      </div>
      <div class="card-item-actions">
        <button class="btn btn-sm btn-success" onclick="runWorkflow(${w.id})" title="手动执行">▶ 执行</button>
        <button class="btn btn-sm" onclick="editWorkflow(${w.id})">编辑</button>
        <button class="btn btn-sm ${w.enabled ? '' : 'btn-success'}" onclick="toggleWorkflow(${w.id}, ${w.enabled ? 0 : 1})">${w.enabled ? '禁用' : '启用'}</button>
        <button class="btn btn-sm btn-danger" onclick="deleteWorkflow(${w.id})">删除</button>
      </div>
    </div>
  `).join('') || '<div class="panel">暂无流程</div>';
}

// ============================================================
// 图形化拖拽流程编辑器
// ============================================================

const STEP_TYPES = {
  navigate:       { emoji: '🗺️', label: '导航到点位', hasCallback: true },
  tts:            { emoji: '🎤', label: '语音播报', hasCallback: true },
  show:           { emoji: '📺', label: '屏幕展示', hasCallback: true },
  resource_play:  { emoji: '▶️', label: '播放资源', hasCallback: false },
  switch_special: { emoji: '🎛️', label: '切换专场', hasCallback: false },
  group_command:  { emoji: '⚡', label: '组策略', hasCallback: false },
  equipment:      { emoji: '🔌', label: '设备控制', hasCallback: false },
  delay:          { emoji: '⏱️', label: '延时等待', hasCallback: false },
  listen:         { emoji: '👂', label: '开始收音', hasCallback: true },
  end:            { emoji: '🏁', label: '结束', hasCallback: false },
  custom_request: { emoji: '🌐', label: '自定义请求', hasCallback: false },
  digital_human: { emoji: '🤖', label: '数字人控制', hasCallback: false },
  pad_active:    { emoji: '📱', label: '激活展项', hasCallback: false },
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

  div.innerHTML = `
    <div class="step-label">
      <span class="step-number">${idx + 1}</span>
      <span class="step-title">${info.emoji} ${escapeHtml(info.label)}</span>
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

async function toggleStepConfig(stepEl, type) {
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
      const savedVal = rsEl.dataset.presetValue || '';
      await loadResourceList(exEl, savedVal);
      // 资源回填后加载讲解词
      if (rsEl.value) {
        loadNarrationForStep(rsEl);
      }
    } else if (rsEl && rsEl.value) {
      // 资源已有值，直接加载讲解词
      loadNarrationForStep(rsEl);
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
    delay: `<div class="form-group"><label>延时秒数</label><input class="cfg-seconds" type="number" value="30"></div>`,
    listen: `<div class="form-group"><label>收音超时秒数</label><input class="cfg-seconds" type="number" value="30"></div>`,
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
        <input class="cfg-resetDelay" type="number" value="10" placeholder="复位后等待时间，默认10秒">
      </div>`,
    pad_active: `
      <div class="form-group"><label>展项</label>
        <select class="cfg-exhibitionID select-full">${exhibitOpts}</select>
        <p class="hint">通知Pad系统激活该展项的数字人，在Enter前调用。</p>
      </div>`,
  };

  // 根据步骤类型设置默认等待方式
  const typeInfo = STEP_TYPES[type] || {};
  const defaultWait = typeInfo.hasCallback ? 'callback' : 'none';

  const waitHtml = `
    <hr style="border-color:rgba(255,255,255,0.2);margin:10px 0">
    <div class="form-group"><label>等待方式</label>
      <select class="cfg-wait_type select-full">
        <option value="callback">等待回调</option>
        <option value="delay">固定延时</option>
        <option value="none">不等待</option>
      </select>
    </div>
    <div class="form-group"><label>超时/延时（秒）</label><input class="cfg-wait_timeout" type="number" value="30"></div>
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

  const wtEl = panel.querySelector('.cfg-wait_type');
  if (wtEl) wtEl.value = waitType || defaultWait;
  const wtoEl = panel.querySelector('.cfg-wait_timeout');
  if (wtoEl) wtoEl.value = waitTimeout || 30;
  const descEl = panel.querySelector('.cfg-description');
  if (descEl) descEl.value = description || '';

  // 导航: pointId 回填到 cfg-pointId 下拉框
  if (type === 'navigate' && config) {
    const navEl = panel.querySelector('.cfg-pointId');
    let pid = config.pointId || config.position || '';
    // 模板步骤只有 pointName 没有 pointId，按名称查找 position_id
    if (!pid && config.pointName) {
      const pos = (state.refs.positions || []).find(p => p.name === config.pointName);
      if (pos) pid = pos.position_id;
    }
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

  // 播放资源级联: 设置专场/展项值，并保存资源预设值到 dataset
  if (type === 'resource_play') {
    const sp = config ? (config.special || '') : '';
    const ex = config ? (config.exhibit || config.exhibitId || '') : '';
    const rs = config ? (config.resource || config.resourceId || '') : '';
    const spEl = panel.querySelector('.cfg-special');
    const exEl = panel.querySelector('.cfg-exhibit');
    const rsEl = panel.querySelector('.cfg-resource');
    if (spEl && sp) spEl.value = sp;
    if (exEl && ex) exEl.value = ex;
    if (rsEl) rsEl.dataset.presetValue = rs;
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
  stepEl.querySelectorAll('.step-config-panel input[class*="cfg-"], .step-config-panel select[class*="cfg-"], .step-config-panel textarea[class*="cfg-"]').forEach(el => {
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
    wait_type: stepEl.querySelector('.cfg-wait_type')?.value || 'callback',
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

    // 所有步骤创建完成后，统一触发 resource_play 的资源级联加载
    await reloadAllResourceSteps();
  } catch (e) {
    showToast(`加载流程失败: ${e.message}`, 'error');
  }
}

async function reloadAllResourceSteps() {
  const steps = document.querySelectorAll('#wf-steps .canvas-step');
  for (const stepEl of steps) {
    if (stepEl.dataset.type !== 'resource_play') continue;
    const panel = stepEl.querySelector('.step-config-panel');
    if (!panel) continue;
    const spEl = panel.querySelector('.cfg-special');
    const exEl = panel.querySelector('.cfg-exhibit');
    const rsEl = panel.querySelector('.cfg-resource');
    if (!spEl || !spEl.value || !exEl || !exEl.value) continue;

    // 加载资源列表
    try {
      const resp = await api(`/api/ref/resources?special_id=${spEl.value}&exhibit_id=${exEl.value}`);
      if (rsEl) {
        rsEl.innerHTML = optionsHtml(resp.data || []);
        // 尝试回填已有的资源值，没有则默认选第一个
        const savedVal = rsEl.dataset.presetValue || '';
        if (savedVal) {
          rsEl.value = savedVal;
        } else if (resp.data && resp.data.length > 0) {
          rsEl.value = resp.data[0].id;
        }
      }
    } catch (e) {
      console.error('reloadAllResourceSteps error', e);
    }
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
  try {
    // 获取在线机器人列表
    const resp = await api('/api/robots');
    const robots = (resp.data || []).filter(r => r.status !== 'offline');
    if (robots.length === 0) {
      showToast('没有在线的机器人，无法执行流程', 'error');
      return;
    }

    let robotId;
    if (robots.length === 1) {
      // 只有一个在线机器人，直接用
      robotId = robots[0].id;
    } else {
      // 多个在线机器人，让用户选择
      const options = robots.map((r, i) => `${i + 1}. ${r.id} ${r.name ? '(' + r.name + ')' : ''}`).join('\n');
      const choice = prompt(`选择执行机器人：\n${options}\n\n输入序号：`);
      if (!choice) return;
      const idx = parseInt(choice) - 1;
      if (idx < 0 || idx >= robots.length) {
        showToast('无效的选择', 'error');
        return;
      }
      robotId = robots[idx].id;
    }

    if (!confirm(`确认使用机器人 [${robotId}] 执行该流程？`)) return;

    const result = await api(`/api/workflows/${id}/run`, {
      method: 'POST',
      body: JSON.stringify({ robot_id: robotId }),
    });

    if (result.status === 'ok') {
      showToast(result.message || '流程已启动', 'ok');
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

async function createBinding() {
  const positionId = document.getElementById('bind-position').value;
  const bindType = document.getElementById('bind-type').value;
  const bindTargetId = Number(document.getElementById('bind-target').value || 0);
  if (!positionId || !bindTargetId) return showToast('请选择点位和绑定目标', 'error');

  try {
    await api('/api/system/position_bindings', {
      method: 'POST',
      body: JSON.stringify({ position_id: positionId, bind_type: bindType, bind_target_id: bindTargetId }),
    });
    showToast('绑定已保存', 'ok');
    await loadBindings();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function loadBindings() {
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
}

async function deleteBinding(id) {
  if (!confirm('确定删除这个绑定吗？')) return;
  await api(`/api/system/position_bindings/${id}`, { method: 'DELETE' });
  showToast('绑定已删除', 'ok');
  await loadBindings();
}

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
        <div class="card-item-title">${t.enabled ? '✅' : '❌'} ${escapeHtml(t.position_name || t.position_id)}</div>
        <div class="card-item-subtitle">结束语：${escapeHtml(t.end_phrase || '默认')} | 返回：${escapeHtml(t.return_position_name || '接待点')}</div>
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
  if (!position_id) return showToast('请选择点位', 'error');
  try {
    await api('/api/system/tour_points', {
      method: 'POST',
      body: JSON.stringify({ position_id, enabled, end_phrase, return_position_id }),
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
async function loadLogs() {
  const type = document.getElementById('log-type-filter')?.value || '';
  const resp = await api(`/api/system/logs?limit=100${type ? `&log_type=${encodeURIComponent(type)}` : ''}`);
  state.logs = resp.data || [];
  const tbody = document.getElementById('log-table-body');
  if (!tbody) return;
  tbody.innerHTML = state.logs.map(log => {
    let detail = log.detail || '';
    // robot_command 类型直接展示原始 JSON 命令(用户要求看到完整指令), 不要解构
    if (log.log_type !== 'robot_command') {
      // 解析JSON格式的detail，提取可读内容
      try {
        const parsed = JSON.parse(detail);
        if (parsed.text !== undefined) {
          detail = `🗣️ 「${parsed.text}」`;
          if (parsed.confidence) detail += ` (置信度:${(parsed.confidence * 100).toFixed(0)}%)`;
        } else {
          detail = Object.entries(parsed).map(([k,v]) => `${k}:${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' | ');
        }
      } catch(_) {}
    }
    return `
    <tr>
      <td>${escapeHtml(log.created_at || '')}</td>
      <td>${escapeHtml(log.log_type || '')}</td>
      <td>${escapeHtml(log.source || '')}</td>
      <td>${escapeHtml(log.action || '')}</td>
      <td title="${escapeHtml(log.detail || '')}" style="max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(detail)}</td>
      <td>${escapeHtml(log.status || '')}</td>
      <td>${log.duration_ms || 0}ms</td>
    </tr>`;
  }).join('') || '<tr><td colspan="7">暂无日志</td></tr>';
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

async function robotTts() {
  const text = document.getElementById('ctrl-tts-text')?.value?.trim();
  if (!text) return showToast('请输入播报内容', 'error');
  await robotCmd('tts', { text, priority: 'normal' });
}

async function robotNavigate() {
  const sel = document.getElementById('ctrl-nav-point');
  if (!sel?.value) return showToast('请选择点位', 'error');
  const pos = (state.refs.positions || []).find(p => p.position_id === sel.value);
  const pointName = pos?.name || '';
  await robotCmd('navigate', { pointId: sel.value, pointName, command: `go_${sel.value}` });
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

async function loadRobotConfig() {
  try {
    const resp = await api('/api/system/config');
    const cfg = resp.data || {};
    // wake_word 已固定为"小豹小豹"，不再走后台配置，UI 只展示不可编辑
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
        onlineEl.innerHTML = online.map(r =>
          `<div style="padding:4px 0">🤖 <b>${r.name || r.id}</b> — ${r.status} | 电量 ${r.battery || '--'}% | ${r.location || ''}</div>`
        ).join('');
        // 填充控制区机器人下拉框
        const sel = document.getElementById('ctrl-robot-id');
        if (sel) {
          sel.innerHTML = online.map(r =>
            `<option value="${r.id}">${r.name || r.id}</option>`
          ).join('');
        }
        if (controlArea) controlArea.style.display = '';
      }
    }
  } catch (e) { console.warn('加载机器人状态失败:', e); }

  // 加载配置下发日志
  try {
    const logResp = await api('/api/system/logs?log_type=config_push&limit=5');
    const logs = logResp.data || [];
    const logEl = document.getElementById('cfg-push-logs');
    if (logEl) {
      if (logs.length === 0) {
        logEl.innerHTML = '<span style="color:#94a3b8">暂无记录</span>';
      } else {
        logEl.innerHTML = logs.map(l =>
          `<div style="padding:2px 0;font-size:13px">✅ ${l.created_at} → <b>${l.target}</b> | ${l.detail}</div>`
        ).join('');
      }
    }
  } catch (e) { console.warn('加载下发日志失败:', e); }
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
window.runWorkflow = runWorkflow;
window.loadWorkflows = loadWorkflows;
window.loadBindings = loadBindings;
window.loadTourPoints = loadTourPoints;
window.saveTourPoint = saveTourPoint;
window.editTourPoint = editTourPoint;
window.deleteTourPoint = deleteTourPoint;
window.loadIntentMappings = loadIntentMappings;
window.loadSchedules = loadSchedules;
window.renumberSteps = renumberSteps;
window.reorderSteps = reorderSteps;
window.saveRobotConfig = saveRobotConfig;
window.loadRobotConfig = loadRobotConfig;
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
      showToast(`流程 "${resp.name}" 已创建`, 'success');
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
    showToast('讲解词已保存', 'success');
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
    showToast('AI 讲解词已生成并保存', 'success');
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
  'openclaw_enabled',
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
      showToast('测试消息已发送', 'success');
    }
  } catch (e) {
    showToast('发送失败: ' + e.message, 'error');
  }
}
