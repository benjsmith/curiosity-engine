"""Regression tests for the score_diff citation gate and its inputs.

Three defects, all observed on a real curate run and all reproducible with
a fixture vault:

  - **implicit-AND citation check.** `verify_new_citations` issued one bare
    FTS5 MATCH built from every content word on the citation's line. FTS5
    ANDs bare terms, so under the default `write_other: ultra` compression
    — where a paragraph is one line of 40-90 content words — the query
    demanded that all of them occur in one document. Measured on a curated
    25-source wiki it rejected 17% of the citations an opus batch reviewer
    had already approved (43% counted per citation *line*), and the
    workaround it taught workers was a short filler lead line carrying a
    duplicate citation. Replaced by a coverage fraction over the claim's
    most distinctive (low document-frequency) terms.

  - **PDF ligatures.** pypdf preserves the codepoints TeX emitted, and
    FTS5's `unicode61` tokenizer folds case but does not decompose
    ligatures, so `speciﬁc` (U+FB01) and `specific` were unrelated tokens
    and no ASCII query could reach the text. Affects a lot of ML
    vocabulary: specific, efficient, different, final.

  - **bloat cap vs stubs.** The skill creates its own placeholder pages,
    then a flat 1.5× body-token ceiling blocked the first real curation
    pass on every one of them.

Fixtures are hand-built vaults indexed through the real `vault_index`
code path. No network, no model load.

Run:  python3 -m unittest discover tests
"""
import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "curiosity-engine" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import naming  # noqa: E402
import score_diff  # noqa: E402
import sweep  # noqa: E402
import vault_index  # noqa: E402


@contextmanager
def workspace(sources: dict):
    """A temp workspace whose vault holds `sources` and is FTS5-indexed.

    `sources` maps `<stem>.extracted.md` -> body text. Indexing runs
    through `vault_index.rebuild()` so the test exercises the same path a
    real ingest does, including ligature normalisation.
    """
    import os
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "vault").mkdir()
        (root / "wiki").mkdir()
        for name, body in sources.items():
            (root / "vault" / name).write_text(body, encoding="utf-8")
        cwd = os.getcwd()
        os.chdir(root)
        try:
            # vault_index resolves DB / config relative to the cwd.
            vault_index.DB = Path("vault/vault.db")
            vault_index.CONFIG_PATH = Path(".curator/config.json")
            with redirect_stdout(io.StringIO()):   # rebuild() prints a JSON line
                vault_index.rebuild()
            yield root
        finally:
            os.chdir(cwd)


# A source written the way a real paper extraction reads, with ligatures
# exactly where pdftex would put them.
PAPER = """---
sha256: abc123
---
We evaluate task-speciﬁc ﬁnetuning against a general instruction-tuned
baseline. Chain-of-thought prompting yields large gains on GSM8K, and the
eﬀect is most pronounced for the largest model. Removing chain-of-thought
data from the ﬁnetuning mixture degrades reasoning on held-out tasks,
which we attribute to the loss of intermediate supervision. The router
assigns consecutive tokens to the same expert far above chance. A more
eﬃcient variant reaches a diﬀerent trade-oﬀ at the same ﬂop budget.
"""

OTHER_PAPER = """---
sha256: def456
---
We introduce a state space model with selective scan. The recurrence is
linear in sequence length and the hidden state is materialised in SRAM.
Throughput improves over an attention baseline at long context. Training
uses a standard cross-entropy objective on a large text corpus.
"""

PAPER_NAME = "20260101-000000-local-flan.pdf.extracted.md"
OTHER_NAME = "20260101-000001-local-mamba.pdf.extracted.md"


def _cite(path):
    return f"(vault:{path})"


class LigatureNormalisation(unittest.TestCase):

    def test_expands_the_affected_set(self):
        self.assertEqual(
            naming.normalize_ligatures("task-speciﬁc eﬃcient diﬀerent ﬂow ﬄuent"),
            "task-specific efficient different flow ffluent",
        )

    def test_idempotent(self):
        once = naming.normalize_ligatures("eﬀect")
        self.assertEqual(once, naming.normalize_ligatures(once))

    def test_does_not_flatten_superscripts_or_fractions(self):
        """Why a targeted table and not unicodedata NFKC: NFKC turns `10²`
        into `102` and `½` into `1⁄2`, silently corrupting scientific prose."""
        self.assertEqual(naming.normalize_ligatures("10² ½ α"), "10² ½ α")

    def test_ascii_query_reaches_ligature_text_after_index(self):
        with workspace({PAPER_NAME: PAPER}):
            conn = sqlite3.connect("vault/vault.db")
            for term in ("specific", "efficient", "different", "finetuning",
                          "effect", "flop"):
                hits = conn.execute(
                    "SELECT count(*) FROM sources WHERE sources MATCH ?",
                    (term,)).fetchone()[0]
                self.assertEqual(hits, 1, f"ASCII query {term!r} found nothing")
            # And the raw ligature form is gone from the index entirely.
            self.assertEqual(
                conn.execute("SELECT count(*) FROM sources WHERE sources MATCH ?",
                             ("speciﬁc",)).fetchone()[0], 0)
            conn.close()

    def test_claim_words_survive_ligatures_in_wiki_prose(self):
        """A curator copying the paper's own wording may carry the ligature
        into the page; `[a-zA-Z]{4,}` would otherwise split it."""
        words = score_diff._claim_words("Removing CoT degrades task-speciﬁc reasoning")
        self.assertIn("specific", words)


class CitationRelevance(unittest.TestCase):

    def test_grounded_compressed_claim_is_accepted(self):
        """The implicit-AND regression: one long ultra-compressed line whose
        claims are all in the source, plus the curator's own analytical
        vocabulary. The AND form fails on the analytical words alone."""
        line = ("**CoT data is load-bearing.** Removing chain-of-thought data "
                "from the finetuning mixture degrades reasoning on held-out "
                "tasks, which the authors attribute to losing intermediate "
                "supervision; interpretation here treats this as evidence "
                "that instruction tuning propagates whichever reasoning "
                "format the mixture contains " + _cite(PAPER_NAME))
        with workspace({PAPER_NAME: PAPER, OTHER_NAME: OTHER_PAPER}):
            db = Path("vault/vault.db")
            self.assertEqual(
                score_diff.verify_new_citations("", line, db), [],
                "well-grounded compressed claim was rejected")
            # Confirm the old implementation really did fail this line, so
            # the test can't quietly stop testing anything.
            conn = sqlite3.connect(str(db))
            and_hits = conn.execute(
                "SELECT count(*) FROM sources WHERE path = ? AND sources MATCH ?",
                (PAPER_NAME,
                 score_diff._sanitize_fts(score_diff._claim_words(line)))
            ).fetchone()[0]
            conn.close()
            self.assertEqual(and_hits, 0,
                             "fixture no longer reproduces the AND failure")

    def test_spurious_citation_still_rejected(self):
        """Cite the state-space paper for the chain-of-thought finding."""
        line = ("Removing chain-of-thought data from the finetuning mixture "
                "degrades reasoning on held-out tasks, and the router assigns "
                "consecutive tokens to the same expert " + _cite(OTHER_NAME))
        with workspace({PAPER_NAME: PAPER, OTHER_NAME: OTHER_PAPER}):
            suspects = score_diff.verify_new_citations(
                "", line, Path("vault/vault.db"))
            self.assertEqual(len(suspects), 1)
            self.assertEqual(suspects[0]["reason"], "low-claim-coverage")
            self.assertLess(suspects[0]["coverage"],
                            score_diff.CITATION_COVERAGE_FLOOR)

    def test_unindexed_source_reported_distinctly(self):
        """A citation path absent from the index is a broken path, not an
        unsupported claim — and it can never verify, so saying `suspect
        citation` sends the curator to rewrite prose that was fine."""
        line = ("Chain-of-thought prompting yields large gains on GSM8K "
                + _cite("gu-2023-mamba.md"))
        with workspace({PAPER_NAME: PAPER}):
            suspects = score_diff.verify_new_citations(
                "", line, Path("vault/vault.db"))
            self.assertEqual(len(suspects), 1)
            self.assertEqual(suspects[0]["reason"], "source-not-indexed")

    def test_unchanged_citations_are_not_rechecked(self):
        line = "Chain-of-thought yields gains on GSM8K " + _cite(PAPER_NAME)
        with workspace({PAPER_NAME: PAPER}):
            self.assertEqual(
                score_diff.verify_new_citations(line, line,
                                                 Path("vault/vault.db")), [])

    def test_no_probeable_terms_fails_open(self):
        """A tiny vault collapses the df ceiling to 1 document; the check
        must skip rather than reject everything."""
        with workspace({PAPER_NAME: PAPER}):
            line = "The model is trained " + _cite(PAPER_NAME)
            out = score_diff.verify_new_citations("", line,
                                                  Path("vault/vault.db"))
            self.assertEqual([s for s in out
                              if s.get("reason") == "low-claim-coverage"], [])

    def test_missing_db_skips_check(self):
        self.assertEqual(
            score_diff.verify_new_citations("", "x " + _cite(PAPER_NAME),
                                             Path("/nonexistent/vault.db")), [])


class BloatCeiling(unittest.TestCase):

    @staticmethod
    def _m(tokens, citations, wikilinks=2):
        return {"tokens": tokens, "citations": citations, "wikilinks": wikilinks}

    def test_stub_may_reach_normal_page_length(self):
        """Source stubs land at 31-62 body tokens; finished concept pages sit
        at 160-230. The flat 1.5× cap made that expansion impossible."""
        ok, why = score_diff.verdict(self._m(56, 1), self._m(200, 9))
        self.assertTrue(ok, why)

    def test_stub_expansion_still_has_a_ceiling(self):
        ok, why = score_diff.verdict(self._m(56, 1), self._m(900, 9))
        self.assertFalse(ok)
        self.assertIn("stub-expansion", why)

    def test_citation_backed_growth_allowed_on_normal_page(self):
        ok, why = score_diff.verdict(self._m(200, 5), self._m(380, 10))
        self.assertTrue(ok, why)

    def test_padding_without_citations_still_rejected(self):
        """The failure mode the cap exists for: prose grows, evidence doesn't."""
        ok, why = score_diff.verdict(self._m(200, 5), self._m(320, 5))
        self.assertFalse(ok)
        self.assertIn("bloat", why)

    def test_citation_loss_still_unconditional(self):
        ok, why = score_diff.verdict(self._m(200, 5), self._m(210, 4))
        self.assertFalse(ok)
        self.assertIn("citation loss", why)

    def test_explicit_bloat_mult_is_never_lowered(self):
        """restyle passes --bloat-mult 2.0; the new allowances must only
        ever raise the ceiling."""
        ok, _ = score_diff.verdict(self._m(500, 5), self._m(990, 5),
                                    bloat_mult=2.0)
        self.assertTrue(ok)


class UnindexedCitationScan(unittest.TestCase):

    def _wiki(self, root, pages: dict):
        for rel, text in pages.items():
            p = root / "wiki" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")

    def test_flags_unindexed_and_records_citing_pages(self):
        with workspace({PAPER_NAME: PAPER}) as root:
            self._wiki(root, {
                "concepts/cot.md": ("---\ntype: concept\n---\nClaim "
                                     + _cite("wei-2022-chain-of-thought.md")),
                "concepts/moe.md": ("---\ntype: concept\n---\nClaim "
                                     + _cite("wei-2022-chain-of-thought.md")),
            })
            out = sweep.scan_unindexed_citations(root / "wiki")
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["vault_path"], "wei-2022-chain-of-thought.md")
            self.assertFalse(out[0]["exists_on_disk"])
            self.assertEqual(len(out[0]["citing_pages"]), 2)

    def test_indexed_citation_not_flagged(self):
        with workspace({PAPER_NAME: PAPER}) as root:
            self._wiki(root, {"concepts/cot.md":
                              "---\ntype: concept\n---\nClaim " + _cite(PAPER_NAME)})
            self.assertEqual(sweep.scan_unindexed_citations(root / "wiki"), [])

    def test_tolerates_vault_double_prefix(self):
        with workspace({PAPER_NAME: PAPER}) as root:
            self._wiki(root, {"concepts/cot.md":
                              "---\ntype: concept\n---\nClaim "
                              + _cite(f"vault/{PAPER_NAME}")})
            self.assertEqual(sweep.scan_unindexed_citations(root / "wiki"), [])

    def test_ignores_schema_placeholder(self):
        """The notes/todos scaffolding carries a literal `(vault:path)`
        inside a `<...>` placeholder showing the citation shape."""
        with workspace({PAPER_NAME: PAPER}) as root:
            self._wiki(root, {"notes.md":
                              "---\ntype: note\n---\n"
                              "<body with [[wikilinks]] and (vault:path) citations>"})
            self.assertEqual(sweep.scan_unindexed_citations(root / "wiki"), [])

    def test_distinguishes_on_disk_but_unindexed(self):
        """The recoverable case: the file is really there, it just isn't an
        indexed FTS5 entry — one `vault_index.py --rebuild` fixes it."""
        with workspace({PAPER_NAME: PAPER}) as root:
            (root / "vault" / "wiki-attention.md").write_text("raw drop file")
            self._wiki(root, {"concepts/attn.md":
                              "---\ntype: concept\n---\nClaim "
                              + _cite("wiki-attention.md")})
            out = sweep.scan_unindexed_citations(root / "wiki")
            self.assertEqual(len(out), 1)
            self.assertTrue(out[0]["exists_on_disk"])


if __name__ == "__main__":
    unittest.main()
