"""Regression tests for the entity-resolution abstention gate (v0.8.3).

The false-bridging bug this pins down: a query naming a look-alike entity
that does not exist ("Project Onyxx" when only "Project Onyx" is curated)
used to retrieve the real entity's documents by lexical/embedding
proximity, and the answer path then reported the real entity's fact as
the look-alike's. The gate must abstain on look-alikes while leaving
real-entity, alias, and synonym queries untouched.

Fixture: a hand-built workspace (no setup.sh, no network, no embeddings,
no kuzu required) with curated entities "Project Onyx" (fact
ORBITAL-PELICAN-7741, alias "Onyx Initiative", same_as codename:onx-1)
and "Project Marlin" (fact MARLIN-AQUAMARINE-2214, pipe-alias "Marlin
Programme"), plus an uncurated vault-only source naming "Project
Falconet". Everything is asserted on script stdout — the same
deterministic surface the answering agent consumes.

Run:  python3 -m unittest discover tests
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "curiosity-engine" / "scripts"

ONYX_FACT = "ORBITAL-PELICAN-7741"
MARLIN_FACT = "MARLIN-AQUAMARINE-2214"

ONYX_PAGE = """---
title: "[ent] Project Onyx"
type: entity
entity_class: org
iri: ce:org:fixture:project-onyx
same_as: [codename:onx-1]
aliases: [Onyx Initiative]
sources: [onyx-brief.extracted.md]
---
Project Onyx launch fact: {fact}. (vault:onyx-brief.extracted.md)
Linked programme: [[project-marlin]].
""".format(fact=ONYX_FACT)

MARLIN_PAGE = """---
title: "[ent] Project Marlin"
type: entity
entity_class: org
iri: ce:org:fixture:project-marlin
sources: [marlin-brief.extracted.md]
---
Project Marlin launch fact: {fact}. (vault:marlin-brief.extracted.md)
""".format(fact=MARLIN_FACT)

PROGRAMMES_PAGE = """---
title: "[con] Deep Sea Programmes"
type: concept
---
Umbrella note covering [[project-onyx]] and the
[[project-marlin|Marlin Programme]].
"""

ONYX_SOURCE = """---
source_path: onyx-brief.pdf
extraction: full
---
Project Onyx launch fact is {fact}.
Project Onyx is an orbital logistics effort.
""".format(fact=ONYX_FACT)

MARLIN_SOURCE = """---
source_path: marlin-brief.pdf
extraction: full
---
Project Marlin launch fact is {fact}.
Project Marlin is a deep-sea survey effort.
""".format(fact=MARLIN_FACT)

FALCONET_SOURCE = """---
source_path: falconet-notes.pdf
extraction: full
---
Project Falconet is an early-stage idea with no wiki page yet.
"""


def run(args, cwd):
    proc = subprocess.run([sys.executable, *args], cwd=cwd,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(
            f"{args} failed rc={proc.returncode}\nstderr: {proc.stderr}")
    return proc.stdout


class EntityGateTest(unittest.TestCase):
    """One shared fixture workspace; every test is a read-only query."""

    @classmethod
    def setUpClass(cls):
        cls.ws = Path(tempfile.mkdtemp(prefix="ce-gate-test-"))
        wiki = cls.ws / "wiki"
        (wiki / "entities").mkdir(parents=True)
        (wiki / "concepts").mkdir()
        (wiki / "entities" / "project-onyx.md").write_text(ONYX_PAGE)
        (wiki / "entities" / "project-marlin.md").write_text(MARLIN_PAGE)
        (wiki / "concepts" / "deep-sea-programmes.md").write_text(PROGRAMMES_PAGE)
        vault = cls.ws / "vault"
        vault.mkdir()
        (vault / "onyx-brief.extracted.md").write_text(ONYX_SOURCE)
        (vault / "marlin-brief.extracted.md").write_text(MARLIN_SOURCE)
        (vault / "falconet-notes.extracted.md").write_text(FALCONET_SOURCE)
        (cls.ws / ".curator").mkdir()
        run([str(SCRIPTS / "vault_index.py"), "--rebuild"], cwd=cls.ws)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.ws, ignore_errors=True)

    # -- helpers ------------------------------------------------------

    def gate(self, query):
        out = run([str(SCRIPTS / "entity_gate.py"), "gate", query],
                  cwd=self.ws)
        return json.loads(out)

    def retrieve(self, query):
        out = run([str(SCRIPTS / "graph.py"), "retrieve", "wiki", query],
                  cwd=self.ws)
        return out, json.loads(out)

    def classify(self, query):
        out = run([str(SCRIPTS / "query_router.py"), "classify", query],
                  cwd=self.ws)
        return out, json.loads(out)

    def single_mention(self, verdict):
        self.assertEqual(len(verdict["mentions"]), 1, verdict)
        return verdict["mentions"][0]

    # -- resolution: real names and aliases must keep answering -------

    def test_canonical_name_resolves(self):
        verdict = self.gate(f"What is Project Onyx's launch fact?")
        self.assertEqual(verdict["action"], "proceed")
        m = self.single_mention(verdict)
        self.assertEqual(m["status"], "resolved")
        self.assertEqual(m["page"], "entities/project-onyx.md")

    def test_frontmatter_alias_resolves(self):
        verdict = self.gate("What do we know about the Onyx Initiative?")
        self.assertEqual(verdict["action"], "proceed")
        m = self.single_mention(verdict)
        self.assertEqual(m["status"], "resolved")
        self.assertEqual(m["page"], "entities/project-onyx.md")

    def test_same_as_id_resolves(self):
        verdict = self.gate("Any updates on ONX-1?")
        self.assertEqual(verdict["action"], "proceed")
        m = self.single_mention(verdict)
        self.assertEqual(m["status"], "resolved")
        self.assertEqual(m["page"], "entities/project-onyx.md")

    def test_pipe_alias_resolves(self):
        verdict = self.gate("Tell me about the Marlin Programme")
        self.assertEqual(verdict["action"], "proceed")
        m = self.single_mention(verdict)
        self.assertEqual(m["status"], "resolved")
        self.assertEqual(m["page"], "entities/project-marlin.md")

    def test_coordinated_mentions_both_resolve(self):
        verdict = self.gate("Compare Project Onyx and Project Marlin")
        self.assertEqual(verdict["action"], "proceed")
        pages = {m["page"] for m in verdict["mentions"]}
        self.assertEqual(pages, {"entities/project-onyx.md",
                                 "entities/project-marlin.md"})

    # -- abstention: look-alikes must not bridge ----------------------

    def test_lookalike_onyxx_abstains(self):
        verdict = self.gate("What is Project Onyxx's launch fact?")
        self.assertEqual(verdict["action"], "abstain")
        m = self.single_mention(verdict)
        self.assertEqual(m["status"], "abstain")
        self.assertEqual(m["look_alike"]["page"], "entities/project-onyx.md")

    def test_lookalike_marlon_abstains(self):
        verdict = self.gate("What is Project Marlon's launch fact?")
        self.assertEqual(verdict["action"], "abstain")
        m = self.single_mention(verdict)
        self.assertEqual(m["status"], "abstain")
        self.assertEqual(m["look_alike"]["page"], "entities/project-marlin.md")

    # -- casing: chat-style lowercase must not reopen false-bridging --

    def test_lowercase_lookalike_onyxx_abstains(self):
        verdict = self.gate("what is project onyxx's launch fact?")
        self.assertEqual(verdict["action"], "abstain", verdict)
        m = self.single_mention(verdict)
        self.assertEqual(m["status"], "abstain")
        self.assertEqual(m["look_alike"]["page"], "entities/project-onyx.md")

    def test_lowercase_canonical_resolves(self):
        verdict = self.gate("what is project onyx's launch fact?")
        self.assertEqual(verdict["action"], "proceed", verdict)
        m = self.single_mention(verdict)
        self.assertEqual(m["status"], "resolved")
        self.assertEqual(m["page"], "entities/project-onyx.md")

    def test_lowercase_alias_resolves(self):
        verdict = self.gate("what do we know about the onyx initiative?")
        self.assertEqual(verdict["action"], "proceed", verdict)
        m = self.single_mention(verdict)
        self.assertEqual(m["status"], "resolved")
        self.assertEqual(m["page"], "entities/project-onyx.md")

    def test_lowercase_retrieve_lookalike_returns_no_context(self):
        raw, out = self.retrieve("what is project onyxx's launch fact?")
        self.assertTrue(out.get("abstain"), out)
        self.assertEqual(out["pages"], [])
        self.assertEqual(out["vault"], [])
        self.assertNotIn(ONYX_FACT, raw)

    # -- retrieve: enforcement at the context-fusion point ------------

    def test_retrieve_abstains_and_returns_no_context(self):
        raw, out = self.retrieve("What is Project Onyxx's launch fact?")
        self.assertTrue(out.get("abstain"), out)
        self.assertEqual(out["pages"], [])
        self.assertEqual(out["vault"], [])
        self.assertNotIn(ONYX_FACT, raw)
        self.assertNotIn("onyx-brief", raw)
        self.assertEqual(out["entity_gate"]["action"], "abstain")

    def test_retrieve_marlon_abstains_and_returns_no_context(self):
        raw, out = self.retrieve("What is Project Marlon's launch fact?")
        self.assertTrue(out.get("abstain"), out)
        self.assertEqual(out["pages"], [])
        self.assertNotIn(MARLIN_FACT, raw)

    def test_retrieve_canonical_name_unchanged(self):
        raw, out = self.retrieve("What is Project Onyx's launch fact?")
        self.assertNotIn("abstain", out)
        pages = [p["page"] for p in out["pages"]]
        self.assertIn("entities/project-onyx.md", pages)
        self.assertEqual(out["seeds"][0], "entities/project-onyx.md")
        self.assertIn(ONYX_FACT, raw)  # blend vault stream carries the fact

    def test_retrieve_alias_reaches_curated_page(self):
        _raw, out = self.retrieve("What do we know about the Onyx Initiative?")
        self.assertNotIn("abstain", out)
        self.assertEqual(out["seeds"][0], "entities/project-onyx.md")

    # -- classify: advisory verdict on the synthesis route ------------

    def test_classify_lookalike_says_abstain(self):
        raw, out = self.classify("What is Project Onyxx's launch fact?")
        self.assertEqual(out["route"], "synthesis")
        self.assertEqual(out["entity_gate"]["action"], "abstain")
        self.assertTrue(out["next"].startswith("ABSTAIN"), out["next"])
        self.assertNotIn(ONYX_FACT, raw)

    def test_classify_canonical_proceeds(self):
        _raw, out = self.classify("What is Project Onyx's launch fact?")
        self.assertEqual(out["route"], "synthesis")
        self.assertEqual(out["entity_gate"]["action"], "proceed")

    # -- fail-open paths: the gate must not over-fire ------------------

    def test_uncurated_vault_only_name_proceeds(self):
        verdict = self.gate("What is Project Falconet?")
        self.assertEqual(verdict["action"], "proceed")
        m = self.single_mention(verdict)
        self.assertEqual(m["status"], "uncurated")
        self.assertGreaterEqual(m["vault_mentions"], 1)

    def test_uncurated_retrieve_excludes_proximity_wiki(self):
        """Option C: vault-only Falconet must not seed Project Onyx."""
        raw, out = self.retrieve("What is Project Falconet?")
        self.assertEqual(out["entity_gate"]["action"], "proceed")
        self.assertTrue(out.get("verbatim_filter"), out)
        pages = [p["page"] for p in out.get("pages", [])]
        self.assertNotIn("entities/project-onyx.md", pages)
        self.assertNotIn(ONYX_FACT, raw)
        # Verbatim vault evidence for Falconet is preserved.
        vault_paths = " ".join(
            str(v.get("path", "")) for v in out.get("vault", []))
        self.assertIn("falconet", vault_paths.lower())

    def test_uncurated_does_not_break_resolved_recall(self):
        raw, out = self.retrieve("What is Project Onyx's launch fact?")
        self.assertNotIn("verbatim_filter", out)
        self.assertIn(ONYX_FACT, raw)
        pages = [p["page"] for p in out.get("pages", [])]
        self.assertIn("entities/project-onyx.md", pages)

    def test_question_without_mentions_passes(self):
        verdict = self.gate("what are the main themes across everything here?")
        self.assertEqual(verdict["mentions"], [])
        self.assertEqual(verdict["action"], "proceed")

    def test_date_noise_is_not_a_mention(self):
        verdict = self.gate("What was planned for July 2026?")
        self.assertEqual(verdict["mentions"], [])
        self.assertEqual(verdict["action"], "proceed")

    def test_partial_abstain_keeps_resolved_context(self):
        raw, out = self.retrieve(
            "How do Project Onyx and Project Onyxx relate?")
        self.assertNotIn("abstain", out)
        self.assertEqual(out["entity_gate"]["action"], "partial")
        pages = [p["page"] for p in out["pages"]]
        self.assertIn("entities/project-onyx.md", pages)
        self.assertIn("Project Onyxx",
                      out["entity_gate"]["abstained_mentions"])


if __name__ == "__main__":
    unittest.main()
