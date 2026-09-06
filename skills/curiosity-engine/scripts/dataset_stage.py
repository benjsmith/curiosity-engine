"""Disposable SQLite staging for exact, bounded record processing."""
import json
import sqlite3
import tempfile
from collections.abc import Sequence
from pathlib import Path

import structured_data as sd


class View(Sequence):
    def __init__(self, stage, mode):
        self.stage, self.mode = stage, mode

    def __len__(self):
        return self.stage.count

    def _decode(self, flat, origin):
        if self.mode == "origins":
            return json.loads(origin)
        if self.mode == "locators":
            return json.loads(origin)["locator"]
        values = sd.loads(flat)
        if self.mode == "values":
            return values
        return ([sd.literal(values.get(k, sd.MISSING)) for k in self.stage.headers]
                if self.stage.headers else [json.dumps(json.loads(origin)["locator"])])

    def __iter__(self):
        for flat, origin in self.stage.db.execute("SELECT flat, origin FROM records ORDER BY seq"):
            yield self._decode(flat, origin)

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            return [self[i] for i in range(start, stop, step)]
        if index < 0:
            index += len(self)
        row = self.stage.db.execute("SELECT flat, origin FROM records WHERE seq=?", (index,)).fetchone()
        if row is None:
            raise IndexError(index)
        return self._decode(*row)

    def __eq__(self, other):
        return len(self) == len(other) and all(a == b for a, b in zip(self, other))


class Stage:
    def __init__(self, cfg=None):
        self.limits = sd.options(cfg)
        if any(self.limits[k] <= 0 for k in sd.DEFAULT_LIMITS):
            raise sd.StructuredError("limits must be positive")
        if self.limits["max_stage_bytes"] < 64 * 1024:
            raise sd.StructuredError("max_stage_bytes must allow at least 64 KiB for SQLite staging")
        self.external_bytes = 0
        self.temp = tempfile.TemporaryDirectory(prefix="ce-records-")
        self.path = Path(self.temp.name)
        self.db = sqlite3.connect(self.path / "stage.db")
        self.db.execute("PRAGMA journal_mode=OFF")
        self.db.execute("PRAGMA cache_size=-2048")
        self.db.execute("PRAGMA temp_store=FILE")
        self.db.execute(f"PRAGMA max_page_count={max(1, self.limits['max_stage_bytes'] // 4096)}")
        self.db.executescript("""
            CREATE TABLE records(seq INTEGER PRIMARY KEY, flat TEXT NOT NULL, origin TEXT NOT NULL);
            CREATE INDEX records_flat ON records(flat);
            CREATE TABLE cells(field TEXT, value TEXT, kind TEXT, seq INTEGER);
            CREATE INDEX cells_field_value ON cells(field, value);
            CREATE INDEX cells_field_seq ON cells(field, seq);
        """)
        self.count = 0
        self.fields = set()

    @property
    def headers(self):
        return sorted(self.fields)

    def add(self, flat, origin):
        from datasets import kind
        self.fields.update(flat)
        n = self.count + 1
        for key, size in (("max_records", n), ("max_fields", len(self.fields)),
                          ("max_cells", n * max(1, len(self.fields)))):
            if size > self.limits[key]:
                raise sd.StructuredError(f"{key} exceeded across selected records")
        canonical = sd.literal({k: flat[k] for k in sorted(flat)})
        try:
            self.db.execute("INSERT INTO records VALUES (?, ?, ?)",
                            (self.count, canonical, json.dumps(origin, sort_keys=True)))
            self.db.executemany("INSERT INTO cells VALUES (?, ?, ?, ?)",
                ((field, sd.literal(value), kind(value), self.count) for field, value in flat.items()))
        except sqlite3.OperationalError as exc:
            if "full" in str(exc):
                raise sd.StructuredError("max_stage_bytes exceeded") from exc
            raise
        self.count = n
        if n % 256 == 0:
            self.check_disk()

    def check_disk(self):
        self.db.commit()
        if self.disk_bytes() + self.external_bytes > self.limits["max_stage_bytes"]:
            raise sd.StructuredError("max_stage_bytes exceeded")

    def disk_bytes(self):
        return sum(p.stat().st_size for p in self.path.iterdir())

    def view(self, mode):
        return View(self, mode)

    def table(self):
        return {"path": "", "kind": "records", "description": "root",
                "headers": self.headers or ["@source_locator"], "values": self.view("values"),
                "rows": self.view("rows"), "locators": self.view("locators")}

    def profile(self):
        columns = []
        for field in self.headers:
            counts = dict(self.db.execute("SELECT kind,count(*) FROM cells WHERE field=? GROUP BY kind", (field,)))
            missing = self.count - sum(counts.values())
            if missing:
                counts["missing"] = missing
            types = set(counts) - {"missing", "null"}
            present_sql = "FROM cells WHERE field=? AND kind != 'null'"
            unique = self.db.execute("SELECT count(DISTINCT value) " + present_sql, (field,)).fetchone()[0]
            samples, seen = [], set()
            for (value,) in self.db.execute("SELECT value " + present_sql + " ORDER BY seq", (field,)):
                if value not in seen:
                    seen.add(value)
                    samples.append(value[:160])
                    if len(samples) == 3:
                        break
            safe_int = types == {"integer"} and all(
                len(v.lstrip("-")) <= 19 and v != "-0" and -(2**63) <= int(v) < 2**63
                for (v,) in self.db.execute("SELECT value " + present_sql, (field,)))
            dtype = ("int" if safe_int else "bool" if types == {"boolean"} else
                     "text" if types <= {"string"} or types <= {"integer", "decimal"} else "json")
            columns.append({"field": field, "observed_types": counts, "storage_type": dtype,
                "nullable": bool(counts.get("null") or missing), "distinct_non_null": unique,
                "unique_non_null_over_full_collection": bool(self.count) and unique == self.count,
                "sample_literals": samples, "sample_cell_limit": 160})
        distinct = self.db.execute("SELECT count(DISTINCT flat) FROM records").fetchone()[0]
        return {"row_count": self.count, "columns": columns, "duplicate_records": self.count - distinct}

    def summary(self):
        from decimal import Decimal, DecimalException
        if not self.headers:
            return [{"name": "@source_locator", "non_null": self.count, "total": self.count,
                     "dtype": "json-literal", "distinct_count": self.count,
                     "sample": [row[0] for row in self.view("rows")[:3]]}]
        out = []
        for col in self.profile()["columns"]:
            field = col["field"]
            types = set(col["observed_types"]) - {"null", "missing"}
            numeric = bool(types) and types <= {"integer", "decimal"}
            info = {"name": field, "total": self.count,
                "non_null": self.count - col["observed_types"].get("null", 0) - col["observed_types"].get("missing", 0),
                "dtype": "numeric" if numeric else "json-literal"}
            if numeric:
                low = high = None
                try:
                    for (value,) in self.db.execute("SELECT value FROM cells WHERE field=? AND kind != 'null'", (field,)):
                        number = Decimal(value)
                        low = number if low is None else min(low, number)
                        high = number if high is None else max(high, number)
                    info.update(min=str(low), max=str(high))
                except (DecimalException, ValueError, OverflowError):
                    info["summary_warning"] = "numeric range exceeds summary limits; consult literal cells"
            else:
                info.update(distinct_count=col["distinct_non_null"], sample=col["sample_literals"])
            out.append(info)
        return out

    def close(self):
        db = getattr(self, "db", None)
        if db is not None:
            self.db = None
            db.close()
        if getattr(self, "temp", None) is not None:
            self.temp.cleanup()

    def __del__(self):
        self.close()
