"""baseline_pypdf — mirror the current local_ingest.py production path.

For PDFs: calls local_ingest._extract_pdf (pypdf text extraction) and
returns the body the way ingest_one would write it. For non-PDFs in the
fixture set (csv/xlsx/pptx), returns `not_extracted` because those
extensions are not in DEFAULT_EXTS.

Output dict shape matches the rest of `tests/extractors/`:
  {"available": bool, "body": str, "error": str | None,
   "extra": {"extraction_method": str, "multimodal_recommended": bool}}
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

NAME = "baseline_pypdf"
SUPPORTS = {"pdf", "csv", "xlsx", "pptx"}


def available() -> tuple[bool, str]:
    try:
        import pypdf  # noqa: F401
        return True, ""
    except Exception as e:
        return False, f"pypdf import failed: {e}"


def extract(path: Path) -> dict:
    ok, msg = available()
    if not ok:
        return {"available": False, "body": "", "error": msg, "extra": {}}

    ext = path.suffix.lower().lstrip(".")
    if ext != "pdf":
        return {
            "available": True,
            "body": "",
            "error": f"not_extracted (.{ext} not in DEFAULT_EXTS)",
            "extra": {"extraction_method": "skipped"},
        }

    from local_ingest import _extract_pdf, _detect_math, _detect_tables, _sanity_check

    raw = path.read_bytes()
    text, note = _extract_pdf(raw)
    sane, sanity_note = _sanity_check(text)
    has_math = _detect_math(text)
    has_tables = _detect_tables(text)
    extraction_method = "pypdf" if sane else "pypdf_failed"
    # Expose the attempted text even when production sanity gate would
    # punt to multimodal — the harness measures raw pypdf capability,
    # not whether the gate fires. The `multimodal_recommended` flag in
    # `extra` records the gate's verdict honestly.
    return {
        "available": True,
        "body": text or "",
        "error": note or (sanity_note if not sane else None),
        "extra": {
            "extraction_method": extraction_method,
            "has_math": has_math,
            "has_tables": has_tables,
            "sanity_passed": sane,
            "sanity_note": sanity_note,
            "multimodal_recommended": (not sane) or has_math or has_tables,
        },
    }
