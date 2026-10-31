"""cross-session-memory CLI.

Single entry point for reading and writing durable memory that outlives a
single conversation. Designed so an agent can call it with one shell command
and parse either the compact text form or `--json`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import retrieval
import store
import textproc
from store import Entry, Vault

DEFAULT_GLOBAL = Path.home() / ".workbuddy-ai" / "memory-vault"
PROJECT_MARKERS = (".workbuddy-ai", ".git")


# --- Vault resolution ------------------------------------------------------


def default_global_root() -> Path:
    env = os.environ.get("CSM_VAULT")
    return Path(env).expanduser() if env else DEFAULT_GLOBAL


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk up to the nearest real project root.

    Two candidates are explicitly rejected, because treating them as projects
    would silently mix user config with project memory:
      - the home directory itself
      - anything living inside a `.workbuddy-ai` config tree
    """
    home = Path.home().resolve()
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if candidate == home or ".workbuddy-ai" in candidate.parts:
            continue
        if any((candidate / marker).exists() for marker in PROJECT_MARKERS):
            return candidate
    return None


def project_vault_root(start: Path | None = None) -> Path | None:
    root = find_project_root(start)
    return (root / ".workbuddy-ai" / "memory") if root else None


class Memory:
    """A view over the global vault plus an optional project-local vault."""

    def __init__(self, global_root: Path, local_root: Path | None = None):
        self.global_vault = Vault(global_root)
        self.local_vault = Vault(local_root) if local_root else None
        self._index: dict | None = None

    @property
    def vaults(self) -> list[tuple[str, Vault]]:
        pairs = [("global", self.global_vault)]
        if self.local_vault is not None and self.local_vault.exists():
            pairs.append(("project", self.local_vault))
        return pairs

    def ensure_index(self, force: bool = False) -> dict:
        if self._index is not None and not force:
            return self._index
        cache = self.global_vault
        expected_roots = [f"{label}:{vault.root}" for label, vault in self.vaults]
        stale = force or cache.index_is_stale() or any(
            v.index_is_stale() for _, v in self.vaults
        )
        if not stale:
            data = cache.read_index()
            if (
                data
                and data.get("version") == retrieval.INDEX_VERSION
                and data.get("vault_roots") == expected_roots
            ):
                self._index = data
                return data
        data = retrieval.build_index(self.vaults)
        cache.write_index(data)
        self._index = data
        return data

    def write_target(self, scope: str) -> Vault:
        if scope == "project":
            if self.local_vault is not None:
                return self.local_vault
            raise SystemExit(
                "error: --scope project 需要在项目目录内运行，或显式指定 --project-root"
            )
        return self.global_vault

    def find_anywhere(self, entry_id: str) -> tuple[Entry, Path, str, Vault] | None:
        for label, vault in self.vaults:
            found = vault.find(entry_id)
            if found:
                return found[0], found[1], label, vault
        return None


# --- Output helpers --------------------------------------------------------


def force_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def clip(text: str, limit: int = 400) -> str:
    """Truncate while preserving line structure.

    Flattening newlines ruins structured entries (a script path on one line, its
    run command on the next, known pitfalls below) -- exactly the entries an agent
    needs to read carefully before acting.
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if "\n" in cut:
        cut = cut.rsplit("\n", 1)[0]
    return cut + "…"


def hit_to_json(hit: retrieval.Hit, body: str = "") -> dict:
    doc = hit.doc
    payload = {
        "id": doc["id"],
        "title": doc["title"],
        "kind": doc["kind"],
        "scope": doc["scope"],
        "project": doc.get("project"),
        "tags": doc["tags"],
        "importance": doc["importance"],
        "confidence": doc["confidence"],
        "created": doc["created"],
        "updated": doc["updated"],
        "score": round(hit.score, 4),
        "signals": {k: round(v, 3) for k, v in hit.signals.items()},
        "vault": doc["vault"],
    }
    if body:
        payload["body"] = body
    return payload


def print_hits(hits: list[retrieval.Hit], bodies: dict[str, str] | None = None) -> None:
    if not hits:
        print("(no matching memories)")
        return
    for rank, hit in enumerate(hits, 1):
        doc = hit.doc
        print(f"{rank}. {doc['title']}")
        print(
            f"   id={doc['id']}  kind={doc['kind']}  imp={doc['importance']}"
            f"  scope={doc['scope']}  score={hit.score:.3f}"
        )
        if bodies and bodies.get(doc["id"]):
            for line in clip(bodies[doc["id"]], 400).split("\n"):
                print(f"   {line}")
        if doc["tags"]:
            print(f"   tags: {', '.join(doc['tags'])}")
        print()


# --- Commands --------------------------------------------------------------


def cmd_add(args, mem: Memory) -> int:
    body = ""
    if args.content:
        body = args.content
    elif args.file:
        body = Path(args.file).expanduser().read_text(encoding="utf-8")
    elif args.stdin:
        # Only read stdin when explicitly asked. Auto-detecting via isatty()
        # hangs in non-interactive shells, which is exactly how agents call this.
        body = sys.stdin.read()
    body = body.strip()

    if not args.title and not body:
        print("error: 需要 --title 或正文内容", file=sys.stderr)
        return 2

    title = args.title or clip(body, 60)
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    kind = args.kind if args.kind in store.VALID_KINDS else "fact"

    if kind != args.kind:
        print(f"warn: 未知 kind '{args.kind}'，回退为 fact", file=sys.stderr)

    scope = args.scope
    project_name = None
    if scope == "project":
        root = find_project_root(Path(args.project_root) if args.project_root else None)
        project_name = root.name if root else None

    # Deduplicate before writing: identical content is idempotent, and
    # near-duplicates surface the existing entry instead of piling up noise.
    if not args.force:
        index = mem.ensure_index()
        probe = retrieval.search(title + " " + body, index, limit=3)
        for hit in probe:
            same_title = hit.doc["title"].strip() == title.strip()
            if same_title and hit.cosine >= 0.90 and hit.doc["kind"] == kind:
                print(f"exists: {hit.doc['id']}  {hit.doc['title']}")
                print("(相同记忆已存在，未重复写入)")
                return 0
            if same_title:
                print(f"conflict: {hit.doc['id']}  {hit.doc['title']}")
                print(f"(同标题条目已存在但内容不同，用 `update {hit.doc['id']}` 修订，"
                      f"或加 --force 另存为新条目)")
                return 0
            if hit.cosine >= 0.92 and hit.doc["kind"] == kind:
                print(f"similar: {hit.doc['id']}  {hit.doc['title']}  cosine={hit.cosine:.3f}")
                print("(已有高度相似的记忆；如确需新增请加 --force，或改用 update 修订)")
                return 0

    entry = Entry(
        title=title,
        body=body,
        kind=kind,
        scope=scope,
        project=project_name,
        tags=tags,
        importance=max(1, min(5, args.importance)),
        confidence=args.confidence,
        source=args.source,
        expires=args.expires,
        supersedes=[s.strip() for s in (args.supersedes or "").split(",") if s.strip()],
    )
    vault = mem.write_target(scope)
    path = vault.save(entry)

    # Retire memories this entry explicitly replaces.
    for old_id in entry.supersedes:
        found = mem.find_anywhere(old_id)
        if found:
            old_entry, _, _, old_vault = found
            old_entry.expires = datetime.now().date().isoformat()
            old_entry.importance = 1
            old_vault.save(old_entry)

    mem.ensure_index(force=True)
    if args.json:
        print(json.dumps({"id": entry.id, "path": str(path), "title": entry.title},
                         ensure_ascii=False, indent=2))
    else:
        print(f"saved: {entry.id}  [{entry.kind}/{entry.scope}]  {entry.title}")
        print(f"  -> {path}")
    return 0


def cmd_search(args, mem: Memory) -> int:
    index = mem.ensure_index()
    hits = retrieval.search(
        args.query,
        index,
        limit=args.limit,
        kinds=args.kind.split(",") if args.kind else None,
        scopes=args.scope.split(",") if args.scope else None,
        tags=args.tags.split(",") if args.tags else None,
        project=args.project,
        include_expired=args.include_expired,
        min_score=args.min_score,
    )
    # JSON consumers need the body to act on a hit, so include it by default --
    # otherwise every match costs a second `get` round trip.
    bodies = {}
    if args.with_body or args.json:
        for hit in hits:
            vault = mem.global_vault if hit.doc["vault"] == "global" else mem.local_vault
            found = vault.find(hit.doc["id"]) if vault else None
            if found:
                bodies[hit.doc["id"]] = found[0].body

    if args.json:
        print(json.dumps([hit_to_json(h, bodies.get(h.doc["id"], "")) for h in hits],
                         ensure_ascii=False, indent=2))
    else:
        print_hits(hits, bodies)
    return 0


def cmd_get(args, mem: Memory) -> int:
    found = mem.find_anywhere(args.id)
    if not found:
        print(f"error: 未找到条目 {args.id}", file=sys.stderr)
        return 1
    entry, path, label, _ = found
    if args.json:
        payload = entry.to_dict()
        payload["body"] = entry.body
        payload["vault"] = label
        payload["path"] = str(path)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"# {entry.title}")
        print(f"id={entry.id}  kind={entry.kind}  scope={entry.scope}  imp={entry.importance}")
        print(f"created={entry.created}  updated={entry.updated}  vault={label}")
        if entry.tags:
            print(f"tags: {', '.join(entry.tags)}")
        if entry.expires:
            print(f"expires: {entry.expires}")
        print()
        print(entry.body)
    return 0


def cmd_list(args, mem: Memory) -> int:
    index = mem.ensure_index()
    docs = [d for d in index["docs"] if not retrieval.is_expired(d, datetime.now().astimezone())]
    if args.kind:
        kinds = {k.strip() for k in args.kind.split(",")}
        docs = [d for d in docs if d["kind"] in kinds]
    if args.scope:
        scopes = {s.strip() for s in args.scope.split(",")}
        docs = [d for d in docs if d["scope"] in scopes]
    if args.sort == "importance":
        docs.sort(key=lambda d: (d["importance"], d["updated"]), reverse=True)
    else:
        docs.sort(key=lambda d: d["updated"] or d["created"], reverse=True)
    docs = docs[: args.limit]

    if args.json:
        print(json.dumps(docs, ensure_ascii=False, indent=2))
        return 0
    if not docs:
        print("(empty)")
        return 0
    for doc in docs:
        print(f"[{doc['kind']:<10}] {doc['id']}  imp={doc['importance']}  {doc['title']}")
    print(f"\n{len(docs)} shown")
    return 0


def cmd_update(args, mem: Memory) -> int:
    found = mem.find_anywhere(args.id)
    if not found:
        print(f"error: 未找到条目 {args.id}", file=sys.stderr)
        return 1
    entry, path, _, vault = found
    if args.title:
        entry.title = args.title
    # Prefer file/stdin for long or path-heavy content: shells rewrite Windows
    # backslash paths (D:\x becomes D:/x) even inside single quotes.
    if args.content is not None:
        entry.body = args.content
    elif args.file:
        entry.body = Path(args.file).expanduser().read_text(encoding="utf-8").strip()
    elif args.stdin:
        entry.body = sys.stdin.read().strip()
    if args.kind:
        entry.kind = args.kind
    if args.tags is not None:
        entry.tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.importance is not None:
        entry.importance = max(1, min(5, args.importance))
    if args.confidence:
        entry.confidence = args.confidence
    if args.expires is not None:
        entry.expires = args.expires or None
    vault.save(entry)
    mem.ensure_index(force=True)
    print(f"updated: {entry.id}  {entry.title}")
    return 0


def cmd_forget(args, mem: Memory) -> int:
    removed = []
    for entry_id in args.ids:
        for _, vault in mem.vaults:
            if vault.delete(entry_id):
                removed.append(entry_id)
                break
    if not removed:
        print("error: 没有条目被删除", file=sys.stderr)
        return 1
    mem.ensure_index(force=True)
    print("moved to .trash: " + ", ".join(removed))
    return 0


def cmd_scan(args, mem: Memory) -> int:
    """Propose (and optionally write) memories for existing local projects."""
    import scan as scanner

    root = Path(args.path).expanduser()
    if not root.is_dir():
        print(f"error: 目录不存在 {root}", file=sys.stderr)
        return 1

    candidates = scanner.scan(root, min_files=args.min_files)
    if not candidates:
        print(f"在 {root} 下没有发现值得记录的内容")
        return 0

    if args.json:
        print(json.dumps([c.to_dict() for c in candidates], ensure_ascii=False, indent=2))
        if not args.write:
            return 0

    if not args.write:
        for idx, cand in enumerate(candidates, 1):
            print(f"{idx}. [{cand.kind}] {cand.title}")
            print(f"   {cand.path}")
            print(f"   {cand.reason}")
        print()
        print(f"共 {len(candidates)} 条候选。确认无误后加 --write 写入记忆库。")
        print("提示：标题是自动推断的，写入后可用 update 改得更准确。")
        return 0

    saved = updated = skipped = 0
    for cand in candidates:
        index = mem.ensure_index()
        hits = retrieval.search(cand.title + " " + cand.body, index, limit=1)
        if hits:
            hit = hits[0]
            same = hit.doc["title"].strip() == cand.title.strip()
            if same or hit.cosine >= 0.92:
                # --update refreshes the body in place, keeping the id and
                # creation date. Useful after improving the extraction logic.
                if args.update:
                    found = mem.find_anywhere(hit.doc["id"])
                    if found:
                        entry, _, _, vault = found
                        entry.body = cand.body
                        entry.tags = cand.tags
                        entry.importance = cand.importance
                        vault.save(entry)
                        mem.ensure_index(force=True)
                        print(f"update {entry.id}  {entry.title}")
                        updated += 1
                        continue
                print(f"skip   {cand.title}  (已存在 {hit.doc['id']})")
                skipped += 1
                continue
        entry = Entry(
            title=cand.title,
            body=cand.body,
            kind=cand.kind,
            scope="global",
            tags=cand.tags,
            importance=cand.importance,
            source="scan",
        )
        mem.write_target("global").save(entry)
        mem.ensure_index(force=True)
        print(f"saved  {entry.id}  {entry.title}")
        saved += 1

    print(f"\n写入 {saved} 条，更新 {updated} 条，跳过 {skipped} 条")
    return 0


def cmd_explain(args, mem: Memory) -> int:
    """Show why a query matched (or failed to match) -- for debugging recall."""
    import synonyms

    index = mem.ensure_index()
    core = textproc.tokenize(args.query)
    extra = synonyms.expand_tokens(core)
    trace = synonyms.explain(args.query)
    hits = retrieval.search(args.query, index, limit=args.limit)

    if args.json:
        print(json.dumps(
            {
                "query": args.query,
                "core_tokens": core,
                "expansion_tokens": extra,
                "expansion_sources": [{"token": t, "implies": w} for t, w in trace],
                "hits": [hit_to_json(h) for h in hits],
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0

    print(f"query     : {args.query}")
    print(f"core      : {core}")
    if trace:
        print("expansion :")
        seen_sets = []
        for token, words in trace:
            if words in seen_sets:
                continue
            seen_sets.append(words)
            print(f"  {token} -> {', '.join(words[:10])}")
    else:
        print("expansion : (no concept match, lexical only)")
    print()
    if not hits:
        print("no hits. Check: is the entry in another vault (project vs global)?")
        print("              is it expired? try --include-expired on search.")
        print("              is the index stale? run `memory.py index`.")
        return 0
    for rank, hit in enumerate(hits, 1):
        signals = "  ".join(f"{k}={v:.2f}" for k, v in sorted(hit.signals.items()))
        print(f"{rank}. {hit.doc['title']}  score={hit.score:.3f}")
        print(f"   {signals}")
    return 0


def cmd_stats(args, mem: Memory) -> int:
    index = mem.ensure_index()
    now = datetime.now().astimezone()
    total = len(index["docs"])
    expired = sum(1 for d in index["docs"] if retrieval.is_expired(d, now))
    by_kind: dict[str, int] = {}
    by_scope: dict[str, int] = {}
    for doc in index["docs"]:
        by_kind[doc["kind"]] = by_kind.get(doc["kind"], 0) + 1
        by_scope[doc["scope"]] = by_scope.get(doc["scope"], 0) + 1
    payload = {
        "global_vault": str(mem.global_vault.root),
        "project_vault": (
            str(mem.local_vault.root)
            if mem.local_vault is not None and mem.local_vault.exists()
            else None
        ),
        "total": total,
        "expired": expired,
        "by_kind": by_kind,
        "by_scope": by_scope,
        "tags": len(index.get("tags") or []),
        "index_built_at": index.get("built_at"),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"global : {payload['global_vault']}")
        if payload["project_vault"]:
            print(f"project: {payload['project_vault']}")
        print(f"total  : {total} entries ({expired} expired)")
        print("kinds  : " + ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items())))
        print("scopes : " + ", ".join(f"{k}={v}" for k, v in sorted(by_scope.items())))
    return 0


def cmd_digest(args, mem: Memory) -> int:
    index = mem.ensure_index()
    hits = retrieval.digest(index, limit=args.limit)
    if args.kind:
        kinds = {k.strip() for k in args.kind.split(",")}
        hits = [h for h in hits if h.doc["kind"] in kinds]
    bodies = {}
    for hit in hits:
        vault = mem.global_vault if hit.doc["vault"] == "global" else mem.local_vault
        found = vault.find(hit.doc["id"]) if vault else None
        if found:
            bodies[hit.doc["id"]] = found[0].body
    if args.json:
        print(json.dumps([hit_to_json(h, bodies.get(h.doc["id"], "")) for h in hits],
                         ensure_ascii=False, indent=2))
    else:
        print_hits(hits, bodies)
    return 0


def cmd_index(args, mem: Memory) -> int:
    data = mem.ensure_index(force=True)
    print(f"indexed {data['n']} entries  avgdl={data['avgdl']:.1f}  "
          f"terms={len(data['df'])}  tags={len(data['tags'])}")
    return 0


def cmd_prune(args, mem: Memory) -> int:
    now = datetime.now().astimezone()
    removed = 0
    for _, vault in mem.vaults:
        for entry in vault.all_entries():
            if entry.is_expired(now):
                vault.delete(entry.id)
                removed += 1
    cutoff = now - timedelta(days=args.trash_days)
    for _, vault in mem.vaults:
        if not vault.trash_dir.is_dir():
            continue
        for path in vault.trash_dir.glob("*.md"):
            try:
                if datetime.fromtimestamp(path.stat().st_mtime).astimezone() < cutoff:
                    path.unlink()
            except OSError:
                continue
    mem.ensure_index(force=True)
    print(f"pruned {removed} expired entries")
    return 0


def cmd_export(args, mem: Memory) -> int:
    index = mem.ensure_index()
    rows = []
    for doc in sorted(index["docs"], key=lambda d: d["created"]):
        vault = mem.global_vault if doc["vault"] == "global" else mem.local_vault
        found = vault.find(doc["id"]) if vault else None
        rows.append({**doc, "body": found[0].body if found else ""})

    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    lines = ["# Memory Export", ""]
    current_kind = None
    for row in rows:
        if row["kind"] != current_kind:
            current_kind = row["kind"]
            lines.append(f"\n## {current_kind}\n")
        lines.append(f"### {row['title']}")
        lines.append(f"<!-- id={row['id']} scope={row['scope']} imp={row['importance']} "
                     f"created={row['created']} -->")
        lines.append("")
        lines.append(row["body"].strip())
        lines.append("")
    print("\n".join(lines))
    return 0


# --- Parser ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    # Options are declared twice on purpose: once on the root parser and once as
    # SUPPRESS-defaulted copies on each subcommand. That makes both
    # `memory.py --json stats` and `memory.py stats --json` work, which matters
    # because callers (human or agent) rarely remember the ordering rule.
    root_opts = argparse.ArgumentParser(add_help=False)
    root_opts.add_argument("--vault", help="全局记忆库路径（默认 ~/.workbuddy-ai/memory-vault）")
    root_opts.add_argument("--project-root", help="显式指定项目根目录")
    root_opts.add_argument("--no-project", action="store_true", help="只使用全局记忆库")
    root_opts.add_argument("--json", action="store_true", help="输出 JSON")

    sub_opts = argparse.ArgumentParser(add_help=False)
    sub_opts.add_argument("--vault", default=argparse.SUPPRESS)
    sub_opts.add_argument("--project-root", default=argparse.SUPPRESS)
    sub_opts.add_argument("--no-project", action="store_true", default=argparse.SUPPRESS)
    sub_opts.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(
        prog="memory",
        parents=[root_opts],
        description="跨会话记忆库：写入、检索、维护长期记忆。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  memory.py add --title '用 pnpm 而非 npm' --kind preference "
            "--tags 工具链 --importance 4 --content '...'\n"
            "  memory.py search '构建工具 选择' --limit 5\n"
            "  memory.py digest --limit 10\n"
        ),
    )

    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help_text, parents=[sub_opts])

    p = add("add", "写入一条记忆")
    p.add_argument("--title", help="一句话标题（也是检索权重最高的字段）")
    p.add_argument("--content", help="正文")
    p.add_argument("--file", help="从文件读取正文")
    p.add_argument("--stdin", action="store_true", help="从标准输入读取正文")
    p.add_argument("--kind", default="fact", help=" | ".join(store.VALID_KINDS))
    p.add_argument("--scope", default="global", choices=store.VALID_SCOPES)
    p.add_argument("--tags", default="", help="逗号分隔的标签")
    p.add_argument("--importance", type=int, default=3, help="1-5，默认 3")
    p.add_argument("--confidence", default="high", choices=store.VALID_CONFIDENCE)
    p.add_argument("--source", default="conversation")
    p.add_argument("--expires", help="过期日期 YYYY-MM-DD（临时信息用）")
    p.add_argument("--supersedes", help="逗号分隔的被取代条目 id")
    p.add_argument("--force", action="store_true", help="跳过查重，强制写入")
    p.set_defaults(func=cmd_add)

    p = add("search", "检索记忆")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--kind", help="逗号分隔")
    p.add_argument("--scope", help="global,project")
    p.add_argument("--tags", help="逗号分隔")
    p.add_argument("--project", help="按项目名过滤")
    p.add_argument("--with-body", action="store_true", help="附带正文摘要")
    p.add_argument("--include-expired", action="store_true")
    p.add_argument("--min-score", type=float, default=0.0)
    p.set_defaults(func=cmd_search)

    p = add("get", "读取单条记忆全文")
    p.add_argument("id")
    p.set_defaults(func=cmd_get)

    p = add("list", "列出记忆")
    p.add_argument("--kind")
    p.add_argument("--scope")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--sort", default="recent", choices=("recent", "importance"))
    p.set_defaults(func=cmd_list)

    p = add("update", "修订已有记忆")
    p.add_argument("id")
    p.add_argument("--title")
    p.add_argument("--content")
    p.add_argument("--file", help="从文件读取正文（推荐用于含反斜杠路径的长内容）")
    p.add_argument("--stdin", action="store_true", help="从标准输入读取正文")
    p.add_argument("--kind")
    p.add_argument("--tags")
    p.add_argument("--importance", type=int)
    p.add_argument("--confidence")
    p.add_argument("--expires")
    p.set_defaults(func=cmd_update)

    p = add("forget", "删除记忆（移入 .trash）")
    p.add_argument("ids", nargs="+")
    p.set_defaults(func=cmd_forget)

    p = add("scan", "扫描目录，把现有项目/任务沉淀进记忆库")
    p.add_argument("path", help="要扫描的根目录，如 D:/workbuddy-ai")
    p.add_argument("--write", action="store_true", help="确认写入（默认只预览）")
    p.add_argument("--update", action="store_true", help="已存在的条目刷新正文而不是跳过")
    p.add_argument("--min-files", type=int, default=1, help="目录至少多少文件才算数")
    p.set_defaults(func=cmd_scan)

    p = add("explain", "诊断检索：查看分词、扩展词与打分构成")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=3)
    p.set_defaults(func=cmd_explain)

    p = add("stats", "统计概览")
    p.set_defaults(func=cmd_stats)

    p = add("digest", "无查询预热：返回最高价值的记忆")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--kind")
    p.set_defaults(func=cmd_digest)

    p = add("index", "重建检索索引")
    p.set_defaults(func=cmd_index)

    p = add("prune", "清理过期记忆与旧回收站")
    p.add_argument("--trash-days", type=int, default=30)
    p.set_defaults(func=cmd_prune)

    p = add("export", "导出全部记忆")
    p.add_argument("--format", default="md", choices=("md", "json"))
    p.set_defaults(func=cmd_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)

    global_root = Path(args.vault).expanduser() if args.vault else default_global_root()
    local_root = None if args.no_project else project_vault_root(
        Path(args.project_root) if args.project_root else None
    )
    mem = Memory(global_root, local_root)
    mem.global_vault.ensure()
    return args.func(args, mem)


if __name__ == "__main__":
    raise SystemExit(main())
