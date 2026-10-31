"""Atomic local preferences and immutable, content-addressed reading results."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from contextlib import contextmanager

DEFAULTS = {
    "outputs": [], "mode": "quick", "depth": "intermediate",
    "layout": "side-by-side", "annotation": "concise", "priority": "card",
    "execution": "economy", "max_workers": 2, "profile": "",
}
CHOICES = {
    "mode": {"quick", "deep"}, "depth": {"beginner", "intermediate", "research"},
    "layout": {"side-by-side", "stacked", "alternating"},
    "annotation": {"concise", "detailed"}, "priority": {"card", "notes", "translation"},
    "execution": {"economy", "parallel"},
}
OUTPUTS = {"notes", "translation", "annotated-pdf", "bilingual-pdf", "zotero-note"}


def root():
    return Path(os.environ.get("ZOTERO_SKILL_CONFIG_DIR", Path.home() / ".zotero-skill"))


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise ValueError("Use a name containing 1-80 letters, numbers, hyphens or underscores")
    return value


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".reader-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def lock(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".reader.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError(f"Reader job is busy: {path}. If a process crashed, remove this lock after checking it stopped.")
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def settings(overrides=None):
    saved = read(root() / "reader-settings.json", {})
    values = dict(DEFAULTS)
    values.update(saved)
    values.update({k: v for k, v in (overrides or {}).items() if v is not None})
    unknown = set(values) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown reader settings: {sorted(unknown)}")
    for key, allowed in CHOICES.items():
        if values[key] not in allowed:
            raise ValueError(f"Invalid {key}; choose from {sorted(allowed)}")
    if not isinstance(values["outputs"], list) or not set(values["outputs"]) <= OUTPUTS:
        raise ValueError(f"outputs must be a list from {sorted(OUTPUTS)}")
    values["outputs"] = list(dict.fromkeys(values["outputs"]))
    if type(values["max_workers"]) is not int or not 1 <= values["max_workers"] <= 4:
        raise ValueError("max_workers must be an integer from 1 to 4")
    if values["profile"]:
        identifier(values["profile"])
    return values


def profile(name):
    if not name:
        return {"question": "", "keywords": [], "priorities": [], "open_questions": []}
    record = read(root() / "reader-profiles" / (identifier(name) + ".json"))
    if record is None:
        raise ValueError(f"Unknown research profile: {name}")
    return record
