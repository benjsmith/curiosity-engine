"""Typed edges from an optional .curator/meta-ontology.yaml.

No file, and a file whose edges do not name endpoints, keep today's
graph, frontmatter allowlist, and viewer. A file that names node types
and typed edges is honoured: the type stays, the frontmatter list is
kept, rebuild stores the relationship, neighbors and retrieve can walk
and filter it, and the rendered graph carries the stroke.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "curiosity-engine" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import graph  # noqa: E402
import meta_ontology  # noqa: E402
import naming  # noqa: E402
import wiki_render  # noqa: E402

ONTOLOGY = """\
version: 1
nodes:
  - type: evidence
    label: Evidence
    folder: evidence
    color: "#73af48"
    description: An observation.
  - type: hypothesis
    label: Hypothesis
    folder: hypotheses
    prefix: hyp
    color: "#7570b3"
    description: |
      State the claim and what would change the verdict.
  - type: concept
    label: Concepts
    description: A stable idea.
edges:
  - type: wikilink
    label: links to
    description: A body wikilink. The graph already draws this.
  - type: supports
    label: supports
    color: "#0072b2"
    source: [evidence]
    target: [hypothesis]
    description: |
      The evidence result matches a prediction the hypothesis states.
  - type: contradicts
    label: contradicts
    color: "#d55e00"
    source: [evidence]
    target: [hypothesis]
    description: |
      The evidence result conflicts with a prediction the hypothesis states.
schema_keys:
  - key: confidence
    applies_to: [hypothesis]
    minimum: 0
    maximum: 1
    description: How firmly the page is held.
"""

NODES_ONLY = """\
version: 1
nodes:
  - type: hypothesis
    label: Hypothesis
    folder: hypotheses
    color: "#7570b3"
    description: State the claim.
edges:
  - type: cites
    description: Same paragraph as the claim.
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _page(wiki: Path, rel: str, body: str) -> None:
    _write(wiki / rel, body)


def _build_view(wiki: Path, out: Path) -> dict:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        wiki_render.cmd_build(wiki, out)
    return json.loads((out / "data.json").read_text(encoding="utf-8"))


def _rebuild(wiki: Path) -> dict:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        graph.rebuild(wiki, force=True)
    return json.loads(buf.getvalue())


def _capture_json(fn) -> object:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn()
    return json.loads(buf.getvalue())


class Parse(unittest.TestCase):
    def test_absent_file_is_none(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(meta_ontology.load(Path(td)))

    def test_typed_edges_and_endpoints(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root / ".curator" / "meta-ontology.yaml", ONTOLOGY)
            ont = meta_ontology.load(root)
            self.assertIsNotNone(ont)
            supports = ont.edge("supports")
            contradicts = ont.edge("contradicts")
            self.assertEqual(supports.source, ("evidence",))
            self.assertEqual(supports.target, ("hypothesis",))
            self.assertTrue(supports.typed)
            self.assertFalse(ont.edge("wikilink").typed)
            self.assertEqual(supports.color, "#0072b2")
            self.assertNotEqual(supports.color, contradicts.color)
            self.assertEqual(ont.node("hypothesis").prefix, "hyp")
            self.assertIn("confidence", meta_ontology.frontmatter_keys(ont))
            self.assertIn("supports", meta_ontology.frontmatter_keys(ont))
            self.assertNotIn("wikilink", meta_ontology.frontmatter_keys(ont))

    def test_string_endpoints_and_fallback_colour(self):
        text = """\
version: 1
nodes:
  - type: evidence
    description: An observation.
  - type: hypothesis
    description: State the claim.
edges:
  - type: supports
    source: evidence
    target: hypothesis
    description: When the result matches a prediction.
  - type: contradicts
    source: [evidence]
    target: [hypothesis]
    description: When the result conflicts with a prediction.
"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root / ".curator" / "meta-ontology.yaml", text)
            ont = meta_ontology.load(root)
            self.assertEqual(ont.edge("supports").source, ("evidence",))
            self.assertNotEqual(
                ont.edge_color("supports"), ont.edge_color("contradicts"),
            )

    def test_source_without_target_is_rejected(self):
        text = """\
version: 1
edges:
  - type: supports
    source: [evidence]
    description: When the result matches.
"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root / ".curator" / "meta-ontology.yaml", text)
            with self.assertRaises(meta_ontology.MetaOntologyError):
                meta_ontology.load(root)

    def test_edges_without_endpoints_collect_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wiki = root / "wiki"
            _write(root / ".curator" / "meta-ontology.yaml", NODES_ONLY)
            _page(wiki, "hypotheses/queue.md",
                  '---\ntitle: "Queue"\ntype: hypothesis\n---\n\nClaim.\n')
            ont = meta_ontology.load(root)
            self.assertEqual(meta_ontology.collect_typed_edges(wiki, ont), [])
            self.assertIsNone(ont.edge_color("cites"))


class Colours(unittest.TestCase):
    def test_builtin_palette_keeps_existing_swatches(self):
        self.assertEqual(wiki_render.PALETTE["concept"], "#38a6a5")
        self.assertEqual(wiki_render.PALETTE["project"], "#4d1ae8")
        self.assertEqual(wiki_render.PALETTE["hypothesis"], "#7570b3")
        self.assertNotEqual(wiki_render.PALETTE["hypothesis"], wiki_render.PALETTE["unclassified"])
        self.assertIn("hypothesis", naming.FRONTMATTER_TYPES)
        self.assertEqual(naming.TYPE_PREFIX["hypothesis"], "[hyp]")

    def test_custom_type_without_colour_is_stable_and_not_unclassified(self):
        text = """\
version: 1
nodes:
  - type: widget
    label: Widget
    description: A custom type with no colour.
  - type: hypothesis
    color: "#123456"
    description: Override the built-in swatch.
"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "ws"
            wiki = root / "wiki"
            _write(root / ".curator" / "meta-ontology.yaml", text)
            _page(wiki, "widgets/one.md",
                  '---\ntitle: "One"\ntype: widget\n---\n\nBody.\n')
            _page(wiki, "hypotheses/queue.md",
                  '---\ntitle: "Queue"\ntype: hypothesis\n---\n\nClaim.\n')
            _page(wiki, "concepts/a.md",
                  '---\ntitle: "A"\ntype: concept\n---\n\nBody.\n')
            data = _build_view(wiki, Path(td) / "out")
            self.assertEqual(data["pages"]["widgets/one"]["type"], "widget")
            self.assertEqual(data["pages"]["hypotheses/queue"]["type"], "hypothesis")
            color = data["palette"]["widget"]
            self.assertEqual(color, meta_ontology.deterministic_color("widget"))
            self.assertNotEqual(color, "#ffffff")
            self.assertNotEqual(color, "#bbbbbb")
            self.assertNotIn(color, set(wiki_render.PALETTE.values()))
            self.assertEqual(data["palette"]["hypothesis"], "#123456")
            self.assertEqual(data["palette"]["concept"], "#38a6a5")
            by_id = {n["id"]: n for n in data["nodes"]}
            self.assertEqual(by_id["widgets/one"]["type"], "widget")
            self.assertNotEqual(by_id["widgets/one"]["type"], "unclassified")


class FrontmatterAndRender(unittest.TestCase):
    def test_unknown_edge_key_is_dropped_without_a_file(self):
        text = '---\ntitle: "CPU"\ntype: evidence\nsupports:\n  - hypotheses/queue\n---\n\nBody.\n'
        fm, _body = naming.read_frontmatter(text)
        self.assertNotIn("supports", fm)

    def test_no_file_render_matches_today(self):
        with tempfile.TemporaryDirectory() as td:
            wiki = Path(td) / "ws" / "wiki"
            _page(wiki, "hypotheses/queue.md",
                  '---\ntitle: "Queue"\ntype: hypothesis\nsupports:\n  - evidence/cpu\n---\n\nClaim.\n')
            _page(wiki, "concepts/a.md",
                  '---\ntitle: "[con] A"\ntype: concept\n---\n\nBody.\n')
            _page(wiki, "notes/odd.md",
                  '---\ntitle: "Odd"\ntype: made-up-type\n---\n\nBody.\n')
            data = _build_view(wiki, Path(td) / "out")
            types = {p["id"]: p["type"] for p in data["pages"].values()}
            self.assertEqual(types["hypotheses/queue"], "hypothesis")
            self.assertEqual(types["concepts/a"], "concept")
            self.assertEqual(types["notes/odd"], "unclassified")
            self.assertEqual(data["edges"], [])
            self.assertNotIn("supports", data["pages"]["hypotheses/queue"]["properties"])
            self.assertEqual(data["palette"]["hypothesis"], "#7570b3")
            self.assertEqual(data["palette"]["concept"], "#38a6a5")
            self.assertEqual(data["palette"]["evidence"], "#73af48")
            self.assertEqual(data["palette"]["unclassified"], "#ffffff")
            by_id = {n["id"]: n for n in data["nodes"]}
            self.assertEqual(by_id["hypotheses/queue"]["type"], "hypothesis")
            self.assertTrue(all("color" not in e for e in data["edges"]))

    def test_nodes_without_typed_edges_keep_the_type_and_add_no_edge(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "ws"
            wiki = root / "wiki"
            _write(root / ".curator" / "meta-ontology.yaml", NODES_ONLY)
            _page(wiki, "hypotheses/queue.md",
                  '---\ntitle: "Queue"\ntype: hypothesis\n---\n\nClaim.\n')
            _page(wiki, "concepts/a.md",
                  '---\ntitle: "[con] A"\ntype: concept\n---\n\nSee [[queue]].\n')
            data = _build_view(wiki, Path(td) / "out")
            self.assertEqual(data["pages"]["hypotheses/queue"]["type"], "hypothesis")
            self.assertEqual(data["pages"]["hypotheses/queue"]["typeLabel"], "Hypothesis")
            self.assertEqual(data["palette"]["hypothesis"], "#7570b3")
            self.assertEqual(data["edges"], [])
            by_id = {n["id"]: n for n in data["nodes"]}
            self.assertEqual(by_id["hypotheses/queue"]["type"], "hypothesis")

    def test_render_emits_typed_edges_and_keeps_lists(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "ws"
            wiki = root / "wiki"
            _write(root / ".curator" / "meta-ontology.yaml", ONTOLOGY)
            _page(wiki, "evidence/cpu.md", """---
title: "CPU"
type: evidence
confidence: "0.4"
supports:
  - "[[hypotheses/queue|Queue]]"
contradicts: [hypotheses/missing]
---

The series moved. See [[queue]].
""")
            _page(wiki, "hypotheses/queue.md", """---
title: "Queue"
type: Hypothesis
---

The queue grows.
""")
            _page(wiki, "concepts/aside.md", """---
title: "Aside"
type: concept
supports:
  - hypotheses/queue
---

Not evidence.
""")
            data = _build_view(wiki, Path(td) / "out")
            self.assertEqual(data["pages"]["hypotheses/queue"]["type"], "hypothesis")
            props = data["pages"]["evidence/cpu"]["properties"]
            self.assertIn("supports", props)
            self.assertTrue(props["supports"])
            self.assertEqual(props["confidence"], "0.4")
            # The concept page's supports list is a declared key, so it is
            # kept on the page, but it is not a graph edge: the source
            # type is not evidence.
            self.assertIn("supports", data["pages"]["concepts/aside"]["properties"])
            typed = [e for e in data["edges"] if e["type"] in {"supports", "contradicts"}]
            self.assertEqual(len(typed), 1)
            edge = typed[0]
            self.assertEqual(edge["source"], "evidence/cpu")
            self.assertEqual(edge["target"], "hypotheses/queue")
            self.assertEqual(edge["type"], "supports")
            self.assertEqual(edge["color"], "#0072b2")
            self.assertEqual(edge["typeLabel"], "supports")
            by_id = {n["id"]: n for n in data["nodes"]}
            self.assertEqual(by_id["evidence/cpu"]["degree"], 1)
            self.assertEqual(by_id["hypotheses/queue"]["degree"], 1)
            for n in data["nodes"]:
                self.assertIn(n["type"], data["palette"])


class GraphBuildAndTraversal(unittest.TestCase):
    def _workspace(self, root: Path, ontology: str | None) -> Path:
        wiki = root / "wiki"
        if ontology is not None:
            _write(root / ".curator" / "meta-ontology.yaml", ontology)
        _page(wiki, "evidence/cpu.md", """---
title: "CPU samples"
type: evidence
supports:
  - hypotheses/queue
contradicts:
  - hypotheses/queue
---

The series moved.
""")
        _page(wiki, "hypotheses/queue.md", """---
title: "Queue grows under load"
type: hypothesis
---

The queue grows.
""")
        _page(wiki, "concepts/a.md", """---
title: "[con] Alpha"
type: concept
---

See [[b]].
""")
        _page(wiki, "concepts/b.md", """---
title: "[con] Beta"
type: concept
---

Linked.
""")
        return wiki

    def test_no_file_rebuild_and_neighbors_stay_wikilinks(self):
        with tempfile.TemporaryDirectory() as td:
            wiki = self._workspace(Path(td), None)
            stats = _rebuild(wiki)
            self.assertEqual(stats["ontology_edges"], 0)
            rows = _capture_json(lambda: graph.cmd_neighbors(
                wiki, "concepts/a.md", 1, "out"))
            self.assertEqual([r["path"] for r in rows], ["concepts/b.md"])
            self.assertNotIn("edge", rows[0])
            found = _capture_json(lambda: graph.cmd_retrieve(
                wiki, "alpha", 2, 6, 1, "graph", False, 1,
                expand_thin_sources=False))
            self.assertTrue(any(p["page"] == "concepts/b.md" for p in found["pages"]))
            self.assertTrue(all("edge" not in p for p in found["pages"]))

    def test_yaml_without_typed_edges_adds_no_relationship(self):
        with tempfile.TemporaryDirectory() as td:
            wiki = self._workspace(Path(td), NODES_ONLY)
            stats = _rebuild(wiki)
            self.assertEqual(stats["ontology_edges"], 0)
            rows = _capture_json(lambda: graph.cmd_neighbors(
                wiki, "hypotheses/queue.md", 1, "in"))
            self.assertEqual(rows, [])

    def test_rebuild_traverses_and_filters_by_edge_type(self):
        with tempfile.TemporaryDirectory() as td:
            wiki = self._workspace(Path(td), ONTOLOGY)
            stats = _rebuild(wiki)
            self.assertEqual(stats["ontology_edges"], 2)

            inbound = _capture_json(lambda: graph.cmd_neighbors(
                wiki, "hypotheses/queue.md", 1, "in"))
            self.assertEqual([r["path"] for r in inbound], ["evidence/cpu.md"])
            self.assertIn(inbound[0]["edge"], {"supports", "contradicts"})
            # Outbound from the hypothesis does not follow edges that
            # point at it. The evidence page is the source.
            outbound = _capture_json(lambda: graph.cmd_neighbors(
                wiki, "hypotheses/queue.md", 1, "out"))
            self.assertEqual(outbound, [])

            supporting = _capture_json(lambda: graph.cmd_neighbors(
                wiki, "hypotheses/queue.md", 1, "in", edge_types=["supports"]))
            self.assertEqual(len(supporting), 1)
            self.assertEqual(supporting[0]["path"], "evidence/cpu.md")
            self.assertEqual(supporting[0]["type"], "evidence")
            self.assertEqual(supporting[0]["edge"], "supports")

            none = _capture_json(lambda: graph.cmd_neighbors(
                wiki, "hypotheses/queue.md", 1, "in", edge_types=["missing"]))
            self.assertEqual(none, [])
            contradicting = _capture_json(lambda: graph.cmd_neighbors(
                wiki, "hypotheses/queue.md", 1, "in", edge_types=["contradicts"]))
            self.assertEqual(len(contradicting), 1)
            self.assertEqual(contradicting[0]["edge"], "contradicts")

            found = _capture_json(lambda: graph.cmd_retrieve(
                wiki, "queue grows", 1, 6, 1, "graph", False, 1,
                expand_thin_sources=False, edge_types=["supports"]))
            evidence = [p for p in found["pages"] if p["page"] == "evidence/cpu.md"]
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0]["edge"], "supports")
            self.assertEqual(evidence[0]["distance"], 1)
            self.assertEqual(evidence[0]["type"], "evidence")

            # Default retrieval also walks the typed edge, and a wikilink
            # neighbourhood is unchanged.
            walked = _capture_json(lambda: graph.cmd_retrieve(
                wiki, "queue grows", 1, 6, 1, "graph", False, 1,
                expand_thin_sources=False))
            self.assertTrue(any(
                p["page"] == "evidence/cpu.md" and p.get("edge") in {"supports", "contradicts"}
                for p in walked["pages"]
            ))
            linked = _capture_json(lambda: graph.cmd_neighbors(
                wiki, "concepts/a.md", 1, "out"))
            self.assertEqual([r["path"] for r in linked], ["concepts/b.md"])
            self.assertNotIn("edge", linked[0])

    def test_broken_file_aborts_rebuild(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wiki = root / "wiki"
            _write(root / ".curator" / "meta-ontology.yaml", "version: 1\n")
            _page(wiki, "concepts/a.md",
                  '---\ntitle: "A"\ntype: concept\n---\n\nBody.\n')
            with self.assertRaises(SystemExit):
                with contextlib.redirect_stdout(io.StringIO()):
                    graph.rebuild(wiki, force=True)


if __name__ == "__main__":
    unittest.main()
