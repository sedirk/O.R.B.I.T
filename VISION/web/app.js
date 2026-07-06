const $ = (id) => document.getElementById(id);

const state = {
  lastImage: null,
  lastAuxImage: null,
  lastResult: null,
  lastLogs: "",
  eventReady: false,
  pendingId: null,
  hasPending: false,
  hasCommitted: false,
  autoSelection: null,
  autoSelectionKey: null,
  userSelectionDirty: false,
  selection: null,
  pointer: null,
  modelEndpoint: null,
  selectedOllama: null,
  activeModule: "overview",
  controlsHydrated: false,
  homeboxOptionsKey: "",
  homeboxOptionsLoading: false,
  homeboxTags: [],
  homeboxLocations: [],
  homeboxConfigDirty: false,
  rfidInventory: null,
  labelPreview: null,
  labelOptionDirtyUntil: 0,
  appMode: "intake",
  findDbRows: [],
  findRfidRows: [],
  findDbLoaded: false,
  findActiveTab: "db",
  findSelected: null,
  findSelectedUrl: "",
};

let configSaveTimer = null;

function fmtTime(ms) {
  if (!ms) return "--";
  return new Date(ms).toLocaleString("zh-CN", { hour12: false });
}

function fmtDuration(ms) {
  if (!Number.isFinite(ms) || ms < 0) return "0s";
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}m ${rest}s`;
}

function fmtWeight(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--.- g";
  return `${Number(value).toFixed(1)} g`;
}

function setChip(id, ok, warn = false, text = null) {
  const el = $(id);
  el.classList.remove("ok", "warn", "bad");
  el.classList.add(ok ? "ok" : warn ? "warn" : "bad");
  if (text) el.textContent = text;
}

function setDot(id, ok, warn = false) {
  const el = $(id);
  if (!el) return;
  el.classList.remove("ok", "warn", "bad");
  el.classList.add(ok ? "ok" : warn ? "warn" : "bad");
}

function setText(id, text) {
  const el = $(id);
  if (el) el.textContent = text || "--";
}

function fieldValue(id, fallback = "") {
  const el = $(id);
  return el ? el.value : fallback;
}

function fieldNumber(id, fallback = 0) {
  const value = fieldValue(id, String(fallback));
  if (value === "") return fallback;
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function fieldChecked(id, fallback = false) {
  const el = $(id);
  return el ? !!el.checked : fallback;
}

function setChecked(id, value) {
  const el = $(id);
  if (el) el.checked = value === true || value === "1" || value === 1 || value === "true";
}

function petLabelEnabled() {
  return fieldChecked("labelPrintPet", fieldChecked("printPetLabels", true));
}

function rfidLabelEnabled() {
  return fieldChecked("labelWriteRfid", fieldChecked("writeRfidTags", true));
}

function syncLabelOption(sourceId, targetId) {
  const source = $(sourceId);
  const target = $(targetId);
  if (source && target) target.checked = source.checked;
  state.labelOptionDirtyUntil = Date.now() + 1500;
  scheduleSaveRuntimeConfig();
}

function applyLabelOptionState(config, force = false) {
  const ids = ["printPetLabels", "labelPrintPet", "writeRfidTags", "labelWriteRfid"];
  const optionsModal = $("labelOptionsModal");
  if (!force && optionsModal && !optionsModal.hidden) return;
  if (!force && Date.now() < state.labelOptionDirtyUntil) return;
  if (!force && ids.some((id) => $(id) === document.activeElement)) return;
  setChecked("printPetLabels", config.print_pet_labels ?? "1");
  setChecked("labelPrintPet", config.print_pet_labels ?? "1");
  setChecked("writeRfidTags", config.write_rfid_tags ?? "1");
  setChecked("labelWriteRfid", config.write_rfid_tags ?? "1");
}

function setBusy(running) {
  ["capture", "identifySelection", "diagnose", "restartOllama"].forEach((id) => {
    $(id).disabled = running;
  });
  $("identifySelection").disabled = running || !state.lastImage;
  $("scanWrite").disabled = running || !state.hasPending || state.hasCommitted;
  $("labelPreviewButton").disabled = running || !state.hasCommitted;
  $("labelWrite").disabled = running || !state.hasCommitted;
  $("cancel").disabled = !running;
}

function setAppMode(mode) {
  state.appMode = mode === "find" ? "find" : "intake";
  $("intakePanel").hidden = state.appMode !== "intake";
  $("finderPanel").hidden = state.appMode !== "find";
  document.querySelectorAll("[data-app-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.appMode === state.appMode);
  });
  if (state.appMode === "find" && !state.findDbLoaded) {
    window.setTimeout(runFindSearch, 0);
  }
}

function render(payload) {
  if (!payload) return;

  const config = payload.config || {};
  applyConfigToControls(config);
  $("clock").textContent = fmtTime(payload.time);
  setText("modelName", `${selectedModelName() || config.ollama_model || "--"} @ ${aiEndpointLabel()}`);
  setText("ollamaEndpoint", aiEndpointLabel());
  setText("homeboxUrl", config.homebox_url || "--");
  setText("findProfile", `${config.homebox_url || "Homebox"} · ${systemLabel(payload.system?.rfid?.available ? "RFID 可用" : "RFID 未就绪")}`);

  const scale = payload.scale || {};
  $("weight").textContent = fmtWeight(scale.weight_g);
  const stable = $("stable");
  stable.classList.remove("ok", "warn", "bad");
  if (!scale.connected) {
    stable.textContent = "未连接";
    stable.classList.add("bad");
  } else if (scale.stable) {
    stable.textContent = "稳定";
    stable.classList.add("ok");
  } else {
    stable.textContent = "变化中";
    stable.classList.add("warn");
  }

  const system = payload.system || {};
  setChip("chip-scale", !!scale.connected, false, scale.connected ? "Scale OK" : "Scale");
  setChip("chip-realsense", !!system.realsense?.connected, false, "主相机");
  const selectedOllama = state.selectedOllama || system.ollama || {};
  setChip("chip-ollama", !!selectedOllama.connected, false, "AI");
  setChip("chip-homebox", !!system.homebox?.connected, false, "Homebox");
  setChip("chip-rfid", !!system.rfid?.available, system.rfid?.state === "driver_error", "RFID");

  const serialPorts = system.serial_ports || [];
  setText("ports", serialPorts.map((p) => p.device).join(", ") || "--");
  $("ollamaPs").textContent = selectedOllama.ps || system.ollama?.ps || "AI 未返回运行模型";
  renderModuleStatus(system, scale, config, payload.task || {});
  renderDevices(system, scale);
  const task = payload.task || {};
  const pending = task.pending_item || null;
  renderTask(task, pending);
  renderPending(pending, task);
}

function systemLabel(text) {
  return text || "--";
}

function modeLabel(value) {
  return {
    auto: "自动",
    scale: "称重台",
    macro: "微距",
    full: "全图",
    furniture: "大件",
  }[value] || value || "--";
}

function renderDevices(system, scale) {
  const rows = [];
  const selectedOllama = state.selectedOllama || system.ollama || {};
  rows.push(deviceRow("主相机", system.realsense?.connected, deviceText(system.realsense)));
  const cameras = system.cameras || {};
  rows.push(deviceRow("辅助相机", !!cameras.aux_detected, cameras.message || "未检测"));
  rows.push(deviceRow("电子秤", !!scale.connected, scale.connected ? `${scale.port} · ${fmtWeight(scale.weight_g)} · ${scale.raw || ""}` : scale.error || "未连接"));
  rows.push(deviceRow("AI", !!selectedOllama.connected, selectedOllama.connected ? `${aiEndpointLabel()} · ${selectedModelName() || selectedOllama.model || ""}` : selectedOllama.error || "未连接"));
  rows.push(deviceRow("Homebox", system.homebox?.connected, system.homebox?.connected ? `${system.homebox.title || "Homebox"} ${system.homebox.version || ""}` : system.homebox?.error || "未连接"));
  const rfid = system.rfid || {};
  rows.push(deviceRow("RFID", !!rfid.available, rfid.message || "未检测", rfid.state === "driver_error"));
  $("deviceList").innerHTML = rows.join("");
}

function renderModuleStatus(system, scale, config, task) {
  const selectedOllama = state.selectedOllama || system.ollama || {};
  const rfid = system.rfid || {};
  const homebox = system.homebox || {};
  const cameras = system.cameras || {};
  const serialPorts = system.serial_ports || [];
  const realsenseOk = !!system.realsense?.connected;
  const auxOk = !!cameras.aux_detected || config.aux_camera_enabled === "0";
  const scaleOk = !!scale.connected;
  const aiOk = !!selectedOllama.connected;
  const homeboxOk = !!homebox.connected;
  const rfidWarn = rfid.state === "driver_error";
  const rfidOk = !!rfid.available;
  const chainOk = realsenseOk && scaleOk && aiOk && homeboxOk;

  setDot("tab-overview-dot", chainOk, !chainOk);
  setDot("tab-camera-dot", realsenseOk && auxOk, !(realsenseOk && auxOk));
  setDot("tab-ai-dot", aiOk);
  setDot("tab-scale-dot", scaleOk);
  setDot("tab-rfid-dot", rfidOk, rfidWarn);
  setDot("tab-homebox-dot", homeboxOk);

  setText("tab-overview-state", chainOk ? "就绪" : "检查中");
  setText("tab-camera-state", cameras.aux_enabled === false ? "辅助关" : cameras.aux_detected ? "在线" : "检查");
  setText("tab-ai-state", aiOk ? "在线" : "离线");
  setText("tab-scale-state", scaleOk ? fmtWeight(scale.weight_g) : "离线");
  setText("tab-rfid-state", rfidOk ? "可用" : rfidWarn ? "驱动" : "未检出");
  setText("tab-homebox-state", homeboxOk ? (homebox.version || "在线") : "离线");
  setText("moduleChainState", chainOk ? "主相机 · 电子秤 · AI · Homebox" : "部分模块未就绪");
  setText("moduleTaskState", task.running ? `${task.label || task.action || "任务"} · 运行中` : "空闲");
  setText("cameraD435State", realsenseOk ? deviceText(system.realsense) : system.realsense?.error || "未连接");
  setText("cameraAuxState", `${cameras.message || "未检测"} · index ${config.aux_camera_index || "0"} · ${config.aux_camera_backend || "dshow"}`);

  setText("scaleConfigState", scaleOk ? `${fmtWeight(scale.weight_g)} · ${scale.stable ? "稳定" : "变化中"}` : scale.error || "未连接");
  setText("rfidConfigState", rfid.message || "未检测");
  setText("ollamaEndpoint", aiEndpointLabel());
  setText("modelName", `${selectedModelName() || config.ollama_model || "--"} @ ${aiEndpointLabel()}`);
  const imageSize = Number(fieldValue("imageSize", config.image_max_size ?? 0));
  const imageText = imageSize > 0 ? `${imageSize}px` : "原图";
  setText("intakeProfile", `${modeLabel(fieldValue("mode", config.mode || "auto"))} · ${selectedModelName() || config.ollama_model || "--"} · ${imageText}`);

  populatePortSelect("scalePort", serialPorts, scale.port || config.scale_port || "auto", true);
  const configuredRfid = config.rfid_port || (rfid.ports || [])[0]?.device || "";
  populatePortSelect("rfidPort", rfid.ports && rfid.ports.length ? rfid.ports : serialPorts, configuredRfid, false);
  if ($("scaleBaud") && config.scale_baud) $("scaleBaud").value = String(config.scale_baud);
  setCameraControls(config);
  applyLabelOptionState(config);
  const homeboxInput = $("homeboxConfigUrl");
  if (homeboxInput && document.activeElement !== homeboxInput) {
    homeboxInput.value = config.homebox_url || homebox.url || homeboxInput.value;
  }
  setText("homeboxAuthState", config.homebox_has_token || config.homebox_has_password ? "已配置" : "未配置");
}

function populatePortSelect(id, ports, preferred = "", includeAuto = false) {
  const select = $(id);
  if (!select) return;
  const items = [];
  if (includeAuto) items.push({ value: "auto", text: "auto" });
  (ports || []).forEach((port) => {
    if (!port?.device) return;
    items.push({ value: port.device, text: serialPortText(port) });
  });
  if (!items.length) items.push({ value: "", text: "--" });
  const signature = JSON.stringify(items);
  const current = select.value || preferred || "";
  if (select.dataset.signature !== signature) {
    select.innerHTML = "";
    items.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.value;
      option.textContent = item.text;
      select.appendChild(option);
    });
    select.dataset.signature = signature;
  }
  const values = items.map((item) => item.value);
  if (values.includes(current)) {
    select.value = current;
  } else if (values.includes(preferred)) {
    select.value = preferred;
  } else {
    select.value = values[0] || "";
  }
}

function serialPortText(port) {
  const desc = port.description && port.description !== port.device ? ` · ${port.description}` : "";
  return `${port.device}${desc}`;
}

function setValueIfIdle(id, value) {
  const el = $(id);
  if (!el || document.activeElement === el || value === undefined || value === null) return;
  el.value = String(value);
}

function cameraCropOptionValue(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "1";
  if (number >= 0.995) return "1";
  if (Math.abs(number - 0.75) < 0.01) return "0.75";
  if (Math.abs(number - 0.66) < 0.01) return "0.66";
  if (Math.abs(number - 0.5) < 0.01) return "0.5";
  return "1";
}

function setCameraControls(config) {
  setChecked("auxCameraEnabled", config.aux_camera_enabled ?? "1");
  setValueIfIdle("auxCameraIndex", config.aux_camera_index || "0");
  setValueIfIdle("auxCameraBackend", config.aux_camera_backend || "dshow");
  setValueIfIdle("auxCameraWidth", config.aux_camera_width || "1280");
  setValueIfIdle("auxCameraHeight", config.aux_camera_height || "720");
  setValueIfIdle("auxCameraCenterCrop", cameraCropOptionValue(config.aux_camera_center_crop || "1"));
  setValueIfIdle("auxCameraWarmup", config.aux_camera_warmup_seconds || "1.2");
  setChecked("auxCameraAutoExposure", config.aux_camera_auto_exposure ?? "1");
}

function deviceText(realsense) {
  if (!realsense?.connected) return realsense?.error || "未连接";
  return (realsense.devices || []).map((d) => `${d.name} ${d.serial}`).join(" · ");
}

function deviceRow(name, ok, text, warn = false) {
  const dot = ok ? "ok" : warn ? "warn" : "bad";
  return `
    <div class="device-row">
      <strong><i class="dot ${dot}"></i>${escapeHtml(name)}</strong>
      <span title="${escapeHtml(text || "")}">${escapeHtml(text || "--")}</span>
    </div>
  `;
}

function renderTask(task, pending = null) {
  const running = !!task.running;
  setBusy(running);
  const timeoutMs = Number(task.timeout_seconds || 0) * 1000;
  const elapsedMs = task.started_at ? Math.max(0, Date.now() - Number(task.started_at)) : 0;
  const limitText = timeoutMs ? ` / 上限 ${fmtDuration(timeoutMs)}` : "";

  $("taskTitle").textContent = running
    ? `${task.label || task.action || "任务"} · 运行中`
    : task.label
      ? `${task.label} · ${task.timed_out ? "超时终止" : task.returncode === 0 ? "完成" : "结束"}`
      : "空闲";

  $("taskState").textContent = running
    ? `已运行 ${fmtDuration(elapsedMs)}${limitText} · 开始于 ${fmtTime(task.started_at)}`
    : task.finished_at
      ? `结束于 ${fmtTime(task.finished_at)} · code ${task.returncode}${task.error ? ` · ${task.error}` : ""}`
      : "--";

  const logs = (task.logs || []).join("\n");
  if (logs && logs !== state.lastLogs) {
    state.lastLogs = logs;
    $("logBox").textContent = logs;
    $("logBox").scrollTop = $("logBox").scrollHeight;
  }

  const pendingPreviewName = pending ? pending.source_image_name || pending.image_name : null;
  const pendingMatchesTask = !!(
    pendingPreviewName &&
    pending?.result_name &&
    task.latest_result &&
    pending.result_name === task.latest_result
  );
  const previewImage = pendingMatchesTask ? pendingPreviewName : task.latest_image;

  if (previewImage && previewImage !== state.lastImage) {
    const requestedSelection = task.request_selection ? clampSelection(task.request_selection) : null;
    state.autoSelection = null;
    state.autoSelectionKey = null;
    showLogImage("preview", "emptyPreview", previewImage, "lastImage");
    if (requestedSelection) {
      state.selection = requestedSelection;
      state.userSelectionDirty = true;
      renderSelectionBox();
    } else {
      state.userSelectionDirty = false;
      resetSelection();
    }
  }

  if (task.latest_aux_image && task.latest_aux_image !== state.lastAuxImage) {
    showLogImage("auxPreview", "emptyAuxPreview", task.latest_aux_image, "lastAuxImage");
  }

  if (task.latest_result) {
    state.lastResult = task.latest_result;
  }
  if (task.latest_selection) {
    const selectionKey = JSON.stringify(task.latest_selection);
    state.autoSelection = clampSelection(task.latest_selection);
    if (state.userSelectionDirty) {
      renderSelectionBox();
      return;
    }
    if (!state.userSelectionDirty && selectionKey !== state.autoSelectionKey) {
      state.autoSelectionKey = selectionKey;
      state.selection = { ...state.autoSelection };
      renderSelectionBox();
    }
  }
}

function renderPending(pending, task = {}) {
  const wasCommitted = state.hasCommitted;
  state.hasPending = !!pending;
  state.hasCommitted = !!pending?.committed_item;
  const running = $("cancel").disabled === false;
  $("scanWrite").disabled = running || !pending || state.hasCommitted;
  $("labelPreviewButton").disabled = running || !state.hasCommitted;
  $("labelWrite").disabled = running || !state.hasCommitted;
  $("discardPending").disabled = running || !pending;

  if (!pending) {
    state.pendingId = null;
    state.hasCommitted = false;
    state.labelPreview = null;
    $("editorStatus").textContent = "识别后可编辑";
    setEditorEnabled(false, true);
    renderLabelPreview(null);
    return;
  }

  setEditorEnabled(!state.hasCommitted);
  $("editorStatus").textContent = state.hasCommitted
    ? `${pending.editable?.name || "未命名物品"} · 已入库`
    : `${pending.editable?.name || "未命名物品"} · 待确认`;
  const previewName = pending.source_image_name || pending.image_name;
  if (!task.latest_image) {
    showLogImage("preview", "emptyPreview", previewName, "lastImage");
  }
  showLogImage("auxPreview", "emptyAuxPreview", pending.aux_image_name, "lastAuxImage");
  if (pending.result_name) state.lastResult = pending.result_name;
  renderLabelPreview(pending.label_preview || pending.label_result || (state.pendingId === pending.id ? state.labelPreview : null));
  if (state.pendingId === pending.id) {
    if (!wasCommitted && state.hasCommitted && pending.editable?.asset_code) {
      $("fieldAssetCode").value = pending.editable.asset_code;
    }
    return;
  }
  state.pendingId = pending.id;

  const editable = pending.editable || {};
  $("fieldName").value = editable.name || "";
  $("fieldCategory").value = editable.category || "";
  $("fieldManufacturer").value = editable.manufacturer || "";
  $("fieldModel").value = editable.model || "";
  $("fieldQuantity").value = editable.quantity || 1;
  $("fieldWeight").value = editable.weight_g ?? "";
  $("fieldSize").value = editable.size || sizeFromMeasurement(editable);
  $("fieldAssetCode").value = editable.asset_code || editable.assetId || editable.asset_id || "";
  $("fieldTags").value = editable.tags || "";
  $("fieldLocation").value = editable.suggested_location || "";
  $("fieldDescription").value = editable.description || "";
  $("fieldReasoning").value = editable.reasoning || "";
}

function showLogImage(imageId, emptyId, imageName, stateKey) {
  if (!imageName || imageName === state[stateKey]) return;
  state[stateKey] = imageName;
  const img = $(imageId);
  img.onload = () => {
    if (imageId === "preview") {
      ensureDefaultSelection();
      renderSelectionBox();
    }
  };
  img.src = `/logs/${encodeURIComponent(imageName)}?t=${Date.now()}`;
  img.style.display = "block";
  $(emptyId).style.display = "none";
}

function imageRect() {
  const img = $("preview");
  const wrap = img.parentElement;
  if (!img.naturalWidth || !img.naturalHeight) return null;
  const box = wrap.getBoundingClientRect();
  const imgRatio = img.naturalWidth / img.naturalHeight;
  const boxRatio = box.width / box.height;
  let width;
  let height;
  let left;
  let top;
  if (boxRatio > imgRatio) {
    height = box.height;
    width = height * imgRatio;
    left = box.left + (box.width - width) / 2;
    top = box.top;
  } else {
    width = box.width;
    height = width / imgRatio;
    left = box.left;
    top = box.top + (box.height - height) / 2;
  }
  return { left, top, width, height };
}

function clampSelection(sel) {
  const x = Math.max(0, Math.min(0.99, sel.x));
  const y = Math.max(0, Math.min(0.99, sel.y));
  const w = Math.max(0.03, Math.min(1 - x, sel.w));
  const h = Math.max(0.03, Math.min(1 - y, sel.h));
  return { x, y, w, h };
}

function ensureDefaultSelection() {
  if (state.selection) return;
  state.selection = state.autoSelection ? { ...state.autoSelection } : { x: 0.34, y: 0.28, w: 0.32, h: 0.34 };
}

function resetSelection() {
  state.userSelectionDirty = false;
  state.selection = state.autoSelection ? { ...state.autoSelection } : null;
  ensureDefaultSelection();
  renderSelectionBox();
}

function renderSelectionBox() {
  const rect = imageRect();
  const box = $("selectionBox");
  if (!rect || !state.selection) {
    box.style.display = "none";
    return;
  }
  const wrapRect = $("preview").parentElement.getBoundingClientRect();
  const sel = clampSelection(state.selection);
  state.selection = sel;
  box.style.display = "block";
  box.style.left = `${rect.left - wrapRect.left + sel.x * rect.width}px`;
  box.style.top = `${rect.top - wrapRect.top + sel.y * rect.height}px`;
  box.style.width = `${sel.w * rect.width}px`;
  box.style.height = `${sel.h * rect.height}px`;
}

function pointToSelection(event) {
  const rect = imageRect();
  if (!rect) return null;
  return {
    x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
    y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
  };
}

function selectionContains(point, sel) {
  return point.x >= sel.x && point.x <= sel.x + sel.w && point.y >= sel.y && point.y <= sel.y + sel.h;
}

function selectionMode(point, sel) {
  const rect = imageRect();
  const px = rect ? Math.max(0.01, 14 / Math.max(rect.width, rect.height)) : 0.025;
  const nearRight = Math.abs(point.x - (sel.x + sel.w)) <= px;
  const nearBottom = Math.abs(point.y - (sel.y + sel.h)) <= px;
  if (selectionContains(point, sel) && nearRight && nearBottom) return "resize";
  if (selectionContains(point, sel)) return "move";
  return "draw";
}

function attachSelectionHandlers() {
  const wrap = $("preview").parentElement;
  wrap.addEventListener("pointerdown", (event) => {
    if (!state.lastImage) return;
    const point = pointToSelection(event);
    if (!point) return;
    event.preventDefault();
    wrap.setPointerCapture(event.pointerId);
    ensureDefaultSelection();
    const sel = state.selection;
    const mode = selectionMode(point, sel);
    state.pointer = {
      id: event.pointerId,
      mode,
      start: point,
      original: { ...sel },
    };
    if (mode === "draw") {
      state.userSelectionDirty = true;
      state.selection = { x: point.x, y: point.y, w: 0.03, h: 0.03 };
      renderSelectionBox();
    }
  });

  wrap.addEventListener("pointermove", (event) => {
    const active = state.pointer;
    if (!active || active.id !== event.pointerId) return;
    const point = pointToSelection(event);
    if (!point) return;
    event.preventDefault();
    if (active.mode === "move") {
      state.userSelectionDirty = true;
      const dx = point.x - active.start.x;
      const dy = point.y - active.start.y;
      state.selection = clampSelection({
        x: active.original.x + dx,
        y: active.original.y + dy,
        w: active.original.w,
        h: active.original.h,
      });
    } else if (active.mode === "resize") {
      state.userSelectionDirty = true;
      const x2 = Math.max(active.original.x + 0.03, point.x);
      const y2 = Math.max(active.original.y + 0.03, point.y);
      state.selection = clampSelection({
        x: active.original.x,
        y: active.original.y,
        w: x2 - active.original.x,
        h: y2 - active.original.y,
      });
    } else {
      state.userSelectionDirty = true;
      const x1 = Math.min(active.start.x, point.x);
      const y1 = Math.min(active.start.y, point.y);
      const x2 = Math.max(active.start.x, point.x);
      const y2 = Math.max(active.start.y, point.y);
      state.selection = clampSelection({ x: x1, y: y1, w: x2 - x1, h: y2 - y1 });
    }
    renderSelectionBox();
  });

  wrap.addEventListener("pointerup", (event) => {
    if (state.pointer?.id === event.pointerId) state.pointer = null;
  });
  wrap.addEventListener("pointercancel", () => {
    state.pointer = null;
  });
  window.addEventListener("resize", renderSelectionBox);
}

function sizeFromMeasurement(editable) {
  if (editable.width_mm === null || editable.width_mm === undefined || editable.height_mm === null || editable.height_mm === undefined) return "";
  const w = Number(editable.width_mm);
  const h = Number(editable.height_mm);
  if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return "";
  return `${w.toFixed(1)}mm x ${h.toFixed(1)}mm`;
}

function setEditorEnabled(enabled, clearValues = false) {
  [
    "fieldName",
    "fieldCategory",
    "fieldManufacturer",
    "fieldModel",
    "fieldQuantity",
    "fieldWeight",
    "fieldSize",
    "fieldAssetCode",
    "fieldTags",
    "fieldLocation",
    "fieldDescription",
    "fieldReasoning",
  ].forEach((id) => {
    $(id).disabled = !enabled;
    if (clearValues) $(id).value = "";
  });
  syncEditorOptionControls(enabled, clearValues);
}

function syncEditorOptionControls(enabled = state.hasPending && !state.hasCommitted, clearValues = false) {
  [
    ["fieldTagSelect", state.homeboxTags.length],
    ["fieldLocationSelect", state.homeboxLocations.length],
  ].forEach(([id, count]) => {
    const el = $(id);
    if (el) {
      el.disabled = !enabled || Number(count) <= 0;
      if (clearValues) el.value = "";
    }
  });
}

function homeboxOptionsKey() {
  return JSON.stringify({
    url: fieldValue("homeboxConfigUrl", ""),
    username: fieldValue("homeboxUsername", ""),
    token: fieldValue("homeboxToken", "") ? "token" : "",
    password: fieldValue("homeboxPassword", "") ? "password" : "",
  });
}

function setNamedOptions(selectId, rows, placeholder) {
  const select = $(selectId);
  if (!select) return;
  const current = select.value;
  select.innerHTML = "";
  const first = document.createElement("option");
  first.value = "";
  first.textContent = placeholder;
  select.appendChild(first);
  (rows || []).forEach((row) => {
    const name = typeof row === "string" ? row : row?.name;
    if (!name) return;
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    if (row?.description) option.title = row.description;
    select.appendChild(option);
  });
  select.value = Array.from(select.options).some((option) => option.value === current) ? current : "";
  syncEditorOptionControls();
}

async function loadHomeboxOptions(force = false) {
  const key = homeboxOptionsKey();
  if (state.homeboxOptionsLoading || (!force && state.homeboxOptionsKey === key)) return;
  state.homeboxOptionsLoading = true;
  setNamedOptions("fieldTagSelect", [], "读取标签中...");
  setNamedOptions("fieldLocationSelect", [], "读取位置中...");
  try {
    const data = await fetch("/api/homebox/options", { cache: "no-store" }).then((r) => r.json());
    state.homeboxOptionsKey = key;
    state.homeboxTags = data.tags || [];
    state.homeboxLocations = data.locations || [];
    setNamedOptions("fieldTagSelect", state.homeboxTags, state.homeboxTags.length ? "添加已有标签" : "无已有标签");
    setNamedOptions("fieldLocationSelect", state.homeboxLocations, state.homeboxLocations.length ? "选择已有位置" : "无已有位置");
    if (!data.ok) appendLog(data.message || "Homebox 选项读取失败");
  } catch (err) {
    state.homeboxOptionsKey = "";
    state.homeboxTags = [];
    state.homeboxLocations = [];
    setNamedOptions("fieldTagSelect", [], "标签读取失败");
    setNamedOptions("fieldLocationSelect", [], "位置读取失败");
    appendLog(`Homebox 选项读取失败: ${err}`);
  } finally {
    state.homeboxOptionsLoading = false;
    syncEditorOptionControls();
  }
}

function addSelectedHomeboxTag() {
  const select = $("fieldTagSelect");
  const value = select?.value || "";
  if (!value) return;
  const input = $("fieldTags");
  const parts = input.value
    .split(new RegExp("[,，、/;；\\n]+"))
    .map((item) => item.trim())
    .filter(Boolean);
  if (!parts.includes(value)) parts.push(value);
  input.value = parts.join(", ");
  select.value = "";
}

function applySelectedHomeboxLocation() {
  const select = $("fieldLocationSelect");
  const value = select?.value || "";
  if (!value) return;
  $("fieldLocation").value = value;
  select.value = "";
}

function homeboxLocationExistsExact(name) {
  const target = String(name || "").trim();
  if (!target) return true;
  return state.homeboxLocations.some((row) => {
    const locationName = typeof row === "string" ? row : row?.name;
    return locationName === target;
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function post(path, payload = {}) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) appendLog(data.message || `请求失败 ${res.status}`);
  return data;
}

async function refreshStatusNow() {
  try {
    const data = await fetch("/api/status", { cache: "no-store" }).then((r) => r.json());
    render(data);
  } catch (err) {
    appendLog(`状态刷新失败: ${err}`);
  }
}

function selectedOllamaTarget() {
  return document.querySelector('input[name="ollamaTarget"]:checked')?.value || "local";
}

function selectedAiProvider() {
  return selectedOllamaTarget() === "cloud" ? fieldValue("cloudProvider", "openai") : "ollama";
}

function defaultCloudBase(provider = "openai") {
  return provider === "gemini"
    ? "https://generativelanguage.googleapis.com/v1beta/openai"
    : "https://api.openai.com/v1";
}

function normalizeOllamaHost(value) {
  let text = String(value || "").trim();
  if (!text) return "http://127.0.0.1:11434";
  if (!/^https?:\/\//i.test(text)) text = `http://${text}`;
  try {
    const url = new URL(text);
    if (!url.port) url.port = "11434";
    url.pathname = "";
    url.search = "";
    url.hash = "";
    return url.toString().replace(/\/$/, "");
  } catch {
    return "http://127.0.0.1:11434";
  }
}

function ollamaBaseUrl() {
  if (selectedOllamaTarget() === "local") return "http://127.0.0.1:11434";
  return normalizeOllamaHost($("ollamaLanHost").value || "192.168.31.164:11434");
}

function ollamaChatUrl() {
  return `${ollamaBaseUrl()}/api/chat`;
}

function normalizeCloudBase(value, provider = "openai") {
  let text = String(value || "").trim() || defaultCloudBase(provider);
  if (!/^https?:\/\//i.test(text)) text = `https://${text}`;
  try {
    const url = new URL(text);
    url.hash = "";
    url.search = "";
    let path = url.pathname.replace(/\/+$/, "");
    path = path.replace(/\/chat\/completions$/i, "");
    url.pathname = path || "/";
    return url.toString().replace(/\/$/, "");
  } catch {
    return defaultCloudBase(provider);
  }
}

function selectedModelName() {
  return fieldValue("modelManual", "") || fieldValue("model", "gemma3:4b");
}

function aiEndpointLabel() {
  if (selectedAiProvider() === "ollama") return ollamaBaseUrl();
  const provider = fieldValue("cloudProvider", "openai");
  return `${provider} @ ${normalizeCloudBase(fieldValue("cloudApiBase", ""), provider)}`;
}

function applyConfigToControls(config) {
  if (!config || state.controlsHydrated) return;
  state.controlsHydrated = true;
  if (config.mode && $("mode")) $("mode").value = config.mode;
  if (config.num_predict && $("tokens")) $("tokens").value = String(config.num_predict);
  if (config.image_max_size !== undefined && $("imageSize")) $("imageSize").value = String(config.image_max_size);
  if (config.homebox_url && $("homeboxConfigUrl")) $("homeboxConfigUrl").value = config.homebox_url;
  if (config.homebox_username && $("homeboxUsername")) $("homeboxUsername").value = config.homebox_username;
  if (config.scale_baud && $("scaleBaud")) $("scaleBaud").value = String(config.scale_baud);
  setCameraControls(config);
  applyLabelOptionState(config, true);

  const base = normalizeOllamaHost(config.ollama_url || "");
  const localBases = new Set(["http://127.0.0.1:11434", "http://localhost:11434"]);
  const target = config.ai_target || (localBases.has(base) ? "local" : "lan");
  const radio = document.querySelector(`input[name="ollamaTarget"][value="${target}"]`);
  if (radio) radio.checked = true;
  if (target === "lan" && $("ollamaLanHost")) $("ollamaLanHost").value = base;
  if ($("cloudProvider")) $("cloudProvider").value = config.cloud_provider || "openai";
  if ($("cloudApiBase")) $("cloudApiBase").value = config.ai_api_base || defaultCloudBase(fieldValue("cloudProvider", "openai"));
  if ($("cloudApiKey")) {
    $("cloudApiKey").placeholder = config.ai_has_api_key ? "已保存，留空则继续使用" : "只保存在当前运行进程中";
  }
  if ($("modelManual")) $("modelManual").value = config.ollama_model || "gemma3:4b";
  updateOllamaControls(false);
  loadHomeboxOptions();
}

function setModelOptions(models, preferred = null) {
  const modelSelect = $("model");
  const current = preferred || selectedModelName();
  const list = Array.isArray(models) && models.length ? models : [current || "gemma3:4b"];
  modelSelect.innerHTML = "";
  list.forEach((name) => {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    modelSelect.appendChild(option);
  });
  if (list.includes(current)) modelSelect.value = current;
  if ($("modelManual") && preferred) $("modelManual").value = preferred;
}

async function refreshModels(preferred = null) {
  const provider = selectedAiProvider();
  const endpoint = provider === "ollama"
    ? ollamaBaseUrl()
    : normalizeCloudBase(fieldValue("cloudApiBase", ""), fieldValue("cloudProvider", "openai"));
  const endpointKey = `${provider}:${endpoint}`;
  state.modelEndpoint = endpointKey;
  const modelSelect = $("model");
  const previous = preferred || selectedModelName();
  modelSelect.disabled = true;
  try {
    const data = await post("/api/ai/models", {
      provider,
      base_url: endpoint,
      api_key: provider === "ollama" ? "" : fieldValue("cloudApiKey", ""),
    });
    if (state.modelEndpoint !== endpointKey) return;
    if (data.ok) {
      state.selectedOllama = { connected: true, models: data.models || [], ps: data.ps || "", error: null };
      setModelOptions(data.models || [], previous);
      $("ollamaPs").textContent = data.ps || (provider === "ollama" ? "Ollama 未返回运行模型" : "云端模型列表已读取");
      setChip("chip-ollama", true, false, "AI");
    } else {
      state.selectedOllama = { connected: false, models: [], ps: "", error: data.message || "未连接" };
      appendLog(data.message || "模型列表读取失败");
      setModelOptions([previous || "gemma3:4b"], previous);
      setChip("chip-ollama", false, false, "AI");
    }
  } catch (err) {
    state.selectedOllama = { connected: false, models: [], ps: "", error: String(err) };
    appendLog(`模型列表读取失败: ${err}`);
    setModelOptions([previous || "gemma3:4b"], previous);
    setChip("chip-ollama", false, false, "AI");
  } finally {
    if (state.modelEndpoint === endpointKey) modelSelect.disabled = false;
  }
}

function updateOllamaControls(save = true) {
  const target = selectedOllamaTarget();
  const lan = target === "lan";
  const cloud = target === "cloud";
  $("ollamaLanHost").disabled = !lan;
  ["cloudProvider", "cloudApiBase", "cloudApiKey"].forEach((id) => {
    const el = $(id);
    if (el) el.disabled = !cloud;
  });
  if (cloud && $("cloudApiBase") && !$("cloudApiBase").value.trim()) {
    $("cloudApiBase").value = defaultCloudBase(fieldValue("cloudProvider", "openai"));
  }
  refreshModels();
  if (save) scheduleSaveRuntimeConfig();
}

function modePayload(extra = {}) {
  return {
    mode: $("mode").value,
    homebox_url: fieldValue("homeboxConfigUrl", ""),
    homebox_username: fieldValue("homeboxUsername", ""),
    homebox_password: fieldValue("homeboxPassword", ""),
    homebox_token: fieldValue("homeboxToken", ""),
    ai_target: selectedOllamaTarget(),
    cloud_provider: fieldValue("cloudProvider", "openai"),
    ai_api_base: normalizeCloudBase(fieldValue("cloudApiBase", ""), fieldValue("cloudProvider", "openai")),
    ai_api_key: fieldValue("cloudApiKey", ""),
    ollama_url: ollamaChatUrl(),
    model: selectedModelName(),
    num_predict: Number($("tokens").value),
    image_max_size: Number($("imageSize").value),
    scale_port: fieldValue("scalePort", "auto"),
    scale_baud: Number(fieldValue("scaleBaud", "9600")),
    rfid_port: fieldValue("rfidPort", ""),
    aux_camera_enabled: fieldChecked("auxCameraEnabled", true),
    aux_camera_index: fieldValue("auxCameraIndex", "0"),
    aux_camera_backend: fieldValue("auxCameraBackend", "dshow"),
    aux_camera_width: Number(fieldValue("auxCameraWidth", "1280")),
    aux_camera_height: Number(fieldValue("auxCameraHeight", "720")),
    aux_camera_center_crop: fieldNumber("auxCameraCenterCrop", 1),
    aux_camera_auto_exposure: fieldChecked("auxCameraAutoExposure", true),
    aux_camera_warmup_seconds: Number(fieldValue("auxCameraWarmup", "1.2")),
    print_pet_labels: fieldChecked("printPetLabels", true),
    write_rfid_tags: fieldChecked("writeRfidTags", true),
    ...extra,
  };
}

function runtimeConfigPayload() {
  return {
    mode: fieldValue("mode", "auto"),
    homebox_url: fieldValue("homeboxConfigUrl", ""),
    homebox_username: fieldValue("homeboxUsername", ""),
    homebox_password: fieldValue("homeboxPassword", ""),
    homebox_token: fieldValue("homeboxToken", ""),
    ai_target: selectedOllamaTarget(),
    cloud_provider: fieldValue("cloudProvider", "openai"),
    ai_api_base: normalizeCloudBase(fieldValue("cloudApiBase", ""), fieldValue("cloudProvider", "openai")),
    ai_api_key: fieldValue("cloudApiKey", ""),
    ollama_url: ollamaChatUrl(),
    ollama_model: selectedModelName(),
    num_predict: Number(fieldValue("tokens", "192")),
    image_max_size: Number(fieldValue("imageSize", "0")),
    scale_port: fieldValue("scalePort", "auto"),
    scale_baud: Number(fieldValue("scaleBaud", "9600")),
    rfid_port: fieldValue("rfidPort", ""),
    aux_camera_enabled: fieldChecked("auxCameraEnabled", true),
    aux_camera_index: fieldValue("auxCameraIndex", "0"),
    aux_camera_backend: fieldValue("auxCameraBackend", "dshow"),
    aux_camera_width: Number(fieldValue("auxCameraWidth", "1280")),
    aux_camera_height: Number(fieldValue("auxCameraHeight", "720")),
    aux_camera_center_crop: fieldNumber("auxCameraCenterCrop", 1),
    aux_camera_auto_exposure: fieldChecked("auxCameraAutoExposure", true),
    aux_camera_warmup_seconds: Number(fieldValue("auxCameraWarmup", "1.2")),
    print_pet_labels: fieldChecked("printPetLabels", true),
    write_rfid_tags: fieldChecked("writeRfidTags", true),
  };
}

function scheduleSaveRuntimeConfig() {
  clearTimeout(configSaveTimer);
  configSaveTimer = setTimeout(saveRuntimeConfig, 250);
}

async function saveRuntimeConfig() {
  try {
    const shouldReloadHomebox = state.homeboxConfigDirty || homeboxOptionsKey() !== state.homeboxOptionsKey;
    const data = await post("/api/config", runtimeConfigPayload());
    if (data.ok && shouldReloadHomebox) {
      state.homeboxConfigDirty = false;
      loadHomeboxOptions(true);
    }
  } catch (err) {
    appendLog(`配置保存失败: ${err}`);
  }
}

function setActiveModule(module) {
  state.activeModule = module;
  document.querySelectorAll(".module-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.module === module);
  });
  document.querySelectorAll(".module-page").forEach((page) => {
    page.classList.toggle("active", page.dataset.modulePage === module);
  });
}

function appendLog(line) {
  const box = $("logBox");
  box.textContent += `${box.textContent ? "\n" : ""}${line}`;
  box.scrollTop = box.scrollHeight;
}

function itemSubtitle(item) {
  const parts = [];
  if (item.location) parts.push(item.location);
  if (item.manufacturer) parts.push(item.manufacturer);
  if (item.model) parts.push(item.model);
  if (item.tags?.length) parts.push(item.tags.slice(0, 3).join(", "));
  return parts.join(" · ") || item.description || "--";
}

function clearFindDetail(message = "选择一个物品查看位置、标签和识别信息") {
  state.findSelected = null;
  state.findSelectedUrl = "";
  $("findOpenHomebox").disabled = true;
  $("findDetail").innerHTML = `<div class="empty-state">${escapeHtml(message)}</div>`;
  document.querySelectorAll(".find-item").forEach((button) => button.classList.remove("active"));
}

function rfidStrength(row) {
  const value = Number(row?.rssi_dbm);
  return Number.isFinite(value) ? value : -999;
}

function normalizedRfidRows(rows) {
  return (rows || [])
    .map((row) => ({ ...row, kind: "rfid" }))
    .sort((a, b) => {
      const matchedDelta = Number(Boolean(b.item || b.matched)) - Number(Boolean(a.item || a.matched));
      if (matchedDelta) return matchedDelta;
      return rfidStrength(b) - rfidStrength(a);
    });
}

function setFindTab(source, selectFirst = false) {
  const tab = source === "rfid" ? "rfid" : "db";
  state.findActiveTab = tab;
  document.querySelectorAll("[data-find-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.findTab === tab);
  });
  document.querySelectorAll("[data-find-panel]").forEach((panel) => {
    const active = panel.dataset.findPanel === tab;
    panel.hidden = !active;
    panel.classList.toggle("active", active);
  });
  if (!selectFirst) return;
  const rows = tab === "rfid" ? state.findRfidRows : state.findDbRows;
  if (rows.length) {
    selectFindRow(tab, 0);
  } else if (!state.findSelected || state.findSelected.source === tab) {
    clearFindDetail(tab === "rfid" ? "尚未盘点 RFID 标签" : "没有选中的 Homebox 物品");
  }
}

function renderFindList(targetId, rows, source, emptyText) {
  const target = $(targetId);
  if (!rows.length) {
    target.innerHTML = `<div class="empty-state">${escapeHtml(emptyText)}</div>`;
    return;
  }
  target.innerHTML = rows
    .map((row, index) => {
      const item = source === "rfid" ? row.item : row;
      if (!item) {
        const rssi = row.rssi_dbm === null || row.rssi_dbm === undefined ? "--" : `${row.rssi_dbm} dBm`;
        const epcText = row.epc_ascii || row.epc || "--";
        return `
          <button class="find-item" data-find-source="${source}" data-find-index="${index}">
            <strong>未匹配 RFID 标签</strong>
            <span>${escapeHtml(epcText)}</span>
            <small>RSSI ${escapeHtml(rssi)} · 天线 ${escapeHtml(row.antenna ?? "--")}</small>
          </button>
        `;
      }
      const rfidText = source === "rfid" ? ` · RSSI ${row.rssi_dbm ?? "--"} dBm` : "";
      return `
        <button class="find-item" data-find-source="${source}" data-find-index="${index}">
          <strong>${escapeHtml(item.name || "未命名物品")}</strong>
          <span>${escapeHtml(itemSubtitle(item))}</span>
          <small class="${source === "rfid" ? "rfid-hit" : ""}">${escapeHtml(item.code || item.rfid_code || item.id || "--")}${escapeHtml(rfidText)}</small>
        </button>
      `;
    })
    .join("");
}

function renderFindDbResults(rows, message = "", emptyText = "没有 Homebox 结果") {
  state.findDbRows = rows || [];
  state.findDbLoaded = true;
  setFindTab("db");
  $("findCount").textContent = message || `${state.findDbRows.length} 项`;
  renderFindList("findResults", state.findDbRows, "db", emptyText);
  if (state.findDbRows.length) {
    window.setTimeout(() => selectFindRow("db", 0), 0);
  } else if (!state.findSelected || state.findSelected.source === "db") {
    clearFindDetail("没有选中的 Homebox 物品");
  }
}

function renderFindRfidResults(rows, message = "", emptyText = "未读到 RFID 标签") {
  state.findRfidRows = normalizedRfidRows(rows);
  setFindTab("rfid");
  $("findRfidCount").textContent = message || `${state.findRfidRows.length} 个标签`;
  renderFindList("findRfidResults", state.findRfidRows, "rfid", emptyText);
  if (state.findRfidRows.length) {
    window.setTimeout(() => selectFindRow("rfid", 0), 0);
  } else if (!state.findSelected) {
    clearFindDetail();
  }
}

async function selectFindRow(source, index) {
  const normalizedSource = source === "rfid" ? "rfid" : "db";
  const rows = normalizedSource === "rfid" ? state.findRfidRows : state.findDbRows;
  const row = rows[index];
  state.findSelected = { source: normalizedSource, index };
  document.querySelectorAll(".find-item").forEach((button) => {
    button.classList.toggle(
      "active",
      button.dataset.findSource === normalizedSource && Number(button.dataset.findIndex) === index,
    );
  });
  if (!row) return;
  const item = normalizedSource === "rfid" ? row.item : row;
  if (!item) {
    renderFindRfidOnly(row);
    return;
  }
  let detail = item;
  if (item.id) {
    const selectedKey = `${normalizedSource}:${index}`;
    const data = await post("/api/find/item", { id: item.id });
    if (!state.findSelected || `${state.findSelected.source}:${state.findSelected.index}` !== selectedKey) return;
    if (data.ok && data.item) detail = data.item;
    else appendLog(data.message || "物品详情读取失败");
  }
  renderFindDetail(detail, normalizedSource === "rfid" ? row : null);
}

function renderFindRfidOnly(tag) {
  state.findSelectedUrl = "";
  $("findOpenHomebox").disabled = true;
  $("findDetail").innerHTML = `
    <div class="detail-title">
      <h3>未匹配 RFID 标签</h3>
      <span class="detail-code">${escapeHtml(tag.epc_ascii || tag.epc || "--")}</span>
    </div>
    <div class="detail-grid">
      <span>EPC HEX</span><strong>${escapeHtml(tag.epc || "--")}</strong>
      <span>RSSI</span><strong>${escapeHtml(tag.rssi_dbm ?? "--")} dBm</strong>
      <span>天线</span><strong>${escapeHtml(tag.antenna ?? "--")}</strong>
    </div>
  `;
}

function renderFindDetail(item, rfidTag = null) {
  state.findSelectedUrl = item.url || "";
  $("findOpenHomebox").disabled = !state.findSelectedUrl;
  const tags = (item.tags || []).map((tag) => `<span class="tag-pill">${escapeHtml(tag)}</span>`).join("");
  const fields = (item.fields || []).map((field) => `
    <span>${escapeHtml(field.name || "--")}</span><strong>${escapeHtml(field.value || "--")}</strong>
  `).join("");
  const rfidRows = rfidTag ? `
    <span>当前 RFID</span><strong>${escapeHtml(rfidTag.epc_ascii || rfidTag.epc || "--")}</strong>
    <span>信号</span><strong>${escapeHtml(rfidTag.rssi_dbm ?? "--")} dBm</strong>
  ` : "";
  $("findDetail").innerHTML = `
    <div class="detail-title">
      <h3>${escapeHtml(item.name || "未命名物品")}</h3>
      <span class="detail-code">${escapeHtml(item.code || item.rfid_code || item.id || "--")}</span>
    </div>
    <div class="detail-grid">
      <span>位置</span><strong>${escapeHtml(item.location || "--")}</strong>
      <span>RFID</span><strong>${escapeHtml(item.rfid_code || "--")}</strong>
      <span>制造商</span><strong>${escapeHtml(item.manufacturer || "--")}</strong>
      <span>型号</span><strong>${escapeHtml(item.model || "--")}</strong>
      <span>数量</span><strong>${escapeHtml(item.quantity ?? "--")}</strong>
      ${rfidRows}
      ${fields}
    </div>
    <div>
      <span class="label">标签</span>
      <div class="tag-line">${tags || '<span class="empty-state">无标签</span>'}</div>
    </div>
    <div>
      <span class="label">描述</span>
      <p>${escapeHtml(item.description || item.notes || "--")}</p>
    </div>
  `;
}

async function runFindSearch() {
  const query = fieldValue("findQuery", "").trim();
  const limit = fieldNumber("findLimit", 50);
  $("findCount").textContent = "搜索中...";
  const data = await post("/api/find/search", { q: query, limit });
  appendLog(data.message || (data.ok ? "找物搜索完成" : "找物搜索失败"));
  renderFindDbResults(data.items || [], data.message || "");
}

async function runFindRfidScan() {
  $("findRfidCount").textContent = "盘点中...";
  const data = await post("/api/find/rfid", runtimeConfigPayload());
  appendLog(data.message || (data.ok ? "RFID 找物完成" : "RFID 找物失败"));
  renderFindRfidResults(data.tags || [], data.message || "");
}

function logImageUrl(nameOrPath) {
  const text = String(nameOrPath || "").trim();
  if (!text) return "";
  const name = text.split(/[\\/]/).pop();
  return name ? `/logs/${encodeURIComponent(name)}?t=${Date.now()}` : "";
}

function renderLabelPreview(preview) {
  const panel = $("labelPreviewPanel");
  if (!preview) {
    panel.hidden = true;
    $("humanLabelPreview").removeAttribute("src");
    $("codeLabelPreview").removeAttribute("src");
    setText("labelPreviewEpc", "--");
    setText("labelPreviewUrl", "--");
    return;
  }
  state.labelPreview = preview;
  panel.hidden = false;
  const human = logImageUrl(preview.human_preview_name || preview.human_preview);
  const code = logImageUrl(preview.code_preview_name || preview.code_preview);
  if (human) $("humanLabelPreview").src = human;
  if (code) $("codeLabelPreview").src = code;
  const payload = preview.rfid_payload || {};
  setText("labelPreviewTitle", preview.code ? `${preview.code} · 预览` : "标签预览");
  setText("labelPreviewEpc", payload.epc_code || payload.epc_hex_candidate || "--");
  setText("labelPreviewUrl", payload.url || "--");
}

async function fetchLabelPreview() {
  const data = await post("/api/label_preview");
  appendLog(data.message || (data.ok ? "标签预览已生成" : "标签预览失败"));
  if (!data.ok || !data.preview) return null;
  renderLabelPreview(data.preview);
  return data.preview;
}

function collectPendingFields() {
  return {
    name: $("fieldName").value.trim(),
    category: $("fieldCategory").value.trim(),
    manufacturer: $("fieldManufacturer").value.trim(),
    model: $("fieldModel").value.trim(),
    quantity: Number($("fieldQuantity").value || 1),
    weight_g: $("fieldWeight").value === "" ? null : Number($("fieldWeight").value),
    size: $("fieldSize").value.trim(),
    asset_code: $("fieldAssetCode").value.trim(),
    tags: $("fieldTags").value.trim(),
    suggested_location: $("fieldLocation").value.trim(),
    description: $("fieldDescription").value.trim(),
    reasoning: $("fieldReasoning").value.trim(),
  };
}

function renderRfidConfirm(data) {
  const payload = data?.target_payload || {};
  $("rfidTargetCode").textContent = payload.epc_code || payload.code || "--";
  const tags = data?.tags || [];
  const defaultEpc = data?.default_epc || tags[0]?.epc || "";
  const list = $("rfidTagList");
  if (!tags.length) {
    list.innerHTML = `<div class="rfid-empty">没有读到 RFID 标签</div>`;
    return;
  }
  list.innerHTML = tags
    .map((tag, index) => {
      const epc = tag.epc || "";
      const checked = epc === defaultEpc || (!defaultEpc && index === 0);
      const ascii = tag.epc_ascii ? `ASCII: ${escapeHtml(tag.epc_ascii)}` : "ASCII: --";
      const rssi = tag.rssi_dbm === null || tag.rssi_dbm === undefined ? "--" : `${tag.rssi_dbm} dBm`;
      return `
        <label class="rfid-choice ${checked ? "selected" : ""}">
          <input type="radio" name="rfidTarget" value="${escapeHtml(epc)}" ${checked ? "checked" : ""}>
          <span>
            <strong>${escapeHtml(epc)}</strong>
            <span>${ascii} · Ant ${escapeHtml(tag.antenna ?? "--")}</span>
          </span>
          <span class="rfid-rssi">${escapeHtml(rssi)}</span>
        </label>
      `;
    })
    .join("");
  document.querySelectorAll(".rfid-choice input").forEach((input) => {
    input.addEventListener("change", () => {
      document.querySelectorAll(".rfid-choice").forEach((row) => row.classList.remove("selected"));
      input.closest(".rfid-choice")?.classList.add("selected");
    });
  });
}

function renderRfidProbePanel(data) {
  const panel = $("rfidProbePanel");
  const tags = data?.tags || [];
  setText("rfidConfigState", data?.message || (data?.ok ? `读到 ${tags.length} 个标签` : "探测失败"));
  if (!data?.ok) {
    panel.innerHTML = `<div class="rfid-empty">${escapeHtml(data?.message || "RFID 探测失败")}</div>`;
    return;
  }
  if (!tags.length) {
    panel.innerHTML = `<div class="rfid-empty">没有读到 RFID 标签</div>`;
    return;
  }
  const target = data.target_payload?.epc_code
    ? `<div class="rfid-probe-target"><span>当前待写入</span><strong>${escapeHtml(data.target_payload.epc_code)}</strong></div>`
    : "";
  panel.innerHTML = `
    ${target}
    <div class="rfid-tag-list">
      ${tags
        .map((tag) => {
          const epc = tag.epc || "--";
          const ascii = tag.epc_ascii ? `ASCII: ${escapeHtml(tag.epc_ascii)}` : "ASCII: --";
          const rssi = tag.rssi_dbm === null || tag.rssi_dbm === undefined ? "--" : `${tag.rssi_dbm} dBm`;
          return `
            <div class="rfid-choice rfid-probe-row">
              <span class="rfid-signal-dot"></span>
              <span>
                <strong>${escapeHtml(epc)}</strong>
                <span>${ascii} · Ant ${escapeHtml(tag.antenna ?? "--")}</span>
              </span>
              <span class="rfid-rssi">${escapeHtml(rssi)}</span>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderRfidReleasePanel(data) {
  const panel = $("rfidProbePanel");
  const killed = data?.killed || [];
  const killedHtml = killed.length
    ? `<div class="rfid-tag-list">${killed
        .map((proc) => `
          <div class="rfid-choice rfid-probe-row">
            <span class="rfid-signal-dot"></span>
            <span>
              <strong>PID ${escapeHtml(proc.pid)}</strong>
              <span>${escapeHtml(proc.name || "")}</span>
            </span>
          </div>
        `)
        .join("")}</div>`
    : "";
  panel.innerHTML = `
    <div class="rfid-empty">${escapeHtml(data?.message || "串口释放完成")}</div>
    ${data?.before_error ? `<div class="rfid-empty">释放前: ${escapeHtml(data.before_error)}</div>` : ""}
    ${data?.after_error ? `<div class="rfid-empty">释放后: ${escapeHtml(data.after_error)}</div>` : ""}
    ${killedHtml}
  `;
}

async function fetchRfidInventoryForConfirm() {
  const data = await post("/api/rfid/inventory", runtimeConfigPayload());
  appendLog(data.message || (data.ok ? "RFID 盘点完成" : "RFID 盘点失败"));
  if (!data.ok) return null;
  state.rfidInventory = data;
  renderRfidConfirm(data);
  return data;
}

function openRfidConfirm(data) {
  renderRfidConfirm(data);
  const modal = $("rfidConfirmModal");
  modal.hidden = false;
  return new Promise((resolve) => {
    const finish = (value) => {
      modal.hidden = true;
      $("rfidConfirmClose").onclick = null;
      $("rfidConfirmWrite").onclick = null;
      $("rfidConfirmRefresh").onclick = null;
      resolve(value);
    };
    $("rfidConfirmClose").onclick = () => finish(null);
    $("rfidConfirmWrite").onclick = () => {
      const checked = document.querySelector('input[name="rfidTarget"]:checked');
      if (!checked?.value) {
        appendLog("请先选择要写入的 RFID 标签");
        return;
      }
      finish(checked.value);
    };
    $("rfidConfirmRefresh").onclick = async () => {
      $("rfidConfirmRefresh").disabled = true;
      try {
        await fetchRfidInventoryForConfirm();
      } finally {
        $("rfidConfirmRefresh").disabled = false;
      }
    };
  });
}

function openLabelOptions() {
  const modal = $("labelOptionsModal");
  if (!modal) return Promise.resolve({ printPet: petLabelEnabled(), writeRfid: rfidLabelEnabled() });
  setChecked("labelPrintPet", true);
  setChecked("labelWriteRfid", true);
  modal.hidden = false;
  return new Promise((resolve) => {
    const finish = (value) => {
      modal.hidden = true;
      $("labelOptionsClose").onclick = null;
      $("labelOptionsContinue").onclick = null;
      resolve(value);
    };
    $("labelOptionsClose").onclick = () => finish(null);
    $("labelOptionsContinue").onclick = () => {
      const printPet = petLabelEnabled();
      const writeRfid = rfidLabelEnabled();
      if (!printPet && !writeRfid) {
        appendLog("请至少选择 PET 标签或 RFID 标签。");
        return;
      }
      finish({ printPet, writeRfid });
    };
  });
}

function attachHandlers() {
  document.querySelectorAll("[data-app-mode]").forEach((button) => {
    button.addEventListener("click", () => setAppMode(button.dataset.appMode || "intake"));
  });
  document.querySelectorAll(".module-tab").forEach((tab) => {
    tab.addEventListener("click", () => setActiveModule(tab.dataset.module || "overview"));
  });
  document.querySelectorAll('input[name="ollamaTarget"]').forEach((el) => {
    el.addEventListener("change", updateOllamaControls);
  });
  $("cloudProvider").addEventListener("change", () => {
    $("cloudApiBase").value = defaultCloudBase(fieldValue("cloudProvider", "openai"));
    updateOllamaControls();
  });
  ["cloudApiBase", "cloudApiKey"].forEach((id) => {
    $(id).addEventListener("change", () => {
      refreshModels();
      scheduleSaveRuntimeConfig();
    });
    $(id).addEventListener("blur", () => {
      refreshModels();
      scheduleSaveRuntimeConfig();
    });
  });
  $("ollamaLanHost").addEventListener("change", () => {
    refreshModels();
    scheduleSaveRuntimeConfig();
  });
  $("ollamaLanHost").addEventListener("blur", () => {
    refreshModels();
    scheduleSaveRuntimeConfig();
  });
  $("refreshModels").addEventListener("click", () => refreshModels());
  $("refreshHomeboxOptions").addEventListener("click", () => loadHomeboxOptions(true));
  $("labelPreviewRefresh").addEventListener("click", fetchLabelPreview);
  $("findSearch").addEventListener("click", runFindSearch);
  $("findRfidScan").addEventListener("click", runFindRfidScan);
  $("findClear").addEventListener("click", () => {
    $("findQuery").value = "";
    state.findDbRows = [];
    state.findRfidRows = [];
    state.findDbLoaded = false;
    $("findCount").textContent = "--";
    $("findRfidCount").textContent = "未盘点";
    $("findResults").innerHTML = `<div class="empty-state">输入关键词搜索数据库</div>`;
    $("findRfidResults").innerHTML = `<div class="empty-state">点击扫 RFID 读取附近标签</div>`;
    setFindTab("db");
    clearFindDetail();
  });
  document.querySelectorAll("[data-find-tab]").forEach((button) => {
    button.addEventListener("click", () => setFindTab(button.dataset.findTab || "db", true));
  });
  $("findQuery").addEventListener("keydown", (event) => {
    if (event.key === "Enter") runFindSearch();
  });
  $("findResults").addEventListener("click", (event) => {
    const button = event.target.closest("[data-find-index]");
    if (!button) return;
    selectFindRow(button.dataset.findSource || "db", Number(button.dataset.findIndex));
  });
  $("findRfidResults").addEventListener("click", (event) => {
    const button = event.target.closest("[data-find-index]");
    if (!button) return;
    selectFindRow(button.dataset.findSource || "rfid", Number(button.dataset.findIndex));
  });
  $("findOpenHomebox").addEventListener("click", () => {
    if (state.findSelectedUrl) window.open(state.findSelectedUrl, "_blank", "noopener");
  });
  [
    "mode",
    "model",
    "modelManual",
    "tokens",
    "imageSize",
    "scalePort",
    "scaleBaud",
    "rfidPort",
    "auxCameraEnabled",
    "auxCameraIndex",
    "auxCameraBackend",
    "auxCameraWidth",
    "auxCameraHeight",
    "auxCameraCenterCrop",
    "auxCameraAutoExposure",
    "auxCameraWarmup",
    "printPetLabels",
    "writeRfidTags",
  ].forEach((id) => {
    const el = $(id);
    if (el) el.addEventListener("change", scheduleSaveRuntimeConfig);
  });
  $("model").addEventListener("change", () => {
    $("modelManual").value = $("model").value;
    scheduleSaveRuntimeConfig();
  });
  $("modelManual").addEventListener("blur", scheduleSaveRuntimeConfig);
  [
    ["printPetLabels", "labelPrintPet"],
    ["writeRfidTags", "labelWriteRfid"],
  ].forEach(([sourceId, targetId]) => {
    const el = $(sourceId);
    if (el) el.addEventListener("change", () => syncLabelOption(sourceId, targetId));
  });
  ["homeboxConfigUrl", "homeboxUsername", "homeboxPassword", "homeboxToken"].forEach((id) => {
    $(id).addEventListener("change", () => {
      state.homeboxConfigDirty = true;
      scheduleSaveRuntimeConfig();
    });
    $(id).addEventListener("blur", () => {
      state.homeboxConfigDirty = true;
      scheduleSaveRuntimeConfig();
    });
  });
  $("fieldTagSelect").addEventListener("change", addSelectedHomeboxTag);
  $("fieldLocationSelect").addEventListener("change", applySelectedHomeboxLocation);
  $("capture").addEventListener("click", () => post("/api/action", modePayload({ action: "capture" })));
  $("identifySelection").addEventListener("click", async () => {
    if (!state.lastImage) {
      appendLog("请先拍照生成可框选图像。");
      return;
    }
    ensureDefaultSelection();
    const data = await post(
      "/api/action",
      modePayload({
        action: "identify_selection",
        image_name: state.lastImage,
        aux_image_name: state.lastAuxImage,
        result_name: state.lastResult,
        bbox: state.selection,
        reexpose: true,
      })
    );
    appendLog(data.message ? `识别任务: ${data.message}` : "已提交识别");
  });
  $("resetSelection").addEventListener("click", resetSelection);
  $("labelPreviewButton").addEventListener("click", fetchLabelPreview);
  $("importRecord").addEventListener("click", () => $("recordFile").click());
  $("recordFile").addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const record = JSON.parse(text);
      const data = await post("/api/import_intake_record", { record });
      appendLog(data.message || (data.ok ? "入库记录已导入" : "入库记录导入失败"));
      await refreshStatusNow();
    } catch (err) {
      appendLog(`入库记录导入失败: ${err}`);
    } finally {
      event.target.value = "";
    }
  });
  $("scanWrite").addEventListener("click", async () => {
    if (!state.hasPending) {
      appendLog("请先点击“识别物品”，生成可编辑物品。");
      return;
    }
    if (state.hasCommitted) {
      appendLog("当前物品已经入库，可直接写标签。");
      return;
    }
    if (!$("fieldName").value.trim()) {
      appendLog("名称不能为空。");
      return;
    }
    const fields = collectPendingFields();
    const locationName = fields.suggested_location;
    if (locationName && !homeboxLocationExistsExact(locationName)) {
      const createLocation = confirm(`Homebox 里没有逐字匹配的位置：\n${locationName}\n\n是否先创建这个新位置，然后继续入库？`);
      if (!createLocation) {
        appendLog("已取消入库：请改为选择已有位置，或确认创建新位置。");
        return;
      }
      fields.create_missing_location = true;
    }
    if (!confirm("确认按当前表单写入 Homebox？")) return;
    const data = await post("/api/commit", fields);
    appendLog(data.message || (data.ok ? "入库完成" : "入库失败"));
    if (data.task?.pending_item?.editable?.asset_code) {
      $("fieldAssetCode").value = data.task.pending_item.editable.asset_code;
    }
    await refreshStatusNow();
  });
  $("labelWrite").addEventListener("click", async () => {
    if (!state.hasCommitted) {
      appendLog("请先入库，再写标签。");
      return;
    }
    const labelOptions = await openLabelOptions();
    if (!labelOptions) return;
    const { printPet, writeRfid } = labelOptions;
    if (!printPet && !writeRfid) {
      appendLog("请至少选择 PET 标签或 RFID 标签。");
      return;
    }
    let configSave = null;
    try {
      configSave = await post("/api/config", {
        ...runtimeConfigPayload(),
        print_pet_labels: printPet,
        write_rfid_tags: writeRfid,
      });
    } catch (err) {
      appendLog(`写标签配置保存失败: ${err}`);
      return;
    }
    if (configSave && configSave.ok === false) {
      appendLog("写标签配置保存失败，已取消。");
      return;
    }
    const preview = await fetchLabelPreview();
    if (!preview) return;
    let rfidTargetEpc = null;
    if (writeRfid) {
      const inventory = await fetchRfidInventoryForConfirm();
      if (!inventory || !(inventory.tags || []).length) return;
      rfidTargetEpc = await openRfidConfirm(inventory);
      if (!rfidTargetEpc) {
        appendLog("已取消 RFID 写入");
        return;
      }
    } else if (printPet && !confirm("只打印 PET 标签？")) {
      return;
    }
    const data = await post("/api/write_labels", {
      print_pet_labels: printPet,
      write_rfid_tags: writeRfid,
      rfid_target_epc: rfidTargetEpc,
    });
    appendLog(data.message || (data.ok ? "写标签完成" : "写标签失败"));
    if (data.result?.label) renderLabelPreview(data.result.label);
  });
  $("diagnose").addEventListener("click", () => post("/api/action", modePayload({ action: "diagnose" })));
  $("restartOllama").addEventListener("click", () => post("/api/action", { action: "ollama_restart" }));
  $("cancel").addEventListener("click", () => post("/api/cancel"));
  $("rfidProbe").addEventListener("click", async () => {
    const data = await post("/api/rfid/inventory", runtimeConfigPayload());
    appendLog(data.message || (data.ok ? "RFID 探测完成" : "RFID 探测失败"));
    renderRfidProbePanel(data);
  });
  $("rfidRelease").addEventListener("click", async () => {
    if (!confirm("只尝试结束 O.R.B.I.T. 自己启动的相关子进程来释放当前 RFID 串口，继续吗？")) return;
    const data = await post("/api/rfid/release", runtimeConfigPayload());
    appendLog(data.message || (data.ok ? "RFID 串口释放完成" : "RFID 串口释放失败"));
    renderRfidReleasePanel(data);
  });
  $("cameraProbe").addEventListener("click", async () => {
    const data = await post("/api/camera/probe", runtimeConfigPayload());
    appendLog(data.message || JSON.stringify(data));
    if (data.image) showLogImage("auxPreview", "emptyAuxPreview", data.image, "lastAuxImage");
  });
  $("clearLogs").addEventListener("click", () => {
    state.lastLogs = "";
    $("logBox").textContent = "";
  });
  $("discardPending").addEventListener("click", async () => {
    const data = await post("/api/discard_pending");
    appendLog(data.message || "已丢弃");
  });
}

async function pollFallback() {
  if (state.eventReady) return;
  try {
    const data = await fetch("/api/status", { cache: "no-store" }).then((r) => r.json());
    render(data);
  } catch (err) {
    appendLog(`状态刷新失败: ${err}`);
  }
}

function connectEvents() {
  const source = new EventSource("/api/events");
  source.onopen = () => {
    state.eventReady = true;
  };
  source.onmessage = (event) => {
    state.eventReady = true;
    render(JSON.parse(event.data));
  };
  source.onerror = () => {
    state.eventReady = false;
  };
  setInterval(pollFallback, 2000);
}

attachHandlers();
attachSelectionHandlers();
setAppMode("intake");
setEditorEnabled(false, true);
updateOllamaControls(false);
connectEvents();
pollFallback();
