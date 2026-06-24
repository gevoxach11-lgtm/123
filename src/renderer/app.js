// Top-level renderer. Wires UI, the embedded <webview>, the region
// selection overlay, the capture loop, and the analyzer.

import { isValidCard, parseHand, cardLabel, isRedCard } from './lib/cards.js';
import { analyze } from './lib/analyzer.js';
import { recognizeHoleCards } from './lib/recognizer.js';

const $ = (id) => document.getElementById(id);

const webview        = $('site');
const urlForm        = $('url-form');
const urlInput       = $('url-input');
const reloadBtn      = $('reload-btn');
const backBtn        = $('back-btn');
const forwardBtn     = $('forward-btn');
const statusEl       = $('status');

const setRegionBtn   = $('set-region-btn');
const clearRegionBtn = $('clear-region-btn');
const autoToggleBtn  = $('auto-toggle');
const captureOnceBtn = $('capture-once-btn');
const intervalInput  = $('interval-input');
const positionSelect = $('position-select');
const playersInput   = $('players-input');

const cardSlot1      = $('card-slot-1');
const cardSlot2      = $('card-slot-2');
const confidenceEl   = $('confidence');
const manualInput    = $('manual-hand-input');
const manualBtn      = $('manual-hand-btn');

const mHandType      = $('m-handtype');
const mTier          = $('m-tier');
const mEquity        = $('m-equity');
const mAction        = $('m-action');
const coachNotes     = $('coach-notes');

const regionOverlay  = $('region-overlay');
const regionRect     = $('region-rect');
const regionCancel   = $('region-cancel');
const savedMarker    = $('saved-region-marker');

const state = {
  region: null,          // { x, y, width, height } in webview CSS pixels
  webContentsId: null,
  autoTimer: null,
  hole: [null, null],
  lastAnalysis: null,
  busy: false
};

// ---------------------------------------------------------------------------
// Webview wiring
// ---------------------------------------------------------------------------

webview.addEventListener('dom-ready', () => {
  state.webContentsId = webview.getWebContentsId();
  setStatus('Browser ready');
});

webview.addEventListener('did-start-loading', () => setStatus('Loading…'));
webview.addEventListener('did-stop-loading', () => setStatus(webview.getURL() || 'Idle'));
webview.addEventListener('did-fail-load', (e) => setStatus('Load failed'));

urlForm.addEventListener('submit', (e) => {
  e.preventDefault();
  let u = urlInput.value.trim();
  if (!u) return;
  if (!/^https?:\/\//i.test(u)) u = 'https://' + u;
  webview.loadURL(u).catch(() => setStatus('Bad URL'));
});

reloadBtn.addEventListener('click', () => webview.reload());
backBtn.addEventListener('click', () => webview.canGoBack() && webview.goBack());
forwardBtn.addEventListener('click', () => webview.canGoForward() && webview.goForward());

// ---------------------------------------------------------------------------
// Region selection overlay
// ---------------------------------------------------------------------------

setRegionBtn.addEventListener('click', () => startRegionSelection());
regionCancel.addEventListener('click', (e) => { e.stopPropagation(); cancelRegionSelection(); });
clearRegionBtn.addEventListener('click', () => {
  state.region = null;
  saveSettings();
  hideMarker();
  setStatus('Region cleared');
});

function startRegionSelection() {
  regionOverlay.classList.remove('hidden');
  regionRect.style.display = 'none';
  let dragging = false, sx = 0, sy = 0;
  const onDown = (e) => {
    if (e.target.closest('.region-overlay-help')) return;
    dragging = true;
    const r = regionOverlay.getBoundingClientRect();
    sx = e.clientX - r.left; sy = e.clientY - r.top;
    regionRect.style.left = sx + 'px';
    regionRect.style.top = sy + 'px';
    regionRect.style.width = '0px';
    regionRect.style.height = '0px';
    regionRect.style.display = 'block';
  };
  const onMove = (e) => {
    if (!dragging) return;
    const r = regionOverlay.getBoundingClientRect();
    const cx = e.clientX - r.left, cy = e.clientY - r.top;
    const x = Math.min(sx, cx), y = Math.min(sy, cy);
    const w = Math.abs(cx - sx), h = Math.abs(cy - sy);
    regionRect.style.left = x + 'px';
    regionRect.style.top = y + 'px';
    regionRect.style.width = w + 'px';
    regionRect.style.height = h + 'px';
  };
  const onUp = (e) => {
    if (!dragging) return;
    dragging = false;
    const left = parseFloat(regionRect.style.left || '0');
    const top  = parseFloat(regionRect.style.top  || '0');
    const w    = parseFloat(regionRect.style.width  || '0');
    const h    = parseFloat(regionRect.style.height || '0');
    cleanup();
    if (w < 20 || h < 20) {
      setStatus('Region too small — try again');
      return;
    }
    // Translate from overlay-coords to webview-content-coords.
    // The overlay covers the same DOM box as the webview, so the rectangle is
    // already in the same coordinate system used by webContents.capturePage.
    state.region = { x: left, y: top, width: w, height: h };
    saveSettings();
    showMarker();
    setStatus(`Region set ${Math.round(w)}×${Math.round(h)}`);
  };
  const cleanup = () => {
    regionOverlay.removeEventListener('mousedown', onDown);
    regionOverlay.removeEventListener('mousemove', onMove);
    regionOverlay.removeEventListener('mouseup', onUp);
    regionOverlay.classList.add('hidden');
  };
  regionOverlay.addEventListener('mousedown', onDown);
  regionOverlay.addEventListener('mousemove', onMove);
  regionOverlay.addEventListener('mouseup', onUp);
}

function cancelRegionSelection() {
  regionOverlay.classList.add('hidden');
}

function showMarker() {
  if (!state.region) return hideMarker();
  const wrap = document.querySelector('.browser-wrap').getBoundingClientRect();
  const wv   = webview.getBoundingClientRect();
  const offX = wv.left - wrap.left, offY = wv.top - wrap.top;
  savedMarker.style.left = (offX + state.region.x) + 'px';
  savedMarker.style.top  = (offY + state.region.y) + 'px';
  savedMarker.style.width  = state.region.width + 'px';
  savedMarker.style.height = state.region.height + 'px';
  savedMarker.classList.remove('hidden');
}

function hideMarker() { savedMarker.classList.add('hidden'); }

window.addEventListener('resize', () => showMarker());

// ---------------------------------------------------------------------------
// Capture loop
// ---------------------------------------------------------------------------

captureOnceBtn.addEventListener('click', () => captureAndAnalyze());

autoToggleBtn.addEventListener('click', () => {
  if (state.autoTimer) {
    clearInterval(state.autoTimer);
    state.autoTimer = null;
    autoToggleBtn.textContent = '▶ Start auto-watch';
    setStatus('Auto-watch off');
  } else {
    const secs = Math.max(1, Number(intervalInput.value) || 3);
    state.autoTimer = setInterval(() => captureAndAnalyze(), secs * 1000);
    autoToggleBtn.textContent = '■ Stop auto-watch';
    setStatus(`Auto-watch every ${secs}s`);
    captureAndAnalyze();
  }
});

async function captureAndAnalyze() {
  if (state.busy) return;
  if (!state.webContentsId) {
    setStatus('Browser not ready');
    return;
  }
  if (!state.region) {
    setStatus('Set the hole-card region first');
    return;
  }
  state.busy = true;
  try {
    const dataUrl = await window.pokerCoach.captureRegion(state.webContentsId, state.region);
    const result = await recognizeHoleCards(dataUrl);
    if (!result.cards[0] || !result.cards[1]) {
      confidenceEl.textContent = `Could not read both cards (${result.cards[0] || '??'} / ${result.cards[1] || '??'}) — try a tighter region or enter manually.`;
      confidenceEl.className = 'confidence bad';
      return;
    }
    if (result.cards[0] === result.cards[1]) {
      confidenceEl.textContent = `Reader returned two identical cards (${result.cards[0]}). Try re-cropping the region.`;
      confidenceEl.className = 'confidence bad';
      return;
    }
    confidenceEl.textContent = `Detected ${cardLabel(result.cards[0])} ${cardLabel(result.cards[1])} (confidence ${result.confidence}%)`;
    confidenceEl.className = 'confidence ' + (result.confidence >= 60 ? 'good' : 'bad');
    setHand(result.cards);
  } catch (err) {
    console.error(err);
    setStatus('Capture error');
  } finally {
    state.busy = false;
  }
}

// ---------------------------------------------------------------------------
// Manual entry
// ---------------------------------------------------------------------------

manualBtn.addEventListener('click', () => applyManual());
manualInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') applyManual(); });

function applyManual() {
  const parsed = parseHand(manualInput.value);
  if (!parsed) {
    setStatus('Enter cards like: Ah Kd');
    return;
  }
  confidenceEl.textContent = `Manual entry`;
  confidenceEl.className = 'confidence good';
  setHand(parsed);
}

// ---------------------------------------------------------------------------
// Display + analysis
// ---------------------------------------------------------------------------

function renderCard(slot, card) {
  if (!isValidCard(card)) {
    slot.textContent = '??';
    slot.className = 'card-slot unknown';
    return;
  }
  slot.textContent = cardLabel(card);
  slot.className = 'card-slot ' + (isRedCard(card) ? 'red' : '');
}

function setHand(cards) {
  state.hole = cards;
  renderCard(cardSlot1, cards[0]);
  renderCard(cardSlot2, cards[1]);
  runAnalysis();
}

async function runAnalysis() {
  if (!isValidCard(state.hole[0]) || !isValidCard(state.hole[1])) return;
  const opponents = Math.max(1, (Number(playersInput.value) || 6) - 1);
  const position  = positionSelect.value;

  mHandType.textContent = 'Analyzing…';
  mTier.textContent = '…';
  mEquity.textContent = '…';
  mAction.textContent = '…';

  // Run on next tick so UI updates.
  await new Promise((r) => setTimeout(r, 0));

  let result;
  try {
    result = analyze({ hole: state.hole, opponents, position, iterations: 1500 });
  } catch (err) {
    console.error(err);
    setStatus('Analysis failed: ' + err.message);
    return;
  }
  state.lastAnalysis = result;
  mHandType.textContent = result.code;
  mTier.textContent = `${result.tier.name} (${result.tier.score})`;
  mEquity.textContent = `${(result.equity * 100).toFixed(1)}%`;
  mAction.textContent = result.action;
  coachNotes.innerHTML = result.notes;
}

positionSelect.addEventListener('change', () => runAnalysis());
playersInput.addEventListener('change', () => runAnalysis());

// ---------------------------------------------------------------------------
// Helpers + persistence
// ---------------------------------------------------------------------------

function setStatus(msg) { statusEl.textContent = msg; }

async function saveSettings() {
  await window.pokerCoach.setSettings({
    region: state.region,
    lastUrl: urlInput.value,
    position: positionSelect.value,
    players: Number(playersInput.value) || 6,
    interval: Number(intervalInput.value) || 3
  });
}

async function init() {
  const s = await window.pokerCoach.getSettings();
  if (s.region) { state.region = s.region; showMarker(); }
  if (s.lastUrl) urlInput.value = s.lastUrl;
  if (s.position) positionSelect.value = s.position;
  if (s.players) playersInput.value = s.players;
  if (s.interval) intervalInput.value = s.interval;
  setStatus('Ready');
}
init();

window.addEventListener('beforeunload', saveSettings);
