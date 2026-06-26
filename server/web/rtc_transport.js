// chessmon transport shim — a WebSocket-compatible connection that runs over a WebRTC data channel,
// signaled through comlos.com. Drop-in for `new WebSocket(url)`: same .send / .onopen / .onmessage /
// .onclose / .onerror / .readyState (0/1/2/3) / .close(). A client speaks the normal chessmon JSON
// protocol over it; only the CONSTRUCTION changes.
//
//   const sock = new ChessmonSocket({ signal: '/relay/signal.php', room: 'club42' });
//   sock.onopen    = () => sock.send(JSON.stringify({ type:'hello', ... }));
//   sock.onmessage = e  => handle(JSON.parse(e.data));
//
// Binary (camera JPEG) rides the channel too (.binaryType='arraybuffer'); large frames will need
// chunking — that is phase 3, not here.
(function (g) {
  class ChessmonSocket {
    constructor(opts) {
      opts = opts || {};
      this.CONNECTING = 0; this.OPEN = 1; this.CLOSING = 2; this.CLOSED = 3;
      this.readyState = 0;
      this.binaryType = 'arraybuffer';
      this.onopen = this.onmessage = this.onclose = this.onerror = null;
      this._signal = opts.signal || 'signal.php';
      this._room = opts.room || 'demo';
      this._session = Math.random().toString(36).slice(2);
      this._poll = opts.pollMs || 500;
      this._tries = opts.tries || 60;
      this._pc = null; this._dc = null;
      this._connect();
    }
    async _connect() {
      try {
        const pc = new RTCPeerConnection(); this._pc = pc;
        const dc = pc.createDataChannel('chessmon'); this._dc = dc;
        dc.binaryType = 'arraybuffer';
        dc.onopen = () => { this.readyState = 1; this.onopen && this.onopen({ type: 'open' }); };
        dc.onmessage = (e) => { this.onmessage && this.onmessage({ data: e.data }); };
        dc.onclose = () => { this._closed(); };
        pc.onconnectionstatechange = () => {
          if (['failed', 'disconnected', 'closed'].indexOf(pc.connectionState) >= 0) this._closed();
        };
        await pc.setLocalDescription(await pc.createOffer());
        await this._waitIce(pc);
        await fetch(this._signal, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ room: this._room, session: this._session, kind: 'offer', sdp: pc.localDescription.sdp })
        });
        let ans = null;
        for (let i = 0; i < this._tries && this.readyState === 0; i++) {
          const j = await (await fetch(this._signal + '?room=' + encodeURIComponent(this._room) +
            '&session=' + this._session + '&kind=answer')).json();
          if (j && j.sdp) { ans = j.sdp; break; }
          await new Promise((r) => setTimeout(r, this._poll));
        }
        if (!ans) { this._fail('no answer from the local server'); return; }
        await pc.setRemoteDescription({ type: 'answer', sdp: ans });
      } catch (e) { this._fail(e); }
    }
    _waitIce(pc) {
      if (pc.iceGatheringState === 'complete') return Promise.resolve();
      return new Promise((res) => {
        const done = () => { if (pc.iceGatheringState === 'complete') { pc.removeEventListener('icegatheringstatechange', done); res(); } };
        pc.addEventListener('icegatheringstatechange', done);
        setTimeout(res, 2500);                       // host candidates are usually ready well before this
      });
    }
    _closed() { if (this.readyState !== 3) { this.readyState = 3; this.onclose && this.onclose({ type: 'close' }); } }
    _fail(e) { this.onerror && this.onerror({ type: 'error', error: e }); this._closed(); }
    send(data) { if (this.readyState === 1) this._dc.send(data); }   // string or ArrayBuffer/Blob, just like WebSocket
    close() {
      this.readyState = 2;
      try { this._dc && this._dc.close(); } catch (e) {}
      try { this._pc && this._pc.close(); } catch (e) {}
      this._closed();
    }
  }
  g.ChessmonSocket = ChessmonSocket;
})(typeof window !== 'undefined' ? window : this);
