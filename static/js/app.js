/* ==========================================================================
   SPHEREx Sky Time Viewer — frontend controller
   ========================================================================== */
'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  datasets: [],
  dataset: null,
  epoch: 0,
  epochA: 0,
  epochB: 0,
  mode: 'image',
  bandIndex: null,
  cmap: 'mono',
  stretch: 'asinh',
  detector: 'all',
  diffScale: 6,
  zoom: 1,
  pan: { x: 0, y: 0 },
  frameSize: 640,
  showGrid: true,
  showCandidates: true,
  candidates: [],
  selectedCandidate: null,
  detection: null,
  catalogs: { gaia: [], wise: [] },
  catalogOn: { gaia: false, wise: false },
  query: null,
  panel: null,
  lastCoord: null,
  spectrum: null,
  spectrumGeom: null,
  bands: null,
  meta: null,
  stack: null,
  imageToken: 0,
  playing: false,
  playTimer: null,
  toastTimer: null,
  loadingTimer: null,
  dragging: false,
  dragMoved: false,
  dragStart: null,
  panStart: null,
  uploadFile: null,
  auth: {
    ready: false,
    user: null,
    csrf: null,
    favorites: [],
    preferences: {},
  },
  ai: { configured: false, mode: 'local', topics: [], tools: [], knowledge: null, classifier: null, training: false },
  workspace: false,
  pendingWorkspace: false,
  assistant: {
    conversations: [],
    conversationId: null,
    messages: [],
    streaming: false,
    controller: null,
    answer: '',
    renderQueued: false,
  },
  prefsTimer: null,
};

const dom = {};

const AI_TOOL_LABELS = {
  list_datasets: 'Listar conjuntos de datos',
  dataset_summary: 'Resumen del campo abierto',
  band_coverage: 'Cobertura de canales',
  quality_report: 'Informe de calidad',
  detect_candidates: 'Detección y clasificación',
  measure_spectrum: 'Medición de espectro',
  search_catalog: 'Buscar en el índice SPHEREx',
  catalog_stats: 'Estadísticas del catálogo',
  search_knowledge: 'Base de conocimiento',
  crossmatch_field: 'Cruce Gaia DR3 / AllWISE',
};

const AI_SHORTCUTS = [
  {
    title: 'Desafío',
    items: [
      { label: 'Entender el reto', prompt: 'Explícame el desafío de NASA Space Apps de este visor SPHEREx: qué problema resuelve, qué datos usa y qué debería demostrar una buena entrega.' },
      { label: 'Guion de demo', prompt: 'Propón un guion de demostración de cinco minutos para presentar este visor SPHEREx a un jurado, con los pasos y las cifras que conviene destacar.' },
      { label: 'Ideas para mejorar', prompt: '¿Qué mejoras científicas y de producto harían más competitivo este visor SPHEREx para el desafío? Prioriza las que se pueden implementar rápido.' },
    ],
  },
  {
    title: 'Astronomía',
    items: [
      { label: 'Qué observa SPHEREx', prompt: 'Explícame qué observa SPHEREx, por qué usa infrarrojo cercano y qué tipos de ciencia permite con sus 102 canales.' },
      { label: 'Leer un espectro', prompt: 'Enséñame a interpretar un espectro SPHEREx entre 0.75 y 5 µm: continuo, líneas, hielos, polvo y qué conclusiones se pueden sacar.' },
      { label: 'Objetos en movimiento', prompt: 'Explícame cómo se detectan objetos en movimiento comparando épocas y qué limitaciones tiene el método de dipolos del visor.' },
    ],
  },
  {
    title: 'Imágenes',
    items: [
      { label: 'Analizar cambios', prompt: 'Ayúdame a analizar los cambios de imagen entre la primera y la última época del campo abierto. Dame una lista de comprobaciones y qué significaría cada patrón.' },
      { label: 'Artefactos y ruido', prompt: '¿Cómo distingo un cambio astronómico real de un artefacto, un rayo cósmico o un problema de registro entre épocas en estas imágenes SPHEREx?' },
      { label: 'Plan de validación', prompt: 'Diseña un plan para validar un candidato detectado en la diferencia de épocas: qué revisar en la imagen, el espectro, los flags y los catálogos.' },
    ],
  },
  {
    title: 'Datos y modelo',
    items: [
      { label: 'Base de datos', prompt: '¿Con qué datos SPHEREx, Gaia y WISE está entrenado el asistente? Cita las cifras del índice local y del clasificador.' },
      { label: 'Cómo clasifica', prompt: 'Explícame qué hace el clasificador de candidatos, con qué datos se entrenó y cuánto puedo confiar en sus etiquetas.' },
    ],
  },
];

const AI_QUICK_PROMPTS = [
  AI_SHORTCUTS[0].items[0],
  AI_SHORTCUTS[1].items[0],
  AI_SHORTCUTS[2].items[0],
  AI_SHORTCUTS[1].items[1],
  AI_SHORTCUTS[3].items[0],
];

/* --------------------------------------------------------------------------
   Utilities
   -------------------------------------------------------------------------- */
function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function fmtNum(value, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return Number(value).toFixed(digits);
}

function fmtFlux(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const n = Number(value);
  if (n === 0) return '0';
  if (Math.abs(n) < 0.001 || Math.abs(n) >= 10000) return n.toExponential(2);
  return n.toFixed(4);
}

function fmtAngle(deg) {
  if (!Number.isFinite(deg)) return '—';
  if (deg < 1 / 60) return `${(deg * 3600).toFixed(0)}″`;
  if (deg < 1) return `${(deg * 60).toFixed(1)}′`;
  return `${deg.toFixed(2)}°`;
}

function niceStep(value) {
  if (!(value > 0)) return 1;
  const exp = Math.floor(Math.log10(value));
  const base = 10 ** exp;
  const f = value / base;
  let n = 10;
  if (f <= 1) n = 1;
  else if (f <= 2) n = 2;
  else if (f <= 5) n = 5;
  return n * base;
}

function debounce(fn, wait) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const type = response.headers.get('content-type') || '';
  const body = type.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    const message = body && body.error ? body.error : `Error ${response.status}`;
    throw new Error(message);
  }
  return body;
}

function authedApi(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  if (state.auth.csrf) headers['X-CSRF-Token'] = state.auth.csrf;
  return api(path, { ...options, headers });
}

function formatDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleDateString('es', { year: 'numeric', month: 'short', day: 'numeric' });
}

function inlineMarkdown(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>');
}

function renderMarkdown(text) {
  const lines = String(text || '').replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let paragraph = [];
  let list = [];
  let code = null;

  const flushParagraph = () => {
    if (paragraph.length) {
      out.push(`<p>${paragraph.map(inlineMarkdown).join('<br>')}</p>`);
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list.length) {
      out.push(`<ul>${list.map((item) => `<li>${inlineMarkdown(item)}</li>`).join('')}</ul>`);
      list = [];
    }
  };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '');
    if (code !== null) {
      if (line.trim().startsWith('```')) {
        out.push(`<pre>${escapeHtml(code.join('\n'))}</pre>`);
        code = null;
      } else {
        code.push(raw);
      }
      continue;
    }
    if (line.trim().startsWith('```')) {
      flushParagraph();
      flushList();
      code = [];
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }
    if (/^\s*(---|___|\*\*\*)\s*$/.test(line)) {
      flushParagraph();
      flushList();
      out.push('<hr>');
      continue;
    }
    const heading = line.match(/^\s*#{1,4}\s+(.*)$/);
    if (heading) {
      flushParagraph();
      flushList();
      out.push(`<div class="md-h">${inlineMarkdown(heading[1])}</div>`);
      continue;
    }
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    if (bullet) {
      flushParagraph();
      list.push(bullet[1]);
      continue;
    }
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (numbered) {
      flushParagraph();
      list.push(numbered[1]);
      continue;
    }
    if (line.startsWith('>')) {
      flushParagraph();
      flushList();
      out.push(`<blockquote>${inlineMarkdown(line.replace(/^>\s?/, ''))}</blockquote>`);
      continue;
    }
    paragraph.push(line);
  }
  if (code !== null) out.push(`<pre>${escapeHtml(code.join('\n'))}</pre>`);
  flushParagraph();
  flushList();
  return out.join('');
}

/* --------------------------------------------------------------------------
   Boot
   -------------------------------------------------------------------------- */
document.addEventListener('DOMContentLoaded', init);

async function init() {
  cacheDom();
  bindEvents();
  resizeFrame();
  await refreshStatus();
  await Promise.all([refreshDatasets(), refreshAuth()]);
  await refreshAssistantStatus();
  await restoreFromUrl();
}

function cacheDom() {
  const ids = [
    'stage', 'viewport', 'image-frame', 'view-image', 'overlay', 'rail', 'panel',
    'panel-title', 'panel-close', 'panel-body', 'search-form', 'search-input',
    'status-chip', 'btn-about', 'search-name', 'search-ra', 'search-dec',
    'search-fov', 'search-epochs', 'search-detectors', 'btn-query', 'btn-load',
    'query-hint', 'query-result', 'demo-list', 'layer-dataset', 'layer-cmap',
    'layer-stretch', 'layer-detector', 'stack-summary', 'toggle-grid', 'toggle-candidates',
    'toggle-gaia', 'toggle-wise', 'gaia-count', 'wise-count', 'view-mode',
    'diff-controls', 'diff-a', 'diff-b', 'diff-scale-wrap', 'diff-scale',
    'diff-scale-value', 'compare-a', 'compare-b', 'detect-threshold',
    'btn-detect', 'detect-summary', 'candidate-list', 'spectrum-coord',
    'spectrum-sub', 'spectrum-csv', 'spectrum-canvas', 'chart-tooltip',
    'spectrum-stats', 'feature-list', 'bands-sub', 'btn-band-reset',
    'bands-grid', 'bands-note', 'meta-content', 'dropzone', 'upload-file',
    'btn-browse', 'upload-filename', 'upload-label', 'upload-ra', 'upload-dec',
    'upload-date', 'btn-upload', 'timeline', 'play-btn', 'timeline-ticks',
    'timeline-readout', 'zoom-in', 'zoom-out', 'zoom-reset', 'coord-ra',
    'coord-dec', 'coord-scale', 'loading', 'loading-title', 'loading-text', 'toast',
    'btn-account', 'account-name', 'btn-favorite', 'favorite-current-label',
    'favorite-btn-label', 'favorite-list', 'assistant-mode', 'assistant-new',
    'assistant-gate', 'assistant-login', 'assistant-main', 'assistant-conversations',
    'assistant-delete', 'assistant-messages', 'assistant-status',
    'assistant-status-text', 'assistant-topics', 'assistant-form', 'assistant-input',
    'assistant-send', 'assistant-expand', 'assistant-stop', 'btn-ai-workspace',
    'aiw', 'aiw-mode', 'aiw-gate', 'aiw-login', 'aiw-body', 'aiw-close',
    'aiw-conversations', 'aiw-messages', 'aiw-status', 'aiw-status-text',
    'aiw-prompts', 'aiw-form', 'aiw-input', 'aiw-send', 'aiw-stop',
    'aiw-context', 'aiw-dataset', 'aiw-shortcuts', 'aiw-tools', 'aiw-model',
    'account-guest', 'account-tabs', 'login-form', 'login-user',
    'login-password', 'btn-login', 'register-form', 'register-name', 'register-user',
    'register-email', 'register-password', 'btn-register', 'auth-hint',
    'account-profile', 'profile-avatar', 'profile-name', 'profile-meta',
    'profile-stats', 'btn-logout',
  ];
  for (const id of ids) {
    const key = id.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    dom[key] = document.getElementById(id);
  }
  dom.overlayCtx = dom.overlay.getContext('2d');
}

/* --------------------------------------------------------------------------
   Status & dataset catalogue
   -------------------------------------------------------------------------- */
async function refreshStatus() {
  try {
    const data = await api('/api/status');
    dom.statusChip.classList.toggle('online', Boolean(data.archive_online));
    dom.statusChip.classList.toggle('offline', !data.archive_online);
    dom.statusChip.querySelector('.status-text').textContent = data.archive_online
      ? `IRSA en línea · ${data.bands} bandas`
      : 'IRSA sin conexión · demo';
  } catch {
    dom.statusChip.classList.add('offline');
    dom.statusChip.querySelector('.status-text').textContent = 'Servidor sin conexión';
  }
}

async function refreshDatasets() {
  try {
    const data = await api('/api/datasets');
    state.datasets = data.datasets || [];
  } catch (error) {
    toast(error.message, 'error');
    state.datasets = [];
  }
  renderDatasetSelect();
  renderDemoList();
}

function renderDatasetSelect() {
  const groups = [
    ['archive', 'Datos reales · IRSA'],
    ['demo', 'Demostración'],
    ['upload', 'Cargas propias'],
  ];
  const selected = state.dataset ? state.dataset.id : '';
  dom.layerDataset.innerHTML = groups.map(([kind, label]) => {
    const items = state.datasets.filter((d) => d.kind === kind);
    if (!items.length) return '';
    return `<optgroup label="${label}">${items.map((d) =>
      `<option value="${escapeHtml(d.id)}"${d.id === selected ? ' selected' : ''}>${escapeHtml(d.label)} · ${d.n_epochs} ép.</option>`
    ).join('')}</optgroup>`;
  }).join('');
}

function renderDemoList() {
  const demos = state.datasets.filter((d) => d.kind === 'demo');
  if (!demos.length) {
    dom.demoList.innerHTML = '<div class="hint">No hay campos de demostración disponibles.</div>';
    return;
  }
  const activeId = state.dataset ? state.dataset.id : '';
  dom.demoList.innerHTML = demos.map((d) => `
    <button class="dataset-card${d.id === activeId ? ' active' : ''}" data-dataset="${escapeHtml(d.id)}" type="button">
      <div class="tag">${escapeHtml(d.tag || 'Demo')}</div>
      <div class="name">${escapeHtml(d.label)}</div>
      <div class="desc">${escapeHtml(d.description || '')}</div>
      <div class="meta">${d.n_epochs} épocas · ${d.n_frames} frames · RA ${fmtNum(d.ra, 2)} Dec ${fmtNum(d.dec, 2)}</div>
    </button>
  `).join('');
}

/* --------------------------------------------------------------------------
   Accounts, favorites & preferences
   -------------------------------------------------------------------------- */
function setBusy(button, busy, label) {
  if (!button) return;
  button.disabled = busy;
  if (label) button.textContent = label;
}

async function refreshAuth() {
  try {
    const data = await api('/api/auth/me');
    state.auth.user = data.user || null;
    state.auth.csrf = data.csrf || null;
    state.auth.favorites = data.favorites || [];
    state.auth.preferences = data.preferences || {};
  } catch {
    state.auth.user = null;
    state.auth.csrf = null;
    state.auth.favorites = [];
    state.auth.preferences = {};
  }
  state.auth.ready = true;
  renderAccount();
  renderFavorites();
  applyPreferences();
}

function applyPreferences() {
  if (!state.auth.user) return;
  const prefs = state.auth.preferences || {};
  if (['mono', 'ir', 'inferno', 'ice'].includes(prefs.cmap)) state.cmap = prefs.cmap;
  if (['asinh', 'log', 'sqrt', 'linear', 'zscale'].includes(prefs.stretch)) state.stretch = prefs.stretch;
  if (typeof prefs.showGrid === 'boolean') state.showGrid = prefs.showGrid;
  if (typeof prefs.showCandidates === 'boolean') state.showCandidates = prefs.showCandidates;
  if (typeof prefs.detector === 'string' && prefs.detector) state.detector = prefs.detector;
  if (state.dataset) {
    syncLayerControls();
    loadImage();
    drawOverlay();
  }
}

const savePreferences = debounce(async () => {
  if (!state.auth.user || !state.auth.csrf) return;
  try {
    const data = await authedApi('/api/prefs', {
      method: 'PUT',
      body: JSON.stringify({
        dataset: state.dataset ? state.dataset.id : null,
        cmap: state.cmap,
        stretch: state.stretch,
        detector: state.detector,
        showGrid: state.showGrid,
        showCandidates: state.showCandidates,
      }),
    });
    state.auth.preferences = data.preferences || state.auth.preferences;
  } catch {
    /* preferences are best-effort */
  }
}, 700);

function renderAccount() {
  const user = state.auth.user;
  if (dom.accountName) {
    dom.accountName.textContent = user ? (user.display_name || user.username) : 'Entrar';
  }
  if (dom.btnAccount) dom.btnAccount.classList.toggle('signed-in', Boolean(user));
  if (dom.accountGuest) dom.accountGuest.hidden = Boolean(user);
  if (dom.accountProfile) dom.accountProfile.hidden = !user;

  if (user) {
    const name = user.display_name || user.username;
    dom.profileName.textContent = name;
    dom.profileAvatar.textContent = String(name || '?').trim().charAt(0).toUpperCase();
    dom.profileMeta.textContent = `@${user.username} · ${user.email}`;
    const stats = user.stats || {};
    dom.profileStats.innerHTML = [
      ['Conversaciones', stats.conversations || 0],
      ['Mensajes', stats.messages || 0],
      ['Favoritos', stats.favorites || 0],
    ].map(([label, value]) => `
      <div class="stat-item">
        <div class="stat-label">${label}</div>
        <div class="stat-value">${value}</div>
      </div>
    `).join('');
    const joined = $('#profile-joined');
    if (joined) joined.textContent = `Desde ${formatDate(user.created_at)}`;
  }
  updateFavoriteButton();
  renderAssistantPanel();
}

function setAuthTab(tab) {
  $$('#account-tabs button').forEach((button) => {
    button.classList.toggle('active', button.dataset.authTab === tab);
  });
  dom.loginForm.hidden = tab !== 'login';
  dom.registerForm.hidden = tab !== 'register';
  dom.authHint.textContent = tab === 'login'
    ? 'Las cuentas guardan historial de chat, campos favoritos y preferencias en una base SQLite local. El visor sigue siendo público.'
    : 'Elige un usuario de 3–32 caracteres y una contraseña de al menos 8. El correo solo se usa para identificarte.';
}

function handleAuthSuccess(data) {
  state.auth.user = data.user || null;
  state.auth.csrf = data.csrf || null;
  state.auth.favorites = data.favorites || [];
  state.auth.preferences = data.preferences || {};
  state.auth.ready = true;
  renderAccount();
  renderFavorites();
  applyPreferences();
  loadAssistantConversations();
  const name = data.user ? (data.user.display_name || data.user.username) : '';
  toast(`Sesión iniciada como ${name}`, 'success');
  if (state.pendingWorkspace) {
    state.pendingWorkspace = false;
    openAiWorkspace();
  }
}

async function submitLogin(event) {
  event.preventDefault();
  const login = dom.loginUser.value.trim();
  const password = dom.loginPassword.value;
  if (!login || !password) {
    toast('Escribe tu usuario y contraseña.', 'error');
    return;
  }
  setBusy(dom.btnLogin, true, 'Entrando…');
  try {
    const data = await api('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ login, password }),
    });
    dom.loginPassword.value = '';
    handleAuthSuccess(data);
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    setBusy(dom.btnLogin, false, 'Entrar');
  }
}

async function submitRegister(event) {
  event.preventDefault();
  const payload = {
    display_name: dom.registerName.value.trim(),
    username: dom.registerUser.value.trim(),
    email: dom.registerEmail.value.trim(),
    password: dom.registerPassword.value,
  };
  if (!payload.username || !payload.email || !payload.password) {
    toast('Completa usuario, correo y contraseña.', 'error');
    return;
  }
  setBusy(dom.btnRegister, true, 'Creando…');
  try {
    const data = await api('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    dom.registerPassword.value = '';
    handleAuthSuccess(data);
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    setBusy(dom.btnRegister, false, 'Crear cuenta');
  }
}

async function logout() {
  try {
    await api('/api/auth/logout', { method: 'POST' });
  } catch {
    /* ignore network errors */
  }
  state.auth.user = null;
  state.auth.csrf = null;
  state.auth.favorites = [];
  state.auth.preferences = {};
  resetAssistant();
  renderAccount();
  renderFavorites();
  renderAssistantPanel();
  toast('Sesión cerrada.', 'success');
}

function isFavorite(datasetId) {
  return Boolean(datasetId) && (state.auth.favorites || []).includes(datasetId);
}

function updateFavoriteButton() {
  if (!dom.btnFavorite) return;
  const id = state.dataset ? state.dataset.id : null;
  const saved = isFavorite(id);
  dom.btnFavorite.classList.toggle('on', saved);
  dom.btnFavorite.disabled = !state.auth.user || !id;
  dom.btnFavorite.title = !state.auth.user
    ? 'Inicia sesión para guardar favoritos'
    : saved ? 'Quitar de favoritos' : 'Guardar el campo abierto en favoritos';
  dom.favoriteBtnLabel.textContent = saved ? 'Guardado' : 'Guardar';
  dom.favoriteCurrentLabel.textContent = state.dataset ? state.dataset.label : 'Sin campo abierto';
}

function renderFavorites() {
  updateFavoriteButton();
  if (!dom.favoriteList) return;
  if (!state.auth.user) {
    dom.favoriteList.innerHTML = '<div class="hint">Inicia sesión para guardar y recuperar campos favoritos.</div>';
    return;
  }
  const ids = state.auth.favorites || [];
  if (!ids.length) {
    dom.favoriteList.innerHTML = '<div class="hint">Aún no hay favoritos. Abre un campo y pulsa Guardar.</div>';
    return;
  }
  dom.favoriteList.innerHTML = ids.map((id) => {
    const dataset = state.datasets.find((item) => item.id === id);
    const label = dataset ? dataset.label : id;
    const kind = dataset ? dataset.kind : 'missing';
    const kindLabel = kind === 'missing' ? 'no disponible'
      : kind === 'archive' ? 'IRSA' : kind === 'upload' ? 'propio' : 'demo';
    const active = state.dataset && state.dataset.id === id;
    return `<button class="favorite-chip${active ? ' active' : ''}" data-favorite="${escapeHtml(id)}" type="button" title="${escapeHtml(id)}">
      <svg class="icon"><use href="#i-star"/></svg>
      <span>${escapeHtml(label)}</span>
      <em>${kindLabel}</em>
    </button>`;
  }).join('');
}

async function toggleFavorite() {
  if (!state.auth.user) {
    openPanel('account');
    toast('Inicia sesión para guardar favoritos.', 'error');
    return;
  }
  if (!state.dataset) return;
  const id = state.dataset.id;
  dom.btnFavorite.disabled = true;
  try {
    const data = await authedApi(`/api/favorites/${encodeURIComponent(id)}`, { method: 'POST' });
    state.auth.favorites = data.ids || [];
    if (state.auth.user.stats) state.auth.user.stats.favorites = state.auth.favorites.length;
    renderFavorites();
    renderAccount();
    toast(data.favorite ? 'Campo guardado en favoritos.' : 'Campo eliminado de favoritos.', 'success');
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    dom.btnFavorite.disabled = false;
  }
}

/* --------------------------------------------------------------------------
   Assistant
   -------------------------------------------------------------------------- */
async function refreshAssistantStatus() {
  try {
    const data = await api('/api/assistant/status');
    state.ai = {
      configured: Boolean(data.configured),
      mode: data.mode || 'local',
      model: data.model || null,
      provider: data.provider || null,
      topics: data.topics || [],
      tools: data.tools || [],
      knowledge: data.knowledge || null,
      classifier: data.classifier || null,
      training: false,
    };
  } catch {
    state.ai = {
      configured: false,
      mode: 'local',
      topics: [],
      tools: [],
      knowledge: null,
      classifier: null,
      training: false,
    };
  }
  if (state.auth.user) await loadAssistantConversations();
  else renderAssistantPanel();
}

async function loadAssistantConversations() {
  if (!state.auth.user) {
    state.assistant.conversations = [];
    renderAssistantPanel();
    return;
  }
  try {
    const data = await authedApi('/api/assistant/conversations');
    state.assistant.conversations = data.conversations || [];
  } catch {
    state.assistant.conversations = [];
  }
  renderAssistantPanel();
}

function resetAssistant() {
  if (state.assistant.controller) {
    try { state.assistant.controller.abort(); } catch { /* ignore */ }
  }
  state.assistant = {
    conversations: [],
    conversationId: null,
    messages: [],
    streaming: false,
    controller: null,
    answer: '',
    renderQueued: false,
  };
}

function aiModeLabel(ai = {}) {
  return ai.mode === 'llm'
    ? `Modelo ${ai.model || 'LLM'} · ${ai.provider || 'API'}`
    : 'Modo local · sin clave de API';
}

function renderAssistantPanel() {
  const user = state.auth.user;
  if (dom.assistantMain) {
    dom.assistantGate.hidden = Boolean(user);
    dom.assistantMain.hidden = !user;
    dom.assistantMode.textContent = aiModeLabel(state.ai || {});
    if (!user) {
      dom.assistantConversations.innerHTML = '';
    } else {
      renderAssistantConversations();
      renderAssistantTopics();
    }
  }
  renderAiWorkspace();
  updateAssistantControls();
}

function renderAssistantConversations() {
  const items = state.assistant.conversations || [];
  const current = state.assistant.conversationId;
  if (dom.assistantConversations) {
    dom.assistantConversations.innerHTML = '<option value="">Nueva conversación</option>'
      + items.map((conversation) => {
        const title = conversation.title || 'Conversación';
        return `<option value="${conversation.id}"${conversation.id === current ? ' selected' : ''}>${escapeHtml(title)} · ${formatDate(conversation.updated_at)}</option>`;
      }).join('');
    dom.assistantConversations.value = current ? String(current) : '';
  }
  if (!dom.aiwConversations) return;
  if (!items.length) {
    dom.aiwConversations.innerHTML = '<div class="aiw-side-empty">Aún no hay conversaciones. Empieza con una pregunta o abre una de las sugerencias.</div>';
    return;
  }
  dom.aiwConversations.innerHTML = items.map((conversation) => {
    const title = conversation.title || 'Conversación';
    const active = conversation.id === current ? ' active' : '';
    return `<div class="aiw-conv-row${active}">
      <button class="aiw-conv" data-conversation="${conversation.id}" type="button">
        <span class="aiw-conv-title">${escapeHtml(title)}</span>
        <span class="aiw-conv-date">${formatDate(conversation.updated_at)}</span>
      </button>
      <button class="aiw-conv-del" data-delete-conversation="${conversation.id}" type="button" title="Eliminar conversación" aria-label="Eliminar conversación"><svg class="icon"><use href="#i-trash"/></svg></button>
    </div>`;
  }).join('');
}

function renderAssistantTopics() {
  if (!dom.assistantTopics) return;
  if ((state.assistant.messages || []).length) {
    dom.assistantTopics.innerHTML = '';
    return;
  }
  dom.assistantTopics.innerHTML = (state.ai.topics || []).map((topic, index) => `
    <button class="topic-chip" data-topic="${index}" type="button">${escapeHtml(topic.label)}</button>
  `).join('');
}

function renderAssistantMessages() {
  const messages = state.assistant.messages || [];
  const panelEmpty = `
    <div class="assistant-empty">
      <svg class="icon"><use href="#i-agent"/></svg>
      <div class="assistant-empty-title">Pregunta sobre el campo abierto</div>
      <div class="assistant-empty-text">Puedo revisar la cobertura de canales, la calidad, los espectros y los candidatos en movimiento con las herramientas del visor.</div>
    </div>`;
  const workspaceEmpty = `
    <div class="aiw-empty">
      <svg class="icon"><use href="#i-agent"/></svg>
      <div class="aiw-empty-title">Conversa, estudia y analiza</div>
      <div class="aiw-empty-text">Pregunta libremente sobre SPHEREx, prepara el desafío o pide ayuda para interpretar los cambios entre épocas. El campo abierto viaja como contexto en cada consulta.</div>
      <div class="aiw-empty-groups">
        ${AI_SHORTCUTS.map((group) => `
          <div class="aiw-empty-group">
            <h4>${escapeHtml(group.title)}</h4>
            ${group.items.map((item) => `<button data-prompt="${escapeHtml(item.prompt)}" type="button">${escapeHtml(item.label)}</button>`).join('')}
          </div>`).join('')}
      </div>
    </div>`;
  const body = messages.length
    ? messages.map((message) => messageHtml(message.role, message.content)).join('')
    : null;
  if (dom.assistantMessages) {
    dom.assistantMessages.innerHTML = body ?? panelEmpty;
  }
  if (dom.aiwMessages) {
    dom.aiwMessages.innerHTML = body ?? workspaceEmpty;
  }
  scrollAssistant();
}

function messageHtml(role, content) {
  const body = role === 'assistant'
    ? renderMarkdown(content || '')
    : `<p>${escapeHtml(content || '').replace(/\n/g, '<br>')}</p>`;
  return `<div class="assistant-msg ${role === 'user' ? 'user' : 'assistant'}">
    <div class="assistant-msg-role">${role === 'user' ? 'Tú' : 'Asistente'}</div>
    <div class="assistant-msg-body">${body}</div>
  </div>`;
}

function appendAssistantMessage(role, content = '') {
  const message = { role, content };
  state.assistant.messages.push(message);
  const bodies = [];
  const containers = [dom.assistantMessages, dom.aiwMessages].filter(Boolean);
  for (const container of containers) {
    const wrapper = document.createElement('div');
    wrapper.className = `assistant-msg ${role === 'user' ? 'user' : 'assistant'}`;
    wrapper.innerHTML = `<div class="assistant-msg-role">${role === 'user' ? 'Tú' : 'Asistente'}</div>
      <div class="assistant-msg-body"></div>`;
    const body = $('.assistant-msg-body', wrapper);
    body.innerHTML = role === 'assistant'
      ? renderMarkdown(content)
      : `<p>${escapeHtml(content).replace(/\n/g, '<br>')}</p>`;
    const empty = $('.assistant-empty, .aiw-empty', container);
    if (empty) empty.remove();
    container.appendChild(wrapper);
    bodies.push(body);
  }
  scrollAssistant();
  return { message, bodies, body: bodies[0] || null };
}

function setAssistantStatus(label) {
  if (dom.assistantStatus) {
    dom.assistantStatus.hidden = !label;
    if (label) dom.assistantStatusText.textContent = label;
  }
  if (dom.aiwStatus) {
    dom.aiwStatus.hidden = !label;
    if (label) dom.aiwStatusText.textContent = label;
  }
}

function scrollAssistant() {
  requestAnimationFrame(() => {
    [dom.assistantMessages, dom.aiwMessages].forEach((container) => {
      if (container) container.scrollTop = container.scrollHeight;
    });
  });
}

function updateAssistantControls() {
  const busy = state.assistant.streaming;
  const user = Boolean(state.auth.user);
  const hasConversation = Boolean(state.assistant.conversationId);
  if (dom.assistantSend) {
    dom.assistantSend.disabled = busy || !user;
    dom.assistantSend.classList.toggle('busy', busy);
  }
  if (dom.assistantInput) dom.assistantInput.disabled = !user;
  if (dom.assistantNew) dom.assistantNew.disabled = busy || !user;
  if (dom.assistantDelete) dom.assistantDelete.disabled = busy || !hasConversation;
  if (dom.assistantConversations) dom.assistantConversations.disabled = busy || !user;
  if (dom.assistantStop) dom.assistantStop.hidden = !busy;
  if (dom.aiwSend) {
    dom.aiwSend.disabled = busy || !user;
    dom.aiwSend.classList.toggle('busy', busy);
  }
  if (dom.aiwInput) dom.aiwInput.disabled = !user;
  if (dom.aiwStop) dom.aiwStop.hidden = !busy;
  document.querySelectorAll('[data-aiw-new]').forEach((button) => {
    button.disabled = busy || !user;
  });
}

function autoGrowAssistantInput(input = dom.assistantInput) {
  if (!input) return;
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, input === dom.aiwInput ? 180 : 120)}px`;
}

function parseSSE(chunk) {
  const data = chunk
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trim())
    .join('\n');
  if (!data) return null;
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}

function focusAssistantInput() {
  const input = state.workspace ? dom.aiwInput : dom.assistantInput;
  if (input) input.focus();
}

function newConversation() {
  if (state.assistant.streaming) return;
  state.assistant.conversationId = null;
  state.assistant.messages = [];
  state.assistant.answer = '';
  setAssistantStatus(null);
  renderAssistantPanel();
  focusAssistantInput();
}

async function openConversation(id) {
  if (!state.auth.user || state.assistant.streaming) return;
  if (!id) {
    newConversation();
    return;
  }
  try {
    const data = await authedApi(`/api/assistant/conversations/${id}`);
    state.assistant.conversationId = data.conversation.id;
    state.assistant.messages = (data.messages || []).map((message) => ({
      role: message.role,
      content: message.content,
    }));
    renderAssistantPanel();
    focusAssistantInput();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function deleteConversation(id = state.assistant.conversationId) {
  if (!id) {
    newConversation();
    return;
  }
  if (!window.confirm('¿Eliminar esta conversación y su historial?')) return;
  try {
    await authedApi(`/api/assistant/conversations/${id}`, { method: 'DELETE' });
    if (state.assistant.conversationId === id) {
      state.assistant.conversationId = null;
      state.assistant.messages = [];
    }
    await loadAssistantConversations();
    renderAssistantPanel();
    toast('Conversación eliminada.', 'success');
  } catch (error) {
    toast(error.message, 'error');
  }
}

function stopAssistant() {
  if (!state.assistant.streaming) return;
  if (state.assistant.controller) {
    try { state.assistant.controller.abort(); } catch { /* ignore */ }
    return;
  }
  state.assistant.streaming = false;
  updateAssistantControls();
}

async function sendAssistantMessage(text) {
  const message = String(text || '').trim();
  if (!message) return;
  if (!state.auth.user) {
    closeAiWorkspace();
    openPanel('account');
    toast('Inicia sesión para usar el asistente.', 'error');
    return;
  }
  if (state.assistant.streaming) return;
  if (!state.auth.csrf) {
    toast('Recarga la página para renovar la sesión.', 'error');
    return;
  }

  state.assistant.streaming = true;
  state.assistant.answer = '';
  updateAssistantControls();
  if (dom.assistantTopics) dom.assistantTopics.innerHTML = '';
  if (dom.aiwPrompts) dom.aiwPrompts.hidden = true;
  appendAssistantMessage('user', message);
  const target = appendAssistantMessage('assistant', '');
  target.bodies.forEach((body) => {
    body.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
  });
  if (dom.assistantInput) dom.assistantInput.value = '';
  if (dom.aiwInput) dom.aiwInput.value = '';
  autoGrowAssistantInput(dom.assistantInput);
  autoGrowAssistantInput(dom.aiwInput);
  setAssistantStatus('Preparando la consulta…');

  const controller = new AbortController();
  state.assistant.controller = controller;
  let answer = '';

  const paint = () => {
    const html = answer.trim()
      ? renderMarkdown(answer)
      : '<span class="typing"><i></i><i></i><i></i></span>';
    target.bodies.forEach((body) => { body.innerHTML = html; });
  };

  const scheduleRender = () => {
    if (state.assistant.renderQueued) return;
    state.assistant.renderQueued = true;
    requestAnimationFrame(() => {
      state.assistant.renderQueued = false;
      paint();
      scrollAssistant();
    });
  };

  try {
    const response = await fetch('/api/assistant/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': state.auth.csrf,
      },
      body: JSON.stringify({
        message,
        conversation_id: state.assistant.conversationId,
        dataset_id: state.dataset ? state.dataset.id : null,
      }),
      signal: controller.signal,
    });

    if (!response.ok) {
      let detail = `Error ${response.status}`;
      try {
        const body = await response.json();
        if (body && body.error) detail = body.error;
      } catch { /* not JSON */ }
      throw new Error(detail);
    }
    if (!response.body) throw new Error('El navegador no admite respuestas en streaming.');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let completed = false;
    while (!completed) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, '\n');
      let index;
      while ((index = buffer.indexOf('\n\n')) >= 0) {
        const chunk = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        const event = parseSSE(chunk);
        if (!event) continue;
        if (event.type === 'meta') {
          if (event.conversation_id) state.assistant.conversationId = event.conversation_id;
        } else if (event.type === 'delta') {
          answer += event.text || '';
          state.assistant.answer = answer;
          scheduleRender();
        } else if (event.type === 'tool') {
          setAssistantStatus(event.label || 'Consultando datos…');
        } else if (event.type === 'done') {
          if (event.conversation_id) state.assistant.conversationId = event.conversation_id;
          completed = true;
          break;
        } else if (event.type === 'error') {
          throw new Error(event.error || 'Error del asistente.');
        }
      }
    }
    try { await reader.cancel(); } catch { /* already closed */ }

    target.message.content = answer;
    const finalHtml = answer.trim()
      ? renderMarkdown(answer)
      : '<p class="assistant-note">Sin respuesta del asistente.</p>';
    target.bodies.forEach((body) => { body.innerHTML = finalHtml; });
    if (!answer.trim()) target.message.content = 'Sin respuesta del asistente.';
    setAssistantStatus(null);
    await loadAssistantConversations();
    try {
      const me = await api('/api/auth/me');
      if (me.user) {
        state.auth.user = me.user;
        renderAccount();
      }
    } catch { /* keep local state */ }
  } catch (error) {
    const aborted = error && error.name === 'AbortError';
    const html = aborted && answer.trim()
      ? `${renderMarkdown(answer)}<p class="assistant-note">Respuesta detenida.</p>`
      : `<p class="assistant-error">${escapeHtml(aborted ? 'Respuesta cancelada.' : (error.message || 'No se pudo completar la consulta.'))}</p>`;
    target.bodies.forEach((body) => { body.innerHTML = html; });
    target.message.content = aborted && answer.trim() ? answer : '';
    setAssistantStatus(null);
    if (!aborted) toast(error.message || 'Error del asistente.', 'error');
  } finally {
    state.assistant.streaming = false;
    state.assistant.controller = null;
    state.assistant.renderQueued = false;
    setAssistantStatus(null);
    updateAssistantControls();
    scrollAssistant();
  }
}

function sendTopic(index) {
  const topic = (state.ai.topics || [])[index];
  if (topic) sendAssistantMessage(topic.prompt);
}

function handleAssistantForm(event) {
  event.preventDefault();
  sendAssistantMessage(dom.assistantInput.value);
}

function handleWorkspaceForm(event) {
  event.preventDefault();
  sendAssistantMessage(dom.aiwInput.value);
}

function handleAssistantKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    if (state.assistant.streaming) return;
    sendAssistantMessage(event.target.value);
  }
}

/* --------------------------------------------------------------------------
   Full AI workspace
   -------------------------------------------------------------------------- */
function openAiWorkspace() {
  if (!dom.aiw) return;
  state.workspace = true;
  dom.aiw.hidden = false;
  if (state.panel) {
    state.panel = null;
    dom.panel.hidden = true;
    $$('.rail-btn').forEach((button) => button.classList.remove('active'));
  }
  renderAssistantPanel();
  resizeFrame();
  updateURL();
  requestAnimationFrame(() => {
    if (state.auth.user) focusAssistantInput();
    else if (dom.aiwLogin) dom.aiwLogin.focus();
  });
}

function closeAiWorkspace() {
  if (!dom.aiw) return;
  state.workspace = false;
  dom.aiw.hidden = true;
  updateURL();
}

function renderAiWorkspace() {
  if (!dom.aiw) return;
  const user = state.auth.user;
  if (dom.aiwMode) dom.aiwMode.textContent = aiModeLabel(state.ai || {});
  dom.aiwGate.hidden = Boolean(user);
  dom.aiwBody.hidden = !user;
  if (!user) {
    if (dom.aiwConversations) dom.aiwConversations.innerHTML = '';
    return;
  }
  renderAssistantConversations();
  renderAiContext();
  renderAiModel();
  renderAiShortcuts();
  renderAiTools();
  renderAiPrompts();
  if (!state.assistant.streaming) renderAssistantMessages();
  updateAssistantControls();
}

function renderAiContext() {
  if (!dom.aiwContext || !dom.aiwDataset) return;
  const dataset = state.dataset;
  if (!dataset) {
    dom.aiwContext.innerHTML = `
      <svg class="icon"><use href="#i-target"/></svg>
      <span>Sin campo abierto</span>
      <span class="aiw-context-meta">Abre un campo para dar contexto al asistente.</span>`;
    dom.aiwDataset.innerHTML = '<div class="aiw-tools-empty">Abre un campo desde Buscar o Capas para que el asistente reciba coordenadas, épocas y cobertura.</div>';
    return;
  }
  const epochs = dataset.epochs || [];
  const epoch = epochs[state.epoch] || epochs[0] || {};
  const coverage = dataset.band_coverage || [];
  const covered = coverage.filter((channel) => channel.covered).length;
  const first = (epochs[0] && epochs[0].date) || '—';
  const last = (epochs[epochs.length - 1] && epochs[epochs.length - 1].date) || '—';
  const label = dataset.label || dataset.id;
  const analyzePrompt = `Analiza los cambios de imagen entre la primera época (${first}) y la última época (${last}) del campo abierto. Indícame qué patrones buscar, qué significaría cada uno y cómo verificarlo con las herramientas del visor.`;
  dom.aiwContext.innerHTML = `
    <svg class="icon"><use href="#i-target"/></svg>
    <span>Campo abierto</span>
    <strong>${escapeHtml(label)}</strong>
    <span class="aiw-context-meta">RA ${fmtNum(dataset.ra, 3)}° · Dec ${fmtNum(dataset.dec, 3)}° · ${epochs.length} épocas</span>
    <span class="aiw-context-actions">
      <button class="aiw-chip strong" data-prompt="${escapeHtml(analyzePrompt)}" type="button">Analizar cambios</button>
      <button class="aiw-chip" data-ai-action="compare" type="button">Comparar épocas</button>
      <button class="aiw-chip" data-ai-action="diff" type="button">Ver diferencia</button>
    </span>`;
  dom.aiwDataset.innerHTML = `
    <div class="aiw-dataset-title">${escapeHtml(label)}</div>
    <span class="aiw-dataset-tag">${escapeHtml(dataset.tag || dataset.kind || 'campo')}</span>
    <div class="aiw-kv">
      <span class="k">RA / Dec</span><span class="v">${fmtNum(dataset.ra, 3)}° / ${fmtNum(dataset.dec, 3)}°</span>
      <span class="k">FOV</span><span class="v">${fmtNum(dataset.fov, 2)}°</span>
      <span class="k">Cuadrícula</span><span class="v">${dataset.npix || (dataset.grid && dataset.grid.npix) || '—'} px</span>
      <span class="k">Épocas</span><span class="v">${epochs.length}</span>
      <span class="k">Frames</span><span class="v">${dataset.n_frames || '—'}</span>
      <span class="k">Canales (ép. 0)</span><span class="v">${covered} / ${dataset.bands_total || 102}</span>
      <span class="k">Detectores</span><span class="v">${escapeHtml((epoch.detectors || []).join(' ') || '—')}</span>
    </div>
    <div class="aiw-actions">
      <button class="aiw-chip strong" data-prompt="${escapeHtml(analyzePrompt)}" type="button">Pedir análisis</button>
      <button class="aiw-chip" data-ai-action="spectrum" type="button">Abrir espectro</button>
    </div>`;
}

function renderAiShortcuts() {
  if (!dom.aiwShortcuts) return;
  dom.aiwShortcuts.innerHTML = AI_SHORTCUTS.map((group) => `
    <div class="aiw-shortcut-group">
      <div class="aiw-shortcut-title">${escapeHtml(group.title)}</div>
      <div class="aiw-shortcut-list">
        ${group.items.map((item) => `<button class="aiw-shortcut" data-prompt="${escapeHtml(item.prompt)}" type="button">${escapeHtml(item.label)}</button>`).join('')}
      </div>
    </div>`).join('');
}

function renderAiModel() {
  if (!dom.aiwModel) return;
  const index = state.ai.knowledge || {};
  const cat = index.catalog || {};
  const kb = index.knowledge || {};
  const clf = state.ai.classifier || {};
  if (!cat.rows && !kb.documents && !clf.available) {
    dom.aiwModel.innerHTML = '<div class="aiw-tools-empty">El índice local y el clasificador se cargan al iniciar el servidor. Si no aparecen, revisa <code>instance/catalog.db</code>.</div>';
    return;
  }
  const mjd = cat.mjd_min != null && cat.mjd_max != null
    ? `${fmtNum(cat.mjd_min, 0)}–${fmtNum(cat.mjd_max, 0)}`
    : '—';
  const kbChars = kb.characters ? `${(kb.characters / 1024).toFixed(0)} KB` : '—';
  const trained = clf.trained_at ? String(clf.trained_at).slice(0, 10) : '—';
  const accuracy = clf.available && clf.accuracy != null
    ? `${fmtNum(clf.accuracy * 100, 0)} %`
    : '—';
  const samples = clf.available
    ? `${clf.n_train != null ? clf.n_train : '—'} / ${clf.n_test != null ? clf.n_test : '—'}`
    : '—';
  const nClasses = (clf.classes || []).length || 4;
  const explainPrompt = '¿Con qué datos SPHEREx, Gaia y WISE está entrenado el asistente? Cita las cifras del índice local y del clasificador, y explica sus límites.';
  dom.aiwModel.innerHTML = `
    <div class="aiw-kv">
      <span class="k">Registros SPHEREx</span><span class="v">${cat.rows != null ? cat.rows : '—'}</span>
      <span class="k">Observaciones</span><span class="v">${cat.observations != null ? cat.observations : '—'}</span>
      <span class="k">Bandas</span><span class="v">${cat.bands != null ? cat.bands : '—'}</span>
      <span class="k">MJD</span><span class="v">${mjd}</span>
      <span class="k">Documentos</span><span class="v">${kb.documents != null ? kb.documents : '—'}</span>
      <span class="k">Texto indexado</span><span class="v">${kbChars}</span>
      <span class="k">Clasificador</span><span class="v">${clf.available ? `v${escapeHtml(clf.version || '1')}` : 'sin entrenar'}</span>
      <span class="k">Exactitud prueba</span><span class="v">${accuracy}</span>
      <span class="k">Muestras train/test</span><span class="v">${samples}</span>
      <span class="k">Entrenado</span><span class="v">${trained}</span>
    </div>
    <div class="aiw-model-note">${nClasses} clases · etiquetas demo y Gaia/WISE. Ayuda al cribado, no confirmación física.</div>
    <div class="aiw-actions">
      <button class="aiw-chip" data-ai-action="retrain" type="button" ${state.ai.training ? 'disabled' : ''}>${state.ai.training ? 'Entrenando…' : 'Reentrenar modelo'}</button>
      <button class="aiw-chip" data-prompt="${escapeHtml(explainPrompt)}" type="button">Explicar datos</button>
    </div>`;
}

async function retrainModel() {
  if (state.ai.training) return;
  if (!state.auth.user) {
    toast('Inicia sesión para reentrenar el clasificador.', 'error');
    return;
  }
  state.ai.training = true;
  renderAiModel();
  try {
    await authedApi('/api/model/train', {
      method: 'POST',
      body: JSON.stringify({ include_archive: true }),
    });
    await refreshAssistantStatus();
    toast('Clasificador reentrenado con datos demo y Gaia/WISE.', 'success');
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    state.ai.training = false;
    renderAiModel();
  }
}

function renderAiTools() {
  if (!dom.aiwTools) return;
  const tools = state.ai.tools || [];
  if (!tools.length) {
    dom.aiwTools.innerHTML = '<div class="aiw-tools-empty">Modo local: el asistente usa la base de conocimiento y los metadatos del campo sin llamar a herramientas externas.</div>';
    return;
  }
  dom.aiwTools.innerHTML = tools.map((name) => `
    <div class="aiw-tool">
      <span class="aiw-tool-dot"></span>
      <div>
        <div class="aiw-tool-name">${escapeHtml(AI_TOOL_LABELS[name] || name)}</div>
        <code>${escapeHtml(name)}</code>
      </div>
    </div>`).join('');
}

function renderAiPrompts() {
  if (!dom.aiwPrompts) return;
  const hasMessages = (state.assistant.messages || []).length > 0;
  dom.aiwPrompts.hidden = hasMessages || state.assistant.streaming;
  if (dom.aiwPrompts.hidden) {
    dom.aiwPrompts.innerHTML = '';
    return;
  }
  dom.aiwPrompts.innerHTML = AI_QUICK_PROMPTS.map((item) => `
    <button class="topic-chip" data-prompt="${escapeHtml(item.prompt)}" type="button">${escapeHtml(item.label)}</button>
  `).join('');
}

function handleAiAction(action) {
  if (!action) return;
  if (action === 'compare') {
    closeAiWorkspace();
    openPanel('compare');
  } else if (action === 'diff') {
    closeAiWorkspace();
    setMode('diff');
    openPanel('layers');
  } else if (action === 'spectrum') {
    closeAiWorkspace();
    openPanel('spectrum');
  } else if (action === 'retrain') {
    retrainModel();
  }
}

/* --------------------------------------------------------------------------
   URL state
   -------------------------------------------------------------------------- */
async function restoreFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const datasetId = params.get('dataset');
  const ra = params.get('ra');
  const dec = params.get('dec');
  const name = params.get('name');

  if (datasetId) {
    try {
      await selectDataset(datasetId, {
        epoch: params.get('epoch'),
        mode: params.get('mode') || 'image',
        band: params.get('band'),
      });
    } catch {
      /* fall through to default */
    }
  }

  if (!state.dataset) {
    const savedId = state.auth.preferences ? state.auth.preferences.dataset : null;
    const preferred = (savedId && state.datasets.find((d) => d.id === savedId))
      || state.datasets.find((d) => d.kind === 'archive')
      || state.datasets.find((d) => d.id === 'demo_neo_asteroid')
      || state.datasets[0];
    if (preferred) await selectDataset(preferred.id);
  }

  if (ra || dec || name) {
    if (ra) dom.searchRa.value = ra;
    if (dec) dom.searchDec.value = dec;
    if (name) dom.searchName.value = name;
    if (params.get('fov')) dom.searchFov.value = params.get('fov');
    if (!datasetId) {
      openPanel('search');
      queryArchive();
    } else if (ra && dec) {
      state.lastCoord = { ra: Number(ra), dec: Number(dec) };
      updateCoordReadout();
      drawOverlay();
      updateURL();
    }
  }

  if (params.get('panel')) openPanel(params.get('panel'));
  if (params.get('ai') === '1') openAiWorkspace();
  if (params.get('mode') === 'band' && params.get('band') != null && state.dataset) {
    state.mode = 'band';
    state.bandIndex = Number(params.get('band'));
    loadImage();
    renderTimeline();
    if (state.panel === 'bands') loadBands();
  }
}

function updateURL() {
  const params = new URLSearchParams();
  if (state.dataset) {
    params.set('dataset', state.dataset.id);
    params.set('epoch', String(state.epoch));
  }
  if (state.mode !== 'image') params.set('mode', state.mode);
  if (state.mode === 'band' && state.bandIndex != null) params.set('band', String(state.bandIndex));
  if (state.panel) params.set('panel', state.panel);
  if (state.workspace) params.set('ai', '1');
  if (state.lastCoord) {
    params.set('ra', state.lastCoord.ra.toFixed(5));
    params.set('dec', state.lastCoord.dec.toFixed(5));
  }
  const query = params.toString();
  history.replaceState(null, '', query ? `?${query}` : window.location.pathname);
}

/* --------------------------------------------------------------------------
   Dataset selection & rendering
   -------------------------------------------------------------------------- */
async function selectDataset(datasetId, options = {}) {
  if (!datasetId) return;
  try {
    const detail = await api(`/api/datasets/${encodeURIComponent(datasetId)}`);
    state.dataset = detail;
    const count = detail.epochs.length;
    state.epoch = clamp(parseInt(options.epoch ?? '0', 10) || 0, 0, Math.max(count - 1, 0));
    state.epochA = 0;
    state.epochB = count > 1 ? count - 1 : 0;
    state.mode = options.mode === 'diff' || options.mode === 'sum' || options.mode === 'band'
      ? options.mode
      : 'image';
    state.bandIndex = options.band != null ? Number(options.band) : null;
    if (state.mode === 'band' && state.bandIndex == null) state.mode = 'image';
    state.detector = 'all';
    state.zoom = 1;
    state.pan = { x: 0, y: 0 };
    state.candidates = [];
    state.selectedCandidate = null;
    state.detection = null;
    state.catalogs = { gaia: [], wise: [] };
    state.catalogOn = { gaia: false, wise: false };
    state.lastCoord = null;
    state.spectrum = null;
    state.spectrumGeom = null;
    state.bands = null;
    state.meta = null;
    dom.toggleGaia.checked = false;
    dom.toggleWise.checked = false;
    dom.gaiaCount.textContent = '';
    dom.wiseCount.textContent = '';
    dom.candidateList.innerHTML = '';
    dom.detectSummary.innerHTML = '';

    renderDatasetSelect();
    renderDemoList();
    syncLayerControls();
    renderEpochSelects();
    renderDetectorOptions();
    const preferredDetector = state.auth.user ? state.auth.preferences.detector : null;
    if (preferredDetector && state.dataset.epochs[state.epoch].detectors.includes(preferredDetector)) {
      state.detector = preferredDetector;
      renderDetectorOptions();
    }
    renderTimeline();
    resizeFrame();
    loadImage();
    updateCoordReadout();
    drawOverlay();
    updateURL();
    renderFavorites();
    savePreferences();
    if (state.panel) refreshPanelData(state.panel);
  } catch (error) {
    toast(error.message, 'error');
  }
}

function renderDetectorOptions() {
  const epoch = state.dataset ? state.dataset.epochs[state.epoch] : null;
  const detectors = epoch && epoch.detectors ? epoch.detectors : [];
  const options = ['<option value="all">Todos (coadd)</option>']
    .concat(detectors.map((d) => `<option value="${escapeHtml(d)}">${escapeHtml(d)}</option>`));
  dom.layerDetector.innerHTML = options.join('');
  dom.layerDetector.value = detectors.includes(state.detector) ? state.detector : 'all';
  state.detector = dom.layerDetector.value;
}

function renderEpochSelects() {
  if (!state.dataset) return;
  const options = state.dataset.epochs.map((epoch) =>
    `<option value="${epoch.index}">${escapeHtml(epoch.date || 'Época')} · ${escapeHtml((epoch.detectors || []).join(' ') || '—')}</option>`
  ).join('');
  for (const select of [dom.diffA, dom.diffB, dom.compareA, dom.compareB]) {
    select.innerHTML = options;
  }
  dom.diffA.value = String(state.epochA);
  dom.diffB.value = String(state.epochB);
  dom.compareA.value = String(state.epochA);
  dom.compareB.value = String(state.epochB);
}

function imageURL() {
  if (!state.dataset) return '';
  const base = `/api/datasets/${encodeURIComponent(state.dataset.id)}`;
  if (state.mode === 'diff') {
    return `${base}/diff.png?epoch_a=${state.epochA}&epoch_b=${state.epochB}&size=1024&scale_sigma=${state.diffScale}`;
  }
  if (state.mode === 'sum') {
    return `${base}/sum.png?epoch_a=${state.epochA}&epoch_b=${state.epochB}&cmap=${state.cmap}&stretch=${state.stretch}&size=1024`;
  }
  if (state.mode === 'band' && state.bandIndex != null) {
    return `${base}/band/${state.bandIndex}.png?epoch=${state.epoch}&cmap=${state.cmap}&stretch=${state.stretch}&size=1024`;
  }
  return `${base}/image.png?epoch=${state.epoch}&detector=${encodeURIComponent(state.detector)}&cmap=${state.cmap}&stretch=${state.stretch}&size=1024`;
}

function loadImage() {
  if (!state.dataset) return;
  const url = imageURL();
  const token = ++state.imageToken;
  const preload = new Image();
  preload.decoding = 'async';
  preload.onload = () => {
    if (token !== state.imageToken) return;
    dom.viewImage.src = url;
    dom.viewImage.classList.add('loaded');
  };
  preload.onerror = () => {
    if (token !== state.imageToken) return;
    dom.viewImage.classList.remove('loaded');
    toast('No se pudo generar la imagen para esta vista.', 'error');
  };
  preload.src = url;
}

function renderTimeline() {
  if (!state.dataset) {
    dom.timelineTicks.innerHTML = '';
    dom.timelineReadout.textContent = '—';
    return;
  }
  const epochs = state.dataset.epochs;
  dom.timelineTicks.innerHTML = epochs.map((epoch, index) => {
    const position = epochs.length === 1 ? 50 : 6 + (index / (epochs.length - 1)) * 88;
    const classes = ['tick'];
    if (index === state.epoch) classes.push('active');
    if (state.mode === 'diff' || state.mode === 'sum') {
      if (index === state.epochA) classes.push('epoch-a');
      if (index === state.epochB) classes.push('epoch-b');
    }
    return `<button class="${classes.join(' ')}" style="left:${position}%" data-epoch="${index}" type="button"
      title="${escapeHtml(epoch.date)} · ${escapeHtml((epoch.detectors || []).join(' '))}">
      <span class="tick-dot"></span></button>`;
  }).join('');

  if (state.mode === 'diff' || state.mode === 'sum') {
    const a = epochs[state.epochA];
    const b = epochs[state.epochB];
    const days = a && b ? Math.abs(Number(b.mjd) - Number(a.mjd)) : 0;
    dom.timelineReadout.textContent = state.mode === 'sum'
      ? `${a ? a.date : '—'} + ${b ? b.date : '—'} · coadd`
      : `${a ? a.date : '—'} → ${b ? b.date : '—'} · Δ ${days.toFixed(1)} d`;
  } else {
    const epoch = epochs[state.epoch];
    dom.timelineReadout.textContent = epoch
      ? `${epoch.date} · MJD ${Number(epoch.mjd).toFixed(2)}`
      : '—';
  }
  dom.playBtn.querySelector('use').setAttribute('href', state.playing ? '#i-pause' : '#i-play');
}

function setEpoch(index) {
  if (!state.dataset) return;
  const count = state.dataset.epochs.length;
  if (!count) return;
  index = clamp(index, 0, count - 1);
  if (state.mode === 'diff' || state.mode === 'sum') {
    state.epochB = index;
    if (state.epochB === state.epochA) {
      state.epochA = (index + 1) % count;
    }
    dom.diffB.value = String(state.epochB);
    dom.diffA.value = String(state.epochA);
    dom.compareB.value = String(state.epochB);
    dom.compareA.value = String(state.epochA);
  } else {
    state.epoch = index;
  }
  renderDetectorOptions();
  renderTimeline();
  loadImage();
  updateURL();
  if (state.panel === 'meta') loadMeta();
  if (state.panel === 'spectrum' && state.lastCoord) loadSpectrum(state.lastCoord.ra, state.lastCoord.dec);
  if (state.panel === 'bands') loadBands();
  if (state.panel === 'layers') loadStack();
}

/* --------------------------------------------------------------------------
   Panels
   -------------------------------------------------------------------------- */
const PANEL_TITLES = {
  search: 'Buscar campo',
  layers: 'Capas y visualización',
  compare: 'Comparar épocas',
  spectrum: 'Espectro',
  bands: '102 canales',
  meta: 'Metadatos',
  upload: 'Cargar archivo',
  assistant: 'Asistente SPHEREx',
  account: 'Cuenta',
};

function openPanel(name) {
  if (!name) return;
  if (state.workspace) closeAiWorkspace();
  if (name !== 'account') state.pendingWorkspace = false;
  state.panel = name;
  dom.panelTitle.textContent = PANEL_TITLES[name] || name;
  dom.panel.hidden = false;
  $$('.panel-section', dom.panel).forEach((section) => {
    section.hidden = section.dataset.panelContent !== name;
  });
  $$('.rail-btn').forEach((button) => {
    button.classList.toggle('active', button.dataset.panel === name);
  });
  resizeFrame();
  refreshPanelData(name);
  updateURL();
}

function closePanel() {
  state.panel = null;
  state.pendingWorkspace = false;
  dom.panel.hidden = true;
  $$('.rail-btn').forEach((button) => button.classList.remove('active'));
  resizeFrame();
  updateURL();
}

function refreshPanelData(name) {
  if (name === 'assistant') {
    renderAssistantPanel();
    return;
  }
  if (name === 'account') {
    renderAccount();
    return;
  }
  if (!state.dataset) return;
  if (name === 'search') renderDemoList();
  if (name === 'layers') {
    syncLayerControls();
    loadStack();
    renderFavorites();
  }
  if (name === 'compare') {
    renderEpochSelects();
    renderCandidates();
  }
  if (name === 'spectrum') {
    if (state.lastCoord) loadSpectrum(state.lastCoord.ra, state.lastCoord.dec);
    else loadSpectrum(state.dataset.ra, state.dataset.dec);
  }
  if (name === 'bands') loadBands();
  if (name === 'meta') loadMeta();
}

/* --------------------------------------------------------------------------
   Search panel
   -------------------------------------------------------------------------- */
async function resolveSearchPosition() {
  const name = dom.searchName.value.trim();
  let ra = dom.searchRa.value.trim();
  let dec = dom.searchDec.value.trim();
  if ((ra === '' || dec === '') && name) {
    const resolved = await api('/api/resolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    ra = String(resolved.ra);
    dec = String(resolved.dec);
    dom.searchRa.value = ra;
    dom.searchDec.value = dec;
    dom.searchName.value = resolved.name || name;
    toast(`Resuelto: ${resolved.name || name}`, 'success');
  }
  if (ra === '' || dec === '') throw new Error('Indica un nombre o coordenadas RA/Dec.');
  const raNum = Number(ra);
  const decNum = Number(dec);
  if (!Number.isFinite(raNum) || !Number.isFinite(decNum)) throw new Error('Coordenadas inválidas.');
  if (decNum < -90 || decNum > 90) throw new Error('La declinación debe estar entre −90° y +90°.');
  return { ra: ((raNum % 360) + 360) % 360, dec: decNum, name: dom.searchName.value.trim() || null };
}

async function queryArchive() {
  try {
    const position = await resolveSearchPosition();
    startLoading('Consultando el archivo', [
      'Consultando NASA/IPAC IRSA…',
      'Buscando observaciones SPHEREx level-2…',
      'Agrupando barridos en épocas…',
    ]);
    const data = await api('/api/archive/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(position),
    });
    stopLoading();
    state.query = data;
    renderQueryResult();
    toast(`${data.n_observations.toLocaleString('es')} observaciones en ${data.n_epochs} épocas`, 'success');
  } catch (error) {
    stopLoading();
    toast(error.message, 'error');
  }
}

function renderQueryResult() {
  const data = state.query;
  if (!data) {
    dom.queryResult.innerHTML = '';
    return;
  }
  const suggested = new Set((data.suggested && data.suggested.epoch_days) || []);
  let epochs = data.epochs.slice();
  if (epochs.length > 14) {
    const sorted = epochs.slice().sort((a, b) => b.n_detectors - a.n_detectors);
    const chosen = new Map();
    sorted.slice(0, 14).forEach((epoch) => chosen.set(epoch.day, epoch));
    suggested.forEach((day) => {
      const epoch = epochs.find((item) => item.day === day);
      if (epoch) chosen.set(day, epoch);
    });
    epochs = Array.from(chosen.values()).sort((a, b) => a.mjd - b.mjd);
  }

  const rows = epochs.map((epoch) => {
    const checked = suggested.has(epoch.day) ? ' checked' : '';
    return `<label class="epoch-row">
      <input type="checkbox" value="${epoch.day}"${checked}>
      <span class="epoch-date">${escapeHtml(epoch.date)}</span>
      <span class="epoch-det">${escapeHtml((epoch.detectors || []).join(' '))}</span>
      <span class="epoch-meta">${epoch.n_detectors} det.</span>
    </label>`;
  }).join('');

  dom.queryResult.innerHTML = `
    <div class="query-summary">
      <div class="qs-line"><span>Posición</span><strong>RA ${fmtNum(data.ra, 4)}° · Dec ${fmtNum(data.dec, 4)}°</strong></div>
      <div class="qs-line"><span>Observaciones</span><span>${data.n_observations.toLocaleString('es')}</span></div>
      <div class="qs-line"><span>Épocas</span><span>${data.n_epochs}</span></div>
      <div class="qs-line"><span>Rango</span><span>${data.mjd_range ? `${data.mjd_range[0].toFixed(1)} – ${data.mjd_range[1].toFixed(1)} MJD` : '—'}</span></div>
      <div class="qs-line"><span>Detectores</span><span>${escapeHtml((data.detectors || []).join(' '))}</span></div>
    </div>
    <div class="section-title">Épocas disponibles <span id="epoch-selection-count"></span></div>
    <div class="epoch-list">${rows || '<div class="hint" style="padding:10px">Sin épocas.</div>'}</div>
    <div class="hint">Marca las épocas a descargar. Si no marcas ninguna, se eligen automáticamente dos barridos separados en el tiempo.</div>
  `;
  updateEpochSelectionCount();
}

function updateEpochSelectionCount() {
  const checked = $$('#query-result .epoch-row input:checked').length;
  const label = $('#epoch-selection-count');
  if (label) label.textContent = checked ? `· ${checked} seleccionadas` : '';
}

async function loadArchive() {
  try {
    const position = await resolveSearchPosition();
    const fov = clamp(Number(dom.searchFov.value) || 0.35, 0.05, 2);
    const maxEpochs = clamp(Number(dom.searchEpochs.value) || 2, 1, 4);
    const maxDetectors = clamp(Number(dom.searchDetectors.value) || 4, 1, 6);
    const selectedDays = $$('#query-result .epoch-row input:checked').map((input) => Number(input.value));

    const payload = {
      ra: position.ra,
      dec: position.dec,
      name: position.name,
      fov,
      npix: 192,
      max_epochs: selectedDays.length || maxEpochs,
      max_detectors: maxDetectors,
    };
    if (selectedDays.length) payload.epoch_days = selectedDays;

    startLoading('Cargando datos reales', [
      'Consultando el archivo IRSA…',
      'Descargando recortes level-2…',
      'Reproyectando a una cuadrícula común…',
      'Coadicionando detectores por varianza inversa…',
    ]);
    const data = await api('/api/archive/load', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    stopLoading();
    await refreshDatasets();
    await selectDataset(data.dataset.id);
    closePanel();
    toast(`Campo real cargado · ${data.dataset.n_frames} frames en ${data.dataset.n_epochs} épocas`, 'success');
  } catch (error) {
    stopLoading();
    toast(error.message, 'error');
  }
}

/* --------------------------------------------------------------------------
   Layers panel
   -------------------------------------------------------------------------- */
function syncLayerControls() {
  if (!state.dataset) return;
  dom.layerDataset.value = state.dataset.id;
  dom.layerCmap.value = state.cmap;
  dom.layerStretch.value = state.stretch;
  dom.toggleGrid.checked = state.showGrid;
  dom.toggleCandidates.checked = state.showCandidates;
  dom.toggleGaia.checked = state.catalogOn.gaia;
  dom.toggleWise.checked = state.catalogOn.wise;
  dom.diffScale.value = String(state.diffScale);
  dom.diffScaleValue.textContent = `${state.diffScale.toFixed(1)}σ`;
  renderDetectorOptions();
  renderEpochSelects();
  $$('#view-mode button').forEach((button) => {
    button.classList.toggle('active', button.dataset.mode === state.mode);
  });
  dom.diffControls.hidden = !(state.mode === 'diff' || state.mode === 'sum');
  dom.diffScaleWrap.hidden = state.mode !== 'diff';
}

async function loadStack() {
  if (!state.dataset) return;
  try {
    const data = await api(`/api/datasets/${encodeURIComponent(state.dataset.id)}/stack?epoch=${state.epoch}`);
    state.stack = data;
    renderStack(data);
  } catch (error) {
    toast(error.message, 'error');
  }
}

function renderStack(data) {
  if (!dom.stackSummary) return;
  const rows = (data.detectors || []).map((detector) => `
    <div class="stack-row">
      <span class="stack-det">${escapeHtml(detector.detector)}</span>
      <span>${fmtNum(detector.sigma, 2)}</span>
      <span>${fmtNum(detector.median, 3)}</span>
      <span>${fmtNum(Number(detector.valid_fraction) * 100, 1)}%</span>
    </div>
  `).join('');
  dom.stackSummary.innerHTML = `
    <div class="stack-row head"><span>Det</span><span>Ruido</span><span>Mediana</span><span>Válido</span></div>
    ${rows || '<div class="hint">Sin detectores en esta época.</div>'}
    <div class="stack-gain">
      <span>${data.n_frames || 0} frames · varianza inversa</span>
      <strong>${fmtNum(data.snr_gain, 2)}× SNR</strong>
    </div>
  `;
}

function setMode(mode) {
  if (!state.dataset) return;
  if ((mode === 'diff' || mode === 'sum') && state.dataset.epochs.length < 2) {
    toast('Este campo solo tiene una época; no se pueden combinar épocas.', 'error');
    return;
  }
  state.mode = mode;
  state.bandIndex = null;
  if ((mode === 'diff' || mode === 'sum') && state.epochA === state.epochB) {
    state.epochB = state.epochA === 0 ? Math.min(1, state.dataset.epochs.length - 1) : 0;
  }
  syncLayerControls();
  renderTimeline();
  loadImage();
  drawOverlay();
  updateURL();
}

async function setCatalog(kind, enabled) {
  state.catalogOn[kind] = enabled;
  if (!enabled || !state.dataset) {
    drawOverlay();
    return;
  }
  if (state.catalogs[kind].length) {
    drawOverlay();
    return;
  }
  try {
    const data = await api(`/api/datasets/${encodeURIComponent(state.dataset.id)}/catalogs?catalog=${kind}&limit=800`);
    state.catalogs[kind] = data.sources || [];
    const countLabel = kind === 'gaia' ? dom.gaiaCount : dom.wiseCount;
    countLabel.textContent = `${data.count}`;
    toast(`${data.count} fuentes ${kind === 'gaia' ? 'Gaia DR2' : 'AllWISE'} en el campo`, 'success');
  } catch (error) {
    state.catalogOn[kind] = false;
    if (kind === 'gaia') dom.toggleGaia.checked = false;
    else dom.toggleWise.checked = false;
    toast(error.message, 'error');
  }
  drawOverlay();
}

/* --------------------------------------------------------------------------
   Compare & detection
   -------------------------------------------------------------------------- */
async function runDetection() {
  if (!state.dataset) return;
  if (state.dataset.epochs.length < 2) {
    toast('Se necesitan al menos dos épocas.', 'error');
    return;
  }
  const a = Number(dom.compareA.value);
  const b = Number(dom.compareB.value);
  if (a === b) {
    toast('Selecciona dos épocas distintas.', 'error');
    return;
  }
  state.epochA = a;
  state.epochB = b;
  const threshold = clamp(Number(dom.detectThreshold.value) || 6, 2, 12);
  dom.btnDetect.disabled = true;
  try {
    const data = await api(`/api/datasets/${encodeURIComponent(state.dataset.id)}/detect?epoch_a=${a}&epoch_b=${b}&threshold=${threshold}&catalog=1`);
    state.detection = data;
    state.candidates = data.candidates || [];
    state.selectedCandidate = null;
    state.mode = 'diff';
    syncLayerControls();
    renderTimeline();
    loadImage();
    renderCandidates(data);
    drawOverlay();
    updateURL();
    toast(`${data.total_candidates} candidatos · ${data.baseline_days} días de línea base`, data.total_candidates ? 'success' : '');
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    dom.btnDetect.disabled = false;
  }
}

function renderCandidates(data) {
  const list = state.candidates;
  if (!list.length) {
    dom.detectSummary.innerHTML = data && data.total_candidates === 0
      ? '<div class="hint">Sin candidatos por encima del umbral. Prueba un umbral menor.</div>'
      : '<div class="hint">Ejecuta el análisis para restar las dos épocas y buscar dipolos de movimiento, transitorios y variables.</div>';
    dom.candidateList.innerHTML = '';
    return;
  }
  const sigma = data ? data.noise_sigma : null;
  const baseline = data ? data.baseline_days : null;
  dom.detectSummary.innerHTML = `
    <div class="ds-grid">
      <div class="ds-item"><div class="ds-label">Candidatos</div><div class="ds-value">${list.length}</div></div>
      <div class="ds-item"><div class="ds-label">Ruido</div><div class="ds-value">${sigma != null ? fmtNum(sigma, 5) : '—'}</div></div>
      <div class="ds-item"><div class="ds-label">Línea base</div><div class="ds-value">${baseline != null ? `${fmtNum(baseline, 1)} d` : '—'}</div></div>
    </div>
    ${data && data.spectral_note ? `<div class="ds-note">${escapeHtml(data.spectral_note)}</div>` : ''}
    ${data && data.classifier && data.classifier.available ? `<div class="ds-note">Clasificador v${escapeHtml(data.classifier.version || '1')}: etiquetas de cribado, no confirmación física.</div>` : ''}`;
  dom.candidateList.innerHTML = list.map((candidate, index) => {
    const ml = candidate.ml && candidate.ml.available ? candidate.ml : null;
    const cat = candidate.catalog && candidate.catalog.available ? candidate.catalog : null;
    const mlBadge = ml
      ? `<div class="c-ml"><span class="c-ml-tag">${escapeHtml(ml.label_es || ml.label || 'Clasificado')}</span><span class="c-ml-conf">${fmtNum((ml.confidence || 0) * 100, 0)} %</span></div>`
      : '';
    const catBadge = cat
      ? `<div class="c-cat">${cat.has_counterpart ? 'Contrapartida Gaia/WISE' : 'Sin contrapartida Gaia/WISE'}</div>`
      : '';
    return `
    <button class="candidate${state.selectedCandidate === candidate ? ' active' : ''}" data-candidate="${index}" type="button">
      <div class="c-head"><span class="c-id">${escapeHtml(candidate.id)}</span><span class="c-snr">SNR ${fmtNum(candidate.snr, 1)}σ</span></div>
      <div class="c-type">${escapeHtml(candidate.type_label || candidate.type)}</div>
      ${mlBadge}
      <div class="c-note">${escapeHtml(candidate.note || '')}</div>
      ${candidate.motion ? `<div class="c-motion">${fmtNum(candidate.motion.arcsec, 1)}″ · ${fmtNum(candidate.motion.rate_arcsec_day, 2)}″/día · ${fmtNum(candidate.motion.angle_deg, 0)}°</div>` : ''}
      ${catBadge}
    </button>`;
  }).join('');
}

function focusCandidate(candidate) {
  state.selectedCandidate = candidate;
  if (candidate.position && candidate.position.ra != null) {
    state.lastCoord = { ra: candidate.position.ra, dec: candidate.position.dec };
  }
  if (candidate.position) focusPixel(candidate.position.x, candidate.position.y);
  drawOverlay();
  renderCandidates(state.detection);
  updateCoordReadout();
  if (state.panel === 'spectrum' && state.lastCoord) loadSpectrum(state.lastCoord.ra, state.lastCoord.dec);
}

function focusPixel(x, y) {
  if (!state.dataset) return;
  const npix = state.dataset.grid.npix || state.dataset.npix || 192;
  state.zoom = Math.max(state.zoom, 2.4);
  state.pan.x = -(x / npix - 0.5) * state.frameSize * state.zoom;
  state.pan.y = (y / npix - 0.5) * state.frameSize * state.zoom;
  applyTransform();
}

/* --------------------------------------------------------------------------
   Spectrum
   -------------------------------------------------------------------------- */
async function loadSpectrum(ra, dec) {
  if (!state.dataset) return;
  try {
    const data = await api(`/api/datasets/${encodeURIComponent(state.dataset.id)}/spectrum?ra=${ra}&dec=${dec}&epoch=${state.epoch}`);
    state.spectrum = data;
    state.lastCoord = { ra, dec };
    renderSpectrum(data);
    updateCoordReadout();
    drawOverlay();
    updateURL();
  } catch (error) {
    toast(error.message, 'error');
  }
}

function renderSpectrum(data) {
  const channels = data.channels || [];
  dom.spectrumCoord.textContent = `RA ${fmtNum(data.ra, 4)}° · Dec ${fmtNum(data.dec, 4)}°`;
  const kind = data.kind === 'demo' ? 'Demo · 102 canales sintéticos' : 'Archivo real · cobertura nativa';
  dom.spectrumSub.textContent = `${kind} · época ${data.epoch != null ? data.epoch : state.epoch}`;

  const detectors = new Set(channels.map((c) => c.detector));
  const fluxes = channels.map((c) => Number(c.flux)).filter(Number.isFinite);
  const median = fluxes.length ? fluxes.slice().sort((a, b) => a - b)[Math.floor(fluxes.length / 2)] : null;
  dom.spectrumStats.innerHTML = `
    <div class="stat"><div class="label">Cobertura</div><div class="value">${channels.length} / ${data.coverage_total || 102}</div></div>
    <div class="stat"><div class="label">Detectores</div><div class="value">${detectors.size || '—'}</div></div>
    <div class="stat"><div class="label">Flujo mediano</div><div class="value">${median != null ? fmtFlux(median) : '—'}</div></div>
    <div class="stat"><div class="label">Muestras</div><div class="value">${(data.native || []).length || channels.length}</div></div>
  `;

  const features = data.features || [];
  dom.featureList.innerHTML = features.map((feature) => `
    <div class="feature">
      <span class="name">${escapeHtml(feature.name)} <span>${escapeHtml(feature.formula)}</span></span>
      <span class="wl">${fmtNum(feature.wavelength_um, 2)} µm</span>
    </div>
  `).join('');

  drawSpectrumChart(data);
}

function drawSpectrumChart(data) {
  const canvas = dom.spectrumCanvas;
  const ctx = canvas.getContext('2d');
  const W = canvas.width;
  const H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  const channels = data.channels || [];
  if (!channels.length) {
    ctx.fillStyle = '#5c6877';
    ctx.font = '18px "IBM Plex Mono", monospace';
    ctx.textAlign = 'center';
    ctx.fillText('Sin cobertura espectral en este punto', W / 2, H / 2);
    state.spectrumGeom = null;
    return;
  }

  const margin = { left: 64, right: 22, top: 34, bottom: 48 };
  const x0 = margin.left;
  const x1 = W - margin.right;
  const y0 = H - margin.bottom;
  const y1 = margin.top;
  const xMin = 0.7;
  const xMax = 5.1;

  const fluxes = channels.map((c) => Number(c.flux)).filter(Number.isFinite);
  let fMin = Math.min(0, ...fluxes);
  let fMax = Math.max(0, ...fluxes);
  if (!Number.isFinite(fMin) || !Number.isFinite(fMax) || fMax - fMin < 1e-9) {
    fMin = -1;
    fMax = 1;
  }
  const pad = (fMax - fMin) * 0.12;
  fMin -= pad;
  fMax += pad;

  const sx = (w) => x0 + ((w - xMin) / (xMax - xMin)) * (x1 - x0);
  const sy = (f) => y0 - ((f - fMin) / (fMax - fMin)) * (y0 - y1);

  // Plot background
  ctx.fillStyle = '#0c0f13';
  ctx.fillRect(x0, y1, x1 - x0, y0 - y1);

  // Grid
  ctx.strokeStyle = '#232a33';
  ctx.lineWidth = 1;
  ctx.font = '16px "IBM Plex Mono", monospace';
  ctx.fillStyle = '#5c6877';
  ctx.textAlign = 'center';
  for (let wl = 1; wl <= 5; wl += 1) {
    const x = sx(wl);
    ctx.beginPath();
    ctx.moveTo(x, y1);
    ctx.lineTo(x, y0);
    ctx.stroke();
    ctx.fillText(`${wl}`, x, y0 + 24);
  }
  ctx.textAlign = 'right';
  for (let i = 0; i <= 4; i += 1) {
    const value = fMin + (i / 4) * (fMax - fMin);
    const y = sy(value);
    ctx.beginPath();
    ctx.moveTo(x0, y);
    ctx.lineTo(x1, y);
    ctx.stroke();
    ctx.fillText(Math.abs(value) >= 100 ? value.toExponential(0) : value.toFixed(3), x0 - 8, y + 5);
  }

  // Spectral feature markers
  ctx.setLineDash([5, 6]);
  ctx.strokeStyle = 'rgba(76, 141, 255, 0.32)';
  for (const feature of (data.features || [])) {
    const x = sx(feature.wavelength_um);
    if (x < x0 || x > x1) continue;
    ctx.beginPath();
    ctx.moveTo(x, y1);
    ctx.lineTo(x, y0);
    ctx.stroke();
  }
  ctx.setLineDash([]);

  // Spectrum line
  const sorted = channels.slice().sort((a, b) => a.index - b.index);
  ctx.strokeStyle = '#4c8dff';
  ctx.lineWidth = 2.6;
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  ctx.beginPath();
  let drawing = false;
  let previousIndex = null;
  for (const channel of sorted) {
    const x = sx(channel.wavelength_um);
    const y = sy(Number(channel.flux));
    if (!drawing || (previousIndex != null && channel.index !== previousIndex + 1)) {
      ctx.moveTo(x, y);
      drawing = true;
    } else {
      ctx.lineTo(x, y);
    }
    previousIndex = channel.index;
  }
  ctx.stroke();

  // Points
  ctx.fillStyle = '#8db5ff';
  for (const channel of sorted) {
    const x = sx(channel.wavelength_um);
    const y = sy(Number(channel.flux));
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fill();
  }

  // Axes labels
  ctx.fillStyle = '#8b97a6';
  ctx.font = '16px "IBM Plex Mono", monospace';
  ctx.textAlign = 'center';
  ctx.fillText('Longitud de onda (µm)', (x0 + x1) / 2, H - 10);
  ctx.save();
  ctx.translate(18, (y0 + y1) / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText('Flujo (MJy/sr)', 0, 0);
  ctx.restore();

  state.spectrumGeom = {
    x0, x1, y0, y1, sx, sy,
    channels: sorted,
    width: W,
    height: H,
  };
}

function handleChartMove(event) {
  const geom = state.spectrumGeom;
  if (!geom) return;
  const rect = dom.spectrumCanvas.getBoundingClientRect();
  const px = ((event.clientX - rect.left) / rect.width) * geom.width;
  const py = ((event.clientY - rect.top) / rect.height) * geom.height;
  let nearest = null;
  let best = Infinity;
  for (const channel of geom.channels) {
    const dx = geom.sx(channel.wavelength_um) - px;
    const dy = geom.sy(Number(channel.flux)) - py;
    const distance = dx * dx + dy * dy;
    if (distance < best) {
      best = distance;
      nearest = channel;
    }
  }
  if (!nearest || best > 60 * 60) {
    dom.chartTooltip.hidden = true;
    return;
  }
  dom.chartTooltip.hidden = false;
  dom.chartTooltip.textContent = `${fmtNum(nearest.wavelength_um, 3)} µm · ${fmtFlux(nearest.flux)} · ${nearest.detector}`;
  dom.chartTooltip.style.left = `${event.clientX - rect.left}px`;
  dom.chartTooltip.style.top = `${event.clientY - rect.top}px`;
}

function exportSpectrumCSV() {
  if (!state.spectrum || !state.spectrum.channels || !state.spectrum.channels.length) {
    toast('No hay espectro para exportar.', 'error');
    return;
  }
  const lines = ['channel,wavelength_um,flux,detector'];
  for (const channel of state.spectrum.channels) {
    lines.push(`${channel.channel},${channel.wavelength_um},${channel.flux},${channel.detector}`);
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${state.dataset ? state.dataset.id : 'spherex'}_spectrum.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

/* --------------------------------------------------------------------------
   Bands
   -------------------------------------------------------------------------- */
async function loadBands() {
  if (!state.dataset) return;
  try {
    const data = await api(`/api/datasets/${encodeURIComponent(state.dataset.id)}/bands?epoch=${state.epoch}`);
    state.bands = data;
    renderBands();
  } catch (error) {
    toast(error.message, 'error');
  }
}

function renderBands() {
  if (!state.dataset) return;
  const coverage = state.bands ? state.bands.channels : (state.dataset.band_coverage || []);
  const covered = coverage.filter((c) => c.covered).length;
  const epoch = state.dataset.epochs[state.epoch];
  dom.bandsSub.textContent = `${covered} / ${coverage.length || 102} canales · ${epoch ? epoch.date : ''}`;

  const detectors = state.dataset.detectors || [];
  const rows = detectors.map((detector) => {
    const channels = coverage.filter((c) => c.detector === detector.id);
    const cells = channels.map((channel) => {
      const classes = ['band-cell'];
      if (channel.covered) classes.push('covered');
      if (state.mode === 'band' && state.bandIndex === channel.index) classes.push('active');
      const title = channel.covered
        ? `Canal ${channel.channel} · ${fmtNum(channel.wavelength_um, 3)} µm · ${channel.detector} · ${channel.samples} px`
        : `Canal ${channel.channel} · ${fmtNum(channel.wavelength_um, 3)} µm · sin cobertura suficiente`;
      const disabled = channel.covered ? '' : ' disabled';
      return `<button class="${classes.join(' ')}" data-band="${channel.index}" title="${title}" type="button"${disabled}>${channel.channel}</button>`;
    }).join('');
    return `<div class="band-row"><span class="band-det">${escapeHtml(detector.id)}</span>${cells}</div>`;
  }).join('');
  dom.bandsGrid.innerHTML = rows;

  dom.bandsNote.textContent = state.dataset.kind === 'demo'
    ? 'Modo demostración: los 102 canales están disponibles para cada época.'
    : 'Los recortes level-2 cubren una porción estrecha del LVF. Un canal se marca cubierto si al menos el 0.5 % de la cuadrícula tiene datos; el resto se muestra en gris.';
}

function selectBand(index) {
  if (!state.dataset) return;
  state.mode = 'band';
  state.bandIndex = index;
  loadImage();
  renderTimeline();
  renderBands();
  updateURL();
}

function resetBandMode() {
  state.mode = 'image';
  state.bandIndex = null;
  loadImage();
  renderTimeline();
  renderBands();
  syncLayerControls();
  updateURL();
}

/* --------------------------------------------------------------------------
   Metadata
   -------------------------------------------------------------------------- */
async function loadMeta() {
  if (!state.dataset) return;
  try {
    const data = await api(`/api/datasets/${encodeURIComponent(state.dataset.id)}/meta?epoch=${state.epoch}`);
    state.meta = data;
    renderMeta(data);
  } catch (error) {
    toast(error.message, 'error');
  }
}

function renderMeta(data) {
  const dataset = data.dataset;
  const quality = data.quality || {};
  const source = data.source || {};
  const kind = dataset.kind;
  const badge = kind === 'archive'
    ? '<span class="badge real">Datos reales · IRSA</span>'
    : kind === 'demo'
      ? '<span class="badge demo">Demostración</span>'
      : '<span class="badge upload">Carga propia</span>';

  const contamination = Number(quality.contamination_index);
  const hasContamination = Number.isFinite(contamination);
  const barClass = contamination > 35 ? 'high' : contamination > 12 ? 'warn' : '';

  const kv = (key, value) => `<div class="kv"><span class="k">${key}</span><span class="v">${escapeHtml(value ?? '—')}</span></div>`;

  const frameRows = (data.frames || []).map((frame) => `
    <tr>
      <td>${frame.epoch}</td>
      <td>${escapeHtml(frame.detector)}</td>
      <td>${escapeHtml(frame.date || '—')}</td>
      <td>${frame.wave_range_um ? `${fmtNum(frame.wave_range_um[0], 3)}–${fmtNum(frame.wave_range_um[1], 3)}` : '—'}</td>
      <td>${frame.valid_fraction != null ? `${fmtNum(frame.valid_fraction * 100, 1)}%` : '—'}</td>
      <td>${frame.zodi_median != null ? fmtNum(frame.zodi_median, 3) : '—'}</td>
    </tr>
  `).join('');

  dom.metaContent.innerHTML = `
    <div class="meta-card">
      ${badge}
      <div class="meta-title">${escapeHtml(dataset.label)}</div>
      <div class="meta-desc">${escapeHtml(dataset.description || '')}</div>
    </div>

    <div class="meta-card">
      <div class="section-title">Campo</div>
      ${kv('RA', `${fmtNum(dataset.ra, 5)}°`)}
      ${kv('Dec', `${fmtNum(dataset.dec, 5)}°`)}
      ${kv('FOV', `${fmtNum(dataset.fov, 3)}°`)}
      ${kv('Píxel', `${fmtNum(dataset.pixel_scale, 2)}″`)}
      ${kv('Cuadrícula', `${dataset.npix} × ${dataset.npix}`)}
      ${kv('Épocas', dataset.n_epochs)}
      ${kv('Frames', dataset.n_frames)}
      ${kv('Cobertura', `${data.band_coverage.covered} / ${data.band_coverage.total} canales`)}
    </div>

    <div class="meta-card">
      <div class="section-title">Calidad de la época</div>
      <div class="quality-head">
        <div>
          <div class="quality-value">${hasContamination ? fmtNum(contamination, 1) : '—'}</div>
          <div class="quality-label">Índice de contaminación</div>
        </div>
        <span class="badge ${quality.contamination_flag === 'ALTA' ? 'real' : ''}">${escapeHtml(quality.contamination_flag || '—')}</span>
      </div>
      <div class="bar ${barClass}"><span style="width:${hasContamination ? clamp(contamination, 0, 100) : 0}%"></span></div>
      ${kv('Fondo mediano', fmtNum(quality.background_median, 5))}
      ${kv('Ruido de fondo', fmtNum(quality.background_sigma, 5))}
      ${kv('Flags de calidad', `${fmtNum(quality.flagged_fraction_pct, 2)}%`)}
      ${kv('Flags informativos', `${fmtNum(quality.informational_flag_pct, 2)}%`)}
      ${kv('Residuales altos', `${fmtNum(quality.cosmic_ray_proxy_pct, 2)}%`)}
      ${kv('Saturación', `${fmtNum(quality.saturation_proxy_pct, 2)}%`)}
      ${kv('Luz zodiacal', quality.zodiacal_level != null ? fmtNum(quality.zodiacal_level, 4) : '—')}
      ${kv('Ganancia SNR', `${fmtNum(quality.snr_gain, 2)}×`)}
      ${kv('Cobertura válida', `${fmtNum(quality.valid_fraction_pct, 1)}%`)}
    </div>

    <div class="meta-card">
      <div class="section-title">Procedencia</div>
      ${kv('Archivo', source.archive || '—')}
      ${kv('Servicio', source.service || '—')}
      ${kv('Consulta', source.query || '—')}
      ${kv('MJD', source.mjd_range ? `${fmtNum(source.mjd_range[0], 1)} – ${fmtNum(source.mjd_range[1], 1)}` : '—')}
      ${kv('Recorte', source.cutout_size_deg ? `${fmtNum(source.cutout_size_deg, 2)}°` : '—')}
      ${kv('Observaciones', source.n_observations ? Number(source.n_observations).toLocaleString('es') : '—')}
    </div>

    <div class="meta-card">
      <div class="section-title">Frames</div>
      <div class="table-wrap">
        <table class="frame-table">
          <thead><tr><th>Ép.</th><th>Det.</th><th>Fecha</th><th>µm</th><th>Válido</th><th>Zodi</th></tr></thead>
          <tbody>${frameRows || '<tr><td colspan="6">Sin frames.</td></tr>'}</tbody>
        </table>
      </div>
    </div>

    <a class="btn full" href="/api/datasets/${encodeURIComponent(dataset.id)}/export" download>Exportar informe JSON</a>
  `;
}

/* --------------------------------------------------------------------------
   Upload
   -------------------------------------------------------------------------- */
function setUploadFile(file) {
  state.uploadFile = file || null;
  dom.uploadFilename.textContent = file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB` : '';
  if (file && !dom.uploadLabel.value) {
    dom.uploadLabel.value = file.name.replace(/\.[^.]+$/, '');
  }
}

async function uploadFile() {
  if (!state.uploadFile) {
    toast('Selecciona un archivo primero.', 'error');
    return;
  }
  const form = new FormData();
  form.append('file', state.uploadFile);
  form.append('label', dom.uploadLabel.value || state.uploadFile.name);
  if (dom.uploadRa.value) form.append('ra', dom.uploadRa.value);
  if (dom.uploadDec.value) form.append('dec', dom.uploadDec.value);
  if (dom.uploadDate.value) form.append('date_obs', dom.uploadDate.value);

  startLoading('Procesando archivo', [
    'Leyendo el archivo…',
    'Detectando WCS y extensión de imagen…',
    'Reproyectando a una cuadrícula común…',
  ]);
  try {
    const data = await api('/api/upload', { method: 'POST', body: form });
    stopLoading();
    await refreshDatasets();
    await selectDataset(data.dataset.id);
    closePanel();
    toast('Archivo cargado y reproyectado.', 'success');
  } catch (error) {
    stopLoading();
    toast(error.message, 'error');
  }
}

/* --------------------------------------------------------------------------
   Coordinate mapping & overlay
   -------------------------------------------------------------------------- */
function pixelToWorld(x, y) {
  const grid = state.dataset ? state.dataset.grid : null;
  if (!grid) return null;
  const npix = Number(grid.npix) || 192;
  const fov = Number(grid.fov) || 0.2;
  const scale = fov / npix;
  const dx = (x - (npix / 2 + 0.5)) * scale;
  const dy = (y - (npix / 2 + 0.5)) * scale;
  const ra0 = (Number(grid.ra) * Math.PI) / 180;
  const dec0 = (Number(grid.dec) * Math.PI) / 180;
  const xi = (-dx * Math.PI) / 180;
  const eta = (dy * Math.PI) / 180;
  const rho = Math.hypot(xi, eta);
  let ra;
  let dec;
  if (rho < 1e-12) {
    ra = ra0;
    dec = dec0;
  } else {
    const c = Math.atan(rho);
    dec = Math.asin(Math.cos(c) * Math.sin(dec0) + (eta * Math.sin(c) * Math.cos(dec0)) / rho);
    ra = ra0 + Math.atan2(
      xi * Math.sin(c),
      rho * Math.cos(dec0) * Math.cos(c) - eta * Math.sin(dec0) * Math.sin(c)
    );
  }
  return {
    ra: ((ra * 180) / Math.PI + 360) % 360,
    dec: (dec * 180) / Math.PI,
  };
}

function worldToPixel(ra, dec) {
  const grid = state.dataset ? state.dataset.grid : null;
  if (!grid) return null;
  const npix = Number(grid.npix) || 192;
  const fov = Number(grid.fov) || 0.2;
  const scale = fov / npix;
  const ra0 = (Number(grid.ra) * Math.PI) / 180;
  const dec0 = (Number(grid.dec) * Math.PI) / 180;
  const raRad = (Number(ra) * Math.PI) / 180;
  const decRad = (Number(dec) * Math.PI) / 180;
  let dra = raRad - ra0;
  while (dra > Math.PI) dra -= 2 * Math.PI;
  while (dra < -Math.PI) dra += 2 * Math.PI;
  const cosc = Math.sin(dec0) * Math.sin(decRad)
    + Math.cos(dec0) * Math.cos(decRad) * Math.cos(dra);
  if (!Number.isFinite(cosc) || cosc <= 1e-6) return null;
  const xi = (Math.cos(decRad) * Math.sin(dra)) / cosc;
  const eta = (Math.cos(dec0) * Math.sin(decRad)
    - Math.sin(dec0) * Math.cos(decRad) * Math.cos(dra)) / cosc;
  const dx = (-xi * 180) / Math.PI;
  const dy = (eta * 180) / Math.PI;
  return {
    x: dx / scale + npix / 2 + 0.5,
    y: dy / scale + npix / 2 + 0.5,
  };
}

function eventToPixel(event) {
  const rect = dom.viewImage.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const fx = (event.clientX - rect.left) / rect.width;
  const fy = (event.clientY - rect.top) / rect.height;
  if (fx < 0 || fx > 1 || fy < 0 || fy > 1) return null;
  const npix = state.dataset ? (state.dataset.grid.npix || state.dataset.npix || 192) : 192;
  // North is up: screen top corresponds to the last FITS row.
  return { x: fx * npix, y: (1 - fy) * npix };
}

function updateCoordReadout(coord) {
  if (!state.dataset) return;
  const point = coord || state.lastCoord || { ra: state.dataset.ra, dec: state.dataset.dec };
  dom.coordRa.textContent = `RA ${fmtNum(point.ra, 4)}°`;
  dom.coordDec.textContent = `Dec ${fmtNum(point.dec, 4)}°`;
  dom.coordScale.textContent = `FOV ${fmtNum(state.dataset.fov, 3)}° · ${fmtNum(state.dataset.pixel_scale, 2)}″/px`;
}

function applyTransform() {
  const maxPan = (state.frameSize * (state.zoom - 1)) / 2;
  state.pan.x = clamp(state.pan.x, -maxPan, maxPan);
  state.pan.y = clamp(state.pan.y, -maxPan, maxPan);
  dom.imageFrame.style.transform = `translate(${state.pan.x}px, ${state.pan.y}px) scale(${state.zoom})`;
}

function zoomBy(factor) {
  const next = clamp(state.zoom * factor, 1, 10);
  if (next === state.zoom) return;
  state.zoom = next;
  if (state.zoom === 1) state.pan = { x: 0, y: 0 };
  applyTransform();
}

function resizeFrame() {
  if (!dom.stage) return;
  const panelOffset = state.panel && window.innerWidth > 720 ? 410 : 0;
  if (dom.viewport) dom.viewport.style.right = `${panelOffset}px`;
  const availableWidth = Math.max(240, dom.stage.clientWidth - panelOffset - 110);
  const availableHeight = Math.max(240, dom.stage.clientHeight - 96);
  const size = Math.max(220, Math.min(availableWidth, availableHeight) * 0.94);
  state.frameSize = size;
  dom.imageFrame.style.width = `${size}px`;
  dom.imageFrame.style.height = `${size}px`;
  applyTransform();
  drawOverlay();
}

function drawOverlay() {
  const ctx = dom.overlayCtx;
  const canvas = dom.overlay;
  if (!ctx || !canvas) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!state.dataset) return;

  const npix = state.dataset.grid.npix || state.dataset.npix || 192;
  const fov = state.dataset.grid.fov || state.dataset.fov || 0.2;
  const toX = (value) => (value / npix) * canvas.width;
  const toY = (value) => (1 - value / npix) * canvas.height;

  if (state.showGrid) {
    const step = niceStep(fov / 4);
    const stepPix = step / (fov / npix);
    const center = npix / 2 + 0.5;
    const limit = Math.ceil(npix / Math.max(stepPix, 1)) + 1;

    ctx.save();
    ctx.strokeStyle = 'rgba(231, 236, 242, 0.13)';
    ctx.lineWidth = 1;
    ctx.font = '16px "IBM Plex Mono", monospace';
    ctx.fillStyle = 'rgba(231, 236, 242, 0.42)';

    for (let k = -limit; k <= limit; k += 1) {
      const x = center + k * stepPix;
      if (x < 0 || x > npix) continue;
      const cx = toX(x);
      ctx.beginPath();
      ctx.moveTo(cx, 0);
      ctx.lineTo(cx, canvas.height);
      ctx.stroke();
      const world = pixelToWorld(x, npix);
      if (world) {
        ctx.textAlign = 'center';
        ctx.fillText(`${world.ra.toFixed(2)}°`, cx, 22);
      }
    }
    for (let k = -limit; k <= limit; k += 1) {
      const y = center + k * stepPix;
      if (y < 0 || y > npix) continue;
      const cy = toY(y);
      ctx.beginPath();
      ctx.moveTo(0, cy);
      ctx.lineTo(canvas.width, cy);
      ctx.stroke();
      const world = pixelToWorld(0, y);
      if (world) {
        ctx.textAlign = 'left';
        ctx.fillText(`${world.dec.toFixed(2)}°`, 8, cy - 6);
      }
    }

    // Scale bar
    const barDeg = niceStep(fov / 5);
    const barPx = (barDeg / fov) * canvas.width;
    const bx = 30;
    const by = canvas.height - 30;
    ctx.strokeStyle = 'rgba(231, 236, 242, 0.55)';
    ctx.fillStyle = 'rgba(231, 236, 242, 0.65)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(bx, by);
    ctx.lineTo(bx + barPx, by);
    ctx.moveTo(bx, by - 5);
    ctx.lineTo(bx, by + 5);
    ctx.moveTo(bx + barPx, by - 5);
    ctx.lineTo(bx + barPx, by + 5);
    ctx.stroke();
    ctx.textAlign = 'center';
    ctx.fillText(fmtAngle(barDeg), bx + barPx / 2, by - 10);
    ctx.restore();
  }

  // Catalog overlays
  drawCatalog(ctx, 'gaia', '#4c8dff', 'circle', toX, toY, npix);
  drawCatalog(ctx, 'wise', '#d8b25a', 'diamond', toX, toY, npix);

  // Candidates
  if (state.showCandidates) {
    ctx.save();
    ctx.font = '15px "IBM Plex Mono", monospace';
    for (const candidate of state.candidates) {
      if (!candidate.position) continue;
      const cx = toX(candidate.position.x);
      const cy = toY(candidate.position.y);
      const selected = state.selectedCandidate === candidate;
      ctx.strokeStyle = selected ? '#ffffff' : '#4c8dff';
      ctx.fillStyle = 'rgba(76, 141, 255, 0.16)';
      ctx.lineWidth = selected ? 2.5 : 1.8;
      ctx.beginPath();
      ctx.arc(cx, cy, selected ? 11 : 7, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();

      if (candidate.motion && candidate.position_a) {
        const ax = toX(candidate.position_a.x);
        const ay = toY(candidate.position_a.y);
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        ctx.lineTo(cx, cy);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(ax, ay, 3, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(76, 141, 255, 0.55)';
        ctx.fill();
      }

      ctx.fillStyle = selected ? '#ffffff' : '#9dc0ff';
      ctx.textAlign = 'left';
      ctx.fillText(candidate.id || '', cx + 14, cy - 8);
    }
    ctx.restore();
  }

  // Last spectrum position
  if (state.lastCoord) {
    const pixel = worldToPixel(state.lastCoord.ra, state.lastCoord.dec);
    if (pixel && Number.isFinite(pixel.x) && Number.isFinite(pixel.y)
        && pixel.x >= 0 && pixel.x <= npix && pixel.y >= 0 && pixel.y <= npix) {
      const cx = toX(pixel.x);
      const cy = toY(pixel.y);
      ctx.save();
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.75)';
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.moveTo(cx - 8, cy);
      ctx.lineTo(cx + 8, cy);
      ctx.moveTo(cx, cy - 8);
      ctx.lineTo(cx, cy + 8);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(cx, cy, 5, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
  }
}

function drawCatalog(ctx, kind, color, shape, toX, toY, npix) {
  if (!state.catalogOn[kind]) return;
  const sources = state.catalogs[kind];
  if (!sources || !sources.length) return;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.globalAlpha = 0.82;
  ctx.lineWidth = 1.4;
  for (const source of sources) {
    const cx = toX(source.x);
    const cy = toY(source.y);
    ctx.beginPath();
    if (shape === 'diamond') {
      ctx.moveTo(cx, cy - 4);
      ctx.lineTo(cx + 4, cy);
      ctx.lineTo(cx, cy + 4);
      ctx.lineTo(cx - 4, cy);
      ctx.closePath();
    } else {
      ctx.arc(cx, cy, 3.6, 0, Math.PI * 2);
    }
    ctx.stroke();
  }
  ctx.restore();
}

/* --------------------------------------------------------------------------
   Interaction
   -------------------------------------------------------------------------- */
function handlePointerDown(event) {
  if (event.button !== 0) return;
  state.dragging = true;
  state.dragMoved = false;
  state.dragStart = { x: event.clientX, y: event.clientY };
  state.panStart = { ...state.pan };
  dom.viewport.classList.add('dragging');
  dom.viewport.setPointerCapture(event.pointerId);
}

function handlePointerMove(event) {
  if (state.dragging) {
    const dx = event.clientX - state.dragStart.x;
    const dy = event.clientY - state.dragStart.y;
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) state.dragMoved = true;
    state.pan.x = state.panStart.x + dx;
    state.pan.y = state.panStart.y + dy;
    applyTransform();
  }
  const pixel = eventToPixel(event);
  if (pixel) {
    const world = pixelToWorld(pixel.x, pixel.y);
    if (world) updateCoordReadout(world);
  }
}

function handlePointerUp(event) {
  if (!state.dragging) return;
  state.dragging = false;
  dom.viewport.classList.remove('dragging');
  try { dom.viewport.releasePointerCapture(event.pointerId); } catch { /* ignore */ }
  if (state.dragMoved) return;
  const pixel = eventToPixel(event);
  if (!pixel) return;
  const world = pixelToWorld(pixel.x, pixel.y);
  if (!world) return;
  state.lastCoord = world;
  if (state.panel !== 'spectrum') openPanel('spectrum');
  loadSpectrum(world.ra, world.dec);
}

function handleWheel(event) {
  event.preventDefault();
  zoomBy(event.deltaY < 0 ? 1.16 : 1 / 1.16);
}

function togglePlay() {
  if (state.playing) {
    clearInterval(state.playTimer);
    state.playing = false;
    state.playTimer = null;
  } else {
    if (!state.dataset || state.dataset.epochs.length < 2) {
      toast('Este campo solo tiene una época.', 'error');
      return;
    }
    state.playing = true;
    state.playTimer = setInterval(() => {
      if (!state.dataset) return;
      if (state.mode === 'diff' || state.mode === 'sum') {
        state.epochB = (state.epochB + 1) % state.dataset.epochs.length;
        if (state.epochB === state.epochA) state.epochB = (state.epochB + 1) % state.dataset.epochs.length;
        renderTimeline();
        loadImage();
      } else {
        state.epoch = (state.epoch + 1) % state.dataset.epochs.length;
        renderTimeline();
        renderDetectorOptions();
        loadImage();
      }
    }, 1100);
  }
  renderTimeline();
}

/* --------------------------------------------------------------------------
   Feedback
   -------------------------------------------------------------------------- */
function toast(message, kind = '') {
  clearTimeout(state.toastTimer);
  dom.toast.textContent = message;
  dom.toast.className = `toast ${kind}`.trim();
  dom.toast.hidden = false;
  state.toastTimer = setTimeout(() => { dom.toast.hidden = true; }, 4200);
}

function startLoading(title, messages) {
  stopLoading();
  dom.loadingTitle.textContent = title;
  dom.loadingText.textContent = messages[0];
  dom.loading.hidden = false;
  let index = 0;
  state.loadingTimer = setInterval(() => {
    index = (index + 1) % messages.length;
    dom.loadingText.textContent = messages[index];
  }, 4200);
}

function stopLoading() {
  clearInterval(state.loadingTimer);
  state.loadingTimer = null;
  dom.loading.hidden = true;
}

/* --------------------------------------------------------------------------
   Event binding
   -------------------------------------------------------------------------- */
function bindEvents() {
  dom.searchForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const value = dom.searchInput.value.trim();
    if (!value) {
      openPanel('search');
      return;
    }
    const numbers = value.replace(/,/g, ' ').split(/\s+/).filter(Boolean).map(Number);
    if (numbers.length === 2 && numbers.every(Number.isFinite)) {
      dom.searchRa.value = numbers[0];
      dom.searchDec.value = numbers[1];
      dom.searchName.value = '';
    } else {
      dom.searchName.value = value;
      dom.searchRa.value = '';
      dom.searchDec.value = '';
    }
    dom.searchInput.value = '';
    openPanel('search');
    queryArchive();
  });

  dom.statusChip.addEventListener('click', () => openPanel('search'));
  dom.btnAbout.addEventListener('click', () => openPanel('meta'));
  dom.btnAccount.addEventListener('click', () => openPanel('account'));

  dom.accountTabs.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-auth-tab]');
    if (button) setAuthTab(button.dataset.authTab);
  });
  dom.loginForm.addEventListener('submit', submitLogin);
  dom.registerForm.addEventListener('submit', submitRegister);
  dom.btnLogout.addEventListener('click', logout);
  dom.btnFavorite.addEventListener('click', toggleFavorite);
  dom.favoriteList.addEventListener('click', (event) => {
    const chip = event.target.closest('[data-favorite]');
    if (chip) selectDataset(chip.dataset.favorite);
  });

  dom.assistantNew.addEventListener('click', newConversation);
  dom.assistantDelete.addEventListener('click', () => deleteConversation());
  dom.assistantConversations.addEventListener('change', () => openConversation(dom.assistantConversations.value));
  dom.assistantForm.addEventListener('submit', handleAssistantForm);
  dom.assistantInput.addEventListener('keydown', handleAssistantKeydown);
  dom.assistantInput.addEventListener('input', () => autoGrowAssistantInput(dom.assistantInput));
  dom.assistantStop.addEventListener('click', stopAssistant);
  dom.assistantExpand.addEventListener('click', openAiWorkspace);
  dom.assistantTopics.addEventListener('click', (event) => {
    const chip = event.target.closest('[data-topic]');
    if (chip) sendTopic(Number(chip.dataset.topic));
  });
  dom.assistantLogin.addEventListener('click', () => openPanel('account'));

  dom.btnAiWorkspace.addEventListener('click', () => {
    if (state.workspace) closeAiWorkspace();
    else openAiWorkspace();
  });
  dom.aiwClose.addEventListener('click', closeAiWorkspace);
  dom.aiwLogin.addEventListener('click', () => {
    state.pendingWorkspace = true;
    closeAiWorkspace();
    openPanel('account');
  });
  dom.aiwForm.addEventListener('submit', handleWorkspaceForm);
  dom.aiwInput.addEventListener('keydown', handleAssistantKeydown);
  dom.aiwInput.addEventListener('input', () => autoGrowAssistantInput(dom.aiwInput));
  dom.aiwStop.addEventListener('click', stopAssistant);
  dom.aiwConversations.addEventListener('click', (event) => {
    const remove = event.target.closest('[data-delete-conversation]');
    if (remove) {
      deleteConversation(Number(remove.dataset.deleteConversation));
      return;
    }
    const item = event.target.closest('[data-conversation]');
    if (item) openConversation(Number(item.dataset.conversation));
  });
  dom.aiw.addEventListener('click', (event) => {
    const prompt = event.target.closest('[data-prompt]');
    if (prompt) {
      if (state.assistant.streaming) return;
      sendAssistantMessage(prompt.dataset.prompt);
      return;
    }
    const action = event.target.closest('[data-ai-action]');
    if (action) handleAiAction(action.dataset.aiAction);
  });
  $$('[data-aiw-new]').forEach((button) => {
    button.addEventListener('click', newConversation);
  });

  $$('.rail-btn[data-panel]').forEach((button) => {
    button.addEventListener('click', () => {
      if (state.panel === button.dataset.panel) closePanel();
      else openPanel(button.dataset.panel);
    });
  });
  dom.panelClose.addEventListener('click', closePanel);

  dom.btnQuery.addEventListener('click', queryArchive);
  dom.btnLoad.addEventListener('click', loadArchive);

  dom.demoList.addEventListener('click', (event) => {
    const card = event.target.closest('[data-dataset]');
    if (card) selectDataset(card.dataset.dataset);
  });

  dom.queryResult.addEventListener('change', (event) => {
    if (event.target.matches('input[type="checkbox"]')) updateEpochSelectionCount();
  });

  dom.layerDataset.addEventListener('change', () => selectDataset(dom.layerDataset.value));
  dom.layerCmap.addEventListener('change', () => {
    state.cmap = dom.layerCmap.value;
    loadImage();
    savePreferences();
  });
  dom.layerStretch.addEventListener('change', () => {
    state.stretch = dom.layerStretch.value;
    loadImage();
    savePreferences();
  });
  dom.layerDetector.addEventListener('change', () => {
    state.detector = dom.layerDetector.value;
    state.mode = 'image';
    syncLayerControls();
    loadImage();
    renderTimeline();
    updateURL();
    savePreferences();
  });

  dom.toggleGrid.addEventListener('change', () => {
    state.showGrid = dom.toggleGrid.checked;
    drawOverlay();
    savePreferences();
  });
  dom.toggleCandidates.addEventListener('change', () => {
    state.showCandidates = dom.toggleCandidates.checked;
    drawOverlay();
    savePreferences();
  });
  dom.toggleGaia.addEventListener('change', () => setCatalog('gaia', dom.toggleGaia.checked));
  dom.toggleWise.addEventListener('change', () => setCatalog('wise', dom.toggleWise.checked));

  dom.viewMode.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-mode]');
    if (button) setMode(button.dataset.mode);
  });

  dom.diffA.addEventListener('change', () => {
    state.epochA = Number(dom.diffA.value);
    if (state.epochA === state.epochB) {
      state.epochB = (state.epochA + 1) % state.dataset.epochs.length;
      dom.diffB.value = String(state.epochB);
      dom.compareB.value = String(state.epochB);
    }
    dom.compareA.value = String(state.epochA);
    renderTimeline();
    loadImage();
    updateURL();
  });
  dom.diffB.addEventListener('change', () => {
    state.epochB = Number(dom.diffB.value);
    if (state.epochB === state.epochA) {
      state.epochA = (state.epochB + 1) % state.dataset.epochs.length;
      dom.diffA.value = String(state.epochA);
      dom.compareA.value = String(state.epochA);
    }
    dom.compareB.value = String(state.epochB);
    renderTimeline();
    loadImage();
    updateURL();
  });

  const reloadDiff = debounce(() => loadImage(), 220);
  dom.diffScale.addEventListener('input', () => {
    state.diffScale = Number(dom.diffScale.value);
    dom.diffScaleValue.textContent = `${state.diffScale.toFixed(1)}σ`;
    reloadDiff();
  });

  dom.compareA.addEventListener('change', () => {
    state.epochA = Number(dom.compareA.value);
    if (state.epochA === state.epochB) {
      state.epochB = (state.epochA + 1) % state.dataset.epochs.length;
      dom.compareB.value = String(state.epochB);
      dom.diffB.value = String(state.epochB);
    }
    dom.diffA.value = String(state.epochA);
    renderTimeline();
    if (state.mode === 'diff' || state.mode === 'sum') loadImage();
    updateURL();
  });
  dom.compareB.addEventListener('change', () => {
    state.epochB = Number(dom.compareB.value);
    if (state.epochB === state.epochA) {
      state.epochA = (state.epochB + 1) % state.dataset.epochs.length;
      dom.compareA.value = String(state.epochA);
      dom.diffA.value = String(state.epochA);
    }
    dom.diffB.value = String(state.epochB);
    renderTimeline();
    if (state.mode === 'diff' || state.mode === 'sum') loadImage();
    updateURL();
  });
  dom.btnDetect.addEventListener('click', runDetection);
  dom.candidateList.addEventListener('click', (event) => {
    const card = event.target.closest('[data-candidate]');
    if (card) focusCandidate(state.candidates[Number(card.dataset.candidate)]);
  });

  dom.spectrumCsv.addEventListener('click', exportSpectrumCSV);
  dom.spectrumCanvas.addEventListener('mousemove', handleChartMove);
  dom.spectrumCanvas.addEventListener('mouseleave', () => { dom.chartTooltip.hidden = true; });

  dom.btnBandReset.addEventListener('click', resetBandMode);
  dom.bandsGrid.addEventListener('click', (event) => {
    const cell = event.target.closest('[data-band]');
    if (cell) selectBand(Number(cell.dataset.band));
  });

  dom.btnBrowse.addEventListener('click', () => dom.uploadFile.click());
  dom.dropzone.addEventListener('click', (event) => {
    if (event.target === dom.dropzone || event.target.closest('.drop-icon, .drop-title, .drop-sub')) {
      dom.uploadFile.click();
    }
  });
  dom.uploadFile.addEventListener('change', () => setUploadFile(dom.uploadFile.files[0]));
  ['dragenter', 'dragover'].forEach((type) => {
    dom.dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      dom.dropzone.classList.add('dragover');
    });
  });
  ['dragleave', 'drop'].forEach((type) => {
    dom.dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      dom.dropzone.classList.remove('dragover');
    });
  });
  dom.dropzone.addEventListener('drop', (event) => {
    if (event.dataTransfer.files.length) setUploadFile(event.dataTransfer.files[0]);
  });
  dom.btnUpload.addEventListener('click', uploadFile);

  dom.playBtn.addEventListener('click', togglePlay);
  dom.timelineTicks.addEventListener('click', (event) => {
    const tick = event.target.closest('[data-epoch]');
    if (tick) setEpoch(Number(tick.dataset.epoch));
  });

  dom.zoomIn.addEventListener('click', () => zoomBy(1.3));
  dom.zoomOut.addEventListener('click', () => zoomBy(1 / 1.3));
  dom.zoomReset.addEventListener('click', () => {
    state.zoom = 1;
    state.pan = { x: 0, y: 0 };
    applyTransform();
  });

  dom.viewport.addEventListener('pointerdown', handlePointerDown);
  dom.viewport.addEventListener('pointermove', handlePointerMove);
  dom.viewport.addEventListener('pointerup', handlePointerUp);
  dom.viewport.addEventListener('pointercancel', handlePointerUp);
  dom.viewport.addEventListener('wheel', handleWheel, { passive: false });
  dom.viewport.addEventListener('dblclick', (event) => {
    event.preventDefault();
    zoomBy(1.6);
  });

  window.addEventListener('resize', debounce(resizeFrame, 100));

  window.addEventListener('keydown', (event) => {
    const tag = (event.target.tagName || '').toLowerCase();
    const typing = tag === 'input' || tag === 'select' || tag === 'textarea';
    if (event.key === 'Escape') {
      if (state.workspace) closeAiWorkspace();
      else closePanel();
      return;
    }
    if (state.workspace) return;
    if (typing) return;
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      setEpoch(state.epoch - 1);
    } else if (event.key === 'ArrowRight') {
      event.preventDefault();
      setEpoch(state.epoch + 1);
    } else if (event.code === 'Space') {
      event.preventDefault();
      togglePlay();
    } else if (event.key === '+' || event.key === '=') {
      zoomBy(1.25);
    } else if (event.key === '-') {
      zoomBy(1 / 1.25);
    } else if (event.key === '0') {
      state.zoom = 1;
      state.pan = { x: 0, y: 0 };
      applyTransform();
    }
  });
}
