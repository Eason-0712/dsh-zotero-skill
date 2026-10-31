"""Scoped evidence snapshots and lexical retrieval using Python's SQLite only."""
from contextlib import closing
from collections import Counter
import json
from pathlib import Path
import re
import sqlite3
from . import jobs, pdf, storage as s


def snapshot(job_ids):
    ids = list(dict.fromkeys(job_ids))
    if not ids or len(ids) > 100:
        raise ValueError("Choose 1-100 explicit reading jobs")
    papers = []
    for job_id in ids:
        directory, manifest = jobs.load(job_id)
        if s.file_hash(manifest["source"]) != manifest["source_hash"]:
            raise ValueError(f"Source changed for {job_id}; prepare it again")
        papers.append({"job_id": job_id, "title": manifest["title"], "source_hash": manifest["source_hash"],
                       "reading_revision": jobs.result_revision(directory)})
    return papers


def check(papers):
    live = {p["job_id"]: p for p in snapshot([p["job_id"] for p in papers])}
    for paper in papers:
        for field in ("source_hash", "reading_revision"):
            if live[paper["job_id"]][field] != paper[field]:
                raise ValueError("Evidence changed since this task was prepared; prepare a fresh context")


def seal(record):
    result = dict(record)
    result["fingerprint"] = s.digest(record)
    return result


def verify(record):
    if not isinstance(record, dict):
        raise ValueError("Expected an evidence context object")
    raw = dict(record)
    fingerprint = raw.pop("fingerprint", None)
    if s.digest(raw) != fingerprint:
        raise ValueError("Evidence context was modified")
    check(record["papers"])


def reference(ref, papers, allowed=None):
    if not isinstance(ref, dict) or ref.get("job_id") not in papers:
        raise ValueError("Unknown paper in evidence reference")
    directory, manifest = jobs.load(ref["job_id"])
    document = s.read(manifest["document"])
    block = next((b for b in document["blocks"] if b["id"] == ref.get("block_id")), None)
    quote = ref.get("quote")
    if block is None or not isinstance(quote, str) or len(pdf.norm(quote)) < 12 or pdf.norm(quote) not in pdf.norm(block["text"]):
        raise ValueError("Quote does not resolve to the selected paper and block")
    if allowed is not None and pdf.norm(quote) not in pdf.norm(allowed.get((ref["job_id"], ref["block_id"]), "")):
        raise ValueError("Answer cites text outside the retrieved context")
    return {"page": block["page"], "url": jobs.source_link(manifest, block["page"]), "title": manifest["title"]}


def tokens(text):
    result = re.findall(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", text.casefold())
    stop = {"the", "and", "for", "with", "what", "how", "this", "that", "from", "are", "does", "paper"}
    result = [t for t in result if t not in stop]
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        result += [run[i:i + 2] for i in range(max(1, len(run) - 1))]
    return result


def retrieve(papers, query, profile, limit=8):
    terms = tokens(query)
    for term in profile.get("glossary", []):
        if term["target"].casefold() in query.casefold():
            terms.extend(tokens(term["source"]))
    terms = list(dict.fromkeys(terms))[:32]
    if not terms:
        return [], "no_query_terms"
    documents = {}
    for paper in papers:
        directory, manifest = jobs.load(paper["job_id"])
        documents[paper["job_id"]] = s.read(manifest["document"])
    path = s.root() / "reader-index.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(sqlite3.connect(path, timeout=10)) as db:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS indexed (job_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)")
                db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(job_id UNINDEXED, block_id UNINDEXED, words)")
                for paper in papers:
                    key = paper["job_id"]
                    fingerprint = s.digest({"source": paper["source_hash"], "document": s.digest(documents[key]), "tokenizer": 1})
                    old = db.execute("SELECT fingerprint FROM indexed WHERE job_id=?", (key,)).fetchone()
                    if old and old[0] == fingerprint:
                        continue
                    db.execute("DELETE FROM passages WHERE job_id=?", (key,))
                    db.executemany("INSERT INTO passages(job_id,block_id,words) VALUES(?,?,?)",
                                   [(key, b["id"], " ".join(tokens(b["text"]))) for b in documents[key]["blocks"]])
                    db.execute("INSERT OR REPLACE INTO indexed VALUES(?,?)", (key, fingerprint))
                match = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
                placeholders = ",".join("?" for _ in papers)
                rows = db.execute(f"SELECT job_id,block_id FROM passages WHERE passages MATCH ? AND job_id IN ({placeholders}) ORDER BY bm25(passages),job_id,block_id LIMIT ?",
                                  (match, *[p["job_id"] for p in papers], limit)).fetchall()
                mode = "sqlite_fts5"
    except sqlite3.OperationalError as error:
        if "no such module: fts5" not in str(error):
            raise ValueError(f"Local evidence index unavailable: {error}; the derived index may be rebuilt without deleting reading results")
        scored = []
        for key, doc in documents.items():
            for block in doc["blocks"]:
                counts = Counter(tokens(block["text"]))
                score = sum(counts[t] for t in terms)
                if score:
                    scored.append((score, key, block["id"]))
        rows = [(key, block) for _, key, block in sorted(scored, reverse=True)[:limit]]
        mode = "stdlib_lexical_fallback"
    # Existing Chinese explanations may point to a passage missed by English keywords.
    for paper in papers:
        directory, _ = jobs.load(paper["job_id"])
        _, claims, _ = jobs.aggregate(directory)
        for claim in claims:
            if set(terms) & set(tokens(claim["statement"])):
                for ref in claim["evidence"]:
                    pair = (paper["job_id"], ref["block_id"])
                    if pair not in rows:
                        rows.append(pair)
    blocks = {(key, b["id"]): b for key, doc in documents.items() for b in doc["blocks"]}
    return [(key, blocks[key, block]) for key, block in rows[:limit]], mode


def prepare_question(job_ids, question, output, *, query=None, profile_name="", max_chars=10000):
    if not isinstance(question, str) or not 3 <= len(question.strip()) <= 2000:
        raise ValueError("Ask a focused question (3-2000 characters)")
    if not 1500 <= max_chars <= 24000:
        raise ValueError("Context budget must be 1500-24000 characters")
    if query is not None and (not isinstance(query, str) or not 1 <= len(query.strip()) <= 2000):
        raise ValueError("Retrieval keywords must contain 1-2000 characters")
    papers = snapshot(job_ids)
    profile = s.profile(profile_name)
    found, mode = retrieve(papers, query or question, profile)
    passages, budget = [], max_chars
    for job_id, block in found:
        text = block["text"][:min(1800, budget)]
        if budget < 300:
            break
        passages.append({"job_id": job_id, "block_id": block["id"], "page": block["page"],
                         "text": text, "truncated": len(text) < len(block["text"]), "ocr": block["ocr"]})
        budget -= len(text)
    selected_text = (query or question) + " " + " ".join(p["text"] for p in passages)
    relevant_terms = [t for t in profile.get("glossary", []) if t["source"].casefold() in selected_text.casefold() or t["target"].casefold() in selected_text.casefold()][:20]
    profile_view = {"question": profile.get("question", "")[:2000],
                    "priorities": profile.get("priorities", [])[:8], "glossary": relevant_terms}
    context = seal({"schema": 1, "task": "focused_question", "question": question, "query": query or question,
                    "profile": profile_view, "papers": papers, "passages": passages,
                    "retrieval": mode, "coverage": "retrieved passages only; not exhaustive or semantic search"})
    write_new(output, context)
    return {"context": str(Path(output).resolve()), "passages": len(passages), "characters": max_chars - budget,
            "retrieval": mode, "next_step": "Use the current host model; answer with source quotes or state insufficient evidence"}


def answer(context, result, output):
    verify(context)
    if not isinstance(result, dict) or result.get("fingerprint") != context["fingerprint"]:
        raise ValueError("Answer does not match its question context")
    status = result.get("status")
    if status not in {"answered", "insufficient_evidence", "conflicting_evidence"}:
        raise ValueError("Answer needs an explicit evidence status")
    text = result.get("answer")
    refs, limitations = result.get("evidence", []), result.get("limitations", [])
    if not isinstance(text, str) or len(text.strip()) < 12 or not isinstance(refs, list):
        raise ValueError("Provide substantive answer text and an evidence list")
    if status != "insufficient_evidence" and not refs:
        raise ValueError("An affirmative answer needs source evidence")
    if status == "conflicting_evidence" and len({(r.get("job_id"), r.get("block_id"), r.get("quote")) for r in refs if isinstance(r, dict)}) < 2:
        raise ValueError("Conflicting evidence needs at least two distinct source quotations")
    if not isinstance(limitations, list) or not limitations or not all(isinstance(v, str) and v.strip() for v in limitations):
        raise ValueError("State the limits of the retrieved evidence")
    papers = {p["job_id"]: p for p in context["papers"]}
    allowed = {(p["job_id"], p["block_id"]): p["text"] for p in context["passages"]}
    label = {"answered": "已找到原文依据", "insufficient_evidence": "证据不足", "conflicting_evidence": "存在冲突证据"}[status]
    lines = ["# " + md(context["question"]), "", "证据状态：" + label, "", md(text), "", "## 原文依据", ""]
    for ref in refs:
        found = reference(ref, papers, allowed)
        lines += ["> " + md(ref["quote"]), "", f"[{md(found['title'])} · 第 {found['page']} 页]({found['url']})", ""]
    lines += ["## 范围与局限", ""] + ["- " + md(item) for item in limitations]
    write_new(output, "\n".join(lines) + "\n")
    return {"markdown": str(Path(output).resolve()), "status": status, "source_quotes": len(refs)}


def md(value):
    from html import escape
    return escape(str(value), quote=False).replace("|", "\\|")


def write_new(path, value):
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    # CSV already carries CRLF; Windows must not translate it to CR-CR-LF.
    with path.open("x", encoding="utf-8", newline="") as stream:
        stream.write(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
