"""Tests for bootstrap densify (captions, floors, normalize, link filter)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "curiosity-engine" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bootstrap  # noqa: E402
from score_diff import new_page_verdict, _floors_for  # noqa: E402


class CaptionHarvest(unittest.TestCase):
    def test_fig_and_table_lines(self):
        text = """\
Fig. 1: Pedestrian decision diagram based on meta-analysis.
Some prose.

Table 2. Dataset sizes for ImageNet and CIFAR-10.
More text.
"""
        caps = bootstrap.harvest_captions_from_text(text, "lec/foo.extracted.md")
        kinds = {c["kind"] for c in caps}
        self.assertIn("figure", kinds)
        self.assertIn("table", kinds)
        self.assertTrue(any("meta-analysis" in c["caption"] for c in caps))

    def test_apply_routes_figure_and_table(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            vault = ws / "vault" / "lec"
            vault.mkdir(parents=True)
            (vault / "x.extracted.md").write_text(
                "---\ntitle: x\n---\n\n"
                "Fig. 3: Convolutional stack with 3x3 filters.\n\n"
                "Table 1: Accuracy of baseline models on CIFAR.\n",
                encoding="utf-8",
            )
            (ws / "wiki").mkdir()
            caps = bootstrap.harvest_captions(ws)
            self.assertGreaterEqual(len(caps), 2)
            result = bootstrap.apply_captions(
                ws, caps, with_facts=False, dry_run=False, gate=True,
            )
            self.assertGreaterEqual(result["n_figures"], 1)
            self.assertGreaterEqual(result["n_tables"], 1)
            self.assertEqual(result["n_facts"], 0)
            figs = list((ws / "wiki" / "figures").glob("*.md"))
            tabs = list((ws / "wiki" / "tables").glob("*.md"))
            self.assertTrue(figs)
            self.assertTrue(tabs)
            fig_text = figs[0].read_text(encoding="utf-8")
            self.assertIn("origin: caption-text", fig_text)
            self.assertIn("(vault:", fig_text)
            tab_text = tabs[0].read_text(encoding="utf-8")
            self.assertIn("type: extracted-table", tab_text)

    def test_optional_fact_twin(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            vault = ws / "vault"
            vault.mkdir()
            (vault / "a.extracted.md").write_text(
                "Fig. 1: Only figure caption for optional fact twin test here.\n",
                encoding="utf-8",
            )
            (ws / "wiki").mkdir()
            caps = bootstrap.harvest_captions(ws)
            result = bootstrap.apply_captions(
                ws, caps, with_facts=True, dry_run=False, gate=True,
            )
            self.assertGreaterEqual(result["n_facts"], 1)
            fact = list((ws / "wiki" / "facts").glob("*.md"))[0].read_text()
            self.assertIn("verbatim: true", fact)
            self.assertIn("origin: bootstrap-caption", fact)


class Floors(unittest.TestCase):
    def test_verbatim_fact_15_words(self):
        page = (
            '---\ntitle: "[fact] X"\ntype: fact\nverbatim: true\n'
            "origin: other\nsources: [a.md]\n---\n\n"
            "Short claim only. (vault:a.md)\n[[stub]]\n"
        )
        # word count may be low — need ~15 body tokens
        page = (
            '---\ntitle: "[fact] X"\ntype: fact\nverbatim: true\n'
            "sources: [a.md]\n---\n\n"
            "Alpha beta gamma delta epsilon zeta eta theta iota kappa "
            "lambda mu nu. (vault:a.md)\n[[stub]]\n"
        )
        ok, reason = new_page_verdict(page, Path("facts/x.md"))
        self.assertTrue(ok, reason)

    def test_bootstrap_fact_zero_wikilinks(self):
        page = (
            '---\ntitle: "[fact] Y"\ntype: fact\n'
            "origin: bootstrap-facts\nsources: [a.md]\n---\n\n"
            "Alpha beta gamma delta epsilon zeta eta theta iota kappa "
            "lambda mu nu xi. (vault:a.md)\n"
        )
        floors = _floors_for(Path("facts/y.md"), page)
        self.assertEqual(floors["wikilinks"], 0)
        ok, reason = new_page_verdict(page, Path("facts/y.md"))
        self.assertTrue(ok, reason)


class NormalizeAndLinks(unittest.TestCase):
    def test_vault_double_prefix(self):
        f = bootstrap._normalize_fact_record({
            "stem": "nesterov-look-ahead",
            "title": "Nesterov",
            "body": "Uses look-ahead. (vault:vault/lec/opt.extracted.md)",
            "source": "vault/lec/opt.extracted.md",
        })
        self.assertEqual(f["source"], "lec/opt.extracted.md")
        self.assertIn("(vault:lec/opt.extracted.md)", f["body"])
        self.assertNotIn("vault:vault/", f["body"])

    def test_link_filter_strips_invents(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            facts = ws / "wiki" / "facts"
            facts.mkdir(parents=True)
            (ws / "wiki" / "concepts").mkdir(parents=True)
            (ws / "wiki" / "concepts" / "momentum.md").write_text(
                '---\ntitle: "[con] Momentum"\ntype: concept\n---\n\nBody.\n',
                encoding="utf-8",
            )
            (facts / "f1.md").write_text(
                '---\ntitle: "[fact] F"\ntype: fact\norigin: bootstrap-facts\n'
                "sources: [a.md]\n---\n\n"
                "Plain claim. (vault:a.md)\n",
                encoding="utf-8",
            )
            result = bootstrap.apply_link_rewrites(
                ws,
                [{
                    "stem": "f1",
                    "body": (
                        "[[momentum]] is good; [[invented-entity]] is not. "
                        "(vault:a.md)"
                    ),
                }],
                dry_run=False,
            )
            self.assertEqual(result["n_applied"], 1)
            body = (facts / "f1.md").read_text(encoding="utf-8")
            self.assertIn("[[momentum]]", body)
            self.assertNotIn("[[invented-entity]]", body)


class PackPlan(unittest.TestCase):
    def test_partition(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            vault = ws / "vault"
            vault.mkdir()
            for i in range(5):
                (vault / f"d{i}.extracted.md").write_text(
                    f"Fig. 1: caption {i} " * 3 + "\nbody\n", encoding="utf-8",
                )
            packs = bootstrap.partition_vault(ws, docs_per_pack=2)
            self.assertEqual(len(packs), 3)
            plan = bootstrap.cmd_facts_plan(ws / "wiki", 2, 50_000)
            self.assertEqual(plan["n_packs"], 3)


if __name__ == "__main__":
    unittest.main()
