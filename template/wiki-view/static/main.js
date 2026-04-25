/* Orchestrator: load data.json, init each module, wire hash routing.
 * Hash format:  #page=<page-id>  → opens that page in the modal.
 */
(async function () {
  let data = null;
  try {
    const res = await fetch('data.json');
    data = await res.json();
  } catch (e) {
    document.body.innerHTML =
      '<div style="padding:40px;font-family:system-ui">' +
      'Failed to load <code>data.json</code>. Re-run ' +
      '<code>bash &lt;skill_path&gt;/scripts/viewer.sh build</code>.' +
      '</div>';
    console.error(e);
    return;
  }

  Theme.init();
  Sidebar.init(data);
  Modal.init(data);
  Graph.init(data);

  function applyHash() {
    const m = window.location.hash.match(/^#page=([^&]+)$/);
    if (m) {
      const pageId = decodeURIComponent(m[1]);
      const ok = Modal.open(pageId);
      if (ok) {
        Sidebar.setActive(pageId);
        Graph.focus(pageId);
      }
    } else {
      Modal.close();
    }
  }
  window.addEventListener('hashchange', applyHash);
  applyHash();
})();
