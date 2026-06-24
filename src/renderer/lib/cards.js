// Card utilities. We use a compact 2-char string for a card: rank + suit,
// e.g. "Ah", "Td", "9c", "2s". Suits use lowercase letters: c d h s.

export const RANKS = ['2', '3', '4', '5', '6', '7', '8', '9', 'T', 'J', 'Q', 'K', 'A'];
export const SUITS = ['c', 'd', 'h', 's'];
export const SUIT_GLYPH = { c: '♣', d: '♦', h: '♥', s: '♠' };
export const SUIT_NAME  = { c: 'clubs', d: 'diamonds', h: 'hearts', s: 'spades' };
export const RED_SUITS  = new Set(['d', 'h']);

export const RANK_INDEX = Object.fromEntries(RANKS.map((r, i) => [r, i]));

export function isValidCard(card) {
  if (typeof card !== 'string' || card.length !== 2) return false;
  const r = card[0].toUpperCase();
  const s = card[1].toLowerCase();
  return RANK_INDEX[r] !== undefined && SUITS.includes(s);
}

export function normaliseCard(card) {
  if (!isValidCard(card)) return null;
  return card[0].toUpperCase() + card[1].toLowerCase();
}

export function parseHand(text) {
  if (!text) return null;
  const tokens = text
    .replace(/[,]/g, ' ')
    .split(/\s+/)
    .map((t) => t.trim())
    .filter(Boolean);
  const cards = tokens.map(normaliseCard).filter(Boolean);
  if (cards.length < 2) return null;
  // dedupe while preserving order
  const seen = new Set();
  const unique = [];
  for (const c of cards) {
    if (!seen.has(c)) { seen.add(c); unique.push(c); }
  }
  return unique.slice(0, 2);
}

export function cardLabel(card) {
  if (!isValidCard(card)) return '??';
  return card[0].toUpperCase() + SUIT_GLYPH[card[1].toLowerCase()];
}

export function isRedCard(card) {
  return isValidCard(card) && RED_SUITS.has(card[1].toLowerCase());
}

export function fullDeck() {
  const deck = [];
  for (const r of RANKS) for (const s of SUITS) deck.push(r + s);
  return deck;
}

/** Fisher-Yates shuffle, in place. */
export function shuffle(arr, rng = Math.random) {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}
