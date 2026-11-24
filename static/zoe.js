const ui = id => document.getElementById(id);
const desk = {
  config: null, draft: null, active: null, record: null, crop: null,
  drawing: null, playback: null, busy: false,
  stencilMarks: [], stencilMarking: false, stencil: null, stencilCompat: null,
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

function zoeDraftUrl(suffix) {
  return '/zoe' + draftPath() + suffix;
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
  const image = ui('stageImage');
  image.removeAttribute('data-cell');
  image.src = src;
  image.alt = alt;
  image.hidden = false;
  ui('stageName').textContent = note;
  requestAnimationFrame(positionMarkLayer);
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
  ui('stageImage').dataset.cell = '1';
  requestAnimationFrame(positionMarkLayer);
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
      if (typeof loadAdoptedMarks === 'function') loadAdoptedMarks();
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
  refreshStencilState();
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
on('runProof', 'click', runProofClick);
on('proofHistory', 'change', async () => {
  const id = ui('proofHistory').value;
  if (id) setProof(await ask(draftPath() + '/proofs/' + id));
  else {
    clearProof();
    frameStage();
  }
});

// ---------------------------------------------------------------------------
// Paper stencil: thin-line marks, tie planning, adoption and refit checks.
// ---------------------------------------------------------------------------

function cellDimensionsMm() {
  const count = Math.max(1, desk.draft.frames.length || 1);
  return {
    w: Number(desk.draft.settings.circumference) / count,
    h: Number(desk.draft.settings.height_mm),
  };
}

function marksToMm() {
  const cell = cellDimensionsMm();
  return desk.stencilMarks.map(mark => [
    Number((mark.u * cell.w).toFixed(2)),
    Number((mark.v * cell.h).toFixed(2)),
  ]);
}

function imageContentRect(img) {
  const box = img.getBoundingClientRect();
  const naturalW = img.naturalWidth || Number(img.width) || box.width;
  const naturalH = img.naturalHeight || Number(img.height) || box.height;
  const scale = Math.min(box.width / naturalW, box.height / naturalH);
  const width = naturalW * scale;
  const height = naturalH * scale;
  return {
    left: box.left + (box.width - width) / 2,
    top: box.top + (box.height - height) / 2,
    width, height,
  };
}

function positionMarkLayer() {
  const layer = ui('markLayer');
  const img = ui('stageImage');
  if (!desk.draft || img.hidden || !img.naturalWidth) {
    layer.style.width = '0';
    layer.style.height = '0';
    return;
  }
  const content = imageContentRect(img);
  const stageRect = ui('stageBox').getBoundingClientRect();
  layer.style.left = (content.left - stageRect.left - ui('stageBox').clientLeft) + 'px';
  layer.style.top = (content.top - stageRect.top - ui('stageBox').clientTop) + 'px';
  layer.style.width = content.width + 'px';
  layer.style.height = content.height + 'px';
}

function renderMarks() {
  const layer = ui('markLayer');
  layer.replaceChildren();
  layer.style.display = desk.stencilMarking ? 'block' : 'none';
  positionMarkLayer();
  if (desk.stencilMarking) {
    desk.stencilMarks.forEach((mark, index) => {
      const dot = document.createElement('button');
      dot.type = 'button';
      dot.className = 'mark-dot';
      dot.textContent = String(index + 1);
      dot.style.left = (mark.u * 100) + '%';
      dot.style.top = (mark.v * 100) + '%';
      dot.title = 'Protected line ' + (index + 1) + ' - click to remove';
      dot.setAttribute('aria-label', 'Remove protected line mark ' + (index + 1));
      dot.addEventListener('click', event => {
        event.stopPropagation();
        desk.stencilMarks.splice(index, 1);
        renderMarks();
      });
      layer.appendChild(dot);
    });
  }

  const chips = ui('stencilMarks');
  chips.replaceChildren(...desk.stencilMarks.map((mark, index) => {
    const chip = document.createElement('span');
    chip.className = 'mark-chip';
    const cell = cellDimensionsMm();
    chip.textContent = `L${index + 1} · ${(mark.u * cell.w).toFixed(1)},${(mark.v * cell.h).toFixed(1)} mm`;
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = '×';
    remove.setAttribute('aria-label', 'Remove mark ' + (index + 1));
    remove.addEventListener('click', () => {
      desk.stencilMarks.splice(index, 1);
      renderMarks();
    });
    chip.append(remove);
    return chip;
  }));
}

function setMarking(enabled) {
  desk.stencilMarking = enabled;
  ui('stencilMark').setAttribute('aria-pressed', String(enabled));
  ui('stencilMark').textContent = enabled
    ? 'Marking… click the thin lines · 点按细线中'
    : 'Mark thin lines to protect · 点选要保住的细线';
  ui('stencilMarkHint').hidden = !enabled;
  ui('stageBox').classList.toggle('marking', enabled);
  if (enabled) {
    stopPlayback();
    frameStage();
  }
  requestAnimationFrame(() => { positionMarkLayer(); renderMarks(); });
}

function onStageImageClick(event) {
  if (!desk.stencilMarking) return;
  const img = ui('stageImage');
  if (img.hidden || img.dataset.cell !== '1') {
    frameStage();
    notice('Showing the selected frame - click on its thin lines to protect.');
    return;
  }
  const rect = imageContentRect(img);
  const u = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
  const v = Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height));
  desk.stencilMarks.push({ u, v });
  renderMarks();
}

function stencilRequestBody(extra = {}) {
  return {
    revision: desk.draft.revision,
    frame: Math.max(0, currentFrame()),
    bridge_mm: parseFloat(ui('stencilBridge').value) || 2,
    marks: marksToMm(),
    ...extra,
  };
}

function renderStencilStats(edition, adopted) {
  if (!edition) {
    ui('stencilStats').textContent = '';
    return;
  }
  const ties = edition.bridge_count ?? (edition.bridges || []).length;
  const islands = edition.island_count ?? (edition.islands || []).length;
  const width = edition.actual_width_mm || edition.bridge_mm;
  ui('stencilStats').textContent =
    (adopted ? '已采用 Adopted · ' : '预览 Preview · ') +
    `${islands} enclosed paper island(s), ${ties} tie(s) at ≥ ${width} mm; ` +
    `${edition.blocked_ink_mm2 ?? 0} mm² original ink covered by ties` +
    (edition.all_connected ? '; every island has a wide path out.' : '; connectivity check did not pass.');
}

function compatClass(compat) {
  if (!compat) return '';
  if (compat.matches) return 'ok';
  if (compat.usable) return 'warn';
  return 'bad';
}

function renderCompat(compat) {
  desk.stencilCompat = compat;
  const box = ui('stencilCompat');
  if (!compat) {
    box.className = 'stencil-compat';
    box.textContent = '';
    return;
  }
  box.className = 'stencil-compat show ' + compatClass(compat);
  const verdict = {
    fits: '完全对位，可直接用',
    registered: '完全对位，可直接用',
    translate: '可平移后继续用',
    'mirror-reversible': '可翻面（镜像）使用',
    'flip-over': '翻面（镜像）后可用',
    'flip-over-translate': '翻面并平移后可用',
    resized: '缩放后不能沿用，需重新切',
    'scale-mismatch': '缩放后不能沿用，需重新切',
    'cell-resized': '格尺寸已变，需重新对位或重切',
    'wrong-frame': '这是另一格的纸模',
    incompatible: '不能沿用，需重新切',
  }[compat.verdict] || compat.verdict;
  box.textContent = `旧纸模对新画位：${verdict}. ${compat.message || ''}`;
}

function loadAdoptedMarks() {
  const record = desk.draft?.stencil;
  if (record && record.frame_id === desk.draft.frames[currentFrame()]?.id &&
      Array.isArray(record.marks) && record.marks.length) {
    const cell = cellDimensionsMm();
    desk.stencilMarks = record.marks.map(([x, y]) => ({ u: x / cell.w, v: y / cell.h }));
  } else {
    desk.stencilMarks = [];
  }
  renderMarks();
}

async function refreshStencilState() {
  desk.stencil = desk.draft?.stencil || null;
  if (desk.stencil?.bridge_mm) ui('stencilBridge').value = desk.stencil.bridge_mm;
  loadAdoptedMarks();
  if (desk.stencil) {
    renderStencilStats(desk.stencil, true);
    try {
      const compat = await ask(draftPath() + '/stencil/compatibility');
      renderCompat(compat);
    } catch {
      renderCompat(null);
    }
  } else {
    renderStencilStats(null, false);
    renderCompat(null);
  }
}

on('stencilMark', 'click', () => setMarking(!desk.stencilMarking));
on('clearMarks', 'click', async () => {
  desk.stencilMarks = [];
  renderMarks();
});
ui('stageImage').addEventListener('click', onStageImageClick);
ui('stageImage').addEventListener('load', () => requestAnimationFrame(positionMarkLayer));
window.addEventListener('resize', () => requestAnimationFrame(positionMarkLayer));

on('stencilPlan', 'click', async () => {
  stopPlayback();
  notice('Planning minimum-width ties…');
  const plan = await ask(draftPath() + '/stencil/plan', 'POST', stencilRequestBody());
  desk.stencilPlan = plan;
  renderStencilStats(plan, false);
  showImage(plan.preview_url + '?v=' + desk.draft.revision,
    'Planned stencil ties', 'Tie layout preview · adopt it to keep this edition');
  notice(`规划完成：${plan.island_count} 个封闭留白，${plan.bridge_count} 条 ≥ ${plan.actual_width_mm} mm 连筋。`);
});

on('stencilAdopt', 'click', async () => {
  const result = await ask(draftPath() + '/stencil/adopt', 'POST', stencilRequestBody());
  renderDraft(result.draft);
  desk.stencil = result.stencil;
  renderStencilStats(result.stencil, true);
  renderCompat(result.compatibility);
  const url = zoeDraftUrl('/stencil/sheet.png?r=' + result.draft.revision);
  showImage(url, 'Adopted stencil cut sheet',
    'Adopted · print via “Print with Cut guide” at 100% or download the sheet');
  notice('已采用这版纸模。现在可用 Cut guide 按毫米打印纸模与对位记号。');
});

function requireStencil() {
  if (!desk.draft?.stencil) {
    notice('先 Plan tie layout 再 Adopt stencil，然后查看切割图或刷色模拟。', true);
    return false;
  }
  return true;
}

on('stencilSheet', 'click', async () => {
  if (!requireStencil()) return;
  showImage(zoeDraftUrl('/stencil/sheet.png?r=' + desk.draft.revision),
    'Stencil cut sheet', 'Millimetre cut sheet with ties and paper alignment marks');
});

on('stencilBrush', 'click', async () => {
  if (!requireStencil()) return;
  showImage(zoeDraftUrl('/stencil/brush.png?r=' + desk.draft.revision),
    'Stencil brush proof', 'Brushing at the original ribbon cell, with bridge-vs-blank diagnostic');
});

on('stencilCutGuide', 'click', async () => {
  if (!requireStencil()) return;
  ui('mode').value = 'cut-guide';
  ui('modeNote').textContent = desk.config.modes['cut-guide'].note;
  await runProofClick();
});

async function runProofClick() {
  stopPlayback();
  notice('Rendering the proof…');
  const mode = ui('mode').value;
  const saved = await ask(draftPath() + '/proofs', 'POST', {
    mode, revision: desk.draft.revision, frame: Math.max(0, currentFrame()),
  });
  desk.draft = saved.draft;
  setProof(saved.proof);
  refreshStencilState();
  notice('Saved ' + saved.proof.title.toLowerCase() + '.');
}

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
