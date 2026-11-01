"""Small deterministic translation checks; no inference or extra dependencies."""
from collections import Counter
from decimal import Decimal, InvalidOperation
import re
from .pdf import norm


def profile_fields(record):
    if not isinstance(record, dict):
        raise ValueError("Research profile must be an object")
    glossary = record.get("glossary", [])
    if not isinstance(glossary, list) or len(glossary) > 200:
        raise ValueError("glossary must contain at most 200 source/target pairs")
    seen = set()
    for row in glossary:
        if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not 1 <= len(row[k].strip()) <= 120 for k in ("source", "target")):
            raise ValueError("Each glossary entry needs source and target strings (1-120 characters)")
        key = row["source"].casefold()
        if key in seen:
            raise ValueError("Duplicate glossary source term")
        seen.add(key)
    columns = record.get("comparison_columns", [])
    if not isinstance(columns, list) or len(columns) > 20 or not all(isinstance(c, str) and 1 <= len(c.strip()) <= 80 for c in columns):
        raise ValueError("comparison_columns must be at most 20 short field names")
    if len(set(columns)) != len(columns):
        raise ValueError("comparison_columns must be distinct")
    return record


def numbers(text):
    text = norm(text).replace("−", "-")
    text = re.sub(r"(?<=\d)[–—-](?=\d)", " ", text)
    pattern = r"(?<![A-Za-z0-9_.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?\s*%?"
    values = []
    for match in re.finditer(pattern, text):
        raw = match.group().strip().replace(",", "")
        try:
            value = Decimal(raw.rstrip("%").strip())
            if raw.endswith("%"):
                value /= 100
            values.append(str(value.normalize()))
        except InvalidOperation:
            continue
    return Counter(values)


def audit(document, translations, profile=None):
    issues = []
    for block in document["blocks"]:
        translated = translations.get(block["id"])
        if translated is None:
            continue
        source = block["text"]
        original_values, translated_values = numbers(source), numbers(translated)
        if original_values != translated_values:
            issues.append({"block_id": block["id"], "code": "number_mismatch", "severity": "review",
                           "missing": list((original_values - translated_values).elements()),
                           "added": list((translated_values - original_values).elements()),
                           "message": "数字出现增减或变化；核对引用号、单位换算与实验结果，不自动认定为错误。"})
        if len(source) > 100 and norm(source) == norm(translated) and re.search(r"[A-Za-z]{4}", source):
            issues.append({"block_id": block["id"], "code": "unchanged_text", "severity": "review",
                           "message": "长段译文与原文相同，请核对是否仍未翻译。"})
        for term in (profile or {}).get("glossary", []):
            pattern = re.escape(term["source"])
            if re.fullmatch(r"[A-Za-z0-9 _-]+", term["source"]):
                pattern = r"(?<!\w)" + pattern + r"(?!\w)"
            if re.search(pattern, source, re.I) and term["target"].casefold() not in translated.casefold():
                issues.append({"block_id": block["id"], "code": "glossary_missing", "severity": "review",
                               "source": term["source"], "expected": term["target"],
                               "message": "译文未使用课题术语表中的预期译法。"})
    return issues
