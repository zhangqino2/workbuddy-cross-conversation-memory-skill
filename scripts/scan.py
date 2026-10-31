"""Scan a directory tree and propose memory entries for what it finds.

Strictly read-only: this module inspects files and returns candidates. Nothing is
written until the caller explicitly asks for it. That separation matters because
scanning runs against the user's real project directories.

The output is a proposal, not a verdict. Auto-generated titles from a README are
serviceable but bland; a human or an agent reviewing the list will usually improve
them. `--dry-run` is therefore the default.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Directories that never carry meaning worth remembering.
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", "dist", "build", ".venv", "venv",
    "target", ".idea", ".vscode", ".workbuddy-ai", ".next", ".nuxt", "vendor",
    ".pytest_cache", ".mypy_cache", "coverage", ".trash",
}

TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:-\d{2}-\d{2}-\d{2})?$")
HTML_RE = re.compile(r"<[^>]+>")
BADGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
NUM_PREFIX_RE = re.compile(r"^\d+[_\-]?")
VS_PREFIX_RE = re.compile(r"^(?:vs|VS)[_\-]")

CODE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt",
    ".c", ".cpp", ".h", ".rb", ".php", ".cs", ".swift", ".sh",
}

# A directory with this many source files is a project even without a manifest --
# plenty of real projects have no README and no package.json.
CODE_FILES_PROJECT_THRESHOLD = 3

# Bundled front-end libraries that tend to dominate a workspace by filename length.
LIBRARY_HINTS = {
    "chart", "jquery", "lodash", "d3", "echarts", "vue", "react", "bootstrap",
    "moment", "dayjs", "axios", "three", "plotly", "alpine", "htmx",
}

STACK_MARKERS = [
    ("package.json", "Node"),
    ("requirements.txt", "Python"),
    ("pyproject.toml", "Python (pyproject)"),
    ("setup.py", "Python"),
    ("build.gradle.kts", "Kotlin/Android (Gradle KTS)"),
    ("build.gradle", "Gradle"),
    ("Cargo.toml", "Rust"),
    ("go.mod", "Go"),
    ("pom.xml", "Java (Maven)"),
    ("composer.json", "PHP"),
    ("Gemfile", "Ruby"),
    ("Dockerfile", "Docker"),
]


@dataclass
class Candidate:
    path: Path
    title: str
    body: str
    kind: str
    tags: list[str] = field(default_factory=list)
    importance: int = 3
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "title": self.title,
            "kind": self.kind,
            "tags": self.tags,
            "importance": self.importance,
            "reason": self.reason,
            "body": self.body,
        }


def visible_files(root: Path, max_depth: int = 2) -> list[Path]:
    out: list[Path] = []
    stack = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if name.startswith(".") and name not in (".env.example",):
                continue
            if entry.is_dir():
                if name in SKIP_DIRS or depth >= max_depth:
                    continue
                stack.append((entry, depth + 1))
            else:
                out.append(entry)
    return out


def readme_summary(root: Path, limit: int = 160) -> str:
    """First meaningful prose from a README, with badges, HTML and tables stripped.

    Table rows are the main trap: a feature matrix is technically prose to a naive
    filter, but as a one-line summary it is noise that crowds out the description.
    """
    for name in ("README.md", "README.zh-CN.md", "readme.md", "使用说明.md",
                 "说明.md", "README_CN.md", "README.txt", "README"):
        path = root / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        chunks: list[str] = []
        for line in text.split("\n"):
            s = line.strip()
            if not s or s.startswith("#") or s.startswith("<") or s.startswith("```"):
                continue
            if s.startswith("|") or s.count("|") >= 2:
                continue
            if set(s) <= set("-|: "):
                continue
            s = s.lstrip(">").strip()
            s = BADGE_RE.sub("", s)
            s = HTML_RE.sub("", s)
            s = LINK_RE.sub(r"\1", s)
            s = s.replace("**", "").replace("__", "").replace("`", "").strip(" |-·")
            if len(s) < 6:
                continue
            chunks.append(s)
            if sum(len(c) for c in chunks) >= limit:
                break
        summary = " ".join(chunks)
        if summary:
            return summary[:limit]
    return ""


def detect_stack(root: Path) -> list[str]:
    found: list[str] = []
    for marker, label in STACK_MARKERS:
        if (root / marker).is_file():
            found.append(label)

    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="ignore"))
            deps = list((data.get("dependencies") or {}).keys())[:6]
            if deps:
                found.append("依赖: " + ", ".join(deps))
        except (json.JSONDecodeError, OSError):
            pass

    req = root / "requirements.txt"
    if req.is_file():
        try:
            names = [
                line.strip().split("==")[0].split(">=")[0]
                for line in req.read_text(encoding="utf-8", errors="ignore").split("\n")
                if line.strip() and not line.startswith("#")
            ][:8]
            if names:
                found.append("依赖: " + ", ".join(names))
        except OSError:
            pass
    return found


def git_remote(root: Path) -> str:
    config = root / ".git" / "config"
    if not config.is_file():
        return ""
    try:
        text = config.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    match = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', text, re.S)
    return match.group(1) if match else ""


def guess_title_from_files(root: Path, files: list[Path]) -> str:
    """Pick the most descriptive artifact name for a workspace-style directory.

    Bundled third-party libraries are the trap here: `chart.umd.min.js` is often
    the longest name in a workspace, and naming the task after it says nothing.
    """
    subdirs = [p.name for p in root.iterdir() if p.is_dir() and p.name not in SKIP_DIRS]
    if len(subdirs) == 1:
        return subdirs[0]
    names = []
    for f in files:
        if ".min." in f.name.lower():
            continue
        stem = NUM_PREFIX_RE.sub("", f.stem)
        stem = VS_PREFIX_RE.sub("", stem)
        stem = stem.replace("_", " ").strip()
        if stem.lower().split(" ")[0] in LIBRARY_HINTS:
            continue
        if 4 <= len(stem) <= 40:
            names.append(stem)
    if not names:
        return root.name
    # Chinese names describe intent better than English file stems, which are
    # usually implementation detail ("build report" vs "纳指100 拟合度分析").
    names.sort(key=lambda s: (sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff"), len(s)), reverse=True)
    return names[0]


def is_project(root: Path, files: list[Path] | None = None) -> bool:
    markers = ["README.md", "package.json", "pyproject.toml", "requirements.txt",
               "Cargo.toml", "go.mod", "pom.xml", "build.gradle.kts", "build.gradle"]
    if any((root / m).is_file() for m in markers):
        return True
    if (root / ".git").is_dir():
        return True
    files = files if files is not None else visible_files(root)
    code = sum(1 for f in files if f.suffix.lower() in CODE_EXTS)
    return code >= CODE_FILES_PROJECT_THRESHOLD


def short_summary(summary: str, limit: int = 30) -> str:
    """Trim to the first clause so titles never end mid-word."""
    for sep in ("。", "；", "：", ":", ";", ". ", "，", ","):
        idx = summary.find(sep)
        if 0 < idx <= limit:
            return summary[:idx]
    if len(summary) <= limit:
        return summary
    return summary[:limit].rstrip() + "…"


def build_candidate(root: Path) -> Candidate | None:
    files = visible_files(root)
    if not files:
        return None

    rel_files = [f.relative_to(root) for f in files]
    top_names = [str(p) for p in rel_files[:10]]
    stack = detect_stack(root)
    remote = git_remote(root)
    summary = readme_summary(root)
    project = is_project(root, files)
    # A timestamped session workspace is a task record even if it happens to
    # contain enough source files to look like a project.
    is_workspace = bool(TIMESTAMP_RE.match(root.name))

    lines = [f"路径：{root}"]
    if is_workspace:
        lines.append("类型：任务工作区（会话产物目录）")
    elif project:
        lines.append("类型：独立项目")
    else:
        lines.append("类型：普通目录")
    if stack:
        lines.append("技术栈：" + "；".join(stack))
    if remote:
        lines.append(f"远程仓库：{remote}")
    if summary:
        lines.append(f"说明：{summary}")
    lines.append(f"文件数：{len(files)}")
    lines.append("主要文件：" + ", ".join(top_names))

    if is_workspace:
        guess = guess_title_from_files(root, files)
        title = f"任务 {root.name}：{guess}"
        kind = "task"
        tags = ["任务", "会话产物"]
        importance = 3
        reason = "带时间戳的会话工作目录，含产物文件"
    elif project:
        head = short_summary(summary) if summary else ""
        title = f"项目 {root.name}" + (f"：{head}" if head else "")
        kind = "entity"
        tags = ["项目", root.name]
        importance = 4
        reason = "含 README/构建配置或源码，是独立项目"
    else:
        title = f"目录 {root.name}"
        kind = "fact"
        tags = ["目录"]
        importance = 2
        reason = "普通目录"

    return Candidate(
        path=root,
        title=title,
        body="\n".join(lines),
        kind=kind,
        tags=tags,
        importance=importance,
        reason=reason,
    )


def scan(root: Path, min_files: int = 1, max_depth: int = 1) -> list[Candidate]:
    """Walk `root` and propose one candidate per meaningful child directory."""
    root = root.expanduser().resolve()
    if not root.is_dir():
        return []

    candidates: list[Candidate] = []
    try:
        children = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return []

    for child in children:
        if child.name.startswith(".") or child.name in SKIP_DIRS:
            continue
        if len(visible_files(child, max_depth=1)) < min_files:
            continue
        cand = build_candidate(child)
        if cand:
            candidates.append(cand)

    # A bare root that is itself a project (no meaningful children) still counts.
    if not candidates and is_project(root):
        cand = build_candidate(root)
        if cand:
            candidates.append(cand)

    return candidates
