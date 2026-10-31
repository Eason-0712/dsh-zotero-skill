"""Resumable host-model work, quote validation, and reusable reading exports."""
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import re
import sys
import time
import urllib.parse

from . import SCHEMA
from . import pdf, storage as s

CATEGORIES = {"overview": "研究问题与章节逻辑", "method": "方法与结构",
              "formula": "公式与推导", "experiment": "实验与消融",
              "contribution": "贡献与创新", "limitation": "局限与疑问",
              "implementation": "实现与复现"}
KINDS = {"reported", "interpretation", "proposal"}
KIND_LABELS = {"reported": "原文报告", "interpretation": "分析解释", "proposal": "待验证建议"}


def now():
    return datetime.now(timezone.utc).isoformat()


def progress(stage, done, total):
    print(json.dumps({"stage": stage, "done": done, "total": total}, ensure_ascii=False), file=sys.stderr, flush=True)


def location(job_id):
    return s.root() / "reader-jobs" / s.identifier(job_id)


def load(job_id):
    directory = location(job_id)
    manifest = s.read(directory / "manifest.json")
    if not manifest or manifest.get("schema") != SCHEMA:
        raise ValueError(f"Unknown or unsupported reading job: {job_id}")
    return directory, manifest


def select(api, args):
    if getattr(args, "selected", False):
        response = api.local_write("/api/dsh-reader/selection", {}, method="POST")
        if response.status == 404:
            raise ValueError("Install the bundled Zotero companion first: node lib/reader-runner.js bridge --out dsh-reader.xpi")
        api.require_ok(response, "Read Zotero selection")
        rows = api.parse_body(response)
        if not isinstance(rows, list):
            raise ValueError("Invalid response from Zotero companion")
        return rows
    return [{"item": item, "api_prefix": api.LOCAL_USER} for item in api.selected_items(args)]


def local_pdf(api, entry, attachment_key=None):
    item, prefix = entry["item"], entry["api_prefix"]
    data = item.get("data", item)
    key = item.get("key") or data.get("key")
    if data.get("itemType") == "attachment":
        attachments = [item]
    else:
        attachments = api.api_get(f"{prefix}/items/{urllib.parse.quote(key)}/children")
    candidates = []
    for attachment in attachments:
        record = attachment.get("data", attachment)
        akey = attachment.get("key") or record.get("key")
        if attachment_key and akey != attachment_key:
            continue
        if record.get("contentType") != "application/pdf" and not str(record.get("filename", "")).lower().endswith(".pdf"):
            continue
        raw = api.api_get(f"{prefix}/items/{urllib.parse.quote(akey)}/file/view/url")
        url = urllib.parse.urlparse(str(raw).strip())
        if url.scheme != "file" or url.netloc not in {"", "localhost"}:
            continue
        path = Path(urllib.parse.unquote(url.path))
        if sys.platform == "win32" and re.match(r"^/[A-Za-z]:", str(path)):
            path = Path(str(path)[1:])
        if path.is_file():
            candidates.append((path.stat().st_size, akey, path))
    if not candidates:
        raise ValueError(f"No local PDF for {key}; download its attachment in Zotero first")
    if len(candidates) > 1 and not attachment_key:
        raise ValueError(f"Several PDF attachments for {key}; choose --attachment from {[row[1] for row in candidates]}")
    _, akey, path = candidates[0]
    return key, akey, path


def prepare(api, args):
    settings = s.settings({k: getattr(args, k, None) for k in s.DEFAULTS})
    if not settings["outputs"]:
        raise ValueError("Choose outputs with the user first, then pass --outputs (or save reader-config)")
    profile = s.profile(settings["profile"])
    rows = select(api, args)
    if not rows:
        raise ValueError("No Zotero items selected")
    completed, failures = [], []
    for entry in rows:
        try:
            completed.append(prepare_one(api, entry, settings, profile, args))
        except (ValueError, OSError) as error:
            failures.append({"item": entry["item"].get("key"), "error": str(error)})
    return {"jobs": completed, "failures": failures}


def prepare_one(api, entry, settings, profile, args):
    started = time.monotonic()
    key, attachment_key, source = local_pdf(api, entry, getattr(args, "attachment", None))
    source_hash = s.file_hash(source)
    cache_key = s.digest({"source": source_hash, "parser": SCHEMA, "ocr": args.ocr})
    cached = s.root() / "reader-cache" / (cache_key + ".json")
    document = s.read(cached)
    cache_hit = document is not None
    if document is None:
        document = pdf.extract(source, ocr=args.ocr, progress=progress)
        s.write(cached, document)
    identity = {"source": source_hash, "settings": settings, "profile": profile,
                "key": key, "prefix": entry["api_prefix"], "parser": SCHEMA, "ocr": args.ocr}
    # Presentation and execution choices must not cause fresh model generation.
    analysis_key = s.digest({"document": cache_key, "profile": profile,
                             "mode": settings["mode"], "depth": settings["depth"]})
    job_id = "read-" + s.digest(identity)[:24]
    directory = location(job_id)
    with s.lock(directory):
        existing = s.read(directory / "manifest.json")
        if existing is None:
            manifest = {"schema": SCHEMA, "id": job_id, "created_at": now(),
                        "source": str(source), "source_hash": source_hash, "document": str(cached),
                        "item_key": key, "attachment_key": attachment_key, "api_prefix": entry["api_prefix"],
                        "title": entry["item"].get("data", entry["item"]).get("title") or key,
                        "settings": settings, "profile": profile, "profile_hash": s.digest(profile),
                        "result_store": str(s.root() / "reader-results" / analysis_key),
                        "prepare_seconds": round(time.monotonic() - started, 3)}
            s.write(directory / "manifest.json", manifest)
        else:
            manifest = existing
        tasks = build_tasks(directory, manifest, document)
    status = inspect(job_id)
    # Estimates use measured throughput only. A first job gets workload, not a fictitious ETA.
    return {"id": job_id, "path": str(directory), "cache_hit": cache_hit,
            "pages": len(document["pages"]), "characters": sum(len(b["text"]) for b in document["blocks"]),
            "execution": settings["execution"], "max_workers": 1 if settings["execution"] == "economy" else settings["max_workers"],
            "tasks": tasks, "status": status, "warnings": document["warnings"],
            "timing": {"prepare_seconds": round(time.monotonic() - started, 3),
                       "target": "10-15 text pages: first useful result within 60s; all selected outputs in 180-300s; unverified target",
                       "estimate": "No calibrated model/OCR throughput yet; measure this run before estimating completion"}}


def build_tasks(directory, manifest, document):
    tasks, batch, chars = [], [], 0
    for block in document["blocks"]:
        if batch and chars + len(block["text"]) > 9000:
            tasks.append(batch)
            batch, chars = [], 0
        batch.append({k: block[k] for k in ("id", "page", "text", "kind", "translate", "ocr")})
        chars += len(block["text"])
    if batch:
        tasks.append(batch)
    result = []
    for index, batch in enumerate(tasks, 1):
        name = f"chunk-{index:04}.json"
        path = directory / "tasks" / name
        s.write(path, {"job_id": manifest["id"], "source_hash": manifest["source_hash"],
                       "profile": manifest["profile"], "settings": manifest["settings"], "blocks": batch})
        result.append(str(path))
    prompt = Path(__file__).resolve().parents[2] / "references" / "reading-workflow.md"
    return {"chunks": result, "instructions": str(prompt), "results": str(result_store(directory))}


def result_store(directory):
    manifest = s.read(directory / "manifest.json", {})
    return Path(manifest.get("result_store", directory / "results"))


def aggregate(directory):
    translations, claims, missing = {}, {}, {}
    for path in sorted(result_store(directory).glob("*.json")):
        record = s.read(path)
        for row in record.get("translations", []):
            old = translations.get(row["block_id"])
            if old is not None and old != row["text"]:
                raise ValueError(f"Conflicting translations for {row['block_id']}")
            translations[row["block_id"]] = row["text"]
        for row in record.get("claims", []):
            if row["id"] in claims and claims[row["id"]] != row:
                raise ValueError(f"Conflicting claim id: {row['id']}")
            claims[row["id"]] = row
        missing.update(record.get("missing", {}))
    return translations, list(claims.values()), missing


def validate(record, manifest, document):
    if not isinstance(record, dict) or record.get("source_hash") != manifest["source_hash"]:
        raise ValueError("Analysis must identify the exact source_hash from this job")
    blocks = {b["id"]: b for b in document["blocks"]}
    translations, claims, missing = record.get("translations", []), record.get("claims", []), record.get("missing", {})
    if not isinstance(translations, list) or not isinstance(claims, list) or not isinstance(missing, dict):
        raise ValueError("translations/claims must be lists and missing must be an object")
    ids = set()
    for row in translations:
        if not isinstance(row, dict) or row.get("block_id") not in blocks:
            raise ValueError("Translation refers to an unknown block")
        if row["block_id"] in ids:
            raise ValueError("Duplicate translation block")
        ids.add(row["block_id"])
        if not isinstance(row.get("text"), str) or len(row["text"].strip()) < 2:
            raise ValueError("Translation is empty")
    ids = set()
    for claim in claims:
        if not isinstance(claim, dict):
            raise ValueError("Each claim must be an object")
        s.identifier(claim.get("id"))
        if claim["id"] in ids:
            raise ValueError("Duplicate claim id")
        ids.add(claim["id"])
        if claim.get("kind") not in KINDS or claim.get("category") not in CATEGORIES:
            raise ValueError("Claim needs an explicit kind and category")
        if not isinstance(claim.get("statement"), str) or len(claim["statement"].strip()) < 12:
            raise ValueError("Claim statement is too short")
        evidence = claim.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("Every claim, including a proposal, needs source evidence")
        for ref in evidence:
            if not isinstance(ref, dict) or ref.get("block_id") not in blocks:
                raise ValueError("Evidence refers to an unknown block")
            quote = ref.get("quote")
            if not isinstance(quote, str) or len(pdf.norm(quote)) < 12:
                raise ValueError("Provide a substantial exact source quote (at least 12 characters)")
            if pdf.norm(quote) not in pdf.norm(blocks[ref["block_id"]]["text"]):
                raise ValueError(f"Quote not found in {ref['block_id']}")
            pdf.quote_boxes(blocks[ref["block_id"]], quote)
    for category, reason in missing.items():
        if category not in CATEGORIES or not isinstance(reason, str) or len(reason.strip()) < 12:
            raise ValueError("Missing coverage needs a category and a concrete explanation")
    return {"source_hash": record["source_hash"], "translations": translations, "claims": claims, "missing": missing}


def accept(job_id, record):
    directory, manifest = load(job_id)
    document = s.read(manifest["document"])
    result = validate(record, manifest, document)
    with s.lock(directory), s.lock(result_store(directory)):
        previous_t, previous_c, _ = aggregate(directory)
        old_claims = {c["id"]: c for c in previous_c}
        for row in result["translations"]:
            if row["block_id"] in previous_t and previous_t[row["block_id"]] != row["text"]:
                raise ValueError(f"Existing accepted translation differs for {row['block_id']}; create a new run/profile to revise")
        for row in result["claims"]:
            if row["id"] in old_claims and old_claims[row["id"]] != row:
                raise ValueError("Accepted claim ids are immutable; use a new claim id for an additional finding")
        s.write(result_store(directory) / (s.digest(result) + ".json"), result)
    return inspect(job_id)


def inspect(job_id):
    directory, manifest = load(job_id)
    document = s.read(manifest["document"])
    translations, claims, missing = aggregate(directory)
    outputs = set(manifest["settings"]["outputs"])
    needs_translation = bool(outputs & {"translation", "bilingual-pdf"})
    untranslated = [b["id"] for b in document["blocks"] if b["translate"] and b["id"] not in translations] if needs_translation else []
    categories = set(CATEGORIES) if manifest["settings"]["mode"] == "deep" else {"overview", "method", "experiment", "contribution", "limitation"}
    absent = sorted(categories - {c["category"] for c in claims} - set(missing)) if outputs & {"notes", "zotero-note", "annotated-pdf"} else []
    unreadable = [p["number"] for p in document["pages"] if not p["readable"]]
    return {"id": job_id, "translations": len(translations), "claims": len(claims),
            "untranslated_blocks": untranslated, "uncovered_categories": absent,
            "unreadable_pages": unreadable, "ready": not (untranslated or absent or unreadable),
            "warnings": document["warnings"]}


def source_link(manifest, page):
    prefix = manifest["api_prefix"]
    library = "library" if prefix == "/api/users/0" else "groups/" + prefix.split("/")[-1]
    return f"zotero://open-pdf/{library}/items/{manifest['attachment_key']}?page={page}"


def markdown(manifest, document, claims, missing):
    blocks = {b["id"]: b for b in document["blocks"]}
    lines = ["# " + manifest["title"], "", f"- 阅读任务：`{manifest['id']}`",
             f"- 原文指纹：`{manifest['source_hash']}`", f"- 研究问题：{manifest['profile'].get('question') or '通用阅读'}",
             "", "原文报告、分析解释和待验证建议分别标记；引文定位校验不等于科学结论已获验证。", ""]
    for category, title in CATEGORIES.items():
        lines += ["## " + title, ""]
        found = [c for c in claims if c["category"] == category]
        for claim in found:
            lines += [f"### {claim['id']} · {KIND_LABELS[claim['kind']]}", "", claim["statement"], ""]
            for ref in claim["evidence"]:
                page = blocks[ref["block_id"]]["page"]
                lines += [f"> {ref['quote']}", "", f"依据：[原文第 {page} 页]({source_link(manifest, page)}) · `{ref['block_id']}`", ""]
        if not found:
            lines += [missing.get(category, "本轮尚未分析此项。"), ""]
    if document["warnings"]:
        lines += ["## 解析注意事项", ""] + ["- " + w for w in document["warnings"]]
    return "\n".join(lines) + "\n"


def export(job_id, output_root, *, partial=False):
    directory, manifest = load(job_id)
    if s.file_hash(manifest["source"]) != manifest["source_hash"]:
        raise ValueError("The PDF changed after preparation; prepare a new reading job")
    state = inspect(job_id)
    if not state["ready"] and not partial:
        raise ValueError("Job is incomplete; inspect remaining blocks/categories before export (or explicitly use --partial for Markdown only)")
    document = s.read(manifest["document"])
    translations, claims, missing = aggregate(directory)
    outputs = set(manifest["settings"]["outputs"])
    if not partial and outputs & {"annotated-pdf", "bilingual-pdf"}:
        pdf.dependencies()
        if "bilingual-pdf" in outputs:
            pdf.font_name()
    revision = s.digest({"translations": translations, "claims": claims, "missing": missing})[:12]
    base = Path(output_root).expanduser().resolve() / job_id
    base.mkdir(parents=True, exist_ok=True)
    target = base / revision
    counter = 1
    while target.exists():
        target = base / f"{revision}-{counter}"
        counter += 1
    target.mkdir()
    files = []
    if outputs & {"notes", "zotero-note"}:
        path = target / ("notes.partial.md" if partial else "notes.md")
        path.write_text(markdown(manifest, document, claims, missing), encoding="utf-8")
        files.append(str(path))
    if outputs & {"translation", "bilingual-pdf"}:
        lines = ["# 全文译文" + ("（未完成）" if not state["ready"] else ""), ""]
        for block in document["blocks"]:
            text = translations.get(block["id"])
            if text:
                lines += [f"<!-- {block['id']} · 原文第 {block['page']} 页 -->", text, ""]
            elif block["translate"]:
                lines += [f"**尚未翻译：{block['id']}**", ""]
        path = target / ("translation.partial.md" if partial else "translation.md")
        path.write_text("\n".join(lines), encoding="utf-8")
        files.append(str(path))
    if not partial and outputs & {"annotated-pdf", "bilingual-pdf"}:
        pdf.dependencies()
        if "annotated-pdf" in outputs:
            path = target / "annotated.pdf"
            pdf.annotate(manifest["source"], path, document, claims, detailed=manifest["settings"]["annotation"] == "detailed")
            files.append(str(path))
        if "bilingual-pdf" in outputs:
            path = target / "bilingual.pdf"
            pdf.bilingual(manifest["source"], path, document, translations, manifest["settings"]["layout"])
            files.append(str(path))
    s.write(target / "provenance.json", {"manifest": manifest, "status": state, "claims": claims,
                                         "translation_blocks": sorted(translations), "exported_at": now()})
    return {"files": files, "directory": str(target), "status": state}


def publish_note(api, job_id):
    directory, manifest = load(job_id)
    if not inspect(job_id)["ready"]:
        raise ValueError("Complete and validate the job before publishing a Zotero note")
    if s.file_hash(manifest["source"]) != manifest["source_hash"]:
        raise ValueError("Source PDF changed; prepare a new job")
    document = s.read(manifest["document"])
    _, claims, missing = aggregate(directory)
    text = markdown(manifest, document, claims, missing)
    # Escape model text. Only tool-created, validated Zotero links become HTML links.
    html = []
    for line in text.splitlines():
        safe = escape(line)
        safe = re.sub(r"\[([^\]]+)\]\((zotero://open-pdf/[A-Za-z0-9_/?=&-]+)\)", r'<a href="\2">\1</a>', safe)
        if line.startswith("#"):
            level = min(3, len(line) - len(line.lstrip("#")))
            safe = f"<h{level}>" + safe.lstrip("# ") + f"</h{level}>"
        else:
            safe = "<p>" + safe + "</p>"
        html.append(safe)
    receipt = directory / "published" / (s.digest({"claims": claims, "missing": missing}) + ".json")
    with s.lock(directory):
        previous = s.read(receipt)
        if previous:
            return dict(previous, reused=True)
        response = api.local_write(f"{manifest['api_prefix']}/items", [{"itemType": "note", "parentItem": manifest["item_key"], "note": "\n".join(html)}], method="POST")
        api.require_ok(response, "Create reading note")
        key = api.created_key(api.parse_body(response))
        if not key:
            raise ValueError("Zotero did not confirm a note key; inspect the API response before retrying")
        result = {"note_key": key, "parent_item": manifest["item_key"], "source_hash": manifest["source_hash"]}
        s.write(receipt, result)
        return result
