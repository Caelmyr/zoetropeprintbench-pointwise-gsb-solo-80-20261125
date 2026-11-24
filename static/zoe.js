const ui = id => document.getElementById(id);
const desk = {
  config: null, draft: null, active: null, record: null, crop: null,
  drawing: null, playback: null, busy: false, placement: null, savedPlacement: null,
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

function renderPlacement(report) {
  const panel = ui('placementPanel');
  desk.placement = report;
  if (!report) {
    panel.hidden = true;
    ui('placementState').textContent = desk.savedPlacement?.adopted
      ? 'Measured print positions are in use.'
      : 'Printing the nominal equal band.';
    ui('adoptPlacement').hidden = true;
    return;
  }
  panel.hidden = false;
  const feasible = report.feasible;
  ui('placementCount').textContent = report.cell_width_mm + ' mm cells';
  ui('placementLead').textContent = feasible
    ? 'Each cell center sits inside the projection of its measured opening, '
      + 'with picture order running from the join.'
    : 'No printable placement fits these measurements.';
  ui('placementBlocked').hidden = feasible;
  ui('placementBlocked').textContent = feasible ? '' : (report.blocked?.message || '');
  ui('placementTable').hidden = !feasible;
  ui('adoptPlacement').hidden = !feasible;
  ui('adoptPlacement').textContent = desk.savedPlacement?.adopted
    ? 'Replace saved measured positions' : 'Use measured positions';
  ui('revertPlacement').hidden = !desk.savedPlacement?.adopted;
  ui('placementCsv').hidden = !feasible;
  ui('placementCsv').textContent = 'Placement CSV';
  if (feasible) {
    const blob = placementCsv(report);
    ui('placementCsv').href = URL.createObjectURL(blob);
    ui('placementCsv').download = 'measured-placement.csv';
  }
  if (feasible) {
    ui('placementRows').replaceChildren(...report.cells.map(cell => {
      const tr = document.createElement('tr');
      for (const value of [
        String(cell.cell), String(cell.slot), cell.center_mm.toFixed(2),
        signed(cell.center_offset_mm) + ' mm', cell.gap_after_mm.toFixed(2) + ' mm',
      ]) {
        const td = document.createElement('td');
        td.textContent = value;
        tr.append(td);
      }
      return tr;
    }));
    ui('placementProjections').replaceChildren(...report.projections.map(row => {
      const tr = document.createElement('tr');
      for (const value of [
        String(row.slot), row.photo_center_mm.toFixed(2),
        row.projected_span_mm.map(v => v.toFixed(2)).join(' – '), row.width_mm.toFixed(2),
      ]) {
        const td = document.createElement('td');
        td.textContent = value;
        tr.append(td);
      }
      return tr;
    }));
    const summary = [
      ['Print placement', 'Measured slots'],
      ['Measured openings', report.measured_openings],
      ['Art quiet margin (mm)', report.quiet_margin_mm],
      ['Picture cells', report.cell_count],
      ['Cell width (mm)', report.cell_width_mm],
      ['Widest cell these openings allow (mm)', report.widest_cell_mm],
      ['Mean center offset (mm)', report.mean_abs_center_offset_mm],
      ['Largest center offset (mm)', report.maximum_center_offset_mm],
      ['Narrowest inner gap (mm)', report.internal_gap_min_mm],
      ['Widest inner gap (mm)', report.internal_gap_max_mm],
      ['Inner gap spread (mm)', report.internal_gap_range_mm],
      ['Gap before first cell (mm)', report.join_lead_gap_mm],
      ['Gap after last cell (mm)', report.gap_after_join_mm],
      ['Complete openings', report.complete_openings],
      ['Incomplete openings', report.incomplete_openings],
    ];
    ui('placementSummary').replaceChildren(...summary.flatMap(([name, value]) => {
      const dt = document.createElement('dt');
      dt.textContent = name;
      const dd = document.createElement('dd');
      dd.textContent = shown(value);
      return [dt, dd];
    }));
  } else {
    ui('placementTable').hidden = true;
    const detail = [
      ['Picture cells', report.frame_count],
      ['Complete openings', report.complete_openings],
      ['Incomplete openings', report.incomplete_openings],
      ['Widest cell these openings allow (mm)', report.widest_cell_mm],
      ['Entered cell width (mm)', report.cell_width_mm],
    ];
    ui('placementSummary').replaceChildren(...detail.flatMap(([name, value]) => {
      const dt = document.createElement('dt');
      dt.textContent = name;
      const dd = document.createElement('dd');
      dd.textContent = shown(value);
      return [dt, dd];
    }));
  }
  ui('placementState').textContent = feasible
    ? 'A printable measured placement is ready. Adopt it to print these positions.'
    : 'The measurements do not allow a printable placement.';
}

async function refreshPlacement() {
  if (!desk.draft) return;
  try {
    const saved = await ask(draftPath() + '/placement');
    desk.savedPlacement = saved;
    ui('revertPlacement').hidden = !saved.adopted;
    ui('placementCsv').hidden = !saved.adopted;
    ui('placementPanel').hidden = true;
    ui('placementState').textContent = saved.adopted
      ? (saved.stale
        ? 'A saved measured placement is out of date with this drum. Recommend it again.'
        : 'Measured print positions are in use: ' + saved.cell_width_mm + ' mm cells.')
      : 'Printing the nominal equal band.';
    if (saved.adopted) {
      ui('placementCsv').href = '/zoe' + draftPath() + '/placement.csv';
      ui('placementCsv').download = 'measured-placement.csv';
    }
  } catch (error) {
    notice(error.message, true);
  }
}

function signed(value) {
  if (value === null || value === undefined) return '—';
  const fixed = value.toFixed(2);
  return value > 0 ? '+' + fixed : fixed;
}

function placementCsv(report) {
  const lines = [[
    'cell', 'start_mm', 'end_mm', 'center_mm', 'assigned_slit', 'photo_slit_center_mm',
    'projected_center_mm', 'projected_span_mm', 'center_offset_mm', 'gap_after_mm',
  ].join(',')];
  for (const cell of report.cells) {
    lines.push([
      cell.cell, cell.start_mm, cell.end_mm, cell.center_mm, cell.slot, cell.photo_center_mm,
      cell.projected_center_mm, cell.projected_span_mm.map(value => value.toFixed(2)).join('–'),
      cell.center_offset_mm, cell.gap_after_mm,
    ].join(','));
  }
  return new Blob([lines.join('\n') + '\n'], { type: 'text/csv' });
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
  renderPlacement(null);
  refreshPlacement();
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
on('recommendPlacement', 'click', async () => {
  notice('Fitting the measured openings…');
  renderPlacement(await ask(draftPath() + '/placement/recommend', 'POST', {}));
  notice(desk.placement.feasible
    ? 'Fit ' + desk.placement.cell_count + ' cells to the measured openings.'
    : 'The measurements do not allow a printable placement.');
});
on('adoptPlacement', 'click', async () => {
  const report = desk.placement;
  if (!report || !report.feasible) return;
  renderDraft(await ask(draftPath() + '/placement', 'POST', {
    revision: desk.draft.revision, cell_width: report.cell_width_mm,
  }));
  renderPlacement(null);
  notice('Print positions now follow the measured openings. Print ribbon and Slit-view proof use them.');
});
on('revertPlacement', 'click', async () => {
  if (!window.confirm('Print the nominal equal band again instead of the measured positions?')) return;
  renderDraft(await ask(draftPath() + '/placement', 'DELETE', { revision: desk.draft.revision }));
  renderPlacement(null);
  notice('Reverted to the nominal equal band used to build a fresh drum.');
});
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
