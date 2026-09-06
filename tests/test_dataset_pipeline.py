"""Recovery, stable IDs, staged JSONL, explicit selectors and record search."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from test_structured_datasets import workspace, ingest, promote, call, install_schema, SCRIPTS
import dataset_recovery as recovery
import dataset_stage
import datasets
import naming
import record_index
import structured_data as sd
import sweep
import tables


def proposal(result, **extra):
    return datasets.propose({"entity_page": "wiki/entities/observations.md", "name": "observations",
        "dataset_id": "instrument-observations", "grain": "one reading",
        "membership_evidence": "same instrument export",
        "sources": [{"extraction": result["extracted"], "collection": ""}], **extra})


class Streaming(unittest.TestCase):
    def test_jsonl_streams_through_ingest_profile_promotion_and_import(self):
        with workspace():
            raw = b"\n".join(sd.literal({"id": str(i), "n": sd.Number(str(i))}).encode() for i in range(300))
            original_read = Path.read_bytes
            def no_jsonl_read(path):
                if path.suffix == ".jsonl":
                    raise AssertionError("whole JSONL read")
                return original_read(path)
            with patch.object(Path, "read_bytes", no_jsonl_read):
                r = ingest(raw, "batch.jsonl", cap=150)
                self.assertTrue(r["ok"], r)
                self.assertEqual(r["record_index"]["records"], 300)
                _, report = promote(r)
                self.assertEqual(report["created"], 1)
                p = proposal(r)
                self.assertEqual(p["profile"]["row_count"], 300)
                self.assertTrue(all(c["unique_non_null_over_full_collection"] for c in p["profile"]["columns"]))
                path, _ = install_schema(p)
                rc, imported = call(tables.cmd_import_dataset, path)
                self.assertEqual(rc, 0, imported)
                self.assertEqual(imported["inserted"], 300)
            page = next(Path("wiki/tables").glob("tab-*.md"))
            self.assertIn("is_snapshot: true", page.read_text())
            with sqlite3.connect(".curator/tables.db") as db:
                self.assertEqual(db.execute("SELECT count(*) FROM _extracted_tables").fetchone()[0], 300)

    def test_stage_profile_matches_full_values_and_cleans_up(self):
        rows = [{"/a": sd.Number("1"), "/b": False}, {"/a": None}, {"/a": sd.Number("1"), "/b": False}]
        stage = dataset_stage.Stage()
        path = stage.path
        try:
            for i, row in enumerate(rows):
                stage.add(row, {"locator": str(i)})
            self.assertEqual(stage.profile(), datasets.profile_values(rows))
            self.assertEqual(sd.table_hash(stage.headers, stage.view("rows")),
                hashlib.sha256(json.dumps([stage.headers, list(stage.view("rows"))]).encode()).hexdigest())
        finally:
            stage.close()
        self.assertFalse(path.exists())

    def test_late_invalid_line_publishes_nothing_and_cleans_stage(self):
        with workspace():
            stages = []
            real = dataset_stage.Stage
            def capture(*args, **kwargs):
                stage = real(*args, **kwargs)
                stages.append(stage.path)
                return stage
            with patch.object(dataset_stage, "Stage", capture):
                r = ingest(b'{"a":1}\n' * 300 + b'{broken}', "bad.jsonl", drop=True)
            self.assertFalse(r["ok"])
            self.assertIn("line 301", r["reason"])
            self.assertTrue(Path("vault/raw/bad.jsonl").exists())
            self.assertEqual(list(Path("vault").glob("*.extracted.md")), [])
            self.assertTrue(all(not path.exists() for path in stages))

    def test_aggregate_field_cell_byte_limits(self):
        for limits, error in (({"max_fields": 1}, "max_fields"),
                              ({"max_cells": 3}, "max_cells"),
                              ({"max_raw_bytes": 15}, "max_raw_bytes")):
            with self.subTest(limits=limits), workspace():
                a = ingest([{"a": 1}], "a.json")
                b = ingest([{"b": 2}], "b.json")
                with self.assertRaisesRegex(ValueError, error):
                    datasets.selected([{"extraction": r["extracted"], "collection": ""} for r in (a,b)], limits)

    def test_staging_disk_limit(self):
        with workspace():
            r = ingest(b'{"a":"' + b'x' * 20000 + b'"}', "wide.jsonl", max_stage_bytes=32768)
            self.assertFalse(r["ok"], r)
            self.assertIn("max_stage_bytes", r["reason"])


class SelectorsIdentity(unittest.TestCase):
    def test_public_selector_and_search_cli(self):
        with workspace():
            Path("input/api.json").write_text('{"response":{"items":[{"name":"needle"}],"meta":{"unit":"mm"}}}')
            args = [sys.executable, str(SCRIPTS / "local_ingest.py"), "--file", "input/api.json",
                    "--record-pointer", "/response/items", "--metadata-pointer", "/response/meta"]
            run = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            result = json.loads(run.stdout)["results"][0]
            self.assertEqual(result["tables_extracted"], 2)
            found = subprocess.run([sys.executable, str(SCRIPTS / "datasets.py"), "search-records", "needle"], capture_output=True, text=True)
            self.assertEqual(found.returncode, 0, found.stderr + found.stdout)
            self.assertEqual(json.loads(found.stdout)["results"][0]["source_locator"], "/response/items/0")
    def test_nested_pointer_metadata_and_empty_collection(self):
        with workspace():
            r = ingest({"response": {"meta": {"unit": "mm"}, "items": [{"id": "a"}]}},
                       record_pointer="/response/items", metadata_pointers=["/response/meta"])
            self.assertTrue(r["ok"], r)
            fm, data = sd.load_extraction(r["extracted"])
            self.assertEqual(data["tables"][1]["locators"], ["/response/items/0"])
            self.assertEqual(data["tables"][0]["rows"], [["/response/meta/unit", '"mm"']])
            _, report = promote(r)
            self.assertEqual(report["created"], 2)
            empty = sd.extract(b'{"items":[]}', "json", {"record_pointer": "/items"})
            self.assertEqual(empty["records"], 0)

    def test_pointer_escaping_and_invalid_selectors(self):
        data = sd.extract(b'{"a/b":{"~items":[{}]}}', "json", {"record_pointer": "/a~1b/~0items"})
        self.assertEqual(data["tables"][0]["locators"], ["/a~1b/~0items/0"])
        for cfg in ({"record_pointer": "/absent"}, {"record_pointer": "/~2"},
                    {"record_pointer": "/meta"}, {"record_pointer": "/items", "metadata_pointers": [""]}):
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                sd.extract(b'{"items":[{}],"meta":1}', "json", cfg)

    def test_relocation_and_preview_changes_do_not_duplicate_ids(self):
        with workspace():
            r = ingest([{"id": "a", "n": 1}], "a.json")
            p = proposal(r)
            path, plan = install_schema(p)
            self.assertEqual(call(tables.cmd_import_dataset, path)[1]["inserted"], 1)
            r2 = ingest([{"id": "a", "n": 1}], "moved.json", cap=20)
            p2 = proposal(r2, mapping=p["mapping"], technical_key="record_id")
            path, _ = install_schema(p2)
            rc, report = call(tables.cmd_import_dataset, path)
            self.assertEqual(rc, 0, report)
            self.assertEqual(report["unchanged"], 1)
            with sqlite3.connect(".curator/tables.db") as db:
                self.assertEqual(db.execute("SELECT count(*) FROM observations").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT count(*) FROM _dataset_lineage").fetchone()[0], 2)
            legacy = dict(plan)
            legacy.pop("identity_policy")
            legacy.pop("dataset_id")
            payload, origin = next(datasets.rows_for_plan(legacy))
            self.assertEqual(payload["record_id"], "rec_" + hashlib.sha256(json.dumps(origin, sort_keys=True).encode()).hexdigest()[:32])


class RecordSearch(unittest.TestCase):
    def test_failed_rebuild_preserves_existing_search_index(self):
        with workspace():
            r = ingest([{"name": "needle"}], in_place=True)
            Path("input/data.json").write_text('[{"name":"changed"}]')
            with self.assertRaisesRegex(ValueError, "hash changed"):
                record_index.rebuild()
            with sqlite3.connect(".curator/records.db") as db:
                self.assertEqual(db.execute("SELECT count(*) FROM records WHERE records MATCH 'needle'").fetchone()[0], 1)

    def test_unicode_beyond_preview_and_rebuild(self):
        with workspace():
            r = ingest([{"name": "plain"}] * 150 + [{"name": "観測天文台 café"}], cap=100)
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["record_index"]["records"], 151)
            promote(r)
            hits = record_index.search("観測天文台")
            self.assertEqual(len(hits["results"]), 1)
            self.assertEqual(hits["results"][0]["source_locator"], "/150")
            self.assertIn("table_link", hits["results"][0])
            self.assertEqual(hits["results"][0]["citation"], f"(vault:{Path(r['extracted']).name})")
            Path(".curator/records.db").unlink()
            self.assertEqual(record_index.rebuild()["records"], 151)
            self.assertEqual(len(record_index.search("café")["results"]), 1)

    def test_changed_in_place_source_is_reported_stale(self):
        with workspace():
            r = ingest([{"name": "needle"}], in_place=True)
            Path("input/data.json").write_text('[{"name":"changed"}]')
            hits = record_index.search("needle")
            self.assertEqual(hits["results"], [])
            self.assertEqual(hits["stale_sources"], [Path(r["extracted"]).name])


class Recovery(unittest.TestCase):
    def test_record_cache_publication_failure_reports_recovered_tables(self):
        with workspace():
            self.fixture()
            real = recovery.publish_database
            live_records = Path(".curator/records.db").resolve()
            def fail_live_record_cache(source, target):
                if Path(target).resolve() == live_records:
                    raise OSError("record cache unavailable")
                return real(source, target)
            with patch.object(recovery, "publish_database", fail_live_record_cache):
                result = recovery.recover(Path("wiki"))
            self.assertEqual(result["status"], "recovered")
            self.assertEqual(result["record_index"]["status"], "error")
            self.assertEqual(result["indexed_records"], 0)

    def test_public_recovery_cli_dry_run(self):
        with workspace():
            self.fixture()
            run = subprocess.run([sys.executable, str(SCRIPTS / "tables.py"), "recover", "--dry-run"], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            self.assertEqual(json.loads(run.stdout)["status"], "validated")

    def test_restore_updates_recipe_and_survives_repromotion_recovery(self):
        with workspace():
            _, page = self.fixture()
            fm, _ = naming.read_frontmatter(page.read_text())
            first = fm["correction_manifest"]
            rc, report = call(tables.cmd_restore_backup, page.stem, fm["backup_id"])
            self.assertEqual(rc, 0, report)
            restored, _ = naming.read_frontmatter(page.read_text())
            self.assertNotEqual(restored["correction_manifest"], first)
            self.assertNotIn("numeric_review_done", restored)
            before = page.read_bytes()
            call(sweep.cmd_promote_extracted_tables, Path("wiki"))
            self.assertEqual(page.read_bytes(), before)
            Path(".curator/tables.db").unlink()
            Path(".curator/records.db").unlink()
            report = recovery.recover(Path("wiki"))
            self.assertEqual(report["corrections"], 0)
            self.assertEqual(report["indexed_records"], 150)
            with sqlite3.connect(".curator/tables.db") as db:
                value = db.execute("SELECT cells_json FROM _extracted_tables WHERE row_idx=140").fetchone()[0]
                self.assertEqual(json.loads(value), ["139"])

    def test_checkpoint_legacy_review_is_idempotent(self):
        with workspace():
            _, page = self.fixture()
            page.write_text(naming.set_frontmatter_field(page.read_text(), "correction_manifest", None))
            self.assertEqual(recovery.checkpoint_reviews(Path("wiki"))["checkpointed"], 1)
            before = page.read_bytes()
            self.assertEqual(recovery.checkpoint_reviews(Path("wiki"))["checkpointed"], 0)
            self.assertEqual(page.read_bytes(), before)
            Path(".curator/tables.db").unlink()
            self.assertEqual(recovery.recover(Path("wiki"))["corrections"], 1)

    def test_correction_source_changes_refuse_publication(self):
        with workspace():
            r, page = self.fixture()
            Path(r["extracted"]).write_text(Path(r["extracted"]).read_text() + "Changed extraction metadata\n")
            before = recovery._class_state(Path(".curator/tables.db"))
            with self.assertRaisesRegex(ValueError, "anchors changed"):
                recovery.recover(Path("wiki"))
            self.assertEqual(recovery._class_state(Path(".curator/tables.db")), before)

    def test_missing_reviewed_rows_report_recovery_instruction(self):
        with workspace():
            self.fixture()
            Path(".curator/tables.db").unlink()
            _, report = call(sweep.cmd_promote_extracted_tables, Path("wiki"))
            self.assertIn("tables.py recover", report["review_warnings"][0]["warning"])

    def test_reviewed_page_frontmatter_stays_valid_yaml(self):
        import yaml
        with workspace():
            _, page = self.fixture()
            fm = yaml.safe_load(page.read_text().split("---", 2)[1])
            self.assertEqual(fm["type"], "extracted-table")
            self.assertIn("999", page.read_text().split("### Column summary")[1])

    def fixture(self):
        r = ingest([{"n": i} for i in range(150)], cap=100)
        promote(r)
        page = next(Path("wiki/tables").glob("tab-*.md"))
        rc, report = call(sweep.cmd_apply_numeric_review, page, json.dumps({"verdict": "wrong",
            "flagged_cells": [{"row_idx": 140, "header": "/n", "suggested": "999"}], "notes": "reviewed original"}))
        self.assertEqual(rc, 0, report)
        p = proposal(r)
        path, _ = install_schema(p)
        self.assertEqual(call(tables.cmd_import_dataset, path)[0], 0)
        return r, page

    def test_recover_snapshot_corrections_and_class_imports(self):
        with workspace():
            _, page = self.fixture()
            files = {p: p.read_bytes() for p in Path("wiki").rglob("*") if p.is_file()}
            dry = recovery.recover(Path("wiki"), True)
            self.assertEqual(dry["status"], "validated")
            Path(".curator/tables.db").unlink()
            result = recovery.recover(Path("wiki"))
            self.assertEqual(result["corrections"], 1)
            with sqlite3.connect(".curator/tables.db") as db:
                value = db.execute("SELECT cells_json FROM _extracted_tables WHERE table_stem=? AND row_idx=140", (page.stem,)).fetchone()[0]
                self.assertEqual(json.loads(value), ["999"])
                self.assertEqual(db.execute("SELECT count(*) FROM observations").fetchone()[0], 150)
            self.assertEqual(files, {p: p.read_bytes() for p in files})

    def test_missing_recipe_refuses_without_replacing_database(self):
        with workspace():
            _, page = self.fixture()
            fm, _ = naming.read_frontmatter(page.read_text())
            (Path("wiki") / fm["correction_manifest"]).unlink()
            before = recovery._class_state(Path(".curator/tables.db"))
            with self.assertRaises((ValueError, OSError)):
                recovery.recover(Path("wiki"))
            self.assertEqual(recovery._class_state(Path(".curator/tables.db")), before)

    def test_unmanifested_class_rows_refuse_recovery(self):
        with workspace():
            self.fixture()
            for path in Path("wiki/_data/imports").glob("*.json"):
                path.unlink()
            with self.assertRaisesRegex(ValueError, "lose or alter"):
                recovery.recover(Path("wiki"))


if __name__ == "__main__":
    unittest.main()
