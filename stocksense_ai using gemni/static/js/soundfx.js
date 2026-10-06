/**
 * Stocksense AI — Sound FX Engine
 * Everything here is synthesized in-browser with the Web Audio API.
 * No external audio files are loaded, so there is nothing to fetch,
 * license, or ship — it also means the sound always matches the
 * current theme (space "chimes" on the landing page, "sonar/bubble"
 * tones on the login page) via the `profile` option.
 *
 * Sound is ON by default. Browsers refuse to play any audio until the
 * page has received a real user gesture, so instead of waiting for the
 * person to find the small speaker icon, we arm a one-time listener for
 * the very first click/tap/key anywhere on the page and start audio
 * right then. The speaker icon still works, for muting.
 */
(function (window) {
  "use strict";

  const STORAGE_KEY = "ssai_sound_enabled";

  function SoundFX(profile) {
    this.profile = profile || "space"; // "space" | "ocean"
    this.ctx = null;
    this.master = null;
    this.ambience = null;
    this.ambientAudio = null;
    this.ambientTrackSrc = null;
    this.ambientVolume = 0.45;
    this.armed = false; // has a first-gesture listener been attached?
    this.started = false; // has audio actually been unlocked+started?
    // Sound defaults to ON. Only an explicit previous mute turns it off.
    this.enabled = window.localStorage.getItem(STORAGE_KEY) !== "off";
  }

  SoundFX.prototype._ensureCtx = function () {
    if (this.ctx) return this.ctx;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    this.ctx = new AC();
    this.master = this.ctx.createGain();
    this.master.gain.value = 0.5;
    this.master.connect(this.ctx.destination);
    return this.ctx;
  };

  SoundFX.prototype.isEnabled = function () {
    return this.enabled;
  };

  /** Actually unlocks + starts audio. Must run inside a user-gesture event handler. */
  SoundFX.prototype._start = function () {
    const ctx = this._ensureCtx();
    if (!ctx) return;
    if (ctx.state === "suspended") ctx.resume();
    this.started = true;
    if (this.enabled) this._startAmbience();
  };

  /** Arms a one-time listener on the whole document so sound starts on
   *  the very first interaction anywhere on the page — no need to find
   *  the toggle first. Safe to call multiple times. */
  SoundFX.prototype.primeOnFirstGesture = function () {
    if (this.armed) return;
    this.armed = true;
    const self = this;
    const kick = function () {
      document.removeEventListener("pointerdown", kick, true);
      document.removeEventListener("keydown", kick, true);
      if (!self.started) self._start();
    };
    document.addEventListener("pointerdown", kick, true);
    document.addEventListener("keydown", kick, true);
  };

  /** Explicit enable (e.g. from the toggle). */
  SoundFX.prototype.enable = function () {
    this.enabled = true;
    window.localStorage.setItem(STORAGE_KEY, "on");
    this._start();
    this.chime("on");
  };

  SoundFX.prototype.disable = function () {
    this.enabled = false;
    window.localStorage.setItem(STORAGE_KEY, "off");
    this._stopAmbience();
  };

  SoundFX.prototype.toggle = function () {
    if (this.enabled) this.disable();
    else this.enable();
    return this.enabled;
  };

  // ---- primitive tone helper -------------------------------------------
  SoundFX.prototype._tone = function (opts) {
    if (!this.enabled) return;
    const ctx = this._ensureCtx();
    if (!ctx) return;
    if (ctx.state === "suspended") ctx.resume();
    const {
      freq = 440,
      end = freq,
      type = "sine",
      dur = 0.14,
      gain = 0.5,
      delay = 0,
    } = opts;
    const t0 = ctx.currentTime + delay;
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t0);
    if (end !== freq) osc.frequency.exponentialRampToValueAtTime(Math.max(end, 1), t0 + dur);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(gain, t0 + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(g);
    g.connect(this.master);
    osc.start(t0);
    osc.stop(t0 + dur + 0.02);
  };

  // ---- UI feedback sounds -------------------------------------------
  SoundFX.prototype.hover = function () {
    if (this.profile === "ocean") {
      this._tone({ freq: 900, end: 1200, type: "sine", dur: 0.07, gain: 0.16 });
    } else {
      this._tone({ freq: 600, end: 900, type: "sine", dur: 0.06, gain: 0.18 });
    }
  };

  SoundFX.prototype.click = function () {
    if (this.profile === "ocean") {
      // soft bubble "pop"
      this._tone({ freq: 700, end: 220, type: "sine", dur: 0.13, gain: 0.45 });
    } else {
      this._tone({ freq: 320, end: 640, type: "triangle", dur: 0.1, gain: 0.4 });
      this._tone({ freq: 960, type: "sine", dur: 0.05, gain: 0.22, delay: 0.02 });
    }
  };

  SoundFX.prototype.type = function () {
    // tiny key-press blip, used for OTP digit entry
    this._tone({
      freq: 500 + Math.random() * 260,
      end: 300 + Math.random() * 120,
      type: this.profile === "ocean" ? "sine" : "square",
      dur: 0.05,
      gain: 0.22,
    });
  };

  SoundFX.prototype.success = function () {
    const notes = this.profile === "ocean" ? [523.25, 659.25, 783.99] : [440, 554.37, 659.25, 880];
    notes.forEach((f, i) => this._tone({ freq: f, type: "sine", dur: 0.32, gain: 0.36, delay: i * 0.09 }));
  };

  SoundFX.prototype.error = function () {
    this._tone({ freq: 220, end: 140, type: "sawtooth", dur: 0.22, gain: 0.3 });
  };

  SoundFX.prototype.chime = function (kind) {
    if (kind === "on") {
      this._tone({ freq: 660, end: 990, type: "sine", dur: 0.18, gain: 0.34 });
    } else {
      this._tone({ freq: 500, end: 260, type: "sine", dur: 0.18, gain: 0.28 });
    }
  };

  // ---- external soundtrack support -----------------------------------
  SoundFX.prototype.setAmbientTrack = function (src, volume) {
    if (!src) return;
    this.ambientTrackSrc = src;
    if (typeof volume === "number") this.ambientVolume = volume;
    if (!this.ambientAudio) {
      this.ambientAudio = new Audio(src);
      this.ambientAudio.loop = true;
      this.ambientAudio.volume = this.ambientVolume;
    } else {
      this.ambientAudio.src = src;
      this.ambientAudio.volume = this.ambientVolume;
    }
    if (this.enabled && this.started) {
      this.ambientAudio.play().catch(function () {});
    }
  };

  // ---- ambience: soft, generative or external track, looping ----------
  SoundFX.prototype._startAmbience = function () {
    if (this.ambientAudio) {
      if (this.enabled) {
        this.ambientAudio.play().catch(function () {});
      }
      return;
    }
    if (this.ambience) return;
    const ctx = this._ensureCtx();
    if (!ctx) return;

    const bus = ctx.createGain();
    bus.gain.value = this.profile === "ocean" ? 0.13 : 0.1;
    bus.connect(this.master);

    const nodes = [];

    if (this.profile === "ocean") {
      // filtered noise = soft surf / underwater swash
      const bufSize = 2 * ctx.sampleRate;
      const buf = ctx.createBuffer(1, bufSize, ctx.sampleRate);
      const data = buf.getChannelData(0);
      let last = 0;
      for (let i = 0; i < bufSize; i++) {
        const white = Math.random() * 2 - 1;
        last = (last + 0.02 * white) / 1.02;
        data[i] = last * 3.2;
      }
      const noise = ctx.createBufferSource();
      noise.buffer = buf;
      noise.loop = true;
      const filter = ctx.createBiquadFilter();
      filter.type = "lowpass";
      filter.frequency.value = 500;
      const lfo = ctx.createOscillator();
      lfo.frequency.value = 0.09;
      const lfoGain = ctx.createGain();
      lfoGain.gain.value = 260;
      lfo.connect(lfoGain);
      lfoGain.connect(filter.frequency);
      noise.connect(filter);
      filter.connect(bus);
      noise.start();
      lfo.start();
      nodes.push(noise, lfo);
    } else {
      // slow detuned pads = distant "space" shimmer
      [220, 220.6, 330].forEach((f, i) => {
        const osc = ctx.createOscillator();
        osc.type = "sine";
        osc.frequency.value = f;
        const g = ctx.createGain();
        g.gain.value = 0.5 / (i + 1);
        const lfo = ctx.createOscillator();
        lfo.frequency.value = 0.05 + i * 0.02;
        const lfoGain = ctx.createGain();
        lfoGain.gain.value = 0.15;
        lfo.connect(lfoGain);
        lfoGain.connect(g.gain);
        osc.connect(g);
        g.connect(bus);
        osc.start();
        lfo.start();
        nodes.push(osc, lfo);
      });
    }

    this.ambience = { bus, nodes };
  };

  SoundFX.prototype._stopAmbience = function () {
    if (this.ambientAudio) {
      try {
        this.ambientAudio.pause();
      } catch (e) {}
    }
    if (!this.ambience) return;
    try {
      this.ambience.nodes.forEach((n) => n.stop && n.stop());
    } catch (e) {
      /* already stopped */
    }
    this.ambience = null;
  };

  /** Wires hover/click blips onto interactive elements + a mute toggle button,
   *  and arms the first-gesture auto-start. */
  SoundFX.prototype.bindUI = function (toggleBtnId) {
    const self = this;
    this.primeOnFirstGesture();

    document.querySelectorAll("a, button").forEach((el) => {
      el.addEventListener("mouseenter", () => self.hover());
      el.addEventListener("click", () => self.click());
    });

    const btn = document.getElementById(toggleBtnId);
    if (!btn) return;
    const render = () => {
      btn.setAttribute("aria-pressed", self.enabled ? "true" : "false");
      btn.innerHTML =
        '<span class="material-symbols-outlined text-[18px]">' +
        (self.enabled ? "volume_up" : "volume_off") +
        "</span>";
      btn.title = self.enabled ? "Sound on — click to mute" : "Sound muted — click to enable";
    };
    render();
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      self.toggle();
      render();
    });
  };

  window.SoundFX = SoundFX;
})(window);
