/* Prolific — interface web (vanilla JS) */

const $ = (sel) => document.querySelector(sel);

let currentJobId = null;
let pollTimer = null;
let lastDetailKey = null;

async function api(path, opts) {
  const res = await fetch('/api' + path, opts);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || res.statusText);
  }
  return res.json();
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function fillSelect(sel, items) {
  sel.innerHTML = items.map((i) => `<option value="${i.value}">${escapeHtml(i.label)}</option>`).join('');
}

async function loadConfig() {
  const cfg = await api('/config');
  fillSelect($('#workflow'), cfg.workflows.map((w) => ({ value: w.name, label: `${w.name} — ${w.style}` })));
  fillSelect($('#model'), [{ value: '', label: '— défaut config —' }, ...cfg.models.map((m) => ({ value: m, label: m }))]);
  $('#datasets').innerHTML = cfg.datasets.length
    ? cfg.datasets.map((d) => `<label class="check"><input type="checkbox" name="dataset" value="${d}"> ${escapeHtml(d)}</label>`).join('')
    : '<em>Aucun dataset indexé.</em>';
  $('#datasets').addEventListener('change', () => {
    const any = document.querySelectorAll('input[name="dataset"]:checked').length > 0;
    $('#random_tags').disabled = !any;
    if (!any) $('#random_tags').checked = false;
  });
}

function formToPayload() {
  const objectiveType = document.querySelector('input[name="objective_type"]:checked').value;
  const datasets = [...document.querySelectorAll('input[name="dataset"]:checked')].map((c) => c.value);
  return {
    theme: $('#theme').value.trim(),
    workflow: $('#workflow').value,
    model: $('#model').value || null,
    objective_type: objectiveType,
    n: objectiveType === 'count' ? parseInt($('#n').value, 10) : null,
    minutes: objectiveType === 'duration' ? parseInt($('#minutes').value, 10) : null,
    orientation: $('#orientation').value,
    style: $('#style').value,
    datasets,
    swap_vram: $('#swap_vram').checked,
    positive_suffix: $('#positive_suffix').value.trim() || null,
    negative: $('#negative').value.trim() || null,
    filename_prefix: $('#filename_prefix').value.trim() || null,
    draft_batch_size: $('#draft_batch_size').value ? parseInt($('#draft_batch_size').value, 10) : null,
    temperature: $('#temperature').value ? parseFloat($('#temperature').value) : null,
    batch: parseInt($('#batch').value, 10) || 1,
    min_words: parseInt($('#min_words').value, 10) || 0,
    random_tags: $('#random_tags').checked,
    seed: $('#seed').value ? parseInt($('#seed').value, 10) : null,
    enable_traces: $('#enable_traces').checked,
  };
}

async function loadJobs() {
  const list = await api('/jobs');
  $('#job-list').innerHTML = list.map((j) => `
    <li class="job" data-id="${j.id}">
      <span class="job-id">#${j.id}</span>
      <span class="job-theme">${escapeHtml(j.theme)}</span>
      <span class="job-progress">${j.images_done}/${j.images_target ?? '—'}</span>
      <span class="badge ${j.status}">${j.status}</span>
    </li>
  `).join('');
  document.querySelectorAll('#job-list li').forEach((li) =>
    li.addEventListener('click', () => loadJobDetail(parseInt(li.dataset.id, 10), { force: true })));
}

async function loadJobDetail(id, { force = false } = {}) {
  currentJobId = id;
  const job = await api('/jobs/' + id);
  const key = `${job.status}:${job.images_done ?? ''}:${job.images_target ?? ''}`;
  if (!force && key === lastDetailKey && $('#job-detail').dataset.jobId == id) {
    return; // rien de nouveau : évite le clignotement des vignettes
  }
  lastDetailKey = key;
  const done = (job.images || []).filter((i) => i.status === 'done');
  const running = ['running', 'pending'].includes(job.status);
  $('#job-detail').dataset.jobId = id;
  $('#job-detail').innerHTML = `
    <h3>Job #${job.id} <span class="badge ${job.status}">${job.status}</span></h3>
    <p class="meta">workflow : ${escapeHtml(job.workflow)} · style : ${escapeHtml(job.style ?? '—')} · ${job.images_done}/${job.images_target ?? '—'} images</p>
    ${job.error ? `<p class="error">${escapeHtml(job.error)}</p>` : ''}
    ${running ? '<button id="stop-btn" type="button">Arrêter</button>' : ''}
    <button id="untrace-btn" type="button" class="ghost">Retirer des traces</button>
    <button id="delete-btn" type="button" class="danger">Supprimer le job (garde les traces)</button>
    <button id="purge-btn" type="button" class="danger">Supprimer job + traces</button>
    <div class="thumbs">
      ${done.map((i) => `<figure><img src="${i.url}" loading="lazy" alt=""><figcaption>${escapeHtml(i.positive)}</figcaption></figure>`).join('') || '<p class="meta">Aucune image générée pour l\'instant.</p>'}
    </div>
  `;
  const stop = $('#stop-btn');
  if (stop) stop.addEventListener('click', async () => {
    await api('/jobs/' + id + '/stop', { method: 'POST' });
    loadJobDetail(id, { force: true });
  });
  const untrace = $('#untrace-btn');
  if (untrace) untrace.addEventListener('click', async () => {
    if (!confirm('Retirer ce job des traces (dataset de fine-tuning) ? Le job et ses images restent.')) return;
    await api('/jobs/' + id + '/traces', { method: 'DELETE' });
    loadJobDetail(id, { force: true });
  });
  const del = $('#delete-btn');
  if (del) del.addEventListener('click', async () => {
    if (!confirm('Supprimer le job et ses images ? Les traces (dataset de fine-tuning) seront conservées.')) return;
    await api('/jobs/' + id, { method: 'DELETE' });
    currentJobId = null;
    $('#job-detail').innerHTML = '<p class="meta">Sélectionne un job pour voir ses images.</p>';
    loadJobs();
  });
  const purge = $('#purge-btn');
  if (purge) purge.addEventListener('click', async () => {
    if (!confirm('Supprimer le job, ses images ET ses traces (dataset de fine-tuning) ? Irréversible.')) return;
    await api('/jobs/' + id + '/purge', { method: 'DELETE' });
    currentJobId = null;
    $('#job-detail').innerHTML = '<p class="meta">Sélectionne un job pour voir ses images.</p>';
    loadJobs();
  });
  attachLightbox();
}

function attachLightbox() {
  const lb = $('#lightbox');
  const lbImg = $('#lightbox-img');
  document.querySelectorAll('#job-detail .thumbs img').forEach((img) => {
    img.addEventListener('click', () => {
      lbImg.src = img.src;
      lb.hidden = false;
    });
  });
}

$('#lightbox').addEventListener('click', () => { $('#lightbox').hidden = true; });

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      await loadJobs();
      if (currentJobId != null) await loadJobDetail(currentJobId);
    } catch (e) { /* le serveur peut être occupé : on ignore */ }
  }, 2000);
}

/* — événements — */
$('#job-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = $('#submit-btn');
  btn.disabled = true;
  try {
    const { job_id } = await api('/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(formToPayload()),
    });
    currentJobId = job_id;
    await loadJobs();
    await loadJobDetail(job_id, { force: true });
    startPolling();
  } catch (err) {
    alert('Erreur : ' + err.message);
  } finally {
    btn.disabled = false;
  }
});

document.querySelectorAll('input[name="objective_type"]').forEach((r) =>
  r.addEventListener('change', () => {
    const isCount = document.querySelector('input[name="objective_type"]:checked').value === 'count';
    $('#duration-wrap').hidden = isCount;
  }));

$('#refresh-btn').addEventListener('click', () => { loadJobs(); if (currentJobId != null) loadJobDetail(currentJobId, { force: true }); });

$('#model').addEventListener('change', updateTemperatureHint);

async function updateTemperatureHint() {
  const model = $('#model').value;
  const hint = $('#temperature-hint');
  if (!model) { hint.textContent = ''; return; }
  try {
    const r = await api('/model_temperature?model=' + encodeURIComponent(model));
    hint.textContent = r.temperature != null ? `(modèle : ${r.temperature})` : '(modèle : défaut 0.8)';
  } catch (e) { hint.textContent = ''; }
}

/* — init — */
(async () => {
  try { await loadConfig(); } catch (e) { console.warn('config:', e); }
  try { await loadJobs(); startPolling(); } catch (e) { console.warn('jobs:', e); }
})();
