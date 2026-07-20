"""Tests for okf_export.py — CE wiki → Open Knowledge Format bundle.

A hand-built tempdir wiki (no setup.sh, no network, no kuzu, no embeddings)
is projected into an OKF bundle; every assertion reads the emitted bundle
files or the JSON manifest on stdout — the same deterministic surface an OKF
consumer sees. Mirrors the fixture/subprocess style of test_entity_gate.py.

Run:  python3 -m unittest discover tests
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "curiosity-engine" / "scripts"
EXPORT = SCRIPTS / "okf_export.py"

ASPIRIN = """---
title: "[ent] Aspirin"
type: entity
entity_class: chemical
iri: ce:chemical:fixture:aspirin
same_as: [pubchem:CID2244, wikidata:Q18216]
aliases: [acetylsalicylic acid, ASA]
created: 2026-01-02
updated: 2026-03-04
sources: [merck-index.extracted.md]
table:
  columns:
    - name: ic50
      type: real
      units: nM
      constraint: ">0"
---
Aspirin is an [[analgesic]] and antiplatelet drug. (vault:merck-index.extracted.md)
It inhibits [[cyclooxygenase]] irreversibly.
See the [[nonexistent-page|missing note]] for context.
"""

ANALGESIC = """---
title: "[con] Analgesic"
type: concept
created: 2026-01-05
updated: 2026-01-05
---
An analgesic relieves pain. [[Aspirin]] is a common example.
"""

SOURCE = """---
title: "[src] Merck Index"
type: source
source_url: https://example.com/merck-index
sources: [merck-index.extracted.md]
created: 2026-01-01
updated: 2026-01-01
---
Reference monograph on aspirin. (vault:merck-index.extracted.md)
"""

# A page whose derived description contains a colon — exercises YAML quoting.
COLON = """---
title: "[con] Ratio Rule"
type: concept
created: 2026-02-02
updated: 2026-02-02
---
The rule: always cite the source before the claim.
"""


class OkfExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = Path(tempfile.mkdtemp(prefix="ce-okf-test-"))
        wiki = cls.ws / "wiki"
        (wiki / "entities").mkdir(parents=True)
        (wiki / "concepts").mkdir()
        (wiki / "sources").mkdir()
        (wiki / "entities" / "aspirin.md").write_text(ASPIRIN)
        (wiki / "concepts" / "analgesic.md").write_text(ANALGESIC)
        (wiki / "concepts" / "ratio-rule.md").write_text(COLON)
        (wiki / "sources" / "merck-index.md").write_text(SOURCE)
        cls.bundle = cls.ws / "bundle"
        cls.manifest = json.loads(cls._build(cls.bundle))

    @classmethod
    def _build(cls, out, *extra):
        proc = subprocess.run(
            [sys.executable, str(EXPORT), "build", str(cls.ws / "wiki"),
             "--output-dir", str(out), "--date", "2026-07-19", *extra],
            capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError(f"export failed rc={proc.returncode}\n{proc.stderr}")
        return proc.stdout

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.ws, ignore_errors=True)

    def read(self, rel):
        return (self.bundle / rel).read_text()

    def front(self, rel):
        """Return the raw frontmatter block text of an emitted concept."""
        text = self.read(rel)
        self.assertTrue(text.startswith("---\n"), rel)
        end = text.find("\n---", 3)
        self.assertNotEqual(end, -1, rel)
        return text[4:end]

    # -- manifest -----------------------------------------------------

    def test_manifest_counts(self):
        m = self.manifest
        self.assertTrue(m["ok"])
        self.assertEqual(m["concepts"], 4)
        self.assertGreaterEqual(m["links"], 2)   # aspirin↔analgesic (both directions)
        self.assertGreaterEqual(m["broken_links"], 2)  # cyclooxygenase, nonexistent-page

    # -- OKF conformance ---------------------------------------------

    def test_every_concept_has_nonempty_type(self):
        for rel in ("entities/aspirin.md", "concepts/analgesic.md",
                    "sources/merck-index.md", "concepts/ratio-rule.md"):
            fm = self.front(rel)
            m = re.search(r"^type:\s*(\S.*)$", fm, re.MULTILINE)
            self.assertIsNotNone(m, rel)
            self.assertTrue(m.group(1).strip(), rel)

    def test_root_index_declares_version(self):
        root = self.read("index.md")
        self.assertIn('okf_version: "0.1"', root)

    def test_per_directory_indexes_exist(self):
        for sub in ("entities", "concepts", "sources"):
            idx = self.bundle / sub / "index.md"
            self.assertTrue(idx.is_file(), sub)
            self.assertIn(f"# {sub}", idx.read_text())

    def test_log_is_okf_conformant(self):
        log = self.read("log.md")
        self.assertIn("## 2026-07-19", log)

    # -- field mapping ------------------------------------------------

    def test_title_prefix_stripped(self):
        fm = self.front("entities/aspirin.md")
        self.assertIn("title: Aspirin\n", fm + "\n")
        self.assertNotIn("[ent]", fm)

    def test_timestamp_from_updated(self):
        self.assertIn("timestamp: 2026-03-04", self.front("entities/aspirin.md"))

    def test_same_as_becomes_resource(self):
        fm = self.front("entities/aspirin.md")
        # pubchem CID2244 → resolvable compound URL
        self.assertIn("resource: https://pubchem.ncbi.nlm.nih.gov/compound/2244", fm)

    def test_source_url_becomes_resource(self):
        self.assertIn("resource: https://example.com/merck-index",
                      self.front("sources/merck-index.md"))

    def test_iri_never_in_resource_only_in_extension(self):
        fm = self.front("entities/aspirin.md")
        resource = re.search(r"^resource:\s*(.*)$", fm, re.MULTILINE)
        self.assertNotIn("ce:chemical:fixture:aspirin", resource.group(1))
        self.assertIn("x_ce_iri: ce:chemical:fixture:aspirin", fm)

    def test_extension_keys_preserved(self):
        fm = self.front("entities/aspirin.md")
        self.assertIn("x_ce_type: entity", fm)
        self.assertIn("x_ce_entity_class: chemical", fm)
        self.assertIn("pubchem:CID2244", fm)  # x_ce_same_as

    def test_table_shape_roundtrips(self):
        fm = self.front("entities/aspirin.md")
        self.assertIn("x_ce_table: |", fm)
        self.assertIn("name: ic50", fm)
        self.assertIn('constraint: ">0"', fm)

    def test_tags_from_aliases(self):
        fm = self.front("entities/aspirin.md")
        self.assertIn("acetylsalicylic acid", fm)
        self.assertIn("ASA", fm)

    def test_description_derived_and_quoted(self):
        # ratio-rule's first sentence contains a colon → must be YAML-quoted.
        fm = self.front("concepts/ratio-rule.md")
        m = re.search(r"^description:\s*(.*)$", fm, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertTrue(m.group(1).startswith('"'), m.group(1))

    # -- body transforms ---------------------------------------------

    def test_wikilink_becomes_bundle_absolute_link(self):
        # no alias → display text comes from the target page's stripped title
        body = self.read("entities/aspirin.md")
        self.assertIn("[Analgesic](/concepts/analgesic.md)", body)

    def test_pipe_alias_display_preserved(self):
        # unresolved [[nonexistent-page|missing note]] → plain display text
        body = self.read("entities/aspirin.md")
        self.assertIn("missing note", body)
        self.assertNotIn("nonexistent-page.md", body)

    def test_unresolved_wikilink_not_fabricated(self):
        body = self.read("entities/aspirin.md")
        self.assertNotIn("(/concepts/cyclooxygenase.md)", body)

    def test_citations_section_and_extension(self):
        body = self.read("entities/aspirin.md")
        self.assertIn("# Citations", body)
        self.assertIn("x_ce_citations", self.front("entities/aspirin.md"))
        # OKF §8 numbered form (not bullets)
        self.assertIsNotNone(re.search(r"^\[1\] ", body, re.MULTILINE))
        self.assertIsNone(re.search(r"^-\s+\[", body, re.MULTILINE))
        # inline (vault:...) marker stripped from prose
        prose = body.split("# Citations")[0]
        self.assertNotIn("(vault:merck-index.extracted.md)", prose)

    def test_citation_links_exported_source(self):
        # merck-index source stub is exported, so the citation should link it
        body = self.read("entities/aspirin.md")
        self.assertIn("[1] [Merck Index](/sources/merck-index.md)", body)

    # -- options / determinism ---------------------------------------

    def test_no_sources_option(self):
        alt = self.ws / "bundle-nosrc"
        self._build(alt, "--no-sources")
        self.assertFalse((alt / "sources" / "merck-index.md").exists())
        # citation degrades to a numbered raw vault ref when the source
        # isn't exported
        body = (alt / "entities" / "aspirin.md").read_text()
        self.assertIn("[1] (vault:merck-index.extracted.md)", body)

    def test_deterministic(self):
        a = self.ws / "det-a"
        b = self.ws / "det-b"
        self._build(a)
        self._build(b)
        for rel in ("entities/aspirin.md", "index.md", "concepts/index.md"):
            self.assertEqual((a / rel).read_bytes(), (b / rel).read_bytes(), rel)

    def test_refuses_output_dir_inside_wiki(self):
        # cmd_build rmtree's the output dir; pointing it at the wiki would
        # destroy the source of truth. Exit 2 + leave wiki pages intact.
        wiki = self.ws / "wiki"
        target = wiki / "entities"
        before = (wiki / "entities" / "aspirin.md").read_text()
        proc = subprocess.run(
            [sys.executable, str(EXPORT), "build", str(wiki),
             "--output-dir", str(target), "--date", "2026-07-19"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        err = json.loads(proc.stdout)
        self.assertIn("inside the wiki", err["error"])
        self.assertEqual((wiki / "entities" / "aspirin.md").read_text(), before)

    def test_refuses_output_dir_is_wiki(self):
        wiki = self.ws / "wiki"
        proc = subprocess.run(
            [sys.executable, str(EXPORT), "build", str(wiki),
             "--output-dir", str(wiki), "--date", "2026-07-19"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("wiki itself", json.loads(proc.stdout)["error"])


if __name__ == "__main__":
    unittest.main()
