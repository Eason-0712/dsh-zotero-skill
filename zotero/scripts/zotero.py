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


def read_text_file(path: str | Path) -> str:
    """Read JSON/Markdown that may come from PowerShell (UTF-16) or editors."""
    data = Path(path).expanduser().read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    return data.decode("utf-8-sig")


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


# ---------------------------------------------------------------------------
# Literature workflow: discovery, frontier tracking, screening, library
# management, batch reading, classification, landscape, and gap analysis.
# ---------------------------------------------------------------------------

WRITE_APP_NAME = "DSH Zotero Skill"
LOCAL_KEY_FILENAME = "local-key.json"
SERVER_ID_FILENAME = "server-id.json"
FRONTIER_FILENAME = "frontier.json"

TIER_CORE = 0.60
TIER_IMPORTANT = 0.42

METHOD_PATTERNS: list[tuple[str, str]] = [
    ("transformer", r"\btransformers?\b"),
    ("cnn", r"\b(?:cnns?|convolutional neural networks?|convnets?)\b"),
    ("gnn", r"\b(?:gnns?|graph neural networks?|graph attention)\b"),
    ("rnn", r"\b(?:rnn|rnns|recurrent neural networks?|lstms?|gru)\b"),
    ("gan", r"\b(?:gans?|generative adversarial)\b"),
    ("diffusion", r"\bdiffusion\b"),
    ("vae", r"\b(?:vae|variational autoencoders?)\b"),
    ("reinforcement", r"\breinforcement learning\b"),
    ("self-supervised", r"\bself[- ]supervised\b"),
    ("contrastive", r"\bcontrastive\b"),
    ("distillation", r"\bknowledge distillation\b"),
    ("federated", r"\bfederated learning\b"),
    ("transfer", r"\btransfer learning\b"),
    ("few-shot", r"\b(?:few[- ]shot|zero[- ]shot|one[- ]shot)\b"),
    ("spiking", r"\bspiking neural networks?\b"),
    ("mamba", r"\b(?:mamba|state[- ]space model)\b"),
    ("eegnet", r"\beegnet\b"),
    ("tcnet", r"\b(?:atcnet|ctnet|tcnet)\b"),
    ("pose", r"\b(?:6d pose|six[- ]dimensional pose|pose estimation|pose regression)\b"),
    ("point-cloud", r"\bpoint clouds?\b"),
    ("grasping", r"\b(?:grasp detection|grasp pose|grasp planning|grasping)\b"),
    ("detection", r"\bobject detection\b"),
    ("segmentation", r"\bsemantic segmentation\b"),
    ("depth", r"\bdepth estimation\b"),
    ("autoencoder", r"\bautoencoders?\b"),
    ("svm", r"\b(?:svm|support vector machines?)\b"),
    ("graph-learning", r"\bgraph learning\b"),
]

DATASET_PATTERNS: list[tuple[str, str]] = [
    ("SEED", r"\bseed(?:[- ]?iv|[- ]?v)?\b"),
    ("DEAP", r"\bdeap\b"),
    ("DREAMER", r"\bdreamer\b"),
    ("MPED", r"\bmped\b"),
    ("PhysioNet", r"\bphysionet\b"),
    ("CHB-MIT", r"\bchb[- ]?mit\b"),
    ("TUSZ", r"\btusz\b"),
    ("TUAB", r"\btuab\b"),
    ("Bonn", r"\bbonn\b"),
    ("CHSZ", r"\bchsz\b"),
    ("BCI-IV-2a", r"\b(?:bci\s*competition\s*iv|iv[- ]?2a)\b"),
    ("EEGMMIDB", r"\b(?:eegmmidb|physionet\s+motor\s+movement)\b"),
    ("ImageNet", r"\bimagenet\b"),
    ("COCO", r"\bcoco\b"),
    ("YCB", r"\bycb\b"),
    ("GraspNet", r"\bgraspnet\b"),
    ("Cornell", r"\bcornell\b"),
    ("Jacquard", r"\bjacquard\b"),
    ("LINEMOD", r"\bline[- ]?mod\b"),
    ("MetaGraspNet", r"\bmetagraspnet\b"),
    ("KITTI", r"\bkitti\b"),
    ("nuScenes", r"\bnuscenes\b"),
    ("ScanNet", r"\bscannet\b"),
    ("ModelNet", r"\bmodelnet\b"),
]

TAG_SCOPE_PREFIXES = ("project:", "topic:", "method:", "dataset:", "venue:", "year:", "auto:")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def load_json_file(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json_file(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def header_value(headers: dict[str, str], name: str) -> str:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return str(value)
    return ""


def split_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = re.split(r"[,;]", str(raw))
    return [part.strip() for part in parts if part.strip()]


def ensure_yes(args: argparse.Namespace, action: str) -> None:
    if not getattr(args, "yes", False) and not getattr(args, "dry_run", False):
        exit_with(f"Refusing to {action} without --yes (use --dry-run to preview)")


# --- local API write access -------------------------------------------------


def server_id_path() -> Path:
    return skill_config_dir() / SERVER_ID_FILENAME


def local_key_path() -> Path:
    return skill_config_dir() / LOCAL_KEY_FILENAME


def server_id(refresh: bool = False) -> str:
    if not refresh:
        cached = load_json_file(server_id_path(), {})
        if isinstance(cached, dict) and cached.get("serverId"):
            return str(cached["serverId"])
    response = request("/api/", timeout=3)
    if not response.ok:
        exit_with("Zotero local API is unreachable; start Zotero and enable its local API")
    value = header_value(response.headers, "Zotero-Server-ID")
    if value:
        save_json_file(server_id_path(), {"serverId": value, "savedAt": now_iso()})
    return value


def load_local_key() -> dict[str, Any]:
    stored = load_json_file(local_key_path(), {})
    return stored if isinstance(stored, dict) else {}


def authorize_zotero(app_name: str = WRITE_APP_NAME) -> dict[str, Any]:
    sid = server_id()
    payload = json.dumps({"appName": app_name}).encode("utf-8")
    headers = {
        "Zotero-API-Version": "3",
        "Content-Type": "application/json",
        "Zotero-Server-ID": sid,
    }
    print(
        "Zotero is asking for permission: choose \"Always Allow\" / 「始终允许」 "
        f"for {app_name}.",
        file=sys.stderr,
    )
    response = request(
        "/api/local/authorize",
        method="POST",
        data=payload,
        headers=headers,
        timeout=300.0,
    )
    if not response.ok:
        detail = response.error or response.text[:TEXT_LIMIT] or "no response body"
        exit_with(f"Authorization failed: status={response.status} detail={detail}")
    body = parse_body(response)
    key = str((body or {}).get("key") or "") if isinstance(body, dict) else ""
    if not key:
        exit_with(f"Authorization returned no key: {response.text[:TEXT_LIMIT]}")
    record = {
        "key": key,
        "appName": app_name,
        "serverId": sid,
        "remember": bool((body or {}).get("remember")),
        "authorizedAt": now_iso(),
    }
    save_json_file(local_key_path(), record)
    return record


def require_local_key() -> str:
    key = os.environ.get("ZOTERO_LOCAL_API_KEY") or str(load_local_key().get("key") or "")
    if not key:
        exit_with(
            "Zotero write access is not authorized yet. Run: zotero.py authorize "
            "(then choose 「始终允许」/Always Allow in Zotero)."
        )
    return key


def local_write(
    path: str,
    payload: Any = None,
    *,
    method: str = "PATCH",
    version: int | str | None = None,
    timeout: float = 30.0,
) -> Response:
    key = require_local_key()
    headers = {"Zotero-API-Key": key, "Zotero-Server-ID": server_id()}
    if version is not None:
        headers["If-Unmodified-Since-Version"] = str(version)
    response = request(path, method=method, data=payload, headers=headers, timeout=timeout)
    if response.status == 412:
        headers["Zotero-Server-ID"] = server_id(refresh=True)
        response = request(path, method=method, data=payload, headers=headers, timeout=timeout)
    if response.status == 401:
        exit_with(
            "Zotero rejected the stored local API key (401). Run: zotero.py authorize --force"
        )
    return response


def item_full(item_key: str) -> dict[str, Any]:
    payload = api_get(f"{LOCAL_USER}/items/{urllib.parse.quote(item_key)}")
    return payload if isinstance(payload, dict) else {}


def patch_item(
    item_key: str, fields: dict[str, Any], *, dry_run: bool = False
) -> dict[str, Any]:
    current = item_full(item_key)
    version = current.get("version")
    result = {
        "key": item_key,
        "version": version,
        "fields": sorted(fields),
        "applied": False,
    }
    if dry_run:
        return result
    response = local_write(
        f"{LOCAL_USER}/items/{urllib.parse.quote(item_key)}",
        fields,
        method="PATCH",
        version=version,
    )
    require_ok(response, f"PATCH item {item_key}")
    result["applied"] = True
    return result


def fetch_all(path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        page_params = dict(params or {})
        page_params.update({"limit": API_PAGE_LIMIT, "start": start})
        joiner = "&" if "?" in path else "?"
        chunk = api_get(f"{path}{joiner}{query(page_params)}")
        if not isinstance(chunk, list) or not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < API_PAGE_LIMIT:
            break
        start += API_PAGE_LIMIT
    return rows


def all_collections() -> list[dict[str, Any]]:
    payload = api_get(f"{LOCAL_USER}/collections")
    return payload if isinstance(payload, list) else []


def find_collection(name: str) -> dict[str, Any] | None:
    target = normalize_text(name)
    for collection in all_collections():
        data = collection.get("data", collection)
        key = collection.get("key") or data.get("key")
        if key == name:
            return collection
        if normalize_text(data.get("name")) == target:
            return collection
    return None


def create_collection(name: str, parent: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name}
    if parent:
        parent_record = find_collection(parent)
        if parent_record is None:
            exit_with(f"Parent collection not found: {parent}")
        data = parent_record.get("data", parent_record)
        payload["parentCollection"] = parent_record.get("key") or data.get("key")
    response = local_write(f"{LOCAL_USER}/collections", payload, method="POST")
    require_ok(response, "POST collection")
    body = parse_body(response)
    created = (body or {}).get("successful") or {}
    if isinstance(created, dict) and created:
        key = next(iter(created))
        return {"key": key, "data": {"name": name}}
    return {"key": None, "data": {"name": name}}


# --- selection & records ----------------------------------------------------


def selected_items(args: argparse.Namespace) -> list[dict[str, Any]]:
    keys = split_csv(getattr(args, "keys", None))
    if keys:
        return [item_full(key) for key in keys]

    limit = clamp_int(getattr(args, "limit", None) or 200, 1, 5000, 200)
    params: dict[str, Any] = {}
    if getattr(args, "query", None):
        params["q"] = args.query
    if getattr(args, "tag", None):
        params["tag"] = args.tag

    collection = getattr(args, "collection", None)
    path = f"{LOCAL_USER}/items/top"
    if collection:
        record = find_collection(collection)
        if record is None:
            exit_with(f"Collection not found: {collection}")
        data = record.get("data", record)
        key = record.get("key") or data.get("key")
        path = f"{LOCAL_USER}/collections/{urllib.parse.quote(key)}/items/top"

    if not params and not collection and not getattr(args, "all", False):
        exit_with("Select items with --keys, --query, --tag, --collection, or --all")

    rows: list[dict[str, Any]] = []
    start = 0
    while len(rows) < limit:
        page_params = dict(params)
        page_params.update({"limit": min(API_PAGE_LIMIT, limit - len(rows)), "start": start})
        chunk = api_get(f"{path}?{query(page_params)}")
        if not isinstance(chunk, list) or not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < API_PAGE_LIMIT:
            break
        start += API_PAGE_LIMIT
    return rows


def record_from_item(item: dict[str, Any]) -> dict[str, Any]:
    data = item.get("data", item)
    venue = (
        data.get("publicationTitle")
        or data.get("conferenceName")
        or data.get("journalAbbreviation")
        or data.get("proceedingsTitle")
        or ""
    )
    tags = [entry.get("tag") for entry in data.get("tags") or [] if entry.get("tag")]
    terms = extract_terms(f"{data.get('title') or ''} {data.get('abstractNote') or ''}")
    return {
        "key": item.get("key") or data.get("key"),
        "itemType": data.get("itemType"),
        "title": data.get("title") or "",
        "year": year_from_date(data.get("date")),
        "venue": str(venue).strip(),
        "doi": normalize_doi(data.get("DOI") or data.get("doi")),
        "abstract": data.get("abstractNote") or "",
        "url": data.get("url") or "",
        "tags": tags,
        "collections": list(data.get("collections") or []),
        "methods": terms["methods"],
        "datasets": terms["datasets"],
    }


def extract_terms(text: str) -> dict[str, list[str]]:
    lowered = str(text or "").lower()
    methods = [name for name, pattern in METHOD_PATTERNS if re.search(pattern, lowered)]
    datasets = [name for name, pattern in DATASET_PATTERNS if re.search(pattern, lowered)]
    return {"methods": methods, "datasets": datasets}


# --- discovery: find / frontier / screen ------------------------------------


def venue_stats_for(works: list[dict[str, Any]], mailto: str = "") -> dict[str, dict[str, Any]]:
    state = load_daily_state()
    cache = state.get("venues") if isinstance(state.get("venues"), dict) else {}
    today = date.today()
    source_ids = [
        ((work.get("primary_location") or {}).get("source") or {}).get("id", "") for work in works
    ]
    stale: list[str] = []
    for source in sorted({sid for sid in source_ids if sid}):
        entry = cache.get(source) or {}
        try:
            cached_on = date.fromisoformat(str(entry.get("date"))[:10])
        except ValueError:
            cached_on = None
        if cached_on is None or (today - cached_on).days >= 30:
            stale.append(source)
    for source, payload in fetch_source_metrics(stale, mailto).items():
        cache[source] = {
            "date": today.isoformat(),
            "display_name": payload.get("display_name"),
            "summary_stats": payload.get("summary_stats") or {},
        }
    if stale and any(cache.get(source) for source in stale):
        state["venues"] = cache
        save_daily_state(state)
    return {
        source: {
            "summary_stats": (cache.get(source) or {}).get("summary_stats") or {},
            "display_name": (cache.get(source) or {}).get("display_name"),
        }
        for source in source_ids
        if source
    }


def gather_candidates(
    queries: list[str],
    start_date: str,
    end_date: str,
    *,
    include_preprints: bool = False,
    mailto: str = "",
    pages: int = 1,
) -> list[dict[str, Any]]:
    works: list[dict[str, Any]] = []
    for topic in queries:
        works.extend(
            fetch_openalex_works(
                topic,
                start_date,
                end_date,
                include_preprints=include_preprints,
                mailto=mailto,
                pages=pages,
            )
        )
    if not works:
        return []

    venue_stats = venue_stats_for(works, mailto)
    today = date.today()
    weights = DEFAULT_DAILY_CONFIG["weights"]
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for work in works:
        candidate = score_candidate(work, venue_stats, weights, today)
        key = normalize_doi(work.get("doi")) or str(work.get("id") or "")
        if key in seen:
            continue
        seen.add(key)
        if not candidate["title"]:
            continue
        access = work.get("open_access") or {}
        candidate["dedupe_key"] = key
        candidate["oa"] = bool(access.get("is_oa"))
        candidate["oa_url"] = str(access.get("oa_url") or "")
        candidate["type"] = work.get("type")
        candidates.append(candidate)
    candidates.sort(key=lambda row: row["score"], reverse=True)
    return candidates


def candidate_rows(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return candidates[: max(0, limit)]


def print_candidates(rows: list[dict[str, Any]]) -> None:
    for index, row in enumerate(rows, start=1):
        authors = row.get("authors") or []
        first = authors[0] if authors else ""
        detail = ", ".join(part for part in [row.get("venue") or "", first] if part)
        flag = "OA " if row.get("oa") else "   "
        print(
            f"{index:2}. {row.get('score', 0):.3f} {flag}{row.get('published', '')[:4]} "
            f"c{row.get('citations', 0):<5} {row.get('title', '')[:110]} | {detail}"
        )


def import_candidates(
    candidates: list[dict[str, Any]], *, session: str | None = None
) -> dict[str, Any]:
    if not candidates:
        return {"imported": 0, "session": None}
    text = "\n\n".join(
        build_bibtex_entry(candidate, index) for index, candidate in enumerate(candidates)
    )
    session = session or f"dsh-{uuid.uuid4().hex}"
    path = f"/connector/import?{query({'session': session})}"
    response = require_ok(connector_post(path, text, content_type="text/plain"), f"POST {path}")
    return {"imported": len(candidates), "session": session, "response": parse_body(response)}


def default_window(args: argparse.Namespace) -> tuple[str, str]:
    today = date.today()
    end = args.date_to or today.isoformat()
    if args.days:
        start = today - timedelta(days=clamp_int(args.days, 1, 3650, 30))
        return start.isoformat(), end
    if args.date_from:
        return args.date_from, end
    return (today - timedelta(days=730)).isoformat(), end


def cmd_find(args: argparse.Namespace) -> None:
    start, end = default_window(args)
    queries = [part.strip() for part in args.query if part and part.strip()]
    if not queries:
        exit_with("Provide at least one query")

    candidates = gather_candidates(
        queries,
        start,
        end,
        include_preprints=args.include_preprints or args.work_type == "preprint",
        mailto=args.mailto,
    )
    api_up = request("/api/", timeout=2).ok
    kept: list[dict[str, Any]] = []
    skipped = {"in_library": 0, "low_score": 0, "not_open_access": 0, "wrong_type": 0}
    for candidate in candidates:
        if args.oa and not candidate.get("oa"):
            skipped["not_open_access"] += 1
            continue
        if args.work_type and candidate.get("type") != args.work_type:
            skipped["wrong_type"] += 1
            continue
        if candidate["score"] < args.min_score:
            skipped["low_score"] += 1
            continue
        if api_up and not args.include_library and library_has_item(
            candidate["title"], candidate["doi"]
        ):
            skipped["in_library"] += 1
            continue
        kept.append(candidate)

    limit = clamp_int(args.limit, 1, 100, 20)
    rows = candidate_rows(kept, limit)

    if args.bib:
        text = "\n\n".join(build_bibtex_entry(row, i) for i, row in enumerate(rows))
        path = Path(args.bib).expanduser().resolve()
        write_text_output(text + ("\n" if text else ""), str(path))
    if args.out:
        save_json_file(Path(args.out).expanduser().resolve(), rows)
    if args.do_import:
        ensure_yes(args, "import search results into Zotero")
        result = import_candidates(rows)
        print(json.dumps(result, ensure_ascii=False))

    payload = {
        "window": {"from": start, "to": end},
        "queries": queries,
        "candidates": len(candidates),
        "kept": len(rows),
        "skipped": skipped,
        "results": rows,
    }
    if args.json:
        dump_json(payload["results"] if args.out or args.bib else payload)
        return
    print(
        f"{len(candidates)} candidates from OpenAlex, kept {len(rows)} "
        f"({skipped['in_library']} already in library, {skipped['low_score']} below score)"
    )
    print_candidates(rows)


def frontier_path() -> Path:
    return skill_config_dir() / FRONTIER_FILENAME


def load_frontier() -> dict[str, Any]:
    stored = load_json_file(frontier_path(), {})
    watches = stored.get("watches") if isinstance(stored, dict) else {}
    return {"watches": watches if isinstance(watches, dict) else {}}


def save_frontier(payload: dict[str, Any]) -> Path:
    return save_json_file(frontier_path(), payload)


def cmd_frontier(args: argparse.Namespace) -> None:
    store = load_frontier()
    watches = store["watches"]

    if args.action == "list":
        if not watches:
            print("No watches yet. Add one: zotero.py frontier add eeg --query \"motor imagery\"")
            return
        dump_json(watches)
        return

    if args.action == "add":
        if not args.name:
            exit_with("Provide a watch name")
        queries = args.query or []
        if not queries:
            exit_with("Provide at least one --query")
        watches[args.name] = {
            "queries": queries,
            "days": clamp_int(args.days, 1, 3650, 30),
            "max": clamp_int(args.max, 1, 100, 10),
            "include_preprints": bool(args.include_preprints),
            "last_run": None,
            "createdAt": now_iso(),
        }
        path = save_frontier(store)
        dump_json({"saved": str(path), "watch": watches[args.name]})
        return

    if args.action == "remove":
        if not args.name or args.name not in watches:
            exit_with(f"Unknown watch: {args.name}")
        removed = watches.pop(args.name)
        save_frontier(store)
        dump_json({"removed": args.name, "queries": removed.get("queries")})
        return

    names = [args.name] if args.name else sorted(watches)
    if not names:
        exit_with("No watches configured; run: zotero.py frontier add <name> --query <text>")

    today = date.today()
    report: dict[str, Any] = {}
    imported_total = 0
    for name in names:
        watch = watches.get(name)
        if not isinstance(watch, dict):
            report[name] = {"error": "unknown watch"}
            continue
        since = None
        if args.since_last and watch.get("last_run"):
            since = str(watch["last_run"])[:10]
        start = since or (today - timedelta(days=int(watch.get("days") or 30))).isoformat()
        candidates = gather_candidates(
            [str(q) for q in watch.get("queries") or []],
            start,
            today.isoformat(),
            include_preprints=bool(watch.get("include_preprints")),
            mailto=args.mailto,
        )
        state = load_daily_state()
        seen = state.get("seen", {}) if isinstance(state.get("seen"), dict) else {}
        api_up = request("/api/", timeout=2).ok
        fresh: list[dict[str, Any]] = []
        for candidate in candidates:
            key = candidate.get("dedupe_key") or ""
            if key and key in seen:
                continue
            if api_up and library_has_item(candidate["title"], candidate["doi"]):
                continue
            fresh.append(candidate)
        fresh = candidate_rows(fresh, int(watch.get("max") or 10))

        if args.do_import and fresh:
            ensure_yes(args, "import frontier results into Zotero")
            import_candidates(fresh)
            imported_total += len(fresh)

        for candidate in fresh:
            key = candidate.get("dedupe_key") or ""
            if key:
                seen[key] = today.isoformat()
        state["seen"] = seen
        save_daily_state(state)

        watch["last_run"] = now_iso()
        report[name] = {
            "window": {"from": start, "to": today.isoformat()},
            "found": len(fresh),
            "results": fresh,
        }

    save_frontier(store)
    if args.out:
        save_json_file(Path(args.out).expanduser().resolve(), report)
    if args.json:
        dump_json(report)
        return
    for name, entry in report.items():
        if "error" in entry:
            print(f"[{name}] error: {entry['error']}")
            continue
        window = entry.get("window") or {}
        print(f"[{name}] {entry.get('found', 0)} new ({window.get('from')} → {window.get('to')})")
        print_candidates(entry.get("results") or [])
    if imported_total:
        print(f"imported {imported_total} records")


def tier_for(score: float) -> str:
    if score >= TIER_CORE:
        return "核心"
    if score >= TIER_IMPORTANT:
        return "重要"
    return "参考"


def library_score(record: dict[str, Any], newest_year: int | None) -> float:
    score = 0.0
    if record.get("doi"):
        score += 0.2
    if record.get("abstract"):
        score += 0.25
    if record.get("venue"):
        score += 0.2
    year = record.get("year")
    if year and newest_year and str(year).isdigit():
        gap = newest_year - int(year)
        score += max(0.0, 0.3 - 0.02 * max(0, gap))
    return round(min(1.0, score), 3)


def screen_markdown(rows: list[dict[str, Any]], title: str, note: str = "") -> str:
    lines = [f"# {title}", ""]
    lines.append("| # | 档位 | 分数 | 年份 | 期刊/会议 | 被引 | OA | 标题 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for index, row in enumerate(rows, start=1):
        tier = row.get("tier") or tier_for(float(row.get("score") or 0))
        score = float(row.get("score") or 0)
        oa = "是" if row.get("oa") else ""
        lines.append(
            "| {index} | {tier} | {score:.3f} | {year} | {venue} | {cites} | {oa} | {title} |".format(
                index=index,
                tier=tier,
                score=score,
                year=row.get("published", "")[:4] or row.get("year") or "",
                venue=md_cell(row.get("venue") or ""),
                cites=row.get("citations", ""),
                oa=oa,
                title=md_cell(row.get("title") or ""),
            )
        )
    lines.append("")
    core = [
        row
        for row in rows
        if (row.get("tier") or tier_for(float(row.get("score") or 0))) == "核心"
    ]
    lines.append(f"共 {len(rows)} 篇，其中核心 {len(core)} 篇。")
    if note:
        lines.append(note)
    return "\n".join(lines) + "\n"


def cmd_screen(args: argparse.Namespace) -> None:
    candidates: list[dict[str, Any]] = []
    if args.input:
        raw = sys.stdin.read() if args.input == "-" else read_text_file(args.input)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            exit_with(f"Cannot parse --input JSON: {exc}")
        if isinstance(payload, dict):
            payload = payload.get("results") or payload.get("candidates") or []
        if not isinstance(payload, list):
            exit_with("--input must contain a JSON list of candidates")
        candidates = [row for row in payload if isinstance(row, dict)]
        for row in candidates:
            row["score"] = float(row.get("score") or 0)
    elif args.query:
        start = (date.today() - timedelta(days=730)).isoformat()
        candidates = gather_candidates(
            args.query, start, date.today().isoformat(), include_preprints=False
        )
    elif args.library:
        if not (args.keys or args.tag or args.collection or args.all):
            args.all = True
        records = [record_from_item(item) for item in selected_items(args)]
        years = [int(row["year"]) for row in records if row.get("year") and str(row["year"]).isdigit()]
        newest = max(years) if years else None
        for record in records:
            record["score"] = library_score(record, newest)
            record["published"] = record.get("year") or ""
        candidates = records
    else:
        exit_with("Provide --input, --query, or --library")

    kept = [row for row in candidates if float(row.get("score") or 0) >= args.min_score]
    kept.sort(key=lambda row: float(row.get("score") or 0), reverse=True)
    rows = kept[: clamp_int(args.top, 1, 200, 15)]
    if args.library:
        # Local items carry no citation counts, so tiers are relative ranks
        # inside the screened set rather than absolute impact scores.
        total = len(rows)
        core_cut = max(1, int(round(total * 0.2)))
        important_cut = max(core_cut, int(round(total * 0.5)))
        for index, row in enumerate(rows):
            if index < core_cut:
                row["tier"] = "核心"
            elif index < important_cut:
                row["tier"] = "重要"
            else:
                row["tier"] = "参考"
    else:
        for row in rows:
            row["tier"] = tier_for(float(row.get("score") or 0))

    note = "本地条目没有引用数据，档位按分数相对排名。" if args.library else ""
    text = screen_markdown(rows, "核心论文筛选", note)
    if args.out:
        path = Path(args.out).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if args.json:
        dump_json(rows)
        return
    print(text, end="")


# --- library management: tag / collect / classify / dupes -------------------


def cmd_authorize(args: argparse.Namespace) -> None:
    if load_local_key().get("key") and not args.force:
        current = load_local_key()
        if args.json:
            dump_json({"status": "already-authorized", **{k: current.get(k) for k in ("appName", "remember", "authorizedAt")}})
            return
        print(
            "A local API key is already stored "
            f"({current.get('authorizedAt')}). Use --force to request a new one."
        )
        return
    record = authorize_zotero(args.app_name)
    if args.json:
        dump_json({"status": "authorized", **{k: record.get(k) for k in ("appName", "remember", "authorizedAt")}})
        return
    remember = record.get("remember")
    print(f"Authorized {record.get('appName')} (persistent={remember}).")
    if not remember:
        print(
            "The key is single-use because \"Always Allow\" was not chosen; "
            "re-run authorize when a write returns 401.",
            file=sys.stderr,
        )


def cmd_tag(args: argparse.Namespace) -> None:
    items = selected_items(args)
    if not items:
        exit_with("No items matched the selection")
    add = split_csv(args.add)
    remove = split_csv(args.remove)
    replace = split_csv(args.replace)
    if not add and not remove and not replace:
        exit_with("Provide --add, --remove, or --replace")
    ensure_yes(args, "modify Zotero tags")

    results: list[dict[str, Any]] = []
    for item in items:
        data = item.get("data", item)
        key = item.get("key") or data.get("key")
        current = [entry.get("tag") for entry in data.get("tags") or [] if entry.get("tag")]
        if replace:
            updated = list(dict.fromkeys(replace))
        else:
            updated = [tag for tag in current if tag not in remove]
            for tag in add:
                if tag not in updated:
                    updated.append(tag)
        if sorted(updated) == sorted(current):
            results.append({"key": key, "tags": updated, "changed": False})
            continue
        outcome = patch_item(key, {"tags": [{"tag": tag} for tag in updated]}, dry_run=args.dry_run)
        outcome.update({"tags": updated, "changed": True})
        results.append(outcome)

    changed = sum(1 for row in results if row.get("changed"))
    dump_json({"items": len(results), "changed": changed, "results": results})


def cmd_collect(args: argparse.Namespace) -> None:
    items = selected_items(args)
    if not items:
        exit_with("No items matched the selection")
    ensure_yes(args, "add items to a Zotero collection")

    record = find_collection(args.name)
    created = False
    if record is None:
        if args.dry_run:
            record = {"key": None, "data": {"name": args.name}}
            created = True
        else:
            record = create_collection(args.name, args.parent)
            created = True
    data = record.get("data", record)
    collection_key = record.get("key") or data.get("key")

    results: list[dict[str, Any]] = []
    for item in items:
        payload = item.get("data", item)
        key = item.get("key") or payload.get("key")
        current = list(payload.get("collections") or [])
        if collection_key and collection_key in current:
            results.append({"key": key, "added": False})
            continue
        updated = current + ([collection_key] if collection_key else [])
        outcome = patch_item(key, {"collections": updated}, dry_run=args.dry_run)
        outcome["added"] = True
        results.append(outcome)

    dump_json(
        {
            "collection": {"name": args.name, "key": collection_key, "created": created},
            "items": len(results),
            "added": sum(1 for row in results if row.get("added")),
            "dryRun": bool(args.dry_run),
            "results": results,
        }
    )


def classify_plan(
    records: list[dict[str, Any]],
    *,
    by_method: bool = True,
    by_dataset: bool = True,
    by_year: bool = False,
    by_venue: bool = False,
    tag_prefix: str = "auto:",
) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for record in records:
        tags: list[str] = []
        collection = None
        if by_method:
            tags.extend(f"{tag_prefix}method:{name}" for name in record.get("methods") or [])
        if by_dataset:
            tags.extend(f"{tag_prefix}dataset:{name}" for name in record.get("datasets") or [])
        if by_year and record.get("year"):
            tags.append(f"{tag_prefix}year:{record['year']}")
        if by_venue and record.get("venue"):
            collection = str(record["venue"])[:120]
        existing = set(record.get("tags") or [])
        missing = [tag for tag in dict.fromkeys(tags) if tag not in existing]
        plan.append(
            {
                "key": record.get("key"),
                "title": record.get("title"),
                "addTags": missing,
                "collection": collection,
            }
        )
    return plan


def cmd_classify(args: argparse.Namespace) -> None:
    records = [record_from_item(item) for item in selected_items(args)]
    if not records:
        exit_with("No items matched the selection")
    plan = classify_plan(
        records,
        by_method=not args.no_method,
        by_dataset=not args.no_dataset,
        by_year=args.by_year,
        by_venue=args.by_venue,
        tag_prefix=args.tag_prefix,
    )
    changed = [row for row in plan if row["addTags"] or row["collection"]]

    if not args.apply:
        if args.json:
            dump_json({"items": len(plan), "toChange": len(changed), "plan": plan})
            return
        lines = [
            f"# 分类整理计划（{len(plan)} 篇，需改动 {len(changed)} 篇）",
            "",
            "只列出将要新增的标签；加 --apply --yes 才会写入 Zotero。",
            "",
            "| 条目 | 新增标签 | 集合 | 标题 |",
            "| --- | --- | --- | --- |",
        ]
        for row in changed:
            lines.append(
                "| `{key}` | {tags} | {collection} | {title} |".format(
                    key=row["key"],
                    tags=", ".join(f"`{tag}`" for tag in row["addTags"]) or "",
                    collection=md_cell(row.get("collection") or ""),
                    title=md_cell(row.get("title") or ""),
                )
            )
        print("\n".join(lines))
        return
    ensure_yes(args, "apply a classification plan")

    collection_keys: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    for row in changed:
        item = item_full(str(row["key"]))
        data = item.get("data", item)
        current_tags = [entry.get("tag") for entry in data.get("tags") or [] if entry.get("tag")]
        merged_tags = current_tags + [tag for tag in row["addTags"] if tag not in current_tags]
        collections = list(data.get("collections") or [])
        name = row.get("collection")
        if name:
            if name not in collection_keys:
                found = find_collection(name)
                if found is None:
                    created = create_collection(name)
                    collection_keys[name] = str(created.get("key") or "")
                else:
                    found_data = found.get("data", found)
                    collection_keys[name] = str(found.get("key") or found_data.get("key") or "")
            key = collection_keys[name]
            if key and key not in collections:
                collections.append(key)
        fields: dict[str, Any] = {}
        if merged_tags != current_tags:
            fields["tags"] = [{"tag": tag} for tag in merged_tags]
        if collections != list(data.get("collections") or []):
            fields["collections"] = collections
        if not fields:
            results.append({"key": row["key"], "changed": False})
            continue
        outcome = patch_item(str(row["key"]), fields, dry_run=args.dry_run)
        outcome["changed"] = True
        results.append(outcome)

    dump_json(
        {
            "items": len(plan),
            "changed": sum(1 for row in results if row.get("changed")),
            "collections": collection_keys,
            "results": results,
        }
    )


def duplicate_groups(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group items that share a DOI (case-insensitive) or a normalized title."""
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    by_key = {str(record.get("key")): record for record in records}
    buckets: dict[str, str] = {}
    for record in records:
        key = str(record.get("key"))
        find(key)
        doi = normalize_doi(record.get("doi"))
        title = normalize_text(record.get("title"))
        for bucket in (f"doi:{doi}" if doi else "", f"title:{title}" if title else ""):
            if not bucket:
                continue
            if bucket in buckets:
                union(buckets[bucket], key)
            else:
                buckets[bucket] = key

    clusters: dict[str, list[dict[str, Any]]] = {}
    for key in by_key:
        clusters.setdefault(find(key), []).append(by_key[key])

    result: list[dict[str, Any]] = []
    for rows in clusters.values():
        if len(rows) < 2:
            continue
        ordered = sorted(
            rows,
            key=lambda row: (
                -len(row.get("tags") or []),
                -len(row.get("abstract") or ""),
                str(row.get("key")),
            ),
        )
        result.append(
            {
                "doi": ordered[0].get("doi"),
                "title": ordered[0].get("title"),
                "keep": ordered[0].get("key"),
                "duplicates": [row.get("key") for row in ordered[1:]],
                "keys": [row.get("key") for row in ordered],
            }
        )
    return result


def cmd_dupes(args: argparse.Namespace) -> None:
    records = [record_from_item(item) for item in selected_items(args)]
    groups = duplicate_groups(records)
    if args.trash:
        ensure_yes(args, "move duplicate items to the Zotero trash")
        for group in groups:
            for key in group["duplicates"]:
                patch_item(str(key), {"deleted": 1}, dry_run=args.dry_run)
    payload = {"items": len(records), "groups": len(groups), "duplicates": groups}
    if args.json:
        dump_json(payload)
        return
    if not groups:
        print("No duplicates found.")
        return
    for group in groups:
        print(f"keep {group['keep']}  dup {', '.join(str(k) for k in group['duplicates'])}")
        print(f"  {group.get('title')}")
        if group.get("doi"):
            print(f"  doi {group['doi']}")


# --- batch reading: digest --------------------------------------------------


def item_attachments(item_key: str) -> list[dict[str, Any]]:
    payload = api_get(f"{LOCAL_USER}/items/{urllib.parse.quote(item_key)}/children")
    return payload if isinstance(payload, list) else []


def attachment_fulltext(attachment_key: str) -> str:
    response = request(
        f"{LOCAL_USER}/items/{urllib.parse.quote(attachment_key)}/fulltext", timeout=20
    )
    if not response.ok:
        return ""
    body = parse_body(response)
    if isinstance(body, dict):
        return str(body.get("content") or "")
    return response.text


DIGEST_HEADING_RE = re.compile(
    r"^(?:(?:[IVXLC]+|\d+(?:\.\d+)*)\.?\s+)?"
    r"(abstract|index terms|introduction|related work[s]?|background|"
    r"proposed (?:method|approach|framework|model|system)|method[s]?|methodology|"
    r"materials and methods|system model|problem formulation|"
    r"experiment[s]?(?: and results)?|experimental (?:setup|results)|results|"
    r"results and discussion|evaluation|discussion|conclusions?|"
    r"conclusions? and future work|future work|limitations|acknowledg(?:e?ments?)?)"
    r"\s*$",
    re.IGNORECASE,
)


def text_outline(text: str, limit: int = 14) -> list[tuple[str, int]]:
    outline: list[tuple[str, int]] = []
    for raw_line in str(text or "").splitlines():
        line = " ".join(raw_line.split())
        if not line or len(line) > 80:
            continue
        if DIGEST_HEADING_RE.match(line):
            if outline and outline[-1][0].lower() == line.lower():
                continue
            outline.append((line, len(outline) + 1))
            if len(outline) >= limit:
                break
    return outline


def slugify(text: str, limit: int = 60) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", str(text or "").strip().lower())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned[:limit] or "paper"


def md_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def wrap_paragraphs(text: str, width: int = 100) -> str:
    paragraphs = [block.strip() for block in str(text or "").split("\n") if block.strip()]
    return "\n\n".join(paragraphs) if paragraphs else ""


def cmd_digest(args: argparse.Namespace) -> None:
    records = [record_from_item(item) for item in selected_items(args)]
    if not records:
        exit_with("No items matched the selection")
    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    limit = clamp_int(args.limit, 1, 500, 50)
    records = records[:limit]
    index_rows: list[tuple[str, str, str, str]] = []
    written: list[dict[str, Any]] = []

    for record in records:
        key = str(record.get("key") or "")
        filename = f"{key}-{slugify(record.get('title') or '')}.md"
        path = out_dir / filename
        abstract = wrap_paragraphs(record.get("abstract") or "")
        sections: list[str] = []
        fulltext_chars = 0
        if args.fulltext:
            for attachment in item_attachments(key):
                data = attachment.get("data", attachment)
                if data.get("itemType") != "attachment":
                    continue
                body = attachment_fulltext(str(attachment.get("key")))
                if not body:
                    continue
                fulltext_chars = max(fulltext_chars, len(body))
                outline = text_outline(body)
                if outline:
                    sections.extend(f"- {title}" for title, _ in outline)
                break

        lines = [
            f"# {record.get('title')}",
            "",
            f"- 条目: `{key}`",
            f"- 年份: {record.get('year') or '未知'}",
            f"- 期刊/会议: {record.get('venue') or '未知'}",
            f"- DOI: {record.get('doi') or '无'}",
            f"- 标签: {', '.join(record.get('tags') or []) or '无'}",
            f"- 方法线索: {', '.join(record.get('methods') or []) or '未识别'}",
            f"- 数据集线索: {', '.join(record.get('datasets') or []) or '未识别'}",
        ]
        if record.get("url"):
            lines.append(f"- 链接: {record['url']}")
        if args.fulltext:
            lines.append(f"- 全文缓存: {fulltext_chars} 字符")
        lines.extend(["", "## 摘要", "", abstract or "_无摘要_", "", "## 阅读提示", ""])
        if sections:
            lines.extend(["从全文缓存识别到的章节：", ""])
            lines.extend(sections)
        else:
            lines.append("未找到全文缓存；先在 Zotero 中为条目建立 PDF 索引，或阅读原文。")
        lines.extend(["", "## 笔记", "", "- 结论：", "- 方法：", "- 数据：", "- 可复用点：", ""])
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        index_rows.append(
            (
                key,
                str(record.get("year") or ""),
                md_cell(record.get("venue") or ""),
                f"[{md_cell(record.get('title') or key)}]({filename})",
            )
        )
        written.append({"key": key, "path": str(path), "fulltextChars": fulltext_chars})

    index = ["# 批量阅读包", "", f"共 {len(written)} 篇，生成于 {now_iso()}。", ""]
    index.append("| 条目 | 年份 | 期刊/会议 | 标题 |")
    index.append("| --- | --- | --- | --- |")
    for row in index_rows:
        index.append("| {0} | {1} | {2} | {3} |".format(*row))
    index.append("")
    (out_dir / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    save_json_file(out_dir / "digest.json", {"generatedAt": now_iso(), "items": written})

    payload = {"out": str(out_dir), "items": len(written), "index": str(out_dir / "index.md")}
    dump_json(payload) if args.json else print(f"wrote {len(written)} notes to {out_dir}")


# --- landscape & gaps -------------------------------------------------------


def tag_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        for tag in record.get("tags") or []:
            counts[tag] = counts.get(tag, 0) + 1
    return dict(sorted(counts.items(), key=lambda row: row[1], reverse=True))


def year_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        year = str(record.get("year") or "")
        if year.isdigit():
            counts[year] = counts.get(year, 0) + 1
    return dict(sorted(counts.items()))


def term_counts(records: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        for term in record.get(field) or []:
            counts[term] = counts.get(term, 0) + 1
    return dict(sorted(counts.items(), key=lambda row: row[1], reverse=True))


def cooccurrence(
    records: list[dict[str, Any]], left_field: str, right_field: str
) -> dict[tuple[str, str], int]:
    matrix: dict[tuple[str, str], int] = {}
    for record in records:
        for left in record.get(left_field) or []:
            for right in record.get(right_field) or []:
                matrix[(left, right)] = matrix.get((left, right), 0) + 1
    return matrix


def landscape_payload(records: list[dict[str, Any]]) -> dict[str, Any]:
    years = year_counts(records)
    recent_years = sorted(years)[-3:]
    earlier_years = sorted(years)[:-3]
    recent = sum(years[year] for year in recent_years)
    earlier = sum(years[year] for year in earlier_years)
    venues = term_counts(
        [dict(record, venue_list=[record["venue"]] if record.get("venue") else []) for record in records],
        "venue_list",
    )
    matrix = cooccurrence(records, "methods", "datasets")
    top_methods = list(term_counts(records, "methods"))[:10]
    top_datasets = list(term_counts(records, "datasets"))[:10]
    grid = [
        {method: matrix.get((method, dataset), 0) for method in top_methods}
        for dataset in top_datasets
    ]
    return {
        "items": len(records),
        "years": years,
        "venues": dict(list(venues.items())[:15]),
        "tags": dict(list(tag_counts(records).items())[:25]),
        "methods": term_counts(records, "methods"),
        "datasets": term_counts(records, "datasets"),
        "matrix": {
            "methods": top_methods,
            "datasets": top_datasets,
            "counts": grid,
        },
        "growth": {
            "recentYears": recent_years,
            "recentCount": recent,
            "earlierYears": earlier_years,
            "earlierCount": earlier,
        },
    }


def landscape_markdown(payload: dict[str, Any]) -> str:
    lines = ["# 研究脉络", "", f"样本 {payload['items']} 篇。", ""]
    lines.append("## 年度分布")
    lines.append("")
    lines.append("| 年份 | 篇数 |")
    lines.append("| --- | --- |")
    for year, count in payload["years"].items():
        lines.append(f"| {year} | {count} |")
    growth = payload["growth"]
    if growth.get("earlierCount") or growth.get("recentCount"):
        lines.append("")
        lines.append(
            "近三年 {recent} 篇，此前 {earlier} 篇（{years}）。".format(
                recent=growth["recentCount"],
                earlier=growth["earlierCount"],
                years=", ".join(growth.get("recentYears") or []) or "无",
            )
        )

    lines.extend(["", "## 主要期刊/会议", ""])
    if payload["venues"]:
        lines.append("| 期刊/会议 | 篇数 |")
        lines.append("| --- | --- |")
        for venue, count in payload["venues"].items():
            lines.append(f"| {md_cell(venue)} | {count} |")
    else:
        lines.append("样本中没有期刊/会议字段。")

    lines.extend(["", "## 方法分布", ""])
    if payload["methods"]:
        lines.append("| 方法 | 篇数 |")
        lines.append("| --- | --- |")
        for method, count in payload["methods"].items():
            lines.append(f"| {method} | {count} |")
    else:
        lines.append("未识别到方法线索（标题与摘要中没有命中术语表）。")

    lines.extend(["", "## 数据集分布", ""])
    if payload["datasets"]:
        lines.append("| 数据集 | 篇数 |")
        lines.append("| --- | --- |")
        for dataset, count in payload["datasets"].items():
            lines.append(f"| {dataset} | {count} |")
    else:
        lines.append("未识别到数据集线索。")

    matrix = payload["matrix"]
    if matrix["methods"] and matrix["datasets"]:
        lines.extend(["", "## 方法 × 数据集", ""])
        header = "| 数据集 \\ 方法 | " + " | ".join(matrix["methods"]) + " |"
        lines.append(header)
        lines.append("| --- " * (len(matrix["methods"]) + 1) + "|")
        for index, dataset in enumerate(matrix["datasets"]):
            row = matrix["counts"][index]
            cells = " | ".join(str(row.get(method, 0)) for method in matrix["methods"])
            lines.append(f"| {dataset} | {cells} |")

    lines.extend(["", "## 标签分布", ""])
    if payload["tags"]:
        lines.append("| 标签 | 篇数 |")
        lines.append("| --- | --- |")
        for tag, count in payload["tags"].items():
            lines.append(f"| {md_cell(tag)} | {count} |")
    else:
        lines.append("样本没有标签。")
    return "\n".join(lines) + "\n"


def cmd_landscape(args: argparse.Namespace) -> None:
    records = [record_from_item(item) for item in selected_items(args)]
    if not records:
        exit_with("No items matched the selection")
    payload = landscape_payload(records)
    text = landscape_markdown(payload)
    if args.out:
        path = Path(args.out).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if args.json:
        dump_json(payload)
        return
    print(text, end="")


def grouping_prefix(records: list[dict[str, Any]]) -> str:
    for prefix in ("project:", "topic:"):
        for record in records:
            for tag in record.get("tags") or []:
                if str(tag).startswith(prefix):
                    return prefix
    return ""


def group_records(
    records: list[dict[str, Any]], prefix: str
) -> dict[str, list[dict[str, Any]]]:
    if not prefix:
        return {"全部文献": records}
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        names = [
            str(tag)[len(prefix) :]
            for tag in record.get("tags") or []
            if str(tag).startswith(prefix)
        ]
        if not names:
            groups.setdefault("未分组", []).append(record)
            continue
        for name in names:
            groups.setdefault(name, []).append(record)
    return dict(sorted(groups.items(), key=lambda row: len(row[1]), reverse=True))


def group_gap_block(rows: list[dict[str, Any]], min_freq: int) -> dict[str, Any]:
    method_counts = term_counts(rows, "methods")
    dataset_counts = term_counts(rows, "datasets")
    matrix = cooccurrence(rows, "methods", "datasets")
    frequent_methods = [name for name, count in method_counts.items() if count >= min_freq]
    frequent_datasets = [name for name, count in dataset_counts.items() if count >= min_freq]
    empty_cells = [
        {
            "method": method,
            "dataset": dataset,
            "methodCount": method_counts[method],
            "datasetCount": dataset_counts[dataset],
        }
        for method in frequent_methods
        for dataset in frequent_datasets
        if matrix.get((method, dataset), 0) == 0
    ]
    empty_cells.sort(key=lambda row: row["methodCount"] * row["datasetCount"], reverse=True)

    tag_years: dict[str, list[int]] = {}
    for record in rows:
        year = str(record.get("year") or "")
        if not year.isdigit():
            continue
        for tag in record.get("tags") or []:
            tag_years.setdefault(str(tag), []).append(int(year))
    cooled = [
        {"tag": tag, "lastYear": max(years), "count": len(years)}
        for tag, years in tag_years.items()
        if len(years) >= min_freq and max(years) <= date.today().year - 2
    ]
    cooled.sort(key=lambda row: (row["lastYear"], -row["count"]))
    unlabeled = [
        {"key": record.get("key"), "title": record.get("title")}
        for record in rows
        if not record.get("methods") or not record.get("datasets")
    ]
    return {
        "items": len(rows),
        "emptyCells": empty_cells[:12],
        "cooledTags": cooled[:10],
        "unlabeled": unlabeled[:20],
        "counts": {"methods": method_counts, "datasets": dataset_counts},
    }


def gap_payload(
    records: list[dict[str, Any]], min_freq: int = 2, prefix: str = ""
) -> dict[str, Any]:
    groups = group_records(records, prefix)
    payload: dict[str, Any] = {
        "items": len(records),
        "groupKey": prefix or None,
        "groups": [],
    }
    for name, rows in groups.items():
        block = group_gap_block(rows, min_freq)
        block["name"] = name
        payload["groups"].append(block)
    return payload


def gap_markdown(payload: dict[str, Any]) -> str:
    lines = ["# 研究空白候选", ""]
    lines.append(
        f"样本 {payload['items']} 篇；按 {payload['groupKey'] or '全部文献'} 分组推导。"
    )
    lines.append("这些是规则推断的候选空白，不是结论，请结合原文核实。")
    for group in payload["groups"]:
        lines.extend(["", f"## {group['name']}（{group['items']} 篇）", ""])
        lines.append("### 方法 × 数据集：从未组合过")
        lines.append("")
        if group["emptyCells"]:
            lines.append("| 方法 | 数据集 | 方法出现 | 数据集出现 |")
            lines.append("| --- | --- | --- | --- |")
            for row in group["emptyCells"]:
                lines.append(
                    f"| {row['method']} | {row['dataset']} | {row['methodCount']} | "
                    f"{row['datasetCount']} |"
                )
            lines.append("")
            lines.append("两边都有积累、却没有交叉，优先检查这些组合。")
        else:
            lines.append("没有发现明显空组合（或术语表覆盖不足）。")

        lines.extend(["", "### 冷却的标签", ""])
        if group["cooledTags"]:
            lines.append("| 标签 | 最近年份 | 篇数 |")
            lines.append("| --- | --- | --- |")
            for row in group["cooledTags"]:
                lines.append(f"| {md_cell(row['tag'])} | {row['lastYear']} | {row['count']} |")
        else:
            lines.append("没有长期停更的标签。")

        lines.extend(["", "### 标注缺失（需人工补方法/数据集）", ""])
        if group["unlabeled"]:
            for row in group["unlabeled"]:
                lines.append(f"- `{row['key']}` {row['title']}")
        else:
            lines.append("全部条目都识别到方法与数据集线索。")
    lines.append("")
    return "\n".join(lines) + "\n"


def cmd_gaps(args: argparse.Namespace) -> None:
    records = [record_from_item(item) for item in selected_items(args)]
    if not records:
        exit_with("No items matched the selection")
    payload = gap_payload(
        records,
        min_freq=clamp_int(args.min_freq, 1, 20, 2),
        prefix=grouping_prefix(records),
    )
    text = gap_markdown(payload)
    if args.out:
        path = Path(args.out).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if args.json:
        dump_json(payload)
        return
    print(text, end="")


def add_selection_args(
    parser: argparse.ArgumentParser, *, with_query: bool = True
) -> None:
    parser.add_argument("--keys", help="Comma-separated Zotero item keys")
    if with_query:
        parser.add_argument("--query", help="Match top-level items by title or author text")
    parser.add_argument("--tag", help="Items carrying this tag")
    parser.add_argument("--collection", help="Collection name or key")
    parser.add_argument("--all", action="store_true", help="Every top-level item")
    parser.add_argument("--limit", type=int, default=200, help="Maximum items to touch")


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

    authorize = subcommands.add_parser(
        "authorize", help="Authorize local API writes (Zotero shows a permission dialog)"
    )
    authorize.add_argument("--app-name", default=WRITE_APP_NAME)
    authorize.add_argument("--force", action="store_true", help="Request a fresh key")
    add_json_flag(authorize)
    authorize.set_defaults(func=cmd_authorize)

    find_cmd = subcommands.add_parser(
        "find", help="Find literature on OpenAlex, ranked by venue, citations, and recency"
    )
    find_cmd.add_argument("query", nargs="+", help="One or more queries")
    find_cmd.add_argument("--from", dest="date_from", help="Start date YYYY-MM-DD")
    find_cmd.add_argument("--to", dest="date_to", help="End date YYYY-MM-DD")
    find_cmd.add_argument("--days", type=int, help="Look back N days instead of --from")
    find_cmd.add_argument("--limit", type=int, default=20, help="Rows to keep (1-100)")
    find_cmd.add_argument("--min-score", type=float, default=0.0)
    find_cmd.add_argument("--oa", action="store_true", help="Only open-access works")
    find_cmd.add_argument(
        "--type", dest="work_type", choices=["article", "review", "preprint"]
    )
    find_cmd.add_argument("--include-preprints", action="store_true")
    find_cmd.add_argument(
        "--include-library", action="store_true", help="Keep works already in Zotero"
    )
    find_cmd.add_argument("--mailto", default="", help="Contact email for the OpenAlex pool")
    find_cmd.add_argument("--bib", help="Write results as BibTeX to this file")
    find_cmd.add_argument("--out", help="Write the JSON result list to this file")
    find_cmd.add_argument(
        "--import", dest="do_import", action="store_true", help="Import results via Connector"
    )
    find_cmd.add_argument("--yes", action="store_true")
    add_json_flag(find_cmd)
    find_cmd.set_defaults(func=cmd_find)

    frontier = subcommands.add_parser(
        "frontier", help="Track research fronts with saved watches"
    )
    frontier.add_argument("action", choices=["add", "list", "remove", "run"])
    frontier.add_argument("name", nargs="?", help="Watch name")
    frontier.add_argument("--query", action="append", help="Query for 'add' (repeatable)")
    frontier.add_argument("--days", type=int, default=30, help="Window size in days")
    frontier.add_argument("--max", type=int, default=10, help="Rows kept per watch")
    frontier.add_argument("--include-preprints", action="store_true")
    frontier.add_argument(
        "--since-last", action="store_true", help="Start the window at the previous run"
    )
    frontier.add_argument("--import", dest="do_import", action="store_true")
    frontier.add_argument("--yes", action="store_true")
    frontier.add_argument("--mailto", default="")
    frontier.add_argument("--out", help="Write the JSON report to this file")
    add_json_flag(frontier)
    frontier.set_defaults(func=cmd_frontier)

    screen = subcommands.add_parser(
        "screen", help="Screen candidates into 核心 / 重要 / 参考 tiers"
    )
    screen.add_argument("--input", help="JSON list from find/frontier (use - for stdin)")
    screen.add_argument("--query", nargs="+", help="Run a fresh OpenAlex search first")
    screen.add_argument("--library", action="store_true", help="Screen items already in Zotero")
    screen.add_argument("--keys", help="Comma-separated item keys (with --library)")
    screen.add_argument("--tag", help="Items carrying this tag (with --library)")
    screen.add_argument("--collection", help="Collection name or key (with --library)")
    screen.add_argument("--all", action="store_true", help="Every top-level item (with --library)")
    screen.add_argument("--top", type=int, default=15, help="Rows to keep")
    screen.add_argument("--min-score", type=float, default=0.0)
    screen.add_argument("--limit", type=int, default=200, help="Items scanned in library mode")
    screen.add_argument("--out", help="Write a Markdown shortlist to this file")
    add_json_flag(screen)
    screen.set_defaults(func=cmd_screen)

    tag = subcommands.add_parser("tag", help="Add, remove, or replace tags on Zotero items")
    add_selection_args(tag)
    tag.add_argument("--add", help="Comma-separated tags to add")
    tag.add_argument("--remove", help="Comma-separated tags to remove")
    tag.add_argument("--replace", help="Comma-separated tags that replace the current set")
    tag.add_argument("--dry-run", action="store_true", help="Preview without writing")
    tag.add_argument("--yes", action="store_true", help="Confirm the Zotero write")
    tag.set_defaults(func=cmd_tag)

    collect = subcommands.add_parser("collect", help="Add items to a collection (created on demand)")
    collect.add_argument("name", help="Collection name (or key)")
    add_selection_args(collect, with_query=True)
    collect.add_argument("--parent", help="Parent collection name for a new subcollection")
    collect.add_argument("--dry-run", action="store_true")
    collect.add_argument("--yes", action="store_true")
    collect.set_defaults(func=cmd_collect)

    classify = subcommands.add_parser(
        "classify", help="Derive method/dataset/venue rules and apply them as tags or collections"
    )
    add_selection_args(classify)
    classify.add_argument("--apply", action="store_true", help="Write the plan to Zotero")
    classify.add_argument("--by-year", action="store_true", help="Also tag year:<YYYY>")
    classify.add_argument("--by-venue", action="store_true", help="Also collect per venue")
    classify.add_argument("--no-method", action="store_true", help="Skip method tags")
    classify.add_argument("--no-dataset", action="store_true", help="Skip dataset tags")
    classify.add_argument("--tag-prefix", default="auto:")
    classify.add_argument("--dry-run", action="store_true")
    classify.add_argument("--yes", action="store_true")
    add_json_flag(classify)
    classify.set_defaults(func=cmd_classify)

    dupes = subcommands.add_parser("dupes", help="Group duplicate items by DOI or normalized title")
    add_selection_args(dupes)
    dupes.add_argument("--trash", action="store_true", help="Move non-keeper copies to trash")
    dupes.add_argument("--dry-run", action="store_true")
    dupes.add_argument("--yes", action="store_true")
    add_json_flag(dupes)
    dupes.set_defaults(func=cmd_dupes)

    digest = subcommands.add_parser(
        "digest", help="Write a batch reading pack (one Markdown note per item)"
    )
    add_selection_args(digest)
    digest.add_argument("--out", required=True, help="Output directory")
    digest.add_argument("--fulltext", action="store_true", help="Add section outlines from PDF text")
    add_json_flag(digest)
    digest.set_defaults(func=cmd_digest)

    landscape = subcommands.add_parser(
        "landscape", help="Summarize years, venues, methods, and datasets for a selection"
    )
    add_selection_args(landscape)
    landscape.add_argument("--out", help="Write the Markdown report to this file")
    add_json_flag(landscape)
    landscape.set_defaults(func=cmd_landscape)

    gaps = subcommands.add_parser(
        "gaps", help="Propose research gaps from method/dataset combinations and stale tags"
    )
    add_selection_args(gaps)
    gaps.add_argument("--min-freq", type=int, default=2, help="Minimum term frequency to consider")
    gaps.add_argument("--out", help="Write the Markdown report to this file")
    add_json_flag(gaps)
    gaps.set_defaults(func=cmd_gaps)

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
