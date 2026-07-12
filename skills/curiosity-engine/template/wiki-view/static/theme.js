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
