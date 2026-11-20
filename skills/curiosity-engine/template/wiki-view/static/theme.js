/* Light/dark theme toggle. Persists to localStorage; first paint
 * uses the stored preference (or OS default if unset) before any
 * other JS runs to avoid a flash of the wrong theme.
 */
(function () {
  const KEY = 'curiosity-engine.theme';
  const stored = localStorage.getItem(KEY);
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const initial = stored || (prefersDark ? 'dark' : 'light');
  document.documentElement.dataset.theme = initial;

  // Stable colour for a type that did not set one. Matches
  // meta_ontology.deterministic_color and Knowledge Atlas.
  function fnv1a(text) {
    let h = 2166136261;
    for (let i = 0; i < text.length; i++) {
      h ^= text.charCodeAt(i);
      h = Math.imul(h, 16777619) >>> 0;
    }
    return h >>> 0;
  }
  const RESERVED = new Set([
    '#4d1ae8', '#1d6996', '#38a6a5', '#0f8554', '#73af48', '#edad08',
    '#e17c05', '#cc503e', '#94346e', '#6f4070', '#9656a2', '#5e4fa2',
    '#c51b8a', '#7570b3', '#ffffff', '#bbbbbb',
  ]);
  function hslHex(hue, sat, light) {
    const c = (1 - Math.abs(2 * light - 1)) * sat;
    const hp = (hue % 360) / 60;
    const x = c * (1 - Math.abs(hp % 2 - 1));
    let r = 0, g = 0, b = 0;
    if (hp < 1) { r = c; g = x; }
    else if (hp < 2) { r = x; g = c; }
    else if (hp < 3) { g = c; b = x; }
    else if (hp < 4) { g = x; b = c; }
    else if (hp < 5) { r = x; b = c; }
    else { r = c; b = x; }
    const m = light - c / 2;
    const ch = (v) => Math.max(0, Math.min(255, Math.round((v + m) * 255)));
    const hex = (n) => n.toString(16).padStart(2, '0');
    return '#' + hex(ch(r)) + hex(ch(g)) + hex(ch(b));
  }
  window.ceTypeColor = function (typeName) {
    const name = String(typeName || '').trim().toLowerCase();
    let hue = fnv1a(name) % 360;
    for (let i = 0; i < 12; i++) {
      const color = hslHex(hue, 0.55, 0.46);
      if (!RESERVED.has(color)) return color;
      hue = (hue + 29) % 360;
    }
    return hslHex(hue, 0.55, 0.46);
  };

  window.Theme = {
    set(t) {
      document.documentElement.dataset.theme = t;
      localStorage.setItem(KEY, t);
    },
    toggle() {
      const cur = document.documentElement.dataset.theme;
      this.set(cur === 'dark' ? 'light' : 'dark');
    },
    init() {
      const btn = document.querySelector('#theme-toggle');
      if (btn) btn.addEventListener('click', () => this.toggle());
    },
  };
})();
