// Poker analysis: preflop hand evaluation, Chen score, Monte Carlo
// equity vs N random opponents, and human-readable coaching advice.
//
// We only see the user's two hole cards by design, so this analyzer is
// preflop-focused. The notes also talk about what to do on the flop.

import { RANK_INDEX, fullDeck, shuffle, SUIT_NAME, isRedCard, cardLabel } from './cards.js';

// pokersolver is a CommonJS module. The renderer runs with node integration
// enabled (see main.js comment), so we can require() it directly.
let Hand = null;
function getHand() {
  if (Hand) return Hand;
  const req = (typeof window !== 'undefined' && window.require) || (typeof require !== 'undefined' && require);
  if (!req) throw new Error('Node require not available in renderer');
  const mod = req('pokersolver');
  Hand = mod.Hand;
  if (!Hand) throw new Error('pokersolver did not expose Hand');
  return Hand;
}

// ---------------------------------------------------------------------------
// Chen formula score - classic preflop heuristic.
// ---------------------------------------------------------------------------

const CHEN_VALUES = {
  A: 10, K: 8, Q: 7, J: 6,
  T: 5, '9': 4.5, '8': 4, '7': 3.5, '6': 3, '5': 2.5, '4': 2, '3': 1.5, '2': 1
};

export function chenScore([c1, c2]) {
  const r1 = c1[0], r2 = c2[0];
  const s1 = c1[1], s2 = c2[1];
  const v1 = CHEN_VALUES[r1], v2 = CHEN_VALUES[r2];
  let score = Math.max(v1, v2);

  if (r1 === r2) {
    score = Math.max(score * 2, 5);
  } else {
    if (s1 === s2) score += 2;
    const gap = Math.abs(RANK_INDEX[r1] - RANK_INDEX[r2]) - 1;
    if (gap === 0) score += 0;
    else if (gap === 1) score -= 1;
    else if (gap === 2) score -= 2;
    else if (gap === 3) score -= 4;
    else score -= 5;
    // straight bonus for connected lower cards
    const lo = Math.min(RANK_INDEX[r1], RANK_INDEX[r2]);
    if (gap <= 1 && lo < RANK_INDEX['Q']) score += 1;
  }
  return Math.round(score * 2) / 2;
}

/** Compact label: "AKs", "QQ", "T9o". */
export function handCode([c1, c2]) {
  const a = c1[0], b = c2[0];
  const suited = c1[1] === c2[1];
  const ia = RANK_INDEX[a], ib = RANK_INDEX[b];
  const hi = ia >= ib ? a : b;
  const lo = ia >= ib ? b : a;
  if (a === b) return a + b;
  return hi + lo + (suited ? 's' : 'o');
}

export function preflopTier([c1, c2]) {
  const score = chenScore([c1, c2]);
  if (score >= 10) return { name: 'Premium', score, color: 'good' };
  if (score >= 8)  return { name: 'Strong',  score, color: 'good' };
  if (score >= 6)  return { name: 'Playable', score, color: 'ok' };
  if (score >= 4)  return { name: 'Speculative', score, color: 'ok' };
  return { name: 'Trash', score, color: 'bad' };
}

// ---------------------------------------------------------------------------
// Monte Carlo equity vs N random opponents to showdown.
// ---------------------------------------------------------------------------

/**
 * @param {string[]} hole - two cards like ["Ah","Kd"]
 * @param {number} opponents - number of opposing players (1..9)
 * @param {number} iterations - Monte Carlo trials (default 2000)
 */
export function monteCarloEquity(hole, opponents = 5, iterations = 2000) {
  const Hand = getHand();
  const board = [];
  const deckStart = fullDeck().filter((c) => !hole.includes(c));

  let wins = 0, ties = 0;

  for (let i = 0; i < iterations; i++) {
    const deck = shuffle(deckStart.slice());
    const community = deck.slice(0, 5);
    const my = Hand.solve(hole.concat(community).map(toSolverCard));
    const hands = [my];
    let idx = 5;
    for (let o = 0; o < opponents; o++) {
      const oh = [deck[idx++], deck[idx++]];
      hands.push(Hand.solve(oh.concat(community).map(toSolverCard)));
    }
    const winners = Hand.winners(hands);
    const won = winners.includes(my);
    if (won && winners.length === 1) wins++;
    else if (won) ties++;
  }

  const equity = (wins + ties / 2) / iterations;
  return { equity, wins, ties, iterations, board };
}

// pokersolver uses "Ah", "Tc", "2s" -- rank uppercased, suit lowercase.
// That already matches our format, but enforce it.
function toSolverCard(c) {
  return c[0].toUpperCase() + c[1].toLowerCase();
}

// ---------------------------------------------------------------------------
// Coaching advice
// ---------------------------------------------------------------------------

// Approximate "open from this position" lookup. Keys are hand codes.
// Categories: OPEN, RAISE_3BET, CALL_ONLY, FOLD.
const POSITION_RANGES = {
  UTG:   { open: new Set(['AA','KK','QQ','JJ','TT','99','88','AKs','AKo','AQs','AQo','AJs','KQs']) },
  'UTG+1': { open: new Set(['AA','KK','QQ','JJ','TT','99','88','77','AKs','AKo','AQs','AQo','AJs','AJo','KQs','KJs']) },
  MP:    { open: new Set(['AA','KK','QQ','JJ','TT','99','88','77','66','AKs','AKo','AQs','AQo','AJs','AJo','ATs','KQs','KJs','QJs']) },
  HJ:    { open: new Set(['AA','KK','QQ','JJ','TT','99','88','77','66','55','AKs','AKo','AQs','AQo','AJs','AJo','ATs','A9s','KQs','KQo','KJs','KTs','QJs','QTs','JTs']) },
  CO:    { open: new Set(['AA','KK','QQ','JJ','TT','99','88','77','66','55','44','AKs','AKo','AQs','AQo','AJs','AJo','ATs','A9s','A8s','A7s','A5s','KQs','KQo','KJs','KJo','KTs','K9s','QJs','QJo','QTs','Q9s','JTs','J9s','T9s','98s']) },
  BTN:   null, // computed via Chen >= 4
  SB:    null, // we mostly limp/3-bet from SB; use Chen >= 5
  BB:    null  // defend wide; we recommend based on equity
};

export function positionAdvice(code, position) {
  const range = POSITION_RANGES[position];
  if (range) {
    if (range.open.has(code)) return 'Open-raise';
    return 'Fold (out of opening range)';
  }
  return null;
}

/**
 * Produce a high level recommendation given hand + position + equity.
 */
export function recommend({ code, tier, equity, opponents, position }) {
  const posMsg = positionAdvice(code, position);
  if (posMsg) return posMsg;

  // Looser positions
  if (position === 'BTN') {
    if (tier.score >= 4) return 'Open-raise';
    return 'Fold';
  }
  if (position === 'SB') {
    if (tier.score >= 6) return 'Open-raise or 3-bet';
    if (tier.score >= 5) return 'Limp / Mixed';
    return 'Fold';
  }
  if (position === 'BB') {
    // Premium hands are always at least a call (and usually a 3-bet).
    if (tier.score >= 9) return '3-bet for value';
    if (tier.score >= 7) return '3-bet / Call';
    // Otherwise use a pot-odds approximation: defending vs a 2.5x raise
    // needs about 25% equity (we already have 1bb invested).
    if (equity >= 0.28) return 'Call (defend)';
    if (equity >= 0.22) return 'Mixed call/fold';
    return 'Fold';
  }
  return tier.score >= 6 ? 'Open-raise' : 'Fold';
}

/**
 * Build a short list of coaching notes / explanations for the user.
 */
export function coachNotes({ hole, code, tier, equity, opponents, position }) {
  const [c1, c2] = hole;
  const suited = c1[1] === c2[1];
  const paired = c1[0] === c2[0];
  const gap = Math.abs(RANK_INDEX[c1[0]] - RANK_INDEX[c2[0]]) - 1;

  const lines = [];
  lines.push(`Your hand is <em>${cardLabel(c1)} ${cardLabel(c2)}</em> (${code}).`);

  if (paired) {
    lines.push(`This is a <em>pocket pair</em>. It is already a made hand pre-flop. With small pairs (22-66) you typically <em>set-mine</em>: try to see a cheap flop and only continue when you flop a set (about 1 time in 8).`);
  } else if (suited) {
    if (gap <= 1) lines.push(`Suited connector / one-gapper: good <em>implied odds</em> hand — plays well in multi-way pots because of straight and flush potential.`);
    else lines.push(`Suited but disconnected: the flush draws give some value, but the straight potential is limited.`);
  } else {
    if (gap === 0) lines.push(`Offsuit connectors are okay in late position; without a flush draw they need to make a pair or straight to win.`);
    else if (RANK_INDEX[c1[0]] >= RANK_INDEX['T'] && RANK_INDEX[c2[0]] >= RANK_INDEX['T'])
      lines.push(`Two big offsuit cards. Strong pre-flop but watch out — top pair with a weak kicker can be expensive on bad boards.`);
    else lines.push(`Offsuit and disconnected. Most of these hands are folds outside of the blinds or button.`);
  }

  const pct = (equity * 100).toFixed(1);
  lines.push(`Estimated equity vs ${opponents} random opponent${opponents === 1 ? '' : 's'}: <em>${pct}%</em>. Tier: <em>${tier.name}</em> (Chen score ${tier.score}).`);

  // Position guidance
  const earlyPositions = ['UTG', 'UTG+1', 'MP'];
  if (earlyPositions.includes(position)) {
    lines.push(`Early position requires a tight range — players left to act can wake up with anything.`);
  } else if (position === 'CO' || position === 'BTN') {
    lines.push(`Late position lets you play wider, both for value and to steal the blinds.`);
  } else if (position === 'SB' || position === 'BB') {
    lines.push(`From the blinds you act first on every postflop street, so be careful with marginal hands.`);
  }

  // Practical tips
  if (tier.score >= 8) {
    lines.push(`<em>Tip:</em> with strong hands, build the pot — most beginners under-raise their premiums.`);
  } else if (tier.score < 4) {
    lines.push(`<em>Tip:</em> folding is free. Saving a few big blinds with weak hands compounds over a session.`);
  } else {
    lines.push(`<em>Tip:</em> playable hands need a plan. Decide before the flop how you'll respond to a raise.`);
  }

  return `<ul><li>${lines.join('</li><li>')}</li></ul>`;
}

/**
 * One-shot analysis: takes two cards and context, returns everything the UI
 * needs to display.
 */
export function analyze({ hole, opponents = 5, position = 'BTN', iterations = 2000 }) {
  const code = handCode(hole);
  const tier = preflopTier(hole);
  const { equity } = monteCarloEquity(hole, opponents, iterations);
  const action = recommend({ code, tier, equity, opponents, position });
  const notes = coachNotes({ hole, code, tier, equity, opponents, position });
  return { code, tier, equity, action, notes };
}
