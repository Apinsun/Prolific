/* Prolific — interface web (vanilla JS) */

const $ = (sel) => document.querySelector(sel);

let currentJobId = null;
let pollTimer = null;
let lastDetailKey = null;
let detailTab = 'images';
const jobsLimit = 20;
let jobsOffset = 0;
let jobsTotal = 0;
let jobsSearch = '';
let jobsStatus = '';
let searchTimer = null;

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
  fillSelect($('#preset'), [{ value: '', label: '— défaut (draft_system.txt) —' }, ...(cfg.presets || []).map((p) => ({ value: p, label: p }))]);
  $('#datasets').innerHTML = cfg.datasets.length
    ? cfg.datasets.map((d) => `<label class="check"><input type="checkbox" name="dataset" value="${d}"> ${escapeHtml(d)}</label>`).join('')
    : '<em>Aucun dataset indexé.</em>';
  $('#random_datasets').innerHTML = cfg.datasets.length
    ? cfg.datasets.map((d) => `<label class="check"><input type="checkbox" name="random_dataset" value="${d}"> ${escapeHtml(d)}</label>`).join('')
    : '<em>Aucun dataset indexé.</em>';

  const anyRandomSource = () =>
    document.querySelectorAll('input[name="dataset"]:checked').length > 0 ||
    document.querySelectorAll('input[name="random_dataset"]:checked').length > 0;
  const refreshRandomState = () => {
    $('#random_tags_count').disabled = !anyRandomSource();
  };
  $('#datasets').addEventListener('change', refreshRandomState);
  $('#random_datasets').addEventListener('change', refreshRandomState);
}

function formToPayload() {
  const objectiveType = document.querySelector('input[name="objective_type"]:checked').value;
  const datasets = [...document.querySelectorAll('input[name="dataset"]:checked')].map((c) => c.value);
  const random_datasets = [...document.querySelectorAll('input[name="random_dataset"]:checked')].map((c) => c.value);
  return {
    theme: $('#theme').value.trim(),
    workflow: $('#workflow').value,
    model: $('#model').value || null,
    objective_type: objectiveType,
    n: objectiveType === 'count' ? parseInt($('#n').value, 10) : null,
    minutes: objectiveType === 'duration' ? parseInt($('#minutes').value, 10) : null,
    orientation: $('#orientation').value,
    style: $('#style').value,
    preset: $('#preset').value || null,
    datasets,
    random_datasets: random_datasets.length ? random_datasets : null,
    swap_vram: $('#swap_vram').checked,
    positive_suffix: $('#positive_suffix').value.trim() || null,
    positive_prefix: $('#positive_prefix').value.trim() || null,
    negative: $('#negative').value.trim() || null,
    filename_prefix: $('#filename_prefix').value.trim() || null,
    draft_batch_size: $('#draft_batch_size').value ? parseInt($('#draft_batch_size').value, 10) : null,
    temperature: $('#temperature').value ? parseFloat($('#temperature').value) : null,
    batch: parseInt($('#batch').value, 10) || 1,
    min_words: parseInt($('#min_words').value, 10) || 0,
    random_tags: datasets.length > 0 || random_datasets.length > 0,
    random_tags_count: parseInt($('#random_tags_count').value, 10) || 15,
    seed: $('#seed').value ? parseInt($('#seed').value, 10) : null,
    enable_traces: $('#enable_traces').checked,
    test_mode: $('#test_mode').checked,
    forced_searches: parseInt($('#forced_searches').value, 10) || 0,
  };
}

function renderJobList(jobs) {
  return jobs.map((j) => `
    <li class="job" data-id="${j.id}">
      <span class="job-id">#${j.id}</span>
      <span class="job-theme">${escapeHtml(j.theme)}</span>
      <span class="job-progress">${j.images_done}/${j.images_target ?? '—'}</span>
      <span class="badge ${j.status}">${j.status}</span>
      <button type="button" class="reuse-btn ghost" data-id="${j.id}" title="Copier les paramètres dans le formulaire">Réutiliser</button>
    </li>
  `).join('');
}

function bindJobList() {
  document.querySelectorAll('#job-list li').forEach((li) =>
    li.addEventListener('click', (e) => {
      if (e.target.closest('.reuse-btn')) return;
      loadJobDetail(parseInt(li.dataset.id, 10), { force: true });
    }));
  document.querySelectorAll('#job-list .reuse-btn').forEach((btn) =>
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      copyJobParams(parseInt(btn.dataset.id, 10));
    }));
}

async function fetchJobs(offset = 0, limit = jobsLimit) {
  const q = new URLSearchParams({
    search: jobsSearch, status: jobsStatus,
    offset: String(offset), limit: String(limit),
  });
  return await api('/jobs?' + q.toString());
}

function updateLoadMoreBtn() {
  const btn = $('#load-more-btn');
  btn.hidden = jobsOffset >= jobsTotal;
}

async function refreshJobs() {
  const data = await fetchJobs(0, Math.max(jobsLimit, jobsOffset));
  jobsTotal = data.total;
  $('#job-list').innerHTML = renderJobList(data.jobs);
  jobsOffset = data.jobs.length;
  bindJobList();
  updateLoadMoreBtn();
}

async function loadMoreJobs() {
  const data = await fetchJobs(jobsOffset, jobsLimit);
  jobsTotal = data.total;
  $('#job-list').insertAdjacentHTML('beforeend', renderJobList(data.jobs));
  jobsOffset += data.jobs.length;
  bindJobList();
  updateLoadMoreBtn();
}

function resetJobs() {
  jobsOffset = 0;
  refreshJobs();
}

async function copyJobParams(id) {
  const job = await api('/jobs/' + id);
  $('#theme').value = job.theme ?? '';
  $('#workflow').value = job.workflow ?? '';
  $('#model').value = job.model ?? '';
  $('#style').value = job.style ?? 'tags';
  $('#preset').value = job.preset ?? '';
  $('#orientation').value = job.orientation ?? 'carre';

  const obj = job.objective || {};
  const isDuration = obj.type === 'duration';
  document.querySelector(`input[name="objective_type"][value="${isDuration ? 'duration' : 'count'}"]`).checked = true;
  $('#duration-wrap').hidden = !isDuration;
  if (isDuration) $('#minutes').value = obj.minutes ?? 20;
  else $('#n').value = obj.n ?? 10;

  const datasets = job.datasets || [];
  document.querySelectorAll('input[name="dataset"]').forEach((c) => {
    c.checked = datasets.includes(c.value);
  });
  const randomDatasets = job.random_datasets || [];
  document.querySelectorAll('input[name="random_dataset"]').forEach((c) => {
    c.checked = randomDatasets.includes(c.value);
  });
  $('#test_mode').checked = !!job.test_mode;
  $('#random_tags_count').value = job.random_tags_count ?? 15;
  $('#random_tags_count').disabled = datasets.length === 0 && randomDatasets.length === 0;

  $('#swap_vram').checked = !!job.swap_vram;
  $('#enable_traces').checked = job.enable_traces == null ? true : !!job.enable_traces;
  $('#positive_suffix').value = job.positive_suffix ?? '';
  $('#positive_prefix').value = job.positive_prefix ?? '';
  $('#negative').value = job.negative ?? '';
  $('#filename_prefix').value = job.filename_prefix ?? '';
  $('#draft_batch_size').value = job.draft_batch_size ?? '';
  $('#forced_searches').value = job.forced_searches ?? 0;
  $('#batch').value = job.batch ?? 1;
  $('#min_words').value = job.min_words ?? 0;
  $('#temperature').value = job.temperature ?? '';
  $('#seed').value = job.seed ?? '';

  updateTemperatureHint();
  document.getElementById('panel-form').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderJobParams(job) {
  const fmt = (v) => (v == null || v === '' ? '—' : escapeHtml(String(v)));
  const yn = (v) => (v ? 'oui' : 'non');
  const d = (iso) => (iso ? new Date(iso).toLocaleString() : '—');
  const objective = job.objective
    ? (job.objective.type === 'count' ? `${job.objective.n} prompts` : `${job.objective.minutes} minutes`)
    : '—';
  const rows = [
    ['Objectif', objective],
    ['Thème', escapeHtml(job.theme ?? '')],
    ['Workflow', escapeHtml(job.workflow ?? '')],
    ['Modèle (LLM)', fmt(job.model)],
    ['Style', fmt(job.style)],
    ['Prompt de base', fmt(job.preset)],
    ['Orientation', fmt(job.orientation)],
    ['Datasets', (job.datasets || []).length ? escapeHtml(job.datasets.join(', ')) : '—'],
    ['Datasets aléatoires', (job.random_datasets || []).length ? escapeHtml(job.random_datasets.join(', ')) : '— (mêmes que recherche)'],
    ['Mode test', yn(job.test_mode)],
    ['Swap VRAM', yn(job.swap_vram)],
    ['Batch (images par prompt)', fmt(job.batch)],
    ['Taille du lot de rédaction', job.draft_batch_size != null ? job.draft_batch_size : 'tout en un appel'],
    ['Recherches forcées', job.forced_searches ?? 0],
    ['Longueur min', fmt(job.min_words)],
    ['Mots aléatoires (RAG)', yn(job.random_tags)],
    ['Mots aléatoires (nb)', job.random_tags_count != null ? job.random_tags_count : 15],
    ['Préfixe positif', fmt(job.positive_prefix)],
    ['Suffixe positif', fmt(job.positive_suffix)],
    ['Négatif', fmt(job.negative)],
    ['Température', job.temperature != null ? job.temperature : 'défaut du modèle'],
    ['Seed', fmt(job.seed)],
    ['Sous-dossier de sortie', fmt(job.filename_prefix)],
    ['Traces', yn(job.enable_traces)],
    ['Statut', escapeHtml(job.status ?? '')],
    ['Créé le', d(job.created_at)],
    ['Démarré le', d(job.started_at)],
    ['Terminé le', d(job.finished_at)],
  ];
  return `<table class="params-table">${rows.map(([k, v]) => `<tr><th>${k}</th><td>${v}</td></tr>`).join('')}</table>`;
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
  const running = ['running', 'pending', 'queued'].includes(job.status);
  const jobChanged = $('#job-detail').dataset.jobId != id;
  if (jobChanged || force) detailTab = 'images';
  $('#job-detail').dataset.jobId = id;
  $('#job-detail').innerHTML = `
    <h3>Job #${job.id} <span class="badge ${job.status}">${job.status}</span></h3>
    <p class="meta">workflow : ${escapeHtml(job.workflow)} · style : ${escapeHtml(job.style ?? '—')} · ${job.images_done}/${job.images_target ?? '—'} images</p>
    ${job.error ? `<p class="error">${escapeHtml(job.error)}</p>` : ''}
    ${running ? '<button id="stop-btn" type="button">Arrêter</button>' : ''}
    ${job.status === 'paused' ? '<button id="resume-btn" type="button">Reprendre</button>' : ''}
    <button id="untrace-btn" type="button" class="ghost">Retirer des traces</button>
    <button id="delete-btn" type="button" class="danger">Supprimer le job (garde les traces)</button>
    <button id="purge-btn" type="button" class="danger">Supprimer job + traces</button>
    <div class="detail-tabs">
      <button type="button" class="tab-btn ${detailTab === 'images' ? 'active' : ''}" data-tab="images">Images (${done.length})</button>
      <button type="button" class="tab-btn ${detailTab === 'params' ? 'active' : ''}" data-tab="params">Paramètres</button>
    </div>
    <div class="tab-panel" id="panel-images" ${detailTab !== 'images' ? 'hidden' : ''}>
      <div class="thumbs">
        ${done.map((i) => `<figure><img src="${i.url}" loading="lazy" alt=""><figcaption>${escapeHtml(i.positive)}</figcaption></figure>`).join('') || '<p class="meta">Aucune image générée pour l\'instant.</p>'}
      </div>
    </div>
    <div class="tab-panel" id="panel-params" ${detailTab !== 'params' ? 'hidden' : ''}>
      ${renderJobParams(job)}
    </div>
  `;
  const stop = $('#stop-btn');
  if (stop) stop.addEventListener('click', async () => {
    await api('/jobs/' + id + '/stop', { method: 'POST' });
    loadJobDetail(id, { force: true });
  });
  const resume = $('#resume-btn');
  if (resume) resume.addEventListener('click', async () => {
    await api('/jobs/' + id + '/resume', { method: 'POST' });
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
  document.querySelectorAll('#job-detail .tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      detailTab = btn.dataset.tab;
      document.querySelectorAll('#job-detail .tab-btn').forEach((b) =>
        b.classList.toggle('active', b.dataset.tab === detailTab));
      const pi = $('#panel-images');
      const pp = $('#panel-params');
      if (pi) pi.hidden = detailTab !== 'images';
      if (pp) pp.hidden = detailTab !== 'params';
    });
  });
  attachLightbox();
}

let lightboxImages = [];
let lightboxIndex = -1;

function attachLightbox() {
  const thumbs = document.querySelectorAll('#job-detail .thumbs img');
  lightboxImages = [...thumbs].map((img) => img.src);
  thumbs.forEach((img, idx) => {
    img.addEventListener('click', () => openLightbox(idx));
  });
}

function openLightbox(index) {
  if (index < 0 || index >= lightboxImages.length) return;
  lightboxIndex = index;
  $('#lightbox').hidden = false;
  updateLightbox();
}

function updateLightbox() {
  $('#lightbox-img').src = lightboxImages[lightboxIndex];
  $('#lightbox-prev').disabled = lightboxIndex <= 0;
  $('#lightbox-next').disabled = lightboxIndex >= lightboxImages.length - 1;
  $('#lightbox-counter').textContent = `${lightboxIndex + 1} / ${lightboxImages.length}`;
}

function lightboxPrev() {
  if (lightboxIndex > 0) { lightboxIndex--; updateLightbox(); }
}
function lightboxNext() {
  if (lightboxIndex < lightboxImages.length - 1) { lightboxIndex++; updateLightbox(); }
}
function closeLightbox() { $('#lightbox').hidden = true; }

$('#lightbox').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) closeLightbox(); // fermer seulement sur le fond
});
$('#lightbox-prev').addEventListener('click', lightboxPrev);
$('#lightbox-next').addEventListener('click', lightboxNext);
$('#lightbox-close').addEventListener('click', closeLightbox);

document.addEventListener('keydown', (e) => {
  if ($('#lightbox').hidden) return;
  if (e.key === 'ArrowLeft') { e.preventDefault(); lightboxPrev(); }
  else if (e.key === 'ArrowRight') { e.preventDefault(); lightboxNext(); }
  else if (e.key === 'Escape') { closeLightbox(); }
});

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      await refreshJobs();
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
    jobsSearch = ''; jobsStatus = '';
    $('#jobs-search').value = ''; $('#jobs-status').value = '';
    await refreshJobs();
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

$('#refresh-btn').addEventListener('click', () => { refreshJobs(); if (currentJobId != null) loadJobDetail(currentJobId, { force: true }); });

$('#jobs-search').addEventListener('input', () => {
  jobsSearch = $('#jobs-search').value.trim();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(resetJobs, 300);
});
$('#jobs-status').addEventListener('change', () => {
  jobsStatus = $('#jobs-status').value;
  resetJobs();
});
$('#load-more-btn').addEventListener('click', loadMoreJobs);

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
  try { await refreshJobs(); startPolling(); } catch (e) { console.warn('jobs:', e); }
})();
