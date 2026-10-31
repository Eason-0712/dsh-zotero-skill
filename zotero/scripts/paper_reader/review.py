"""Corpus synthesis uses accepted evidence, not metadata-based quality rankings."""
from pathlib import Path
from . import storage as s, jobs, evidence as source_evidence


def prepare(job_ids, profile_name, output):
    papers, warnings = [], []
    for job_id in job_ids:
        directory, manifest = jobs.load(job_id)
        if s.file_hash(manifest["source"]) != manifest["source_hash"]:
            raise ValueError(f"Stale source for {job_id}; read the changed PDF first")
        _, claims, missing = jobs.aggregate(directory)
        if not claims:
            warnings.append(f"{job_id}: no accepted reading evidence yet")
        papers.append({"job_id": job_id, "title": manifest["title"], "source_hash": manifest["source_hash"],
                       "reading_revision": jobs.result_revision(directory),
                       "claims": claims, "missing": missing, "status": jobs.inspect(job_id),
                       "manifest": manifest})
    if not papers:
        raise ValueError("Choose at least one reading job")
    corpus = {"schema": 1, "profile": s.profile(profile_name), "papers": papers, "warnings": warnings}
    corpus["fingerprint"] = s.digest(corpus)
    path = Path(output).expanduser().resolve()
    if path.exists():
        raise ValueError("Corpus input already exists; choose a new path to preserve edits")
    s.write(path, corpus)
    return {"input": str(path), "papers": len(papers), "warnings": warnings,
            "instructions": str(Path(__file__).resolve().parents[2] / "references" / "review-workflow.md")}


def render(corpus, analysis, output):
    original = dict(corpus)
    original.pop("fingerprint", None)
    if s.digest(original) != corpus.get("fingerprint"):
        raise ValueError("Prepared corpus was modified; prepare it again")
    if analysis.get("fingerprint") != corpus.get("fingerprint"):
        raise ValueError("Review does not match the prepared corpus")
    source_evidence.check(corpus["papers"])
    known = {(p["job_id"], c["id"]): (p, c) for p in corpus["papers"] for c in p["claims"]}
    lines = ["# 文献比较与研究综述", "", "研究问题：" + (corpus["profile"].get("question") or "未指定"), "",
             "本报告的覆盖范围仅限列出的论文；候选空白不代表整个领域没有研究。", ""]
    for key, title in [("comparisons", "跨论文比较"), ("development", "方法发展与联系"),
                       ("gaps", "候选空白与验证方向"), ("reading_order", "建议阅读顺序与理由")]:
        entries = analysis.get(key)
        if not isinstance(entries, list):
            raise ValueError(f"Review needs a {key} list (use an empty list with an explicit corpus limitation if unsupported)")
        lines += ["## " + title, ""]
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("statement"), str) or len(entry["statement"]) < 12:
                raise ValueError("Review statements must contain substantive text")
            if entry.get("kind") not in {"synthesis", "hypothesis"} or (key == "gaps" and entry["kind"] != "hypothesis"):
                raise ValueError("Review must label synthesis and hypothesis; research gaps are hypotheses")
            evidence = entry.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise ValueError("Review findings need accepted paper/claim references")
            refs = []
            for ref in evidence:
                pair = (ref.get("job_id"), ref.get("claim_id"))
                if pair not in known:
                    raise ValueError(f"Unknown corpus evidence: {pair}")
                paper, claim = known[pair]
                if claim["kind"] == "proposal" and entry["kind"] != "hypothesis":
                    raise ValueError("An untested single-paper proposal cannot become an established synthesis")
                directory, manifest = jobs.load(paper["job_id"])
                document = s.read(manifest["document"])
                block = next(b for b in document["blocks"] if b["id"] == claim["evidence"][0]["block_id"])
                refs.append(f"[{paper['title']} · {claim['id']}]({jobs.source_link(manifest, block['page'])})")
            lines += [f"- **{'待验证假设' if entry['kind'] == 'hypothesis' else '综合分析'}**：{entry['statement']}",
                      "  依据：" + "；".join(refs), ""]
    limitations = analysis.get("limitations")
    queries = analysis.get("search_queries")
    if not isinstance(limitations, list) or not limitations or not all(isinstance(v, str) and v.strip() for v in limitations):
        raise ValueError("Describe missing evidence and corpus limitations explicitly")
    if not isinstance(queries, list) or not queries or not all(isinstance(q, str) and q.strip() for q in queries):
        raise ValueError("Provide targeted search_queries to investigate missing evidence")
    lines += ["## 已有文献的不足", ""] + ["- " + text for text in limitations]
    lines += ["", "## 自动补充检索的查询", ""] + ["- " + q for q in queries]
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise ValueError("Review output already exists; choose a new path")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"markdown": str(output), "search_queries": queries}


def search(api, queries, profile_name, limit=12):
    profile = profile_name if isinstance(profile_name, dict) else s.profile(profile_name)
    from datetime import date
    candidates = api.gather_candidates(queries, "1900-01-01",
                                       date.today().isoformat(), include_preprints=True)
    keywords = profile.get("keywords", [])
    for row in candidates:
        text = (row.get("title", "") + " " + row.get("abstract", "")).casefold()
        matched = [word for word in keywords if word.casefold() in text]
        row["matched_profile_terms"] = matched
        row["recommendation_reason"] = ("题名或摘要命中研究关键词：" + ", ".join(matched)) if matched else "由针对缺失证据生成的查询检索到，需进一步核对课题关联度"
        row["evidence_level"] = "metadata_and_abstract" if row.get("abstract") else "metadata_only"
        row["needs_fulltext_review"] = True
        row["quality_assessment"] = "创新性、可实践性与实验可信度尚需全文精读；期刊和被引不是结论"
    candidates.sort(key=lambda r: (len(r["matched_profile_terms"]), r.get("score", 0)), reverse=True)
    return {"queries": queries, "profile": profile, "recommendations": candidates[:limit],
            "network_or_coverage_warning": "Empty results may reflect network/API errors or limited retrieval coverage; no completeness claim",
            "next_step": "Have the host model assess relation, innovation and learnability; show reasons and missing evidence before importing"}
