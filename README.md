# Poker Coach

A desktop app that embeds a browser, opens your poker website, watches **only the small rectangle where your two hole cards are shown**, and acts as a real-time learning coach: it tells you the name of the hand, an equity estimate, what tier the hand is, what the textbook action is for your position, and a few sentences of explanation so you actually learn poker rather than blindly follow advice.

It is deliberately scoped to **just your two cards**. The app never reads opponents' cards or the community board, and never automates clicks. It is intended as a study tool for play-money tables, training sites, hand replayers, or reviewing your own play.

> Most real-money online poker rooms forbid real-time assistance (RTA) software. Do **not** use this on a site where it would breach the terms of service. Use it to learn, not to cheat.

## Features

- Embedded browser (a Chromium `<webview>`) that loads any site you want and remembers logins between launches.
- Drag-to-select region for "where my hole cards live" on the table. Saved per-machine; no extra setup.
- Auto-watch loop captures only that region every N seconds and runs OCR (Tesseract.js) plus a suit-colour heuristic to recognise the two cards.
- Manual entry fallback (`Ah Kd`) for when OCR struggles or you just want to study a hand.
- Analysis panel:
  - Hand code (`AA`, `KQs`, `T9o`, ...)
  - Preflop tier using the Chen formula
  - Monte Carlo equity vs. N random opponents (configurable)
  - Position-aware action recommendation (UTG / MP / HJ / CO / BTN / SB / BB)
  - Plain-English coaching notes that explain *why*

## Install & run

Requirements: Node.js 18+ (tested on 22) and the platform-appropriate build tools for Electron.

```bash
npm install
npm start
```

On first run Tesseract.js will download a small English language model (~10 MB) the first time it OCRs a card.

## How to use

1. Launch the app (`npm start`).
2. In the URL bar enter the URL of your poker site (or training site, replayer, etc.) and press **Go**.
3. Log in / sit down at a table. You should see your two hole cards in the embedded browser.
4. Click **📐 Set hole-card region** and drag a tight rectangle around *just* your two cards. The rectangle is saved.
5. Click **▶ Start auto-watch** (or **Capture once** for a one-off read).
6. The right panel updates whenever your cards change: detected hand, tier, equity, recommendation, and a short coaching explanation.
7. If OCR gets a card wrong, type the correct hand in the manual entry box (e.g. `Ah Kd`) and press **Set**.

Tips for good OCR:
- Crop tightly: just the card faces, ideally with the big rank glyph visible. Less background = better.
- Zoom your poker UI so the cards are at least ~40-50 pixels tall.
- Use a UI theme where cards are white with red/black suits (the default for nearly every site).

## Project layout

```
src/
  main/
    main.js          - Electron main process: window, IPC, region capture.
    preload.js       - Bridges IPC into the renderer's window object.
  renderer/
    index.html       - App shell: top bar, browser, coach panel.
    styles.css       - Dark UI theme.
    app.js           - UI wiring: URL bar, region picker, capture loop.
    lib/
      cards.js       - Card primitives (ranks, suits, deck, helpers).
      analyzer.js    - Chen score, preflop tier, Monte Carlo equity,
                       position-aware recommendation, coaching notes.
      recognizer.js  - Two-card OCR + suit colour heuristic.
scripts/
  smoke-analyzer.mjs - Node smoke test for the analyzer pipeline.
```

## Design notes

- **The renderer has Node integration enabled.** This is safe because the
  renderer only loads our own local HTML; the user's untrusted site is
  loaded inside a `<webview>` tag which runs in its own isolated process
  and partition.
- **Card recognition is intentionally simple.** Two cards side-by-side,
  white background, big rank glyph, coloured suit pip. We split the
  region in half, OCR the upper-left of each half for the rank, and
  use red-vs-black pixel ratios plus a width/height aspect heuristic
  to pick the suit. It is correct on the major UIs after a few seconds
  of region tuning, and you can always type the hand by hand.
- **Equity uses Monte Carlo.** 1500 trials per analysis is enough to
  stabilise to within a percent or so. Bumping iterations costs CPU
  but improves precision.
- **Position guidance.** UTG/UTG+1/MP/HJ/CO use explicit open-raise
  ranges roughly aligned with a modern 6-max chart. BTN uses Chen >= 4
  (loose steal range). SB uses Chen >= 6 with limp/raise mixing.
  BB uses tier + equity-vs-required-defending-equity.

## Responsible use

This app is meant to teach. Treat it like a chess engine you consult after a game, not during a money tournament. Anything that lets you make real-time GTO decisions for cash at a real-money site is almost certainly forbidden by that site's TOS and may be illegal in your jurisdiction. Use it on:

- Free-play / play-money tables.
- Training sites such as replayers and quiz tools.
- Your own past hands, while you study them.

The author and contributors take no responsibility for misuse.

## License

MIT.
