"""Bounded one-hop citation discovery from OpenAlex, not a graph application."""
from . import evidence, jobs


def discover(api, job_ids, direction="both", limit=12):
    if direction not in {"references", "citations", "both"} or not 1 <= limit <= 30:
        raise ValueError("Choose references/citations/both and limit 1-30")
    selected = list(dict.fromkeys(job_ids))
    evidence.snapshot(selected)  # Check scope and source freshness before remote lookup.
    seeds, warnings, candidates = [], [], {}
    for job_id in selected[:5]:
        _, manifest = jobs.load(job_id)
        doi = manifest.get("doi")
        if not doi:
            record = api.api_get(f"{manifest['api_prefix']}/items/{manifest['item_key']}")
            data = record.get("data", record)
            doi = api.normalize_doi(data.get("DOI") or data.get("doi"))
        params = {"per-page": 3, "select": "id,title,doi,referenced_works"}
        params.update({"filter": "doi:https://doi.org/" + doi} if doi else {"search": manifest["title"]})
        response = api.openalex_get("/works", params, optional=True)
        matches = response.get("results", [])
        if not doi:
            matches = [m for m in matches if api.normalize_text(m.get("title")) == api.normalize_text(manifest["title"])]
        if len(matches) != 1:
            warnings.append({"job_id": job_id, "reason": "Seed ambiguous; verify DOI/title" if len(matches) > 1 else "Seed unavailable; check metadata, network and index coverage before citation chaining"})
            continue
        seed = matches[0]
        seeds.append({"job_id": job_id, "id": seed["id"], "title": seed["title"]})
        requests = []
        if direction in {"references", "both"} and seed.get("referenced_works"):
            if len(seed["referenced_works"]) > 30:
                warnings.append({"job_id": job_id, "omitted_references": len(seed["referenced_works"]) - 30,
                                 "reason": "Reference lookup is capped at 30 works per seed"})
            ids = [value.rsplit("/", 1)[-1] for value in seed["referenced_works"][:30]]
            requests.append(("seed_references_candidate", {"filter": "openalex_id:" + "|".join(ids), "per-page": 30}))
        if direction in {"citations", "both"}:
            requests.append(("candidate_cites_seed", {"filter": "cites:" + seed["id"].rsplit("/", 1)[-1], "per-page": limit, "sort": "publication_date:desc"}))
        for relation, request in requests:
            batch = api.openalex_get("/works", request, optional=True)
            if not batch.get("results"):
                warnings.append({"job_id": job_id, "relation": relation, "reason": "No results; network and index coverage may be incomplete"})
            for work in batch.get("results", []):
                key = work.get("id")
                if not key or key == seed["id"]:
                    continue
                row = candidates.setdefault(key, {"id": key, "doi": api.normalize_doi(work.get("doi")),
                    "title": work.get("title"), "published": work.get("publication_date"),
                    "is_retracted": work.get("is_retracted"), "evidence_level": "metadata_and_citation_relation",
                    "relations": [], "needs_fulltext_review": True})
                row["relations"].append({"seed_job": job_id, "seed_title": seed["title"], "kind": relation})
    if len(selected) > 5:
        warnings.append({"unprocessed_jobs": selected[5:], "reason": "One request handles at most 5 seeds; continue in batches"})
    result = sorted(candidates.values(), key=lambda r: (len({x["seed_job"] for x in r["relations"]}), r.get("published") or ""), reverse=True)[:limit]
    for row in result:
        row["recommendation_reason"] = "与已选论文存在明确参考/被引联系：" + "; ".join(r["seed_title"] for r in row["relations"])
        row["caution"] = "引用联系不表示支持、反驳或质量高低；需结合全文核验。"
        row["integrity_warning"] = "OpenAlex 标记已撤稿，不应作为可靠支持证据。" if row["is_retracted"] is True else "未在此查询中独立核实撤稿或更正状态。"
    return {"seeds": seeds, "recommendations": result, "warnings": warnings,
            "coverage": "One hop; capped seed/reference/citing lists, not a complete citation network"}
