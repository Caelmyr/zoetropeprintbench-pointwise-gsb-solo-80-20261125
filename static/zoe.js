const ui = id => document.getElementById(id);
const desk = {
  config: null, draft: null, active: null, record: null, crop: null,
  drawing: null, playback: null, busy: false,
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
      const overlay = lineageOverlayUrl(index + 1);
      if (overlay) {
        showImage(overlay, 'Cross-revision overlay', 'Cross-revision overlay · frame ' + (index + 1));
      } else {
        frameStage();
      }
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
  renderLineagePanel();
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
  if (detail.mode === 'lineage') {
    renderRetainedLineage(detail);
    return;
  }
  ui('lineageQuestions').replaceChildren();
  const url = '/zoe' + draftPath() + '/proofs/' + detail.id + '/image';
  showImage(url, detail.title, detail.title + ' · ' + detail.size.join(' × ') + ' px' + (detail.dpi ? ' · ' + detail.dpi + ' DPI' : ''));
}

function renderRetainedLineage(detail) {
  const body = ui('lineageRows');
  const questionBlock = ui('lineageQuestions');
  questionBlock.replaceChildren();
  const questions = detail.lineage.questions || [];
  if (questions.length) {
    const heading = document.createElement('h3');
    heading.textContent = 'Pause-area judgements retained with this proof';
    questionBlock.append(heading);
    for (const question of questions) {
      const card = document.createElement('div');
      card.className = 'lineage-question';
      const lead = document.createElement('p');
      lead.textContent = 'New frame ' + question.new_frame + ' was reviewed.';
      const advice = document.createElement('p');
      advice.className = 'lineage-advice';
      advice.textContent = 'Suggested old ' + question.suggested_old_frame + '. ' + question.reason;
      card.append(lead, advice);
      questionBlock.append(card);
    }
  }
  body.replaceChildren(...detail.lineage.pairs.map(row => {
    const tr = document.createElement('tr');
    tr.className = 'lineage-row-' + row.status;
    const cells = [
      row.new_frame,
      row.old_frame ? String(row.old_frame) : '—',
      row.pose_score === null || row.pose_score === '' ? '—' : Number(row.pose_score).toFixed(2),
      row.continuity_score === null || row.continuity_score === '' ? '—' : Number(row.continuity_score).toFixed(2),
      statusText(row.status),
    ];
    for (const value of cells) {
      const td = document.createElement('td');
      td.textContent = value;
      tr.append(td);
    }
    return tr;
  }));
  const dropped = ui('lineageDropped');
  const droppedRows = (detail.rows || []).filter(row => row.status === 'old_frame_without_new_partner');
  dropped.hidden = !droppedRows.length;
  dropped.textContent = droppedRows.length
    ? 'Old frames without a new counterpart: ' + droppedRows.map(row => 'old ' + row.old_frame).join(', ')
    : '';
  ui('lineageResult').hidden = false;
  ui('lineagePanel').hidden = false;
  const url = lineageOverlayUrl(1) || ('/zoe' + draftPath() + '/proofs/' + detail.id + '/image');
  showImage(url, detail.title, detail.title + ' · click any current frame for its paired old frame');
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

const lineage = { analysis: null, savedId: null };

function lineageProofChoices() {
  const usable = desk.draft.proofs.filter(item => item.mode !== 'lineage');
  options(ui('lineageProof'), [['', 'Select retained proof'], ...usable.map(item => [item.id, item.title])]);
}

function fillAnchor(select, count, prefix) {
  select.replaceChildren(...Array.from({ length: count }, (_, index) =>
    new Option(prefix + ' ' + (index + 1), String(index))));
}

function renderLineagePanel() {
  lineage.analysis = null;
  lineage.savedId = null;
  ui('lineageResult').hidden = true;
  const hasOldProof = desk.draft.proofs.some(item => item.mode !== 'lineage');
  ui('lineagePanel').hidden = !hasOldProof || desk.draft.frames.length < 2;
  if (!hasOldProof) return;
  lineageProofChoices();
  fillAnchor(ui('lineageNewAnchor'), desk.draft.frames.length, 'New');
  ui('lineageOldAnchor').replaceChildren();
}

on('lineageProof', 'change', async () => {
  ui('lineageResult').hidden = true;
  const id = ui('lineageProof').value;
  ui('lineageOldAnchor').replaceChildren();
  if (!id) return;
  const detail = await ask(draftPath() + '/proofs/' + id);
  if (!detail.frames || !detail.frames.length) {
    notice('That retained proof does not carry a complete frame loop.', true);
    ui('lineageProof').value = '';
    return;
  }
  fillAnchor(ui('lineageOldAnchor'), detail.frames.length, 'Old');
});

function statusText(status) {
  return {
    anchor: 'anchor', matched: 'matched', uncertain: 'uncertain',
    unmatched: 'no reliable match', old_frame_without_new_partner: 'old frame, no new partner',
  }[status] || status;
}

function renderLineageAnalysis(analysis) {
  lineage.analysis = analysis;
  ui('lineageResult').hidden = false;
  const questionBlock = ui('lineageQuestions');
  questionBlock.replaceChildren();
  if (analysis.questions.length) {
    const heading = document.createElement('h3');
    heading.textContent = 'Frames near the held pause need a judgement';
    questionBlock.append(heading);
    for (const question of analysis.questions) {
      const card = document.createElement('div');
      card.className = 'lineage-question';
      const lead = document.createElement('p');
      lead.textContent = 'New frame ' + question.new_frame
        + (question.current_old_frame ? ' currently pairs with old ' + question.current_old_frame
          : ' currently has no old partner') + '.';
      const advice = document.createElement('p');
      advice.className = 'lineage-advice';
      advice.textContent = 'Suggested: old ' + question.suggested_old_frame + '. ' + question.reason;
      const choices = document.createElement('div');
      choices.className = 'lineage-choice-row';
      for (const alternative of question.alternatives) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'paper-button' + (alternative.old_frame === question.suggested_old_frame ? ' suggested' : '');
        button.disabled = alternative.in_use_elsewhere;
        button.textContent = 'Old ' + alternative.old_frame
          + ' · pose ' + alternative.pose_score.toFixed(2)
          + ' · flow ' + alternative.continuity_score.toFixed(2)
          + (alternative.in_use_elsewhere ? ' (used)' : '');
        button.addEventListener('click', () => {
          const row = analysis.pairs.find(item => item.new_frame === question.new_frame);
          for (const other of analysis.pairs) {
            if (other.old_frame === alternative.old_frame && other.new_frame !== question.new_frame) {
              other.old_frame = null;
              other.old_title = null;
              other.status = 'unmatched';
              other.reliable = false;
              other.pose_score = null;
              other.continuity_score = null;
            }
          }
          row.old_frame = alternative.old_frame;
          row.old_title = 'Old frame ' + alternative.old_frame;
          row.pose_score = alternative.pose_score;
          row.continuity_score = alternative.continuity_score;
          row.status = row.anchor ? 'anchor' : alternative.pose_score >= 0.58 ? 'matched' : 'uncertain';
          row.reliable = alternative.pose_score >= 0.58;
          renderLineageAnalysis(analysis);
        });
        choices.append(button);
      }
      card.append(lead, advice, choices);
      questionBlock.append(card);
    }
  }
  const body = ui('lineageRows');
  body.replaceChildren(...analysis.pairs.map(row => {
    const tr = document.createElement('tr');
    tr.className = 'lineage-row-' + row.status;
    const cells = [
      row.new_frame,
      row.old_frame ? String(row.old_frame) : '—',
      row.pose_score === null ? '—' : Number(row.pose_score).toFixed(2),
      row.continuity_score === null ? '—' : Number(row.continuity_score).toFixed(2),
      statusText(row.status),
    ];
    for (const value of cells) {
      const td = document.createElement('td');
      td.textContent = value;
      tr.append(td);
    }
    return tr;
  }));
  const dropped = ui('lineageDropped');
  dropped.hidden = !analysis.dropped.length;
  dropped.textContent = analysis.dropped.length
    ? 'Old frames without a new counterpart: ' + analysis.dropped.map(item => 'old ' + item.old_frame).join(', ')
    : '';
}

on('lineageAnalyze', 'click', async () => {
  const proofId = ui('lineageProof').value;
  if (!proofId) {
    notice('Choose the retained proof of the earlier loop first.', true);
    return;
  }
  const analysis = await ask(draftPath() + '/lineage/analyze', 'POST', {
    lineage_proof: proofId,
    anchor_old: Number(ui('lineageOldAnchor').value),
    anchor_new: Number(ui('lineageNewAnchor').value),
  });
  renderLineageAnalysis(analysis);
  notice('Correspondence built along the loop order. Review uncertain frames before adopting it.');
});

on('lineageAdopt', 'click', async () => {
  if (!lineage.analysis) return;
  const saved = await ask(draftPath() + '/proofs', 'POST', {
    mode: 'lineage',
    revision: desk.draft.revision,
    lineage_proof: ui('lineageProof').value,
    anchor_old: Number(ui('lineageOldAnchor').value),
    anchor_new: Number(ui('lineageNewAnchor').value),
    mapping: {
      pairs: lineage.analysis.pairs,
      dropped: lineage.analysis.dropped,
      questions: lineage.analysis.questions,
    },
  });
  desk.draft = saved.draft;
  lineage.savedId = saved.proof.id;
  renderArchive();
  lineageProofChoices();
  fillAnchor(ui('lineageNewAnchor'), desk.draft.frames.length, 'New');
  ui('lineagePanel').hidden = false;
  ui('proofHistory').value = saved.proof.id;
  setProof(saved.proof);
  notice('Retained the cross-revision lineage. Click any current frame to see its paired old frame.');
});

function lineageOverlayUrl(frameNumber) {
  const record = desk.record;
  if (!record || record.mode !== 'lineage' || !record.lineage) return null;
  return '/zoe' + draftPath() + '/lineage/' + record.id + '/overlay/' + frameNumber;
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
