"""User-defined evidence tables, without a spreadsheet service or UI."""
import csv
import io
from pathlib import Path
from . import evidence, jobs, storage as s

DEFAULT_COLUMNS = ["研究问题", "方法", "数据与划分", "实验结果", "复现条件", "局限"]
STATUSES = {"reported", "interpretation", "missing", "not_comparable"}
STATUS_LABELS = {"reported": "原文报告", "interpretation": "分析解释", "missing": "未报告", "not_comparable": "不可直接比较"}


def prepare(job_ids, profile_name, columns, output):
    papers = evidence.snapshot(job_ids)
    profile = s.profile(profile_name)
    columns = columns or profile.get("comparison_columns") or DEFAULT_COLUMNS
    if not isinstance(columns, list) or not 1 <= len(columns) <= 20 or len(set(columns)) != len(columns) or not all(isinstance(c, str) and 1 <= len(c.strip()) <= 80 for c in columns):
        raise ValueError("Choose 1-20 distinct, short comparison columns")
    readings = []
    for paper in papers:
        directory, _ = jobs.load(paper["job_id"])
        _, claims, missing = jobs.aggregate(directory)
        readings.append({"job_id": paper["job_id"], "claims": claims, "missing": missing})
    context = evidence.seal({"schema": 1, "task": "comparison_matrix", "papers": papers, "profile": profile,
                             "columns": columns, "readings": readings,
                             "instruction": "Every cell needs an evidence status; compare metrics only under compatible protocols"})
    evidence.write_new(output, context)
    return {"context": str(Path(output).resolve()), "columns": columns, "papers": len(papers)}


def csv_safe(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r")) else value


def render(context, result, output_prefix):
    evidence.verify(context)
    if not isinstance(result, dict) or result.get("fingerprint") != context["fingerprint"]:
        raise ValueError("Matrix result does not match its context")
    rows = result.get("rows")
    papers = {p["job_id"]: p for p in context["papers"]}
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows) or len(rows) != len(papers) or {r.get("job_id") for r in rows} != set(papers):
        raise ValueError("Matrix must include each selected paper exactly once")
    claims = {r["job_id"]: {c["id"]: c for c in r["claims"]} for r in context["readings"]}
    header = ["论文", *context["columns"]]
    table, sources = [], []
    for row in rows:
        key = row["job_id"]
        cells = row.get("cells")
        if not isinstance(cells, dict) or set(cells) != set(context["columns"]):
            raise ValueError("Every paper needs every requested column, including explicit missing values")
        values = [papers[key]["title"]]
        for column in context["columns"]:
            cell = cells[column]
            if not isinstance(cell, dict) or cell.get("status") not in STATUSES or not isinstance(cell.get("value"), str) or not cell["value"].strip():
                raise ValueError("Each matrix cell needs a value and an evidence status")
            refs = cell.get("claim_ids", [])
            if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
                raise ValueError("Cell claim_ids must be a list of reading claim identifiers")
            if cell["status"] in {"reported", "interpretation"} and not refs:
                raise ValueError("Reported and interpreted matrix cells require source claims")
            links = []
            for claim_id in refs:
                claim = claims[key].get(claim_id)
                if claim is None:
                    raise ValueError("Matrix cell cites a claim outside its paper")
                if cell["status"] == "reported" and claim["kind"] != "reported":
                    raise ValueError("A proposal or interpretation cannot be relabelled as a reported finding")
                if cell["status"] == "interpretation" and claim["kind"] == "proposal":
                    raise ValueError("Untested proposals belong in a separate hypotheses section")
                ref = dict(claim["evidence"][0], job_id=key)
                location = evidence.reference(ref, papers)
                links.append(f"[依据 {len(links) + 1}]({location['url']})")
                sources.append({"job_id": key, "column": column, "claim_id": claim_id,
                                "kind": claim["kind"], "evidence": claim["evidence"]})
            values.append(f"[{STATUS_LABELS[cell['status']]}] {cell['value']}" + (" · " + "; ".join(links) if links else ""))
        table.append(values)
    lines = ["# 文献证据对比表", "", "不同数据集、划分和指标不可直接排名；缺失信息与不可比结果分别标记。", "",
             "| " + " | ".join(evidence.md(c) for c in header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    # Preserve generated source links; model text and pipe characters are escaped separately.
    for row in table:
        lines.append("| " + " | ".join(evidence.md(c).replace("\n", " ") for c in row) + " |")
    csv_stream = io.StringIO(newline="")
    writer = csv.writer(csv_stream)
    writer.writerow(header)
    writer.writerows([[csv_safe(c) for c in row] for row in table])
    base = str(Path(output_prefix).expanduser().resolve())
    outputs = {base + ".md": "\n".join(lines) + "\n", base + ".csv": csv_stream.getvalue(),
               base + ".evidence.json": {"fingerprint": context["fingerprint"], "sources": sources}}
    if any(Path(path).exists() for path in outputs):
        raise ValueError("Matrix output already exists; choose a new prefix")
    for path, value in outputs.items():
        evidence.write_new(path, value)
    return {"files": list(outputs), "rows": len(rows), "columns": len(context["columns"])}
