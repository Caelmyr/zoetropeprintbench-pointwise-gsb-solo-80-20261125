const ui = id => document.getElementById(id);
const desk = {
  config: null, draft: null, active: null, record: null, crop: null,
  drawing: null, playback: null, busy: false,
  calibrationOn: true, marking: false, markDrawing: null,
  markRegions: [], planRows: [],
};

async function ask(path, method = 'GET', value = null) {
  const options = { method };
  if (value instanceof FormData) {
    options.body = value;
  } else if (value !== null) {
    options.headers = { 'Content-Type': 'application/json' };
    options.body = JSON.stringify(value);
  }
  const response = await fetch('/zoe' + path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'This operation could not be completed.');
  return result;
}

function notice(message, problem = false) {
  ui('status').textContent = message;
  ui('status').classList.toggle('error', problem);
}

function on(id, event, action) {
  ui(id).addEventListener(event, async ev => {
    if (event === 'submit') ev.preventDefault();
    if (desk.busy) return;
    desk.busy = true;
    try {
      await action(ev);
    } catch (error) {
      notice(error.message, true);
    } finally {
      desk.busy = false;
    }
  });
}

function options(select, entries, value) {
  select.replaceChildren(...entries.map(([key, label]) => new Option(label, key)));
  if (entries.some(([key]) => key === value)) select.value = value;
}

function draftPath() {
  return '/drafts/' + desk.draft.id;
}

function framePath(id, mode) {
  let path = '/zoe' + draftPath() + '/frames/' + id + '/' + mode + '?revision=' + desk.draft.revision;
  if (mode === 'prepared' && !desk.calibrationOn) path += '&cal=0';
  return path;
}

function fields(form, value) {
  for (const [name, entry] of Object.entries(value)) {
    const input = form.elements.namedItem(name);
    if (!input) continue;
    if (input.type === 'checkbox') input.checked = entry;
    else input.value = entry;
  }
}

function formValues(form) {
  const values = {};
  for (const input of form.elements) {
    if (!input.name || ['file', 'submit', 'button'].includes(input.type)) continue;
    values[input.name] = input.type === 'checkbox' ? input.checked : input.value;
  }
  return values;
}

function label(key) {
  const base = key.replaceAll('_', ' ');
  return base.charAt(0).toUpperCase() + base.slice(1);
}

function shown(value) {
  if (value === null) return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  return String(value);
}

function showImage(src, alt, note) {
  ui('scanStage').hidden = true;
  ui('alignStage').hidden = true;
  ui('stageEmpty').hidden = true;
  ui('stageImage').src = src;
  ui('stageImage').alt = alt;
  ui('stageImage').hidden = false;
  ui('stageName').textContent = note;
}

function stopPlayback() {
  if (desk.playback !== null) clearInterval(desk.playback);
  desk.playback = null;
  ui('play').textContent = 'Play direct loop';
  ui('play').setAttribute('aria-pressed', 'false');
}

function currentFrame() {
  return desk.draft.frames.findIndex(item => item.id === desk.active);
}

function frameStage() {
  const frame = desk.draft.frames[currentFrame()];
  if (!frame) {
    ui('stageImage').hidden = true;
    ui('scanStage').hidden = true;
    ui('stageEmpty').hidden = false;
    ui('stageName').textContent = '';
    return;
  }
  const mode = desk.draft.frames.length > 1 ? 'prepared' : 'thumb';
  showImage(framePath(frame.id, mode), frame.title, frame.title + ' · current artwork');
}

function renderFrames() {
  const frames = desk.draft.frames;
  const frame = frames[currentFrame()] || null;
  ui('frames').replaceChildren(...frames.map((item, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'frame-tile';
    button.title = item.title;
    button.setAttribute('aria-label', 'Select frame ' + (index + 1) + ', ' + item.title);
    button.setAttribute('aria-pressed', String(item.id === desk.active));
    const icon = new Image();
    icon.src = framePath(item.id, 'thumb');
    icon.alt = '';
    const number = document.createElement('span');
    number.textContent = String(index + 1).padStart(2, '0');
    button.append(icon, number);
    button.addEventListener('click', () => {
      if (desk.busy) return;
      stopPlayback();
      desk.marking = false;
      syncMarkButton();
      desk.active = item.id;
      for (const other of ui('frames').children) other.setAttribute('aria-pressed', String(other === button));
      if (desk.markRegions.length && desk.draft.alignment?.reference_frame !== item.id) {
        desk.markRegions = [];
        renderMarkList();
      } else if (desk.draft.alignment?.reference_frame === item.id) {
        desk.markRegions = (desk.draft.alignment.regions || []).map(r => ({ box: [...r.box], label: r.label || '' }));
        renderMarkList();
      }
      renderAdjustment(item);
      frameStage();
    });
    return button;
  }));
  ui('frameCount').textContent = frames.length + (frames.length === 1 ? ' frame' : ' frames');
  ui('play').disabled = frames.length < 2;
  renderAdjustment(frame);
}

function renderAdjustment(frame) {
  ui('frameAdjust').hidden = !frame;
  ui('calBase').hidden = !frame;
  if (!frame) return;
  ui('frameTitle').textContent = frame.title;
  ui('downloadFrame').href = framePath(frame.id, 'original');
  fields(ui('frameAdjust'), frame.transform);
  ui('earlier').disabled = currentFrame() < 1;
  ui('later').disabled = currentFrame() >= desk.draft.frames.length - 1;
  renderCalBase(frame);
}

const CAL_STATUS_TEXT = {
  off: 'not calibrated', reference: 'reference', aligned: 'aligned',
  limited: 'at max offset', 'edge-limited': 'edge protected',
  unresolved: 'no reliable reference', manual: 'manually tuned',
};

function renderCalBase(frame) {
  const cal = frame.calibration || { shift_x: 0, shift_y: 0, status: 'off' };
  ui('calShiftX').value = cal.shift_x ?? 0;
  ui('calShiftY').value = cal.shift_y ?? 0;
  const badge = ui('calStatus');
  badge.textContent = CAL_STATUS_TEXT[cal.status] || cal.status || 'off';
  badge.dataset.status = cal.status || 'off';
}

function renderArchive() {
  const saved = [['', 'Open proof'], ...[...desk.draft.proofs].reverse().map(item => [item.id, item.title])];
  options(ui('proofHistory'), saved, desk.record?.id || '');
}

function renderDraft(draft, preserveFrame = true) {
  stopPlayback();
  desk.draft = draft;
  desk.marking = false;
  if (!preserveFrame || currentFrame() < 0) desk.active = draft.frames[0]?.id || null;
  ui('welcome').hidden = true;
  ui('work').hidden = false;
  ui('drumControls').hidden = false;
  ui('draftTitle').textContent = draft.title;
  ui('draftSub').textContent = (draft.synthetic ? 'Synthetic example · ' : '') +
    draft.settings.circumference + ' mm circumference · ' + draft.settings.height_mm + ' mm art height';
  ui('prepLink').href = '/prepare?drum=' + draft.id;
  fields(ui('settings'), draft.settings);
  ui('register').hidden = !draft.scan;
  desk.crop = draft.scan ? [...draft.scan.registration.crop] : null;
  if (draft.scan) fields(ui('register'), draft.scan.registration);
  ui('alignMax').value = draft.alignment?.max_shift_mm ?? draft.settings.align_max_shift_mm ?? 6;
  if (draft.alignment?.reference_frame === desk.active) {
    desk.markRegions = (draft.alignment.regions || []).map(region => ({
      box: [...region.box], label: region.label || '',
    }));
  } else {
    desk.markRegions = [];
  }
  desk.planRows = draft.alignment_plan?.frames ? draft.alignment_plan.frames.map(row => ({ ...row })) : [];
  renderMarkList();
  renderPlan(draft.alignment_plan || null);
  syncMarkButton();
  renderFrames();
  renderArchive();
  frameStage();
  history.replaceState({}, '', '/?drum=' + draft.id);
}

async function refreshLibrary() {
  const drafts = await ask('/drafts');
  options(ui('library'), [['', 'Open a drum'], ...drafts.map(item => [item.id, item.title])], desk.draft?.id || '');
}

function clearProof() {
  desk.record = null;
  ui('resultDetail').hidden = true;
}

async function openDraft(key) {
  clearProof();
  renderDraft(await ask('/drafts/' + key), false);
  await refreshLibrary();
}

async function openDemo() {
  clearProof();
  renderDraft(await ask('/demo', 'POST', {}), false);
  await refreshLibrary();
  notice('Opened the sample with eight synthetic frames and a backlit slot scan.');
}

function renderSummary(detail) {
  const proofLink = '/zoe' + draftPath() + '/proofs/' + detail.id;
  ui('proofTitle').textContent = detail.title;
  ui('resultDetail').hidden = false;
  ui('pngLink').href = proofLink + '/png';
  ui('csvLink').href = proofLink + '/csv';
  ui('zipLink').href = proofLink + '/zip';
  ui('csvLink').hidden = detail.rows.length === 0;
  ui('summary').replaceChildren(...Object.entries(detail.summary).flatMap(([key, value]) => {
    const name = document.createElement('dt');
    const reading = document.createElement('dd');
    name.textContent = label(key);
    reading.textContent = shown(value);
    return [name, reading];
  }));
  ui('rowBlock').hidden = detail.rows.length === 0;
  if (!detail.rows.length) return;
  const hiddenColumns = new Set(['unphotographed_fraction', 'missing_edge_fraction']);
  const columnKeys = Object.keys(detail.rows[0]).filter(key => !hiddenColumns.has(key));
  const header = document.createElement('tr');
  for (const key of columnKeys) {
    const th = document.createElement('th');
    th.scope = 'col';
    th.textContent = label(key);
    header.append(th);
  }
  ui('resultColumns').replaceChildren(header);
  ui('resultRows').replaceChildren(...detail.rows.map(row => {
    const tr = document.createElement('tr');
    for (const key of columnKeys) {
      const cell = document.createElement('td');
      cell.textContent = shown(row[key]);
      tr.append(cell);
    }
    return tr;
  }));
}

function setProof(detail) {
  stopPlayback();
  desk.record = detail;
  renderArchive();
  renderSummary(detail);
  const url = '/zoe' + draftPath() + '/proofs/' + detail.id + '/image';
  showImage(url, detail.title, detail.title + ' · ' + detail.size.join(' × ') + ' px' + (detail.dpi ? ' · ' + detail.dpi + ' DPI' : ''));
}

function setCrop(crop) {
  desk.crop = crop;
  const [x1, y1, x2, y2] = crop;
  Object.assign(ui('scanBox').style, {
    left: x1 * 100 + '%', top: y1 * 100 + '%',
    width: (x2 - x1) * 100 + '%', height: (y2 - y1) * 100 + '%',
  });
}

function scanStage() {
  if (!desk.draft.scan) return;
  stopPlayback();
  ui('stageEmpty').hidden = true;
  ui('stageImage').hidden = true;
  ui('markedScan').src = '/zoe' + draftPath() + '/scan/image?revision=' + desk.draft.revision;
  ui('scanStage').hidden = false;
  ui('stageName').textContent = 'Drag a rectangle along the slit row, then save the scan setup.';
  setCrop(desk.crop);
}

function scanPoint(event) {
  const box = ui('markedScan').getBoundingClientRect();
  return [Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)),
    Math.max(0, Math.min(1, (event.clientY - box.top) / box.height))];
}

ui('scanStage').addEventListener('pointerdown', event => {
  desk.drawing = scanPoint(event);
  ui('scanStage').setPointerCapture(event.pointerId);
});
function extendCrop(event) {
  if (!desk.drawing) return;
  const [startX, startY] = desk.drawing;
  const [endX, endY] = scanPoint(event);
  setCrop([Math.min(startX, endX), Math.min(startY, endY), Math.max(startX, endX), Math.max(startY, endY)]);
}
ui('scanStage').addEventListener('pointermove', extendCrop);
ui('scanStage').addEventListener('pointerup', event => {
  extendCrop(event);
  desk.drawing = null;
});
ui('scanStage').addEventListener('pointercancel', () => {
  desk.drawing = null;
  if (desk.draft.scan) setCrop([...desk.draft.scan.registration.crop]);
});

on('welcomeDemo', 'click', openDemo);
on('demo', 'click', openDemo);
on('library', 'change', async () => {
  if (ui('library').value) await openDraft(ui('library').value);
});
on('reload', 'click', async () => {
  await openDraft(desk.draft.id);
  notice('Reloaded the saved drum.');
});
on('newDraft', 'submit', async event => {
  const draft = await ask('/drafts', 'POST', formValues(event.target));
  event.target.reset();
  ui('newPanel').open = false;
  clearProof();
  renderDraft(draft, false);
  await refreshLibrary();
  notice('Created the drum. Import frames to start a picture ribbon.');
});
on('settings', 'submit', async event => {
  renderDraft(await ask(draftPath() + '/settings', 'PUT', {
    revision: desk.draft.revision, settings: formValues(event.target),
  }));
  notice('Saved the drum setup.');
});

for (const [form, path, message] of [
  ['importFrame', '/frames', 'Added the artwork frame.'],
  ['contact', '/contact', 'Extracted the contact-sheet frames.'],
  ['importScan', '/scan', 'Imported the backlit scan.'],
]) {
  on(form, 'submit', async event => {
    const data = new FormData(event.target);
    data.set('revision', String(desk.draft.revision));
    renderDraft(await ask(draftPath() + path, 'POST', data));
    event.target.reset();
    if (form === 'importScan') scanStage();
    notice(message);
  });
}
on('showScan', 'click', scanStage);
on('register', 'submit', async event => {
  renderDraft(await ask(draftPath() + '/scan', 'PUT', {
    revision: desk.draft.revision,
    registration: { ...formValues(event.target), crop: desk.crop },
  }));
  scanStage();
  notice('Saved the scan setup.');
});
on('frameAdjust', 'submit', async event => {
  renderDraft(await ask(draftPath() + '/frames/' + desk.active, 'PUT', {
    revision: desk.draft.revision, transform: formValues(event.target),
  }));
  notice('Saved the artwork placement.');
});
async function moveFrame(offset) {
  const order = desk.draft.frames.map(frame => frame.id);
  const location = currentFrame();
  [order[location], order[location + offset]] = [order[location + offset], order[location]];
  renderDraft(await ask(draftPath() + '/order', 'PUT', { revision: desk.draft.revision, frames: order }));
  notice('Updated the picture order.');
}
on('earlier', 'click', async () => moveFrame(-1));
on('later', 'click', async () => moveFrame(1));
on('remove', 'click', async () => {
  const frame = desk.draft.frames[currentFrame()];
  if (!window.confirm('Remove ' + frame.title + ' from the working drum?')) return;
  renderDraft(await ask(draftPath() + '/frames/' + desk.active, 'DELETE', { revision: desk.draft.revision }));
  notice('Removed the frame from this draft.');
});
on('play', 'click', () => {
  if (desk.playback !== null) {
    stopPlayback();
    frameStage();
    return;
  }
  const frames = [...desk.draft.frames];
  const speed = desk.draft.settings.rpm;
  const direction = desk.draft.settings.direction === 'clockwise' ? 1 : -1;
  let index = Math.max(0, currentFrame());
  const sources = frames.map(frame => {
    const image = new Image();
    image.src = framePath(frame.id, 'prepared');
    return image.src;
  });
  const step = () => {
    showImage(sources[index], frames[index].title, 'Direct loop · frame ' + (index + 1));
    index = (index + frames.length + direction) % frames.length;
  };
  step();
  desk.playback = setInterval(step, 60000 / (speed * frames.length));
  ui('play').textContent = 'Pause loop';
  ui('play').setAttribute('aria-pressed', 'true');
});
on('mode', 'change', () => {
  ui('modeNote').textContent = desk.config.modes[ui('mode').value].note;
});
on('runProof', 'click', async () => {
  stopPlayback();
  notice('Rendering the proof…');
  const mode = ui('mode').value;
  const saved = await ask(draftPath() + '/proofs', 'POST', {
    mode, revision: desk.draft.revision, frame: Math.max(0, currentFrame()),
  });
  desk.draft = saved.draft;
  setProof(saved.proof);
  notice('Saved ' + saved.proof.title.toLowerCase() + '.');
});
on('proofHistory', 'change', async () => {
  const id = ui('proofHistory').value;
  if (id) setProof(await ask(draftPath() + '/proofs/' + id));
  else {
    clearProof();
    frameStage();
  }
});

function clamp01(value) {
  return Math.max(0, Math.min(1, value));
}

function syncMarkButton() {
  const button = ui('markRefs');
  if (!button) return;
  button.textContent = desk.marking ? 'Finish marking boxes' : 'Mark reference scenery on selected frame';
  button.classList.toggle('accent', desk.marking);
  button.setAttribute('aria-pressed', String(desk.marking));
}

function renderMarkBoxes(rubber = null) {
  const layer = ui('markLayer');
  if (!layer) return;
  layer.replaceChildren();
  const boxes = rubber ? [...desk.markRegions, { box: rubber, drawing: true }] : desk.markRegions;
  boxes.forEach((region, index) => {
    const [x1, y1, x2, y2] = region.box;
    const box = document.createElement('div');
    box.className = 'mark-box' + (region.drawing ? ' drawing' : '');
    Object.assign(box.style, {
      left: (x1 * 100) + '%', top: (y1 * 100) + '%',
      width: Math.max(0, (x2 - x1) * 100) + '%', height: Math.max(0, (y2 - y1) * 100) + '%',
    });
    if (!region.drawing) {
      const tag = document.createElement('span');
      tag.className = 'mark-tag';
      tag.textContent = region.label || ('Mark ' + (index + 1));
      box.appendChild(tag);
    }
    layer.appendChild(box);
  });
}

function renderMarkList() {
  const list = ui('markList');
  if (!list) return;
  list.hidden = desk.markRegions.length === 0;
  list.replaceChildren(...desk.markRegions.map((region, index) => {
    const row = document.createElement('div');
    row.className = 'mark-row';
    const input = document.createElement('input');
    input.type = 'text';
    input.value = region.label || ('Mark ' + (index + 1));
    input.setAttribute('aria-label', 'Label for reference mark ' + (index + 1));
    input.addEventListener('input', () => {
      region.label = input.value;
      renderMarkBoxes();
    });
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'mark-delete';
    remove.textContent = '×';
    remove.title = 'Remove this mark';
    remove.setAttribute('aria-label', 'Remove mark ' + (index + 1));
    remove.addEventListener('click', event => {
      event.stopPropagation();
      desk.markRegions.splice(index, 1);
      renderMarkList();
    });
    row.append(input, remove);
    return row;
  }));
  renderMarkBoxes();
}

function markPoint(event) {
  const rect = ui('alignImage').getBoundingClientRect();
  return [
    clamp01((event.clientX - rect.left) / rect.width),
    clamp01((event.clientY - rect.top) / rect.height),
  ];
}

ui('alignStage').addEventListener('pointerdown', event => {
  if (!desk.marking) return;
  desk.markDrawing = markPoint(event);
  ui('alignStage').setPointerCapture(event.pointerId);
});
ui('alignStage').addEventListener('pointermove', event => {
  if (!desk.markDrawing) return;
  const [startX, startY] = desk.markDrawing;
  const [endX, endY] = markPoint(event);
  renderMarkBoxes([Math.min(startX, endX), Math.min(startY, endY),
    Math.max(startX, endX), Math.max(startY, endY)]);
});
function finishMarkDraw(event) {
  if (!desk.markDrawing) return;
  const [startX, startY] = desk.markDrawing;
  const end = event ? markPoint(event) : start;
  const box = [Math.min(startX, end[0]), Math.min(startY, end[1]),
    Math.max(startX, end[0]), Math.max(startY, end[1])];
  desk.markDrawing = null;
  if (box[2] - box[0] >= .012 && box[3] - box[1] >= .012) {
    desk.markRegions.push({ box, label: 'Mark ' + (desk.markRegions.length + 1) });
  }
  renderMarkList();
}
ui('alignStage').addEventListener('pointerup', finishMarkDraw);
ui('alignStage').addEventListener('pointercancel', () => {
  desk.markDrawing = null;
  renderMarkBoxes();
});

function enterMarking() {
  const frame = desk.draft?.frames[currentFrame()];
  if (!frame) {
    notice('Select a frame before marking scenery.', true);
    return;
  }
  stopPlayback();
  desk.marking = true;
  ui('stageImage').hidden = true;
  ui('scanStage').hidden = true;
  ui('stageEmpty').hidden = true;
  ui('alignStage').hidden = false;
  let source = framePath(frame.id, 'prepared');
  if (!/cal=0/.test(source)) source += '&cal=0';
  ui('alignImage').src = source;
  ui('stageName').textContent = 'Drag boxes around fixed scenery on ' + frame.title + ' — keep the moving bird out.';
  syncMarkButton();
  renderMarkBoxes();
}

const ALIGN_STATUS_TEXT = {
  reference: 'Reference', aligned: 'Aligned', limited: 'At max offset',
  'edge-limited': 'Edge protected', unresolved: 'No reliable reference', manual: 'Manual',
};
const ALIGN_REASON_TEXT = {
  weak_reference: 'marked features not recognised in this frame',
  ambiguous: 'marks disagree with each other',
  blank_reference: 'reference mark has no detail',
  max_shift: 'true shift exceeds the maximum offset',
  edge_protected: 'shift softened to protect edge artwork',
};

function edgeSummary(row) {
  if (row.status === 'unresolved') return ALIGN_REASON_TEXT[row.reason] || 'no trustworthy match';
  const coverage = row.coverage || {};
  const parts = [];
  if ((coverage.ink_cut_fraction || 0) > .004) parts.push('cut ' + (coverage.ink_cut_fraction * 100).toFixed(1) + '%');
  const gap = Math.max(coverage.uncovered_left_mm || 0, coverage.uncovered_right_mm || 0,
    coverage.uncovered_top_mm || 0, coverage.uncovered_bottom_mm || 0);
  if (gap > .05) parts.push('uncovered ' + gap.toFixed(1) + ' mm');
  if (row.status === 'edge-limited') parts.push('edge-capped');
  if (row.reason === 'max_shift') parts.push('at max');
  return parts.join(' · ') || 'edge-safe';
}

function renderPlan() {
  const panel = ui('alignPlanPanel');
  const rows = desk.planRows;
  if (!rows.length) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const summary = desk.draft.alignment_plan?.summary;
  if (summary) {
    ui('alignPlanSummary').textContent =
      summary.reliable + ' reliable · ' + summary.unresolved + ' unresolved · ' + summary.total + ' frames';
  }
  const body = ui('alignPlanRows');
  body.replaceChildren(...rows.map((row) => {
    const tr = document.createElement('tr');
    tr.dataset.status = row.status;

    const useCell = document.createElement('td');
    const use = document.createElement('input');
    use.type = 'checkbox';
    use.checked = row.apply !== false;
    use.setAttribute('aria-label', 'Apply shift to frame ' + (row.index + 1));
    use.addEventListener('change', () => { row.apply = use.checked; });
    useCell.appendChild(use);

    const indexCell = document.createElement('td');
    indexCell.textContent = String(row.index + 1).padStart(2, '0');
    const titleCell = document.createElement('td');
    titleCell.textContent = row.title;

    const statusCell = document.createElement('td');
    const badge = document.createElement('span');
    badge.className = 'badge';
    badge.dataset.status = row.status;
    badge.textContent = ALIGN_STATUS_TEXT[row.status] || row.status;
    statusCell.appendChild(badge);

    const xCell = document.createElement('td');
    const xInput = document.createElement('input');
    xInput.type = 'number';
    xInput.step = '.01';
    xInput.className = 'plan-shift';
    xInput.value = row.shift_x_mm ?? 0;
    xInput.setAttribute('aria-label', 'X shift for ' + row.title);
    xInput.addEventListener('input', () => { row.shift_x_mm = parseFloat(xInput.value) || 0; });
    xCell.appendChild(xInput);

    const yCell = document.createElement('td');
    const yInput = document.createElement('input');
    yInput.type = 'number';
    yInput.step = '.01';
    yInput.className = 'plan-shift';
    yInput.value = row.shift_y_mm ?? 0;
    yInput.setAttribute('aria-label', 'Y shift for ' + row.title);
    yInput.addEventListener('input', () => { row.shift_y_mm = parseFloat(yInput.value) || 0; });
    yCell.appendChild(yInput);

    const confCell = document.createElement('td');
    confCell.textContent = row.confidence == null ? '—' : Number(row.confidence).toFixed(2);

    const edgeCell = document.createElement('td');
    edgeCell.className = 'edge-cell';
    edgeCell.textContent = edgeSummary(row);
    edgeCell.title = row.reason ? (ALIGN_REASON_TEXT[row.reason] || row.reason) : '';

    tr.append(useCell, indexCell, titleCell, statusCell, xCell, yCell, confCell, edgeCell);
    return tr;
  }));
}

function choicesFromPlan(useFull) {
  return desk.planRows
    .filter(row => row.apply !== false)
    .map(row => ({
      frame: row.frame,
      shift_x_mm: useFull ? (row.alignment_shift_x_mm ?? row.shift_x_mm ?? 0) : (row.shift_x_mm ?? 0),
      shift_y_mm: useFull ? (row.alignment_shift_y_mm ?? row.shift_y_mm ?? 0) : (row.shift_y_mm ?? 0),
      status: useFull && row.status === 'edge-limited' ? 'aligned' : (row.status || 'manual'),
      confidence: row.confidence ?? null,
      score: row.score ?? null,
      reason: useFull ? null : (row.reason ?? null),
      dx: row.dx ?? 0,
      dy: row.dy ?? 0,
    }));
}

on('demoAlign', 'click', openAlignmentDemo);
on('welcomeAlign', 'click', openAlignmentDemo);
async function openAlignmentDemo() {
  clearProof();
  const draft = await ask('/demo-alignment', 'POST', {});
  renderDraft(draft, false);
  await refreshLibrary();
  notice('Opened the moon jitter sample. The scenery marks are ready — choose Suggest translation set.');
}

on('markRefs', 'click', async () => {
  if (desk.marking) {
    desk.marking = false;
    syncMarkButton();
    frameStage();
  } else {
    enterMarking();
  }
});

on('suggestAlign', 'click', async () => {
  const frame = desk.draft?.frames[currentFrame()];
  if (!frame) throw new Error('Select a frame first.');
  if (!desk.markRegions.length) {
    throw new Error('Drag at least one box around fixed scenery, such as the moon or ground line.');
  }
  const maxShift = parseFloat(ui('alignMax').value);
  if (!Number.isFinite(maxShift) || maxShift < .25) throw new Error('Enter the maximum allowed offset in mm.');
  notice('Matching the marked scenery across every frame…');
  const draft = await ask(draftPath() + '/alignment/suggest', 'POST', {
    revision: desk.draft.revision,
    reference_frame: frame.id,
    space: 'cell',
    max_shift_mm: maxShift,
    regions: desk.markRegions.map(region => ({ box: region.box, label: region.label || null })),
  });
  renderDraft(draft);
  const summary = draft.alignment_plan.summary;
  notice('Suggested shifts: ' + summary.reliable + ' reliable, ' + summary.unresolved +
    ' without a trustworthy reference. Review the table, then apply.');
  ui('alignPlanPanel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
});

on('applyAlign', 'click', async () => {
  const frames = choicesFromPlan(false);
  if (!frames.length) throw new Error('Tick at least one frame to apply.');
  const draft = await ask(draftPath() + '/alignment/apply', 'POST', {
    revision: desk.draft.revision, frames,
  });
  renderDraft(draft);
  notice('Applied the calibrated base to ' + frames.length +
    ' frames. Offsets, mirror and scale are untouched — Play the direct loop, toggle Calibrated framing to compare, or save the Print clipping map.');
});

on('applyFullAlign', 'click', async () => {
  const frames = choicesFromPlan(true);
  if (!frames.length) throw new Error('Tick at least one reliable frame to apply.');
  const draft = await ask(draftPath() + '/alignment/apply', 'POST', {
    revision: desk.draft.revision, frames,
  });
  renderDraft(draft);
  notice('Applied full landmark shifts to ' + frames.length + ' frames (edge cap ignored). Check the clipping map.');
});

on('discardAlign', 'click', async () => {
  desk.planRows = [];
  ui('alignPlanPanel').hidden = true;
});

on('resetAlign', 'click', async () => {
  const draft = await ask(draftPath() + '/alignment/reset', 'POST', {
    revision: desk.draft.revision,
  });
  renderDraft(draft);
  notice('Cleared every calibrated base shift. Picture offsets, mirror and scale were kept.');
});

ui('calToggle').addEventListener('change', event => {
  desk.calibrationOn = event.target.checked;
  stopPlayback();
  renderFrames();
  frameStage();
});

on('saveCal', 'click', async () => {
  const frame = desk.draft.frames[currentFrame()];
  if (!frame) return;
  const shiftX = parseFloat(ui('calShiftX').value) || 0;
  const shiftY = parseFloat(ui('calShiftY').value) || 0;
  const draft = await ask(draftPath() + '/frames/' + desk.active + '/calibration', 'PUT', {
    revision: desk.draft.revision,
    calibration: { shift_x: shiftX, shift_y: shiftY, status: 'manual' },
  });
  renderDraft(draft);
  notice('Fine-tuned the calibration base.');
});

on('clearCal', 'click', async () => {
  const draft = await ask(draftPath() + '/frames/' + desk.active + '/calibration', 'PUT', {
    revision: desk.draft.revision,
    calibration: { shift_x: 0, shift_y: 0, status: 'off' },
  });
  renderDraft(draft);
  notice('Cleared this frame’s calibration base.');
});

(async () => {
  try {
    desk.config = await ask('/config');
    options(ui('mode'), Object.entries(desk.config.modes).map(([key, row]) => [key, row.title]), 'ribbon');
    ui('modeNote').textContent = desk.config.modes.ribbon.note;
    await refreshLibrary();
    const key = new URLSearchParams(location.search).get('drum');
    if (key) await openDraft(key);
  } catch (error) {
    notice(error.message, true);
  }
})();
