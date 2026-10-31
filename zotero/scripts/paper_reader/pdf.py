"""Local PDF geometry, OCR, annotations and page-aligned bilingual editions.

Optional dependencies are bootstrapped by lib/reader-runner.js, never at import.
Coordinates in reading manifests use PDF points with a top-left origin.
"""
from collections import defaultdict
from html import escape
import io
from pathlib import Path
import re


def component(name="pdf"):
    raise SystemExit(f"READER_COMPONENT_REQUIRED:{name}")


def font_name():
    """Embed a cached OFL font so Chinese PDF output is portable."""
    import os
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from .storage import root
    path = Path(os.environ.get("DSH_READER_FONT", root() / "reader-runtime/fonts/NotoSansSC-Regular.ttf"))
    if not path.is_file():
        component("font")
    name = "DSHReaderNotoSC"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(path)))
    return name


def dependencies():
    try:
        import pdfplumber
        import pypdf
        import pypdfium2
        import reportlab
    except ImportError:
        component()


def norm(text):
    import unicodedata
    return " ".join(unicodedata.normalize("NFKC", str(text)).replace("\u00ad", "").split())


def _lines(words):
    rows = []
    for word in sorted(words, key=lambda w: (round(w["top"] / 3), w["x0"])):
        if not rows or abs(rows[-1][0]["top"] - word["top"]) > 3:
            rows.append([])
        rows[-1].append(word)
    return [sorted(row, key=lambda w: w["x0"]) for row in rows]


def _bbox(words):
    return [min(w["x0"] for w in words), min(w["top"] for w in words),
            max(w["x1"] for w in words), max(w["bottom"] for w in words)]


def _column_groups(words, width):
    """Detect a central gutter from word geometry, retaining spanning headings."""
    if len(words) < 40:
        return [words]
    middle = width / 2
    spanning = [w for w in words if w["x0"] < middle - 8 and w["x1"] > middle + 8]
    left = [w for w in words if w["x1"] <= middle + 8]
    right = [w for w in words if w["x0"] >= middle - 8]
    # A normal single-column line crosses the gutter on most baselines.
    cross_rows = len({round(w["top"] / 8) for w in spanning})
    all_rows = max(1, len({round(w["top"] / 8) for w in words}))
    if len(left) < 15 or len(right) < 15 or cross_rows / all_rows > .18:
        return [words]
    # Split at full-width headings, then consume the left column before right.
    boundaries = sorted({w["bottom"] + 2 for w in spanning})
    groups, low = [], -1
    for high in boundaries + [float("inf")]:
        band = [w for w in words if low <= w["top"] < high]
        cross = [w for w in band if w in spanning]
        columns = [[w for w in band if w not in spanning and (w["x0"] + w["x1"]) / 2 < middle],
                   [w for w in band if w not in spanning and (w["x0"] + w["x1"]) / 2 >= middle]]
        groups.extend(g for g in columns if g)
        if cross:
            groups.append(cross)
        low = high
    return groups


def _ocr(pdf, index):
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        component("ocr")
    import numpy as np
    if not hasattr(_ocr, "engine"):
        _ocr.engine = RapidOCR()
    page = pdf[index]
    bitmap = page.render(scale=2)
    image = bitmap.to_pil()
    try:
        result, _ = _ocr.engine(np.array(image))
    finally:
        image.close()
        bitmap.close()
        page.close()
    return [{"text": text, "x0": min(p[0] for p in box) / 2,
             "top": min(p[1] for p in box) / 2, "x1": max(p[0] for p in box) / 2,
             "bottom": max(p[1] for p in box) / 2, "confidence": float(confidence)}
            for box, text, confidence in result or [] if text.strip()]


def extract(path, *, ocr="auto", progress=None):
    dependencies()
    import pdfplumber
    import pypdfium2
    pages, blocks, warnings = [], [], []
    renderer = None
    in_references = False
    try:
        with pdfplumber.open(path) as pdf:
            for index, page in enumerate(pdf.pages):
                if progress:
                    progress("extract", index + 1, len(pdf.pages))
                words = page.extract_words(x_tolerance=2, y_tolerance=3)
                used_ocr = len(" ".join(w["text"] for w in words)) < 30
                if used_ocr and ocr != "off":
                    if renderer is None:
                        renderer = pypdfium2.PdfDocument(str(path))
                    words = _ocr(renderer, index)
                    warnings.append(f"Page {index + 1}: OCR text and reading order require review")
                if not words:
                    warnings.append(f"Page {index + 1}: no readable text; page retained in PDF output")
                page_blocks = []
                for group in _column_groups(words, page.width):
                    paragraphs = []
                    previous_heading = False
                    for line in _lines(group):
                        text = " ".join(w["text"] for w in line)
                        prev = paragraphs[-1] if paragraphs else []
                        gap = line[0]["top"] - max((w["bottom"] for w in prev), default=-100)
                        heading = bool(re.match(r"^(?:\d+(?:\.\d+)*[ .]+)?(?:abstract|introduction|methods?|results?|discussion|conclusions?|references|appendix)\b", text, re.I))
                        if not prev or gap > 8 or heading or previous_heading or len(" ".join(w["text"] for w in prev)) > 1400:
                            paragraphs.append([])
                        paragraphs[-1].extend(line)
                        previous_heading = heading
                    for paragraph in paragraphs:
                        text = " ".join(w["text"] for w in paragraph)
                        if re.match(r"^(?:\d+[ .]+)?references\s*$", text, re.I):
                            in_references = True
                        if re.match(r"^(?:\d+[ .]+)?appendi[xc]", text, re.I):
                            in_references = False
                        kind = "reference" if in_references else "text"
                        if re.match(r"^(?:fig(?:ure)?\.?|table)\s*\d", text, re.I):
                            kind = "caption"
                        if re.search(r"[∑∫∂∇⊗∈≤≥]", text) and len(text) < 350:
                            kind = "equation"
                        block = {"id": f"p{index + 1}-b{len(page_blocks) + 1}", "page": index + 1,
                                 "text": text, "bbox": _bbox(paragraph), "kind": kind,
                                 "translate": kind == "text", "ocr": used_ocr,
                                 "words": [{k: w[k] for k in ("text", "x0", "top", "x1", "bottom")} for w in paragraph]}
                        page_blocks.append(block)
                # Raster figures and vector tables remain visible in both editions.
                regions = []
                for image in page.images:
                    box = [max(0, image["x0"]), max(0, image["top"]),
                           min(page.width, image["x1"]), min(page.height, image["bottom"])]
                    if box[2] - box[0] > 30 and box[3] - box[1] > 20 and not used_ocr:
                        regions.append({"kind": "figure", "bbox": box})
                for table in page.find_tables():
                    box = list(table.bbox)
                    regions.append({"kind": "table", "bbox": box})
                    for block in page_blocks:
                        x0, top, x1, bottom = block["bbox"]
                        if box[0] <= (x0 + x1) / 2 <= box[2] and box[1] <= (top + bottom) / 2 <= box[3]:
                            block["kind"] = "table"
                            block["translate"] = False
                regions += [{"kind": "equation", "bbox": b["bbox"]} for b in page_blocks if b["kind"] == "equation"]
                pages.append({"number": index + 1, "width": float(page.width), "height": float(page.height),
                              "blocks": [b["id"] for b in page_blocks], "regions": regions, "ocr": used_ocr,
                              "readable": bool(words)})
                blocks.extend(page_blocks)
    finally:
        if renderer is not None:
            renderer.close()
    return {"pages": pages, "blocks": blocks, "warnings": warnings}


def quote_boxes(block, quote):
    """Resolve a validated quote to word boxes, grouped by baseline."""
    words = block["words"]
    joined, offsets = "", []
    for word in words:
        start = len(joined)
        joined += (" " if joined else "") + norm(word["text"])
        offsets.append((start, len(joined), word))
    start = joined.find(norm(quote))
    if start < 0:
        raise ValueError(f"Quote cannot be located in {block['id']}")
    end = start + len(norm(quote))
    selected = [w for a, b, w in offsets if a < end and b > start]
    return [_bbox(line) for line in _lines(selected)]


COLORS = {"contribution": "FFE066", "method": "9AD0F5", "formula": "B4D8FC",
          "experiment": "A6E3A1", "limitation": "FAB387", "implementation": "CBA6F7"}


def annotate(source, target, document, claims, *, detailed=False):
    from pypdf import PdfReader, PdfWriter
    from pypdf.annotations import Highlight
    from pypdf.generic import ArrayObject, FloatObject, TextStringObject, NameObject
    writer = PdfWriter(clone_from=PdfReader(source))
    for page in writer.pages:
        if page.rotation:
            page.transfer_rotation_to_content()
    blocks = {b["id"]: b for b in document["blocks"]}
    added = 0
    for claim in claims:
        if not detailed and claim["category"] not in {"contribution", "method", "experiment", "limitation"}:
            continue
        if not detailed and added >= 12:
            break
        for ref in claim["evidence"][:(3 if detailed else 1)]:
            block = blocks[ref["block_id"]]
            page = document["pages"][block["page"] - 1]
            boxes = quote_boxes(block, ref["quote"])
            quads = []
            for x0, top, x1, bottom in boxes:
                quads.extend([x0, page["height"] - top, x1, page["height"] - top,
                              x0, page["height"] - bottom, x1, page["height"] - bottom])
            rect = [min(b[0] for b in boxes), page["height"] - max(b[3] for b in boxes),
                    max(b[2] for b in boxes), page["height"] - min(b[1] for b in boxes)]
            annotation = Highlight(rect=rect, quad_points=ArrayObject([FloatObject(n) for n in quads]),
                                   highlight_color=COLORS.get(claim["category"], "FFE066"))
            annotation[NameObject("/Contents")] = TextStringObject(f"[{claim['kind']}] {claim['statement']}")
            annotation[NameObject("/T")] = TextStringObject("DSH Zotero Reader")
            writer.add_annotation(block["page"] - 1, annotation)
            added += 1
    writer.write(target)
    return added


def bilingual(source, target, document, translations, layout):
    """Keep original pages as vector PDF; translated overflow gets continuation sheets."""
    from pypdf import PdfReader, PdfWriter, Transformation
    import pypdfium2
    from reportlab.pdfbase import pdfmetrics
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak
    font = font_name()
    style = ParagraphStyle("Chinese", fontName=font, fontSize=10, leading=16, wordWrap="CJK", spaceAfter=7)
    original = PdfReader(source)
    renderer = pypdfium2.PdfDocument(str(source))
    writer = PdfWriter()
    blocks = {b["id"]: b for b in document["blocks"]}
    try:
        for meta, original_page in zip(document["pages"], original.pages):
            if original_page.rotation:
                original_page.transfer_rotation_to_content()
            width, height = meta["width"], meta["height"]
            flow = [Paragraph(f"中文译文 · 原文第 {meta['number']} 页", style), Spacer(1, 8)]
            for block_id in meta["blocks"]:
                block = blocks[block_id]
                if block["kind"] == "equation":
                    continue
                text = translations.get(block_id, block["text"])
                label = "" if block_id in translations else "[原文保留] "
                flow.append(Paragraph(escape(label + text).replace("\n", "<br/>"), style))
            if not meta["readable"]:
                flow.append(Paragraph("此页未识别出可翻译文本，请核对原页。", style))
            if meta["regions"]:
                render_page = renderer[meta["number"] - 1]
                bitmap = render_page.render(scale=1.5)
                image = bitmap.to_pil()
                try:
                    for region in meta["regions"]:
                        x0, y0, x1, y1 = region["bbox"]
                        crop = image.crop((int(x0 * 1.5), int(y0 * 1.5), int(x1 * 1.5), int(y1 * 1.5)))
                        data = io.BytesIO()
                        crop.save(data, format="PNG")
                        crop.close()
                        data.seek(0)
                        scale = min((width - 70) / max(x1 - x0, 1), (height - 130) / max(y1 - y0, 1), 1)
                        flow.append(Image(data, width=(x1 - x0) * scale, height=(y1 - y0) * scale))
                        flow.append(Spacer(1, 8))
                finally:
                    image.close()
                    bitmap.close()
                    render_page.close()
            stream = io.BytesIO()
            SimpleDocTemplate(stream, pagesize=(width, height), leftMargin=35, rightMargin=35,
                              topMargin=35, bottomMargin=35).build(flow)
            translated = PdfReader(stream)
            for index, translated_page in enumerate(translated.pages):
                if layout == "alternating":
                    if index == 0:
                        writer.add_page(original_page)
                    writer.add_page(translated_page)
                else:
                    side = layout == "side-by-side"
                    page = writer.add_blank_page(width=width * (2 if side else 1), height=height * (1 if side else 2))
                    page.merge_transformed_page(original_page, Transformation().translate(0, 0 if side else height))
                    page.merge_transformed_page(translated_page, Transformation().translate(width if side else 0, 0))
        writer.write(target)
    finally:
        renderer.close()
    return len(writer.pages)
