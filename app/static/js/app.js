import { api } from './api.js';

const MAX_SLOTS = 6;
const STORAGE_KEY = 'rtsp-studio-layout-v1';
const VIEWS = ['live', 'streams', 'recordings'];

const STREAM_STATUS = {
  online: { label: '在线', dot: 'bg-emerald-400' },
  offline: { label: '离线', dot: 'bg-rose-400' },
  checking: { label: '检测中', dot: 'bg-amber-400' },
  unknown: { label: '未检测', dot: 'bg-slate-500' },
};

const RECORDING_STATUS = {
  starting: { label: '启动中', tone: 'checking' },
  recording: { label: '录制中', tone: 'checking' },
  completed: { label: '已完成', tone: 'online' },
  failed: { label: '失败', tone: 'offline' },
  stopped: { label: '已停止', tone: 'unknown' },
};

const AVATAR_STYLES = [
  'from-brand-500/30 to-sky-500/10 text-brand-200 border-brand-400/20',
  'from-emerald-500/25 to-teal-500/10 text-emerald-200 border-emerald-400/20',
  'from-amber-500/25 to-orange-500/10 text-amber-200 border-amber-400/20',
  'from-fuchsia-500/20 to-violet-500/10 text-fuchsia-200 border-fuchsia-400/20',
];

const state = {
  streams: [],
  recordings: [],
  system: null,
  count: 1,
  view: 'live',
  slots: Array.from({ length: MAX_SLOTS }, (_, index) => ({
    index,
    streamId: null,
    hls: null,
    gen: 0,
    muted: true,
    pending: false,
    playing: false,
    connectTimer: null,
    lastErrorToastAt: 0,
    networkStrikes: 0,
  })),
  previousStreamStatuses: new Map(),
  previousRecordingStatuses: new Map(),
  recordingBusy: new Set(),
  checkingStreams: new Set(),
  checkingAll: false,
  openPicker: null,
  openPickerSignature: '',
  loading: true,
  dataInitialized: false,
  apiDown: false,
  layoutReady: false,
  pollTimer: null,
  savedSlots: Array(MAX_SLOTS).fill(null),
  editingId: null,
  formBusy: false,
  confirmResolver: null,
};

const dom = {};

function cacheDom() {
  const ids = [
    'server-pill',
    'server-pill-text',
    'preview-grid',
    'slot-template',
    'layout-switcher',
    'btn-add-stream',
    'btn-add-stream-2',
    'btn-empty-add',
    'btn-stop-all',
    'btn-check-all',
    'btn-refresh-recordings',
    'stat-total',
    'stat-total-hint',
    'stat-online',
    'stat-online-hint',
    'stat-recording',
    'stat-recording-hint',
    'stream-count',
    'streams-loading',
    'streams-empty',
    'streams-table-wrap',
    'streams-tbody',
    'recording-count',
    'recordings-loading',
    'recordings-empty',
    'recordings-table-wrap',
    'recordings-tbody',
    'recordings-path',
    'app-version',
    'stream-modal',
    'stream-modal-title',
    'stream-modal-subtitle',
    'stream-form',
    'stream-name',
    'stream-url',
    'stream-auto-reconnect',
    'stream-form-error',
    'stream-submit',
    'playback-modal',
    'playback-title',
    'playback-meta',
    'playback-video',
    'playback-download',
    'confirm-modal',
    'confirm-title',
    'confirm-message',
    'confirm-cancel',
    'confirm-ok',
    'toasts',
    'slot-picker-panel',
  ];
  for (const id of ids) {
    dom[toCamel(id)] = document.getElementById(id);
  }
  dom.streamSubmitSpinner = dom.streamSubmit.querySelector('[data-role="submit-spinner"]');
  dom.streamSubmitLabel = dom.streamSubmit.querySelector('[data-role="submit-label"]');
  dom.layoutButtons = [...dom.layoutSwitcher.querySelectorAll('[data-count]')];
}

function toCamel(value) {
  return value.replace(/-([a-z0-9])/g, (_, character) => character.toUpperCase());
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[char]);
}

function getStream(streamId) {
  return state.streams.find((stream) => stream.id === Number(streamId)) || null;
}

function getVisibleSlotEl(index) {
  return dom.previewGrid.querySelector(`[data-slot][data-index="${index}"]`);
}

function isVisibleSlot(index) {
  return index < state.count;
}

/* ------------------------------------------------------------------ boot */

async function bootstrap() {
  cacheDom();
  const saved = loadSavedLayout();
  state.count = saved.count;
  state.savedSlots = saved.slots;
  buildSlots();
  bindEvents();
  applyView(viewFromHash() || 'live');
  updateCountUI();
  updateSectionVisibility();

  const ok = await refreshData({ initial: true });
  if (ok) restoreSavedSlots();

  state.layoutReady = true;
  setLoading(false);
  persistLayout();
  schedulePoll();

  // Previews are heavy; only start them when the live tab is actually open.
  // Starting a preview can take a few seconds, so never hold the data tables
  // on a skeleton while FFmpeg warms up.
  if (ok && state.view === 'live') {
    await Promise.allSettled(
      state.slots
        .filter((slot) => isVisibleSlot(slot.index) && slot.streamId)
        .map((slot) => mountPreview(slot.index)),
    );
  }
}

/* ------------------------------------------------------------- tab views */

function viewFromHash() {
  const hash = (window.location.hash || '').replace(/^#/, '');
  return VIEWS.includes(hash) ? hash : null;
}

function applyView(view) {
  state.view = view;
  for (const button of document.querySelectorAll('[data-view]')) {
    button.setAttribute('aria-selected', String(button.dataset.view === view));
  }
  for (const panel of document.querySelectorAll('[data-view-panel]')) {
    panel.classList.toggle('hidden', panel.dataset.viewPanel !== view);
  }
}

function setView(next, { scroll = true, fromHash = false } = {}) {
  if (!VIEWS.includes(next)) return;
  const changed = state.view !== next;
  applyView(next);
  if (!fromHash) {
    window.history.replaceState(null, '', `#${next}`);
  }
  if (next === 'live') resumeLivePreviews();
  if (changed && scroll) {
    const panel = document.querySelector(`[data-view-panel="${next}"]`);
    if (!panel) return;
    const header = document.querySelector('header');
    const top = window.scrollY + panel.getBoundingClientRect().top
      - (header?.offsetHeight || 0) - 12;
    window.scrollTo({ top: Math.max(0, top), behavior: 'auto' });
  }
}

function resumeLivePreviews() {
  if (!state.dataInitialized) return;
  for (let index = 0; index < state.count; index += 1) {
    const slot = state.slots[index];
    if (!slot.streamId) continue;
    if (slot.hls) {
      const video = getVisibleSlotEl(index)?.querySelector('[data-role="video"]');
      video?.play?.().catch(() => {});
      continue;
    }
    if (!slot.pending) mountPreview(index);
  }
}

function loadSavedLayout() {
  const fallback = { count: 1, slots: Array(MAX_SLOTS).fill(null) };
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    const count = Math.min(MAX_SLOTS, Math.max(1, Number(parsed?.count) || 1));
    const slots = Array.from({ length: MAX_SLOTS }, (_, index) => {
      const id = Number(parsed?.slots?.[index]);
      return Number.isInteger(id) && id > 0 ? id : null;
    });
    return { count, slots };
  } catch {
    return fallback;
  }
}

function persistLayout() {
  if (!state.layoutReady) return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      count: state.count,
      slots: state.slots.map((slot) => slot.streamId),
    }));
  } catch {
    /* Private mode / storage full: layout persistence is optional. */
  }
}

function restoreSavedSlots() {
  for (let index = 0; index < state.count; index += 1) {
    const id = state.savedSlots[index];
    if (id && getStream(id)) state.slots[index].streamId = id;
  }
  updateAllSlots();
}

function buildSlots() {
  const fragment = document.createDocumentFragment();
  for (let index = 0; index < MAX_SLOTS; index += 1) {
    const node = dom.slotTemplate.content.firstElementChild.cloneNode(true);
    node.dataset.index = String(index);
    node.classList.toggle('hidden', index >= state.count);
    node.querySelector('[data-role="index"]').textContent = `CAM ${String(index + 1).padStart(2, '0')}`;
    const video = node.querySelector('[data-role="video"]');
    video.addEventListener('playing', () => {
      const slot = state.slots[index];
      if (video.dataset.gen !== String(slot.gen) || !slot.streamId) return;
      slot.playing = true;
      clearTimeout(slot.connectTimer);
      slot.connectTimer = null;
      setSlotVisual(index, 'playing');
    });
    video.addEventListener('error', () => {
      const slot = state.slots[index];
      if (video.dataset.gen !== String(slot.gen) || !slot.streamId) return;
      if (slot.hls) return;
      showSlotError(index, '视频加载失败，请重试或检查流地址。', true);
    });
    video.addEventListener('click', () => {
      if (!video.src && !video.currentSrc) return;
      if (video.paused) video.play().catch(() => {});
      else video.pause();
    });
    video.addEventListener('dblclick', () => toggleFullscreen(index));
    fragment.appendChild(node);
  }
  dom.previewGrid.appendChild(fragment);
}

function updateCountUI() {
  dom.previewGrid.dataset.count = String(state.count);
  for (const button of dom.layoutButtons) {
    button.setAttribute('aria-pressed', button.dataset.count === String(state.count) ? 'true' : 'false');
  }
  for (let index = 0; index < MAX_SLOTS; index += 1) {
    const element = getVisibleSlotEl(index);
    element?.classList.toggle('hidden', index >= state.count);
  }
}

function changeCount(nextCount) {
  const count = Math.min(MAX_SLOTS, Math.max(1, Number(nextCount)));
  if (count === state.count) return;
  closeAllPickers();
  if (count < state.count) {
    for (let index = count; index < state.count; index += 1) {
      releaseSlot(index, { stopServer: true });
    }
  }
  state.count = count;
  updateCountUI();
  updateAllSlots();
  persistLayout();
  if (count > 1) {
    for (let index = 0; index < count; index += 1) {
      if (state.slots[index].streamId && !state.slots[index].hls) {
        mountPreview(index);
      }
    }
  }
}

/* -------------------------------------------------------------- data sync */

async function refreshData({ initial = false } = {}) {
  try {
    const [streams, recordings, system] = await Promise.all([
      api.listStreams(),
      api.listRecordings(),
      api.systemInfo(),
    ]);
    state.system = system;
    applyStreams(streams, { initial });
    applyRecordings(recordings, { initial });
    applySystemInfo(system);
    updateStats();

    if (state.apiDown) {
      toast('后端连接已恢复', 'success');
    }
    state.apiDown = false;
    state.dataInitialized = true;
    setServerState('online', '服务在线');
    return true;
  } catch (error) {
    console.error('刷新数据失败', error);
    setServerState('error', '连接中断');
    if (!state.apiDown) {
      toast(error?.message || '无法连接后端服务', 'error', 6500);
    }
    state.apiDown = true;
    return false;
  }
}

function applyStreams(streams, { initial }) {
  if (state.dataInitialized) {
    for (const stream of streams) {
      const previous = state.previousStreamStatuses.get(stream.id);
      if (!previous || previous === stream.status) continue;
      if (previous === 'online' && stream.status === 'offline') {
        toast(`「${stream.name}」连接中断`, 'error');
      } else if (previous === 'offline' && stream.status === 'online') {
        toast(`「${stream.name}」已恢复连接`, 'success');
      }
    }
  }

  for (let index = 0; index < state.count; index += 1) {
    const slot = state.slots[index];
    if (slot.streamId && !streams.some((stream) => stream.id === slot.streamId)) {
      releaseSlot(index, { stopServer: false });
    }
  }

  state.streams = streams;
  state.previousStreamStatuses = new Map(streams.map((stream) => [stream.id, stream.status]));
  renderStreams();
  updateAllSlots();
  updateStats();
  updateSectionVisibility();
  if (!initial) {
    // A poll may reveal that a preview worker stopped unexpectedly.
    for (const slot of state.slots) {
      if (!isVisibleSlot(slot.index) || !slot.streamId || slot.pending) continue;
      const stream = getStream(slot.streamId);
      if (!stream) continue;
      const element = getVisibleSlotEl(slot.index);
      const video = element?.querySelector('[data-role="video"]');
      const currentTime = video?.currentTime || 0;
      const progressing = currentTime > (slot.lastVideoTime || 0) + 0.05;
      slot.lastVideoTime = currentTime;
      const errorOverlay = element?.querySelector('[data-role="error"]');
      if (stream.status === 'offline' && !progressing) {
        showSlotError(
          slot.index,
          stream.auto_reconnect
            ? '视频流连接中断，正在等待自动重连…'
            : '视频流连接中断，请检查视频源后重试。',
          false,
        );
      } else if (stream.status === 'online' && progressing && errorOverlay && !errorOverlay.classList.contains('hidden')) {
        setSlotVisual(slot.index, 'playing');
      }
      if (slot.hls && !stream.preview_active) {
        showSlotError(slot.index, '预览转码已停止，点击重新连接。', false);
      }
    }
  }
}

function applyRecordings(recordings, { initial }) {
  if (state.dataInitialized) {
    for (const recording of recordings) {
      const previous = state.previousRecordingStatuses.get(recording.id);
      if (!previous || previous === recording.status) continue;
      const wasActive = previous === 'recording' || previous === 'starting';
      if (wasActive && recording.status === 'failed') {
        toast(`录制失败：${recording.error_message || recording.filename}`, 'error', 7000);
      } else if (wasActive && recording.status === 'completed') {
        toast(`录制完成：${recording.filename}`, 'success');
      }
    }
  }
  state.recordings = recordings;
  state.previousRecordingStatuses = new Map(
    recordings.map((recording) => [recording.id, recording.status]),
  );
  renderRecordings();
  updateStats();
  updateSectionVisibility();
}

function applySystemInfo(system) {
  dom.recordingsPath.textContent = `保存至 ${system.recordings_path}`;
  dom.recordingsPath.title = system.recordings_path;
  dom.appVersion.textContent = `v${system.version}`;
}

function setServerState(status, label) {
  dom.serverPill.dataset.state = status;
  dom.serverPillText.textContent = label;
}

function updateStats() {
  const total = state.streams.length;
  const online = state.streams.filter((stream) => stream.status === 'online').length;
  const recordingFromStreams = state.streams.filter((stream) => stream.is_recording).length;
  const activeFiles = state.recordings.filter((recording) => (
    recording.status === 'recording' || recording.status === 'starting'
  )).length;
  const recordingCount = Math.max(recordingFromStreams, activeFiles);

  dom.statTotal.textContent = String(total);
  dom.statOnline.textContent = String(online);
  dom.statRecording.textContent = String(recordingCount);
  dom.statTotalHint.textContent = total ? '已配置' : '等待添加';
  dom.statOnlineHint.textContent = total ? `${Math.round((online / total) * 100)}% 在线` : '暂无流';
  dom.statRecordingHint.textContent = recordingCount ? `${recordingCount} 路写入中` : '空闲';
  dom.streamCount.textContent = String(total);
  dom.recordingCount.textContent = String(state.recordings.length);
}

/* ---------------------------------------------------------------- previews */

async function mountPreview(index) {
  const slot = state.slots[index];
  const element = getVisibleSlotEl(index);
  if (!element || !slot.streamId) return;
  const stream = getStream(slot.streamId);
  if (!stream) return;

  const gen = ++slot.gen;
  clearTimeout(slot.connectTimer);
  slot.connectTimer = null;
  slot.recoveryScheduled = false;
  destroyHls(slot, element);
  slot.pending = true;
  slot.playing = false;
  updateSlotChrome(index);
  setSlotVisual(index, 'connecting');

  try {
    const preview = await api.startPreview(stream.id);
    if (gen !== slot.gen || slot.streamId !== stream.id) return;

    // FFmpeg needs a few seconds to write the first HLS segment. Waiting for
    // a playable playlist here avoids hls.js treating the initial 404 as fatal.
    const ready = await waitForPreviewPlaylist(stream.id, gen, index);
    if (gen !== slot.gen || slot.streamId !== stream.id) return;
    slot.pending = false;
    updateSlotChrome(index);
    if (!ready) {
      showSlotError(index, '预览转码尚未就绪，请检查视频流状态后重试。', false);
      return;
    }
    attachHls(index, preview.preview_url, gen);
  } catch (error) {
    if (gen !== slot.gen || slot.streamId !== stream.id) return;
    slot.pending = false;
    updateSlotChrome(index);
    showSlotError(index, error?.message || '无法启动预览转码。', true);
  }
}

function schedulePreviewRecovery(index, gen) {
  const slot = state.slots[index];
  if (!slot) return;
  const stream = getStream(slot.streamId);
  if (!stream || !stream.auto_reconnect || slot.recoveryScheduled) return;
  slot.recoveryScheduled = true;
  void recoverPreview(index, gen);
}

async function recoverPreview(index, gen) {
  const initialSlot = state.slots[index];
  if (!initialSlot) return;
  const streamId = initialSlot.streamId;
  if (!streamId) {
    initialSlot.recoveryScheduled = false;
    return;
  }
  initialSlot.pending = true;
  updateSlotChrome(index);

  try {
    for (let attempt = 1; attempt <= 12; attempt += 1) {
      if (gen !== state.slots[index].gen || state.slots[index].streamId !== streamId) return;
      if (state.slots[index].playing) return;
      await new Promise((resolve) => window.setTimeout(resolve, attempt === 1 ? 1500 : 3000));
      if (gen !== state.slots[index].gen || state.slots[index].streamId !== streamId) return;

      try {
        await api.startPreview(streamId);
      } catch {
        continue;
      }
      if (gen !== state.slots[index].gen) return;
      const ready = await waitForPreviewPlaylist(streamId, gen, index, 10000);
      if (gen !== state.slots[index].gen) return;
      if (ready) {
        if (state.slots[index].playing) return;
        const slot = state.slots[index];
        if (slot.hls) {
          const staleHls = slot.hls;
          slot.hls = null;
          try { staleHls.destroy(); } catch { /* already destroyed */ }
        }
        setSlotVisual(index, 'connecting');
        attachHls(index, `/media/hls/${streamId}/index.m3u8`, gen);
        return;
      }
    }
    if (gen === state.slots[index].gen) {
      showSlotError(index, '自动重连失败，请检查视频流状态后手动重试。', false);
    }
  } finally {
    if (gen === state.slots[index].gen) {
      state.slots[index].recoveryScheduled = false;
      state.slots[index].pending = false;
      updateSlotChrome(index);
    }
  }
}

async function waitForPreviewPlaylist(streamId, gen, index, timeoutMs = 25000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (state.slots[index]?.gen !== gen) return false;
    try {
      const result = await api.previewReady(streamId);
      if (result?.ready) return true;
    } catch {
      /* Backend may still be starting; retry below. */
    }
    await new Promise((resolve) => window.setTimeout(resolve, 800));
  }
  return false;
}

function attachHls(index, previewUrl, gen) {
  const slot = state.slots[index];
  const element = getVisibleSlotEl(index);
  const video = element?.querySelector('[data-role="video"]');
  if (!video || gen !== slot.gen) return;

  const url = `${previewUrl}${previewUrl.includes('?') ? '&' : '?'}t=${Date.now()}`;
  video.dataset.gen = String(gen);
  video.muted = slot.muted;
  slot.networkStrikes = 0;
  slot.playing = false;

  if (window.Hls && window.Hls.isSupported()) {
    const hls = new window.Hls({
      enableWorker: true,
      lowLatencyMode: false,
      backBufferLength: 30,
      maxBufferLength: 20,
      manifestLoadingTimeOut: 15000,
      manifestLoadingMaxRetry: 20,
      manifestLoadingRetryDelay: 1000,
      levelLoadingMaxRetry: 20,
      levelLoadingRetryDelay: 1000,
      fragLoadingMaxRetry: 20,
      fragLoadingRetryDelay: 1000,
    });
    slot.hls = hls;

    hls.on(window.Hls.Events.MANIFEST_PARSED, () => {
      if (gen !== slot.gen) return;
      video.play().catch(() => {
        toast('浏览器阻止了自动播放，请点击画面开始播放', 'info');
      });
    });

    hls.on(window.Hls.Events.ERROR, (_event, data) => {
      if (!data?.fatal || gen !== slot.gen || slot.hls !== hls) return;
      if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR) {
        slot.networkStrikes += 1;
        if (slot.networkStrikes <= 4) {
          window.setTimeout(() => {
            if (gen === slot.gen && slot.hls === hls) {
              try { hls.startLoad(); } catch { /* destroyed */ }
            }
          }, Math.min(5000, 1200 * slot.networkStrikes));
        }
        showSlotError(index, '视频流暂时不可用，正在自动重试…', slot.networkStrikes === 1);
        schedulePreviewRecovery(index, gen);
      } else if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) {
        try { hls.recoverMediaError(); } catch { /* destroyed */ }
        showSlotError(index, '视频解码异常，正在恢复…', false);
      } else if (window.Hls.ErrorTypes.KEY_SYSTEM_ERROR === data.type) {
        showSlotError(index, '视频加密格式不受支持。', true);
      } else {
        showSlotError(index, '预览播放失败，请检查流地址或编码格式。', true);
      }
    });

    hls.loadSource(url);
    hls.attachMedia(video);
  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = url;
    video.addEventListener('loadedmetadata', () => {
      video.play().catch(() => {});
    }, { once: true });
    video.load();
  } else {
    showSlotError(index, '当前浏览器不支持 HLS 播放，请使用最新版 Chrome、Edge 或 Safari。', true);
    return;
  }

  clearTimeout(slot.connectTimer);
  slot.connectTimer = window.setTimeout(() => {
    if (gen !== slot.gen || slot.playing) return;
    showSlotError(index, '连接超时，请检查流是否在线或稍后重试。', false);
  }, 16000);
}

function destroyHls(slot, element) {
  clearTimeout(slot.connectTimer);
  slot.connectTimer = null;
  if (slot.hls) {
    const hls = slot.hls;
    slot.hls = null;
    try { hls.destroy(); } catch { /* already destroyed */ }
  }
  const video = element?.querySelector('[data-role="video"]');
  if (video) {
    video.dataset.gen = '';
    try { video.pause(); } catch { /* noop */ }
    video.removeAttribute('src');
    try { video.load(); } catch { /* noop */ }
  }
  slot.playing = false;
  slot.networkStrikes = 0;
}

function setSlotVisual(index, mode, message = '') {
  const element = getVisibleSlotEl(index);
  if (!element) return;
  const placeholder = element.querySelector('[data-role="placeholder"]');
  const connecting = element.querySelector('[data-role="connecting"]');
  const error = element.querySelector('[data-role="error"]');
  const live = element.querySelector('[data-role="live-pill"]');
  const errorText = element.querySelector('[data-role="error-text"]');

  placeholder.classList.toggle('hidden', mode !== 'empty');
  connecting.classList.toggle('hidden', mode !== 'connecting');
  error.classList.toggle('hidden', mode !== 'error');
  live.classList.remove('hidden');
  live.style.display = mode === 'playing' ? 'flex' : 'none';
  if (mode === 'error') {
    errorText.textContent = message || '视频加载失败';
    state.slots[index].playing = false;
  }
}

function showSlotError(index, message, notify) {
  const slot = state.slots[index];
  clearTimeout(slot.connectTimer);
  slot.connectTimer = null;
  setSlotVisual(index, 'error', message);
  if (notify && Date.now() - slot.lastErrorToastAt > 30000) {
    slot.lastErrorToastAt = Date.now();
    toast(message, 'error', 6000);
  }
}

function releaseSlot(index, { stopServer = true, silent = false } = {}) {
  const slot = state.slots[index];
  const element = getVisibleSlotEl(index);
  if (!slot || !element) return;
  closePicker(index);
  const oldStreamId = slot.streamId;
  slot.gen += 1;
  slot.pending = false;
  destroyHls(slot, element);
  slot.streamId = null;
  setSlotVisual(index, 'empty');
  updateSlotOptions(index);
  updateSlotChrome(index);
  if (oldStreamId && stopServer) stopServerPreviewIfUnused(oldStreamId);
  persistLayout();
  if (!silent) updateAllSlots();
}

function stopServerPreviewIfUnused(streamId) {
  const stillUsed = state.slots.some((slot) => slot.streamId === streamId);
  if (!stillUsed) {
    api.stopPreview(streamId).catch(() => {});
  }
}

function onSlotSelect(index, value) {
  closePicker(index);
  const nextId = value ? Number(value) : null;
  const slot = state.slots[index];
  if (nextId === slot.streamId) return;

  if (nextId && state.slots.some((other) => other.index !== index && other.streamId === nextId)) {
    toast('该视频流已在其他画面中，请选择不同的流', 'info');
    updateSlotChrome(index);
    return;
  }

  const oldId = slot.streamId;
  slot.gen += 1;
  slot.pending = false;
  const element = getVisibleSlotEl(index);
  destroyHls(slot, element);
  slot.streamId = nextId;
  if (oldId && oldId !== nextId) stopServerPreviewIfUnused(oldId);

  persistLayout();
  updateAllSlots();

  if (nextId) {
    mountPreview(index);
  } else {
    setSlotVisual(index, 'empty');
    toast('已清空该画面', 'info', 2200);
  }
}

function changeSlotStream(index, streamId) {
  if (index >= state.count) {
    state.count = index + 1;
    updateCountUI();
  }
  onSlotSelect(index, String(streamId));
}

function updateAllSlots() {
  for (let index = 0; index < state.count; index += 1) {
    updateSlotOptions(index);
    updateSlotChrome(index);
  }
  if (state.openPicker !== null) {
    // Re-render only when the data actually changed, otherwise the periodic
    // poll would reset the list's scroll position while the user browses.
    const signature = pickerSignature(state.openPicker);
    if (signature !== state.openPickerSignature) {
      state.openPickerSignature = signature;
      renderPickerList(state.openPicker);
    }
  }
}

function updateSlotOptions(index) {
  const element = getVisibleSlotEl(index);
  if (!element) return;
  const trigger = element.querySelector('[data-role="picker-trigger"]');
  if (trigger) trigger.disabled = state.streams.length === 0;
}

/* ------------------------------------------------------------- slot picker */
// The panel lives at the end of <body>: preview cards clip their children
// (overflow-hidden) and their backdrop-filter would otherwise become the
// containing block for a position:fixed panel.

function pickerElements() {
  const panel = dom.slotPickerPanel;
  return {
    panel,
    search: panel?.querySelector('[data-role="picker-search"]'),
    list: panel?.querySelector('[data-role="picker-list"]'),
  };
}

function slotIndexOf(node) {
  const slot = node.closest('[data-slot]');
  const index = Number(slot?.dataset.index);
  return Number.isInteger(index) ? index : -1;
}

function pickerSignature(index) {
  const used = state.slots
    .filter((slot) => slot.index !== index && slot.streamId)
    .map((slot) => slot.streamId)
    .join(',');
  const selected = state.slots[index]?.streamId ?? '';
  const streams = state.streams
    .map((stream) => `${stream.id}:${stream.name}:${stream.status}`)
    .join('|');
  return `${streams}#${used}#${selected}`;
}

function emptyPickerRow(text) {
  const row = document.createElement('li');
  row.className = 'slot-picker-empty';
  row.setAttribute('role', 'presentation');
  row.textContent = text;
  return row;
}

function renderPickerList(index = state.openPicker) {
  const { list, search } = pickerElements();
  if (!list || index === null || index < 0 || !state.slots[index]) return;

  const slot = state.slots[index];
  const query = (search?.value || '').trim().toLowerCase();
  const used = new Set(
    state.slots
      .filter((other) => other.index !== index && other.streamId)
      .map((other) => other.streamId),
  );

  if (!state.streams.length) {
    list.replaceChildren(emptyPickerRow('还没有添加视频流'));
    return;
  }

  const matches = state.streams.filter((stream) => {
    if (!query) return true;
    return stream.name.toLowerCase().includes(query)
      || stream.url.toLowerCase().includes(query);
  });
  if (!matches.length) {
    list.replaceChildren(emptyPickerRow('没有匹配的视频流'));
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const stream of matches) {
    const selected = stream.id === slot.streamId;
    const taken = used.has(stream.id);
    const item = document.createElement('li');
    item.className = 'slot-picker-option'
      + (selected ? ' is-selected' : '')
      + (taken ? ' is-disabled' : '');
    item.dataset.pickerValue = String(stream.id);
    item.setAttribute('role', 'option');
    item.setAttribute('aria-selected', String(selected));
    item.setAttribute('aria-disabled', String(taken));
    item.title = taken ? '已在其他画面中使用' : stream.name;

    const dot = document.createElement('span');
    dot.className = `slot-picker-dot ${STREAM_STATUS[stream.status]?.dot || 'bg-slate-500'}`;
    const label = document.createElement('span');
    label.className = 'min-w-0 flex-1 truncate';
    label.textContent = stream.name;
    item.append(dot, label);

    if (taken) {
      const tag = document.createElement('span');
      tag.className = 'slot-picker-tag';
      tag.textContent = '已占用';
      item.append(tag);
    }
    fragment.appendChild(item);
  }
  list.replaceChildren(fragment);
}

function positionPicker(index) {
  const trigger = getVisibleSlotEl(index)?.querySelector('[data-role="picker-trigger"]');
  const { panel } = pickerElements();
  if (!trigger || !panel) return;

  const rect = trigger.getBoundingClientRect();
  const width = Math.max(rect.width, 240);
  const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
  const spaceBelow = window.innerHeight - rect.bottom - 12;
  const spaceAbove = rect.top - 12;
  const openUp = spaceBelow < 220 && spaceAbove > spaceBelow;
  const maxHeight = Math.max(160, Math.min(340, openUp ? spaceAbove : spaceBelow));

  panel.style.width = `${width}px`;
  panel.style.left = `${left}px`;
  panel.style.maxHeight = `${maxHeight}px`;
  if (openUp) {
    panel.style.top = 'auto';
    panel.style.bottom = `${window.innerHeight - rect.top + 6}px`;
  } else {
    panel.style.bottom = 'auto';
    panel.style.top = `${rect.bottom + 6}px`;
  }
}

function closePicker(index) {
  const element = getVisibleSlotEl(index);
  element?.querySelector('[data-role="picker"]')?.classList.remove('is-open');
  element?.querySelector('[data-role="picker-trigger"]')?.setAttribute('aria-expanded', 'false');
  pickerElements().panel?.classList.add('hidden');
  if (state.openPicker === index) state.openPicker = null;
  state.openPickerSignature = '';
}

function closeAllPickers() {
  if (state.openPicker === null) return;
  closePicker(state.openPicker);
}

function openPicker(index) {
  if (!state.streams.length) return;
  closeAllPickers();
  const element = getVisibleSlotEl(index);
  const trigger = element?.querySelector('[data-role="picker-trigger"]');
  const { panel, search } = pickerElements();
  if (!element || !trigger || !panel || !search) return;

  state.openPicker = index;
  element.querySelector('[data-role="picker"]').classList.add('is-open');
  trigger.setAttribute('aria-expanded', 'true');
  panel.classList.remove('hidden');
  search.value = '';
  renderPickerList(index);
  state.openPickerSignature = pickerSignature(index);
  positionPicker(index);
  search.focus({ preventScroll: true });
  panel.querySelector('.slot-picker-option.is-selected')?.scrollIntoView({ block: 'nearest' });
}

function movePickerActive(step) {
  const { list } = pickerElements();
  if (!list) return;
  const options = [...list.querySelectorAll('.slot-picker-option:not(.is-disabled)')];
  if (!options.length) return;

  let current = options.findIndex((option) => option.classList.contains('is-active'));
  if (current === -1) {
    const selected = options.findIndex((option) => option.classList.contains('is-selected'));
    current = selected === -1 ? (step > 0 ? -1 : 0) : selected;
  }
  const next = (current + step + options.length) % options.length;
  list.querySelectorAll('.slot-picker-option.is-active').forEach((option) => option.classList.remove('is-active'));
  options[next].classList.add('is-active');
  options[next].scrollIntoView({ block: 'nearest' });
}

function choosePickerValue(index, value) {
  closePicker(index);
  getVisibleSlotEl(index)?.querySelector('[data-role="picker-trigger"]')?.focus({ preventScroll: true });
  onSlotSelect(index, value);
}

function commitPickerActive() {
  const { list } = pickerElements();
  if (!list || state.openPicker === null) return;
  const active = list.querySelector('.slot-picker-option.is-active')
    || list.querySelector('.slot-picker-option.is-selected')
    || list.querySelector('.slot-picker-option:not(.is-disabled)');
  if (active) choosePickerValue(state.openPicker, active.dataset.pickerValue);
}

function updateSlotChrome(index) {
  const element = getVisibleSlotEl(index);
  const slot = state.slots[index];
  if (!element || !slot) return;

  const trigger = element.querySelector('[data-role="picker-trigger"]');
  const pickerLabel = element.querySelector('[data-role="picker-label"]');
  const statusBadge = element.querySelector('[data-role="status"]');
  const statusText = element.querySelector('[data-role="status-text"]');
  const name = element.querySelector('[data-role="stream-name"]');
  const meta = element.querySelector('[data-role="stream-meta"]');
  const footDot = element.querySelector('[data-role="foot-dot"]');
  const recordButton = element.querySelector('[data-action="record"]');
  const recordIconUse = recordButton.querySelector('[data-role="record-icon"] use');
  const recordLabel = recordButton.querySelector('[data-role="record-label"]');
  const volumeUse = element.querySelector('[data-role="volume-icon"] use');
  const video = element.querySelector('[data-role="video"]');

  const stream = getStream(slot.streamId);
  if (!stream) {
    if (pickerLabel) pickerLabel.textContent = state.streams.length ? '选择一路流…' : '暂无视频流';
    if (trigger) trigger.disabled = state.streams.length === 0;
    statusBadge.dataset.status = 'unknown';
    statusText.textContent = '未选择';
    name.textContent = '尚未选择视频源';
    name.title = '';
    meta.textContent = '—';
    footDot.className = 'h-1.5 w-1.5 shrink-0 rounded-full bg-slate-600';
    recordButton.disabled = true;
    recordButton.classList.remove('is-recording');
    recordIconUse.setAttribute('href', '#i-record');
    recordLabel.textContent = '录制';
    recordButton.setAttribute('aria-label', '录制控制');
    video.muted = slot.muted;
    return;
  }

  statusBadge.dataset.status = stream.status in STREAM_STATUS ? stream.status : 'unknown';
  statusText.textContent = STREAM_STATUS[stream.status]?.label || '未知';
  name.textContent = stream.name;
  name.title = stream.name;
  meta.textContent = `${stream.auto_reconnect ? 'AUTO' : 'MANUAL'} · ${hostFromUrl(stream.url)}`;
  footDot.className = `h-1.5 w-1.5 shrink-0 rounded-full ${STREAM_STATUS[stream.status]?.dot || 'bg-slate-500'}`;
  if (pickerLabel) pickerLabel.textContent = stream.name;
  if (trigger) trigger.disabled = false;

  const busy = state.recordingBusy.has(stream.id);
  const recording = stream.is_recording;
  recordButton.disabled = busy || slot.pending;
  recordButton.classList.toggle('is-recording', recording && !busy);
  recordIconUse.setAttribute('href', recording ? '#i-stop' : '#i-record');
  recordLabel.textContent = busy ? '保存中…' : recording ? '停止' : '录制';
  recordButton.setAttribute('aria-label', recording ? '停止录制' : '开始录制');
  volumeUse.setAttribute('href', slot.muted ? '#i-muted' : '#i-volume');
  video.muted = slot.muted;
}

function toggleMute(index) {
  const slot = state.slots[index];
  const element = getVisibleSlotEl(index);
  const video = element?.querySelector('[data-role="video"]');
  if (!video) return;
  slot.muted = !slot.muted;
  video.muted = slot.muted;
  element.querySelector('[data-role="volume-icon"] use')
    .setAttribute('href', slot.muted ? '#i-muted' : '#i-volume');
}

async function toggleFullscreen(index) {
  const element = getVisibleSlotEl(index);
  const media = element?.querySelector('[data-role="media"]');
  if (!media) return;
  try {
    if (document.fullscreenElement) {
      await document.exitFullscreen();
    } else {
      await media.requestFullscreen();
    }
  } catch {
    toast('当前浏览器不支持全屏播放', 'error');
  }
}

function stopAllPreviews() {
  const active = state.slots.some((slot) => isVisibleSlot(slot.index) && slot.streamId);
  if (!active) {
    toast('当前没有正在预览的画面', 'info');
    return;
  }
  for (let index = 0; index < state.count; index += 1) {
    releaseSlot(index, { stopServer: true, silent: true });
  }
  updateAllSlots();
  toast('已停止所有预览', 'success');
}

/* --------------------------------------------------------------- rendering */

function renderStreams() {
  if (!dom.streamsTbody) return;
  dom.streamsTbody.innerHTML = state.streams.map((stream) => {
    const status = STREAM_STATUS[stream.status] || STREAM_STATUS.unknown;
    const busy = state.recordingBusy.has(stream.id);
    const checking = state.checkingStreams.has(stream.id);
    const avatar = AVATAR_STYLES[stream.id % AVATAR_STYLES.length];
    const initial = firstCharacter(stream.name);
    const recordLabel = busy ? '处理中' : stream.is_recording ? '停止' : '录制';
    return `
      <tr>
        <td>
          <div class="flex min-w-0 items-center gap-3">
            <span class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border bg-gradient-to-br text-[11px] font-semibold ${avatar}">${escapeHtml(initial)}</span>
            <div class="min-w-0">
              <div class="flex items-center gap-1.5">
                <span class="table-name">${escapeHtml(stream.name)}</span>
                ${stream.is_recording ? '<span class="rounded bg-rose-500/10 px-1.5 py-0.5 font-mono text-[9px] font-semibold text-rose-300">REC</span>' : ''}
                ${stream.preview_active ? '<span class="rounded bg-brand-500/10 px-1.5 py-0.5 font-mono text-[9px] font-semibold text-brand-300">HLS</span>' : ''}
              </div>
              <div class="table-sub" title="${escapeHtml(maskUrl(stream.url))}">${escapeHtml(maskUrl(stream.url))}</div>
            </div>
          </div>
        </td>
        <td data-label="连接状态">
          <div class="flex flex-col items-start gap-1">
            <span class="status-badge" data-status="${escapeHtml(stream.status)}"><span class="status-dot"></span>${escapeHtml(status.label)}</span>
            <span class="max-w-[210px] truncate text-[10px] text-slate-600" title="${escapeHtml(stream.status_message || '')}">${escapeHtml(stream.status_message || '—')}</span>
          </div>
        </td>
        <td data-label="最近检测">
          <span class="text-[11px] text-slate-400" title="${escapeHtml(formatFullDate(stream.last_checked_at))}">${escapeHtml(relativeTime(stream.last_checked_at))}</span>
        </td>
        <td data-label="自动重连">
          <button type="button" class="inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[10px] transition ${stream.auto_reconnect ? 'border-emerald-400/15 bg-emerald-500/[0.06] text-emerald-300 hover:bg-emerald-500/10' : 'border-white/[0.07] bg-white/[0.025] text-slate-500 hover:text-slate-300'}" data-action="toggle-reconnect" data-stream-id="${stream.id}" title="点击切换自动重连">
            <span class="h-1.5 w-1.5 rounded-full ${stream.auto_reconnect ? 'bg-emerald-400' : 'bg-slate-600'}"></span>
            ${stream.auto_reconnect ? '已开启' : '已关闭'}
          </button>
        </td>
        <td data-label="操作">
          <div class="flex items-center justify-end gap-1">
            <button type="button" class="record-button ${stream.is_recording ? 'is-recording' : ''}" data-action="record" data-stream-id="${stream.id}" ${busy ? 'disabled' : ''}>
              <svg class="icon h-3.5 w-3.5"><use href="${stream.is_recording ? '#i-stop' : '#i-record'}"></use></svg>
              ${recordLabel}
            </button>
            <button type="button" class="table-action" data-action="play" data-stream-id="${stream.id}" title="添加到预览">
              <svg class="icon h-3.5 w-3.5"><use href="#i-play"></use></svg>
            </button>
            <button type="button" class="table-action" data-action="check" data-stream-id="${stream.id}" title="立即检测连接" ${checking ? 'disabled' : ''}>
              <svg class="icon h-3.5 w-3.5 ${checking ? 'animate-spin' : ''}"><use href="#i-refresh"></use></svg>
            </button>
            <button type="button" class="table-action" data-action="edit" data-stream-id="${stream.id}" title="编辑">
              <svg class="icon h-3.5 w-3.5"><use href="#i-edit"></use></svg>
            </button>
            <button type="button" class="table-action table-action-danger" data-action="delete" data-stream-id="${stream.id}" title="删除">
              <svg class="icon h-3.5 w-3.5"><use href="#i-trash"></use></svg>
            </button>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

function renderRecordings() {
  if (!dom.recordingsTbody) return;
  dom.recordingsTbody.innerHTML = state.recordings.map((recording) => {
    const meta = RECORDING_STATUS[recording.status] || RECORDING_STATUS.failed;
    const active = recording.status === 'recording' || recording.status === 'starting';
    const duration = active
      ? formatDuration(elapsedSeconds(recording.started_at))
      : formatDuration(recording.duration_seconds);
    const fileIconTone = recording.status === 'failed'
      ? 'border-rose-400/15 bg-rose-500/[0.07] text-rose-300'
      : 'border-white/[0.07] bg-white/[0.03] text-slate-400';
    const errorLine = recording.error_message
      ? `<span class="mt-1 block max-w-[220px] truncate text-[10px] text-rose-400/80" title="${escapeHtml(recording.error_message)}">${escapeHtml(recording.error_message)}</span>`
      : '';
    const playButton = recording.file_exists
      ? `<button type="button" class="table-action" data-action="play-recording" data-recording-id="${recording.id}" title="回放"><svg class="icon h-3.5 w-3.5"><use href="#i-play"></use></svg></button>`
      : `<button type="button" class="table-action" disabled title="文件尚不可播放"><svg class="icon h-3.5 w-3.5"><use href="#i-play"></use></svg></button>`;
    const downloadLink = recording.file_exists
      ? `<a class="table-action" href="${escapeHtml(recording.download_url)}" title="下载"><svg class="icon h-3.5 w-3.5"><use href="#i-download"></use></svg></a>`
      : `<span class="table-action pointer-events-none opacity-30" title="文件尚不可下载"><svg class="icon h-3.5 w-3.5"><use href="#i-download"></use></svg></span>`;
    return `
      <tr class="${active ? 'bg-rose-500/[0.018]' : ''}">
        <td>
          <div class="flex min-w-0 items-center gap-3">
            <span class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border ${fileIconTone}">
              <svg class="icon h-3.5 w-3.5"><use href="${recording.status === 'failed' ? '#i-alert' : '#i-video'}"></use></svg>
            </span>
            <div class="min-w-0">
              <div class="table-name max-w-[330px]" title="${escapeHtml(recording.filename)}">${escapeHtml(recording.filename)}</div>
              <div class="table-sub">${escapeHtml(recording.stream_name)}</div>
            </div>
          </div>
        </td>
        <td data-label="状态">
          <span class="status-badge" data-status="${meta.tone}"><span class="status-dot ${active ? 'animate-pulse-soft' : ''}"></span>${meta.label}</span>
          ${errorLine}
        </td>
        <td data-label="时长" class="font-mono text-[11px] text-slate-400">${escapeHtml(duration)}</td>
        <td data-label="大小" class="text-[11px] text-slate-400">${recording.size_bytes ? escapeHtml(formatBytes(recording.size_bytes)) : '—'}</td>
        <td data-label="开始时间">
          <span class="text-[11px] text-slate-400" title="${escapeHtml(formatFullDate(recording.started_at))}">${escapeHtml(formatDate(recording.started_at))}</span>
        </td>
        <td data-label="操作">
          <div class="flex items-center justify-end gap-1">
            ${playButton}
            ${downloadLink}
            <button type="button" class="table-action table-action-danger" data-action="delete-recording" data-recording-id="${recording.id}" title="删除录制" ${active ? 'disabled' : ''}>
              <svg class="icon h-3.5 w-3.5"><use href="#i-trash"></use></svg>
            </button>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

function updateSectionVisibility() {
  setDisplay(dom.streamsLoading, state.loading, 'block');
  setDisplay(dom.recordingsLoading, state.loading, 'block');
  setDisplay(dom.streamsEmpty, !state.loading && state.streams.length === 0, 'flex');
  setDisplay(dom.streamsTableWrap, !state.loading && state.streams.length > 0, 'block');
  setDisplay(dom.recordingsEmpty, !state.loading && state.recordings.length === 0, 'flex');
  setDisplay(dom.recordingsTableWrap, !state.loading && state.recordings.length > 0, 'block');
}

function setDisplay(element, visible, display) {
  if (!element) return;
  element.classList.toggle('hidden', !visible);
  element.style.display = visible ? display : 'none';
}

function setLoading(loading) {
  state.loading = loading;
  updateSectionVisibility();
}

/* ----------------------------------------------------------- stream actions */

async function openStreamModal(stream = null) {
  state.editingId = stream?.id ?? null;
  dom.streamForm.reset();
  dom.streamName.value = stream?.name || '';
  dom.streamUrl.value = stream?.url || '';
  dom.streamAutoReconnect.checked = stream
    ? Boolean(stream.auto_reconnect)
    : Boolean(state.system?.auto_reconnect_default ?? true);
  dom.streamModalTitle.textContent = stream ? '编辑视频流' : '新增视频流';
  dom.streamModalSubtitle.textContent = stream
    ? '修改连接配置，保存后重新建立预览'
    : '配置 RTSP 地址，保存后立即生效';
  dom.streamSubmitLabel.textContent = stream ? '保存修改' : '保存配置';
  dom.streamFormError.classList.add('hidden');
  dom.streamFormError.textContent = '';
  showModal('stream-modal');
  dom.streamName.focus();
}

async function submitStreamForm(event) {
  event.preventDefault();
  if (state.formBusy) return;
  const name = dom.streamName.value.trim();
  const url = dom.streamUrl.value.trim();
  const autoReconnect = dom.streamAutoReconnect.checked;
  if (!name || !url) return;

  const wasEditing = state.editingId;
  const previous = wasEditing ? getStream(wasEditing) : null;
  const needsPreviewRestart = Boolean(
    previous
    && state.slots.some((slot) => slot.streamId === previous.id)
    && (previous.url !== url || previous.auto_reconnect !== autoReconnect),
  );

  setFormBusy(true);
  try {
    if (wasEditing) {
      await api.updateStream(wasEditing, { name, url, auto_reconnect: autoReconnect });
    } else {
      await api.createStream({ name, url, auto_reconnect: autoReconnect });
    }
    // The close guard blocks user dismissal while saving; the request has now
    // succeeded, so release it before closing.
    setFormBusy(false);
    closeModal('stream-modal');
    toast(wasEditing ? '视频流配置已更新' : '视频流已添加', 'success');
    await refreshData();
    if (wasEditing && needsPreviewRestart) {
      for (const slot of state.slots) {
        if (isVisibleSlot(slot.index) && slot.streamId === wasEditing) mountPreview(slot.index);
      }
    }
  } catch (error) {
    dom.streamFormError.textContent = error?.message || '保存失败';
    dom.streamFormError.classList.remove('hidden');
  } finally {
    setFormBusy(false);
  }
}

function setFormBusy(busy) {
  state.formBusy = busy;
  dom.streamSubmit.disabled = busy;
  dom.streamSubmitSpinner.classList.toggle('hidden', !busy);
  dom.streamSubmitLabel.textContent = busy
    ? '正在保存…'
    : state.editingId ? '保存修改' : '保存配置';
}

async function toggleRecording(streamId) {
  const stream = getStream(streamId);
  if (!stream || state.recordingBusy.has(streamId)) return;
  state.recordingBusy.add(streamId);
  renderStreams();
  updateAllSlots();

  try {
    if (stream.is_recording) {
      toast('正在停止录制并写入 MP4 索引…', 'info', 3200);
      const recording = await api.stopRecording(streamId);
      if (recording?.status === 'completed') {
        toast(`录制已保存：${recording.filename}`, 'success');
      } else {
        toast('录制已停止，但文件未能完整保存', 'error');
      }
    } else {
      const recording = await api.startRecording(streamId);
      toast(`开始录制：${recording.filename}`, 'success');
    }
  } catch (error) {
    toast(error?.message || '录制操作失败', 'error', 6000);
  } finally {
    state.recordingBusy.delete(streamId);
    await refreshData();
    renderStreams();
    updateAllSlots();
  }
}

async function deleteStream(stream) {
  const extra = stream.is_recording ? '该流正在录制，删除前会先停止录制并保存当前文件。' : '';
  const ok = await askConfirm({
    title: '删除视频流',
    message: `确定删除「${stream.name}」吗？${extra}已保存的录制文件不会被删除。`,
    okText: '删除流',
  });
  if (!ok) return;
  try {
    await api.deleteStream(stream.id);
    for (const slot of state.slots) {
      if (slot.streamId === stream.id) releaseSlot(slot.index, { stopServer: false, silent: true });
    }
    updateAllSlots();
    toast('视频流已删除', 'success');
    await refreshData();
  } catch (error) {
    toast(error?.message || '删除失败', 'error');
  }
}

async function checkOneStream(stream) {
  if (state.checkingStreams.has(stream.id)) return;
  state.checkingStreams.add(stream.id);
  renderStreams();
  try {
    const updated = await api.checkStream(stream.id);
    toast(
      updated.status === 'online' ? `「${stream.name}」在线` : `「${stream.name}」离线：${updated.status_message || '无法连接'}`,
      updated.status === 'online' ? 'success' : 'error',
      5000,
    );
    await refreshData();
  } catch (error) {
    toast(error?.message || '连接检测失败', 'error');
  } finally {
    state.checkingStreams.delete(stream.id);
    renderStreams();
  }
}

async function checkAllStreams() {
  if (state.checkingAll) return;
  if (!state.streams.length) {
    toast('请先添加视频流', 'info');
    return;
  }
  state.checkingAll = true;
  const original = dom.btnCheckAll.innerHTML;
  dom.btnCheckAll.disabled = true;
  dom.btnCheckAll.innerHTML = '<span class="spinner" style="width:14px;height:14px;border-width:1.5px"></span>检测中…';
  try {
    const results = await Promise.allSettled(state.streams.map((stream) => api.checkStream(stream.id)));
    const online = results.filter((result) => (
      result.status === 'fulfilled' && result.value.status === 'online'
    )).length;
    const failed = results.length - online;
    toast(`检测完成：${online} 路在线，${failed} 路离线`, failed ? 'info' : 'success');
    await refreshData();
  } catch (error) {
    toast(error?.message || '批量检测失败', 'error');
  } finally {
    state.checkingAll = false;
    dom.btnCheckAll.disabled = false;
    dom.btnCheckAll.innerHTML = original;
  }
}

async function toggleReconnect(stream) {
  try {
    await api.updateStream(stream.id, { auto_reconnect: !stream.auto_reconnect });
    await refreshData();
    const selected = state.slots.filter((slot) => isVisibleSlot(slot.index) && slot.streamId === stream.id);
    for (const slot of selected) mountPreview(slot.index);
    toast(`「${stream.name}」自动重连已${stream.auto_reconnect ? '关闭' : '开启'}`, 'success');
  } catch (error) {
    toast(error?.message || '更新失败', 'error');
  }
}

function playStream(streamId) {
  const existing = state.slots.find((slot) => isVisibleSlot(slot.index) && slot.streamId === streamId);
  if (existing) {
    setView('live');
    getVisibleSlotEl(existing.index)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    toast(`已在 CAM ${String(existing.index + 1).padStart(2, '0')} 中播放`, 'info', 2500);
    return;
  }

  let index = -1;
  for (let i = 0; i < state.count; i += 1) {
    if (!state.slots[i].streamId) {
      index = i;
      break;
    }
  }
  if (index === -1 && state.count < MAX_SLOTS) {
    index = state.count;
    state.count += 1;
    updateCountUI();
  }
  if (index === -1) {
    toast('6 路预览已满，请先清空一路画面', 'info');
    return;
  }

  setView('live');
  changeSlotStream(index, streamId);
}

async function deleteRecording(recording) {
  const ok = await askConfirm({
    title: '删除录制文件',
    message: `确定删除「${recording.filename}」吗？文件将从磁盘永久移除。`,
    okText: '永久删除',
  });
  if (!ok) return;
  try {
    await api.deleteRecording(recording.id);
    toast('录制文件已删除', 'success');
    await refreshData();
  } catch (error) {
    toast(error?.message || '删除失败', 'error');
  }
}

/* ---------------------------------------------------------------- playback */

function openPlayback(recording) {
  if (!recording?.file_exists) {
    toast('该录制文件不可用', 'error');
    return;
  }
  dom.playbackTitle.textContent = recording.filename;
  dom.playbackMeta.textContent = `${recording.stream_name} · ${formatFullDate(recording.started_at)} · ${formatDuration(recording.duration_seconds)} · ${formatBytes(recording.size_bytes)}`;
  dom.playbackDownload.href = recording.download_url;
  dom.playbackDownload.setAttribute('download', recording.filename);
  dom.playbackVideo.src = recording.playback_url;
  dom.playbackVideo.load();
  showModal('playback-modal');
  dom.playbackVideo.play().catch(() => {});
}

function closePlayback() {
  dom.playbackVideo.pause();
  dom.playbackVideo.removeAttribute('src');
  dom.playbackVideo.load();
}

/* ------------------------------------------------------------------- modals */

function showModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  document.body.classList.add('overflow-hidden');
}

function hideModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
  if (!document.querySelector('.modal:not(.hidden)')) {
    document.body.classList.remove('overflow-hidden');
  }
}

function closeModal(id) {
  if (id === 'stream-modal' && state.formBusy) return;
  if (id === 'confirm-modal' && state.confirmResolver) {
    settleConfirm(false);
    return;
  }
  if (id === 'playback-modal') closePlayback();
  hideModal(id);
  if (id === 'stream-modal') {
    state.editingId = null;
    dom.streamFormError.classList.add('hidden');
  }
}

function askConfirm({ title = '确认操作', message, okText = '确认' }) {
  if (state.confirmResolver) settleConfirm(false);
  dom.confirmTitle.textContent = title;
  dom.confirmMessage.textContent = message;
  dom.confirmOk.textContent = okText;
  showModal('confirm-modal');
  window.setTimeout(() => dom.confirmCancel.focus(), 50);
  return new Promise((resolve) => {
    state.confirmResolver = resolve;
  });
}

function settleConfirm(value) {
  const resolve = state.confirmResolver;
  state.confirmResolver = null;
  hideModal('confirm-modal');
  if (resolve) resolve(Boolean(value));
}

/* -------------------------------------------------------------------- toasts */

function toast(message, tone = 'info', duration = 4600) {
  const icon = tone === 'success' ? '#i-check' : tone === 'error' ? '#i-alert' : '#i-activity';
  const element = document.createElement('div');
  element.className = `toast toast-${tone}`;
  element.setAttribute('role', tone === 'error' ? 'alert' : 'status');
  element.innerHTML = `
    <span class="toast-icon"><svg class="icon h-3 w-3"><use href="${icon}"></use></svg></span>
    <span class="min-w-0 flex-1"></span>
    <button type="button" class="ml-1 shrink-0 rounded p-0.5 text-slate-500 transition hover:text-slate-200" aria-label="关闭通知">
      <svg class="icon h-3.5 w-3.5"><use href="#i-x"></use></svg>
    </button>
  `;
  element.querySelector('.min-w-0').textContent = message;
  const remove = () => {
    element.style.opacity = '0';
    element.style.transform = 'translateY(6px)';
    element.style.transition = 'opacity .2s ease, transform .2s ease';
    window.setTimeout(() => element.remove(), 220);
  };
  element.querySelector('button').addEventListener('click', remove);
  while (dom.toasts.children.length >= 4) dom.toasts.firstElementChild?.remove();
  dom.toasts.appendChild(element);
  window.setTimeout(remove, duration);
}

/* ------------------------------------------------------------------- events */

function bindEvents() {
  dom.btnAddStream.addEventListener('click', () => openStreamModal());
  dom.btnAddStream2.addEventListener('click', () => openStreamModal());
  dom.btnEmptyAdd.addEventListener('click', () => openStreamModal());
  dom.streamForm.addEventListener('submit', submitStreamForm);
  dom.btnStopAll.addEventListener('click', stopAllPreviews);
  dom.btnCheckAll.addEventListener('click', checkAllStreams);
  dom.btnRefreshRecordings.addEventListener('click', async () => {
    dom.btnRefreshRecordings.disabled = true;
    try {
      await refreshData();
      toast('列表已刷新', 'info', 2000);
    } finally {
      dom.btnRefreshRecordings.disabled = false;
    }
  });

  dom.layoutSwitcher.addEventListener('click', (event) => {
    const button = event.target.closest('[data-count]');
    if (button) changeCount(Number(button.dataset.count));
  });

  document.querySelector('header')?.addEventListener('click', (event) => {
    const tab = event.target.closest('[data-view]');
    if (!tab) return;
    setView(tab.dataset.view);
  });

  window.addEventListener('hashchange', () => {
    const view = viewFromHash();
    if (view) setView(view, { fromHash: true });
  });

  dom.previewGrid.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-role="picker-trigger"]');
    if (!trigger) return;
    const index = slotIndexOf(trigger);
    if (index < 0) return;
    if (state.openPicker === index) closePicker(index);
    else openPicker(index);
  });

  dom.previewGrid.addEventListener('keydown', (event) => {
    const trigger = event.target.closest('[data-role="picker-trigger"]');
    if (!trigger) return;
    const index = slotIndexOf(trigger);
    if (index < 0) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (state.openPicker === index) closePicker(index);
      else openPicker(index);
    }
  });

  dom.slotPickerPanel.addEventListener('click', (event) => {
    const option = event.target.closest('[data-picker-value]');
    if (!option || state.openPicker === null) return;
    if (option.classList.contains('is-disabled')) return;
    choosePickerValue(state.openPicker, option.dataset.pickerValue);
  });

  dom.slotPickerPanel.addEventListener('input', () => renderPickerList());

  dom.slotPickerPanel.addEventListener('keydown', (event) => {
    if (state.openPicker === null) return;
    if (event.key === 'Escape') {
      event.stopPropagation();
      const index = state.openPicker;
      closePicker(index);
      getVisibleSlotEl(index)?.querySelector('[data-role="picker-trigger"]')?.focus({ preventScroll: true });
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      movePickerActive(event.key === 'ArrowDown' ? 1 : -1);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      commitPickerActive();
    }
  });

  // Clicking anywhere outside the trigger or the panel closes the picker.
  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-role="picker"]')) return;
    if (event.target.closest('[data-role="picker-panel"]')) return;
    closeAllPickers();
  });

  // A fixed-position panel would drift when the page moves, so close it.
  window.addEventListener('resize', closeAllPickers);
  window.addEventListener('scroll', (event) => {
    if (event.target instanceof Element && event.target.closest('[data-role="picker-panel"]')) return;
    closeAllPickers();
  }, true);

  dom.previewGrid.addEventListener('click', (event) => {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const article = button.closest('[data-slot]');
    const index = Number(article?.dataset.index);
    if (!Number.isInteger(index)) return;
    const action = button.dataset.action;
    if (action === 'clear-slot') {
      releaseSlot(index, { stopServer: true });
    } else if (action === 'retry') {
      mountPreview(index);
    } else if (action === 'mute') {
      toggleMute(index);
    } else if (action === 'fullscreen') {
      toggleFullscreen(index);
    } else if (action === 'record') {
      const slot = state.slots[index];
      if (slot.streamId) toggleRecording(slot.streamId);
    }
  });

  dom.streamsTbody.addEventListener('click', (event) => {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const stream = getStream(Number(button.dataset.streamId));
    if (!stream) return;
    const action = button.dataset.action;
    if (action === 'edit') openStreamModal(stream);
    else if (action === 'delete') deleteStream(stream);
    else if (action === 'check') checkOneStream(stream);
    else if (action === 'record') toggleRecording(stream.id);
    else if (action === 'play') playStream(stream.id);
    else if (action === 'toggle-reconnect') toggleReconnect(stream);
  });

  dom.recordingsTbody.addEventListener('click', (event) => {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const recording = state.recordings.find((item) => item.id === Number(button.dataset.recordingId));
    if (!recording) return;
    if (button.dataset.action === 'play-recording') openPlayback(recording);
    else if (button.dataset.action === 'delete-recording') deleteRecording(recording);
  });

  document.addEventListener('click', (event) => {
    const closer = event.target.closest('[data-close]');
    if (closer) closeModal(closer.dataset.close);
  });

  dom.confirmCancel.addEventListener('click', () => settleConfirm(false));
  dom.confirmOk.addEventListener('click', () => settleConfirm(true));

  dom.playbackVideo.addEventListener('error', () => {
    if (dom.playbackVideo.getAttribute('src')) toast('录制视频加载失败，文件可能已损坏', 'error');
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const open = [...document.querySelectorAll('.modal:not(.hidden)')].at(-1);
    if (!open) return;
    if (open.id === 'confirm-modal') settleConfirm(false);
    else closeModal(open.id);
  });

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshData();
  });

  window.addEventListener('online', () => refreshData());
}

function schedulePoll() {
  clearTimeout(state.pollTimer);
  const delay = document.hidden ? 15000 : 5000;
  state.pollTimer = window.setTimeout(async () => {
    await refreshData();
    schedulePoll();
  }, delay);
}

/* ------------------------------------------------------------------ helpers */

function firstCharacter(value) {
  return [...(value || '?').trim()][0]?.toUpperCase() || '?';
}

function hostFromUrl(url) {
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
}

function maskUrl(url) {
  // Mask the whole userinfo, including passwords that contain "@".
  return String(url || '').replace(/^([a-z][a-z0-9+.-]*:\/\/)([^/\s]+)@/i, '$1***@');
}

function parseDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function relativeTime(value) {
  const date = parseDate(value);
  if (!date) return '—';
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 15) return '刚刚';
  if (seconds < 60) return `${seconds} 秒前`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} 天前`;
  return formatDate(value);
}

function formatDate(value) {
  const date = parseDate(value);
  if (!date) return '—';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date);
}

function formatFullDate(value) {
  const date = parseDate(value);
  if (!date) return '—';
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date);
}

function formatDuration(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return '—';
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  }
  return `${minutes}:${String(secs).padStart(2, '0')}`;
}

function elapsedSeconds(startedAt) {
  const date = parseDate(startedAt);
  if (!date) return null;
  return Math.max(0, (Date.now() - date.getTime()) / 1000);
}

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const exponent = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const amount = bytes / (1024 ** exponent);
  return `${amount.toFixed(amount >= 10 || exponent === 0 ? 0 : 1)} ${units[exponent]}`;
}

bootstrap().catch((error) => {
  console.error(error);
  setServerState('error', '初始化失败');
  setLoading(false);
  toast(error?.message || '页面初始化失败', 'error', 8000);
});
