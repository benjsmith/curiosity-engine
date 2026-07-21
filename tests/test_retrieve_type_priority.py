"""Unit tests for graph.py type-aware demotion (no kuzu / no network)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "curiosity-engine" / "scripts"))

import graph  # noqa: E402


class NeedleHeuristic(unittest.TestCase):
    def test_caption_query_is_needle(self):
        self.assertTrue(graph.query_is_needle("What does Fig. 2 say about padding?"))

    def test_exam_compare_not_necessarily_needle(self):
        # Type demotion still prefers facts over analyses via synth ranks.
        self.assertFalse(graph.query_is_needle(
            "How does Nesterov momentum differ from classical momentum?"))

    def test_quoted_passage_is_needle(self):
        self.assertTrue(graph.query_is_needle(
            'Explain the passage "hidden technical debt in ML systems"'))


class TypeRank(unittest.TestCase):
    def test_facts_beat_analyses_on_needle(self):
        self.assertLess(
            graph.type_rank("facts", needle=True),
            graph.type_rank("analyses", needle=True),
        )

    def test_facts_still_beat_analyses_on_synth(self):
        self.assertLess(
            graph.type_rank("facts", needle=False),
            graph.type_rank("analyses", needle=False),
        )

    def test_page_type_bucket_from_path(self):
        self.assertEqual(graph.page_type_bucket("facts/nesterov.md"), "facts")
        self.assertEqual(graph.page_type_bucket("analyses/opt-survey.md"), "analyses")
        self.assertEqual(
            graph.page_type_bucket("orphan.md", fm_type="fact"), "fact")


class DemoteByType(unittest.TestCase):
    def test_demote_analysis_after_fact_and_figure(self):
        pages = [
            "analyses/long-survey.md",
            "facts/exam-atom.md",
            "figures/fig-caption.md",
            "concepts/momentum.md",
        ]
        types = {
            "analyses/long-survey.md": "analyses",
            "facts/exam-atom.md": "facts",
            "figures/fig-caption.md": "figures",
            "concepts/momentum.md": "concepts",
        }
        out = graph.demote_by_type(pages, types, needle=True)
        self.assertEqual(out[0], "facts/exam-atom.md")
        self.assertEqual(out[1], "figures/fig-caption.md")
        self.assertEqual(out[-1], "analyses/long-survey.md")

    def test_stable_within_same_rank(self):
        pages = ["facts/a.md", "facts/b.md", "facts/c.md"]
        types = {p: "facts" for p in pages}
        self.assertEqual(graph.demote_by_type(pages, types), pages)

    def test_synth_keeps_analysis_before_notes(self):
        pages = ["notes/n.md", "analyses/a.md", "facts/f.md"]
        types = {
            "notes/n.md": "notes",
            "analyses/a.md": "analyses",
            "facts/f.md": "facts",
        }
        out = graph.demote_by_type(pages, types, needle=False)
        self.assertEqual(out[0], "facts/f.md")
        self.assertEqual(out[1], "analyses/a.md")
        self.assertEqual(out[2], "notes/n.md")


if __name__ == "__main__":
    unittest.main()
