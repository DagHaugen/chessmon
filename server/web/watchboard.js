/* Shared chessmon watch-board -- the IDENTICAL live board used by viewers.html and monitor.html.
   cmWatchBoard(game, orient) -> a DOM element ('land' | 'port'); cmTickBoard(el, game) refreshes its clocks.
   `game` is the page's per-table object: {t (the table), w, b, turn, running, syncAt, sug}. */
(function () {
  const G = {p:'♟',n:'♞',b:'♝',r:'♜',q:'♛',k:'♚'};
  function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
  function names(t){const w=t.match&&t.match.white?t.match.white.name:'White',b=t.match&&t.match.black?t.match.black.name:'Black';return [w,b];}
  function fmtClk(ms){if(ms==null)return '–';const s=Math.floor(ms/1000),h=Math.floor(s/3600),m=Math.floor(s%3600/60),ss=s%60,p=n=>String(n).padStart(2,'0');return h>0?h+':'+p(m)+':'+p(ss):m+':'+p(ss);}
  function live(g,side){let ms=side==='w'?g.w:g.b;if(ms==null)return null;if(g.running&&g.turn===side)ms-=(performance.now()-g.syncAt);return Math.max(0,ms);}
  function pctOf(a,i){const t=(a[0]+a[1]+a[2])||1;return Math.round(a[i]/t*100);}
  function posBar(w){if(!w)return '<div class="wb-wdl"></div><div class="wb-wdlk"><span>&nbsp;</span></div>';   // placeholder (empty bar) while waiting for Stockfish -> reserves the height
    const t=(w[0]+w[1]+w[2])||1;
    return '<div class="wb-wdl"><span class="ww" style="width:'+(w[0]/t*100)+'%"></span><span class="wd" style="width:'+(w[1]/t*100)+'%"></span><span class="wb2" style="width:'+(w[2]/t*100)+'%"></span></div>'
         +'<div class="wb-wdlk"><span>White '+pctOf(w,0)+'%</span><span>Draw '+pctOf(w,1)+'%</span><span>Black '+pctOf(w,2)+'%</span></div>';}
  function pieceGlyph(san){const c=(san[0]==='O')?'k':(('KQRBN'.includes(san[0])?san[0]:'P').toLowerCase());return G[c]||'';}
  function pieceImg(san,turn){const p=(san[0]==='O')?'K':('KQRBN'.includes(san[0])?san[0]:'P'),c=(turn==='b')?'b':'w';return '<img class="pcg'+(c==='b'?' chip':'')+'" src="pieces/'+c+p+'.svg" alt="">';}   // SVG piece in the side-to-move colour (the Unicode glyph was unreadable on iOS); black pieces get a light chip so they don't vanish into the dark panel
  function moveColor(m){if(m.mate!=null)return m.mate>0?'#2fe08a':'#ff5a5a';const cp=m.cp;if(cp==null)return 'var(--mut)';if(cp>=150)return '#2fe08a';if(cp>=40)return '#82c4a0';if(cp>-40)return 'var(--mut)';if(cp>-150)return '#d39090';return '#ff5a5a';}
  function moveTable(moves,turn){moves=moves||[];let rows='';
    for(let i=0;i<3;i++){const m=moves[i];
      if(m){const w=m.wdl||[0,0,0],c=moveColor(m);
        rows+='<tr><td class="mc" style="color:'+c+'">'+pieceImg(m.san,turn)+esc(m.san)+'</td><td>'+pctOf(w,0)+'%</td><td>'+pctOf(w,1)+'%</td><td>'+pctOf(w,2)+'%</td></tr>';}
      else rows+='<tr class="wb-empty"><td class="mc"><span class="pcg"></span></td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>';}   // always 3 rows -> fixed height while Stockfish thinks
    return '<div class="wb-sgh">Stockfish suggestions</div><table class="wb-sgt"><thead><tr><th>Move</th><th>Win</th><th>Draw</th><th>Lose</th></tr></thead><tbody>'+rows+'</tbody></table>';}
  function settled(t){if(!t||!t.result)return '';const who=t.result==='1-0'?'White won':t.result==='0-1'?'Black won':'Draw';
    const how={checkmate:'by checkmate',stalemate:'by stalemate',insufficient_material:'insufficient material',threefold_repetition:'by repetition',fivefold_repetition:'by repetition',fifty_moves:'by the 50-move rule',seventyfive_moves:'by the 75-move rule',timeout:'on time',resignation:'by resignation',agreement:'by agreement'}[t.termination]||'';
    return how?who+' '+how:who;}
  function sq(grid,r,f,piece){const d=document.createElement('div');d.className='wb-sq '+((r+f)%2?'d':'l');
    if(piece){const i=document.createElement('img');i.alt=piece;
      i.onerror=()=>{i.remove();const sp=document.createElement('span');sp.textContent=G[piece.toLowerCase()]||'';sp.style.color=(piece<'a')?'#fff':'#111';d.append(sp);};
      i.src='pieces/'+(piece<'a'?'w':'b')+piece.toUpperCase()+'.svg';d.append(i);}grid.append(d);}
  function renderBoard(grid,fen){grid.innerHTML='';const place=(fen||'8/8/8/8/8/8/8/8').split(' ')[0];
    place.split('/').forEach((row,r)=>{let f=0;for(const ch of row){if(ch>='1'&&ch<='8'){for(let k=0;k<+ch;k++){sq(grid,r,f);f++;}}else{sq(grid,r,f,ch);f++;}}});}
  function movesHtml(t){const san=t.san||[];let h='';for(let i=0;i<san.length;i+=2)h+='<div><span class="mvn">'+(i/2+1)+'.</span>'+esc(san[i])+(san[i+1]?' '+esc(san[i+1]):'')+'</div>';return h||'<span style="color:var(--mut)">no moves yet</span>';}
  function panel(g,side,name,fin,t){const isW=side==='w';const el=document.createElement('div');
    el.className='wb-pl '+(isW?'white':'black')+((!fin&&g.turn===side)?' act':'')+(((isW&&t.result==='1-0')||(!isW&&t.result==='0-1'))?' win':'');
    el.innerHTML='<span class="nm">'+esc(name)+'</span><span class="ck" data-side="'+side+'">'+fmtClk(live(g,side))+'</span>';return el;}
  // The eval bar + suggestions area is RESERVED once a game has suggestions, so the layout doesn't
  // resize as Stockfish clears (after a move) and re-fills. sugInner -> null means no suggestions area.
  function barInner(g,t,fin){if(fin)return '<div class="wb-banner">'+esc(settled(t))+'</div>';
    if(!g.sug)return '';                                   // suggestions off / none seen yet -> bar collapses
    const s=(g.sug.fen===t.fen)?g.sug:null;return posBar(s&&s.wdl_white?s.wdl_white:null);}   // eval bar or placeholder
  function sugInner(g,t,fin){if(fin||!g.sug)return null;   // null -> no suggestions area at all
    const s=(g.sug.fen===t.fen)?g.sug:null;return moveTable(s?s.moves:[],g.turn);}   // fixed 3-row table (blank while waiting)

  window.cmWatchBoard = function (g, orient) {
    const t=g.t,nm=names(t),fin=!!t.result;
    const wb=document.createElement('div');wb.className='wb '+(orient==='port'?'port':'land');
    const bar=document.createElement('div');bar.className='wb-bar';bar.innerHTML=barInner(g,t,fin);
    const bwrap=document.createElement('div');bwrap.className='wb-bwrap';
    const bd=document.createElement('div');bd.className='wb-board';renderBoard(bd,t.fen);bwrap.append(bd);
    const side=document.createElement('div');side.className='wb-side';
    side.append(panel(g,'b',nm[1],fin,t));
    const mid=document.createElement('div');mid.className='wb-mid';
    const mv=document.createElement('div');mv.className='wb-moves';mv.innerHTML=movesHtml(t);mid.append(mv);
    const sgHtml=sugInner(g,t,fin);
    if(sgHtml!=null){const sg=document.createElement('div');sg.className='wb-sug';sg.innerHTML=sgHtml;mid.append(sg);}
    side.append(mid);
    side.append(panel(g,'w',nm[0],fin,t));
    wb.append(bar,bwrap,side);
    return wb;
  };
  window.cmFit = function (wb) {   // landscape: size the board to the largest square that fits, match the eval bar to it
    if (!wb) return;
    const mv = wb.querySelector('.wb-moves'); if (mv) { mv.scrollTop = mv.scrollHeight; requestAnimationFrame(function () { mv.scrollTop = mv.scrollHeight; }); }   // keep the latest move in view (sync + deferred, so it sticks on a freshly-built monitor grid too)
    if (!wb.classList.contains('land')) return;   // portrait is sized by CSS
    const bwrap = wb.querySelector('.wb-bwrap'), board = wb.querySelector('.wb-board'), bar = wb.querySelector('.wb-bar');
    if (!bwrap || !board) return;
    const s = Math.min(bwrap.clientWidth, bwrap.clientHeight) | 0;
    if (s <= 0) return;
    board.style.width = board.style.height = s + 'px';
    if (bar) { bar.style.width = s + 'px'; bar.style.marginLeft = 'auto'; bar.style.marginRight = 'auto'; }
    const side = wb.querySelector('.wb-side'); if (side) side.style.height = s + 'px';   // info column matches the board height -> white sits at the board's bottom edge
  };
  window.cmTickBoard = function (el, g) {
    el.querySelectorAll('.wb-pl .ck').forEach(c => { c.textContent = fmtClk(live(g, c.dataset.side)); });
  };
  window.cmUpdateSug = function (el, g) {   // refresh the eval bar + suggestion table in place (no board re-render)
    const t = g.t, fin = !!t.result;
    const bar = el.querySelector('.wb-bar');
    if (bar) bar.innerHTML = barInner(g, t, fin);
    const mid = el.querySelector('.wb-mid');
    if (mid) {
      const mv = mid.querySelector('.wb-moves');
      const wasBottom = mv && (mv.scrollHeight - mv.scrollTop - mv.clientHeight < 24);   // was it pinned to the latest move?
      const sgHtml = sugInner(g, t, fin);
      let sg = mid.querySelector('.wb-sug');
      if (sgHtml != null) {
        if (!sg) { sg = document.createElement('div'); sg.className = 'wb-sug'; mid.append(sg); }
        sg.innerHTML = sgHtml;
      } else if (sg) { sg.remove(); }
      if (mv && wasBottom) requestAnimationFrame(function () { mv.scrollTop = mv.scrollHeight; });   // re-pin to the latest move (deferred; skipped if scrolled up)
    }
  };
  // small shared helpers the lobby / ingest reuse, so a page doesn't keep its own copy
  window.cmNames = names; window.cmEsc = esc; window.cmFmtClk = fmtClk; window.cmLive = live; window.cmSettled = settled;
  window.cmBaseMs = function (t) { return t.match && t.match.format ? t.match.format.base_ms : null; };
})();
