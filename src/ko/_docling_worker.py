"""Runs INSIDE docling's own environment (`uv run --no-project --with docling python <this>`),
never imported by ko — docling pulls in torch and ~1 GB of models, which ko doesn't carry.

Input: one JSON argument {path, pages: [first, last] | null, ocr, assets_dir | null, describe:
{model, prompt} | null}. Output on stdout: one JSON object {total, figures, pages: [{num, md}],
described: {tried, got, errors}} — ko adds its own source line and page markers. Logs go to stderr.
"""

import json
import logging
import os
import sys
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    PictureDescriptionApiOptions,
)
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import ImageRefMode

# Gemini speaks the OpenAI chat-completions shape at this endpoint; docling's API picture
# describer posts exactly that, so no Gemini SDK is needed in this environment.
_GEMINI_OPENAI = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"


class _Errors(logging.Handler):
    """docling reports SUCCESS when every picture-description call failed (a 402 from Gemini
    only reaches its log, leaving empty descriptions) — keep the errors so ko can say so."""

    def __init__(self):
        super().__init__(logging.ERROR)
        self.seen: list[str] = []

    def emit(self, record):
        if len(self.seen) < 3:
            self.seen.append(record.getMessage()[:300])


def main(args: dict) -> dict:
    errors = _Errors()
    logging.getLogger().addHandler(errors)
    path = Path(args["path"])
    assets = Path(args["assets_dir"]) if args.get("assets_dir") else None
    opts = PdfPipelineOptions(
        do_ocr=args["ocr"],
        do_table_structure=True,
        generate_picture_images=bool(assets or args.get("describe")),
        images_scale=2.0,  # figures at 144 dpi: legible diagrams, sane file sizes
    )
    if describe := args.get("describe"):
        opts.enable_remote_services = True
        opts.do_picture_description = True
        opts.picture_description_options = PictureDescriptionApiOptions(
            url=_GEMINI_OPENAI,
            headers={"Authorization": f"Bearer {os.environ['GEMINI_API_KEY']}"},
            params={"model": describe["model"], "max_completion_tokens": 400},
            prompt=describe["prompt"],
            timeout=90,
            concurrency=4,
        )
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    kw = {"page_range": tuple(args["pages"])} if args.get("pages") else {}
    result = converter.convert(str(path), **kw)
    doc = result.document

    mode = ImageRefMode.PLACEHOLDER
    if assets is not None:
        assets.mkdir(parents=True, exist_ok=True)
        # The copy save_as_markdown makes: pictures written to `assets`, referenced by a path
        # relative to where the markdown will sit (its parent dir).
        doc = doc._make_copy_with_refmode(assets, ImageRefMode.REFERENCED, page_no=None, reference_path=assets.parent)
        mode = ImageRefMode.REFERENCED

    def md(page_no=None) -> str:
        return doc.export_to_markdown(
            page_no=page_no, image_mode=mode, compact_tables=True, image_placeholder="<!-- figure -->"
        )

    numbers = sorted(doc.pages)  # PDFs have pages; a .docx/.pptx/.xlsx usually doesn't
    pages = [{"num": n, "md": md(n)} for n in numbers] if numbers else [{"num": None, "md": md()}]
    total = result.input.page_count or len(numbers) or 1  # the whole file, not just --pages
    # tried = pictures big enough to be sent (docling skips tiny ones); got = came back with text
    tried = [p.meta.description for p in doc.pictures if p.meta and p.meta.description is not None]
    described = {"tried": len(tried), "got": sum(1 for d in tried if d.text.strip()), "errors": errors.seen}
    return {"total": total, "figures": len(doc.pictures), "pages": pages, "described": described}


if __name__ == "__main__":
    json.dump(main(json.loads(sys.argv[1])), sys.stdout)
