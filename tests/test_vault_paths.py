"""Regression tests for vault-original resolution, citation paths, and
table-extraction quality.

Four defects, all found while curating real workspaces:

  - **`kept_as` missing for in-place vault ingests.** `--source-path-only`
    conflated "original is outside the vault" with "original is already
    inside the vault", writing `source_in_place: true` and no `kept_as` for
    both. Consumers key on `kept_as` to locate the original, so in-place
    PDFs could never enter the multimodal or figure queues.

  - **No `source_path` fallback in those consumers.** Independently of the
    above, project-dir ingests legitimately have no `kept_as` (the original
    stays where the user keeps it), so externally-scanned PDFs could never
    reach those queues at all.

  - **`source_in_place` missing from `ALLOWED_FM_KEYS`.** `local_ingest`
    has always written it, but `read_frontmatter` silently dropped it — so
    no consumer using the parser could see it, which is why `scan.py`
    raw-parses the key by line prefix. This is what kept the first bug
    invisible, and it silently no-ops any code that filters on the field.

  - **Citations naming an unindexed path.** A `(vault:...)` path the FTS5
    index doesn't hold is invisible to search and can never verify a claim.
    Observed on 38 pages of one wiki; 36 carried the same wrong path in
    frontmatter `sources:`, which is where the worker copied it from.

Plus the table fill-rate filter: pdfplumber emits a label column with
nothing beside it (59 rows of language names, every data column empty),
which cleared every existing filter and became a wiki page carrying no
information.

Run:  python3 -m unittest discover tests
"""
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "curiosity-engine" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import naming  # noqa: E402
import sweep  # noqa: E402
import vault_index  # noqa: E402


@contextmanager
def workspace():
    """Bare workspace with vault/ and wiki/ and a cwd pinned to its root."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "vault").mkdir()
        (root / "wiki" / "sources").mkdir(parents=True)
        (root / "wiki" / "concepts").mkdir(parents=True)
        cwd = os.getcwd()
        os.chdir(root)
        try:
            yield root
        finally:
            os.chdir(cwd)


def _extraction(root, name, *, source_path, in_place=True, kept_as=None,
                 body="prose body"):
    fm = ["---", f"source_path: {source_path}", "sha256: deadbeef"]
    if in_place:
        fm.append("source_in_place: true")
    if kept_as:
        fm.append(f"kept_as: {kept_as}")
    fm += ["extraction: full", "extraction_method: pypdf",
            "has_tables: true", "tables_extracted: 0",
            "multimodal_recommended: true", "---", "", body, ""]
    p = root / "vault" / name
    p.write_text("\n".join(fm))
    return p


class AllowedFrontmatterKeys(unittest.TestCase):

    def test_source_in_place_survives_the_parser(self):
        """local_ingest writes it, so the parser must not drop it. When it
        did, any consumer filtering on the field silently matched nothing."""
        self.assertIn("source_in_place", naming.ALLOWED_FM_KEYS)
        fm, _ = naming.read_frontmatter(
            "---\nsource_path: vault/a.pdf\nsource_in_place: true\n---\nx\n")
        self.assertEqual(str(fm.get("source_in_place")).lower(), "true")

    def test_tables_filtered_survives_the_parser(self):
        self.assertIn("tables_filtered", naming.ALLOWED_FM_KEYS)


class OriginalResolution(unittest.TestCase):

    def test_prefers_kept_as_when_present(self):
        with workspace() as root:
            (root / "vault" / "paper.pdf").write_bytes(b"%PDF")
            fm = {"kept_as": "paper.pdf", "source_path": "/elsewhere/x.pdf"}
            got = sweep._original_for_extraction(fm, root / "vault")
            self.assertEqual(got, root / "vault" / "paper.pdf")

    def test_falls_back_to_source_path_for_project_dir_ingests(self):
        """The case that could never reach the queues at all."""
        with workspace() as root:
            ext = root / "outside"
            ext.mkdir()
            (ext / "paper.pdf").write_bytes(b"%PDF")
            fm = {"source_path": str(ext / "paper.pdf")}
            got = sweep._original_for_extraction(fm, root / "vault")
            self.assertEqual(got, ext / "paper.pdf")

    def test_none_when_original_is_gone(self):
        with workspace() as root:
            fm = {"kept_as": "vanished.pdf"}
            self.assertIsNone(
                sweep._original_for_extraction(fm, root / "vault"))
            fm = {"source_path": "/nope/vanished.pdf"}
            self.assertIsNone(
                sweep._original_for_extraction(fm, root / "vault"))

    def test_none_when_nothing_recorded(self):
        with workspace() as root:
            self.assertIsNone(sweep._original_for_extraction({}, root / "vault"))

    def test_display_is_honest_about_location(self):
        with workspace() as root:
            vault = root / "vault"
            (vault / "paper.pdf").write_bytes(b"%PDF")
            ext = root / "outside"
            ext.mkdir()
            (ext / "other.pdf").write_bytes(b"%PDF")
            self.assertEqual(
                sweep._original_ref_display(vault / "paper.pdf", vault),
                "vault/paper.pdf")
            # Prefixing `vault/` here would name a file that doesn't exist.
            self.assertEqual(
                sweep._original_ref_display(ext / "other.pdf", vault),
                str(ext / "other.pdf"))
            self.assertEqual(
                sweep._original_ref_display(None, vault),
                "(original not found)")


class MultimodalQueueReachability(unittest.TestCase):

    def _queue(self, root):
        buf = io.StringIO()
        with redirect_stdout(buf):
            sweep.cmd_multimodal_table_candidates(root / "wiki")
        return json.loads(buf.getvalue())

    def test_all_three_ingest_shapes_are_reachable(self):
        with workspace() as root:
            vault = root / "vault"
            outside = root / "outside"
            outside.mkdir()
            for n in ("a.pdf", "c.pdf"):
                (vault / n).write_bytes(b"%PDF")
            (outside / "b.pdf").write_bytes(b"%PDF")
            # in-place from inside vault/, pre-fix (no kept_as)
            _extraction(root, "20260101-a.pdf.extracted.md",
                         source_path="vault/a.pdf")
            # project-dir ingest, original outside the vault
            _extraction(root, "20260101-b.pdf.extracted.md",
                         source_path=str(outside / "b.pdf"))
            # already carries kept_as
            _extraction(root, "20260101-c.pdf.extracted.md",
                         source_path="vault/c.pdf", kept_as="c.pdf")
            got = self._queue(root)
            self.assertEqual(got["count"], 3, got)
            for entry in got["queue"]:
                self.assertTrue(entry["original_path"],
                                f"unresolved original: {entry}")

    def test_missing_original_still_skipped(self):
        with workspace() as root:
            _extraction(root, "20260101-gone.pdf.extracted.md",
                         source_path="vault/gone.pdf")
            self.assertEqual(self._queue(root)["count"], 0)


class BackfillKeptAs(unittest.TestCase):

    def _run(self, root, dry_run=False):
        buf = io.StringIO()
        with redirect_stdout(buf):
            sweep.cmd_backfill_kept_as(root / "wiki", dry_run=dry_run)
        return json.loads(buf.getvalue())

    def _fixture(self, root):
        vault = root / "vault"
        outside = root / "outside"
        outside.mkdir()
        (vault / "a.pdf").write_bytes(b"%PDF")
        (vault / "c.pdf").write_bytes(b"%PDF")
        (outside / "b.pdf").write_bytes(b"%PDF")
        _extraction(root, "20260101-a.pdf.extracted.md",
                     source_path="vault/a.pdf")
        _extraction(root, "20260101-b.pdf.extracted.md",
                     source_path=str(outside / "b.pdf"))
        _extraction(root, "20260101-c.pdf.extracted.md",
                     source_path="vault/c.pdf", kept_as="c.pdf")

    def test_patches_only_in_vault_originals(self):
        with workspace() as root:
            self._fixture(root)
            got = self._run(root)
            self.assertEqual(got["patched"], 1)
            self.assertEqual(got["skipped_already_have_kept_as"], 1)
            self.assertEqual(got["skipped_original_outside_vault"], 1)
            patched = (root / "vault" / "20260101-a.pdf.extracted.md").read_text()
            self.assertIn("kept_as: a.pdf", patched)
            self.assertIn("source_in_place: true", patched)
            # The external one is left for the source_path fallback.
            self.assertNotIn(
                "kept_as:",
                (root / "vault" / "20260101-b.pdf.extracted.md").read_text())

    def test_idempotent(self):
        with workspace() as root:
            self._fixture(root)
            self._run(root)
            again = self._run(root)
            self.assertEqual(again["patched"], 0)
            self.assertEqual(again["skipped_already_have_kept_as"], 2)

    def test_dry_run_writes_nothing(self):
        with workspace() as root:
            self._fixture(root)
            before = (root / "vault" / "20260101-a.pdf.extracted.md").read_text()
            got = self._run(root, dry_run=True)
            self.assertEqual(got["patched"], 1)
            self.assertEqual(
                (root / "vault" / "20260101-a.pdf.extracted.md").read_text(),
                before)

    def test_restamps_paired_stub_hash(self):
        """Patching the extraction changes its bytes, staling the stub's
        vault_sha256. Benign for dedup, but the two shouldn't drift."""
        with workspace() as root:
            self._fixture(root)
            (root / "wiki" / "sources" / "a-2026.md").write_text(
                "---\ntitle: \"[src] A\"\ntype: source\n"
                "sources: [20260101-a.pdf.extracted.md]\n"
                "vault_sha256: STALE\n---\n\n"
                "Summary (vault:20260101-a.pdf.extracted.md)\n")
            got = self._run(root)
            self.assertEqual(got["stubs_restamped"], 1)
            stub = (root / "wiki" / "sources" / "a-2026.md").read_text()
            self.assertNotIn("STALE", stub)


class CitationPathAliasing(unittest.TestCase):

    def test_resolves_original_name_to_indexed_path(self):
        m = sweep._indexed_alias_map({
            "20260728-230732-local-gu-2023-mamba.pdf.extracted.md",
            "20260728-231916-local-wiki-rag.md.extracted.md",
        })
        self.assertEqual(m["gu-2023-mamba"],
                          "20260728-230732-local-gu-2023-mamba.pdf.extracted.md")
        self.assertEqual(m["wiki-rag.md"],
                          "20260728-231916-local-wiki-rag.md.extracted.md")

    def test_ambiguous_alias_is_dropped_not_guessed(self):
        """`llama` is a substring of two sources; anchoring on the whole
        original name is what keeps them apart, and a genuinely shared
        alias must resolve to neither."""
        m = sweep._indexed_alias_map({
            "20260101-000000-local-llama.pdf.extracted.md",
            "20260101-000001-remote-llama.pdf.extracted.md",
        })
        self.assertNotIn("llama", m)

    def test_distinct_names_containing_each_other_stay_distinct(self):
        m = sweep._indexed_alias_map({
            "20260101-000000-local-touvron-2023-llama.pdf.extracted.md",
            "20260101-000001-local-roziere-2023-code-llama.pdf.extracted.md",
        })
        self.assertEqual(
            m["touvron-2023-llama"],
            "20260101-000000-local-touvron-2023-llama.pdf.extracted.md")
        self.assertEqual(
            m["roziere-2023-code-llama"],
            "20260101-000001-local-roziere-2023-code-llama.pdf.extracted.md")


class FixCitationPaths(unittest.TestCase):

    INDEXED = "20260101-000000-local-gu-2023-mamba.pdf.extracted.md"

    @contextmanager
    def _ws(self):
        with workspace() as root:
            (root / "vault" / self.INDEXED).write_text(
                "---\nsha256: x\n---\nselective scan state space prose\n")
            vault_index.DB = Path("vault/vault.db")
            vault_index.CONFIG_PATH = Path(".curator/config.json")
            with redirect_stdout(io.StringIO()):
                vault_index.rebuild()
            yield root

    def _run(self, root, dry_run=False):
        buf = io.StringIO()
        with redirect_stdout(buf):
            sweep.cmd_fix_citation_paths(root / "wiki", dry_run=dry_run)
        return json.loads(buf.getvalue())

    def test_repoints_stub_style_path_in_body_and_frontmatter(self):
        with self._ws() as root:
            page = root / "wiki" / "concepts" / "ssm.md"
            page.write_text(
                "---\ntype: concept\nsources: [gu-2023-mamba.md]\n---\n\n"
                "Linear-time sequence modelling (vault:gu-2023-mamba.md).\n")
            got = self._run(root)
            self.assertEqual(got["pages_patched"], 1)
            self.assertEqual(got["unresolved"], [])
            text = page.read_text()
            self.assertIn(f"(vault:{self.INDEXED})", text)
            self.assertIn(f"sources: [{self.INDEXED}]", text)
            self.assertNotIn("gu-2023-mamba.md)", text)

    def test_preserves_citation_count(self):
        """Substitution is 1:1 — the ratchet must not see a citation change."""
        from score_diff import citation_count
        with self._ws() as root:
            page = root / "wiki" / "concepts" / "ssm.md"
            page.write_text(
                "---\ntype: concept\n---\n\n"
                "A (vault:gu-2023-mamba.md). B (vault:gu-2023-mamba.md).\n")
            before = citation_count(page.read_text())
            self._run(root)
            self.assertEqual(citation_count(page.read_text()), before)

    def test_normalises_vault_double_prefix(self):
        with self._ws() as root:
            page = root / "wiki" / "concepts" / "ssm.md"
            page.write_text("---\ntype: concept\n---\n\n"
                            f"Claim (vault:vault/{self.INDEXED}).\n")
            self._run(root)
            self.assertIn(f"(vault:{self.INDEXED})", page.read_text())

    def test_unresolvable_path_reported_not_guessed(self):
        with self._ws() as root:
            page = root / "wiki" / "concepts" / "ssm.md"
            page.write_text("---\ntype: concept\n---\n\n"
                            "Claim (vault:no-such-paper-2099.md).\n")
            got = self._run(root)
            self.assertEqual(got["pages_patched"], 0)
            self.assertEqual(len(got["unresolved"]), 1)
            self.assertEqual(got["unresolved"][0]["vault_path"],
                              "no-such-paper-2099.md")
            self.assertIn("no-such-paper-2099.md", page.read_text())

    def test_ignores_schema_placeholder(self):
        with self._ws() as root:
            page = root / "wiki" / "concepts" / "notes.md"
            page.write_text("---\ntype: note\n---\n\n"
                            "<body with (vault:path) citations>\n")
            got = self._run(root)
            self.assertEqual(got["pages_patched"], 0)
            self.assertEqual(got["unresolved"], [])

    def test_dry_run_writes_nothing(self):
        with self._ws() as root:
            page = root / "wiki" / "concepts" / "ssm.md"
            page.write_text("---\ntype: concept\n---\n\n"
                            "Claim (vault:gu-2023-mamba.md).\n")
            before = page.read_text()
            got = self._run(root, dry_run=True)
            self.assertEqual(got["pages_patched"], 1)
            self.assertEqual(page.read_text(), before)

    def test_scan_agrees_after_the_fix(self):
        with self._ws() as root:
            page = root / "wiki" / "concepts" / "ssm.md"
            page.write_text("---\ntype: concept\n---\n\n"
                            "Claim (vault:gu-2023-mamba.md).\n")
            self.assertEqual(
                len(sweep.scan_unindexed_citations(root / "wiki")), 1)
            self._run(root)
            self.assertEqual(
                sweep.scan_unindexed_citations(root / "wiki"), [])


class SpuriousTableFilter(unittest.TestCase):

    def test_rejects_label_column_with_empty_data(self):
        """59 rows of language names, every data column blank. Cleared all
        three pre-existing filters and became a page carrying no data."""
        headers = ["Language", "BLEU", "chrF", "TER"]
        rows = [[n, "", "", ""] for n in
                ["Afrikaans", "Albanian", "Amharic", "Arabic"] * 5]
        spurious, why = sweep.looks_spurious_table(headers, rows)
        self.assertTrue(spurious)
        self.assertEqual(why, "empty-data-columns")

    def test_spares_genuinely_sparse_benchmark_grid(self):
        headers = ["Model", "MMLU", "GSM8K", "HumanEval"]
        rows = [["LLaMA 7B", "35.1", "11.0", ""],
                ["LLaMA 13B", "46.9", "", "15.8"],
                ["LLaMA 65B", "63.4", "50.9", "23.7"]]
        self.assertEqual(sweep.looks_spurious_table(headers, rows), (False, ""))

    def test_spares_garbled_but_populated_transcription(self):
        """Character interleaving is the numeric-review path's job — every
        cheap heuristic for it also rejects real tables."""
        headers = ["Task", "T0-SFT0-SF"]
        rows = [["SummSuamrizmataiorinzat", "MuppMetuppet"], ["a", "b"]]
        self.assertEqual(sweep.looks_spurious_table(headers, rows), (False, ""))

    def test_preexisting_filters_intact(self):
        self.assertEqual(
            sweep.looks_spurious_table(["Only"], [["a"], ["b"]])[1],
            "single-column")
        self.assertEqual(
            sweep.looks_spurious_table(["A", "B"], [["x" * 200, "y" * 200]])[1],
            "cells-look-like-prose")
        self.assertEqual(
            sweep.looks_spurious_table(["Model", "Score"],
                                        [["LLaMA", "63.4"]]),
            (False, ""))

    def test_back_compat_alias_still_exported(self):
        self.assertIs(sweep._looks_spurious_table, sweep.looks_spurious_table)


class IngestAppliesTableFilter(unittest.TestCase):

    def test_local_ingest_shares_the_filter(self):
        """tables_extracted must count usable tables: multimodal_recommended
        is derived from `tables_extracted == 0`, so counting junk locked a
        PDF out of the recovery pass built for exactly that case."""
        src = (SCRIPTS / "local_ingest.py").read_text()
        self.assertIn("from sweep import looks_spurious_table", src)
        self.assertIn("tables_filtered", src)


class CliWiring(unittest.TestCase):

    def test_new_commands_are_dispatchable(self):
        for cmd in ("backfill-kept-as", "fix-citation-paths"):
            with workspace():
                out = subprocess.run(
                    [sys.executable, str(SCRIPTS / "sweep.py"), cmd, "wiki"],
                    capture_output=True, text=True)
                self.assertEqual(out.returncode, 0,
                                  f"{cmd} failed: {out.stderr[:400]}")
                json.loads(out.stdout)   # must emit one JSON object


if __name__ == "__main__":
    unittest.main()
