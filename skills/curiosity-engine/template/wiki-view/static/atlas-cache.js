/* atlas-cache.js — IndexedDB cache for CE embed atlas cold-start.
 *
 * Stores data.json (+ optional layout positions) keyed by workspace tip
 * so hard-reload Graph paints instantly, then background-revalidates.
 *
 *   await CEAtlasCache.get(key) -> { generatedAt, workspace, data, positions } | null
 *   await CEAtlasCache.put(key, { generatedAt, workspace, data, positions })
 *   await CEAtlasCache.putPositions(key, positions)
 *   await CEAtlasCache.clear(key?)
 */
(function () {
  'use strict';

  var DB_NAME = 'ce-atlas-cache';
  var DB_VERSION = 1;
  var STORE = 'entries';
  var dbPromise = null;

  function openDb() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise(function (resolve, reject) {
      if (!window.indexedDB) {
        reject(new Error('indexedDB unavailable'));
        return;
      }
      var req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = function () {
        var db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE, { keyPath: 'key' });
        }
      };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error || new Error('idb open failed')); };
    });
    return dbPromise;
  }

  function basename(path) {
    var s = String(path || '').replace(/\/+$/, '');
    var parts = s.split('/').filter(Boolean);
    return parts.length ? parts[parts.length - 1] : 'default';
  }

  /** Prefer explicit opts, then Switchbay workspace snapshot, then default. */
  function resolveKey(opts) {
    opts = opts || {};
    if (opts.cacheKey) return String(opts.cacheKey);
    if (opts.workspace) return 'ce-atlas-v1:' + basename(opts.workspace);
    try {
      var raw = localStorage.getItem('sy.workspaces.snapshot');
      if (raw) {
        var snap = JSON.parse(raw);
        if (snap && typeof snap.workspace === 'string' && snap.workspace) {
          return 'ce-atlas-v1:' + basename(snap.workspace);
        }
      }
    } catch (e) { /* ignore */ }
    try {
      var last = localStorage.getItem('ce-atlas-v1:last-key');
      if (last) return last;
    } catch (e) { /* ignore */ }
    return 'ce-atlas-v1:default';
  }

  function rememberKey(key) {
    try { localStorage.setItem('ce-atlas-v1:last-key', key); } catch (e) { /* ignore */ }
  }

  function tipOf(data) {
    if (!data || typeof data !== 'object') return '';
    if (typeof data.generated_at === 'string' && data.generated_at) return data.generated_at;
    // Fallback fingerprint: node/edge counts (cheap, not cryptographic).
    var n = Array.isArray(data.nodes) ? data.nodes.length : 0;
    var e = Array.isArray(data.edges) ? data.edges.length : 0;
    var p = data.pages && typeof data.pages === 'object' ? Object.keys(data.pages).length : 0;
    return 'n' + n + ':e' + e + ':p' + p;
  }

  function get(key) {
    return openDb()
      .then(function (db) {
        return new Promise(function (resolve, reject) {
          var tx = db.transaction(STORE, 'readonly');
          var req = tx.objectStore(STORE).get(key);
          req.onsuccess = function () { resolve(req.result || null); };
          req.onerror = function () { reject(req.error); };
        });
      })
      .catch(function () { return null; });
  }

  function put(key, entry) {
    rememberKey(key);
    var row = {
      key: key,
      generatedAt: entry.generatedAt || tipOf(entry.data) || '',
      workspace: entry.workspace || '',
      data: entry.data,
      positions: entry.positions || null,
      savedAt: Date.now(),
    };
    return openDb()
      .then(function (db) {
        return new Promise(function (resolve, reject) {
          var tx = db.transaction(STORE, 'readwrite');
          tx.objectStore(STORE).put(row);
          tx.oncomplete = function () { resolve(row); };
          tx.onerror = function () { reject(tx.error); };
        });
      })
      .catch(function (e) {
        console.warn('[CEAtlasCache] put failed', e);
        return null;
      });
  }

  function putPositions(key, positions) {
    return get(key).then(function (row) {
      if (!row || !row.data) return null;
      row.positions = positions || null;
      row.savedAt = Date.now();
      return put(key, row);
    });
  }

  function clear(key) {
    return openDb()
      .then(function (db) {
        return new Promise(function (resolve, reject) {
          var tx = db.transaction(STORE, 'readwrite');
          var store = tx.objectStore(STORE);
          if (key) store.delete(key);
          else store.clear();
          tx.oncomplete = function () { resolve(); };
          tx.onerror = function () { reject(tx.error); };
        });
      })
      .catch(function () { /* ignore */ });
  }

  /**
   * Serialize layout Map / Record to a plain object for structured clone.
   * Caps at maxIds to keep IDB writes bounded (BioCure ~28k is fine).
   */
  function serializePositions(positions, maxIds) {
    maxIds = maxIds || 120000;
    var out = Object.create(null);
    var n = 0;
    if (!positions) return null;
    if (typeof positions.forEach === 'function' && typeof positions.get === 'function') {
      positions.forEach(function (p, id) {
        if (n >= maxIds) return;
        if (!p || typeof p.x !== 'number' || typeof p.y !== 'number') return;
        out[id] = { x: p.x, y: p.y, r: typeof p.r === 'number' ? p.r : 4 };
        n++;
      });
    } else if (typeof positions === 'object') {
      var keys = Object.keys(positions);
      for (var i = 0; i < keys.length && n < maxIds; i++) {
        var id = keys[i];
        var p = positions[id];
        if (!p || typeof p.x !== 'number' || typeof p.y !== 'number') continue;
        out[id] = { x: p.x, y: p.y, r: typeof p.r === 'number' ? p.r : 4 };
        n++;
      }
    }
    return n ? out : null;
  }

  window.CEAtlasCache = {
    resolveKey: resolveKey,
    tipOf: tipOf,
    get: get,
    put: put,
    putPositions: putPositions,
    clear: clear,
    serializePositions: serializePositions,
    basename: basename,
  };
})();
