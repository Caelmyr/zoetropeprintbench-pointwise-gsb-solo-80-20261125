const ui = id => document.getElementById(id);
const desk = {
  config: null, draft: null, active: null, record: null, crop: null,
  drawing: null, playback: null, busy: false,
  marking: false, pendingPoint: null, stageKind: null, checks: {},
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
  return '/zoe' + draftPath() + '/frames/' + id + '/' + mode + '?revision=' + desk.draft.revision;
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
  desk.pendingPoint = null;
  if (!frame) {
    ui('stageImage').hidden = true;
    ui('scanStage').hidden = true;
    ui('stageEmpty').hidden = false;
    ui('stageName').textContent = '';
    desk.stageKind = 'empty';
    syncMarkOverlay();
    return;
  }
  const mode = desk.draft.frames.length > 1 ? 'prepared' : 'thumb';
  desk.stageKind = 'art';
  showImage(framePath(frame.id, mode), frame.title, frame.title + ' · current artwork');
  requestAnimationFrame(syncMarkOverlay);
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
      desk.active = item.id;
      for (const other of ui('frames').children) other.setAttribute('aria-pressed', String(other === button));
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
  if (!frame) return;
  ui('frameTitle').textContent = frame.title;
  ui('downloadFrame').href = framePath(frame.id, 'original');
  fields(ui('frameAdjust'), frame.transform);
  ui('earlier').disabled = currentFrame() < 1;
  ui('later').disabled = currentFrame() >= desk.draft.frames.length - 1;
}

function renderArchive() {
  const saved = [['', 'Open proof'], ...[...desk.draft.proofs].reverse().map(item => [item.id, item.title])];
  options(ui('proofHistory'), saved, desk.record?.id || '');
}

function renderDraft(draft, preserveFrame = true) {
  stopPlayback();
  desk.draft = draft;
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
  renderFrames();
  renderArchive();
  frameStage();
  renderSeparation();
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
  const columnKeys = Object.keys(detail.rows[0]);
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
  desk.checks = {};
  (detail.checks || []).forEach(check => { desk.checks[check.id] = check; });
  renderArchive();
  renderSummary(detail);
  renderProofChecks(detail.checks || []);
  const url = '/zoe' + draftPath() + '/proofs/' + detail.id + '/image';
  desk.stageKind = 'proof';
  showImage(url, detail.title, detail.title + ' · ' + detail.size.join(' × ') + ' px' + (detail.dpi ? ' · ' + detail.dpi + ' DPI' : ''));
  requestAnimationFrame(syncMarkOverlay);
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
  desk.stageKind = 'scan';
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

// ---------------------------------------------------------------------------
// Ink separation panel, artwork marking and printed-edge inspection
// ---------------------------------------------------------------------------

function separation() {
  return desk.draft?.separation || null;
}

function toneValues() {
  const first = Number(ui('grayLevel1').value);
  const second = Number(ui('grayLevel2').value);
  return { tones: [Number.isFinite(first) ? first : 186, Number.isFinite(second) ? second : 82] };
}

async function commitSeparation(request) {
  const saved = await request;
  desk.draft = saved.draft;
  renderSeparation();
  requestAnimationFrame(syncMarkOverlay);
  return saved.separation;
}

function levelLabel(index, sep) {
  const value = sep?.tones?.[index]?.value ?? (index === 0 ? 186 : 82);
  return 'Gray ' + (index + 1) + ' · ' + value;
}

function renderSwatches(sep) {
  const box = ui('swatchList');
  const swatches = sep?.swatches || [];
  box.hidden = swatches.length === 0;
  box.replaceChildren(...swatches.map(swatch => {
    const row = document.createElement('div');
    row.className = 'swatch-row';
    row.dataset.swatchId = swatch.id;
    const chip = document.createElement('span');
    chip.className = 'swatch-chip';
    chip.style.background = swatch.hex;
    chip.title = swatch.hex;
    const meta = document.createElement('span');
    meta.className = 'swatch-meta';
    const code = document.createElement('code');
    code.textContent = swatch.hex;
    const sub = document.createElement('small');
    sub.textContent = (swatch.frames?.length || 0) + ' frames' + (swatch.dedicated ? ' · marked' : '');
    meta.append(code, sub);
    const select = document.createElement('select');
    select.setAttribute('aria-label', 'Ink level for ' + swatch.hex);
    select.dataset.swatchId = swatch.id;
    [0, 1].forEach(level => select.append(new Option(levelLabel(level, sep), String(level))));
    select.value = String(sep.assignment?.[swatch.id] ?? 0);
    row.append(chip, meta, select);
    return row;
  }));
}

function markStatus(mark) {
  const inspected = desk.checks[mark.id];
  if (inspected) return inspected.status;
  const preview = (separation()?.mark_preview || []).find(item => item.id === mark.id);
  if (preview && preview.separable === false) return 'would-merge';
  if (preview && preview.separable === true) return 'would-separate';
  return null;
}

function renderMarks(sep) {
  const list = ui('markList');
  const marks = sep?.marks || [];
  list.replaceChildren(...marks.map((mark, index) => {
    const item = document.createElement('li');
    item.className = 'mark-item';
    const status = markStatus(mark);
    if (status) item.dataset.status = status;
    const indexBadge = document.createElement('span');
    indexBadge.className = 'mark-index';
    indexBadge.textContent = String(index + 1);
    const colors = document.createElement('span');
    colors.className = 'mark-colors';
    (mark.colors || []).forEach(rgb => {
      const chip = document.createElement('i');
      chip.className = 'mini-chip';
      chip.style.background = Array.isArray(rgb) ? 'rgb(' + rgb.join(',') + ')' : rgb;
      colors.append(chip);
    });
    const text = document.createElement('span');
    text.className = 'mark-text';
    const frameNo = desk.draft.frames.findIndex(f => f.id === mark.frame) + 1;
    text.textContent = (mark.label || ('Pair ' + (index + 1))) + ' · frame ' + (frameNo || '?');
    const state = document.createElement('span');
    state.className = 'mark-state';
    if (status === 'separated') state.textContent = 'Separated · 分得开';
    else if (status === 'merged') state.textContent = 'Merged · 并掉了';
    else if (status === 'would-separate') state.textContent = 'suggested apart';
    else if (status === 'would-merge') state.textContent = 'suggested merged';
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'mark-remove quiet';
    remove.textContent = '✕';
    remove.title = 'Remove this pair';
    remove.dataset.removeMark = mark.id;
    item.append(indexBadge, colors, text, state, remove);
    return item;
  }));
}

function renderSeparation() {
  if (!desk.draft || !ui('separationPanel')) return;
  const sep = separation();
  if (!desk.tonesDirty) {
    ui('grayLevel1').value = sep?.tones?.[0]?.value ?? 186;
    ui('grayLevel2').value = sep?.tones?.[1]?.value ?? 82;
  }
  renderSwatches(sep);
  renderMarks(sep);
  const summary = sep?.summary;
  const stats = ui('separationStats');
  if (summary) {
    stats.hidden = false;
    stats.textContent = summary.swatches + ' primaries · cut edges ' +
      summary.cut_edges_kept + '/' + summary.cut_edges + ' kept · action lines ' +
      summary.line_edges_kept + '/' + summary.line_edges + ' kept · marked pairs ' +
      summary.marks_separable + '/' + summary.marks_total + ' separable';
  } else {
    stats.hidden = true;
    stats.textContent = '';
  }
  const state = ui('adoptState');
  if (sep?.adopted) {
    state.textContent = 'Adopted ✓ Print ribbon downloads the separated strip and Proof tone inspection reads its pixels.';
    state.dataset.adopted = 'true';
    ui('adoptSeparation').textContent = 'Update adopted separation · 更新采用';
  } else {
    state.textContent = 'Not adopted yet — Print ribbon stays full colour.';
    state.dataset.adopted = 'false';
    ui('adoptSeparation').textContent = 'Adopt · apply to Print ribbon · 采用分墨';
  }
  ui('clearMarks').disabled = !(sep?.marks?.length);
  requestAnimationFrame(syncMarkOverlay);
}

function renderProofChecks(checks) {
  const box = ui('proofChecks');
  const list = ui('checkList');
  if (!checks.length) {
    box.hidden = true;
    list.replaceChildren();
    return;
  }
  box.hidden = false;
  list.replaceChildren(...checks.map((check, index) => {
    const row = document.createElement('li');
    row.className = 'check-item';
    row.dataset.status = check.status;
    const badge = document.createElement('span');
    badge.className = 'check-status';
    badge.textContent = check.status === 'separated' ? '✓ Separated · 分得开' : '✕ Merged · 并掉了';
    const label = document.createElement('span');
    label.className = 'check-label';
    label.textContent = (index + 1) + '. ' + check.label + ' · frame ' + check.frame;
    const tones = document.createElement('span');
    tones.className = 'check-tones';
    [check.tone_a, check.tone_b].forEach(hex => {
      const chip = document.createElement('i');
      chip.className = 'mini-chip';
      chip.style.background = hex;
      chip.title = hex;
      tones.append(chip);
    });
    row.append(badge, label, tones);
    return row;
  }));
}

function imageContentRect() {
  const img = ui('stageImage');
  const rect = img.getBoundingClientRect();
  let width = rect.width;
  let height = rect.height;
  if (img.naturalWidth && img.naturalHeight && width && height) {
    const scale = Math.min(width / img.naturalWidth, height / img.naturalHeight);
    width = img.naturalWidth * scale;
    height = img.naturalHeight * scale;
  }
  return {
    left: rect.left + (rect.width - width) / 2,
    top: rect.top + (rect.height - height) / 2,
    width, height,
  };
}

function syncMarkOverlay() {
  const svg = ui('markOverlay');
  const stage = ui('stageBox');
  if (!svg || !stage) return;
  if (!desk.draft || desk.stageKind !== 'art' || ui('stageImage').hidden) {
    svg.hidden = true;
    svg.replaceChildren();
    return;
  }
  const rect = imageContentRect();
  const stageRect = stage.getBoundingClientRect();
  const left = rect.left - stageRect.left - stage.clientLeft + stage.scrollLeft;
  const top = rect.top - stageRect.top - stage.clientTop + stage.scrollTop;
  svg.hidden = false;
  svg.style.left = left + 'px';
  svg.style.top = top + 'px';
  svg.style.width = rect.width + 'px';
  svg.style.height = rect.height + 'px';
  svg.setAttribute('width', Math.max(1, Math.round(rect.width)));
  svg.setAttribute('height', Math.max(1, Math.round(rect.height)));
  drawMarkOverlay(rect.width, rect.height);
}

const SVG_NS = 'http://www.w3.org/2000/svg';

function svgNode(name, attrs) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function drawMarkOverlay(width, height) {
  const svg = ui('markOverlay');
  svg.replaceChildren();
  if (!desk.draft || width <= 0 || height <= 0) return;
  const frameId = desk.draft.frames[currentFrame()]?.id;
  const marks = (separation()?.marks || []).filter(mark => mark.frame === frameId);
  marks.forEach((mark, index) => {
    const points = (mark.points || []).map(([u, v]) => [u * width, v * height]);
    const status = markStatus(mark);
    const color = status === 'separated' ? '#1f9d63'
      : status === 'merged' ? '#d3411f'
      : status === 'would-merge' ? '#c9821b' : '#1769e0';
    if (points.length === 2) {
      svg.append(svgNode('line', {
        x1: points[0][0], y1: points[0][1], x2: points[1][0], y2: points[1][1],
        stroke: color, 'stroke-width': 2.5, 'stroke-dasharray': '6 4',
      }));
    }
    points.forEach(([x, y]) => {
      svg.append(svgNode('circle', { cx: x, cy: y, r: 7, fill: color, stroke: '#fff', 'stroke-width': 2.5 }));
    });
    if (points[0]) {
      const text = svgNode('text', {
        x: points[0][0] + 10, y: points[0][1] - 9, fill: color,
        'font-size': 15, 'font-weight': 800, stroke: '#fff', 'stroke-width': 3,
        'paint-order': 'stroke',
      });
      text.textContent = String(index + 1);
      svg.append(text);
    }
  });
  if (desk.pendingPoint) {
    const [x, y] = [desk.pendingPoint[0] * width, desk.pendingPoint[1] * height];
    svg.append(svgNode('circle', { cx: x, cy: y, r: 8, fill: '#1769e0', stroke: '#fff', 'stroke-width': 3 }));
  }
}

function artworkPoint(event) {
  const rect = imageContentRect();
  return [
    Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
    Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
  ];
}

on('sampleColors', 'click', async () => {
  await desk.toneChain;
  desk.tonesDirty = false;
  notice('Sampling recurring colours across every frame…');
  const sep = await commitSeparation(ask(draftPath() + '/separation/sample', 'POST', {
    revision: desk.draft.revision, ...toneValues(),
  }));
  notice('Sampled ' + sep.swatches.length + ' loop primaries and suggested ink levels.');
});

on('suggestSeparation', 'click', async () => {
  await desk.toneChain;
  desk.tonesDirty = false;
  const sep = await commitSeparation(ask(draftPath() + '/separation/suggest', 'POST', {
    revision: desk.draft.revision, ...toneValues(),
  }));
  notice('Suggested assignment: ' + sep.summary.marks_separable + '/' +
    sep.summary.marks_total + ' marked pairs kept apart.');
});

on('adoptSeparation', 'click', async () => {
  await desk.toneChain;
  desk.tonesDirty = false;
  const sep = await commitSeparation(ask(draftPath() + '/separation/adopt', 'POST', {
    revision: desk.draft.revision, ...toneValues(),
  }));
  ui('mode').value = 'ribbon';
  ui('modeNote').textContent = desk.config.modes.ribbon.note +
    ' The adopted two-level separation is applied to the downloaded ribbon.';
  notice('Adopted ' + sep.tones.map(t => t.value).join(' / ') +
    ' gray levels. Save the Print ribbon proof, then use PNG to download the separated strip.');
});

on('clearMarks', 'click', async () => {
  await commitSeparation(ask(draftPath() + '/separation', 'PUT', {
    revision: desk.draft.revision, marks: [],
  }));
  desk.checks = {};
  ui('proofChecks').hidden = true;
  notice('Cleared all marked pairs.');
});

on('markPair', 'click', async () => {
  desk.marking = !desk.marking;
  desk.pendingPoint = null;
  if (desk.marking) {
    stopPlayback();
    if (currentFrame() < 0) {
      desk.marking = false;
      notice('Open a frame before marking colours.', true);
      return;
    }
    frameStage();
    requestAnimationFrame(() => {
      ui('stageImage').scrollIntoView({ block: 'center', behavior: 'instant' });
      syncMarkOverlay();
    });
  }
  ui('markPair').setAttribute('aria-pressed', String(desk.marking));
  ui('markHint').hidden = !desk.marking;
  ui('stageImage').classList.toggle('marking-cursor', desk.marking);
  requestAnimationFrame(syncMarkOverlay);
});

ui('stageImage').addEventListener('click', async event => {
  if (!desk.marking || desk.stageKind !== 'art' || desk.busy) return;
  const point = artworkPoint(event);
  if (!desk.pendingPoint) {
    desk.pendingPoint = point;
    syncMarkOverlay();
    return;
  }
  const points = [desk.pendingPoint, point];
  desk.pendingPoint = null;
  desk.busy = true;
  try {
    const frame = desk.draft.frames[currentFrame()];
    await commitSeparation(ask(draftPath() + '/separation/marks', 'POST', {
      revision: desk.draft.revision, frame: frame.id, points,
    }));
    notice('Added adjacent colour pair. Click the next pair, or toggle Mark adjacent pair to finish.');
  } catch (error) {
    notice(error.message, true);
  } finally {
    desk.busy = false;
    syncMarkOverlay();
  }
});

desk.swatchChain = Promise.resolve();
ui('swatchList').addEventListener('change', event => {
  const select = event.target.closest('select[data-swatch-id]');
  if (!select || !desk.draft) return;
  const swatchId = select.dataset.swatchId;
  const level = Number(select.value);
  desk.swatchChain = desk.swatchChain.then(async () => {
    try {
      const saved = await ask(draftPath() + '/separation', 'PUT', {
        revision: desk.draft.revision, assignment: { [swatchId]: level },
      });
      desk.draft = saved.draft;
      renderSeparation();
    } catch (error) {
      notice(error.message, true);
    }
  });
});

desk.toneChain = Promise.resolve();
desk.tonesDirty = false;
function toneEdited() {
  desk.tonesDirty = true;
}
ui('grayLevel1').addEventListener('input', toneEdited);
ui('grayLevel2').addEventListener('input', toneEdited);
ui('grayLevel1').addEventListener('change', toneEdited);
ui('grayLevel2').addEventListener('change', toneEdited);

desk.removeChain = Promise.resolve();
ui('markList').addEventListener('click', event => {
  const button = event.target.closest('[data-remove-mark]');
  if (!button || !desk.draft) return;
  const markId = button.dataset.removeMark;
  desk.removeChain = desk.removeChain.then(async () => {
    try {
      const saved = await ask(draftPath() + '/separation/marks/' + markId,
        'DELETE', { revision: desk.draft.revision });
      desk.draft = saved.draft;
      renderSeparation();
    } catch (error) {
      notice(error.message, true);
    }
  });
});

window.addEventListener('resize', syncMarkOverlay);
ui('stageImage').addEventListener('load', () => requestAnimationFrame(syncMarkOverlay));
ui('stageBox').addEventListener('scroll', syncMarkOverlay);

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
