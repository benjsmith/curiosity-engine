"""v1.8: QUERY anti-crowding + substantive source summaries + thin expand.

No network. Temporary workspaces only.
Run: python3 -m unittest tests.test_v18_anti_crowding_sources
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "curiosity-engine" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from naming import (  # noqa: E402
    build_source_summary,
    jaccard,
    lexical_tokens,
    recommend_analysis_write,
)


def run(argv, cwd):
    return subprocess.run(
        [sys.executable, *argv],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


class LexicalHelpers(unittest.TestCase):
    def test_jaccard_identity(self):
        a = lexical_tokens("Chinchilla scaling laws compute optimal")
        self.assertEqual(jaccard(a, a), 1.0)

    def test_jaccard_disjoint(self):
        a = lexical_tokens("alpha beta gamma")
        b = lexical_tokens("delta epsilon zeta")
        self.assertEqual(jaccard(a, b), 0.0)


class RecommendAnalysisWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.wiki = self.root / "wiki"
        (self.wiki / "analyses").mkdir(parents=True)
        (self.wiki / "analyses" / "chinchilla-scaling.md").write_text(
            "---\n"
            'title: "[anl] Chinchilla scaling laws"\n'
            "type: analysis\n"
            "---\n\n"
            "Compute-optimal training doubles tokens when parameters double "
            "across Chinchilla and related scaling results.\n"
        )
        (self.wiki / "analyses" / "attention-mechanisms.md").write_text(
            "---\n"
            'title: "[anl] Attention mechanisms across architectures"\n'
            "type: analysis\n"
            "---\n\n"
            "How soft attention differs from hard attention in transformers.\n"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_update_near_twin(self):
        out = recommend_analysis_write(
            self.wiki,
            "Chinchilla scaling laws and compute optimality",
            "Compute-optimal training doubles tokens with parameters",
        )
        self.assertEqual(out["action"], "update")
        self.assertIn("chinchilla-scaling", out["best"]["page"])

    def test_link_related(self):
        # Mid overlap with an existing analysis title → link band.
        out = recommend_analysis_write(
            self.wiki,
            "Attention mechanisms and memory",
            "Brief note relating attention to working memory theories",
            update_threshold=0.85,
            link_threshold=0.25,
        )
        self.assertEqual(out["action"], "link")
        self.assertIn("attention-mechanisms", out["best"]["page"])

    def test_new_unrelated(self):
        out = recommend_analysis_write(
            self.wiki,
            "Clinical audit templates across hospitals",
            "Shared patterns in clinical audit checklists and board minutes",
        )
        self.assertEqual(out["action"], "new")

    def test_cli(self):
        proc = run(
            [str(SCRIPTS / "naming.py"), "recommend-analysis", "wiki",
             "--title", "Chinchilla scaling laws compute optimal",
             "--head", "Compute-optimal training doubles tokens"],
            cwd=self.root,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["action"], "update")


class BuildSourceSummary(unittest.TestCase):
    def test_summary_has_claims_and_citation(self):
        meta = {
            "topic": "attention-is-all-you-need",
            "author": "Vaswani",
            "year": "2017",
            "origin": "arxiv",
            "full_title": "Attention Is All You Need",
        }
        body = (
            "<!-- BEGIN FETCHED CONTENT -->\n"
            "# Attention Is All You Need\n\n"
            "We propose a new simple network architecture, the Transformer, "
            "based solely on attention mechanisms, dispensing with recurrence "
            "and convolutions entirely.\n\n"
            "Experiments on two machine translation tasks show these models "
            "to be superior in quality while being more parallelizable.\n"
            "<!-- END FETCHED CONTENT -->\n"
        )
        summary = build_source_summary(meta, body, "attention.extracted.md")
        self.assertIn("Vaswani", summary)
        self.assertIn("2017", summary)
        self.assertIn("Key claims:", summary)
        self.assertIn("(vault:attention.extracted.md)", summary)
        self.assertNotIn("http", summary.casefold())
        self.assertNotIn("BEGIN FETCHED", summary)


class FixSourceStubsSubstantive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.wiki = self.root / "wiki"
        self.vault = self.root / "vault"
        (self.wiki / "sources").mkdir(parents=True)
        self.vault.mkdir()
        (self.root / ".curator").mkdir()
        extract = (
            "---\n"
            "source_path: paper.pdf\n"
            "date: 2017-06-01\n"
            "title: Attention Is All You Need\n"
            "---\n\n"
            "<!-- BEGIN FETCHED CONTENT -->\n"
            "Attention Is All You Need\n\n"
            "Ashish Vaswani and colleagues introduce the Transformer, a "
            "architecture based solely on attention mechanisms without "
            "recurrence or convolution for sequence transduction tasks.\n\n"
            "On WMT 2014 English-to-German translation the model obtains "
            "a new state of the art BLEU score while training faster.\n"
            "<!-- END FETCHED CONTENT -->\n"
        )
        (self.vault / "attention.extracted.md").write_text(extract)
        # Hollow legacy stub covering the same extraction
        (self.wiki / "sources" / "vaswani-2017-attention.md").write_text(
            "---\n"
            'title: "[src] Attention Is All You Need — Vaswani, 2017"\n'
            "type: source\n"
            "created: 2017-06-01\n"
            "updated: 2017-06-01\n"
            "sources: [attention.extracted.md]\n"
            "vault_sha256: deadbeef\n"
            "---\n\n"
            "Attention Is All You Need (vault:attention.extracted.md)\n"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_refreshes_thin_stub(self):
        proc = run(
            [str(SCRIPTS / "sweep.py"), "fix-source-stubs", "wiki"],
            cwd=self.root,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        data = json.loads(proc.stdout)
        self.assertGreaterEqual(data.get("updated", 0), 1)
        body = (self.wiki / "sources" / "vaswani-2017-attention.md").read_text()
        self.assertIn("Key claims:", body)
        self.assertIn("(vault:attention.extracted.md)", body)
        self.assertIn("Who/when:", body)


class ThinSourceRetrieveExpand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.wiki = self.root / "wiki"
        self.vault = self.root / "vault"
        (self.wiki / "sources").mkdir(parents=True)
        (self.wiki / "concepts").mkdir(parents=True)
        self.vault.mkdir()
        (self.root / ".curator").mkdir()
        (self.wiki / "concepts" / "self-attention.md").write_text(
            "---\n"
            'title: "[con] Self attention"\n'
            "type: concept\n"
            "---\n\n"
            "Sequence models using [[vaswani-2017-attention]] and BLEU "
            "English-German translation results from the paper.\n"
        )
        (self.wiki / "sources" / "vaswani-2017-attention.md").write_text(
            "---\n"
            'title: "[src] Attention Is All You Need"\n'
            "type: source\n"
            "sources: [attention.extracted.md]\n"
            "---\n\n"
            "Short stub only. (vault:attention.extracted.md)\n"
        )
        (self.vault / "attention.extracted.md").write_text(
            "---\n"
            "source_path: paper.pdf\n"
            "---\n"
            "<!-- BEGIN FETCHED CONTENT -->\n"
            "The Transformer relies entirely on self-attention to compute "
            "representations of its input and output without recurrence.\n"
            "BLEU scores improve on English-German translation benchmarks.\n"
            "<!-- END FETCHED CONTENT -->\n"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_retrieve_attaches_vault_extracts(self):
        proc = run(
            [str(SCRIPTS / "graph.py"), "retrieve", "wiki",
             "self-attention BLEU translation vaswani",
             "--route", "graph", "--limit", "6"],
            cwd=self.root,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        data = json.loads(proc.stdout)
        extracts = data.get("vault_extracts") or []
        self.assertTrue(extracts, msg=proc.stdout)
        self.assertTrue(extracts[0].get("untrusted"))
        self.assertIn("BEGIN FETCHED CONTENT", extracts[0].get("text", ""))
        self.assertEqual(extracts[0].get("reason"), "thin_source")

    def test_no_expand_flag(self):
        proc = run(
            [str(SCRIPTS / "graph.py"), "retrieve", "wiki",
             "self-attention BLEU translation vaswani",
             "--route", "graph", "--no-expand-thin-sources"],
            cwd=self.root,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        data = json.loads(proc.stdout)
        self.assertFalse(data.get("vault_extracts"))


if __name__ == "__main__":
    unittest.main()
