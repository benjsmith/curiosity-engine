"""Procedure / execution thin types + procedure_maturity ranker."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "curiosity-engine" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import naming  # noqa: E402
import procedure_maturity  # noqa: E402
import score_diff  # noqa: E402


CHANGE_CONTROL = '''---
title: "[proc] Change control"
type: procedure
created: 2026-09-21
updated: 2026-09-21
sources:
  - example-change-control.extracted.md
status: needs-tightening
applies_to: [manufacturing-compliance]
steps_attested: false
---

Change control is a named governance how-to. Threads describe it as
scattered rather than a finished SOP. (vault:example-change-control.extracted.md)

## What the vault attests (not a full SOP)

- Document what changed and who approved it. (vault:example-change-control.extracted.md)

No ordered step list is inventable — keep steps_attested false.

## Agents

[[cathy-paganini|Cathy Paganini]] and [[elena-simpson|Elena Simpson]].
Dated handoff reviews: [[exec-change-control-handoff-review-2024-02-15]].
'''

SCALE_UP = '''---
title: "[proc] Manufacturing scale-up"
type: procedure
created: 2026-09-21
updated: 2026-09-21
sources:
  - example-scale-up.extracted.md
status: in-use
applies_to: [manufacturing-process-development]
steps_attested: true
---

Manufacturing scale-up moves a process toward production volumes.
(vault:example-scale-up.extracted.md)

## Steps (attested fragments only)

1. Optimize batch sizes for the target volume regime. (vault:example-scale-up.extracted.md)
2. Refine reactor configurations for the scaled process. (vault:example-scale-up.extracted.md)
3. Stress-test at higher volumes before committing to full production. (vault:example-scale-up.extracted.md)

## Agents

[[leila-williams|Leila Williams]] reviews readiness.
Dated timeline syncs: [[exec-scale-up-timeline-sync-2024-02-15]].
'''

RETURNS = '''---
title: "[proc] Returns processing"
type: procedure
created: 2026-09-21
updated: 2026-09-21
sources:
  - example-returns.extracted.md
status: in-use
applies_to: [distribution-logistics]
steps_attested: partial
---

Returns processing is the distribution how-to for intake and disposition.
(vault:example-returns.extracted.md)

## Steps (partial)

1. Initial intake of the return. (vault:example-returns.extracted.md)
2. Product disposition. (vault:example-returns.extracted.md)

[[emily-moran|Emily Moran]] flags bottlenecks.
'''


class TestNamingPrefixes(unittest.TestCase):
    def test_type_prefix_and_stem(self):
        self.assertEqual(naming.TYPE_PREFIX["procedure"], "[proc]")
        self.assertEqual(naming.TYPE_PREFIX["execution"], "[exec]")
        self.assertIn("procedure", naming.FRONTMATTER_TYPES)
        self.assertIn("execution", naming.FRONTMATTER_TYPES)
        self.assertEqual(naming.STEM_PREFIX["execution"], "exec-")
        self.assertNotIn("procedure", naming.STEM_PREFIX)
        self.assertEqual(
            naming.prefixed_stem("execution", "scale-up-sync-2024-02-15"),
            "exec-scale-up-sync-2024-02-15",
        )
        self.assertEqual(
            naming.prefixed_stem("procedure", "change-control"),
            "change-control",
        )
        # Idempotent on already-prefixed stems
        self.assertEqual(
            naming.prefixed_stem("execution", "exec-already"),
            "exec-already",
        )

    def test_allowed_fm_keys(self):
        for key in ("status", "applies_to", "steps_attested"):
            self.assertIn(key, naming.ALLOWED_FM_KEYS)

    def test_frontmatter_roundtrip_optional_keys(self):
        text = (
            '---\ntitle: "[proc] X"\ntype: procedure\n'
            "status: needs-tightening\nsteps_attested: false\n"
            "applies_to: [a, b]\n---\n\nBody.\n"
        )
        fm, body = naming.read_frontmatter(text)
        self.assertEqual(fm["type"], "procedure")
        self.assertEqual(fm["status"], "needs-tightening")
        self.assertEqual(fm["steps_attested"], "false")
        self.assertEqual(fm["applies_to"], ["a", "b"])
        self.assertIn("Body", body)


class TestScoreDiffFloors(unittest.TestCase):
    def test_procedure_and_execution_floors(self):
        proc = Path("wiki/procedures/change-control.md")
        exec_p = Path("wiki/executions/exec-x.md")
        self.assertEqual(
            score_diff._floors_for(proc),
            {"citations": 1, "wikilinks": 1, "words": 80},
        )
        self.assertEqual(
            score_diff._floors_for(exec_p),
            {"citations": 1, "wikilinks": 1, "words": 50},
        )


class TestProcedureMaturity(unittest.TestCase):
    def _wiki(self, td: str) -> Path:
        wiki = Path(td) / "wiki"
        (wiki / "procedures").mkdir(parents=True)
        (wiki / "procedures" / "change-control.md").write_text(
            CHANGE_CONTROL, encoding="utf-8")
        (wiki / "procedures" / "manufacturing-scale-up.md").write_text(
            SCALE_UP, encoding="utf-8")
        (wiki / "procedures" / "returns-processing.md").write_text(
            RETURNS, encoding="utf-8")
        return wiki

    def test_immature_ranks_above_mature(self):
        with tempfile.TemporaryDirectory() as td:
            wiki = self._wiki(td)
            out = procedure_maturity.rank_procedures(wiki)
            by_stem = {r["stem"]: r for r in out["procedures"]}
            cc = by_stem["change-control"]
            su = by_stem["manufacturing-scale-up"]
            self.assertFalse(cc["steps_attested"])
            self.assertTrue(su["steps_attested"])
            self.assertEqual(su["attested_step_count"], 3)
            self.assertGreater(cc["spend_priority"], su["spend_priority"])
            self.assertLess(cc["maturity_score"], su["maturity_score"])
            # Highest spend first
            self.assertEqual(out["ranked_for_spend"][0], "change-control")
            self.assertIn("manufacturing-scale-up", out["ranked_for_spend"])

    def test_cli_json(self):
        with tempfile.TemporaryDirectory() as td:
            wiki = self._wiki(td)
            rc = procedure_maturity.main([str(wiki), "--pretty", "--limit", "2"])
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
