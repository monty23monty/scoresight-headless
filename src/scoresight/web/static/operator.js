const csrf = document.querySelector('meta[name="scoresight-csrf"]').content;
let config = JSON.parse(document.querySelector('#initial-config').textContent);
let selectedId = null;
let drawMode = null;
let dragStart = null;
let perspectivePoints = [];
let previewBitmap = null;

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

function updateRegionEditor() {
  const region = selectedRegion();
  byId('region-empty').hidden = Boolean(region);
  byId('region-fields').disabled = !region;
  if (!region) return;
  byId('region-name').value = region.name;
  byId('region-regex').value = region.format_regex;
  byId('region-confidence').value = region.confidence_threshold;
  byId('region-smoothing').value = region.smoothing_window;
  byId('region-threshold').value = region.preprocess.threshold_method;
  byId('region-invert').checked = region.preprocess.invert;
  byId('region-leading').checked = region.remove_leading_zeros;
}

function collectRegion() {
  const region = selectedRegion();
  if (!region) return;
  region.name = byId('region-name').value;
  region.format_regex = byId('region-regex').value;
  region.confidence_threshold = Number(byId('region-confidence').value);
  region.smoothing_window = Number(byId('region-smoothing').value);
  region.preprocess.threshold_method = byId('region-threshold').value;
  region.preprocess.invert = byId('region-invert').checked;
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
  const corners = perspectivePoints.length ? perspectivePoints : (config.perspective || []);
  if (corners.length) {
    context.strokeStyle = '#ff6bba'; context.fillStyle = '#ff6bba'; context.lineWidth = 2;
    context.beginPath();
    corners.forEach((point, index) => { const x = point.x * canvas.width, y = point.y * canvas.height; if (index) context.lineTo(x, y); else context.moveTo(x, y); context.fillRect(x - 5, y - 5, 10, 10); });
    if (corners.length === 4) context.closePath(); context.stroke();
  }
  for (const region of config.regions) {
    const {x, y, width, height} = region.rect;
    context.strokeStyle = region.id === selectedId ? '#55d9d0' : '#f4c56a';
    context.lineWidth = region.id === selectedId ? 3 : 2;
    context.strokeRect(x * canvas.width, y * canvas.height, width * canvas.width, height * canvas.height);
    context.fillStyle = '#071014cc';
    context.fillRect(x * canvas.width, y * canvas.height - 23, Math.max(80, region.name.length * 8), 22);
    context.fillStyle = context.strokeStyle;
    context.font = '14px system-ui';
    context.fillText(region.name, x * canvas.width + 5, y * canvas.height - 7);
  }
}

function canvasPoint(event) {
  const bounds = canvas.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
    y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)),
  };
}

canvas.addEventListener('pointerdown', (event) => {
  const point = canvasPoint(event);
  if (drawMode === 'perspective') {
    perspectivePoints.push(point);
    if (perspectivePoints.length === 4) { config.perspective = [...perspectivePoints]; perspectivePoints = []; drawMode = null; toast('Perspective corners set'); }
    render(); return;
  }
  if (drawMode) {
    dragStart = point;
    canvas.setPointerCapture(event.pointerId);
    return;
  }
  selectedId = [...config.regions].reverse().find((region) => point.x >= region.rect.x && point.x <= region.rect.x + region.rect.width && point.y >= region.rect.y && point.y <= region.rect.y + region.rect.height)?.id || null;
  updateRegionEditor(); render();
});

canvas.addEventListener('pointerup', (event) => {
  if (!dragStart) return;
  const end = canvasPoint(event);
  const rect = {x: Math.min(dragStart.x, end.x), y: Math.min(dragStart.y, end.y), width: Math.abs(end.x - dragStart.x), height: Math.abs(end.y - dragStart.y)};
  const mode = drawMode;
  dragStart = null; drawMode = null;
  if (rect.width < .005 || rect.height < .005) return;
  if (mode === 'crop') { config.crop = rect; render(); toast('Crop set'); return; }
  selectedId = crypto.randomUUID();
  config.regions.push({id: selectedId, name: `Region ${config.regions.length + 1}`, rect, enabled: true, format_regex: '^.*$', confidence_threshold: .5, smoothing_window: 1, remove_leading_zeros: false, preprocess: {threshold_method: 'otsu', invert: false, dilate_iterations: 0, vertical_scale: 1, autocrop: false, skip_similar: true, similarity_threshold: .02}});
  updateRegionEditor(); render();
});

byId('add-region').onclick = () => { drawMode = 'region'; toast('Drag over the new OCR region'); };
byId('set-crop').onclick = () => { drawMode = 'crop'; toast('Drag over the retained frame area'); };
byId('set-perspective').onclick = () => { drawMode = 'perspective'; perspectivePoints = []; toast('Select corners: top-left, top-right, bottom-right, bottom-left'); };
byId('reset-transform').onclick = () => { config.crop = null; config.perspective = null; perspectivePoints = []; drawMode = null; render(); };
byId('clear-selection').onclick = () => { selectedId = null; updateRegionEditor(); render(); };
byId('delete-region').onclick = () => { if (!selectedId) return; config.regions = config.regions.filter((region) => region.id !== selectedId); selectedId = null; updateRegionEditor(); render(); };
byId('region-fields').addEventListener('input', () => { collectRegion(); render(); });

byId('save-config').onclick = async () => {
  try { config = await api('/api/v1/config', {method: 'PUT', body: collectConfig()}); bindConfig(); toast('Configuration saved'); }
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

function connectResults() {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const socket = new WebSocket(`${scheme}://${location.host}/api/v1/events`);
  socket.onopen = () => { byId('connection-dot').classList.add('online'); byId('connection-label').textContent = 'Live'; };
  socket.onclose = () => { byId('connection-dot').classList.remove('online'); byId('connection-label').textContent = 'Reconnecting'; setTimeout(connectResults, 1000); };
  socket.onmessage = ({data}) => {
    const event = JSON.parse(data);
    byId('results').replaceChildren(...(event.fields || []).map((field) => {
      const row = document.createElement('tr');
      for (const value of [field.name, field.value, field.state, field.confidence == null ? '—' : field.confidence.toFixed(2)]) { const cell = document.createElement('td'); cell.textContent = value; row.append(cell); }
      return row;
    }));
  };
}

function connectPreview() {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const socket = new WebSocket(`${scheme}://${location.host}/api/v1/preview`);
  socket.binaryType = 'blob';
  socket.onmessage = async ({data}) => {
    if (typeof data === 'string') return;
    if (previewBitmap) previewBitmap.close();
    previewBitmap = await createImageBitmap(data);
    byId('preview-empty').hidden = true;
    render();
  };
  socket.onclose = () => setTimeout(connectPreview, 1500);
}

bindConfig(); refreshProfiles().catch(() => {}); connectResults(); connectPreview();
