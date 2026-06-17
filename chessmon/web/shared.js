// Shared client helpers for both device pages (board + clock).
// Holds the polled server STATE and notifies page-specific renderers.

const GLYPH = {K:'♔',Q:'♕',R:'♖',B:'♗',N:'♘',P:'♙',
               k:'♚',q:'♛',r:'♜',b:'♝',n:'♞',p:'♟'};
const FILES = 'abcdefgh';

let STATE = null;
let _lastFetch = 0;
const _subs = [];

function onState(fn){ _subs.push(fn); if (STATE) fn(STATE); }
function _apply(s){ STATE = s; _lastFetch = performance.now(); for (const fn of _subs) fn(s); }

async function poll(){
  try { const r = await fetch('/state'); _apply(await r.json()); } catch (e) {}
}
async function post(path, body){
  try {
    const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
                                 body: JSON.stringify(body || {})});
    _apply(await r.json());
  } catch (e) {}
}

function fenToGrid(fen){
  const rows = fen.split(' ')[0].split('/');
  const g = [];
  for (const row of rows){
    const r = [];
    for (const ch of row){
      if (ch >= '1' && ch <= '8'){ for (let i=0;i<+ch;i++) r.push(null); }
      else r.push(ch);
    }
    g.push(r);
  }
  return g; // g[rank8..rank1][a..h]
}
function sqName(r, c){ return FILES[c] + (8 - r); }

function fmtTime(s){
  s = Math.max(0, s);
  if (s < 20) return s.toFixed(1);
  const m = (s/60)|0, sec = Math.floor(s % 60);
  return m + ':' + String(sec).padStart(2,'0');
}
function liveTime(color){
  if (!STATE) return 0;
  let s = STATE.times[color];
  if (STATE.active === color && STATE.mode !== 'gameover')
    s -= (performance.now() - _lastFetch) / 1000;
  return Math.max(0, s);
}

function startPolling(ms){ setInterval(poll, ms || 500); poll(); }
