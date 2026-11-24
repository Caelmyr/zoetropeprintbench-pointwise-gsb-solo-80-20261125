const ui = id => document.getElementById(id);
const desk = {
  config: null, draft: null, active: null, record: null, crop: null,
  drawing: null, playback: null, busy: false,
  band: null, plan: null, reviewing: false, marking: null, markDrag: null, overrides: {},
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
  const note = frame.missing
    ? frame.title + ' · gap in the loop'
    : frame.title + ' · current artwork';
  showImage(framePath(frame.id, mode), frame.title, note);
}

function renderFrames() {
  const frames = desk.draft.frames;
  const frame = frames[currentFrame()] || null;
  ui('frames').replaceChildren(...frames.map((item, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'frame-tile' + (item.missing ? ' missing' : '');
    button.title = item.title;
    button.setAttribute('aria-pressed', String(item.id === desk.active));
    const icon = new Image();
    icon.src = framePath(item.id, 'thumb');
    icon.alt = '';
    const number = document.createElement('span');
    const slot = item.band && item.band.slot ? item.band.slot : index + 1;
    number.textContent = String(slot).padStart(2, '0') + (item.missing ? ' · gap' : '');
    button.setAttribute('aria-label', 'Select frame ' + slot + ', ' + item.title + (item.missing ? ', not captured' : ''));
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
  const missing = Boolean(frame?.missing);
  ui('frameAdjust').hidden = !frame || missing;
  ui('frameSource').hidden = !frame || !frame.band;
  if (!frame) {
    ui('sourceOverlay').hidden = true;
    return;
  }
  if (frame.band) {
    const source = frame.band;
    if (missing) {
      ui('sourceText').textContent = source.note || 'No marked photo captured this frame completely, so it stays an empty beat in its original place.';
      ui('sourcePhoto').hidden = true;
      ui('showSource').hidden = true;
      ui('sourceOverlay').hidden = true;
    } else {
      ui('sourceText').textContent =
        'Old-band frame ' + String(source.slot).padStart(2, '0') + ' of ' + source.frame_count +
        ' · taken whole from ' + source.photo +
        ' · line clarity ' + Number(source.clarity).toFixed(4) +
        ' · mean luminance ' + Number(source.luminance).toFixed(3) +
        ' · ' + Math.round((source.box?.[2] - source.box?.[0]) * 100) + '% of photo width';
      ui('sourcePhoto').hidden = false;
      ui('sourcePhoto').href = '/zoe' + draftPath() + '/frames/' + frame.id + '/band-photo';
      ui('showSource').hidden = false;
    }
  }
  if (missing) return;
  ui('frameTitle').textContent = frame.title;
  ui('downloadFrame').href = framePath(frame.id, 'original');
  fields(ui('frameAdjust'), frame.transform);
  ui('earlier').disabled = currentFrame() < 1;
  ui('later').disabled = currentFrame() >= desk.draft.frames.length - 1;
}

function showSourceOverlay() {
  const frame = desk.draft.frames[currentFrame()];
  const image = ui('sourceOverlay');
  if (!frame || !frame.band || frame.band.missing) return;
  if (image.dataset.frame === frame.id && !image.hidden) {
    image.hidden = true;
    return;
  }
  image.src = '/zoe' + draftPath() + '/frames/' + frame.id + '/band-overlay?revision=' + desk.draft.revision;
  image.dataset.frame = frame.id;
  image.hidden = false;
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
  renderBand();
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

// ------------------------------------------------------ old band reassembly

function renderBand() {
  const band = desk.draft.band || { frame_count: 8, captures: [] };
  ui('bandCount').elements.frame_count.value = band.frame_count;
  const captures = band.captures || [];
  ui('bandCaptures').replaceChildren(...captures.map((capture, index) => {
    const row = document.createElement('div');
    row.className = 'band-capture' + (capture.registration ? ' ready' : '');
    const line = document.createElement('div');
    line.className = 'row';
    const name = document.createElement('strong');
    name.textContent = (index + 1) + '. ' + capture.title;
    const state = document.createElement('small');
    state.textContent = capture.registration ? 'marked' : 'not marked';
    line.append(name, state);
    const detail = document.createElement('div');
    detail.className = 'capline';
    const low = capture.registration?.boundaries?.[0];
    const high = capture.registration?.boundaries?.[1];
    detail.innerHTML = capture.registration
      ? 'Boundaries: frame <b>' + low.frame + '</b> at ' + low.position.toFixed(3) +
        ' · frame <b>' + high.frame + '</b> at ' + high.position.toFixed(3) +
        ' · ' + capture.width + '×' + capture.height + ' px'
      : 'Mark the visible strip and two named boundaries before reviewing sources.';
    const actions = document.createElement('div');
    actions.className = 'capture-actions';
    const mark = document.createElement('button');
    mark.type = 'button';
    mark.textContent = capture.registration ? 'Re-mark' : 'Mark strip';
    mark.addEventListener('click', () => startMarking(capture));
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'quiet danger';
    remove.textContent = 'Remove';
    remove.addEventListener('click', () => removeCapture(capture));
    actions.append(mark, remove);
    row.append(line, detail, actions);
    return row;
  }));
  if (!captures.length) {
    const empty = document.createElement('p');
    empty.className = 'band-hint';
    empty.textContent = 'No band photo yet. Add the flat photographs of the old strip.';
    ui('bandCaptures').append(empty);
  }
}

async function reloadBand(message) {
  const result = await ask(draftPath() + '/band');
  desk.draft.band = result.band;
  desk.plan = result.plan;
  desk.overrides = {};
  renderBand();
  renderBandPlan();
  if (message) notice(message);
}

function reviewBand() {
  desk.reviewing = true;
  ui('bandPanel').hidden = false;
  ui('bandPanel').scrollIntoView({ behavior: 'smooth', block: 'start' });
  ui('bandStatus').textContent = '';
  reloadBand();
}

function closeBand() {
  desk.reviewing = false;
  ui('bandPanel').hidden = true;
}

function captureLabel(plan, id) {
  const capture = plan.captures.find(row => row.id === id);
  return capture ? 'Photo ' + capture.index + ' · ' + capture.photo : 'Photo';
}

function chosenCapture(slot) {
  const override = desk.overrides[slot.frame];
  if (override !== undefined) return override;
  return slot.status === 'taken' ? slot.source.capture : null;
}

function renderBandPlan() {
  const plan = desk.plan;
  if (!plan) {
    ui('bandSlots').replaceChildren();
    ui('adoptBand').disabled = true;
    return;
  }
  const summary = plan.summary;
  ui('bandSummary').textContent =
    summary.taken + ' of ' + summary.frames + ' frames taken whole from marked photos · ' +
    summary.missing + ' gap' + (summary.missing === 1 ? '' : 's') +
    (summary.max_neighbour_jump === null ? '' : ' · largest neighbour luminance step ' + Number(summary.max_neighbour_jump).toFixed(3)) +
    (summary.ready_captures ? '' : ' · mark at least one photo first');
  const ready = plan.captures.some(row => row.ready);
  const slots = plan.slots.map(slot => {
    const card = document.createElement('div');
    card.className = 'band-slot ' + (chosenCapture(slot) ? 'taken' : 'missing');
    const head = document.createElement('div');
    head.className = 'slot-head';
    const name = document.createElement('span');
    name.textContent = 'Frame ' + String(slot.frame).padStart(2, '0');
    const count = document.createElement('em');
    count.textContent = slot.alternatives.length + (slot.alternatives.length === 1 ? ' take' : ' takes');
    head.append(name, count);
    card.append(head);
    const chosen = chosenCapture(slot);
    const match = chosen ? slot.alternatives.find(row => row.capture === chosen) : null;
    const preview = new Image();
    preview.alt = '';
    if (chosen) {
      const adopted = selectedFrameId(slot);
      preview.src = adopted
        ? framePath(adopted, 'thumb')
        : '/zoe' + draftPath() + '/band/take/' + slot.frame + '/' + chosen + '?revision=' + desk.draft.revision;
      preview.title = 'From ' + captureLabel(plan, chosen);
      const read = document.createElement('div');
      read.className = 'slot-read';
      if (match) {
        read.textContent = 'clarity ' + Number(match.relative_clarity).toFixed(2) +
          ' · luma ' + Number(match.luminance).toFixed(3);
      }
      card.append(preview, read);
    } else {
      preview.className = 'missing-art';
      preview.alt = 'Not captured';
      const read = document.createElement('div');
      read.className = 'slot-note';
      read.textContent = slot.alternatives.length
        ? 'Partly visible only; no whole take inside a marked strip.'
        : (slot.note || 'Not visible in any marked photo.');
      card.append(preview, read);
    }
    if (slot.alternatives.length > 1) {
      const select = document.createElement('select');
      select.setAttribute('aria-label', 'Source for old-band frame ' + slot.frame);
      for (const option of slot.alternatives) {
        select.append(new Option(
          captureLabel(plan, option.capture) + ' · clarity ' + Number(option.relative_clarity).toFixed(2) +
          ' · luma ' + Number(option.luminance).toFixed(3) +
          (slot.status === 'taken' && option.capture === slot.source.capture ? ' · recommended' : ''),
          option.capture,
        ));
      }
      select.value = chosen || '';
      select.addEventListener('change', () => {
        const recommended = slot.status === 'taken' ? slot.source.capture : null;
        if (select.value === recommended) delete desk.overrides[slot.frame];
        else desk.overrides[slot.frame] = select.value || null;
        renderBandPlan();
      });
      card.append(select);
    }
    return card;
  });
  ui('bandSlots').replaceChildren(...slots);
  ui('adoptBand').disabled = !ready || !plan.slots.some(slot => chosenCapture(slot));
  ui('adoptBand').textContent = Object.keys(desk.overrides).length ? 'Use this source set' : 'Use recommended sequence';
}

function selectedFrameId(slot) {
  const chosen = chosenCapture(slot);
  if (!chosen) return null;
  const frame = (desk.draft.frames || []).find(item => item.band && item.band.slot === slot.frame &&
    item.band.capture === chosen);
  return frame ? frame.id : null;
}

async function removeCapture(capture) {
  if (!window.confirm('Remove band photo ' + capture.title + '?')) return;
  renderDraft(await ask(draftPath() + '/band/captures/' + capture.id, 'DELETE', { revision: desk.draft.revision }));
  notice('Removed the band photo.');
  if (desk.reviewing) await reloadBand();
}

function startMarking(capture) {
  desk.marking = capture;
  desk.markDrag = null;
  ui('markStage').hidden = false;
  ui('markName').textContent = 'Marking ' + capture.title;
  ui('markImage').src = '/zoe' + draftPath() + '/band/captures/' + capture.id + '/image?revision=' + desk.draft.revision;
  const registration = capture.registration || {
    quad: [[.05, .27], [.95, .27], [.95, .73], [.05, .73]],
    boundaries: [{ frame: 1, position: .3 }, { frame: 2, position: .7 }],
  };
  desk.marking.quad = registration.quad.map(point => [...point]);
  desk.marking.boundaries = registration.boundaries.map(row => ({ ...row }));
  ui('markFrameA').value = desk.marking.boundaries[0].frame;
  ui('markFrameB').value = desk.marking.boundaries[1].frame;
  ui('markPosA').value = desk.marking.boundaries[0].position;
  ui('markPosB').value = desk.marking.boundaries[1].position;
  drawMark();
}

function drawMark() {
  const capture = desk.marking;
  if (!capture) return;
  const [x0, y0] = capture.quad[0];
  const stripWidth = Math.max(0, capture.quad[1][0] - x0);
  const stripHeight = Math.max(0, capture.quad[2][1] - y0);
  Object.assign(ui('markRect').style, {
    left: x0 * 100 + '%', top: y0 * 100 + '%',
    width: stripWidth * 100 + '%', height: stripHeight * 100 + '%',
  });
  const ticks = capture.boundaries.map(boundary => {
    const tick = document.createElement('i');
    tick.style.left = (x0 + boundary.position * stripWidth) * 100 + '%';
    tick.style.top = y0 * 100 + '%';
    tick.style.bottom = (1 - (y0 + stripHeight)) * 100 + '%';
    const label = document.createElement('b');
    label.textContent = String(boundary.frame);
    tick.append(label);
    return tick;
  });
  ui('markTicks').replaceChildren(...ticks);
  updateMarkPreview();
}

function updateMarkPreview() {
  const capture = desk.marking;
  if (!capture) return;
  const [first, second] = capture.boundaries;
  const distance = Math.abs((second.frame - first.frame) / (second.position - first.position));
  const width = Math.max(.001, capture.quad[1][0] - capture.quad[0][0]);
  ui('markPreview').textContent =
    'One old frame is about ' + (width / distance * 100).toFixed(1) + '% of the marked strip ' +
    '(' + (width * 100).toFixed(0) + '% of the photo width). Frames touching the strip edge stay gaps.';
}

function markPoint(event) {
  const box = ui('markImage').getBoundingClientRect();
  return [Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)),
    Math.max(0, Math.min(1, (event.clientY - box.top) / box.height))];
}

ui('markCanvas').addEventListener('pointerdown', event => {
  if (!desk.marking) return;
  desk.markDrag = markPoint(event);
  ui('markCanvas').setPointerCapture(event.pointerId);
});
ui('markCanvas').addEventListener('pointermove', event => {
  if (!desk.markDrag || !desk.marking) return;
  const [startX, startY] = desk.markDrag;
  const [endX, endY] = markPoint(event);
  const x0 = Math.min(startX, endX), x1 = Math.max(startX, endX);
  const y0 = Math.min(startY, endY), y1 = Math.max(startY, endY);
  desk.marking.quad = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
  drawMark();
});
ui('markCanvas').addEventListener('pointerup', () => { desk.markDrag = null; });
ui('markCanvas').addEventListener('pointercancel', () => { desk.markDrag = null; });
for (const [frameId, positionId, index] of [
  ['markFrameA', 'markPosA', 0], ['markFrameB', 'markPosB', 1],
]) {
  const frameInput = ui(frameId), positionInput = ui(positionId);
  const update = () => {
    if (!desk.marking) return;
    desk.marking.boundaries[index] = {
      frame: Math.max(1, Math.round(Number(frameInput.value) || 1)),
      position: Number(positionInput.value),
    };
    drawMark();
  };
  frameInput.addEventListener('input', update);
  positionInput.addEventListener('input', update);
}

on('bandCount', 'submit', async event => {
  renderDraft(await ask(draftPath() + '/band', 'PUT', {
    revision: desk.draft.revision, frame_count: event.target.elements.frame_count.value,
  }));
  await reloadBand();
  notice('Saved the old band frame count.');
});
on('bandUpload', 'submit', async event => {
  const data = new FormData(event.target);
  data.set('revision', String(desk.draft.revision));
  renderDraft(await ask(draftPath() + '/band/captures', 'POST', data));
  event.target.reset();
  await reloadBand();
  notice('Added the band photo. Mark its strip to use it.');
});
on('bandReview', 'click', reviewBand);
on('closeBand', 'click', closeBand);
on('showSource', 'click', showSourceOverlay);
on('cancelMark', 'click', () => {
  desk.marking = null;
  ui('markStage').hidden = true;
});
on('saveMark', 'click', async () => {
  const capture = desk.marking;
  if (!capture) return;
  renderDraft(await ask(draftPath() + '/band/captures/' + capture.id, 'PUT', {
    revision: desk.draft.revision,
    registration: { quad: capture.quad, boundaries: capture.boundaries },
  }));
  desk.marking = null;
  ui('markStage').hidden = true;
  if (!desk.reviewing) reviewBand();
  else await reloadBand();
  notice('Saved the strip marking.');
});
on('adoptBand', 'click', async () => {
  const slots = {};
  for (const [frame, capture] of Object.entries(desk.overrides)) slots[frame] = capture;
  const result = await ask(draftPath() + '/band/adopt', 'POST', {
    revision: desk.draft.revision, slots,
  });
  desk.plan = null;
  renderDraft(result);
  await reloadBand();
  notice('Placed the old-band frames in their original order. Gaps keep their numbers.');
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
  const sources = frames.map(frame => framePath(frame.id, 'prepared'));
  const step = () => {
    const frame = frames[index];
    showImage(sources[index], frame.title,
      'Direct loop · frame ' + (index + 1) + (frame.missing ? ' · not captured' : ''));
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
