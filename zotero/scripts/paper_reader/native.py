"""Native Zotero highlights via the existing authorized API; never edit its DB."""
from html import escape
from html.parser import HTMLParser
import json
from . import jobs, pdf, storage as s


def native_key(value):
    alphabet = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"
    number = int(s.digest(value)[:10], 16)
    return "".join(alphabet[(number >> (5 * index)) & 31] for index in range(8))


def original_rects(source_page, meta, boxes):
    """Undo the display rotation before sending PDF coordinates to Zotero."""
    from pypdf import Transformation
    media = source_page.mediabox
    if tuple(media) != tuple(source_page.cropbox) or float(media.left) != 0 or float(media.bottom) != 0:
        raise ValueError("Native highlights currently require an uncropped PDF with a zero-origin MediaBox; use the annotated copy for this file")
    transform = Transformation().translate(-float(media.width) / 2, -float(media.height) / 2).rotate(-source_page.rotation)
    p1, p2 = transform.apply_on(media.lower_left), transform.apply_on(media.upper_right)
    transform = transform.translate(-min(p1[0], p2[0]), -min(p1[1], p2[1]))
    a, b, c, d, e, f = transform.ctm
    determinant = a * d - b * c
    def inverse(x, y):
        return ((d * (x - e) - c * (y - f)) / determinant,
                (-b * (x - e) + a * (y - f)) / determinant)
    result = []
    for x0, top, x1, bottom in boxes:
        points = [inverse(x, meta["height"] - y) for x in (x0, x1) for y in (top, bottom)]
        rect = [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]
        if rect[0] < -.1 or rect[1] < -.1 or rect[2] > float(media.width) + .1 or rect[3] > float(media.height) + .1:
            raise ValueError("Highlight geometry is outside the original PDF page")
        result.append([round(n, 3) for n in rect])
    return result


def plan(job_id):
    pdf.dependencies()
    from pypdf import PdfReader
    directory, manifest = jobs.load(job_id)
    if s.file_hash(manifest["source"]) != manifest["source_hash"]:
        raise ValueError("Source PDF changed; prepare it again before native annotation")
    _, claims, _ = jobs.aggregate(directory)
    document = s.read(manifest["document"])
    blocks = {b["id"]: b for b in document["blocks"]}
    reader = PdfReader(manifest["source"])
    detailed = manifest["settings"]["annotation"] == "detailed"
    items, seen = [], set()
    for claim in claims:
        if not detailed and claim["category"] not in {"contribution", "method", "experiment", "limitation"}:
            continue
        for ref in claim["evidence"][:3 if detailed else 1]:
            block = blocks[ref["block_id"]]
            key = native_key([manifest["source_hash"], manifest["attachment_key"], claim["id"], ref])
            if key in seen or (not detailed and len(items) >= 12):
                continue
            seen.add(key)
            meta = document["pages"][block["page"] - 1]
            boxes = pdf.quote_boxes(block, ref["quote"])
            rects = original_rects(reader.pages[block["page"] - 1], meta, boxes)
            items.append({"key": key, "itemType": "annotation", "parentItem": manifest["attachment_key"],
                          "annotationType": "highlight", "annotationText": escape(ref["quote"]),
                          "annotationComment": escape(f"[{jobs.KIND_LABELS[claim['kind']]}] {claim['statement']}"),
                          "annotationColor": "#" + pdf.COLORS.get(claim["category"], "FFE066").lower(),
                          "annotationPageLabel": str(block["page"]),
                          "annotationSortIndex": f"{block['page'] - 1:05}|000000|{int(boxes[0][1]):05}",
                          "annotationPosition": json.dumps({"pageIndex": block["page"] - 1, "rects": rects}),
                          "tags": [{"tag": "dsh-reader"}, {"tag": "dsh-source:" + manifest["source_hash"][:12]}]})
    return manifest, items


def publish(api, job_id, *, yes=False):
    manifest, items = plan(job_id)
    if not yes:
        return {"dry_run": True, "count": len(items), "items": items, "target": manifest["attachment_key"]}
    directory, _ = jobs.load(job_id)
    created, reused, failures, preserved = [], [], [], []
    with s.lock(directory):
        for item in items:
            existing = api.request(f"{manifest['api_prefix']}/items/{item['key']}")
            if existing.ok:
                data = api.parse_body(existing)
                data = data.get("data", data)
                owned = any(t.get("tag") == "dsh-reader" for t in data.get("tags", []))
                if data.get("parentItem") != manifest["attachment_key"] or data.get("itemType") != "annotation" or not owned:
                    raise ValueError("Annotation key collision with a different object; no existing object was changed")
                reused.append(item["key"])
                if data.get("annotationComment") != item["annotationComment"]:
                    preserved.append({"key": item["key"], "reason": "Existing comment differs from current analysis; preserved for user review"})
                continue  # Preserve user edits to an existing generated annotation.
            if existing.status != 404:
                api.require_ok(existing, "Check existing native annotation")
            response = api.local_write(f"{manifest['api_prefix']}/items", [item], method="POST")
            api.require_ok(response, "Create native annotation")
            body = api.parse_body(response)
            if not isinstance(body, dict) or body.get("failed") or not api.created_key(body):
                failures.append({"key": item["key"], "response": body})
                break
            created.append(item["key"])
        receipt = {"source_hash": manifest["source_hash"], "created": created, "reused": reused, "failures": failures,
                   "preserved_differences": preserved}
        s.write(directory / "native-annotations.json", receipt)
    return dict(receipt, complete=not failures, dry_run=False)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)


def plain(text):
    parser = _Text()
    parser.feed(text or "")
    return " ".join(" ".join(parser.parts).split())


def collect(api, job_id):
    directory, manifest = jobs.load(job_id)
    path = f"{manifest['api_prefix']}/items/{manifest['attachment_key']}/children"
    records = api.fetch_all(path, {"itemType": "annotation"})
    result = []
    for record in records:
        data = record.get("data", record)
        if data.get("itemType") != "annotation":
            continue
        result.append({"key": record.get("key") or data.get("key"), "quote": plain(data.get("annotationText", "")),
                       "comment": plain(data.get("annotationComment", "")), "page_label": data.get("annotationPageLabel"),
                       "kind": "user_annotation_not_paper_fact", "type": data.get("annotationType")})
    s.write(directory / "user-annotations.json", {"source_hash": manifest["source_hash"], "annotations": result})
    return {"annotations": result[:50], "total": len(result), "truncated": len(result) > 50,
            "path": str(directory / "user-annotations.json"),
            "next_step": "Use these as user questions and reading priorities; verify each answer against paper passages"}
