// Card recognizer.
//
// Strategy: most poker UIs render a card as a white background with a big
// rank glyph (A/K/Q/J/T or a digit) and a coloured suit pip. We:
//   1. Get an image of the user-defined region (already cropped to "just
//      my two cards" by them).
//   2. Split the region in two halves horizontally -> one image per card.
//   3. For each half:
//        a. Detect dominant non-white colour to decide suit colour
//           (red = hearts/diamonds, black = clubs/spades).
//        b. Run Tesseract OCR on the upper-left corner to read the rank.
//        c. Use a shape heuristic on the dominant-colour pixels to decide
//           between the two same-colour suits (hearts vs diamonds,
//           clubs vs spades). For learning purposes the suit-colour alone
//           is enough to drive most analysis; we still attempt full suit
//           detection so equity calculations are correct.
//
// The recognizer is intentionally tolerant: we report confidence so the
// UI can ask the user to confirm.

const req = (typeof window !== 'undefined' && window.require) || (typeof require !== 'undefined' && require);
const Tesseract = req('tesseract.js');

const RANK_ALIAS = {
  '0': 'T', 'O': 'T', 'D': 'T', // Tesseract often confuses T for these
  '1': null,
  'I': null,
  'L': null,
  'B': '8',
  'S': '5',
  'Z': '2',
  'G': '6'
};
const VALID_RANKS = new Set(['2','3','4','5','6','7','8','9','T','J','Q','K','A']);

let workerPromise = null;
function getWorker() {
  if (!workerPromise) {
    workerPromise = (async () => {
      const worker = await Tesseract.createWorker('eng');
      await worker.setParameters({
        tessedit_char_whitelist: '0123456789AKQJTakqjt',
        tessedit_pageseg_mode: '10' // single character
      });
      return worker;
    })();
  }
  return workerPromise;
}

export async function terminateRecognizer() {
  if (workerPromise) {
    const w = await workerPromise;
    await w.terminate();
    workerPromise = null;
  }
}

/**
 * Recognise two hole cards from an image dataURL of the user-defined
 * region. Returns { cards: [card|null, card|null], confidence, debug }.
 */
export async function recognizeHoleCards(dataUrl) {
  const img = await loadImage(dataUrl);

  // Split into two halves left/right.
  const halfW = Math.floor(img.width / 2);
  const left = cropToCanvas(img, 0, 0, halfW, img.height);
  const right = cropToCanvas(img, halfW, 0, img.width - halfW, img.height);

  const [c1, c2] = await Promise.all([
    recognizeOneCard(left),
    recognizeOneCard(right)
  ]);

  const cards = [c1.card, c2.card];
  const confidence = Math.min(c1.confidence, c2.confidence);
  return { cards, confidence, debug: { left: c1, right: c2 } };
}

async function recognizeOneCard(canvas) {
  const { suitColor, colorScore, suit } = detectSuit(canvas);
  const rank = await detectRank(canvas);
  if (!rank.value) {
    return { card: null, confidence: 0, reason: 'rank-unknown', rank, suit, colorScore };
  }
  const card = rank.value + (suit || (suitColor === 'red' ? 'h' : 's'));
  // Combine OCR confidence with colour confidence
  const conf = Math.round(0.6 * rank.confidence + 0.4 * colorScore * 100);
  return { card, confidence: conf, rank, suit, suitColor, colorScore };
}

// ---------------------------------------------------------------------------
// Image helpers
// ---------------------------------------------------------------------------

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
}

function cropToCanvas(img, x, y, w, h) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  c.getContext('2d').drawImage(img, x, y, w, h, 0, 0, w, h);
  return c;
}

// ---------------------------------------------------------------------------
// Suit colour detection (red vs black). Looks at non-white, non-grey pixels.
// ---------------------------------------------------------------------------

function detectSuit(canvas) {
  const ctx = canvas.getContext('2d');
  const { data, width, height } = ctx.getImageData(0, 0, canvas.width, canvas.height);
  let red = 0, black = 0;
  let redXs = [], redYs = [];
  let blackXs = [], blackYs = [];

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 4;
      const r = data[i], g = data[i + 1], b = data[i + 2];
      // skip near-white
      if (r > 220 && g > 220 && b > 220) continue;
      // skip near-grey
      const maxc = Math.max(r, g, b), minc = Math.min(r, g, b);
      if (maxc - minc < 40 && maxc < 80) {
        black++;
        blackXs.push(x); blackYs.push(y);
        continue;
      }
      if (r > 130 && r > g + 40 && r > b + 40) {
        red++;
        redXs.push(x); redYs.push(y);
      }
    }
  }

  const total = red + black;
  const suitColor = red > black ? 'red' : 'black';
  const colorScore = total === 0 ? 0 : Math.max(red, black) / total;

  // Suit shape heuristic: ratio of width-extent to height-extent of the
  // largest blob of the dominant colour pixels.
  let suit = null;
  const xs = suitColor === 'red' ? redXs : blackXs;
  const ys = suitColor === 'red' ? redYs : blackYs;
  if (xs.length > 25) {
    const sx = stats(xs), sy = stats(ys);
    const aspect = (sx.max - sx.min + 1) / Math.max(1, (sy.max - sy.min + 1));
    // diamonds tend to be vertically tall and narrow (aspect ~ 0.7),
    // hearts a bit wider (aspect ~ 0.95). Same for spades vs clubs.
    if (suitColor === 'red') {
      suit = aspect < 0.85 ? 'd' : 'h';
    } else {
      suit = aspect < 0.85 ? 's' : 'c';
    }
  }
  return { suitColor, colorScore, suit };
}

function stats(arr) {
  let min = Infinity, max = -Infinity, sum = 0;
  for (const v of arr) { if (v < min) min = v; if (v > max) max = v; sum += v; }
  return { min, max, mean: sum / arr.length };
}

// ---------------------------------------------------------------------------
// Rank OCR. We OCR the top-left corner where most card UIs print the rank
// large and clearly.
// ---------------------------------------------------------------------------

async function detectRank(canvas) {
  const w = canvas.width, h = canvas.height;
  // Crop top-left quadrant; this is where the big rank glyph usually sits.
  const cropW = Math.max(24, Math.floor(w * 0.55));
  const cropH = Math.max(24, Math.floor(h * 0.55));
  const crop = document.createElement('canvas');
  crop.width = cropW; crop.height = cropH;
  crop.getContext('2d').drawImage(canvas, 0, 0, cropW, cropH, 0, 0, cropW, cropH);

  // Boost contrast: convert to binary based on luminance.
  const binCanvas = binarize(crop);

  // Up-scale 3x so Tesseract has enough pixels to work with.
  const up = document.createElement('canvas');
  const scale = 3;
  up.width = binCanvas.width * scale;
  up.height = binCanvas.height * scale;
  const upctx = up.getContext('2d');
  upctx.imageSmoothingEnabled = false;
  upctx.drawImage(binCanvas, 0, 0, up.width, up.height);

  const worker = await getWorker();
  const dataUrl = up.toDataURL('image/png');
  const { data } = await worker.recognize(dataUrl);
  const raw = (data.text || '').trim().replace(/\s+/g, '');
  const conf = data.confidence || 0;

  const value = mapRank(raw);
  return { value, raw, confidence: conf };
}

function binarize(canvas) {
  const ctx = canvas.getContext('2d');
  const { width: w, height: h } = canvas;
  const imgd = ctx.getImageData(0, 0, w, h);
  const d = imgd.data;
  for (let i = 0; i < d.length; i += 4) {
    const r = d[i], g = d[i + 1], b = d[i + 2];
    // Treat reds and dark pixels as foreground.
    const isRed = r > 130 && r > g + 40 && r > b + 40;
    const lum = 0.299 * r + 0.587 * g + 0.114 * b;
    const isFg = isRed || lum < 110;
    const v = isFg ? 0 : 255;
    d[i] = d[i + 1] = d[i + 2] = v; d[i + 3] = 255;
  }
  ctx.putImageData(imgd, 0, 0);
  return canvas;
}

function mapRank(raw) {
  if (!raw) return null;
  // Tesseract may return e.g. "10", "1O", "lO", or single chars.
  const s = raw.toUpperCase().replace(/[^0-9AKQJT]/g, '');
  if (!s) return null;
  if (s === '10' || s === '1O' || s === 'IO' || s === 'LO') return 'T';
  const first = s[0];
  if (VALID_RANKS.has(first)) return first;
  if (RANK_ALIAS[first]) return RANK_ALIAS[first];
  return null;
}
