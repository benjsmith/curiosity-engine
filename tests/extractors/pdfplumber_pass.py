"""pdfplumber_pass — pypdf prose + pdfplumber table-pass.

Probes whether the cheap pdfplumber stage would meaningfully close the
table-fidelity gap on the PDF fixtures. Emits the pypdf prose first,
then a `## Extracted tables` section with one GFM block per table that
pdfplumber found.

PDF only — non-PDF fixtures return `not_applicable`.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

NAME = "pdfplumber_pass"
SUPPORTS = {"pdf"}


def available() -> tuple[bool, str]:
    try:
        import pdfplumber  # noqa: F401
        import pypdf  # noqa: F401
        return True, ""
    except Exception as e:
        return False, f"missing dep: {e}"


def _gfm_table(rows: list) -> str:
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    norm = [
        [("" if c is None else str(c).strip().replace("|", "\\|").replace("\n", " "))
         for c in r + [""] * (width - len(r))]
        for r in rows
    ]
    out = ["| " + " | ".join(norm[0]) + " |",
           "|" + "|".join(["---"] * width) + "|"]
    for r in norm[1:]:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def extract(path: Path) -> dict:
    ok, msg = available()
    if not ok:
        return {"available": False, "body": "", "error": msg, "extra": {}}
    ext = path.suffix.lower().lstrip(".")
    if ext != "pdf":
        return {"available": True, "body": "", "error": "not_applicable",
                "extra": {}}

    import io
    import pdfplumber
    from local_ingest import _extract_pdf

    raw = path.read_bytes()
    prose, note = _extract_pdf(raw)
    chunks = [prose] if prose else []
    n_tables = 0
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for page_idx, page in enumerate(pdf.pages, 1):
                tables = page.extract_tables() or []
                for tbl in tables:
                    if not tbl or not any(any(c for c in r if c is not None) for r in tbl):
                        continue
                    if n_tables == 0:
                        chunks.append("\n\n## Extracted tables\n")
                    chunks.append(f"\n### Table p.{page_idx}")
                    chunks.append(_gfm_table(tbl))
                    n_tables += 1
    except Exception as e:
        return {"available": True, "body": "\n".join(chunks),
                "error": f"pdfplumber failed: {type(e).__name__}: {e}",
                "extra": {"tables_found": n_tables}}

    return {
        "available": True,
        "body": "\n".join(chunks),
        "error": note or None,
        "extra": {"tables_found": n_tables, "extraction_method": "pypdf+pdfplumber"},
    }
