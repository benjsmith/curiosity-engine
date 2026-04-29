"""docling_pass — IBM Docling with the BigMixSolDB / Voinea 2026 settings.

Settings:
  image_scale = 4
  TableFormer mode = ACCURATE
  formula enrichment = on
  post-strip any character that repeats >8 times (Docling's known
  formula-loop failure mode)

Reference: Voinea, A. et al. (2026). BigMixSolDB. ChemRxiv preprint.
DOI: 10.26434/chemrxiv.15001616/v1.

PDF only — non-PDF fixtures return `not_applicable`. Docling has heavy
dependencies (torch + OCR models); when unavailable the harness reports
`unavailable` cleanly.
"""
from pathlib import Path
import re

NAME = "docling_pass"
SUPPORTS = {"pdf"}

_REPEAT_RE = re.compile(r"(.)\1{8,}")


def available() -> tuple[bool, str]:
    try:
        from docling.document_converter import DocumentConverter  # noqa: F401
    except Exception as e:
        return False, f"docling not installed: {e}"
    # Docling pulls layout/table models from HuggingFace at first run.
    # In sandboxed/offline environments the download fails and every
    # extract call surfaces a network error. Detect that here so the
    # harness reports UNAV (environment problem) instead of per-cell
    # FAIL (verdict on Docling's quality).
    try:
        from huggingface_hub import snapshot_download
        # Cheap probe: does the layout model already exist locally?
        snapshot_download(
            repo_id="docling-project/docling-layout-heron",
            local_files_only=True,
        )
    except Exception as e:
        return False, ("docling models not cached locally and HuggingFace "
                       f"unreachable: {type(e).__name__}. To enable, run "
                       "Docling once with network access so models are "
                       "cached, then re-run the harness.")
    return True, ""


def extract(path: Path) -> dict:
    ok, msg = available()
    if not ok:
        return {"available": False, "body": "", "error": msg, "extra": {}}
    ext = path.suffix.lower().lstrip(".")
    if ext != "pdf":
        return {"available": True, "body": "", "error": "not_applicable",
                "extra": {}}

    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode

        pipeline = PdfPipelineOptions()
        pipeline.images_scale = 4
        pipeline.do_table_structure = True
        pipeline.table_structure_options.mode = TableFormerMode.ACCURATE
        pipeline.do_formula_enrichment = True
        # Our fixtures are reportlab-generated text PDFs — Unicode-mapped
        # fonts mean Docling's text-layer reader handles them without OCR.
        # Real scanned scientific PDFs would need OCR on; flip to True
        # when measuring against scanned material.
        pipeline.do_ocr = False

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline)
            }
        )
        result = converter.convert(str(path))
        md = result.document.export_to_markdown()
    except Exception as e:
        return {"available": True, "body": "",
                "error": f"docling conversion failed: {type(e).__name__}: {e}",
                "extra": {}}

    cleaned = _REPEAT_RE.sub(lambda m: m.group(1) * 8, md)
    return {
        "available": True,
        "body": cleaned,
        "error": None,
        "extra": {"extraction_method": "docling",
                  "image_scale": 4,
                  "tableformer_mode": "ACCURATE",
                  "formula_enrichment": True},
    }
