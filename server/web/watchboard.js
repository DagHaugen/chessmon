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
  function posBar(w){const t=(w[0]+w[1]+w[2])||1;
    return '<div class="wb-wdl"><span class="ww" style="width:'+(w[0]/t*100)+'%"></span><span class="wd" style="width:'+(w[1]/t*100)+'%"></span><span class="wb2" style="width:'+(w[2]/t*100)+'%"></span></div>'
         +'<div class="wb-wdlk"><span>White '+pctOf(w,0)+'%</span><span>Draw '+pctOf(w,1)+'%</span><span>Black '+pctOf(w,2)+'%</span></div>';}
  function pieceGlyph(san){const c=(san[0]==='O')?'k':(('KQRBN'.includes(san[0])?san[0]:'P').toLowerCase());return G[c]||'';}
  function moveColor(m){if(m.mate!=null)return m.mate>0?'#2fe08a':'#ff5a5a';const cp=m.cp;if(cp==null)return 'var(--mut)';if(cp>=150)return '#2fe08a';if(cp>=40)return '#82c4a0';if(cp>-40)return 'var(--mut)';if(cp>-150)return '#d39090';return '#ff5a5a';}
  function moveTable(moves){let rows='';moves.forEach(m=>{const w=m.wdl||[0,0,0],c=moveColor(m);
    rows+='<tr><td class="mc" style="color:'+c+'"><span class="pcg">'+pieceGlyph(m.san)+'</span>'+esc(m.san)+'</td><td>'+pctOf(w,0)+'%</td><td>'+pctOf(w,1)+'%</td><td>'+pctOf(w,2)+'%</td></tr>';});
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

  window.cmWatchBoard = function (g, orient) {
    const t=g.t,nm=names(t),fin=!!t.result;
    const sug=(g.sug&&g.sug.fen===t.fen)?g.sug:null;
    const wb=document.createElement('div');wb.className='wb '+(orient==='port'?'port':'land');
    const bcol=document.createElement('div');bcol.className='wb-bcol';
    const bar=document.createElement('div');bar.className='wb-bar';
    if(fin)bar.innerHTML='<div class="wb-banner">'+esc(settled(t))+'</div>';
    else if(sug&&sug.wdl_white)bar.innerHTML=posBar(sug.wdl_white);
    bcol.append(bar);
    const bwrap=document.createElement('div');bwrap.className='wb-bwrap';
    const bd=document.createElement('div');bd.className='wb-board';renderBoard(bd,t.fen);bwrap.append(bd);bcol.append(bwrap);
    const side=document.createElement('div');side.className='wb-side';
    side.append(panel(g,'b',nm[1],fin,t));
    const mid=document.createElement('div');mid.className='wb-mid';
    const mv=document.createElement('div');mv.className='wb-moves';mv.innerHTML=movesHtml(t);mid.append(mv);
    if(!fin&&sug&&sug.moves&&sug.moves.length){const sg=document.createElement('div');sg.className='wb-sug';sg.innerHTML=moveTable(sug.moves);mid.append(sg);}
    side.append(mid);
    side.append(panel(g,'w',nm[0],fin,t));
    wb.append(bcol,side);
    return wb;
  };
  window.cmTickBoard = function (el, g) {
    el.querySelectorAll('.wb-pl .ck').forEach(c => { c.textContent = fmtClk(live(g, c.dataset.side)); });
  };
  window.cmUpdateSug = function (el, g) {   // refresh just the eval bar + suggestion table (no board re-render)
    const t = g.t, fin = !!t.result, sug = (g.sug && g.sug.fen === t.fen) ? g.sug : null;
    const bar = el.querySelector('.wb-bar');
    if (bar) bar.innerHTML = fin ? '<div class="wb-banner">' + esc(settled(t)) + '</div>' : ((sug && sug.wdl_white) ? posBar(sug.wdl_white) : '');
    const mid = el.querySelector('.wb-mid');
    if (mid) {
      let sg = mid.querySelector('.wb-sug');
      if (!fin && sug && sug.moves && sug.moves.length) {
        if (!sg) { sg = document.createElement('div'); sg.className = 'wb-sug'; mid.append(sg); }
        sg.innerHTML = moveTable(sug.moves);
      } else if (sg) { sg.remove(); }
    }
  };
  // small shared helpers the lobby / ingest reuse, so a page doesn't keep its own copy
  window.cmNames = names; window.cmEsc = esc; window.cmFmtClk = fmtClk; window.cmLive = live; window.cmSettled = settled;
  window.cmBaseMs = function (t) { return t.match && t.match.format ? t.match.format.base_ms : null; };
})();
