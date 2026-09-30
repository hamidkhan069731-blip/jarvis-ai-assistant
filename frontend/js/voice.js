// Voice pipeline (browser side): speech-to-text, text-to-speech, wake word,
// and live mic-level metering that drives the orb.
//
// Privacy: the microphone is only opened while actively listening (push-to-talk
// or after the wake word). Nothing is recorded or stored. Wake-word listening is
// opt-in and can be turned off at any time. Browser STT (Web Speech API) streams
// audio to the browser's recognition service — this is documented in SECURITY.md.

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

export class Voice {
  constructor(handlers = {}) {
    this.on = {
      result: () => {}, listening: () => {}, level: () => {},
      wake: () => {}, speaking: () => {}, error: () => {}, ...handlers,
    };
    this.lang = "en-US";
    this.wakeWord = "hey jarvis";
    this.wakeEnabled = false;
    this.rate = 1.0;
    this.volume = 1.0;
    this.preferredVoice = "";
    this._recog = null;
    this._mode = "idle";      // idle | wake | command
    this._meterRaf = 0;
    this._speaking = false;
    if (SR) this._buildRecognizer();
    if ("speechSynthesis" in window) speechSynthesis.getVoices(); // warm voice list
  }

  // -------- capability reporting (honest, no fakes) -------- //
  get sttSupported() { return !!SR; }
  get ttsSupported() { return "speechSynthesis" in window; }

  setLang(lang) {
    this.lang = lang;
    if (this._recog) this._recog.lang = lang;
  }
  configure({ wakeWord, wakeEnabled, rate, volume, voice } = {}) {
    if (wakeWord != null) this.wakeWord = String(wakeWord).toLowerCase();
    if (rate != null) this.rate = Math.max(0.5, Math.min(2, rate / 175)); // map 80–300 -> ~0.45–1.7
    if (volume != null) this.volume = volume;
    if (voice != null) this.preferredVoice = voice;
    if (wakeEnabled != null) {
      this.wakeEnabled = wakeEnabled;
      wakeEnabled ? this.startWake() : this.stopWake();
    }
  }

  // ------------------- recognition ------------------- //
  _buildRecognizer() {
    const r = new SR();
    r.continuous = false;
    r.interimResults = true;
    r.lang = this.lang;
    r.onresult = (e) => this._onResult(e);
    r.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        this.on.error("Microphone permission denied.");
        this.wakeEnabled = false;
      }
    };
    r.onend = () => this._onEnd();
    this._recog = r;
  }

  _onResult(e) {
    let interim = "", finalText = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const t = e.results[i][0].transcript;
      if (e.results[i].isFinal) finalText += t; else interim += t;
    }
    if (this._mode === "wake") {
      const heard = (finalText || interim).toLowerCase();
      if (heard.includes(this.wakeWord)) {
        this._stopRecog();
        this.on.wake();
        this.startCommand(); // capture the command that follows
      }
      return;
    }
    if (this._mode === "command") {
      if (interim) this.on.result(interim, false);
      if (finalText) this.on.result(finalText.trim(), true);
    }
  }

  _onEnd() {
    const wasCommand = this._mode === "command";
    this._mode = "idle";
    this._stopMeter();
    this.on.listening(false);
    // return to passive wake listening if enabled
    if (this.wakeEnabled && wasCommand) setTimeout(() => this.startWake(), 400);
  }

  startCommand() {
    if (!this._recog) return this.on.error("Speech recognition not supported in this browser.");
    this._mode = "command";
    this._recog.continuous = false;
    this._recog.interimResults = true;
    this._safeStart();
    this._startMeter();
    this.on.listening(true);
  }

  stopCommand() {
    if (this._mode === "command") this._stopRecog();
  }

  startWake() {
    if (!this._recog || !this.wakeEnabled) return;
    if (this._mode !== "idle") return;
    this._mode = "wake";
    this._recog.continuous = true;
    this._recog.interimResults = true;
    this._safeStart();
  }

  stopWake() {
    if (this._mode === "wake") this._stopRecog();
  }

  _safeStart() {
    try { this._recog.start(); }
    catch { /* already started; ignore */ }
  }
  _stopRecog() {
    try { this._recog.stop(); } catch { /* ignore */ }
    this._mode = "idle";
    this._stopMeter();
  }

  // ------------------- mic level metering ------------------- //
  async _startMeter() {
    if (!navigator.mediaDevices?.getUserMedia || this._ac) return;
    try {
      this._stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this._ac = new (window.AudioContext || window.webkitAudioContext)();
      const src = this._ac.createMediaStreamSource(this._stream);
      this._analyser = this._ac.createAnalyser();
      this._analyser.fftSize = 512;
      src.connect(this._analyser);
      const buf = new Uint8Array(this._analyser.frequencyBinCount);
      const tick = () => {
        if (!this._analyser) return;
        this._analyser.getByteTimeDomainData(buf);
        let sum = 0;
        for (const v of buf) { const x = (v - 128) / 128; sum += x * x; }
        this.on.level(Math.min(1, Math.sqrt(sum / buf.length) * 3.2));
        this._meterRaf = requestAnimationFrame(tick);
      };
      tick();
    } catch { /* metering is optional; recognition still works */ }
  }

  _stopMeter() {
    cancelAnimationFrame(this._meterRaf);
    this._analyser = null;
    if (this._stream) { this._stream.getTracks().forEach((t) => t.stop()); this._stream = null; }
    if (this._ac) { this._ac.close().catch(() => {}); this._ac = null; }
    this.on.level(0);
  }

  // ------------------- text to speech ------------------- //
  voices() {
    return this.ttsSupported ? speechSynthesis.getVoices() : [];
  }

  _pickVoice() {
    const vs = this.voices();
    if (!vs.length) return null;
    if (this.preferredVoice) {
      const m = vs.find((v) => v.name === this.preferredVoice);
      if (m) return m;
    }
    const langPrefix = this.lang.split("-")[0];
    return vs.find((v) => v.lang?.startsWith(langPrefix)) || vs[0];
  }

  speak(text) {
    if (!this.ttsSupported || !text) return;
    this.cancelSpeech();
    const u = new SpeechSynthesisUtterance(text.replace(/[*_`#>]/g, ""));
    const v = this._pickVoice();
    if (v) u.voice = v;
    u.lang = this.lang;
    u.rate = this.rate;
    u.volume = this.volume;
    u.onstart = () => { this._speaking = true; this.on.speaking(true); this.on.level(0.4); };
    u.onboundary = () => { this.on.level(0.85); setTimeout(() => this._speaking && this.on.level(0.35), 110); };
    u.onend = u.onerror = () => { this._speaking = false; this.on.speaking(false); this.on.level(0); };
    speechSynthesis.speak(u);
  }

  cancelSpeech() {
    if (this.ttsSupported) speechSynthesis.cancel();
    this._speaking = false;
    this.on.speaking(false);
  }

  get speaking() { return this._speaking; }
}
