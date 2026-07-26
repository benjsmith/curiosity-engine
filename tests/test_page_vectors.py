"""Regression tests for the `_page_vectors` non-finite / near-zero guard.

`_page_vectors` used to admit any page whose mean chunk vector had a norm
`> 0`. Two distinct pages slipped through:

  - **inf / huge components** (a corrupt or mis-dtype stored blob): the
    norm is `inf`, which passes `> 0`, and `inf / inf` is `NaN`. The page
    entered the cosine matrix as a NaN row and silently poisoned every
    similarity computed against it. Computing that norm also emits the
    numpy RuntimeWarnings seen during a full rebuild — they originate in
    `np.mean` / `np.linalg.norm` here, *not* at the `m @ m.T` matmul.
  - **near-zero norm** (chunk vectors that nearly cancel: a semantically
    mixed page, or a stub): normalises to a finite unit vector, but its
    direction is amplified noise, so it injects junk into the cosine
    ranking and can form spurious embedding edges.

A NaN norm was already dropped (NaN fails every comparison); the guard
just makes that explicit.

Fixture: a hand-built workspace (no setup.sh, no network, no embedding
model load) whose `.curator/wiki.db` is populated with stored chunk blobs
directly — the same read path `_page_vectors` uses.

Run:  python3 -m unittest discover tests
"""
import json
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "curiosity-engine" / "scripts"

try:
    import numpy as np
    import sqlite_vec
    _HAVE_DEPS = True
except ImportError:                                   # pragma: no cover
    _HAVE_DEPS = False

DIM = 8

NEAR_ZERO = "analyses/near-zero.md"
NON_FINITE = "analyses/corrupt-blob.md"
NAN_PAGE = "analyses/nan-blob.md"
HEALTHY = "facts/healthy.md"


def _blob(head, dim=DIM):
    """A float32 chunk blob whose leading components are `head`."""
    vals = list(head) + [0.0] * (dim - len(head))
    return np.asarray(vals, dtype=np.float32).tobytes()


@unittest.skipUnless(_HAVE_DEPS, "requires numpy + sqlite-vec")
class PageVectorsGuard(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(SCRIPTS))
        import graph
        cls.graph = graph

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        ws = Path(self._tmp.name)
        self.wiki = ws / "wiki"
        self.wiki.mkdir()
        curator = ws / ".curator"
        curator.mkdir()
        (curator / "config.json").write_text(json.dumps({"embedding_enabled": True}))

        # Cancel to 1 ulp of float32 at 1.0 -> norm ~2.98e-8: nonzero (so
        # the old `> 0` floor admitted it) but far below the epsilon floor.
        # 1.0 - 0.99999994 is exactly representable; a smaller delta would
        # round away to an exact zero and be dropped by both floors.
        pages = {
            NEAR_ZERO: [_blob([1.0]), _blob([-0.99999994])],
            # Mean overflows float32 -> inf -> norm inf -> inf/inf = NaN.
            NON_FINITE: [_blob([3e38, 1.0]), _blob([3e38, 1.0])],
            NAN_PAGE: [_blob([np.nan, 1.0]), _blob([0.0, 1.0])],
            HEALTHY: [_blob([0.0, 1.0]), _blob([0.0, 1.0])],
        }

        conn = self.graph._open_wiki_db(self.wiki, sqlite_vec)
        self.graph._init_wiki_db(conn, DIM)
        vec_id = 0
        for rel, blobs in pages.items():
            for idx, blob in enumerate(blobs):
                vec_id += 1
                conn.execute("INSERT INTO wiki_chunks(rowid, embedding) VALUES(?,?)",
                             (vec_id, blob))
                conn.execute("INSERT INTO wiki_chunk_meta(vec_id, path, chunk_idx) "
                             "VALUES(?,?,?)", (vec_id, rel, idx))
        conn.commit()
        conn.close()

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_runtime_warnings(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")          # RuntimeWarning -> failure
            pv = self.graph._page_vectors(self.wiki)
        self.assertIsNotNone(pv, "index should be readable")

    def test_excludes_unusable_pages_and_keeps_healthy(self):
        pv = self.graph._page_vectors(self.wiki)
        for rel in (NEAR_ZERO, NON_FINITE, NAN_PAGE):
            self.assertNotIn(rel, pv, f"{rel} has no usable direction")
        self.assertIn(HEALTHY, pv, "normal pages are unaffected")
        self.assertAlmostEqual(float(np.linalg.norm(pv[HEALTHY])), 1.0, places=5)

    def test_kept_vectors_are_finite_unit_rows(self):
        pv = self.graph._page_vectors(self.wiki)
        m = np.stack(list(pv.values()))
        self.assertTrue(np.isfinite(m).all(), "no NaN/inf rows may reach the matmul")
        # The matmul in _build_provisional is deliberately unguarded, so a
        # finite cosine matrix here is what keeps it warning-free.
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            sims = m @ m.T
        self.assertTrue(np.isfinite(sims).all(), "cosine matrix must be finite")


if __name__ == "__main__":
    unittest.main()
