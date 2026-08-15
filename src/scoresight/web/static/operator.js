const csrf = document.querySelector('meta[name="scoresight-csrf"]').content;
let config = JSON.parse(document.querySelector('#initial-config').textContent);
let selectedId = null;
let drawMode = null;
let dragStart = null;
let dragCurrent = null;
let perspectivePoints = [];
let previewBitmap = null;
let latestResultFields = [];
let latestResultSequence = 0;
let regionInteraction = null;
let lastFilteredPreviewAt = 0;
const acceptedPreviews = new Map();

const byId = (id) => document.getElementById(id);
const canvas = byId('preview');
const context = canvas.getContext('2d');

function toast(message, isError = false) {
  const node = byId('toast');
  node.textContent = message;
  node.style.borderColor = isError ? 'var(--red)' : 'var(--cyan)';
  node.classList.add('show');
  setTimeout(() => node.classList.remove('show'), 2800);
}

async function api(url, options = {}) {
  const headers = {...(options.headers || {})};
  if (options.method && options.method !== 'GET') headers['X-CSRF-Token'] = csrf;
  if (options.body && typeof options.body !== 'string') {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(url, {...options, headers});
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || response.statusText);
  if (response.status === 204) return null;
  return response.json();
}

function bindConfig() {
  byId('source-kind').value = config.source.kind;
  byId('source-device').value = config.source.device_id;
  byId('source-mode').value = config.source.mode;
  byId('target-hz').value = config.ocr.target_hz;
  byId('ocr-workers').value = config.ocr.workers;
  byId('ocr-model').value = config.ocr.model;
  byId('outputs-json').value = JSON.stringify(config.outputs, null, 2);
  pruneAcceptedPreviews();
  updateRegionEditor();
  render();
}

function collectConfig() {
  config.source.kind = byId('source-kind').value;
  config.source.device_id = byId('source-device').value;
  config.source.mode = byId('source-mode').value;
  config.ocr.target_hz = Number(byId('target-hz').value);
  config.ocr.workers = Number(byId('ocr-workers').value);
  config.ocr.model = byId('ocr-model').value;
  config.outputs = JSON.parse(byId('outputs-json').value || '[]');
  delete config.security;
  return config;
}

function selectedRegion() { return config.regions.find((region) => region.id === selectedId); }

function resultForRegion(regionId) {
  return latestResultFields.find((field) => field.id === regionId);
}

function updateRegionRuntime(forcePreview = false) {
  const region = selectedRegion();
  const field = region ? resultForRegion(region.id) : null;
  byId('selected-accepted-value').textContent = field?.value || '—';
  byId('selected-candidate-value').textContent = field?.candidate_value || '—';
  byId('filtered-preview-state').textContent = field?.state || 'Waiting';
  if (!region || !latestResultSequence) return;
  const now = performance.now();
  if (!forcePreview && now - lastFilteredPreviewAt < 180) return;
  lastFilteredPreviewAt = now;
  byId('filtered-preview').src = `/api/v1/regions/${encodeURIComponent(region.id)}/filter-preview?sequence=${latestResultSequence}`;
}

function pruneAcceptedPreviews() {
  const activeIds = new Set(config.regions.map((region) => region.id));
  for (const [regionId, bitmap] of acceptedPreviews) {
    if (!activeIds.has(regionId)) {
      bitmap.close();
      acceptedPreviews.delete(regionId);
    }
  }
}

async function captureAcceptedPreview(field) {
  if (!previewBitmap) return false;
  const region = config.regions.find((candidate) => candidate.id === field.id);
  if (!region) return false;
  const sourceWidth = previewBitmap.width;
  const sourceHeight = previewBitmap.height;
  const x = Math.max(0, Math.round(region.rect.x * sourceWidth));
  const y = Math.max(0, Math.round(region.rect.y * sourceHeight));
  const width = Math.max(1, Math.min(sourceWidth - x, Math.round(region.rect.width * sourceWidth)));
  const height = Math.max(1, Math.min(sourceHeight - y, Math.round(region.rect.height * sourceHeight)));
  try {
    const snapshot = await createImageBitmap(previewBitmap, x, y, width, height);
    acceptedPreviews.get(field.id)?.close();
    acceptedPreviews.set(field.id, snapshot);
    return true;
  } catch {
    return false;
  }
}

function drawAcceptedPreview(canvasNode, bitmap) {
  const thumbnailContext = canvasNode.getContext('2d');
  thumbnailContext.fillStyle = '#05080a';
  thumbnailContext.fillRect(0, 0, canvasNode.width, canvasNode.height);
  const scale = Math.min(canvasNode.width / bitmap.width, canvasNode.height / bitmap.height);
  const width = bitmap.width * scale;
  const height = bitmap.height * scale;
  thumbnailContext.drawImage(
    bitmap,
    (canvasNode.width - width) / 2,
    (canvasNode.height - height) / 2,
    width,
    height,
  );
}

function renderResults(fields) {
  byId('results').replaceChildren(...fields.map((field) => {
    const row = document.createElement('tr');
    const previewCell = document.createElement('td');
    const snapshot = acceptedPreviews.get(field.id);
    if (snapshot) {
      const thumbnail = document.createElement('canvas');
      thumbnail.width = 192;
      thumbnail.height = 96;
      thumbnail.className = 'result-thumbnail';
      thumbnail.setAttribute('role', 'img');
      thumbnail.setAttribute('aria-label', `Last accepted frame for ${field.name}`);
      drawAcceptedPreview(thumbnail, snapshot);
      previewCell.append(thumbnail);
    } else {
      previewCell.textContent = '—';
      previewCell.className = 'result-preview-empty';
    }
    row.append(previewCell);
    for (const value of [
      field.name,
      field.value,
      field.candidate_value || '—',
      field.state,
      field.confidence == null ? '—' : field.confidence.toFixed(2),
    ]) {
      const cell = document.createElement('td');
      cell.textContent = value;
      row.append(cell);
    }
    return row;
  }));
}

function updateRegionEditor() {
  const region = selectedRegion();
  byId('region-empty').hidden = Boolean(region);
  byId('region-fields').disabled = !region;
  if (!region) return;
  byId('region-name').value = region.name;
  byId('region-field-type').value = region.field_type || 'text';
  byId('region-regex').value = region.format_regex;
  byId('region-confidence').value = region.confidence_threshold;
  byId('region-confirmation').value = region.confirmation_frames || 2;
  byId('region-smoothing').value = region.smoothing_window;
  byId('region-threshold').value = region.preprocess.threshold_method;
  byId('region-dilate').value = region.preprocess.dilate_iterations;
  byId('region-vscale').value = region.preprocess.vertical_scale;
  byId('region-invert').checked = region.preprocess.invert;
  byId('region-autocrop').checked = region.preprocess.autocrop;
  byId('region-leading').checked = region.remove_leading_zeros;
  updateRegionRuntime(true);
}

function collectRegion() {
  const region = selectedRegion();
  if (!region) return;
  region.name = byId('region-name').value;
  region.field_type = byId('region-field-type').value;
  region.format_regex = byId('region-regex').value;
  region.confidence_threshold = Number(byId('region-confidence').value);
  region.confirmation_frames = Number(byId('region-confirmation').value);
  region.smoothing_window = Number(byId('region-smoothing').value);
  region.preprocess.threshold_method = byId('region-threshold').value;
  region.preprocess.dilate_iterations = Number(byId('region-dilate').value);
  region.preprocess.vertical_scale = Number(byId('region-vscale').value);
  region.preprocess.invert = byId('region-invert').checked;
  region.preprocess.autocrop = byId('region-autocrop').checked;
  region.remove_leading_zeros = byId('region-leading').checked;
}

function render() {
  context.clearRect(0, 0, canvas.width, canvas.height);
  if (previewBitmap) context.drawImage(previewBitmap, 0, 0, canvas.width, canvas.height);
  if (config.crop) {
    context.strokeStyle = '#78a9ff'; context.lineWidth = 3; context.setLineDash([8, 5]);
    context.strokeRect(config.crop.x * canvas.width, config.crop.y * canvas.height, config.crop.width * canvas.width, config.crop.height * canvas.height);
    context.setLineDash([]);
  }
  const corners = perspectivePoints;
  if (corners.length) {
    context.strokeStyle = '#ff6bba'; context.fillStyle = '#ff6bba'; context.lineWidth = 2;
    context.beginPath();
    corners.forEach((point, index) => { const x = point.x * canvas.width, y = point.y * canvas.height; if (index) context.lineTo(x, y); else context.moveTo(x, y); context.fillRect(x - 5, y - 5, 10, 10); });
    if (corners.length === 4) context.closePath(); context.stroke();
  }
  if (dragStart && dragCurrent) {
    const x = Math.min(dragStart.x, dragCurrent.x) * canvas.width;
    const y = Math.min(dragStart.y, dragCurrent.y) * canvas.height;
    const width = Math.abs(dragCurrent.x - dragStart.x) * canvas.width;
    const height = Math.abs(dragCurrent.y - dragStart.y) * canvas.height;
    context.strokeStyle = drawMode === 'crop' ? '#78a9ff' : '#55d9d0';
    context.lineWidth = 2;
    context.setLineDash([7, 4]);
    context.strokeRect(x, y, width, height);
    context.setLineDash([]);
  }
  for (const region of config.regions) {
    const {x, y, width, height} = region.rect;
    const isSelected = region.id === selectedId;
    const result = resultForRegion(region.id);
    context.strokeStyle = isSelected ? '#55d9d0' : '#f4c56a';
    context.lineWidth = isSelected ? 3 : 2;
    context.strokeRect(x * canvas.width, y * canvas.height, width * canvas.width, height * canvas.height);
    const label = result?.value ? `${region.name} · ${result.value}` : region.name;
    context.font = '14px system-ui';
    const labelWidth = Math.max(80, context.measureText(label).width + 12);
    const labelY = Math.max(0, y * canvas.height - 23);
    context.fillStyle = '#071014cc';
    context.fillRect(x * canvas.width, labelY, labelWidth, 22);
    context.fillStyle = context.strokeStyle;
    context.fillText(label, x * canvas.width + 5, labelY + 16);
    if (isSelected) {
      context.fillStyle = '#071014';
      context.strokeStyle = '#55d9d0';
      context.lineWidth = 2;
      for (const [handleX, handleY] of [
        [x, y], [x + width, y], [x + width, y + height], [x, y + height],
      ]) {
        context.beginPath();
        context.rect(handleX * canvas.width - 6, handleY * canvas.height - 6, 12, 12);
        context.fill();
        context.stroke();
      }
    }
  }
}

function canvasPoint(event) {
  const bounds = canvas.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
    y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)),
  };
}

function regionUnderPoint(point) {
  return [...config.regions].reverse().find(
    (region) =>
      point.x >= region.rect.x
      && point.x <= region.rect.x + region.rect.width
      && point.y >= region.rect.y
      && point.y <= region.rect.y + region.rect.height,
  );
}

function resizeHandleAt(point, region) {
  if (!region) return null;
  const bounds = canvas.getBoundingClientRect();
  const thresholdX = 12 / bounds.width;
  const thresholdY = 12 / bounds.height;
  const {x, y, width, height} = region.rect;
  const handles = {
    nw: {x, y},
    ne: {x: x + width, y},
    se: {x: x + width, y: y + height},
    sw: {x, y: y + height},
  };
  return Object.entries(handles).find(
    ([, handle]) =>
      Math.abs(point.x - handle.x) <= thresholdX
      && Math.abs(point.y - handle.y) <= thresholdY,
  )?.[0] || null;
}

function updateRegionInteraction(point) {
  if (!regionInteraction) return;
  const region = config.regions.find((candidate) => candidate.id === regionInteraction.id);
  if (!region) return;
  const original = regionInteraction.original;
  const minimum = 0.005;
  if (regionInteraction.type === 'move') {
    const deltaX = point.x - regionInteraction.start.x;
    const deltaY = point.y - regionInteraction.start.y;
    region.rect.x = Math.max(0, Math.min(1 - original.width, original.x + deltaX));
    region.rect.y = Math.max(0, Math.min(1 - original.height, original.y + deltaY));
    return;
  }
  let x1 = original.x;
  let y1 = original.y;
  let x2 = original.x + original.width;
  let y2 = original.y + original.height;
  if (regionInteraction.handle.includes('w')) x1 = Math.max(0, Math.min(x2 - minimum, point.x));
  if (regionInteraction.handle.includes('e')) x2 = Math.min(1, Math.max(x1 + minimum, point.x));
  if (regionInteraction.handle.includes('n')) y1 = Math.max(0, Math.min(y2 - minimum, point.y));
  if (regionInteraction.handle.includes('s')) y2 = Math.min(1, Math.max(y1 + minimum, point.y));
  region.rect = {x: x1, y: y1, width: x2 - x1, height: y2 - y1};
}

canvas.addEventListener('pointerdown', (event) => {
  const point = canvasPoint(event);
  if (drawMode === 'perspective') {
    perspectivePoints.push(point);
    if (perspectivePoints.length === 4) {
      if (config.regions.length && !window.confirm('Perspective correction changes the coordinate space. Clear the existing OCR regions and redraw them on the corrected preview?')) {
        perspectivePoints = [];
        drawMode = null;
        toast('Perspective change cancelled');
        render();
        return;
      }
      config.perspective = [...perspectivePoints];
      config.regions = [];
      selectedId = null;
      perspectivePoints = [];
      drawMode = null;
      pruneAcceptedPreviews();
      updateRegionEditor();
      toast('Perspective set. Save, then redraw regions on the corrected preview');
    }
    render(); return;
  }
  if (drawMode) {
    dragStart = point;
    dragCurrent = point;
    canvas.setPointerCapture(event.pointerId);
    return;
  }
  const selected = selectedRegion();
  const handle = resizeHandleAt(point, selected);
  const region = handle ? selected : regionUnderPoint(point);
  selectedId = region?.id || null;
  if (region) {
    regionInteraction = {
      id: region.id,
      type: handle ? 'resize' : 'move',
      handle,
      start: point,
      original: {...region.rect},
    };
    canvas.setPointerCapture(event.pointerId);
  }
  updateRegionEditor(); render();
});

canvas.addEventListener('pointermove', (event) => {
  const point = canvasPoint(event);
  if (regionInteraction) {
    updateRegionInteraction(point);
    render();
    return;
  }
  if (dragStart) {
    dragCurrent = point;
    render();
    return;
  }
  const handle = resizeHandleAt(point, selectedRegion());
  if (handle === 'nw' || handle === 'se') canvas.style.cursor = 'nwse-resize';
  else if (handle === 'ne' || handle === 'sw') canvas.style.cursor = 'nesw-resize';
  else canvas.style.cursor = regionUnderPoint(point) ? 'move' : 'crosshair';
});

canvas.addEventListener('pointerup', (event) => {
  if (regionInteraction) {
    regionInteraction = null;
    updateRegionEditor();
    render();
    return;
  }
  if (!dragStart) return;
  const end = canvasPoint(event);
  const rect = {x: Math.min(dragStart.x, end.x), y: Math.min(dragStart.y, end.y), width: Math.abs(end.x - dragStart.x), height: Math.abs(end.y - dragStart.y)};
  const mode = drawMode;
  dragStart = null; dragCurrent = null; drawMode = null;
  if (rect.width < .005 || rect.height < .005) return;
  if (mode === 'crop') {
    if (config.regions.length && !window.confirm('Cropping changes the coordinate space. Clear the existing OCR regions and redraw them on the cropped preview?')) {
      render();
      toast('Crop change cancelled');
      return;
    }
    config.crop = rect;
    config.regions = [];
    selectedId = null;
    pruneAcceptedPreviews();
    updateRegionEditor();
    render();
    toast('Crop set. Save, then redraw regions on the cropped preview');
    return;
  }
  selectedId = crypto.randomUUID();
  config.regions.push({id: selectedId, name: `Region ${config.regions.length + 1}`, rect, enabled: true, field_type: 'time', format_regex: '^.*$', confidence_threshold: .5, confirmation_frames: 2, smoothing_window: 1, remove_leading_zeros: false, preprocess: {threshold_method: 'otsu', invert: false, dilate_iterations: 0, vertical_scale: 1, autocrop: false, skip_similar: true, similarity_threshold: .02}});
  updateRegionEditor(); render();
});

canvas.addEventListener('pointercancel', () => {
  regionInteraction = null;
  dragStart = null;
  dragCurrent = null;
  render();
});

byId('add-region').onclick = () => { drawMode = 'region'; toast('Drag over the new OCR region'); };
byId('set-crop').onclick = () => {
  if (config.crop) {
    toast('Reset the current transform and save before selecting a new crop', true);
    return;
  }
  drawMode = 'crop';
  toast('Drag over the retained frame area');
};
byId('set-perspective').onclick = () => {
  if (config.perspective || config.crop) {
    toast('Reset the current transform and save before selecting new source corners', true);
    return;
  }
  drawMode = 'perspective';
  perspectivePoints = [];
  toast('Select corners: top-left, top-right, bottom-right, bottom-left');
};
byId('reset-transform').onclick = () => {
  if ((config.crop || config.perspective) && config.regions.length && !window.confirm('Resetting the transform changes the coordinate space. Clear the OCR regions and redraw them on the source preview?')) return;
  config.crop = null;
  config.perspective = null;
  config.regions = [];
  selectedId = null;
  perspectivePoints = [];
  drawMode = null;
  pruneAcceptedPreviews();
  updateRegionEditor();
  render();
  toast('Transform reset. Save, then redraw regions on the source preview');
};
byId('clear-selection').onclick = () => { selectedId = null; updateRegionEditor(); render(); };
byId('delete-region').onclick = () => { if (!selectedId) return; config.regions = config.regions.filter((region) => region.id !== selectedId); selectedId = null; updateRegionEditor(); render(); };
byId('region-fields').addEventListener('input', () => { collectRegion(); render(); });

byId('save-config').onclick = async () => {
  try { config = await api('/api/v1/config', {method: 'PUT', body: collectConfig()}); bindConfig(); await refreshOutputStatus(); toast('Configuration saved'); }
  catch (error) { toast(error.message, true); }
};

byId('refresh-sources').onclick = async () => {
  try {
    const result = await api('/api/v1/sources');
    byId('source-errors').textContent = result.errors.join(' ');
    if (result.devices.length) toast(`Found ${result.devices.length} source device(s)`); else toast('No capture devices found', true);
  } catch (error) { toast(error.message, true); }
};

async function refreshProfiles() {
  const names = await api('/api/v1/profiles');
  byId('profiles').replaceChildren(...names.map((name) => {
    const item = document.createElement('li'); item.textContent = name;
    const remove = document.createElement('button'); remove.textContent = 'Delete';
    const activate = document.createElement('button'); activate.textContent = 'Activate';
    activate.onclick = async () => { config = await api(`/api/v1/profiles/${encodeURIComponent(name)}/activate`, {method: 'POST'}); bindConfig(); toast('Profile activated'); };
    remove.onclick = async () => { await api(`/api/v1/profiles/${encodeURIComponent(name)}`, {method: 'DELETE'}); refreshProfiles(); };
    const actions = document.createElement('span'); actions.append(activate, remove); item.append(actions); return item;
  }));
}
byId('refresh-profiles').onclick = () => refreshProfiles().catch((error) => toast(error.message, true));
byId('save-profile').onclick = async () => { try { const name = byId('profile-name').value; await api(`/api/v1/profiles/${encodeURIComponent(name)}`, {method: 'PUT'}); await refreshProfiles(); toast('Profile saved'); } catch (error) { toast(error.message, true); } };

async function refreshOutputStatus() {
  const statuses = await api('/api/v1/outputs');
  byId('output-status').replaceChildren(...Object.entries(statuses).map(([adapterId, status]) => {
    const item = document.createElement('li');
    const configured = config.outputs.find((output) => output.id === adapterId);
    const label = document.createElement('span');
    label.textContent = `${configured?.kind || adapterId}: ${status.state}`;
    const detail = document.createElement('span');
    const ack = status.details || {};
    const ignored = ack.ignored_fields?.length ? ` · ignored ${ack.ignored_fields.join(', ')}` : '';
    const conflicts = ack.conflict_fields?.length ? ` · conflicts ${ack.conflict_fields.join(', ')}` : '';
    detail.textContent = `${ack.live_data_mode || status.message || `sent ${status.sent}`}${ignored}${conflicts}`;
    item.append(label, detail);
    return item;
  }));
}

function connectResults() {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const socket = new WebSocket(`${scheme}://${location.host}/api/v1/events`);
  socket.onopen = () => { byId('connection-dot').classList.add('online'); byId('connection-label').textContent = 'Live'; };
  socket.onclose = () => { byId('connection-dot').classList.remove('online'); byId('connection-label').textContent = 'Reconnecting'; setTimeout(connectResults, 1000); };
  socket.onmessage = ({data}) => {
    const event = JSON.parse(data);
    latestResultSequence = event.sequence || 0;
    latestResultFields = event.fields || [];
    updateRegionRuntime();
    render();
    Promise.all(
      latestResultFields
        .filter((field) => field.state === 'ok')
        .map(captureAcceptedPreview),
    ).finally(() => renderResults(latestResultFields));
  };
}

function connectPreview() {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const socket = new WebSocket(`${scheme}://${location.host}/api/v1/preview`);
  socket.binaryType = 'blob';
  socket.onmessage = async ({data}) => {
    if (typeof data === 'string') {
      try {
        const metadata = JSON.parse(data);
        if (metadata.type === 'preview.meta') updatePreviewGeometry(metadata.width, metadata.height);
      } catch {}
      return;
    }
    const nextPreviewBitmap = await createImageBitmap(data);
    updatePreviewGeometry(nextPreviewBitmap.width, nextPreviewBitmap.height);
    const previousPreviewBitmap = previewBitmap;
    previewBitmap = nextPreviewBitmap;
    previousPreviewBitmap?.close();
    byId('preview-empty').hidden = true;
    render();
    const missingAccepted = latestResultFields.filter(
      (field) =>
        !acceptedPreviews.has(field.id)
        && (field.state === 'ok' || field.state === 'unchanged'),
    );
    if (missingAccepted.length) {
      await Promise.all(missingAccepted.map(captureAcceptedPreview));
      renderResults(latestResultFields);
    }
  };
  socket.onclose = () => setTimeout(connectPreview, 1500);
}

function updatePreviewGeometry(width, height) {
  if (!width || !height || (canvas.width === width && canvas.height === height)) return;
  canvas.width = width;
  canvas.height = height;
  byId('preview-shell').style.aspectRatio = `${width} / ${height}`;
}

byId('filtered-preview').onload = () => {
  byId('filtered-preview').hidden = false;
  byId('filtered-preview-empty').hidden = true;
};
byId('filtered-preview').onerror = () => {
  byId('filtered-preview').hidden = true;
  byId('filtered-preview-empty').hidden = false;
};

bindConfig();
refreshProfiles().catch(() => {});
refreshOutputStatus().catch(() => {});
setInterval(() => refreshOutputStatus().catch(() => {}), 2000);
connectResults();
connectPreview();
