#!/usr/bin/env python3
"""Operate Zotero Desktop's local API and connector server.

This helper is dependency-free on purpose: it runs with Python 3 stdlib only,
and Zotero calls default to the local Desktop HTTP surfaces on
http://127.0.0.1:23119.
"""

from __future__ import annotations

import argparse
import configparser
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = os.environ.get("ZOTERO_LOCAL_BASE_URL", "http://127.0.0.1:23119")
LOCAL_API_PREF = "extensions.zotero.httpServer.localAPI.enabled"
LOCAL_USER = "/api/users/0"
API_VERSION_HEADERS = {"Zotero-API-Version": "3"}
CONNECTOR_HEADERS = {"X-Zotero-Connector-API-Version": "3"}
TEXT_LIMIT = 300
API_PAGE_LIMIT = 100


@dataclass(frozen=True)
class Response:
    status: int | None
    headers: dict[str, str]
    text: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    @property
    def content_type(self) -> str:
        return self.headers.get("Content-Type", "")


def dump_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=False, ensure_ascii=False))


def exit_with(message: str) -> None:
    raise SystemExit(message)


def zotero_roots() -> list[Path]:
    home = Path.home()
    system = platform.system()
    roots: list[Path] = []

    if system == "Darwin":
        roots.append(home / "Library/Application Support/Zotero")
    elif system == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            roots.extend([Path(appdata) / "Zotero/Zotero", Path(appdata) / "Zotero"])
    else:
        roots.extend(
            [
                home / ".zotero/zotero",
                home / ".var/app/org.zotero.Zotero/data/zotero",
            ]
        )

    # Useful fallback when scripts run under shells whose platform config is odd.
    roots.append(home / "Library/Application Support/Zotero")
    return list(dict.fromkeys(roots))


def profiles_ini_path() -> Path | None:
    for root in zotero_roots():
        candidate = root / "profiles.ini"
        if candidate.exists():
            return candidate
    return None


def profile_dir() -> Path | None:
    override = os.environ.get("ZOTERO_PROFILE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    ini = profiles_ini_path()
    if ini is None:
        return None

    parser = configparser.RawConfigParser()
    parser.read(ini)
    root = ini.parent
    candidates: list[tuple[int, Path]] = []

    for section in parser.sections():
        if not section.lower().startswith("profile") or not parser.has_option(section, "Path"):
            continue
        raw_path = parser.get(section, "Path")
        path = (
            root / raw_path
            if parser.get(section, "IsRelative", fallback="1") == "1"
            else Path(raw_path)
        )
        score = 0
        if parser.get(section, "Default", fallback="0") == "1":
            score += 10
        if (path / "prefs.js").exists():
            score += 5
        candidates.append((score, path))

    if candidates:
        return max(candidates, key=lambda item: item[0])[1]

    profiles = sorted((root / "Profiles").glob("*.default*"))
    return profiles[0] if profiles else None


def prefs_file() -> Path | None:
    profile = profile_dir()
    if profile is None:
        return None
    candidate = profile / "prefs.js"
    return candidate if candidate.exists() else None


def pref_pattern() -> re.Pattern[str]:
    return re.compile(r'user_pref\("' + re.escape(LOCAL_API_PREF) + r'",\s*(true|false)\s*\);')


def read_local_api_pref() -> bool | None:
    prefs = prefs_file()
    if prefs is None:
        return None
    match = pref_pattern().search(prefs.read_text(encoding="utf-8", errors="replace"))
    if match is None:
        return None
    return match.group(1) == "true"


def set_local_api_pref(enabled: bool) -> Path:
    prefs = prefs_file()
    if prefs is None:
        exit_with("Could not find Zotero prefs.js. Start Zotero once, then retry.")

    backup = prefs.with_suffix(prefs.suffix + f".zotero-skill-backup-{int(time.time())}")
    shutil.copy2(prefs, backup)

    text = prefs.read_text(encoding="utf-8", errors="replace")
    new_line = f'user_pref("{LOCAL_API_PREF}", {str(enabled).lower()});'
    pattern = pref_pattern()
    if pattern.search(text):
        text = pattern.sub(new_line, text, count=1)
    else:
        text = text.rstrip("\n") + "\n" + new_line + "\n"
    prefs.write_text(text, encoding="utf-8")
    return backup


def url_for(path: str, base_url: str = DEFAULT_BASE_URL) -> str:
    return base_url.rstrip("/") + path


def request(
    path: str,
    *,
    method: str = "GET",
    data: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> Response:
    req_headers = dict(headers or {})
    body: bytes | None = None

    if path.startswith("/api"):
        req_headers.update({k: v for k, v in API_VERSION_HEADERS.items() if k not in req_headers})
    if path.startswith("/connector"):
        req_headers.update({k: v for k, v in CONNECTOR_HEADERS.items() if k not in req_headers})

    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")
        elif isinstance(data, bytes):
            body = data
        else:
            body = str(data).encode("utf-8")

    try:
        req = urllib.request.Request(url_for(path), data=body, method=method, headers=req_headers)
        # Local traffic must not go through HTTP_PROXY/HTTPS_PROXY.
        host = urllib.parse.urlsplit(req.full_url).hostname
        if host in {"127.0.0.1", "localhost", "::1"}:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            response_context = opener.open(req, timeout=timeout)
        else:
            response_context = urllib.request.urlopen(req, timeout=timeout)
        with response_context as response:
            return Response(
                status=response.status,
                headers=dict(response.headers.items()),
                text=response.read().decode("utf-8", errors="replace"),
            )
    except urllib.error.HTTPError as exc:
        return Response(
            status=exc.code,
            headers=dict(exc.headers.items()),
            text=exc.read().decode("utf-8", errors="replace"),
            error=str(exc),
        )
    except Exception as exc:  # local server down, Zotero closed, malformed URL, etc.
        return Response(status=None, headers={}, text="", error=str(exc))


def parse_body(response: Response) -> Any:
    if "json" not in response.content_type.lower():
        return response.text
    try:
        return json.loads(response.text or "null")
    except json.JSONDecodeError:
        return response.text


def require_ok(response: Response, action: str) -> Response:
    if response.ok:
        return response
    detail = response.error or response.text[:TEXT_LIMIT] or "no response body"
    exit_with(f"{action} failed: status={response.status} detail={detail}")
    raise AssertionError("unreachable")


def api_response(path: str) -> Response:
    api_path = path if path.startswith("/api") else "/api" + path
    return require_ok(request(api_path), f"GET {api_path}")


def api_get(path: str) -> Any:
    return parse_body(api_response(path))


def query(params: dict[str, str | int | bool | None]) -> str:
    clean = {key: value for key, value in params.items() if value is not None}
    return urllib.parse.urlencode(clean)


def zotero_executable() -> str | None:
    override = os.environ.get("ZOTERO_EXECUTABLE")
    if override:
        path = Path(override).expanduser()
        return str(path) if path.is_file() else None
    found = shutil.which("zotero.exe" if platform.system() == "Windows" else "zotero")
    if found:
        return found
    if platform.system() == "Windows":
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\App Paths\zotero.exe"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (0, winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as handle:
                        candidate = Path(winreg.QueryValue(handle, None).strip('"'))
                        if candidate.is_file():
                            return str(candidate)
                except OSError:
                    pass
        for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(variable)
            if base:
                path = Path(base) / "Zotero" / "zotero.exe"
                if path.is_file():
                    return str(path)
    return None


def restart_zotero(wait_for_api: bool = True) -> bool:
    # Never force-kill the application. The user must close it first.
    # This function starts a closed app, or activates an already-running one.
    try:
        if platform.system() == "Darwin":
            subprocess.run(["open", "-a", "Zotero"], check=True)
        else:
            executable = zotero_executable()
            if not executable:
                print("Zotero executable not found; set ZOTERO_EXECUTABLE.", file=sys.stderr)
                return False
            options = {}
            if platform.system() == "Windows":
                options["startupinfo"] = subprocess.STARTUPINFO()
                options["startupinfo"].dwFlags |= subprocess.STARTF_USESHOWWINDOW
                options["startupinfo"].wShowWindow = 0
            subprocess.Popen([executable], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, **options)
    except (OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return False
    if not wait_for_api:
        return True
    for _ in range(15):
        if request("/api/", timeout=1).ok:
            return True
        time.sleep(0.5)
    return False


def creators_from_item(data: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for creator in data.get("creators", []) or []:
        name = creator.get("name") or " ".join(
            part for part in [creator.get("firstName"), creator.get("lastName")] if part
        )
        if name:
            names.append(name)
    return names


def year_from_date(raw: str | None) -> str | None:
    if not raw:
        return None
    match = re.search(r"(\d{4})", raw)
    return match.group(1) if match else None


def summarize_item(item: dict[str, Any]) -> dict[str, Any]:
    data = item.get("data", item)
    return {
        "key": item.get("key") or data.get("key"),
        "itemType": data.get("itemType"),
        "title": data.get("title"),
        "creators": creators_from_item(data),
        "year": year_from_date(data.get("date")),
    }


def summarize_collection(collection: dict[str, Any]) -> dict[str, Any]:
    data = collection.get("data", collection)
    return {
        "key": collection.get("key") or data.get("key"),
        "name": data.get("name"),
        "parentCollection": data.get("parentCollection"),
        "version": collection.get("version"),
    }


def summarize_tag(tag: dict[str, Any]) -> dict[str, Any]:
    return {"tag": tag.get("tag"), "numItems": (tag.get("meta") or {}).get("numItems")}


def summarize_group(group: dict[str, Any]) -> dict[str, Any]:
    data = group.get("data", group)
    return {
        "id": group.get("id") or data.get("id"),
        "name": data.get("name"),
        "type": data.get("type"),
    }


def print_items(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        creators = ", ".join(row.get("creators") or [])
        print(
            f"{row.get('key') or '':10} "
            f"{row.get('itemType') or '':14} "
            f"{row.get('year') or '':4} "
            f"{row.get('title') or ''} | {creators}"
        )


def extract_bibtex_keys(text: str) -> list[str]:
    return re.findall(r"@\w+\s*\{\s*([^,\s]+)", text)


def count_bibtex_entries(text: str) -> int:
    return len(extract_bibtex_keys(text))


def total_results(response: Response) -> int | None:
    raw = response.headers.get("Total-Results")
    return int(raw) if raw and raw.isdigit() else None


def export_bibtex(item_key: str | None = None, *, include_children: bool = False) -> str:
    if item_key:
        params = query({"itemKey": item_key, "format": "bibtex", "limit": API_PAGE_LIMIT})
        return api_response(f"{LOCAL_USER}/items?{params}").text

    endpoint = "items" if include_children else "items/top"
    start = 0
    chunks: list[str] = []
    while True:
        params = query(
            {
                "format": "bibtex",
                "sort": "title",
                "direction": "asc",
                "limit": API_PAGE_LIMIT,
                "start": start,
            }
        )
        response = api_response(f"{LOCAL_USER}/{endpoint}?{params}")
        if response.text.strip():
            chunks.append(response.text.strip())

        total = total_results(response)
        start += API_PAGE_LIMIT
        if total is not None and start >= total:
            break
        if total is None and count_bibtex_entries(response.text) < API_PAGE_LIMIT:
            break

    text = "\n\n".join(chunks)
    return text + "\n" if text else ""


def write_text_output(text: str, out: str | None) -> None:
    if out is None:
        print(text, end="" if text.endswith("\n") else "\n")
        return

    path = Path(out).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    dump_json(
        {
            "path": str(path),
            "bytes": len(text.encode("utf-8")),
            "bibtex_entries": count_bibtex_entries(text),
        }
    )


def append_bib_entry(bib_path: Path, entry: str) -> tuple[str, bool]:
    keys = extract_bibtex_keys(entry)
    if not keys:
        exit_with("Could not extract a BibTeX key from Zotero export")
    key = keys[0]

    existing = bib_path.read_text(encoding="utf-8", errors="replace") if bib_path.exists() else ""
    already_present = re.search(r"@\w+\s*\{\s*" + re.escape(key) + r"\s*,", existing) is not None
    if already_present:
        return key, False

    bib_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = existing.rstrip("\n") + "\n\n" if existing else ""
    bib_path.write_text(prefix + entry.strip() + "\n", encoding="utf-8")
    return key, True


def insert_citation(target: Path, citation: str, marker: str | None) -> None:
    text = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
    if marker:
        if marker not in text:
            exit_with(f"Marker not found in {target}: {marker!r}")
        target.write_text(text.replace(marker, citation, 1), encoding="utf-8")
        return

    suffix = "" if not text or text.endswith("\n") else "\n"
    target.write_text(text + suffix + citation + "\n", encoding="utf-8")


def find_item(*, item_key: str | None, query_text: str | None) -> dict[str, Any]:
    if item_key:
        return api_get(f"{LOCAL_USER}/items/{urllib.parse.quote(item_key)}")
    if not query_text:
        exit_with("Provide --item-key or --query")

    params = query({"q": query_text})
    matches = api_get(f"{LOCAL_USER}/items/top?{params}")
    if not matches:
        exit_with(f"No top-level Zotero items matched query: {query_text}")
    if len(matches) > 1:
        keys = ", ".join(str(item.get("key")) for item in matches)
        exit_with(f"Ambiguous query: {len(matches)} matches ({keys}). Use --item-key.")
    return matches[0]


def status_payload() -> dict[str, Any]:
    root = request("/api/", timeout=2)
    connector = request("/connector/ping", timeout=2)
    profile = profile_dir()
    prefs = prefs_file()
    return {
        "profile": str(profile) if profile else None,
        "prefs_file": str(prefs) if prefs else None,
        "local_api_enabled_pref": read_local_api_pref(),
        "api_running": root.ok,
        "api_status": root.status,
        "api_error": root.error,
        "zotero_version": root.headers.get("X-Zotero-Version")
        or connector.headers.get("X-Zotero-Version"),
        "api_version": root.headers.get("Zotero-API-Version"),
        "schema_version": root.headers.get("Zotero-Schema-Version"),
        "connector_running": connector.ok,
        "connector_status": connector.status,
        "connector_error": connector.error,
        "base_url": DEFAULT_BASE_URL,
    }


def cmd_status(args: argparse.Namespace) -> None:
    payload = status_payload()
    if args.json:
        dump_json(payload)
        return
    print(f"Zotero local API pref: {payload['local_api_enabled_pref']} ({payload['prefs_file']})")
    print(
        "API running: "
        f"{payload['api_running']} status={payload['api_status']} "
        f"version={payload['api_version']} zotero={payload['zotero_version']}"
    )
    print(f"Connector running: {payload['connector_running']} status={payload['connector_status']}")


def cmd_set_pref(args: argparse.Namespace, enabled: bool) -> None:
    backup = set_local_api_pref(enabled)
    restarted = restart_zotero(wait_for_api=enabled) if args.restart else False
    dump_json(
        {
            "enabled": enabled,
            "backup": str(backup),
            "restarted": restarted,
            "status": status_payload(),
        }
    )


def cmd_restart(_: argparse.Namespace) -> None:
    dump_json({"restarted": restart_zotero(wait_for_api=True)})


def cmd_probe(args: argparse.Namespace) -> None:
    endpoints = [
        ("root", "/api/"),
        ("schema", "/api/schema"),
        ("itemTypes", "/api/itemTypes"),
        ("itemFields", "/api/itemFields"),
        ("creatorFields", "/api/creatorFields"),
        ("collections", f"{LOCAL_USER}/collections"),
        ("topCollections", f"{LOCAL_USER}/collections/top"),
        ("topItems", f"{LOCAL_USER}/items/top"),
        ("tags", f"{LOCAL_USER}/tags"),
        ("searches", f"{LOCAL_USER}/searches"),
        ("groups", f"{LOCAL_USER}/groups"),
        ("fulltextVersions", f"{LOCAL_USER}/fulltext?since=0"),
        ("connectorPing", "/connector/ping"),
    ]
    rows: list[dict[str, Any]] = []
    for label, path in endpoints:
        response = request(path)
        parsed = parse_body(response)
        if isinstance(parsed, list):
            summary: Any = {"type": "array", "len": len(parsed)}
        elif isinstance(parsed, dict):
            summary = {"type": "object", "keys": list(parsed)[:8]}
        else:
            summary = str(parsed)[:160]
        rows.append(
            {
                "label": label,
                "path": path,
                "status": response.status,
                "content_type": response.content_type,
                "total": response.headers.get("Total-Results"),
                "summary": summary,
            }
        )

    if args.json:
        dump_json(rows)
        return
    for row in rows:
        print(
            f"{row['status'] or 'ERR':>3} {row['label']:18} {row['path']:45} total={row['total']} {row['summary']}"
        )


def cmd_inventory(args: argparse.Namespace) -> None:
    endpoint = "items" if args.include_children else "items/top"
    params = query({"sort": "title", "direction": "asc"})
    rows = [summarize_item(item) for item in api_get(f"{LOCAL_USER}/{endpoint}?{params}")]
    dump_json(rows) if args.json else print_items(rows)


def cmd_collections(args: argparse.Namespace) -> None:
    rows = [summarize_collection(collection) for collection in api_get(f"{LOCAL_USER}/collections")]
    if args.json:
        dump_json(rows)
        return
    for row in rows:
        parent = f" parent={row['parentCollection']}" if row.get("parentCollection") else ""
        print(f"{row.get('key') or '':10} {row.get('name') or ''}{parent}")


def cmd_tags(args: argparse.Namespace) -> None:
    rows = [summarize_tag(tag) for tag in api_get(f"{LOCAL_USER}/tags")]
    if args.json:
        dump_json(rows)
        return
    for row in rows:
        print(f"{row.get('tag') or ''} ({row.get('numItems') or 0})")


def cmd_groups(args: argparse.Namespace) -> None:
    rows = [summarize_group(group) for group in api_get(f"{LOCAL_USER}/groups")]
    if args.json:
        dump_json(rows)
        return
    for row in rows:
        print(f"{row.get('id') or '':>10} {row.get('type') or '':12} {row.get('name') or ''}")


def cmd_search(args: argparse.Namespace) -> None:
    params = query({"q": args.query})
    rows = [summarize_item(item) for item in api_get(f"{LOCAL_USER}/items/top?{params}")]
    if args.with_bibtex_keys:
        for row in rows:
            bibtex = export_bibtex(row.get("key")) if row.get("key") else ""
            keys = extract_bibtex_keys(bibtex)
            row["bibtexKey"] = keys[0] if keys else None
    dump_json(rows) if args.json else print_items(rows)


def cmd_export_bibtex(args: argparse.Namespace) -> None:
    write_text_output(
        export_bibtex(args.item_key, include_children=args.include_children), args.out
    )


def cmd_sync_bib(args: argparse.Namespace) -> None:
    text = export_bibtex(include_children=args.include_children)
    path = Path(args.out).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    dump_json({"path": str(path), "entries": count_bibtex_entries(text)})


def cmd_citations(args: argparse.Namespace) -> None:
    params = query({"include": "data,citation", "style": args.style})
    rows: list[dict[str, Any]] = []
    for item in api_get(f"{LOCAL_USER}/items/top?{params}"):
        row = summarize_item(item)
        row["citation"] = item.get("citation")
        rows.append(row)
    if args.json:
        dump_json(rows)
        return
    for row in rows:
        print(f"{row.get('key')} {row.get('citation')}")


def cmd_children(args: argparse.Namespace) -> None:
    data = api_get(f"{LOCAL_USER}/items/{urllib.parse.quote(args.item_key)}/children")
    rows = [summarize_item(item) for item in data]
    dump_json(rows) if args.json else print_items(rows)


def cmd_fulltext(args: argparse.Namespace) -> None:
    data = api_get(f"{LOCAL_USER}/items/{urllib.parse.quote(args.attachment_key)}/fulltext")
    content = data.get("content", "") if isinstance(data, dict) else str(data)
    if args.out is None:
        print(content)
        return
    path = Path(args.out).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    dump_json(
        {
            "path": str(path),
            "chars": len(content),
            "indexedPages": data.get("indexedPages") if isinstance(data, dict) else None,
            "totalPages": data.get("totalPages") if isinstance(data, dict) else None,
        }
    )


def cmd_file_url(args: argparse.Namespace) -> None:
    print(api_get(f"{LOCAL_USER}/items/{urllib.parse.quote(args.attachment_key)}/file/view/url"))


def cmd_cite(args: argparse.Namespace) -> None:
    target = Path(args.tex or args.markdown).expanduser().resolve()
    bib_path = Path(args.bib).expanduser().resolve()
    if not target.is_file():
        exit_with(f"Draft does not exist: {target}")
    if target == bib_path:
        exit_with("Draft and bibliography must be different files")
    draft = target.read_text(encoding="utf-8")
    if args.marker and draft.count(args.marker) != 1:
        exit_with("Citation marker must occur exactly once; no files changed")
    item = find_item(item_key=args.item_key, query_text=args.query)
    item_key = item.get("key")
    if not item_key:
        exit_with("Matched Zotero item has no key")

    entry = export_bibtex(item_key)
    if len(extract_bibtex_keys(entry)) != 1:
        exit_with("Expected exactly one BibTeX entry; no files changed")
    backups = []
    for path in (target, bib_path):
        if path.exists():
            backup = path.with_name(path.name + ".backup-" + uuid.uuid4().hex[:12])
            shutil.copy2(path, backup)
            backups.append(str(backup))
    citekey, added = append_bib_entry(bib_path, entry)
    citation = f"\\cite{{{citekey}}}" if args.tex else f"[@{citekey}]"
    target = Path(args.tex or args.markdown).expanduser().resolve()
    insert_citation(target, citation, args.marker)
    dump_json(
        {
            "item_key": item_key,
            "title": summarize_item(item).get("title"),
            "bibtex_key": citekey,
            "bib_path": str(Path(args.bib).expanduser().resolve()),
            "bib_entry_added": added,
            "edited_file": str(target),
            "inserted": citation,
            "backups": backups,
        }
    )


def connector_post(path: str, payload: Any, *, content_type: str = "application/json") -> Response:
    return request(path, method="POST", data=payload, headers={"Content-Type": content_type})


def cmd_selected_target(args: argparse.Namespace) -> None:
    response = require_ok(
        connector_post("/connector/getSelectedCollection", {}),
        "POST /connector/getSelectedCollection",
    )
    payload = parse_body(response)
    print(json.dumps(payload, indent=2) if args.json else payload)


def cmd_import_records(args: argparse.Namespace, kind: str) -> None:
    if not args.yes:
        exit_with(
            f"Refusing to write to Zotero without --yes. "
            f"This imports {kind} into the selected Zotero library/collection."
        )
    text = Path(args.file).expanduser().read_text(encoding="utf-8") if args.file else args.text
    if not text:
        exit_with("Provide --file or --text")

    session = args.session or f"dsh-{uuid.uuid4().hex}"
    path = f"/connector/import?{query({'session': session})}"
    response = require_ok(connector_post(path, text, content_type="text/plain"), f"POST {path}")
    dump_json({"status": response.status, "session": session, "response": parse_body(response)})


# ---------------------------------------------------------------------------
# Daily discovery: OpenAlex metadata -> ranked candidates -> Connector import
# ---------------------------------------------------------------------------

DEFAULT_DAILY_CONFIG: dict[str, Any] = {
    "enabled": False,
    "max_items": 10,
    "days_back": 7,
    "topics": [],
    "include_preprints": False,
    "min_venue_score": 0.0,
    "max_per_venue": 3,
    "weights": {"venue": 0.45, "citations": 0.30, "relevance": 0.15, "recency": 0.10},
    "mailto": "",
    "schedule_time": "08:00",
}
MAX_DAILY_ITEMS = 30
DAILY_TASK_NAME = "DSH-Zotero-Daily"
OPENALEX_BASE = "https://api.openalex.org"


def skill_config_dir() -> Path:
    override = os.environ.get("ZOTERO_SKILL_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".zotero-skill"


def daily_config_path() -> Path:
    return skill_config_dir() / "config.json"


def daily_state_path() -> Path:
    return skill_config_dir() / "state.json"


def clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def load_daily_config() -> dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_DAILY_CONFIG))
    path = daily_config_path()
    if path.is_file():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            exit_with(f"Cannot read {path}: {exc}")
        if isinstance(stored, dict):
            for key, value in stored.items():
                if key == "weights" and isinstance(value, dict):
                    config["weights"].update(value)
                elif key in DEFAULT_DAILY_CONFIG:
                    config[key] = value
    config["max_items"] = clamp_int(config.get("max_items"), 1, MAX_DAILY_ITEMS, 10)
    config["days_back"] = clamp_int(config.get("days_back"), 1, 30, 7)
    config["max_per_venue"] = clamp_int(config.get("max_per_venue"), 1, MAX_DAILY_ITEMS, 3)
    if not isinstance(config.get("topics"), list):
        config["topics"] = []
    config["topics"] = [str(topic).strip() for topic in config["topics"] if str(topic).strip()]
    return config


def save_daily_config(config: dict[str, Any]) -> Path:
    path = daily_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_daily_state() -> dict[str, Any]:
    path = daily_state_path()
    if not path.is_file():
        return {"seen": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"seen": {}}
    if not isinstance(state, dict) or not isinstance(state.get("seen"), dict):
        return {"seen": {}}
    return state


def save_daily_state(state: dict[str, Any]) -> Path:
    path = daily_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 90 * 86400
    seen: dict[str, str] = {}
    for key, stamp in state.get("seen", {}).items():
        try:
            when = time.mktime(time.strptime(str(stamp)[:10], "%Y-%m-%d"))
        except ValueError:
            when = time.time()
        if when >= cutoff:
            seen[key] = stamp
    state["seen"] = seen
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def openalex_get(
    path: str,
    params: dict[str, Any],
    mailto: str = "",
    *,
    optional: bool = False,
    attempts: int = 3,
) -> dict[str, Any]:
    payload = {key: value for key, value in params.items() if value is not None}
    if mailto:
        payload["mailto"] = mailto
    url = OPENALEX_BASE + path + "?" + urllib.parse.urlencode(payload)
    request_headers = {
        "User-Agent": "dsh-zotero-skill/0.2 (+https://github.com/Eason-0712/dsh-zotero-skill)"
    }
    last_error: Exception | None = None
    for attempt in range(attempts):
        req = urllib.request.Request(url, headers=request_headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(1.5 * (attempt + 1))
    if optional:
        print(f"OpenAlex request failed ({path}): {last_error}", file=sys.stderr)
        return {}
    exit_with(f"OpenAlex request failed ({path}): {last_error}")


def fetch_openalex_works(
    topic: str,
    start_date: str,
    end_date: str,
    *,
    include_preprints: bool = False,
    mailto: str = "",
    pages: int = 1,
) -> list[dict[str, Any]]:
    types = "article|review|preprint" if include_preprints else "article|review"
    results: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        payload = openalex_get(
            "/works",
            {
                "filter": (
                    f"from_publication_date:{start_date},"
                    f"to_publication_date:{end_date},type:{types}"
                ),
                "search": topic,
                "per-page": 100,
                "page": page,
                "select": (
                    "id,doi,title,publication_date,cited_by_count,primary_location,"
                    "authorships,open_access,type,relevance_score,abstract_inverted_index"
                ),
            },
            mailto,
            optional=True,
        )
        batch = payload.get("results") or []
        results.extend(batch)
        if len(batch) < 100:
            break
    return results


def fetch_source_metrics(source_ids: list[str], mailto: str = "") -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    ids = [
        re.sub(r"^https?://openalex\.org/", "", str(source_id))
        for source_id in source_ids
        if source_id
    ]
    for index in range(0, len(ids), 25):
        chunk = ids[index : index + 25]
        payload = openalex_get(
            "/sources",
            {
                "filter": "openalex_id:" + "|".join(chunk),
                "per-page": 50,
                "select": "id,display_name,summary_stats,issn_l",
            },
            mailto,
            optional=True,
        )
        for source in payload.get("results") or []:
            metrics[source.get("id", "")] = source
    return metrics


def parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def rebuild_abstract(inverted: Any) -> str:
    if not isinstance(inverted, dict):
        return ""
    slots: list[tuple[int, str]] = []
    for word, positions in inverted.items():
        for position in positions or []:
            try:
                slots.append((int(position), str(word)))
            except (TypeError, ValueError):
                continue
    slots.sort(key=lambda row: row[0])
    return " ".join(word for _, word in slots)


def normalize_text(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def normalize_doi(value: Any) -> str:
    doi = str(value or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix) :]
    return doi


def normalize_ceiling(value: float, ceiling: float) -> float:
    if value <= 0:
        return 0.0
    return min(1.0, math.log1p(value) / math.log1p(ceiling))


def score_candidate(
    work: dict[str, Any],
    venue_stats: dict[str, dict[str, Any]],
    weights: dict[str, float],
    today: date | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    published = parse_iso_date(work.get("publication_date")) or today
    age_days = max(0, (today - published).days)
    source = (work.get("primary_location") or {}).get("source") or {}
    stats = (venue_stats.get(source.get("id", "")) or {}).get("summary_stats") or {}
    venue_raw = float(stats.get("2yr_mean_citedness") or 0.0)
    citations = int(work.get("cited_by_count") or 0)
    years = max(age_days / 365.25, 0.08)
    cites_per_year = citations / years
    relevance = float(work.get("relevance_score") or 0.0)

    venue_norm = normalize_ceiling(venue_raw, 20.0)
    cite_norm = normalize_ceiling(cites_per_year, 10.0)
    rel_norm = min(1.0, max(0.0, relevance / 100.0))
    recency = math.exp(-age_days / 14.0)

    score = (
        float(weights.get("venue", 0.0)) * venue_norm
        + float(weights.get("citations", 0.0)) * cite_norm
        + float(weights.get("relevance", 0.0)) * rel_norm
        + float(weights.get("recency", 0.0)) * recency
    )
    authors = [
        str((entry.get("author") or {}).get("display_name") or "").strip()
        for entry in work.get("authorships") or []
    ]
    authors = [author for author in authors if author]
    title = str(work.get("title") or "").strip()
    doi = normalize_doi(work.get("doi"))
    return {
        "id": work.get("id"),
        "doi": doi,
        "title": title,
        "published": published.isoformat(),
        "venue": str(source.get("display_name") or "").strip(),
        "venue_score": round(venue_raw, 2),
        "citations": citations,
        "authors": authors,
        "abstract": rebuild_abstract(work.get("abstract_inverted_index")),
        "score": round(score, 4),
        "metrics": {
            "venue": round(venue_norm, 3),
            "citations": round(cite_norm, 3),
            "relevance": round(rel_norm, 3),
            "recency": round(recency, 3),
        },
    }


def library_has_item(title: str, doi: str) -> bool:
    words = " ".join(normalize_text(title).split()[:6])
    if not words:
        return False
    try:
        params = query({"q": words, "limit": 10})
        items = api_get(f"{LOCAL_USER}/items/top?{params}")
    except SystemExit:
        return False
    target = normalize_text(title)
    for item in items:
        data = item.get("data", item)
        if normalize_text(data.get("title")) == target:
            return True
        if doi and normalize_doi(data.get("DOI") or data.get("doi")) == doi:
            return True
    return False


def bibtex_key_for(candidate: dict[str, Any], index: int = 0) -> str:
    authors = candidate.get("authors") or []
    surname = "anon"
    if authors:
        parts = normalize_text(authors[0]).split()
        if parts:
            surname = re.sub(r"[^a-z0-9]", "", parts[-1]) or "anon"
    year = str(candidate.get("published") or "")[:4] or "n"
    words = [word for word in normalize_text(candidate.get("title")).split() if len(word) > 2]
    word = re.sub(r"[^a-z0-9]", "", words[0]) if words else "paper"
    suffix = "" if index == 0 else chr(ord("a") + index)
    return f"{surname}{year}{word or 'paper'}{suffix}"


def escape_bibtex(value: str) -> str:
    text = " ".join(str(value).split())
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def build_bibtex_entry(candidate: dict[str, Any], index: int = 0) -> str:
    doi = candidate.get("doi") or ""
    fields = [
        ("title", candidate.get("title") or ""),
        ("author", " and ".join(candidate.get("authors") or [])),
        ("journal", candidate.get("venue") or ""),
        ("year", str(candidate.get("published") or "")[:4]),
        ("doi", doi),
        ("url", f"https://doi.org/{doi}" if doi else ""),
        ("abstract", candidate.get("abstract") or ""),
    ]
    body = ",\n".join(
        f"  {name} = {{{escape_bibtex(value)}}}" for name, value in fields if value
    )
    return f"@article{{{bibtex_key_for(candidate, index)},\n{body}\n}}"


def parse_topics(raw: str) -> list[str]:
    separator = ";" if ";" in raw else ","
    topics = [part.strip() for part in raw.split(separator)]
    return [topic for topic in topics if topic]


def collect_daily_candidates(config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    today = date.today()
    start = today - timedelta(days=config["days_back"])
    mailto = config.get("mailto") or ""
    works: list[dict[str, Any]] = []
    for topic in config["topics"]:
        works.extend(
            fetch_openalex_works(
                topic,
                start.isoformat(),
                today.isoformat(),
                include_preprints=bool(config.get("include_preprints")),
                mailto=mailto,
            )
        )

    if not works:
        exit_with("No candidates returned from OpenAlex; check topics, network, or days_back")

    state = load_daily_state()
    seen = state.get("seen", {})
    unique: dict[str, dict[str, Any]] = {}
    for work in works:
        key = normalize_doi(work.get("doi")) or str(work.get("id") or "")
        if key and key not in seen:
            unique.setdefault(key, work)

    source_ids = [
        ((work.get("primary_location") or {}).get("source") or {}).get("id", "")
        for work in unique.values()
    ]
    cache = state.get("venues") if isinstance(state.get("venues"), dict) else {}
    stale = []
    for source_id in {sid for sid in source_ids if sid}:
        entry = cache.get(source_id) or {}
        try:
            cached_on = date.fromisoformat(str(entry.get("date"))[:10])
        except ValueError:
            cached_on = None
        if cached_on is None or (today - cached_on).days >= 30:
            stale.append(source_id)
    for source_id, source in fetch_source_metrics(stale, mailto).items():
        cache[source_id] = {
            "date": today.isoformat(),
            "display_name": source.get("display_name"),
            "summary_stats": source.get("summary_stats") or {},
        }
    if stale and any(cache.get(sid) for sid in stale):
        state["venues"] = cache
        save_daily_state(state)
    venue_stats = {
        source_id: {
            "summary_stats": (cache.get(source_id) or {}).get("summary_stats") or {},
            "display_name": (cache.get(source_id) or {}).get("display_name"),
        }
        for source_id in source_ids
        if source_id
    }

    candidates: list[dict[str, Any]] = []
    skipped = {"seen": len(works) - len(unique), "low_venue": 0, "in_library": 0}
    api_up = request("/api/", timeout=2).ok
    for key, work in unique.items():
        candidate = score_candidate(work, venue_stats, config.get("weights") or {}, today)
        candidate["dedup_key"] = key
        if not candidate["title"]:
            continue
        if candidate["venue_score"] < float(config.get("min_venue_score") or 0):
            skipped["low_venue"] += 1
            continue
        if api_up and library_has_item(candidate["title"], candidate["doi"]):
            skipped["in_library"] += 1
            continue
        candidates.append(candidate)

    candidates.sort(key=lambda row: row["score"], reverse=True)
    return candidates, skipped


def select_candidates(
    candidates: list[dict[str, Any]], max_items: int, max_per_venue: int = 3
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    overflow: list[dict[str, Any]] = []
    per_venue: dict[str, int] = {}
    for candidate in candidates:
        venue = str(candidate.get("venue") or "")
        if venue and per_venue.get(venue, 0) >= max_per_venue:
            overflow.append(candidate)
            continue
        selected.append(candidate)
        per_venue[venue] = per_venue.get(venue, 0) + 1
        if len(selected) >= max_items:
            return selected
    for candidate in overflow:
        if len(selected) >= max_items:
            break
        selected.append(candidate)
    return selected


def cmd_daily(args: argparse.Namespace) -> None:
    config = load_daily_config()
    if args.max is not None:
        config["max_items"] = clamp_int(args.max, 1, MAX_DAILY_ITEMS, config["max_items"])
    if args.days is not None:
        config["days_back"] = clamp_int(args.days, 1, 30, config["days_back"])
    if getattr(args, "max_per_venue", None) is not None:
        config["max_per_venue"] = clamp_int(
            args.max_per_venue, 1, MAX_DAILY_ITEMS, config["max_per_venue"]
        )
    if not config["enabled"] and not args.force:
        dump_json(
            {
                "enabled": False,
                "hint": "Enable with: zotero.py daily-config --enable",
                "config_path": str(daily_config_path()),
            }
        )
        return
    if not config["topics"]:
        exit_with('No topics configured. Run: zotero.py daily-config --topics "topic one;topic two"')

    candidates, skipped = collect_daily_candidates(config)
    selected = select_candidates(
        candidates, config["max_items"], int(config.get("max_per_venue") or 3)
    )
    payload: dict[str, Any] = {
        "enabled": True,
        "dry_run": bool(args.dry_run),
        "days_back": config["days_back"],
        "max_items": config["max_items"],
        "candidates": len(candidates),
        "skipped": skipped,
        "items": selected,
    }
    if args.dry_run or not selected:
        payload["imported"] = 0
        dump_json(payload)
        return

    if not request("/connector/ping", timeout=2).ok and not request("/api/", timeout=2).ok:
        restart_zotero(wait_for_api=True)
    if not request("/connector/ping", timeout=5).ok:
        exit_with("Zotero connector is not reachable; start Zotero and retry")

    bibtex = "\n\n".join(build_bibtex_entry(item, index) for index, item in enumerate(selected))
    session = f"dsh-daily-{uuid.uuid4().hex}"
    path = f"/connector/import?{query({'session': session})}"
    response = require_ok(connector_post(path, bibtex, content_type="text/plain"), f"POST {path}")

    state = load_daily_state()
    today = date.today().isoformat()
    for item in selected:
        state["seen"][item["dedup_key"]] = today
    state_path = save_daily_state(state)

    payload.update(
        {
            "imported": len(selected),
            "session": session,
            "response": parse_body(response),
            "state_path": str(state_path),
        }
    )
    dump_json(payload)


def cmd_daily_config(args: argparse.Namespace) -> None:
    config = load_daily_config()
    changed = False
    if args.enable:
        config["enabled"] = True
        changed = True
    if args.disable:
        config["enabled"] = False
        changed = True
    if args.max is not None:
        config["max_items"] = clamp_int(args.max, 1, MAX_DAILY_ITEMS, config["max_items"])
        changed = True
    if args.days is not None:
        config["days_back"] = clamp_int(args.days, 1, 30, config["days_back"])
        changed = True
    if args.max_per_venue is not None:
        config["max_per_venue"] = clamp_int(
            args.max_per_venue, 1, MAX_DAILY_ITEMS, config["max_per_venue"]
        )
        changed = True
    if args.topics is not None:
        config["topics"] = parse_topics(args.topics)
        changed = True
    if args.mailto is not None:
        config["mailto"] = args.mailto.strip()
        changed = True
    if args.include_preprints:
        config["include_preprints"] = True
        changed = True
    if args.exclude_preprints:
        config["include_preprints"] = False
        changed = True
    if args.min_venue is not None:
        config["min_venue_score"] = max(0.0, float(args.min_venue))
        changed = True
    if args.time is not None:
        config["schedule_time"] = args.time
        changed = True

    if changed:
        save_daily_config(config)
    dump_json(
        {
            "config_path": str(daily_config_path()),
            "saved": changed,
            "config": config,
        }
    )


def cmd_daily_schedule(args: argparse.Namespace) -> None:
    if platform.system() != "Windows":
        exit_with("daily-schedule currently supports Windows Task Scheduler only")
    config = load_daily_config()
    name = args.name or DAILY_TASK_NAME
    if args.remove:
        result = subprocess.run(
            ["schtasks", "/Delete", "/F", "/TN", name],
            capture_output=True,
            text=True,
        )
        dump_json(
            {
                "task": name,
                "removed": result.returncode == 0,
                "message": (result.stdout or result.stderr).strip(),
            }
        )
        return

    time_text = args.time or str(config.get("schedule_time") or "08:00")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_text):
        exit_with("Schedule time must look like HH:MM (24-hour), e.g. 08:30")

    config["schedule_time"] = time_text
    save_daily_config(config)

    wrapper = skill_config_dir() / "daily.cmd"
    log_path = skill_config_dir() / "daily.log"
    script = Path(__file__).resolve()
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        f'"{sys.executable}" "{script}" daily >> "{log_path}" 2>&1\r\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "schtasks",
            "/Create",
            "/F",
            "/SC",
            "DAILY",
            "/ST",
            time_text,
            "/TN",
            name,
            "/TR",
            f'"{wrapper}"',
        ],
        capture_output=True,
        text=True,
    )
    dump_json(
        {
            "task": name,
            "created": result.returncode == 0,
            "time": time_text,
            "wrapper": str(wrapper),
            "log": str(log_path),
            "message": (result.stdout or result.stderr).strip(),
        }
    )


def add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json", action="store_true", help="Print JSON instead of a compact text summary"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Operate Zotero Desktop local API and connector server."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    status = subcommands.add_parser("status", help="Show Zotero local API and connector readiness")
    add_json_flag(status)
    status.set_defaults(func=cmd_status)

    enable = subcommands.add_parser("enable", help="Enable Zotero's local Desktop API preference")
    enable.add_argument(
        "--restart", action="store_true", help="Start Zotero after editing prefs.js (close Zotero first)"
    )
    enable.set_defaults(func=lambda args: cmd_set_pref(args, True))

    disable = subcommands.add_parser(
        "disable", help="Disable Zotero's local Desktop API preference"
    )
    disable.add_argument(
        "--restart", action="store_true", help="Start Zotero after editing prefs.js (close Zotero first)"
    )
    disable.set_defaults(func=lambda args: cmd_set_pref(args, False))

    restart = subcommands.add_parser("restart", help="Start/activate Zotero without force-killing; close it first to restart")
    restart.set_defaults(func=cmd_restart)

    probe = subcommands.add_parser("probe", help="Probe common safe local API routes")
    add_json_flag(probe)
    probe.set_defaults(func=cmd_probe)

    inventory = subcommands.add_parser("inventory", help="List Zotero items")
    inventory.add_argument(
        "--include-children", action="store_true", help="Include child notes and attachments"
    )
    inventory.add_argument(
        "--all", action="store_true", dest="include_children", help=argparse.SUPPRESS
    )
    add_json_flag(inventory)
    inventory.set_defaults(func=cmd_inventory)

    collections = subcommands.add_parser("collections", help="List collections")
    add_json_flag(collections)
    collections.set_defaults(func=cmd_collections)

    tags = subcommands.add_parser("tags", help="List tags")
    add_json_flag(tags)
    tags.set_defaults(func=cmd_tags)

    groups = subcommands.add_parser("groups", help="List synced group libraries visible locally")
    add_json_flag(groups)
    groups.set_defaults(func=cmd_groups)

    search = subcommands.add_parser("search", help="Search top-level Zotero items")
    search.add_argument("query")
    search.add_argument("--with-bibtex-keys", action="store_true")
    add_json_flag(search)
    search.set_defaults(func=cmd_search)

    export = subcommands.add_parser("export-bibtex", help="Export Zotero items as BibTeX")
    export.add_argument("--item-key")
    export.add_argument("--include-children", action="store_true")
    export.add_argument(
        "--all", action="store_true", dest="include_children", help=argparse.SUPPRESS
    )
    export.add_argument("--out")
    export.set_defaults(func=cmd_export_bibtex)

    sync_bib = subcommands.add_parser("sync-bib", help="Write a references.bib export")
    sync_bib.add_argument("--out", default="references.bib")
    sync_bib.add_argument("--include-children", action="store_true")
    sync_bib.add_argument(
        "--all", action="store_true", dest="include_children", help=argparse.SUPPRESS
    )
    sync_bib.set_defaults(func=cmd_sync_bib)

    citations = subcommands.add_parser("citations", help="Render formatted citations")
    citations.add_argument("--style", default="apa")
    add_json_flag(citations)
    citations.set_defaults(func=cmd_citations)

    children = subcommands.add_parser("children", help="List child notes/attachments for an item")
    children.add_argument("item_key")
    add_json_flag(children)
    children.set_defaults(func=cmd_children)

    fulltext = subcommands.add_parser(
        "fulltext", help="Print or save indexed full text for an attachment"
    )
    fulltext.add_argument("attachment_key")
    fulltext.add_argument("--out")
    fulltext.set_defaults(func=cmd_fulltext)

    file_url = subcommands.add_parser(
        "file-url", help="Print Zotero's local file URL for an attachment"
    )
    file_url.add_argument("attachment_key")
    file_url.set_defaults(func=cmd_file_url)

    cite = subcommands.add_parser(
        "cite", help="Insert a citation into a TeX or Markdown file and update a .bib file"
    )
    source = cite.add_mutually_exclusive_group(required=True)
    source.add_argument("--item-key")
    source.add_argument("--query")
    target = cite.add_mutually_exclusive_group(required=True)
    target.add_argument("--tex")
    target.add_argument("--markdown")
    cite.add_argument("--bib", default="references.bib")
    cite.add_argument(
        "--marker", help="Replace this marker with the citation; otherwise append the citation"
    )
    cite.set_defaults(func=cmd_cite)

    selected = subcommands.add_parser(
        "selected-target", help="Show the currently selected Zotero library/collection"
    )
    add_json_flag(selected)
    selected.set_defaults(func=cmd_selected_target)

    for command, kind in [("import-bibtex", "BibTeX"), ("import-ris", "RIS")]:
        import_cmd = subcommands.add_parser(command, help=f"Import {kind} through Zotero Connector")
        input_group = import_cmd.add_mutually_exclusive_group(required=True)
        input_group.add_argument("--file")
        input_group.add_argument("--text")
        import_cmd.add_argument("--session")
        import_cmd.add_argument("--yes", action="store_true", help="Confirm Zotero library write")
        import_cmd.set_defaults(func=lambda args, kind=kind: cmd_import_records(args, kind))

    daily = subcommands.add_parser(
        "daily", help="Fetch new papers for your topics and import the best ones into Zotero"
    )
    daily.add_argument(
        "--dry-run", action="store_true", help="Only rank candidates; do not write to Zotero"
    )
    daily.add_argument(
        "--force", action="store_true", help="Run even when the daily feature is disabled"
    )
    daily.add_argument("--max", type=int, help=f"Papers to import this run (1-{MAX_DAILY_ITEMS})")
    daily.add_argument("--days", type=int, help="Look back this many days (1-30)")
    daily.add_argument(
        "--max-per-venue", type=int, help="At most this many papers from the same venue"
    )
    daily.set_defaults(func=cmd_daily)

    daily_config = subcommands.add_parser(
        "daily-config", help="Show or change daily discovery settings"
    )
    toggle = daily_config.add_mutually_exclusive_group()
    toggle.add_argument("--enable", action="store_true", help="Turn daily discovery on")
    toggle.add_argument("--disable", action="store_true", help="Turn daily discovery off")
    daily_config.add_argument("--max", type=int, help=f"Max papers per run (1-{MAX_DAILY_ITEMS})")
    daily_config.add_argument("--days", type=int, help="Look back this many days (1-30)")
    daily_config.add_argument(
        "--max-per-venue", type=int, help="At most this many papers from the same venue"
    )
    daily_config.add_argument(
        "--topics", help='Research topics separated by ";" e.g. "transformer;large language model"'
    )
    daily_config.add_argument("--mailto", help="Contact email for the OpenAlex polite pool")
    preprints = daily_config.add_mutually_exclusive_group()
    preprints.add_argument("--include-preprints", action="store_true")
    preprints.add_argument("--exclude-preprints", action="store_true")
    daily_config.add_argument(
        "--min-venue", type=float, help="Minimum venue 2-year mean citedness (raw value)"
    )
    daily_config.add_argument("--time", help="Default schedule time, HH:MM (24-hour)")
    daily_config.set_defaults(func=cmd_daily_config)

    daily_schedule = subcommands.add_parser(
        "daily-schedule", help="Create or remove a Windows scheduled task for daily discovery"
    )
    daily_schedule.add_argument("--time", help="Run time, HH:MM (24-hour), default from config")
    daily_schedule.add_argument("--name", help=f"Task name, default {DAILY_TASK_NAME}")
    daily_schedule.add_argument("--remove", action="store_true", help="Delete the scheduled task")
    daily_schedule.set_defaults(func=cmd_daily_schedule)

    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
