// Quick sanity check for the analyzer pipeline. Runs under plain Node
// (the analyzer requires pokersolver, which is CommonJS, so we shim
// window.require by exposing Node's require).

import { createRequire } from 'module';
const require = createRequire(import.meta.url);
globalThis.window = { require };

const { analyze, chenScore, handCode, preflopTier, monteCarloEquity } =
  await import('../src/renderer/lib/analyzer.js');

const cases = [
  { hole: ['Ah', 'Ad'], position: 'UTG', players: 6 },
  { hole: ['Kc', 'Qc'], position: 'CO',  players: 6 },
  { hole: ['7d', '2s'], position: 'BTN', players: 6 },
  { hole: ['Tc', '9c'], position: 'BTN', players: 6 },
  { hole: ['Ah', 'Kd'], position: 'BB',  players: 6 },
  { hole: ['5s', '5h'], position: 'MP',  players: 6 }
];

for (const c of cases) {
  const start = Date.now();
  const res = analyze({
    hole: c.hole,
    opponents: c.players - 1,
    position: c.position,
    iterations: 800
  });
  const ms = Date.now() - start;
  console.log(`${c.hole.join(' ')} pos=${c.position} -> ${res.code} tier=${res.tier.name}(${res.tier.score}) eq=${(res.equity*100).toFixed(1)}% action=${res.action} [${ms}ms]`);
}
