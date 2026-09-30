// The arc-reactor orb: a canvas HUD element that visualizes JARVIS's state.
// Real-time rendering via requestAnimationFrame. `setState` changes color and
// motion; `setLevel` feeds it live audio amplitude (0..1) while listening or
// speaking so the waveform ring genuinely reacts to sound.

const STATE_COLORS = {
  idle:             { c: [56, 225, 255],  spin: 0.15, name: "idle" },
  listening:        { c: [63, 240, 168],  spin: 0.4,  name: "listening" },
  thinking:         { c: [56, 225, 255],  spin: 1.4,  name: "thinking" },
  planning:         { c: [124, 178, 255], spin: 1.0,  name: "planning" },
  executing:        { c: [255, 183, 3],   spin: 0.8,  name: "executing" },
  speaking:         { c: [127, 236, 255], spin: 0.5,  name: "speaking" },
  waiting_approval: { c: [255, 202, 74],  spin: 0.2,  name: "awaiting you" },
  watching:         { c: [63, 240, 168],  spin: 0.22, name: "watching" },
  standby:          { c: [86, 112, 130],  spin: 0.04, name: "standby" },
  offline:          { c: [150, 96, 96],   spin: 0.05, name: "offline" },
  error:            { c: [255, 80, 105],  spin: 0.1,  name: "error" },
};

export class Orb {
  constructor(canvas, labelEl) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.labelEl = labelEl;
    this.state = "idle";
    this.level = 0;          // smoothed audio level
    this._targetLevel = 0;
    this.angle = 0;
    this.t = 0;
    this._bars = new Array(64).fill(0);
    this._resize();
    window.addEventListener("resize", () => this._resize());
    this._loop = this._loop.bind(this);
    requestAnimationFrame(this._loop);
  }

  _resize() {
    const dpr = window.devicePixelRatio || 1;
    const size = Math.min(this.canvas.clientWidth || 360, 360);
    this.canvas.width = size * dpr;
    this.canvas.height = size * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.size = size;
  }

  setState(state) {
    if (!STATE_COLORS[state]) state = "idle";
    this.state = state;
    if (this.labelEl) this.labelEl.textContent = STATE_COLORS[state].name;
    if (state !== "listening" && state !== "speaking") this._targetLevel = 0;
  }

  setLevel(v) { this._targetLevel = Math.max(0, Math.min(1, v)); }

  _loop(now) {
    const cfg = STATE_COLORS[this.state] || STATE_COLORS.idle;
    this.t += 0.016;
    this.angle += cfg.spin * 0.03;
    // ease audio level toward target
    this.level += (this._targetLevel - this.level) * 0.18;

    const ctx = this.ctx, S = this.size, cx = S / 2, cy = S / 2;
    const R = S * 0.42;
    ctx.clearRect(0, 0, S, S);
    const [r, g, b] = cfg.c;
    const rgb = (a) => `rgba(${r},${g},${b},${a})`;

    // idle breathing + audio push
    const breathe = 1 + Math.sin(this.t * 1.6) * 0.02 + this.level * 0.06;

    // --- outer segmented ring (rotating) ---
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(this.angle);
    const segs = 48;
    for (let i = 0; i < segs; i++) {
      const a = (i / segs) * Math.PI * 2;
      const long = i % 6 === 0;
      const r1 = R * 0.98, r2 = R * (long ? 1.06 : 1.02);
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * r1, Math.sin(a) * r1);
      ctx.lineTo(Math.cos(a) * r2, Math.sin(a) * r2);
      ctx.strokeStyle = rgb(long ? 0.7 : 0.28);
      ctx.lineWidth = long ? 2 : 1;
      ctx.stroke();
    }
    ctx.restore();

    // --- counter-rotating arc segments ---
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(-this.angle * 1.3);
    ctx.lineWidth = 3;
    ctx.lineCap = "round";
    for (let k = 0; k < 3; k++) {
      const start = (k / 3) * Math.PI * 2;
      ctx.beginPath();
      ctx.arc(0, 0, R * 0.82, start, start + Math.PI * 0.45);
      ctx.strokeStyle = rgb(0.55);
      ctx.shadowColor = rgb(0.8);
      ctx.shadowBlur = 12;
      ctx.stroke();
    }
    ctx.restore();

    // --- audio-reactive waveform ring ---
    const bars = this._bars.length;
    ctx.save();
    ctx.translate(cx, cy);
    for (let i = 0; i < bars; i++) {
      // target height: noise + audio level, symmetric
      const base = 0.12 + Math.abs(Math.sin(this.t * 2 + i * 0.5)) * 0.10;
      const target = base + this.level * (0.3 + Math.abs(Math.sin(i * 0.9)) * 0.5);
      this._bars[i] += (target - this._bars[i]) * 0.25;
      const a = (i / bars) * Math.PI * 2 + this.angle * 0.5;
      const inner = R * 0.6;
      const outer = inner + this._bars[i] * R * 0.7;
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * inner, Math.sin(a) * inner);
      ctx.lineTo(Math.cos(a) * outer, Math.sin(a) * outer);
      ctx.strokeStyle = rgb(0.35 + this._bars[i]);
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    ctx.restore();

    // --- glowing core ---
    const coreR = R * 0.34 * breathe;
    const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR);
    grad.addColorStop(0, "rgba(234,252,255,0.95)");
    grad.addColorStop(0.4, rgb(0.9));
    grad.addColorStop(1, rgb(0));
    ctx.beginPath();
    ctx.fillStyle = grad;
    ctx.arc(cx, cy, coreR, 0, Math.PI * 2);
    ctx.fill();

    // core rim
    ctx.beginPath();
    ctx.arc(cx, cy, coreR * 0.72, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(234,252,255,0.85)";
    ctx.lineWidth = 1.5;
    ctx.stroke();

    // inner triangle (arc-reactor motif)
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(this.angle * 0.5);
    ctx.beginPath();
    for (let i = 0; i < 3; i++) {
      const a = (i / 3) * Math.PI * 2 - Math.PI / 2;
      const rr = coreR * 0.5;
      const x = Math.cos(a) * rr, y = Math.sin(a) * rr;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.strokeStyle = rgb(0.9);
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.restore();

    requestAnimationFrame(this._loop);
  }
}
