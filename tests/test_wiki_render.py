"""wiki_render.py regression tests — the data.json contract.

Covers the three v1.2.1 fixes:
  * `summary-table` / `extracted-table` frontmatter types render as
    `table` instead of falling into `unclassified` (TYPE_ALIASES);
  * pages on disk but missing from kuzu (graph drift) get a synthesised
    degree-0 node instead of silently vanishing from graph/sidebar/search;
  * `degree` is recomputed from the drift-filtered edge set, so it never
    exceeds a node's actual incident-edge count in data.json.

No kuzu, no network: the graph layer is exercised by patching
`_build_graph` where a partial (drifted) graph is needed, and via its
documented no-kuzu fallback otherwise.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "curiosity-engine" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import wiki_render  # noqa: E402


def _write_page(wiki: Path, rel: str, ptype: str, title: str) -> None:
    p = wiki / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f'---\ntitle: "{title}"\ntype: {ptype}\n---\n\nBody text.\n',
        encoding="utf-8",
    )


def _build(wiki: Path, out: Path) -> dict:
    with contextlib.redirect_stdout(io.StringIO()):
        wiki_render.cmd_build(wiki, out)
    return json.loads((out / "data.json").read_text(encoding="utf-8"))


class TestTypeAliases(unittest.TestCase):
    def test_table_variants_render_as_table(self):
        with tempfile.TemporaryDirectory() as td:
            wiki = Path(td) / "ws" / "wiki"
            _write_page(wiki, "tables/tab-extracted.md", "extracted-table",
                        "[tab] Extracted")
            _write_page(wiki, "tables/tbl-summary.md", "summary-table",
                        "[tbl] Summary")
            _write_page(wiki, "concepts/thing.md", "concept", "[con] Thing")
            _write_page(wiki, "notes/odd.md", "made-up-type", "Odd")
            data = _build(wiki, Path(td) / "out")

            types = {p["id"]: p["type"] for p in data["pages"].values()}
            self.assertEqual(types["tables/tab-extracted"], "table")
            self.assertEqual(types["tables/tbl-summary"], "table")
            self.assertEqual(types["concepts/thing"], "concept")
            # Genuinely unknown types still bucket to unclassified.
            self.assertEqual(types["notes/odd"], "unclassified")

            # Nodes carry the reconciled type too, and every emitted node
            # type resolves in the palette (colourFor's lookup contract).
            for n in data["nodes"]:
                self.assertIn(n["type"], data["palette"])


class TestGraphDrift(unittest.TestCase):
    def _drifted_workspace(self, td: str):
        """Three pages on disk; kuzu (mocked) knows only a and b, plus an
        edge to a page that no longer exists on disk ("ghost")."""
        wiki = Path(td) / "ws" / "wiki"
        _write_page(wiki, "concepts/a.md", "concept", "[con] A")
        _write_page(wiki, "concepts/b.md", "concept", "[con] B")
        _write_page(wiki, "concepts/c.md", "concept", "[con] C")
        graph = (
            [
                {"id": "concepts/a", "path": "concepts/a.md",
                 "type": "concept", "title": "[con] A"},
                {"id": "concepts/b", "path": "concepts/b.md",
                 "type": "concept", "title": "[con] B"},
            ],
            [
                {"source": "concepts/a", "target": "concepts/b",
                 "type": "wikilink"},
                {"source": "concepts/a", "target": "concepts/ghost",
                 "type": "wikilink"},
            ],
        )
        return wiki, graph

    def test_disk_only_pages_get_synthesised_nodes(self):
        with tempfile.TemporaryDirectory() as td:
            wiki, graph = self._drifted_workspace(td)
            with mock.patch.object(wiki_render, "_build_graph",
                                   return_value=graph):
                data = _build(wiki, Path(td) / "out")

            by_id = {n["id"]: n for n in data["nodes"]}
            self.assertIn("concepts/c", by_id)
            c = by_id["concepts/c"]
            self.assertEqual(c["degree"], 0)
            self.assertEqual(c["type"], "concept")
            self.assertEqual(c["title"], "[con] C")
            self.assertEqual(c["path"], "concepts/c.md")
            # nodes and pages agree on membership.
            self.assertEqual(set(by_id), set(data["pages"]))

    def test_degree_matches_shipped_edges(self):
        with tempfile.TemporaryDirectory() as td:
            wiki, graph = self._drifted_workspace(td)
            with mock.patch.object(wiki_render, "_build_graph",
                                   return_value=graph):
                data = _build(wiki, Path(td) / "out")

            # The a->ghost edge was drift-filtered out...
            self.assertEqual(
                [(e["source"], e["target"]) for e in data["edges"]],
                [("concepts/a", "concepts/b")],
            )
            # ...and degree reflects the shipped edges, not kuzu's count.
            by_id = {n["id"]: n for n in data["nodes"]}
            self.assertEqual(by_id["concepts/a"]["degree"], 1)
            self.assertEqual(by_id["concepts/b"]["degree"], 1)

    def test_no_kuzu_fallback_still_ships_every_page(self):
        with tempfile.TemporaryDirectory() as td:
            wiki = Path(td) / "ws" / "wiki"
            _write_page(wiki, "concepts/a.md", "concept", "[con] A")
            _write_page(wiki, "facts/f.md", "fact", "[fact] F")
            with contextlib.redirect_stderr(io.StringIO()):
                data = _build(wiki, Path(td) / "out")
            self.assertEqual(
                {n["id"] for n in data["nodes"]},
                {"concepts/a", "facts/f"},
            )
            self.assertTrue(all(n["degree"] == 0 for n in data["nodes"]))
            self.assertEqual(data["edges"], [])


if __name__ == "__main__":
    unittest.main()
