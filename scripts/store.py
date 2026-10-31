"""Storage layer: memory entries as Markdown + YAML frontmatter files.

Design goals, in priority order:
1. Human readable and hand-editable -- the user can open any entry and fix it.
2. Git friendly -- one file per memory means clean diffs and history.
3. Blast radius of one entry -- a corrupted file never takes down the store.
4. Idempotent writes -- re-adding identical content yields the same id.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

VALID_KINDS = (
    "fact",        # durable truths about the user, environment, or domain
    "preference",  # how the user wants things done
    "decision",    # a choice made, with its rationale
    "lesson",      # what went wrong and why (post-mortem knowledge)
    "entity",      # a person, project, system, or product worth tracking
    "task",        # in-flight work state that outlives a single session
    "snippet",     # a reusable command, config, or code fragment
)

VALID_SCOPES = ("global", "project")
VALID_CONFIDENCE = ("high", "medium", "low")

# Half-life in days used by the recency signal during ranking.
HALFLIFE_BY_KIND = {
    "preference": 540.0,
    "decision": 360.0,
    "lesson": 360.0,
    "fact": 270.0,
    "entity": 270.0,
    "snippet": 180.0,
    "task": 30.0,
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def today_stamp() -> str:
    return datetime.now().strftime("%Y%m%d")


# --- Frontmatter serialization --------------------------------------------


def _quote(value: str) -> str:
    """Quote a scalar only when the bare form would be ambiguous."""
    if value == "":
        return '""'
    needs = (
        any(ch in value for ch in ",[]{}#&*!|>'\"%@`")
        or value.strip() != value
        or value.lower() in ("true", "false", "null", "none", "yes", "no")
        or value.startswith("-")
    )
    if needs:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _dump_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        return "[" + ", ".join(_quote(str(v)) for v in value) + "]"
    return _quote(str(value))


def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw == "" or raw.lower() in ("null", "none", "~"):
        return None
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        return raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
        return raw[1:-1].replace("''", "'")
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _parse_flow_list(raw: str) -> list[str]:
    inner = raw.strip()[1:-1].strip()
    if not inner:
        return []
    items, buf, in_quote, quote_ch = [], [], False, ""
    for ch in inner:
        if in_quote:
            buf.append(ch)
            if ch == quote_ch:
                in_quote = False
            continue
        if ch in "\"'":
            in_quote, quote_ch = True, ch
            buf.append(ch)
        elif ch == ",":
            items.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        items.append("".join(buf))
    return [str(_parse_scalar(i.strip()) or "").strip() for i in items if i.strip()]


def parse_document(text: str) -> tuple[dict[str, Any], str]:
    """Split a Markdown document into (frontmatter dict, body)."""
    text = text.replace("\r\n", "\n").lstrip("\ufeff")
    if not text.startswith("---"):
        return {}, text.strip()

    lines = text.split("\n")
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() in ("---", "..."):
            end = idx
            break
    if end is None:
        return {}, text.strip()

    meta: dict[str, Any] = {}
    pending_key: str | None = None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and pending_key:
            meta.setdefault(pending_key, [])
            if not isinstance(meta[pending_key], list):
                meta[pending_key] = []
            meta[pending_key].append(_parse_scalar(stripped[2:]))
            continue
        if ":" not in stripped:
            continue
        key, _, raw = stripped.partition(":")
        key = key.strip()
        raw = raw.strip()
        if raw.startswith("[") and raw.endswith("]"):
            meta[key] = _parse_flow_list(raw)
            pending_key = None
        elif raw == "":
            meta[key] = []
            pending_key = key
        else:
            meta[key] = _parse_scalar(raw)
            pending_key = None

    body = "\n".join(lines[end + 1 :]).strip()
    return meta, body


def render_document(entry: "Entry") -> str:
    ordered = [
        "id", "title", "kind", "scope", "project", "created", "updated",
        "tags", "importance", "confidence", "source", "expires",
        "supersedes", "related",
    ]
    data = entry.to_dict()
    lines = ["---"]
    for key in ordered:
        lines.append(f"{key}: {_dump_value(data.get(key))}")
    for key, value in data.items():
        if key not in ordered:
            lines.append(f"{key}: {_dump_value(value)}")
    lines.append("---")
    lines.append("")
    lines.append(entry.body.strip())
    lines.append("")
    return "\n".join(lines)


# --- Entry model -----------------------------------------------------------


@dataclass
class Entry:
    title: str
    body: str = ""
    kind: str = "fact"
    scope: str = "global"
    project: str | None = None
    id: str = ""
    created: str = ""
    updated: str = ""
    tags: list[str] = field(default_factory=list)
    importance: int = 3
    confidence: str = "high"
    source: str = "conversation"
    expires: str | None = None
    supersedes: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("body", None)
        return data

    @property
    def searchable_text(self) -> str:
        """Text fed to the index: title weighted by repetition, then tags, body."""
        parts = [self.title, self.title, " ".join(self.tags), self.body]
        return "\n".join(p for p in parts if p)

    def is_expired(self, at: datetime | None = None) -> bool:
        if not self.expires:
            return False
        at = at or datetime.now().astimezone()
        try:
            raw = str(self.expires).replace("Z", "+00:00")
            if len(raw) == 10:
                exp = datetime.fromisoformat(raw).replace(tzinfo=at.tzinfo)
            else:
                exp = datetime.fromisoformat(raw)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=at.tzinfo)
            return exp < at
        except ValueError:
            return False

    def age_days(self, at: datetime | None = None) -> float:
        at = at or datetime.now().astimezone()
        try:
            raw = str(self.updated or self.created).replace("Z", "+00:00")
            ts = datetime.fromisoformat(raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=at.tzinfo)
            return max(0.0, (at - ts).total_seconds() / 86400.0)
        except (ValueError, TypeError):
            return 0.0


def make_entry_id(title: str, body: str) -> str:
    """Deterministic id: same title+body always maps to the same entry."""
    digest = hashlib.sha256(f"{title.strip()}\n{body.strip()}".encode("utf-8"))
    return f"{today_stamp()}-{digest.hexdigest()[:8]}"


# --- Vault -----------------------------------------------------------------


class Vault:
    """A directory holding memory entries plus a derived search index."""

    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser()
        self.entries_dir = self.root / "entries"
        self.index_dir = self.root / "index"
        self.trash_dir = self.root / ".trash"
        self.index_path = self.index_dir / "index.json"
        self.config_path = self.root / "config.json"

    # -- lifecycle --

    def ensure(self) -> None:
        for path in (self.root, self.entries_dir, self.index_dir):
            path.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.entries_dir.is_dir()

    def config(self) -> dict[str, Any]:
        if self.config_path.is_file():
            try:
                return json.loads(self.config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def write_config(self, data: dict[str, Any]) -> None:
        self.ensure()
        self.config_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # -- entries --

    def entry_path(self, entry: Entry) -> Path:
        stamp = (entry.created or now_iso())[:10].replace("-", "") or today_stamp()
        return self.entries_dir / stamp[:4] / stamp[4:6] / f"{entry.id}.md"

    def iter_paths(self) -> Iterator[Path]:
        if not self.entries_dir.is_dir():
            return
        for path in sorted(self.entries_dir.rglob("*.md")):
            yield path

    def load(self, path: Path) -> Entry | None:
        try:
            meta, body = parse_document(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return None
        if not meta.get("id") and not meta.get("title"):
            return None
        entry = Entry(
            title=str(meta.get("title") or "(untitled)"),
            body=body,
            kind=str(meta.get("kind") or "fact"),
            scope=str(meta.get("scope") or "global"),
            project=meta.get("project"),
            id=str(meta.get("id") or path.stem),
            created=str(meta.get("created") or ""),
            updated=str(meta.get("updated") or ""),
            tags=[str(t) for t in (meta.get("tags") or [])],
            importance=int(meta.get("importance") or 3),
            confidence=str(meta.get("confidence") or "high"),
            source=str(meta.get("source") or "conversation"),
            expires=meta.get("expires"),
            supersedes=[str(s) for s in (meta.get("supersedes") or [])],
            related=[str(s) for s in (meta.get("related") or [])],
        )
        entry.importance = max(1, min(5, entry.importance))
        return entry

    def all_entries(self) -> list[Entry]:
        entries = []
        for path in self.iter_paths():
            entry = self.load(path)
            if entry is not None:
                entry._path = path  # type: ignore[attr-defined]
                entries.append(entry)
        return entries

    def save(self, entry: Entry) -> Path:
        self.ensure()
        if not entry.id:
            entry.id = make_entry_id(entry.title, entry.body)
        stamp = now_iso()
        entry.created = entry.created or stamp
        entry.updated = stamp
        path = self.entry_path(entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(render_document(entry), encoding="utf-8")
        os.replace(tmp, path)
        entry._path = path  # type: ignore[attr-defined]
        return path

    def find(self, entry_id: str) -> tuple[Entry, Path] | None:
        for path in self.iter_paths():
            if path.stem == entry_id:
                entry = self.load(path)
                if entry is not None:
                    return entry, path
        return None

    def delete(self, entry_id: str) -> bool:
        found = self.find(entry_id)
        if not found:
            return False
        _, path = found
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        target = self.trash_dir / f"{path.stem}.{datetime.now():%Y%m%d%H%M%S}.md"
        shutil.move(str(path), str(target))
        return True

    def stats(self) -> dict[str, Any]:
        entries = self.all_entries()
        by_kind: dict[str, int] = {}
        by_scope: dict[str, int] = {}
        for entry in entries:
            by_kind[entry.kind] = by_kind.get(entry.kind, 0) + 1
            by_scope[entry.scope] = by_scope.get(entry.scope, 0) + 1
        newest = max((e.updated or e.created for e in entries), default=None)
        return {
            "root": str(self.root),
            "total": len(entries),
            "by_kind": by_kind,
            "by_scope": by_scope,
            "newest": newest,
        }

    # -- index --

    def index_is_stale(self) -> bool:
        if not self.index_path.is_file():
            return True
        try:
            index_mtime = self.index_path.stat().st_mtime
        except OSError:
            return True
        for path in self.iter_paths():
            try:
                if path.stat().st_mtime > index_mtime:
                    return True
            except OSError:
                continue
        return False

    def read_index(self) -> dict[str, Any] | None:
        if not self.index_path.is_file():
            return None
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def write_index(self, data: dict[str, Any]) -> None:
        self.ensure()
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.index_path)
