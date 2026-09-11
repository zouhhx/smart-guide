"use strict";

const $ = (id) => document.getElementById(id);
const REALTIME_POLL_MS = 50;

const elements = {
  headerStatusDot: $("headerStatusDot"),
  headerStatusText: $("headerStatusText"),
  heroTitle: $("heroTitle"),
  heroDescription: $("heroDescription"),
  primaryAction: $("primaryAction"),
  refreshAll: $("refreshAll"),
  pipelineState: $("pipelineState"),
  uptime: $("uptime"),
  processId: $("processId"),
  deviceSelect: $("deviceSelect"),
  deviceMeta: $("deviceMeta"),
  refreshDevices: $("refreshDevices"),
  volumeSlider: $("volumeSlider"),
  volumeValue: $("volumeValue"),
  volumeMessage: $("volumeMessage"),
  inputMeter: $("inputMeter"),
  inputMeterFill: $("inputMeterFill"),
  inputMeterPeak: $("inputMeterPeak"),
  inputMeterValue: $("inputMeterValue"),
  inputMeterStatus: $("inputMeterStatus"),
  activationMode: $("activationMode"),
  activationModeHint: $("activationModeHint"),
  levelTriggerControls: $("levelTriggerControls"),
  levelThreshold: $("levelThreshold"),
  levelThresholdValue: $("levelThresholdValue"),
  levelThresholdNumber: $("levelThresholdNumber"),
  applyLevelThreshold: $("applyLevelThreshold"),
  levelTriggerComparison: $("levelTriggerComparison"),
  kwsSettings: $("kwsSettings"),
  keywordInput: $("keywordInput"),
  kwsScore: $("kwsScore"),
  kwsScoreValue: $("kwsScoreValue"),
  kwsThreshold: $("kwsThreshold"),
  kwsThresholdValue: $("kwsThresholdValue"),
  vadSilence: $("vadSilence"),
  vadSilenceValue: $("vadSilenceValue"),
  restoreDefaults: $("restoreDefaults"),
  saveConfig: $("saveConfig"),
  kwsListeningIndicator: $("kwsListeningIndicator"),
  listeningKicker: $("listeningKicker"),
  kwsListeningTitle: $("kwsListeningTitle"),
  kwsListeningHint: $("kwsListeningHint"),
  kwsListeningBadge: $("kwsListeningBadge"),
  lastAsrText: $("lastAsrText"),
  lastActivationLabel: $("lastActivationLabel"),
  lastWake: $("lastWake"),
  lastLanguage: $("lastLanguage"),
  lastAsrDuration: $("lastAsrDuration"),
  lastSpeechDuration: $("lastSpeechDuration"),
  asrStatusBadge: $("asrStatusBadge"),
  modelAnswer: $("modelAnswer"),
  modelStatusBadge: $("modelStatusBadge"),
  modelTarget: $("modelTarget"),
  modelSeq: $("modelSeq"),
  modelDuration: $("modelDuration"),
  logOutput: $("logOutput"),
  refreshLogs: $("refreshLogs"),
  autoScroll: $("autoScroll"),
  appVersion: $("appVersion"),
  toast: $("toast"),
};

const state = {
  status: null,
  config: null,
  devices: [],
  busy: false,
  meterBusy: false,
  meterPeak: 0,
  meterDbfs: -120,
  realtimeBusy: false,
  realtimeTimer: null,
  realtimeFailures: 0,
  statusSignature: "",
  logCursor: 0,
  logStreamId: "",
  toastTimer: null,
};

const kwsPresets = {
  stable: { score: 1.1, threshold: 0.4 },
  balanced: { score: 1.5, threshold: 0.25 },
  sensitive: { score: 2.2, threshold: 0.17 },
};

const vadPresets = {
  fast: { silence: 0.35 },
  balanced: { silence: 0.6 },
  complete: { silence: 1.0 },
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `请求失败（${response.status}）`);
  }
  return payload;
}

function showToast(message, isError = false) {
  clearTimeout(state.toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.toggle("error", isError);
  elements.toast.classList.add("visible");
  state.toastTimer = setTimeout(() => {
    elements.toast.classList.remove("visible");
  }, isError ? 5200 : 3000);
}

function setBusy(busy) {
  state.busy = busy;
  [
    elements.primaryAction,
    elements.saveConfig,
    elements.restoreDefaults,
    elements.refreshDevices,
    elements.applyLevelThreshold,
  ].forEach((button) => {
    button.disabled = busy;
  });
  renderActivationControls();
}

function formatDuration(seconds) {
  const total = Math.max(0, Number(seconds) || 0);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return [hours, minutes, secs]
    .map((value) => String(value).padStart(2, "0"))
    .join(":");
}

function selectedDeviceValue() {
  return elements.deviceSelect.value === ""
    ? null
    : Number(elements.deviceSelect.value);
}

function selectedActivationMode() {
  return elements.activationMode.value || state.config?.activation?.mode || "kws";
}

function normalizedLevelThreshold(value) {
  return Math.max(-60, Math.min(-15, Math.round(Number(value) || -35)));
}

function renderLevelComparison() {
  const threshold = normalizedLevelThreshold(elements.levelThreshold.value);
  const input = state.meterDbfs <= -119
    ? "静音"
    : `${state.meterDbfs.toFixed(1)} dBFS`;
  const relation = state.meterDbfs >= threshold ? "已达到触发线" : "低于触发线";
  elements.levelTriggerComparison.textContent =
    `当前输入：${input} / 触发阈值：${threshold} dBFS（${relation}）`;
}

function renderActivationControls() {
  const mode = selectedActivationMode();
  const levelMode = mode === "level";
  elements.levelTriggerControls.hidden = !levelMode;
  elements.kwsSettings.classList.toggle("is-disabled", levelMode);
  [elements.keywordInput, elements.kwsScore, elements.kwsThreshold].forEach(
    (control) => { control.disabled = levelMode; }
  );
  document.querySelectorAll("[data-kws-preset]").forEach((button) => {
    button.disabled = levelMode;
  });
  const modeAlreadySaved = state.config?.activation?.mode === mode;
  elements.applyLevelThreshold.disabled =
    state.busy || !levelMode || !modeAlreadySaved;
  elements.activationModeHint.textContent = !modeAlreadySaved
    ? "触发模式已修改，请点击“保存并应用”后生效。"
    : levelMode
      ? "输入电平连续超过阈值后开始听取问题；无需说唤醒词，请根据现场噪声调试。"
      : "识别到唤醒词后才开始听取问题，误触发风险最低。";
  renderLevelComparison();
}

function renderStatus(status) {
  state.status = status;
  state.statusSignature = JSON.stringify(status);
  const running = Boolean(status.running);
  const desiredRunning =
    status.desired_running == null
      ? running
      : Boolean(status.desired_running);
  const serviceState = status.service_state || "STOPPED";
  const activationMode =
    status.activation_mode || state.config?.activation?.mode || "kws";
  elements.headerStatusDot.className = "status-dot";

  if (serviceState === "ERROR") {
    elements.headerStatusDot.classList.add("error");
    elements.headerStatusText.textContent = "运行异常";
  } else if (
    serviceState === "STARTING" ||
    serviceState === "STOPPING" ||
    serviceState === "RECOVERING"
  ) {
    elements.headerStatusDot.classList.add("starting");
    elements.headerStatusText.textContent = {
      STARTING: "正在启动",
      STOPPING: "正在停止",
      RECOVERING: "自动恢复中",
    }[serviceState];
  } else if (running) {
    elements.headerStatusDot.classList.add("online");
    elements.headerStatusText.textContent = "运行中";
  } else {
    elements.headerStatusDot.classList.add("offline");
    elements.headerStatusText.textContent = "已停止";
  }

  elements.pipelineState.textContent = status.pipeline_state || "IDLE";
  elements.uptime.textContent = formatDuration(status.uptime_seconds);
  elements.processId.textContent = status.pid || "—";
  elements.primaryAction.classList.toggle("stop", desiredRunning);
  elements.primaryAction.querySelector(".button-icon").textContent =
    desiredRunning ? "■" : "▶";
  elements.primaryAction.querySelector("span:last-child").textContent =
    desiredRunning ? "停止语音" : "一键启动";

  if (serviceState === "RECOVERING") {
    const retry = Number(status.restart_in_seconds) || 0;
    elements.heroTitle.textContent = "语音链路正在自动恢复";
    elements.heroDescription.textContent =
      retry > 0
        ? `${status.last_error || "语音进程暂不可用"}，约 ${retry.toFixed(0)} 秒后重试。`
        : status.last_error || "正在重新连接麦克风或恢复语音进程。";
  } else if (running) {
    elements.heroTitle.textContent = "本地语音链路正在工作";
    elements.heroDescription.textContent = activationMode === "level"
      ? `当前为分贝触发模式，输入达到 ${Number(status.level_threshold_dbfs ?? -35).toFixed(0)} dBFS 后自动听取。`
      : `当前状态 ${status.pipeline_state || "IDLE"}，等待“${state.config?.kws?.keyword || "唤醒词"}”。`;
  } else if (serviceState === "ERROR") {
    elements.heroTitle.textContent = "语音链路需要处理";
    elements.heroDescription.textContent =
      status.last_error || "进程发生异常，请查看运行日志。";
  } else {
    elements.heroTitle.textContent = "让语音网关准备就绪";
    elements.heroDescription.textContent =
      "选择录音设备并确认参数，然后一键启动唤醒、断句和本地识别链路。";
  }

  renderListeningState(status);
  renderLastResult(status.last_asr, status.last_wake, status);
  renderModelResult(status.last_model);
  renderLeadershipLog(status);
}

function renderListeningState(status) {
  const running = Boolean(status?.running);
  const serviceState = status?.service_state || "STOPPED";
  const pipelineState = status?.pipeline_state || "IDLE";
  const activationMode =
    status?.activation_mode || state.config?.activation?.mode || "kws";
  const levelMode = activationMode === "level";
  const keyword =
    state.config?.kws?.keyword || status?.last_wake || "唤醒词";
  const modelPending = status?.last_model?.status === "pending";
  let view;

  if (serviceState === "ERROR") {
    view = {
      className: "is-error",
      title: "语音服务异常",
      hint: status?.last_error || "请查看运行状态后重新启动",
      badge: "不可用",
    };
  } else if (
    serviceState === "STARTING" ||
    serviceState === "RECOVERING" ||
    serviceState === "STOPPING"
  ) {
    view = {
      className: "is-received",
      title: {
        STARTING: "正在准备麦克风",
        RECOVERING: "语音服务正在恢复",
        STOPPING: "语音服务正在停止",
      }[serviceState],
      hint: levelMode ? "准备完成后将自动监测输入音量" : "准备完成后即可使用唤醒词",
      badge: "准备中",
    };
  } else if (!running) {
    view = {
      className: "is-idle",
      title: "语音服务未启动",
      hint: "请先点击上方“一键启动”",
      badge: "待启动",
    };
  } else if (pipelineState === "LISTENING") {
    view = {
      className: "is-listening",
      title: levelMode ? "声音已触发，可以讲话" : "唤醒成功，可以讲话",
      hint: "数字人正在听，请说出您的问题",
      badge: "正在聆听",
    };
  } else if (pipelineState === "RECOGNIZING") {
    view = {
      className: "is-recognizing",
      title: "已收到语音，正在识别",
      hint: "讲话已经结束，请稍候",
      badge: "识别中",
    };
  } else if (pipelineState === "COOLDOWN" || modelPending) {
    view = {
      className: "is-received",
      title: "问题已收到",
      hint: modelPending
        ? "局域网模型正在生成回答"
        : "正在准备下一步处理",
      badge: "已接收",
    };
  } else {
    view = {
      className: "is-idle",
      title: levelMode ? "等待声音触发" : "等待唤醒",
      hint: levelMode
        ? `输入连续达到 ${Number(status?.level_threshold_dbfs ?? -35).toFixed(0)} dBFS 后开始听取`
        : `请说“${keyword}”开始对话`,
      badge: "待机",
    };
  }

  elements.listeningKicker.textContent = levelMode
    ? "分贝触发 / VAD 状态"
    : "KWS / VAD 状态";
  elements.kwsListeningIndicator.className =
    `listening-indicator ${view.className}`;
  elements.kwsListeningTitle.textContent = view.title;
  elements.kwsListeningHint.textContent = view.hint;
  elements.kwsListeningBadge.textContent = view.badge;
}

function renderLastResult(result, lastWake, status) {
  const levelMode =
    (status?.activation_mode || state.config?.activation?.mode) === "level";
  elements.lastActivationLabel.textContent = levelMode ? "触发方式" : "唤醒词";
  if (!result) {
    elements.lastAsrText.textContent = "尚无识别结果";
    elements.lastWake.textContent = levelMode ? "分贝触发" : lastWake || "—";
    elements.lastLanguage.textContent = "—";
    elements.lastAsrDuration.textContent = "—";
    elements.lastSpeechDuration.textContent = "—";
    elements.asrStatusBadge.textContent = "等待语音";
    return;
  }
  elements.lastAsrText.textContent = result.text || "识别结果为空";
  elements.lastWake.textContent = levelMode
    ? "分贝触发"
    : result.wake_word || lastWake || "—";
  elements.lastLanguage.textContent = result.language || "—";
  elements.lastAsrDuration.textContent =
    result.asr_duration_ms != null ? `${result.asr_duration_ms} ms` : "—";
  elements.lastSpeechDuration.textContent =
    result.speech_duration_ms != null ? `${result.speech_duration_ms} ms` : "—";
  elements.asrStatusBadge.textContent =
    result.status === "success" || result.text ? "识别成功" : "结果为空";
}

function renderModelResult(result) {
  const status = result?.status || "idle";
  elements.modelTarget.textContent = result?.target || "2";
  elements.modelSeq.textContent =
    result?.seq != null ? String(result.seq) : "—";
  elements.modelDuration.textContent =
    result?.duration_ms != null ? `${result.duration_ms} ms` : "—";

  if (status === "pending") {
    elements.modelStatusBadge.textContent = "正在回答";
    elements.modelAnswer.textContent = "局域网模型正在生成回答…";
    return;
  }
  if (status === "success") {
    elements.modelStatusBadge.textContent = "回答成功";
    elements.modelAnswer.textContent = result.answer || "回答内容为空";
    return;
  }
  if (status === "error") {
    elements.modelStatusBadge.textContent = "回答失败";
    elements.modelAnswer.textContent =
      result.error || "局域网模型暂时不可用";
    return;
  }
  elements.modelStatusBadge.textContent = "等待提问";
  elements.modelAnswer.textContent = "尚无回答";
}

function renderLeadershipLog(status) {
  const levelMode =
    (status?.activation_mode || state.config?.activation?.mode) === "level";
  const wakeWord =
    state.config?.kws?.keyword || status?.last_wake || "—";
  const asrText = status?.last_asr?.text || "等待识别";
  const model = status?.last_model;
  let modelText = "等待回答";
  if (model?.status === "pending") {
    modelText = "正在生成回答…";
  } else if (model?.status === "success") {
    modelText = model.answer || "回答内容为空";
  } else if (model?.status === "error") {
    modelText = model.error || "局域网模型暂时不可用";
  }
  renderLogs([
    levelMode
      ? `当前触发方式：分贝触发（${Number(status?.level_threshold_dbfs ?? -35).toFixed(0)} dBFS）`
      : `当前唤醒词：${wakeWord}`,
    `ASR识别结果：${asrText}`,
    `模型回答：${modelText}`,
  ]);
}

function renderConfig(config) {
  state.config = config;
  elements.deviceSelect.value =
    config.audio?.input_device == null ? "" : String(config.audio.input_device);
  elements.activationMode.value = config.activation?.mode || "kws";
  const levelThreshold = normalizedLevelThreshold(
    config.activation?.level_threshold_dbfs ?? -35
  );
  elements.levelThreshold.value = levelThreshold;
  elements.levelThresholdNumber.value = levelThreshold;
  elements.keywordInput.value = config.kws?.keyword || "";
  elements.kwsScore.value = config.kws?.score ?? 1.5;
  elements.kwsThreshold.value = config.kws?.threshold ?? 0.25;
  elements.vadSilence.value = config.vad?.min_silence_seconds ?? 0.6;
  renderSliderValues();
  renderActivationControls();
  renderDeviceMeta();
}

function renderDevices(devices, selectedValue = null) {
  state.devices = devices || [];
  const selected =
    selectedValue !== null
      ? selectedValue
      : state.config?.audio?.input_device ?? null;
  elements.deviceSelect.innerHTML = "";

  const defaultOption = document.createElement("option");
  defaultOption.value = "";
  defaultOption.textContent = "Windows 默认录音设备";
  elements.deviceSelect.appendChild(defaultOption);

  const recommended = state.devices.filter((item) => item.recommended);
  const compatible = state.devices.filter((item) => !item.recommended);
  [
    ["WASAPI（推荐）", recommended],
    ["兼容设备", compatible],
  ].forEach(([label, items]) => {
    if (!items.length) return;
    const group = document.createElement("optgroup");
    group.label = label;
    items.forEach((device) => {
      const option = document.createElement("option");
      option.value = String(device.index);
      option.textContent = `${device.name} · ${device.host_api}`;
      group.appendChild(option);
    });
    elements.deviceSelect.appendChild(group);
  });
  elements.deviceSelect.value = selected == null ? "" : String(selected);
  renderDeviceMeta();
}

function renderDeviceMeta() {
  const selected = selectedDeviceValue();
  const device =
    selected == null
      ? state.devices.find((item) => item.is_default)
      : state.devices.find((item) => item.index === selected);
  elements.deviceMeta.innerHTML = "";
  if (!device) {
    elements.deviceMeta.innerHTML =
      "<span>使用 Windows 当前默认录音设备</span>";
    return;
  }
  [
    `设备 #${device.index}`,
    device.host_api,
    `${device.inputs} 声道`,
    `${device.sample_rate} Hz`,
  ].forEach((text) => {
    const span = document.createElement("span");
    span.textContent = text;
    elements.deviceMeta.appendChild(span);
  });
}

function renderVolume(volume) {
  if (!volume?.available) {
    elements.volumeSlider.disabled = true;
    elements.volumeValue.textContent = "不可用";
    elements.volumeMessage.textContent =
      volume?.message || "系统输入音量暂不可用";
    elements.volumeMessage.classList.add("warning");
    return;
  }
  const value = Math.round(Number(volume.volume) || 0);
  elements.volumeSlider.disabled = volume.writable === false;
  elements.volumeSlider.value = value;
  elements.volumeValue.textContent = `${value}%`;
  elements.volumeMessage.textContent =
    volume.message ||
    `${volume.name}${volume.is_default ? " · Windows默认端点" : ""}`;
  elements.volumeMessage.classList.toggle(
    "warning",
    volume.writable === false
  );
}

function renderSliderValues() {
  const levelThreshold = normalizedLevelThreshold(elements.levelThreshold.value);
  elements.levelThresholdValue.textContent = `${levelThreshold} dBFS`;
  elements.levelThresholdNumber.value = levelThreshold;
  elements.kwsScoreValue.textContent = Number(elements.kwsScore.value).toFixed(2);
  elements.kwsThresholdValue.textContent =
    Number(elements.kwsThreshold.value).toFixed(2);
  elements.vadSilenceValue.textContent =
    `${Number(elements.vadSilence.value).toFixed(2)} s`;
  renderLevelComparison();
}

function collectConfig() {
  return {
    audio: { input_device: selectedDeviceValue() },
    activation: {
      mode: selectedActivationMode(),
      level_threshold_dbfs: normalizedLevelThreshold(
        elements.levelThreshold.value
      ),
    },
    kws: {
      keyword: elements.keywordInput.value.trim(),
      score: Number(elements.kwsScore.value),
      threshold: Number(elements.kwsThreshold.value),
    },
    vad: {
      min_silence_seconds: Number(elements.vadSilence.value),
    },
    session: {
      cooldown_ms: state.config?.session?.cooldown_ms ?? 150,
    },
  };
}

async function bootstrap() {
  setBusy(true);
  try {
    const payload = await api("/api/bootstrap");
    elements.appVersion.textContent = `v${payload.version}`;
    renderDevices(payload.devices);
    renderConfig(payload.config);
    renderStatus(payload.status);
    renderVolume(payload.volume);
    renderInputMeter(payload.meter);
    state.logCursor = Number(payload.log_cursor) || 0;
    state.logStreamId = payload.log_stream_id || "";
    await startInputMeter();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

function scheduleRealtime(delay = REALTIME_POLL_MS) {
  clearTimeout(state.realtimeTimer);
  state.realtimeTimer = setTimeout(refreshRealtime, delay);
}

async function refreshRealtime() {
  if (state.realtimeBusy) return;
  if (document.hidden) {
    scheduleRealtime(500);
    return;
  }
  state.realtimeBusy = true;
  let nextDelay = REALTIME_POLL_MS;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1000);
  try {
    const payload = await api(
      `/api/realtime?after=${encodeURIComponent(state.logCursor)}` +
        `&stream_id=${encodeURIComponent(state.logStreamId)}`,
      { signal: controller.signal }
    );
    state.realtimeFailures = 0;
    const signature = JSON.stringify(payload.status);
    if (signature !== state.statusSignature) {
      renderStatus(payload.status);
    }

    state.logCursor = Number(payload.log_cursor) || 0;
    state.logStreamId = payload.log_stream_id || state.logStreamId;
    if (payload.has_more) {
      nextDelay = 0;
    }
  } catch (error) {
    state.realtimeFailures += 1;
    nextDelay = Math.min(
      5000,
      250 * 2 ** Math.min(state.realtimeFailures - 1, 5)
    );
    elements.headerStatusDot.className = "status-dot error";
    elements.headerStatusText.textContent = "管理端断开";
  } finally {
    clearTimeout(timeout);
    state.realtimeBusy = false;
    scheduleRealtime(nextDelay);
  }
}

async function refreshDevices() {
  try {
    const current = selectedDeviceValue();
    const payload = await api("/api/devices");
    renderDevices(payload.devices, current);
    await refreshVolume();
    await startInputMeter();
    showToast(`已发现 ${payload.devices.length} 个录音入口`);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function refreshVolume() {
  const value = elements.deviceSelect.value;
  const query = value === "" ? "" : `?device_index=${encodeURIComponent(value)}`;
  try {
    renderVolume(await api(`/api/audio/volume${query}`));
  } catch (error) {
    renderVolume({ available: false, message: error.message });
  }
}

function renderInputMeter(meter) {
  const active = Boolean(meter?.active);
  const reportedDbfs = Number(meter?.dbfs);
  state.meterDbfs = active && Number.isFinite(reportedDbfs)
    ? reportedDbfs
    : -120;
  renderLevelComparison();
  const peak = active
    ? Math.max(0, Math.min(100, Number(meter.peak) || 0))
    : 0;
  state.meterPeak =
    peak >= state.meterPeak
      ? peak
      : Math.max(peak, state.meterPeak - 2.2);

  elements.inputMeter.style.setProperty("--meter-level", `${peak}%`);
  elements.inputMeter.style.setProperty(
    "--meter-peak",
    `${state.meterPeak}%`
  );
  elements.inputMeter.classList.toggle("hot", peak >= 75 && peak < 97);
  elements.inputMeter.classList.toggle("clipping", peak >= 97);
  elements.inputMeter.setAttribute("aria-valuenow", peak.toFixed(0));

  if (!meter?.available) {
    elements.inputMeterValue.textContent = "不可用";
    elements.inputMeterStatus.textContent =
      meter?.error || "输入电平检测组件不可用";
    elements.inputMeterStatus.classList.add("warning");
    return;
  }
  if (!active) {
    elements.inputMeterValue.textContent = "−∞ dBFS";
    elements.inputMeterStatus.textContent =
      meter?.error || "等待输入电平检测启动";
    elements.inputMeterStatus.classList.toggle("warning", Boolean(meter?.error));
    return;
  }

  const dbfs = state.meterDbfs;
  elements.inputMeterValue.textContent =
    dbfs <= -119 ? "−∞ dBFS" : `${dbfs.toFixed(1)} dBFS`;
  elements.inputMeterStatus.textContent =
    meter.endpoint_name || "正在监测当前录音端点";
  elements.inputMeterStatus.classList.remove("warning");
}

async function startInputMeter() {
  try {
    const payload = await api("/api/audio/meter/start", {
      method: "POST",
      body: JSON.stringify({ device_index: selectedDeviceValue() }),
    });
    state.meterPeak = 0;
    renderInputMeter(payload);
  } catch (error) {
    renderInputMeter({
      available: false,
      active: false,
      error: error.message,
    });
  }
}

async function refreshInputMeter() {
  if (state.meterBusy || document.hidden) return;
  state.meterBusy = true;
  try {
    renderInputMeter(await api("/api/audio/meter"));
  } catch (error) {
    renderInputMeter({
      available: false,
      active: false,
      error: error.message,
    });
  } finally {
    state.meterBusy = false;
  }
}

async function saveConfig(showMessage = true) {
  const payload = await api("/api/config", {
    method: "POST",
    body: JSON.stringify(collectConfig()),
  });
  renderConfig(payload.config);
  renderStatus(payload.status);
  if (showMessage) showToast(payload.message);
  return payload;
}

async function applyLevelThreshold() {
  const threshold = normalizedLevelThreshold(elements.levelThreshold.value);
  elements.applyLevelThreshold.disabled = true;
  try {
    const payload = await api("/api/activation/level-threshold", {
      method: "POST",
      body: JSON.stringify({ level_threshold_dbfs: threshold }),
    });
    renderConfig(payload.config);
    renderStatus(payload.status);
    showToast(
      payload.runtime?.applied_to_running_gateway
        ? "分贝触发阈值已即时生效，无需重启语音服务"
        : "分贝触发阈值已保存，将在下次启动时生效"
    );
  } catch (error) {
    showToast(error.message, true);
  } finally {
    renderActivationControls();
  }
}

async function toggleGateway() {
  setBusy(true);
  try {
    if (state.status?.desired_running ?? state.status?.running) {
      const payload = await api("/api/gateway/stop", {
        method: "POST",
        body: "{}",
      });
      renderStatus(payload.status);
      showToast(payload.message);
    } else {
      const saved = await saveConfig(false);
      if (saved.status?.running) {
        renderStatus(saved.status);
        showToast(saved.message);
        return;
      }
      const payload = await api("/api/gateway/start", {
        method: "POST",
        body: "{}",
      });
      renderStatus(payload.status);
      showToast(payload.message);
    }
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function updateSystemVolume() {
  if (elements.volumeSlider.disabled) return;
  elements.volumeSlider.disabled = true;
  try {
    const payload = await api("/api/audio/volume", {
      method: "POST",
      body: JSON.stringify({
        device_index: selectedDeviceValue(),
        volume: Number(elements.volumeSlider.value),
      }),
    });
    renderVolume(payload);
    showToast(payload.message);
  } catch (error) {
    showToast(error.message, true);
    await refreshVolume();
  }
}

function renderLogs(lines) {
  const value = lines?.length ? lines.join("\n") : "暂无运行日志";
  const changed = elements.logOutput.textContent !== value;
  elements.logOutput.textContent = value;
  if (changed && elements.autoScroll.checked) {
    elements.logOutput.scrollTop = elements.logOutput.scrollHeight;
  }
}

async function refreshLogs() {
  try {
    const status = await api("/api/status");
    renderStatus(status);
  } catch (error) {
    // 状态轮询会显示连接问题，日志轮询保持安静。
  }
}

function bindEvents() {
  elements.primaryAction.addEventListener("click", toggleGateway);
  elements.refreshAll.addEventListener("click", bootstrap);
  elements.refreshDevices.addEventListener("click", refreshDevices);
  elements.deviceSelect.addEventListener("change", async () => {
    renderDeviceMeta();
    await refreshVolume();
    await startInputMeter();
  });
  elements.volumeSlider.addEventListener("input", () => {
    elements.volumeValue.textContent = `${elements.volumeSlider.value}%`;
  });
  elements.volumeSlider.addEventListener("change", updateSystemVolume);
  elements.activationMode.addEventListener("change", () => {
    renderActivationControls();
    if (state.status) renderStatus(state.status);
  });
  elements.levelThreshold.addEventListener("input", () => {
    renderSliderValues();
  });
  elements.levelThresholdNumber.addEventListener("change", () => {
    const value = normalizedLevelThreshold(elements.levelThresholdNumber.value);
    elements.levelThreshold.value = value;
    renderSliderValues();
  });
  elements.applyLevelThreshold.addEventListener("click", applyLevelThreshold);

  [
    elements.kwsScore,
    elements.kwsThreshold,
    elements.vadSilence,
  ].forEach((control) => control.addEventListener("input", renderSliderValues));

  document.querySelectorAll("[data-kws-preset]").forEach((button) => {
    button.addEventListener("click", () => {
      const preset = kwsPresets[button.dataset.kwsPreset];
      elements.kwsScore.value = preset.score;
      elements.kwsThreshold.value = preset.threshold;
      document.querySelectorAll("[data-kws-preset]").forEach((item) =>
        item.classList.toggle("active", item === button)
      );
      renderSliderValues();
    });
  });

  document.querySelectorAll("[data-vad-preset]").forEach((button) => {
    button.addEventListener("click", () => {
      elements.vadSilence.value = vadPresets[button.dataset.vadPreset].silence;
      document.querySelectorAll("[data-vad-preset]").forEach((item) =>
        item.classList.toggle("active", item === button)
      );
      renderSliderValues();
    });
  });

  elements.saveConfig.addEventListener("click", async () => {
    const originalLabel = elements.saveConfig.textContent;
    setBusy(true);
    elements.saveConfig.textContent = "保存并应用中…";
    try {
      await saveConfig(true);
    } catch (error) {
      showToast(error.message, true);
    } finally {
      elements.saveConfig.textContent = originalLabel;
      setBusy(false);
    }
  });

  elements.restoreDefaults.addEventListener("click", async () => {
    const confirmed = window.confirm(
      "确定恢复录音设备、唤醒词、KWS和VAD默认参数吗？当前配置会先备份；语音运行中会自动重启。"
    );
    if (!confirmed) return;
    setBusy(true);
    try {
      const payload = await api("/api/config/defaults", {
        method: "POST",
        body: "{}",
      });
      renderConfig(payload.config);
      renderStatus(payload.status);
      await refreshVolume();
      await startInputMeter();
      showToast(payload.message);
    } catch (error) {
      showToast(error.message, true);
    } finally {
      setBusy(false);
    }
  });

  elements.refreshLogs.addEventListener("click", refreshLogs);
}

async function startApp() {
  bindEvents();
  await bootstrap();
  scheduleRealtime(0);
  setInterval(refreshInputMeter, 160);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) scheduleRealtime(0);
  });
}

startApp();
