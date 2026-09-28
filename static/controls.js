// Shared flight input for the drone controller and the sim game.
// Keyboard, gamepad and touch sticks are merged into Tello rc values:
// [roll (left/right), pitch (forward/back), throttle (up/down), yaw], each -100..100.

const DZ = 0.08;
const STICK_RADIUS = 62;

/**
 * settings:     live object with speed (0-100), expo (0-1), padMode (1|2)
 * zoneLeft/Right: elements containing .base > .knob and .zlabel (touch sticks)
 * onKey(e):     called for every keydown (movement keys are handled here already)
 * onPad(pressed, edge): called every read() with button helpers, for page actions
 */
export function createControls({ settings, zoneLeft, zoneRight, onKey, onPad }) {
  const shape = v => {
    const a = Math.abs(v);
    if (a < DZ) return 0;
    const n = Math.min(1, (a - DZ) / (1 - DZ)), e = settings.expo;
    return Math.sign(v) * ((1 - e) * n + e * n * n * n);
  };

  // ---------- keyboard ----------
  const keys = new Set();
  const kb = [0, 0, 0, 0];
  addEventListener('keydown', e => {
    if (e.target.tagName === 'INPUT') e.target.blur();
    if (e.code.startsWith('Arrow') || e.code === 'Space') e.preventDefault();
    keys.add(e.code);
    if (onKey) onKey(e);
  });
  addEventListener('keyup', e => keys.delete(e.code));
  addEventListener('blur', () => keys.clear());
  document.addEventListener('visibilitychange', () => keys.clear());

  function readKeyboard() {
    const k = c => keys.has(c) ? 1 : 0;
    const sp = (k('ShiftLeft') || k('ShiftRight')) ? 100 : settings.speed;
    const target = [
      k('KeyD') - k('KeyA'),
      k('KeyW') - k('KeyS'),
      k('ArrowUp') - k('ArrowDown'),
      Math.max(-1, Math.min(1, k('ArrowRight') + k('KeyE') - k('ArrowLeft') - k('KeyQ'))),
    ];
    for (let i = 0; i < 4; i++) {
      kb[i] += (target[i] * sp - kb[i]) * 0.35; // ramp so taps aren't jerky
      if (!target[i] && Math.abs(kb[i]) < 2) kb[i] = 0;
    }
    return kb;
  }

  // ---------- gamepad ----------
  const prevBtn = [];
  let padName = null;
  function readPad() {
    const pads = navigator.getGamepads ? [...navigator.getGamepads()].filter(p => p && p.connected) : [];
    const p = pads[0];
    padName = p ? p.id : null;
    if (!p) return [0, 0, 0, 0];
    const pressed = i => !!(p.buttons[i] && (p.buttons[i].pressed || p.buttons[i].value > 0.5));
    const edge = [];
    for (let i = 0; i < p.buttons.length; i++) { const now = pressed(i); edge[i] = now && !prevBtn[i]; prevBtn[i] = now; }
    if (onPad) onPad(pressed, edge);
    const ax = i => shape(p.axes[i] || 0);
    const lx = ax(0), ly = -ax(1), rx = ax(2), ry = -ax(3);
    const sp = pressed(7) ? 100 : settings.speed;
    const v = settings.padMode == 1 ? [rx, ly, ry, lx] : [rx, ry, ly, lx];
    return v.map(x => x * sp);
  }

  // ---------- touch sticks ----------
  function makeStick(zone) {
    const s = { x: 0, y: 0, id: null };
    if (!zone) return s;
    const base = zone.querySelector('.base'), knob = zone.querySelector('.knob'), label = zone.querySelector('.zlabel');
    const place = (x, y) => {
      base.style.left = x + 'px'; base.style.top = y + 'px';
      if (label) { label.style.left = x + 'px'; label.style.top = (y + 84) + 'px'; }
      s.cx = x; s.cy = y;
    };
    const home = () => { const r = zone.getBoundingClientRect(); place(r.width / 2, r.height - 120); };
    const setKnob = (dx, dy) => { knob.style.transform = `translate(${dx}px, ${dy}px)`; };
    zone.addEventListener('pointerdown', e => {
      if (s.id !== null) return;
      s.id = e.pointerId; zone.setPointerCapture(e.pointerId); zone.classList.add('active');
      const r = zone.getBoundingClientRect();
      place(e.clientX - r.left, e.clientY - r.top);
      setKnob(0, 0);
    });
    zone.addEventListener('pointermove', e => {
      if (e.pointerId !== s.id) return;
      const r = zone.getBoundingClientRect();
      let dx = e.clientX - r.left - s.cx, dy = e.clientY - r.top - s.cy;
      const m = Math.hypot(dx, dy);
      if (m > STICK_RADIUS) { dx *= STICK_RADIUS / m; dy *= STICK_RADIUS / m; }
      setKnob(dx, dy);
      s.x = dx / STICK_RADIUS; s.y = -dy / STICK_RADIUS;
    });
    const end = e => {
      if (e.pointerId !== s.id) return;
      s.id = null; s.x = s.y = 0; zone.classList.remove('active'); setKnob(0, 0); home();
    };
    zone.addEventListener('pointerup', end);
    zone.addEventListener('pointercancel', end);
    addEventListener('resize', home);
    home();
    return s;
  }
  const stickL = makeStick(zoneLeft), stickR = makeStick(zoneRight);
  function readTouch() {
    const sp = settings.speed;
    return [shape(stickR.x) * sp, shape(stickR.y) * sp, shape(stickL.y) * sp, shape(stickL.x) * sp];
  }

  /** mode: 'auto' (on for touch screens) | 'on' | 'off' */
  function setSticks(mode) {
    const touchy = navigator.maxTouchPoints > 0 || matchMedia('(pointer: coarse)').matches;
    document.body.classList.toggle('sticks', mode === 'on' || (mode === 'auto' && touchy));
    dispatchEvent(new Event('resize'));
  }

  /** Current merged stick values plus which inputs are being used. */
  function read() {
    const sources = { Keyboard: readKeyboard(), Gamepad: readPad(), Touch: readTouch() };
    const rc = [0, 0, 0, 0], active = [];
    for (const [name, v] of Object.entries(sources)) {
      if (v.some(x => Math.abs(x) >= 1)) active.push(name);
      for (let i = 0; i < 4; i++) if (Math.abs(v[i]) > Math.abs(rc[i])) rc[i] = v[i];
    }
    return { rc: rc.map(x => Math.round(Math.max(-100, Math.min(100, x)))), active, padName };
  }

  return { read, setSticks, keys };
}

export function loadSettings(defaults) {
  const settings = { ...defaults };
  try { Object.assign(settings, JSON.parse(localStorage.getItem('tello-settings') || '{}')); } catch (e) {}
  settings.save = () => {
    try {
      const { save, ...plain } = settings;
      localStorage.setItem('tello-settings', JSON.stringify(plain));
    } catch (e) {}
  };
  return settings;
}

export function toast(msg, kind = '') {
  const log = document.getElementById('log');
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.textContent = msg;
  log.appendChild(el);
  while (log.children.length > 5) log.firstChild.remove();
  setTimeout(() => el.classList.add('fade'), 3800);
  setTimeout(() => el.remove(), 4300);
}

/** Wire the settings panel controls (range inputs with an #id-v output, and .seg button groups). */
export function bindRange(el, settings, key, fmt) {
  const out = document.getElementById(el.id + '-v');
  el.value = settings[key]; out.textContent = fmt(settings[key]);
  el.addEventListener('input', () => { settings[key] = +el.value; out.textContent = fmt(+el.value); settings.save(); });
}
export function bindSeg(seg, settings, key, onChange) {
  const paint = () => seg.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.v == settings[key]));
  seg.addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    settings[key] = isNaN(+b.dataset.v) ? b.dataset.v : +b.dataset.v;
    settings.save(); paint(); if (onChange) onChange();
  });
  paint();
  return paint;
}
