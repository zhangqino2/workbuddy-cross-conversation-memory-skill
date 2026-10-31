# Entry Schema · 条目结构

**中文** — 每条记忆是一个 Markdown 文件。frontmatter 是机器可读部分，正文是载荷。
两者都参与索引。

**English** — Every memory is one Markdown file. The frontmatter is the machine-readable
part; the body is the payload. Both are indexed.

## File layout · 文件布局

```
~/.workbuddy-ai/memory-vault/          # 全局库 / global vault
├── entries/2026/10/20261031-a6d1bb45.md
├── index/index.json                   # 派生索引，可随时重建，删掉也安全
├── .trash/                            # 被 forget 的条目，可恢复
└── config.json                        # 可选覆盖项

<project>/.workbuddy-ai/memory/        # 项目库 / project vault，结构相同
```

**中文** — 路径中的日期段来自 `created`，文件名就是条目 id。

**English** — The date path segments come from `created`. The filename is the entry id.

## Frontmatter fields · 字段说明

| 字段 Field | 类型 Type | 默认 Default | 作用 Effect |
|---|---|---|---|
| `id` | string | 自动 auto | `YYYYMMDD-<hash8>`，由 title+body 决定。`get`/`update`/`forget` 用它定位 |
| `title` | string | 必填 | 被索引两次，主导排序。写成一句完整的论断，不要写成标签 |
| `kind` | enum | `fact` | 见下表。同时决定该条目在排序中的衰减速度 |
| `scope` | `global` \| `project` | `global` | 存在哪个库。项目级条目在该项目外不可见 |
| `project` | string \| null | null | 项目目录名，`--scope project` 时自动写入 |
| `created` | ISO 8601 | 当前时间 | 不可变，决定日期路径 |
| `updated` | ISO 8601 | 当前时间 | 每次保存刷新，驱动时效打分 |
| `tags` | string[] | `[]` | CLI 上用逗号分隔。精确、廉价的过滤手段；查询命中标签会加分 |
| `importance` | 1-5 | 3 | 直接影响排序，也是 `digest` 的主要输入。要诚实：多数条目就是 3 |
| `confidence` | `high`\|`medium`\|`low` | `high` | 用户明确说的，还是 agent 推断的。low 表示呈现时应说明是推测 |
| `source` | string | `conversation` | 自由格式来源：`conversation`、`user-stated`、`scan`、`docs` |
| `expires` | date \| null | null | 硬截止。过期条目从检索中消失，但文件保留到 `prune` |
| `supersedes` | string[] | `[]` | 本条取代的条目 id。写入时那些条目会被自动置为过期 |
| `related` | string[] | `[]` | 软链接，仅供人看，检索时不遍历 |

## Kinds and their decay · 类型与衰减

**中文** — `kind` 不只是标签——它决定时效信号的半衰期，所以不同类别无需调参就有不同的
排序行为。

| kind | 半衰期 Half-life | 适用内容 |
|---|---|---|
| `preference` | 540 天 | 稳定的工作习惯，应当压过几乎所有其他条目 |
| `decision` | 360 天 | 带理由的选择，寿命长但可修订 |
| `lesson` | 360 天 | 事后复盘。恰恰因为旧才有价值 |
| `fact` | 270 天 | 环境与领域事实 |
| `entity` | 270 天 | 人、服务、项目 |
| `snippet` | 180 天 | 命令与配置，会随工具演进漂移 |
| `task` | 30 天 | 在办状态，做完就该快速淡出 |

**中文** — 两年前的 `lesson` 仍然值得排前面；两年前的 `task` 基本消失。这就是为什么
选对 kind 比选对 importance 更重要。

**English** — `kind` is not just a label — it sets the half-life used by the recency
signal, so ranking behaves differently per category without any tuning. A two-year-old
`lesson` still scores respectably; a two-year-old `task` effectively disappears. This is
why picking the right kind matters more than picking the right importance.

## Full example · 完整示例

```markdown
---
id: 20261031-a6d1bb45
title: 构建统一用 pnpm，禁用 npm
kind: preference
scope: global
project: null
created: 2026-10-31T18:35:30+08:00
updated: 2026-10-31T18:35:30+08:00
tags: [工具链, node]
importance: 4
confidence: high
source: conversation
expires: null
supersedes: []
related: []
---

所有 Node 项目使用 pnpm。原因：CI 缓存命中率更高，且 lockfile 冲突更少。
不要生成 package-lock.json；已存在的要删除并加入 .gitignore。
```

## Manual editing · 手工编辑

**中文** — 支持并鼓励直接改文件——这正是选择 Markdown 的意义。两条规矩：

1. 保留 frontmatter 的 `---` 分隔符和字段名不变
2. 改了正文就顺手更新 `updated`，否则时效打分会低估你的修改。或者干脆通过
   `update <id>` 重新保存，它会自动处理

批量手改之后跑一次 `python scripts/memory.py index`。日常单条修改会被文件 mtime 的
增量检测自动发现。

**English** — Editing files by hand is supported and encouraged — that is the point of
choosing Markdown. Keep the frontmatter delimiters and field names intact, and bump
`updated` if you change the body (or just re-save through `update <id>`). Run
`python scripts/memory.py index` after a bulk hand-edit; single edits are picked up
automatically via mtime.

## Recovery · 恢复

**中文** — `forget` 把文件移进 `.trash/` 而不是删除。要恢复，把文件移回
`entries/<YYYY>/<MM>/` 再重建索引。`prune` 默认清理超过 30 天的回收站内容
（用 `--trash-days N` 调整）。

**English** — `forget` moves files to `.trash/` rather than deleting. To restore, move the
file back under `entries/<YYYY>/<MM>/` and rebuild the index. `prune` clears trash older
than 30 days by default (`--trash-days N` to change).
