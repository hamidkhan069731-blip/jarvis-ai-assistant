// WebSocket client: realtime channel to the JARVIS core.
// Auto-reconnects with backoff and dispatches typed messages to listeners.

export class WSClient {
  constructor(url) {
    this.url = url;
    this.ws = null;
    this.handlers = {};          // type -> [fn]
    this.statusFn = () => {};
    this._backoff = 500;
    this._alive = false;
    this._queue = [];            // messages queued while disconnected
  }

  on(type, fn) {
    (this.handlers[type] ||= []).push(fn);
    return this;
  }
  onStatus(fn) { this.statusFn = fn; return this; }

  connect() {
    this.statusFn("connecting");
    try {
      this.ws = new WebSocket(this.url);
    } catch {
      return this._reconnect();
    }
    this.ws.onopen = () => {
      this._alive = true;
      this._backoff = 500;
      this.statusFn("connected");
      this._queue.forEach((m) => this.ws.send(m));
      this._queue = [];
    };
    this.ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      const list = this.handlers[msg.type] || [];
      list.forEach((fn) => { try { fn(msg); } catch (e) { console.error(e); } });
      (this.handlers["*"] || []).forEach((fn) => fn(msg));
    };
    this.ws.onclose = () => {
      this._alive = false;
      this.statusFn("disconnected");
      this._reconnect();
    };
    this.ws.onerror = () => { try { this.ws.close(); } catch {} };
  }

  _reconnect() {
    this._backoff = Math.min(this._backoff * 1.7, 8000);
    setTimeout(() => this.connect(), this._backoff);
  }

  send(obj) {
    const data = JSON.stringify(obj);
    if (this._alive && this.ws?.readyState === WebSocket.OPEN) this.ws.send(data);
    else this._queue.push(data);
  }

  get connected() { return this._alive; }
}
